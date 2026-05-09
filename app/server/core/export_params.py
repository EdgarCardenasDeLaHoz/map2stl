"""
core/export_params.py — Export parameter parsing and DEM cache resolution.

Extracts, validates, and type-casts export parameters from client requests.
Supports both inline DEM arrays and cache-based DEM resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


def resolve_dem_from_cache(data: dict) -> tuple[list, int, int] | None:
    """Look up a cached DEM from bbox + DEM settings.

    The DEM endpoint caches processed arrays under a key derived from
    bbox + {dim, src, proj, ds, ws, sw, md, cn, sat}.  If the caller
    provides these settings instead of raw ``dem_values``, we can
    reconstruct the key and read from disk — eliminating the need to
    retransmit the (potentially multi-MB) array.

    Returns ``(dem_values_list, height, width)`` or ``None`` on cache miss.
    """
    from app.server.core.cache import make_cache_key, read_array_cache

    bbox = data.get("bbox") or data
    north = bbox.get("north")
    south = bbox.get("south")
    east  = bbox.get("east")
    west  = bbox.get("west")
    if None in (north, south, east, west):
        return None

    # DEM settings — match the key structure in terrain.py get_terrain_dem()
    dem = data.get("dem") or data
    dim         = int(dem.get("dim", 200))
    dem_source  = dem.get("dem_source", "local")
    projection  = dem.get("projection", "cosine")
    depth_scale = float(dem.get("depth_scale", 0.5))
    water_scale = float(dem.get("water_scale", 0.05))
    subtract_water     = bool(dem.get("subtract_water", True))
    maintain_dimensions = bool(dem.get("maintain_dimensions", True))
    clip_nans   = bool(dem.get("clip_nans", False))
    show_sat    = bool(dem.get("show_sat", False))

    cache_key = make_cache_key("dem", north, south, east, west, {
        "dim": dim, "src": dem_source, "proj": projection,
        "ds": depth_scale, "ws": water_scale,
        "sw": subtract_water, "md": maintain_dimensions,
        "cn": clip_nans, "sat": show_sat,
    })

    cached = read_array_cache("dem", cache_key)
    if cached is None or cached[0].get("dem") is None:
        logger.debug("DEM cache miss for export (key %s)", cache_key[:8])
        return None

    dem_arr = cached[0]["dem"]  # np.ndarray (H, W)
    h, w = dem_arr.shape
    logger.info("DEM resolved from cache for export (key %s, %dx%d)", cache_key[:8], w, h)
    return dem_arr.ravel().tolist(), h, w


@dataclass
class ExportContext:
    """Typed container for parsed export parameters.

    Replaces the raw dict returned by _parse_export_params, giving IDE
    autocompletion and catching typos at attribute-access time.
    """
    dem_values: List[float]
    height: int
    width: int
    model_height: float = 20.0
    base_height: float = 5.0
    exaggeration: float = 1.0
    sea_level_cap: bool = False
    name: str = "terrain"

    @classmethod
    def from_request(cls, data: dict) -> "ExportContext":
        """Construct from an incoming request dict.

        Supports two modes:
        - **Legacy (array):** ``dem_values``, ``height``, ``width`` in the dict.
        - **Settings-only:** ``bbox`` + ``dem`` settings — DEM is read from
          the server-side disk cache (populated when the user loaded the DEM
          in the browser).  This avoids retransmitting multi-MB arrays.
        """
        dem_values = data.get("dem_values", [])
        height = data.get("height", 0)
        width = data.get("width", 0)

        # Settings-only mode: resolve DEM from cache
        if not dem_values:
            resolved = resolve_dem_from_cache(data)
            if resolved is not None:
                dem_values, height, width = resolved

        return cls(
            dem_values=dem_values,
            height=height,
            width=width,
            model_height=float(data.get("model_height", 20)),
            base_height=float(data.get("base_height", 5)),
            exaggeration=float(data.get("exaggeration", 1.0)),
            sea_level_cap=bool(data.get("sea_level_cap", False)),
            name=data.get("name", "terrain"),
        )


def _parse_export_params(data: dict) -> ExportContext:
    """Extract and type-cast the common export parameters from a request dict."""
    return ExportContext.from_request(data)
