#!/usr/bin/env python3
"""Create a human-editable review bundle for skyline correspondences."""

from __future__ import annotations
from city2stl.skyline_cv.review import create_review_bundle
from city2stl.skyline_cv.pipeline import RunSpec, _load_site_config, resolve_viewpoints, stage_osm, stage_streetview

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
    bundle = create_review_bundle(run, buildings, captured)
    bundle_path = Path(bundle["bundle_dir"]) / "review_bundle.json"
    print(f"Wrote review bundle: {bundle_path}")
    print(f"Edit manual_keep / manual_height_m / manual_confidence in the bundle, then run 07_apply_review_bundle.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
