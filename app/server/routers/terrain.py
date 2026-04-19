"""
Terrain / elevation routes: DEM preview, water mask, raw DEM, merge, sources.

All heavy lifting is in core.dem and core.cache; this module is a thin
HTTP adapter that parses requests, delegates, and formats responses.
"""

from app.server.core.hydrology import (
    fetch_natural_earth_rivers as _fetch_natural_earth_rivers,
    filter_rivers_by_bbox as _filter_rivers_by_bbox,
    rasterize_rivers_with_buffering as _rasterize_rivers_with_buffering,
    merge_rivers_with_dem as _merge_rivers_with_dem,
)
from app.server.core.hydrorivers import (
    fetch_hydrorivers as _fetch_hydrorivers,
    rasterize_hydrorivers as _rasterize_hydrorivers,
)
from app.server.core.sat import (
    fetch_water_mask as _fetch_water_mask,
    fetch_water_mask_images as _fetch_water_mask_images,
    fetch_sat_overlay as _fetch_sat_overlay,
    fetch_satellite_tiles as _fetch_satellite_tiles,
)
from app.server.core.dem import (
    fetch_layer_data as _fetch_layer_data,
    apply_layer_processing as _apply_layer_processing,
    blend_layers as _blend_layers,
    upsample_dem as _upsample_dem,
    make_dem_payload as _make_dem_payload,
    compute_raw_dem as _compute_raw_dem,
)
from app.server.core.cache import make_cache_key, write_array_cache, read_array_cache
from app.server.core.validation import (
    parse_float as _parse_float,
    parse_int as _parse_int,
    parse_bool as _parse_bool,
    b64_encode as _b64,
    validate_bbox as _validate_bbox,
    validate_dim as _validate_dim,
    run_sync,
)
from app.server.core.projection import (
    project_grid as _project_grid_impl,
    project_water_arrays as _project_water_arrays_impl,
    project_rgb_image as _project_rgb_image,
)
from app.server.core.responses import error_response
from app.server.config import (
    TEST_MODE,
    OPENTOPO_DATASETS,
    OPENTOPO_API_KEY as _OPENTOPO_API_KEY,
    H5_SRTM_AVAILABLE as _H5_SRTM_AVAILABLE,
    MAX_DIM,
)
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Request, Query
import numpy as np
import math
import os
import sys
import asyncio
import logging
from functools import partial
from pathlib import Path

# Ensure local packages (numpy2stl, geo2stl) are importable without os.chdir.
# app/server/routers → routers → server → app → strm2stl
_STRM2STL_DIR = str(Path(__file__).parent.parent.parent.parent)
if _STRM2STL_DIR not in sys.path:
    sys.path.insert(0, _STRM2STL_DIR)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["terrain"])


# ---------------------------------------------------------------------------
# Sync compute helpers (called via run_in_executor to avoid blocking the loop)
# ---------------------------------------------------------------------------


def _project_grid(arr, north, south, east, west, projection, clip_nans,
                  categorical=False):
    """Apply geo2stl projection to a 2-D array. Sync helper.

    Delegates to core.projection.project_grid — kept as a thin wrapper
    so existing call-sites in this module do not change.
    """
    return _project_grid_impl(arr, north, south, east, west, projection,
                              clip_nans, categorical=categorical)


def _project_water_arrays(water_mask, esa_img, north, south, east, west,
                          projection, clip_nans):
    """Project both water mask and ESA arrays to keep them aligned.

    Delegates to core.projection.project_water_arrays.
    """
    return _project_water_arrays_impl(water_mask, esa_img, north, south,
                                      east, west, projection, clip_nans)


def _make_local_dem(north, south, east, west, dim, depth_scale, water_scale,
                    subtract_water, projection, maintain_dimensions, clip_nans):
    """Run make_dem_image synchronously. Called from run_in_executor.

    Always fetches in Plate Carrée (projection='none') so the server can
    apply projection externally, consistent with OpenTopo/H5 sources.
    """
    from numpy2stl.oceans import make_dem_image
    target_bbox = (north, south, east, west)
    try:
        return make_dem_image(
            target_bbox, dim=dim, depth_scale=depth_scale,
            water_scale=water_scale,
            subtract_water=subtract_water, projection='none',
            maintain_dimensions=maintain_dimensions,
            clip_nans=False)
    except TypeError:
        return make_dem_image(
            target_bbox, dim=dim, depth_scale=depth_scale,
            water_scale=water_scale,
            subtract_water=subtract_water,
            maintain_dimensions=maintain_dimensions)


def _fetch_dem_array(dem_source, north, south, east, west, dim,
                     depth_scale, water_scale,
                     subtract_water, projection, maintain_dimensions,
                     clip_nans):
    """
    Fetch a DEM array from the specified source. Sync — call via run_in_executor.

    Routing:
      h5_local or any OPENTOPO_DATASETS key → _fetch_layer_data (handles h5→SRTMGL3 fallback)
      "local" or unknown                    → _make_local_dem (local SRTM tiles)
    """
    if dem_source in ("h5_local", *OPENTOPO_DATASETS):
        return _fetch_layer_data(dem_source, north, south, east, west, dim)

    try:
        return _make_local_dem(north, south, east, west, dim, depth_scale,
                               water_scale, subtract_water,
                               projection, maintain_dimensions, clip_nans)
    except Exception as dem_err:
        logger.warning(f"Local DEM failed: {dem_err}, returning zeros")
        lat_r = abs(north - south)
        lon_r = abs(east - west)
        if lat_r > lon_r:
            mh, mw = dim, max(1, int(dim * lon_r / lat_r))
        else:
            mw, mh = dim, max(1, int(dim * lat_r / lon_r))
        return np.zeros((mh, mw), dtype=float)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.api_route("/api/terrain/dem", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_dem(request: Request):
    """
    Fetch a Digital Elevation Model preview for a bounding box.
    Returns raw elevation values for client-side colormap rendering.
    """
    params = request.query_params

    north = _parse_float(params, "north")
    south = _parse_float(params, "south")
    east = _parse_float(params, "east")
    west = _parse_float(params, "west")
    dim = _parse_int(params, "dim", 100)
    depth_scale = _parse_float(params, "depth_scale", 0.5)
    water_scale = _parse_float(params, "water_scale", 0.05)
    subtract_water = _parse_bool(params, "subtract_water", True)
    show_sat = _parse_bool(params, "show_sat", False)
    dataset = params.get("dataset", "esa")
    projection = params.get("projection", "cosine")
    maintain_dimensions = _parse_bool(params, "maintain_dimensions", True)
    clip_nans = _parse_bool(params, "clip_nans", False)
    dem_source = params.get("dem_source", "local")

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    logger.debug(
        f"GET /api/terrain/dem north={north} south={south} east={east} "
        f"west={west} dim={dim} show_sat={show_sat}")

    # --- DEM disk cache check ---
    _dem_cache_key = make_cache_key("dem", north, south, east, west, {
        "dim": dim, "src": dem_source, "proj": projection,
        "ds": depth_scale, "ws": water_scale,
        "sw": subtract_water, "md": maintain_dimensions,
        "cn": clip_nans, "sat": show_sat,
    })
    _cached = read_array_cache("dem", _dem_cache_key)
    if _cached is not None and _cached[0].get("dem") is not None:
        logger.info(f"DEM cache hit: {_dem_cache_key[:8]}…")
        payload = _make_dem_payload(_cached[0]["dem"], west, south, east, north,
                                    show_sat, upscale_dim=dim)
        payload["from_cache"] = True
        return JSONResponse(content=payload)

    # TEST_MODE: return deterministic gradient without network I/O
    if TEST_MODE:
        im = np.linspace(0, 100, num=(dim * dim),
                         dtype=float).reshape((dim, dim))
        # Apply projection even in TEST_MODE so tests exercise the full pipeline
        if projection != "none":
            im = _project_grid(
                im.astype(np.float32), north or 0.0, south or 0.0,
                east or 0.0, west or 0.0,
                projection, clip_nans, categorical=False,
            )
        payload = _make_dem_payload(im, west or 0.0, south or 0.0,
                                    east or 0.0, north or 0.0, show_sat=False)
        payload["sat_available"] = False
        return JSONResponse(content=payload)

    # Guard: bbox already validated above but south/north could be None only in edge cases
    if north is None or south is None:
        south, north = -0.01, 0.01
    if east is None or west is None:
        west, east = -0.01, 0.01

    try:
        im = await run_sync(_fetch_dem_array, dem_source,
                            north, south, east, west, dim,
                            depth_scale, water_scale,
                            subtract_water, projection, maintain_dimensions,
                            clip_nans)
        im = _upsample_dem(im, dim)

        # Apply projection uniformly for ALL sources.
        # All fetch functions now return Plate Carrée data;
        # projection is applied here as a single external step.
        if projection != "none":
            im = _project_grid(
                im.astype(np.float32), north, south, east, west,
                projection, clip_nans, categorical=False,
            )

        response_content = _make_dem_payload(
            im, west, south, east, north, show_sat)
        height_px, width_px = response_content["dimensions"]

        # Optional satellite/land-use overlay
        if show_sat:
            try:
                sat_result = await run_sync(
                    _fetch_sat_overlay, north, south, east, west,
                    dataset, width_px, height_px, dim)
                if sat_result is not None:
                    sat_values, sat_width, sat_height = sat_result
                    response_content["sat_available"] = True
                    response_content["sat_values"] = sat_values
                    response_content["sat_dimensions"] = [
                        sat_height, sat_width]
            except Exception as sat_err:
                logger.warning(f"Satellite fetch failed: {sat_err}")

        # Write DEM disk cache (skip when satellite overlay is embedded)
        if not show_sat:
            im_clean = np.nan_to_num(im, nan=0.0).astype(np.float32)
            if im_clean.shape != (height_px, width_px):
                import cv2 as _cv2
                im_clean = _cv2.resize(im_clean, (width_px, height_px),
                                       interpolation=_cv2.INTER_LINEAR)
            write_array_cache(
                "dem", _dem_cache_key,
                {"dem": im_clean},
                {"min_elevation": response_content["min_elevation"],
                 "max_elevation": response_content["max_elevation"],
                 "mean_elevation": response_content["mean_elevation"],
                 "bbox": [west, south, east, north],
                 "shape": [height_px, width_px]})

        return JSONResponse(content=response_content)

    except Exception as e:
        logger.error(f"Error in get_terrain_dem: {e}", exc_info=True)
        return error_response("DEM processing failed")


@router.api_route("/api/terrain/dem/raw", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_dem_raw(request: Request):
    """Fetch unprocessed SRTM/GEBCO elevation data before water subtraction."""
    params = request.query_params

    north = _parse_float(params, "north")
    south = _parse_float(params, "south")
    east = _parse_float(params, "east")
    west = _parse_float(params, "west")
    dim = _parse_int(params, "dim", 200)
    depth_scale = _parse_float(params, "depth_scale", 0.5)

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    logger.debug(
        f"GET /api/terrain/dem/raw bbox=({north},{south},{east},{west}) dim={dim}")

    try:
        if TEST_MODE:
            im = np.linspace(-50, 150, num=(dim * dim),
                             dtype=float).reshape((dim, dim))
            return JSONResponse(content={
                "dem_values_b64": _b64(np.nan_to_num(im)),
                "dimensions": [dim, dim],
                "min_elevation": float(np.nanmin(im)),
                "max_elevation": float(np.nanmax(im)),
                "bbox": [west, south, east, north],
            })

        im_r = await run_sync(
            _compute_raw_dem, north, south, east, west, dim, depth_scale)
        new_h, new_w = im_r.shape

        return JSONResponse(content={
            "dem_values_b64": _b64(im_r),
            "dimensions": [new_h, new_w],
            "min_elevation": float(np.nanmin(im_r)),
            "max_elevation": float(np.nanmax(im_r)),
            "mean_elevation": float(np.nanmean(im_r)),
            "ptp": float(np.ptp(im_r)),
            "bbox": [west, south, east, north],
        })

    except Exception as e:
        logger.error(f"Error in get_terrain_dem_raw: {e}", exc_info=True)
        return error_response("DEM processing failed")


@router.api_route("/api/terrain/water-mask", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_water_mask(request: Request):
    """Fetch a binary water mask and ESA WorldCover land-cover data."""
    logger.info("Received request for /api/terrain/water-mask")
    try:
        params = request.query_params

        north = _parse_float(params, "north")
        south = _parse_float(params, "south")
        east = _parse_float(params, "east")
        west = _parse_float(params, "west")
        sat_scale = _parse_int(params, "sat_scale", 500)
        water_dataset = params.get("dataset", "esa")
        if water_dataset not in ("esa", "jrc"):
            water_dataset = "esa"
        projection = params.get("projection", "none")
        clip_nans = _parse_bool(params, "clip_nans", False)

        err = _validate_bbox(north, south, east, west)
        if err:
            return err

        # Scale clamping is handled inside fetch_water_mask (both 50MB request-size
        # limit and 32768px grid-dimension limit), so no pre-clamp needed here.

        # --- Water mask disk cache check ---
        _water_cache_key = make_cache_key("water", north, south, east, west, {
            "ss": sat_scale, "ds": water_dataset,
            "proj": projection, "cn": clip_nans})
        _wc = read_array_cache("water", _water_cache_key)
        if _wc is not None:
            _warr, _wmeta = _wc
            _wm = _warr.get("water_mask")
            _esa = _warr.get("esa")
            if _wm is not None and _esa is not None:
                logger.info(f"Water mask cache hit: {_water_cache_key[:8]}…")
                _h, _w = _wm.shape
                _wp = int(np.sum(_wm > 0.5))
                _tp = _h * _w
                return JSONResponse(content={
                    "water_mask_values_b64": _b64(_wm),
                    "water_mask_dimensions": [_h, _w],
                    "water_pixels": _wp,
                    "total_pixels": _tp,
                    "water_percentage": 100.0 * _wp / _tp if _tp else 0.0,
                    "esa_values_b64": _b64(_esa),
                    "esa_dimensions": [_h, _w],
                    "from_cache": True,
                })

        if TEST_MODE:
            h, w = 50, 50
            water_arr = np.zeros((h, w), dtype=float)
            water_arr[h // 4:h // 2, w // 4:w // 2] = 1.0
            esa_arr = water_arr.copy()
            # Apply projection even in TEST_MODE
            if projection != "none":
                water_arr, esa_arr = _project_water_arrays(
                    water_arr.astype(np.float32), esa_arr.astype(np.float32),
                    north, south, east, west, projection, clip_nans)
                h, w = water_arr.shape
            wp = int(np.sum(water_arr > 0.5))
            tp = h * w
            return JSONResponse(content={
                "water_mask_values_b64": _b64(water_arr),
                "water_mask_dimensions": [h, w],
                "water_pixels": wp,
                "total_pixels": tp,
                "water_percentage": 100.0 * wp / tp,
                "esa_values_b64": _b64(esa_arr),
                "esa_dimensions": [h, w],
            })

        try:
            water_mask, img, sat_scale = await run_sync(
                _fetch_water_mask, north, south, east, west,
                sat_scale, water_dataset)
        except RuntimeError as fetch_err:
            return error_response(str(fetch_err))

        h, w = water_mask.shape
        water_pixels = int(np.sum(water_mask))
        total_pixels = h * w

        # Apply projection if requested
        if projection != "none":
            water_mask, img = _project_water_arrays(
                water_mask, img, north, south, east, west, projection, clip_nans)
            h, w = water_mask.shape
            water_pixels = int(np.sum(water_mask > 0.5))
            total_pixels = h * w

        write_array_cache("water", _water_cache_key,
                          {"water_mask": water_mask.astype(np.float32),
                           "esa": img.astype(np.float32)},
                          {"shape": [h, w]})

        return JSONResponse(content={
            "water_mask_values_b64": _b64(water_mask),
            "water_mask_dimensions": [h, w],
            "water_pixels": water_pixels,
            "total_pixels": total_pixels,
            "water_percentage": 100.0 * water_pixels / total_pixels if total_pixels > 0 else 0.0,
            "esa_values_b64": _b64(img),
            "esa_dimensions": [h, w],
        })

    except ValueError as ve:
        return error_response(str(ve), 400)
    except Exception as e:
        logger.error(f"Unhandled error in get_terrain_water_mask: {e}")
        return error_response(str(e))


@router.api_route("/api/terrain/esa-land-cover", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_esa_land_cover(request: Request):
    """Fetch ESA WorldCover land-cover class data independently of the water mask."""
    logger.info("Received request for /api/terrain/esa-land-cover")
    try:
        params = request.query_params
        north = _parse_float(params, "north")
        south = _parse_float(params, "south")
        east = _parse_float(params, "east")
        west = _parse_float(params, "west")
        sat_scale = _parse_int(params, "sat_scale", 500)
        projection = params.get("projection", "none")
        clip_nans = _parse_bool(params, "clip_nans", False)

        err = _validate_bbox(north, south, east, west)
        if err:
            return err

        _esa_cache_key = make_cache_key("esa_lc", north, south, east, west, {
            "ss": sat_scale, "proj": projection, "cn": clip_nans})
        _ec = read_array_cache("esa_lc", _esa_cache_key)
        if _ec is not None:
            _earr, _emeta = _ec
            _esa = _earr.get("esa")
            if _esa is not None:
                logger.info(f"ESA land cover cache hit: {_esa_cache_key[:8]}…")
                _h, _w = _esa.shape
                return JSONResponse(content={
                    "esa_values_b64": _b64(_esa),
                    "esa_dimensions": [_h, _w],
                    "from_cache": True,
                })

        if TEST_MODE:
            h, w = 50, 50
            esa_arr = np.full((h, w), 10, dtype=np.float32)
            # Apply projection even in TEST_MODE
            if projection != "none":
                esa_arr = _project_grid(
                    esa_arr, north, south, east, west,
                    projection, clip_nans, categorical=True)
                h, w = esa_arr.shape
            return JSONResponse(content={
                "esa_values_b64": _b64(esa_arr),
                "esa_dimensions": [h, w],
            })

        # Fetch ESA image via the shared helper (dataset=esa always for land cover)
        try:
            _wm, img, sat_scale = await run_sync(
                _fetch_water_mask, north, south, east, west,
                sat_scale, "esa")
        except RuntimeError as fetch_err:
            return error_response(str(fetch_err))

        # Apply projection if requested
        if projection != "none":
            img = _project_grid(img.astype(np.float32), north, south, east, west,
                                projection, clip_nans, categorical=True)

        h, w = img.shape

        write_array_cache("esa_lc", _esa_cache_key,
                          {"esa": img.astype(np.float32)},
                          {"shape": [h, w]})

        return JSONResponse(content={
            "esa_values_b64": _b64(img),
            "esa_dimensions": [h, w],
        })

    except ValueError as ve:
        return error_response(str(ve), 400)
    except Exception as e:
        logger.error(f"Unhandled error in get_terrain_esa_land_cover: {e}")
        return error_response(str(e))


@router.get("/api/terrain/satellite", tags=["terrain"])
async def get_terrain_satellite(request: Request):
    """
    Fetch real satellite imagery (ESRI World Imagery WMTS tiles) for a bounding box.
    Returns a base64-encoded JPEG string.

    Supports map projection via ``projection`` and ``clip_nans`` query params,
    consistent with all other raster endpoints.
    """
    params = request.query_params
    north = _parse_float(params, "north")
    south = _parse_float(params, "south")
    east = _parse_float(params, "east")
    west = _parse_float(params, "west")
    dim = _parse_int(params, "dim", 400)
    projection = params.get("projection", "none")
    clip_nans = _parse_bool(params, "clip_nans", True)

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    if TEST_MODE:
        import base64
        from PIL import Image
        from io import BytesIO
        img = Image.new("RGB", (dim, dim), color=(80, 120, 60))
        # Apply projection even in TEST_MODE
        if projection != "none":
            img_arr = np.array(img)
            projected = _project_rgb_image(
                img_arr, north, south, east, west, projection, clip_nans)
            img = Image.fromarray(projected)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return JSONResponse(content={"image": b64, "bbox": [west, south, east, north]})

    try:
        b64 = await run_sync(
            _fetch_satellite_tiles, north, south, east, west, dim)

        # Apply map projection to the satellite image (channel-by-channel)
        if projection != "none":
            import base64 as _b64mod
            from io import BytesIO as _BytesIO
            from PIL import Image as _Image

            raw_bytes = _b64mod.b64decode(b64)
            img_pil = _Image.open(_BytesIO(raw_bytes)).convert("RGB")
            img_arr = np.array(img_pil)

            projected = await run_sync(
                _project_rgb_image, img_arr,
                north, south, east, west, projection, clip_nans)

            out_img = _Image.fromarray(projected)
            buf = _BytesIO()
            out_img.save(buf, format="JPEG", quality=85)
            b64 = _b64mod.b64encode(buf.getvalue()).decode()

        return JSONResponse(content={"image": b64, "bbox": [west, south, east, north]})
    except Exception as e:
        logger.error(f"Error fetching satellite tiles: {e}", exc_info=True)
        return error_response(str(e))


@router.get("/api/terrain/sources", tags=["terrain"])
async def get_terrain_sources():
    """List available DEM data sources with availability status."""
    sources = [
        {"id": "local", "label": "Local SRTM Tiles", "provider": "local",
         "resolution_m": 30, "requires_api_key": False, "available": True},
        {"id": "h5_local", "label": "Local SRTM H5 (City-scale, ~90m)",
         "provider": "local_h5", "resolution_m": 90,
         "requires_api_key": False, "available": _H5_SRTM_AVAILABLE,
         "note": "High-fidelity SRTM3 from local strm_data.h5 — best for regions < 15 km."},
    ]
    has_key = bool(_OPENTOPO_API_KEY)
    for demtype, info in OPENTOPO_DATASETS.items():
        sources.append({
            "id": demtype, "label": info["label"], "provider": "OpenTopography",
            "resolution_m": info["resolution_m"],
            "requires_api_key": True, "available": has_key,
        })
    return JSONResponse(content={
        "sources": sources,
        "opentopo_api_key_configured": has_key,
        "h5_srtm_available": _H5_SRTM_AVAILABLE,
    })


@router.post("/api/dem/merge", tags=["terrain"])
async def merge_dem_layers(request: Request):
    """
    Merge multiple elevation/mask layers into one composite DEM.
    Each layer specifies a source, resolution, per-layer processing, and a blend mode.
    """
    from app.server.schemas import MergeRequest
    try:
        body = await request.json()
        req = MergeRequest(**body)
    except Exception as e:
        return JSONResponse(content={"error": f"Invalid request: {e}"}, status_code=422)

    if not req.layers:
        return JSONResponse(content={"error": "At least one layer required"}, status_code=422)

    north = req.bbox.get("north")
    south = req.bbox.get("south")
    east = req.bbox.get("east")
    west = req.bbox.get("west")
    if None in (north, south, east, west):
        return JSONResponse(content={"error": "bbox must contain north/south/east/west"}, status_code=422)

    if TEST_MODE:
        h = w = req.dim
        im = np.linspace(0, 100, h * w, dtype=np.float64).reshape(h, w)
        return JSONResponse(content={
            "dem_values_b64": _b64(im),
            "dimensions": [h, w],
            "min_elevation": 0.0, "max_elevation": 100.0, "mean_elevation": 50.0,
            "bbox": [west, south, east, north],
            "source": "merge", "layer_count": len(req.layers),
        })

    try:
        composite = None

        for spec in req.layers:
            raw = await run_sync(
                _fetch_layer_data, spec.source, north, south, east, west, spec.dim)
            processed = await run_sync(
                _apply_layer_processing, raw, spec.processing)

            if composite is None:
                import cv2 as _cv2
                h, w = processed.shape
                if h >= w:
                    out_h, out_w = req.dim, max(1, int(req.dim * w / h))
                else:
                    out_w, out_h = req.dim, max(1, int(req.dim * h / w))
                composite = _cv2.resize(
                    processed.astype(np.float32), (out_w, out_h),
                    interpolation=_cv2.INTER_LINEAR).astype(np.float64)
            else:
                composite = _blend_layers(
                    base=composite, layer=processed,
                    blend_mode=spec.blend_mode, weight=spec.weight,
                    output_shape=composite.shape)

        if composite is None:
            return JSONResponse(content={"error": "No layers produced output"}, status_code=500)

        composite = np.nan_to_num(composite, nan=0.0,
                                  posinf=np.finfo(np.float32).max,
                                  neginf=np.finfo(np.float32).min)
        h, w = composite.shape
        return JSONResponse(content={
            "dem_values_b64": _b64(composite),
            "dimensions": [h, w],
            "min_elevation": float(np.nanmin(composite)),
            "max_elevation": float(np.nanmax(composite)),
            "mean_elevation": float(np.nanmean(composite)),
            "bbox": [west, south, east, north],
            "source": "merge", "layer_count": len(req.layers),
        })

    except Exception as e:
        logger.error(f"DEM merge failed: {e}", exc_info=True)
        return error_response("DEM merge failed")


# ---------------------------------------------------------------------------
# Hydrology endpoints
# ---------------------------------------------------------------------------

def _fetch_and_rasterize_hydrology(north, south, east, west, dim, scale_m, depression_m,
                                   source="natural_earth", min_order=3,
                                   order_exponent=1.5):
    """Fetch rivers and rasterize to grid. Sync — call via run_in_executor.

    source='natural_earth': Natural Earth dataset (global, 3 tiers, coarse)
    source='hydrorivers':   HydroRIVERS dataset (regional shapefiles, ~500 m detail,
                            downloaded on first use and cached permanently)
    """
    import time as _time
    t0 = _time.perf_counter()
    try:
        if source == "hydrorivers":
            t_fetch = _time.perf_counter()
            geojson = _fetch_hydrorivers(north, south, east, west, min_order=min_order)
            dt_fetch = _time.perf_counter() - t_fetch
            if geojson is None:
                logger.info(f"HydroRIVERS: no features in region (fetch took {dt_fetch:.2f}s)")
                return None
            n_features = len(geojson.get("features", []))
            logger.info(f"HydroRIVERS fetch: {n_features} features in {dt_fetch:.2f}s")

            t_rast = _time.perf_counter()
            river_grid = _rasterize_hydrorivers(
                geojson, north, south, east, west, dim,
                depression_base=depression_m,
                order_exponent=order_exponent,
            )
            dt_rast = _time.perf_counter() - t_rast
            dt_total = _time.perf_counter() - t0
            logger.info(f"HydroRIVERS total: {dt_total:.2f}s "
                        f"(fetch={dt_fetch:.2f}s, rasterize={dt_rast:.2f}s)")
            return {"river_grid": river_grid, "feature_count": n_features, "source": "hydrorivers"}

        # Default: Natural Earth
        geojson = _fetch_natural_earth_rivers(scale_m=scale_m)
        if geojson is None:
            logger.warning("Natural Earth hydrology fetch failed (geopandas/requests unavailable?)")
            return None
        bbox_tuple = (west, south, east, north)
        geojson_filtered = _filter_rivers_by_bbox(geojson, bbox_tuple)
        n_features = len(geojson_filtered.get("features", []))
        if n_features == 0:
            logger.info("No rivers found in region")
            return None
        river_grid = _rasterize_rivers_with_buffering(
            geojson_filtered, bbox_tuple, dim, depression_m=depression_m)
        dt_total = _time.perf_counter() - t0
        logger.info(f"Natural Earth hydrology total: {dt_total:.2f}s, {n_features} features")
        return {"river_grid": river_grid, "feature_count": n_features, "source": "natural_earth"}

    except Exception as e:
        logger.error(f"Hydrology fetch/rasterize failed: {e}", exc_info=True)
        return None


@router.get("/api/terrain/hydrology", tags=["terrain"])
async def get_terrain_hydrology(request: Request):
    """
    Fetch river hydrology and rasterize as an elevation depression grid.

    Query parameters:
        north, south, east, west: bounding box
        dim:            output grid resolution (pixels per side, default 300)
        depression_m:   max river depression in metres, negative (default -5.0)
        source:         'natural_earth' (default, global, coarse) or
                        'hydrorivers'   (HydroRIVERS ~500 m detail, downloaded on first use)

    natural_earth-only:
        scale_m:        Natural Earth dataset tier — 10, 50, or 110 (default 10 = finest)

    hydrorivers-only:
        min_order:      minimum Strahler order to include, 1–9 (default 3; 1=all streams,
                        5=major rivers only, 9=Amazon/Nile/Congo only)
        order_exponent: how steeply depression scales with order (default 1.5)
    """
    params = request.query_params

    north = _parse_float(params, "north")
    south = _parse_float(params, "south")
    east = _parse_float(params, "east")
    west = _parse_float(params, "west")
    dim = _parse_int(params, "dim", 300)
    depression_m = _parse_float(params, "depression_m", -5.0)
    source = params.get("source", "natural_earth")
    if source not in ("natural_earth", "hydrorivers"):
        source = "natural_earth"

    # natural_earth params
    scale_m = _parse_int(params, "scale_m", 10)
    if scale_m not in (10, 50, 110):
        scale_m = 10

    # hydrorivers params
    min_order = _parse_int(params, "min_order", 3)
    min_order = max(1, min(9, min_order))
    order_exponent = _parse_float(params, "order_exponent", 1.5)

    projection = params.get("projection", "none")
    clip_nans = _parse_bool(params, "clip_nans", False)

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    logger.debug(f"GET /api/terrain/hydrology bbox=({north},{south},{east},{west}) "
                 f"dim={dim} source={source} depression={depression_m}")

    if TEST_MODE:
        h, w = dim, dim
        river_arr = np.zeros((h, w), dtype=np.float32)
        river_arr[h//4:h//3, w//4:3*w//4] = depression_m
        # Apply projection even in TEST_MODE
        if projection != "none":
            river_arr = _project_grid(
                river_arr, north, south, east, west,
                projection, clip_nans, categorical=False)
            river_arr = np.nan_to_num(river_arr, nan=0.0)
            h, w = river_arr.shape
        return JSONResponse(content={
            "river_grid_values_b64": _b64(river_arr),
            "river_grid_dimensions": [h, w],
            "feature_count": 5,
            "source": source,
            "depression_m": depression_m,
        })

    try:
        result = await run_sync(
            _fetch_and_rasterize_hydrology,
            north, south, east, west, dim,
            scale_m, depression_m,
            source, min_order, order_exponent)

        if result is None:
            return JSONResponse(content={
                "river_grid_values": [],
                "river_grid_dimensions": [dim, dim],
                "feature_count": 0,
                "source": source,
                "depression_m": depression_m,
                "error": "No rivers found in region",
            }, status_code=200)

        river_grid = result["river_grid"]

        # Apply projection if requested
        if projection != "none":
            river_grid = _project_grid(
                river_grid, north, south, east, west, projection, clip_nans,
                categorical=False)
            # Replace NaN fill (from projection) with 0 (= no river) so JSON
            # serialisation produces 0.0 instead of null.
            river_grid = np.nan_to_num(river_grid, nan=0.0)

        h, w = river_grid.shape

        return JSONResponse(content={
            "river_grid_values_b64": _b64(river_grid),
            "river_grid_dimensions": [h, w],
            "feature_count": result["feature_count"],
            "source": result.get("source", source),
            "depression_m": depression_m,
        })

    except Exception as e:
        logger.error(f"Error in get_terrain_hydrology: {e}", exc_info=True)
        return error_response("Hydrology fetch failed")


def _merge_hydrology_into_dem(dem_arr, river_arr):
    """Merge river depressions with DEM. Sync — call via run_in_executor."""
    try:
        merged = _merge_rivers_with_dem(dem_arr, river_arr)
        return merged
    except Exception as e:
        logger.error(f"Hydrology merge failed: {e}", exc_info=True)
        return None


@router.post("/api/terrain/hydrology/merge", tags=["terrain"])
async def merge_terrain_hydrology(request: Request):
    """
    Merge hydrology depressions with a DEM elevation grid.

    JSON body:
        dem_values: list of float elevation values (flattened from H×W grid)
        dem_dimensions: [height, width]
        river_grid_values: list of float river depression values
        river_grid_dimensions: [height, width]
    """
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse(content={"error": f"Invalid JSON: {e}"}, status_code=400)

    # Extract and validate DEM
    dem_values = body.get("dem_values", [])
    dem_dims = body.get("dem_dimensions", [])
    if not dem_values or len(dem_dims) != 2:
        return JSONResponse(content={"error": "dem_values and dem_dimensions required"},
                            status_code=400)

    # Extract and validate river grid
    river_values = body.get("river_grid_values", [])
    river_dims = body.get("river_grid_dimensions", [])
    if not river_values or len(river_dims) != 2:
        return JSONResponse(content={"error": "river_grid_values and river_grid_dimensions required"},
                            status_code=400)

    dem_h, dem_w = dem_dims
    river_h, river_w = river_dims

    # Reshape grids
    try:
        dem_arr = np.array(dem_values, dtype=np.float32).reshape(dem_h, dem_w)
        river_arr = np.array(
            river_values, dtype=np.float32).reshape(river_h, river_w)
    except Exception as e:
        return JSONResponse(content={"error": f"Failed to reshape arrays: {e}"}, status_code=400)

    # Check dimensions match
    if dem_arr.shape != river_arr.shape:
        return JSONResponse(
            content={
                "error": f"DEM shape {dem_arr.shape} != river shape {river_arr.shape}"},
            status_code=400)

    if TEST_MODE:
        # In test mode, just return DEM unchanged
        return JSONResponse(content={
            "merged_dem_values": dem_arr.ravel().tolist(),
            "merged_dimensions": [dem_h, dem_w],
        })

    try:
        merged = await run_sync(_merge_hydrology_into_dem, dem_arr, river_arr)

        if merged is None:
            return JSONResponse(content={"error": "Merge operation failed"}, status_code=500)

        return JSONResponse(content={
            "merged_dem_values": merged.ravel().tolist(),
            "merged_dimensions": [merged.shape[0], merged.shape[1]],
        })

    except Exception as e:
        logger.error(f"Error in merge_terrain_hydrology: {e}", exc_info=True)
        return error_response("Hydrology merge failed")


@router.post("/api/export/preview", tags=["terrain"])
async def export_preview(request: Request):
    """Return DEM values for a Three.js PlaneGeometry heightmap. Delegates to get_terrain_dem."""
    return await get_terrain_dem(request)
