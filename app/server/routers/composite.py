"""
Composite DEM routes — fast city raster contribution endpoint.

POST /api/composite/city-raster
  Reads OSM buildings/roads/waterways from the disk cache (written by
  /api/cities) and rasterizes them into per-pixel height-delta arrays using
  PIL/Pillow.  This is ~50× faster than the equivalent JS scanline fill.

  Weights / scales are NOT applied server-side — the client multiplies these
  normalized arrays by the slider values.  This means only a bbox or dimension
  change triggers a new backend call; all slider adjustments are instant
  client-side multiplications.

  Input:  { north, south, east, west, width, height }
  Output: { buildings, roads, waterways, walls, width, height }
            each is a flat float32 list at (width × height) pixels.
              buildings  — per-pixel building height in metres  (scale=1)
              roads      — binary road mask (0 or 1)
              waterways  — binary waterway mask (0 or 1)
              walls      — per-pixel wall height in metres  (scale=1)

  Cached under namespace "composite" by (bbox, width, height).
"""

import asyncio
import logging
from functools import partial
from pathlib import Path
import sys

# app/server/routers → routers → server → app → strm2stl
_STRM2STL_DIR = str(Path(__file__).parent.parent.parent.parent)
if _STRM2STL_DIR not in sys.path:
    sys.path.insert(0, _STRM2STL_DIR)

import numpy as np
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.server.core.cache import make_cache_key, osm_cache_key, read_array_cache, write_array_cache, read_osm_cache

logger = logging.getLogger(__name__)
router = APIRouter(tags=["composite"])



class CityRasterRequest(BaseModel):
    north:  float
    south:  float
    east:   float
    west:   float
    width:  int = 512
    height: int = 512


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
        geom  = feat.get("geometry") or {}
        h_m   = float((feat.get("properties") or {}).get("height_m") or 10)
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
    img  = Image.new("1", (PW, PH), 0)
    draw = ImageDraw.Draw(img)
    for feat in features:
        geom  = feat.get("geometry") or {}
        w_m   = float((feat.get("properties") or {}).get("road_width_m") or 6)
        w_px  = max(1, round(w_m / m_per_px))
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
    img  = Image.new("1", (PW, PH), 0)
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
        geom  = feat.get("geometry") or {}
        h_m   = float((feat.get("properties") or {}).get("height_m") or 5)
        w_px  = max(1, round(2.0 / m_per_px))
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

def _rasterize_city(req: CityRasterRequest) -> dict:
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

    lat_mid  = (N + S) / 2
    m_per_px = (lon_span * np.cos(np.radians(lat_mid)) * 111320) / PW

    _, coords_to_px = _make_geo_to_px(N, S, E, W, PW, PH)

    osm_key  = osm_cache_key(N, S, E, W)
    osm_data = read_osm_cache(osm_key)
    if not osm_data:
        logger.debug(f"No OSM cache for composite city-raster ({osm_key[:8]}…)")
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
async def get_city_raster(req: CityRasterRequest):
    """
    Rasterize OSM features to height-delta grids using PIL.
    Returns normalized arrays (scale=1); client applies slider weights.
    """
    comp_key = make_cache_key(
        "composite", req.north, req.south, req.east, req.west,
        {"w": req.width, "h": req.height}
    )
    cached = read_array_cache("composite", comp_key)
    if cached:
        arrays, meta = cached
        logger.debug(f"Composite city-raster cache hit: {comp_key[:8]}…")
        return JSONResponse(content={
            "buildings":  arrays["buildings"].ravel().tolist(),
            "roads":      arrays["roads"].ravel().tolist(),
            "waterways":  arrays["waterways"].ravel().tolist(),
            "walls":      arrays["walls"].ravel().tolist(),
            "width":      int(meta.get("width",  req.width)),
            "height":     int(meta.get("height", req.height)),
        })

    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, partial(_rasterize_city, req))

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

    return JSONResponse(content=result)
