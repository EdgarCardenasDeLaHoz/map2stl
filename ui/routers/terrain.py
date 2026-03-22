"""
Terrain / elevation routes: DEM preview, water mask, raw DEM, merge, sources.

All heavy lifting is in core.dem and core.cache; this module is a thin
HTTP adapter that parses requests, delegates, and formats responses.
"""

import os
import sys
import asyncio
import logging
from functools import partial
from pathlib import Path

# Ensure local packages (numpy2stl, geo2stl) are importable without os.chdir.
_STRM2STL_DIR = str(Path(__file__).parent.parent.parent)
if _STRM2STL_DIR not in sys.path:
    sys.path.insert(0, _STRM2STL_DIR)

import numpy as np
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

from config import (
    TEST_MODE,
    OPENTOPO_DATASETS,
    OPENTOPO_API_KEY as _OPENTOPO_API_KEY,
    H5_SRTM_AVAILABLE as _H5_SRTM_AVAILABLE,
)
from core.cache import make_cache_key, write_array_cache, read_array_cache
from core.dem import (
    fetch_layer_data as _fetch_layer_data,
    apply_layer_processing as _apply_layer_processing,
    blend_layers as _blend_layers,
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


def _validate_bbox(north, south, east, west):
    """Return a JSONResponse error if bbox is missing or incoherent, else None."""
    if any(v is None for v in (north, south, east, west)):
        return JSONResponse(content={"error": "north, south, east, west are all required"}, status_code=400)
    if north <= south:
        return JSONResponse(content={"error": "north must be greater than south"}, status_code=400)
    if east <= west:
        return JSONResponse(content={"error": "east must be greater than west"}, status_code=400)
    return None


def _validate_dim(dim, max_dim=2000):
    """Return a JSONResponse error if dim is out of range, else None."""
    if dim is not None and not (1 <= dim <= max_dim):
        return JSONResponse(content={"error": f"dim must be between 1 and {max_dim}"}, status_code=400)
    return None


# ---------------------------------------------------------------------------
# Sync compute helpers (called via run_in_executor to avoid blocking the loop)
# ---------------------------------------------------------------------------

def _make_local_dem(north, south, east, west, dim, depth_scale, water_scale,
                    height, base, subtract_water, projection, maintain_dimensions):
    """Run make_dem_image synchronously. Called from run_in_executor."""
    from numpy2stl.oceans import make_dem_image
    target_bbox = (north, south, east, west)
    try:
        return make_dem_image(
            target_bbox, dim=dim, depth_scale=depth_scale,
            water_scale=water_scale, height=height, base=base,
            subtract_water=subtract_water, projection=projection,
            maintain_dimensions=maintain_dimensions)
    except TypeError:
        return make_dem_image(
            target_bbox, dim=dim, depth_scale=depth_scale,
            water_scale=water_scale, height=height, base=base,
            subtract_water=subtract_water,
            maintain_dimensions=maintain_dimensions)


def _fetch_sat_overlay(north, south, east, west, dataset, width_px, height_px, dim):
    """Fetch + resize satellite overlay. Returns (values_list, w, h) or None."""
    import cv2 as _cv2
    import numpy as _np
    from geo2stl.sat2stl import fetch_bbox_image
    sat = fetch_bbox_image(north, south, east, west, scale=30, dataset=dataset)
    if sat is None:
        return None
    sat_arr = _np.array(sat)
    if sat_arr.size == 0:
        return None
    sat_tw = max(width_px, dim or width_px)
    sat_th = max(height_px, dim or height_px)
    sat_arr = _cv2.resize(sat_arr, (sat_tw, sat_th), interpolation=_cv2.INTER_LINEAR)
    return (sat_arr.ravel().tolist(), sat_arr.shape[1], sat_arr.shape[0])


def _compute_raw_dem(north, south, east, west, dim, depth_scale):
    """Compute raw (unprocessed) DEM array. Called from run_in_executor."""
    import cv2 as _cv2
    import numpy as _np
    from numpy2stl.oceans import stitch_tiles_no_rasterio, proj_map_geo_to_2D
    target_bbox = _np.array((north, south, east, west))
    im = stitch_tiles_no_rasterio(target_bbox) * 1.0
    im[im < 0] = im[im < 0] * depth_scale
    im = proj_map_geo_to_2D(im, target_bbox)
    im = im[:, ~_np.any(_np.isnan(im), axis=0)]
    h, w = im.shape
    if h > w:
        new_h, new_w = dim, max(1, int(dim * w / h))
    else:
        new_w, new_h = dim, max(1, int(dim * h / w))
    im_r = _cv2.resize(im, (new_w, new_h), interpolation=_cv2.INTER_LINEAR)
    return im_r


def _fetch_water_mask_images(north, south, east, west, sat_scale, water_dataset,
                              target_width, target_height):
    """Fetch ESA/JRC images and optional elevation for bathymetry. Called from run_in_executor.
    Returns (img, jrc_img_or_None, elevation_raw_or_None).
    """
    import cv2 as _cv2
    from geo2stl.sat2stl import fetch_bbox_image
    img = fetch_bbox_image(north, south, east, west,
                           scale=sat_scale, dataset="esa", use_cache=True)
    jrc_img = None
    if water_dataset == "jrc":
        try:
            jrc_img = fetch_bbox_image(north, south, east, west,
                                       scale=sat_scale, dataset="jrc", use_cache=True)
        except Exception:
            pass
    elevation_raw = None
    try:
        from geo2stl.geo2stl import stitch_tiles_no_rasterio as _stitch
        elevation_raw = _stitch((north, south, east, west))
    except Exception:
        pass
    # Resize img to target dims if needed
    if img is not None and img.ndim == 3:
        img = img[:, :, 0]
    if img is not None and target_width and target_height and \
            (img.shape[1] != target_width or img.shape[0] != target_height):
        img = _cv2.resize(img.astype(float), (target_width, target_height),
                          interpolation=_cv2.INTER_NEAREST)
    return img, jrc_img, elevation_raw


def _fetch_dem_array(dem_source, north, south, east, west, dim,
                     depth_scale, water_scale, height, base,
                     subtract_water, projection, maintain_dimensions):
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
                               water_scale, height, base, subtract_water,
                               projection, maintain_dimensions)
    except Exception as dem_err:
        logger.warning(f"Local DEM failed: {dem_err}, returning zeros")
        lat_r = abs(north - south)
        lon_r = abs(east - west)
        if lat_r > lon_r:
            mh, mw = dim, max(1, int(dim * lon_r / lat_r))
        else:
            mw, mh = dim, max(1, int(dim * lat_r / lon_r))
        return np.zeros((mh, mw), dtype=float)


def _upsample_dem(im: np.ndarray, dim: int) -> np.ndarray:
    """Upsample DEM if its native resolution is smaller than dim."""
    import cv2 as _cv2
    if dim and im is not None:
        h_nat, w_nat = im.shape[:2]
        if max(h_nat, w_nat) < dim:
            scale = float(dim) / float(max(h_nat, w_nat))
            new_w = max(1, int(round(w_nat * scale)))
            new_h = max(1, int(round(h_nat * scale)))
            logger.info(f"Upsampling DEM {w_nat}×{h_nat} → {new_w}×{new_h}")
            im = _cv2.resize(im, (new_w, new_h), interpolation=_cv2.INTER_LINEAR)
    return im


def _make_dem_payload(im: np.ndarray, west, south, east, north,
                      show_sat: bool, upscale_dim: int = None) -> dict:
    """
    Build the standard DEM response dict from a numpy array.
    Optionally upsamples to upscale_dim before serialising (used for cache hits).
    """
    import cv2 as _cv2
    if upscale_dim:
        im = _upsample_dem(im, upscale_dim)
    im_clean = np.nan_to_num(im, nan=0.0,
                              posinf=np.finfo(np.float32).max,
                              neginf=np.finfo(np.float32).min)
    h_px, w_px = im_clean.shape
    return {
        "dem_values":     im_clean.ravel().tolist(),
        "dimensions":     [h_px, w_px],
        "min_elevation":  float(np.nanmin(im)),
        "max_elevation":  float(np.nanmax(im)),
        "mean_elevation": float(np.nanmean(im)),
        "bbox":           [west, south, east, north],
        "show_sat":       show_sat,
        "sat_available":  False,
    }


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

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    logger.debug(
        f"GET /api/terrain/dem north={north} south={south} east={east} "
        f"west={west} dim={dim} show_sat={show_sat}")

    # --- DEM disk cache check ---
    _dem_cache_key = make_cache_key("dem", north, south, east, west, {
        "dim": dim, "src": dem_source, "proj": projection,
        "ds": depth_scale, "ws": water_scale, "h": height, "b": base,
        "sw": subtract_water, "md": maintain_dimensions,
        "sat": show_sat, "lu": show_landuse,
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
        im = np.linspace(0, 100, num=(dim * dim), dtype=float).reshape((dim, dim))
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
        loop = asyncio.get_running_loop()

        im = await loop.run_in_executor(
            None, partial(_fetch_dem_array, dem_source,
                          north, south, east, west, dim,
                          depth_scale, water_scale, height, base,
                          subtract_water, projection, maintain_dimensions))
        im = _upsample_dem(im, dim)

        response_content = _make_dem_payload(im, west, south, east, north, show_sat)
        height_px, width_px = response_content["dimensions"]

        # Optional satellite/land-use overlay
        if show_sat or show_landuse:
            try:
                sat_result = await loop.run_in_executor(
                    None, partial(_fetch_sat_overlay, north, south, east, west,
                                  dataset, width_px, height_px, dim))
                if sat_result is not None:
                    sat_values, sat_width, sat_height = sat_result
                    response_content["sat_available"] = True
                    response_content["sat_values"] = sat_values
                    response_content["sat_dimensions"] = [sat_height, sat_width]
            except Exception as sat_err:
                logger.warning(f"Satellite fetch failed: {sat_err}")

        # Write DEM disk cache (skip when satellite overlay is embedded)
        if not show_sat:
            im_clean = np.array(response_content["dem_values"], dtype=np.float32).reshape(
                height_px, width_px)
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
        return JSONResponse(content={"error": "DEM processing failed"}, status_code=500)


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

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

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

        im_r = await asyncio.get_running_loop().run_in_executor(
            None, partial(_compute_raw_dem, north, south, east, west, dim, depth_scale))
        new_h, new_w = im_r.shape

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
        logger.error(f"Error in get_terrain_dem_raw: {e}", exc_info=True)
        return JSONResponse(content={"error": "DEM processing failed"}, status_code=500)


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

        err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
        if err:
            return err

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

        import cv2 as _cv2
        img, _jrc_img, _elevation_raw = await asyncio.get_running_loop().run_in_executor(
            None, partial(_fetch_water_mask_images, north, south, east, west,
                          sat_scale, water_dataset, target_width, target_height))

        if img is None:
            return JSONResponse(
                content={"error": "Failed to fetch ESA land cover data."},
                status_code=500)

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
        loop = asyncio.get_running_loop()

        for spec in req.layers:
            raw = await loop.run_in_executor(
                None, partial(_fetch_layer_data, spec.source, north, south, east, west, spec.dim))
            processed = await loop.run_in_executor(
                None, partial(_apply_layer_processing, raw, spec.processing))

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
        logger.error(f"DEM merge failed: {e}", exc_info=True)
        return JSONResponse(content={"error": "DEM merge failed"}, status_code=500)



@router.post("/api/export/preview", tags=["terrain"])
async def export_preview(request: Request):
    """Return DEM values for a Three.js PlaneGeometry heightmap. Delegates to get_terrain_dem."""
    return await get_terrain_dem(request)
