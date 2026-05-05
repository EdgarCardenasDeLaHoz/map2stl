"""scripts/compare_models.py — Compare two models on tall-building performance.

Usage:
    python scripts/compare_models.py --baseline models/retna_rebuild.pt --test models/retna_phase2_regression.pt

Loads both checkpoints and computes per-tile MAE, highlighting tall-building tiles.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

_HERE = Path(__file__).resolve().parent
_STRM2STL = _HERE.parents[1]
for _p in (_STRM2STL, _STRM2STL.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.ml.train.train_retna import HeightTileDataset

HEIGHT_NORM_M = 200.0


def load_model(ckpt_path: Path, device: str = "cpu"):
    """Load a Retna_V1 checkpoint."""
    from tools.ml.models import Retna_V1

    state = torch.load(ckpt_path, map_location=device)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]

    # Infer architecture from state keys
    hidden = []
    for key in state.keys():
        match = key.split(".")
        if len(match) >= 3 and match[0] == "blocks":
            block_idx = int(match[1])
            while len(hidden) <= block_idx:
                hidden.append(None)
            if match[2] == "conv" and len(match) > 3:
                out_channels = state[key].shape[0]
                hidden[block_idx] = out_channels

    hidden = [c for c in hidden if c is not None]
    if not hidden:
        raise ValueError(f"Could not infer architecture from {ckpt_path}")

    model = Retna_V1(hidden=hidden)
    model.load_state_dict(state)
    model.to(device)
    model.eval()
    return model


def evaluate_by_tile(model, tile_paths: list[Path], device: str = "cpu"):
    """Return per-tile metrics (height_m, pred_mae_m, target_mean_m)."""
    results = []

    with torch.no_grad():
        for tile_path in tile_paths:
            with np.load(tile_path) as d:
                rgb = torch.tensor(d["rgb"], dtype=torch.float32, device=device).unsqueeze(0)
                target = torch.tensor(d["height"][0], dtype=torch.float32, device=device).unsqueeze(0).unsqueeze(0)

            pred = model(rgb)
            target_m = target * HEIGHT_NORM_M
            pred_m = pred * HEIGHT_NORM_M
            mask = (target_m > 0).float()
            mask_n = mask.sum().clamp(min=1.0)

            mae_m = (mask * (pred_m - target_m).abs()).sum() / mask_n
            target_mean_m = (mask * target_m).sum() / mask_n

            results.append({
                "tile": tile_path.stem,
                "target_mean_m": target_mean_m.item(),
                "pred_mae_m": mae_m.item(),
                "target_height_category": "tall" if target_mean_m.item() > 20 else "short",
            })

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, type=Path)
    ap.add_argument("--test", required=True, type=Path)
    ap.add_argument("--tile-dir", default="cache/height_tiles_combined", type=Path)
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()

    tile_dir = Path(args.tile_dir)
    if not tile_dir.is_absolute():
        tile_dir = _STRM2STL / tile_dir

    tile_paths = list(tile_dir.glob("*.npz"))
    if not tile_paths:
        print(f"No tiles found in {tile_dir}")
        return 1

    print(f"Loading {len(tile_paths)} tiles from {tile_dir}")

    # Load models
    print(f"\nLoading baseline: {args.baseline}")
    baseline = load_model(args.baseline, device=args.device)

    print(f"Loading test: {args.test}")
    test = load_model(args.test, device=args.device)

    # Evaluate
    print(f"\nEvaluating baseline...")
    baseline_results = evaluate_by_tile(baseline, tile_paths, device=args.device)

    print(f"Evaluating test...")
    test_results = evaluate_by_tile(test, tile_paths, device=args.device)

    # Compare
    print("\n" + "=" * 80)
    print("TALL-BUILDING PERFORMANCE COMPARISON")
    print("=" * 80)

    tall_baseline = [r for r in baseline_results if r["target_height_category"] == "tall"]
    tall_test = [r for r in test_results if r["target_height_category"] == "tall"]
    short_baseline = [r for r in baseline_results if r["target_height_category"] == "short"]
    short_test = [r for r in test_results if r["target_height_category"] == "short"]

    def stats(results):
        maes = [r["pred_mae_m"] for r in results]
        if not maes:
            return None
        return {
            "mean_mae": np.mean(maes),
            "std_mae": np.std(maes),
            "min_mae": np.min(maes),
            "max_mae": np.max(maes),
        }

    print(f"\nTALL BUILDINGS (mean height > 20m):")
    print(f"  Count: baseline={len(tall_baseline)}, test={len(tall_test)}")
    if tall_baseline:
        b = stats(tall_baseline)
        print(f"  Baseline MAE: {b['mean_mae']:.2f}m ± {b['std_mae']:.2f}m (min {b['min_mae']:.2f}, max {b['max_mae']:.2f})")
    if tall_test:
        t = stats(tall_test)
        print(f"  Test MAE:     {t['mean_mae']:.2f}m ± {t['std_mae']:.2f}m (min {t['min_mae']:.2f}, max {t['max_mae']:.2f})")
        if tall_baseline and t and b:
            improvement = (b["mean_mae"] - t["mean_mae"]) / b["mean_mae"] * 100
            print(f"  IMPROVEMENT:  {improvement:+.1f}%")

    print(f"\nSHORT BUILDINGS (mean height <= 20m):")
    print(f"  Count: baseline={len(short_baseline)}, test={len(short_test)}")
    if short_baseline:
        b = stats(short_baseline)
        print(f"  Baseline MAE: {b['mean_mae']:.2f}m ± {b['std_mae']:.2f}m (min {b['min_mae']:.2f}, max {b['max_mae']:.2f})")
    if short_test:
        t = stats(short_test)
        print(f"  Test MAE:     {t['mean_mae']:.2f}m ± {t['std_mae']:.2f}m (min {t['min_mae']:.2f}, max {t['max_mae']:.2f})")
        if short_baseline and t and b:
            improvement = (b["mean_mae"] - t["mean_mae"]) / b["mean_mae"] * 100
            print(f"  CHANGE:       {improvement:+.1f}%")

    print("\n" + "=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
