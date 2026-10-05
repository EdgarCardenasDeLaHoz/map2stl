"""
routers/cities.py — /api/cities/* endpoints.

Extracted from location_picker.py (backend refactor, step 6).
Delegates OSM fetching and caching to core/city_data.py.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.server.core import city_fetch_tasks
from app.server.core.cache import CACHE_ROOT, osm_cache_key
from app.server.core.city_data import get_city_layers, read_city_layers_any_tolerance
from app.server.core.responses import error_response
from app.server.core.validation import run_sync, validate_bbox_diagonal
from app.server.schemas import (
    CityRasterRequest,
    CityRequest,
    EnhanceHeightsRequest,
    LandmarkPreviewRequest,
    LandmarksRequest,
)
from city2stl.rasterize import rasterize_city_data as _rasterize_city_data

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cities"])


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
    cached = (CACHE_ROOT / "osm" / f"{key}.json.gz").exists()
    return JSONResponse(content={"cached": cached, "cache_key": key})


def _resolve_city_request(city_req: CityRequest):
    """Apply the detail tier and the size guard.

    Returns ``(layers, min_area, diag_km, None)`` or ``(None, None, None, error_response)``.
    """
    from app.server.config import COARSE_MIN_BUILDING_AREA_M2, MAX_BBOX_DIAGONAL_KM_COARSE

    north, south, east, west = city_req.north, city_req.south, city_req.east, city_req.west
    layers = city_req.layers or ["buildings", "roads", "waterways"]
    min_area = city_req.min_area

    is_coarse = city_req.detail == "coarse"
    if is_coarse:
        # Coarse tier: infrastructure + large buildings only, no wall detail.
        layers = [lyr for lyr in layers if lyr != "walls"] or ["buildings", "roads", "waterways"]
        min_area = max(min_area, COARSE_MIN_BUILDING_AREA_M2)

    # Server-side size guard (coarse tier gets a larger cap)
    if is_coarse:
        diag_km, diag_err = validate_bbox_diagonal(
            north, south, east, west, max_km=MAX_BBOX_DIAGONAL_KM_COARSE)
    else:
        diag_km, diag_err = validate_bbox_diagonal(north, south, east, west)
    if diag_err:
        return None, None, None, diag_err
    # detail is folded into the cache key (via min_area, which differs
    # between tiers) so full/coarse results never collide.
    return layers, min_area, diag_km, None


@router.post("/api/cities")
async def get_city_data(city_req: CityRequest):
    """
    Fetch OSM building, road, waterway, and POI data for a small bounding box.
    Results are cached as .json.gz. Region must be ≤ 15 km diagonal (≤ 25 km
    for ``detail="coarse"`` requests, which drop walls/small buildings).

    Blocks until every layer is in. The browser client uses the background
    variant (``/api/cities/start`` + ``/status`` + ``/result``); this one stays
    for the SDK.
    """
    layers, min_area, diag_km, err = _resolve_city_request(city_req)
    if err:
        return err
    north, south, east, west = city_req.north, city_req.south, city_req.east, city_req.west
    try:
        result = await run_sync(get_city_layers, north, south, east, west, layers,
                                city_req.simplify_tolerance, min_area)
    except Exception as e:
        logger.error(f"OSM fetch error: {e}")
        return error_response(f"OSM fetch failed: {str(e)}")
    result["diagonal_km"] = round(diag_km, 2)
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Background city fetch (per-layer progress, mirror, cancel)
# ---------------------------------------------------------------------------

@router.post("/api/cities/start")
async def start_city_fetch(city_req: CityRequest):
    """Start the ``POST /api/cities`` fetch in the background.

    Same body and size guard. Returns the status shape of
    ``GET /api/cities/status/{task_id}``; an identical fetch still running is joined.
    """
    layers, min_area, diag_km, err = _resolve_city_request(city_req)
    if err:
        return err
    task = city_fetch_tasks.start_city_fetch(
        city_req.north, city_req.south, city_req.east, city_req.west,
        layers, city_req.simplify_tolerance, min_area, diagonal_km=diag_km)
    return JSONResponse(content=task.snapshot())


@router.get("/api/cities/status/{task_id}")
async def city_fetch_status(task_id: str):
    """``{task_id, status: running|done|error|cancelled, layers: [{name, state}],
    mirror, message, error, elapsed_s, diagonal_km}``; layer state is one of
    pending, fetching, done, failed, cached, cancelled."""
    task = city_fetch_tasks.get_task(task_id)
    if task is None:
        return error_response("Unknown or expired city fetch task", 404)
    return JSONResponse(content=task.snapshot())


@router.get("/api/cities/result/{task_id}")
async def city_fetch_result(task_id: str):
    """The finished fetch's payload — the same body ``POST /api/cities`` returns."""
    task = city_fetch_tasks.get_task(task_id)
    if task is None:
        return error_response("Unknown or expired city fetch task", 404)
    if task.status != "done" or task.result is None:
        return error_response(f"City fetch is {task.status}", 409)
    return JSONResponse(content=task.result)


@router.post("/api/cities/cancel/{task_id}")
async def cancel_city_fetch(task_id: str):
    """Cancel a running fetch; returns its status. Layers already being fetched
    finish in the background and are discarded."""
    task = city_fetch_tasks.cancel_task(task_id)
    if task is None:
        return error_response("Unknown or expired city fetch task", 404)
    return JSONResponse(content=task.snapshot())


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

    def _sanitize_raster_result(payload: dict[str, Any]) -> dict[str, Any]:
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

    clip_valid_region = bool(req.clip_valid_region)

    # Note: Cache key does NOT include projection or clip_valid_region.
    # Raw city raster is cached once per bbox; projection/clipping applied on fetch.
    cache_key = hashlib.md5(
        f"cityRaster|{req.north:.4f}_{req.south:.4f}_{req.east:.4f}_{req.west:.4f}"
        f"_dim{req.dim}_bs{req.building_scale}_rd{req.road_depression_m}_wd{req.water_depression_m}".encode()
    ).hexdigest()

    # Cache check
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
                    maintain_dimensions=req.maintain_dimensions,
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
            and not waterways.get("features")):
        osm_data, osm_key = read_city_layers_any_tolerance(
            req.north, req.south, req.east, req.west)
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
                            req.projection, clip_valid_region, categorical=False,
                            maintain_dimensions=req.maintain_dimensions)
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


# ---------------------------------------------------------------------------
# Google 3D height enhancement
# ---------------------------------------------------------------------------

@router.get("/api/cities/google3d-available")
async def google3d_available():
    """Check if Google 3D Tiles API key is configured."""
    from city2stl.height.providers.google_3d import get_api_key
    return JSONResponse(content={"available": get_api_key() is not None})


@router.post("/api/cities/enhance-heights")
async def enhance_heights(req: EnhanceHeightsRequest):
    """Enhance building heights using Google 3D photogrammetric tiles.

    Fetches a height raster from Google 3D Tiles, then samples it at
    each building centroid to replace default (10 m) heights with real
    photogrammetric measurements.
    """
    import numpy as np

    from city2stl.height.providers.google_3d import Google3DProvider, get_api_key
    from city2stl.heights import enhance_buildings_with_raster

    if not get_api_key():
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
    if (not buildings or not buildings.get("features")):
        osm_data, osm_key = read_city_layers_any_tolerance(
            req.north, req.south, req.east, req.west)
        if osm_data and osm_data.get("buildings"):
            buildings = osm_data["buildings"]
            logger.debug(
                "Enhance heights: resolved buildings from OSM cache (%s)", osm_key[:8])
        else:
            return error_response("Buildings not found in cache — load city data first", 400)

    bbox = (req.north, req.south, req.east, req.west)
    dim = (req.dim, req.dim)

    try:
        # No external DEM: the provider takes the ground from the mesh itself.
        # The orthometric DEM this used to pass is tens of metres off the
        # tiles' ellipsoidal heights (the provider's own docstring warns of
        # the geoid separation) (audit 2026-10-05).
        provider = Google3DProvider()
        height_result = await run_sync(provider.fetch_heights, bbox, dim)

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


# ---------------------------------------------------------------------------
# Landmarks (F-LANDMARK §3-§5): list, survey sources, preview
# ---------------------------------------------------------------------------

@router.post("/api/cities/landmarks")
async def list_city_landmarks(req: LandmarksRequest):
    """Notable buildings in the posted buildings layer (``city2stl.landmarks.list_landmarks``),
    the nDSM survey sources covering them, and the region's stored overrides."""
    from app.server.core import landmarks as lm_core
    from city2stl.height.providers.survey import available_for_bbox
    from city2stl.landmarks import list_landmarks

    feats = (req.buildings or {}).get("features") or []
    items = await run_sync(list_landmarks, feats, req.tallest_n)
    sources = []
    if items:
        n = max(i["bbox"]["north"] for i in items)
        s = min(i["bbox"]["south"] for i in items)
        e = max(i["bbox"]["east"] for i in items)
        w = min(i["bbox"]["west"] for i in items)
        sources = available_for_bbox((n, s, e, w))
    overrides = lm_core.load_region_overrides(req.region) if req.region else {}
    return JSONResponse(content={"landmarks": items, "survey_sources": sources,
                                 "overrides": overrides,
                                 "ids_missing": bool(feats) and not any(
                                     (f.get("properties") or {}).get("osm_id") for f in feats)})


@router.get("/api/cities/survey-sources")
async def survey_sources(north: float, south: float, east: float, west: float):
    """Surveyed nDSM providers and whether each covers the bbox (no network)."""
    from city2stl.height.providers.survey import available_for_bbox
    return JSONResponse(content={"sources": available_for_bbox((north, south, east, west))})


@router.post("/api/cities/landmarks/preview")
async def preview_city_landmark(req: LandmarkPreviewRequest):
    """One landmark alone as the City Model would build it with ``override`` applied:
    ``{vertices, faces, size_mm, mm_per_m, report}`` (flat lists for three.js)."""
    from app.server.core import landmarks as lm_core
    from city2stl.landmarks import LandmarkError

    try:
        out = await run_sync(lm_core.preview, req.buildings, req.osm_id, req.override)
    except LandmarkError as exc:
        return error_response(str(exc), 400)
    except Exception as exc:
        logger.error("Landmark preview failed: %s", exc, exc_info=True)
        return error_response(f"Landmark preview failed: {exc}")
    return JSONResponse(content=out)
