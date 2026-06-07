"""
city2stl/height/train -- U-Net height prediction training pipeline.

Phase 2.2 of 3D_plan1.md.

Architecture:
  U-Net with EfficientNet-B4 encoder (timm).
  Input:  (3, 256, 256) float32 RGB tile in [0, 1].
  Output: (1, 256, 256) float32 height in metres.
  Loss:   L1 + gradient (Sobel) loss.
  Optimiser: AdamW, cosine-annealed LR.

Data:
  Tile pairs are collected from Phase 1 ground-truth providers and cached
  under cache/height_tiles/<city>/<idx>.npz.  Each .npz contains:
    rgb   : (3, 256, 256) float32
    height: (1, 256, 256) float32  (NaN-free -- tiles with >50% NaN dropped)

This module contains the pure training pipeline (no server dependencies).
Tile collection (collect_tiles) lives in app.server.core.height.train
because it depends on the server's cache-backed height data providers.

Usage (session):
  s.train_height_model(cities=["Barcelona"], epochs=50)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Dataset
# ------------------------------------------------------------------------------

# Pixel size for tiles (must match train + inference)
TILE_SIZE = 256
# Target ground resolution during tile collection
TARGET_RES_M = 5.0
# Maximum fraction of NaN pixels tolerated in a height tile
MAX_NAN_FRAC = 0.5


@dataclass
class TileDataset:
    """PyTorch Dataset of (rgb, height) tile pairs loaded from .npz files.

    Parameters
    ----------
    tile_paths : list of Path objects pointing to .npz files.
    augment    : whether to apply random horizontal/vertical flips.
    """

    tile_paths: List[Path]
    augment: bool = True

    def __post_init__(self):
        try:
            import torch  # noqa: F401
        except ImportError as exc:
            raise ImportError("torch is required to use TileDataset") from exc

    def __len__(self) -> int:
        return len(self.tile_paths)

    def __getitem__(self, idx: int):
        import torch

        data = np.load(self.tile_paths[idx])
        rgb = data["rgb"].astype(np.float32)      # (3, H, W) in [0, 1]
        height = data["height"].astype(np.float32)  # (1, H, W) in metres

        if self.augment:
            if np.random.random() > 0.5:
                rgb = rgb[:, :, ::-1].copy()
                height = height[:, :, ::-1].copy()
            if np.random.random() > 0.5:
                rgb = rgb[:, ::-1, :].copy()
                height = height[:, ::-1, :].copy()

        return torch.from_numpy(rgb), torch.from_numpy(height)


# ------------------------------------------------------------------------------
# Loss
# ------------------------------------------------------------------------------

def gradient_loss(pred: "torch.Tensor", target: "torch.Tensor") -> "torch.Tensor":
    """Sobel gradient loss -- encourages edges to align with ground truth."""
    import torch
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


def combined_loss(
    pred: "torch.Tensor",
    target: "torch.Tensor",
    grad_weight: float = 0.5,
) -> "torch.Tensor":
    """L1 + weighted Sobel gradient loss."""
    import torch.nn.functional as F

    l1 = F.l1_loss(pred, target)
    gl = gradient_loss(pred, target)
    return l1 + grad_weight * gl


# ------------------------------------------------------------------------------
# City specs (used by collect_tiles in the server layer and by terrain_session)
# ------------------------------------------------------------------------------

@dataclass
class CitySpec:
    """Geographic description of a training city."""
    name: str
    north: float
    south: float
    east: float
    west: float


# Default city bounding boxes used for tile collection
_DEFAULT_CITIES: dict[str, CitySpec] = {
    "Barcelona": CitySpec("Barcelona", 41.470, 41.310, 2.230, 1.980),
    "Granada":   CitySpec("Granada",   37.230, 37.110, -3.540, -3.720),
    "Cartagena": CitySpec("Cartagena", 10.480, 10.340, -75.440, -75.590),
}


# ------------------------------------------------------------------------------
# Training loop
# ------------------------------------------------------------------------------

@dataclass
class TrainConfig:
    """Hyperparameters for the U-Net training run."""
    epochs: int = 50
    batch_size: int = 8
    lr: float = 1e-4
    weight_decay: float = 1e-5
    grad_weight: float = 0.5
    val_split: float = 0.15
    seed: int = 42
    device: str = "cpu"
    num_workers: int = 0
    save_every: int = 10     # checkpoint frequency in epochs


def train(
    tile_paths: List[Path],
    output_path: Path,
    config: Optional[TrainConfig] = None,
) -> dict:
    """Train the U-Net and save a checkpoint.

    Parameters
    ----------
    tile_paths   : All collected tile .npz files.
    output_path  : Destination for the best checkpoint (.pt).
    config       : TrainConfig; defaults constructed if omitted.

    Returns
    -------
    dict with keys: best_val_loss, epochs_trained, n_train, n_val.
    """
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, random_split
    except ImportError as exc:
        raise ImportError(
            "torch and torchvision are required for training.  "
            "Install with: pip install torch torchvision timm"
        ) from exc

    from city2stl.skyline_cv.height.predict import _build_unet

    cfg = config or TrainConfig()
    torch.manual_seed(cfg.seed)
    device = torch.device(cfg.device)

    if not tile_paths:
        raise ValueError("No tile paths provided -- collect tiles first.")

    full_dataset = TileDataset(tile_paths, augment=True)
    n_val = max(1, int(len(full_dataset) * cfg.val_split))
    n_train = len(full_dataset) - n_val

    train_ds, val_ds = random_split(
        full_dataset,
        [n_train, n_val],
        generator=torch.Generator().manual_seed(cfg.seed),
    )
    # Disable augmentation on val split
    val_ds.dataset.augment = False  # type: ignore[attr-defined]

    train_loader = DataLoader(
        train_ds,
        batch_size=cfg.batch_size,
        shuffle=True,
        num_workers=cfg.num_workers,
        pin_memory=(cfg.device != "cpu"),
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=cfg.batch_size,
        num_workers=cfg.num_workers,
        pin_memory=(cfg.device != "cpu"),
    )

    model = _build_unet(pretrained_encoder=True).to(device)
    optimiser = torch.optim.AdamW(
        model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimiser, T_max=cfg.epochs, eta_min=cfg.lr * 0.01
    )

    best_val_loss = float("inf")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, cfg.epochs + 1):
        # -- train -----------------------------------------------------------
        model.train()
        train_loss = 0.0
        for rgb, height in train_loader:
            rgb = rgb.to(device)
            height = height.to(device)
            optimiser.zero_grad()
            pred = model(rgb)
            loss = combined_loss(pred, height, cfg.grad_weight)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimiser.step()
            train_loss += loss.item() * rgb.size(0)
        train_loss /= n_train

        # -- validate --------------------------------------------------------
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for rgb, height in val_loader:
                rgb = rgb.to(device)
                height = height.to(device)
                pred = model(rgb)
                loss = combined_loss(pred, height, cfg.grad_weight)
                val_loss += loss.item() * rgb.size(0)
        val_loss /= n_val

        scheduler.step()
        logger.info(
            "Epoch %3d/%d  train=%.4f  val=%.4f  lr=%.2e",
            epoch, cfg.epochs, train_loss, val_loss,
            scheduler.get_last_lr()[0],
        )

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save(
                {
                    "epoch": epoch,
                    "model_state_dict": model.state_dict(),
                    "optimiser_state_dict": optimiser.state_dict(),
                    "val_loss": val_loss,
                    "config": cfg,
                },
                output_path,
            )
            logger.info("  -> Saved best checkpoint (val=%.4f)", val_loss)

        if epoch % cfg.save_every == 0:
            interim = output_path.with_suffix(f".epoch{epoch:03d}.pt")
            torch.save(
                {"epoch": epoch, "model_state_dict": model.state_dict()},
                interim,
            )

    return {
        "best_val_loss": best_val_loss,
        "epochs_trained": cfg.epochs,
        "n_train": n_train,
        "n_val": n_val,
        "checkpoint": str(output_path),
    }
