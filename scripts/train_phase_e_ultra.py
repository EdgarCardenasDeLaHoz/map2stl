"""scripts/train_phase_e_ultra.py — Ultra-refined training for peak performance.

Strategy: After Phase D completes, do one final polish with:
1. Even longer cycles: 80 epochs per cycle (extensive per-cycle convergence)
2. Many cycles: 50 cycles (comprehensive search)
3. Minimal growth: +0.5 channels per cycle (careful micro-improvements)
4. Aggressive ablation: Every cycle (keep model minimal)
5. Very low LR: 1e-5 (fine-grained optimization)
6. High dropout: Stronger regularization

Expected: Squeeze out final 0.1-0.2% improvement, approaching theoretical limit.

Usage (called after Phase D):
    python scripts/train_phase_e_ultra.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── CONFIGURATION ──────────────────────────────────────────────────────────

# Start from Phase D final checkpoint
RESUME = REPO / "models" / "retna_extended.pt"

TILE_DIR = REPO / "cache" / "height_tiles_combined"
TILE_SIZE = 128

# Phase E: Ultra-refined training for peak performance
PHASE_E_CYCLES = 50           # Even more cycles (was 40 in Phase D)
PHASE_E_INNER_EPOCHS = 80    # Longest per-cycle training
PHASE_E_GROW_CHANNELS = 0    # All-block widen only (no extra growth)
PHASE_E_LR = 1e-5            # Very low LR for fine-grained optimization
PHASE_E_BATCH_SIZE = 3
PHASE_E_SMART_INIT_JITTER = 0.03  # Even higher jitter for exploration

# Output
OUT_FINAL = REPO / "models" / "retna_ultra.pt"
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


def phase_e_ultra():
    """Phase E: Ultra-refined training with longest cycles and aggressive ablation."""
    print("\n" + "=" * 70)
    print("PHASE E: ULTRA-REFINED TRAINING (50 cycles, 80 epochs/cycle)")
    print("=" * 70)
    print(f"Input:  {RESUME}")
    print(f"Output: {OUT_FINAL}")
    print(f"Strategy: Peak performance through exhaustive refinement")

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
        PHASE_E_CYCLES,
        "--inner-epochs",
        PHASE_E_INNER_EPOCHS,
        "--grow-channels",
        PHASE_E_GROW_CHANNELS,
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        PHASE_E_BATCH_SIZE,
        "--lr",
        PHASE_E_LR,
        "--lr-patience",
        5,  # Maximum patience with low LR
        "--overfit-stale-epochs",
        25,  # Even longer training
        "--smart-init-jitter",
        PHASE_E_SMART_INIT_JITTER,
        "--start-checkpoint",
        RESUME,
        "--smart-init",
        "--final-prune",
        "--final-prune-tolerance",
        0.003,  # Tighter tolerance
        "--final-prune-floor-pct",
        30.0,  # More aggressive floor
        "--final-prune-retrain-epochs",
        30,  # Longest final retrain
        "--prune-every",
        1,  # Ablate every cycle (maximum tightness)
    ]

    rc = _run(cmd, "phase_e_ultra.log")
    if rc != 0:
        raise SystemExit(f"Phase E failed (exit {rc})")

    return OUT_FINAL


def inspect_final(ckpt: Path):
    """Generate inspection report with new random sample selection."""
    print("\n" + "=" * 70)
    print("INSPECTION: Phase E ultra checkpoint with fresh samples")
    print("=" * 70)

    out_dir = REPO / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{ckpt.stem}_inspect_ultra.pdf"

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
        50,  # Even more samples
        "--seed",
        45,  # Different seed
    ]

    rc = _run(cmd, "inspect_ultra.log")
    if rc == 0:
        print(f"\n  PDF: {out_pdf}")
        return out_pdf
    return None


def main():
    print("\n" + "=" * 70)
    print("PHASE E: ULTRA-REFINED TRAINING")
    print("=" * 70)
    print(f"Baseline:  {RESUME}")
    print(f"Tile dir:  {TILE_DIR}")
    print()

    # Phase E: Ultra-refined training
    final_ckpt = phase_e_ultra()

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
