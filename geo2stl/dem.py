"""
geo2stl/dem.py â€” DEM fetch and layer-blend helpers.

Covers elevation data only:
  - fetch_dem               — DEM for a bbox from any source (local / h5 / OpenTopography)
  - fetch_layer_data        â€” dispatcher for all DEM sources
  - fetch_local_dem         â€” local SRTM tiles via numpy2stl
  - fetch_h5_dem            â€” local SRTM HDF5 tile store
  - fetch_esa_water_layer   â€” ESA WorldCover water band as float array
  - fetch_opentopo_dem      â€” re-exported from geo2stl.opentopo (cached GeoTIFF)
  - apply_layer_processing  â€” clip / smooth / sharpen / normalise pipeline
  - blend_layers            â€” blend two arrays with a named mode
  - upsample_dem            â€” cv2 upscale to display resolution
  - make_dem_payload        â€” build standard DEM JSON response dict
  - compute_raw_dem         â€” unprocessed DEM array (call via run_in_executor)
  - DEM_SOURCE_INFO / dem_sampling / default_dem_source — native resolution per
    source, real samples across a bbox vs the returned grid, the default source

Satellite and water-mask imagery lives in geo2stl/sat.py.
"""

from __future__ import annotations

import base64
import logging
import math
import os
from itertools import product as _product
from pathlib import Path

import cv2 as _cv2
import numpy as np
from skimage import filters as _ski_filters

from geo2stl.geo import bbox_size_m
from geo2stl.opentopo import OPENTOPO_DATASETS, fetch_opentopo_dem  # noqa: F401 (re-export)
from geo2stl.processing import apply_layer_processing, blend_layers, upsample_dem  # noqa: F401
from geo2stl.projections import project_coordinates, project_grid
from geo2stl.sat2stl import fetch_bbox_image
from geo2stl.sat2stl import get_aquatic_regions as _get_aquatic_regions
from geo2stl.tiles import stitch_tiles_no_rasterio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config constants â€” inlined from app.server.config so this module can run
# outside the FastAPI server (notebooks, SDK, etc.).
# ---------------------------------------------------------------------------

# H5 SRTM tile store
#
# The default must stay in step with app/server/config.py:H5_SRTM_ROOT, which
# falls back to a project-relative path (Code/../strm_h5) when STRM_H5_ROOT is
# unset. This module previously read the env var alone, so with the var unset
# it resolved to None while config.py resolved to a file that exists. The
# server therefore advertised `h5_local` as available while every h5_local
# request silently fell through to the OpenTopography network path.
#
# geo2stl must not import from app.server (the dependency runs the other way),
# so the fallback is duplicated here rather than shared. Keep both in sync.
# dem.py -> geo2stl -> map2stl -> Code -> "3D Maps"; strm_h5 sits beside Code.
_H5_SRTM_ROOT: str | None = os.environ.get("STRM_H5_ROOT") or str(
    (Path(__file__).resolve().parents[3] / "strm_h5").resolve()
)
_H5_SRTM_FILE: Path | None = (
    Path(_H5_SRTM_ROOT) / "strm_data.h5" if _H5_SRTM_ROOT else None
)
_H5_SRTM_AVAILABLE: bool = bool(_H5_SRTM_FILE and _H5_SRTM_FILE.exists())


# ---------------------------------------------------------------------------
# DEM source metadata: native resolution and the default source
# ---------------------------------------------------------------------------

#: Native grid of every DEM source. ``arcsec`` is the sample spacing (exact for
#: counting samples across a bbox); ``native_resolution_m`` is its nominal ground
#: size at the equator, for display ("~30 m"). ``local`` is whatever GeoTIFF store
#: ``config.json``'s ``ocean_root`` points at; the project's store is GEBCO 2025
#: (15 arc-second, ~460 m), not SRTM, despite the source's historical label.
DEM_SOURCE_INFO: dict[str, dict] = {
    "local": {"label": "Local tile store (GEBCO 2025)", "arcsec": 15,
              "native_resolution_m": 460},
    "h5_local": {"label": "Local SRTM3 H5", "arcsec": 3, "native_resolution_m": 90},
    **{demtype: {"label": info["label"], "arcsec": info["arcsec"],
                 "native_resolution_m": info["resolution_m"]}
       for demtype, info in OPENTOPO_DATASETS.items()},
}


def native_resolution_m(source: str) -> float | None:
    """Nominal native ground resolution of *source* in metres (None if unknown)."""
    info = DEM_SOURCE_INFO.get(source)
    return float(info["native_resolution_m"]) if info else None


def dem_sampling(source: str, bbox, grid_shape) -> dict | None:
    """How many real samples of *source* span *bbox*, against the returned grid.

    Returns ``{source, native_resolution_m, native_samples: [rows, cols],
    grid: [rows, cols], upsample: float}`` where ``upsample`` is the larger of the
    two per-axis ratios grid / native (> 1 means the grid is interpolated from
    fewer real samples). None for an unknown source.

    Args:
        bbox: ``(north, south, east, west)`` or a dict with those keys.
        grid_shape: ``(rows, cols)`` of the array actually returned.
    """
    info = DEM_SOURCE_INFO.get(source)
    if info is None:
        return None
    if isinstance(bbox, dict):
        north, south, east, west = bbox["north"], bbox["south"], bbox["east"], bbox["west"]
    else:
        north, south, east, west = bbox
    per_deg = 3600.0 / float(info["arcsec"])
    rows = max(1, int(round(abs(north - south) * per_deg)))
    cols = max(1, int(round(abs(east - west) * per_deg)))
    g_rows, g_cols = int(grid_shape[0]), int(grid_shape[1])
    return {
        "source": source,
        "native_resolution_m": float(info["native_resolution_m"]),
        "native_samples": [rows, cols],
        "grid": [g_rows, g_cols],
        "upsample": round(max(g_rows / rows, g_cols / cols), 2),
    }


def default_dem_source(api_key_configured: bool,
                       h5_available: bool | None = None) -> str:
    """The DEM source a new region starts with.

    SRTM 30 m (``SRTMGL1``) whenever an OpenTopography key is configured; without
    one, the best local store: the ~90 m SRTM3 H5 file if present, else the tile
    store (``local``).
    """
    if api_key_configured:
        return "SRTMGL1"
    if h5_available is None:
        h5_available = _H5_SRTM_AVAILABLE
    return "h5_local" if h5_available else "local"


# ---------------------------------------------------------------------------
# Layer source registry
# ---------------------------------------------------------------------------
#
# The composite DEM draws on layers this library cannot reach: the OSM
# rasterizers read the server-side OSM cache, and geo2stl must not import from
# app.server (the dependency runs the other way). The registry inverts that -
# the server registers its own sources here at import, and fetch_layer_data
# resolves them alongside the built-in elevation and water sources.
#
# A provider is called as provider(north, south, east, west, dim, options) and
# must return a 2-D float array. ``options`` is the per-layer parameter bag
# from MergeLayerSpec, for anything a scalar weight cannot express (the ESA
# class-to-height table, a trails difficulty filter).

_LAYER_SOURCES: dict = {}


def register_layer_source(name: str, provider) -> None:
    """Register a named layer source for fetch_layer_data.

    Registering a name that already exists replaces it, so a server module can
    be re-imported without raising.
    """
    _LAYER_SOURCES[name] = provider


def registered_layer_sources() -> list:
    """Names of every source registered beyond the built-in ones."""
    return sorted(_LAYER_SOURCES)


def is_terrain_relative_source(name: str) -> bool:
    """True for a registered source whose grid is a depth relative to the ground.

    Such a provider (``provider.terrain_relative = True``; the rivers and lakes
    of ``geo2stl.water_layers``) is called with ``base=`` the base DEM layer's
    raw grid, returns negative metres on that grid, and is blended with ``add``.
    """
    return bool(getattr(_LAYER_SOURCES.get(name), "terrain_relative", False))


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def fetch_layer_data(
    source: str,
    north: float, south: float, east: float, west: float,
    dim: int,
    options: dict | None = None,
    *,
    api_key: str | None = None,
    base: np.ndarray | None = None,
) -> np.ndarray:
    """
    Fetch a 2-D float64 numpy array for one merge layer.

    Built-in sources:
      "local"     - local SRTM elevation tiles (metres)
      "h5_local"  - the local HDF5 SRTM store, falling back to SRTMGL3
      "water_esa" - ESA WorldCover water mask (0/1 float)
      Any key in OPENTOPO_DATASETS - OpenTopography elevation (metres)

    Anything registered through register_layer_source resolves first, so
    the server can supply layers this library cannot reach on its own -
    the OSM rasterizers, which read the server-side OSM cache. *options*
    is the per-layer parameter bag those providers receive; the built-in
    sources ignore it. *api_key* is the OpenTopography key (None:
    ``geo2stl.opentopo.get_api_key()``). *base* is the base DEM layer's raw
    grid, passed only to terrain-relative providers
    (:func:`is_terrain_relative_source`), which rasterise onto it.
    """
    provider = _LAYER_SOURCES.get(source)
    if provider is not None:
        if getattr(provider, "terrain_relative", False):
            return provider(north, south, east, west, dim, options or {}, base=base)
        return provider(north, south, east, west, dim, options or {})

    if source == "water_esa":
        return fetch_esa_water_layer(north, south, east, west, dim)
    elif source == "h5_local":
        try:
            # 2x the requested size, so the resize to dim still averages.
            return fetch_h5_dem(north, south, east, west, max_px=2 * dim if dim else None)
        except FileNotFoundError as exc:
            logger.warning(
                "h5_local DEM unavailable (%s); falling back to SRTMGL3 via OpenTopography", exc
            )
            return fetch_opentopo_dem(north, south, east, west,
                                      demtype="SRTMGL3",
                                      api_key=api_key,
                                      dim=dim)
    elif source in OPENTOPO_DATASETS:
        return fetch_opentopo_dem(north, south, east, west,
                                  demtype=source,
                                  api_key=api_key,
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
            _w_m, _h_m = bbox_size_m({"north": N, "south": S, "east": E, "west": W})
            min_safe = int(math.ceil(math.sqrt(_w_m * _h_m / _ee_max_px)))
            sat_scale = max(sat_scale, min_safe)
            try:
                sat = fetch_bbox_image(
                    N, S, E, W, scale=sat_scale, dataset="esa")
                if sat is not None:
                    sat = np.array(sat).clip(0, 100)
                    water = 1.0 * ((sat == 80) | (sat == 0))
                    water = _ski_filters.median(water, np.ones((3, 3)))
                    h_im, w_im = im.shape
                    water = _cv2.resize(water, (w_im, h_im),
                                        interpolation=_cv2.INTER_LINEAR)
                    water = water * np.ptp(im.ravel()) * water_scale
                    im = im - water
            except Exception as exc:
                logger.warning("Could not process ESA water data: %s", exc)
        elif water_dataset == "jrc":
            try:
                target_dim = min(max(im.shape[0], im.shape[1]), 500)
                img = _get_aquatic_regions(
                    N, S, E, W, dataset="jrc", scale=None, target_dim=target_dim)
                if img is not None:
                    img2 = img.copy().astype(np.uint8)
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
    return im.astype(np.float64, copy=False)


def fetch_dem(
    bbox,
    dim: int,
    source: str = "local",
    api_key: str | None = None,
    *,
    depth_scale: float = 0.5,
    water_scale: float = 0.05,
    subtract_water: bool = True,
    maintain_dimensions: bool = True,
) -> np.ndarray:
    """Fetch a plate-carrée DEM for *bbox* from any DEM source.

    Routing:
      ``h5_local`` or an ``OPENTOPO_DATASETS`` key -> :func:`fetch_layer_data`
      (``h5_local`` falls back to OpenTopography SRTMGL3 when the store is missing);
      ``local`` or unknown -> :func:`fetch_local_dem` (local SRTM tiles, with the
      depth / water options). A local failure (usually no tile coverage) returns
      a zero array with the bbox aspect, longer side *dim*; callers detect the
      flat result and warn.

    Args:
        bbox: ``(north, south, east, west)`` or a dict with those keys.
        dim: Longer output side in pixels (sources may return their native size;
            upsample with :func:`upsample_dem`).
        source: DEM source id.
        api_key: OpenTopography key; None means ``geo2stl.opentopo.get_api_key()``.
    """
    if isinstance(bbox, dict):
        north, south, east, west = bbox["north"], bbox["south"], bbox["east"], bbox["west"]
    else:
        north, south, east, west = bbox
    if source in ("h5_local", *OPENTOPO_DATASETS):
        return fetch_layer_data(source, north, south, east, west, dim, api_key=api_key)

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
_H5_BAND_ROWS: int = 1024   # fetch_h5_dem reads each tile in row bands of about this size
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
    h5_file: Path | None = None,
    max_px: int | None = None,
) -> np.ndarray:
    """
    Read elevation from the local SRTM HDF5 tile store (strm_data.h5).

    The h5 file stores SRTM3 tiles at 6000Ã—6000 px per 5Â° tile (~90m/px).
    Returns a float64 array cropped to the requested bbox at native resolution, or
    with ``max_px`` the mean of each k x k block so the longer side is about
    ``max_px``. Tiles are read one at a time, in row bands, and summed into the
    output, so memory stays at one band: a 40 x 34 degree box is 40,800 x 48,000
    native pixels, which as a full mosaic plus float64 crop needed ~20 GB and
    killed the server. Block means, not every k-th pixel: at k = 40 a single
    90 m sample per 3.6 km aliases peaks and valleys. The caller resizes to the
    display resolution; finished DEMs are cached per request by the server.

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
        raise ImportError(
            "h5py is required for h5_local DEM source: pip install h5py") from exc

    tx1, ty1, px1, py1 = _geo_to_tile_pixel(north, west)
    tx2, ty2, px2, py2 = _geo_to_tile_pixel(south, east)

    # Pixel extents span possibly multiple tiles
    px2_abs = (tx2 - tx1) * _H5_TILE_PX + px2
    py2_abs = (ty2 - ty1) * _H5_TILE_PX + py2

    x1i, y1i = int(round(px1)), int(round(py1))
    x2i, y2i = int(round(px2_abs)), int(round(py2_abs))
    span_x = (tx2 - tx1 + 1)
    span_y = (ty2 - ty1 + 1)
    x1i, y1i = max(0, x1i), max(0, y1i)
    x2i = min(span_x * _H5_TILE_PX, x2i)
    y2i = min(span_y * _H5_TILE_PX, y2i)
    step = max(1, max(y2i - y1i, x2i - x1i) // max_px) if max_px else 1
    out_h = -(-(y2i - y1i) // step)
    out_w = -(-(x2i - x1i) // step)
    total = np.zeros((out_h, out_w), dtype=np.float64)
    count = np.zeros((out_h, out_w), dtype=np.float64)

    # The datasets are stored row=lat (north at row 0), col=lon; iy walks tile rows
    # southward, ix walks tile columns eastward. A transpose used to be applied
    # here, which swapped the pixel axes and silently sampled a point elsewhere in
    # the same 5-degree tile: Breckenridge read 1745-2029 m instead of its true
    # 2860-4210 m. Verified against SRTMGL1/COP30 for the same bbox.
    band = max(step, (_H5_BAND_ROWS // step) * step)
    tiles_found = 0
    with h5py.File(str(h5_file), "r") as fh:
        for ix, iy in _product(range(span_x), range(span_y)):
            key = f"srtm_{tx1 + ix:02d}_{ty1 + iy:02d}"
            if key not in fh:
                logger.debug(f"h5 tile missing: {key}")
                continue
            tiles_found += 1
            ds = fh[key]
            r0, c0 = iy * _H5_TILE_PX, ix * _H5_TILE_PX
            # Global rows / cols of this tile inside the box.
            ga, gb = max(y1i, r0), min(y2i, r0 + min(ds.shape[0], _H5_TILE_PX))
            ca, cb = max(x1i, c0), min(x2i, c0 + min(ds.shape[1], _H5_TILE_PX))
            if ga >= gb or ca >= cb:
                continue
            ocol = (np.arange(ca, cb) - x1i) // step
            cstarts = np.flatnonzero(np.r_[True, ocol[1:] != ocol[:-1]])
            for ra in range(ga, gb, band):
                rb = min(gb, ra + band)
                data = np.maximum(ds[ra - r0:rb - r0, ca - c0:cb - c0], 0).astype(np.float64)
                orow = (np.arange(ra, rb) - y1i) // step
                rstarts = np.flatnonzero(np.r_[True, orow[1:] != orow[:-1]])
                sums = np.add.reduceat(np.add.reduceat(data, rstarts, axis=0), cstarts, axis=1)
                n_r = np.diff(np.r_[rstarts, len(orow)])
                n_c = np.diff(np.r_[cstarts, len(ocol)])
                rr, cc = orow[rstarts][:, None], ocol[cstarts][None, :]
                total[rr, cc] += sums
                count[rr, cc] += n_r[:, None] * n_c[None, :]

    if tiles_found == 0:
        raise FileNotFoundError(
            f"h5 file '{Path(h5_file).name}' contains no tiles covering "
            f"bbox ({north},{south},{east},{west})"
        )
    # Cells no tile covers (missing tiles) stay 0, as the old mosaic did.
    cropped = np.divide(total, count, out=np.zeros_like(total), where=count > 0)

    # Clamp ocean floor noise and normalise like the notebook pipeline:
    # raise negatives (depth_scale will be applied by the caller), floor at 0.
    cropped = np.maximum(cropped, 0.0)
    logger.info(
        f"h5_local DEM: bbox=({north},{south},{east},{west}) "
        f"shape={cropped.shape} step={step} h5={Path(h5_file).name}"
    )
    return cropped


def fetch_esa_water_layer(
    north: float, south: float, east: float, west: float, dim: int
) -> np.ndarray:
    """Open water (sea and lakes) at *dim* on the longer side: 0 = land, 1 = water.

    :func:`geo2stl.hydrology.water_surface_mask` - ESA WorldCover class 80 plus its
    no-data (the ocean: WorldCover maps land only) united with the sea of the
    local elevation store. Class 80 alone left the open sea out, so the
    composite lowered lakes and rivers but not the sea, and every river mouth
    became a notch in the coast.
    """
    from geo2stl.hydrology import water_surface_mask

    lat_span, lon_span = abs(north - south), abs(east - west)
    if lat_span >= lon_span:
        out_h, out_w = dim, max(1, int(round(dim * lon_span / max(lat_span, 1e-12))))
    else:
        out_h, out_w = max(1, int(round(dim * lat_span / lon_span))), dim
    water = water_surface_mask(north, south, east, west, (out_h, out_w))
    if water is None:
        return np.zeros((out_h, out_w), dtype=np.float64)
    return water.astype(np.float64)


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
    # Preserve NaN padding in the binary payload so projected invalid regions
    # remain distinguishable on the client (transparent in renderer) and the
    # clip-valid toggle has a visible effect. Only clamp infinities.
    im_clean = im.astype(np.float32, copy=True)
    im_clean[np.isposinf(im_clean)] = np.finfo(np.float32).max
    im_clean[np.isneginf(im_clean)] = np.finfo(np.float32).min
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

def create_dem_model(im: np.ndarray, **kwargs) -> list:
    """Convert a DEM array to a list of mesh dicts via numpy2stl.

    Returns a list of dicts with keys ``vertices``, ``faces``, ``name``.
    Extra *kwargs* are forwarded to :func:`numpy2stl.array_to_mesh`.
    """
    from numpy2stl import array_to_mesh

    # array_to_mesh accepts kwargs like mask_val, solid, walls, floor, floor_val
    mesh_kwargs = {k: v for k, v in kwargs.items()
                   if k in ("mask_val", "solid", "floor_val", "walls", "floor")}
    vertices, faces = array_to_mesh(im, **mesh_kwargs)
    return [{"vertices": vertices, "faces": faces, "name": "terrain"}]


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
