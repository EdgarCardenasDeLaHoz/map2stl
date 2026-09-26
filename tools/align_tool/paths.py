"""Filesystem locations used by the align tool, in one place.

Everything resolves from this file's location, so the tool works on any PC
whatever the OneDrive root is. Override with environment variables:

    MAPS_ROOT       the "3D Maps" folder holding Cities/ and strm_h5/
    ALIGN_DATA_DIR  exported per-city rasters (default: ./data, gitignored)
    STRM_H5_ROOT    folder holding strm_data.h5 (default: MAPS_ROOT/strm_h5)
"""

from __future__ import annotations

import os
from pathlib import Path

from app.paths import REPO_ROOT

HERE = Path(__file__).resolve().parent            # strm2stl/tools/align_tool/
MAPS_ROOT = Path(os.environ.get("MAPS_ROOT") or REPO_ROOT.parents[1])   # .../3D Maps/

DATA = Path(os.environ.get("ALIGN_DATA_DIR") or HERE / "data")
GROUND_TRUTH = HERE / "ground_truth"              # hand alignments, tracked in git

CITIES = MAPS_ROOT / "Cities"
H5_ROOT = Path(os.environ.get("STRM_H5_ROOT") or MAPS_ROOT / "strm_h5")
