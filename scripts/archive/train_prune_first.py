"""scripts/train_prune_first.py — Prune current checkpoint, then grow/train.

Strategy: Start with Phase 2 checkpoint (or best current), run aggressive ablation
to remove dead channels, then grow again with fresh training.

This normally helps because:
1. Pruning removes noise/dead capacity first
2. Fresh growth from a leaner baseline is more targeted
3. Model can learn better without bloat

Usage:
    python scripts/train_prune_first.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── CONFIGURATION ──────────────────────────────────────────────────────────

# Start from Phase 2 checkpoint (already trained, has good baseline)
RESUME = REPO / "models" / "retna_phase2_regression.pt"
HIDDEN = [8, 8, 10, 20, 14, 14, 16, 16, 22]  # Phase 2 architecture

TILE_DIR = REPO / "cache" / "height_tiles_combined"
TILE_SIZE = 128

# Phase A: Aggressive pruning (single-channel ablation + compact + retrain)
PHASE_A_PRUNE_TOLERANCE = 0.01  # Accept up to +0.01 loss when zeroing channels
PHASE_A_PRUNE_FLOOR_PCT = 20.0  # Only test bottom-20% by grad×act (most dead)
PHASE_A_RETRAIN_EPOCHS = 20     # Recovery training after pruning

# Phase B: Grow from pruned checkpoint (fresh cycles)
PHASE_B_CYCLES = 30
PHASE_B_INNER_EPOCHS = 30
PHASE_B_GROW_CHANNELS = 0       # All-block widen only (no extra growth)
PHASE_B_LR = 3e-5
PHASE_B_BATCH_SIZE = 3

# Output
OUT_PRUNED = REPO / "models" / "retna_pruned_first.pt"
OUT_FINAL = REPO / "models" / "retna_pruned_and_grown.pt"
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


def phase_a_prune():
    """Phase A: Aggressive single-channel pruning + retrain."""
    print("\n" + "=" * 70)
    print("PHASE A: AGGRESSIVE PRUNING (single-channel ablation)")
    print("=" * 70)
    print(f"Input:  {RESUME}")
    print(f"Output: {OUT_PRUNED}")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.ml.train.grow_prune",
        "--tiles",
        TILE_DIR,
        "--output",
        OUT_PRUNED,
        "--cycles",
        0,  # Don't grow, just prune
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        PHASE_B_BATCH_SIZE,
        "--lr",
        PHASE_B_LR,
        "--start-checkpoint",
        RESUME,
        "--final-prune",
        "--final-prune-tolerance",
        PHASE_A_PRUNE_TOLERANCE,
        "--final-prune-floor-pct",
        PHASE_A_PRUNE_FLOOR_PCT,
        "--final-prune-retrain-epochs",
        PHASE_A_RETRAIN_EPOCHS,
        "--no-smart-init",
    ]

    rc = _run(cmd, "phase_a_pruning.log")
    if rc != 0:
        raise SystemExit(f"Phase A failed (exit {rc})")

    print("\n  Pruning complete. Checkpoint ready for growth phase.")
    return OUT_PRUNED


def phase_b_grow():
    """Phase B: Grow from pruned baseline."""
    print("\n" + "=" * 70)
    print("PHASE B: GROW FROM PRUNED BASELINE (30 cycles)")
    print("=" * 70)
    print(f"Input:  {OUT_PRUNED}")
    print(f"Output: {OUT_FINAL}")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.ml.train.grow_prune",
        "--tiles",
        TILE_DIR,
        "--output",
        OUT_FINAL,
        "--cycles",
        PHASE_B_CYCLES,
        "--inner-epochs",
        PHASE_B_INNER_EPOCHS,
        "--grow-channels",
        PHASE_B_GROW_CHANNELS,
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        PHASE_B_BATCH_SIZE,
        "--lr",
        PHASE_B_LR,
        "--lr-patience",
        3,
        "--overfit-stale-epochs",
        15,
        "--smart-init-jitter",
        0.0,
        "--start-checkpoint",
        OUT_PRUNED,
        "--smart-init",
        "--final-prune",
        "--final-prune-tolerance",
        0.005,
        "--final-prune-floor-pct",
        25.0,
        "--final-prune-retrain-epochs",
        20,
        "--prune-every",
        3,
    ]

    rc = _run(cmd, "phase_b_growing.log")
    if rc != 0:
        raise SystemExit(f"Phase B failed (exit {rc})")

    return OUT_FINAL


def inspect_final(ckpt: Path):
    """Generate inspection report with new random sample selection."""
    print("\n" + "=" * 70)
    print("INSPECTION: Final checkpoint with fresh sample selection")
    print("=" * 70)

    out_dir = REPO / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{ckpt.stem}_inspect_pruned_and_grown.pdf"

    cmd = [
        sys.executable,
        "-m",
        "tools.ml.analysis.inspect_retna",
        "--checkpoint",
        ckpt,
        "--tiles",
        TILE_DIR,
        "--out",
        out_pdf,
        "--tile-size",
        TILE_SIZE,
        "--n-samples",
        30,  # More samples for better coverage
        "--seed",
        42,  # Different seed = new random selection
    ]

    rc = _run(cmd, "inspect_pruned_and_grown.log")
    if rc == 0:
        print(f"\n  PDF: {out_pdf}")
        return out_pdf
    return None


def main():
    print("\n" + "=" * 70)
    print("PRUNE-FIRST STRATEGY")
    print("=" * 70)
    print(f"Baseline:  {RESUME}")
    print(f"Tile dir:  {TILE_DIR}")
    print()

    # Phase A: Aggressive pruning
    pruned_ckpt = phase_a_prune()

    # Phase B: Grow from pruned baseline
    final_ckpt = phase_b_grow()

    # Inspect with new samples
    inspect_pdf = inspect_final(final_ckpt)

    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Pruned:   {pruned_ckpt}")
    print(f"Final:    {final_ckpt}")
    if inspect_pdf:
        print(f"Report:   {inspect_pdf}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
