"""scripts/train_phase_c_wider_early.py — Wider early layers + smarter channel cloning.

Strategy: Phase B ended with a lean model [8, 7, 9, 12, 6, 6, 7, 7, 7] (26k params, val_loss=0.4169).
Now grow from there with:
1. Wider early layers: block 0-3 start with 1.5x channels (12, 11, 14, 18 instead of 8, 7, 9, 12)
2. Smart-init v2: Clone top-scoring channels into early-layer slots first, then hot-blocks
3. Early-block focus: Extra growth in early layers (architecture bottleneck detection)

Expected: Better feature extraction early on, faster convergence, potentially < 0.41 val_loss.

Usage:
    python scripts/train_phase_c_wider_early.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── CONFIGURATION ──────────────────────────────────────────────────────────

# Start from Phase B final checkpoint (26k params, val_loss=0.4169)
RESUME = REPO / "models" / "retna_pruned_and_grown.pt"

# Phase B ended with: [8, 7, 9, 12, 6, 6, 7, 7, 7] (26,238 params)
# Phase C starts wider in early layers: [12, 11, 14, 18, 6, 6, 7, 7, 7] (early 1.5x growth)
# This provides more early feature capacity before the narrower middle layers

TILE_DIR = REPO / "cache" / "height_tiles_combined"
TILE_SIZE = 128

# Phase C: Grow from wider early-layer baseline (30 cycles, smart-init v2)
PHASE_C_CYCLES = 30
PHASE_C_INNER_EPOCHS = 30
PHASE_C_GROW_CHANNELS = 0       # All-block widen only
PHASE_C_LR = 3e-5
PHASE_C_BATCH_SIZE = 3
PHASE_C_SMART_INIT_JITTER = 0.01  # Slightly higher jitter for exploration

# Output
OUT_FINAL = REPO / "models" / "retna_wider_early.pt"
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


def phase_c_grow_wider():
    """Phase C: Grow from wider early-layer baseline with smart-init."""
    print("\n" + "=" * 70)
    print("PHASE C: GROW FROM WIDER EARLY-LAYER BASELINE (30 cycles)")
    print("=" * 70)
    print(f"Input:  {RESUME}")
    print(f"Output: {OUT_FINAL}")
    print(f"Strategy: Start [12, 11, 14, 18, 6, 6, 7, 7, 7] for wider early layers")

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
        PHASE_C_CYCLES,
        "--inner-epochs",
        PHASE_C_INNER_EPOCHS,
        "--grow-channels",
        PHASE_C_GROW_CHANNELS,
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        PHASE_C_BATCH_SIZE,
        "--lr",
        PHASE_C_LR,
        "--lr-patience",
        3,
        "--overfit-stale-epochs",
        15,
        "--smart-init-jitter",
        PHASE_C_SMART_INIT_JITTER,
        "--start-checkpoint",
        RESUME,
        "--initial-channels",
        "12", "11", "14", "18", "6", "6", "7", "7", "7",  # Wider early blocks (0-3: 1.5x)
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

    rc = _run(cmd, "phase_c_wider_early.log")
    if rc != 0:
        raise SystemExit(f"Phase C failed (exit {rc})")

    return OUT_FINAL


def inspect_final(ckpt: Path):
    """Generate inspection report with new random sample selection."""
    print("\n" + "=" * 70)
    print("INSPECTION: Phase C wider-early checkpoint with fresh samples")
    print("=" * 70)

    out_dir = REPO / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{ckpt.stem}_inspect_wider_early.pdf"

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
        30,
        "--seed",
        43,  # Different seed from Phase B (was 42)
    ]

    rc = _run(cmd, "inspect_wider_early.log")
    if rc == 0:
        print(f"\n  PDF: {out_pdf}")
        return out_pdf
    return None


def main():
    print("\n" + "=" * 70)
    print("PHASE C: WIDER EARLY LAYERS")
    print("=" * 70)
    print(f"Baseline:  {RESUME}")
    print(f"Tile dir:  {TILE_DIR}")
    print()

    # Phase C: Grow from wider early-layer baseline
    final_ckpt = phase_c_grow_wider()

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
