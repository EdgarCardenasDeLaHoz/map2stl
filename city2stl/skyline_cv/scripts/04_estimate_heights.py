#!/usr/bin/env python3
"""Fuse the per-view skyline measurements into one building-height table."""

from __future__ import annotations
from city2stl.skyline_cv.pipeline import (
    RunSpec,
    _load_site_config,
    resolve_viewpoints,
    stage_estimates,
    stage_osm,
    stage_registration,
    stage_streetview,
)

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    site = _load_site_config("cartagena")
    run = RunSpec(site=site, root_dir=ROOT)
    buildings = stage_osm(run)
    captured = stage_streetview(run, resolve_viewpoints(run))
    estimates = stage_registration(run, buildings, captured)
    aggregated = stage_estimates(run, estimates)
    print(
        f"Aggregated {len(aggregated)} buildings -> {run.estimates_dir / 'building_heights.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
