"""
core/osm.py — OpenStreetMap / Overpass data fetcher.

Extracted from location_picker.py (backend refactor, step 4).
Pure synchronous function — call via asyncio.run_in_executor to avoid
blocking the FastAPI event loop.
"""

from __future__ import annotations

import json
import logging
from typing import List

logger = logging.getLogger(__name__)


def _reduce_buildings(gdf):
    """
    Reduce building polygon count by merging only buildings that physically
    touch or overlap and share the same rounded height.

    Uses a spatial-graph approach: build an adjacency graph from intersecting
    pairs, find connected components, and dissolve each component separately.
    This avoids the unary_union-per-height-group mistake that previously merged
    ALL buildings of the same height into one blob regardless of distance.

    Falls back to the original gdf on any error.
    """
    try:
        import numpy as np

        original_crs = gdf.crs
        gdf = gdf.copy().to_crs(epsg=3857)
        gdf['height_m'] = gdf['height_m'].round(0)
        gdf = gdf.reset_index(drop=True)

        n = len(gdf)
        if n == 0:
            return gdf.to_crs(original_crs)

        # Build adjacency: find pairs that touch/overlap using spatial index
        sindex = gdf.sindex
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            pi, pj = find(i), find(j)
            if pi != pj:
                parent[pi] = pj

        for i, geom in enumerate(gdf.geometry):
            candidates = list(sindex.query(geom, predicate='intersects'))
            for j in candidates:
                if j <= i:
                    continue
                if gdf.at[i, 'height_m'] == gdf.at[j, 'height_m']:
                    union(i, j)

        # Assign component labels and dissolve each component
        gdf['_comp'] = [find(i) for i in range(n)]
        gdf_dissolved = gdf.dissolve(by='_comp').reset_index(drop=True)
        gdf_dissolved['geometry'] = gdf_dissolved.geometry.make_valid()

        # Restore height_m from the group (it was the dissolve value)
        # dissolve keeps the first row's non-geometry columns
        gdf_out = gdf_dissolved.explode(index_parts=False).reset_index(drop=True)
        gdf_out = gdf_out[
            gdf_out.geometry.notna() &
            gdf_out.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])
        ].reset_index(drop=True)

        return gdf_out[['geometry', 'height_m']].to_crs(original_crs)
    except Exception as exc:
        logger.warning(f"_reduce_buildings failed, using raw geometries: {exc}")
        return gdf


def _fill_building_heights(gdf):
    """
    Ensure every building row has a reliable *height_m* column.

    Priority:
      1. OSM ``height`` tag (may be "12 m", "12.5", etc.) — strip units, parse.
      2. ``building:levels`` × 4.0 m per storey (default 3 storeys if missing).
      3. Hard fallback: 10 m.

    Values are clipped to [3, 300] m and rounded to one decimal.
    The original ``height`` / ``building:levels`` columns are left intact.
    """
    try:
        import pandas as pd
    except ImportError:
        # pandas not available — return gdf unchanged
        return gdf

    # Levels-based estimate
    if 'building:levels' in gdf.columns:
        levels = pd.to_numeric(gdf['building:levels'], errors='coerce').fillna(3.0)
    else:
        levels = 3.0
    height_from_levels = levels * 4.0

    # Explicit OSM height (strip trailing unit strings like " m" or "ft")
    if 'height' in gdf.columns:
        raw = gdf['height'].astype(str).str.extract(r'([\d.]+)', expand=False)
        explicit = pd.to_numeric(raw, errors='coerce')
        height_m = explicit.fillna(height_from_levels)
    else:
        height_m = height_from_levels

    height_m = (
        pd.to_numeric(height_m, errors='coerce')
        .fillna(10.0)
        .clip(lower=3.0, upper=300.0)
        .round(1)
    )
    gdf = gdf.copy()
    gdf['height_m'] = height_m
    return gdf


# Road half-widths in metres (used to add road_width_m property to each road feature).
# Values follow city2stl/roads.py; default for unknown types is 3 m.
_HIGHWAY_WIDTHS: dict = {
    'motorway': 12,       'motorway_link': 6,
    'trunk': 10,          'trunk_link': 5,
    'primary': 8,         'primary_link': 4,
    'secondary': 7,       'secondary_link': 3.5,
    'tertiary': 6,        'tertiary_link': 3,
    'residential': 4,     'living_street': 3,
    'service': 2,         'track': 2,
    'footway': 1.5,       'path': 1.5,       'cycleway': 1.5,
    'steps': 1,           'pedestrian': 3,
    'unclassified': 4,
}


def _get_road_width_m(highway) -> float:
    """Return the approximate total road width in metres for the given highway tag."""
    if isinstance(highway, list):
        highway = highway[0] if highway else 'unclassified'
    return float(_HIGHWAY_WIDTHS.get(str(highway), 3.0))


def fetch_osm_data(
    north: float, south: float, east: float, west: float,
    layers: List[str],
    simplify_tolerance: float = 0.5,
    min_area: float = 5.0,
) -> dict:
    """
    Fetch OSM building, road, waterway, and POI data for a bounding box.

    Uses osmnx to query the Overpass API.  Returns a dict with one key per
    requested layer (buildings / roads / waterways / pois), each value being a
    GeoJSON FeatureCollection dict.

    Raises:
        RuntimeError  if osmnx is not installed.
    """
    try:
        import osmnx as ox
    except ImportError:
        raise RuntimeError("osmnx is not installed. Run: pip install osmnx")

    # Convert simplification tolerance from metres to degrees (~111 km per degree)
    tol_deg = simplify_tolerance / 111_000.0

    result: dict = {}

    # osmnx 2.x bbox format: (left, bottom, right, top) = (west, south, east, north)
    bbox = (west, south, east, north)

    if "buildings" in layers:
        try:
            gdf = ox.features_from_bbox(bbox, tags={"building": True})
            gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
            if min_area > 0 and len(gdf):
                # Project to EPSG:3857 (metres) for accurate area calculation; WGS84 degree²
                # is distorted by cos(lat) and would filter too aggressively at higher latitudes.
                gdf_m = gdf.to_crs(epsg=3857)
                gdf = gdf[gdf_m.geometry.area >= min_area].reset_index(drop=True)
            if tol_deg > 0 and len(gdf):
                # Simplify individual building polygons to reduce vertex count before dissolve.
                gdf["geometry"] = gdf["geometry"].simplify(tol_deg, preserve_topology=True)
                gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
            # Fill heights before reduce so height_m is available as dissolve key
            gdf = _fill_building_heights(gdf)
            # Dissolve touching same-height buildings — reduces polygon count
            gdf = _reduce_buildings(gdf)
            keep = ["geometry", "height_m"]
            gdf = gdf[[c for c in keep if c in gdf.columns]]
            result["buildings"] = json.loads(gdf.to_json())
        except Exception as e:
            logger.warning(f"OSM buildings fetch failed: {e}", exc_info=True)
            result["buildings"] = {"type": "FeatureCollection", "features": [], "error": str(e)}

    if "roads" in layers:
        try:
            G = ox.graph_from_bbox(bbox, network_type="drive")
            _, edges = ox.graph_to_gdfs(G)
            edges = edges.reset_index(drop=True)
            # Cities 6: add road_width_m per feature for width-aware rendering
            if "highway" in edges.columns:
                edges["road_width_m"] = edges["highway"].apply(_get_road_width_m)
            keep = ["geometry", "highway", "name", "lanes", "maxspeed", "road_width_m"]
            edges = edges[[c for c in keep if c in edges.columns]]
            result["roads"] = json.loads(edges.to_json())
        except Exception as e:
            logger.warning(f"OSM roads fetch failed: {e}", exc_info=True)
            result["roads"] = {"type": "FeatureCollection", "features": [], "error": str(e)}

    if "waterways" in layers:
        try:
            water_tags = {"waterway": True, "natural": ["water", "wetland"], "landuse": ["reservoir"]}
            gdf = ox.features_from_bbox(bbox, tags=water_tags)
            gdf = gdf.reset_index(drop=True)
            # Drop null geometries and types the renderer can't handle
            _supported = {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}
            gdf = gdf[gdf.geometry.notna() & gdf.geometry.geom_type.isin(_supported)].reset_index(drop=True)
            if tol_deg > 0:
                gdf["geometry"] = gdf["geometry"].simplify(tol_deg, preserve_topology=True)
            # Fix any degenerate geometries produced by simplification
            gdf["geometry"] = gdf.geometry.make_valid()
            gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
            keep = ["geometry", "waterway", "natural", "name", "water"]
            gdf = gdf[[c for c in keep if c in gdf.columns]]
            result["waterways"] = json.loads(gdf.to_json())
        except Exception as e:
            logger.warning(f"OSM waterways fetch failed: {e}", exc_info=True)
            result["waterways"] = {"type": "FeatureCollection", "features": [], "error": str(e)}

    if "pois" in layers:
        try:
            poi_tags = {"amenity": True, "tourism": True, "historic": True}
            gdf = ox.features_from_bbox(bbox, tags=poi_tags)
            gdf = gdf[gdf.geometry.geom_type == "Point"].reset_index(drop=True)
            keep = ["geometry", "amenity", "tourism", "historic", "name"]
            gdf = gdf[[c for c in keep if c in gdf.columns]]
            result["pois"] = json.loads(gdf.to_json())
        except Exception as e:
            logger.warning(f"OSM pois fetch failed: {e}", exc_info=True)
            result["pois"] = {"type": "FeatureCollection", "features": [], "error": str(e)}

    return result


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
    Burn OSM vector features onto a dim×dim float32 height-map grid.

    Layer order (painter's algorithm — later layers overwrite earlier ones):
      1. waterways  — polygons/lines burned at water_depression_m
      2. roads      — lines buffered to road_width_m, burned at road_depression_m
      3. buildings  — polygons burned at height_m * building_scale (np.maximum so tall wins)

    Returns a dict compatible with the DEM response format:
      { values: [float, ...], width, height, vmin, vmax, bbox }
    """
    import numpy as np
    from rasterio.transform import from_bounds
    from rasterio.features import rasterize as _rasterize
    from shapely.geometry import shape, mapping

    transform = from_bounds(west, south, east, north, dim, dim)
    grid = np.zeros((dim, dim), dtype=np.float32)

    # ── Waterways ──────────────────────────────────────────────────────────
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
            continue
    if water_shapes:
        try:
            _rasterize(water_shapes, out=grid, transform=transform,
                       merge_alg="replace", dtype="float32")
        except Exception as e:
            logger.warning(f"rasterize waterways failed: {e}")

    # ── Roads ───────────────────────────────────────────────────────────────
    road_shapes = []
    for feat in (roads_geojson.get("features") or []):
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            width_m = (feat.get("properties") or {}).get("road_width_m", 4.0)
            # Convert metres to degrees (approximate at this latitude)
            mid_lat = (north + south) / 2
            import math
            metres_per_deg_lat = 111_000.0
            metres_per_deg_lon = 111_000.0 * math.cos(math.radians(mid_lat))
            buf_deg = (width_m / 2) / metres_per_deg_lon
            s = shape(geom).buffer(max(buf_deg, (north - south) / dim * 0.5))
            if not s.is_empty:
                road_shapes.append((mapping(s), road_depression_m))
        except Exception:
            continue
    if road_shapes:
        try:
            _rasterize(road_shapes, out=grid, transform=transform,
                       merge_alg="replace", dtype="float32")
        except Exception as e:
            logger.warning(f"rasterize roads failed: {e}")

    # ── Buildings ───────────────────────────────────────────────────────────
    # Burn each building separately and take the maximum so tall buildings
    # win over adjacent shorter ones (can't batch because each has a different value).
    building_shapes = []
    for feat in (buildings_geojson.get("features") or []):
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            h = float((feat.get("properties") or {}).get("height_m", 10.0)) * building_scale
            building_shapes.append((mapping(shape(geom)), h))
        except Exception:
            continue
    if building_shapes:
        try:
            # Rasterize all at once using max merge_alg (rasterio ≥ 1.2)
            building_grid = _rasterize(
                building_shapes, out_shape=(dim, dim), transform=transform,
                fill=0, dtype="float32", merge_alg="add",
            )
            # Use np.maximum so building heights always win over road/water values
            np.maximum(grid, building_grid, out=grid)
        except Exception:
            # Fallback: rasterize one by one
            for feat_shape, h in building_shapes:
                try:
                    tmp = _rasterize(
                        [(feat_shape, h)], out_shape=(dim, dim),
                        transform=transform, fill=0, dtype="float32",
                    )
                    np.maximum(grid, tmp, out=grid)
                except Exception:
                    continue

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
