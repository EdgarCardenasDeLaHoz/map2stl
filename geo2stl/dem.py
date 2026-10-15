"""
geo2stl/dem.py â€” DEM fetch and layer-blend helpers.

Covers elevation data only:
  - fetch_dem               — DEM for a bbox from any source (local / h5 / OpenTopography)
  - fetch_layer_data        â€” dispatcher for all DEM sources
  - fetch_local_dem         â€” local SRTM tiles via numpy2stl
  - fetch_h5_dem            â€” local SRTM HDF5 tile store
  - fetch_esa_water_layer   â€” ESA WorldCover water band as float array
  - water_fraction_for_dem  - water (0..1) on a DEM's plate-carree grid, with a
    note when ESA is unavailable and only the sea (from elevation) could be found
  - apply_water_and_depth   - depth scale + water subtraction, the one block every
    DEM source goes through (fetch_dem, make_dem_image)
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
import threading
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


def is_shared_grid_source(name: str) -> bool:
    """True for a terrain-relative source that may be built once on the shared grid.

    Such a provider (``provider.shared_grid = True``: the river sources) gives the
    same answer on any grid at least as fine, so ``compute_composite_dem`` calls it
    on :func:`shared_base_grid` and pools the result down to each projection's
    source grid, deepest kept (``geo2stl.water_layers.pool_deepest``).
    """
    return bool(getattr(_LAYER_SOURCES.get(name), "shared_grid", False))


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
    notes: list[str] | None = None,
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
    (:func:`is_terrain_relative_source`), which rasterise onto it. *notes*
    receives user-facing warnings (``h5_local`` coverage incomplete).
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
            return _fetch_h5_with_fallback(north, south, east, west, dim,
                                           api_key=api_key, notes=notes)
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
        # Explicitly no water subtraction: a merge layer is raw elevation (the
        # composite carves water with its own layers), whatever fetch_local_dem's
        # default.
        return fetch_local_dem(north, south, east, west, dim, subtract_water=False)


SHARED_BASE_VERSION = 1   # bump when shared_base_grid's output changes
_SHARED_BASE_MEMO: dict = {}          # key -> float32 grid, insertion order = age
_SHARED_BASE_MEMO_MAX = 4
_SHARED_BASE_GUARD = threading.Lock()
_SHARED_BASE_LOCKS: dict[str, list] = {}   # key -> [lock, callers holding or waiting]


def is_shared_base_source(source: str) -> bool:
    """Whether *source* is a built-in elevation source :func:`shared_base_grid` serves.

    Registered providers (OSM rasters, rivers, lakes) and ``water_esa`` are not:
    they are categorical or relative grids that an area resample would smear.
    """
    return source not in _LAYER_SOURCES and source != "water_esa"


def shared_base_grid(source: str, north: float, south: float, east: float, west: float,
                     dim: int, options: dict | None = None,
                     maintain_dimensions: bool = False) -> np.ndarray:
    """The base elevation layer on the projection-independent shared grid.

    ``fetch_layer_data(source, ..., shared_source_dim(dim))`` fitted to
    ``geo2stl.projections.shared_source_shape(dim)`` as float32 (half the memory
    of the float64 copy it once was; same values, so the same carve hash), held in memory (the last :data:`_SHARED_BASE_MEMO_MAX` boxes) and, for
    OpenTopography sources (an immutable GeoTIFF per box), on disk. Each
    projection takes its own source grid from it with ``fit_grid`` instead of
    decoding the GeoTIFF again: Europa's SRTM15+ file (15272 x 9796 px, 376 MB)
    cost 3.6 s per read, once for the composite and once for the rivers preview
    on every projection switch. Concurrent callers for one key wait for one read.
    Use only for :func:`is_shared_base_source` sources.
    """
    from geo2stl.cache import make_cache_key, read_array_cache, write_array_cache
    from geo2stl.projections import fit_grid, shared_source_dim, shared_source_shape

    bbox = (north, south, east, west)
    sdim = shared_source_dim(dim, bbox, maintain_dimensions)
    shape = shared_source_shape(dim, bbox, maintain_dimensions)
    key = make_cache_key("shared_base", north, south, east, west, {
        "src": source, "sd": sdim, "shape": list(shape), "opts": options or {},
        "v": SHARED_BASE_VERSION})
    with _SHARED_BASE_GUARD:
        hit = _SHARED_BASE_MEMO.get(key)
        if hit is not None:
            return hit
        entry = _SHARED_BASE_LOCKS.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    on_disk = source in OPENTOPO_DATASETS
    try:
        with entry[0]:
            with _SHARED_BASE_GUARD:
                hit = _SHARED_BASE_MEMO.get(key)
            if hit is not None:
                return hit
            cached = read_array_cache("shared_base", key) if on_disk else None
            if cached is not None and cached[0].get("dem") is not None:
                grid = np.asarray(cached[0]["dem"], dtype=np.float32)
            else:
                from geo2stl.perf import perf_step
                with perf_step("shared_base", key=key, src=source, shape=list(shape)):
                    raw = fetch_layer_data(source, north, south, east, west, sdim, options)
                    # float32, as the disk cache stores it: the river carve hashes
                    # this grid (as float64 bytes, water_layers.river_carve_layers),
                    # and a memory hit must hash the same as a disk hit.
                    grid = fit_grid(np.asarray(raw, dtype=np.float32), shape)
                if on_disk:
                    write_array_cache("shared_base", key, {"dem": grid})
            with _SHARED_BASE_GUARD:
                _SHARED_BASE_MEMO[key] = grid
                while len(_SHARED_BASE_MEMO) > _SHARED_BASE_MEMO_MAX:
                    _SHARED_BASE_MEMO.pop(next(iter(_SHARED_BASE_MEMO)))
            return grid
    finally:
        with _SHARED_BASE_GUARD:
            entry[1] -= 1
            if entry[1] == 0:
                _SHARED_BASE_LOCKS.pop(key, None)


#: Earth Engine's request cap (50 MB at ~2 bytes/px) and its 32768 px axis limit.
_EE_MAX_PX = 50_331_648 // 2
_EE_MAX_AXIS_PX = 32768

#: Shown when ESA WorldCover cannot be read (Earth Engine not set up and no
#: cached image): lakes and rivers stay at land height.
WATER_NOTE_SEA_ONLY = (
    "Water subtraction: lakes and rivers were not lowered, because ESA WorldCover "
    "needs Earth Engine (not set up on this server; see the Keys panel). Only the "
    "sea, read from the elevation data, was lowered.")
WATER_NOTE_NONE = (
    "Water subtraction found no water: ESA WorldCover needs Earth Engine (not set "
    "up on this server; see the Keys panel), and the elevation data has no sea in "
    "this box. Lakes and rivers were not lowered.")


def _ee_scale_floor(north, south, east, west) -> int:
    """Smallest metres/pixel Earth Engine will serve for the box (continental
    boxes need a coarse scale or the request is refused)."""
    w_m, h_m = bbox_size_m({"north": north, "south": south, "east": east, "west": west})
    by_size = int(math.ceil(math.sqrt(w_m * h_m / _EE_MAX_PX)))
    by_axis = int(math.ceil(max(w_m, h_m) / _EE_MAX_AXIS_PX))
    return max(by_size, by_axis, 1)


def water_fraction_for_dem(
    bbox, shape, *, dataset: str = "esa", sat_scale: float | None = None,
    elevation: np.ndarray | None = None,
) -> tuple[np.ndarray | None, str | None]:
    """Water on a DEM's plate-carree grid: ``(fraction, note)``.

    *fraction* is a float array of *shape* in 0..1 (row 0 north), or None when no
    water source answered. *note* is a user-facing sentence when the mask is
    incomplete, for the DEM response (``source_note``); None when ESA was read.

    Sources, first that answers:
      1. ESA WorldCover (``dataset="esa"``: class 80 water plus class 0, its
         no-data, where the ocean lies) or JRC surface water, through
         :func:`geo2stl.sat2stl.fetch_bbox_image`, which serves its disk cache
         before trying Earth Engine. *sat_scale* (m/px; None: one ESA pixel per
         DEM pixel, at least 10 m) is raised to Earth Engine's floor for the box.
      2. The sea in *elevation* itself (the DEM being processed, so it is
         aligned): cells at or below 0 m connected to the box edge
         (:func:`geo2stl.water_layers.ocean_mask`). No lakes or rivers.
    """
    north, south, east, west = bbox
    h, w = int(shape[0]), int(shape[1])
    if sat_scale is None:
        w_m, h_m = bbox_size_m({"north": north, "south": south, "east": east, "west": west})
        sat_scale = max(10, int(math.ceil(max(w_m, h_m, 1.0) / max(h, w, 1))))
    scale = max(int(math.ceil(sat_scale)), _ee_scale_floor(north, south, east, west))
    try:
        if dataset == "jrc":
            img = _get_aquatic_regions(north, south, east, west, dataset="jrc", scale=None,
                                       target_dim=min(max(h, w), 500))
            if img is not None:
                frac = _ski_filters.median(np.asarray(img).astype(np.uint8), np.ones((3, 3)))
                frac = _cv2.resize(frac, (w, h), interpolation=_cv2.INTER_LINEAR).astype(float)
                if elevation is not None:
                    frac[np.asarray(elevation) < 0] = 200     # the sea counts as water
                    frac[np.asarray(elevation) > 500] = 0     # JRC noise on mountains
                return (frac / 100).clip(0, 1), None
        else:
            sat = fetch_bbox_image(north, south, east, west, scale=scale, dataset="esa")
            if sat is not None and np.size(sat):
                sat = np.asarray(sat)
                if sat.ndim == 3:
                    sat = sat[:, :, 0]
                sat = sat.clip(0, 100)
                water = 1.0 * ((sat == 80) | (sat == 0))
                water = _ski_filters.median(water, np.ones((3, 3)))
                water = _cv2.resize(water.astype(np.float32), (w, h),
                                    interpolation=_cv2.INTER_LINEAR)
                return np.clip(water.astype(np.float64), 0.0, 1.0), None
    except Exception as exc:     # Earth Engine missing / not authenticated / offline
        logger.warning("water_fraction_for_dem: %s water unavailable (%s)", dataset, exc)

    if elevation is not None and np.size(elevation):
        from geo2stl.water_layers import ocean_mask

        z = np.asarray(elevation, dtype=np.float64)
        if z.shape != (h, w):
            z = _cv2.resize(z.astype(np.float32), (w, h),
                            interpolation=_cv2.INTER_LINEAR).astype(np.float64)
        sea = ocean_mask(z)
        if sea.any():
            return sea.astype(np.float64), WATER_NOTE_SEA_ONLY
    return None, WATER_NOTE_NONE


def apply_water_and_depth(
    im: np.ndarray, bbox, *,
    depth_scale: float = 0.5, water_scale: float = 0.05, subtract_water: bool = True,
    water_dataset: str = "esa", sat_scale: float | None = None,
    dtype=np.float32,
) -> tuple[np.ndarray, str | None]:
    """Depth scaling and water subtraction on a plate-carree DEM: ``(im, note)``.

    The one definition, for every DEM source (:func:`fetch_dem` runs it on local,
    h5 and OpenTopography grids alike; :func:`make_dem_image` on its stitch).

      * Cells below 0 m are multiplied by *depth_scale*.
      * *subtract_water*: the water fraction (:func:`water_fraction_for_dem`) times
        ``relief * water_scale`` is subtracted, so water sits below the shore.
        *note* says so when only the sea, or no water, could be found.
      * otherwise land above 0 m is raised by ``relief * water_scale`` (the old
        "no subtraction" step, kept so the setting means the same on every source).

    ``relief`` is the finite range of the grid after depth scaling; NaN no-data
    (OpenTopography) is left as NaN.

    The result is a new *dtype* array (float32: the DEM response sends float32,
    and a float64 copy of a large grid cost 18-107 ms). :func:`make_dem_image`
    asks for float64: its output is the ``local`` source's raw grid, whose bytes
    key the river carve cache (``water_layers.river_carve_layers``).
    """
    im = np.array(im, dtype=dtype, copy=True)
    finite = np.isfinite(im)
    below = finite & (im < 0)
    im[below] = im[below] * depth_scale
    relief = float(np.ptp(im[finite])) if finite.any() else 0.0
    note = None
    if subtract_water:
        water, note = water_fraction_for_dem(bbox, im.shape, dataset=water_dataset,
                                             sat_scale=sat_scale, elevation=im)
        if water is not None:
            # In place, computed in float64 (water is float64) and stored as *dtype*.
            im -= water * relief * water_scale
    else:
        above = finite & (im > 0)
        im[above] = im[above] + relief * water_scale
    return im, note


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

    # Depth scale and water subtraction: the shared block (apply_water_and_depth).
    # float64 kept: this is the ``local`` layer's raw grid, and the river carve
    # cache is keyed by its bytes (float32 arithmetic would move the last bit).
    im, _note = apply_water_and_depth(
        result, (N, S, E, W), depth_scale=depth_scale, water_scale=water_scale,
        subtract_water=subtract_water, water_dataset=water_dataset, sat_scale=sat_scale,
        dtype=np.float64)

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
    subtract_water: bool = True,
    maintain_dimensions: bool = True,
) -> np.ndarray:
    """Fetch local SRTM elevation tiles and return as a float64 array.

    Water is subtracted by default (user, 2026-10-13: "that should be enabled by
    default"); pass ``subtract_water=False`` for elevation without it (plus the
    land step of :func:`apply_water_and_depth`).
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
    water_dataset: str = "esa",
    notes: list[str] | None = None,
    shared_dim: int | None = None,
) -> np.ndarray:
    """Fetch a plate-carrée DEM for *bbox* from any DEM source.

    Every source then goes through :func:`apply_water_and_depth` (depth scale,
    water subtraction) on the grid it returned, so ``subtract_water``,
    ``water_scale`` and ``depth_scale`` mean the same for local, h5 and
    OpenTopography (before 2026-10-13 only ``local`` honoured them). A note for
    the user (water only partly found) is appended to *notes* when given.

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
        shared_dim: the target (projected) dim when *dim* is one projection's
            source_dim. Then an OpenTopography grid is taken from
            :func:`shared_base_grid` (the GeoTIFF decoded once per box, not once per
            projection: Colorado's 352 MB file cost 4.7 s on every switch) when that
            grid covers this one, and the water mask is read at the shared grid's
            scale for every projection, so a switch reuses the cached ESA image
            instead of asking Earth Engine for a finer one (Norway: 7 s).
    """
    if isinstance(bbox, dict):
        north, south, east, west = bbox["north"], bbox["south"], bbox["east"], bbox["west"]
    else:
        north, south, east, west = bbox
    bbox_t = (north, south, east, west)
    sat_scale = None
    shared_shape = None
    if shared_dim is not None and not maintain_dimensions:
        from geo2stl.projections import shared_source_shape
        shared_shape = shared_source_shape(int(shared_dim), bbox_t)
        w_m, h_m = bbox_size_m({"north": north, "south": south, "east": east, "west": west})
        sat_scale = max(10, int(math.ceil(max(w_m, h_m, 1.0) / max(shared_shape))))

    def _finish(raw: np.ndarray) -> np.ndarray:
        im, note = apply_water_and_depth(
            raw, bbox_t, depth_scale=depth_scale, water_scale=water_scale,
            subtract_water=subtract_water, water_dataset=water_dataset,
            sat_scale=sat_scale)
        if note and notes is not None:
            notes.append(note)
        return im

    if source in OPENTOPO_DATASETS and shared_shape is not None:
        from geo2stl.projections import fit_grid, projected_shape, shared_covers
        shape = projected_shape(bbox_t, 'none', dim)
        if shared_covers(shared_shape, shape):
            base = shared_base_grid(source, north, south, east, west, int(shared_dim))
            return _finish(fit_grid(np.asarray(base, dtype=np.float32), shape))
    if source in ("h5_local", *OPENTOPO_DATASETS):
        return _finish(fetch_layer_data(source, north, south, east, west, dim,
                                        api_key=api_key, notes=notes))

    try:
        # Raw elevation at dim (no water, unit depth scale, no land step); the
        # shared block runs once below, as for every other source.
        raw = fetch_local_dem(
            north, south, east, west, dim,
            depth_scale=1.0, water_scale=0.0, subtract_water=False,
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
        return np.zeros((mh, mw), dtype=np.float32)
    return _finish(raw)


# ---------------------------------------------------------------------------
# h5_local: fill tiles missing from the store
# ---------------------------------------------------------------------------

#: (dataset, bbox) -> monotonic time of a failed OpenTopography fill. A failure is
#: not retried for this long: the API allows 50 calls a day and answers 401 when
#: they are used up, so a UI that re-requests a DEM must not burn one per request.
_FALLBACK_FAILED: dict[tuple, float] = {}
_FALLBACK_RETRY_S: float = 15 * 60


def _h5_fill_source(north, south, east, west, shape, api_key):
    """Elevation for the bbox at *shape* from the first source that answers, for
    the tiles the h5 store lacks. Returns ``(grid, label)``; raises RuntimeError
    with every source's reason when none does.

    Order: the local GEBCO folder (``config.json`` ``ocean_root``; no network),
    then OpenTopography SRTMGL3, or the coarser dataset its area cap routes a
    large box to (SRTM15Plus above 4.05M km2).
    """
    import time

    from geo2stl.opentopo import dataset_for_area
    from geo2stl.projections import fit_grid
    from geo2stl.tiles import get_tile_files

    reasons = []
    if get_tile_files():
        try:
            g = fetch_local_dem(north, south, east, west, max(shape), depth_scale=1.0,
                                water_scale=0.0, subtract_water=False)
            return (fit_grid(np.asarray(g, dtype=np.float32), shape),
                    DEM_SOURCE_INFO["local"]["label"])
        except Exception as exc:  # noqa: BLE001 - try the next source
            reasons.append(f"local tile store: {exc}")
    else:
        reasons.append("local tile store: no GeoTIFFs in config.json ocean_root")

    demtype = dataset_for_area("SRTMGL3", north, south, east, west)
    memo = (demtype, round(north, 5), round(south, 5), round(east, 5), round(west, 5))
    failed_at = _FALLBACK_FAILED.get(memo)
    if failed_at is not None and time.monotonic() - failed_at < _FALLBACK_RETRY_S:
        reasons.append(f"OpenTopography {demtype}: failed in the last "
                       f"{_FALLBACK_RETRY_S / 60:.0f} min, not retried")
    else:
        try:
            g = fetch_opentopo_dem(north, south, east, west, demtype=demtype,
                                   api_key=api_key, dim=max(shape))
            _FALLBACK_FAILED.pop(memo, None)
            return (fit_grid(np.asarray(g, dtype=np.float32), shape),
                    DEM_SOURCE_INFO[demtype]["label"])
        except Exception as exc:  # noqa: BLE001 - reported to the user below
            _FALLBACK_FAILED[memo] = time.monotonic()
            reasons.append(f"OpenTopography {demtype}: {exc}")
    raise RuntimeError("; ".join(reasons))


def _fetch_h5_with_fallback(north, south, east, west, dim, *, api_key=None, notes=None):
    """:func:`fetch_h5_dem`, with tiles missing from the store filled from
    :func:`_h5_fill_source`.

    A missing tile used to stay 0, so a box mostly outside the store came back as
    one flat plain with a few rectangles of relief (Amazon, 2026-10-14: 12 of 63
    tiles). When no fill source answers, the gap stays 0 and a note saying the
    coverage is incomplete goes to *notes* (the route shows it as
    ``source_note`` and does not cache the grid). SRTM has no bathymetry (sea is
    0), so fill values below 0 are raised to 0: a bathymetric fill (GEBCO,
    SRTM15Plus) would otherwise drop the sea kilometres at the tile seams.
    """
    cov: dict = {}
    # 2x the requested size, so the resize to dim still averages.
    grid = fetch_h5_dem(north, south, east, west,
                        max_px=2 * dim if dim else None, coverage=cov)
    missing = cov.get("missing_mask")
    if missing is None or not missing.any():
        return grid
    n_miss, n_need = len(cov["tiles_missing"]), len(cov["tiles_needed"])
    try:
        fill, label = _h5_fill_source(north, south, east, west, grid.shape, api_key)
    except RuntimeError as exc:
        msg = (f"Incomplete coverage: the local SRTM store lacks {n_miss} of {n_need} "
               f"tiles for this area, shown flat at sea level. No fallback source "
               f"answered ({exc}).")
        logger.warning(msg)
        if notes is not None:
            notes.append(msg)
        return grid
    fill = np.nan_to_num(np.maximum(fill.astype(np.float64), 0.0), nan=0.0)
    grid[missing] = fill[missing]
    logger.info("h5_local DEM: %d of %d tiles filled from %s", n_miss, n_need, label)
    return grid


# ---------------------------------------------------------------------------
# H5 tile constants (used by fetch_h5_dem and _geo_to_tile_pixel)
# ---------------------------------------------------------------------------

_H5_TILE_PX: int = 6000   # pixels per tile side
_H5_BAND_ROWS: int = 1024   # fetch_h5_dem reads each tile in row bands of about this size
#: SRTM's no-data value (voids: steep terrain, water); excluded from block means.
_SRTM_VOID: int = -32768
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
    coverage: dict | None = None,
) -> np.ndarray:
    """
    Read elevation from the local SRTM HDF5 tile store (strm_data.h5).

    The store holds only the tiles someone put in it (94 of the ~870 SRTM land
    tiles on the 2026-10-14 PC), so a large box can be mostly missing. Pass a
    dict as *coverage* to learn which: it receives ``tiles_needed`` (keys of the
    tiles the box overlaps), ``tiles_missing`` (those not in the store) and
    ``missing_mask`` (bool, output shape: cells inside a missing tile, which hold
    0). :func:`_fetch_h5_with_fallback` fills them from another source.

    The h5 file stores SRTM3 tiles at 6000Ã—6000 px per 5Â° tile (~90m/px).
    SRTM voids (-32768) are excluded; real below-sea-level ground stays negative.
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
    tiles_needed: list[str] = []
    tiles_missing: list[str] = []
    missing_mask = np.zeros((out_h, out_w), dtype=bool)
    with h5py.File(str(h5_file), "r") as fh:
        for ix, iy in _product(range(span_x), range(span_y)):
            key = f"srtm_{tx1 + ix:02d}_{ty1 + iy:02d}"
            r0, c0 = iy * _H5_TILE_PX, ix * _H5_TILE_PX
            if key not in fh:
                # Global rows / cols this tile would cover inside the box; a box edge
                # on a tile boundary touches the next tile with zero width.
                ga, gb = max(y1i, r0), min(y2i, r0 + _H5_TILE_PX)
                ca, cb = max(x1i, c0), min(x2i, c0 + _H5_TILE_PX)
                if ga < gb and ca < cb:
                    tiles_needed.append(key)
                    tiles_missing.append(key)
                    missing_mask[(ga - y1i) // step:-(-(gb - y1i) // step),
                                 (ca - x1i) // step:-(-(cb - x1i) // step)] = True
                    logger.debug(f"h5 tile missing: {key}")
                continue
            tiles_found += 1
            ds = fh[key]
            # Global rows / cols of this tile inside the box.
            ga, gb = max(y1i, r0), min(y2i, r0 + min(ds.shape[0], _H5_TILE_PX))
            ca, cb = max(x1i, c0), min(x2i, c0 + min(ds.shape[1], _H5_TILE_PX))
            if ga >= gb or ca >= cb:
                continue
            tiles_needed.append(key)
            ocol = (np.arange(ca, cb) - x1i) // step
            cstarts = np.flatnonzero(np.r_[True, ocol[1:] != ocol[:-1]])
            for ra in range(ga, gb, band):
                rb = min(gb, ra + band)
                raw = ds[ra - r0:rb - r0, ca - c0:cb - c0]
                valid = raw != _SRTM_VOID
                data = np.where(valid, raw, 0).astype(np.float64)
                orow = (np.arange(ra, rb) - y1i) // step
                rstarts = np.flatnonzero(np.r_[True, orow[1:] != orow[:-1]])
                sums = np.add.reduceat(np.add.reduceat(data, rstarts, axis=0), cstarts, axis=1)
                ns = np.add.reduceat(np.add.reduceat(valid.astype(np.float64), rstarts, axis=0),
                                     cstarts, axis=1)
                rr, cc = orow[rstarts][:, None], ocol[cstarts][None, :]
                total[rr, cc] += sums
                count[rr, cc] += ns

    if tiles_found == 0:
        raise FileNotFoundError(
            f"h5 file '{Path(h5_file).name}' contains no tiles covering "
            f"bbox ({north},{south},{east},{west})"
        )
    # Voids (-32768) are left out of each block's mean; a cell with no valid sample (a
    # missing tile, an all-void block) stays 0, as the old mosaic did. Real negatives
    # (Dead Sea, polders) are kept: they used to be floored at 0 together with the
    # voids (PA-15); the caller scales sub-zero values by depth_scale as for the other
    # sources.
    cropped = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
    # A block straddling a present and a missing tile has real samples: not missing.
    missing_mask &= count == 0
    if coverage is not None:
        coverage.update(tiles_needed=tiles_needed, tiles_missing=tiles_missing,
                        missing_mask=missing_mask)
    if tiles_missing:
        logger.warning("h5_local DEM: %d of %d tiles missing from %s: %s",
                       len(tiles_missing), len(tiles_needed), Path(h5_file).name,
                       ", ".join(tiles_missing))
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
