"""
tools.ml.simulate_data — Synthetic shape datasets for model verification.

Adapted from OrBITS/retna/simulate_data.py.  Use this to confirm a model
can learn a trivially solvable task before trusting it on real satellite data.
If a model fails here, something is wrong with the architecture or training
loop — not the data.

Two dataset modes
-----------------
SimShapeDataset  (mode="seg")
    Input : (3, H, W) float32 in [0, 1]  — grayscale shapes replicated to 3ch
    Target: (6, H, W) float32 in {0, 1}  — one binary mask per shape class
            [square, filled_circle, triangle, hollow_circle, mesh_square, plus]
    Use for: verifying multi-class segmentation heads.

SimHeightDataset (mode="height")
    Input : (3, H, W) float32 in [0, 1]
    Target: (1, H, W) float32 >= 0       — scalar "height" per shape pixel
            Each shape instance gets a random height in [5, 40] m.  Background = 0.
    Use for: verifying HeightUNet / RoofNetV3_S on a known-correct task.
             A well-trained model should reach MAE < 1 m within 10 epochs.

Quick verification
------------------
    from tools.ml.simulate_data import smoke_test_height
    smoke_test_height()   # prints MAE per epoch, should be <1m by epoch 10
"""

from __future__ import annotations

import numpy as np
from numpy import random as npr

import torch
from torch.utils.data import Dataset, DataLoader

# ---------------------------------------------------------------------------
# Shape primitives  (all operate on 2-D bool arrays)
# ---------------------------------------------------------------------------

def _logical_and(arrays):
    out = np.ones(arrays[0].shape, dtype=bool)
    for a in arrays:
        out = np.logical_and(out, a)
    return out


def _add_filled_square(arr, x, y, size):
    s = max(1, int(size / 2))
    xx, yy = np.mgrid[:arr.shape[0], :arr.shape[1]]
    return np.logical_or(arr, _logical_and([xx > x-s, xx < x+s, yy > y-s, yy < y+s]))


def _add_mesh_square(arr, x, y, size):
    s = max(1, int(size / 2))
    xx, yy = np.mgrid[:arr.shape[0], :arr.shape[1]]
    return np.logical_or(arr, _logical_and(
        [xx > x-s, xx < x+s, xx % 2 == 1, yy > y-s, yy < y+s, yy % 2 == 1]
    ))


def _add_triangle(arr, x, y, size):
    size = max(2, size)
    s = int(size / 2)
    tri = np.tril(np.ones((size, size), dtype=bool))
    r0, r1 = max(0, x-s), min(arr.shape[0], x-s+tri.shape[0])
    c0, c1 = max(0, y-s), min(arr.shape[1], y-s+tri.shape[1])
    tr0, tr1 = r0-(x-s), tri.shape[0]-(x-s+tri.shape[0]-r1)
    tc0, tc1 = c0-(y-s), tri.shape[1]-(y-s+tri.shape[1]-c1)
    if r0 < r1 and c0 < c1 and tr0 < tr1 and tc0 < tc1:
        arr[r0:r1, c0:c1] = np.logical_or(arr[r0:r1, c0:c1], tri[tr0:tr1, tc0:tc1])
    return arr


def _add_circle(arr, x, y, size, fill=False):
    size = max(2, size)
    xx, yy = np.mgrid[:arr.shape[0], :arr.shape[1]]
    dist = np.sqrt((xx - x)**2 + (yy - y)**2)
    inner = 0.0 if fill else size * 0.7
    return np.logical_or(arr, (dist < size) & (dist >= inner))


def _add_plus(arr, x, y, size):
    s = max(1, int(size / 2))
    H, W = arr.shape
    arr[max(0,x-1):min(H,x+1), max(0,y-s):min(W,y+s)] = True
    arr[max(0,x-s):min(H,x+s), max(0,y-1):min(W,y+1)] = True
    return arr


def _random_loc(height, width, zoom=1.0):
    x = int(height * npr.uniform(0.15, 0.85))
    y = int(width  * npr.uniform(0.15, 0.85))
    size = max(4, int(min(height, width) * npr.uniform(0.06, 0.18) * zoom))
    return x, y, size


# ---------------------------------------------------------------------------
# Image + mask generator
# ---------------------------------------------------------------------------

SHAPE_NAMES = ["square", "filled_circle", "triangle", "hollow_circle", "mesh_square", "plus"]


def _generate_sample(height: int = 120, width: int = 120, n_repeats: int = 5):
    """Return (image, seg_masks, height_map).

    image      : (H, W) float32 in [0, 1]
    seg_masks  : (6, H, W) float32 binary, one channel per shape class
    height_map : (H, W) float32 >= 0, each shape gets a random height in [5,40]m
    """
    img   = np.zeros((height, width), dtype=bool)
    masks = np.zeros((6, height, width), dtype=bool)
    hmap  = np.zeros((height, width), dtype=np.float32)

    for _ in range(n_repeats):
        locs = {
            "triangle":      _random_loc(height, width),
            "hollow_circle": _random_loc(height, width, zoom=0.7),
            "filled_circle": _random_loc(height, width, zoom=0.5),
            "mesh_square":   _random_loc(height, width, zoom=1.5),
            "square":        _random_loc(height, width, zoom=0.8),
            "plus":          _random_loc(height, width, zoom=1.2),
        }

        img = _add_triangle(img,      *locs["triangle"])
        img = _add_circle(img,        *locs["hollow_circle"])
        img = _add_circle(img,        *locs["filled_circle"], fill=True)
        img = _add_mesh_square(img,   *locs["mesh_square"])
        img = _add_filled_square(img, *locs["square"])
        img = _add_plus(img,          *locs["plus"])

        for i, (name, fn) in enumerate([
            ("square",        lambda arr, l: _add_filled_square(arr, *l)),
            ("filled_circle", lambda arr, l: _add_circle(arr, *l, fill=True)),
            ("triangle",      lambda arr, l: _add_triangle(arr, *l)),
            ("hollow_circle", lambda arr, l: _add_circle(arr, *l)),
            ("mesh_square",   lambda arr, l: _add_filled_square(arr, *l)),
            ("plus",          lambda arr, l: _add_plus(arr, *l)),
        ]):
            masks[i] = fn(masks[i], locs[name])
            h_val = float(npr.uniform(5, 40))
            new_px = fn(np.zeros((height, width), dtype=bool), locs[name])
            hmap = np.where(new_px & (hmap == 0), h_val, hmap)

    img_f = img.astype(np.float32)
    # Add spatially-varying noise
    noise = npr.uniform(-0.3, 0.3) + npr.uniform(-0.3, 0.3, (height, width)).astype(np.float32)
    img_f = np.clip(img_f + noise, 0.0, 1.0)

    return img_f, masks.astype(np.float32), hmap


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

class SimShapeDataset(Dataset):
    """Infinite synthetic segmentation dataset.  Each __getitem__ generates a
    fresh random sample so epoch size is controlled by ``count``.

    Returns
    -------
    image : (3, H, W) float32  — grayscale shape image replicated to 3 channels
    masks : (6, H, W) float32  — binary mask per shape class
    """

    def __init__(self, count: int = 500, height: int = 120, width: int = 120,
                 n_repeats: int = 5, seed: int | None = None):
        self.count = count
        self.height = height
        self.width = width
        self.n_repeats = n_repeats
        self._rng_seed = seed

    def __len__(self):
        return self.count

    def __getitem__(self, idx):
        if self._rng_seed is not None:
            npr.seed(self._rng_seed + idx)
        img, masks, _ = _generate_sample(self.height, self.width, self.n_repeats)
        # Replicate grayscale → 3 channels with small per-channel jitter
        rgb = np.stack([
            np.clip(img + npr.uniform(-0.05, 0.05, img.shape).astype(np.float32), 0, 1)
            for _ in range(3)
        ], axis=0)  # (3, H, W)
        return torch.from_numpy(rgb), torch.from_numpy(masks)


class SimHeightDataset(Dataset):
    """Infinite synthetic height-regression dataset.

    Returns
    -------
    image      : (3, H, W) float32
    height_map : (1, H, W) float32 — height in metres, 0 = background
    """

    def __init__(self, count: int = 500, height: int = 120, width: int = 120,
                 n_repeats: int = 3, seed: int | None = None):
        self.count = count
        self.height = height
        self.width = width
        self.n_repeats = n_repeats
        self._rng_seed = seed

    def __len__(self):
        return self.count

    def __getitem__(self, idx):
        if self._rng_seed is not None:
            npr.seed(self._rng_seed + idx)
        img, _, hmap = _generate_sample(self.height, self.width, self.n_repeats)
        rgb = np.stack([
            np.clip(img + npr.uniform(-0.05, 0.05, img.shape).astype(np.float32), 0, 1)
            for _ in range(3)
        ], axis=0)
        return torch.from_numpy(rgb), torch.from_numpy(hmap[np.newaxis])  # (1,H,W)


# ---------------------------------------------------------------------------
# DataLoader helpers
# ---------------------------------------------------------------------------

def make_sim_height_loaders(
    train_count: int = 800,
    val_count: int = 100,
    height: int = 120,
    width: int = 120,
    batch_size: int = 8,
    n_repeats: int = 3,
) -> tuple[DataLoader, DataLoader]:
    train_ds = SimHeightDataset(train_count, height, width, n_repeats, seed=None)
    val_ds   = SimHeightDataset(val_count,   height, width, n_repeats, seed=42)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,  num_workers=0)
    val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False, num_workers=0)
    return train_loader, val_loader


def make_sim_seg_loaders(
    train_count: int = 800,
    val_count: int = 100,
    height: int = 120,
    width: int = 120,
    batch_size: int = 8,
) -> tuple[DataLoader, DataLoader]:
    train_ds = SimShapeDataset(train_count, height, width, seed=None)
    val_ds   = SimShapeDataset(val_count,   height, width, seed=42)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,  num_workers=0)
    val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False, num_workers=0)
    return train_loader, val_loader


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

def smoke_test_height(epochs: int = 15, device: str = "cpu") -> dict:
    """Verify HeightUNet learns the synthetic height task.

    A correctly wired model should reach val MAE < 2 m within 10–15 epochs.
    If it doesn't, something is wrong with the model or training loop.

    Returns the final history list.
    """
    import torch.nn.functional as F
    from tools.ml.models import HeightUNet
    from tools.ml.train import _unet_loss

    print("=== HeightUNet smoke test (synthetic data) ===")
    train_loader, val_loader = make_sim_height_loaders(
        train_count=400, val_count=80, height=120, width=120, batch_size=8
    )

    dev = torch.device(device)
    model = HeightUNet(pretrained=False, freeze_backbone=False, dec_ch=24).to(dev)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)

    history = []
    for epoch in range(1, epochs + 1):
        model.train()
        for rgb, hmap in train_loader:
            rgb, hmap = rgb.to(dev), hmap.to(dev)
            optimizer.zero_grad()
            mask_logits, height_pred = model(rgb)
            loss = _unet_loss(mask_logits, height_pred, hmap,
                              grad_weight=0.1, bldg_weight=3.0)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

        model.eval()
        mae_sum, n = 0.0, 0
        with torch.no_grad():
            for rgb, hmap in val_loader:
                rgb, hmap = rgb.to(dev), hmap.to(dev)
                _, height_pred = model(rgb)
                bldg = hmap > 0
                if bldg.any():
                    mae_sum += (height_pred - hmap).abs()[bldg].sum().item()
                    n += bldg.sum().item()
        val_mae = mae_sum / max(n, 1)
        history.append({"epoch": epoch, "val_mae": val_mae})
        status = "OK" if val_mae < 2.0 else ("converging" if val_mae < 5.0 else "slow")
        print(f"  Epoch {epoch:2d}: val_mae={val_mae:.2f}m  [{status}]")

    final_mae = history[-1]["val_mae"]
    if final_mae < 2.0:
        print(f"\nPASS — model learned synthetic heights (MAE={final_mae:.2f}m < 2m)")
    else:
        print(f"\nFAIL — MAE={final_mae:.2f}m still too high after {epochs} epochs")
    return history


def smoke_test_seg(epochs: int = 10, device: str = "cpu") -> dict:
    """Verify HeightUNet mask head learns 6-class shape segmentation.

    Treats the union of all 6 shape masks as the binary foreground.
    Should reach IoU > 0.7 within 10 epochs.
    """
    import torch.nn.functional as F
    from tools.ml.models import HeightUNet

    print("=== HeightUNet mask-head smoke test (segmentation) ===")
    train_loader, val_loader = make_sim_seg_loaders(
        train_count=400, val_count=80, height=120, width=120, batch_size=8
    )

    dev = torch.device(device)
    model = HeightUNet(pretrained=False, freeze_backbone=False, dec_ch=24).to(dev)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)

    history = []
    for epoch in range(1, epochs + 1):
        model.train()
        for rgb, masks in train_loader:
            rgb, masks = rgb.to(dev), masks.to(dev)
            # Union of all shape masks = foreground
            fg = (masks.sum(dim=1, keepdim=True) > 0).float()
            optimizer.zero_grad()
            mask_logits, _ = model(rgb)
            loss = F.binary_cross_entropy_with_logits(mask_logits, fg)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

        model.eval()
        iou_sum, n = 0.0, 0
        with torch.no_grad():
            for rgb, masks in val_loader:
                rgb, masks = rgb.to(dev), masks.to(dev)
                fg = (masks.sum(dim=1, keepdim=True) > 0).float()
                mask_logits, _ = model(rgb)
                pred = (torch.sigmoid(mask_logits) > 0.5).float()
                inter = (pred * fg).sum().item()
                union = ((pred + fg) > 0).float().sum().item()
                iou_sum += inter / max(union, 1)
                n += 1
        val_iou = iou_sum / max(n, 1)
        history.append({"epoch": epoch, "val_iou": val_iou})
        status = "OK" if val_iou > 0.7 else ("converging" if val_iou > 0.4 else "slow")
        print(f"  Epoch {epoch:2d}: val_iou={val_iou:.3f}  [{status}]")

    final_iou = history[-1]["val_iou"]
    if final_iou > 0.7:
        print(f"\nPASS — mask head learned shapes (IoU={final_iou:.3f} > 0.7)")
    else:
        print(f"\nFAIL — IoU={final_iou:.3f} still too low after {epochs} epochs")
    return history


if __name__ == "__main__":
    smoke_test_height()
    smoke_test_seg()
