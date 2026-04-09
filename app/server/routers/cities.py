"""
routers/cities.py — /api/cities/* endpoints.

Extracted from location_picker.py (backend refactor, step 6).
Delegates OSM fetching to core/osm.py and caching to core/cache.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cities"])

# ---------------------------------------------------------------------------
# Config imports
# ---------------------------------------------------------------------------
try:
    from app.server.config import OSM_CACHE_PATH
except ImportError:
    _UI_DIR = Path(__file__).parent.parent
    OSM_CACHE_PATH = _UI_DIR.parent / "osm_raw_cache"

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
try:
    from app.server.core.cache import read_osm_cache, write_osm_cache, osm_cache_key, CACHE_ROOT
    _CACHE_AVAILABLE = True
except ImportError:
    _CACHE_AVAILABLE = False
    read_osm_cache = write_osm_cache = osm_cache_key = CACHE_ROOT = None  # type: ignore

# ---------------------------------------------------------------------------
# OSM fetch helper
# ---------------------------------------------------------------------------
try:
    from app.server.core.osm import fetch_osm_data as _fetch_osm_data, rasterize_city_data as _rasterize_city_data
except ImportError:
    def _fetch_osm_data(*a, **kw):
        raise RuntimeError("core.osm not available")
    def _rasterize_city_data(*a, **kw):
        raise RuntimeError("core.osm not available")

# ---------------------------------------------------------------------------
# 3D export helper
# ---------------------------------------------------------------------------
try:
    from app.server.core.cities_3d import generate_city_3mf
    _CITIES_3D_AVAILABLE = True
except ImportError:
    _CITIES_3D_AVAILABLE = False
    generate_city_3mf = None  # type: ignore

from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from app.server.schemas import CityRequest, CityRasterRequest


class CityExportRequest(BaseModel):
    """Request body for POST /api/cities/export3mf."""
    north: float
    south: float
    east: float
    west: float
    dem_values:   List[float]
    dem_width:    int
    dem_height:   int
    buildings:    Dict[str, Any]          # GeoJSON FeatureCollection
    model_height_mm:  float = 20.0
    base_mm:          float = 5.0
    building_z_scale: float = 0.5        # mm per real metre for building heights
    simplify_terrain: bool  = True       # Cities 14: reduce terrain triangle count
    name:             str   = "city"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/cities/cached")
async def check_city_cache(
    north: float, south: float, east: float, west: float,
    simplify_tolerance: float = 2.0, min_area: float = 20.0,
):
    """Check whether OSM city data for this bbox is already cached locally."""
    key = osm_cache_key(north, south, east, west, simplify_tolerance, min_area)
    if _CACHE_AVAILABLE:
        cached = (CACHE_ROOT / "osm" / f"{key}.json.gz").exists()
    else:
        OSM_CACHE_PATH.mkdir(parents=True, exist_ok=True)
        cached = (OSM_CACHE_PATH / f"{key}.json").exists()
    return JSONResponse(content={"cached": cached, "cache_key": key})


@router.post("/api/cities")
async def get_city_data(city_req: CityRequest):
    """
    Fetch OSM building, road, waterway, and POI data for a small bounding box.
    Results are cached as .json.gz. Region must be ≤ 15 km diagonal.
    """
    north, south, east, west = city_req.north, city_req.south, city_req.east, city_req.west
    layers = city_req.layers or ["buildings", "roads", "waterways"]

    # Server-side size guard
    R = 6371.0
    dLat = (north - south) * math.pi / 180
    dLon = (east - west) * math.pi * math.cos(((north + south) / 2) * math.pi / 180) / 180
    diag_km = math.sqrt((R * dLat) ** 2 + (R * dLon) ** 2)
    if diag_km > 15:
        return JSONResponse(
            content={"error": f"Bounding box too large ({diag_km:.1f} km diagonal, max 15 km)"},
            status_code=422,
        )

    cache_key = osm_cache_key(north, south, east, west,
                               city_req.simplify_tolerance, city_req.min_area)

    # Cache check
    if _CACHE_AVAILABLE:
        cached_data = read_osm_cache(cache_key)
        if cached_data is not None:
            logger.info(f"Serving OSM data from .json.gz cache: {cache_key}")
            return JSONResponse(content=cached_data)
    else:
        OSM_CACHE_PATH.mkdir(parents=True, exist_ok=True)
        cache_file = OSM_CACHE_PATH / f"{cache_key}.json"
        if cache_file.exists():
            try:
                cached_data = json.loads(cache_file.read_text())
                logger.info(f"Serving OSM data from legacy cache: {cache_key}")
                return JSONResponse(content=cached_data)
            except Exception as cache_read_err:
                logger.debug(f"Legacy OSM cache read failed, re-fetching: {cache_read_err}")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, _fetch_osm_data, north, south, east, west, layers,
            city_req.simplify_tolerance, city_req.min_area,
        )
    except Exception as e:
        logger.error(f"OSM fetch error: {e}")
        return JSONResponse(content={"error": f"OSM fetch failed: {str(e)}"}, status_code=500)

    result["cache_key"] = cache_key
    result["diagonal_km"] = round(diag_km, 2)
    has_error = any("error" in v for v in result.values() if isinstance(v, dict))
    if not has_error:
        if _CACHE_AVAILABLE:
            write_osm_cache(cache_key, result)
        else:
            OSM_CACHE_PATH.mkdir(parents=True, exist_ok=True)
            try:
                (OSM_CACHE_PATH / f"{cache_key}.json").write_text(json.dumps(result))
            except Exception as ce:
                logger.warning(f"OSM cache write failed: {ce}")

    return JSONResponse(content=result)


@router.post("/api/cities/raster")
async def get_city_raster(req: CityRasterRequest):
    """
    Burn OSM building/road/waterway GeoJSON onto a dim×dim float32 height-map.
    Buildings are raised by their height_m, roads are flat, waterways depressed.
    Returns a DEM-compatible response: { values, width, height, vmin, vmax, bbox }.
    Cached as .npz alongside other DEM rasters.
    """
    import hashlib

    cache_key = hashlib.md5(
        f"cityRaster|{req.north:.4f}_{req.south:.4f}_{req.east:.4f}_{req.west:.4f}"
        f"_dim{req.dim}_bs{req.building_scale}_rd{req.road_depression_m}_wd{req.water_depression_m}".encode()
    ).hexdigest()

    # Cache check
    if _CACHE_AVAILABLE and CACHE_ROOT is not None:
        import numpy as np
        cache_path = CACHE_ROOT / "dem" / f"{cache_key}.npz"
        if cache_path.exists():
            try:
                arr = np.load(cache_path)
                values = arr["values"].flatten().tolist()
                return JSONResponse(content={
                    "values": values,
                    "width": int(arr["width"]),
                    "height": int(arr["height"]),
                    "vmin": float(arr["vmin"]),
                    "vmax": float(arr["vmax"]),
                    "bbox": {"north": req.north, "south": req.south,
                             "east": req.east, "west": req.west},
                })
            except Exception as e:
                logger.debug(f"City raster cache read failed: {e}")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: _rasterize_city_data(
                req.north, req.south, req.east, req.west, req.dim,
                req.buildings, req.roads, req.waterways,
                req.building_scale, req.road_depression_m, req.water_depression_m,
            ),
        )
    except Exception as e:
        logger.error(f"City raster error: {e}", exc_info=True)
        return JSONResponse(content={"error": str(e)}, status_code=500)

    # Cache result
    if _CACHE_AVAILABLE and CACHE_ROOT is not None:
        try:
            import numpy as np
            cache_path = CACHE_ROOT / "dem" / f"{cache_key}.npz"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                cache_path,
                values=np.array(result["values"], dtype=np.float32),
                width=np.array(result["width"]),
                height=np.array(result["height"]),
                vmin=np.array(result["vmin"]),
                vmax=np.array(result["vmax"]),
            )
        except Exception as e:
            logger.debug(f"City raster cache write failed: {e}")

    return JSONResponse(content=result)


@router.post("/api/cities/export3mf")
async def export_city_3mf(req: CityExportRequest):
    """
    Generate a 3MF file containing the terrain mesh plus extruded building prisms.
    Cities 10+12.

    Expects DEM values (from /api/terrain/dem) and a buildings GeoJSON
    FeatureCollection (from /api/cities) with height_m and terrain_z properties.
    """
    if not _CITIES_3D_AVAILABLE:
        return JSONResponse(
            content={"error": "core.cities_3d not available"},
            status_code=501,
        )
    try:
        bbox = {"north": req.north, "south": req.south, "east": req.east, "west": req.west}
        three_mf_bytes = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: generate_city_3mf(
                buildings_geojson=req.buildings,
                dem_values=req.dem_values,
                dem_width=req.dem_width,
                dem_height=req.dem_height,
                bbox=bbox,
                model_height_mm=req.model_height_mm,
                base_mm=req.base_mm,
                building_z_scale=req.building_z_scale,
                simplify_terrain=req.simplify_terrain,
                name=req.name,
            ),
        )
        filename = f"{req.name}_city.3mf"
        return Response(
            content=three_mf_bytes,
            media_type="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.error(f"City 3MF export error: {e}", exc_info=True)
        return JSONResponse(content={"error": "3MF export failed"}, status_code=500)
