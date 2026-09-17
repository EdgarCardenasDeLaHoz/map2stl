"""
core/dem_cache.py — The single definition of the DEM disk-cache key.

Why this module exists
----------------------
The saved-location render pipeline is split across two unrelated HTTP requests
joined only by this cache:

  * ``GET /api/terrain/dem`` fetches the DEM and *writes* the array to disk.
  * ``POST /api/export/start`` receives settings but not the array, and *reads*
    it back.

Both sides have to compute an identical key from the same settings. They used
to do that with two hand-maintained copies of the same dict literal and the
same set of defaults, and the copies drifted twice:

  * ``dim`` defaulted to 200 on the export side and 600 on the terrain side.
  * ``maintain_dimensions`` changed default on the terrain side (F-PROJ-DIMS)
    and the export side was kept in step only by a comment.

Each drift made every settings-only export miss the cache and fail with
"Missing DEM data". Both sides now call :func:`dem_cache_key`, so a future
change to the key is a single edit that cannot disagree with itself.

What is deliberately *not* in the key
-------------------------------------
``projection`` and ``clip_valid_region`` are applied per request, after the
cache read, so the raw grid is cached once per bbox and toggling a projection
is free. Do not add them.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Bump when the *meaning* of a cached entry changes, so stale entries written
# under the old interpretation are ignored rather than misread.
DEM_CACHE_SCHEMA_VERSION = 3

# The canonical default for every field in the key. Both the writer
# (routers/terrain.py) and the reader (core/export_params.py) resolve missing
# fields through here, so "absent" means the same thing on both sides.
DEM_SETTING_DEFAULTS: dict[str, Any] = {
    "dim": 600,
    "dem_source": "local",
    "depth_scale": 0.5,
    "water_scale": 0.05,
    "subtract_water": True,
    "maintain_dimensions": False,
    "show_sat": False,
}

# Short names used inside the hashed payload. Kept separate from the readable
# setting names so the on-disk key format is stable even if a setting is
# renamed in the API.
_KEY_ABBREV = {
    "dim": "dim",
    "dem_source": "src",
    "depth_scale": "ds",
    "water_scale": "ws",
    "subtract_water": "sw",
    "maintain_dimensions": "md",
    "show_sat": "sat",
}

_COERCE = {
    "dim": int,
    "dem_source": str,
    "depth_scale": float,
    "water_scale": float,
    "subtract_water": bool,
    "maintain_dimensions": bool,
    "show_sat": bool,
}


def normalize_dem_settings(settings: dict | None) -> dict[str, Any]:
    """Return every key-relevant DEM setting, defaulted and type-coerced.

    Accepts a partial dict (as sent by a client) and fills the gaps from
    :data:`DEM_SETTING_DEFAULTS`. Unknown keys are ignored, so callers can pass
    a whole settings blob without filtering it first.
    """
    src = settings or {}
    out: dict[str, Any] = {}
    for name, default in DEM_SETTING_DEFAULTS.items():
        value = src.get(name, default)
        if value is None:
            value = default
        try:
            out[name] = _COERCE[name](value)
        except (TypeError, ValueError):
            logger.debug("dem setting %r=%r not coercible; using default %r",
                         name, value, default)
            out[name] = default
    return out


def dem_cache_key(north: float, south: float, east: float, west: float,
                  settings: dict | None) -> str:
    """Return the disk-cache key for a DEM at ``bbox`` with ``settings``.

    This is the only place the key is built. ``settings`` may be partial; it is
    passed through :func:`normalize_dem_settings` first.
    """
    from app.server.core.cache import make_cache_key

    norm = normalize_dem_settings(settings)
    payload = {"v": DEM_CACHE_SCHEMA_VERSION}
    payload.update({_KEY_ABBREV[name]: norm[name] for name in DEM_SETTING_DEFAULTS})
    return make_cache_key("dem", north, south, east, west, payload)
