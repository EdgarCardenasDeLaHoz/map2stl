"""
routers/cities.py — /api/cities/* endpoints.

Extracted from location_picker.py (backend refactor, step 6).
Delegates OSM fetching to core/osm.py and caching to core/cache.py.
"""

from __future__ import annotations
from app.server.schemas import CityRequest, CityRasterRequest, EnhanceHeightsRequest
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from app.server.config import OSM_CACHE_PATH
from app.server.core.validation import validate_bbox_diagonal, run_sync
from app.server.core.responses import error_response

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cities"])

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
    from city2stl.fetch import fetch_osm_data as _fetch_osm_data
    from city2stl.rasterize import rasterize_city_data as _rasterize_city_data
    from city2stl.cache_policy import (
        city_cache_missing_building_parts,
        city_cache_missing_height_source,
    )
except ImportError:
    def _fetch_osm_data(*a, **kw):
        raise RuntimeError(
            "city2stl.fetch or city2stl.rasterize not available")

    def _rasterize_city_data(*a, **kw):
        raise RuntimeError(
            "city2stl.fetch or city2stl.rasterize not available")

    def city_cache_missing_building_parts(*a, **kw):
        return False

    def city_cache_missing_height_source(*a, **kw):
        return False

# ---------------------------------------------------------------------------
# 3D export helper
# ---------------------------------------------------------------------------
try:
    from app.server.core.cities_3d import generate_city_3mf
    _CITIES_3D_AVAILABLE = True
except ImportError:
    _CITIES_3D_AVAILABLE = False
    generate_city_3mf = None  # type: ignore

try:
    from app.server.core.height.service import enhance_city_data as _enhance_city_data
except ImportError:
    _enhance_city_data = None  # type: ignore


class CityExportRequest(BaseModel):
    """Request body for POST /api/cities/export3mf.

    DEM and buildings data are resolved from the server-side disk cache.
    Legacy callers may still pass dem_values/buildings directly — the
    endpoint accepts both forms.
    """
    north: float
    south: float
    east: float
    west: float
    dem_values:   Optional[List[float]] = None
    dem_width:    Optional[int] = None
    dem_height:   Optional[int] = None
    buildings:    Optional[Dict[str, Any]] = None   # GeoJSON FeatureCollection
    # DEM cache lookup settings (used when dem_values is not provided)
    bbox:         Optional[Dict[str, float]] = None
    dem:          Optional[Dict[str, Any]] = None
    model_height_mm:  float = 20.0
    base_mm:          float = 5.0
    building_z_scale: float = 0.5        # mm per real metre for building heights
    simplify_terrain: bool = True       # Cities 14: reduce terrain triangle count
    name:             str = "city"


def _city_clip_valid_region(req) -> bool:
    """Return the preferred clip setting while accepting the legacy alias."""
    clip_valid_region = getattr(req, "clip_valid_region", None)
    if clip_valid_region is not None:
        return bool(clip_valid_region)
    return bool(getattr(req, "clip_nans", True))


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
    diag_km, diag_err = validate_bbox_diagonal(north, south, east, west)
    if diag_err:
        return diag_err

    cache_key = osm_cache_key(north, south, east, west,
                              city_req.simplify_tolerance, city_req.min_area)

    # Cache check
    if _CACHE_AVAILABLE:
        cached_data = read_osm_cache(cache_key)
        if cached_data is not None:
            if city_cache_missing_height_source(cached_data) or city_cache_missing_building_parts(cached_data):
                logger.info("Ignoring stale OSM cache payload: %s", cache_key)
            else:
                logger.info(
                    f"Serving OSM data from .json.gz cache: {cache_key}")
                return JSONResponse(content=cached_data)
    else:
        OSM_CACHE_PATH.mkdir(parents=True, exist_ok=True)
        cache_file = OSM_CACHE_PATH / f"{cache_key}.json"
        if cache_file.exists():
            try:
                cached_data = json.loads(cache_file.read_text())
                if city_cache_missing_height_source(cached_data) or city_cache_missing_building_parts(cached_data):
                    logger.info(
                        "Ignoring stale legacy OSM cache payload: %s", cache_key)
                else:
                    logger.info(
                        f"Serving OSM data from legacy cache: {cache_key}")
                    return JSONResponse(content=cached_data)
            except Exception as cache_read_err:
                logger.debug(
                    f"Legacy OSM cache read failed, re-fetching: {cache_read_err}")

    try:
        result = await run_sync(
            _fetch_osm_data, north, south, east, west, layers,
            city_req.simplify_tolerance, city_req.min_area,
        )
    except Exception as e:
        logger.error(f"OSM fetch error: {e}")
        return error_response(f"OSM fetch failed: {str(e)}")

    if _enhance_city_data is not None:
        try:
            result = await run_sync(
                _enhance_city_data,
                result,
                north,
                south,
                east,
                west,
            )
        except Exception as enhance_err:
            logger.warning(
                "City height auto-enhancement skipped: %s", enhance_err)

    result.setdefault("city_pipeline_version", 2)

    result["cache_key"] = cache_key
    result["diagonal_km"] = round(diag_km, 2)
    has_error = any("error" in v for v in result.values()
                    if isinstance(v, dict))
    if not has_error:
        if _CACHE_AVAILABLE:
            write_osm_cache(cache_key, result)
        else:
            OSM_CACHE_PATH.mkdir(parents=True, exist_ok=True)
            try:
                (OSM_CACHE_PATH /
                 f"{cache_key}.json").write_text(json.dumps(result))
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
    import numpy as np

    def _sanitize_raster_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize raster payload so JSON serialization never sees NaN/Inf."""
        grid = np.array(payload["values"], dtype=np.float32).reshape(
            int(payload["height"]), int(payload["width"])
        )

        finite = np.isfinite(grid)
        if not finite.all():
            bad_count = int(grid.size - np.count_nonzero(finite))
            logger.warning(
                f"City raster contained {bad_count} non-finite values; replacing with 0.0")
            grid = np.nan_to_num(grid, nan=0.0, posinf=0.0, neginf=0.0)
            finite = np.isfinite(grid)

        finite_vals = grid[finite]
        if finite_vals.size == 0:
            vmin = 0.0
            vmax = 0.0
        else:
            vmin = float(finite_vals.min())
            vmax = float(finite_vals.max())

        return {
            "values": grid.flatten().tolist(),
            "width": int(payload["width"]),
            "height": int(payload["height"]),
            "vmin": vmin,
            "vmax": vmax,
            "bbox": payload["bbox"],
        }

    clip_valid_region = _city_clip_valid_region(req)

    # Note: Cache key does NOT include projection or clip_valid_region.
    # Raw city raster is cached once per bbox; projection/clipping applied on fetch.
    cache_key = hashlib.md5(
        f"cityRaster|{req.north:.4f}_{req.south:.4f}_{req.east:.4f}_{req.west:.4f}"
        f"_dim{req.dim}_bs{req.building_scale}_rd{req.road_depression_m}_wd{req.water_depression_m}".encode()
    ).hexdigest()

    # Cache check
    if _CACHE_AVAILABLE and CACHE_ROOT is not None:
        cache_path = CACHE_ROOT / "dem" / f"{cache_key}.npz"
        if cache_path.exists():
            try:
                arr = np.load(cache_path)
                raw_h = int(arr["height"])
                raw_w = int(arr["width"])
                grid = np.array(arr["values"], dtype=np.float32).reshape(
                    raw_h, raw_w)

                # Projection/clipping is always applied fresh from raw cached raster.
                if req.projection != "none":
                    from geo2stl.projections import project_grid
                    grid = project_grid(
                        grid,
                        req.north, req.south, req.east, req.west,
                        req.projection, clip_valid_region, categorical=False,
                    )

                cached_result = _sanitize_raster_result({
                    "values": np.nan_to_num(grid, nan=0.0).flatten().tolist(),
                    "width": int(grid.shape[1]),
                    "height": int(grid.shape[0]),
                    "vmin": float(np.nanmin(grid)),
                    "vmax": float(np.nanmax(grid)),
                    "bbox": {"north": req.north, "south": req.south,
                             "east": req.east, "west": req.west},
                })
                return JSONResponse(content=cached_result)
            except Exception as e:
                logger.debug(f"City raster cache read failed: {e}")

    # Resolve GeoJSON from OSM cache when not provided in request body
    buildings = req.buildings
    roads = req.roads
    waterways = req.waterways
    _from_cache = False
    if (not buildings.get("features") and not roads.get("features")
            and not waterways.get("features") and _CACHE_AVAILABLE):
        osm_key = osm_cache_key(req.north, req.south, req.east, req.west)
        osm_data = read_osm_cache(osm_key)
        if osm_data:
            buildings = osm_data.get("buildings", buildings)
            roads = osm_data.get("roads", roads)
            waterways = osm_data.get("waterways", waterways)
            _from_cache = True
            logger.debug(
                "City raster: resolved GeoJSON from OSM cache (%s)", osm_key[:8])

    try:
        result = await run_sync(
            _rasterize_city_data,
            req.north, req.south, req.east, req.west, req.dim,
            buildings, roads, waterways,
            req.building_scale, req.road_depression_m, req.water_depression_m,
        )
    except Exception as e:
        logger.error(f"City raster error: {e}", exc_info=True)
        return error_response(str(e))

    # Cache raw unprojected result (cache key has NO projection/clip params).
    raw_result = _sanitize_raster_result(result)
    if _CACHE_AVAILABLE and CACHE_ROOT is not None:
        try:
            cache_path = CACHE_ROOT / "dem" / f"{cache_key}.npz"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                cache_path,
                values=np.array(raw_result["values"], dtype=np.float32),
                width=np.array(raw_result["width"]),
                height=np.array(raw_result["height"]),
                vmin=np.array(raw_result["vmin"]),
                vmax=np.array(raw_result["vmax"]),
            )
        except Exception as e:
            logger.debug(f"City raster cache write failed: {e}")

    # Apply map projection (all raster layers use the same pipeline)
    if req.projection != "none":
        from geo2stl.projections import project_grid

        grid = np.array(result["values"], dtype=np.float32).reshape(
            result["height"], result["width"])
        grid = project_grid(grid, req.north, req.south, req.east, req.west,
                            req.projection, clip_valid_region, categorical=False)
        h, w = grid.shape
        result = {
            "values": np.nan_to_num(grid, nan=0.0).flatten().tolist(),
            "width": w,
            "height": h,
            "vmin": float(np.nanmin(grid)),
            "vmax": float(np.nanmax(grid)),
            "bbox": {"north": req.north, "south": req.south,
                     "east": req.east, "west": req.west},
        }

    result = _sanitize_raster_result(result)

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
        return error_response("core.cities_3d not available", 501)

    # Resolve DEM from cache when not provided
    dem_values = req.dem_values
    dem_width = req.dem_width
    dem_height = req.dem_height
    if not dem_values:
        from app.server.core.export import resolve_dem_from_cache
        req_dict = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        resolved = resolve_dem_from_cache(req_dict)
        if resolved:
            dem_values, dem_height, dem_width = resolved
        else:
            return error_response("DEM not found in cache — load DEM first", 400)

    # Resolve buildings from OSM cache when not provided
    buildings = req.buildings
    if (not buildings or not buildings.get("features")) and _CACHE_AVAILABLE:
        osm_key = osm_cache_key(req.north, req.south, req.east, req.west)
        osm_data = read_osm_cache(osm_key)
        if osm_data and osm_data.get("buildings"):
            buildings = osm_data["buildings"]
            logger.debug(
                "City export: resolved buildings from OSM cache (%s)", osm_key[:8])
        else:
            return error_response("Buildings not found in cache — load city data first", 400)

    try:
        bbox = {"north": req.north, "south": req.south,
                "east": req.east, "west": req.west}
        three_mf_bytes = await run_sync(
            generate_city_3mf,
            buildings_geojson=buildings,
            dem_values=dem_values,
            dem_width=dem_width,
            dem_height=dem_height,
            bbox=bbox,
            model_height_mm=req.model_height_mm,
            base_mm=req.base_mm,
            building_z_scale=req.building_z_scale,
            simplify_terrain=req.simplify_terrain,
            name=req.name,
        )
        filename = f"{req.name}_city.3mf"
        return Response(
            content=three_mf_bytes,
            media_type="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.error(f"City 3MF export error: {e}", exc_info=True)
        return error_response("3MF export failed")


# ---------------------------------------------------------------------------
# Google 3D height enhancement
# ---------------------------------------------------------------------------

@router.get("/api/cities/google3d-available")
async def google3d_available():
    """Check if Google 3D Tiles API key is configured."""
    from city2stl.skyline_cv.height.providers.google_3d import _get_api_key
    return JSONResponse(content={"available": _get_api_key() is not None})


@router.post("/api/cities/enhance-heights")
async def enhance_heights(req: EnhanceHeightsRequest):
    """Enhance building heights using Google 3D photogrammetric tiles.

    Fetches a height raster from Google 3D Tiles, then samples it at
    each building centroid to replace default (10 m) heights with real
    photogrammetric measurements.
    """
    from city2stl.skyline_cv.height.providers.google_3d import Google3DProvider, _get_api_key
    from city2stl.heights import enhance_buildings_with_raster
    import numpy as np

    if not _get_api_key():
        return error_response(
            "Google Maps API key not configured. "
            "Set GOOGLE_MAPS_API_KEY env var or add google_maps_api_key to config.json.",
            400,
        )

    diag_km, diag_err = validate_bbox_diagonal(
        req.north, req.south, req.east, req.west
    )
    if diag_err:
        return diag_err

    # Resolve buildings from OSM cache when not provided
    buildings = req.buildings
    if (not buildings or not buildings.get("features")) and _CACHE_AVAILABLE:
        osm_key = osm_cache_key(req.north, req.south, req.east, req.west)
        osm_data = read_osm_cache(osm_key)
        if osm_data and osm_data.get("buildings"):
            buildings = osm_data["buildings"]
            logger.debug(
                "Enhance heights: resolved buildings from OSM cache (%s)", osm_key[:8])
        else:
            return error_response("Buildings not found in cache — load city data first", 400)

    bbox = (req.north, req.south, req.east, req.west)
    dim = (req.dim, req.dim)

    try:
        # Fetch terrain DEM for ground subtraction (DSM - DEM = building height)
        from geo2stl.dem import compute_raw_dem
        dem_result = await run_sync(
            compute_raw_dem, req.north, req.south, req.east, req.west,
            req.dim, 1,  # depth_scale=1 (no bathymetry scaling)
        )
        dem_array = None
        if dem_result is not None:
            dem_array = np.asarray(dem_result, dtype=np.float32)

        # Fetch Google 3D height raster
        provider = Google3DProvider()
        height_result = await run_sync(
            provider.fetch_heights, bbox, dim, dem_array
        )

        valid_px = int(np.count_nonzero(~np.isnan(height_result.raster)))
        logger.info(
            f"Google 3D raster: {valid_px}/{height_result.raster.size} valid pixels "
            f"({valid_px / height_result.raster.size * 100:.1f}% coverage)"
        )

        # Enhance buildings
        result = enhance_buildings_with_raster(
            buildings,
            height_result.raster,
            bbox,
            confidence_raster=height_result.confidence,
            source_name="google3d",
        )

        return JSONResponse(content=result)

    except Exception as e:
        logger.error(f"Height enhancement error: {e}", exc_info=True)
        return error_response(f"Height enhancement failed: {str(e)}")
