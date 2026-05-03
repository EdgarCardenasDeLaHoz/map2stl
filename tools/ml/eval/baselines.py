"""Heuristic baselines for building-height regression.

These are zero-parameter rules that establish a floor any neural model must
beat. If a network with a million parameters does not outperform a 5-line
green-gray detector on the validation set, the network is broken.

Usage:
    python -m tools.ml.baselines \\
        --tiles cache/height_tiles_combined \\
        --tile-size 128 \\
        --baseline gray_minus_green
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

import numpy as np


def gray_minus_green(rgb: np.ndarray, max_h_m: float = 50.0) -> np.ndarray:
    """Predict per-pixel height from a tile's RGB.

    Logic:
        - Vegetation (green-dominant) suppresses height to 0.
        - Roads / water / dark patches suppress height (low brightness).
        - Bright, gray, low-saturation pixels are treated as buildings and
          mapped to a height proportional to brightness.

    Args:
        rgb:       (3, H, W) float32 in [0, 1]
        max_h_m:   metres assigned to fully bright, fully gray pixels.
                   Tuned to the dataset's typical max — 50 m is a reasonable
                   compromise for European mid-rise + Cartagena low-rise.

    Returns:
        (H, W) float32 height in metres.
    """
    if rgb.ndim != 3 or rgb.shape[0] != 3:
        raise ValueError(f"expected (3, H, W); got {rgb.shape}")

    r, g, b = rgb[0], rgb[1], rgb[2]
    brightness = (r + g + b) / 3.0

    # Greenness: how much g exceeds the mean of r/b.
    greenness = np.clip(g - 0.5 * (r + b), 0.0, 1.0)

    # Saturation proxy: spread between max and min channel.
    cmax = np.maximum.reduce([r, g, b])
    cmin = np.minimum.reduce([r, g, b])
    saturation = cmax - cmin  # ∈ [0, 1]

    # Building score: bright, low-saturation, low-greenness.
    building_score = brightness * (1.0 - 1.5 * greenness) * (1.0 - saturation)
    building_score = np.clip(building_score, 0.0, 1.0)

    # Threshold: only pixels with score > 0.25 register as buildings.
    score = np.where(building_score > 0.25, building_score, 0.0)

    # Map score to metres. Linear: score 0.25 → ~3 m, score 1.0 → max_h_m.
    height = score * max_h_m
    return height.astype(np.float32)


_BASELINES: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "gray_minus_green": gray_minus_green,
    "constant_mean":    lambda rgb: np.full(rgb.shape[1:], 7.0, dtype=np.float32),
    "constant_zero":    lambda rgb: np.zeros(rgb.shape[1:], dtype=np.float32),
}


def evaluate(tiles_dir: Path, tile_size: int, baseline: str,
             seed: int = 42, val_frac: float = 0.15) -> dict:
    """Run a baseline on the validation split. Returns metrics dict."""
    fn = _BASELINES.get(baseline)
    if fn is None:
        raise ValueError(f"unknown baseline {baseline!r}; choose from {list(_BASELINES)}")

    paths = sorted(tiles_dir.glob("*.npz"))
    if not paths:
        raise FileNotFoundError(f"no tiles in {tiles_dir}")

    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(paths))
    n_val = max(1, int(val_frac * len(paths)))
    val_indices = set(perm[:n_val])

    maes = []
    rmses = []
    target_means = []  # for correlation analysis
    iou_num, iou_den = 0.0, 0.0

    for i, p in enumerate(paths):
        if i not in val_indices:
            continue
        with np.load(p) as d:
            rgb = d["rgb"]
            height = d["height"][0]  # (H, W)

        # Center-crop to tile_size if needed
        if height.shape[0] > tile_size:
            off = (height.shape[0] - tile_size) // 2
            height = height[off:off + tile_size, off:off + tile_size]
            rgb = rgb[:, off:off + tile_size, off:off + tile_size]

        pred = fn(rgb)
        target_mask = (height > 0).astype(np.float32)
        bldg_n = float(target_mask.sum()) or 1.0

        err = pred - height
        mae_pixels = (target_mask * np.abs(err)).sum() / bldg_n
        rmse_pixels = float(np.sqrt(((target_mask * err * err).sum()) / bldg_n))
        maes.append(mae_pixels)
        rmses.append(rmse_pixels)
        target_means.append(height[height > 0].mean() if (height > 0).any() else 0.0)

        # Mask IoU using a 3 m threshold on prediction
        pred_mask = (pred > 3.0).astype(np.float32)
        iou_num += float((pred_mask * target_mask).sum())
        iou_den += float((np.maximum(pred_mask, target_mask)).sum())

    if not maes:
        return {"baseline": baseline, "n_val": 0}

    maes = np.array(maes)
    rmses = np.array(rmses)
    target_means = np.array(target_means)
    pearson = (
        float(np.corrcoef(maes, target_means)[0, 1])
        if maes.std() > 0 and target_means.std() > 0
        else None
    )

    return {
        "baseline": baseline,
        "tile_dir": str(tiles_dir),
        "tile_size": tile_size,
        "n_val": int(maes.size),
        "val_mae_mean":   float(maes.mean()),
        "val_mae_median": float(np.median(maes)),
        "val_rmse_mean":  float(rmses.mean()),
        "val_iou":        iou_num / max(iou_den, 1.0),
        "pearson_mae_height": pearson,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", required=True)
    ap.add_argument("--tile-size", type=int, default=128)
    ap.add_argument("--baseline", default="gray_minus_green",
                    choices=list(_BASELINES.keys()))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    strm2stl = Path(__file__).resolve().parents[2]
    tiles = Path(args.tiles)
    if not tiles.is_absolute():
        tiles = strm2stl / args.tiles

    result = evaluate(tiles, args.tile_size, args.baseline, args.seed)
    print(json.dumps(result, indent=2))

    # Also record on the scoreboard for direct comparison with neural runs.
    try:
        from tools.ml.eval.scoreboard import record_run
        record_run(
            model_path=f"baseline://{args.baseline}",
            arch=f"baseline_{args.baseline}",
            task="height",
            best_metrics={
                "val_loss": None,
                "val_mae": result.get("val_mae_mean"),
                "val_rmse": result.get("val_rmse_mean"),
                "val_mask_iou": result.get("val_iou"),
                "pearson_mae_height": result.get("pearson_mae_height"),
            },
            n_train=0, n_val=result.get("n_val", 0), epochs=0,
            config={"baseline": args.baseline, "tile_size": args.tile_size},
            notes=f"tile_dir={tiles.name}",
        )
    except Exception as e:
        print(f"(scoreboard record failed: {e})")


if __name__ == "__main__":
    main()
