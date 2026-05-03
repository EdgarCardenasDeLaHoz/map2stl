"""
geo2stl/dem.py â€” DEM fetch and layer-blend helpers.

Covers elevation data only:
  - fetch_layer_data        â€” dispatcher for all DEM sources
  - fetch_local_dem         â€” local SRTM tiles via numpy2stl
  - fetch_h5_dem            â€” local SRTM HDF5 tile store
  - fetch_esa_water_layer   â€” ESA WorldCover water band as float array
  - fetch_opentopo_dem      â€” OpenTopography global DEM API (cached GeoTIFF)
  - apply_layer_processing  â€” clip / smooth / sharpen / normalise pipeline
  - blend_layers            â€” blend two arrays with a named mode
  - upsample_dem            â€” cv2 upscale to display resolution
  - make_dem_payload        â€” build standard DEM JSON response dict
  - compute_raw_dem         â€” unprocessed DEM array (call via run_in_executor)

Satellite and water-mask imagery lives in geo2stl/sat.py.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import sys
from itertools import product as _product
from pathlib import Path
from typing import Optional

import cv2 as _cv2
import numpy as np
import requests as _requests
from geo2stl.sat2stl import fetch_bbox_image
from geo2stl.tiles import stitch_tiles_no_rasterio
from geo2stl.projections import project_grid, project_coordinates
from geo2stl.processing import apply_layer_processing, blend_layers, upsample_dem  # noqa: F401
try:
    from skimage import filters as _ski_filters
except ImportError:
    _ski_filters = None
try:
    from geo2stl.sat2stl import get_aquatic_regions as _get_aquatic_regions
except ImportError:
    _get_aquatic_regions = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config constants â€” inlined from app.server.config so this module can run
# outside the FastAPI server (notebooks, SDK, etc.).
# ---------------------------------------------------------------------------

# strm2stl root dir (geo2stl/dem.py â†’ geo2stl â†’ strm2stl)
_STRM2STL_DIR = Path(__file__).parent.parent

# OpenTopography GeoTIFF tile cache
_OPENTOPO_CACHE_PATH: Path = _STRM2STL_DIR / "cache" / "opentopo"

# OpenTopography API key: env var > config.json > None
_OPENTOPO_API_KEY: Optional[str] = os.environ.get("OPENTOPO_API_KEY")
try:
    _cfg_path = _STRM2STL_DIR / "config.json"
    if _cfg_path.exists() and _OPENTOPO_API_KEY is None:
        _cfg = json.loads(_cfg_path.read_text())
        _OPENTOPO_API_KEY = _cfg.get("opentopo_api_key") or None
except Exception:
    pass

if not _OPENTOPO_API_KEY:
    logger.warning(
        "No OpenTopography API key found. "
        "Set the OPENTOPO_API_KEY environment variable to enable DEM downloads."
    )

# Supported OpenTopography DEM types
OPENTOPO_DATASETS: dict[str, dict] = {
    "SRTMGL1":    {"label": "SRTM 30m (Global)",          "resolution_m": 30},
    "SRTMGL3":    {"label": "SRTM 90m (Global)",          "resolution_m": 90},
    "AW3D30":     {"label": "ALOS World 3D 30m",          "resolution_m": 30},
    "COP30":      {"label": "Copernicus DSM 30m",         "resolution_m": 30},
    "COP90":      {"label": "Copernicus DSM 90m",         "resolution_m": 90},
    "SRTM15Plus": {"label": "SRTM15+ (Bathymetry+Land)", "resolution_m": 500},
}

# H5 SRTM tile store
_H5_SRTM_ROOT: Optional[str] = os.environ.get("STRM_H5_ROOT")
_H5_SRTM_FILE: Optional[Path] = (
    Path(_H5_SRTM_ROOT) / "strm_data.h5" if _H5_SRTM_ROOT else None
)
_H5_SRTM_AVAILABLE: bool = bool(_H5_SRTM_FILE and _H5_SRTM_FILE.exists())


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
      "local"           â€“ local SRTM elevation tiles (metres)
      "water_esa"       â€“ ESA WorldCover water mask  (0/1 float)
      Any key in OPENTOPO_DATASETS â€“ OpenTopography elevation (metres)
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
    else:  # "local" or unknown â†’ local SRTM
        return fetch_local_dem(north, south, east, west, dim)


def make_dem_image(
    target_bbox,
    dim=600,
    depth_scale=0.5,
    sat_scale=400,
    water_scale=0.1,
    base=0.1,
    height=25,
    subtract_water=True,
    water_dataset="esa",
    clip=None,
    smooth=None,
    projection="cosine",
    maintain_dimensions=True,
    clip_nans=False,
):
    """Create a processed DEM array from a geographic bounding box.

    Stitches local SRTM tiles, optionally subtracts water bodies, applies a
    map projection and resizes to *dim* pixels on the longest axis.

    Migrated from numpy2stl/oceans.py â€” all dependencies are in geo2stl.
    """
    N, S, E, W = target_bbox
    result = stitch_tiles_no_rasterio(target_bbox)
    im = result.copy() * 1.0

    # Scale sub-zero (ocean / below-sea-level) values
    im[im < 0] = im[im < 0] * depth_scale

    if subtract_water:
        if water_dataset == "esa":
            _ee_max_px = 50_331_648 // 2
            mid_lat = (N + S) / 2.0
            _w_m = abs(E - W) * 111_320 * math.cos(math.radians(mid_lat))
            _h_m = abs(N - S) * 111_320
            min_safe = int(math.ceil(math.sqrt(_w_m * _h_m / _ee_max_px)))
            sat_scale = max(sat_scale, min_safe)
            try:
                sat = fetch_bbox_image(N, S, E, W, scale=sat_scale, dataset="esa")
                if sat is not None:
                    sat = np.array(sat).clip(0, 100)
                    water = 1.0 * ((sat == 80) | (sat == 0))
                    if _ski_filters is not None:
                        water = _ski_filters.median(water, np.ones((3, 3)))
                    h_im, w_im = im.shape
                    water = _cv2.resize(water, (w_im, h_im), interpolation=_cv2.INTER_LINEAR)
                    water = water * np.ptp(im.ravel()) * water_scale
                    im = im - water
            except Exception as exc:
                logger.warning("Could not process ESA water data: %s", exc)
        elif water_dataset == "jrc" and _get_aquatic_regions is not None:
            try:
                target_dim = min(max(im.shape[0], im.shape[1]), 500)
                img = _get_aquatic_regions(N, S, E, W, dataset="jrc", scale=None, target_dim=target_dim)
                if img is not None:
                    img2 = img.copy().astype(np.uint8)
                    if _ski_filters is not None:
                        img2 = _ski_filters.median(img2, np.ones((3, 3)))
                    img2 = _cv2.resize(img2, (im.shape[1], im.shape[0]),
                                       interpolation=_cv2.INTER_LINEAR).astype(int)
                    img2[im < 0] = 200
                    img2[im > 500] = 0
                    img2 = (img2 / 100).clip(0, 1)
                    im = im - img2 * np.ptp(im.ravel()) * water_scale
            except Exception as exc:
                logger.warning("Could not process JRC water data: %s", exc)
    else:
        im[im > 0] = im[im > 0] + np.ptp(im.ravel()) * water_scale

    if projection != "none":
        im, _ = project_coordinates(
            im,
            (N, S, E, W),
            projection=projection,
            maintain_dimensions=maintain_dimensions,
            fill_value=np.nan,
            clip_nans=clip_nans,
        )

    if dim:
        h, w = im.shape
        scale = dim / max(h, w)
        new_size = (int(w * scale), int(h * scale))
        im = _cv2.resize(im, new_size, interpolation=_cv2.INTER_LINEAR)

    return im


def fetch_local_dem(
    north: float, south: float, east: float, west: float, dim: int,
    *,
    depth_scale: float = 0.5,
    water_scale: float = 0.05,
    subtract_water: bool = False,
    maintain_dimensions: bool = True,
) -> np.ndarray:
    """Fetch local SRTM elevation tiles and return as a float64 array.

    Parameters beyond *dim* are optional; callers that just need raw
    elevation can ignore them (defaults match the old behaviour).
    """
    target_bbox = (north, south, east, west)
    im = make_dem_image(
        target_bbox, dim=dim,
        depth_scale=depth_scale,
        water_scale=water_scale,
        subtract_water=subtract_water,
        projection="none",
        maintain_dimensions=maintain_dimensions,
        clip_nans=False,
    )
    return np.nan_to_num(im, nan=0.0).astype(np.float64)


def fetch_dem_from_source(
    source: str,
    north: float,
    south: float,
    east: float,
    west: float,
    dim: int,
    *,
    depth_scale: float = 0.5,
    water_scale: float = 0.05,
    subtract_water: bool = True,
    maintain_dimensions: bool = True,
) -> np.ndarray:
    """Fetch DEM for any supported source with local failure fallback.

    Returns a zero-filled array if local DEM fetch fails at runtime.
    """
    if source in ("h5_local", *OPENTOPO_DATASETS):
        return fetch_layer_data(source, north, south, east, west, dim)

    try:
        return fetch_local_dem(
            north,
            south,
            east,
            west,
            dim,
            depth_scale=depth_scale,
            water_scale=water_scale,
            subtract_water=subtract_water,
            maintain_dimensions=maintain_dimensions,
        )
    except Exception as dem_err:
        logger.warning("Local DEM failed: %s, returning zeros", dem_err)
        lat_r = abs(north - south)
        lon_r = abs(east - west)
        if lat_r > lon_r:
            mh, mw = dim, max(1, int(dim * lon_r / lat_r))
        else:
            mw, mh = dim, max(1, int(dim * lat_r / lon_r))
        return np.zeros((mh, mw), dtype=float)


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

    The h5 file stores SRTM3 tiles at 6000Ã—6000 px per 5Â° tile (~90m/px).
    Returns a float64 array cropped to the requested bbox at native resolution.
    The caller is responsible for upsampling to the desired display resolution.

    Tile naming convention: srtm_{tilX:02d}_{tilY:02d}
      tilX = floor(lon / 5) + 37     (1-indexed, 36 tiles wide)
      tilY = floor(-lat / 5) + 13    (1-indexed, northward from equator)
    Each tile is 6000Ã—6000 pixels covering 5Â° Ã— 5Â°.

    Future: if h5 file is absent, fall back to OpenTopography SRTMGL3 API
    (same 90m data, global) or Google Earth Engine SRTM/NASADEM (30m).
    """
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

    Responses are cached locally under _OPENTOPO_CACHE_PATH.

    Raises:
        RuntimeError  if the API returns an error or rasterio is unavailable.
    """
    try:
        import rasterio
        from rasterio.enums import Resampling
    except ImportError:
        raise RuntimeError("rasterio is required for OpenTopography DEM fetching. "
                           "Install it with: pip install rasterio")

    cache_key = hashlib.md5(
        f"{demtype}_{north:.5f}_{south:.5f}_{east:.5f}_{west:.5f}_{dim}".encode()
    ).hexdigest()
    _OPENTOPO_CACHE_PATH.mkdir(parents=True, exist_ok=True)
    cache_file = _OPENTOPO_CACHE_PATH / f"{cache_key}.tif"

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


# apply_layer_processing, blend_layers, upsample_dem are re-exported above
# (imported from geo2stl.processing via the noqa: F401 import at the top).


def make_dem_payload(im: np.ndarray, west, south, east, north,
                     show_sat: bool, upscale_dim: int = None) -> dict:
    """
    Build the standard DEM response dict from a numpy array.

    Elevation values are encoded as base64 little-endian float32 to avoid
    the cost of converting large arrays to Python lists and JSON-serialising
    them on the main event-loop thread.  The client decodes with:
        new Float32Array(await res.arrayBuffer())  (after atob + Uint8Array)

    Optionally upsamples to upscale_dim before serialising (used for cache hits).
    """
    if upscale_dim:
        im = upsample_dem(im, upscale_dim)
    im_clean = np.nan_to_num(im, nan=0.0,
                              posinf=np.finfo(np.float32).max,
                              neginf=np.finfo(np.float32).min).astype(np.float32)
    h_px, w_px = im_clean.shape
    return {
        "dem_values_b64": base64.b64encode(im_clean.ravel().tobytes()).decode("ascii"),
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
    target_bbox = np.array((north, south, east, west))
    im = stitch_tiles_no_rasterio(target_bbox) * 1.0
    im[im < 0] = im[im < 0] * depth_scale
    im = project_grid(im, north, south, east, west,
                      projection='cosine', clip_nans=True)
    h, w = im.shape
    if h > w:
        new_h, new_w = dim, max(1, int(dim * w / h))
    else:
        new_w, new_h = dim, max(1, int(dim * h / w))
    im_r = _cv2.resize(im, (new_w, new_h), interpolation=_cv2.INTER_LINEAR)
    return im_r


# ---------------------------------------------------------------------------
# Mesh generation — bbox → DEM → STL pipeline
# ---------------------------------------------------------------------------

def create_dem_model(
    im: np.ndarray,
    simplify: bool = False,
    max_faces: int = 50_000,
    **kwargs,
) -> list:
    """Convert a DEM array to a list of mesh dicts via numpy2stl.

    Returns a list of dicts with keys ``vertices``, ``faces``, ``name``.
    Pass ``simplify=True`` to reduce the face count (requires numpy2stl.simplify).
    Extra *kwargs* are forwarded to :func:`numpy2stl.array_to_mesh`.
    """
    from numpy2stl import array_to_mesh  # lazy import — keeps geo2stl usable without numpy2stl

    # array_to_mesh accepts kwargs like mask_val, solid, walls, floor, floor_val
    mesh_kwargs = {k: v for k, v in kwargs.items()
                   if k in ("mask_val", "solid", "floor_val", "walls", "floor")}
    vertices, faces = array_to_mesh(im, **mesh_kwargs)
    models = [{"vertices": vertices, "faces": faces, "name": "terrain"}]

    if simplify:
        try:
            from numpy2stl.simplify import simplify_mesh
            models[0]["vertices"], models[0]["faces"] = simplify_mesh(
                vertices, faces, max_faces=max_faces
            )
        except ImportError:
            logger.warning("numpy2stl.simplify unavailable; skipping mesh simplification")

    return models


def process_region(
    name: str,
    bbox: tuple,
    output_dir,
    **processing_kwargs,
) -> str:
    """End-to-end bbox → STL pipeline: fetch DEM, build mesh, write STL file.

    Args:
        name: Region name used as the output filename stem.
        bbox: ``(north, south, east, west)`` bounding box.
        output_dir: Directory where the STL is written (created if absent).
        **processing_kwargs: Forwarded to :func:`make_dem_image` and
            :func:`create_dem_model`.

    Returns:
        Absolute path of the written STL file.
    """
    from numpy2stl import triangles_to_facets, writeSTL

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    im = make_dem_image(bbox, **processing_kwargs)
    models = create_dem_model(im, **processing_kwargs)

    stl_path = output_dir / f"{name}.stl"
    vertices = models[0]["vertices"]
    faces = models[0]["faces"]
    facets = triangles_to_facets(vertices[faces])
    writeSTL(facets, str(stl_path))
    logger.info("Saved terrain STL: %s", stl_path)
    return str(stl_path)
