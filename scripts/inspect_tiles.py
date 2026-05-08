"""Visual inspection tool: load and display problematic tiles."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def main():
    ap = argparse.ArgumentParser(
        description="Inspect individual tiles by examining NPZ contents"
    )
    ap.add_argument(
        "--tile-idx",
        type=int,
        required=True,
        help="Tile index to inspect (0-14)",
    )
    ap.add_argument(
        "--tiles-dir",
        default="cache/height_tiles_combined",
        help="Path to tiles directory",
    )
    ap.add_argument(
        "--stats-only",
        action="store_true",
        help="Print stats only (no matplotlib display)",
    )
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[1]
    tiles_dir = Path(args.tiles_dir)
    if not tiles_dir.is_absolute():
        tiles_dir = repo / tiles_dir

    tile_path = tiles_dir / f"tile_{args.tile_idx}.npz"
    if not tile_path.exists():
        print(f"ERROR: Tile not found: {tile_path}")
        return 1

    # Load tile
    data = np.load(tile_path)
    rgb = data["rgb"]  # Shape: (3, 128, 128)
    target = data["target"]  # Shape: (1, 128, 128)

    print("\n" + "=" * 70)
    print(f"TILE {args.tile_idx} INSPECTION")
    print("=" * 70)

    # Transpose for display
    rgb_hwc = np.transpose(rgb, (1, 2, 0))  # (128, 128, 3)
    target_hw = target[0]  # (128, 128)

    print(f"\nFile: {tile_path}")
    print(f"\nRGB (satellite imagery):")
    print(f"  Shape: {rgb_hwc.shape}")
    print(f"  Dtype: {rgb_hwc.dtype}")
    print(f"  Value range: [{rgb_hwc.min():.1f}, {rgb_hwc.max():.1f}]")
    print(f"  Mean: {rgb_hwc.mean():.2f}, Std: {rgb_hwc.std():.2f}")

    print(f"\nTarget height map (ground truth):")
    print(f"  Shape: {target_hw.shape}")
    print(f"  Dtype: {target_hw.dtype}")
    print(f"  Value range: [{target_hw.min():.1f}, {target_hw.max():.1f}]")
    print(f"  Mean: {target_hw.mean():.2f}, Std: {target_hw.std():.2f}")
    print(f"  Non-zero pixels: {(target_hw > 0).sum()} / {target_hw.size}")
    print(f"  Building pixels: {(target_hw > 0).sum() / target_hw.size * 100:.1f}%")

    # Analyze building heights
    mask = target_hw > 0
    if mask.any():
        heights = target_hw[mask] * 200  # Denormalize
        print(f"\nBuilding height statistics (in meters):")
        print(f"  Min: {heights.min():.1f}m")
        print(f"  Q1: {np.percentile(heights, 25):.1f}m")
        print(f"  Median: {np.median(heights):.1f}m")
        print(f"  Q3: {np.percentile(heights, 75):.1f}m")
        print(f"  Max: {heights.max():.1f}m")
        print(f"  Mean: {heights.mean():.1f}m")
        print(f"  Std: {heights.std():.1f}m")

    # Check for issues
    print(f"\nData Quality Checks:")
    rgb_uniform = rgb_hwc.std(axis=(0, 1))
    print(f"  • RGB channel std: R={rgb_uniform[0]:.2f}, G={rgb_uniform[1]:.2f}, B={rgb_uniform[2]:.2f}")
    if (rgb_uniform < 10).all():
        print(f"    ⚠ Low variance in RGB — possible low-quality or overexposed imagery")

    if mask.sum() < 100:
        print(f"  • ⚠ Very few building pixels ({mask.sum()}) — sparse city or detection issue")
    elif mask.sum() > target_hw.size * 0.9:
        print(f"  • ⚠ Nearly all pixels are buildings — unusual, check labels")

    if target_hw.std() == 0 and mask.any():
        print(f"  • ⚠ Uniform building height — may be synthetic or coarse labels")

    if target_hw.max() > 150:
        print(f"  • ⚠ Very tall buildings (>30m after denorm) — check label correctness")

    if args.stats_only:
        print("\n[Stats only mode]")
        return 0

    # Display visualization
    print(f"\nGenerating visualization...")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 3, figsize=(15, 5))

        # RGB
        axes[0].imshow(np.clip(rgb_hwc, 0, 1))
        axes[0].set_title("Satellite RGB")
        axes[0].set_xticks([]); axes[0].set_yticks([])

        # Target height
        im1 = axes[1].imshow(target_hw * 200, cmap="viridis")
        axes[1].set_title(f"Target Height Map\n(mean={target_hw.mean()*200:.1f}m, std={target_hw.std()*200:.1f}m)")
        axes[1].set_xticks([]); axes[1].set_yticks([])
        fig.colorbar(im1, ax=axes[1], label="height (m)")

        # Building mask
        axes[2].imshow(mask, cmap="gray")
        axes[2].set_title(f"Building Mask\n({mask.sum()} / {mask.size} pixels)")
        axes[2].set_xticks([]); axes[2].set_yticks([])

        fig.suptitle(f"Tile {args.tile_idx} Visual Inspection", fontsize=14, fontweight="bold")
        out_path = Path(f"output/tile_{args.tile_idx}_inspection.png")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=100, bbox_inches="tight")
        print(f"Wrote: {out_path}")
        plt.close(fig)
    except Exception as e:
        print(f"Could not generate visualization: {e}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
