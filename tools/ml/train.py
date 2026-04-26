"""
tools.ml.train — Unified training loop for building height + roof shape.

Supports three training modes:
  - "shape"  : train shape classification head only (CrossEntropy)
  - "height" : train height regression head only (L1 + Sobel gradient)
  - "both"   : joint training with weighted multi-task loss

Features:
  - Early stopping on validation metric
  - Cosine annealing with warm-up
  - Automatic backbone freeze/unfreeze schedule
  - Checkpoint saving (best + periodic)
  - Full training history saved as JSON

Usage (CLI):
    python -m tools.ml.train --task shape --data-dir output/roof_crops --epochs 60
    python -m tools.ml.train --task height --tile-dir cache/height_tiles --epochs 50

Usage (Python):
    from tools.ml.train import train_shape, train_height
    result = train_shape(data_dir="output/roof_crops", epochs=60)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from tools.ml.config import (
    SHAPE_LABELS,
    DEFAULT_CROP_SIZE,
    DEFAULT_TILE_SIZE,
    DEFAULT_EPOCHS,
    DEFAULT_BATCH_SIZE,
    DEFAULT_LR,
    DEFAULT_WEIGHT_DECAY,
    EARLY_STOP_PATIENCE,
    DEFAULT_SHAPE_MODEL,
    DEFAULT_HEIGHT_MODEL,
    MAX_HEIGHT_M,
)

logger = logging.getLogger(__name__)

_TORCH_AVAILABLE = False
try:
    import torch
    import torch.nn as nn
    _TORCH_AVAILABLE = True
except ImportError:
    pass


def _require_torch():
    if not _TORCH_AVAILABLE:
        raise ImportError("PyTorch required. Install: pip install torch torchvision")


# ---------------------------------------------------------------------------
# Training config
# ---------------------------------------------------------------------------

@dataclass
class TrainConfig:
    """Training hyperparameters."""
    task: str = "shape"             # "shape", "height", or "both"
    epochs: int = DEFAULT_EPOCHS
    batch_size: int = DEFAULT_BATCH_SIZE
    lr: float = DEFAULT_LR
    weight_decay: float = DEFAULT_WEIGHT_DECAY
    patience: int = EARLY_STOP_PATIENCE
    crop_size: int = DEFAULT_CROP_SIZE
    tile_size: int = DEFAULT_TILE_SIZE
    device: str = "auto"
    num_workers: int = 0
    seed: int = 42
    # Backbone schedule: freeze for first N epochs, then unfreeze
    freeze_backbone_epochs: int = 5
    # Multi-task loss weights (for task="both")
    shape_loss_weight: float = 1.0
    height_loss_weight: float = 1.0
    # Gradient loss weight for height regression
    grad_loss_weight: float = 0.5
    # Checkpoint frequency
    save_every: int = 10


# ---------------------------------------------------------------------------
# Loss functions
# ---------------------------------------------------------------------------

def _gradient_loss(pred: "torch.Tensor", target: "torch.Tensor") -> "torch.Tensor":
    """Sobel gradient loss for height maps."""
    import torch.nn.functional as F

    sobel_x = torch.tensor(
        [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=torch.float32
    ).view(1, 1, 3, 3).to(pred.device)
    sobel_y = torch.tensor(
        [[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=torch.float32
    ).view(1, 1, 3, 3).to(pred.device)

    def _grad(t):
        gx = F.conv2d(t, sobel_x, padding=1)
        gy = F.conv2d(t, sobel_y, padding=1)
        return gx, gy

    pgx, pgy = _grad(pred)
    tgx, tgy = _grad(target)
    return F.l1_loss(pgx, tgx) + F.l1_loss(pgy, tgy)


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def _resolve_device(device_str: str) -> "torch.device":
    if device_str == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    return torch.device(device_str)


def _train_epoch_shape(model, loader, criterion, optimizer, device):
    """One epoch of shape-only training."""
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
        nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        optimizer.step()
        total_loss += loss.item() * len(labels)
        correct += (shape_logits.argmax(1) == labels).sum().item()
        n += len(labels)
    return total_loss / max(n, 1), correct / max(n, 1)


@torch.no_grad()
def _eval_epoch_shape(model, loader, criterion, device):
    """One epoch of shape-only evaluation."""
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


def _height_loss(pred, target, grad_weight: float, bldg_weight: float = 5.0):
    """Per-pixel weighted L1 + Sobel gradient loss for sparse height maps.

    Background pixels (target == 0) make up ~70% of urban tiles. A naive L1
    average lets the model collapse to all-zeros and still get a respectable
    loss. We upweight building pixels (target > 0) by `bldg_weight` so the
    gradient signal is dominated by what we actually care about.
    """
    import torch.nn.functional as F
    bldg_mask = (target > 0).float()
    weight = 1.0 + bldg_weight * bldg_mask
    l1 = (weight * (pred - target).abs()).sum() / weight.sum().clamp(min=1.0)
    gl = _gradient_loss(pred, target)
    return l1 + grad_weight * gl


# ---------------------------------------------------------------------------
# Dice + BCE losses for the segmentation mask head and continuous-overlap
# regularizer on the height regression output.
# ---------------------------------------------------------------------------

def _dice_loss_binary(logits, target_mask, smooth: float = 1.0):
    """Soft Dice loss on binary segmentation logits.

    logits      : B x 1 x H x W raw (apply sigmoid inside)
    target_mask : B x 1 x H x W in {0, 1}
    """
    p = torch.sigmoid(logits)
    p = p.flatten(1)
    t = target_mask.flatten(1).float()
    inter = (p * t).sum(dim=1)
    denom = p.sum(dim=1) + t.sum(dim=1)
    dice = (2 * inter + smooth) / (denom + smooth)
    return (1.0 - dice).mean()


def _dice_loss_continuous(pred, target, smooth: float = 1.0):
    """Continuous Dice/Tversky-style overlap regulariser for height regression.

    Treats the values themselves as the "amount of building" per pixel.
    Encourages the model to put height energy in the right pixels even
    when the magnitude is wrong, complementing the per-pixel L1 term.
    """
    p = torch.clamp(pred, min=0.0).flatten(1)
    t = target.flatten(1)
    inter = (p * t).sum(dim=1)
    denom = (p * p).sum(dim=1) + (t * t).sum(dim=1)
    dice = (2 * inter + smooth) / (denom + smooth)
    return (1.0 - dice).mean()


def _v3_loss(
    mask_logits, height_pred, target_height,
    grad_weight: float = 0.5,
    bldg_weight: float = 5.0,
    bce_weight: float = 1.0,
    dice_weight: float = 1.0,
    height_dice_weight: float = 0.5,
):
    """Multi-component loss for RoofNetV3.

    L = weighted_L1(height) + grad * Sobel(height)
      + bce * BCE(mask, target>0) + dice * Dice(mask, target>0)
      + height_dice * ContinuousDice(height, target)
    """
    import torch.nn.functional as F
    target_mask = (target_height > 0).float()

    # Height regression: weighted L1 + Sobel gradient + continuous Dice
    weight = 1.0 + bldg_weight * target_mask
    l1 = (weight * (height_pred - target_height).abs()).sum() / weight.sum().clamp(min=1.0)
    gl = _gradient_loss(height_pred, target_height)
    h_dice = _dice_loss_continuous(height_pred, target_height)

    # Mask segmentation: BCE + Dice
    bce = F.binary_cross_entropy_with_logits(mask_logits, target_mask)
    m_dice = _dice_loss_binary(mask_logits, target_mask)

    return l1 + grad_weight * gl + bce_weight * bce + dice_weight * m_dice + height_dice_weight * h_dice


def _train_epoch_height(model, loader, optimizer, device, grad_weight: float):
    """One epoch of height-only training."""
    model.train()
    total_loss = 0.0
    n = 0
    for rgb, height in loader:
        rgb, height = rgb.to(device), height.to(device)
        optimizer.zero_grad()
        height_pred, _ = model(rgb)
        loss = _height_loss(height_pred, height, grad_weight)
        loss.backward()
        #nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        optimizer.step()
        total_loss += loss.item() * rgb.size(0)
        n += rgb.size(0)
    return total_loss / max(n, 1)


@torch.no_grad()
def _eval_epoch_height(model, loader, device, grad_weight: float):
    """One epoch of height-only evaluation."""
    model.eval()
    total_loss = 0.0
    n = 0
    for rgb, height in loader:
        rgb, height = rgb.to(device), height.to(device)
        height_pred, _ = model(rgb)
        loss = _height_loss(height_pred, height, grad_weight)
        total_loss += loss.item() * rgb.size(0)
        n += rgb.size(0)
    return total_loss / max(n, 1)


# ---------------------------------------------------------------------------
# RoofNetV3 train / eval (iterative refinement, mask + height heads)
# ---------------------------------------------------------------------------

def _train_epoch_v3(model, loader, optimizer, device, n_iters: int, grad_weight: float):
    """One epoch of RoofNetV3 training with deep supervision across iters."""
    model.train()
    total_loss = 0.0
    total_mae = 0.0
    n = 0
    for rgb, height in loader:
        rgb, height = rgb.to(device), height.to(device)
        optimizer.zero_grad()

        outs = model(rgb, n_iters=n_iters, return_all=True)
        # Deep supervision: weight later iterations more heavily
        loss = 0.0
        for it, (mask_logits, height_pred, _) in enumerate(outs):
            w = (it + 1) / len(outs)  # 0.5, 1.0 for n_iters=2
            loss = loss + w * _v3_loss(mask_logits, height_pred, height, grad_weight)

        # Final-iteration MAE on building pixels for monitoring
        mask_logits, height_pred, _ = outs[-1]
        mask_prob = torch.sigmoid(mask_logits)
        gated = torch.clamp(height_pred, min=0.0) * (mask_prob > 0.3).float()
        bldg = (height > 0).float()
        mae = (bldg * (gated - height).abs()).sum() / bldg.sum().clamp(min=1.0)

        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        optimizer.step()
        total_loss += loss.item() * rgb.size(0)
        total_mae += mae.item() * rgb.size(0)
        n += rgb.size(0)
    return total_loss / max(n, 1), total_mae / max(n, 1)


@torch.no_grad()
def _eval_epoch_v3(model, loader, device, n_iters: int, grad_weight: float):
    model.eval()
    total_loss = 0.0
    total_mae = 0.0
    n = 0
    for rgb, height in loader:
        rgb, height = rgb.to(device), height.to(device)
        mask_logits, height_pred, _ = model(rgb, n_iters=n_iters)
        loss = _v3_loss(mask_logits, height_pred, height, grad_weight)

        mask_prob = torch.sigmoid(mask_logits)
        gated = torch.clamp(height_pred, min=0.0) * (mask_prob > 0.3).float()
        bldg = (height > 0).float()
        mae = (bldg * (gated - height).abs()).sum() / bldg.sum().clamp(min=1.0)

        total_loss += loss.item() * rgb.size(0)
        total_mae += mae.item() * rgb.size(0)
        n += rgb.size(0)
    return total_loss / max(n, 1), total_mae / max(n, 1)


def train_v3(
    tile_dir: str = "cache/height_tiles_osm",
    output_model: str = "models/roofnet_v3.pt",
    config: "TrainConfig | None" = None,
    n_iters: int = 2,
    arch: str = "v3",
    verbose: bool = True,
) -> dict:
    """Train RoofNetV3 with iterative refinement + mask/height/Dice losses.

    Parameters
    ----------
    tile_dir : Tile directory.
    output_model : Output checkpoint path.
    config : Training config.
    n_iters : Refinement iterations per forward pass.  Default 2.
    """
    _require_torch()
    from tools.ml.models import build_model
    from tools.ml.data import make_height_loaders

    cfg = config or TrainConfig(task="height")
    device = _resolve_device(cfg.device)

    strm2stl = Path(__file__).resolve().parents[2]
    tile_root = Path(tile_dir)
    if not tile_root.is_absolute():
        tile_root = strm2stl / tile_dir

    tile_paths = sorted(tile_root.glob("*.npz"))
    if not tile_paths:
        raise FileNotFoundError(f"No tiles in {tile_root}")

    out_path = Path(output_model)
    if not out_path.is_absolute():
        out_path = strm2stl / output_model
    out_path.parent.mkdir(parents=True, exist_ok=True)

    train_loader, val_loader, split = make_height_loaders(
        tile_paths,
        tile_size=cfg.tile_size,
        batch_size=cfg.batch_size,
        seed=cfg.seed,
        num_workers=cfg.num_workers,
    )

    if verbose:
        print(f"Tiles: {split['n_train']} train / {split['n_val']} val")

    # v3s: backbone starts frozen, unfreezes after freeze_backbone_epochs
    is_small = arch in ("v3s", "v3_s", "v3-s")
    model = build_model(arch=arch, task="both", pretrained=True, device=str(device))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    if verbose:
        print(f"{arch.upper()}: {total:,} total params ({trainable:,} trainable), "
              f"{n_iters} iter(s) per forward")

    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=cfg.lr, weight_decay=cfg.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.epochs, eta_min=cfg.lr * 0.01,
    )

    history = []
    best_val = float("inf")
    best_epoch = 0
    patience_counter = 0

    for epoch in range(1, cfg.epochs + 1):
        # Unfreeze backbone after warmup (v3s: backbone frozen initially)
        if is_small and epoch == cfg.freeze_backbone_epochs + 1:
            if hasattr(model, "unfreeze_backbone"):
                model.unfreeze_backbone()
                # Rebuild optimizer to include newly unfrozen backbone params
                optimizer = torch.optim.AdamW(
                    model.parameters(),
                    lr=cfg.lr * 0.1,  # lower LR for fine-tuning backbone
                    weight_decay=cfg.weight_decay,
                )
                scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                    optimizer,
                    T_max=max(cfg.epochs - epoch, 1),
                    eta_min=cfg.lr * 0.001,
                )
                if verbose:
                    print(f"  [epoch {epoch}] Backbone unfrozen, lr={cfg.lr * 0.1:.1e}")

        t0 = time.perf_counter()
        train_loss, train_mae = _train_epoch_v3(
            model, train_loader, optimizer, device, n_iters, cfg.grad_loss_weight,
        )
        val_loss, val_mae = _eval_epoch_v3(
            model, val_loader, device, n_iters, cfg.grad_loss_weight,
        )
        scheduler.step()
        dt = time.perf_counter() - t0

        history.append({
            "epoch": epoch,
            "train_loss": train_loss, "train_mae": train_mae,
            "val_loss": val_loss, "val_mae": val_mae,
            "lr": scheduler.get_last_lr()[0],
        })

        is_best = val_loss < best_val
        if is_best:
            best_val = val_loss
            best_epoch = epoch
            patience_counter = 0
            torch.save({
                "epoch": epoch,
                "arch": arch,
                "n_iters": n_iters,
                "model_state_dict": model.state_dict(),
                "val_loss": val_loss, "val_mae": val_mae,
                "config": cfg.__dict__,
            }, str(out_path))
        else:
            patience_counter += 1

        if verbose:
            marker = "* best" if is_best else ""
            print(
                f"  Epoch {epoch:3d}/{cfg.epochs}  "
                f"train_loss={train_loss:.3f} train_mae={train_mae:.2f}m  "
                f"val_loss={val_loss:.3f} val_mae={val_mae:.2f}m  "
                f"{marker:7}  ({dt:.1f}s)"
            )

        if patience_counter >= cfg.patience:
            if verbose:
                print(f"  Early stopping at epoch {epoch}")
            break

    history_path = out_path.parent / (out_path.stem + "_history.json")
    with open(history_path, "w", encoding="utf-8") as fh:
        json.dump({
            "history": history,
            "best_val_loss": best_val,
            "best_epoch": best_epoch,
            "n_iters": n_iters,
            "arch": arch,
        }, fh, indent=2)

    if verbose:
        print(f"\n  Best val loss: {best_val:.4f} (epoch {best_epoch})")
        print(f"  Model: {out_path}")

    return {
        "best_val_loss": best_val,
        "best_epoch": best_epoch,
        "model_path": str(out_path),
        "history": history,
    }


# ---------------------------------------------------------------------------
# Shape training entry point
# ---------------------------------------------------------------------------

def train_shape(
    data_dir: str = "output/roof_crops",
    output_model: str = DEFAULT_SHAPE_MODEL,
    config: "TrainConfig | None" = None,
    verbose: bool = True,
) -> dict:
    """Train the RoofNetV2 shape classification head.

    Parameters
    ----------
    data_dir : Root of crop directory with manifest.csv.
    output_model : Path to save best model checkpoint.
    config : Training config (defaults constructed if None).
    verbose : Print per-epoch progress.

    Returns
    -------
    dict with best_val_acc, best_epoch, per_class_acc, model_path, history.
    """
    _require_torch()
    from tools.ml.models import build_model
    from tools.ml.data import make_roof_loaders

    cfg = config or TrainConfig(task="shape")
    device = _resolve_device(cfg.device)

    if verbose:
        print(f"Device: {device}")

    strm2stl = Path(__file__).resolve().parents[2]
    data_root = Path(data_dir)
    if not data_root.is_absolute():
        data_root = strm2stl / data_dir

    manifest = data_root / "manifest.csv"
    if not manifest.exists():
        raise FileNotFoundError(
            f"Manifest not found: {manifest}\n"
            "Run: python -m tools.ml.data harvest  (or harvest_roof_crops.py)"
        )

    out_path = Path(output_model)
    if not out_path.is_absolute():
        out_path = strm2stl / output_model
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Data
    train_loader, val_loader, test_loader, split_info = make_roof_loaders(
        manifest, data_root,
        crop_size=cfg.crop_size,
        batch_size=cfg.batch_size,
        num_workers=cfg.num_workers,
        seed=cfg.seed,
    )

    if verbose:
        print(f"Split: {split_info['n_train']} train / {split_info['n_val']} val / {split_info['n_test']} test")
        print(f"Train distribution: {split_info['train_distribution']}")

    # Model — start with frozen backbone
    model = build_model(
        task="shape",
        pretrained=True,
        freeze_backbone=(cfg.freeze_backbone_epochs > 0),
        device=str(device),
    )

    if verbose:
        print(f"Model: RoofNetV2 ({model.total_params():,} params, {model.trainable_params():,} trainable)")

    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=cfg.lr, weight_decay=cfg.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.epochs, eta_min=cfg.lr * 0.01
    )
    criterion = nn.CrossEntropyLoss()

    # Training loop
    history: list[dict] = []
    best_val_acc = 0.0
    best_epoch = 0
    patience_counter = 0

    for epoch in range(1, cfg.epochs + 1):
        # Unfreeze backbone after warmup
        if epoch == cfg.freeze_backbone_epochs + 1:
            model.unfreeze_backbone()
            # Rebuild optimizer with all params
            optimizer = torch.optim.AdamW(
                model.parameters(),
                lr=cfg.lr * 0.1,  # lower LR for backbone
                weight_decay=cfg.weight_decay,
            )
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                optimizer, T_max=cfg.epochs - epoch + 1, eta_min=cfg.lr * 0.001
            )
            if verbose:
                print(f"  Epoch {epoch}: unfreezing backbone ({model.trainable_params():,} trainable)")

        t0 = time.perf_counter()
        train_loss, train_acc = _train_epoch_shape(model, train_loader, criterion, optimizer, device)
        val_loss, val_acc = _eval_epoch_shape(model, val_loader, criterion, device)
        scheduler.step()
        dt = time.perf_counter() - t0

        history.append({
            "epoch": epoch,
            "train_loss": train_loss,
            "train_acc": train_acc,
            "val_loss": val_loss,
            "val_acc": val_acc,
            "lr": scheduler.get_last_lr()[0],
        })

        is_best = val_acc > best_val_acc
        if is_best:
            best_val_acc = val_acc
            best_epoch = epoch
            patience_counter = 0
            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_acc": val_acc,
                "val_loss": val_loss,
                "config": cfg.__dict__,
            }, str(out_path))
        else:
            patience_counter += 1

        if verbose:
            marker = "* best" if is_best else ""
            print(
                f"  Epoch {epoch:3d}/{cfg.epochs}  "
                f"train_loss={train_loss:.4f}  train_acc={train_acc:.3f}  "
                f"val_loss={val_loss:.4f}  val_acc={val_acc:.3f}  "
                f"{marker:7}  ({dt:.1f}s)"
            )

        # Early stopping
        if patience_counter >= cfg.patience:
            if verbose:
                print(f"  Early stopping at epoch {epoch} (no improvement for {cfg.patience} epochs)")
            break

        # Periodic checkpoint
        if epoch % cfg.save_every == 0:
            interim = out_path.with_suffix(f".epoch{epoch:03d}.pt")
            torch.save({"epoch": epoch, "model_state_dict": model.state_dict()}, str(interim))

    # Per-class accuracy on test set
    per_class_acc: dict[str, float] = {}
    if test_loader:
        state = torch.load(str(out_path), map_location=device, weights_only=True)
        model.load_state_dict(state["model_state_dict"])
        per_class_acc = _per_class_accuracy(model, test_loader, device)

        if verbose:
            print(f"\n  Test per-class accuracy (best epoch {best_epoch}):")
            for lbl, acc in per_class_acc.items():
                print(f"    {lbl:<12} {acc:.3f}")

    # Save history
    history_path = out_path.parent / (out_path.stem + "_history.json")
    with open(history_path, "w", encoding="utf-8") as fh:
        json.dump({
            "history": history,
            "per_class_acc": per_class_acc,
            "best_val_acc": best_val_acc,
            "best_epoch": best_epoch,
            "split_info": split_info,
            "config": {
                "task": cfg.task, "epochs": cfg.epochs, "batch_size": cfg.batch_size,
                "lr": cfg.lr, "crop_size": cfg.crop_size, "patience": cfg.patience,
                "freeze_backbone_epochs": cfg.freeze_backbone_epochs,
            },
        }, fh, indent=2)

    if verbose:
        print(f"\n  Best val accuracy: {best_val_acc:.3f} (epoch {best_epoch})")
        print(f"  Model: {out_path}")
        print(f"  History: {history_path}")

    return {
        "best_val_acc": best_val_acc,
        "best_epoch": best_epoch,
        "per_class_acc": per_class_acc,
        "model_path": str(out_path),
        "history": history,
    }


# ---------------------------------------------------------------------------
# Height training entry point
# ---------------------------------------------------------------------------

def train_height(
    tile_dir: str = "cache/height_tiles",
    output_model: str = DEFAULT_HEIGHT_MODEL,
    config: "TrainConfig | None" = None,
    verbose: bool = True,
) -> dict:
    """Train the RoofNetV2 height regression head.

    Parameters
    ----------
    tile_dir : Directory containing .npz tile files.
    output_model : Path to save best checkpoint.
    config : Training config (defaults constructed if None).
    verbose : Print per-epoch progress.

    Returns
    -------
    dict with best_val_loss, model_path, history.
    """
    _require_torch()
    from tools.ml.models import build_model
    from tools.ml.data import make_height_loaders

    cfg = config or TrainConfig(task="height")
    device = _resolve_device(cfg.device)

    strm2stl = Path(__file__).resolve().parents[2]
    tile_root = Path(tile_dir)
    if not tile_root.is_absolute():
        tile_root = strm2stl / tile_dir

    tile_paths = sorted(tile_root.glob("*.npz"))
    if not tile_paths:
        raise FileNotFoundError(f"No .npz tiles found in {tile_root}")

    out_path = Path(output_model)
    if not out_path.is_absolute():
        out_path = strm2stl / output_model
    out_path.parent.mkdir(parents=True, exist_ok=True)

    train_loader, val_loader, split_info = make_height_loaders(
        tile_paths,
        tile_size=cfg.tile_size,
        batch_size=cfg.batch_size,
        seed=cfg.seed,
        num_workers=cfg.num_workers,
    )

    if verbose:
        print(f"Tiles: {split_info['n_train']} train / {split_info['n_val']} val")

    model = build_model(task="height", pretrained=True, device=str(device))
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.epochs, eta_min=cfg.lr * 0.01
    )

    history: list[dict] = []
    best_val_loss = float("inf")
    best_epoch = 0
    patience_counter = 0

    for epoch in range(1, cfg.epochs + 1):
        t0 = time.perf_counter()
        train_loss = _train_epoch_height(model, train_loader, optimizer, device, cfg.grad_loss_weight)
        val_loss = _eval_epoch_height(model, val_loader, device, cfg.grad_loss_weight)
        scheduler.step()
        dt = time.perf_counter() - t0

        history.append({
            "epoch": epoch,
            "train_loss": train_loss,
            "val_loss": val_loss,
            "lr": scheduler.get_last_lr()[0],
        })

        is_best = val_loss < best_val_loss
        if is_best:
            best_val_loss = val_loss
            best_epoch = epoch
            patience_counter = 0
            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "val_loss": val_loss,
                "config": cfg.__dict__,
            }, str(out_path))
        else:
            patience_counter += 1

        if verbose:
            marker = "* best" if is_best else ""
            print(
                f"  Epoch {epoch:3d}/{cfg.epochs}  "
                f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
                f"{marker:7}  ({dt:.1f}s)"
            )

        if patience_counter >= cfg.patience:
            if verbose:
                print(f"  Early stopping at epoch {epoch}")
            break

    history_path = out_path.parent / (out_path.stem + "_history.json")
    with open(history_path, "w", encoding="utf-8") as fh:
        json.dump({
            "history": history,
            "best_val_loss": best_val_loss,
            "best_epoch": best_epoch,
        }, fh, indent=2)

    if verbose:
        print(f"\n  Best val loss: {best_val_loss:.4f} (epoch {best_epoch})")
        print(f"  Model: {out_path}")

    return {
        "best_val_loss": best_val_loss,
        "best_epoch": best_epoch,
        "model_path": str(out_path),
        "history": history,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Train RoofNetV2")
    parser.add_argument("--task", choices=["shape", "height"], default="shape")
    parser.add_argument("--data-dir", default="output/roof_crops",
                        help="Roof crop directory (for --task shape)")
    parser.add_argument("--tile-dir", default="cache/height_tiles",
                        help="Height tile directory (for --task height)")
    parser.add_argument("--output-model", default=None,
                        help="Output model path (auto-selected by task if omitted)")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=DEFAULT_LR)
    parser.add_argument("--crop-size", type=int, default=DEFAULT_CROP_SIZE)
    parser.add_argument("--patience", type=int, default=EARLY_STOP_PATIENCE)
    parser.add_argument("--freeze-backbone-epochs", type=int, default=5)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cfg = TrainConfig(
        task=args.task,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        crop_size=args.crop_size,
        patience=args.patience,
        freeze_backbone_epochs=args.freeze_backbone_epochs,
        device=args.device,
    )

    output = args.output_model
    if output is None:
        output = DEFAULT_SHAPE_MODEL if args.task == "shape" else DEFAULT_HEIGHT_MODEL

    if args.task == "shape":
        train_shape(data_dir=args.data_dir, output_model=output, config=cfg, verbose=not args.quiet)
    else:
        train_height(tile_dir=args.tile_dir, output_model=output, config=cfg, verbose=not args.quiet)


if __name__ == "__main__":
    main()
