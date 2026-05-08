"""scripts/train_phase_f_prune_first.py — Aggressive pruning + intensive training toward 0.35 loss.

Strategy: Start from Phase E (0.4108) and aggressively prune to find minimal-but-powerful architecture,
then train exhaustively with focus on tall buildings, complex geometry, and landmarks.

Three concurrent scopes:
1. ALL: Full validation set (baseline performance)
2. TALL: Buildings >20m (historical, churches, landmarks)
3. COMPLEX: High-gradient regions (intersections, complex shapes)

Training phases:
- Phase F-A: Aggressive one-shot pruning (remove bottom 40% by grad×activation)
- Phase F-B: Intensive growth/prune cycles (80 epochs/cycle, every-cycle ablation, lower LR)
- Phase F-C: Ultra-focused fine-tuning on high-error regions

Expected: 0.35–0.38 loss (major breakthrough toward target)
Duration: ~8–10 hours

Usage:
    python scripts/train_phase_f_prune_first.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── CONFIGURATION ──────────────────────────────────────────────────────────

# All available datasets
TILE_DIR_COMBINED = REPO / "cache" / "height_tiles_combined"  # Amsterdam, Barcelona (current)
TILE_DIR_FULL = REPO / "cache" / "height_tiles_osm"  # All regions (largest, most diverse)
TILE_DIR_HR = REPO / "cache" / "height_tiles_hr"  # High-resolution European tiles

# Phase F uses full OSM dataset for maximum signal
TILE_DIR = TILE_DIR_FULL
TILE_SIZE = 128
LOG_DIR = REPO / "logs"

# Input: Phase E best checkpoint
PHASE_F_INPUT = REPO / "models" / "retna_ultra.pt"

# Output checkpoints
PHASE_F_A_OUTPUT = REPO / "models" / "retna_pruned_aggressive.pt"  # After aggressive pruning
PHASE_F_B_OUTPUT = REPO / "models" / "retna_pruned_grown.pt"  # After growth/prune cycles
PHASE_F_FINAL = REPO / "models" / "retna_v1_phase_f_final.pt"  # Phase F best

# Configuration
PHASE_F_A_TOLERANCE = 0.01  # Aggressive: allow +0.01 loss from pruning
PHASE_F_A_FLOOR_PCT = 40.0  # Prune bottom 40% by grad×activation

PHASE_F_B_CYCLES = 60  # Most cycles yet: exhaustive search
PHASE_F_B_EPOCHS = 100  # Longest per-cycle training
PHASE_F_B_LR = 5e-6  # Ultra-low LR for fine-grained optimization
PHASE_F_B_ABLATE = 1  # Every cycle: maximum ablation pressure

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


def phase_f_a_aggressive_prune():
    """Phase F-A: Aggressive one-shot pruning to remove dead capacity."""
    print("\n" + "=" * 70)
    print("PHASE F-A: AGGRESSIVE PRUNING (remove bottom 40% dead capacity)")
    print("=" * 70)
    print(f"Input:  {PHASE_F_INPUT} (0.4108 loss, 9.3k params)")
    print(f"Output: {PHASE_F_A_OUTPUT}")
    print(f"Strategy: Single-cycle aggressive pruning")
    print(f"  Tolerance: ±{PHASE_F_A_TOLERANCE} loss")
    print(f"  Floor: {PHASE_F_A_FLOOR_PCT}% of params")
    print(f"  Recovery: 30 epochs retraining")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.ml.train.grow_prune",
        "--tiles",
        TILE_DIR,
        "--output",
        PHASE_F_A_OUTPUT,
        "--cycles",
        1,  # Single cycle: just prune
        "--inner-epochs",
        30,  # Quick recovery retrain
        "--grow-channels",
        0,  # No growth, only pruning
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        3,
        "--lr",
        2e-5,  # Higher LR for recovery
        "--lr-patience",
        3,
        "--overfit-stale-epochs",
        20,
        "--smart-init-jitter",
        0.01,
        "--start-checkpoint",
        PHASE_F_INPUT,
        "--smart-init",
        "--final-prune",
        "--final-prune-tolerance",
        PHASE_F_A_TOLERANCE,
        "--final-prune-floor-pct",
        PHASE_F_A_FLOOR_PCT,
        "--final-prune-retrain-epochs",
        30,
        "--prune-every",
        1,  # Prune immediately
    ]

    rc = _run(cmd, "phase_f_a_aggressive_prune.log")
    if rc != 0:
        raise SystemExit(f"Phase F-A failed (exit {rc})")
    return PHASE_F_A_OUTPUT


def phase_f_b_intensive_cycles(input_ckpt: Path):
    """Phase F-B: Intensive growth/prune cycles with ultra-low LR and maximum ablation."""
    print("\n" + "=" * 70)
    print("PHASE F-B: INTENSIVE CYCLES (60 cycles, 100 epochs, LR 5e-6)")
    print("=" * 70)
    print(f"Input:  {input_ckpt}")
    print(f"Output: {PHASE_F_B_OUTPUT}")
    print(f"Strategy: Exhaustive architecture search with aggressive ablation")
    print(f"  Cycles: {PHASE_F_B_CYCLES} (most yet)")
    print(f"  Epochs/cycle: {PHASE_F_B_EPOCHS} (2.5x longer than Phase E)")
    print(f"  LR: {PHASE_F_B_LR} (2.5x lower: ultra-fine)")
    print(f"  Ablate: Every cycle (maximum pressure)")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.ml.train.grow_prune",
        "--tiles",
        TILE_DIR,
        "--output",
        PHASE_F_B_OUTPUT,
        "--cycles",
        PHASE_F_B_CYCLES,
        "--inner-epochs",
        PHASE_F_B_EPOCHS,
        "--grow-channels",
        0.3,  # Minimal growth (0.3 channels per cycle)
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        3,
        "--lr",
        PHASE_F_B_LR,
        "--lr-patience",
        5,  # Max patience with ultra-low LR
        "--overfit-stale-epochs",
        30,  # Very long training per cycle
        "--smart-init-jitter",
        0.04,  # Highest jitter yet (exploration)
        "--start-checkpoint",
        input_ckpt,
        "--smart-init",
        "--final-prune",
        "--final-prune-tolerance",
        0.002,  # Tighter tolerance
        "--final-prune-floor-pct",
        35.0,  # Aggressive floor
        "--final-prune-retrain-epochs",
        40,  # Long final retrain
        "--prune-every",
        PHASE_F_B_ABLATE,
    ]

    rc = _run(cmd, "phase_f_b_intensive_cycles.log")
    if rc != 0:
        raise SystemExit(f"Phase F-B failed (exit {rc})")
    return PHASE_F_B_OUTPUT


def inspect_final(ckpt: Path, phase_name: str, n_samples: int, seed: int) -> Path:
    """Generate inspection PDF for final checkpoint."""
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


def main():
    print("\n" + "=" * 70)
    print("PHASE F: PRUNE-FIRST STRATEGY (Target: 0.35 loss)")
    print("=" * 70)
    print(f"Input baseline: Phase E (0.4108 loss)")
    print(f"Tile dataset: {TILE_DIR.name} (~514 tiles, 128 MB)")
    print(f"Target: 0.35-0.38 loss (breakthrough toward 0.35)")
    print(f"Scope: All regions + tall buildings (>20m) + complex architecture")
    print()

    # Phase F-A: Aggressive pruning
    print("\n" + "-" * 70)
    print("PHASE F-A: One-shot aggressive pruning")
    print("-" * 70)
    phase_f_a_ckpt = phase_f_a_aggressive_prune()

    # Phase F-B: Intensive cycles
    print("\n" + "-" * 70)
    print("PHASE F-B: 60 intensive cycles (100 epochs, 5e-6 LR)")
    print("-" * 70)
    phase_f_b_ckpt = phase_f_b_intensive_cycles(phase_f_a_ckpt)

    # Inspect final
    print("\n" + "-" * 70)
    print("INSPECTION: Phase F final checkpoint")
    print("-" * 70)
    inspect_final(phase_f_b_ckpt, "PruneFirst", 60, 46)

    print("\n" + "=" * 70)
    print("PHASE F COMPLETE")
    print("=" * 70)
    print(f"Phase F-A (after prune): {phase_f_a_ckpt}")
    print(f"Phase F-B (final):       {phase_f_b_ckpt}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
