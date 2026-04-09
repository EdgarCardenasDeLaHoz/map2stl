"""
core/dem.py — DEM fetch and layer-blend helpers.

Extracted from location_picker.py (backend refactor, step 3).
These are pure (non-HTTP) functions that can be imported and called
from route handlers or tested independently.
"""

from __future__ import annotations

import logging
import math
import os
import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.schemas import ProcessingSpec

logger = logging.getLogger(__name__)

from app.config import (
    OPENTOPO_CACHE_PATH,
    OPENTOPO_API_KEY as _OPENTOPO_API_KEY,
    OPENTOPO_DATASETS,
    H5_SRTM_FILE as _H5_SRTM_FILE,
    H5_SRTM_AVAILABLE as _H5_SRTM_AVAILABLE,
)

# strm2stl root dir (app/core/dem.py → app/core → app → strm2stl)
_STRM2STL_DIR = Path(__file__).parent.parent.parent
# Ensure local packages (numpy2stl, geo2stl) are importable without os.chdir.
if str(_STRM2STL_DIR) not in sys.path:
    sys.path.insert(0, str(_STRM2STL_DIR))


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def fetch_layer_data(
    source: str,
    north: float, south: float, east: float, west: float,
    dim: int,
) -> np.ndarray:
    """
    Fetch a 2-D float64 numpy array for one merge layer.

    Sources:
      "local"           – local SRTM elevation tiles (metres)
      "water_esa"       – ESA WorldCover water mask  (0/1 float)
      Any key in OPENTOPO_DATASETS – OpenTopography elevation (metres)
    """
    if source == "water_esa":
        return fetch_esa_water_layer(north, south, east, west, dim)
    elif source == "h5_local":
        try:
            return fetch_h5_dem(north, south, east, west)
        except FileNotFoundError as exc:
            logger.warning(
                "h5_local DEM unavailable (%s); falling back to SRTMGL3 via OpenTopography", exc
            )
            return fetch_opentopo_dem(north, south, east, west,
                                      demtype="SRTMGL3",
                                      api_key=_OPENTOPO_API_KEY,
                                      dim=dim)
    elif source in OPENTOPO_DATASETS:
        return fetch_opentopo_dem(north, south, east, west,
                                  demtype=source,
                                  api_key=_OPENTOPO_API_KEY,
                                  dim=dim)
    else:  # "local" or unknown → local SRTM
        return fetch_local_dem(north, south, east, west, dim)


def fetch_local_dem(
    north: float, south: float, east: float, west: float, dim: int
) -> np.ndarray:
    """Fetch local SRTM elevation tiles and return as a float64 array."""
    from numpy2stl.oceans import make_dem_image
    target_bbox = (north, south, east, west)
    im = make_dem_image(target_bbox, dim=dim,
                        subtract_water=False,
                        projection="none",
                        maintain_dimensions=True)
    return np.nan_to_num(im, nan=0.0).astype(np.float64)


# ---------------------------------------------------------------------------
# H5 tile constants (used by fetch_h5_dem and _geo_to_tile_pixel)
# ---------------------------------------------------------------------------

_H5_TILE_PX: int = 6000   # pixels per tile side
_H5_TILE_DEG: float = 5.0  # degrees per tile


def _geo_to_tile_pixel(lat: float, lon: float):
    """Return (tile_x, tile_y, pix_x, pix_y) for a geographic coordinate.

    Tile index convention: srtm_{tilX:02d}_{tilY:02d}
      tilX = floor(lon / 5) + 37  (1-indexed, 36 tiles wide)
      tilY = floor(-lat / 5) + 13 (1-indexed, northward from equator)
    """
    tx = int(math.floor(lon / _H5_TILE_DEG)) + 36 + 1
    ty = int(math.floor(-lat / _H5_TILE_DEG)) + 12 + 1
    px = (lon / _H5_TILE_DEG - math.floor(lon / _H5_TILE_DEG)) * _H5_TILE_PX
    py = (-lat / _H5_TILE_DEG - math.floor(-lat / _H5_TILE_DEG)) * _H5_TILE_PX
    return tx, ty, px, py


def fetch_h5_dem(
    north: float, south: float, east: float, west: float,
    h5_file: Optional[Path] = None,
) -> np.ndarray:
    """
    Read elevation from the local SRTM HDF5 tile store (strm_data.h5).

    The h5 file stores SRTM3 tiles at 6000×6000 px per 5° tile (~90m/px).
    Returns a float64 array cropped to the requested bbox at native resolution.
    The caller is responsible for upsampling to the desired display resolution.

    Tile naming convention: srtm_{tilX:02d}_{tilY:02d}
      tilX = floor(lon / 5) + 37     (1-indexed, 36 tiles wide)
      tilY = floor(-lat / 5) + 13    (1-indexed, northward from equator)
    Each tile is 6000×6000 pixels covering 5° × 5°.

    Future: if h5 file is absent, fall back to OpenTopography SRTMGL3 API
    (same 90m data, global) or Google Earth Engine SRTM/NASADEM (30m).
    """
    from itertools import product as _product

    if h5_file is None:
        h5_file = _H5_SRTM_FILE
    if not h5_file or not Path(h5_file).exists():
        raise FileNotFoundError(f"SRTM h5 file not found: {h5_file}")

    try:
        import h5py
    except ImportError as exc:
        raise ImportError("h5py is required for h5_local DEM source: pip install h5py") from exc

    tx1, ty1, px1, py1 = _geo_to_tile_pixel(north, west)
    tx2, ty2, px2, py2 = _geo_to_tile_pixel(south, east)

    # Pixel extents span possibly multiple tiles
    px2_abs = (tx2 - tx1) * _H5_TILE_PX + px2
    py2_abs = (ty2 - ty1) * _H5_TILE_PX + py2

    x1i, y1i = int(round(px1)), int(round(py1))
    x2i, y2i = int(round(px2_abs)), int(round(py2_abs))
    span_x = (tx2 - tx1 + 1)
    span_y = (ty2 - ty1 + 1)
    mosaic_h = span_y * _H5_TILE_PX
    mosaic_w = span_x * _H5_TILE_PX
    mosaic = np.zeros((mosaic_h, mosaic_w), dtype=np.int16)

    tiles_found = 0
    with h5py.File(str(h5_file), "r") as fh:
        for ix, iy in _product(range(span_x), range(span_y)):
            key = f"srtm_{tx1 + ix:02d}_{ty1 + iy:02d}"
            if key not in fh:
                logger.debug(f"h5 tile missing: {key}")
                continue
            tiles_found += 1
            data = fh[key][:]
            th, tw = data.shape[:2]
            out_r = iy * _H5_TILE_PX
            out_c = ix * _H5_TILE_PX
            mosaic[out_r:out_r + min(th, _H5_TILE_PX),
                   out_c:out_c + min(tw, _H5_TILE_PX)] = data[:_H5_TILE_PX, :_H5_TILE_PX]

    if tiles_found == 0:
        raise FileNotFoundError(
            f"h5 file '{Path(h5_file).name}' contains no tiles covering "
            f"bbox ({north},{south},{east},{west})"
        )

    # Transpose to match row=lat, col=lon orientation and crop
    mosaic = mosaic.T
    x1i = max(0, x1i); y1i = max(0, y1i)
    x2i = min(mosaic.shape[1], x2i); y2i = min(mosaic.shape[0], y2i)
    cropped = mosaic[y1i:y2i, x1i:x2i].astype(np.float64)

    # Clamp ocean floor noise and normalise like the notebook pipeline:
    # raise negatives (depth_scale will be applied by the caller), floor at 0.
    cropped = np.maximum(cropped, 0.0)
    logger.info(
        f"h5_local DEM: bbox=({north},{south},{east},{west}) "
        f"native_shape={cropped.shape} h5={Path(h5_file).name}"
    )
    return cropped


def fetch_esa_water_layer(
    north: float, south: float, east: float, west: float, dim: int
) -> np.ndarray:
    """
    Fetch ESA WorldCover water mask (class 80) at the requested resolution.
    Returns a float64 array: 0 = land, 1 = water.
    """
    from geo2stl.sat2stl import fetch_bbox_image
    import cv2 as _cv2
    img = fetch_bbox_image(north, south, east, west, scale=30, dataset="esa", use_cache=True)

    if img is None:
        return np.zeros((dim, dim), dtype=np.float64)

    if img.ndim == 3:
        img = img[:, :, 0]

    src_h, src_w = img.shape
    if src_h >= src_w:
        out_h, out_w = dim, max(1, int(dim * src_w / src_h))
    else:
        out_h, out_w = max(1, int(dim * src_h / src_w)), dim
    img_r = _cv2.resize(img.astype(np.float32), (out_w, out_h), interpolation=_cv2.INTER_NEAREST)
    return (img_r == 80).astype(np.float64)


def fetch_opentopo_dem(
    north: float, south: float, east: float, west: float,
    demtype: str, api_key: Optional[str], dim: int
) -> np.ndarray:
    """
    Download a GeoTIFF from OpenTopography's global DEM API and return a
    (height, width) numpy float64 array of elevation values (metres).

    Responses are cached locally under OPENTOPO_CACHE_PATH.

    Raises:
        RuntimeError  if the API returns an error or rasterio is unavailable.
    """
    import hashlib

    try:
        import rasterio
        from rasterio.enums import Resampling
    except ImportError:
        raise RuntimeError("rasterio is required for OpenTopography DEM fetching. "
                           "Install it with: pip install rasterio")

    import requests as _requests

    cache_key = hashlib.md5(
        f"{demtype}_{north:.5f}_{south:.5f}_{east:.5f}_{west:.5f}_{dim}".encode()
    ).hexdigest()
    OPENTOPO_CACHE_PATH.mkdir(parents=True, exist_ok=True)
    cache_file = OPENTOPO_CACHE_PATH / f"{cache_key}.tif"

    if not cache_file.exists():
        url = "https://portal.opentopography.org/API/globaldem"
        params = {
            "demtype": demtype,
            "south": south, "north": north, "west": west, "east": east,
            "outputFormat": "GTiff",
        }
        if api_key:
            params["API_Key"] = api_key

        logger.info(f"Fetching OpenTopography DEM: {demtype} bbox=({north},{south},{east},{west})")
        resp = _requests.get(url, params=params, timeout=120)

        if resp.status_code != 200:
            try:
                err_text = resp.text[:500]
            except Exception:
                err_text = f"HTTP {resp.status_code}"
            raise RuntimeError(f"OpenTopography API error ({resp.status_code}): {err_text}")

        cache_file.write_bytes(resp.content)
        logger.info(f"Cached OpenTopography response to {cache_file}")

    with rasterio.open(str(cache_file)) as src:
        src_h, src_w = src.height, src.width
        if src_h == 0 or src_w == 0:
            raise RuntimeError("OpenTopography returned an empty raster for this bbox.")

        if src_h >= src_w:
            out_h = dim
            out_w = max(1, int(dim * src_w / src_h))
        else:
            out_w = dim
            out_h = max(1, int(dim * src_h / src_w))

        data = src.read(
            1,
            out_shape=(out_h, out_w),
            resampling=Resampling.bilinear,
        ).astype(np.float64)

        nodata = src.nodata
        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)

    return data


def apply_layer_processing(arr: np.ndarray, spec: "ProcessingSpec") -> np.ndarray:
    """
    Apply the processing pipeline defined in spec to a 2-D float64 array.

    Operations (in order): clip → smooth → sharpen → extract_rivers → normalize → invert
    """
    import cv2 as _cv2
    from scipy import ndimage as ndi

    out = arr.astype(np.float64)

    if spec.clip_min is not None or spec.clip_max is not None:
        lo = spec.clip_min if spec.clip_min is not None else out.min()
        hi = spec.clip_max if spec.clip_max is not None else out.max()
        out = np.clip(out, lo, hi)

    if spec.smooth_sigma > 0:
        out = ndi.gaussian_filter(out, sigma=spec.smooth_sigma)

    if spec.sharpen:
        blurred = ndi.gaussian_filter(out, sigma=1.5)
        out = out + 0.5 * (out - blurred)

    if spec.extract_rivers:
        binary = out > 0.5
        r = spec.river_max_width_px
        half = max(1, r // 2)
        struct = np.ones((half * 2 + 1, half * 2 + 1), dtype=bool)
        large_bodies = ndi.binary_opening(binary, structure=struct)
        out = (binary & ~large_bodies).astype(np.float64)

    if spec.normalize:
        lo, hi = out.min(), out.max()
        if hi > lo:
            out = (out - lo) / (hi - lo)

    if spec.invert:
        lo, hi = out.min(), out.max()
        out = hi - out + lo

    return out


def blend_layers(
    base: np.ndarray,
    layer: np.ndarray,
    blend_mode: str,
    weight: float,
    output_shape: tuple,
) -> np.ndarray:
    """Blend `layer` onto `base` using the specified mode."""
    import cv2 as _cv2

    if layer.shape != base.shape:
        h, w = base.shape
        layer = _cv2.resize(layer.astype(np.float32), (w, h),
                            interpolation=_cv2.INTER_LINEAR).astype(np.float64)

    if blend_mode == "base":
        return base.copy()
    elif blend_mode == "replace":
        mask = layer != 0
        out = base.copy()
        out[mask] = layer[mask]
        return out
    elif blend_mode == "blend":
        return base * (1.0 - weight) + layer * weight
    elif blend_mode == "rivers":
        return base - layer * weight
    elif blend_mode == "max":
        return np.maximum(base, layer)
    elif blend_mode == "min":
        return np.minimum(base, layer)
    else:
        raise ValueError(
            f"Unknown blend_mode {blend_mode!r}. "
            "Valid modes: base, replace, blend, rivers, max, min"
        )


# ---------------------------------------------------------------------------
# DEM / satellite computation helpers (moved from routers/terrain.py)
# These are pure-computation functions with no HTTP dependencies; keeping them
# here makes them independently testable and keeps terrain.py thin.
# ---------------------------------------------------------------------------

def upsample_dem(im: np.ndarray, dim: int) -> np.ndarray:
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


def make_dem_payload(im: np.ndarray, west, south, east, north,
                     show_sat: bool, upscale_dim: int = None) -> dict:
    """
    Build the standard DEM response dict from a numpy array.
    Optionally upsamples to upscale_dim before serialising (used for cache hits).
    """
    if upscale_dim:
        im = upsample_dem(im, upscale_dim)
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


def compute_raw_dem(north, south, east, west, dim, depth_scale):
    """Compute raw (unprocessed) DEM array. Call via run_in_executor."""
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


def fetch_water_mask(
    north: float, south: float, east: float, west: float,
    sat_scale: int, dataset: str
) -> tuple:
    """Fetch and build a binary water mask for a bounding box.

    Auto-scales *sat_scale* upward if the estimated pixel count would exceed
    Earth Engine's 5 Mpx limit.

    Returns:
        (water_mask, esa_img, sat_scale_used)
        - water_mask:   float32 (H×W), 0 = land, 1 = water
        - esa_img:      uint8 (H×W) ESA WorldCover class values
        - sat_scale_used: int, the (possibly auto-scaled) sat_scale actually used
    """
    import cv2 as _cv2

    # Auto-scale sat_scale to avoid Earth Engine pixel limits
    bbox_w = abs(east - west)
    bbox_h = abs(north - south)
    mid_lat = (north + south) / 2.0
    m_per_deg_lon = 111000.0 * math.cos(math.radians(mid_lat))
    m_per_deg_lat = 111000.0
    est_px = (bbox_w * m_per_deg_lon / sat_scale) * (bbox_h * m_per_deg_lat / sat_scale)
    if est_px > 5_000_000:
        sat_scale = max(sat_scale, int(sat_scale * math.sqrt(est_px / 5_000_000)))
        logger.info(f"fetch_water_mask: auto-scaled sat_scale to {sat_scale}")

    img, jrc_img, elevation_raw = fetch_water_mask_images(north, south, east, west, sat_scale, dataset)

    if img is None:
        raise RuntimeError("Failed to fetch ESA land cover data")

    h, w = img.shape

    # Build water mask from selected dataset
    if dataset == "jrc" and jrc_img is not None:
        if jrc_img.ndim == 3:
            jrc_img = jrc_img[:, :, 0]
        if jrc_img.shape != (h, w):
            jrc_img = _cv2.resize(jrc_img.astype(np.float32), (w, h),
                                   interpolation=_cv2.INTER_LINEAR)
        water_mask = (jrc_img > 50).astype(np.float32)
    else:
        water_mask = (img == 80).astype(np.float32)

    # SRTM bathymetry augmentation — only for larger regions (> 30 km diagonal)
    # to avoid misclassifying low-lying coastal land at city scale.
    bbox_diag_km = math.sqrt(
        (bbox_h * m_per_deg_lat) ** 2 + (bbox_w * m_per_deg_lon) ** 2
    ) / 1000.0
    if elevation_raw is not None and elevation_raw.size > 0 and bbox_diag_km > 30:
        elev_r = _cv2.resize(elevation_raw.astype(np.float32), (w, h),
                              interpolation=_cv2.INTER_LINEAR)
        water_mask = np.maximum(water_mask, (elev_r < -2).astype(np.float32))

    return water_mask, img, sat_scale


def fetch_water_mask_images(north, south, east, west, sat_scale, water_dataset):
    """Fetch ESA/JRC images and optional elevation for bathymetry. Call via run_in_executor.
    Returns (img, jrc_img_or_None, elevation_raw_or_None) at native sat_scale resolution.
    """
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
    if img is not None and img.ndim == 3:
        img = img[:, :, 0]
    return img, jrc_img, elevation_raw


def fetch_sat_overlay(north, south, east, west, dataset, width_px, height_px, dim):
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


# ---------------------------------------------------------------------------
# ESRI World Imagery WMTS tile constants + Web Mercator helpers
# ---------------------------------------------------------------------------

_SAT_TILE_URL: str = (
    "https://server.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
_SAT_TILE_SIZE: int = 256  # pixels per tile side (standard WMTS)


def _wm_lon_to_tile(lon: float, n: int) -> int:
    """Return the X tile index for a longitude at zoom level with *n* = 2**zoom tiles."""
    return int((lon + 180.0) / 360.0 * n)


def _wm_lat_to_tile(lat: float, n: int) -> int:
    """Return the Y tile index for a latitude at zoom level with *n* = 2**zoom tiles.

    Uses the Web Mercator (EPSG:3857) tile formula. Clamps lat to ±85.05°.
    """
    lat_r = math.radians(max(-85.05, min(85.05, lat)))
    return int(
        (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi)
        / 2.0 * n
    )


def fetch_satellite_tiles(north: float, south: float, east: float, west: float, dim: int = 400) -> str:
    """
    Stitch ESRI World Imagery WMTS tiles into a bbox-cropped JPEG and return as base64.

    Automatically selects a zoom level so the output is at least *dim* pixels in the
    larger dimension.  No API key required — ESRI World Imagery tiles are publicly
    accessible for reasonable use.

    Returns a base64-encoded JPEG string, or raises on failure.
    """
    import base64
    import requests
    from PIL import Image
    from io import BytesIO

    lon_range = east - west
    lat_range = north - south
    deg_span  = max(lon_range, lat_range)
    zoom = 12  # fallback
    for z in range(10, 20):
        n = 2 ** z
        px_per_deg = _SAT_TILE_SIZE * n / 360.0
        if deg_span * px_per_deg >= dim:
            zoom = z
            break

    n = 2 ** zoom

    tx_min = _wm_lon_to_tile(west, n)
    tx_max = _wm_lon_to_tile(east, n)
    ty_min = _wm_lat_to_tile(north, n)
    ty_max = _wm_lat_to_tile(south, n)

    max_t = n - 1
    tx_min = max(0, min(tx_min, max_t))
    tx_max = max(0, min(tx_max, max_t))
    ty_min = max(0, min(ty_min, max_t))
    ty_max = max(0, min(ty_max, max_t))

    img_w = (tx_max - tx_min + 1) * _SAT_TILE_SIZE
    img_h = (ty_max - ty_min + 1) * _SAT_TILE_SIZE
    composite = Image.new("RGB", (img_w, img_h))

    session = requests.Session()
    session.headers["User-Agent"] = "strm2stl/1.0"

    tiles_loaded = 0
    tiles_total  = (tx_max - tx_min + 1) * (ty_max - ty_min + 1)
    last_tile_err = None

    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            url = _SAT_TILE_URL.format(z=zoom, y=ty, x=tx)
            try:
                resp = session.get(url, timeout=8)
                resp.raise_for_status()
                tile = Image.open(BytesIO(resp.content)).convert("RGB")
                composite.paste(tile, ((tx - tx_min) * _SAT_TILE_SIZE, (ty - ty_min) * _SAT_TILE_SIZE))
                tiles_loaded += 1
            except Exception as tile_err:
                last_tile_err = tile_err
                logger.debug(f"Satellite tile {z}/{ty}/{tx} failed: {tile_err}")

    if tiles_loaded == 0:
        raise RuntimeError(
            f"All {tiles_total} satellite tiles failed to load. "
            f"Last error: {last_tile_err}. "
            "Check network access to server.arcgisonline.com."
        )
    logger.info(f"Satellite tiles: {tiles_loaded}/{tiles_total} loaded at zoom {zoom}")

    def _lon2px(lon):
        return int((lon + 180.0) / 360.0 * n * _SAT_TILE_SIZE) - tx_min * _SAT_TILE_SIZE

    def _lat2py(lat):
        lat_r = math.radians(max(-85.05, min(85.05, lat)))
        return int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n * _SAT_TILE_SIZE) - ty_min * _SAT_TILE_SIZE

    crop = composite.crop((
        max(0, _lon2px(west)),
        max(0, _lat2py(north)),
        min(img_w, _lon2px(east)),
        min(img_h, _lat2py(south)),
    ))
    cw, ch = crop.size
    if cw >= ch:
        out_w, out_h = dim, max(1, round(dim * ch / cw))
    else:
        out_w, out_h = max(1, round(dim * cw / ch)), dim
    crop = crop.resize((out_w, out_h), Image.BILINEAR)

    buf = BytesIO()
    crop.save(buf, format="JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()
