"""
Composite DEM routes — composition operations that combine data layers.

POST /api/composite/city-raster
  Reads OSM buildings/roads/waterways from the disk cache (written by
  /api/cities) and rasterizes them into per-pixel height-delta arrays using
  PIL/Pillow.  This is ~50x faster than the equivalent JS scanline fill.

  Weights / scales are NOT applied server-side — the client multiplies these
  normalized arrays by the slider values.  This means only a bbox or dimension
  change triggers a new backend call; all slider adjustments are instant
  client-side multiplications.

  Input:  { north, south, east, west, width, height }
  Output: { buildings, roads, waterways, walls, width, height }
            each is a flat float32 list at (width x height) pixels.
              buildings  — per-pixel building height in metres  (scale=1)
              roads      ��� binary road mask (0 or 1)
              waterways  — binary waterway mask (0 or 1)
              walls      — per-pixel wall height in metres  (scale=1)

  Cached under namespace "composite" by (bbox, width, height).

POST /api/composite/dem-merge
  Merge multiple elevation/mask layers into one composite DEM with
  per-layer processing (clip, smooth, sharpen, normalize) and blend modes.

POST /api/composite/hydrology-merge
  Merge river depression values into a DEM elevation grid.
"""

from app.server.core.validation import run_sync, METRES_PER_DEGREE
from app.server.core.cache import make_cache_key, osm_cache_key, read_array_cache, write_array_cache, read_osm_cache
from app.server.schemas import HydrologyMergeRequest, MergeRequest
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from fastapi import APIRouter
import numpy as np
import logging

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
    clip_valid_region: bool | None = None
    clip_nans: bool = True


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

def _rasterize_buildings(features, coords_to_px, PW, PH):
    """Return a float32 array (PH×PW) with per-pixel building height in metres."""
    from PIL import Image, ImageDraw
    arr = np.zeros((PH, PW), dtype=np.float32)
    for feat in features:
        geom = feat.get("geometry") or {}
        h_m = float((feat.get("properties") or {}).get("height_m") or 10)
        rings = []
        if geom.get("type") == "Polygon":
            rings = [geom["coordinates"][0]]
        elif geom.get("type") == "MultiPolygon":
            rings = [p[0] for p in geom["coordinates"]]
        for ring in rings:
            if not ring:
                continue
            px = coords_to_px(ring)
            mask = Image.new("1", (PW, PH), 0)
            ImageDraw.Draw(mask).polygon(px, fill=1)
            arr += np.array(mask, dtype=np.float32) * h_m
    return arr


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
    m_per_px = (lon_span * np.cos(np.radians(lat_mid))
                * METRES_PER_DEGREE) / PW

    _, coords_to_px = _make_geo_to_px(N, S, E, W, PW, PH)

    osm_key = osm_cache_key(N, S, E, W)
    osm_data = read_osm_cache(osm_key)
    if not osm_data:
        logger.debug(
            f"No OSM cache for composite city-raster ({osm_key[:8]}...)")
        return _empty_result()

    building_arr = _rasterize_buildings(
        (osm_data.get("buildings") or {}).get("features") or [],
        coords_to_px, PW, PH,
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


@router.post("/api/composite/city-raster")
async def get_city_raster(req: CompositeCityRasterRequest):
    """
    Rasterize OSM features to height-delta grids using PIL.
    Returns normalized arrays (scale=1); client applies slider weights.

    Supports ``projection`` and ``clip_valid_region`` for uniform pipeline alignment
    with all other raster layers (DEM, water, hydrology, satellite, city).
    """
    def _json_safe_flat(arr: np.ndarray) -> list[float]:
        safe = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
        return safe.ravel().tolist()

    clip_valid_region = req.clip_valid_region if req.clip_valid_region is not None else req.clip_nans
    # Note: Cache key does NOT include projection or clip_valid_region.
    # Raw city raster is cached once per bbox; projection/clipping applied on fetch.
    comp_key = make_cache_key(
        "composite", req.north, req.south, req.east, req.west,
        {"w": req.width, "h": req.height}
    )
    cached = read_array_cache("composite", comp_key)
    if cached:
        arrays, meta = cached
        logger.debug(f"Composite city-raster cache hit: {comp_key[:8]}...")
        h = int(meta.get("height", req.height))
        w = int(meta.get("width", req.width))
        out = {
            "buildings": np.array(arrays["buildings"], dtype=np.float32).reshape(h, w),
            "roads": np.array(arrays["roads"], dtype=np.float32).reshape(h, w),
            "waterways": np.array(arrays["waterways"], dtype=np.float32).reshape(h, w),
            "walls": np.array(arrays["walls"], dtype=np.float32).reshape(h, w),
        }

        # Projection/clipping is always applied fresh from raw cached arrays.
        if req.projection != "none":
            from geo2stl.projections import project_grid
            for lname in ["buildings", "roads", "waterways", "walls"]:
                out[lname] = project_grid(
                    out[lname],
                    req.north, req.south, req.east, req.west,
                    req.projection, clip_valid_region, categorical=False,
                )

        ph, pw = out["buildings"].shape
        return JSONResponse(content={
            "buildings":  _json_safe_flat(out["buildings"]),
            "roads":      _json_safe_flat(out["roads"]),
            "waterways":  _json_safe_flat(out["waterways"]),
            "walls":      _json_safe_flat(out["walls"]),
            "width":      pw,
            "height":     ph,
        })

    result = await run_sync(_rasterize_city, req)

    # Cache raw unprojected result (cache key has NO projection/clip params)
    # Projection and clipping are applied fresh on every request
    PW, PH = result["width"], result["height"]
    try:
        write_array_cache("composite", comp_key, {
            "buildings":  np.array(result["buildings"], dtype=np.float32).reshape(PH, PW),
            "roads":      np.array(result["roads"], dtype=np.float32).reshape(PH, PW),
            "waterways":  np.array(result["waterways"], dtype=np.float32).reshape(PH, PW),
            "walls":      np.array(result["walls"], dtype=np.float32).reshape(PH, PW),
        }, {"width": PW, "height": PH})
    except Exception as e:
        logger.warning(f"Failed to cache composite city-raster: {e}")

    # Apply map projection (all raster layers share the same pipeline)
    # This is applied FRESH on every request, not cached
    if req.projection != "none":
        from geo2stl.projections import project_grid
        layer_names = ["buildings", "roads", "waterways", "walls"]
        PW, PH = result["width"], result["height"]
        for lname in layer_names:
            arr = np.array(result[lname], dtype=np.float32).reshape(PH, PW)
            arr = project_grid(arr, req.north, req.south, req.east, req.west,
                               req.projection, clip_valid_region, categorical=False)
            result[lname] = _json_safe_flat(arr)
        # Update dimensions to projected output size
        result["height"], result["width"] = arr.shape

    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# DEM layer merge — composite multiple elevation/mask layers
# ---------------------------------------------------------------------------

def _composite_cache_key(north: float, south: float, east: float, west: float,
                          dim: int, layers: list) -> str:
    """Stable hash of (bbox, dim, layer specs). Used as cache key."""
    from app.server.core.cache import make_cache_key
    # Render each spec to a JSON-serializable form for stable hashing
    spec_dicts = []
    for spec in layers:
        if hasattr(spec, "model_dump"):
            spec_dicts.append(spec.model_dump())
        elif hasattr(spec, "dict"):
            spec_dicts.append(spec.dict())
        elif isinstance(spec, dict):
            spec_dicts.append(spec)
        else:
            spec_dicts.append(dict(spec))
    return make_cache_key("composite", north, south, east, west,
                          {"dim": dim, "layers": spec_dicts})


def compute_composite_dem(bbox: dict, dim: int, layers: list) -> "np.ndarray":
    """Run the dem-merge pipeline and return a numpy array.

    Used both by the HTTP endpoint and inline by the export pipeline so the
    3D model is rendered from the same composite the user configured.
    Caches results on disk under the ``composite`` namespace keyed by
    (bbox, dim, layers) so the same spec hits cache on subsequent requests.
    """
    from geo2stl.dem import (
        fetch_layer_data, apply_layer_processing, blend_layers,
    )
    from app.server.core.cache import read_array_cache, write_array_cache
    from app.server.config import TEST_MODE
    import cv2 as _cv2

    north = bbox.get("north")
    south = bbox.get("south")
    east = bbox.get("east")
    west = bbox.get("west")
    if None in (north, south, east, west):
        raise ValueError("bbox must contain north/south/east/west")

    cache_key = _composite_cache_key(north, south, east, west, dim, layers)
    cached = read_array_cache("composite", cache_key)
    if cached is not None and cached[0].get("composite") is not None:
        logger.debug("Composite cache hit (%s)", cache_key[:8])
        return cached[0]["composite"]

    if TEST_MODE:
        h = w = dim
        composite = np.linspace(0, 100, h * w, dtype=np.float64).reshape(h, w)
    else:
        composite = None
        for spec in layers:
            source = getattr(spec, "source", None) or spec["source"]
            spec_dim = getattr(spec, "dim", None) or spec["dim"]
            blend_mode = getattr(spec, "blend_mode", None) or spec.get("blend_mode", "blend")
            weight = getattr(spec, "weight", None) if hasattr(spec, "weight") else spec.get("weight", 1.0)
            processing = getattr(spec, "processing", None) or spec.get("processing")

            raw = fetch_layer_data(source, north, south, east, west, spec_dim)
            processed = apply_layer_processing(raw, processing)

            if composite is None:
                h, w = processed.shape
                if h >= w:
                    out_h, out_w = dim, max(1, int(dim * w / h))
                else:
                    out_w, out_h = dim, max(1, int(dim * h / w))
                composite = _cv2.resize(
                    processed.astype(np.float32), (out_w, out_h),
                    interpolation=_cv2.INTER_LINEAR).astype(np.float64)
            else:
                composite = blend_layers(
                    base=composite, layer=processed,
                    blend_mode=blend_mode, weight=weight,
                    output_shape=composite.shape)

        if composite is None:
            raise RuntimeError("No layers produced output")

        composite = np.nan_to_num(composite, nan=0.0,
                                  posinf=np.finfo(np.float32).max,
                                  neginf=np.finfo(np.float32).min)

    write_array_cache("composite", cache_key, {"composite": composite})
    logger.info("Composite cached (%s, %s)", cache_key[:8], composite.shape)
    return composite


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

    try:
        composite = await run_sync(compute_composite_dem,
                                   req.bbox, req.dim, list(req.layers))
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
        })
    except Exception as e:
        logger.error(f"DEM merge failed: {e}", exc_info=True)
        return JSONResponse(content={"error": "DEM merge failed"}, status_code=500)


# ---------------------------------------------------------------------------
# Hydrology merge — combine river depressions with a DEM grid
# ---------------------------------------------------------------------------

@router.post("/api/composite/hydrology-merge")
async def merge_hydrology(req: HydrologyMergeRequest):
    """
    Merge hydrology depression values into a DEM elevation grid.

    Both arrays must have identical dimensions. River depression values
    (negative) are added to the DEM via element-wise minimum.
    """
    from app.server.core.hydrology import merge_rivers_with_dem
    from app.server.core.validation import b64_encode
    from app.server.config import TEST_MODE

    dem_values = req.dem_values
    dem_dims = req.dem_dimensions
    river_values = req.river_grid_values
    river_dims = req.river_grid_dimensions

    # Settings-only mode: resolve DEM from cache
    if not dem_values and req.bbox:
        from app.server.core.export import resolve_dem_from_cache
        req_dict = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        resolved = resolve_dem_from_cache(req_dict)
        if resolved:
            dem_values_list, h, w = resolved
            dem_values = dem_values_list
            dem_dims = [h, w]
        else:
            return JSONResponse(content={"error": "DEM not in cache — load DEM first"}, status_code=400)

    # Resolve hydrology from cache
    if not river_values and req.bbox:
        from app.server.core.cache import make_cache_key, read_array_cache
        bbox = req.bbox
        hydro_key = make_cache_key("hydrology", bbox["north"], bbox["south"],
                                   bbox["east"], bbox["west"])
        cached = read_array_cache("hydrology", hydro_key)
        if cached and cached[0].get("river_grid") is not None:
            rg = cached[0]["river_grid"]
            river_values = rg.ravel().tolist()
            river_dims = list(rg.shape)
        else:
            return JSONResponse(content={"error": "Hydrology not in cache — load hydrology first"}, status_code=400)

    if not dem_values or not dem_dims or not river_values or not river_dims:
        return JSONResponse(content={"error": "Missing DEM or river data"}, status_code=400)

    dem_h, dem_w = dem_dims
    river_h, river_w = river_dims

    try:
        dem_arr = np.array(dem_values, dtype=np.float32).reshape(dem_h, dem_w)
        river_arr = np.array(river_values, dtype=np.float32).reshape(river_h, river_w)
    except Exception as e:
        return JSONResponse(content={"error": f"Failed to reshape arrays: {e}"}, status_code=400)

    if dem_arr.shape != river_arr.shape:
        return JSONResponse(
            content={"error": f"DEM shape {dem_arr.shape} != river shape {river_arr.shape}"},
            status_code=400)

    if TEST_MODE:
        return JSONResponse(content={
            "merged_dem_b64": b64_encode(dem_arr),
            "merged_dimensions": [dem_h, dem_w],
        })

    try:
        merged = await run_sync(merge_rivers_with_dem, dem_arr, river_arr)
        if merged is None:
            return JSONResponse(content={"error": "Merge operation failed"}, status_code=500)
        return JSONResponse(content={
            "merged_dem_b64": b64_encode(merged),
            "merged_dimensions": [merged.shape[0], merged.shape[1]],
        })
    except Exception as e:
        logger.error(f"Hydrology merge failed: {e}", exc_info=True)
        return JSONResponse(content={"error": "Hydrology merge failed"}, status_code=500)
