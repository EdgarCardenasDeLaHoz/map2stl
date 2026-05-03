"""
tools.ml.train â€” Unified training loop for building height + roof shape.

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
    # Gradient loss weight for height regression (edge regularizer)
    grad_loss_weight: float = 0.2
    # Extra pixelwise L2 weight blended with weighted L1 (encourages sharp pixel fit)
    pixel_l2_weight: float = 0.25
    # Pixel regression objective: "l1", "rmse", or "hybrid"
    pixel_loss_mode: str = "hybrid"
    # Max gradient norm for clipping (0 = disabled)
    grad_clip_norm: float = 1.0
    # Building-pixel upweighting factor for sparse height supervision
    bldg_loss_weight: float = 5.0
    # Checkpoint frequency
    save_every: int = 10
    # If True, run backward/step for each image (sample-level SGD updates)
    per_image_backprop: bool = True
    # Metric used for selecting best checkpoints: "loss", "mae", or "rmse"
    selection_metric: str = "rmse"
    # Resume from latest checkpoint (or resume_from if provided)
    resume: bool = False
    # Optional explicit checkpoint path to resume from
    resume_from: Optional[str] = None
    # If True, also load scheduler state when resuming
    resume_scheduler: bool = True


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


def _load_history(history_path: Path) -> list[dict]:
    if not history_path.exists():
        return []
    try:
        with open(history_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        hist = payload.get("history", [])
        return hist if isinstance(hist, list) else []
    except Exception:
        return []


def _train_epoch_shape(model, loader, criterion, optimizer, device, per_image_backprop: bool = True):
    """One epoch of shape-only training."""
    model.train()
    total_loss = 0.0
    correct = 0
    n = 0
    for imgs, labels in loader:
        imgs, labels = imgs.to(device), labels.to(device)
        if per_image_backprop:
            for i in range(imgs.size(0)):
                x = imgs[i:i + 1]
                y = labels[i:i + 1]
                optimizer.zero_grad()
                _, shape_logits = model(x)
                loss = criterion(shape_logits, y)
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                total_loss += loss.item()
                correct += (shape_logits.argmax(1) == y).sum().item()
                n += 1
        else:
            optimizer.zero_grad()
            _, shape_logits = model(imgs)
            loss = criterion(shape_logits, labels)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
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


def _height_loss(
    pred,
    target,
    grad_weight: float,
    bldg_weight: float = 5.0,
    pixel_l2_weight: float = 0.25,
    pixel_loss_mode: str = "hybrid",
):
    """Per-pixel weighted L1 + Sobel gradient loss for sparse height maps.

    Background pixels (target == 0) make up ~70% of urban tiles. A naive L1
    average lets the model collapse to all-zeros and still get a respectable
    loss. We upweight building pixels (target > 0) by `bldg_weight` so the
    gradient signal is dominated by what we actually care about.
    """
    import torch.nn.functional as F
    bldg_mask = (target > 0).float()
    weight = 1.0 + bldg_weight * bldg_mask
    diff = pred - target
    l1 = (weight * diff.abs()).sum() / weight.sum().clamp(min=1.0)
    mse = (weight * (diff * diff)).sum() / weight.sum().clamp(min=1.0)
    rmse = torch.sqrt(mse + 1e-8)

    mode = (pixel_loss_mode or "hybrid").lower()
    if mode == "l1":
        pixel = l1
    elif mode == "rmse":
        pixel = rmse
    else:
        # Hybrid keeps MAE robustness while adding stronger penalty to large errors.
        pixel = l1 + pixel_l2_weight * rmse

    gl = _gradient_loss(pred, target)
    return pixel + grad_weight * gl


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


def _height_iou_loss(pred, target, smooth: float = 1.0):
    """Height-overlap IoU loss for regression.

    Treats height values as 1-D intervals rooted at 0.  For each pixel pair
    (p, t) both â‰¥ 0, IoU = min(p,t) / max(p,t).  Summing over building
    pixels encourages the model to simultaneously predict the correct
    locations AND magnitudes, without requiring exact pixel accuracy.

    This is complementary to L1 (which only penalises magnitude error):
    if the model predicts 20m where the truth is 10m, L1 = 10 but
    IoU-loss captures that the overlap is only 50%.

    Returns 1 - mean_IoU so that 0 = perfect and 1 = no overlap.
    """
    bldg = (target > 0)
    if not bldg.any():
        return pred.sum() * 0.0  # differentiable zero
    p = pred[bldg].clamp(min=0.0)
    t = target[bldg].clamp(min=0.0)
    inter = torch.minimum(p, t)
    union = torch.maximum(p, t)
    iou = (inter.sum() + smooth) / (union.sum() + smooth)
    return 1.0 - iou


def _v3_loss(
    mask_logits, height_pred, target_height,
    grad_weight: float = 0.5,
    bldg_weight: float = 5.0,
    pixel_l2_weight: float = 0.25,
    pixel_loss_mode: str = "hybrid",
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
    diff = height_pred - target_height
    l1 = (weight * diff.abs()).sum() / weight.sum().clamp(min=1.0)
    mse = (weight * (diff * diff)).sum() / weight.sum().clamp(min=1.0)
    rmse = torch.sqrt(mse + 1e-8)

    mode = (pixel_loss_mode or "hybrid").lower()
    if mode == "l1":
        pixel = l1
    elif mode == "rmse":
        pixel = rmse
    else:
        pixel = l1 + pixel_l2_weight * rmse

    gl = _gradient_loss(height_pred, target_height)
    h_dice = _dice_loss_continuous(height_pred, target_height)

    # Mask segmentation: BCE + Dice
    bce = F.binary_cross_entropy_with_logits(mask_logits, target_mask)
    m_dice = _dice_loss_binary(mask_logits, target_mask)

    return pixel + grad_weight * gl + bce_weight * bce + dice_weight * m_dice + height_dice_weight * h_dice


def _train_epoch_height(
    model,
    loader,
    optimizer,
    device,
    grad_weight: float,
    bldg_weight: float,
    pixel_l2_weight: float,
    pixel_loss_mode: str,
    per_image_backprop: bool = True,
    grad_clip_norm: float = 1.0,
):
    """One epoch of height-only training."""
    model.train()
    total_loss = 0.0
    n = 0
    for rgb, height in loader:
        rgb, height = rgb.to(device), height.to(device)
        if per_image_backprop:
            for i in range(rgb.size(0)):
                x = rgb[i:i + 1]
                y = height[i:i + 1]
                optimizer.zero_grad()
                height_pred, _ = model(x)
                loss = _height_loss(
                    height_pred,
                    y,
                    grad_weight,
                    bldg_weight=bldg_weight,
                    pixel_l2_weight=pixel_l2_weight,
                    pixel_loss_mode=pixel_loss_mode,
                )
                loss.backward()
                if grad_clip_norm > 0:
                    nn.utils.clip_grad_norm_(model.parameters(), grad_clip_norm)
                optimizer.step()
                total_loss += loss.item()
                n += 1
        else:
            optimizer.zero_grad()
            height_pred, _ = model(rgb)
            loss = _height_loss(
                height_pred,
                height,
                grad_weight,
                bldg_weight=bldg_weight,
                pixel_l2_weight=pixel_l2_weight,
                pixel_loss_mode=pixel_loss_mode,
            )
            loss.backward()
            if grad_clip_norm > 0:
                nn.utils.clip_grad_norm_(model.parameters(), grad_clip_norm)
            optimizer.step()
            total_loss += loss.item() * rgb.size(0)
            n += rgb.size(0)
    return total_loss / max(n, 1)


@torch.no_grad()
def _eval_epoch_height(
    model,
    loader,
    device,
    grad_weight: float,
    bldg_weight: float,
    pixel_l2_weight: float,
    pixel_loss_mode: str,
):
    """One epoch of height-only evaluation."""
    model.eval()
    total_loss = 0.0
    total_abs = 0.0
    total_sq = 0.0
    total_px = 0
    n = 0
    for rgb, height in loader:
        rgb, height = rgb.to(device), height.to(device)
        height_pred, _ = model(rgb)
        loss = _height_loss(
            height_pred,
            height,
            grad_weight,
            bldg_weight=bldg_weight,
            pixel_l2_weight=pixel_l2_weight,
            pixel_loss_mode=pixel_loss_mode,
        )
        total_loss += loss.item() * rgb.size(0)
        diff = (height_pred - height)
        total_abs += float(diff.abs().sum().item())
        total_sq += float((diff * diff).sum().item())
        total_px += int(diff.numel())
        n += rgb.size(0)
    mae = total_abs / max(total_px, 1)
    rmse = float(np.sqrt(total_sq / max(total_px, 1)))
    return total_loss / max(n, 1), mae, rmse


# ---------------------------------------------------------------------------
# RoofNetV3 train / eval (iterative refinement, mask + height heads)
# ---------------------------------------------------------------------------

def _train_epoch_v3(
    model,
    loader,
    optimizer,
    device,
    n_iters: int,
    grad_weight: float,
    bldg_weight: float,
    pixel_l2_weight: float,
    pixel_loss_mode: str,
    per_image_backprop: bool = True,
    grad_clip_norm: float = 1.0,
):
    """One epoch of RoofNetV3 training with deep supervision across iters."""
    model.train()
    total_loss = 0.0
    total_mae = 0.0
    n = 0
    for rgb, height in loader:
        rgb, height = rgb.to(device), height.to(device)
        if per_image_backprop:
            for i in range(rgb.size(0)):
                x = rgb[i:i + 1]
                y = height[i:i + 1]
                optimizer.zero_grad()

                outs = model(x, n_iters=n_iters, return_all=True)
                # Deep supervision: weight later iterations more heavily
                loss = 0.0
                for it, (mask_logits, height_pred, _) in enumerate(outs):
                    w = (it + 1) / len(outs)  # 0.5, 1.0 for n_iters=2
                    loss = loss + w * _v3_loss(
                        mask_logits,
                        height_pred,
                        y,
                        grad_weight,
                        bldg_weight=bldg_weight,
                        pixel_l2_weight=pixel_l2_weight,
                        pixel_loss_mode=pixel_loss_mode,
                    )

                # Final-iteration MAE on building pixels for monitoring
                mask_logits, height_pred, _ = outs[-1]
                mask_prob = torch.sigmoid(mask_logits)
                gated = torch.clamp(height_pred, min=0.0) * (mask_prob > 0.3).float()
                bldg = (y > 0).float()
                mae = (bldg * (gated - y).abs()).sum() / bldg.sum().clamp(min=1.0)

                loss.backward()
                if grad_clip_norm > 0:
                    nn.utils.clip_grad_norm_(model.parameters(), grad_clip_norm)
                optimizer.step()
                total_loss += loss.item()
                total_mae += mae.item()
                n += 1
        else:
            optimizer.zero_grad()

            outs = model(rgb, n_iters=n_iters, return_all=True)
            # Deep supervision: weight later iterations more heavily
            loss = 0.0
            for it, (mask_logits, height_pred, _) in enumerate(outs):
                w = (it + 1) / len(outs)  # 0.5, 1.0 for n_iters=2
                loss = loss + w * _v3_loss(
                    mask_logits,
                    height_pred,
                    height,
                    grad_weight,
                    bldg_weight=bldg_weight,
                    pixel_l2_weight=pixel_l2_weight,
                    pixel_loss_mode=pixel_loss_mode,
                )

            # Final-iteration MAE on building pixels for monitoring
            mask_logits, height_pred, _ = outs[-1]
            mask_prob = torch.sigmoid(mask_logits)
            gated = torch.clamp(height_pred, min=0.0) * (mask_prob > 0.3).float()
            bldg = (height > 0).float()
            mae = (bldg * (gated - height).abs()).sum() / bldg.sum().clamp(min=1.0)

            loss.backward()
            if grad_clip_norm > 0:
                nn.utils.clip_grad_norm_(model.parameters(), grad_clip_norm)
            optimizer.step()
            total_loss += loss.item() * rgb.size(0)
            total_mae += mae.item() * rgb.size(0)
            n += rgb.size(0)
    return total_loss / max(n, 1), total_mae / max(n, 1)


@torch.no_grad()
def _eval_epoch_v3(
    model,
    loader,
    device,
    n_iters: int,
    grad_weight: float,
    bldg_weight: float,
    pixel_l2_weight: float,
    pixel_loss_mode: str,
):
    """Return (loss, mae_metres, mask_iou, mask_acc, rmse_metres).

    All metrics computed on the mask-gated prediction so they reflect what an
    inference user would see.
    """
    model.eval()
    total_loss = 0.0
    total_mae = 0.0
    total_sq = 0.0
    total_iou_num = 0.0
    total_iou_den = 0.0
    total_correct = 0
    total_pixels = 0
    n = 0
    for rgb, height in loader:
        rgb, height = rgb.to(device), height.to(device)
        mask_logits, height_pred, _ = model(rgb, n_iters=n_iters)
        loss = _v3_loss(
            mask_logits,
            height_pred,
            height,
            grad_weight,
            bldg_weight=bldg_weight,
            pixel_l2_weight=pixel_l2_weight,
            pixel_loss_mode=pixel_loss_mode,
        )

        mask_prob = torch.sigmoid(mask_logits)
        pred_mask = (mask_prob > 0.5).float()
        gated = torch.clamp(height_pred, min=0.0) * (mask_prob > 0.3).float()
        target_mask = (height > 0).float()
        bldg_pixels = target_mask.sum().clamp(min=1.0)

        mae_pixels = (target_mask * (gated - height).abs()).sum()
        sq_pixels = (target_mask * (gated - height).pow(2)).sum()

        # Per-pixel mask IoU (numerator/denominator accumulated across batch)
        intersection = (pred_mask * target_mask).sum()
        union = ((pred_mask + target_mask).clamp(max=1.0)).sum()

        # Per-pixel mask accuracy
        correct = (pred_mask == target_mask).sum()

        bs = rgb.size(0)
        total_loss += loss.item() * bs
        total_mae += (mae_pixels / bldg_pixels).item() * bs
        total_sq += (sq_pixels / bldg_pixels).item() * bs
        total_iou_num += intersection.item()
        total_iou_den += union.item()
        total_correct += correct.item()
        total_pixels += pred_mask.numel()
        n += bs

    nn = max(n, 1)
    iou = total_iou_num / max(total_iou_den, 1.0)
    mask_acc = total_correct / max(total_pixels, 1)
    rmse = (total_sq / nn) ** 0.5
    return total_loss / nn, total_mae / nn, iou, mask_acc, rmse


def _unet_loss(
    mask_logits, height_pred, target_height,
    grad_weight: float = 0.2,
    bldg_weight: float = 5.0,
    pixel_l2_weight: float = 0.25,
    pixel_loss_mode: str = "hybrid",
    mask_weight: float = 1.0,
    iou_weight: float = 0.5,
):
    """Combined loss for HeightUNet dual-head output.

    mask_weight * (BCE + Dice) on the segmentation head
    + _height_loss on the height head (weighted L1 + Sobel gradient)
    + iou_weight * _height_iou_loss (height-overlap IoU encourages correct
      location AND magnitude simultaneously)

    Segmentation is an easier task than height regression â€” the mask head
    converges in the first few epochs and provides a clean building/background
    signal that bootstraps the height head's learning.
    """
    target_mask = (target_height > 0).float()
    h_loss = _height_loss(height_pred, target_height, grad_weight, bldg_weight,
                          pixel_l2_weight, pixel_loss_mode)
    iou_l  = _height_iou_loss(height_pred, target_height)
    bce    = torch.nn.functional.binary_cross_entropy_with_logits(mask_logits, target_mask)
    m_dice = _dice_loss_binary(mask_logits, target_mask)
    return h_loss + iou_weight * iou_l + mask_weight * (bce + m_dice)


def train_unet(
    tile_dir: str = "cache/height_tiles_osm",
    output_model: str = "models/roofnet_unet.pt",
    config: "TrainConfig | None" = None,
    verbose: bool = True,
) -> dict:
    """Train HeightUNet â€” U-Net with shared decoder, mask head + height head.

    The mask head (building/non-building segmentation) provides a strong,
    easy-to-learn signal from the start, even on tiles with noisy height labels.
    The height head is supervised only on building pixels (bldg_weight upweighting).
    At inference the height output is gated by the predicted mask.

    Returns the same result dict as ``train_v3``.
    """
    _require_torch()
    from tools.ml.models import HeightUNet
    from tools.ml.data.datasets import make_height_loaders

    cfg = config or TrainConfig()
    device = torch.device(
        "cuda" if cfg.device == "auto" and torch.cuda.is_available()
        else ("cpu" if cfg.device == "auto" else cfg.device)
    )

    strm2stl = Path(__file__).resolve().parents[2]
    tile_path = Path(tile_dir) if Path(tile_dir).is_absolute() else strm2stl / tile_dir
    out_path  = Path(output_model) if Path(output_model).is_absolute() else strm2stl / output_model
    out_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path = out_path.with_suffix(".latest.pt")

    tile_paths = sorted(tile_path.glob("*.npz"))
    if not tile_paths:
        raise FileNotFoundError(f"No .npz tiles found in {tile_path}")

    torch.manual_seed(cfg.seed)
    train_loader, val_loader, split = make_height_loaders(
        tile_paths, tile_size=cfg.tile_size,
        batch_size=cfg.batch_size, num_workers=cfg.num_workers,
    )

    model = HeightUNet(pretrained=True, freeze_backbone=True).to(device)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total     = sum(p.numel() for p in model.parameters())
    if verbose:
        print(f"HeightUNet: {total:,} total params ({trainable:,} trainable)")
        print(f"Tiles: {split['n_train']} train / {split['n_val']} val")

    backbone_ids = set(id(p) for p in model.backbone.parameters())
    dec_params = [p for p in model.parameters() if id(p) not in backbone_ids]
    bb_params  = [p for p in model.backbone.parameters()]
    optimizer = torch.optim.AdamW(
        [{"params": dec_params, "lr": cfg.lr},
         {"params": bb_params,  "lr": cfg.lr * 0.1}],
        lr=cfg.lr, weight_decay=cfg.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.epochs, eta_min=cfg.lr * 0.01,
    )

    history_path = out_path.parent / (out_path.stem + "_history.json")
    history = _load_history(history_path)
    best_val = float("inf")
    best_selection = float("inf")
    best_epoch = 0
    patience_counter = 0
    start_epoch = 1

    resume_ckpt: Path | None = None
    if cfg.resume_from:
        candidate = Path(cfg.resume_from)
        if not candidate.is_absolute():
            candidate = strm2stl / candidate
        if candidate.exists():
            resume_ckpt = candidate
    elif cfg.resume and latest_path.exists():
        resume_ckpt = latest_path

    if resume_ckpt is not None:
        state = torch.load(str(resume_ckpt), map_location=device, weights_only=False)
        sd = state.get("model_state_dict", state) if isinstance(state, dict) else state
        model.load_state_dict(sd, strict=False)
        if isinstance(state, dict):
            try: optimizer.load_state_dict(state["optimizer_state_dict"])
            except Exception: pass
            if cfg.resume_scheduler:
                try: scheduler.load_state_dict(state["scheduler_state_dict"])
                except Exception: pass
            prev_epoch = int(state.get("epoch", 0) or 0)
            start_epoch = prev_epoch + 1
            best_val = float(state.get("best_val_loss", best_val))
            best_epoch = int(state.get("best_epoch", prev_epoch))
        if verbose:
            print(f"Resuming from {resume_ckpt} (start epoch {start_epoch})")

    backbone_unfrozen = False
    end_epoch = start_epoch + cfg.epochs - 1

    for epoch in range(start_epoch, end_epoch + 1):
        if not backbone_unfrozen and epoch > cfg.freeze_backbone_epochs:
            model.unfreeze_backbone()
            backbone_unfrozen = True
            if verbose:
                print(f"  [epoch {epoch}] Backbone unfrozen")

        model.train()
        t0 = time.perf_counter()
        total_loss, total_mae, n = 0.0, 0.0, 0

        for rgb, height in train_loader:
            rgb, height = rgb.to(device), height.to(device)
            optimizer.zero_grad()
            mask_logits, height_pred = model(rgb)
            loss = _unet_loss(mask_logits, height_pred, height,
                              cfg.grad_loss_weight, cfg.bldg_loss_weight,
                              cfg.pixel_l2_weight, cfg.pixel_loss_mode)
            loss.backward()
            if cfg.grad_clip_norm > 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip_norm)
            optimizer.step()
            with torch.no_grad():
                bldg = (height > 0)
                mae = (height_pred - height).abs()[bldg].mean() if bldg.any() else torch.tensor(0.0)
            total_loss += loss.item() * rgb.size(0)
            total_mae  += mae.item() * rgb.size(0)
            n += rgb.size(0)

        train_loss = total_loss / max(n, 1)
        train_mae  = total_mae  / max(n, 1)

        # Validation
        model.eval()
        val_loss, val_mae_sum, val_sq_sum, val_n = 0.0, 0.0, 0.0, 0
        with torch.no_grad():
            for rgb, height in val_loader:
                rgb, height = rgb.to(device), height.to(device)
                mask_logits, height_pred = model(rgb)
                loss = _unet_loss(mask_logits, height_pred, height,
                                  cfg.grad_loss_weight, cfg.bldg_loss_weight,
                                  cfg.pixel_l2_weight, cfg.pixel_loss_mode)
                bldg = (height > 0)
                if bldg.any():
                    errs = (height_pred - height).abs()[bldg]
                    val_mae_sum += errs.sum().item()
                    val_sq_sum  += (errs ** 2).sum().item()
                    val_n       += bldg.sum().item()
                val_loss += loss.item() * rgb.size(0)
            val_loss /= max(len(val_loader.dataset), 1)

        val_mae  = val_mae_sum  / max(val_n, 1)
        val_rmse = (val_sq_sum  / max(val_n, 1)) ** 0.5

        scheduler.step()
        dt = time.perf_counter() - t0

        metric = cfg.selection_metric.lower()
        sel_val = val_rmse if metric == "rmse" else (val_mae if metric == "mae" else val_loss)

        history.append({
            "epoch": epoch, "arch": "unet",
            "train_loss": train_loss, "train_mae": train_mae,
            "val_loss": val_loss, "val_mae": val_mae, "val_rmse": val_rmse,
            "lr": scheduler.get_last_lr()[0],
        })

        is_best = sel_val < best_selection
        latest_state = {
            "epoch": epoch, "arch": "unet",
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict(),
            "val_loss": val_loss, "val_mae": val_mae, "val_rmse": val_rmse,
            "best_val_loss": best_val, "best_epoch": best_epoch,
            "config": cfg.__dict__,
        }
        torch.save(latest_state, str(latest_path))

        if is_best:
            best_val = val_loss
            best_selection = sel_val
            best_epoch = epoch
            patience_counter = 0
            latest_state.update({"best_val_loss": best_val, "best_epoch": best_epoch})
            torch.save(latest_state, str(out_path))
        else:
            patience_counter += 1

        with open(history_path, "w", encoding="utf-8") as fh:
            json.dump({
                "history": history,
                "best_val_loss": best_val,
                "best_selection_value": best_selection,
                "selection_metric": cfg.selection_metric,
                "best_epoch": best_epoch,
            }, fh, indent=2)

        if verbose:
            marker = "* best" if is_best else ""
            print(f"  Epoch {epoch:3d}/{end_epoch}  "
                  f"train={train_loss:.3f} mae={train_mae:.2f}m  "
                  f"val={val_loss:.3f} mae={val_mae:.2f}m rmse={val_rmse:.2f}m  "
                  f"{marker:7}  ({dt:.1f}s)")

        if patience_counter >= cfg.patience:
            if verbose:
                print(f"  Early stopping at epoch {epoch}")
            break

    return {
        "best_val_loss": best_val,
        "best_epoch": best_epoch,
        "model_path": str(out_path),
        "history": history,
        "arch": "unet",
    }


def _make_v3_optimizer(model, lr: float, weight_decay: float, backbone_frozen: bool = False):
    """AdamW with per-group LRs: FPN 3Ã—, heads 5Ã—, backbone 0.1Ã— (or excluded when frozen)."""
    decoder_ids = set()
    for name in ("fpn", "mask_head", "height_head", "scratchpad"):
        mod = getattr(model, name, None)
        if mod is not None:
            decoder_ids.update(id(p) for p in mod.parameters())

    fpn_params, head_params, backbone_params = [], [], []
    for name, p in model.named_parameters():
        if not p.requires_grad:
            continue
        if id(p) in decoder_ids:
            if "fpn" in name:
                fpn_params.append(p)
            else:
                head_params.append(p)
        else:
            backbone_params.append(p)

    groups = [
        {"params": fpn_params,      "lr": lr * 3.0},
        {"params": head_params,     "lr": lr * 5.0},
    ]
    if backbone_params and not backbone_frozen:
        groups.append({"params": backbone_params, "lr": lr * 0.1})

    return torch.optim.AdamW(groups, lr=lr, weight_decay=weight_decay)


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
    from tools.ml.data.datasets import make_height_loaders

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
    latest_path = out_path.with_suffix(".latest.pt")

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

    optimizer = _make_v3_optimizer(model, cfg.lr, cfg.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.epochs, eta_min=cfg.lr * 0.01,
    )

    history_path = out_path.parent / (out_path.stem + "_history.json")
    history = _load_history(history_path)
    best_val = float("inf")
    best_epoch = 0
    patience_counter = 0
    start_epoch = 1

    resume_ckpt: Path | None = None
    if cfg.resume_from:
        candidate = Path(cfg.resume_from)
        if not candidate.is_absolute():
            candidate = strm2stl / candidate
        if candidate.exists():
            resume_ckpt = candidate
    elif cfg.resume:
        if latest_path.exists():
            resume_ckpt = latest_path
        elif out_path.exists():
            resume_ckpt = out_path

    if resume_ckpt is not None:
        state = torch.load(str(resume_ckpt), map_location=device, weights_only=False)
        if isinstance(state, dict) and "model_state_dict" in state:
            model.load_state_dict(state["model_state_dict"])
        elif isinstance(state, dict):
            model.load_state_dict(state)

        if isinstance(state, dict) and "optimizer_state_dict" in state:
            try:
                optimizer.load_state_dict(state["optimizer_state_dict"])
            except Exception:
                pass
        if cfg.resume_scheduler and isinstance(state, dict) and "scheduler_state_dict" in state:
            try:
                scheduler.load_state_dict(state["scheduler_state_dict"])
            except Exception:
                pass

        if isinstance(state, dict):
            prev_epoch = int(state.get("epoch", 0) or 0)
            start_epoch = prev_epoch + 1
            best_val = float(state.get("best_val_loss", state.get("val_loss", best_val)))
            best_epoch = int(state.get("best_epoch", prev_epoch if prev_epoch > 0 else 0))

        if verbose:
            print(f"Resuming from {resume_ckpt} (start epoch {start_epoch})")

    end_epoch = start_epoch + cfg.epochs - 1
    for epoch in range(start_epoch, end_epoch + 1):
        # Unfreeze backbone after warmup (v3s: backbone frozen initially)
        if is_small and epoch == cfg.freeze_backbone_epochs + 1:
            if hasattr(model, "unfreeze_backbone"):
                model.unfreeze_backbone()
                # Rebuild optimizer with per-group LRs; backbone at 0.1Ã— base
                optimizer = _make_v3_optimizer(
                    model, cfg.lr * 0.1, cfg.weight_decay, backbone_frozen=False
                )
                scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                    optimizer,
                    T_max=max(cfg.epochs - epoch, 1),
                    eta_min=cfg.lr * 0.001,
                )
                if verbose:
                    print(f"  [epoch {epoch}] Backbone unfrozen, "
                          f"backbone lr={cfg.lr * 0.01:.1e}, "
                          f"fpn lr={cfg.lr * 0.3:.1e}, "
                          f"heads lr={cfg.lr * 0.5:.1e}")

        t0 = time.perf_counter()
        train_loss, train_mae = _train_epoch_v3(
            model,
            train_loader,
            optimizer,
            device,
            n_iters,
            cfg.grad_loss_weight,
            cfg.bldg_loss_weight,
            cfg.pixel_l2_weight,
            cfg.pixel_loss_mode,
            cfg.per_image_backprop,
            cfg.grad_clip_norm,
        )
        val_loss, val_mae, val_iou, val_mask_acc, val_rmse = _eval_epoch_v3(
            model,
            val_loader,
            device,
            n_iters,
            cfg.grad_loss_weight,
            cfg.bldg_loss_weight,
            cfg.pixel_l2_weight,
            cfg.pixel_loss_mode,
        )
        scheduler.step()
        dt = time.perf_counter() - t0

        history.append({
            "epoch": epoch,
            "train_loss": train_loss, "train_mae": train_mae,
            "val_loss": val_loss, "val_mae": val_mae,
            "val_rmse": val_rmse,
            "val_mask_iou": val_iou,
            "val_mask_acc": val_mask_acc,
            "lr": scheduler.get_last_lr()[0],
        })

        is_best = val_loss < best_val
        latest_state = {
            "epoch": epoch,
            "arch": arch,
            "n_iters": n_iters,
            "model_state_dict": model.state_dict(),
            "val_loss": val_loss,
            "val_mae": val_mae,
            "val_rmse": val_rmse,
            "val_mask_iou": val_iou,
            "val_mask_acc": val_mask_acc,
            "best_val_loss": best_val,
            "best_epoch": best_epoch,
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict(),
            "config": cfg.__dict__,
        }
        torch.save(latest_state, str(latest_path))

        if is_best:
            best_val = val_loss
            best_epoch = epoch
            patience_counter = 0
            latest_state["best_val_loss"] = best_val
            latest_state["best_epoch"] = best_epoch
            torch.save(latest_state, str(out_path))
        else:
            patience_counter += 1

        if verbose:
            marker = "* best" if is_best else ""
            print(
                f"  Epoch {epoch:3d}/{end_epoch}  "
                f"train_loss={train_loss:.3f} train_mae={train_mae:.2f}m  "
                f"val_loss={val_loss:.3f} val_mae={val_mae:.2f}m "
                f"rmse={val_rmse:.2f}m iou={val_iou:.3f}  "
                f"{marker:7}  ({dt:.1f}s)"
            )

        if patience_counter >= cfg.patience:
            if verbose:
                print(f"  Early stopping at epoch {epoch}")
            break

    with open(history_path, "w", encoding="utf-8") as fh:
        json.dump({
            "history": history,
            "best_val_loss": best_val,
            "best_epoch": best_epoch,
            "n_iters": n_iters,
            "arch": arch,
        }, fh, indent=2)

    # Record run on the unified scoreboard so we can compare across architectures.
    try:
        from tools.ml.eval.scoreboard import record_run
        # Pull best epoch's full metric set from history
        best_entry = next(
            (h for h in reversed(history) if h.get("epoch") == best_epoch),
            history[-1] if history else {},
        )
        record_run(
            model_path=str(out_path),
            arch=arch or "roofnet_v3",
            task="height",
            best_metrics={
                "val_loss": best_entry.get("val_loss"),
                "val_mae": best_entry.get("val_mae"),
                "val_rmse": best_entry.get("val_rmse"),
                "val_mask_iou": best_entry.get("val_mask_iou"),
                "val_mask_acc": best_entry.get("val_mask_acc"),
            },
            n_train=int(split.get("n_train", 0) if isinstance(split, dict) else 0),
            n_val=int(split.get("n_val", 0) if isinstance(split, dict) else 0),
            epochs=int(best_epoch),
            config={k: v for k, v in cfg.__dict__.items() if not callable(v)},
            notes=f"tile_dir={Path(tile_dir).name}",
        )
    except Exception as e:
        logger.warning(f"scoreboard record failed: {e}")

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
    from tools.ml.data.datasets import make_roof_loaders

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
    latest_path = out_path.with_suffix(".latest.pt")

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

    # Model â€” start with frozen backbone
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
    history_path = out_path.parent / (out_path.stem + "_history.json")
    history: list[dict] = _load_history(history_path)
    best_val_acc = 0.0
    best_epoch = 0
    patience_counter = 0
    start_epoch = 1

    resume_ckpt: Path | None = None
    if cfg.resume_from:
        candidate = Path(cfg.resume_from)
        if not candidate.is_absolute():
            candidate = strm2stl / candidate
        if candidate.exists():
            resume_ckpt = candidate
    elif cfg.resume:
        if latest_path.exists():
            resume_ckpt = latest_path
        elif out_path.exists():
            resume_ckpt = out_path

    if resume_ckpt is not None:
        state = torch.load(str(resume_ckpt), map_location=device, weights_only=False)
        if isinstance(state, dict) and "model_state_dict" in state:
            model.load_state_dict(state["model_state_dict"])
        elif isinstance(state, dict):
            model.load_state_dict(state)

        if isinstance(state, dict) and "optimizer_state_dict" in state:
            try:
                optimizer.load_state_dict(state["optimizer_state_dict"])
            except Exception:
                pass
        if isinstance(state, dict) and "scheduler_state_dict" in state:
            try:
                scheduler.load_state_dict(state["scheduler_state_dict"])
            except Exception:
                pass

        if isinstance(state, dict):
            prev_epoch = int(state.get("epoch", 0) or 0)
            start_epoch = prev_epoch + 1
            best_val_acc = float(state.get("best_val_acc", state.get("val_acc", best_val_acc)))
            best_epoch = int(state.get("best_epoch", prev_epoch if prev_epoch > 0 else 0))

        if verbose:
            print(f"Resuming from {resume_ckpt} (start epoch {start_epoch})")

    end_epoch = start_epoch + cfg.epochs - 1
    for epoch in range(start_epoch, end_epoch + 1):
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
        train_loss, train_acc = _train_epoch_shape(
            model,
            train_loader,
            criterion,
            optimizer,
            device,
            cfg.per_image_backprop,
        )
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
        latest_state = {
            "epoch": epoch,
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict(),
            "val_acc": val_acc,
            "val_loss": val_loss,
            "best_val_acc": best_val_acc,
            "best_epoch": best_epoch,
            "config": cfg.__dict__,
        }
        torch.save(latest_state, str(latest_path))

        if is_best:
            best_val_acc = val_acc
            best_epoch = epoch
            patience_counter = 0
            latest_state["best_val_acc"] = best_val_acc
            latest_state["best_epoch"] = best_epoch
            torch.save(latest_state, str(out_path))
        else:
            patience_counter += 1

        if verbose:
            marker = "* best" if is_best else ""
            print(
                f"  Epoch {epoch:3d}/{end_epoch}  "
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
    from tools.ml.data.datasets import make_height_loaders

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
    latest_path = out_path.with_suffix(".latest.pt")

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

    history_path = out_path.parent / (out_path.stem + "_history.json")
    history: list[dict] = _load_history(history_path)
    best_val_loss = float("inf")
    best_selection_value = float("inf")
    best_epoch = 0
    patience_counter = 0
    start_epoch = 1

    resume_ckpt: Path | None = None
    if cfg.resume_from:
        candidate = Path(cfg.resume_from)
        if not candidate.is_absolute():
            candidate = strm2stl / candidate
        if candidate.exists():
            resume_ckpt = candidate
    elif cfg.resume:
        if latest_path.exists():
            resume_ckpt = latest_path
        elif out_path.exists():
            resume_ckpt = out_path

    if resume_ckpt is not None:
        state = torch.load(str(resume_ckpt), map_location=device, weights_only=False)
        model_state = state.get("model_state_dict") if isinstance(state, dict) else None
        if model_state is None and isinstance(state, dict):
            model_state = state
        if model_state is not None:
            # Migrate old checkpoints: height_head.2.* -> height_head.3.*
            migrated = {}
            for k, v in model_state.items():
                if k.startswith("height_head.2."):
                    migrated["height_head.3." + k[len("height_head.2."):]] = v
                else:
                    migrated[k] = v
            model.load_state_dict(migrated, strict=False)

        if isinstance(state, dict) and "optimizer_state_dict" in state:
            try:
                optimizer.load_state_dict(state["optimizer_state_dict"])
            except Exception:
                pass
        if isinstance(state, dict) and "scheduler_state_dict" in state:
            try:
                scheduler.load_state_dict(state["scheduler_state_dict"])
            except Exception:
                pass

        if isinstance(state, dict):
            prev_epoch = int(state.get("epoch", 0) or 0)
            start_epoch = prev_epoch + 1
            best_val_loss = float(state.get("best_val_loss", state.get("val_loss", best_val_loss)))
            best_selection_value = float(
                state.get(
                    "best_selection_value",
                    state.get("best_val_rmse", state.get("best_val_loss", best_selection_value)),
                )
            )
            best_epoch = int(state.get("best_epoch", prev_epoch if prev_epoch > 0 else 0))

        if verbose:
            print(f"Resuming from {resume_ckpt} (start epoch {start_epoch})")

    end_epoch = start_epoch + cfg.epochs - 1
    for epoch in range(start_epoch, end_epoch + 1):
        t0 = time.perf_counter()
        train_loss = _train_epoch_height(
            model,
            train_loader,
            optimizer,
            device,
            cfg.grad_loss_weight,
            cfg.bldg_loss_weight,
            cfg.pixel_l2_weight,
            cfg.pixel_loss_mode,
            cfg.per_image_backprop,
            cfg.grad_clip_norm,
        )
        val_loss, val_mae, val_rmse = _eval_epoch_height(
            model,
            val_loader,
            device,
            cfg.grad_loss_weight,
            cfg.bldg_loss_weight,
            cfg.pixel_l2_weight,
            cfg.pixel_loss_mode,
        )
        scheduler.step()
        dt = time.perf_counter() - t0

        history.append({
            "epoch": epoch,
            "train_loss": train_loss,
            "val_loss": val_loss,
            "val_mae": val_mae,
            "val_rmse": val_rmse,
            "lr": scheduler.get_last_lr()[0],
        })

        metric = (cfg.selection_metric or "rmse").lower()
        if metric == "mae":
            selection_value = val_mae
        elif metric == "loss":
            selection_value = val_loss
        else:
            selection_value = val_rmse

        is_best = selection_value < best_selection_value
        latest_state = {
            "epoch": epoch,
            "model_state_dict": model.state_dict(),
            "val_loss": val_loss,
            "val_mae": val_mae,
            "val_rmse": val_rmse,
            "best_val_loss": best_val_loss,
            "best_selection_value": best_selection_value,
            "selection_metric": metric,
            "best_epoch": best_epoch,
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict(),
            "config": cfg.__dict__,
        }
        torch.save(latest_state, str(latest_path))

        if is_best:
            best_val_loss = val_loss
            best_selection_value = selection_value
            best_epoch = epoch
            patience_counter = 0
            latest_state["best_val_loss"] = best_val_loss
            latest_state["best_selection_value"] = best_selection_value
            latest_state["best_epoch"] = best_epoch
            # Keep latest checkpoint metadata aligned with the newly promoted best.
            torch.save(latest_state, str(latest_path))
            torch.save(latest_state, str(out_path))
        else:
            patience_counter += 1

        if verbose:
            marker = "* best" if is_best else ""
            print(
                f"  Epoch {epoch:3d}/{end_epoch}  "
                f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
                f"val_mae={val_mae:.4f}  val_rmse={val_rmse:.4f}  "
                f"{marker:7}  ({dt:.1f}s)"
            )

        if patience_counter >= cfg.patience:
            if verbose:
                print(f"  Early stopping at epoch {epoch}")
            break

    with open(history_path, "w", encoding="utf-8") as fh:
        json.dump({
            "history": history,
            "best_val_loss": best_val_loss,
            "best_selection_value": best_selection_value,
            "selection_metric": (cfg.selection_metric or "rmse").lower(),
            "best_epoch": best_epoch,
        }, fh, indent=2)

    final_metric = (cfg.selection_metric or "rmse").lower()
    if verbose:
        print(
            f"\n  Best {final_metric}: {best_selection_value:.4f} "
            f"(epoch {best_epoch}); corresponding val_loss={best_val_loss:.4f}"
        )
        print(f"  Model: {out_path}")

    return {
        "best_val_loss": best_val_loss,
        "best_selection_value": best_selection_value,
        "selection_metric": final_metric,
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
    parser.add_argument("--weight-decay", type=float, default=DEFAULT_WEIGHT_DECAY,
                        help="AdamW weight-decay regularization.")
    parser.add_argument("--grad-loss-weight", type=float, default=0.2,
                        help="Edge (Sobel gradient) regularization weight for height loss.")
    parser.add_argument("--pixel-l2-weight", type=float, default=0.25,
                        help="Extra pixelwise L2 term weight added to weighted L1.")
    parser.add_argument("--pixel-loss", choices=["l1", "rmse", "hybrid"], default="hybrid",
                        help="Pixel regression objective.")
    parser.add_argument("--selection-metric", choices=["loss", "mae", "rmse"], default="rmse",
                        help="Metric used to decide/save best checkpoint.")
    parser.add_argument("--bldg-loss-weight", type=float, default=5.0,
                        help="Building-pixel upweight factor for sparse height targets.")
    parser.add_argument("--crop-size", type=int, default=DEFAULT_CROP_SIZE)
    parser.add_argument("--patience", type=int, default=EARLY_STOP_PATIENCE)
    parser.add_argument("--freeze-backbone-epochs", type=int, default=5)
    parser.add_argument("--resume", action="store_true",
                        help="Resume training from existing output checkpoint.")
    parser.add_argument("--resume-from", default=None,
                        help="Explicit checkpoint path to resume from.")
    parser.add_argument("--resume-scheduler", action="store_true",
                        help="Also restore scheduler state when resuming.")
    parser.add_argument(
        "--batch-backprop",
        action="store_true",
        help="Use one backward/step per batch instead of per image.",
    )
    parser.add_argument("--device", default="auto")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cfg = TrainConfig(
        task=args.task,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        weight_decay=args.weight_decay,
        grad_loss_weight=args.grad_loss_weight,
        pixel_l2_weight=args.pixel_l2_weight,
        pixel_loss_mode=args.pixel_loss,
        selection_metric=args.selection_metric,
        bldg_loss_weight=args.bldg_loss_weight,
        crop_size=args.crop_size,
        patience=args.patience,
        freeze_backbone_epochs=args.freeze_backbone_epochs,
        per_image_backprop=not args.batch_backprop,
        resume=args.resume,
        resume_from=args.resume_from,
        resume_scheduler=args.resume_scheduler,
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
