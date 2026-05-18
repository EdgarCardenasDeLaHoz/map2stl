"""
city2stl/fetch.py — OSM Overpass/osmnx data fetchers.

Provides pure osmnx wrappers that fetch building, road, waterway, and POI
data from the OpenStreetMap Overpass API. No HTTP cache or server deps --
caching is handled at the router layer (app.server.routers.cities).

Server entry point: app.server.core.osm re-exports all public symbols.

-- Legacy note --
city2stl/osm2stl.py had get_roads_osmnx() and get_rivers() which fetched
the same data using the old osmnx graph API. fetch_osm_data() here is the
modern replacement: it uses ox.features_from_bbox() (osmnx 2.x), applies
geometry simplification, fills heights, and dissolves touching buildings.
"""

from __future__ import annotations

import json
import logging
from typing import List

logger = logging.getLogger(__name__)

# _HIGHWAY_WIDTHS is defined in city2stl.roads (authoritative source).
# Imported here so fetch.py is the single osm-facing module without callers
# needing to know the internal split between roads.py and fetch.py.
from .roads import _HIGHWAY_WIDTHS, get_road_width_m as _get_road_width_m
from .heights import _fill_heights, _reduce_buildings
from .rasterize import _count_verts, _empty_fc


# ---------------------------------------------------------------------------
# Per-layer fetch helpers
# ---------------------------------------------------------------------------

def _fetch_buildings(ox, bbox, tol_deg: float, simplify_tolerance: float, min_area: float) -> dict:
    try:
        import pandas as pd

        base_gdf = ox.features_from_bbox(bbox, tags={"building": True})
        part_gdf = ox.features_from_bbox(bbox, tags={"building:part": True})
        if len(base_gdf) and len(part_gdf):
            # Keep first occurrence for duplicated OSM ids returned by both queries.
            gdf = pd.concat([part_gdf, base_gdf], axis=0, copy=False)
            gdf = gdf.reset_index().drop_duplicates(subset=["element", "id"], keep="first")
            gdf = gdf.set_index(["element", "id"])
        elif len(base_gdf):
            gdf = base_gdf
        else:
            gdf = part_gdf
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
        n_raw = len(gdf)
        if min_area > 0 and len(gdf):
            gdf_m = gdf.to_crs(epsg=3857)
            gdf = gdf[gdf_m.geometry.area >= min_area].reset_index(drop=True)
        logger.info(
            f"[buildings] raw={n_raw} features  after area filter (>={min_area} m^2): {len(gdf)} features"
        )
        if tol_deg > 0 and len(gdf):
            gdf_m_pre = gdf.to_crs(epsg=3857)
            verts_before = int(gdf_m_pre.geometry.apply(lambda g: sum(len(p.exterior.coords) for p in ([g] if g.geom_type == 'Polygon' else g.geoms))).sum())
            area_before  = float(gdf_m_pre.geometry.area.sum())
            gdf["geometry"] = gdf["geometry"].simplify(tol_deg, preserve_topology=True)
            gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
            gdf_m_post = gdf.to_crs(epsg=3857)
            verts_after = int(gdf_m_post.geometry.apply(lambda g: sum(len(p.exterior.coords) for p in ([g] if g.geom_type == 'Polygon' else g.geoms))).sum())
            area_after  = float(gdf_m_post.geometry.area.sum())
            area_delta_pct = (area_after - area_before) / area_before * 100 if area_before else 0
            logger.info(
                f"[buildings] geometry simplify (tol={simplify_tolerance} m): "
                f"vertices {verts_before} -> {verts_after} ({verts_after/verts_before*100:.1f}%)  |  "
                f"area {area_before/1e4:.2f} -> {area_after/1e4:.2f} ha  (d {area_delta_pct:+.2f}%)"
            )
        gdf = _fill_heights(gdf, default_m=10.0, lo=3.0, hi=300.0, levels_col='building:levels')
        n_pre_dissolve = len(gdf)
        gdf_m_pre_d = gdf.to_crs(epsg=3857)
        area_pre_dissolve = float(gdf_m_pre_d.geometry.area.sum())
        gdf = _reduce_buildings(gdf)
        gdf_m_post_d = gdf.to_crs(epsg=3857)
        area_post_dissolve = float(gdf_m_post_d.geometry.area.sum())
        area_dissolve_delta_pct = (area_post_dissolve - area_pre_dissolve) / area_pre_dissolve * 100 if area_pre_dissolve else 0
        logger.info(
            f"[buildings] dissolve: {n_pre_dissolve} -> {len(gdf)} features  |  "
            f"area {area_pre_dissolve/1e4:.2f} -> {area_post_dissolve/1e4:.2f} ha  (d {area_dissolve_delta_pct:+.2f}%)"
        )
        # Keep roof geometry tags so mesh generation can produce shaped roofs.
        # building:levels and min_height are also passed through for completeness.
        keep = [
            "geometry", "height_m", "height_source",
            "roof:shape", "roof:height", "roof:levels",
            "roof:direction", "roof:orientation",
            "roof:colour", "roof:material",
            "building:levels", "min_height", "building:part",
        ]
        gdf = gdf[[c for c in keep if c in gdf.columns]]
        return json.loads(gdf.to_json())
    except Exception as e:
        logger.warning(f"OSM buildings fetch failed: {e}", exc_info=True)
        return _empty_fc(str(e))


def _fetch_roads(ox, bbox) -> dict:
    try:
        G = ox.graph_from_bbox(bbox, network_type="drive")
        _, edges = ox.graph_to_gdfs(G)
        edges = edges.reset_index(drop=True)
        if "highway" in edges.columns:
            edges["road_width_m"] = edges["highway"].apply(_get_road_width_m)
        keep = ["geometry", "highway", "name", "lanes", "maxspeed", "road_width_m"]
        edges = edges[[c for c in keep if c in edges.columns]]
        return json.loads(edges.to_json())
    except Exception as e:
        logger.warning(f"OSM roads fetch failed: {e}", exc_info=True)
        return _empty_fc(str(e))


def _fetch_waterways(ox, bbox, tol_deg: float, simplify_tolerance: float) -> dict:
    try:
        water_tags = {
            "waterway": True,
            "natural":  ["water", "wetland", "coastline", "bay", "strait"],
            "landuse":  ["reservoir", "basin"],
            "place":    ["ocean", "sea"],
        }
        gdf = ox.features_from_bbox(bbox, tags=water_tags)
        gdf = gdf.reset_index(drop=True)
        _supported = {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}
        gdf = gdf[gdf.geometry.notna() & gdf.geometry.geom_type.isin(_supported)].reset_index(drop=True)
        if tol_deg > 0 and len(gdf):
            gdf_m_pre = gdf.to_crs(epsg=3857)
            poly_mask = gdf_m_pre.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
            verts_before = int(gdf_m_pre.geometry.apply(_count_verts).sum())
            area_before  = float(gdf_m_pre.geometry[poly_mask].area.sum()) if poly_mask.any() else 0.0
            gdf["geometry"] = gdf["geometry"].simplify(tol_deg, preserve_topology=True)
            gdf["geometry"] = gdf.geometry.make_valid()
            gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
            gdf_m_post = gdf.to_crs(epsg=3857)
            poly_mask_post = gdf_m_post.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
            verts_after = int(gdf_m_post.geometry.apply(_count_verts).sum())
            area_after  = float(gdf_m_post.geometry[poly_mask_post].area.sum()) if poly_mask_post.any() else 0.0
            area_delta_pct = (area_after - area_before) / area_before * 100 if area_before else 0
            logger.info(
                f"[waterways] geometry simplify (tol={simplify_tolerance} m): "
                f"vertices {verts_before} -> {verts_after} ({verts_after/verts_before*100:.1f}% of original)  |  "
                f"polygon area {area_before/1e4:.2f} -> {area_after/1e4:.2f} ha  (d {area_delta_pct:+.2f}%)"
            )
        else:
            gdf["geometry"] = gdf.geometry.make_valid()
            gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
        keep = ["geometry", "waterway", "natural", "name", "water"]
        gdf = gdf[[c for c in keep if c in gdf.columns]]
        return json.loads(gdf.to_json())
    except Exception as e:
        logger.warning(f"OSM waterways fetch failed: {e}", exc_info=True)
        return _empty_fc(str(e))


def _fetch_pois(ox, bbox) -> dict:
    try:
        poi_tags = {"amenity": True, "tourism": True, "historic": True}
        gdf = ox.features_from_bbox(bbox, tags=poi_tags)
        gdf = gdf[gdf.geometry.geom_type == "Point"].reset_index(drop=True)
        keep = ["geometry", "amenity", "tourism", "historic", "name"]
        gdf = gdf[[c for c in keep if c in gdf.columns]]
        return json.loads(gdf.to_json())
    except Exception as e:
        logger.warning(f"OSM pois fetch failed: {e}", exc_info=True)
        return _empty_fc(str(e))


def _fetch_polygon_layer(
    ox, bbox, tags: dict,
    height_default: float, height_lo: float, height_hi: float,
    keep_cols: list, label: str,
) -> dict:
    """Generic fetch for polygon-only layers (walls, towers, churches, fortifications).

    Fetches features, filters to Polygon/MultiPolygon, fills heights, trims columns.
    """
    try:
        gdf = ox.features_from_bbox(bbox, tags=tags)
        gdf = gdf.reset_index(drop=True)
        gdf = gdf[
            gdf.geometry.notna() &
            gdf.geometry.geom_type.isin({"Polygon", "MultiPolygon", "LineString", "MultiLineString"})
        ].reset_index(drop=True)
        gdf = _fill_heights(gdf, default_m=height_default, lo=height_lo, hi=height_hi)
        keep = ["geometry", "height_m", "height_source"] + keep_cols
        gdf = gdf[[c for c in keep if c in gdf.columns]]
        result = json.loads(gdf.to_json())
        logger.info(f"[{label}] fetched {len(gdf)} features")
        return result
    except Exception as e:
        logger.warning(f"OSM {label} fetch failed: {e}", exc_info=True)
        return _empty_fc(str(e))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_osm_data(
    north: float, south: float, east: float, west: float,
    layers: List[str],
    simplify_tolerance: float = 0.5,
    min_area: float = 5.0,
) -> dict:
    """
    Fetch OSM building, road, waterway, and POI data for a bounding box.

    Uses osmnx to query the Overpass API.  Returns a dict with one key per
    requested layer (buildings / roads / waterways / pois / walls / towers /
    churches / fortifications), each value being a GeoJSON FeatureCollection.

    Raises:
        RuntimeError  if osmnx is not installed.
    """
    try:
        import osmnx as ox
    except ImportError:
        raise RuntimeError("osmnx is not installed. Run: pip install osmnx")

    # Convert simplification tolerance from metres to degrees (~111 km per degree)
    tol_deg = simplify_tolerance / 111_000.0

    # osmnx 2.x bbox format: (left, bottom, right, top) = (west, south, east, north)
    bbox = (west, south, east, north)

    result: dict = {}

    if "buildings" in layers:
        result["buildings"] = _fetch_buildings(ox, bbox, tol_deg, simplify_tolerance, min_area)

    if "roads" in layers:
        result["roads"] = _fetch_roads(ox, bbox)

    if "waterways" in layers:
        result["waterways"] = _fetch_waterways(ox, bbox, tol_deg, simplify_tolerance)

    if "pois" in layers:
        result["pois"] = _fetch_pois(ox, bbox)

    if "walls" in layers:
        result["walls"] = _fetch_polygon_layer(
            ox, bbox,
            tags={"historic": "city_wall", "barrier": "city_wall"},
            height_default=8.0, height_lo=2.0, height_hi=30.0,
            keep_cols=["name"], label="walls",
        )

    if "towers" in layers:
        result["towers"] = _fetch_polygon_layer(
            ox, bbox,
            tags={"historic": ["tower", "watchtower", "fortification"],
                  "man_made": ["defensive_works"],
                  "tower:type": ["defensive", "watchtower", "bell_tower", "minaret"]},
            height_default=20.0, height_lo=5.0, height_hi=200.0,
            keep_cols=["name"], label="towers",
        )

    if "churches" in layers:
        result["churches"] = _fetch_polygon_layer(
            ox, bbox,
            tags={"amenity": "place_of_worship"},
            height_default=15.0, height_lo=3.0, height_hi=150.0,
            keep_cols=["name", "amenity", "religion"], label="churches",
        )

    if "fortifications" in layers:
        result["fortifications"] = _fetch_polygon_layer(
            ox, bbox,
            tags={"historic": ["fort", "castle", "fortress", "fortification"]},
            height_default=12.0, height_lo=3.0, height_hi=60.0,
            keep_cols=["name", "historic"], label="fortifications",
        )

    result["city_pipeline_version"] = 2

    return result
