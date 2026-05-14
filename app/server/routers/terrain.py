"""
Terrain / elevation routes: DEM preview, water mask, raw DEM, merge, sources.

All heavy lifting is in core.dem and core.cache; this module is a thin
HTTP adapter that parses requests, delegates, and formats responses.
"""

import asyncio
from geo2stl.hydrology import (
    fetch_and_rasterize_hydrology as _fetch_and_rasterize_hydrology,
)
from geo2stl.sat2stl import (
    fetch_water_mask as _fetch_water_mask,
    fetch_water_mask_images as _fetch_water_mask_images,
    fetch_sat_overlay as _fetch_sat_overlay,
    fetch_satellite_tiles as _fetch_satellite_tiles,
)
from geo2stl.dem import (
    fetch_layer_data as _fetch_layer_data,
    fetch_local_dem as _fetch_local_dem,
    make_dem_payload as _make_dem_payload,
)
from geo2stl.processing import (
    upsample_dem as _upsample_dem,
)
from app.server.core.cache import make_cache_key, write_array_cache, read_array_cache
from app.server.core.validation import (
    BboxQueryParams,
    parse_float as _parse_float,
    parse_int as _parse_int,
    parse_bool as _parse_bool,
    parse_bbox_query as _parse_bbox_query,
    b64_encode as _b64,
    validate_bbox as _validate_bbox,
    validate_dim as _validate_dim,
    run_sync,
)
from geo2stl.projections import (
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
from typing import Optional
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Request, Query, Depends
import numpy as np
import math
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["terrain"])

# In-flight request dedupe: maps cache_key -> asyncio.Future of the JSONResponse
# payload dict. When a duplicate request arrives while one is running, it awaits
# the same Future instead of starting a fresh pipeline. Cleared on completion.
_HYDRO_INFLIGHT: dict[str, "asyncio.Future"] = {}


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


def _parse_clip_valid_region(params, default: bool = True) -> bool:
    """Parse projection-edge clipping flag with backward compatibility.

    Preferred query key is ``clip_valid_region``.
    Legacy ``clip_nans`` remains accepted for existing clients.
    """
    if "clip_valid_region" in params:
        return _parse_bool(params, "clip_valid_region", default)
    if "clip_nans" in params:
        return _parse_bool(params, "clip_nans", default)
    return default


def _make_local_dem(north, south, east, west, dim, depth_scale, water_scale,
                    subtract_water, projection, maintain_dimensions, clip_nans):
    """Run fetch_local_dem synchronously. Called from run_in_executor.

    Always fetches in Plate Carree (projection='none') so the server can
    apply projection externally, consistent with OpenTopo/H5 sources.
    """
    return _fetch_local_dem(
        north, south, east, west, dim,
        depth_scale=depth_scale,
        water_scale=water_scale,
        subtract_water=subtract_water,
        maintain_dimensions=maintain_dimensions,
    )


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
async def get_terrain_dem(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: Optional[int] = Query(
        None, description="Output grid resolution (pixels per side)"),
    depth_scale: Optional[float] = Query(
        None, description="Depth scaling factor for ocean/bathymetry"),
    water_scale: Optional[float] = Query(
        None, description="Water subtraction strength"),
    subtract_water: Optional[bool] = Query(
        None, description="Subtract water bodies from terrain"),
    show_sat: Optional[bool] = Query(
        None, description="Include ESA land-use overlay in response"),
    dataset: Optional[str] = Query(
        None, description="Land-use dataset: 'esa' or 'jrc'"),
    projection: Optional[str] = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    maintain_dimensions: Optional[bool] = Query(
        None, description="Maintain output dimensions after projection"),
    clip_valid_region: Optional[bool] = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    clip_nans: Optional[bool] = Query(
        None,
        description="Deprecated alias for clip_valid_region.",
        deprecated=True,
    ),
    dem_source: Optional[str] = Query(
        None, description="DEM source: 'local', 'h5_local', or OpenTopography key"),
):
    """
    Fetch a Digital Elevation Model preview for a bounding box.
    Returns raw elevation values for client-side colormap rendering.
    """
    params = request.query_params

    north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
    dim = _parse_int(params, "dim", 600)
    depth_scale = _parse_float(params, "depth_scale", 0.5)
    water_scale = _parse_float(params, "water_scale", 0.05)
    subtract_water = _parse_bool(params, "subtract_water", True)
    show_sat = _parse_bool(params, "show_sat", False)
    dataset = params.get("dataset", "esa")
    projection = params.get("projection", "cosine")
    maintain_dimensions = _parse_bool(params, "maintain_dimensions", True)
    clip_valid_region = _parse_clip_valid_region(params, default=True)
    dem_source = params.get("dem_source", "local")

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    logger.debug(
        f"GET /api/terrain/dem north={north} south={south} east={east} "
        f"west={west} dim={dim} show_sat={show_sat}")

    # --- DEM disk cache check ---
    # Note: Cache key does NOT include projection or clip_valid_region.
    # Raw data is cached once per bbox; projection/clipping applied on fetch.
    _dem_cache_key = make_cache_key("dem", north, south, east, west, {
        "dim": dim, "src": dem_source,
        "ds": depth_scale, "ws": water_scale,
        "sw": subtract_water, "md": maintain_dimensions,
        "sat": show_sat,
    })
    _cached = read_array_cache("dem", _dem_cache_key)
    if _cached is not None and _cached[0].get("dem") is not None:
        logger.info(f"DEM cache hit: {_dem_cache_key[:8]}...")
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
                projection, clip_valid_region, categorical=False,
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
                            clip_valid_region)
        im = _upsample_dem(im, dim)

        # Apply projection uniformly for ALL sources.
        # All fetch functions now return Plate Carrée data;
        # projection is applied here as a single external step.
        if projection != "none":
            im = _project_grid(
                im.astype(np.float32), north, south, east, west,
                projection, clip_valid_region, categorical=False,
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
                    # Project the ESA overlay to align with the projected DEM geometry
                    if projection != "none":
                        sat_arr = np.array(sat_values, dtype=np.float32).reshape(
                            sat_height, sat_width)
                        sat_arr = _project_grid(
                            sat_arr, north, south, east, west,
                            projection, clip_valid_region, categorical=True)
                        sat_height, sat_width = sat_arr.shape
                        sat_values = sat_arr.ravel().tolist()
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


@router.api_route("/api/terrain/water-mask", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_water_mask(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: Optional[int] = Query(
        None, description="Output grid resolution (pixels per side)"),
    dataset: Optional[str] = Query(
        None, description="Water dataset: 'esa' or 'jrc'"),
    projection: Optional[str] = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_valid_region: Optional[bool] = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    clip_nans: Optional[bool] = Query(
        None,
        description="Deprecated alias for clip_valid_region.",
        deprecated=True,
    ),
):
    """Fetch a binary water mask and ESA WorldCover land-cover data."""
    logger.info("Received request for /api/terrain/water-mask")
    try:
        params = request.query_params

        north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
        dim = _parse_int(params, "dim", 600)
        water_dataset = params.get("dataset", "esa")
        if water_dataset not in ("esa", "jrc"):
            water_dataset = "esa"
        projection = params.get("projection", "none")
        clip_valid_region = _parse_clip_valid_region(params, default=True)

        err = _validate_bbox(north, south, east, west)
        if err:
            return err

        # Derive sat_scale (m/px) from requested dim and bbox size.
        # Scale clamping (50 MB / 32768 px limits) is handled inside fetch_water_mask.
        mid_lat = ((north or 0.0) + (south or 0.0)) / 2.0
        _m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
        _bbox_w_m = abs((east or 0.0) - (west or 0.0)) * _m_per_deg_lon
        _bbox_h_m = abs((north or 0.0) - (south or 0.0)) * 111_320.0
        _longer_m = max(_bbox_w_m, _bbox_h_m, 1.0)
        sat_scale = max(10, int(math.ceil(_longer_m / dim)))

        # --- Water mask disk cache check ---
        # Note: Cache key does NOT include projection or clip_valid_region.
        # Raw data is cached once per bbox; projection/clipping applied on fetch.
        _water_cache_key = make_cache_key("water", north, south, east, west, {
            "dim": dim, "ds": water_dataset})
        _wc = read_array_cache("water", _water_cache_key)
        if _wc is not None:
            _warr, _wmeta = _wc
            _wm = _warr.get("water_mask")
            _esa = _warr.get("esa")
            if _wm is not None and _esa is not None:
                logger.info(f"Water mask cache hit: {_water_cache_key[:8]}...")
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
                    north, south, east, west, projection, clip_valid_region)
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
                "resolution_m": sat_scale,
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
                water_mask, img, north, south, east, west, projection, clip_valid_region)
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
            "resolution_m": sat_scale,
        })

    except ValueError as ve:
        return error_response(str(ve), 400)
    except Exception as e:
        logger.error(f"Unhandled error in get_terrain_water_mask: {e}")
        return error_response(str(e))


@router.api_route("/api/terrain/esa-land-cover", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_esa_land_cover(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: Optional[int] = Query(
        None, description="Output grid resolution (pixels per side)"),
    projection: Optional[str] = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_valid_region: Optional[bool] = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    clip_nans: Optional[bool] = Query(
        None,
        description="Deprecated alias for clip_valid_region.",
        deprecated=True,
    ),
):
    """Fetch ESA WorldCover land-cover class data independently of the water mask."""
    logger.info("Received request for /api/terrain/esa-land-cover")
    try:
        params = request.query_params
        north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
        dim = _parse_int(params, "dim", 600)
        projection = params.get("projection", "none")
        clip_valid_region = _parse_clip_valid_region(params, default=True)

        err = _validate_bbox(north, south, east, west)
        if err:
            return err

        # Derive sat_scale from requested dim and bbox size.
        mid_lat = ((north or 0.0) + (south or 0.0)) / 2.0
        _m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
        _bbox_w_m = abs((east or 0.0) - (west or 0.0)) * _m_per_deg_lon
        _bbox_h_m = abs((north or 0.0) - (south or 0.0)) * 111_320.0
        _longer_m = max(_bbox_w_m, _bbox_h_m, 1.0)
        sat_scale = max(10, int(math.ceil(_longer_m / dim)))

        # Note: Cache key does NOT include projection or clip_valid_region.
        # Raw data is cached once per bbox; projection/clipping applied on fetch.
        _esa_cache_key = make_cache_key("esa_lc", north, south, east, west, {
            "dim": dim})
        _ec = read_array_cache("esa_lc", _esa_cache_key)
        if _ec is not None:
            _earr, _emeta = _ec
            _esa = _earr.get("esa")
            if _esa is not None:
                logger.info(
                    f"ESA land cover cache hit: {_esa_cache_key[:8]}...")
                _h, _w = _esa.shape
                return JSONResponse(content={
                    "esa_values_b64": _b64(_esa),
                    "esa_dimensions": [_h, _w],
                    "resolution_m": sat_scale,
                    "from_cache": True,
                })

        if TEST_MODE:
            h, w = 50, 50
            esa_arr = np.full((h, w), 10, dtype=np.float32)
            # Apply projection even in TEST_MODE
            if projection != "none":
                esa_arr = _project_grid(
                    esa_arr, north, south, east, west,
                    projection, clip_valid_region, categorical=True)
                h, w = esa_arr.shape
            return JSONResponse(content={
                "esa_values_b64": _b64(esa_arr),
                "esa_dimensions": [h, w],
                "resolution_m": sat_scale,
            })

        # Fetch ESA image directly — skip the water mask pipeline
        # (fetch_water_mask would also download SRTM tiles for bathymetry,
        # build a water mask, and apply JRC logic — all discarded here).
        # Apply the same scale-clamping guards as fetch_water_mask.
        bbox_w = abs(east - west)
        bbox_h = abs(north - south)
        mid_lat = (north + south) / 2.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
        bbox_w_m = bbox_w * m_per_deg_lon
        bbox_h_m = bbox_h * 111_320.0
        _MAX_ESA_PX = 50_331_648 // 2
        est_px = (bbox_w_m / sat_scale) * (bbox_h_m / sat_scale)
        if est_px > _MAX_ESA_PX:
            sat_scale = max(sat_scale, int(
                math.ceil(math.sqrt(bbox_w_m * bbox_h_m / _MAX_ESA_PX))))
        min_safe_dim = max(
            int(math.ceil(bbox_w_m / 32768)),
            int(math.ceil(bbox_h_m / 32768)), 1)
        if sat_scale < min_safe_dim:
            sat_scale = min_safe_dim

        try:
            img, _jrc, _elev = await run_sync(
                _fetch_water_mask_images, north, south, east, west,
                sat_scale, "esa")
        except RuntimeError as fetch_err:
            return error_response(str(fetch_err))

        if img is None:
            return error_response("Failed to fetch ESA land cover data")

        # Apply projection if requested
        if projection != "none":
            img = _project_grid(img.astype(np.float32), north, south, east, west,
                                projection, clip_valid_region, categorical=True)

        h, w = img.shape

        write_array_cache("esa_lc", _esa_cache_key,
                          {"esa": img.astype(np.float32)},
                          {"shape": [h, w]})

        return JSONResponse(content={
            "esa_values_b64": _b64(img),
            "esa_dimensions": [h, w],
            "resolution_m": sat_scale,
        })

    except ValueError as ve:
        return error_response(str(ve), 400)
    except Exception as e:
        logger.error(f"Unhandled error in get_terrain_esa_land_cover: {e}")
        return error_response(str(e))


@router.get("/api/terrain/satellite", tags=["terrain"])
async def get_terrain_satellite(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: Optional[int] = Query(
        None, description="Output image resolution (pixels per side)"),
    projection: Optional[str] = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_valid_region: Optional[bool] = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    clip_nans: Optional[bool] = Query(
        None,
        description="Deprecated alias for clip_valid_region.",
        deprecated=True,
    ),
):
    """
    Fetch real satellite imagery (ESRI World Imagery WMTS tiles) for a bounding box.
    Returns a base64-encoded JPEG string.

    Supports map projection via ``projection`` and ``clip_valid_region`` query params,
    consistent with all other raster endpoints.
    """
    params = request.query_params
    north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
    dim = _parse_int(params, "dim", 600)
    projection = params.get("projection", "none")
    clip_valid_region = _parse_clip_valid_region(params, default=True)

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
                img_arr, north, south, east, west, projection, clip_valid_region)
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
                north, south, east, west, projection, clip_valid_region)

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


# ---------------------------------------------------------------------------
# Hydrology endpoints
# ---------------------------------------------------------------------------

@router.get("/api/terrain/hydrology", tags=["terrain"])
async def get_terrain_hydrology(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: Optional[int] = Query(
        None, description="Output grid resolution (pixels per side)"),
    depression_m: Optional[float] = Query(
        None, description="Max river depression in metres (negative, default -5.0)"),
    source: Optional[str] = Query(
        None, description="River data source: 'natural_earth' or 'hydrorivers'"),
    scale_m: Optional[int] = Query(
        None, description="Natural Earth dataset tier: 10 (finest), 50, or 110"),
    min_order: Optional[int] = Query(
        None, description="HydroRIVERS minimum Strahler order 1-9 (1=all, 9=major only)"),
    order_exponent: Optional[float] = Query(
        None, description="HydroRIVERS depression scaling exponent"),
    width_factor: Optional[float] = Query(
        None, description="HydroRIVERS line width multiplier (default 1.0; higher = thicker rivers)"),
    projection: Optional[str] = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_valid_region: Optional[bool] = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    clip_nans: Optional[bool] = Query(
        None,
        description="Deprecated alias for clip_valid_region.",
        deprecated=True,
    ),
):
    """
    Fetch river hydrology and rasterize as an elevation depression grid.

    Query parameters:
        north, south, east, west: bounding box
        dim:            output grid resolution (pixels per side, default 600)
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

    north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
    dim = _parse_int(params, "dim", 600)
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
    width_factor = _parse_float(params, "width_factor", 1.0)
    width_factor = max(0.1, min(20.0, width_factor))

    projection = params.get("projection", "none")
    clip_valid_region = _parse_clip_valid_region(params, default=True)

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    logger.debug(f"GET /api/terrain/hydrology bbox=({north},{south},{east},{west}) "
                 f"dim={dim} source={source} depression={depression_m}")

    # Cache key covers every parameter that affects the rasterized output.
    # Note: Cache key does NOT include projection or clip_valid_region.
    # Raw data is cached once per bbox; projection/clipping applied on fetch.
    cache_extra = {
        "dim": dim, "src": source, "dep": depression_m,
        "scl": scale_m, "mo": min_order, "oe": order_exponent,
        "wf": width_factor,
    }
    cache_key = make_cache_key(
        "hydrology", north, south, east, west, cache_extra)
    cached = read_array_cache("hydrology", cache_key)
    if cached is not None:
        arrs, meta = cached
        river_grid = arrs.get("river_grid")
        if river_grid is not None:
            h, w = river_grid.shape
            logger.info("Hydrology cache hit: %s (%dx%d)", cache_key[:8], w, h)
            return JSONResponse(content={
                "river_grid_values_b64": _b64(river_grid),
                "river_grid_dimensions": [h, w],
                "feature_count": int(meta.get("feature_count", 0)),
                "source": meta.get("source", source),
                "depression_m": depression_m,
            })

    # In-flight dedupe: if an identical request is already running, await its result
    # instead of starting a duplicate ~150-second pipeline.
    inflight = _HYDRO_INFLIGHT.get(cache_key)
    if inflight is not None:
        logger.info("Hydrology in-flight join: %s", cache_key[:8])
        payload = await inflight
        return JSONResponse(content=payload)

    if TEST_MODE:
        h, w = dim, dim
        river_arr = np.zeros((h, w), dtype=np.float32)
        river_arr[h//4:h//3, w//4:3*w//4] = depression_m
        # Apply projection even in TEST_MODE
        if projection != "none":
            river_arr = _project_grid(
                river_arr, north, south, east, west,
                projection, clip_valid_region, categorical=False)
            river_arr = np.nan_to_num(river_arr, nan=0.0)
            h, w = river_arr.shape
        return JSONResponse(content={
            "river_grid_values_b64": _b64(river_arr),
            "river_grid_dimensions": [h, w],
            "feature_count": 5,
            "source": source,
            "depression_m": depression_m,
        })

    # No cache, no in-flight: register a Future so concurrent duplicates can join.
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _HYDRO_INFLIGHT[cache_key] = fut

    try:
        result = await run_sync(
            _fetch_and_rasterize_hydrology,
            north, south, east, west, dim,
            scale_m, depression_m,
            source, min_order, order_exponent, width_factor)

        if result is None:
            payload = {
                "river_grid_values": [],
                "river_grid_dimensions": [dim, dim],
                "feature_count": 0,
                "source": source,
                "depression_m": depression_m,
                "error": "No rivers found in region",
            }
            if not fut.done():
                fut.set_result(payload)
            return JSONResponse(content=payload, status_code=200)

        river_grid = result["river_grid"]

        # Apply projection if requested
        if projection != "none":
            river_grid = _project_grid(
                river_grid, north, south, east, west, projection, clip_valid_region,
                categorical=False)
            # Replace NaN fill (from projection) with 0 (= no river) so JSON
            # serialisation produces 0.0 instead of null.
            river_grid = np.nan_to_num(river_grid, nan=0.0)

        h, w = river_grid.shape

        write_array_cache(
            "hydrology", cache_key,
            {"river_grid": river_grid},
            {"feature_count": int(result["feature_count"]),
             "source": result.get("source", source)},
        )

        payload = {
            "river_grid_values_b64": _b64(river_grid),
            "river_grid_dimensions": [h, w],
            "feature_count": result["feature_count"],
            "source": result.get("source", source),
            "depression_m": depression_m,
        }
        if not fut.done():
            fut.set_result(payload)
        return JSONResponse(content=payload)

    except Exception as e:
        logger.error(f"Error in get_terrain_hydrology: {e}", exc_info=True)
        if not fut.done():
            fut.set_exception(e)
        return error_response("Hydrology fetch failed")
    finally:
        # Always clear the in-flight slot so retries can start a fresh pipeline.
        _HYDRO_INFLIGHT.pop(cache_key, None)
