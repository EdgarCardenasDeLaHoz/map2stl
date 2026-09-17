"""Configuration for v2.

v2 reuses v1's on-disk assets — the same SQLite database of saved regions, the same
OpenTopography key, the same local SRTM store — but owns none of them. Everything here
resolves a path or reads a value; nothing is written back to a v1 location except the
region settings blob, and only when the user explicitly saves.

Paths are resolved relative to this file so the server can be started from any working
directory. v1 had the same requirement and met it the same way; the rule that produced it
(never call os.chdir) applies here too.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# .../strm2stl/v2/server/config.py -> server -> v2 -> strm2stl
STRM2STL_DIR = Path(__file__).resolve().parent.parent.parent
CODE_DIR = STRM2STL_DIR.parent

# geo2stl lives under strm2stl/, numpy2stl under Code/. Neither is installed into the
# virtualenv, so both roots have to be importable. v1 does this in server.py:55.
for _p in (str(CODE_DIR), str(STRM2STL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

V2_DIR = STRM2STL_DIR / "v2"
CACHE_DIR = V2_DIR / "cache"
DEM_STORE_DIR = CACHE_DIR / "dem"
WEB_BUILD_DIR = V2_DIR / "web" / "build"

# Shared with v1, read-only unless the user saves a region's settings.
DB_PATH = STRM2STL_DIR / "data.db"


def _read_opentopo_key() -> str | None:
    """Environment first, then v1's config.json.

    config.json holds the key in plaintext and is gitignored. Never log the value.
    """
    key = os.environ.get("OPENTOPO_API_KEY")
    if key:
        return key
    cfg = STRM2STL_DIR / "config.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("opentopo_api_key") or None
        except Exception as exc:  # pragma: no cover - corrupt config is not fatal
            logger.warning("Could not read %s: %s", cfg.name, exc)
    return None


OPENTOPO_API_KEY = _read_opentopo_key()

# v1 resolved this two different ways — geo2stl/dem.py read STRM_H5_ROOT alone, which is
# unset, while app/server/config.py used a project-relative fallback that exists. The
# mismatch made every "local H5" request silently fall back to a network fetch and turned a
# 0.27 s load into 11.9 s. One resolution, used everywhere.
STRM_H5_ROOT = Path(os.environ.get("STRM_H5_ROOT") or (CODE_DIR.parent / "strm_h5")).resolve()

HOST = os.environ.get("V2_HOST", "127.0.0.1")
PORT = int(os.environ.get("V2_PORT", "9100"))

# Guardrails. A request past these is rejected with a message naming the limit, rather
# than accepted and left to exhaust memory.
MAX_DIM = 2000
MAX_BBOX_SPAN_DEG = 60.0

# How long a fetched DEM stays addressable by its id, and how many are kept resident.
DEM_TTL_SECONDS = 60 * 60
DEM_MEMORY_LIMIT = 8

# How long a finished export stays downloadable before its temp file is swept.
JOB_TTL_SECONDS = 30 * 60
