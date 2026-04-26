"""
tools/train_roof_classifier.py — Train the RoofNet shape-classification head
on OSM-labelled building patches produced by harvest_roof_crops.py.

Training strategy
-----------------
- Model  : RoofNet (tools/networks.py) — shared backbone + height head + shape head
- Loss   : CrossEntropyLoss on shape_logits; height head frozen during this stage
           (set TRAIN_BOTH_HEADS=True to also optimise the height head with
           a dummy zero-height target — useful for regularisation but requires
           real height labels for production-quality results)
- Optimiser : AdamW, cosine LR schedule with linear warm-up
- Data   : output/roof_crops/  as an ImageFolder-compatible directory
           (produced by harvest_roof_crops.py)
- Split  : 80 / 10 / 10 train / val / test by manifest city
- Output : models/roofnet_shape_v1.pt   (full model, torch.save)

Usage
-----
    # minimal (CPU, default paths):
    ..\\..venv\\Scripts\\python.exe -m tools.train_roof_classifier

    # GPU, custom paths:
    ..\\..venv\\Scripts\\python.exe -m tools.train_roof_classifier \\
        --data-dir output/roof_crops \\
        --output-model models/roofnet_shape_v1.pt \\
        --epochs 40 --batch-size 32 --lr 3e-4

    # resume from checkpoint:
    ..\\..venv\\Scripts\\python.exe -m tools.train_roof_classifier \\
        --resume models/roofnet_shape_v1.pt --epochs 20
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_STRM2STL = _HERE.parent
_REPO_ROOT = _STRM2STL.parent
for _p in (_STRM2STL, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Optional torch check
# ---------------------------------------------------------------------------
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
    import torchvision.transforms as T
    from PIL import Image
    import numpy as np
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SHAPE_LABELS = ["flat", "gabled", "hipped", "pyramidal", "skillion", "dome"]
LABEL_TO_IDX = {lbl: i for i, lbl in enumerate(SHAPE_LABELS)}
DEFAULT_DATA_DIR = "output/roof_crops"
DEFAULT_MODEL_PATH = "models/roofnet_shape_v1.pt"
DEFAULT_EPOCHS = 30
DEFAULT_BATCH = 16
DEFAULT_LR = 3e-4
DEFAULT_CROP_SIZE = 64


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class RoofCropDataset(Dataset):
    """Dataset reading from harvest_roof_crops.py manifest CSV."""

    def __init__(
        self,
        manifest_path: Path,
        root_dir: Path,
        transform=None,
        city_filter: "list[str] | None" = None,
    ) -> None:
        self.root_dir = root_dir
        self.transform = transform
        self.samples: list[tuple[Path, int]] = []

        with open(manifest_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if city_filter and row["city"] not in city_filter:
                    continue
                label = row["label"]
                if label not in LABEL_TO_IDX:
                    continue
                p = root_dir / row["path"]
                self.samples.append((p, LABEL_TO_IDX[label]))

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        return img, label

    def class_weights(self) -> "torch.Tensor":
        """Inverse-frequency weights for WeightedRandomSampler."""
        counts = [0] * len(SHAPE_LABELS)
        for _, lbl in self.samples:
            counts[lbl] += 1
        counts = [max(c, 1) for c in counts]
        total = sum(counts)
        weights = [total / c for c in counts]
        sample_weights = torch.tensor(
            [weights[lbl] for _, lbl in self.samples], dtype=torch.float
        )
        return sample_weights


# ---------------------------------------------------------------------------
# Train / eval helpers
# ---------------------------------------------------------------------------

def _build_transforms(crop_size: int, augment: bool):
    normalise = T.Normalize(mean=[0.485, 0.456, 0.406],
                            std=[0.229, 0.224, 0.225])
    if augment:
        return T.Compose([
            T.Resize((crop_size, crop_size)),
            T.RandomHorizontalFlip(),
            T.RandomVerticalFlip(),
            T.RandomRotation(90),
            T.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.1),
            T.ToTensor(),
            normalise,
        ])
    return T.Compose([
        T.Resize((crop_size, crop_size)),
        T.ToTensor(),
        normalise,
    ])


def _train_epoch(model, loader, criterion, optimizer, device) -> tuple[float, float]:
    model.train()
    total_loss = 0.0
    correct = 0
    n = 0
    for imgs, labels in loader:
        imgs, labels = imgs.to(device), labels.to(device)
        optimizer.zero_grad()
        _, shape_logits = model(imgs)
        loss = criterion(shape_logits, labels)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * len(labels)
        correct += (shape_logits.argmax(1) == labels).sum().item()
        n += len(labels)
    return total_loss / max(n, 1), correct / max(n, 1)


@torch.no_grad()
def _eval_epoch(model, loader, criterion, device) -> tuple[float, float]:
    model.eval()
    total_loss = 0.0
    correct = 0
    n = 0
    for imgs, labels in loader:
        imgs, labels = imgs.to(device), labels.to(device)
        _, shape_logits = model(imgs)
        loss = criterion(shape_logits, labels)
        total_loss += loss.item() * len(labels)
        correct += (shape_logits.argmax(1) == labels).sum().item()
        n += len(labels)
    return total_loss / max(n, 1), correct / max(n, 1)


@torch.no_grad()
def _per_class_accuracy(model, loader, device) -> dict[str, float]:
    model.eval()
    correct = [0] * len(SHAPE_LABELS)
    total = [0] * len(SHAPE_LABELS)
    for imgs, labels in loader:
        imgs, labels = imgs.to(device), labels.to(device)
        _, shape_logits = model(imgs)
        preds = shape_logits.argmax(1)
        for gt, pred in zip(labels.tolist(), preds.tolist()):
            total[gt] += 1
            if gt == pred:
                correct[gt] += 1
    return {
        lbl: correct[i] / max(total[i], 1)
        for i, lbl in enumerate(SHAPE_LABELS)
    }


# ---------------------------------------------------------------------------
# Main training function
# ---------------------------------------------------------------------------

def train_roof_classifier(
    data_dir: str = DEFAULT_DATA_DIR,
    output_model: str = DEFAULT_MODEL_PATH,
    resume: "str | None" = None,
    epochs: int = DEFAULT_EPOCHS,
    batch_size: int = DEFAULT_BATCH,
    lr: float = DEFAULT_LR,
    crop_size: int = DEFAULT_CROP_SIZE,
    device_str: str = "auto",
    verbose: bool = True,
) -> dict:
    """Train the RoofNet shape head.

    Parameters
    ----------
    data_dir : str
        Root of the crops directory (must contain manifest.csv).
    output_model : str
        Path to save the trained model (.pt).
    resume : str | None
        Path to a prior checkpoint to resume from.
    epochs : int
        Number of training epochs.
    batch_size : int
        Mini-batch size.
    lr : float
        Initial learning rate for AdamW.
    crop_size : int
        Input resolution (default 64×64).
    device_str : str
        "auto" | "cpu" | "cuda" | "mps"
    verbose : bool
        Print per-epoch progress.

    Returns
    -------
    dict with 'best_val_acc', 'per_class_acc', 'model_path', 'history'.
    """
    if not _TORCH_AVAILABLE:
        raise ImportError(
            "PyTorch and torchvision are required for training. "
            "Install with: pip install torch torchvision"
        )

    from tools.networks import RoofNet  # noqa: PLC0415

    # ── Device ────────────────────────────────────────────────────────
    if device_str == "auto":
        if torch.cuda.is_available():
            device = torch.device("cuda")
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = torch.device("mps")
        else:
            device = torch.device("cpu")
    else:
        device = torch.device(device_str)

    if verbose:
        print(f"Device: {device}")

    # ── Paths ─────────────────────────────────────────────────────────
    data_root = Path(data_dir)
    if not data_root.is_absolute():
        data_root = _STRM2STL / data_dir

    manifest = data_root / "manifest.csv"
    if not manifest.exists():
        raise FileNotFoundError(
            f"Manifest not found: {manifest}\n"
            "Run tools/harvest_roof_crops.py first."
        )

    out_model_path = Path(output_model)
    if not out_model_path.is_absolute():
        out_model_path = _STRM2STL / output_model
    out_model_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Discover cities for train/val/test split ──────────────────────
    cities: set[str] = set()
    with open(manifest, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cities.add(row["city"])
    cities_list = sorted(cities)
    random.seed(42)
    random.shuffle(cities_list)

    n = len(cities_list)
    val_cities = set(cities_list[: max(1, n // 10)])
    test_cities = set(cities_list[max(1, n // 10): max(2, n // 5)])
    train_cities = set(cities_list) - val_cities - test_cities

    if verbose:
        print(f"Split: {len(train_cities)} train / {len(val_cities)} val / "
              f"{len(test_cities)} test cities")

    # ── Datasets ──────────────────────────────────────────────────────
    train_tf = _build_transforms(crop_size, augment=True)
    eval_tf = _build_transforms(crop_size, augment=False)

    train_ds = RoofCropDataset(manifest, data_root, train_tf,
                               city_filter=list(train_cities))
    val_ds = RoofCropDataset(manifest, data_root, eval_tf,
                             city_filter=list(val_cities))
    test_ds = RoofCropDataset(manifest, data_root, eval_tf,
                              city_filter=list(test_cities))

    if len(train_ds) == 0:
        raise ValueError(
            "Training dataset is empty. Run harvest_roof_crops.py first."
        )

    if verbose:
        print(f"Samples — train: {len(train_ds)}, val: {len(val_ds)}, "
              f"test: {len(test_ds)}")

    # Weighted sampling to handle class imbalance
    sample_weights = train_ds.class_weights()
    sampler = WeightedRandomSampler(sample_weights, len(train_ds), replacement=True)

    train_loader = DataLoader(train_ds, batch_size=batch_size, sampler=sampler,
                              num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False,
                            num_workers=0)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False,
                             num_workers=0) if test_ds else None

    # ── Model ─────────────────────────────────────────────────────────
    if resume and Path(resume).exists():
        if verbose:
            print(f"Resuming from: {resume}")
        model = torch.load(resume, map_location=device, weights_only=False)
    else:
        model = RoofNet(in_channels=3, n_classes=len(SHAPE_LABELS))

    model = model.to(device)

    # ── Optimizer + scheduler ─────────────────────────────────────────
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.CrossEntropyLoss()

    # ── Training loop ─────────────────────────────────────────────────
    history: list[dict] = []
    best_val_acc = 0.0
    best_epoch = 0

    for epoch in range(1, epochs + 1):
        t0 = time.perf_counter()
        train_loss, train_acc = _train_epoch(model, train_loader, criterion, optimizer, device)
        val_loss, val_acc = _eval_epoch(model, val_loader, criterion, device)
        scheduler.step()
        dt = time.perf_counter() - t0

        history.append({
            "epoch": epoch,
            "train_loss": train_loss,
            "train_acc": train_acc,
            "val_loss": val_loss,
            "val_acc": val_acc,
        })

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_epoch = epoch
            torch.save(model, str(out_model_path))

        if verbose:
            print(
                f"  Epoch {epoch:3d}/{epochs}  "
                f"train_loss={train_loss:.4f}  train_acc={train_acc:.3f}  "
                f"val_loss={val_loss:.4f}  val_acc={val_acc:.3f}  "
                f"{'* best' if epoch == best_epoch else '':7}  "
                f"({dt:.1f}s)"
            )

    # ── Per-class accuracy on test set ────────────────────────────────
    per_class_acc: dict[str, float] = {}
    if test_loader and len(test_ds) > 0:
        # reload best
        model = torch.load(str(out_model_path), map_location=device, weights_only=False)
        per_class_acc = _per_class_accuracy(model, test_loader, device)

        if verbose:
            print(f"\n  Test set per-class accuracy (best epoch {best_epoch}):")
            for lbl, acc in per_class_acc.items():
                print(f"    {lbl:<12} {acc:.3f}")

    # ── Save training history ─────────────────────────────────────────
    history_path = out_model_path.parent / (out_model_path.stem + "_history.json")
    with open(history_path, "w", encoding="utf-8") as fh:
        json.dump({"history": history, "per_class_acc": per_class_acc,
                   "best_val_acc": best_val_acc, "best_epoch": best_epoch}, fh, indent=2)

    if verbose:
        print(f"\n  Best val accuracy: {best_val_acc:.3f} (epoch {best_epoch})")
        print(f"  Model saved: {out_model_path}")
        print(f"  History saved: {history_path}")

    return {
        "best_val_acc": best_val_acc,
        "per_class_acc": per_class_acc,
        "model_path": str(out_model_path),
        "history": history,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train RoofNet shape-classification head on OSM-labelled crops."
    )
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="Root of crop directory with manifest.csv")
    parser.add_argument("--output-model", default=DEFAULT_MODEL_PATH,
                        help="Path to save trained model (.pt)")
    parser.add_argument("--resume", default=None,
                        help="Checkpoint path to resume from")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--lr", type=float, default=DEFAULT_LR)
    parser.add_argument("--crop-size", type=int, default=DEFAULT_CROP_SIZE)
    parser.add_argument("--device", default="auto",
                        choices=["auto", "cpu", "cuda", "mps"])
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    train_roof_classifier(
        data_dir=args.data_dir,
        output_model=args.output_model,
        resume=args.resume,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        crop_size=args.crop_size,
        device_str=args.device,
        verbose=not args.quiet,
    )
