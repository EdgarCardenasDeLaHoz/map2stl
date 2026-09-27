"""
city2stl/rasterize.py — Rasterize OSM vector features onto a height-map grid.

Provides pure-computation helpers that burn building, road, and waterway
GeoJSON features onto a float32 numpy grid. No HTTP, cache, or server deps.

Server entry point: app.server.core.osm re-exports all public symbols.
"""

from __future__ import annotations

import logging

import numpy as np

from geo2stl.geo import m_per_deg_lon

logger = logging.getLogger(__name__)

_DEFAULT_BUILDING_HEIGHT_M = 10.0


def _count_verts(g) -> int:
    """Count total exterior vertices in a geometry (for simplification logging)."""
    if g.geom_type == "LineString":
        return len(g.coords)
    if g.geom_type == "MultiLineString":
        return sum(len(ln.coords) for ln in g.geoms)
    if g.geom_type == "Polygon":
        return len(g.exterior.coords)
    if g.geom_type == "MultiPolygon":
        return sum(len(p.exterior.coords) for p in g.geoms)
    return 0


def _empty_fc(error: str = "") -> dict:
    fc: dict = {"type": "FeatureCollection", "features": []}
    if error:
        fc["error"] = error
    return fc


def rasterize_city_data(
    north: float, south: float, east: float, west: float,
    dim: int,
    buildings_geojson: dict,
    roads_geojson: dict,
    waterways_geojson: dict,
    building_scale: float = 1.0,
    road_depression_m: float = 0.0,
    water_depression_m: float = -2.0,
) -> dict:
    """
    Burn OSM vector features onto a dim x dim float32 height-map grid.

    Layer order (painter's algorithm -- later layers overwrite earlier ones):
      1. waterways  -- polygons/lines burned at water_depression_m
      2. roads      -- lines buffered to road_width_m, burned at road_depression_m
      3. buildings  -- polygons burned at height_m * building_scale (np.maximum so tall wins)

    Returns a dict compatible with the DEM response format:
      { values: [float, ...], width, height, vmin, vmax, bbox }
    """
    from rasterio.enums import MergeAlg
    from rasterio.features import rasterize as _rasterize
    from rasterio.transform import from_bounds
    from shapely.geometry import mapping, shape

    transform = from_bounds(west, south, east, north, dim, dim)
    grid = np.zeros((dim, dim), dtype=np.float32)
    # Features that fail to convert or burn are skipped, counted per layer and
    # reported once at the end rather than dropped silently.
    skipped = {"waterways": 0, "roads": 0, "buildings": 0}

    # -- Waterways --------------------------------------------------------
    water_shapes = []
    for feat in (waterways_geojson.get("features") or []):
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            s = shape(geom)
            # Buffer lines to give them 1-pixel minimum width in degree units
            if s.geom_type in ("LineString", "MultiLineString"):
                pixel_deg = (north - south) / dim
                s = s.buffer(pixel_deg * 0.5)
            if not s.is_empty:
                water_shapes.append((mapping(s), water_depression_m))
        except Exception:
            skipped["waterways"] += 1
    if water_shapes:
        try:
            _rasterize(water_shapes, out=grid, transform=transform,
                       merge_alg=MergeAlg.replace, dtype="float32")
        except Exception as e:
            logger.warning(f"rasterize waterways failed: {e}")

    # -- Roads ------------------------------------------------------------
    road_shapes = []
    for feat in (roads_geojson.get("features") or []):
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            width_m = (feat.get("properties") or {}).get("road_width_m", 4.0)
            # Convert metres to degrees (approximate at this latitude)
            mid_lat = (north + south) / 2
            metres_per_deg_lon = m_per_deg_lon(mid_lat)
            buf_deg = (width_m / 2) / metres_per_deg_lon
            s = shape(geom).buffer(max(buf_deg, (north - south) / dim * 0.5))
            if not s.is_empty:
                road_shapes.append((mapping(s), road_depression_m))
        except Exception:
            skipped["roads"] += 1
    if road_shapes:
        try:
            _rasterize(road_shapes, out=grid, transform=transform,
                       merge_alg=MergeAlg.replace, dtype="float32")
        except Exception as e:
            logger.warning(f"rasterize roads failed: {e}")

    # -- Buildings --------------------------------------------------------
    # Burn each building separately and take the maximum so tall buildings
    # win over adjacent shorter ones (can't batch because each has a different value).
    building_shapes = []
    for feat in (buildings_geojson.get("features") or []):
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            # A present-but-null height means "unknown": use the default
            # rather than dropping the building on float(None).
            height_m = (feat.get("properties") or {}).get("height_m")
            if height_m is None:
                height_m = _DEFAULT_BUILDING_HEIGHT_M
            h = float(height_m) * building_scale
            building_shapes.append((mapping(shape(geom)), h))
        except Exception:
            skipped["buildings"] += 1
    if building_shapes:
        for feat_shape, h in building_shapes:
            try:
                tmp = _rasterize(
                    [(feat_shape, h)], out_shape=(dim, dim),
                    transform=transform, fill=0, dtype="float32",
                )
                np.maximum(grid, tmp, out=grid)
            except Exception:
                skipped["buildings"] += 1

    if any(skipped.values()):
        logger.warning("rasterize_city_data skipped features: %s",
                       ", ".join(f"{k}={v}" for k, v in skipped.items() if v))

    vmin = float(grid.min())
    vmax = float(grid.max())
    return {
        "values": grid.flatten().tolist(),
        "width": dim,
        "height": dim,
        "vmin": vmin,
        "vmax": vmax,
        "bbox": {"north": north, "south": south, "east": east, "west": west},
    }
