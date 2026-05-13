#!/usr/bin/env python3
"""Capture multiple Street View images for the Cartagena baseline."""

from __future__ import annotations
from city2stl.skyline_cv.pipeline import RunSpec, _load_site_config, resolve_viewpoints, stage_streetview

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    site = _load_site_config("cartagena")
    run = RunSpec(site=site, root_dir=ROOT)
    viewpoints = resolve_viewpoints(run)
    captured = stage_streetview(run, viewpoints)
    print(f"Captured {len(captured)} images -> {run.streetview_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
