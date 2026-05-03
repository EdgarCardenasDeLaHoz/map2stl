"""
Terrain / elevation routes: DEM preview, water mask, raw DEM, merge, sources.

All heavy lifting is in core.dem and core.cache; this module is a thin
HTTP adapter that parses requests, delegates, and formats responses.
"""

import logging
from typing import Optional

import numpy as np
from cachetools import LRUCache
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from geo2stl.dem import (
    fetch_dem_from_source as _fetch_dem_from_source,
    fetch_local_dem as _fetch_local_dem,
    make_dem_payload as _make_dem_payload,
    upsample_dem as _upsample_dem,
)
from geo2stl.hydrology import HYDROLOGY_LAYER
from geo2stl.projections import (
    project_grid as _geo_project_grid,
    project_rgb_image,
    project_water_arrays as project_water_layers,
)
from geo2stl.sat2stl import (
    fetch_sat_overlay as _fetch_sat_overlay,
    fetch_satellite_tiles as _fetch_satellite_tiles,
    fetch_water_mask as _fetch_water_mask,
    fetch_water_mask_images as _fetch_water_mask_images,
)

from app.server.config import (
    H5_SRTM_AVAILABLE as _H5_SRTM_AVAILABLE,
    MAX_DIM,
    OPENTOPO_API_KEY as _OPENTOPO_API_KEY,
    OPENTOPO_DATASETS,
    TEST_MODE,
)
from geo2stl.raster import clamp_esa_scale, derive_sat_scale

from app.server.core.cache import make_cache_key, read_array_cache, write_array_cache
from app.server.core.responses import error_response
from app.server.core.validation import (
    BboxQueryParams,
    b64_encode as _b64,
    parse_bbox_query as _parse_bbox_query,
    run_sync,
    validate_bbox as _validate_bbox,
    validate_dim as _validate_dim,
)


def project_scalar_grid(arr, north, south, east, west, projection, clip_nans):
    return _geo_project_grid(arr, north, south, east, west, projection, clip_nans, categorical=False)


def project_categorical_grid(arr, north, south, east, west, projection, clip_nans):
    return _geo_project_grid(arr, north, south, east, west, projection, clip_nans, categorical=True)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["terrain"])

_fetch_and_rasterize_hydrology = HYDROLOGY_LAYER.fetch_and_rasterize

# In-memory LRU cache for fully-serialised DEM response dicts.
# Sits in front of the disk cache so that repeated hot requests skip both
# the .npz read and the base64 encode step.
_dem_mem_cache: LRUCache = LRUCache(maxsize=30)


def _project_grid(arr, north, south, east, west, projection, clip_nans,
                  categorical=False):
    """Compatibility wrapper kept for tests and local call sites."""
    if categorical:
        return project_categorical_grid(arr, north, south, east, west,
                                        projection, clip_nans)
    return project_scalar_grid(arr, north, south, east, west,
                               projection, clip_nans)


def _project_water_arrays(water_mask, esa_img, north, south, east, west,
                          projection, clip_nans):
    """Compatibility wrapper kept for tests and local call sites."""
    return project_water_layers(water_mask, esa_img, north, south,
                                east, west, projection, clip_nans)


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.api_route("/api/terrain/dem", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_dem(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: Optional[int] = Query(None, description="Output grid resolution (pixels per side)"),
    depth_scale: Optional[float] = Query(None, description="Depth scaling factor for ocean/bathymetry"),
    water_scale: Optional[float] = Query(None, description="Water subtraction strength"),
    subtract_water: Optional[bool] = Query(None, description="Subtract water bodies from terrain"),
    show_sat: Optional[bool] = Query(None, description="Include ESA land-use overlay in response"),
    dataset: Optional[str] = Query(None, description="Land-use dataset: 'esa' or 'jrc'"),
    projection: Optional[str] = Query(None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    maintain_dimensions: Optional[bool] = Query(None, description="Maintain output dimensions after projection"),
    clip_nans: Optional[bool] = Query(None, description="Clip NaN-only border rows/cols from projected output"),
    dem_source: Optional[str] = Query(None, description="DEM source: 'local', 'h5_local', or OpenTopography key"),
):
    """
    Fetch a Digital Elevation Model preview for a bounding box.
    Returns raw elevation values for client-side colormap rendering.
    """
    north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    dim = dim if dim is not None else 600
    depth_scale = depth_scale if depth_scale is not None else 0.5
    water_scale = water_scale if water_scale is not None else 0.05
    subtract_water = subtract_water if subtract_water is not None else True
    show_sat = show_sat if show_sat is not None else False
    dataset = dataset or "esa"
    projection = projection or "cosine"
    maintain_dimensions = maintain_dimensions if maintain_dimensions is not None else True
    clip_nans = clip_nans if clip_nans is not None else False
    dem_source = dem_source or "local"

    logger.debug(
        f"GET /api/terrain/dem north={north} south={south} east={east} "
        f"west={west} dim={dim} show_sat={show_sat}")

    # --- DEM in-memory cache check (fastest path) ---
    _dem_cache_key = make_cache_key("dem", north, south, east, west, {
        "dim": dim, "src": dem_source, "proj": projection,
        "ds": depth_scale, "ws": water_scale,
        "sw": subtract_water, "md": maintain_dimensions,
        "cn": clip_nans, "sat": show_sat,
    })
    if _dem_cache_key in _dem_mem_cache:
        logger.info(f"DEM mem-cache hit: {_dem_cache_key[:8]}...")
        return JSONResponse(content=_dem_mem_cache[_dem_cache_key])

    # --- DEM disk cache check ---
    _disk_cached = read_array_cache("dem", _dem_cache_key)
    if _disk_cached is not None and _disk_cached[0].get("dem") is not None:
        logger.info(f"DEM disk-cache hit: {_dem_cache_key[:8]}...")
        payload = _make_dem_payload(_disk_cached[0]["dem"], west, south, east, north,
                                    show_sat, upscale_dim=dim)
        payload["from_cache"] = True
        # Promote to in-memory cache
        _dem_mem_cache[_dem_cache_key] = payload
        return JSONResponse(content=payload)

    # TEST_MODE: return deterministic gradient without network I/O
    if TEST_MODE:
        im = np.linspace(0, 100, num=(dim * dim),
                         dtype=float).reshape((dim, dim))
        # Apply projection even in TEST_MODE so tests exercise the full pipeline
        if projection != "none":
            im = project_scalar_grid(
                im.astype(np.float32), north or 0.0, south or 0.0,
                east or 0.0, west or 0.0, projection, clip_nans,
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
        im = await run_sync(_fetch_dem_from_source, dem_source,
                            north, south, east, west, dim,
                            depth_scale=depth_scale, water_scale=water_scale,
                            subtract_water=subtract_water,
                            maintain_dimensions=maintain_dimensions)
        im = _upsample_dem(im, dim)

        # Apply projection uniformly for ALL sources.
        # All fetch functions now return Plate CarrÃ©e data;
        # projection is applied here as a single external step.
        if projection != "none":
            im = project_scalar_grid(
                im.astype(np.float32), north, south, east, west,
                projection, clip_nans,
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
                        sat_arr = project_categorical_grid(
                            sat_arr, north, south, east, west,
                            projection, clip_nans)
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

        # Promote to in-memory cache for subsequent requests in this server session
        _dem_mem_cache[_dem_cache_key] = response_content

        return JSONResponse(content=response_content)

    except Exception as e:
        logger.error(f"Error in get_terrain_dem: {e}", exc_info=True)
        return error_response("DEM processing failed")


@router.api_route("/api/terrain/water-mask", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_water_mask(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: Optional[int] = Query(None, description="Output grid resolution (pixels per side)"),
    dataset: Optional[str] = Query(None, description="Water dataset: 'esa' or 'jrc'"),
    projection: Optional[str] = Query(None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_nans: Optional[bool] = Query(None, description="Clip NaN-only border rows/cols from projected output"),
):
    """Fetch a binary water mask and ESA WorldCover land-cover data."""
    logger.info("Received request for /api/terrain/water-mask")
    try:
        north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
        water_dataset = dataset or "esa"
        if water_dataset not in ("esa", "jrc"):
            water_dataset = "esa"
        projection = projection or "none"
        clip_nans = clip_nans if clip_nans is not None else False

        err = _validate_bbox(north, south, east, west)
        if err:
            return err

        dim = dim if dim is not None else 600

        # Derive sat_scale (m/px) from requested dim and bbox size.
        # Scale clamping (50 MB / 32768 px limits) is handled inside fetch_water_mask.
        sat_scale = derive_sat_scale(north or 0.0, south or 0.0, east or 0.0, west or 0.0, dim)

        # --- Water mask disk cache check ---
        _water_cache_key = make_cache_key("water", north, south, east, west, {
            "dim": dim, "ds": water_dataset,
            "proj": projection, "cn": clip_nans})
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
                water_arr, esa_arr = project_water_layers(
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
            water_mask, img = project_water_layers(
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
    dim: Optional[int] = Query(None, description="Output grid resolution (pixels per side)"),
    projection: Optional[str] = Query(None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_nans: Optional[bool] = Query(None, description="Clip NaN-only border rows/cols from projected output"),
):
    """Fetch ESA WorldCover land-cover class data independently of the water mask."""
    logger.info("Received request for /api/terrain/esa-land-cover")
    try:
        north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
        projection = projection or "none"
        clip_nans = clip_nans if clip_nans is not None else False

        err = _validate_bbox(north, south, east, west)
        if err:
            return err

        dim = dim if dim is not None else 600

        # Derive sat_scale from requested dim and bbox size.
        sat_scale = derive_sat_scale(north or 0.0, south or 0.0, east or 0.0, west or 0.0, dim)

        _esa_cache_key = make_cache_key("esa_lc", north, south, east, west, {
            "dim": dim, "proj": projection, "cn": clip_nans})
        _ec = read_array_cache("esa_lc", _esa_cache_key)
        if _ec is not None:
            _earr, _emeta = _ec
            _esa = _earr.get("esa")
            if _esa is not None:
                logger.info(f"ESA land cover cache hit: {_esa_cache_key[:8]}...")
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
                esa_arr = project_categorical_grid(
                    esa_arr, north, south, east, west, projection, clip_nans)
                h, w = esa_arr.shape
            return JSONResponse(content={
                "esa_values_b64": _b64(esa_arr),
                "esa_dimensions": [h, w],
                "resolution_m": sat_scale,
            })

        # Fetch ESA image directly â€” skip the water mask pipeline
        # (fetch_water_mask would also download SRTM tiles for bathymetry,
        # build a water mask, and apply JRC logic â€” all discarded here).
        # Apply the same scale-clamping guards as fetch_water_mask.
        sat_scale = clamp_esa_scale(north, south, east, west, sat_scale)

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
            img = project_categorical_grid(
                img.astype(np.float32), north, south, east, west,
                projection, clip_nans)

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
    dim: Optional[int] = Query(None, description="Output image resolution (pixels per side)"),
    projection: Optional[str] = Query(None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_nans: Optional[bool] = Query(None, description="Clip NaN-only border rows/cols from projected output"),
):
    """
    Fetch real satellite imagery (ESRI World Imagery WMTS tiles) for a bounding box.
    Returns a base64-encoded JPEG string.

    Supports map projection via ``projection`` and ``clip_nans`` query params,
    consistent with all other raster endpoints.
    """
    north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
    projection = projection or "none"
    clip_nans = clip_nans if clip_nans is not None else True

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    dim = dim if dim is not None else 600

    if TEST_MODE:
        import base64
        from PIL import Image
        from io import BytesIO
        img = Image.new("RGB", (dim, dim), color=(80, 120, 60))
        # Apply projection even in TEST_MODE
        if projection != "none":
            img_arr = np.array(img)
            projected = project_rgb_image(
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
                project_rgb_image, img_arr,
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
         "note": "High-fidelity SRTM3 from local strm_data.h5 â€” best for regions < 15 km."},
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
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: Optional[int] = Query(None, description="Output grid resolution (pixels per side)"),
    depression_m: Optional[float] = Query(None, description="Max river depression in metres (negative, default -5.0)"),
    source: Optional[str] = Query(None, description="River data source: 'natural_earth' or 'hydrorivers'"),
    scale_m: Optional[int] = Query(None, description="Natural Earth dataset tier: 10 (finest), 50, or 110"),
    min_order: Optional[int] = Query(None, description="HydroRIVERS minimum Strahler order 1-9 (1=all, 9=major only)"),
    order_exponent: Optional[float] = Query(None, description="HydroRIVERS depression scaling exponent"),
    projection: Optional[str] = Query(None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_nans: Optional[bool] = Query(None, description="Clip NaN-only border rows/cols from projected output"),
):
    """
    Fetch river hydrology and rasterize as an elevation depression grid.

    Query parameters:
        north, south, east, west: bounding box
        dim:            output grid resolution (pixels per side, default 300)
        depression_m:   max river depression in metres, negative (default -5.0)
        source:         'natural_earth' (default, global, coarse) or
                        'hydrorivers'   (HydroRIVERS ~500 m detail, downloaded on first use)

    natural_earth-only:
        scale_m:        Natural Earth dataset tier â€” 10, 50, or 110 (default 10 = finest)

    hydrorivers-only:
        min_order:      minimum Strahler order to include, 1â€“9 (default 3; 1=all streams,
                        5=major rivers only, 9=Amazon/Nile/Congo only)
        order_exponent: how steeply depression scales with order (default 1.5)
    """
    north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
    source = source or "natural_earth"
    if source not in ("natural_earth", "hydrorivers"):
        source = "natural_earth"
    scale_m = scale_m if scale_m is not None else 10
    if scale_m not in (10, 50, 110):
        scale_m = 10
    min_order = min_order if min_order is not None else 3
    min_order = max(1, min(9, min_order))
    order_exponent = order_exponent if order_exponent is not None else 1.5
    projection = projection or "none"
    clip_nans = clip_nans if clip_nans is not None else False
    depression_m = depression_m if depression_m is not None else -5.0

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    dim = dim if dim is not None else 300

    logger.debug(f"GET /api/terrain/hydrology bbox=({north},{south},{east},{west}) "
                 f"dim={dim} source={source} depression={depression_m}")

    if TEST_MODE:
        h, w = dim, dim
        river_arr = np.zeros((h, w), dtype=np.float32)
        river_arr[h//4:h//3, w//4:3*w//4] = depression_m
        # Apply projection even in TEST_MODE
        if projection != "none":
            river_arr = project_scalar_grid(
                river_arr, north, south, east, west,
                projection, clip_nans)
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
            river_grid = project_scalar_grid(
                river_grid, north, south, east, west, projection, clip_nans)
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




