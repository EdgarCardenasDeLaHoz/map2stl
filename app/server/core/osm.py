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


def _fill_heights(
    gdf,
    default_m: float,
    lo: float = 2.0,
    hi: float = 300.0,
    levels_col: str | None = None,
):
    """Fill height_m for OSM features from the ``height`` tag.

    Args:
        default_m:  Fallback height when tag is absent or unparseable.
        lo, hi:     Clip bounds in metres.
        levels_col: If set, use ``gdf[levels_col] * 4.0`` as a secondary
                    fallback before *default_m* (buildings only).
    """
    try:
        import pandas as pd
    except ImportError:
        gdf = gdf.copy()
        gdf['height_m'] = float(default_m)
        return gdf

    # Levels-based estimate (buildings only)
    if levels_col and levels_col in gdf.columns:
        levels = pd.to_numeric(gdf[levels_col], errors='coerce').fillna(3.0)
        height_from_levels = levels * 4.0
    else:
        height_from_levels = float(default_m)

    # Explicit OSM height tag (strip trailing unit strings like " m" or "ft")
    if 'height' in gdf.columns:
        raw = gdf['height'].astype(str).str.extract(r'([\d.]+)', expand=False)
        explicit = pd.to_numeric(raw, errors='coerce')
        height_m = explicit.fillna(height_from_levels)
    else:
        height_m = height_from_levels

    height_m = (
        pd.to_numeric(height_m, errors='coerce')
        .fillna(float(default_m))
        .clip(lower=lo, upper=hi)
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


def _count_verts(g) -> int:
    """Count total exterior vertices in a geometry (for simplification logging)."""
    if g.geom_type == "LineString":
        return len(g.coords)
    if g.geom_type == "MultiLineString":
        return sum(len(l.coords) for l in g.geoms)
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


# ---------------------------------------------------------------------------
# Per-layer fetch helpers
# ---------------------------------------------------------------------------

def _fetch_buildings(ox, bbox, tol_deg: float, simplify_tolerance: float, min_area: float) -> dict:
    try:
        gdf = ox.features_from_bbox(bbox, tags={"building": True})
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
        n_raw = len(gdf)
        if min_area > 0 and len(gdf):
            gdf_m = gdf.to_crs(epsg=3857)
            gdf = gdf[gdf_m.geometry.area >= min_area].reset_index(drop=True)
        logger.info(
            f"[buildings] raw={n_raw} features  after area filter (>={min_area} m²): {len(gdf)} features"
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
                f"vertices {verts_before} → {verts_after} ({verts_after/verts_before*100:.1f}%)  |  "
                f"area {area_before/1e4:.2f} → {area_after/1e4:.2f} ha  (Δ {area_delta_pct:+.2f}%)"
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
            f"[buildings] dissolve: {n_pre_dissolve} → {len(gdf)} features  |  "
            f"area {area_pre_dissolve/1e4:.2f} → {area_post_dissolve/1e4:.2f} ha  (Δ {area_dissolve_delta_pct:+.2f}%)"
        )
        keep = ["geometry", "height_m"]
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
                f"vertices {verts_before} → {verts_after} ({verts_after/verts_before*100:.1f}% of original)  |  "
                f"polygon area {area_before/1e4:.2f} → {area_after/1e4:.2f} ha  (Δ {area_delta_pct:+.2f}%)"
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
        keep = ["geometry", "height_m"] + keep_cols
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
