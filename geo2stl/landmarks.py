"""Named landmarks close to a region box's edge ("Alhambra is 120 m outside the east edge").

A print whose box stops just short of the landmark the user cares about is only
noticed after the model is built. :func:`edge_landmarks` asks Overpass once for
named notable features (places of worship, town halls, castles, museums,
attractions, viewpoints, historic sites) in a band straddling the box edge and
reports each one that lies within *warn_m* of it, inside or outside.

- :func:`fetch_edge_features` — one Overpass query over the four edge strips,
  cached in the ``landmarks`` namespace of :mod:`geo2stl.cache`.
- :func:`edge_proximity` — pure geometry: where a feature's bounds sit relative
  to the box (inside / outside / crosses, which edge, how far).
- :func:`edge_landmarks` — the two together, nearest first.
"""

from __future__ import annotations

import logging
import math

from geo2stl.cache import json_cache_key, read_json_cache, write_json_cache
from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

logger = logging.getLogger(__name__)

#: Width of the queried band, centred on the edge (half inside, half outside).
DEFAULT_BAND_M = 400.0
#: Features closer than this to the edge (either side) are reported.
DEFAULT_WARN_M = 200.0

#: Overpass tag filters for "notable". Each is combined with ``["name"]``.
NOTABLE_SELECTORS = (
    '["amenity"~"^(place_of_worship|townhall)$"]',
    '["historic"]["historic"!~"^(memorial|boundary_stone|milestone|wayside_cross|'
    'wayside_shrine|marker|district|road|yes)$"]',
    '["tourism"~"^(attraction|museum|viewpoint)$"]',
)

#: Bump when the selectors or the cached shape change.
_CACHE_VERSION = 1

_USER_AGENT = "map2stl/0.1 (+https://github.com/EdgarCardenasDeLaHoz/map2stl)"


def _deg_offsets(bbox: dict, metres: float) -> tuple[float, float]:
    """(dlat, dlon) in degrees for *metres* at the box's mid-latitude."""
    mid = (bbox["north"] + bbox["south"]) / 2.0
    return metres / M_PER_DEG_LAT, metres / max(float(m_per_deg_lon(mid)), 1e-6)


def edge_strips(bbox: dict, band_m: float = DEFAULT_BAND_M) -> list[tuple[float, float, float, float]]:
    """The four (south, west, north, east) strips of width *band_m* centred on the edges."""
    n, s, e, w = bbox["north"], bbox["south"], bbox["east"], bbox["west"]
    dlat, dlon = _deg_offsets(bbox, band_m / 2.0)
    return [
        (n - dlat, w - dlon, n + dlat, e + dlon),   # north
        (s - dlat, w - dlon, s + dlat, e + dlon),   # south
        (s - dlat, e - dlon, n + dlat, e + dlon),   # east
        (s - dlat, w - dlon, n + dlat, w + dlon),   # west
    ]


def build_query(bbox: dict, band_m: float = DEFAULT_BAND_M, timeout_s: int = 60) -> str:
    """Overpass QL for named notable features in the edge band (one request)."""
    parts = []
    for strip in edge_strips(bbox, band_m):
        box = ",".join(f"{v:.6f}" for v in strip)
        for sel in NOTABLE_SELECTORS:
            parts.append(f'  nwr{sel}["name"]({box});')
    body = "\n".join(parts)
    return f"[out:json][timeout:{int(timeout_s)}];\n(\n{body}\n);\nout tags bb;"


def _element_feature(el: dict) -> dict | None:
    """Overpass element -> {name, class, type, south, west, north, east, lat, lon}."""
    tags = el.get("tags") or {}
    name = tags.get("name:en") or tags.get("name")
    if not name:
        return None
    if el.get("type") == "node" and "lat" in el:
        s = n = float(el["lat"])
        w = e = float(el["lon"])
    elif isinstance(el.get("bounds"), dict):
        b = el["bounds"]
        s, w, n, e = (float(b["minlat"]), float(b["minlon"]),
                      float(b["maxlat"]), float(b["maxlon"]))
    elif isinstance(el.get("center"), dict):
        s = n = float(el["center"]["lat"])
        w = e = float(el["center"]["lon"])
    else:
        return None
    for key in ("amenity", "historic", "tourism"):
        if key in tags:
            cls, typ = key, tags[key]
            break
    else:
        cls, typ = None, None
    return {
        "name": name, "class": cls, "type": typ,
        "osm_type": el.get("type"), "osm_id": el.get("id"),
        "south": s, "west": w, "north": n, "east": e,
        "lat": (s + n) / 2.0, "lon": (w + e) / 2.0,
    }


def fetch_edge_features(bbox: dict, band_m: float = DEFAULT_BAND_M, *,
                        use_cache: bool = True) -> list[dict]:
    """Named notable features in the band around *bbox* (one Overpass query, cached)."""
    from geo2stl.osm import overpass_query

    key = json_cache_key(
        "landmarks", _CACHE_VERSION, round(float(band_m)),
        *(round(float(bbox[k]), 4) for k in ("north", "south", "east", "west")))
    if use_cache:
        cached = read_json_cache("landmarks", key)
        if cached is not None:
            return cached
    elements = overpass_query(build_query(bbox, band_m), attempts=3, timeout_s=60,
                              user_agent=_USER_AGENT)
    seen: set = set()
    features = []
    for el in elements:
        ident = (el.get("type"), el.get("id"))
        if ident in seen:
            continue
        seen.add(ident)
        feat = _element_feature(el)
        if feat:
            features.append(feat)
    write_json_cache("landmarks", key, features)
    return features


_EDGES = ("north", "south", "east", "west")


def edge_proximity(bbox: dict, feat: dict) -> dict:
    """Where *feat*'s bounds sit relative to *bbox*.

    Returns ``{"position": "inside" | "outside" | "crosses", "edge": str,
    "distance_m": float}``. ``edge`` is one of north/south/east/west, or a corner
    ("north-east") for a feature diagonally outside. ``distance_m`` is the gap to
    the box for an outside feature, the gap to the nearest edge for an inside one,
    and how far it sticks out for one that crosses an edge.
    """
    mid = (bbox["north"] + bbox["south"]) / 2.0
    mx = float(m_per_deg_lon(mid))
    my = M_PER_DEG_LAT
    # Metres the feature extends beyond each edge (positive = beyond).
    beyond = {
        "north": (feat["north"] - bbox["north"]) * my,
        "south": (bbox["south"] - feat["south"]) * my,
        "east": (feat["east"] - bbox["east"]) * mx,
        "west": (bbox["west"] - feat["west"]) * mx,
    }
    # Metres of gap between the feature and the box beyond each edge (positive = gap).
    gap = {
        "north": (feat["south"] - bbox["north"]) * my,
        "south": (bbox["south"] - feat["north"]) * my,
        "east": (feat["west"] - bbox["east"]) * mx,
        "west": (bbox["west"] - feat["east"]) * mx,
    }
    outside = [edge for edge in _EDGES if gap[edge] > 0]
    if outside:
        if len(outside) == 1:
            return {"position": "outside", "edge": outside[0], "distance_m": gap[outside[0]]}
        ns = "north" if "north" in outside else "south"
        ew = "east" if "east" in outside else "west"
        return {"position": "outside", "edge": f"{ns}-{ew}",
                "distance_m": math.hypot(gap[ns], gap[ew])}
    crossing = [edge for edge in _EDGES if beyond[edge] > 0]
    if crossing:
        edge = max(crossing, key=lambda k: beyond[k])
        return {"position": "crosses", "edge": edge, "distance_m": beyond[edge]}
    edge = min(_EDGES, key=lambda k: -beyond[k])
    return {"position": "inside", "edge": edge, "distance_m": -beyond[edge]}


def describe(name: str, prox: dict) -> str:
    """"Alhambra is 120 m outside the east edge" / "... crosses the east edge"."""
    where = f"the {prox['edge']} {'corner' if '-' in prox['edge'] else 'edge'}"
    if prox["position"] == "crosses":
        return f"{name} crosses {where} ({prox['distance_m']:.0f} m sticks out)"
    return f"{name} is {prox['distance_m']:.0f} m {prox['position']} {where}"


def edge_landmarks(bbox: dict, warn_m: float = DEFAULT_WARN_M,
                   band_m: float = DEFAULT_BAND_M, *, features: list[dict] | None = None,
                   limit: int = 20) -> list[dict]:
    """Named notable features within *warn_m* of the box edge, nearest first.

    *features* skips the Overpass fetch (tests, or a caller that already has them).
    """
    if features is None:
        features = fetch_edge_features(bbox, band_m)
    out = []
    for feat in features:
        prox = edge_proximity(bbox, feat)
        if prox["position"] != "crosses" and prox["distance_m"] > warn_m:
            continue
        out.append({
            "name": feat["name"], "class": feat.get("class"), "type": feat.get("type"),
            "lat": feat["lat"], "lon": feat["lon"],
            "osm_type": feat.get("osm_type"), "osm_id": feat.get("osm_id"),
            "position": prox["position"], "edge": prox["edge"],
            "distance_m": round(prox["distance_m"], 1),
            "message": describe(feat["name"], prox),
        })
    # One entry per name: a castle mapped as a node and an area reads once.
    out.sort(key=lambda r: (r["position"] != "crosses", r["distance_m"]))
    unique, names = [], set()
    for r in out:
        if r["name"] in names:
            continue
        names.add(r["name"])
        unique.append(r)
    return unique[:limit]
