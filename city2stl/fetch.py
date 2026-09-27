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

logger = logging.getLogger(__name__)


class OverpassUpstreamError(RuntimeError):
    """Every Overpass mirror failed to serve a layer the caller asked for.

    Distinct from a bbox that genuinely contains no buildings. The two used to be
    indistinguishable through the HTTP API: during the 2026-08-30 outage Naples answered 200 with
    an empty buildings collection while Palermo, hitting the same outage, correctly failed. An
    empty answer that is really an outage is a wrong answer, not a missing one, and it poisons
    the OSM cache and every export built on it.
    """


from geo2stl.osm import OVERPASS_ENDPOINTS as _OVERPASS_ENDPOINTS  # noqa: E402
from geo2stl.osm import healthy_overpass_endpoints as _healthy_overpass_endpoints  # noqa: E402
from geo2stl.osm import use_overpass_endpoint  # noqa: E402

# Per-request budget for the Overpass queries themselves. A city-sized
# buildings query on a healthy mirror is ~60 s; 300 s leaves room for a loaded
# one while still failing over in bounded time rather than hanging.
_OVERPASS_REQUEST_TIMEOUT_S = 300


# _HIGHWAY_WIDTHS is defined in city2stl.roads (authoritative source).
# Imported here so fetch.py is the single osm-facing module without callers
# needing to know the internal split between roads.py and fetch.py.
from .heights import _fill_heights, _reduce_buildings  # noqa: E402
from .rasterize import _count_verts, _empty_fc  # noqa: E402
from .roads import get_road_width_m as _get_road_width_m  # noqa: E402

try:
    from osmnx._errors import InsufficientResponseError
except Exception:  # pragma: no cover - osmnx layout change
    #: Nothing will match, so every failure keeps the old "worth another mirror" reading.
    InsufficientResponseError = ()


# ---------------------------------------------------------------------------
# Per-layer fetch helpers
# ---------------------------------------------------------------------------

def _to_metric(gdf):
    """
    Reproject to a CRS whose areas are true square metres.

    EPSG:3857 (Web Mercator) is conformal, not equal-area: its areas are
    inflated by sec^2(latitude) — 1.7x at 40 deg, 4x at 60 deg. Anything that
    compares a computed area against a threshold in m^2 (the ``min_area``
    building filter) or reports one in a log line must not use it. The local
    UTM zone is accurate to a fraction of a percent over a city-sized bbox.
    """
    try:
        return gdf.to_crs(gdf.estimate_utm_crs())
    except Exception as e:  # pragma: no cover - depends on pyproj grid availability
        logger.warning(f"UTM estimation failed ({e}); falling back to EPSG:3857 areas")
        return gdf.to_crs(epsg=3857)


def _features_or_none(ox, bbox, tags):
    """One ``features_from_bbox`` query, with "nothing matched" as a value rather than a raise.

    osmnx signals an empty result by raising, which is the wrong shape for a caller that runs
    several queries and merges them: most bboxes have no ``building:part`` at all, and letting
    that raise would throw away the footprints the other query did find. A genuine request
    failure still propagates, because failover has to be able to see it.
    """
    try:
        return ox.features_from_bbox(bbox, tags=tags)
    except InsufficientResponseError:
        return None


def _fetch_buildings(ox, bbox, tol_deg: float, simplify_tolerance: float, min_area: float) -> dict:
    try:
        import pandas as pd

        base_gdf = _features_or_none(ox, bbox, {"building": True})
        part_gdf = _features_or_none(ox, bbox, {"building:part": True})
        if base_gdf is None and part_gdf is None:
            logger.info("OSM buildings: none in region")
            return _empty_fc()
        if base_gdf is not None and part_gdf is not None:
            # Keep first occurrence for duplicated OSM ids returned by both queries.
            gdf = pd.concat([part_gdf, base_gdf], axis=0, copy=False)
            gdf = gdf.reset_index().drop_duplicates(subset=["element", "id"], keep="first")
            gdf = gdf.set_index(["element", "id"])
        elif base_gdf is not None:
            gdf = base_gdf
        else:
            gdf = part_gdf
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
        n_raw = len(gdf)
        if min_area > 0 and len(gdf):
            gdf_m = _to_metric(gdf)
            gdf = gdf[gdf_m.geometry.area >= min_area].reset_index(drop=True)
        logger.info(
            f"[buildings] raw={n_raw} features  after area filter (>={min_area} m^2): {len(gdf)} features"
        )
        if tol_deg > 0 and len(gdf):
            gdf_m_pre = _to_metric(gdf)
            verts_before = int(gdf_m_pre.geometry.apply(lambda g: sum(len(p.exterior.coords) for p in ([g] if g.geom_type == 'Polygon' else g.geoms))).sum())
            area_before  = float(gdf_m_pre.geometry.area.sum())
            gdf["geometry"] = gdf["geometry"].simplify(tol_deg, preserve_topology=True)
            gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
            gdf_m_post = _to_metric(gdf)
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
        gdf_m_pre_d = _to_metric(gdf)
        area_pre_dissolve = float(gdf_m_pre_d.geometry.area.sum())
        gdf = _reduce_buildings(gdf)
        gdf_m_post_d = _to_metric(gdf)
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
    except InsufficientResponseError as e:
        logger.info(f"OSM buildings: none in region ({e})")
        return _empty_fc()
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
    except InsufficientResponseError as e:
        logger.info(f"OSM roads: none in region ({e})")
        return _empty_fc()
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
            gdf_m_pre = _to_metric(gdf)
            poly_mask = gdf_m_pre.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
            verts_before = int(gdf_m_pre.geometry.apply(_count_verts).sum())
            area_before  = float(gdf_m_pre.geometry[poly_mask].area.sum()) if poly_mask.any() else 0.0
            gdf["geometry"] = gdf["geometry"].simplify(tol_deg, preserve_topology=True)
            gdf["geometry"] = gdf.geometry.make_valid()
            gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
            gdf_m_post = _to_metric(gdf)
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
    except InsufficientResponseError as e:
        logger.info(f"OSM waterways: none in region ({e})")
        return _empty_fc()
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
    except InsufficientResponseError as e:
        logger.info(f"OSM pois: none in region ({e})")
        return _empty_fc()
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
    except InsufficientResponseError as e:
        logger.info(f"OSM {label}: none in region ({e})")
        return _empty_fc()
    except Exception as e:
        logger.warning(f"OSM {label} fetch failed: {e}", exc_info=True)
        return _empty_fc(str(e))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_osm_data(
    north: float, south: float, east: float, west: float,
    layers: list[str],
    simplify_tolerance: float = 0.5,
    min_area: float = 5.0,
) -> dict:
    """
    Fetch OSM building, road, waterway, and POI data for a bounding box.

    Uses osmnx to query the Overpass API.  Returns a dict with one key per
    requested layer (buildings / roads / waterways / pois / walls / towers /
    churches / fortifications / green / railways), each value being a GeoJSON FeatureCollection.

    Raises:
        RuntimeError  if osmnx is not installed.
    """
    try:
        import osmnx as ox
    except ImportError as exc:
        raise RuntimeError("osmnx is not installed. Run: pip install osmnx") from exc

    healthy = _healthy_overpass_endpoints()
    if not healthy:
        raise RuntimeError(
            "No Overpass endpoint answered its /status probe: "
            + ", ".join(_OVERPASS_ENDPOINTS))

    # Convert simplification tolerance from metres to degrees (~111 km per degree)
    tol_deg = simplify_tolerance / 111_000.0

    # osmnx 2.x bbox format: (left, bottom, right, top) = (west, south, east, north)
    bbox = (west, south, east, north)

    # The layer fetchers swallow their own exceptions and return an empty
    # FeatureCollection carrying an ``error`` key, so a mirror that answers its
    # status probe and then 502s every query used to produce a "successful"
    # fetch with no buildings in it. Retry the whole set on the next healthy
    # mirror instead, and only give up when none of them can serve the layer
    # the caller actually came for.
    #
    # The layers of one pass are fetched concurrently (``_fetch_layers``), but
    # ``ox.settings`` is process-global, so the mirror only changes between
    # passes, after every thread of the previous one has returned. A layer can
    # therefore never be half-served by one mirror and half by the next; the
    # layers that failed are retried together on the next mirror, and the ones
    # that succeeded are kept.
    result: dict = {}
    pending = list(layers)
    for attempt, endpoint in enumerate(healthy):
        use_overpass_endpoint(ox, endpoint, _OVERPASS_REQUEST_TIMEOUT_S)
        result.update(_fetch_layers(ox, bbox, pending, tol_deg,
                                    simplify_tolerance, min_area))
        failed = _layers_failed(result, layers)
        if not failed:
            break
        pending = failed
        remaining = len(healthy) - attempt - 1
        logger.warning(
            "Overpass %s returned no usable data for %s; %d mirror(s) left",
            endpoint, ",".join(failed), remaining)
    else:
        # Out of mirrors with the caller's own layers still failing. Returning the last empty
        # collection here would present an outage as an empty region.
        raise OverpassUpstreamError(
            f"All {len(healthy)} Overpass mirror(s) failed to serve "
            f"{', '.join(failed)}: {', '.join(healthy)}")

    result["city_pipeline_version"] = 2
    return result


def _layers_failed(result: dict, layers: list[str]) -> list[str]:
    """Requested layers that came back empty *and* carrying a fetch error.

    An empty layer is not by itself a failure — plenty of bboxes genuinely have
    no city walls. The ``error`` key is what distinguishes "nothing there" from
    "the server refused", and only the latter is worth another mirror.
    """
    failed = []
    for name in layers:
        fc = result.get(name)
        if isinstance(fc, dict) and fc.get("error") and not fc.get("features"):
            failed.append(name)
    return failed


#: Overpass queries in flight at once. The public servers allow a couple of
#: slots per client; three keeps a 9-layer fetch well under the sequential time
#: without hammering a shared, donated service.
_MAX_CONCURRENT_LAYERS = 3


def _layer_jobs(ox, bbox, tol_deg: float, simplify_tolerance: float,
                min_area: float) -> dict:
    """Layer name -> zero-argument fetcher, in the order results are reported."""
    def polygon(tags, height_default, height_lo, height_hi, keep_cols, label):
        return lambda: _fetch_polygon_layer(
            ox, bbox, tags=tags,
            height_default=height_default, height_lo=height_lo, height_hi=height_hi,
            keep_cols=keep_cols, label=label,
        )

    return {
        "buildings": lambda: _fetch_buildings(ox, bbox, tol_deg, simplify_tolerance, min_area),
        "roads": lambda: _fetch_roads(ox, bbox),
        "waterways": lambda: _fetch_waterways(ox, bbox, tol_deg, simplify_tolerance),
        "pois": lambda: _fetch_pois(ox, bbox),
        "walls": polygon(
            {"historic": "city_wall", "barrier": "city_wall"},
            8.0, 2.0, 30.0, ["name"], "walls"),
        "towers": polygon(
            {"historic": ["tower", "watchtower", "fortification"],
             "man_made": ["defensive_works"],
             "tower:type": ["defensive", "watchtower", "bell_tower", "minaret"]},
            20.0, 5.0, 200.0, ["name"], "towers"),
        "churches": polygon(
            {"amenity": "place_of_worship"},
            15.0, 3.0, 150.0, ["name", "amenity", "religion"], "churches"),
        "fortifications": polygon(
            {"historic": ["fort", "castle", "fortress", "fortification"]},
            12.0, 3.0, 60.0, ["name", "historic"], "fortifications"),
        # F-SKY18 vegetation landmarks: parks / grass / forest / wood as
        # polygon areas, used to match pano vegetation regions by bearing.
        "green": polygon(
            {"leisure": ["park", "garden", "recreation_ground"],
             "landuse": ["grass", "forest", "meadow", "recreation_ground", "village_green"],
             "natural": ["wood", "scrub", "grassland"]},
            0.0, 0.0, 0.0, ["name", "leisure", "landuse", "natural"], "green"),
        # Surface rail for the city model (subways run underground and are left out).
        "railways": polygon(
            {"railway": ["rail", "light_rail", "tram", "narrow_gauge", "funicular"]},
            0.0, 0.0, 0.0, ["name", "railway"], "railways"),
    }


def _fetch_layers(ox, bbox, layers: list[str], tol_deg: float,
                  simplify_tolerance: float, min_area: float) -> dict:
    """One pass over the requested layers against the currently-set mirror.

    Up to ``_MAX_CONCURRENT_LAYERS`` layers are fetched at once; each is an
    independent Overpass round trip of 15-20 s, so running them one after
    another made a 9-layer city fetch take minutes. Every fetcher reports its
    own failure as an ``error`` key on an empty collection; a fetcher that raises
    anyway is recorded the same way, so one broken layer cannot lose the others.
    """
    from concurrent.futures import ThreadPoolExecutor

    jobs = _layer_jobs(ox, bbox, tol_deg, simplify_tolerance, min_area)
    wanted = [name for name in jobs if name in layers]
    if not wanted:
        return {}
    with ThreadPoolExecutor(max_workers=min(_MAX_CONCURRENT_LAYERS, len(wanted)),
                            thread_name_prefix="osm-layer") as pool:
        futures = {name: pool.submit(jobs[name]) for name in wanted}
    result: dict = {}
    for name in wanted:
        try:
            result[name] = futures[name].result()
        except Exception as e:
            logger.warning(f"OSM {name} fetch failed: {e}", exc_info=True)
            result[name] = _empty_fc(str(e))
    return result
