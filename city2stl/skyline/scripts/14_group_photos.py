#!/usr/bin/env python3
"""F-WEB2 step G2: pairwise outline similarity of a region's photos, groups, and a check.

    python -m city2stl.skyline.scripts.14_group_photos --region miami [--workers 4] [--max-misfit 0.15]

Reads the outline cache (``13_photo_profiles``), scores every pair once
(``photo_groups.outline_similarity``, B onto A; the zoom range covers both directions) in a
process pool, and writes ``runs/commons_cache/<region>/pairs.json`` (every pair with misfit
<= 0.5, so the threshold can be tuned without recomputing) and ``groups.json``: direct links from unlocated to located photos and complete-linkage
groups of unlocated photos (``photo_groups.link_to_located``, ``cliques``; no chaining).

The check: for pairs where both photos carry a camera location, the distance between their
cameras against the misfit. Photos from the same spot should be close.
"""

from __future__ import annotations

import argparse

from city2stl.skyline import photo_groups as pg
from city2stl.skyline.commons_pipeline.groups import group_region


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-misfit", type=float, default=pg.SAME_SPOT_MISFIT)
    args = ap.parse_args()
    group_region(args.region, args.workers, args.max_misfit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
