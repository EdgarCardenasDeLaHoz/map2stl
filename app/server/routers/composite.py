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

from app.server.core.validation import run_sync
from app.server.core.cache import make_cache_key, osm_cache_key, read_array_cache, write_array_cache, read_osm_cache
from app.server.schemas import CompositeCityRasterRequest, HydrologyMergeRequest, MergeRequest
from fastapi.responses import JSONResponse
from fastapi import APIRouter
import numpy as np
import logging
from city2stl.rasterize import rasterize_composite_layers

logger = logging.getLogger(__name__)
router = APIRouter(tags=["composite"])


def _rasterize_city(req: CompositeCityRasterRequest) -> dict:
    """Synchronous rasterization wrapper — called via run_in_executor."""
    osm_key = osm_cache_key(req.north, req.south, req.east, req.west,
                            req.simplify_tolerance, req.min_area,
                            req.m_per_level)
    osm_data = read_osm_cache(osm_key)
    if not osm_data:
        logger.debug(
            f"No OSM cache for composite city-raster ({osm_key[:8]}...)")
    return rasterize_composite_layers(
        north=req.north,
        south=req.south,
        east=req.east,
        west=req.west,
        width=req.width,
        height=req.height,
        osm_data=osm_data,
    )


@router.post("/api/composite/city-raster")
async def get_city_raster(req: CompositeCityRasterRequest):
    """
    Rasterize OSM features to height-delta grids using PIL.
    Returns normalized arrays (scale=1); client applies slider weights.

    Supports ``projection`` and ``clip_nans`` for uniform pipeline alignment
    with all other raster layers (DEM, water, hydrology, satellite, city).
    """
    comp_key = make_cache_key(
        "composite", req.north, req.south, req.east, req.west,
        {"w": req.width, "h": req.height,
         "proj": req.projection, "cn": req.clip_nans,
         "mpl": req.m_per_level, "tol": req.simplify_tolerance}
    )
    cached = read_array_cache("composite", comp_key)
    if cached:
        arrays, meta = cached
        logger.debug(f"Composite city-raster cache hit: {comp_key[:8]}...")
        return JSONResponse(content={
            "buildings":  arrays["buildings"].ravel().tolist(),
            "roads":      arrays["roads"].ravel().tolist(),
            "waterways":  arrays["waterways"].ravel().tolist(),
            "walls":      arrays["walls"].ravel().tolist(),
            "width":      int(meta.get("width",  req.width)),
            "height":     int(meta.get("height", req.height)),
        })

    result = await run_sync(_rasterize_city, req)

    # Write to disk cache (30-day TTL via "composite" namespace)
    PW, PH = result["width"], result["height"]
    try:
        write_array_cache("composite", comp_key, {
            "buildings":  np.array(result["buildings"], dtype=np.float32).reshape(PH, PW),
            "roads":      np.array(result["roads"],     dtype=np.float32).reshape(PH, PW),
            "waterways":  np.array(result["waterways"], dtype=np.float32).reshape(PH, PW),
            "walls":      np.array(result["walls"],     dtype=np.float32).reshape(PH, PW),
        }, {"width": PW, "height": PH})
    except Exception as e:
        logger.warning(f"Failed to cache composite city-raster: {e}")

    # Apply map projection (all raster layers share the same pipeline)
    if req.projection != "none":
        from geo2stl.projections import project_grid
        layer_names = ["buildings", "roads", "waterways", "walls"]
        PW, PH = result["width"], result["height"]
        for lname in layer_names:
            arr = np.array(result[lname], dtype=np.float32).reshape(PH, PW)
            arr = project_grid(arr, req.north, req.south, req.east, req.west,
                               req.projection, req.clip_nans, categorical=False)
            result[lname] = arr.ravel().tolist()
        # Update dimensions to projected output size
        result["height"], result["width"] = arr.shape

    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# DEM layer merge — composite multiple elevation/mask layers
# ---------------------------------------------------------------------------

@router.post("/api/composite/dem-merge")
async def merge_dem_layers(req: MergeRequest):
    """
    Merge multiple elevation/mask layers into one composite DEM.
    Each layer specifies a source, resolution, per-layer processing, and a blend mode.
    """
    from geo2stl.dem import (
        fetch_layer_data, apply_layer_processing, blend_layers,
    )
    from app.server.core.validation import b64_encode
    from app.server.config import TEST_MODE

    if not req.layers:
        return JSONResponse(content={"error": "At least one layer required"}, status_code=422)

    north = req.bbox.get("north")
    south = req.bbox.get("south")
    east = req.bbox.get("east")
    west = req.bbox.get("west")
    if None in (north, south, east, west):
        return JSONResponse(content={"error": "bbox must contain north/south/east/west"}, status_code=422)

    if TEST_MODE:
        h = w = req.dim
        im = np.linspace(0, 100, h * w, dtype=np.float64).reshape(h, w)
        return JSONResponse(content={
            "dem_values_b64": b64_encode(im),
            "dimensions": [h, w],
            "min_elevation": 0.0, "max_elevation": 100.0, "mean_elevation": 50.0,
            "bbox": [west, south, east, north],
            "source": "merge", "layer_count": len(req.layers),
        })

    try:
        import cv2 as _cv2
        composite = None

        for spec in req.layers:
            raw = await run_sync(
                fetch_layer_data, spec.source, north, south, east, west, spec.dim)
            processed = await run_sync(
                apply_layer_processing, raw, spec.processing)

            if composite is None:
                h, w = processed.shape
                if h >= w:
                    out_h, out_w = req.dim, max(1, int(req.dim * w / h))
                else:
                    out_w, out_h = req.dim, max(1, int(req.dim * h / w))
                composite = _cv2.resize(
                    processed.astype(np.float32), (out_w, out_h),
                    interpolation=_cv2.INTER_LINEAR).astype(np.float64)
            else:
                composite = blend_layers(
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
            "dem_values_b64": b64_encode(composite),
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
    from geo2stl.hydrology import merge_rivers_with_dem
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
