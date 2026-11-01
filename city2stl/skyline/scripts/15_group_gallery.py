#!/usr/bin/env python3
"""F-WEB2 step G3: review page for photo grouping (no recomputing).

    python -m city2stl.skyline.scripts.15_group_gallery --region miami [--max-misfit 0.07]

Reads ``pairs.json`` (``14_group_photos``), applies ``photo_groups.link_to_located`` and
``cliques`` at the threshold, writes ``groups.json`` and ``runs/commons_review/<region>_groups.html``:
each unlocated photo beside the located photo it matched (with that photo's camera), each group
of unlocated photos together, and every match's misfit, so a person can confirm the matches
before anything relies on them.
"""

from __future__ import annotations

import argparse

from city2stl.skyline import photo_groups as pg
from city2stl.skyline.commons_pipeline.cache import ROOT
from city2stl.skyline.commons_pipeline.groups import gallery_region


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--max-misfit", type=float, default=pg.SAME_SPOT_MISFIT)
    args = ap.parse_args()
    gallery_region(args.region, args.max_misfit, ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
