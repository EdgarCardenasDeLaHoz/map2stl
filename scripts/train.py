"""scripts/train.py — Self-contained Retna height-model training entry point.

Single Python entry point for repeated training runs. No external CLI tools
required, works identically on Windows / macOS / Linux. All knobs live as
constants at the top of this file — edit them and re-run.

Modes:
    python scripts/train.py train      Plain training, resume from best baseline
    python scripts/train.py grow       Grow/prune NAS (smart-init, widening only)
    python scripts/train.py deep       Grow/prune with deepen + widen
    python scripts/train.py collect    Collect tiles only (cities below)
    python scripts/train.py full       Collect -> train -> inspect
    python scripts/train.py inspect <ckpt>  Render 20-sample inspection PDF
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ── EDIT THESE ──────────────────────────────────────────────────────────────

# All 11 European cities + 3 non-OSM regions. More cities -> more diverse data.
# Tile collection skips low-coverage cells, so the actual count varies.
CITIES = [
    "Amsterdam", "Barcelona", "Berlin", "Vienna", "Paris", "Prague",
    "Rotterdam", "Cologne", "Bruges", "Florence", "Munich",
    # Non-Europe (use --label-source providers instead of OSM):
    # "Cartagena", "Manhattan", "Tokyo",
]
TILES_PER_CITY = 60

TILE_SIZE = 128
TILE_DIR = REPO / "cache" / "height_tiles_combined"

# Resume from the current best baseline. Set to None for cold start.
RESUME = REPO / "models" / "retna_rebuild.pt"
HIDDEN = [8, 8, 10, 20, 14, 14, 16, 16, 22]  # 9-block baseline from rebuild

# Rebuild target — used by 'rebuild' mode; grow stops when val_loss hits this
TARGET_VAL_LOSS = 0.37

# Plain-train hyperparameters
EPOCHS = 20
BATCH_SIZE = 3
LR = 3e-5
LR_PATIENCE = 3
LOSS = "dice"  # or "dice_l3" for cubic residual
L2_WEIGHT = 0.5

# Grow/prune NAS hyperparameters
CYCLES = 30
INNER_EPOCHS = 30
GROW_CHANNELS = 0
OVERFIT_STALE = 15
SMART_INIT = True
SMART_JITTER = 0.0
ALLOW_DEEPEN = True
MAX_DEPTH = 8

# Rebuild mode: unlimited grow cycles until TARGET_VAL_LOSS
REBUILD_CYCLES = 30

# Final-prune (ablation) settings
FINAL_PRUNE = True
FINAL_PRUNE_TOLERANCE = 0.005     # accept channel-zero if val_loss Δ < this
FINAL_PRUNE_FLOOR_PCT = 25.0      # only test the bottom-N% by grad×act score
FINAL_PRUNE_RETRAIN_EPOCHS = 20   # recovery training after pruning
PRUNE_EVERY = 3                   # 0 = off; N = ablate+compact every Nth cycle

OUT_DIR = REPO / "output"
LOG_DIR = REPO / "logs"

# ── END EDITABLE SECTION ────────────────────────────────────────────────────


def _run(cmd: list, log_name: str) -> int:
    """Run a subprocess, tee output to logs/<log_name>.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_name
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
            sys.stdout.buffer.write(line); sys.stdout.flush()
            logf.write(line)
        return proc.wait()


def _train(out_name: str = "retna_continue.pt") -> Path:
    out = REPO / "models" / out_name
    cmd = [
        sys.executable, "-u", "-m", "tools.ml.train.train_retna",
        "--tiles", TILE_DIR, "--output", out,
        "--hidden-channels", *map(str, HIDDEN),
        "--epochs", EPOCHS, "--tile-size", TILE_SIZE,
        "--batch-size", BATCH_SIZE, "--lr", LR, "--lr-patience", LR_PATIENCE,
        "--loss", LOSS, "--l2-weight", L2_WEIGHT,
    ]
    if RESUME and RESUME.exists():
        cmd += ["--resume", RESUME]
    rc = _run(cmd, f"{out.stem}.log")
    if rc != 0:
        raise SystemExit(f"train failed (exit {rc})")
    return out


def _grow(out_name: str, allow_deepen: bool, cycles: int | None = None,
          target_val_loss: float | None = None) -> Path:
    out = REPO / "models" / out_name
    _cycles = cycles if cycles is not None else CYCLES
    cmd = [
        sys.executable, "-u", "-m", "tools.ml.train.grow_prune",
        "--tiles", TILE_DIR, "--output", out,
        "--cycles", _cycles, "--inner-epochs", INNER_EPOCHS,
        "--grow-channels", GROW_CHANNELS,
        "--tile-size", TILE_SIZE, "--batch-size", BATCH_SIZE,
        "--lr", LR, "--lr-patience", LR_PATIENCE,
        "--overfit-stale-epochs", OVERFIT_STALE,
        "--smart-init-jitter", SMART_JITTER,
    ]
    if RESUME and RESUME.exists():
        cmd += ["--start-checkpoint", RESUME]
    cmd += ["--smart-init"] if SMART_INIT else ["--no-smart-init"]
    if allow_deepen:
        cmd += ["--allow-deepen", "--max-depth", MAX_DEPTH]
    if FINAL_PRUNE:
        cmd += [
            "--final-prune",
            "--final-prune-tolerance", FINAL_PRUNE_TOLERANCE,
            "--final-prune-floor-pct", FINAL_PRUNE_FLOOR_PCT,
            "--final-prune-retrain-epochs", FINAL_PRUNE_RETRAIN_EPOCHS,
        ]
    if PRUNE_EVERY > 0:
        cmd += ["--prune-every", PRUNE_EVERY]
    if target_val_loss is not None:
        cmd += ["--target-val-loss", target_val_loss]
    rc = _run(cmd, f"{out.stem}.log")
    if rc != 0:
        raise SystemExit(f"grow_prune failed (exit {rc})")
    return out


def _collect():
    cmd = [
        sys.executable, "-u", "-m", "tools.ml.data.collect_osm_tiles",
        "--cities", *CITIES,
        "--tiles-per-city", TILES_PER_CITY,
        "--tile-size", TILE_SIZE,
        "--tile-dir", TILE_DIR,
    ]
    rc = _run(cmd, "collect.log")
    if rc != 0:
        raise SystemExit(f"collect failed (exit {rc})")


def _inspect(ckpt: Path):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_pdf = OUT_DIR / f"{ckpt.stem}_inspect.pdf"
    cmd = [
        sys.executable, "-m", "tools.ml.analysis.inspect_retna",
        "--checkpoint", ckpt, "--tiles", TILE_DIR,
        "--out", out_pdf, "--tile-size", TILE_SIZE, "--n-samples", 20,
    ]
    rc = _run(cmd, f"inspect_{ckpt.stem}.log")
    if rc == 0:
        print(f"\n  PDF: {out_pdf}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "train"
    print(f"mode: {mode}")

    if mode == "train":
        out = _train()
        _inspect(out)
    elif mode == "grow":
        out = _grow("retna_grow_continue.pt", allow_deepen=False)
        _inspect(out)
    elif mode == "deep":
        out = _grow("retna_deep_continue.pt", allow_deepen=True)
        _inspect(out)
    elif mode == "rebuild":
        # Cold-start train with the full 9-block architecture, then grow/prune
        # with deepen enabled until TARGET_VAL_LOSS is achieved.
        global RESUME
        RESUME = None  # force cold start for the train phase
        print(f"[rebuild] Phase 1: cold-start plain training with arch={HIDDEN}")
        out = _train("retna_rebuild.pt")
        RESUME = out   # hand the trained checkpoint to grow
        print(f"[rebuild] Phase 2: grow/prune (target val_loss <= {TARGET_VAL_LOSS})")
        out = _grow("retna_grow_continue.pt", allow_deepen=True,
                    cycles=REBUILD_CYCLES, target_val_loss=TARGET_VAL_LOSS)
        _inspect(out)
    elif mode == "collect":
        _collect()
    elif mode == "full":
        _collect()
        out = _train("retna_full.pt")
        _inspect(out)
    elif mode == "inspect":
        if len(sys.argv) < 3:
            print("usage: scripts/train.py inspect <ckpt-path>")
            return 2
        ckpt = Path(sys.argv[2])
        if not ckpt.is_absolute():
            ckpt = REPO / ckpt
        _inspect(ckpt)
    else:
        print(f"unknown mode: {mode}")
        print("modes: train, grow, deep, collect, full, inspect")
        return 1


if __name__ == "__main__":
    main()