"""Quick test: train best architecture for 1 cycle on combined dataset."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PHASE_G_INPUT = REPO / "models" / "retna_pruned.pt"
TILE_DIR = REPO / "cache" / "height_tiles_combined"
TILE_SIZE = 128
PHASE_G_OUTPUT = REPO / "models" / "retna_phase_g_test.pt"
LOG_DIR = REPO / "logs"


def _run(cmd: list, log_name: str) -> int:
    """Run subprocess with output tee."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_name
    print(f"\n$ {' '.join(str(c) for c in cmd)}\n")
    print(f"Log: {log_path}\n")
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
    print("TEST: Single cycle, 10 epochs, combined dataset")
    print("=" * 70)

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.ml.train.grow_prune",
        "--tiles",
        TILE_DIR,
        "--output",
        PHASE_G_OUTPUT,
        "--cycles",
        1,
        "--inner-epochs",
        10,
        "--grow-channels",
        0,
        "--tile-size",
        TILE_SIZE,
        "--batch-size",
        2,
        "--lr",
        5e-6,
        "--lr-patience",
        2,
        "--overfit-stale-epochs",
        5,
        "--start-checkpoint",
        PHASE_G_INPUT,
        "--smart-init",
    ]

    rc = _run(cmd, "phase_g_test_single_cycle.log")
    if rc != 0:
        print(f"\nTest failed (exit {rc})")
    else:
        print(f"\nTest passed! Output: {PHASE_G_OUTPUT}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
