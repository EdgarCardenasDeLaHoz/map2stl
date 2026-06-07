#!/usr/bin/env python3
"""Concatenate per-view street view + minimap into a single PNG.

Helps a reviewer (human or AI) spot heading mismatches at a glance:
the image's left/right edges should map to the bearings drawn by the
minimap's FOV cone. When they don't, the registration anchor is off.

Output goes to ``runs/region_reports/<region>_skyline_report/assets/
compare/<seed>_view_<i>.png``.

Usage::

    PYTHONPATH=. python city2stl/skyline_cv/scripts/16_view_minimap_compare.py \
        --region Cartagena --seeds seed_1 seed_4 --views 0 2 4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _slug(seed: str) -> str:
    return seed[5:] if seed.startswith("seed_") else seed


def _concat(left: Path, right: Path, out: Path,
            pad_px: int = 8, bg: tuple[int, int, int] = (255, 255, 255)) -> bool:
    if not left.exists() or not right.exists():
        return False
    try:
        L = Image.open(left).convert("RGB")
        R = Image.open(right).convert("RGB")
    except Exception as exc:
        print(f"[compare] open failed {left} / {right}: {exc}")
        return False
    # Match heights so the two panels read side-by-side cleanly.
    target_h = max(L.height, R.height)
    if L.height != target_h:
        scale = target_h / L.height
        L = L.resize((int(L.width * scale), target_h), Image.LANCZOS)
    if R.height != target_h:
        scale = target_h / R.height
        R = R.resize((int(R.width * scale), target_h), Image.LANCZOS)
    canvas = Image.new(
        "RGB", (L.width + pad_px + R.width, target_h), bg)
    canvas.paste(L, (0, 0))
    canvas.paste(R, (L.width + pad_px, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, optimize=True)
    return True


def _run(args: argparse.Namespace) -> int:
    report_dir = (ROOT / "city2stl" / "skyline_cv" / "runs" /
                  "region_reports" / f"{args.region}_skyline_report")
    if not report_dir.is_dir():
        print(f"[compare] report dir not found: {report_dir}")
        return 2

    views_dir = report_dir / "assets" / "views"
    minimap_dir = report_dir / "assets" / "minimap"
    out_dir = report_dir / "assets" / "compare"
    out_dir.mkdir(parents=True, exist_ok=True)

    seeds: list[str]
    if args.seeds:
        seeds = list(args.seeds)
    else:
        # Auto-discover by globbing the views dir for "<slug>_view_0.png"
        # naming the report uses.
        slugs: set[str] = set()
        for p in views_dir.glob("*_view_0.png"):
            name = p.name
            if name.endswith("_mask.png"):
                continue
            slug = name.split("_view_")[0]
            slugs.add(slug)
        seeds = sorted(f"seed_{s}" for s in slugs)

    n_written = 0
    for seed in seeds:
        slug = _slug(seed)
        view_idx = 0
        while True:
            if args.views and view_idx not in args.views:
                # Honour an explicit view list but keep walking until a
                # missing index breaks the loop.
                view_path = views_dir / f"{slug}_view_{view_idx}.png"
                if not view_path.exists():
                    break
                view_idx += 1
                continue
            view_path = views_dir / f"{slug}_view_{view_idx}.png"
            map_path = minimap_dir / f"{slug}_view_{view_idx}.png"
            if not view_path.exists():
                break
            out_path = out_dir / f"{slug}_view_{view_idx}.png"
            if _concat(view_path, map_path, out_path):
                n_written += 1
                if args.verbose:
                    print(f"[compare] {seed} view {view_idx} -> {out_path}")
            view_idx += 1

    print(f"[compare] wrote {n_written} concatenated comparisons to "
          f"{out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--region", required=True,
                   help="Region name; the report dir is "
                        "runs/region_reports/<region>_skyline_report/.")
    p.add_argument("--seeds", nargs="*",
                   help="Subset of seeds to process, e.g. 'seed_1 seed_4'. "
                        "Default: every seed found in the report's "
                        "assets/views directory.")
    p.add_argument("--views", nargs="*", type=int,
                   help="Subset of view indices to concatenate, e.g. "
                        "'0 2 4'. Default: all views available.")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    return _run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
