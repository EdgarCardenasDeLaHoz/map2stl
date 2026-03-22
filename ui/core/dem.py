"""
core/dem.py — DEM fetch and layer-blend helpers.

Extracted from location_picker.py (backend refactor, step 3).
These are pure (non-HTTP) functions that can be imported and called
from route handlers or tested independently.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from schemas import ProcessingSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import config — fall back to location_picker globals if not available
# ---------------------------------------------------------------------------
try:
    from config import (
        OPENTOPO_CACHE_PATH,
        OPENTOPO_API_KEY as _OPENTOPO_API_KEY,
        OPENTOPO_DATASETS,
        H5_SRTM_FILE as _H5_SRTM_FILE,
        H5_SRTM_AVAILABLE as _H5_SRTM_AVAILABLE,
    )
except ImportError:
    # Running from a context where config.py is not on sys.path.
    # Derive the same paths inline so the module is self-contained.
    _UI_DIR = Path(__file__).parent.parent
    _STRM2STL_DIR = _UI_DIR.parent
    OPENTOPO_CACHE_PATH = _STRM2STL_DIR / "opentopo_cache"
    _OPENTOPO_API_KEY = os.environ.get("OPENTOPO_API_KEY")
    OPENTOPO_DATASETS = {
        "SRTMGL1":    {"label": "SRTM 30m (Global)",          "resolution_m": 30},
        "SRTMGL3":    {"label": "SRTM 90m (Global)",          "resolution_m": 90},
        "AW3D30":     {"label": "ALOS World 3D 30m",          "resolution_m": 30},
        "COP30":      {"label": "Copernicus DSM 30m",         "resolution_m": 30},
        "COP90":      {"label": "Copernicus DSM 90m",         "resolution_m": 90},
        "SRTM15Plus": {"label": "SRTM15+ (Bathymetry+Land)", "resolution_m": 500},
    }
    _h5_path = Path(os.environ.get("STRM_H5_ROOT", r"C:\Users\eac84\Desktop\Desktop\FILES")) / "strm_data.h5"
    _H5_SRTM_FILE = _h5_path
    _H5_SRTM_AVAILABLE = _h5_path.exists()

# strm2stl root dir (parent of ui/)
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
    import math
    from itertools import product as _product

    if h5_file is None:
        h5_file = _H5_SRTM_FILE
    if not h5_file or not Path(h5_file).exists():
        raise FileNotFoundError(f"SRTM h5 file not found: {h5_file}")

    try:
        import h5py
    except ImportError as exc:
        raise ImportError("h5py is required for h5_local DEM source: pip install h5py") from exc

    TILE_PX = 6000      # pixels per tile side
    TILE_DEG = 5.0      # degrees per tile

    def _geo_to_tile_pixel(lat: float, lon: float):
        """Return (tile_x, tile_y, pix_x, pix_y) for a geographic coordinate."""
        tx = int(math.floor(lon / TILE_DEG)) + 36 + 1
        ty = int(math.floor(-lat / TILE_DEG)) + 12 + 1
        px = (lon / TILE_DEG - math.floor(lon / TILE_DEG)) * TILE_PX
        py = (-lat / TILE_DEG - math.floor(-lat / TILE_DEG)) * TILE_PX
        return tx, ty, px, py

    tx1, ty1, px1, py1 = _geo_to_tile_pixel(north, west)
    tx2, ty2, px2, py2 = _geo_to_tile_pixel(south, east)

    # Pixel extents span possibly multiple tiles
    px2_abs = (tx2 - tx1) * TILE_PX + px2
    py2_abs = (ty2 - ty1) * TILE_PX + py2

    x1i, y1i = int(round(px1)), int(round(py1))
    x2i, y2i = int(round(px2_abs)), int(round(py2_abs))
    span_x = (tx2 - tx1 + 1)
    span_y = (ty2 - ty1 + 1)
    mosaic_h = span_y * TILE_PX
    mosaic_w = span_x * TILE_PX
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
            out_r = iy * TILE_PX
            out_c = ix * TILE_PX
            mosaic[out_r:out_r + min(th, TILE_PX),
                   out_c:out_c + min(tw, TILE_PX)] = data[:TILE_PX, :TILE_PX]

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
