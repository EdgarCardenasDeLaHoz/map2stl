#!/usr/bin/env python3
"""F-WEB2 step G1: cache the skyline outline of every Commons skyline photo of a region.

    python -m city2stl.skyline.scripts.13_photo_profiles --region miami

All landscape JPEGs in the region's skyline categories, located or not, wide or not (portraits
are recorded and skipped). Per photo: the Commons metadata (location, EXIF FOV, compass, date,
licence), the outline (``skyline_match.photo_profile``: top building row per column) at
1600 px wide, and its clarity (``commons_photos.skyline_quality``, sky brightness). Dark photos
are no longer skipped: the photo pipeline keeps a low-light photo whose skyline is clear
(``commons_photos.quality_reject``; user review 2026-10-07).

``--rescreen``: outline the photos an earlier run skipped as dark, and add clarity fields to
cached outlines (from ``img/``, no GPU). Writes ``runs/commons_cache/<region>/profiles.json`` +
``profiles.npz``; reruns skip photos already cached. SegFormer runs on the GPU when there is
one; the CUDA cache is released after each photo so other jobs on the card are not blocked.

The work is in ``city2stl.skyline.commons_pipeline.profiles``.
"""

from __future__ import annotations

import argparse
import logging

from city2stl.skyline.commons_pipeline.profiles import profile_region, rescreen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--rescreen", action="store_true",
                    help="outline photos skipped as dark; add clarity to cached outlines")
    args = ap.parse_args()
    from city2stl.resources import wait_for_ram

    wait_for_ram()                              # CLAUDE.md "Shared machine resources"
    from city2stl.skyline._core.segmentation import _neural_sky_and_building_masks

    if args.rescreen:
        rescreen(args.region, _neural_sky_and_building_masks)
    else:
        profile_region(args.region, _neural_sky_and_building_masks)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
