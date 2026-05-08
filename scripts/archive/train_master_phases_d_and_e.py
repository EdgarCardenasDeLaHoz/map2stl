"""scripts/train_master_phases_d_and_e.py — Chain Phase D and E for continuous optimization.

After Phase C achieves 0.4157 loss with 9.3k params, run two more phases:
1. Phase D: Extended training (40 cycles, 50 epochs/cycle, LR 2e-5)
2. Phase E: Ultra-refined training (50 cycles, 80 epochs/cycle, LR 1e-5)

This script runs both sequentially, generating inspection reports after each.

Usage:
    python scripts/train_master_phases_d_and_e.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── CONFIGURATION ──────────────────────────────────────────────────────────

TILE_DIR = REPO / "cache" / "height_tiles_combined"
TILE_SIZE = 128
LOG_DIR = REPO / "logs"

# Phase D: Extended training
PHASE_D_INPUT = REPO / "models" / "retna_wider_early.pt"
PHASE_D_OUTPUT = REPO / "models" / "retna_extended.pt"
PHASE_D_CYCLES = 40
PHASE_D_EPOCHS = 50
PHASE_D_LR = 2e-5
PHASE_D_ABLATE = 2

# Phase E: Ultra-refined training
PHASE_E_INPUT = PHASE_D_OUTPUT  # Chain from Phase D
PHASE_E_OUTPUT = REPO / "models" / "retna_ultra.pt"
PHASE_E_CYCLES = 50
PHASE_E_EPOCHS = 80
PHASE_E_LR = 1e-5
PHASE_E_ABLATE = 1

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


def phase_d():
    """Phase D: Extended training."""
    print("\n" + "=" * 70)
    print("PHASE D: EXTENDED TRAINING (40 cycles, 50 epochs/cycle)")
    print("=" * 70)
    print(f"Input:  {PHASE_D_INPUT}")
    print(f"Output: {PHASE_D_OUTPUT}")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.ml.train.grow_prune",
        "--tiles",
        TILE_DIR,
        "--output",
        PHASE_D_OUTPUT,
        "--cycles",
        PHASE_D_CYCLES,
        "--inner-epochs",
        PHASE_D_EPOCHS,
        "--grow-channels",
        0,
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        3,
        "--lr",
        PHASE_D_LR,
        "--lr-patience",
        4,
        "--overfit-stale-epochs",
        20,
        "--smart-init-jitter",
        0.02,
        "--start-checkpoint",
        PHASE_D_INPUT,
        "--smart-init",
        "--final-prune",
        "--final-prune-tolerance",
        0.005,
        "--final-prune-floor-pct",
        25.0,
        "--final-prune-retrain-epochs",
        25,
        "--prune-every",
        PHASE_D_ABLATE,
    ]

    rc = _run(cmd, "phase_d_extended.log")
    if rc != 0:
        raise SystemExit(f"Phase D failed (exit {rc})")
    return PHASE_D_OUTPUT


def inspect_phase(ckpt: Path, phase_name: str, n_samples: int, seed: int) -> Path:
    """Generate inspection report."""
    print(f"\n  Inspecting {phase_name}...")

    out_dir = REPO / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{ckpt.stem}_inspect_{phase_name.lower()}.pdf"

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
        n_samples,
        "--seed",
        seed,
    ]

    rc = _run(cmd, f"inspect_{phase_name.lower()}.log")
    if rc == 0:
        print(f"  PDF: {out_pdf}")
        return out_pdf
    return None


def phase_e(input_ckpt: Path):
    """Phase E: Ultra-refined training."""
    print("\n" + "=" * 70)
    print("PHASE E: ULTRA-REFINED TRAINING (50 cycles, 80 epochs/cycle)")
    print("=" * 70)
    print(f"Input:  {input_ckpt}")
    print(f"Output: {PHASE_E_OUTPUT}")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.ml.train.grow_prune",
        "--tiles",
        TILE_DIR,
        "--output",
        PHASE_E_OUTPUT,
        "--cycles",
        PHASE_E_CYCLES,
        "--inner-epochs",
        PHASE_E_EPOCHS,
        "--grow-channels",
        0,
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        3,
        "--lr",
        PHASE_E_LR,
        "--lr-patience",
        5,
        "--overfit-stale-epochs",
        25,
        "--smart-init-jitter",
        0.03,
        "--start-checkpoint",
        input_ckpt,
        "--smart-init",
        "--final-prune",
        "--final-prune-tolerance",
        0.003,
        "--final-prune-floor-pct",
        30.0,
        "--final-prune-retrain-epochs",
        30,
        "--prune-every",
        PHASE_E_ABLATE,
    ]

    rc = _run(cmd, "phase_e_ultra.log")
    if rc != 0:
        raise SystemExit(f"Phase E failed (exit {rc})")
    return PHASE_E_OUTPUT


def main():
    print("\n" + "=" * 70)
    print("MASTER: PHASES D AND E")
    print("=" * 70)
    print()

    # Phase D
    phase_d_ckpt = phase_d()
    inspect_phase(phase_d_ckpt, "Extended", 40, 44)

    # Phase E
    phase_e_ckpt = phase_e(phase_d_ckpt)
    inspect_phase(phase_e_ckpt, "Ultra", 50, 45)

    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Phase D: {phase_d_ckpt}")
    print(f"Phase E: {phase_e_ckpt}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
