"""Phase G High-Resolution Training: 512×512 px at 1 m/pixel.

Combines two improvements:
  1. Higher resolution (512×512 px at 1 m/px = double pixel density)
  2. Random crops (192×192 from 512×512 for multi-scale learning)

Expected improvement: Better building boundaries + roof detail.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def main():
    print("\n" + "=" * 70)
    print("PHASE G: HIGH-RESOLUTION TRAINING (512×512 @ 1 m/px)")
    print("=" * 70)
    print()

    global_hires_dir = REPO / "cache" / "height_tiles_global_hires"

    if not global_hires_dir.exists():
        print(f"ERROR: High-res dataset not found at {global_hires_dir}")
        print("Please run collection first:")
        print("  python scripts/recollect_tiles_hires.py")
        return 1

    tile_count = len(list(global_hires_dir.glob("*.npz")))
    print(f"Found {tile_count} high-res tiles in {global_hires_dir}")
    print()

    output_model = REPO / "models" / "retna_phase_g_hires.pt"

    print(f"Training configuration:")
    print(f"  Input tiles: 512×512 px at 1 m/pixel")
    print(f"  Crop size: 192×192 px (0.375 ratio, more context than 96×96)")
    print(f"  Effective samples: ~{tile_count * 5} (5x multiplication from crops)")
    print(f"  Output model: {output_model}")
    print()

    # Grow-prune command with high-res training
    cmd = [
        sys.executable, "-m", "tools.ml.train.grow_prune",
        "--tiles", str(global_hires_dir),
        "--output", str(output_model),
        # Grow-prune strategy
        "--cycles", "10",           # 10 cycles (enough for convergence)
        "--inner-epochs", "35",     # 35 epochs per cycle (longer for high-res)
        "--grow-channels", "1",
        # High-res crop training
        "--tile-size", "192",       # 192×192 crops from 512×512 tiles
        "--batch-size", "4",        # Smaller batch: high-res uses more memory
        # Learning rate
        "--lr", "8e-6",             # Slightly lower LR for high-res stability
        "--lr-patience", "6",
        # Smart init
        "--smart-init",
        "--smart-init-jitter", "0.02",
        # Final pruning
        "--final-prune",
        "--final-prune-tolerance", "0.01",
        "--final-prune-floor-pct", "25.0",
        "--final-prune-retrain-epochs", "25",
        "--prune-every", "3",
        # Warmstart from retna_pruned
        "--start-checkpoint", str(REPO / "models" / "retna_pruned.pt"),
    ]

    print("Running high-res grow-prune training:")
    print(f"  Tiles: 512×512 px")
    print(f"  Crops: 192×192 px (random position + flip)")
    print(f"  Cycles: 10 | Epochs: 35 | Batch: 4 | LR: 8e-6")
    print()

    try:
        result = subprocess.run(
            cmd,
            cwd=str(REPO),
            capture_output=False,
        )
        if result.returncode != 0:
            print(f"\nERROR: Training failed with exit code {result.returncode}")
            return result.returncode

        print("\n" + "=" * 70)
        print("HIGH-RES TRAINING COMPLETE")
        print("=" * 70)
        print(f"\nOutput model: {output_model}")
        print("\nNext steps:")
        print("  1. Evaluate per-tile metrics:")
        print("     python scripts/extract_phase_g_tile_metrics.py \\")
        print(f"       --checkpoint {output_model}")
        print("  2. Compare high-res vs standard resolution:")
        print("     - Measure improvement in building IoU")
        print("     - Check regional performance (EU/US)")
        print("  3. If improvement > 5%, integrate high-res into data pipeline")
        print()

        return 0

    except Exception as e:
        print(f"\nERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
