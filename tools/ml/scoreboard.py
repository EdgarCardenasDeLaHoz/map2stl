"""tools.ml.scoreboard — Aggregate training-run metrics into one JSON registry.

Each call to ``record_run()`` appends an entry to ``models/scoreboard.json``
with the checkpoint's best validation metrics, run timestamp, dataset size,
and config hash. This lets us compare RoofNetV3 vs HeightUNet vs cascade
runs without grepping through scattered ``*_history.json`` files.

Usage:
    from tools.ml.scoreboard import record_run
    record_run(
        model_path="models/roofnet_v3.pt",
        arch="roofnet_v3",
        task="height",
        best_metrics={"val_loss": 14.46, "val_mae": 6.0, "val_iou": 0.85},
        n_train=437, n_val=77, epochs=8,
        config={"lr": 3e-4, "batch_size": 16},
        notes="philadelphia_only",
    )

CLI:
    python -m tools.ml.scoreboard show          # print sorted scoreboard
    python -m tools.ml.scoreboard show --task height
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default scoreboard location: alongside the model checkpoints
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "models" / "scoreboard.json"


def _config_fingerprint(config: dict) -> str:
    """Stable 8-char hash so we can spot repeated runs with the same config."""
    payload = json.dumps(config, sort_keys=True, default=str).encode()
    return hashlib.md5(payload).hexdigest()[:8]


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("runs", []) if isinstance(data, dict) else data
    except Exception as e:
        logger.warning(f"scoreboard load failed: {e}")
        return []


def _save(path: Path, runs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"runs": runs, "version": 1}, f, indent=2, default=str)


def record_run(
    model_path: str,
    arch: str,
    task: str,
    best_metrics: dict[str, float],
    n_train: int,
    n_val: int,
    epochs: int,
    config: dict | None = None,
    notes: str = "",
    scoreboard_path: str | Path | None = None,
) -> dict:
    """Append one entry to the scoreboard.

    Returns the entry that was written.
    """
    path = Path(scoreboard_path) if scoreboard_path else _DEFAULT_PATH
    runs = _load(path)
    config = config or {}
    entry = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "arch": arch,
        "task": task,
        "model_path": str(model_path),
        "best_metrics": {k: round(float(v), 4) if isinstance(v, (int, float)) else v
                         for k, v in best_metrics.items()},
        "n_train": int(n_train),
        "n_val": int(n_val),
        "epochs": int(epochs),
        "config_hash": _config_fingerprint(config),
        "notes": notes,
    }
    runs.append(entry)
    _save(path, runs)
    return entry


def show(task: str | None = None, scoreboard_path: str | Path | None = None) -> None:
    path = Path(scoreboard_path) if scoreboard_path else _DEFAULT_PATH
    runs = _load(path)
    if not runs:
        print(f"(scoreboard at {path} is empty)")
        return
    if task:
        runs = [r for r in runs if r.get("task") == task]
    runs = sorted(
        runs,
        key=lambda r: r.get("best_metrics", {}).get("val_mae",
                       r.get("best_metrics", {}).get("val_loss", 1e9)),
    )
    print(f"{len(runs)} run(s){' for task=' + task if task else ''}:\n")
    def _fmt(v, w=6, p=2):
        if v is None:
            return f"{'—':>{w}}"
        try:
            return f"{float(v):>{w}.{p}f}"
        except Exception:
            return f"{str(v):>{w}}"
    for r in runs:
        m = r.get("best_metrics", {})
        n_total = r.get("n_train", 0) + r.get("n_val", 0)
        print(
            f"  {(r.get('arch') or '?'):<22s} {(r.get('task') or '?'):<8s} "
            f"mae={_fmt(m.get('val_mae'),6,2)} "
            f"rmse={_fmt(m.get('val_rmse'),6,2)} "
            f"iou={_fmt(m.get('val_mask_iou'),5,3)} "
            f"loss={_fmt(m.get('val_loss'),6,3)}  "
            f"n={n_total:<5d} ep={int(r.get('epochs',0)):<3d} "
            f"{r.get('ts','')}  {r.get('notes','')}"
        )
    print()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    show_p = sub.add_parser("show")
    show_p.add_argument("--task", choices=["height", "shape", "both"], default=None)
    show_p.add_argument("--path", default=None)
    args = p.parse_args()

    if args.cmd == "show":
        show(task=args.task, scoreboard_path=args.path)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
