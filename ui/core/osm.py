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
    simplify_tolerance: float = 2.0,
    min_area: float = 20.0,
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
            if min_area > 0:
                min_area_deg2 = min_area / (111_000.0 ** 2)
                gdf = gdf[gdf.geometry.area >= min_area_deg2].reset_index(drop=True)
            if tol_deg > 0:
                gdf["geometry"] = gdf["geometry"].simplify(tol_deg, preserve_topology=True)
            # Cities 4: fill missing heights from building:levels (4 m/storey), fallback 10 m
            gdf = _fill_building_heights(gdf)
            keep = ["geometry", "building", "height", "building:levels", "height_m", "name"]
            gdf = gdf[[c for c in keep if c in gdf.columns]]
            result["buildings"] = json.loads(gdf.to_json())
        except Exception as e:
            logger.warning(f"OSM buildings fetch failed: {e}")
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
            logger.warning(f"OSM roads fetch failed: {e}")
            result["roads"] = {"type": "FeatureCollection", "features": [], "error": str(e)}

    if "waterways" in layers:
        try:
            water_tags = {"waterway": True, "natural": ["water", "wetland"], "landuse": ["reservoir"]}
            gdf = ox.features_from_bbox(bbox, tags=water_tags)
            gdf = gdf.reset_index(drop=True)
            if tol_deg > 0:
                gdf["geometry"] = gdf["geometry"].simplify(tol_deg, preserve_topology=True)
            keep = ["geometry", "waterway", "natural", "name", "water"]
            gdf = gdf[[c for c in keep if c in gdf.columns]]
            result["waterways"] = json.loads(gdf.to_json())
        except Exception as e:
            logger.warning(f"OSM waterways fetch failed: {e}")
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
            logger.warning(f"OSM pois fetch failed: {e}")
            result["pois"] = {"type": "FeatureCollection", "features": [], "error": str(e)}

    return result
