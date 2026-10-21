"""Where the skyline pipeline's single-use caches live (kept out of OneDrive).

``runs/`` is git-ignored and, inside the OneDrive project folder, is synced file by file.
The big pipeline intermediates (Street View, satellite and Commons images, dated benchmark
runs) are not needed to generate a city STL, so they live in a local folder instead:

- env ``SKYLINE_LOCAL_DIR`` set -> ``$SKYLINE_LOCAL_DIR/<name>``;
- unset -> ``runs/<name>`` beside the code (the old place, so a fresh checkout still works).

Small things the renders read (survey truth, heights.json reports, ``osm_layers``, ``survey``)
stay under ``runs/`` and are not routed through here. The stage cache has its own variable,
``SKYLINE_STAGE_CACHE_DIR`` (``_pano/stage_cache.py``).
"""
from __future__ import annotations

import os
from pathlib import Path

ENV = "SKYLINE_LOCAL_DIR"
RUNS = Path(__file__).resolve().parent / "runs"


def local_dir(name: str) -> Path:
    """The folder for the single-use cache ``name`` (not created)."""
    root = os.environ.get(ENV, "").strip()
    return (Path(root) if root else RUNS) / name
