#!/usr/bin/env python3
"""Write a markdown report for the Cartagena skyline CV baseline."""

from __future__ import annotations
from city2stl.skyline_cv.pipeline import (
    RunSpec,
    _load_site_config,
    resolve_viewpoints,
    stage_estimates,
    stage_osm,
    stage_registration,
    stage_report,
    stage_streetview,
    stage_site,
)

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    site = _load_site_config("cartagena")
    run = RunSpec(site=site, root_dir=ROOT)
    site_payload = stage_site(run)
    buildings = stage_osm(run)
    captured = stage_streetview(run, resolve_viewpoints(run))
    estimates = stage_registration(run, buildings, captured)
    aggregated = stage_estimates(run, estimates)
    report = stage_report(run, site_payload, buildings, aggregated)
    print(f"Wrote {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
