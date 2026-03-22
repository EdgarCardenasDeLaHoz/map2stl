"""
config.py — Application-wide constants, paths, and environment settings.

Extracted from location_picker.py (backend refactor, step 1).
Import this module to access paths and settings rather than duplicating
them across files.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Test mode
# ---------------------------------------------------------------------------

TEST_MODE: bool = os.environ.get("STRM2STL_TEST_MODE", "0") == "1"

# ---------------------------------------------------------------------------
# Filesystem paths
# ---------------------------------------------------------------------------

_UI_DIR = Path(__file__).parent                             # strm2stl/ui/
_STRM2STL_DIR = _UI_DIR.parent                              # strm2stl/
_PROJECT_ROOT = _STRM2STL_DIR.parent                        # Code/

COORDINATES_PATH = _STRM2STL_DIR / "coordinates.json"
REGION_SETTINGS_PATH = _STRM2STL_DIR / "region_settings.json"

# Legacy OSM cache (plain JSON — migrated to CACHE_ROOT/osm/ on startup)
OSM_CACHE_PATH = _STRM2STL_DIR / "osm_raw_cache"

# OpenTopography GeoTIFF tile cache
OPENTOPO_CACHE_PATH = _STRM2STL_DIR / "opentopo_cache"

# Earth Engine / legacy ee-joblib cache
EE_CACHE_DIR = _PROJECT_ROOT / "cache" / "ee"

# ---------------------------------------------------------------------------
# OpenTopography API key
# ---------------------------------------------------------------------------

_OPENTOPO_API_KEY: Optional[str] = os.environ.get("OPENTOPO_API_KEY")
try:
    _cfg_path = _STRM2STL_DIR / "config.json"
    if _cfg_path.exists() and _OPENTOPO_API_KEY is None:
        _cfg = json.loads(_cfg_path.read_text())
        _OPENTOPO_API_KEY = _cfg.get("opentopo_api_key") or None
except Exception:
    pass

OPENTOPO_API_KEY: Optional[str] = _OPENTOPO_API_KEY

if not OPENTOPO_API_KEY:
    _log.warning(
        "No OpenTopography API key found. "
        "Set the OPENTOPO_API_KEY environment variable to enable DEM downloads."
    )

# ---------------------------------------------------------------------------
# Supported OpenTopography DEM types
# ---------------------------------------------------------------------------

OPENTOPO_DATASETS: dict[str, dict] = {
    "SRTMGL1":    {"label": "SRTM 30m (Global)",          "resolution_m": 30},
    "SRTMGL3":    {"label": "SRTM 90m (Global)",          "resolution_m": 90},
    "AW3D30":     {"label": "ALOS World 3D 30m",          "resolution_m": 30},
    "COP30":      {"label": "Copernicus DSM 30m",         "resolution_m": 30},
    "COP90":      {"label": "Copernicus DSM 90m",         "resolution_m": 90},
    "SRTM15Plus": {"label": "SRTM15+ (Bathymetry+Land)", "resolution_m": 500},
}

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Local SRTM HDF5 tile store (city2stl pipeline)
# ---------------------------------------------------------------------------
# strm_data.h5 contains SRTM3 tiles (6000×6000 px per 5° tile = ~90m resolution).
# Set STRM_H5_ROOT env var or update the default below to point at the directory
# containing strm_data.h5.
#
# Future: if the h5 file is absent, fall back to OpenTopography SRTMGL3 API
# (same 90m SRTM3 data) or Google Earth Engine (SRTM/NASADEM, higher resolution
# possible).  See TODO.md "SRTM h5 web fallback" for the roadmap.
H5_SRTM_ROOT: Optional[str] = os.environ.get(
    "STRM_H5_ROOT",
    r"C:\Users\eac84\Desktop\Desktop\FILES",
)
H5_SRTM_FILE: Optional[Path] = (
    Path(H5_SRTM_ROOT) / "strm_data.h5" if H5_SRTM_ROOT else None
)
H5_SRTM_AVAILABLE: bool = bool(H5_SRTM_FILE and H5_SRTM_FILE.exists())

# ---------------------------------------------------------------------------
# Legacy EE cache management constants (kept for clear_caches_if_needed())
# ---------------------------------------------------------------------------

CACHE_DIRS = [EE_CACHE_DIR]
CACHE_CLEAR_INTERVAL = 3600  # seconds between periodic EE cache sweeps
CACHE_MAX_FILES = 100        # trigger a sweep when this many files exist
