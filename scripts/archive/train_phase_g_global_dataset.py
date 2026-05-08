"""Phase G: Best architecture on global dataset (EU + US + Cartagena for segmentation)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── CONFIGURATION ──────────────────────────────────────────────────────────

# Input: retna_phase_g_osm.pt from Stage 1
# Falls back to retna_pruned.pt if OSM checkpoint doesn't exist yet
CHECKPOINT_OSM = REPO / "models" / "retna_phase_g_osm.pt"
CHECKPOINT_FALLBACK = REPO / "models" / "retna_pruned.pt"
PHASE_G_INPUT = CHECKPOINT_OSM if CHECKPOINT_OSM.exists() else CHECKPOINT_FALLBACK

# Global dataset: EU (514) + US (240) + Cartagena (30) = 784 tiles
TILE_DIR = REPO / "cache" / "height_tiles_global"
TILE_SIZE = 128

# Phase G: Train on global dataset
PHASE_G_CYCLES = 15            # More cycles for larger dataset
PHASE_G_INNER_EPOCHS = 25      # Epochs per cycle
PHASE_G_GROW_CHANNELS = 1      # Grow by 1 channel/block/cycle
PHASE_G_LR = 5e-6              # Conservative (will adapt to mixed data)
PHASE_G_BATCH_SIZE = 4
PHASE_G_SMART_INIT_JITTER = 0.02

# Output
PHASE_G_OUTPUT = REPO / "models" / "retna_phase_g_global.pt"
LOG_DIR = REPO / "logs"

# ── END CONFIGURATION ──────────────────────────────────────────────────────


def _run(cmd: list, log_name: str) -> int:
    """Run subprocess with output tee."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_name
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    print(f"  log: {log_path}\n")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(REPO),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        for line in proc.stdout:
            sys.stdout.buffer.write(line)
            sys.stdout.flush()
            logf.write(line)
        return proc.wait()


def main():
    print("\n" + "=" * 70)
    print("PHASE G: BEST ARCHITECTURE ON GLOBAL DATASET")
    print("=" * 70)
    print(f"\nInput: {PHASE_G_INPUT.resolve()}")
    if PHASE_G_INPUT == CHECKPOINT_OSM and CHECKPOINT_OSM.exists():
        print(f"  (from Stage 1: OSM training)")
    else:
        print(f"  (fallback: retna_pruned.pt)")
    print(f"  Architecture: [8,8,10,20,14,14,16,16,22]")
    print()
    print(f"Dataset: {TILE_DIR.resolve()}")
    print(f"  European cities (514 tiles):")
    print(f"    Amsterdam, Barcelona, Berlin, Bruges, Cologne, Florence,")
    print(f"    Munich, Paris, Prague, Rotterdam, Vienna")
    print(f"  US cities (240 tiles):")
    print(f"    Philadelphia, Chicago, New York City, Boston, Los Angeles, Seattle")
    print(f"  Cartagena (30 tiles):")
    print(f"    For segmentation training (no height labels)")
    print(f"  Total: 784 tiles")
    print()
    print(f"Output: {PHASE_G_OUTPUT.resolve()}")
    print()
    print(f"Strategy: Grow + prune on global geographic dataset")
    print(f"  Cycles: {PHASE_G_CYCLES}")
    print(f"  Epochs/cycle: {PHASE_G_INNER_EPOCHS}")
    print(f"  LR: {PHASE_G_LR}")
    print(f"  Growth: +{PHASE_G_GROW_CHANNELS} channel/block/cycle")
    print(f"  Smart-init: Yes (clone high-scoring channels)")
    print(f"  Final pruning: Yes")
    print()
    print(f"Expected: MAE < 4.5m on global validation set")
    print(f"  • EU tiles: ~3.8-4.0m (similar to retna_pruned baseline)")
    print(f"  • US tiles: ~5.0-5.5m (taller buildings, harder)")
    print(f"  • Cartagena: IoU > 0.60 (segmentation only)")
    print(f"Duration: ~4-5 hours")
    print()

    # Check that tile directory exists
    if not TILE_DIR.exists():
        print(f"ERROR: Global dataset directory not found: {TILE_DIR}")
        print(f"\nTo create it, run:")
        print(f"  mkdir -p {TILE_DIR}")
        print(f"  cp {REPO / 'cache' / 'height_tiles_osm'}/*.npz {TILE_DIR}/")
        print(f"  cp {REPO / 'cache' / 'height_tiles_us'}/*.npz {TILE_DIR}/")
        print(f"  cp {REPO / 'cache' / 'height_tiles_osm_cartagena'}/*.npz {TILE_DIR}/")
        return 1

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.ml.train.grow_prune",
        "--tiles",
        str(TILE_DIR.resolve()),
        "--output",
        str(PHASE_G_OUTPUT.resolve()),
        "--cycles",
        str(PHASE_G_CYCLES),
        "--inner-epochs",
        str(PHASE_G_INNER_EPOCHS),
        "--grow-channels",
        str(PHASE_G_GROW_CHANNELS),
        "--tile-size",
        str(TILE_SIZE),
        "--batch-size",
        str(PHASE_G_BATCH_SIZE),
        "--lr",
        str(PHASE_G_LR),
        "--lr-patience",
        "5",
        "--overfit-stale-epochs",
        "12",
        "--smart-init-jitter",
        str(PHASE_G_SMART_INIT_JITTER),
        "--start-checkpoint",
        str(PHASE_G_INPUT.resolve()),
        "--smart-init",
        "--final-prune",
        "--final-prune-tolerance",
        "0.01",
        "--final-prune-floor-pct",
        "25.0",
        "--final-prune-retrain-epochs",
        "20",
        "--prune-every",
        "3",
    ]

    rc = _run(cmd, "phase_g_global.log")
    if rc != 0:
        raise SystemExit(f"Phase G global training failed (exit {rc})")

    print("\n" + "=" * 70)
    print("PHASE G GLOBAL TRAINING COMPLETE")
    print("=" * 70)
    print(f"Output: {PHASE_G_OUTPUT}")
    print()
    print("Evaluation:")
    print("  1. Extract per-tile metrics:")
    print("     python scripts/extract_phase_g_tile_metrics.py --checkpoint models/retna_phase_g_global.pt")
    print()
    print("  2. Compare to retna_pruned baseline:")
    print("     python scripts/compare_phase_g_vs_baseline.py")
    print()
    print("  3. Visualize regional performance:")
    print("     python scripts/visualize_regional_tiles.py")
    print()
    print("Success criteria:")
    print("  • Mean MAE < 4.5m")
    print("  • EU tiles MAE < 4.0m (match retna_pruned)")
    print("  • US tiles MAE < 5.5m (expected: taller buildings)")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
