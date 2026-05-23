"""city2stl.skyline_cv.osm_water — OSM coastline + water extraction (F-SKY13).

OSM is the **primary** ground truth for pano↔geography coastline
registration (see ``docs/plans/F-SKY13-osm-coastline-footprints-overlay.md``
and feedback memory ``feedback_satellite_coastline_hsv_unreliable``).
This module reads the waterways layer that ``city2stl.fetch.fetch_osm_data``
already populates, extracts coastline linestrings and water polygons,
and clips them to a 1 km radius around a seed point so the consideration
window stays manageable for both rendering and registration scoring.

Public surface:

  extract_coastline_features(osm_data)
      Pull ``natural=coastline`` linestrings out of an osm_data dict.
  extract_water_features(osm_data)
      Pull water polygons (``natural=water``, ``natural=bay``,
      ``place=ocean``/``sea``) out of an osm_data dict.
  clip_to_radius(features, seed_lonlat, radius_m)
      Return a copy with each feature clipped to a circle of
      ``radius_m`` around the seed. Features fully outside are dropped.
  sample_coastline_points(features, spacing_m=20.0)
      Re-sample coastline linestrings into evenly-spaced lon/lat points,
      suitable as registration keypoints for ``project_lonlat_to_view``
      and the existing pano-side scoring machinery.

These functions are pure and do not import the matplotlib / fetch stack
so they unit-test cheaply.
"""

from __future__ import annotations

import logging
import math
from typing import Iterable

from city2stl.skyline_cv.pipeline import lonlat_to_local_m

logger = logging.getLogger(__name__)


# Earth radius for haversine; kept for parity with the prior implementation but
# the radius-clipping helper now uses the canonical equirectangular projection
# (``pipeline.lonlat_to_local_m``) so the distance metric matches the rest of
# the skyline_cv pipeline (minimap rendering, registration sweeps).
_EARTH_R_M = 6_378_137.0


# ---------------------------------------------------------------------------
# Feature extraction from the existing osm_data dict
# ---------------------------------------------------------------------------

def _waterway_features(osm_data: dict) -> list[dict]:
    """Return the raw feature list under osm_data['waterways']['features']."""
    return list((osm_data.get("waterways") or {}).get("features") or [])


def extract_coastline_features(osm_data: dict) -> list[dict]:
    """Return only ``natural=coastline`` features (typically LineStrings)."""
    out: list[dict] = []
    for feat in _waterway_features(osm_data):
        props = feat.get("properties") or {}
        if props.get("natural") == "coastline":
            out.append(feat)
    return out


def extract_water_features(osm_data: dict) -> list[dict]:
    """Return water-area polygons (excluding bare coastline linestrings).

    Captures ``natural`` in ``{water, bay, strait}`` and ``place`` in
    ``{ocean, sea}``. Inland ``waterway=*`` linestrings (rivers, canals)
    are intentionally excluded — those are tracked as separate features
    by the rest of the pipeline.
    """
    natural_water = {"water", "bay", "strait"}
    place_water = {"ocean", "sea"}
    out: list[dict] = []
    for feat in _waterway_features(osm_data):
        props = feat.get("properties") or {}
        geom = (feat.get("geometry") or {}).get("type")
        if geom not in ("Polygon", "MultiPolygon"):
            continue
        if props.get("natural") in natural_water or props.get("place") in place_water:
            out.append(feat)
    return out


# ---------------------------------------------------------------------------
# 1 km radius clipping
# ---------------------------------------------------------------------------

def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance between two lon/lat points in metres."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2.0 * _EARTH_R_M * math.asin(math.sqrt(a))


def _coords_within(coords: Iterable, seed_lonlat: tuple[float, float], radius_m: float) -> bool:
    """True if *any* coord in the (possibly nested) coords sequence is within radius.

    Uses the canonical equirectangular projection (``pipeline.lonlat_to_local_m``)
    so the distance metric matches the rest of skyline_cv. At ≤ 1 km windows the
    difference vs full haversine is sub-metre.
    """
    lon0, lat0 = seed_lonlat
    radius_sq = radius_m * radius_m
    for c in coords:
        # Nested ring (Polygon) or LineString of [lon, lat] pairs
        if isinstance(c, (list, tuple)) and len(c) >= 2 and isinstance(c[0], (int, float)):
            dx, dy = lonlat_to_local_m(float(c[0]), float(c[1]), lon0, lat0)
            if dx * dx + dy * dy <= radius_sq:
                return True
        elif isinstance(c, (list, tuple)):
            if _coords_within(c, seed_lonlat, radius_m):
                return True
    return False


def clip_to_radius(
    features: list[dict],
    seed_lonlat: tuple[float, float],
    radius_m: float = 1000.0,
) -> list[dict]:
    """Drop features with no vertex inside ``radius_m`` of ``seed_lonlat``.

    Cheap pre-filter — does not actually trim geometries (the renderer
    can clip to the visible window itself). A feature is kept if at
    least one of its coordinates falls within the radius; this is
    intentionally permissive so that long coastline linestrings spanning
    the seed window are not dropped.

    Returns a new list; original features are not mutated.
    """
    if not features:
        return []

    kept: list[dict] = []
    for feat in features:
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if coords is None:
            continue
        if _coords_within(coords, seed_lonlat, radius_m):
            kept.append(feat)
    return kept


# ---------------------------------------------------------------------------
# Coastline keypoint sampling
# ---------------------------------------------------------------------------

def _iter_linestring_coords(feat: dict) -> Iterable[list[list[float]]]:
    """Yield each LineString's coordinate list from a feature.

    Handles both ``LineString`` (one list) and ``MultiLineString``
    (list-of-lists) without raising on unexpected types.
    """
    geom = feat.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if coords is None:
        return
    if gtype == "LineString":
        yield coords
    elif gtype == "MultiLineString":
        for ring in coords:
            yield ring


def osm_keypoints_for_scoring(
    coastline_features: list[dict],
    seed_lonlat: tuple[float, float],
    spacing_m: float = 20.0,
) -> list[dict]:
    """Convert OSM coastline features into the keypoint dict shape used by
    ``coastline_registration.score_pano_offset_keypoints``.

    Each returned dict has the same ``{bearing_deg, distance_m}`` shape that
    ``detect_coastline_keypoints`` produces from a satellite mask — this
    lets the existing scoring + sweep functions consume OSM-derived points
    without modification. F-SKY13 uses this to feed OSM into the same
    pano-side scoring path that F-SKY11.1 was using for the (unreliable)
    satellite HSV coastline.

    Bearing is measured from north, clockwise, in degrees. Distance is the
    straight-line distance from the seed in metres (equirectangular
    approximation — exact enough at ≤ 1 km).
    """
    out: list[dict] = []
    lon0, lat0 = seed_lonlat
    for lon, lat in sample_coastline_points(coastline_features, spacing_m=spacing_m):
        dx, dy = lonlat_to_local_m(lon, lat, lon0, lat0)
        distance_m = math.hypot(dx, dy)
        if distance_m < 1.0:
            continue
        # atan2(east, north) gives bearing from north, clockwise; ``dx`` is
        # east-component in metres, ``dy`` is north-component.
        bearing_deg = math.degrees(math.atan2(dx, dy)) % 360.0
        out.append({
            "lon": lon,
            "lat": lat,
            "distance_m": distance_m,
            "bearing_deg": bearing_deg,
        })
    return out


def sample_coastline_points(
    features: list[dict],
    spacing_m: float = 20.0,
) -> list[tuple[float, float]]:
    """Re-sample coastline linestrings into evenly-spaced (lon, lat) points.

    ``spacing_m`` controls keypoint density — default 20 m is dense
    enough for visual overlay on a 1 km minimap and sparse enough for
    fast pano-side scoring. Points are ordered by their position along
    each linestring; the result is the concatenation across all input
    features (no de-duplication — adjacent OSM segments may share
    endpoints, that's acceptable for both rendering and scoring).
    """
    out: list[tuple[float, float]] = []
    for feat in features:
        for line in _iter_linestring_coords(feat):
            if not line or len(line) < 2:
                continue
            # Walk the polyline accumulating distance until we hit the
            # next spacing step, then emit the interpolated point.
            carry = 0.0
            prev_lon, prev_lat = float(line[0][0]), float(line[0][1])
            out.append((prev_lon, prev_lat))
            for vert in line[1:]:
                lon, lat = float(vert[0]), float(vert[1])
                seg_m = _haversine_m(prev_lon, prev_lat, lon, lat)
                if seg_m <= 0.0:
                    prev_lon, prev_lat = lon, lat
                    continue
                t = (spacing_m - carry) / seg_m
                while t <= 1.0:
                    out.append(
                        (prev_lon + t * (lon - prev_lon), prev_lat + t * (lat - prev_lat))
                    )
                    t += spacing_m / seg_m
                carry = (carry + seg_m) % spacing_m
                prev_lon, prev_lat = lon, lat
    return out
