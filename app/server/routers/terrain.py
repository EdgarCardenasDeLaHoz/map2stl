"""
Terrain / elevation routes: DEM preview, water mask, raw DEM, merge, sources.

All heavy lifting is in core.dem and core.cache; this module is a thin
HTTP adapter that parses requests, delegates, and formats responses.
"""

import asyncio
import logging
import math

import numpy as np
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.server.config import (
    H5_SRTM_AVAILABLE as _H5_SRTM_AVAILABLE,
)
from app.server.config import (
    OPENTOPO_DATASETS,
    TEST_MODE,
)
from app.server.core.cache import make_cache_key, read_array_cache, write_array_cache
from app.server.core.dem_cache import dem_cache_key
from app.server.core.dem_store import dem_store
from app.server.core.inflight import dedupe
from app.server.core.responses import error_response
from app.server.core.validation import (
    BboxQueryParams,
    run_sync,
)
from app.server.core.validation import (
    b64_encode as _b64,
)
from app.server.core.validation import (
    parse_bbox_query as _parse_bbox_query,
)
from app.server.core.validation import (
    parse_bool as _parse_bool,
)
from app.server.core.validation import (
    parse_float as _parse_float,
)
from app.server.core.validation import (
    parse_int as _parse_int,
)
from app.server.core.validation import (
    validate_bbox as _validate_bbox,
)
from app.server.core.validation import (
    validate_dim as _validate_dim,
)
from geo2stl import opentopo as _opentopo
from geo2stl.dem import (
    DEM_SOURCE_INFO,
    default_dem_source,
    dem_sampling,
)
from geo2stl.dem import (
    fetch_dem as _fetch_dem,
)
from geo2stl.dem import (
    make_dem_payload as _make_dem_payload,
)
from geo2stl.geo import bbox_size_m
from geo2stl.hydrology import (
    fetch_and_rasterize_hydrology as _fetch_and_rasterize_hydrology,
)
from geo2stl.processing import (
    upsample_dem as _upsample_dem,
)
from geo2stl.projections import (
    project_grid as _project_grid_impl,
)
from geo2stl.projections import (
    project_rgb_image as _project_rgb_image,
)
from geo2stl.projections import (
    project_water_arrays as _project_water_arrays_impl,
)
from geo2stl.sat2stl import (
    fetch_sat_overlay as _fetch_sat_overlay,
)
from geo2stl.sat2stl import (
    fetch_satellite_tiles as _fetch_satellite_tiles,
)
from geo2stl.sat2stl import (
    fetch_water_mask as _fetch_water_mask,
)
from geo2stl.sat2stl import (
    fetch_water_mask_images as _fetch_water_mask_images,
)
from geo2stl.trails import (
    SKI_DIFFICULTY_CLASSES,
    TrailsUpstreamError,
)
from geo2stl.trails import (
    fetch_and_rasterize_trails as _fetch_and_rasterize_trails,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["terrain"])

# In-flight request dedupe registries (see app.server.core.inflight.dedupe):
# cache_key -> asyncio.Future of the JSONResponse payload dict.
_HYDRO_INFLIGHT: dict[str, "asyncio.Future"] = {}
_TRAILS_INFLIGHT: dict[str, "asyncio.Future"] = {}


# ---------------------------------------------------------------------------
# Sync compute helpers (called via run_in_executor to avoid blocking the loop)
# ---------------------------------------------------------------------------


def _project_grid(arr, north, south, east, west, projection, clip_valid_region,
                  categorical=False, maintain_dimensions=False):
    """Apply geo2stl projection to a 2-D array. Sync helper.

    Delegates to core.projection.project_grid — kept as a thin wrapper
    so existing call-sites in this module do not change.
    """
    return _project_grid_impl(arr, north, south, east, west, projection,
                              clip_valid_region, categorical=categorical,
                              maintain_dimensions=maintain_dimensions)


def _project_water_arrays(water_mask, esa_img, north, south, east, west,
                          projection, clip_valid_region, maintain_dimensions=False):
    """Project both water mask and ESA arrays to keep them aligned.

    Delegates to core.projection.project_water_arrays.
    """
    return _project_water_arrays_impl(water_mask, esa_img, north, south,
                                      east, west, projection, clip_valid_region,
                                      maintain_dimensions=maintain_dimensions)


def _fetch_dem_array(dem_source, north, south, east, west, dim,
                     depth_scale, water_scale, subtract_water, maintain_dimensions):
    """Fetch a plate-carrée DEM array. Sync — call via run_in_executor.

    Source routing, the h5 -> SRTMGL3 fallback and the zero array on a local
    failure (detected by _dem_empty_warning) are ``geo2stl.dem.fetch_dem``;
    projection is applied by the caller.
    """
    return _fetch_dem((north, south, east, west), dim, dem_source,
                      depth_scale=depth_scale, water_scale=water_scale,
                      subtract_water=subtract_water,
                      maintain_dimensions=maintain_dimensions)


def _dem_empty_warning(im: np.ndarray) -> str | None:
    """Return a user-facing warning if the DEM has no usable elevation data.

    A flat (all-equal, incl. all-zero) or all-NaN array means the source had
    no coverage for this bbox — the local fallback returns zeros in that case.
    Returns None when the DEM contains real relief.
    """
    if im is None or im.size == 0:
        return "The DEM for this region is empty."
    finite = im[np.isfinite(im)]
    if finite.size == 0 or float(np.ptp(finite)) == 0.0:
        return (
            "No elevation data covers this region (the DEM is flat). Try a "
            "smaller area, or switch the DEM source to an OpenTopography "
            "dataset — that needs a free API key, which you can add in the "
            "\U0001f511 Keys panel."
        )
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.api_route("/api/terrain/dem", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_dem(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: int | None = Query(
        None, description="Output grid resolution (pixels per side)"),
    depth_scale: float | None = Query(
        None, description="Depth scaling factor for ocean/bathymetry"),
    water_scale: float | None = Query(
        None, description="Water subtraction strength"),
    subtract_water: bool | None = Query(
        None, description="Subtract water bodies from terrain"),
    show_sat: bool | None = Query(
        None, description="Include ESA land-use overlay in response"),
    dataset: str | None = Query(
        None, description="Land-use dataset: 'esa' or 'jrc'"),
    projection: str | None = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    maintain_dimensions: bool | None = Query(
        None, description="Maintain output dimensions after projection"),
    clip_valid_region: bool | None = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    dem_source: str | None = Query(
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
    # 'none' matches every other terrain endpoint's default (water-mask, esa,
    # satellite, hydrology) — this endpoint previously defaulted to 'cosine'
    # alone, a real cross-layer inconsistency masked until now by
    # maintain_dimensions=True forcing every endpoint back to the same (m,n)
    # regardless of which projection was silently applied (F-PROJ-DIMS audit).
    projection = params.get("projection", "none")
    maintain_dimensions = _parse_bool(params, "maintain_dimensions", False)
    clip_valid_region = _parse_bool(params, "clip_valid_region", True)
    dem_source = params.get("dem_source", "local")

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    logger.debug(
        f"GET /api/terrain/dem north={north} south={south} east={east} "
        f"west={west} dim={dim} show_sat={show_sat}")

    # --- DEM disk cache check ---
    # The key is built by core/dem_cache.py, which the export path also calls —
    # the two must agree exactly or a settings-only export fails with
    # "Missing DEM data". Do not inline the key here again.
    # Note: the key deliberately excludes projection and clip_valid_region.
    # Raw data is cached once per bbox; projection/clipping applied on fetch.
    _dem_cache_key = dem_cache_key(north, south, east, west, {
        "dim": dim,
        "dem_source": dem_source,
        "depth_scale": depth_scale,
        "water_scale": water_scale,
        "subtract_water": subtract_water,
        "maintain_dimensions": maintain_dimensions,
        "show_sat": show_sat,
    })
    _cached = read_array_cache("dem", _dem_cache_key)
    im_raw = None
    from_cache = False
    if _cached is not None and _cached[0].get("dem") is not None:
        logger.info(f"DEM cache hit: {_dem_cache_key[:8]}...")
        im_raw = _cached[0]["dem"].astype(np.float32, copy=False)
        from_cache = True

    # TEST_MODE: return deterministic gradient without network I/O
    if TEST_MODE:
        im_raw_test = np.linspace(0, 100, num=(dim * dim),
                                  dtype=float).reshape((dim, dim))
        # Write to the same disk cache the real path uses (raw/unprojected),
        # so the settings-only export/preview path — which resolves the DEM by
        # reconstructing this cache key — also works under TEST_MODE. Without
        # this, e2e tests that load a DEM then build the Extrude preview would
        # always miss the cache and get "Missing DEM data" in test mode only.
        if not show_sat and not from_cache:
            write_array_cache(
                "dem", _dem_cache_key,
                {"dem": im_raw_test.astype(np.float32, copy=False)},
                {"min_elevation": float(im_raw_test.min()),
                 "max_elevation": float(im_raw_test.max()),
                 "mean_elevation": float(im_raw_test.mean()),
                 "bbox": [west or 0.0, south or 0.0, east or 0.0, north or 0.0],
                 "shape": list(im_raw_test.shape)})

        im = im_raw_test
        # Apply projection even in TEST_MODE so tests exercise the full pipeline
        if projection != "none":
            im = _project_grid(
                im.astype(np.float32), north or 0.0, south or 0.0,
                east or 0.0, west or 0.0,
                projection, clip_valid_region, categorical=False,
                maintain_dimensions=maintain_dimensions,
            )
        payload = _make_dem_payload(im, west or 0.0, south or 0.0,
                                    east or 0.0, north or 0.0, show_sat=False)
        payload["sat_available"] = False
        payload["dem_id"] = dem_store.put(im, {"north": north, "south": south,
                                               "east": east, "west": west})
        payload["source_resolution"] = dem_sampling(
            dem_source, (north or 0.0, south or 0.0, east or 0.0, west or 0.0),
            payload["dimensions"])
        return JSONResponse(content=payload)

    # Guard: bbox already validated above but south/north could be None only in edge cases
    if north is None or south is None:
        south, north = -0.01, 0.01
    if east is None or west is None:
        west, east = -0.01, 0.01

    try:
        if im_raw is None:
            im_raw = await run_sync(_fetch_dem_array, dem_source,
                                    north, south, east, west, dim,
                                    depth_scale, water_scale,
                                    subtract_water, maintain_dimensions)
            im_raw = _upsample_dem(im_raw, dim)

        # Apply projection uniformly for ALL sources.
        # All fetch functions now return Plate Carrée data;
        # projection is applied here as a single external step.
        im = im_raw
        if projection != "none":
            im = _project_grid(
                im_raw.astype(np.float32), north, south, east, west,
                projection, clip_valid_region, categorical=False,
                maintain_dimensions=maintain_dimensions,
            )

        response_content = _make_dem_payload(
            im, west, south, east, north, show_sat)
        height_px, width_px = response_content["dimensions"]
        # Handle for the exact grid returned (projected + clipped): export asks for
        # it by id instead of re-deriving the cache key (core/dem_store.py).
        response_content["dem_id"] = dem_store.put(
            im, {"north": north, "south": south, "east": east, "west": west})

        # Pre-projection grid size. Any other layer that must line up with this
        # DEM has to be rasterized at THIS size and then projected once — feeding
        # the already-projected dimensions back in projects the data twice, which
        # both shrinks the grid again and shears its contents relative to the DEM.
        response_content["source_dimensions"] = [
            int(im_raw.shape[0]), int(im_raw.shape[1])]

        # Native resolution of the source and how many real samples span the box,
        # against the grid returned: a 2 km box on SRTM 30 m is ~65 samples across,
        # so a 600 px grid is mostly interpolation (F-UX batch 2).
        response_content["source_resolution"] = dem_sampling(
            dem_source, (north, south, east, west), response_content["dimensions"])

        # Flag DEMs that came back with no real relief (source had no coverage
        # for this bbox) so the client can warn instead of showing a flat map.
        _empty_warning = _dem_empty_warning(im)
        if _empty_warning:
            response_content["dem_empty"] = True
            response_content["dem_warning"] = _empty_warning

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

        if from_cache:
            response_content["from_cache"] = True

        # Write DEM disk cache (skip when satellite overlay is embedded)
        # Cache raw (unprojected) DEM so projection/clip toggles are honored per request.
        # An empty DEM is a failure, not a result: caching it would keep serving
        # the flat map after the underlying cause is fixed (a repointed tile
        # folder, a newly added API key), and the fix would look like it did
        # nothing until the cache was cleared by hand.
        if not show_sat and not from_cache and not _empty_warning:
            im_clean = im_raw.astype(np.float32, copy=False)
            write_array_cache(
                "dem", _dem_cache_key,
                {"dem": im_clean},
                {"min_elevation": float(np.nanmin(im_raw)),
                 "max_elevation": float(np.nanmax(im_raw)),
                 "mean_elevation": float(np.nanmean(im_raw)),
                 "bbox": [west, south, east, north],
                 "shape": list(im_clean.shape)})

        return JSONResponse(content=response_content)

    except Exception as e:
        logger.error(f"Error in get_terrain_dem: {e}", exc_info=True)
        msg = str(e)
        # OpenTopography rejects requests whose bbox exceeds the per-dataset
        # area cap (e.g. SRTMGL3 = 4,050,000 km2). Surface that as a clear,
        # actionable 400 instead of a generic 500 so the user knows to shrink
        # the region or pick a coarser dataset.
        if "maximum area" in msg or "OpenTopography API error" in msg:
            detail = (
                "This region is too large for the selected OpenTopography "
                "dataset. Choose a smaller area, or use a coarser dataset "
                "(e.g. SRTM15+/COP90)."
            )
            return error_response(f"{detail} (source said: {msg[:200]})", status=400)
        return error_response("DEM processing failed")


@router.api_route("/api/terrain/water-mask", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_water_mask(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: int | None = Query(
        None, description="Output grid resolution (pixels per side)"),
    dataset: str | None = Query(
        None, description="Water dataset: 'esa' or 'jrc'"),
    projection: str | None = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_valid_region: bool | None = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    maintain_dimensions: bool | None = Query(
        None, description="Maintain output dimensions after projection"),
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
        clip_valid_region = _parse_bool(params, "clip_valid_region", True)
        maintain_dimensions = _parse_bool(params, "maintain_dimensions", False)

        err = _validate_bbox(north, south, east, west)
        if err:
            return err

        # Derive sat_scale (m/px) from requested dim and bbox size.
        # Scale clamping (50 MB / 32768 px limits) is handled inside fetch_water_mask.
        _longer_m = max(*bbox_size_m({"north": north or 0.0, "south": south or 0.0,
                                      "east": east or 0.0, "west": west or 0.0}), 1.0)
        sat_scale = max(10, int(math.ceil(_longer_m / dim)))

        # --- Water mask disk cache check ---
        # Note: Cache key does NOT include projection or clip_valid_region.
        # Raw data is cached once per bbox; projection/clipping applied per request.
        _water_cache_key = make_cache_key("water", north, south, east, west, {
            "dim": dim, "ds": water_dataset})
        _wc = read_array_cache("water", _water_cache_key)
        if _wc is not None:
            _warr, _wmeta = _wc
            _wm = _warr.get("water_mask")
            _esa = _warr.get("esa")
            if _wm is not None and _esa is not None:
                logger.info(f"Water mask cache hit: {_water_cache_key[:8]}...")
                if projection != "none":
                    _wm, _esa = _project_water_arrays(
                        _wm.astype(np.float32), _esa.astype(np.float32),
                        north, south, east, west, projection, clip_valid_region,
                        maintain_dimensions=maintain_dimensions)
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
                    north, south, east, west, projection, clip_valid_region,
                    maintain_dimensions=maintain_dimensions)
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

        write_array_cache("water", _water_cache_key,
                          {"water_mask": water_mask.astype(np.float32),
                           "esa": img.astype(np.float32)},
                          {"shape": [h, w]})

        # Apply projection if requested
        if projection != "none":
            water_mask, img = _project_water_arrays(
                water_mask, img, north, south, east, west, projection, clip_valid_region,
                maintain_dimensions=maintain_dimensions)
            h, w = water_mask.shape
            water_pixels = int(np.sum(water_mask > 0.5))
            total_pixels = h * w

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
    dim: int | None = Query(
        None, description="Output grid resolution (pixels per side)"),
    projection: str | None = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_valid_region: bool | None = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    maintain_dimensions: bool | None = Query(
        None, description="Maintain output dimensions after projection"),
):
    """Fetch ESA WorldCover land-cover class data independently of the water mask."""
    logger.info("Received request for /api/terrain/esa-land-cover")
    try:
        params = request.query_params
        north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
        dim = _parse_int(params, "dim", 600)
        projection = params.get("projection", "none")
        clip_valid_region = _parse_bool(params, "clip_valid_region", True)
        maintain_dimensions = _parse_bool(params, "maintain_dimensions", False)

        err = _validate_bbox(north, south, east, west)
        if err:
            return err

        # Derive sat_scale from requested dim and bbox size.
        _longer_m = max(*bbox_size_m({"north": north or 0.0, "south": south or 0.0,
                                      "east": east or 0.0, "west": west or 0.0}), 1.0)
        sat_scale = max(10, int(math.ceil(_longer_m / dim)))

        # Cache key does NOT include projection/clip_valid_region/maintain_dimensions —
        # raw (unprojected) data is cached once per bbox; projection is (re-)applied
        # on every request, including cache hits, so switching projection or
        # maintain_dimensions for an already-cached bbox reflects the new setting
        # instead of silently returning a stale shape from a prior request.
        _esa_cache_key = make_cache_key("esa_lc", north, south, east, west, {
            "dim": dim})
        _ec = read_array_cache("esa_lc", _esa_cache_key)
        if _ec is not None:
            _earr, _emeta = _ec
            _esa_raw = _earr.get("esa")
            if _esa_raw is not None:
                logger.info(
                    f"ESA land cover cache hit: {_esa_cache_key[:8]}...")
                _esa = _esa_raw
                if projection != "none":
                    _esa = _project_grid(
                        _esa_raw.astype(np.float32), north, south, east, west,
                        projection, clip_valid_region, categorical=True,
                        maintain_dimensions=maintain_dimensions)
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
                    projection, clip_valid_region, categorical=True,
                    maintain_dimensions=maintain_dimensions)
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
        bbox_w_m, bbox_h_m = bbox_size_m(
            {"north": north, "south": south, "east": east, "west": west})
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

        # Cache the RAW (unprojected) fetch — projection is applied fresh
        # below, after the write, so it never gets baked into the cached
        # value (see the cache-hit path above for why: baking it in made
        # a later request with a different projection/maintain_dimensions
        # silently return the first request's shape).
        img = img.astype(np.float32)
        write_array_cache("esa_lc", _esa_cache_key,
                          {"esa": img},
                          {"shape": list(img.shape)})

        if projection != "none":
            img = _project_grid(img, north, south, east, west,
                                projection, clip_valid_region, categorical=True,
                                maintain_dimensions=maintain_dimensions)

        h, w = img.shape

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
    dim: int | None = Query(
        None, description="Output image resolution (pixels per side)"),
    projection: str | None = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_valid_region: bool | None = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    maintain_dimensions: bool | None = Query(
        None, description="Maintain output dimensions after projection"),
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
    clip_valid_region = _parse_bool(params, "clip_valid_region", True)
    maintain_dimensions = _parse_bool(params, "maintain_dimensions", False)

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    if TEST_MODE:
        import base64
        from io import BytesIO

        from PIL import Image
        img = Image.new("RGB", (dim, dim), color=(80, 120, 60))
        # Apply projection even in TEST_MODE
        if projection != "none":
            img_arr = np.array(img)
            projected = _project_rgb_image(
                img_arr, north, south, east, west, projection, clip_valid_region,
                maintain_dimensions=maintain_dimensions)
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
                north, south, east, west, projection, clip_valid_region,
                maintain_dimensions)

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
    # The local tile store is only "available" if it actually has tiles: with an
    # empty or missing folder the fetch quietly returns zeros and exports a flat
    # slab, so reporting it as ready is worse than reporting nothing at all.
    from app.server.core import tile_store
    local_status = tile_store.status()
    # resolution_m is kept for older clients; native_resolution_m comes from
    # geo2stl.dem.DEM_SOURCE_INFO, the table the DEM response's sampling uses.
    sources = [
        {"id": "local", "label": "Local SRTM Tiles", "provider": "local",
         "resolution_m": DEM_SOURCE_INFO["local"]["native_resolution_m"],
         "native_resolution_m": DEM_SOURCE_INFO["local"]["native_resolution_m"],
         "requires_api_key": False,
         "available": local_status["available"], "note": local_status["note"]},
        {"id": "h5_local", "label": "Local SRTM H5 (City-scale, ~90m)",
         "provider": "local_h5", "resolution_m": 90,
         "native_resolution_m": DEM_SOURCE_INFO["h5_local"]["native_resolution_m"],
         "requires_api_key": False, "available": _H5_SRTM_AVAILABLE,
         "note": "High-fidelity SRTM3 from local strm_data.h5 — best for regions < 15 km."},
    ]
    has_key = bool(_opentopo.get_api_key())
    for demtype, info in OPENTOPO_DATASETS.items():
        sources.append({
            "id": demtype, "label": info["label"], "provider": "OpenTopography",
            "resolution_m": info["resolution_m"],
            "native_resolution_m": DEM_SOURCE_INFO[demtype]["native_resolution_m"],
            "requires_api_key": True, "available": has_key,
        })
    return JSONResponse(content={
        "sources": sources,
        "default_source": default_dem_source(has_key, _H5_SRTM_AVAILABLE),
        "opentopo_api_key_configured": has_key,
        "h5_srtm_available": _H5_SRTM_AVAILABLE,
        "local_tile_store": local_status,
    })


# ---------------------------------------------------------------------------
# Hydrology endpoints
# ---------------------------------------------------------------------------

@router.get("/api/terrain/hydrology", tags=["terrain"])
async def get_terrain_hydrology(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: int | None = Query(
        None, description="Output grid resolution (pixels per side)"),
    depression_m: float | None = Query(
        None, description="Max river depression in metres (negative, default -5.0)"),
    source: str | None = Query(
        None, description="River data source: 'natural_earth' or 'hydrorivers'"),
    scale_m: int | None = Query(
        None, description="Natural Earth dataset tier: 10 (finest), 50, or 110"),
    min_order: int | None = Query(
        None, description="HydroRIVERS minimum Strahler order 1-9 (1=all, 9=major only)"),
    order_exponent: float | None = Query(
        None, description="HydroRIVERS depression scaling exponent"),
    width_factor: float | None = Query(
        None, description="HydroRIVERS line width multiplier (default 1.0; higher = thicker rivers)"),
    projection: str | None = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_valid_region: bool | None = Query(
        None,
        description="Clip projection padding to valid data extent (recommended).",
    ),
    maintain_dimensions: bool | None = Query(
        None, description="Maintain output dimensions after projection"),
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
    clip_valid_region = _parse_bool(params, "clip_valid_region", True)
    maintain_dimensions = _parse_bool(params, "maintain_dimensions", False)

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    logger.debug(f"GET /api/terrain/hydrology bbox=({north},{south},{east},{west}) "
                 f"dim={dim} source={source} depression={depression_m}")

    # Cache key covers every parameter that affects the rasterized output.
    # Cache key does NOT include projection/clip_valid_region/maintain_dimensions —
    # raw (unprojected) data is cached once per bbox; projection is (re-)applied
    # on every request, including cache hits, so switching projection or
    # maintain_dimensions for an already-cached bbox reflects the new setting
    # instead of silently returning whichever projection was baked in first.
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
            if projection != "none":
                river_grid = _project_grid(
                    river_grid, north, south, east, west, projection, clip_valid_region,
                    categorical=False, maintain_dimensions=maintain_dimensions)
                river_grid = np.nan_to_num(river_grid, nan=0.0)
            h, w = river_grid.shape
            logger.info("Hydrology cache hit: %s (%dx%d)", cache_key[:8], w, h)
            return JSONResponse(content={
                "river_grid_values_b64": _b64(river_grid),
                "river_grid_dimensions": [h, w],
                "feature_count": int(meta.get("feature_count", 0)),
                "source": meta.get("source", source),
                "depression_m": depression_m,
            })

    if TEST_MODE:
        h, w = dim, dim
        river_arr = np.zeros((h, w), dtype=np.float32)
        river_arr[h//4:h//3, w//4:3*w//4] = depression_m
        # Apply projection even in TEST_MODE
        if projection != "none":
            river_arr = _project_grid(
                river_arr, north, south, east, west,
                projection, clip_valid_region, categorical=False,
                maintain_dimensions=maintain_dimensions)
            river_arr = np.nan_to_num(river_arr, nan=0.0)
            h, w = river_arr.shape
        return JSONResponse(content={
            "river_grid_values_b64": _b64(river_arr),
            "river_grid_dimensions": [h, w],
            "feature_count": 5,
            "source": source,
            "depression_m": depression_m,
        })

    # In-flight dedupe: if an identical request is already running, await its result
    # instead of starting a duplicate ~150-second pipeline.
    async def _compute() -> dict:
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
            return payload

        # Cache the RAW (unprojected) river grid — projection is applied fresh
        # below (and on every future cache hit above), never baked into the
        # cached value. See the cache-hit path's comment for why.
        river_grid_raw = result["river_grid"]
        write_array_cache(
            "hydrology", cache_key,
            {"river_grid": river_grid_raw},
            {"feature_count": int(result["feature_count"]),
             "source": result.get("source", source)},
        )

        river_grid = river_grid_raw
        # Apply projection if requested
        if projection != "none":
            river_grid = _project_grid(
                river_grid, north, south, east, west, projection, clip_valid_region,
                categorical=False, maintain_dimensions=maintain_dimensions)
            # Replace NaN fill (from projection) with 0 (= no river) so JSON
            # serialisation produces 0.0 instead of null.
            river_grid = np.nan_to_num(river_grid, nan=0.0)

        h, w = river_grid.shape

        payload = {
            "river_grid_values_b64": _b64(river_grid),
            "river_grid_dimensions": [h, w],
            "feature_count": result["feature_count"],
            "source": result.get("source", source),
            "depression_m": depression_m,
        }

    if cache_key in _HYDRO_INFLIGHT:
        logger.info("Hydrology in-flight join: %s", cache_key[:8])
    try:
        payload = await dedupe(_HYDRO_INFLIGHT, cache_key, _compute)
    except Exception as e:
        logger.error(f"Error in get_terrain_hydrology: {e}", exc_info=True)
        return error_response("Hydrology fetch failed")
    return JSONResponse(content=payload)


# ---------------------------------------------------------------------------
# Trails — ski pistes and hiking paths
# ---------------------------------------------------------------------------

@router.get("/api/terrain/trails", tags=["terrain"])
async def get_terrain_trails(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: int | None = Query(
        None, description="Output grid resolution (pixels per side)"),
    relief_m: float | None = Query(
        None, description="Signed trail relief in metres (negative engraves, default -2.0)"),
    width_m: float | None = Query(
        None, description="Rendered trail width in metres (default 8, floored at 2 px)"),
    source: str | None = Query(
        None, description="Trail source: 'osm', 'usfs', or 'all' (default)"),
    categories: str | None = Query(
        None, description="Comma-separated categories to fetch: 'ski', 'hiking'"),
    projection: str | None = Query(
        None, description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"),
    clip_valid_region: bool | None = Query(
        None, description="Clip projection padding to valid data extent (recommended)."),
    maintain_dimensions: bool | None = Query(
        None, description="Maintain output dimensions after projection"),
):
    """
    Fetch ski and hiking trails and rasterize each category to its own relief grid.

    Both categories come back in one response even when the client is showing only
    one of them, so toggling ski/hiking in the UI is a repaint rather than a second
    Overpass round trip.

    Query parameters:
        north, south, east, west: bounding box
        dim:        output grid resolution (pixels per side, default 600)
        relief_m:   signed relief in metres; negative engraves (default -2.0)
        width_m:    rendered trail width in metres (default 8.0)
        source:     'osm' (global, both categories), 'usfs' (US hiking only),
                    or 'all' (default, union of both)
        categories: comma-separated subset of 'ski,hiking' to fetch
    """
    params = request.query_params

    north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
    dim = _parse_int(params, "dim", 600)
    relief_m = _parse_float(params, "relief_m", -2.0)
    width_m = _parse_float(params, "width_m", 8.0)
    width_m = max(1.0, min(500.0, width_m))

    source = params.get("source", "all")
    if source not in ("osm", "usfs", "all"):
        source = "all"

    raw_categories = params.get("categories", "ski,hiking")
    categories = tuple(
        c for c in (p.strip() for p in raw_categories.split(","))
        if c in ("ski", "hiking")
    ) or ("ski", "hiking")

    projection = params.get("projection", "none")
    clip_valid_region = _parse_bool(params, "clip_valid_region", True)
    maintain_dimensions = _parse_bool(params, "maintain_dimensions", False)

    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err

    logger.debug(f"GET /api/terrain/trails bbox=({north},{south},{east},{west}) "
                 f"dim={dim} source={source} categories={categories}")

    def _project_many(*grids, categorical=False):
        """Apply the requested projection to every grid, or return them unchanged.

        ``categorical`` switches the resampler to nearest-neighbour, which the
        difficulty grid needs: its values are class indices, and interpolating
        between "easy" and "expert" would invent a grade that is neither.
        """
        if projection == "none":
            return list(grids)
        out = []
        for grid in grids:
            g = _project_grid(
                grid, north, south, east, west, projection, clip_valid_region,
                categorical=categorical, maintain_dimensions=maintain_dimensions)
            # Projection pads with NaN; here 0 is the "no trail" value.
            out.append(np.nan_to_num(g, nan=0.0))
        return out

    # Cache key covers every parameter that changes the rasterized output.
    # Projection is deliberately excluded and re-applied per request, matching the
    # hydrology endpoint: switching projection must not force a refetch. Categories
    # are excluded too — both grids are always computed, so an entry written for
    # one category request serves the other as well.
    cache_extra = {"dim": dim, "src": source, "rel": relief_m, "wid": width_m}
    cache_key = make_cache_key("trails", north, south, east, west, cache_extra)
    cached = read_array_cache("trails", cache_key)
    if cached is not None:
        arrs, meta = cached
        # Every grid must be present. The area masks and the difficulty grid were
        # each added after the first entries were written, and an entry missing
        # one is served as a miss rather than backfilled with zeros: an all-zero
        # difficulty grid is indistinguishable from a resort whose pistes carry
        # no grades, so the degradation would be a silent wrong answer. The cost
        # is one refetch per stale entry.
        ski = arrs.get("ski_grid")
        hiking = arrs.get("hiking_grid")
        ski_area = arrs.get("ski_area_grid")
        hiking_area = arrs.get("hiking_area_grid")
        ski_difficulty = arrs.get("ski_difficulty_grid")
        if all(a is not None for a in
               (ski, hiking, ski_area, hiking_area, ski_difficulty)):
            ski, hiking, ski_area, hiking_area = _project_many(
                ski, hiking, ski_area, hiking_area)
            ski_difficulty, = _project_many(ski_difficulty, categorical=True)
            h, w = ski.shape
            logger.info("Trails cache hit: %s (%dx%d)", cache_key[:8], w, h)
            ski_count = int(meta.get("ski_count", 0))
            hiking_count = int(meta.get("hiking_count", 0))
            return JSONResponse(content={
                "ski_grid_values_b64": _b64(ski),
                "hiking_grid_values_b64": _b64(hiking),
                "ski_area_grid_values_b64": _b64(ski_area),
                "hiking_area_grid_values_b64": _b64(hiking_area),
                "ski_difficulty_grid_values_b64": _b64(ski_difficulty),
                "difficulty_classes": list(SKI_DIFFICULTY_CLASSES),
                "grid_dimensions": [h, w],
                "ski_count": ski_count,
                "hiking_count": hiking_count,
                "feature_count": ski_count + hiking_count,
                "sources": meta.get("sources", []),
                "source": source,
                "relief_m": relief_m,
            })

    if TEST_MODE:
        ski = np.zeros((dim, dim), dtype=np.float32)
        hiking = np.zeros((dim, dim), dtype=np.float32)
        ski[dim // 3, :] = relief_m
        hiking[:, dim // 3] = relief_m
        ski_area = np.zeros((dim, dim), dtype=np.float32)
        hiking_area = np.zeros((dim, dim), dtype=np.float32)
        ski_difficulty = np.zeros((dim, dim), dtype=np.uint8)
        ski_difficulty[dim // 3, :] = 3          # one "intermediate" run
        ski, hiking, ski_area, hiking_area = _project_many(
            ski, hiking, ski_area, hiking_area)
        ski_difficulty, = _project_many(ski_difficulty, categorical=True)
        h, w = ski.shape
        return JSONResponse(content={
            "ski_grid_values_b64": _b64(ski),
            "hiking_grid_values_b64": _b64(hiking),
            "ski_area_grid_values_b64": _b64(ski_area),
            "hiking_area_grid_values_b64": _b64(hiking_area),
            "ski_difficulty_grid_values_b64": _b64(ski_difficulty),
            "difficulty_classes": list(SKI_DIFFICULTY_CLASSES),
            "grid_dimensions": [h, w],
            "ski_count": 1,
            "hiking_count": 1,
            "feature_count": 2,
            "sources": ["osm"],
            "source": source,
            "relief_m": relief_m,
        })

    # In-flight dedupe: Overpass trail queries are slow, and the layer can be asked
    # for by the load button and the layer auto-load at the same moment.
    async def _compute() -> dict:
        try:
            result = await run_sync(
                _fetch_and_rasterize_trails,
                north, south, east, west, dim,
                relief_m, width_m, source, categories)

            if result is None:
                payload = {
                    "ski_grid_values_b64": None,
                    "hiking_grid_values_b64": None,
                    "ski_area_grid_values_b64": None,
                    "hiking_area_grid_values_b64": None,
                    "ski_difficulty_grid_values_b64": None,
                    "difficulty_classes": list(SKI_DIFFICULTY_CLASSES),
                    "grid_dimensions": [dim, dim],
                    "ski_count": 0,
                    "hiking_count": 0,
                    "feature_count": 0,
                    "sources": [],
                    "source": source,
                    "relief_m": relief_m,
                    "error": "No trails found in region",
                }
                return payload

            # Cache the RAW (unprojected) grids; projection is applied fresh below and
            # on every later cache hit.
            write_array_cache(
                "trails", cache_key,
                {"ski_grid": result["ski_grid"], "hiking_grid": result["hiking_grid"],
                 "ski_area_grid": result["ski_area_grid"],
                 "hiking_area_grid": result["hiking_area_grid"],
                 "ski_difficulty_grid": result["ski_difficulty_grid"]},
                {"ski_count": int(result["ski_count"]),
                 "hiking_count": int(result["hiking_count"]),
                 "sources": result.get("sources", [])},
            )

            ski, hiking, ski_area, hiking_area = _project_many(
                result["ski_grid"], result["hiking_grid"],
                result["ski_area_grid"], result["hiking_area_grid"])
            ski_difficulty, = _project_many(
                result["ski_difficulty_grid"], categorical=True)
            h, w = ski.shape

            payload = {
                "ski_grid_values_b64": _b64(ski),
                "hiking_grid_values_b64": _b64(hiking),
                "ski_area_grid_values_b64": _b64(ski_area),
                "hiking_area_grid_values_b64": _b64(hiking_area),
                # Encoded as float32 like every other grid so the client reuses one
                # decoder; the values are small integers, so the cast is exact.
                "ski_difficulty_grid_values_b64": _b64(ski_difficulty),
                "difficulty_classes": list(SKI_DIFFICULTY_CLASSES),
                "grid_dimensions": [h, w],
                "ski_count": result["ski_count"],
                "hiking_count": result["hiking_count"],
                "feature_count": result["feature_count"],
                "sources": result.get("sources", []),
                "source": source,
                "relief_m": relief_m,
            }
            return payload

        except TrailsUpstreamError as e:
            # Nothing is cached: an outage must not be remembered as an answer. The
            # payload is shaped like the empty one so the client decodes it the same
            # way, but the message says the source could not be reached rather than
            # that the region has no trails - the client shows it verbatim.
            logger.warning(f"Trails upstream unavailable: {e}")
            payload = {
                "ski_grid_values_b64": None,
                "hiking_grid_values_b64": None,
                "ski_area_grid_values_b64": None,
                "hiking_area_grid_values_b64": None,
                "ski_difficulty_grid_values_b64": None,
                "difficulty_classes": list(SKI_DIFFICULTY_CLASSES),
                "grid_dimensions": [dim, dim],
                "ski_count": 0,
                "hiking_count": 0,
                "feature_count": 0,
                "sources": [],
                "source": source,
                "relief_m": relief_m,
                "upstream_error": True,
                "error": f"Trail data source unreachable - {e}",
            }
            return payload

    if cache_key in _TRAILS_INFLIGHT:
        logger.info("Trails in-flight join: %s", cache_key[:8])
    try:
        payload = await dedupe(_TRAILS_INFLIGHT, cache_key, _compute)
    except Exception as e:
        logger.error(f"Error in get_terrain_trails: {e}", exc_info=True)
        return error_response("Trails fetch failed")
    return JSONResponse(content=payload)
