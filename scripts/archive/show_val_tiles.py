"""Show which actual tile files are in the validation set."""

from __future__ import annotations

import sys
from pathlib import Path

import torch
from torch.utils.data import random_split

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ml.train.train_retna import HeightTileDataset

repo = Path(__file__).resolve().parents[1]
tiles_dir = repo / "cache" / "height_tiles_combined"

paths = sorted(tiles_dir.glob("*.npz"))
n = len(paths)
n_val = max(1, int(0.15 * n))
n_train = n - n_val

print(f"\n{len(paths)} total tiles in {tiles_dir}")
print(f"Train/Val split: {n_train} / {n_val} (15%)")

full = HeightTileDataset(paths, 128, augment=False)
_, val_ds = random_split(
    full, [n_train, n_val], generator=torch.Generator().manual_seed(42)
)

# The val_ds contains indices into the original paths list
val_indices = val_ds.indices

print(f"\nValidation tile indices (position in sorted list):")
for i, idx in enumerate(sorted(val_indices)):
    tile_path = paths[idx]
    print(f"  Val tile {i:2d}: index {idx:2d}  -> {tile_path.name}")

print(f"\nUse these filenames to inspect individual tiles:")
print(f"  python scripts/inspect_tiles.py --filename <tile_filename>")
