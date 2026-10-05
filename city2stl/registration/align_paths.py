"""Where the plate-registration code keeps its packs and caches, defined once.

The align tool (``tools/align_tool/``) writes one directory per plate pack
(``data/<slug>/meta.json`` plus rasters) and caches Overpass answers beside it.
The logic that reads them now lives in ``city2stl.registration`` so the web app
can import it; the files stay where the tool has always put them.

Environment overrides (same names the tool's ``paths.py`` honours):

    ALIGN_DATA_DIR   exported per-plate packs (default ``tools/align_tool/data``)
    MAPS_ROOT        the "3D Maps" folder holding ``Cities/`` and ``strm_h5/``
    STRM_H5_ROOT     folder holding ``strm_data.h5`` (default ``MAPS_ROOT/strm_h5``)

No ``app`` import: city2stl stays usable without the server.
"""

from __future__ import annotations

import os
from pathlib import Path

#: map2stl/ — registration -> city2stl -> map2stl
REPO_ROOT = Path(__file__).resolve().parents[2]
ALIGN_TOOL = REPO_ROOT / "tools" / "align_tool"
MAPS_ROOT = Path(os.environ.get("MAPS_ROOT") or REPO_ROOT.parents[1])


def data_dir() -> Path:
    """Pack directory, read fresh so tests can point ``ALIGN_DATA_DIR`` at a fixture."""
    return Path(os.environ.get("ALIGN_DATA_DIR") or ALIGN_TOOL / "data")


#: Overpass water layers cached by ``osm_water`` (always beside the tool, as before).
WATER_CACHE = ALIGN_TOOL / "data" / "water_cache"
#: Raw OSM elements and DEM tiles cached by the street placement.
STREET_CACHE = ALIGN_TOOL / "cache" / "street_place"
H5_ROOT = Path(os.environ.get("STRM_H5_ROOT") or MAPS_ROOT / "strm_h5")
