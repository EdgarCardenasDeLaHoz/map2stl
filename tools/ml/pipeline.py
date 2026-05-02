"""tools.ml.pipeline — End-to-end driver: collect -> train -> inspect.

One CLI that chains the height-model pipeline:
  1. Collect (RGB, height) tiles via osm or providers
  2. Train Retna_V1 (or grow/prune) on the collected tiles
  3. Inspect the final checkpoint and emit sample renders

Examples:
    # Full run from scratch
    python -m tools.ml.pipeline run \
        --cities Amsterdam Barcelona --tiles-per-city 60 \
        --tile-size 512 --tile-dir cache/tiles_512 \
        --output models/retna_pipeline.pt --epochs 60

    # Just train + inspect on an existing tile dir
    python -m tools.ml.pipeline run --skip-collect \
        --tile-dir cache/height_tiles_combined \
        --output models/retna_pipeline.pt --epochs 30

    # Grow/prune mode
    python -m tools.ml.pipeline run --skip-collect \
        --tile-dir cache/height_tiles_combined \
        --output models/retna_grow.pt --grow --cycles 6 --inner-epochs 30
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_STRM2STL = Path(__file__).resolve().parents[2]


def _run(cmd: list[str], description: str) -> int:
    print(f"\n=== {description} ===")
    print("$ " + " ".join(cmd))
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.call(cmd, cwd=str(_STRM2STL), env=env)


def cmd_run(args: argparse.Namespace) -> int:
    tile_dir = Path(args.tile_dir)
    if not tile_dir.is_absolute():
        tile_dir = _STRM2STL / tile_dir

    if not args.skip_collect:
        if not args.cities:
            print("error: --cities is required unless --skip-collect", file=sys.stderr)
            return 2
        cmd = [
            sys.executable, "-u", "-m", "tools.ml.collect_osm_tiles",
            "--cities", *args.cities,
            "--tiles-per-city", str(args.tiles_per_city),
            "--tile-size", str(args.tile_size),
            "--tile-dir", str(tile_dir),
        ]
        if args.label_source:
            cmd += ["--label-source", args.label_source]
        rc = _run(cmd, f"collect tiles -> {tile_dir}")
        if rc != 0:
            return rc

    output = Path(args.output)
    if not output.is_absolute():
        output = _STRM2STL / output

    if args.grow:
        cmd = [
            sys.executable, "-u", "-m", "tools.ml.grow_prune",
            "--tiles", str(tile_dir),
            "--output", str(output),
            "--cycles", str(args.cycles),
            "--inner-epochs", str(args.inner_epochs),
            "--grow-channels", str(args.grow_channels),
            "--tile-size", str(args.tile_size),
            "--batch-size", str(args.batch_size),
            "--lr", str(args.lr),
            "--lr-patience", str(args.lr_patience),
        ]
        if args.resume:
            cmd += ["--start-checkpoint", args.resume]
    else:
        cmd = [
            sys.executable, "-u", "-m", "tools.ml.train_retna",
            "--tiles", str(tile_dir),
            "--output", str(output),
            "--epochs", str(args.epochs),
            "--tile-size", str(args.tile_size),
            "--batch-size", str(args.batch_size),
            "--lr", str(args.lr),
            "--lr-patience", str(args.lr_patience),
            "--loss", args.loss,
            "--l2-weight", str(args.l2_weight),
            "--hidden-channels", *map(str, args.hidden_channels),
        ]
        if args.resume:
            cmd += ["--resume", args.resume]
    rc = _run(cmd, f"train -> {output}")
    if rc != 0:
        return rc

    if not args.skip_inspect:
        inspect_out = output.with_suffix("").name + "_inspect.pdf"
        cmd = [
            sys.executable, "-m", "tools.ml.inspect_retna",
            "--checkpoint", str(output),
            "--tiles", str(tile_dir),
            "--out", str(output.parent / inspect_out),
            "--n-samples", str(args.inspect_samples),
        ]
        rc = _run(cmd, f"inspect -> {inspect_out}")
        if rc != 0:
            return rc

    print(f"\nPipeline complete: {output}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("run", help="collect + train + inspect")
    # Collect args
    rp.add_argument("--cities", nargs="*", default=None)
    rp.add_argument("--tiles-per-city", type=int, default=60)
    rp.add_argument("--tile-dir", required=True)
    rp.add_argument("--label-source", choices=["osm", "providers"], default=None)
    rp.add_argument("--skip-collect", action="store_true")
    # Train args
    rp.add_argument("--output", required=True)
    rp.add_argument("--tile-size", type=int, default=512)
    rp.add_argument("--batch-size", type=int, default=4)
    rp.add_argument("--epochs", type=int, default=60)
    rp.add_argument("--lr", type=float, default=3e-4)
    rp.add_argument("--lr-patience", type=int, default=6)
    rp.add_argument("--loss", choices=["dice", "dice_l2", "dice_l3"], default="dice_l2")
    rp.add_argument("--l2-weight", type=float, default=1.0)
    rp.add_argument("--hidden-channels", nargs="+", type=int, default=[8, 8, 8, 8])
    rp.add_argument("--resume", default=None)
    # Grow/prune mode
    rp.add_argument("--grow", action="store_true",
                    help="Use grow/prune instead of plain train")
    rp.add_argument("--cycles", type=int, default=6)
    rp.add_argument("--inner-epochs", type=int, default=30)
    rp.add_argument("--grow-channels", type=int, default=4)
    # Inspect args
    rp.add_argument("--skip-inspect", action="store_true")
    rp.add_argument("--inspect-samples", type=int, default=8)

    rp.set_defaults(func=cmd_run)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
