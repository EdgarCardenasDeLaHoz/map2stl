#!/usr/bin/env python3
"""F-WEB2 step G4: image embeddings of a region's photos (DINOv2-small), for same-spot search.

    python -m city2stl.skyline.scripts.16_embed_photos --region miami

Why: skyline-outline similarity alone matched only 3-4 of 18 Miami links correctly (simple or
partial skylines fit almost anything). An image embedding sees the whole scene (water, bridges,
foreground), so it proposes same-spot candidates; the outline fit then confirms them
(``photo_groups.embed_links``). User choice 2026-10-04.

Reads the outline cache (``13_photo_profiles``) for the photo list, caches 500 px thumbnails in
``runs/commons_cache/<region>/img/`` (one download per photo, ever), writes ``embed.npz``
(L2-normalised CLS embeddings, keyed like the profiles). GPU when available; the CUDA cache is
released at the end.
"""

from __future__ import annotations

import argparse
import logging

from city2stl.resources import wait_for_ram
from city2stl.skyline.commons_pipeline.embed import embed_region


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    args = ap.parse_args()
    wait_for_ram()                              # CLAUDE.md "Shared machine resources"
    embed_region(args.region)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
