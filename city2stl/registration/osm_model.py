"""OSM elements as geometry and tag classes, rasterized as coverage.

The placement's map side (street_place.map_buildings and friends) is built from here.  Two
things differ from locate._geometries plus locate._rasterize, which the placement used until
2026-09-13:

* Coverage, not all_touched.  Each window is burned at SS times its grid with centre sampling
  and area-averaged, so a cell holds the fraction of it a footprint covers.  all_touched grows
  every footprint by up to a cell on each side; at the placement's 8 m cell that made the map
  about 40% more built than it is, and a street between two blocks could vanish.
* Tags are kept.  A relation's inner rings stay holes (a courtyard is not a roof), and a
  footprint whose tags say it is not standing above the street (building=construction, ruins,
  underground car parks, building=no) is left out.

Raw elements are cached as gzipped JSON per selector and window, so a new class model does not
cost another Overpass query.

Promoted 2026-09-27 from ``tools/align_tool/osm_model.py`` (now an alias of this module).
"""
from __future__ import annotations

import gzip
import json
import math
import pathlib

import numpy as np

from city2stl.registration import osm_water as _ow
from geo2stl.geo import M_PER_DEG_LAT as M_PER_DEG

SS = 4   # supersampling per cell side when rasterizing coverage


# ---------------------------------------------------------------------------------------------
# Raw elements

def elements(selector: str, bbox, cache_dir: pathlib.Path, kind: str, nodes: bool = False):
    """Overpass elements for one selector over `bbox`, cached as `<kind>_<bbox>.json.gz`."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = "_".join(f"{v:.5f}" for v in bbox)
    path = cache_dir / f"osm_{kind}_{key}.json.gz"
    if path.exists():
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, EOFError, json.JSONDecodeError):
            path.unlink()   # a write cut short; fetch again
    els = _nodes(selector, bbox) if nodes else _ow._overpass(selector, bbox)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(els, f)
    tmp.replace(path)
    return els


def _nodes(selector: str, bbox):
    """Overpass nodes for one selector (trees are points)."""
    from geo2stl.osm import overpass_query
    N, S, E, W = bbox
    query = (f"[out:json][timeout:{_ow.OVERPASS_TIMEOUT_S}];"
             f"node{selector}({S},{W},{N},{E});out;")
    return overpass_query(query, urls=_ow.OVERPASS_URLS, attempts=_ow.OVERPASS_ATTEMPTS,
                          timeout_s=_ow.OVERPASS_TIMEOUT_S,
                          min_gap_s=_ow.OVERPASS_MIN_GAP_S,
                          user_agent=_ow.OVERPASS_USER_AGENT)


# ---------------------------------------------------------------------------------------------
# Geometry

def _ring(coords):
    return len(coords) >= 4 and coords[0] == coords[-1]


def areal(els, keep_holes=True):
    """(geometry, tags) for each areal element.  A relation's outer and inner members are stitched
    into rings separately, so a courtyard stays a hole."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import linemerge, polygonize, unary_union
    out = []
    for el in els or ():
        tags = el.get("tags") or {}
        try:
            if el.get("type") == "way" and el.get("geometry"):
                c = [(p["lon"], p["lat"]) for p in el["geometry"]]
                if _ring(c):
                    g = Polygon(c)
                    out.append((g if g.is_valid else g.buffer(0), tags))
            elif el.get("type") == "relation":
                rings = {"outer": [], "inner": []}
                for m in el.get("members") or ():
                    c = [(p["lon"], p["lat"]) for p in m.get("geometry") or ()]
                    if len(c) >= 2:
                        rings["inner" if m.get("role") == "inner" else "outer"].append(LineString(c))
                if not rings["outer"]:
                    continue
                outer = unary_union([g for g in polygonize(linemerge(rings["outer"]))
                                     if not g.is_empty])
                if keep_holes and rings["inner"]:
                    inner = [g for g in polygonize(linemerge(rings["inner"])) if not g.is_empty]
                    if inner:
                        outer = outer.difference(unary_union(inner))
                if not outer.is_empty:
                    out.append((outer, tags))
        except Exception:
            continue
    return out


def stroked(els, width_of):
    """(geometry, tags) for each linear element, buffered to width_of(tags) metres (None = skip)."""
    from shapely.affinity import scale
    from shapely.geometry import LineString
    out = []
    for el in els or ():
        tags = el.get("tags") or {}
        w = width_of(tags)
        if not w:
            continue
        lines = [el["geometry"]] if el.get("geometry") else []
        lines += [m["geometry"] for m in el.get("members") or () if m.get("geometry")]
        for pts in lines:
            c = [(p["lon"], p["lat"]) for p in pts]
            if len(c) < 2:
                continue
            # Buffer in a local metric frame: squeeze lon by cos(lat), buffer, stretch back.
            k = 1.0 / math.cos(math.radians(c[0][1]))
            ln = scale(LineString(c), xfact=1.0 / k, yfact=1.0, origin=c[0])
            g = scale(ln.buffer(w / 2.0 / M_PER_DEG, cap_style=2), xfact=k, yfact=1.0,
                      origin=c[0])
            out.append((g, tags))
    return out


def points(els, radius_of):
    """(disc, tags) for each node, radius_of(tags) metres across the ground."""
    from shapely.affinity import scale
    from shapely.geometry import Point
    out = []
    for el in els or ():
        if el.get("type") != "node":
            continue
        tags = el.get("tags") or {}
        k = 1.0 / math.cos(math.radians(el["lat"]))
        g = scale(Point(el["lon"], el["lat"]).buffer(radius_of(tags) / M_PER_DEG, 8),
                  xfact=k, yfact=1.0)
        out.append((g, tags))
    return out


def coverage(geoms, bbox, grid, ss=SS):
    """Fraction of each cell covered, row 0 south (the plate rasters' orientation)."""
    from numpy2stl.raster import burn_polygons
    N, S, E, W = bbox
    g = grid * ss
    arr = burn_polygons(geoms, (g, g), bounds=(W, S, E, N), values=1.0, dtype=np.uint8)
    cov = arr.reshape(grid, ss, grid, ss).mean(axis=(1, 3)).astype(np.float32)
    return np.flipud(cov).copy()


# ---------------------------------------------------------------------------------------------
# Tag models

ROAD_W = {"motorway": 22, "trunk": 18, "primary": 14, "secondary": 12, "tertiary": 10,
          "motorway_link": 8, "trunk_link": 8, "primary_link": 8, "secondary_link": 7,
          "tertiary_link": 7, "residential": 8, "unclassified": 7, "living_street": 6,
          "pedestrian": 6, "service": 5, "busway": 7, "road": 7,
          "footway": 3, "cycleway": 3, "path": 2, "steps": 3, "track": 3}
PATHS = ("footway", "cycleway", "path", "steps", "track")
RAIL_W = 6.0


def _num(v):
    try:
        return float(str(v).strip().split()[0].rstrip("m"))
    except Exception:
        return None


def road_width(tags):
    hw = tags.get("highway")
    if hw not in ROAD_W:
        return None
    w = _num(tags.get("width"))
    if w and 1 < w < 60:
        return w
    lanes = _num(tags.get("lanes"))
    if lanes and hw not in PATHS:
        return max(ROAD_W[hw], 3.5 * lanes)
    return ROAD_W[hw]


def raised(tags):
    """A way that stands above the street: a bridge, a viaduct, or a positive layer."""
    b = tags.get("bridge")
    if b and b != "no":
        return True
    lay = _num(tags.get("layer"))
    return bool(lay and lay > 0)


def sunk(tags):
    t = tags.get("tunnel")
    if t and t != "no":
        return True
    if tags.get("covered") == "yes":
        return True
    lay = _num(tags.get("layer"))
    return bool(lay and lay < 0) or tags.get("location") in ("underground",)


def tree_radius(tags):
    d = _num(tags.get("diameter_crown"))
    return max(2.0, min(10.0, d / 2.0)) if d else 4.0


def building_class(tags):
    b = tags.get("building", "")
    if b in ("no",):
        return "not_a_building"
    if b in ("construction", "proposed", "demolished", "razed", "destroyed", "disused"):
        return "construction"
    if b in ("ruins",) or tags.get("ruins") == "yes":
        return "ruins"
    if sunk(tags) or b in ("underground",) or tags.get("parking") == "underground":
        return "underground"
    if b in ("roof", "carport", "canopy") or tags.get("building:levels") == "0":
        return "roof"
    if b in ("garage", "garages", "shed", "hut", "kiosk", "cabin", "toilets", "service",
             "container", "greenhouse", "bunker"):
        return "small"
    if b in ("parking",) or tags.get("amenity") == "parking":
        return "parking"
    if b in ("bridge",) or tags.get("man_made") == "bridge":
        return "bridge"
    if b in ("stadium", "grandstand", "sports_centre", "stands"):
        return "stadium"
    return "building"


# The classes that stand above the street and so should be in the plate's relief.
STANDING = ("building", "stadium", "small", "parking", "bridge", "roof")
