"""
Composite DEM routes - composition operations that combine data layers.

POST /api/composite/city-raster
  Reads OSM buildings/roads/waterways/walls from the disk cache (written by
  /api/cities) and rasterizes them into per-pixel height-delta arrays:
  building footprints with numpy2stl.raster.burn_polygons (tallest wins where
  footprints overlap, holes left empty), lines with PIL/Pillow.  ~50x faster
  than the equivalent JS scanline fill.

  Weights / scales are NOT applied server-side - the client multiplies these
  normalized arrays by the slider values.  This means only a bbox or dimension
  change triggers a new backend call; all slider adjustments are instant
  client-side multiplications.

  Input:  { north, south, east, west, width, height }
  Output: { buildings, roads, waterways, walls, width, height }
            each is a flat float32 list at (width x height) pixels.
              buildings  - per-pixel building height in metres  (scale=1)
              roads      - binary road mask (0 or 1)
              waterways  - binary waterway mask (0 or 1)
              walls      - per-pixel wall height in metres  (scale=1)

  Cached under namespace "composite" by (bbox, width, height, detail).

  The same four channels are also registered as geo2stl layer sources
  ("osm_buildings", "osm_roads", "osm_waterways", "osm_walls") so the composite
  DEM can add and subtract them server-side - see register_city_layer_sources.

POST /api/composite/dem-merge
  Merge multiple elevation/mask layers into one composite DEM with
  per-layer processing (clip, smooth, sharpen, normalize) and blend modes.
"""

import logging
import threading
import time
from contextlib import contextmanager

import numpy as np
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from numpy2stl.raster import burn_polygons
from pydantic import BaseModel

from app.server.core.cache import (
    make_cache_key,
    osm_cache_key,
    read_array_cache,
    read_osm_cache,
    write_array_cache,
)
from app.server.core.validation import model_to_dict, run_sync
from app.server.schemas import MergeRequest
from geo2stl.geo import m_per_deg_lon

logger = logging.getLogger(__name__)
router = APIRouter(tags=["composite"])


class CompositeCityRasterRequest(BaseModel):
    north:  float
    south:  float
    east:   float
    west:   float
    width:  int = 512
    height: int = 512
    projection: str = "none"
    clip_valid_region: bool = True
    detail: str = "full"  # "full" or "coarse" — must match the tier used by /api/cities
                          # so the OSM cache lookup key resolves to the matching entry
    maintain_dimensions: bool = False  # keep input shape after projection (legacy/opt-in)


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _make_geo_to_px(N, S, E, W, PW, PH):
    """Return (geo_to_px, coords_to_px) closures for this bbox/canvas."""
    lat_span = N - S
    lon_span = E - W

    def geo_to_px(lon, lat):
        x = (lon - W) / lon_span * PW
        y = (N - lat) / lat_span * PH
        return (x, y)

    def coords_to_px(coords):
        return [geo_to_px(lon, lat) for lon, lat in coords]

    return geo_to_px, coords_to_px


# ---------------------------------------------------------------------------
# Per-layer rasterizers
# ---------------------------------------------------------------------------

def _rasterize_buildings(features, bounds, PW, PH):
    """Return a float32 array (PH×PW, row 0 = north) with per-pixel building height in metres.

    ``bounds`` is (west, south, east, north).  Overlapping footprints keep the
    tallest and courtyards (holes) stay empty (``numpy2stl.raster.burn_polygons``,
    cell-centre rule).
    """
    geoms, heights = [], []
    for feat in features:
        geom = feat.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon") or not geom.get("coordinates"):
            continue
        geoms.append(geom)
        heights.append(float((feat.get("properties") or {}).get("height_m") or 10))
    return burn_polygons(geoms, (PH, PW), bounds=bounds, values=heights, mode="max",
                         dtype=np.float32)


def _rasterize_roads(features, coords_to_px, PW, PH, m_per_px):
    """Return a binary float32 array (PH×PW) marking road pixels."""
    from PIL import Image, ImageDraw
    img = Image.new("1", (PW, PH), 0)
    draw = ImageDraw.Draw(img)
    for feat in features:
        geom = feat.get("geometry") or {}
        w_m = float((feat.get("properties") or {}).get("road_width_m") or 6)
        w_px = max(1, round(w_m / m_per_px))
        lines = []
        if geom.get("type") == "LineString":
            lines = [geom["coordinates"]]
        elif geom.get("type") == "MultiLineString":
            lines = geom["coordinates"]
        for line in lines:
            px = coords_to_px(line)
            if len(px) >= 2:
                draw.line(px, fill=1, width=w_px)
    return np.array(img, dtype=np.float32)


def _rasterize_waterways(features, coords_to_px, PW, PH, m_per_px):
    """Return a binary float32 array (PH×PW) marking waterway pixels."""
    from PIL import Image, ImageDraw
    img = Image.new("1", (PW, PH), 0)
    draw = ImageDraw.Draw(img)
    w_px = max(2, round(4.0 / m_per_px))
    for feat in features:
        geom = feat.get("geometry") or {}
        if geom.get("type") == "LineString":
            px = coords_to_px(geom["coordinates"])
            if len(px) >= 2:
                draw.line(px, fill=1, width=w_px)
        elif geom.get("type") == "MultiLineString":
            for line in geom["coordinates"]:
                px = coords_to_px(line)
                if len(px) >= 2:
                    draw.line(px, fill=1, width=w_px)
        elif geom.get("type") == "Polygon":
            px = coords_to_px(geom["coordinates"][0])
            if px:
                draw.polygon(px, fill=1)
        elif geom.get("type") == "MultiPolygon":
            for poly in geom["coordinates"]:
                px = coords_to_px(poly[0])
                if px:
                    draw.polygon(px, fill=1)
    return np.array(img, dtype=np.float32)


def _rasterize_walls(features, coords_to_px, PW, PH, m_per_px):
    """Return a float32 array (PH×PW) with per-pixel wall height in metres."""
    from PIL import Image, ImageDraw
    arr = np.zeros((PH, PW), dtype=np.float32)
    for feat in features:
        geom = feat.get("geometry") or {}
        h_m = float((feat.get("properties") or {}).get("height_m") or 5)
        w_px = max(1, round(2.0 / m_per_px))
        lines = []
        if geom.get("type") == "LineString":
            lines = [geom["coordinates"]]
        elif geom.get("type") == "MultiLineString":
            lines = geom["coordinates"]
        for line in lines:
            px = coords_to_px(line)
            if len(px) >= 2:
                mask = Image.new("1", (PW, PH), 0)
                ImageDraw.Draw(mask).line(px, fill=1, width=w_px)
                arr += np.array(mask, dtype=np.float32) * h_m
    return arr


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

def _rasterize_city(req: CompositeCityRasterRequest) -> dict:
    """Synchronous rasterization — called via run_in_executor."""
    N, S, E, W = req.north, req.south, req.east, req.west
    PW, PH = req.width, req.height
    lat_span = N - S
    lon_span = E - W

    def _empty_result():
        z = [0.0] * (PW * PH)
        return {"buildings": z, "roads": z, "waterways": z, "walls": z,
                "width": PW, "height": PH}

    if lat_span <= 0 or lon_span <= 0:
        return _empty_result()

    lat_mid = (N + S) / 2
    m_per_px = lon_span * m_per_deg_lon(lat_mid) / PW

    _, coords_to_px = _make_geo_to_px(N, S, E, W, PW, PH)

    # min_area must match what /api/cities used for this tier, or the cache
    # key (which is hashed from bbox + tol + min_area) will miss entirely.
    from app.server.config import COARSE_MIN_BUILDING_AREA_M2
    min_area = COARSE_MIN_BUILDING_AREA_M2 if req.detail == "coarse" else 5.0
    osm_key = osm_cache_key(N, S, E, W, min_area=min_area)
    osm_data = read_osm_cache(osm_key)
    if not osm_data:
        logger.debug(
            f"No OSM cache for composite city-raster ({osm_key[:8]}...)")
        return _empty_result()

    building_arr = _rasterize_buildings(
        (osm_data.get("buildings") or {}).get("features") or [],
        (W, S, E, N), PW, PH,
    )
    road_arr = _rasterize_roads(
        (osm_data.get("roads") or {}).get("features") or [],
        coords_to_px, PW, PH, m_per_px,
    )
    ww_arr = _rasterize_waterways(
        (osm_data.get("waterways") or {}).get("features") or [],
        coords_to_px, PW, PH, m_per_px,
    )
    wall_arr = _rasterize_walls(
        (osm_data.get("walls") or {}).get("features") or [],
        coords_to_px, PW, PH, m_per_px,
    )

    return {
        "buildings":  building_arr.ravel().tolist(),
        "roads":      road_arr.ravel().tolist(),
        "waterways":  ww_arr.ravel().tolist(),
        "walls":      wall_arr.ravel().tolist(),
        "width":      PW,
        "height":     PH,
    }


_CITY_CHANNELS = ("buildings", "roads", "waterways", "walls")


def _city_raster_arrays(req: CompositeCityRasterRequest) -> dict:
    """Return the four unprojected city channels as 2-D float32 arrays.

    Reads the disk cache first and rasterizes on a miss, writing the raw result
    back.  Projection is deliberately absent here and absent from the cache
    key: the raw raster is cached once per (bbox, size, detail) and each caller
    projects it fresh.  ``detail`` is part of the key because the coarse tier
    drops small buildings, so the two tiers are different rasters.
    """
    comp_key = make_cache_key(
        "composite", req.north, req.south, req.east, req.west,
        # "burn": 2 = buildings burnt with max + holes (was additive, holes ignored)
        {"w": req.width, "h": req.height, "detail": req.detail, "burn": 2},
    )
    cached = read_array_cache("composite", comp_key)
    if cached:
        arrays, meta = cached
        logger.debug(f"Composite city-raster cache hit: {comp_key[:8]}...")
        h = int(meta.get("height", req.height))
        w = int(meta.get("width", req.width))
        return {name: np.asarray(arrays[name], dtype=np.float32).reshape(h, w)
                for name in _CITY_CHANNELS}

    result = _rasterize_city(req)
    pw, ph = result["width"], result["height"]
    out = {name: np.asarray(result[name], dtype=np.float32).reshape(ph, pw)
           for name in _CITY_CHANNELS}
    try:
        write_array_cache("composite", comp_key, out,
                          {"width": pw, "height": ph})
    except Exception as e:
        logger.warning(f"Failed to cache composite city-raster: {e}")
    return out


def _make_city_layer_source(channel: str):
    """Build a geo2stl layer provider for one OSM channel."""

    def provider(north, south, east, west, dim, options):
        req = CompositeCityRasterRequest(
            north=north, south=south, east=east, west=west,
            width=dim, height=dim,
            detail=str((options or {}).get("detail", "full")),
        )
        return _city_raster_arrays(req)[channel].astype(np.float64)

    provider.__name__ = f"city_layer_source_{channel}"
    return provider


def register_city_layer_sources() -> None:
    """Expose the OSM channels to geo2stl's fetch_layer_data.

    geo2stl must not import from app.server - the dependency runs the other
    way - and these rasterizers need the server-side OSM cache, so the server
    pushes them into the registry instead.  Called at import, and safe to call
    again because registering a name replaces it.
    """
    from geo2stl.dem import register_layer_source
    for channel in _CITY_CHANNELS:
        register_layer_source(f"osm_{channel}",
                              _make_city_layer_source(channel))


register_city_layer_sources()


def register_water_sources() -> None:
    """Register the terrain-relative water sources (F-REGION).

    ``hydrorivers`` and ``natural_earth_rivers`` live in geo2stl; ``lakes``
    needs the OSM fetch in city2stl, which geo2stl must not import, so the
    server supplies it (``city2stl.fetch.fetch_osm_lakes``, disk-cached).
    """
    from city2stl.fetch import fetch_osm_lakes
    from geo2stl.water_layers import register_water_layer_sources
    register_water_layer_sources(fetch_lakes=fetch_osm_lakes)


register_water_sources()


@router.post("/api/composite/city-raster")
async def get_city_raster(req: CompositeCityRasterRequest):
    """
    Rasterize OSM features to height-delta grids using PIL.
    Returns normalized arrays (scale=1); client applies slider weights.

    Supports ``projection`` and ``clip_valid_region`` for uniform pipeline
    alignment with all other raster layers (DEM, water, hydrology, satellite,
    city).  Projection is applied fresh on every request, never cached.
    """
    def _json_safe_flat(arr: np.ndarray) -> list[float]:
        safe = np.nan_to_num(arr, nan=0.0, posinf=0.0,
                             neginf=0.0).astype(np.float32)
        return safe.ravel().tolist()

    clip_valid_region = req.clip_valid_region

    out = await run_sync(_city_raster_arrays, req)

    if req.projection != "none":
        from geo2stl.projections import project_grid
        for name in _CITY_CHANNELS:
            out[name] = project_grid(
                out[name], req.north, req.south, req.east, req.west,
                req.projection, clip_valid_region, categorical=False,
                maintain_dimensions=req.maintain_dimensions,
            )

    ph, pw = out["buildings"].shape
    payload = {name: _json_safe_flat(out[name]) for name in _CITY_CHANNELS}
    payload["width"] = pw
    payload["height"] = ph
    return JSONResponse(content=payload)


# ---------------------------------------------------------------------------
# DEM layer merge — composite multiple elevation/mask layers
# ---------------------------------------------------------------------------

#: Bumped when the composite's arithmetic changes, so older cached grids are not
#: served: 2 = projected base grid kept (not stretched to dim), rivers snapped to
#: the valley floor, lakes levelled after the median (F-REGION step 4).
COMPOSITE_CACHE_VERSION = 6   # 6: still-water grid cached; 5: large-box lakes from ESA water
#: Layers whose cells are standing water (water_out["still"] of compute_composite_dem).
STILL_WATER_SOURCES = ("lakes", "water_esa")
# 4: sea mask from the unweighted base (3 erased weight-0 previews)


def _composite_cache_key(north: float, south: float, east: float, west: float,
                         dim: int, layers: list,
                         projection: str = "none",
                         clip_valid_region: bool = True,
                         maintain_dimensions: bool = False) -> str:
    """Stable hash of (bbox, dim, layer specs, projection). Used as cache key.

    The projection belongs in the key because compute_composite_dem projects
    each layer before blending, so two projections of the same layer stack
    are different grids.
    """
    from app.server.core.cache import make_cache_key
    # Render each spec to a JSON-serializable form for stable hashing
    spec_dicts = [model_to_dict(spec) for spec in layers]
    return make_cache_key("composite", north, south, east, west,
                          {"v": COMPOSITE_CACHE_VERSION, "dim": dim, "layers": spec_dicts,
                           "projection": projection,
                           "clip": bool(clip_valid_region),
                           "maintain": bool(maintain_dimensions)})


#: How long a composite built with a skipped (failed) layer is reused before
#: the failed source is tried again (seconds).
RETRY_SKIPPED_S = 15 * 60


_inflight_guard = threading.Lock()
_inflight: dict[str, list] = {}     # cache key -> [lock, number of callers holding or waiting]


@contextmanager
def _single_flight(key: str):
    """One computation per key at a time: a second identical request waits, then hits the cache.

    Opening Colombia (20 x 20 deg, 126k river reaches) sent the same dem-merge twice;
    both computed the whole composite side by side for ~6.5 min each.
    """
    with _inflight_guard:
        entry = _inflight.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _inflight_guard:
            entry[1] -= 1
            if entry[1] == 0:
                _inflight.pop(key, None)


def compute_composite_dem(bbox: dict, dim: int, layers: list,
                          projection: str = "none",
                          clip_valid_region: bool = True,
                          maintain_dimensions: bool = False,
                          *, split_carve: bool = False,
                          warnings: list | None = None,
                          water_out: dict | None = None):
    """Run the dem-merge pipeline and return a numpy array.

    Used both by the HTTP endpoint and inline by the export pipeline so the
    3D model is rendered from the same composite the user configured.
    Caches results on disk under the ``composite`` namespace keyed by
    (bbox, dim, layers, projection) so the same spec hits cache on subsequent
    requests.

    Each layer is fetched, projected onto the requested map projection, put
    through its own processing pipeline, and blended onto the running
    composite.  The first layer sets the output grid; later layers are resized
    onto it by ``blend_layers``.

    *layers* accepts MergeLayerSpec objects or the plain dicts an export
    request carries; dicts are coerced so processing specs behave the same
    either way.

    Terrain-relative sources (rivers, lakes: ``geo2stl.water_layers``) blended
    with ``add`` are fetched on the base layer's raw grid, projected with
    nearest-neighbour, resized keeping the deepest value, and combined into a
    separate *carve* grid (deeper wins where they overlap). The result is
    ``composite + carve``; with ``split_carve=True`` it is ``(composite,
    carve)`` instead, so the mesh export can add the carve after its median
    filter, which would otherwise erase a one-pixel channel.

    A layer after the first whose source fails (``water_esa`` without Earth
    Engine, a download error) is skipped instead of failing the whole stack:
    the Region preset leaves the ESA water channel on, and one unavailable
    optional service used to cost the user every river and lake as well (the
    export fell back to the plain DEM, Apply to DEM returned 500). Each skip is
    logged and appended to *warnings* (the dem-merge response returns them).
    A composite with a skipped layer is cached with the skips recorded and
    reused for :data:`RETRY_SKIPPED_S` only (so pre-flight, preview and export
    agree and an Overpass outage is not waited out three times), then rebuilt.
    The base layer failing still raises.

    *water_out*, when given, receives ``"still"``: a boolean grid of standing water
    (cells the ``lakes`` layer carves, or the ``water_esa`` mask covers) on the
    composite grid, so the 3D viewer colours lakes like the Edit map (cached too).
    """
    from app.server.schemas import MergeLayerSpec

    def _result(composite, carve):
        if split_carve:
            return composite, carve
        return composite + carve

    north = bbox.get("north")
    south = bbox.get("south")
    east = bbox.get("east")
    west = bbox.get("west")
    if None in (north, south, east, west):
        raise ValueError("bbox must contain north/south/east/west")

    specs = [spec if hasattr(spec, "source") else MergeLayerSpec(**spec)
             for spec in layers]
    clip_valid = bool(clip_valid_region)

    cache_key = _composite_cache_key(north, south, east, west, dim, specs,
                                     projection, clip_valid,
                                     maintain_dimensions)
    from geo2stl.perf import perf_step
    with _single_flight(cache_key), perf_step("composite", key=cache_key,
                                              sources=[s.source for s in specs], dim=dim):
        return _composite_for_key(cache_key, specs, north, south, east, west, dim,
                                  projection, clip_valid, maintain_dimensions,
                                  _result, warnings, water_out)


def _composite_for_key(cache_key, specs, north, south, east, west, dim, projection,
                       clip_valid, maintain_dimensions, _result, warnings, water_out):
    """compute_composite_dem's body, run under its single-flight lock."""
    import cv2 as _cv2

    from app.server.config import TEST_MODE
    from app.server.core.cache import read_array_cache, write_array_cache
    from geo2stl.dem import (
        apply_layer_processing,
        blend_layers,
        fetch_layer_data,
        is_terrain_relative_source,
        upsample_dem,
    )
    from geo2stl.water_layers import resize_relative

    cached = read_array_cache("composite", cache_key)
    if cached is not None and cached[0].get("composite") is not None:
        skipped_before = list(cached[1].get("skipped") or [])
        if skipped_before and (time.time() - float(cached[1].get("_cached_at", 0))
                               > RETRY_SKIPPED_S):
            cached = None           # retry the layers that failed last time
        elif skipped_before and warnings is not None:
            warnings.extend(skipped_before)
    if cached is not None and cached[0].get("composite") is not None:
        logger.debug("Composite cache hit (%s)", cache_key[:8])
        hit = cached[0]["composite"]
        carve = cached[0].get("carve")
        still = cached[0].get("still")
        if water_out is not None and still is not None and still.shape == hit.shape:
            water_out["still"] = still.astype(bool)
        return _result(hit, carve if carve is not None and carve.shape == hit.shape
                       else np.zeros_like(hit))

    carve = None
    terrain = None
    still_parts = []   # standing-water grids (any shape), resized onto the composite below
    skipped: list[str] = []
    if TEST_MODE:
        h = w = dim
        composite = np.linspace(0, 100, h * w, dtype=np.float64).reshape(h, w)
    else:
        composite = None
        base_raw = None
        relative = []   # (processed, weight) of terrain-relative layers
        for spec in specs:
            is_relative = (composite is not None and spec.blend_mode == "add"
                           and is_terrain_relative_source(spec.source))
            try:
                raw = fetch_layer_data(spec.source, north, south, east, west,
                                       spec.dim, spec.options,
                                       base=base_raw if is_relative else None)
            except Exception as exc:  # noqa: BLE001 - see the docstring
                if composite is None:
                    raise
                msg = f"Layer '{spec.source}' skipped: {type(exc).__name__}: {exc}"[:300]
                logger.warning("Composite: %s", msg)
                skipped.append(msg)
                continue
            if base_raw is None:
                # Same order as /api/terrain/dem: upsample the raw grid to *dim*,
                # then project. The terrain-relative layers rasterise on it.
                raw = upsample_dem(raw, dim)
                base_raw = raw

            if projection and projection != "none":
                from geo2stl.projections import project_grid
                raw = project_grid(raw, north, south, east, west, projection,
                                   clip_valid, categorical=is_relative,
                                   maintain_dimensions=maintain_dimensions)

            processed = apply_layer_processing(raw, spec.processing)

            if spec.source in STILL_WATER_SOURCES:
                still_parts.append((spec.source, processed))
            if is_relative:
                relative.append((processed, float(spec.weight)))
            elif composite is None:
                # The first layer sets the grid: the projected grid itself, as
                # /api/terrain/dem returns it (a cosine projection narrows the
                # longer side below *dim*). Stretching it back to *dim* made
                # Apply to DEM widen the model by 1/cos(lat) - a 403 mm Grand
                # Canyon became 500 mm - and interpolated every cell. Only a grid
                # larger than *dim* is resized (down).
                h, w = processed.shape
                composite = processed.astype(np.float64)
                if max(h, w) > dim:
                    if h >= w:
                        out_h, out_w = dim, max(1, int(dim * w / h))
                    else:
                        out_w, out_h = dim, max(1, int(dim * h / w))
                    composite = _cv2.resize(
                        composite.astype(np.float32), (out_w, out_h),
                        interpolation=_cv2.INTER_AREA).astype(np.float64)
                # The terrain itself, before its weight: the open-sea mask for the
                # carve below must not depend on the weight (the 2D river preview
                # sends weight 0, which made every cell "sea" and erased the rivers).
                terrain = composite.copy()
                # Its weight scales it, so the panel's DEM weight means the same
                # thing here as in the browser; blend_layers never sees this layer.
                composite = composite * float(spec.weight)
            else:
                composite = blend_layers(
                    base=composite, layer=processed,
                    blend_mode=spec.blend_mode, weight=spec.weight,
                    output_shape=composite.shape)

        if composite is None:
            raise RuntimeError("No layers produced output")

        composite = np.nan_to_num(composite, nan=0.0,
                                  posinf=np.finfo(np.float32).max,
                                  neginf=np.finfo(np.float32).min)
        for processed, weight in relative:
            layer = np.minimum(np.nan_to_num(
                resize_relative(processed, composite.shape), nan=0.0), 0.0) * weight
            carve = layer if carve is None else np.minimum(carve, layer)

    if carve is None:
        carve = np.zeros_like(composite)
    elif not TEST_MODE and terrain is not None:
        # Rivers and lakes cut land only: carve that reaches the open sea (river
        # mouths, coast-hugging reaches) dents the sea floor along the shore and
        # leaves a ring around the coast once subtracted.
        from geo2stl.water_layers import ocean_mask
        sea = ocean_mask(terrain)
        if sea.any():
            carve = np.where(sea, 0.0, carve)
    still = np.zeros(composite.shape, dtype=bool)
    for source, grid in still_parts:
        g = np.nan_to_num(np.asarray(grid, dtype=np.float32), nan=0.0)
        if g.shape != composite.shape:
            g = _cv2.resize(g, (composite.shape[1], composite.shape[0]),
                            interpolation=_cv2.INTER_NEAREST)
        still |= (g < 0) if source == "lakes" else (g > 0.5)
    if water_out is not None:
        water_out["still"] = still
    if skipped and warnings is not None:
        warnings.extend(skipped)
    write_array_cache("composite", cache_key, {"composite": composite, "carve": carve,
                                               "still": still.astype(np.uint8)},
                      {"skipped": skipped} if skipped else None)
    logger.info("Composite cached (%s, %s%s)", cache_key[:8], composite.shape,
                f", {len(skipped)} layer(s) skipped" if skipped else "")
    return _result(composite, carve)


@router.post("/api/composite/dem-merge")
async def merge_dem_layers(req: MergeRequest):
    """
    Merge multiple elevation/mask layers into one composite DEM.
    Each layer specifies a source, resolution, per-layer processing, and a blend mode.
    Result is cached server-side; the same spec is reused inline by the
    3D preview / export endpoints when ``composite_layers`` is included
    in those requests.
    """
    from app.server.core.validation import b64_encode

    if not req.layers:
        return JSONResponse(content={"error": "At least one layer required"}, status_code=422)
    if any(req.bbox.get(k) is None for k in ("north", "south", "east", "west")):
        return JSONResponse(content={"error": "bbox must contain north/south/east/west"}, status_code=422)

    warnings: list[str] = []
    try:
        composite = await run_sync(compute_composite_dem,
                                   req.bbox, req.dim, list(req.layers),
                                   req.projection, req.clip_valid_region,
                                   req.maintain_dimensions,
                                   warnings=warnings)
        h, w = composite.shape
        return JSONResponse(content={
            "dem_values_b64": b64_encode(composite),
            "dimensions": [h, w],
            "min_elevation": float(np.nanmin(composite)),
            "max_elevation": float(np.nanmax(composite)),
            "mean_elevation": float(np.nanmean(composite)),
            "bbox": [req.bbox["west"], req.bbox["south"],
                     req.bbox["east"], req.bbox["north"]],
            "source": "merge", "layer_count": len(req.layers),
            "warnings": warnings,
        })
    except Exception as e:
        logger.error(f"DEM merge failed: {e}", exc_info=True)
        return JSONResponse(content={"error": "DEM merge failed"}, status_code=500)
