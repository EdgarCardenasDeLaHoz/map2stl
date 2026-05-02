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


def _building_features(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    buildings = payload.get("buildings") or {}
    features = buildings.get("features") or []
    return features if isinstance(features, list) else []


def _city_cache_missing_height_source(payload: Dict[str, Any]) -> bool:
    """Detect older cached city payloads created before height_source existed."""
    features = _building_features(payload)
    if not features:
        return False
    return any("height_source" not in ((feat.get("properties") or {})) for feat in features)


def _city_cache_missing_building_parts(payload: Dict[str, Any]) -> bool:
    """Detect cached payloads written before building:parts and extended layers were added.

    When towers/churches/fortifications keys are absent the data was cached before
    the _fetch_building_parts merge was introduced — a fresh fetch is needed.
    """
    return "towers" not in payload and "churches" not in payload


def _city_cache_needs_enrichment(payload: Dict[str, Any]) -> bool:
    """Return True when default-height buildings still need raster enhancement."""
    if _city_cache_missing_height_source(payload):
        return True

    features = _building_features(payload)
    if not features:
        return False

    enhancement = payload.get("height_enhancement") or {}
    if enhancement.get("source_name") == "merged":
        return False

    return any((feat.get("properties") or {}).get("height_source") == "default" for feat in features)


def _enhance_city_data(
    payload: Dict[str, Any],
    north: float,
    south: float,
    east: float,
    west: float,
    dim: int = 512,
) -> Dict[str, Any]:
    """Fill default OSM building heights from the merged global height stack."""
    from app.server.core.height import merge_height_rasters
    from app.server.core.height.providers.ndsm import NDSMProvider
    from app.server.core.height.providers.wsf3d import WSF3DProvider
    from app.server.core.height.providers.copernicus import CopernicusProvider
    from app.server.core.height.providers.ghsl import GHSLProvider
    from app.server.core.height.providers.open_buildings import OpenBuildingsProvider
    from app.server.core.height.providers.shadow_height import ShadowHeightProvider
    from app.server.core.osm import enhance_buildings_with_raster

    features = _building_features(payload)
    if not features:
        return payload

    # Keep US regions OSM-driven (height / building:levels + default fallback)
    # instead of applying raster enhancement.
    center_lat = (north + south) * 0.5
    center_lon = (east + west) * 0.5
    in_conus = 24.0 <= center_lat <= 50.0 and -125.0 <= center_lon <= -66.0
    in_alaska = 51.0 <= center_lat <= 72.0 and -170.0 <= center_lon <= -129.0
    in_hawaii = 18.0 <= center_lat <= 23.5 and -161.0 <= center_lon <= -154.0
    in_us = in_conus or in_alaska or in_hawaii
    if in_us:
        payload["height_enhancement"] = {
            "source_name": "osm_only_us",
            "providers_used": [],
            "resolution_m": 0.0,
            "stats": {
                "skipped": True,
                "reason": "US bbox uses OSM heights only",
            },
        }
        return payload

    default_count = sum(
        1 for feat in features
        if (feat.get("properties") or {}).get("height_source") == "default"
    )
    if default_count == 0:
        return payload

    bbox = (north, south, east, west)
    providers = [
        provider for provider in (
            NDSMProvider(),
            CopernicusProvider(),
            OpenBuildingsProvider(),
            WSF3DProvider(),
            GHSLProvider(),
            ShadowHeightProvider(),
        )
        if provider.covers(bbox)
    ]

    results = []
    for provider in providers:
        try:
            result = provider.fetch_heights(bbox, (dim, dim))
        except Exception as exc:
            logger.warning("City height enhancement provider '%s' failed: %s", provider.name, exc)
            continue

        if result.raster.size == 0:
            continue

        try:
            import numpy as np

            valid_pixels = int(np.count_nonzero(~np.isnan(result.raster)))
        except Exception:
            valid_pixels = 0
        if valid_pixels <= 0:
            continue
        results.append(result)

    if not results:
        return payload

    merged = merge_height_rasters(results, target_shape=(dim, dim))
    enhanced = enhance_buildings_with_raster(
        payload["buildings"],
        merged.raster,
        bbox,
        confidence_raster=merged.confidence,
        source_name=merged.source_name,
    )
    payload["buildings"] = enhanced["buildings"]
    payload["height_enhancement"] = {
        "source_name": merged.source_name,
        "providers_used": [item.source_name for item in results],
        "resolution_m": float(merged.resolution_m),
        "stats": enhanced.get("stats") or {},
    }
    return payload

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/cities/cached")
async def check_city_cache(
    north: float, south: float, east: float, west: float,
    simplify_tolerance: float = 3.0, min_area: float = 5.0,
    m_per_level: float = 3.5,
):
    """Check whether OSM city data for this bbox is already cached locally."""
    key = osm_cache_key(north, south, east, west, simplify_tolerance, min_area, m_per_level)
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
                              city_req.simplify_tolerance, city_req.min_area,
                              city_req.m_per_level)

    # Cache check
    if _CACHE_AVAILABLE:
        cached_data = read_osm_cache(cache_key)
        if cached_data is not None:
            if _city_cache_missing_height_source(cached_data):
                logger.info("Ignoring stale OSM city cache missing height_source: %s", cache_key)
            elif _city_cache_missing_building_parts(cached_data):
                logger.info("Ignoring stale OSM cache missing building:parts support: %s", cache_key)
            else:
                if _city_cache_needs_enrichment(cached_data):
                    try:
                        cached_data = await run_sync(
                            _enhance_city_data,
                            cached_data,
                            north,
                            south,
                            east,
                            west,
                        )
                        write_osm_cache(cache_key, cached_data)
                    except Exception as exc:
                        logger.warning("Cached city height enhancement failed: %s", exc)
                logger.info(f"Serving OSM data from .json.gz cache: {cache_key}")
                return JSONResponse(content=cached_data)
    else:
        OSM_CACHE_PATH.mkdir(parents=True, exist_ok=True)
        cache_file = OSM_CACHE_PATH / f"{cache_key}.json"
        if cache_file.exists():
            try:
                cached_data = json.loads(cache_file.read_text())
                if _city_cache_missing_height_source(cached_data):
                    logger.info("Ignoring stale legacy OSM city cache missing height_source: %s", cache_key)
                else:
                    if _city_cache_needs_enrichment(cached_data):
                        try:
                            cached_data = await run_sync(
                                _enhance_city_data,
                                cached_data,
                                north,
                                south,
                                east,
                                west,
                            )
                            cache_file.write_text(json.dumps(cached_data))
                        except Exception as exc:
                            logger.warning("Legacy cached city height enhancement failed: %s", exc)
                    logger.info(f"Serving OSM data from legacy cache: {cache_key}")
                    return JSONResponse(content=cached_data)
            except Exception as cache_read_err:
                logger.debug(
                    f"Legacy OSM cache read failed, re-fetching: {cache_read_err}")

    try:
        result = await run_sync(
            _fetch_osm_data, north, south, east, west, layers,
            city_req.simplify_tolerance, city_req.min_area,
            city_req.m_per_level,
        )
    except Exception as e:
        logger.error(f"OSM fetch error: {e}")
        return error_response(f"OSM fetch failed: {str(e)}")

    try:
        result = await run_sync(
            _enhance_city_data,
            result,
            north,
            south,
            east,
            west,
        )
    except Exception as exc:
        logger.warning("City height enhancement failed: %s", exc)

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
            logger.warning(f"City raster contained {bad_count} non-finite values; replacing with 0.0")
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

    # v3: roof_shapes flag added (F-ROOF1)
    _RASTER_CACHE_VERSION = "v3"
    cache_key = hashlib.md5(
        f"cityRaster|{_RASTER_CACHE_VERSION}|{req.north:.4f}_{req.south:.4f}_{req.east:.4f}_{req.west:.4f}"
        f"_dim{req.dim}_bs{req.building_scale}_rd{req.road_depression_m}_wd{req.water_depression_m}"
        f"_proj{req.projection}_cn{req.clip_nans}"
        f"_tol{req.simplify_tolerance}_a{req.min_area}_mpl{req.m_per_level:.2f}"
        f"_roof{int(bool(req.roof_shapes))}".encode()
    ).hexdigest()

    # Cache check
    if _CACHE_AVAILABLE and CACHE_ROOT is not None:
        cache_path = CACHE_ROOT / "dem" / f"{cache_key}.npz"
        if cache_path.exists():
            try:
                arr = np.load(cache_path)
                cached_result = _sanitize_raster_result({
                    "values": arr["values"].flatten().tolist(),
                    "width": int(arr["width"]),
                    "height": int(arr["height"]),
                    "vmin": float(arr["vmin"]),
                    "vmax": float(arr["vmax"]),
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
        osm_key = osm_cache_key(req.north, req.south, req.east, req.west,
                                getattr(req, "simplify_tolerance", 3.0),
                                getattr(req, "min_area", 5.0),
                                getattr(req, "m_per_level", 3.5))
        osm_data = read_osm_cache(osm_key)
        if osm_data:
            buildings = osm_data.get("buildings", buildings)
            roads = osm_data.get("roads", roads)
            waterways = osm_data.get("waterways", waterways)
            _from_cache = True
            logger.debug("City raster: resolved GeoJSON from OSM cache (%s)", osm_key[:8])

    try:
        result = await run_sync(
            _rasterize_city_data,
            req.north, req.south, req.east, req.west, req.dim,
            buildings, roads, waterways,
            req.building_scale, req.road_depression_m, req.water_depression_m,
            req.roof_shapes,
        )
    except Exception as e:
        logger.error(f"City raster error: {e}", exc_info=True)
        return error_response(str(e))

    # Apply map projection (all raster layers use the same pipeline)
    if req.projection != "none":
        from app.server.core.projection import project_grid

        grid = np.array(result["values"], dtype=np.float32).reshape(
            result["height"], result["width"])
        grid = project_grid(grid, req.north, req.south, req.east, req.west,
                            req.projection, req.clip_nans, categorical=False)
        h, w = grid.shape
        result = {
            "values": grid.flatten().tolist(),
            "width": w,
            "height": h,
            "vmin": float(np.nanmin(grid)),
            "vmax": float(np.nanmax(grid)),
            "bbox": {"north": req.north, "south": req.south,
                     "east": req.east, "west": req.west},
        }

    result = _sanitize_raster_result(result)

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
        osm_key = osm_cache_key(req.north, req.south, req.east, req.west,
                                getattr(req, "simplify_tolerance", 3.0),
                                getattr(req, "min_area", 5.0),
                                getattr(req, "m_per_level", 3.5))
        osm_data = read_osm_cache(osm_key)
        if osm_data and osm_data.get("buildings"):
            buildings = osm_data["buildings"]
            logger.debug("City export: resolved buildings from OSM cache (%s)", osm_key[:8])
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
    from app.server.core.height.providers.google_3d import _get_api_key
    return JSONResponse(content={"available": _get_api_key() is not None})


@router.post("/api/cities/enhance-heights")
async def enhance_heights(req: EnhanceHeightsRequest):
    """Enhance building heights using Google 3D photogrammetric tiles.

    Fetches a height raster from Google 3D Tiles, then samples it at
    each building centroid to replace default (10 m) heights with real
    photogrammetric measurements.
    """
    from app.server.core.height.providers.google_3d import Google3DProvider, _get_api_key
    from app.server.core.osm import enhance_buildings_with_raster
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
        osm_key = osm_cache_key(req.north, req.south, req.east, req.west,
                                getattr(req, "simplify_tolerance", 3.0),
                                getattr(req, "min_area", 5.0),
                                getattr(req, "m_per_level", 3.5))
        osm_data = read_osm_cache(osm_key)
        if osm_data and osm_data.get("buildings"):
            buildings = osm_data["buildings"]
            logger.debug("Enhance heights: resolved buildings from OSM cache (%s)", osm_key[:8])
        else:
            return error_response("Buildings not found in cache — load city data first", 400)

    bbox = (req.north, req.south, req.east, req.west)
    dim = (req.dim, req.dim)

    try:
        # Fetch terrain DEM for ground subtraction (DSM - DEM = building height)
        from app.server.core.dem import compute_raw_dem
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
