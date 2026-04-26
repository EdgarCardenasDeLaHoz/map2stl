"""
tools/train_pseudo_ndsm.py — Train the RoofNet height-regression head on
paired (RGB satellite, nDSM) tiles.

Data sources
------------
The script expects a dataset of paired tiles previously downloaded by a
separate data-prep step.  Each sample is a pair of:

  - RGB tile : HxWx3 uint8 PNG  (satellite imagery, any GSD)
  - nDSM tile: HxW  float32 GeoTIFF  (normalised digital surface model
                                       — heights above terrain in metres)

The dataset directory layout expected::

    <data_dir>/
        train/
            rgb/   <sample_id>.png
            ndsm/  <sample_id>.tif
        val/
            rgb/   ...
            ndsm/  ...
        test/
            rgb/   ...
            ndsm/  ...

You can also point --data-dir at a flat manifest CSV with columns:
    split, rgb_path, ndsm_path

Open LiDAR sources for building the dataset:
  - Netherlands AHN4 (CC0): https://ahn.arcgisonline.nl/
  - UK DEFRA DSM (OGL v3): https://environment.data.gov.uk/DefraDataDownload/
  - US 3DEP 1m (public domain): https://www.usgs.gov/3d-elevation-program
  - Austria Vienna LiDAR (CC BY 4.0): https://www.data.gv.at/

Usage
-----
    # minimal — CPU, default paths:
    ..\\..venv\\Scripts\\python.exe -m tools.train_pseudo_ndsm

    # GPU with custom paths:
    ..\\..venv\\Scripts\\python.exe -m tools.train_pseudo_ndsm \\
        --data-dir data/ndsm_tiles \\
        --output-model models/roofnet_height_v1.pt \\
        --epochs 60 --batch-size 8 --lr 1e-4

    # resume:
    ..\\..venv\\Scripts\\python.exe -m tools.train_pseudo_ndsm \\
        --resume models/roofnet_height_v1.pt --epochs 20
"""

from __future__ import annotations

import argparse
import csv
import json
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
    from torch.utils.data import DataLoader, Dataset
    import torchvision.transforms as T
    import torchvision.transforms.functional as TF
    from PIL import Image
    import numpy as np
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False

print(_TORCH_AVAILABLE)
# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_DATA_DIR = "data/ndsm_tiles"
DEFAULT_MODEL_PATH = "models/roofnet_height_v1.pt"
DEFAULT_EPOCHS = 1000
DEFAULT_BATCH = 8
DEFAULT_LR = 1e-4
DEFAULT_TILE_SIZE = 256   # larger tiles for the dense height head
MAX_HEIGHT_M = 50.0       # heights above this are clipped to 1.0 when normalised


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class NdsmDataset(Dataset):
    """Paired RGB + nDSM tile dataset.

    Supports two layouts:
    1. Directory layout (train/rgb/, train/ndsm/)
    2. Manifest CSV (split, rgb_path, ndsm_path)
    """

    def __init__(
        self,
        data_dir: Path,
        split: str = "train",
        tile_size: int = DEFAULT_TILE_SIZE,
        augment: bool = False,
        manifest_csv: "Path | None" = None,
    ) -> None:
        self.tile_size = tile_size
        self.augment = augment
        self.samples: list[tuple[Path, Path]] = []

        if manifest_csv and manifest_csv.exists():
            with open(manifest_csv, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if row.get("split") != split:
                        continue
                    rgb_p = Path(row["rgb_path"])
                    ndsm_p = Path(row["ndsm_path"])
                    if rgb_p.exists() and ndsm_p.exists():
                        self.samples.append((rgb_p, ndsm_p))
        else:
            rgb_dir = data_dir / split / "rgb"
            ndsm_dir = data_dir / split / "ndsm"
            if rgb_dir.exists() and ndsm_dir.exists():
                for rgb_path in sorted(rgb_dir.glob("*.png")):
                    ndsm_path = ndsm_dir / rgb_path.with_suffix(".tif").name
                    if ndsm_path.exists():
                        self.samples.append((rgb_path, ndsm_path))

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        rgb_path, ndsm_path = self.samples[idx]

        # ── Load RGB ──────────────────────────────────────────────────
        img = Image.open(rgb_path).convert("RGB")
        img = img.resize((self.tile_size, self.tile_size), Image.BILINEAR)

        # ── Load nDSM ─────────────────────────────────────────────────
        try:
            import rasterio
            with rasterio.open(ndsm_path) as src:
                ndsm = src.read(1).astype(np.float32)
        except ImportError:
            # Fallback: load as numpy if saved as .npy
            ndsm = np.load(str(ndsm_path)).astype(np.float32)

        # Resize nDSM to match tile_size
        from PIL import Image as PILImage
        ndsm_img = PILImage.fromarray(ndsm, mode="F")
        ndsm_img = ndsm_img.resize((self.tile_size, self.tile_size), PILImage.BILINEAR)
        ndsm = np.array(ndsm_img, dtype=np.float32)

        # Normalise: clip to [0, MAX_HEIGHT_M], divide
        ndsm = np.clip(ndsm, 0.0, MAX_HEIGHT_M) / MAX_HEIGHT_M

        # ── Augmentation ──────────────────────────────────────────────
        if self.augment:
            img_t = TF.to_tensor(img)
            ndsm_t = torch.from_numpy(ndsm).unsqueeze(0)

            if torch.rand(1) < 0.5:
                img_t = TF.hflip(img_t)
                ndsm_t = TF.hflip(ndsm_t)
            if torch.rand(1) < 0.5:
                img_t = TF.vflip(img_t)
                ndsm_t = TF.vflip(ndsm_t)
            k = torch.randint(0, 4, (1,)).item()
            img_t = torch.rot90(img_t, k, [1, 2])
            ndsm_t = torch.rot90(ndsm_t, k, [1, 2])

            img_t = T.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225])(img_t)
            return img_t, ndsm_t
        else:
            img_t = T.Compose([
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406],
                            std=[0.229, 0.224, 0.225]),
            ])(img)
            ndsm_t = torch.from_numpy(ndsm).unsqueeze(0)
            return img_t, ndsm_t


# ---------------------------------------------------------------------------
# Train / eval helpers
# ---------------------------------------------------------------------------

def _train_epoch(model, loader, criterion, optimizer, device) -> tuple[float, float]:
    model.train()
    total_loss = 0.0
    total_mae = 0.0
    n = 0
    for imgs, ndsm_targets in loader:
        imgs = imgs.to(device)
        ndsm_targets = ndsm_targets.to(device)
        optimizer.zero_grad()
        height_map, _ = model(imgs)
        loss = criterion(height_map, ndsm_targets)
        loss.backward()
        optimizer.step()

        mae = (height_map.detach() - ndsm_targets).abs().mean().item()
        total_loss += loss.item() * len(imgs)
        total_mae += mae * len(imgs)
        n += len(imgs)
    return total_loss / max(n, 1), total_mae * MAX_HEIGHT_M / max(n, 1)


@torch.no_grad()
def _eval_epoch(model, loader, criterion, device) -> tuple[float, float]:
    model.eval()
    total_loss = 0.0
    total_rmse_sq = 0.0
    n = 0
    for imgs, ndsm_targets in loader:
        imgs = imgs.to(device)
        ndsm_targets = ndsm_targets.to(device)
        height_map, _ = model(imgs)
        loss = criterion(height_map, ndsm_targets)
        rmse_sq = ((height_map - ndsm_targets) ** 2).mean().item()
        total_loss += loss.item() * len(imgs)
        total_rmse_sq += rmse_sq * len(imgs)
        n += len(imgs)
    rmse_m = (total_rmse_sq / max(n, 1)) ** 0.5 * MAX_HEIGHT_M
    return total_loss / max(n, 1), rmse_m


# ---------------------------------------------------------------------------
# Main training function
# ---------------------------------------------------------------------------

def train_pseudo_ndsm(
    data_dir: str = DEFAULT_DATA_DIR,
    output_model: str = DEFAULT_MODEL_PATH,
    resume: "str | None" = None,
    epochs: int = DEFAULT_EPOCHS,
    batch_size: int = DEFAULT_BATCH,
    lr: float = DEFAULT_LR,
    tile_size: int = DEFAULT_TILE_SIZE,
    device_str: str = "auto",
    verbose: bool = True,
) -> dict:
    """Train the RoofNet height-regression head.

    Parameters
    ----------
    data_dir : str
        Root of the tile dataset (must contain train/rgb/, train/ndsm/).
    output_model : str
        Path to save the trained model (.pt).
    resume : str | None
        Path to a prior checkpoint to resume from.
    epochs : int
        Training epochs.
    batch_size : int
        Mini-batch size (keep small for large tiles).
    lr : float
        Initial AdamW learning rate.
    tile_size : int
        Input resolution (default 256×256 for dense height prediction).
    device_str : str
        "auto" | "cpu" | "cuda" | "mps"
    verbose : bool
        Print per-epoch progress.

    Returns
    -------
    dict with 'best_val_rmse_m', 'model_path', 'history'.
    """
    if not _TORCH_AVAILABLE:
        raise ImportError(
            "PyTorch and torchvision are required. "
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

    if not data_root.exists():
        raise FileNotFoundError(
            f"Data directory not found: {data_root}\n"
            "Download paired RGB + nDSM tiles before training.\n"
            "See docs/roof-ml-architecture.md §3.1 for data sources."
        )

    out_model_path = Path(output_model)
    if not out_model_path.is_absolute():
        out_model_path = _STRM2STL / output_model
    out_model_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Datasets ──────────────────────────────────────────────────────
    train_ds = NdsmDataset(data_root, "train", tile_size, augment=True)
    val_ds = NdsmDataset(data_root, "val", tile_size, augment=False)
    test_ds = NdsmDataset(data_root, "test", tile_size, augment=False)

    if len(train_ds) == 0:
        raise ValueError(
            f"Training set is empty in {data_root / 'train'}.\n"
            "Expected sub-directories rgb/ and ndsm/ with matching .png / .tif files."
        )

    if verbose:
        print(f"Samples — train: {len(train_ds)}, val: {len(val_ds)}, "
              f"test: {len(test_ds)}")

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,
                              num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False,
                            num_workers=0)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False,
                             num_workers=0) if len(test_ds) > 0 else None

    # ── Model ─────────────────────────────────────────────────────────
    if resume and Path(resume).exists():
        if verbose:
            print(f"Resuming from: {resume}")
        model = torch.load(resume, map_location=device, weights_only=False)
    else:
        # Use wider channels for tile-level height prediction
        model = RoofNet(
            in_channels=3,
            n_classes=6,
            hidden_channels=[64, 128, 256, 256],
        )

    model = model.to(device)

    # ── Optimizer + scheduler ─────────────────────────────────────────
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    # SmoothL1 (Huber loss) — robust to noisy LiDAR / registration errors
    criterion = nn.SmoothL1Loss()

    # ── Training loop ─────────────────────────────────────────────────
    history: list[dict] = []
    best_val_rmse = float("inf")
    best_epoch = 0

    for epoch in range(1, epochs + 1):
        t0 = time.perf_counter()
        train_loss, train_mae = _train_epoch(model, train_loader, criterion,
                                             optimizer, device)
        val_loss, val_rmse = _eval_epoch(model, val_loader, criterion, device)
        scheduler.step()
        dt = time.perf_counter() - t0

        history.append({
            "epoch": epoch,
            "train_loss": train_loss,
            "train_mae_m": train_mae,
            "val_loss": val_loss,
            "val_rmse_m": val_rmse,
        })

        is_best = val_rmse < best_val_rmse
        if is_best:
            best_val_rmse = val_rmse
            best_epoch = epoch
            torch.save(model, str(out_model_path))

        if verbose:
            print(
                f"  Epoch {epoch:3d}/{epochs}  "
                f"train_loss={train_loss:.4f}  train_MAE={train_mae:.2f}m  "
                f"val_loss={val_loss:.4f}  val_RMSE={val_rmse:.2f}m  "
                f"{'* best' if is_best else '':7}  ({dt:.1f}s)"
            )

    # ── Test RMSE ─────────────────────────────────────────────────────
    test_rmse: float | None = None
    if test_loader:
        best_model = torch.load(str(out_model_path), map_location=device,
                                weights_only=False)
        _, test_rmse = _eval_epoch(best_model, test_loader, criterion, device)
        if verbose:
            print(f"\n  Test RMSE: {test_rmse:.2f} m  (best epoch {best_epoch})")

    # ── Save history ──────────────────────────────────────────────────
    history_path = out_model_path.parent / (out_model_path.stem + "_history.json")
    with open(history_path, "w", encoding="utf-8") as fh:
        json.dump({
            "history": history,
            "best_val_rmse_m": best_val_rmse,
            "test_rmse_m": test_rmse,
            "best_epoch": best_epoch,
        }, fh, indent=2)

    if verbose:
        print(f"  Best val RMSE: {best_val_rmse:.2f} m (epoch {best_epoch})")
        print(f"  Model saved: {out_model_path}")

    return {
        "best_val_rmse_m": best_val_rmse,
        "test_rmse_m": test_rmse,
        "model_path": str(out_model_path),
        "history": history,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train RoofNet height-regression head on paired RGB + nDSM tiles."
    )
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="Root tile directory (must have train/rgb, train/ndsm)")
    parser.add_argument("--output-model", default=DEFAULT_MODEL_PATH,
                        help="Path to save trained model (.pt)")
    parser.add_argument("--resume", default=None,
                        help="Checkpoint path to resume from")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--lr", type=float, default=DEFAULT_LR)
    parser.add_argument("--tile-size", type=int, default=DEFAULT_TILE_SIZE)
    parser.add_argument("--device", default="auto",
                        choices=["auto", "cpu", "cuda", "mps"])
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    train_pseudo_ndsm(
        data_dir=args.data_dir,
        output_model=args.output_model,
        resume=args.resume,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        tile_size=args.tile_size,
        device_str=args.device,
        verbose=not args.quiet,
    )
