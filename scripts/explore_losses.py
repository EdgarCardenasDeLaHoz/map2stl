"""scripts/explore_losses.py — Quick exploration of loss functions and strategies.

Tests different loss combinations on a 5-epoch quick run to estimate convergence
speed and final validation metrics. Results go into a comparison CSV.

Loss strategies to test:
  1. dice — pure Dice (shape/overlap focus)
  2. dice_l2 — Dice + MSE (balance shape + magnitude)
  3. dice_l3 — Dice + cubic (tall-building emphasis)
  4. bce — binary cross-entropy (for phase 1 segmentation)
  5. mse — pure MSE (for phase 2 regression)

Each run: 5 epochs, same dataset, log metrics for comparison.

Usage:
    python scripts/explore_losses.py
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Quick test config
TILE_DIR = REPO / "cache" / "height_tiles_combined"
QUICK_EPOCHS = 5
BATCH_SIZE = 3
LR = 3e-5
HIDDEN = [8, 8, 10, 20, 14, 14, 16, 16, 22]

LOSSES = [
    ("dice", "pure Dice (shape/overlap)"),
    ("dice_l2", "Dice + MSE (balance)"),
    ("dice_l3", "Dice + cubic (tall emphasis)"),
]

OUT_CSV = REPO / "output" / "loss_exploration.csv"
OUT_DIR = REPO / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _run_quick_train(loss_name: str, l2_weight: float = 1.0) -> dict:
    """Train for QUICK_EPOCHS with given loss, parse results."""
    out_pt = REPO / "models" / f"retna_loss_test_{loss_name}.pt"
    log_file = REPO / "logs" / f"loss_test_{loss_name}.log"

    cmd = [
        sys.executable, "-u", "-m", "tools.ml.train.train_retna",
        "--tiles", TILE_DIR, "--output", out_pt,
        "--hidden-channels", *map(str, HIDDEN),
        "--epochs", QUICK_EPOCHS, "--tile-size", 128,
        "--batch-size", BATCH_SIZE, "--lr", LR,
        "--loss", loss_name, "--l2-weight", l2_weight,
    ]

    print(f"\n{'=' * 70}")
    print(f"Testing: {loss_name} (l2_weight={l2_weight})")
    print(f"{'=' * 70}")
    print(f"$ {' '.join(str(c) for c in cmd)}\n")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    start_time = time.time()
    try:
        with open(log_file, "wb") as logf:
            proc = subprocess.Popen(
                [str(c) for c in cmd],
                cwd=str(REPO),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            output_lines = []
            for line in proc.stdout:
                output_lines.append(line)
                sys.stdout.buffer.write(line)
                sys.stdout.flush()
                logf.write(line)
            rc = proc.wait()
    except Exception as e:
        print(f"ERROR running train: {e}")
        return {}

    elapsed = time.time() - start_time

    # Parse final metrics from output
    result = {
        "loss_name": loss_name,
        "l2_weight": l2_weight,
        "elapsed_sec": elapsed,
        "exit_code": rc,
    }

    # Try to extract final metrics
    try:
        output_str = b"".join(output_lines).decode("utf-8", errors="replace")
        lines = output_str.split("\n")
        for line in reversed(lines):
            if "val_mae" in line and "val_loss" in line:
                # e.g. "val_loss=0.45 val_mae=5.2m val_rmse=8.9m val_iou=0.42"
                import re

                match_loss = re.search(r"val_loss=([\d.]+)", line)
                match_mae = re.search(r"val_mae=([\d.]+)", line)
                match_iou = re.search(r"val_iou=([\d.]+)", line)
                if match_loss:
                    result["final_val_loss"] = float(match_loss.group(1))
                if match_mae:
                    result["final_val_mae_m"] = float(match_mae.group(1))
                if match_iou:
                    result["final_val_iou"] = float(match_iou.group(1))
                break
    except Exception as e:
        print(f"Warning: could not parse final metrics: {e}")

    return result


def main():
    print("\n" + "=" * 70)
    print("LOSS FUNCTION EXPLORATION")
    print("=" * 70)
    print(f"Dataset: {TILE_DIR}")
    print(f"Quick test: {QUICK_EPOCHS} epochs")
    print(f"Config: batch={BATCH_SIZE}, lr={LR}, hidden={HIDDEN}")
    print()

    results = []
    for loss_name, description in LOSSES:
        result = _run_quick_train(loss_name)
        if result:
            result["description"] = description
            results.append(result)
            print(f"\n[OK] {loss_name}: {result}")
        else:
            print(f"\n[FAIL] {loss_name} FAILED")

    # Write CSV
    if results:
        print("\n" + "=" * 70)
        print(f"Saving results to {OUT_CSV}")
        print("=" * 70)
        with open(OUT_CSV, "w", newline="") as f:
            keys = [
                "loss_name",
                "description",
                "l2_weight",
                "elapsed_sec",
                "final_val_loss",
                "final_val_mae_m",
                "final_val_iou",
                "exit_code",
            ]
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in results:
                w.writerow(r)

        print(f"\nResults:")
        for r in results:
            print(
                f"  {r['loss_name']:15} loss={r.get('final_val_loss', '?'):6} "
                f"mae={r.get('final_val_mae_m', '?'):5} "
                f"iou={r.get('final_val_iou', '?'):5} "
                f"time={r['elapsed_sec']:.0f}s"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
