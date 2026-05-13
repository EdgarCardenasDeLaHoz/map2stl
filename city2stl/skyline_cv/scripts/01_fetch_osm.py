#!/usr/bin/env python3
"""Fetch Cartagena OSM buildings and write a GeoJSON snapshot."""

from __future__ import annotations
from city2stl.skyline_cv.pipeline import RunSpec, _load_site_config, stage_osm

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    site = _load_site_config("cartagena")
    run = RunSpec(site=site, root_dir=ROOT)
    buildings = stage_osm(run)
    print(
        f"Fetched {len(buildings)} buildings -> {run.osm_dir / 'buildings.geojson'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
