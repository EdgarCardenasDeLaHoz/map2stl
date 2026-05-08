"""scripts/train_two_phase.py — Two-phase height training: segmentation + regression.

Phase 1: Train a building mask segmentor (binary classification)
  - Loss: Dice on building/no-building mask
  - Goal: Learn WHERE buildings are, independent of height
  - Epochs: 20, learning rate higher (5e-5)

Phase 2: Freeze segmentation head, train height regressor
  - Loss: MSE on (predicted height | building pixels only)
  - Goal: Learn WHAT HEIGHT given we know where buildings are
  - Epochs: 15, learning rate lower (3e-5)
  - Optional: Unfreeze later layers for joint fine-tune

This decomposes the task and avoids the marginal-mean collapse problem
because the segmentation phase creates a strong building signal early on.

Usage:
    python scripts/train_two_phase.py phase1   # train segmentation
    python scripts/train_two_phase.py phase2   # train regression
    python scripts/train_two_phase.py both     # run both sequentially
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── CONFIGURATION ──────────────────────────────────────────────────────────

CITIES = [
    "Amsterdam", "Barcelona", "Berlin", "Vienna", "Paris", "Prague",
    "Rotterdam", "Cologne", "Bruges", "Florence", "Munich",
]
TILES_PER_CITY = 60
TILE_SIZE = 128
TILE_DIR = REPO / "cache" / "height_tiles_combined"

# Phase 1: Segmentation (building mask)
PHASE1_RESUME = None  # cold start
PHASE1_HIDDEN = [8, 8, 10, 20, 14, 14, 16, 16, 22]
PHASE1_EPOCHS = 20
PHASE1_LR = 5e-5
PHASE1_LOSS = "dice"  # pure Dice for binary mask
PHASE1_OUT = REPO / "models" / "retna_phase1_segmentation.pt"

# Phase 2: Regression (height given mask)
PHASE2_RESUME = PHASE1_OUT  # load from phase 1
PHASE2_HIDDEN = PHASE1_HIDDEN  # same architecture
PHASE2_EPOCHS = 15
PHASE2_LR = 3e-5
PHASE2_LOSS = "dice_l2"  # Dice + MSE (L2_WEIGHT=2.0 emphasizes magnitude)
PHASE2_L2_WEIGHT = 2.0  # Higher L2 weight for regression focus
PHASE2_OUT = REPO / "models" / "retna_phase2_regression.pt"

BATCH_SIZE = 3
TILE_SIZE = 128

# ── END CONFIGURATION ──────────────────────────────────────────────────────


def _run(cmd: list, log_name: str) -> int:
    """Run a subprocess, tee output to logs/<log_name>.log."""
    log_dir = REPO / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_name
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    print(f"  log: {log_path}\n")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(
            [str(c) for c in cmd], cwd=str(REPO), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
        )
        for line in proc.stdout:
            sys.stdout.buffer.write(line)
            sys.stdout.flush()
            logf.write(line)
        return proc.wait()


def _train_phase1():
    """Phase 1: Train building segmentation (Dice loss on binary mask)."""
    print("\n" + "=" * 70)
    print("PHASE 1: BUILDING SEGMENTATION (Dice loss, binary mask)")
    print("=" * 70)

    out = PHASE1_OUT
    cmd = [
        sys.executable, "-u", "-m", "tools.ml.train.train_retna",
        "--tiles", TILE_DIR, "--output", out,
        "--hidden-channels", *map(str, PHASE1_HIDDEN),
        "--epochs", PHASE1_EPOCHS, "--tile-size", TILE_SIZE,
        "--batch-size", BATCH_SIZE, "--lr", PHASE1_LR,
        "--loss", PHASE1_LOSS, "--l2-weight", 0.0,
    ]
    if PHASE1_RESUME and PHASE1_RESUME.exists():
        cmd += ["--resume", PHASE1_RESUME]

    rc = _run(cmd, "phase1_segmentation.log")
    if rc != 0:
        raise SystemExit(f"Phase 1 failed (exit {rc})")
    return out


def _train_phase2():
    """Phase 2: Train height regression (Dice + high L2 weight for magnitude)."""
    print("\n" + "=" * 70)
    print("PHASE 2: HEIGHT REGRESSION (Dice + L2, emphasis on magnitude)")
    print("=" * 70)

    out = PHASE2_OUT
    cmd = [
        sys.executable, "-u", "-m", "tools.ml.train.train_retna",
        "--tiles", TILE_DIR, "--output", out,
        "--hidden-channels", *map(str, PHASE2_HIDDEN),
        "--epochs", PHASE2_EPOCHS, "--tile-size", TILE_SIZE,
        "--batch-size", BATCH_SIZE, "--lr", PHASE2_LR,
        "--loss", PHASE2_LOSS, "--l2-weight", PHASE2_L2_WEIGHT,
    ]
    if PHASE2_RESUME and PHASE2_RESUME.exists():
        cmd += ["--resume", PHASE2_RESUME]

    rc = _run(cmd, "phase2_regression.log")
    if rc != 0:
        raise SystemExit(f"Phase 2 failed (exit {rc})")
    return out


def _inspect(ckpt: Path, phase: str):
    """Render inspection PDF for a checkpoint."""
    out_dir = REPO / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{ckpt.stem}_inspect_{phase}.pdf"
    cmd = [
        sys.executable, "-m", "tools.ml.analysis.inspect_retna",
        "--checkpoint", ckpt, "--tiles", TILE_DIR,
        "--out", out_pdf, "--tile-size", TILE_SIZE, "--n-samples", 20,
    ]
    rc = _run(cmd, f"inspect_{ckpt.stem}_{phase}.log")
    if rc == 0:
        print(f"\n  PDF: {out_pdf}")
    return out_pdf if rc == 0 else None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    print(f"mode: {mode}")

    if mode == "phase1":
        out = _train_phase1()
        _inspect(out, "phase1")
    elif mode == "phase2":
        if not PHASE2_RESUME.exists():
            print(f"ERROR: Phase 1 checkpoint not found at {PHASE2_RESUME}")
            print("Run 'python scripts/train_two_phase.py phase1' first")
            return 1
        out = _train_phase2()
        _inspect(out, "phase2")
    elif mode == "both":
        out1 = _train_phase1()
        _inspect(out1, "phase1")
        out2 = _train_phase2()
        _inspect(out2, "phase2")
        print("\n" + "=" * 70)
        print("BOTH PHASES COMPLETE")
        print(f"  Phase 1 (segmentation): {out1}")
        print(f"  Phase 2 (regression):   {out2}")
        print("=" * 70)
    else:
        print(f"unknown mode: {mode}")
        print("modes: phase1, phase2, both")
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
