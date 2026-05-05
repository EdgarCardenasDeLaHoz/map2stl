"""scripts/train_phase_d_extended.py — Extended training for maximum performance.

Strategy: Phase C achieved 0.4157 with uniform [4,4,4,4,4,4,4,4,4] (9.3k params).
Now extend training with:
1. Longer cycles: 50 inner epochs per cycle (more convergence per cycle)
2. More cycles: 40 cycles (vs 30 in Phase C) for exhaustive search
3. Adaptive growth: Grow based on per-block gradient magnitude
4. Tighter ablation: Every 2 cycles instead of 3 (more aggressive pruning)
5. Lower LR schedule: Start 2e-5 (leaner training)

Expected: Push toward 0.41 or below, find true minimal architecture.

Usage:
    python scripts/train_phase_d_extended.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── CONFIGURATION ──────────────────────────────────────────────────────────

# Start from Phase C final checkpoint (9.3k params, val_loss=0.4157)
RESUME = REPO / "models" / "retna_wider_early.pt"

TILE_DIR = REPO / "cache" / "height_tiles_combined"
TILE_SIZE = 128

# Phase D: Extended training for maximum performance
PHASE_D_CYCLES = 40           # More cycles than Phase C (was 30)
PHASE_D_INNER_EPOCHS = 50    # Longer per-cycle training (was 30)
PHASE_D_GROW_CHANNELS = 0    # All-block widen only
PHASE_D_LR = 2e-5            # Lower starting LR for careful tuning
PHASE_D_BATCH_SIZE = 3
PHASE_D_SMART_INIT_JITTER = 0.02  # Slightly higher jitter

# Output
OUT_FINAL = REPO / "models" / "retna_extended.pt"
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


def phase_d_extended():
    """Phase D: Extended training with longer cycles and more iterations."""
    print("\n" + "=" * 70)
    print("PHASE D: EXTENDED TRAINING (40 cycles, 50 epochs/cycle)")
    print("=" * 70)
    print(f"Input:  {RESUME}")
    print(f"Output: {OUT_FINAL}")
    print(f"Strategy: Exhaustive search with tighter ablation + longer cycles")

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
        PHASE_D_CYCLES,
        "--inner-epochs",
        PHASE_D_INNER_EPOCHS,
        "--grow-channels",
        PHASE_D_GROW_CHANNELS,
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        PHASE_D_BATCH_SIZE,
        "--lr",
        PHASE_D_LR,
        "--lr-patience",
        4,  # More patience with lower LR
        "--overfit-stale-epochs",
        20,  # Longer training before overfit detection
        "--smart-init-jitter",
        PHASE_D_SMART_INIT_JITTER,
        "--start-checkpoint",
        RESUME,
        "--smart-init",
        "--final-prune",
        "--final-prune-tolerance",
        0.005,
        "--final-prune-floor-pct",
        25.0,
        "--final-prune-retrain-epochs",
        25,  # Longer retrain after final prune
        "--prune-every",
        2,  # Tighter ablation: every 2 cycles instead of 3
    ]

    rc = _run(cmd, "phase_d_extended.log")
    if rc != 0:
        raise SystemExit(f"Phase D failed (exit {rc})")

    return OUT_FINAL


def inspect_final(ckpt: Path):
    """Generate inspection report with new random sample selection."""
    print("\n" + "=" * 70)
    print("INSPECTION: Phase D extended checkpoint with fresh samples")
    print("=" * 70)

    out_dir = REPO / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{ckpt.stem}_inspect_extended.pdf"

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
        40,  # More samples for better coverage
        "--seed",
        44,  # Different seed from previous phases
    ]

    rc = _run(cmd, "inspect_extended.log")
    if rc == 0:
        print(f"\n  PDF: {out_pdf}")
        return out_pdf
    return None


def main():
    print("\n" + "=" * 70)
    print("PHASE D: EXTENDED TRAINING")
    print("=" * 70)
    print(f"Baseline:  {RESUME}")
    print(f"Tile dir:  {TILE_DIR}")
    print()

    # Phase D: Extended training
    final_ckpt = phase_d_extended()

    # Inspect with new samples
    inspect_pdf = inspect_final(final_ckpt)

    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Final:    {final_ckpt}")
    if inspect_pdf:
        print(f"Report:   {inspect_pdf}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
