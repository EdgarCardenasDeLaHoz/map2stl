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
    b64_encode_u8 as _b64u8,
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
from geo2stl.projections import (
    fit_grid as _fit_grid,
)
from geo2stl.projections import (
    project_grid as _project_grid,
)
from geo2stl.projections import (
    project_rgb_image as _project_rgb_image,
)
from geo2stl.projections import (
    project_water_arrays as _project_water_arrays,
)
from geo2stl.projections import (
    projected_shape as _projected_shape,
)
from geo2stl.projections import (
    source_dim as _source_dim,
)
from geo2stl.projections import (
    source_shape as _source_shape,
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


def _layer_source_dim(dim: int, bbox: tuple[float, float, float, float],
                      projection: str, maintain_dimensions: bool) -> int:
    """Sampling size for a layer that is fetched once per box (water, ESA).

    The shared source grid (``geo2stl.projections.shared_source_dim``) is fine
    enough for every projection, so a projection switch reuses the cached fetch
    instead of downloading from Earth Engine again (7-12 s on a large box). A
    projection the shared grid cannot cover keeps its own ``source_dim``.
    """
    from geo2stl.projections import shared_covers, shared_source_dim, shared_source_shape

    own = _source_dim(dim, bbox, projection, maintain_dimensions)
    if maintain_dimensions:
        return own
    if shared_covers(shared_source_shape(dim, bbox, maintain_dimensions),
                     _source_shape(dim, bbox, projection, maintain_dimensions)):
        return max(own, shared_source_dim(dim, bbox, maintain_dimensions))
    return own


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
        None, description="Longest side of the returned grid, after projection (pixels)"),
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
    # The raw grid is cached once per bbox and sampling size; projection and
    # clipping are applied after the read. The projection is passed only so the
    # key can hold the sampling size (source_dim), which depends on it.
    _dem_cache_key = dem_cache_key(north, south, east, west, {
        "dim": dim,
        "projection": projection,
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

    # ``dim`` is the longest side AFTER projection (user decision 2026-10-13).
    # The raw grid is sampled at the plate-carree size that projects to it
    # (source_dim), so projection only ever shrinks, and target_dim lands the
    # result on exactly projected_shape(bbox, projection, dim).
    _bbox = (north, south, east, west)
    _sdim = _source_dim(dim, _bbox, projection, maintain_dimensions)
    _sshape = _source_shape(dim, _bbox, projection, maintain_dimensions)

    # TEST_MODE: return deterministic gradient without network I/O
    if TEST_MODE:
        im_raw_test = np.linspace(0, 100, num=(_sshape[0] * _sshape[1]),
                                  dtype=float).reshape(_sshape)
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

        # Apply projection even in TEST_MODE so tests exercise the full pipeline
        im = _project_grid(
            im_raw_test.astype(np.float32), north or 0.0, south or 0.0,
            east or 0.0, west or 0.0,
            projection, clip_valid_region, categorical=False,
            maintain_dimensions=maintain_dimensions, target_dim=dim,
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

    # User-facing notes from the fetch (water subtraction only partly possible:
    # no ESA WorldCover without Earth Engine). Shown as source_note; a DEM with a
    # note is not cached, so it is redone once the water source is set up.
    _water_notes: list[str] = []
    try:
        if im_raw is None:
            im_raw = await run_sync(_fetch_dem, _bbox, _sdim,
                                    dem_source, depth_scale=depth_scale,
                                    water_scale=water_scale,
                                    subtract_water=subtract_water,
                                    maintain_dimensions=maintain_dimensions,
                                    notes=_water_notes, shared_dim=dim)
            # Sources return their native size (h5 up to 2x, a coarse source
            # less): bring it to the exact sampling grid.
            im_raw = _fit_grid(np.asarray(im_raw, dtype=np.float32), _sshape)

        # Apply projection uniformly for ALL sources (fetchers return plate
        # carree); 'none' only checks the shape.
        im = _project_grid(
            im_raw.astype(np.float32), north, south, east, west,
            projection, clip_valid_region, categorical=False,
            maintain_dimensions=maintain_dimensions, target_dim=dim,
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
        # A box over the dataset's area cap was fetched from a coarser one: say so.
        from geo2stl.opentopo import OPENTOPO_DATASETS, bbox_area_km2, dataset_for_area
        used = dataset_for_area(dem_source, north, south, east, west)
        if used != dem_source:
            asked, got = OPENTOPO_DATASETS[dem_source], OPENTOPO_DATASETS[used]
            response_content["source_note"] = (
                f"{asked['label']} covers at most {asked['max_area_km2']:,} km²; this "
                f"{bbox_area_km2(north, south, east, west):,.0f} km² area uses {got['label']}.")
        if _water_notes:
            response_content["source_note"] = " ".join(
                [n for n in (response_content.get("source_note"), *_water_notes) if n])
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
                    # Project the plate-carree overlay onto the DEM's exact grid
                    sat_arr = np.asarray(sat_values, dtype=np.float32).reshape(
                        sat_height, sat_width)
                    sat_arr = _project_grid(
                        sat_arr, north, south, east, west,
                        projection, clip_valid_region, categorical=True,
                        maintain_dimensions=maintain_dimensions, target_dim=dim)
                    sat_height, sat_width = sat_arr.shape
                    response_content["sat_available"] = True
                    # base64 float32: tolist() took 0.2 s and 4.9 MB of JSON
                    response_content["sat_values_b64"] = _b64(sat_arr)
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
        if not show_sat and not from_cache and not _empty_warning and not _water_notes:
            im_clean = im_raw.astype(np.float32, copy=False)
            # Off the event loop: compressing a 1360x1600 grid took 1.37 s.
            await run_sync(
                write_array_cache,
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

        # Sampled on the shared source grid (one fetch serves every projection),
        # then projected onto the DEM's exact grid.
        # Scale clamping (50 MB / 32768 px limits) is handled inside fetch_water_mask.
        _sdim = _layer_source_dim(dim, (north, south, east, west), projection,
                                  maintain_dimensions)
        _longer_m = max(*bbox_size_m({"north": north or 0.0, "south": south or 0.0,
                                      "east": east or 0.0, "west": west or 0.0}), 1.0)
        sat_scale = max(10, int(math.ceil(_longer_m / _sdim)))

        # --- Water mask disk cache check ---
        # Keyed on the sampling size, not projection or clip_valid_region:
        # raw data is cached once per bbox; projection/clipping applied per request.
        # v2: ESA no-data (the open sea) counts as water (sat2stl.esa_water, 2026-10-14)
        _water_cache_key = make_cache_key("water", north, south, east, west, {
            "dim": _sdim, "ds": water_dataset, "v": 2})
        def _water_payload(water_mask: np.ndarray, esa: np.ndarray, **extra) -> dict:
            # Project onto the DEM's exact grid ('none' only resamples to it)
            water_mask, esa = _project_water_arrays(
                water_mask.astype(np.float32), esa.astype(np.float32),
                north, south, east, west, projection, clip_valid_region,
                maintain_dimensions=maintain_dimensions, target_dim=dim)
            h, w = water_mask.shape
            wp = int(np.sum(water_mask > 0.5))
            tp = h * w
            return {
                "water_mask_values_b64": _b64u8(water_mask),
                "water_mask_dtype": "uint8",
                "water_mask_dimensions": [h, w],
                "water_pixels": wp,
                "total_pixels": tp,
                "water_percentage": 100.0 * wp / tp if tp else 0.0,
                "esa_values_b64": _b64u8(esa),
                "esa_dtype": "uint8",
                "esa_dimensions": [h, w],
                **extra,
            }

        def _water_from_cache() -> dict | None:
            hit = read_array_cache("water", _water_cache_key)
            if hit is None:
                return None
            wm, esa = hit[0].get("water_mask"), hit[0].get("esa")
            if wm is None or esa is None:
                return None
            logger.info(f"Water mask cache hit: {_water_cache_key[:8]}...")
            return _water_payload(wm, esa, from_cache=True)

        # Off the event loop: a hit still reads, projects and encodes two grids,
        # which queued behind other layers for 0.35-1.0 s during a region open.
        _hit = await run_sync(_water_from_cache)
        if _hit is not None:
            return JSONResponse(content=_hit)

        if TEST_MODE:
            h, w = 50, 50
            water_arr = np.zeros((h, w), dtype=float)
            water_arr[h // 4:h // 2, w // 4:w // 2] = 1.0
            esa_arr = water_arr.copy()
            # Apply projection even in TEST_MODE
            water_arr, esa_arr = _project_water_arrays(
                water_arr.astype(np.float32), esa_arr.astype(np.float32),
                north, south, east, west, projection, clip_valid_region,
                maintain_dimensions=maintain_dimensions, target_dim=dim)
            h, w = water_arr.shape
            wp = int(np.sum(water_arr > 0.5))
            tp = h * w
            return JSONResponse(content={
                "water_mask_values_b64": _b64u8(water_arr),
                "water_mask_dtype": "uint8",
                "water_mask_dimensions": [h, w],
                "water_pixels": wp,
                "total_pixels": tp,
                "water_percentage": 100.0 * wp / tp,
                "esa_values_b64": _b64u8(esa_arr),
                "esa_dtype": "uint8",
                "esa_dimensions": [h, w],
                "resolution_m": sat_scale,
            })

        try:
            water_mask, img, sat_scale = await run_sync(
                _fetch_water_mask, north, south, east, west,
                sat_scale, water_dataset)
        except RuntimeError as fetch_err:
            return error_response(str(fetch_err))

        def _water_store() -> dict:
            write_array_cache("water", _water_cache_key,
                              {"water_mask": water_mask.astype(np.float32),
                               "esa": img.astype(np.float32)},
                              {"shape": list(water_mask.shape)})
            return _water_payload(water_mask, img, resolution_m=sat_scale)

        return JSONResponse(content=await run_sync(_water_store))

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

        # Sampled on the shared source grid: one fetch serves every projection.
        _sdim = _layer_source_dim(dim, (north, south, east, west), projection,
                                  maintain_dimensions)
        _longer_m = max(*bbox_size_m({"north": north or 0.0, "south": south or 0.0,
                                      "east": east or 0.0, "west": west or 0.0}), 1.0)
        sat_scale = max(10, int(math.ceil(_longer_m / _sdim)))

        # Cache key does NOT include projection/clip_valid_region/maintain_dimensions —
        # raw (unprojected) data is cached once per bbox; projection is (re-)applied
        # on every request, including cache hits, so switching projection or
        # maintain_dimensions for an already-cached bbox reflects the new setting
        # instead of silently returning a stale shape from a prior request.
        _esa_cache_key = make_cache_key("esa_lc", north, south, east, west, {
            "dim": _sdim})
        def _esa_payload(esa: np.ndarray, **extra) -> dict:
            esa = _project_grid(
                esa.astype(np.float32), north, south, east, west,
                projection, clip_valid_region, categorical=True,
                maintain_dimensions=maintain_dimensions, target_dim=dim)
            h, w = esa.shape
            return {"esa_values_b64": _b64u8(esa), "esa_dtype": "uint8",
                    "esa_dimensions": [h, w],
                    "resolution_m": sat_scale, **extra}

        def _esa_from_cache() -> dict | None:
            hit = read_array_cache("esa_lc", _esa_cache_key)
            if hit is None or hit[0].get("esa") is None:
                return None
            logger.info(f"ESA land cover cache hit: {_esa_cache_key[:8]}...")
            return _esa_payload(hit[0]["esa"], from_cache=True)

        _hit = await run_sync(_esa_from_cache)   # off the event loop, as for water
        if _hit is not None:
            return JSONResponse(content=_hit)

        if TEST_MODE:
            h, w = 50, 50
            esa_arr = np.full((h, w), 10, dtype=np.float32)
            # Apply projection even in TEST_MODE
            esa_arr = _project_grid(
                esa_arr, north, south, east, west,
                projection, clip_valid_region, categorical=True,
                maintain_dimensions=maintain_dimensions, target_dim=dim)
            h, w = esa_arr.shape
            return JSONResponse(content={
                "esa_values_b64": _b64u8(esa_arr),
                "esa_dtype": "uint8",
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
        def _esa_store() -> dict:
            raw = img.astype(np.float32)
            write_array_cache("esa_lc", _esa_cache_key,
                              {"esa": raw},
                              {"shape": list(raw.shape)})
            return _esa_payload(raw)

        return JSONResponse(content=await run_sync(_esa_store))

    except ValueError as ve:
        return error_response(str(ve), 400)
    except Exception as e:
        logger.error(f"Unhandled error in get_terrain_esa_land_cover: {e}")
        return error_response(str(e))


def _satellite_payload(b64: str, north: float, south: float,
                       east: float, west: float) -> dict:
    """Satellite response: the JPEG, its bbox, and ``dimensions`` ``[h, w]`` (the
    DEM payload's convention) so a client can check it lines up with the DEM grid
    (docs/history/issues-resolved.md §0f). The size comes from the JPEG header; nothing is decoded."""
    import base64
    from io import BytesIO

    from PIL import Image
    with Image.open(BytesIO(base64.b64decode(b64))) as img:
        w, h = img.size
    return {"image": b64, "bbox": [west, south, east, north], "dimensions": [h, w]}


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
        projected = _project_rgb_image(
            np.array(img), north, south, east, west, projection, clip_valid_region,
            maintain_dimensions=maintain_dimensions, target_dim=dim)
        img = Image.fromarray(projected)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return JSONResponse(content=_satellite_payload(b64, north, south, east, west))

    try:
        # On the DEM's unprojected grid at the DEM's sampling size (source_dim),
        # so the projection below lands it on the DEM's exact grid.
        _sdim = _source_dim(dim, (north, south, east, west), projection, maintain_dimensions)
        b64 = await run_sync(
            _fetch_satellite_tiles, north, south, east, west, _sdim, degree_grid=True)

        # Project channel-by-channel ('none' only resamples to the DEM's shape)
        import base64 as _b64mod
        from io import BytesIO as _BytesIO

        from PIL import Image as _Image

        raw_bytes = _b64mod.b64decode(b64)
        img_pil = _Image.open(_BytesIO(raw_bytes)).convert("RGB")
        img_arr = np.array(img_pil)

        projected = await run_sync(
            _project_rgb_image, img_arr,
            north, south, east, west, projection, clip_valid_region,
            maintain_dimensions, dim)

        if projection != "none" or projected.shape[:2] != img_arr.shape[:2]:
            out_img = _Image.fromarray(projected)
            buf = _BytesIO()
            out_img.save(buf, format="JPEG", quality=85)
            b64 = _b64mod.b64encode(buf.getvalue()).decode()

        return JSONResponse(content=_satellite_payload(b64, north, south, east, west))
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

HYDRO_ORDERS_VERSION = 1   # bump when the order grid or water mask changes
HYDRO_ORDER_BASE = 3       # fetch at least orders >= 3 so 3..9 reuse one raster
WATER_CODE = 100           # open water in the order grid sent to the client


def _hydrorivers_layers(north, south, east, west, dim, width_factor, min_order,
                        water_dim=None):
    """(order grid uint8, water bool | None, reach counts per order) for HydroRIVERS.

    ``water_dim`` is the ESA sampling size for the open-water union
    (``geo2stl.hydrology.water_surface_mask`` ``fetch_dim``).

    Cached without min order, depth or exponent - those are lookups on the order
    grid (geo2stl.hydrology.order_depth_grid) - so stepping the min order
    reuses one raster. The cached base covers orders >= min(min_order,
    HYDRO_ORDER_BASE); a lower min order needs (and caches) a finer base.
    """
    from geo2stl.hydrology import (
        fetch_hydrorivers,
        rasterize_hydrorivers_orders,
        water_surface_mask,
    )

    def key(base):
        return make_cache_key("hydro_orders", north, south, east, west,
                              {"dim": dim, "wf": width_factor, "base": base,
                               "v": HYDRO_ORDERS_VERSION})

    for base in range(min_order, 0, -1):
        hit = read_array_cache("hydro_orders", key(base))
        if hit is not None:
            arrs, meta = hit
            water = arrs.get("water")
            return (arrs["orders"], None if water is None or not water.any() else water.astype(bool),
                    {int(k): v for k, v in meta.get("counts", {}).items()})
    base = min(min_order, HYDRO_ORDER_BASE)
    gdf = fetch_hydrorivers(north, south, east, west, min_order=base)
    if gdf is None or len(gdf) == 0:
        orders, counts = np.zeros((dim, dim), np.uint8), {}
    else:
        orders = rasterize_hydrorivers_orders(gdf, north, south, east, west, dim, width_factor)
        vc = gdf["ORD_STRA"].clip(lower=1, upper=10).astype(int).value_counts()
        counts = {int(k): int(v) for k, v in vc.items()}
    water = water_surface_mask(north, south, east, west, orders.shape, fetch_dim=water_dim)
    write_array_cache("hydro_orders", key(base),
                      {"orders": orders,
                       "water": (water if water is not None else np.zeros_like(orders, bool)).astype(np.uint8)},
                      {"counts": {str(k): v for k, v in counts.items()}})
    return orders, water, counts


def _hydrorivers_payload(north, south, east, west, dim, depression_m, min_order,
                         order_exponent, width_factor, projection, clip_valid_region,
                         maintain_dimensions):
    """The HydroRIVERS hydrology response: depression grid (rivers united with the
    open water) and the Strahler-order grid for the colour-by-order view.

    Rasterised at source_dim and projected onto the DEM's exact grid (``dim`` is
    the longest side after projection)."""
    from geo2stl.hydrology import order_depth_grid, union_water_surface

    sdim = _source_dim(dim, (north, south, east, west), projection, maintain_dimensions)
    orders, water, counts = _hydrorivers_layers(
        north, south, east, west, sdim, width_factor, min_order,
        _layer_source_dim(dim, (north, south, east, west), projection, maintain_dimensions))
    depth = order_depth_grid(orders, depression_m, order_exponent, min_order)
    shown = np.where(orders >= min_order, orders, 0).astype(np.float32)
    if water is not None:
        depth = union_water_surface(depth, water, depression_m)
        shown[(shown == 0) & water] = WATER_CODE
    features = sum(v for k, v in counts.items() if k >= min_order)
    depth = np.nan_to_num(_project_grid(
        depth, north, south, east, west, projection, clip_valid_region,
        categorical=False, maintain_dimensions=maintain_dimensions,
        target_dim=dim), nan=0.0)
    shown = np.nan_to_num(_project_grid(
        shown, north, south, east, west, projection, clip_valid_region,
        categorical=True, maintain_dimensions=maintain_dimensions,
        target_dim=dim), nan=0.0)
    h, w = depth.shape
    return {
        "river_grid_values_b64": _b64(depth.astype(np.float32)),
        "river_grid_dimensions": [h, w],
        "order_grid_b64": _b64(shown.astype(np.float32)),
        "order_water_code": WATER_CODE,
        "order_counts": {str(k): v for k, v in sorted(counts.items()) if k >= min_order},
        "feature_count": int(features),
        "water_surface": water is not None,
        "source": "hydrorivers",
        "depression_m": depression_m,
    }


def _river_print_payload(north, south, east, west, dim, dem_source, river_source,
                         min_order, width_scale, depth_scale, projection,
                         clip_valid_region, maintain_dimensions):
    """Hydrology preview = what prints: the composite's river carve.

    Same steps as ``composite.compute_composite_dem`` for a river layer: the base
    DEM on the shared grid (``geo2stl.dem.shared_base_grid``; ``dim`` is the
    longest side after projection), the reaches snapped onto it, carved per
    discharge once for every projection (``geo2stl.water_layers.river_carve_layers``,
    cached per order), the stack pooled to the projection's source_shape
    (``pool_river_stack``) while the DEM is fitted to it, zeroed on the open sea,
    projected categorically. Adds the Strahler-order grid
    and the open water for the colour-by-order view.
    """
    from geo2stl.dem import fetch_layer_data, is_shared_base_source, shared_base_grid
    from geo2stl.hydrology import water_surface_mask
    from geo2stl.projections import shared_covers, shared_source_dim, shared_source_shape
    from geo2stl.water_layers import (
        RIVER_FETCH,
        ocean_mask,
        pool_river_stack,
        river_carve,
        river_carve_layers,
        river_order_grid,
    )

    bbox = (north, south, east, west)
    sdim = _source_dim(dim, bbox, projection, maintain_dimensions)
    sshape = _source_shape(dim, bbox, projection, maintain_dimensions)
    options = {"min_order": min_order, "width_scale": width_scale}
    if is_shared_base_source(dem_source) and shared_covers(
            shared_source_shape(dim, bbox, maintain_dimensions), sshape):
        # The composite's path: DEM and carve on the projection-independent shared
        # grid (same key, so the two requests share one carve), pooled per projection.
        shared = shared_base_grid(dem_source, north, south, east, west, dim, {},
                                  maintain_dimensions)
        base_raw = np.asarray(_fit_grid(shared, sshape), dtype=np.float64)
        orders, stack = river_carve_layers(
            river_source, RIVER_FETCH[river_source], north, south, east, west,
            shared_source_dim(dim, bbox, maintain_dimensions), options, shared)
        stack = pool_river_stack(stack, sshape)
    else:
        base_raw = _fit_grid(
            np.asarray(fetch_layer_data(dem_source, north, south, east, west, sdim, {}),
                       dtype=np.float64), sshape)
        orders, stack = river_carve_layers(river_source, RIVER_FETCH[river_source], north,
                                           south, east, west, sdim, options, base_raw)
    if len(orders):
        carve = river_carve(orders, stack, min_order, depth_scale)
        shown = river_order_grid(orders, stack, min_order).astype(np.float32)
    else:
        carve = np.zeros(base_raw.shape, dtype=np.float64)
        shown = np.zeros(base_raw.shape, dtype=np.float32)
    water = water_surface_mask(
        north, south, east, west, base_raw.shape,
        fetch_dim=_layer_source_dim(dim, bbox, projection, maintain_dimensions))
    terrain = base_raw
    water_f = (water.astype(np.float32) if water is not None
               else np.zeros(base_raw.shape, dtype=np.float32))
    # The composite's order: project each grid (rivers categorically, the
    # terrain continuously) onto the exact output grid, then mask the sea on
    # the projected terrain.
    def proj(a, categorical):
        return np.nan_to_num(_project_grid(
            a, north, south, east, west, projection, clip_valid_region,
            categorical=categorical, maintain_dimensions=maintain_dimensions,
            target_dim=dim), nan=0.0)
    carve, shown, water_f = proj(carve, True), proj(shown, True), proj(water_f, True)
    terrain = _project_grid(base_raw, north, south, east, west, projection,
                            clip_valid_region, categorical=False,
                            maintain_dimensions=maintain_dimensions, target_dim=dim)
    sea = ocean_mask(np.nan_to_num(terrain, nan=1.0))
    carve[sea] = 0.0
    shown[sea] = 0.0
    deepest = float(carve.min()) if carve.size and carve.min() < 0 else -1.0
    if water is not None:
        wet = water_f > 0.5
        shown[(shown == 0) & wet] = WATER_CODE
        carve = np.where(wet & (carve == 0), deepest, carve)
    present = sorted({int(o) for o in np.unique(shown) if 0 < o < WATER_CODE})
    h, w = carve.shape
    return {
        "river_grid_values_b64": _b64(carve.astype(np.float32)),
        "river_grid_dimensions": [h, w],
        "order_grid_b64": _b64(shown.astype(np.float32)),
        "order_water_code": WATER_CODE,
        "order_counts": {str(o): int(np.count_nonzero(shown == o)) for o in present},
        "order_counts_unit": "px",
        "feature_count": int(np.count_nonzero((shown > 0) & (shown < WATER_CODE))),
        "water_surface": water is not None,
        "model": "print",
        "source": river_source,
        "depression_m": deepest,
    }


def _print_payload_complete(payload: dict) -> bool:
    """Whether a rivers preview may be cached: it has the open water and a river.

    A payload built while a source was down (Earth Engine, the local sea store,
    HydroRIVERS) has no water surface or no reach; cached, it froze a hollow sea
    with no rivers on Europa for good (2026-10-14). Such a payload is served but
    rebuilt next time; a box that truly has no river only costs that rebuild.
    """
    return bool(payload.get("water_surface")) and int(payload.get("feature_count") or 0) > 0


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

    dem_source = params.get("dem_source")
    if dem_source and not TEST_MODE:
        # Preview = print: the composite's own river carve on the base DEM.
        river_source = "hydrorivers" if source == "hydrorivers" else "natural_earth_rivers"
        width_scale = _parse_float(params, "width_scale", 1.0)
        depth_scale = _parse_float(params, "depth_scale", 1.0)
        key = (f"print:{north}:{south}:{east}:{west}:{dim}:{dem_source}:{river_source}:"
               f"{min_order}:{width_scale}:{depth_scale}:{projection}:{clip_valid_region}:"
               f"{maintain_dimensions}")

        def _cached_print() -> dict:
            # The finished preview is cached too, not only the river carve inside
            # it: a repeat open still re-fetched the DEM and water mask and
            # rebuilt the payload (~3 s for Colombia_2).
            from geo2stl.cache import json_cache_key, read_json_cache, write_json_cache
            from geo2stl.water_layers import RIVER_CARVE_VERSION
            # v3: carve on the shared grid, pooled per projection (2026-10-14);
            # v2: dim is the longest side after projection (2026-10-13)
            ck = json_cache_key("hydrology", "print", 3, RIVER_CARVE_VERSION, key)
            hit = read_json_cache("hydrology", ck)
            if hit is not None and _print_payload_complete(hit):
                return hit
            from geo2stl.perf import perf_step
            with perf_step("rivers_preview", key=ck):
                payload = _river_print_payload(
                    north, south, east, west, dim, dem_source, river_source, min_order,
                    width_scale, depth_scale, projection, clip_valid_region,
                    maintain_dimensions)
            if _print_payload_complete(payload):
                write_json_cache("hydrology", ck, payload)
            return payload

        async def _compute_print() -> dict:
            return await run_sync(_cached_print)
        try:
            payload = await dedupe(_HYDRO_INFLIGHT, key, _compute_print)
        except Exception as e:
            logger.error(f"Error in get_terrain_hydrology: {e}", exc_info=True)
            return error_response("Hydrology fetch failed")
        return JSONResponse(content=payload)

    if source == "hydrorivers" and not TEST_MODE:
        inflight_key = f"orders:{north}:{south}:{east}:{west}:{dim}:{width_factor}:{min_order}"

        async def _compute_orders() -> dict:
            return await run_sync(
                _hydrorivers_payload, north, south, east, west, dim, depression_m,
                min_order, order_exponent, width_factor, projection, clip_valid_region,
                maintain_dimensions)
        try:
            payload = await dedupe(_HYDRO_INFLIGHT, inflight_key + f":{depression_m}:"
                                   f"{order_exponent}:{projection}:{clip_valid_region}:"
                                   f"{maintain_dimensions}", _compute_orders)
        except Exception as e:
            logger.error(f"Error in get_terrain_hydrology: {e}", exc_info=True)
            return error_response("Hydrology fetch failed")
        return JSONResponse(content=payload)

    # Cache key covers every parameter that affects the rasterized output.
    # Cache key does NOT include projection/clip_valid_region/maintain_dimensions —
    # raw (unprojected) data is cached once per bbox; projection is (re-)applied
    # on every request, including cache hits, so switching projection or
    # maintain_dimensions for an already-cached bbox reflects the new setting
    # instead of silently returning whichever projection was baked in first.
    # Rasterised at source_dim, projected onto the DEM's exact grid below.
    sdim = _source_dim(dim, (north, south, east, west), projection, maintain_dimensions)
    cache_extra = {
        "dim": sdim, "src": source, "dep": depression_m,
        "scl": scale_m, "mo": min_order, "oe": order_exponent,
        "wf": width_factor,
        "ws": 1,   # rivers united with the open water surface (sea, lakes), 2026-09-30
    }
    cache_key = make_cache_key(
        "hydrology", north, south, east, west, cache_extra)
    def _river_payload(river_grid: np.ndarray, feature_count: int, src: str) -> dict:
        # Project onto the DEM's exact grid. NaN fill (from projection) becomes
        # 0 (= no river) so JSON serialisation produces 0.0 instead of null.
        river_grid = np.nan_to_num(_project_grid(
            river_grid, north, south, east, west, projection, clip_valid_region,
            categorical=False, maintain_dimensions=maintain_dimensions,
            target_dim=dim), nan=0.0)
        h, w = river_grid.shape
        return {
            "river_grid_values_b64": _b64(river_grid),
            "river_grid_dimensions": [h, w],
            "feature_count": int(feature_count),
            "source": src,
            "depression_m": depression_m,
        }

    def _river_from_cache() -> dict | None:
        hit = read_array_cache("hydrology", cache_key)
        if hit is None or hit[0].get("river_grid") is None:
            return None
        arrs, meta = hit
        payload = _river_payload(arrs["river_grid"], meta.get("feature_count", 0),
                                 meta.get("source", source))
        logger.info("Hydrology cache hit: %s (%dx%d)", cache_key[:8],
                    *payload["river_grid_dimensions"][::-1])
        return payload

    _hit = await run_sync(_river_from_cache)   # off the event loop
    if _hit is not None:
        return JSONResponse(content=_hit)

    if TEST_MODE:
        h, w = dim, dim
        river_arr = np.zeros((h, w), dtype=np.float32)
        river_arr[h//4:h//3, w//4:3*w//4] = depression_m
        # Apply projection even in TEST_MODE
        river_arr = np.nan_to_num(_project_grid(
            river_arr, north, south, east, west,
            projection, clip_valid_region, categorical=False,
            maintain_dimensions=maintain_dimensions, target_dim=dim), nan=0.0)
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
            north, south, east, west, sdim,
            scale_m, depression_m,
            source, min_order, order_exponent, width_factor)

        if result is None:
            payload = {
                "river_grid_values": [],
                "river_grid_dimensions": list(_projected_shape(
                    (north, south, east, west), projection, dim, maintain_dimensions)),
                "feature_count": 0,
                "source": source,
                "depression_m": depression_m,
                "error": "No rivers found in region",
            }
            return payload

        # Cache the RAW (unprojected) river grid — projection is applied fresh
        # below (and on every future cache hit above), never baked into the
        # cached value. See the cache-hit path's comment for why.
        def _store() -> dict:
            write_array_cache(
                "hydrology", cache_key,
                {"river_grid": result["river_grid"]},
                {"feature_count": int(result["feature_count"]),
                 "source": result.get("source", source)},
            )
            return _river_payload(result["river_grid"], result["feature_count"],
                                  result.get("source", source))

        # a missing return here once answered `null` to every cache miss (2026-09-30)
        return await run_sync(_store)

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

@router.get("/api/terrain/borders", tags=["terrain"])
async def get_terrain_borders(
    request: Request,
    bbox: BboxQueryParams = Depends(_parse_bbox_query),
    dim: int | None = Query(None, description="Grid resolution (the longer side, like the DEM)"),
    projection: str | None = Query(None, description="Map projection: 'none', 'cosine', 'mercator', ..."),
    clip_valid_region: bool | None = Query(None, description="Clip projection padding to valid data extent"),
    maintain_dimensions: bool | None = Query(None, description="Maintain output dimensions after projection"),
):
    """Country and state/province borders on a grid over the box, for the Edit map (view only).

    Grid codes: 0 none, 1 state/province line, 2 country border (``geo2stl/borders.py``,
    Natural Earth 10 m). The raw grid (``source_shape``, the plate-carree grid that
    projects to ``dim``) is cached per box and size; the projection is applied per
    request onto the DEM's exact grid, nearest-neighbour (the codes are classes).
    """
    from geo2stl.borders import borders_grid

    params = request.query_params
    north, south, east, west = bbox.north, bbox.south, bbox.east, bbox.west
    dim = _parse_int(params, "dim", 600)
    projection = params.get("projection", "none")
    clip_valid_region = _parse_bool(params, "clip_valid_region", True)
    maintain_dimensions = _parse_bool(params, "maintain_dimensions", False)
    err = _validate_bbox(north, south, east, west) or _validate_dim(dim)
    if err:
        return err
    shape = _source_shape(dim, (north, south, east, west), projection, maintain_dimensions)

    def _compute() -> tuple[np.ndarray, dict]:
        if TEST_MODE:   # no Natural Earth download in tests: a cross of a country and a state line
            g = np.zeros(shape, dtype=np.uint8)
            g[shape[0] // 2, :] = 2
            g[:, shape[1] // 2] = 1
            return g, {"countries": 1, "states": 1}
        key = make_cache_key("borders", north, south, east, west, {"shape": list(shape), "v": 1})
        hit = read_array_cache("borders", key)
        if hit is not None and hit[0].get("grid") is not None:
            return hit[0]["grid"].astype(np.uint8), dict(hit[1].get("counts") or {})
        g, counts = borders_grid(north, south, east, west, shape)
        write_array_cache("borders", key, {"grid": g}, {"counts": counts})
        return g, counts

    def _payload() -> dict:
        grid, counts = _compute()
        grid = np.nan_to_num(_project_grid(
            grid.astype(np.float32), north, south, east, west, projection, clip_valid_region,
            categorical=True, maintain_dimensions=maintain_dimensions, target_dim=dim), nan=0.0)
        h, w = grid.shape
        return {
            "grid_values_b64": _b64(np.asarray(grid, dtype=np.float32)),
            "grid_dimensions": [h, w],
            "codes": {"state": 1, "country": 2},
            "counts": counts,
        }

    try:
        payload = await run_sync(_payload)   # read, project and encode off the event loop
    except Exception as e:  # noqa: BLE001 - offline or a bad archive: say so, don't 500 the page
        logger.warning("Borders unavailable: %s", e)
        return error_response(f"Borders unavailable: {e}")
    return JSONResponse(content=payload)


#: Largest region (bbox diagonal) trails are fetched for; see get_terrain_trails.
TRAILS_MAX_DIAG_KM = 300.0


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
    # Rasterised at source_dim, projected onto the DEM's exact grid (dim = the
    # longest side after projection).
    sdim = _source_dim(dim, (north, south, east, west), projection, maintain_dimensions)
    out_dims = list(_projected_shape(
        (north, south, east, west), projection, dim, maintain_dimensions))

    from geo2stl.geo import bbox_size_m
    w_m, h_m = bbox_size_m({"north": north, "south": south, "east": east, "west": west})
    diag_km = float(np.hypot(w_m, h_m)) / 1000.0
    if diag_km > TRAILS_MAX_DIAG_KM and not TEST_MODE:
        # A trail is metres wide, far under a pixel of such a box, and Overpass
        # splits the query: Colombia (2,800 km across) became ~2,000 sub-queries
        # that held a worker for over 10 minutes (2026-10-03 perf audit).
        return JSONResponse(content={
            "ski_grid_values_b64": None, "hiking_grid_values_b64": None,
            "ski_area_grid_values_b64": None, "hiking_area_grid_values_b64": None,
            "ski_difficulty_grid_values_b64": None,
            "difficulty_classes": list(SKI_DIFFICULTY_CLASSES),
            "grid_dimensions": out_dims, "ski_count": 0, "hiking_count": 0,
            "feature_count": 0, "sources": [], "source": source, "relief_m": relief_m,
            "skipped": "too_large",
            "error": (f"Trails are skipped for regions over {TRAILS_MAX_DIAG_KM:.0f} km across "
                      f"(this one is {diag_km:,.0f} km): a trail is far thinner than a pixel."),
        })

    def _project_many(*grids, categorical=False):
        """Project every grid onto the DEM's exact grid ('none' only resamples).

        ``categorical`` switches the resampler to nearest-neighbour, which the
        difficulty grid needs: its values are class indices, and interpolating
        between "easy" and "expert" would invent a grade that is neither.
        """
        out = []
        for grid in grids:
            g = _project_grid(
                grid, north, south, east, west, projection, clip_valid_region,
                categorical=categorical, maintain_dimensions=maintain_dimensions,
                target_dim=dim)
            # Projection pads with NaN; here 0 is the "no trail" value.
            out.append(np.nan_to_num(g, nan=0.0))
        return out

    # Cache key covers every parameter that changes the rasterized output.
    # Projection is deliberately excluded and re-applied per request, matching the
    # hydrology endpoint: switching projection must not force a refetch. Categories
    # are excluded too — both grids are always computed, so an entry written for
    # one category request serves the other as well.
    cache_extra = {"dim": sdim, "src": source, "rel": relief_m, "wid": width_m}
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
                north, south, east, west, sdim,
                relief_m, width_m, source, categories)

            if result is None:
                payload = {
                    "ski_grid_values_b64": None,
                    "hiking_grid_values_b64": None,
                    "ski_area_grid_values_b64": None,
                    "hiking_area_grid_values_b64": None,
                    "ski_difficulty_grid_values_b64": None,
                    "difficulty_classes": list(SKI_DIFFICULTY_CLASSES),
                    "grid_dimensions": out_dims,
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
                "grid_dimensions": out_dims,
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
