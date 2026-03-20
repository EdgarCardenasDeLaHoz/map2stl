"""
Terrain / elevation routes: DEM preview, water mask, raw DEM, merge, sources.

All heavy lifting is in core.dem and core.cache; this module is a thin
HTTP adapter that parses requests, delegates, and formats responses.
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

from config import (
    TEST_MODE,
    OPENTOPO_DATASETS,
    OPENTOPO_API_KEY as _OPENTOPO_API_KEY,
    H5_SRTM_AVAILABLE as _H5_SRTM_AVAILABLE,
    H5_SRTM_FILE as _H5_SRTM_FILE,
)
from core.cache import make_cache_key, write_array_cache, read_array_cache
from core.dem import (
    fetch_layer_data as _fetch_layer_data,
    apply_layer_processing as _apply_layer_processing,
    blend_layers as _blend_layers,
    fetch_opentopo_dem as _fetch_opentopo_dem,
    fetch_h5_dem as _fetch_h5_dem,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["terrain"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_float(params, key, default=None):
    val = params.get(key)
    if val is None or val == '':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _parse_int(params, key, default=None):
    val = params.get(key)
    if val is None or val == '':
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _parse_bool(params, key, default=False):
    val = params.get(key)
    if val is None or val == '':
        return default
    return val.lower() in ('true', '1', 'yes', 'on')


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

    north        = _parse_float(params, "north")
    south        = _parse_float(params, "south")
    east         = _parse_float(params, "east")
    west         = _parse_float(params, "west")
    dim          = _parse_int(params, "dim", 100)
    depth_scale  = _parse_float(params, "depth_scale", 0.5)
    water_scale  = _parse_float(params, "water_scale", 0.05)
    height       = _parse_float(params, "height", 10)
    base         = _parse_float(params, "base", 2)
    subtract_water      = _parse_bool(params, "subtract_water", True)
    show_sat            = _parse_bool(params, "show_sat", False)
    show_landuse        = _parse_bool(params, "show_landuse", False)
    dataset             = params.get("dataset", "esa")
    projection          = params.get("projection", "cosine")
    maintain_dimensions = _parse_bool(params, "maintain_dimensions", True)
    dem_source          = params.get("dem_source", "local")

    logger.debug(
        f"GET /api/terrain/dem north={north} south={south} east={east} "
        f"west={west} dim={dim} show_sat={show_sat}")

    # --- DEM disk cache check ---
    _dem_cache_key = None
    if north is not None and south is not None and east is not None and west is not None:
        _dem_cache_key = make_cache_key("dem", north, south, east, west, {
            "dim": dim, "src": dem_source, "proj": projection,
            "ds": depth_scale, "ws": water_scale, "h": height, "b": base,
            "sw": subtract_water, "md": maintain_dimensions
        })
        _cached = read_array_cache("dem", _dem_cache_key)
        if _cached is not None:
            _arrays, _meta = _cached
            _im = _arrays.get("dem")
            if _im is not None:
                logger.info(f"DEM cache hit: {_dem_cache_key[:8]}…")
                if dim and _im is not None:
                    _h, _w = _im.shape[:2]
                    if max(_h, _w) < dim:
                        import cv2 as _cv2_cache
                        _scale = float(dim) / float(max(_h, _w))
                        _nw = max(1, int(round(_w * _scale)))
                        _nh = max(1, int(round(_h * _scale)))
                        _im = _cv2_cache.resize(
                            _im.astype(np.float32), (_nw, _nh),
                            interpolation=_cv2_cache.INTER_LINEAR)
                _im_clean = np.nan_to_num(_im, nan=0.0)
                return JSONResponse(content={
                    "dem_values": _im_clean.ravel().tolist(),
                    "dimensions": list(_im.shape),
                    "min_elevation": float(np.nanmin(_im)),
                    "max_elevation": float(np.nanmax(_im)),
                    "mean_elevation": float(np.nanmean(_im)),
                    "bbox": [west, south, east, north],
                    "show_sat": show_sat,
                    "sat_available": False,
                    "from_cache": True,
                })

    # TEST_MODE: return deterministic gradient without network I/O
    if TEST_MODE:
        im = np.linspace(0, 100, num=(dim * dim), dtype=float).reshape((dim, dim))
        im_clean = np.nan_to_num(im, nan=0.0,
                                 posinf=np.finfo(np.float32).max,
                                 neginf=np.finfo(np.float32).min)
        h_px, w_px = im.shape
        return JSONResponse(content={
            "dem_values": im_clean.ravel().tolist(),
            "dimensions": [h_px, w_px],
            "min_elevation": float(np.nanmin(im)),
            "max_elevation": float(np.nanmax(im)),
            "mean_elevation": float(np.nanmean(im)),
            "bbox": [west or 0.0, south or 0.0, east or 0.0, north or 0.0],
            "show_sat": False,
            "sat_available": False,
        })

    try:
        original_cwd = os.getcwd()
        strm2stl_dir = Path(__file__).parent.parent.parent
        os.chdir(str(strm2stl_dir))
        sys.path.append(str(strm2stl_dir))
        from numpy2stl.numpy2stl.oceans import make_dem_image

        # Fallback for missing bbox params
        if north is None or south is None:
            south, north = -0.01, 0.01
        if east is None or west is None:
            west, east = -0.01, 0.01

        target_bbox = (north, south, east, west)

        if dem_source == "h5_local":
            if not _H5_SRTM_AVAILABLE:
                return JSONResponse(
                    content={"error": "h5_local source unavailable — strm_data.h5 not found",
                             "h5_path": str(_H5_SRTM_FILE)},
                    status_code=503)
            im = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _fetch_h5_dem(north, south, east, west, _H5_SRTM_FILE))

        elif dem_source and dem_source != "local" and dem_source in OPENTOPO_DATASETS:
            try:
                im = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: _fetch_opentopo_dem(
                        north, south, east, west,
                        demtype=dem_source, api_key=_OPENTOPO_API_KEY, dim=dim))
            except RuntimeError as ot_err:
                logger.error(f"OpenTopography fetch failed: {ot_err}")
                return JSONResponse(
                    content={"error": str(ot_err), "source": dem_source},
                    status_code=502)
        else:
            try:
                try:
                    im = make_dem_image(
                        target_bbox, dim=dim, depth_scale=depth_scale,
                        water_scale=water_scale, height=height, base=base,
                        subtract_water=subtract_water, projection=projection,
                        maintain_dimensions=maintain_dimensions)
                except TypeError:
                    im = make_dem_image(
                        target_bbox, dim=dim, depth_scale=depth_scale,
                        water_scale=water_scale, height=height, base=base,
                        subtract_water=subtract_water,
                        maintain_dimensions=maintain_dimensions)
            except Exception as dem_err:
                logger.warning(f"DEM generation failed: {dem_err}, returning zeros")
                lat_r = abs(north - south)
                lon_r = abs(east - west)
                if lat_r > lon_r:
                    mh, mw = dim, max(1, int(dim * lon_r / lat_r))
                else:
                    mw, mh = dim, max(1, int(dim * lat_r / lon_r))
                im = np.zeros((mh, mw), dtype=float)

        import cv2 as _cv2
        # Upsample if native resolution is smaller than requested dim
        if dim and im is not None:
            h_nat, w_nat = im.shape[:2]
            if max(h_nat, w_nat) < dim:
                scale = float(dim) / float(max(h_nat, w_nat))
                new_w = max(1, int(round(w_nat * scale)))
                new_h = max(1, int(round(h_nat * scale)))
                logger.info(f"Upsampling DEM {w_nat}×{h_nat} → {new_w}×{new_h}")
                im = _cv2.resize(im, (new_w, new_h), interpolation=_cv2.INTER_LINEAR)

        im_clean = np.nan_to_num(im, nan=0.0,
                                 posinf=np.finfo(np.float32).max,
                                 neginf=np.finfo(np.float32).min)
        dem_values = im_clean.ravel().tolist()
        height_px, width_px = im.shape

        # Optional satellite/land-use overlay
        sat_values = sat_width = sat_height = None
        sat_available = False
        if show_sat or show_landuse:
            try:
                from geo2stl.sat2stl import fetch_bbox_image
                sat = fetch_bbox_image(north, south, east, west, scale=30, dataset=dataset)
                if sat is not None:
                    sat_arr = np.array(sat)
                    if sat_arr.size > 0:
                        sat_tw = max(width_px, dim or width_px)
                        sat_th = max(height_px, dim or height_px)
                        sat_arr = _cv2.resize(sat_arr, (sat_tw, sat_th),
                                              interpolation=_cv2.INTER_LINEAR)
                        sat_values = sat_arr.ravel().tolist()
                        sat_height, sat_width = sat_arr.shape[:2]
                        sat_available = True
            except Exception as sat_err:
                logger.warning(f"Satellite fetch failed: {sat_err}")

        os.chdir(original_cwd)

        response_content = {
            "dem_values": dem_values,
            "dimensions": [height_px, width_px],
            "min_elevation": float(np.nanmin(im)),
            "max_elevation": float(np.nanmax(im)),
            "mean_elevation": float(np.nanmean(im)),
            "bbox": [west, south, east, north],
            "show_sat": show_sat,
            "sat_available": sat_available,
        }
        if sat_values is not None:
            response_content["sat_values"] = sat_values
            response_content["sat_dimensions"] = [sat_height, sat_width]

        # Write DEM disk cache
        if _dem_cache_key and not show_sat:
            write_array_cache(
                "dem", _dem_cache_key,
                {"dem": im_clean.reshape(height_px, width_px)},
                {"min_elevation": float(np.nanmin(im)),
                 "max_elevation": float(np.nanmax(im)),
                 "mean_elevation": float(np.nanmean(im)),
                 "bbox": [west, south, east, north],
                 "shape": [height_px, width_px]})

        return JSONResponse(content=response_content)

    except Exception as e:
        logger.error(f"Error in get_terrain_dem: {e}")
        import traceback
        return JSONResponse(content={"error": str(e), "traceback": traceback.format_exc()},
                            status_code=500)


@router.api_route("/api/terrain/dem/raw", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_dem_raw(request: Request):
    """Fetch unprocessed SRTM/GEBCO elevation data before water subtraction."""
    params = request.query_params

    north       = _parse_float(params, "north")
    south       = _parse_float(params, "south")
    east        = _parse_float(params, "east")
    west        = _parse_float(params, "west")
    dim         = _parse_int(params, "dim", 200)
    depth_scale = _parse_float(params, "depth_scale", 0.5)

    if north is None or south is None:
        south, north = -0.01, 0.01
    if east is None or west is None:
        west, east = -0.01, 0.01

    logger.debug(f"GET /api/terrain/dem/raw bbox=({north},{south},{east},{west}) dim={dim}")

    try:
        if TEST_MODE:
            im = np.linspace(-50, 150, num=(dim * dim), dtype=float).reshape((dim, dim))
            return JSONResponse(content={
                "dem_values": np.nan_to_num(im).ravel().tolist(),
                "dimensions": [dim, dim],
                "min_elevation": float(np.nanmin(im)),
                "max_elevation": float(np.nanmax(im)),
                "bbox": [west, south, east, north],
            })

        original_cwd = os.getcwd()
        strm2stl_dir = Path(__file__).parent.parent.parent
        os.chdir(str(strm2stl_dir))
        sys.path.append(str(strm2stl_dir))

        from numpy2stl.numpy2stl.oceans import stitch_tiles_no_rasterio, proj_map_geo_to_2D

        target_bbox = np.array((north, south, east, west))
        im = stitch_tiles_no_rasterio(target_bbox) * 1.0
        im[im < 0] = im[im < 0] * depth_scale
        im = proj_map_geo_to_2D(im, target_bbox)
        im = im[:, ~np.any(np.isnan(im), axis=0)]

        import cv2 as _cv2
        h, w = im.shape
        if h > w:
            new_h, new_w = dim, max(1, int(dim * w / h))
        else:
            new_w, new_h = dim, max(1, int(dim * h / w))
        im_r = _cv2.resize(im, (new_w, new_h), interpolation=_cv2.INTER_LINEAR)

        os.chdir(original_cwd)

        return JSONResponse(content={
            "dem_values": im_r.ravel().tolist(),
            "dimensions": [new_h, new_w],
            "min_elevation": float(np.nanmin(im_r)),
            "max_elevation": float(np.nanmax(im_r)),
            "mean_elevation": float(np.nanmean(im_r)),
            "ptp": float(np.ptp(im_r)),
            "bbox": [west, south, east, north],
        })

    except Exception as e:
        logger.error(f"Error in get_terrain_dem_raw: {e}")
        import traceback
        return JSONResponse(content={"error": str(e), "traceback": traceback.format_exc()},
                            status_code=500)


@router.api_route("/api/terrain/water-mask", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_water_mask(request: Request):
    """Fetch a binary water mask and ESA WorldCover land-cover data."""
    logger.info("Received request for /api/terrain/water-mask")
    try:
        params = request.query_params

        north        = _parse_float(params, "north")
        south        = _parse_float(params, "south")
        east         = _parse_float(params, "east")
        west         = _parse_float(params, "west")
        sat_scale    = _parse_int(params, "sat_scale", 500)
        dim          = _parse_int(params, "dim", 200)
        target_width  = _parse_int(params, "target_width")
        target_height = _parse_int(params, "target_height")
        water_dataset = params.get("dataset", "esa")
        if water_dataset not in ("esa", "jrc"):
            water_dataset = "esa"

        if north is None or south is None or east is None or west is None:
            return JSONResponse(
                content={"error": "Missing bbox parameters (north, south, east, west)"},
                status_code=400)

        # --- Water mask disk cache check ---
        _water_cache_key = make_cache_key("water", north, south, east, west, {
            "ss": sat_scale, "dim": dim,
            "tw": target_width, "th": target_height, "ds": water_dataset})
        _wc = read_array_cache("water", _water_cache_key)
        if _wc is not None:
            _warr, _wmeta = _wc
            _wm  = _warr.get("water_mask")
            _esa = _warr.get("esa")
            if _wm is not None and _esa is not None:
                logger.info(f"Water mask cache hit: {_water_cache_key[:8]}…")
                _h, _w = _wm.shape
                _wp = int(np.sum(_wm > 0.5))
                _tp = _h * _w
                return JSONResponse(content={
                    "water_mask_values": _wm.ravel().tolist(),
                    "water_mask_dimensions": [_h, _w],
                    "water_pixels": _wp,
                    "total_pixels": _tp,
                    "water_percentage": 100.0 * _wp / _tp if _tp else 0.0,
                    "esa_values": _esa.ravel().tolist(),
                    "esa_dimensions": [_h, _w],
                    "from_cache": True,
                })

        # Auto-scale sat_scale to avoid Earth Engine pixel limits
        import math as _math
        _bbox_w = abs(east - west)
        _bbox_h = abs(north - south)
        _mid_lat = (north + south) / 2.0
        _m_per_deg_lon = 111000.0 * _math.cos(_math.radians(_mid_lat))
        _m_per_deg_lat = 111000.0
        _est_px = (_bbox_w * _m_per_deg_lon / sat_scale) * (_bbox_h * _m_per_deg_lat / sat_scale)
        if _est_px > 5_000_000:
            sat_scale = max(sat_scale, int(sat_scale * _math.sqrt(_est_px / 5_000_000)))
            logger.info(f"Auto-scaled sat_scale to {sat_scale}")

        if TEST_MODE:
            h, w = (target_height or dim, target_width or dim)
            water_arr = np.zeros((h, w), dtype=float)
            water_arr[h // 4:h // 2, w // 4:w // 2] = 1.0
            wp = int(np.sum(water_arr))
            tp = h * w
            return JSONResponse(content={
                "water_mask_values": water_arr.ravel().tolist(),
                "water_mask_dimensions": [h, w],
                "water_pixels": wp,
                "total_pixels": tp,
                "water_percentage": 100.0 * wp / tp,
                "esa_values": water_arr.ravel().tolist(),
                "esa_dimensions": [h, w],
            })

        original_cwd = os.getcwd()
        strm2stl_dir = Path(__file__).parent.parent.parent
        os.chdir(str(strm2stl_dir))
        sys.path.append(str(strm2stl_dir))

        try:
            from geo2stl.sat2stl import fetch_bbox_image
            import cv2 as _cv2

            img = fetch_bbox_image(north, south, east, west,
                                   scale=sat_scale, dataset="esa", use_cache=True)

            _jrc_img = None
            if water_dataset == "jrc":
                try:
                    _jrc_img = fetch_bbox_image(north, south, east, west,
                                                scale=sat_scale, dataset="jrc", use_cache=True)
                except Exception as _jrc_e:
                    logger.warning(f"JRC fetch failed, falling back to ESA: {_jrc_e}")

            _elevation_raw = None
            try:
                from geo2stl.geo2stl import stitch_tiles_no_rasterio as _stitch
                _elevation_raw = _stitch((north, south, east, west))
            except Exception as _e:
                logger.warning(f"Could not fetch elevation for bathymetry check: {_e}")
        finally:
            os.chdir(original_cwd)

        if img is None:
            return JSONResponse(
                content={"error": "Failed to fetch ESA land cover data."},
                status_code=500)

        if img.ndim == 3:
            img = img[:, :, 0]
        if target_width and target_height and (img.shape[1] != target_width or img.shape[0] != target_height):
            img = _cv2.resize(img.astype(np.float32), (target_width, target_height),
                              interpolation=_cv2.INTER_NEAREST)

        h, w = img.shape

        # Build water mask from selected dataset
        if water_dataset == "jrc" and _jrc_img is not None:
            # JRC occurrence >50% = permanent water; correctly classifies coastal peninsulas
            if _jrc_img.ndim == 3:
                _jrc_img = _jrc_img[:, :, 0]
            if _jrc_img.shape != (h, w):
                _jrc_img = _cv2.resize(_jrc_img.astype(np.float32), (w, h),
                                       interpolation=_cv2.INTER_LINEAR)
            water_mask = (_jrc_img > 50).astype(float)
        else:
            water_mask = (img == 80).astype(float)

        # SRTM bathymetry augmentation — only for larger regions (> 30 km diagonal)
        # to avoid misclassifying low-lying coastal land at city scale.
        _bbox_diag_km = _math.sqrt(
            (_bbox_h * _m_per_deg_lat) ** 2 + (_bbox_w * _m_per_deg_lon) ** 2
        ) / 1000.0
        if _elevation_raw is not None and _elevation_raw.size > 0 and _bbox_diag_km > 30:
            elev_r = _cv2.resize(_elevation_raw.astype(np.float32), (w, h),
                                 interpolation=_cv2.INTER_LINEAR)
            water_mask = np.maximum(water_mask, (elev_r < -2).astype(float))

        water_pixels = int(np.sum(water_mask))
        total_pixels = h * w

        write_array_cache("water", _water_cache_key,
                          {"water_mask": water_mask.astype(np.float32),
                           "esa": img.astype(np.float32)},
                          {"shape": [h, w]})

        return JSONResponse(content={
            "water_mask_values": water_mask.ravel().tolist(),
            "water_mask_dimensions": [h, w],
            "water_pixels": water_pixels,
            "total_pixels": total_pixels,
            "water_percentage": 100.0 * water_pixels / total_pixels if total_pixels > 0 else 0.0,
            "esa_values": img.ravel().tolist(),
            "esa_dimensions": [h, w],
        })

    except ValueError as ve:
        return JSONResponse(content={"error": str(ve)}, status_code=400)
    except Exception as e:
        logger.error(f"Unhandled error in get_terrain_water_mask: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.api_route("/api/terrain/satellite", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_satellite(request: Request):
    """
    Fetch satellite or land-cover imagery for a bounding box.
    TODO: Implement as a standalone endpoint (currently handled inline in get_terrain_dem).
    """
    return JSONResponse(content={"error": "Not implemented"}, status_code=501)


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
    from schemas import MergeRequest
    try:
        body = await request.json()
        req = MergeRequest(**body)
    except Exception as e:
        return JSONResponse(content={"error": f"Invalid request: {e}"}, status_code=422)

    if not req.layers:
        return JSONResponse(content={"error": "At least one layer required"}, status_code=422)

    north = req.bbox.get("north")
    south = req.bbox.get("south")
    east  = req.bbox.get("east")
    west  = req.bbox.get("west")
    if None in (north, south, east, west):
        return JSONResponse(content={"error": "bbox must contain north/south/east/west"}, status_code=422)

    if TEST_MODE:
        h = w = req.dim
        im = np.linspace(0, 100, h * w, dtype=np.float64).reshape(h, w)
        return JSONResponse(content={
            "dem_values": im.ravel().tolist(),
            "dimensions": [h, w],
            "min_elevation": 0.0, "max_elevation": 100.0, "mean_elevation": 50.0,
            "bbox": [west, south, east, north],
            "source": "merge", "layer_count": len(req.layers),
        })

    try:
        composite = None
        loop = asyncio.get_event_loop()

        for spec in req.layers:
            raw = await loop.run_in_executor(
                None, lambda s=spec: _fetch_layer_data(
                    s.source, north, south, east, west, s.dim))
            processed = await loop.run_in_executor(
                None, lambda s=spec, r=raw: _apply_layer_processing(r, s.processing))

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
            "dem_values": composite.ravel().tolist(),
            "dimensions": [h, w],
            "min_elevation": float(np.nanmin(composite)),
            "max_elevation": float(np.nanmax(composite)),
            "mean_elevation": float(np.nanmean(composite)),
            "bbox": [west, south, east, north],
            "source": "merge", "layer_count": len(req.layers),
        })

    except Exception as e:
        logger.error(f"DEM merge failed: {e}")
        import traceback
        return JSONResponse(content={"error": str(e), "traceback": traceback.format_exc()},
                            status_code=500)


@router.get("/api/terrain/elevation-profile", tags=["terrain"])
async def get_elevation_profile(
    lat1: float = Query(..., ge=-90, le=90),
    lon1: float = Query(..., ge=-180, le=180),
    lat2: float = Query(..., ge=-90, le=90),
    lon2: float = Query(..., ge=-180, le=180),
    samples: int = Query(100, ge=2, le=1000),
):
    """Return an elevation profile along a straight transect between two points. (TODO: implement)"""
    return JSONResponse(content={"error": "Not implemented"}, status_code=501)


@router.post("/api/export/preview", tags=["terrain"])
async def export_preview(request: Request):
    """Return DEM values for a Three.js PlaneGeometry heightmap. Delegates to get_terrain_dem."""
    return await get_terrain_dem(request)
