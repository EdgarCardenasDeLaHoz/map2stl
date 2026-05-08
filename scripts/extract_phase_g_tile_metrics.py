"""Extract per-tile metrics from Phase G test model and export as JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, random_split

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.ml.models import Retna_V1
from tools.ml.train.train_retna import HeightTileDataset, HEIGHT_NORM_M, dice_loss


def extract_tile_metrics(
    checkpoint_path: Path, tiles_dir: Path, seed: int = 42, tile_size: int = 128
) -> dict:
    """Compute per-tile metrics for Phase G test model.

    Returns:
        {
            "model_path": str,
            "arch": [8, 8, 10, ...],
            "n_params": int,
            "height_norm_m": float,
            "n_tiles": int,
            "metrics_summary": {
                "mean_mae": float,
                "median_mae": float,
                "std_mae": float,
                "min_mae": float,
                "max_mae": float,
            },
            "per_tile": [
                {"tile_idx": int, "mae": float, "rmse": float, "iou": float, "r": float},
                ...
            ],
            "best_tiles": [{"tile_idx": int, "mae": float}, ...],
            "worst_tiles": [{"tile_idx": int, "mae": float}, ...],
        }
    """
    state = torch.load(str(checkpoint_path), map_location="cpu", weights_only=False)
    hidden = state.get("hidden_channels", [8, 8, 8, 8])
    norm_m = state.get("height_norm_m", HEIGHT_NORM_M)

    model = Retna_V1(in_channels=3, out_classes=1, hidden_channels=hidden)
    model.load_state_dict(state["model_state_dict"])
    model.eval()

    n_params = sum(p.numel() for p in model.parameters())

    # Load tiles
    paths = sorted(tiles_dir.glob("*.npz"))
    n = len(paths)
    n_val = max(1, int(0.15 * n))
    n_train = n - n_val

    full = HeightTileDataset(paths, tile_size, augment=False)
    _, val_ds = random_split(
        full, [n_train, n_val], generator=torch.Generator().manual_seed(seed)
    )
    loader = DataLoader(val_ds, batch_size=1, shuffle=False, num_workers=0)

    # Compute per-tile metrics
    per_tile = []
    with torch.no_grad():
        for tile_idx, (rgb, target) in enumerate(loader):
            pred = model(rgb)
            target_m = target[0, 0].numpy() * norm_m
            pred_m = pred[0, 0].numpy() * norm_m

            # Mask: non-zero target pixels
            mask = target_m > 0
            if not mask.any():
                mask = np.ones_like(target_m, dtype=bool)

            # MAE
            mae = float(np.abs(pred_m[mask] - target_m[mask]).mean())

            # RMSE
            rmse = float(
                np.sqrt(np.mean((pred_m[mask] - target_m[mask]) ** 2))
            )

            # IoU (intersection / union of pixels > 0)
            pred_pos = pred_m[mask] > 0
            target_pos = target_m[mask] > 0
            intersection = np.sum(pred_pos & target_pos)
            union = np.sum(pred_pos | target_pos)
            iou = float(intersection / union) if union > 0 else 0.0

            # Pearson R
            if len(target_m[mask]) > 1 and target_m[mask].std() > 0 and pred_m[mask].std() > 0:
                r = float(
                    np.corrcoef(target_m[mask], pred_m[mask])[0, 1]
                )
            else:
                r = 0.0

            per_tile.append({
                "tile_idx": tile_idx,
                "mae": mae,
                "rmse": rmse,
                "iou": iou,
                "r": r,
                "target_mean": float(target_m[mask].mean()) if mask.any() else 0.0,
                "target_std": float(target_m[mask].std()) if mask.any() else 0.0,
                "pred_mean": float(pred_m[mask].mean()) if mask.any() else 0.0,
            })

    mae_vals = np.array([t["mae"] for t in per_tile])

    # Find best/worst tiles
    sorted_by_mae = sorted(per_tile, key=lambda x: x["mae"])
    best_count = min(5, len(sorted_by_mae) // 4)
    worst_count = min(5, len(sorted_by_mae) // 4)

    return {
        "model_path": str(checkpoint_path),
        "arch": hidden,
        "n_params": n_params,
        "height_norm_m": norm_m,
        "n_tiles": len(per_tile),
        "metrics_summary": {
            "mean_mae": float(mae_vals.mean()),
            "median_mae": float(np.median(mae_vals)),
            "std_mae": float(mae_vals.std()),
            "min_mae": float(mae_vals.min()),
            "max_mae": float(mae_vals.max()),
            "p25_mae": float(np.percentile(mae_vals, 25)),
            "p75_mae": float(np.percentile(mae_vals, 75)),
        },
        "per_tile": per_tile,
        "best_tiles": sorted_by_mae[:best_count],
        "worst_tiles": sorted_by_mae[-worst_count:],
    }


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Extract per-tile metrics from a height model.")
    ap.add_argument(
        "--checkpoint",
        default="models/retna_phase_g_test.pt",
        help="Path to model checkpoint (default: retna_phase_g_test.pt)",
    )
    ap.add_argument(
        "--tiles",
        default="cache/height_tiles_combined",
        help="Path to tile directory (default: height_tiles_combined)",
    )
    ap.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: auto-named from checkpoint)",
    )
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[1]
    checkpoint = Path(args.checkpoint)
    if not checkpoint.is_absolute():
        checkpoint = repo / checkpoint

    tiles_dir = Path(args.tiles)
    if not tiles_dir.is_absolute():
        tiles_dir = repo / tiles_dir

    if args.output:
        output_json = Path(args.output)
    else:
        # Auto-name based on checkpoint
        model_name = checkpoint.stem  # retna_pruned, retna_phase_g_test, etc.
        output_json = repo / "output" / f"{model_name}_tile_metrics.json"

    if not checkpoint.exists():
        print(f"ERROR: Model not found: {checkpoint}")
        print("\nRun first: python scripts/train_phase_g_test_single_cycle.py")
        return 1

    if not tiles_dir.exists():
        print(f"ERROR: Tiles not found: {tiles_dir}")
        return 1

    output_json.parent.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("PHASE G TILE METRICS EXTRACTION")
    print("=" * 70)
    print(f"Model:  {checkpoint}")
    print(f"Tiles:  {tiles_dir}")
    print(f"Output: {output_json}")
    print()

    metrics = extract_tile_metrics(checkpoint, tiles_dir)

    with open(output_json, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"[OK] Wrote metrics to {output_json}\n")

    # Print summary
    summary = metrics["metrics_summary"]
    print("PER-TILE MAE STATISTICS:")
    print(f"  Mean:   {summary['mean_mae']:.2f} m")
    print(f"  Median: {summary['median_mae']:.2f} m")
    print(f"  Std:    {summary['std_mae']:.2f} m")
    print(f"  Min:    {summary['min_mae']:.2f} m")
    print(f"  Max:    {summary['max_mae']:.2f} m")
    print(f"  Q1:     {summary['p25_mae']:.2f} m")
    print(f"  Q3:     {summary['p75_mae']:.2f} m")
    print()

    print(f"BEST TILES (lowest MAE):")
    for t in metrics["best_tiles"]:
        print(f"  Tile {t['tile_idx']:3d}: MAE={t['mae']:6.2f}m  RMSE={t['rmse']:6.2f}m  IoU={t['iou']:.3f}  R={t['r']:+.3f}")
    print()

    print(f"WORST TILES (highest MAE):")
    worst = metrics["worst_tiles"]
    for t in reversed(worst):  # Show worst-first
        print(f"  Tile {t['tile_idx']:3d}: MAE={t['mae']:6.2f}m  RMSE={t['rmse']:6.2f}m  IoU={t['iou']:.3f}  R={t['r']:+.3f}")
    print()

    print("INTERPRETATION:")
    print(f"  • Low IoU tiles may have poor building/no-building discrimination")
    print(f"  • Low R tiles indicate weak correlation with ground truth")
    print(f"  • High target_std tiles are harder (more variable buildings)")
    print(f"  • Check JSON for full per-tile metrics including target_mean/std")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
