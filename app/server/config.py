"""
config.py — Application-wide constants, paths, and environment settings.

Extracted from location_picker.py (backend refactor, step 1).
Import this module to access paths and settings rather than duplicating
them across files.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from geo2stl import opentopo as _opentopo

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Test mode
# ---------------------------------------------------------------------------

TEST_MODE: bool = os.environ.get("MAP2STL_TEST_MODE", "0") == "1"

# ---------------------------------------------------------------------------
# Filesystem paths
# ---------------------------------------------------------------------------

_MAP2STL_DIR = Path(__file__).parent.parent.parent         # map2stl/
_PROJECT_ROOT = _MAP2STL_DIR.parent                        # Code/

COORDINATES_PATH = _MAP2STL_DIR / "coordinates.json"
REGION_SETTINGS_PATH = _MAP2STL_DIR / "region_settings.json"

# Legacy OSM cache (plain JSON — migrated to CACHE_ROOT/osm/ on startup)
OSM_CACHE_PATH = _MAP2STL_DIR / "osm_raw_cache"

# OpenTopography GeoTIFF tile cache (under unified cache/ tree)
OPENTOPO_CACHE_PATH = _MAP2STL_DIR / "cache" / "opentopo"

# Earth Engine / legacy ee-joblib cache
EE_CACHE_DIR = _PROJECT_ROOT / "cache" / "ee"

# ---------------------------------------------------------------------------
# External STL/OBJ mesh library (F-MESHIMPORT)
# ---------------------------------------------------------------------------
# Pre-made city mesh sets (e.g. commercial "micropolitan" STL packs) live
# outside the repo, as a sibling of Code/. Configurable + relative so the
# path travels with the project if "3D Maps/" is relocated or used on
# another machine. Default matches the current on-disk layout:
#   3D Maps/
#   ├── Code/                       <- _PROJECT_ROOT
#   └── Cities/micropolitan/_extracted/
# The pre-rename variable (STRM2STL_*) is still honoured.
_MICROPOLITAN_ENV = (os.environ.get("MAP2STL_MICROPOLITAN_DIR")
                     or os.environ.get("STRM2STL_MICROPOLITAN_DIR")
                     or "../Cities/micropolitan/_extracted")
MICROPOLITAN_STL_DIR: Path = (_PROJECT_ROOT / _MICROPOLITAN_ENV).resolve()

# ---------------------------------------------------------------------------
# OpenTopography API key
# ---------------------------------------------------------------------------

# The key and the dataset table live in geo2stl.opentopo ($OPENTOPO_API_KEY, else
# config.json's opentopo_api_key); this is the value at startup. routers/auth.py
# rebinds it together with geo2stl.opentopo.set_api_key() when a key is saved.
OPENTOPO_API_KEY: str | None = _opentopo.get_api_key()

# ---------------------------------------------------------------------------
# Supported OpenTopography DEM types
# ---------------------------------------------------------------------------

OPENTOPO_DATASETS: dict[str, dict] = _opentopo.OPENTOPO_DATASETS

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Local SRTM HDF5 tile store (city2stl pipeline)
# ---------------------------------------------------------------------------
# strm_data.h5 contains SRTM3 tiles (6000×6000 px per 5° tile = ~90m resolution).
# Set STRM_H5_ROOT env var to override. Default is relative to _PROJECT_ROOT
# (Code/) so it travels with the project like MICROPOLITAN_STL_DIR — matches
# the actual on-disk layout:
#   3D Maps/
#   ├── Code/            <- _PROJECT_ROOT
#   └── strm_h5/strm_data.h5
#
# Future: if the h5 file is absent, fall back to OpenTopography SRTMGL3 API
# (same 90m SRTM3 data) or Google Earth Engine (SRTM/NASADEM, higher resolution
# possible).  See docs/todos/README.md for the current roadmap location.
_STRM_H5_ENV = os.environ.get("STRM_H5_ROOT")
H5_SRTM_ROOT: str | None = (
    _STRM_H5_ENV if _STRM_H5_ENV else str((_PROJECT_ROOT / ".." / "strm_h5").resolve())
)
H5_SRTM_FILE: Path | None = (
    Path(H5_SRTM_ROOT) / "strm_data.h5" if H5_SRTM_ROOT else None
)
H5_SRTM_AVAILABLE: bool = bool(H5_SRTM_FILE and H5_SRTM_FILE.exists())

# ---------------------------------------------------------------------------
# Legacy EE cache management constants (kept for clear_caches_if_needed())
# ---------------------------------------------------------------------------

CACHE_DIRS = [EE_CACHE_DIR]
CACHE_CLEAR_INTERVAL = 3600  # seconds between periodic EE cache sweeps
CACHE_MAX_FILES = 100        # trigger a sweep when this many files exist

# ---------------------------------------------------------------------------
# Grid / render limits
# ---------------------------------------------------------------------------

MAX_DIM: int = 2000            # maximum grid resolution accepted by all endpoints
MAX_BBOX_DIAGONAL_KM: float = 15.0  # cities endpoint bounding-box size cap (full detail tier)
MAX_BBOX_DIAGONAL_KM_COARSE: float = 25.0  # cities endpoint cap for detail="coarse" requests
# Building area floor for detail="coarse" requests. This is 1200 rather than the original
# 2000 because the filter used to measure area in Web Mercator, whose areas are inflated by
# sec^2(latitude) — about 1.68x at Breckenridge. Measurement now goes through local UTM
# (city2stl.fetch._to_metric), so keeping 2000 would have quietly tightened a threshold
# nobody chose. 1200 leaves the tier filtering about where it always did in practice, and
# now does so identically at every latitude.
COARSE_MIN_BUILDING_AREA_M2: float = 1200.0

# ---------------------------------------------------------------------------
# Luminance / colour constants (ITU-R BT.601 perceptual weights)
# ---------------------------------------------------------------------------

LUMINANCE_R: float = 0.299
LUMINANCE_G: float = 0.587
LUMINANCE_B: float = 0.114
