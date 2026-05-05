"""scripts/train_phase_g_best_arch_larger_data.py — Best architecture on largest dataset.

Strategy: Phase G combines the best-discovered architecture [8,8,10,20,14,14,16,16,22] from
retna_pruned.pt with the larger, more diverse OSM dataset (437+77 tiles) that Phase E used.

Expected: Overcome data limitation of retna_pruned (only 111 train tiles, curated).
Result: More robust model that generalizes across diverse EU regions.
Target: 0.25-0.27 loss (best arch + best dataset)

Usage:
    python scripts/train_phase_g_best_arch_larger_data.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── CONFIGURATION ──────────────────────────────────────────────────────────

# Best model checkpoint (the one that achieved 0.2691 loss)
PHASE_G_INPUT = REPO / "models" / "retna_pruned.pt"

# Larger, more diverse dataset (100 train + ?  val tiles - combined dataset for testing)
TILE_DIR = REPO / "cache" / "height_tiles_combined"
TILE_SIZE = 128

# Phase G: Train best architecture on larger dataset
PHASE_G_CYCLES = 40            # Moderate cycles (balanced training)
PHASE_G_INNER_EPOCHS = 100     # Long per-cycle training
PHASE_G_GROW_CHANNELS = 0      # No growth (architecture already optimized)
PHASE_G_LR = 5e-6              # Ultra-low LR for fine-tuning
PHASE_G_BATCH_SIZE = 3
PHASE_G_SMART_INIT_JITTER = 0.02

# Output
PHASE_G_OUTPUT = REPO / "models" / "retna_phase_g_final.pt"
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


def phase_g_best_arch_larger_data():
    """Phase G: Train best architecture [8,8,10,20,14,14,16,16,22] on larger dataset."""
    print("\n" + "=" * 70)
    print("PHASE G: BEST ARCHITECTURE ON LARGER DATASET")
    print("=" * 70)
    print(f"Input:  {PHASE_G_INPUT.resolve()}")
    print(f"  Architecture: [8,8,10,20,14,14,16,16,22] (from retna_pruned)")
    print(f"  Previous loss: 0.2691 (on 111 train / 19 val)")
    print()
    print(f"Dataset: {TILE_DIR.resolve()}")
    print(f"  {TILE_DIR.name} ({sum(1 for _ in TILE_DIR.glob('*.npz'))} tiles)")
    print()
    print(f"Output: {PHASE_G_OUTPUT.resolve()}")
    print()
    print(f"Strategy: Combine best architecture with best dataset")
    print(f"  Cycles: {PHASE_G_CYCLES}")
    print(f"  Epochs/cycle: {PHASE_G_INNER_EPOCHS}")
    print(f"  LR: {PHASE_G_LR}")
    print(f"  Growth: {PHASE_G_GROW_CHANNELS} channels/cycle")
    print()
    print(f"Expected: 0.25-0.27 loss (improved from 0.2691)")
    print(f"Duration: ~6-8 hours")

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
        "25",
        "--smart-init-jitter",
        str(PHASE_G_SMART_INIT_JITTER),
        "--start-checkpoint",
        str(PHASE_G_INPUT.resolve()),
        "--smart-init",
        "--final-prune",
        "--final-prune-tolerance",
        "0.005",
        "--final-prune-floor-pct",
        "30.0",
        "--final-prune-retrain-epochs",
        "30",
        "--prune-every",
        "2",
    ]

    rc = _run(cmd, "phase_g_best_arch_larger_data.log")
    if rc != 0:
        raise SystemExit(f"Phase G failed (exit {rc})")
    return PHASE_G_OUTPUT


def main():
    print("\n" + "=" * 70)
    print("PHASE G: BEST ARCHITECTURE + LARGER DATASET")
    print("=" * 70)
    print(f"Starting from retna_pruned.pt (0.2691 loss, learned architecture)")
    print(f"Training on OSM dataset (437+77 tiles, all EU regions)")
    print()

    # Phase G: Train best arch on larger dataset
    print("\n" + "-" * 70)
    print("PHASE G: Main training")
    print("-" * 70)
    phase_g_ckpt = phase_g_best_arch_larger_data()

    print("\n" + "=" * 70)
    print("PHASE G COMPLETE")
    print("=" * 70)
    print(f"Output: {phase_g_ckpt}")
    print()
    print("Next steps:")
    print("  1. Review phase_g_best_arch_larger_data.log for final loss")
    print("  2. If loss < 0.2691: commit to git as new best model")
    print("  3. If loss >= 0.2691: keep retna_pruned as production")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
