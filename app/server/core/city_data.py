"""OSM city layers for a bbox: cache first, fetch only what is missing.

The cache key is the bbox plus simplification settings, not the layer list, so a
payload cached for three layers used to be served as-is to a request for nine and
the extra layers silently never arrived. Here the missing layers are fetched and
merged into the cached payload instead.

When the exact entry is missing, a *finer* entry is reused before going to
Overpass (:func:`lookup_city_layers`): one written with a simplification
tolerance and min_area no larger than the requested ones, for the same bbox or
one enclosing it. Its features are cut to the requested bbox (features that
intersect it, as an Overpass bbox query returns them), buildings under the
requested min_area are dropped, and buildings / waterways are re-simplified at the
requested tolerance: a few seconds of vectorised shapely instead of minutes of
Overpass. A city build used to refetch every layer (979 s for Granada) because
the panel had loaded the region at other settings.
"""

from __future__ import annotations

import json
import logging
import math

import numpy as np
import shapely

import geo2stl.cache as _gc
from app.server.core.cache import osm_cache_key, read_osm_cache, write_osm_cache
from city2stl.cache_policy import (
    CITY_PIPELINE_VERSION,
    city_cache_missing_building_parts,
    city_cache_missing_height_source,
    city_cache_stale_buildings_only,
)
from city2stl.fetch import FetchCancelled, fetch_osm_data
from city2stl.height.service import enhance_city_data
from geo2stl.cache import list_osm_cache_params, write_osm_cache_params
from geo2stl.geo import M_PER_DEG_LAT, bbox_diagonal_km

logger = logging.getLogger(__name__)

#: Largest bbox diagonal (km) served with city layers unless the caller overrides
#: (``allow_large``). Beyond it an OSM fetch takes minutes and most features print
#: below nozzle width; large regions are terrain + rivers (F-REGION, Region preset).
CITY_LAYERS_MAX_DIAGONAL_KM = 25.0


class CityAreaTooLarge(ValueError):
    """City layers were requested for a bbox beyond CITY_LAYERS_MAX_DIAGONAL_KM."""


def check_city_area(north: float, south: float, east: float, west: float,
                    layers: list[str], allow_large: bool = False) -> None:
    """Raise :class:`CityAreaTooLarge` for city layers on a box > 25 km diagonal."""
    if not layers or allow_large:
        return
    diag = bbox_diagonal_km({"north": north, "south": south, "east": east, "west": west})
    if diag > CITY_LAYERS_MAX_DIAGONAL_KM:
        raise CityAreaTooLarge(
            f"City layers are limited to regions of {CITY_LAYERS_MAX_DIAGONAL_KM:.0f} km "
            f"diagonal; this one is {diag:.1f} km. Turn the city layers off (the Region "
            f"preset builds terrain with rivers and lakes), choose a smaller box, or set "
            f"allow_large_city to fetch anyway.")


def get_city_layers(north: float, south: float, east: float, west: float,
                    layers: list[str], simplify_tolerance: float = 0.5,
                    min_area: float = 5.0, *, progress=None, on_mirror=None,
                    should_cancel=None, allow_large: bool = False) -> dict:
    """Return {layer: FeatureCollection, ...} for ``layers`` (plus whatever else is cached).

    Cached layers come from :func:`lookup_city_layers` (the exact entry, or one
    derived from a finer / enclosing entry); only layers neither has are fetched.

    Optional hooks, for the background fetch (``core/city_fetch_tasks.py``):
    ``progress(layer, state)`` gets "cached" for layers served from disk, then
    "fetching" / "done" / "failed" from ``city2stl.fetch.fetch_osm_data``, and
    ``("heights", "fetching" | "done")`` around building-height enhancement;
    ``on_mirror(endpoint)`` names the Overpass mirror of each pass;
    ``should_cancel()`` is checked before each layer and before the height step
    (raises ``city2stl.fetch.FetchCancelled``). A cancelled fetch writes no cache.

    Raises :class:`CityAreaTooLarge` when layers are asked for on a bbox over
    ``CITY_LAYERS_MAX_DIAGONAL_KM`` diagonal, unless ``allow_large``.
    """
    check_city_area(north, south, east, west, layers, allow_large)
    key = osm_cache_key(north, south, east, west, simplify_tolerance, min_area)
    cached, _ = lookup_city_layers(north, south, east, west, layers,
                                   simplify_tolerance, min_area)

    missing = [name for name in layers if name not in cached]
    if progress:
        for name in layers:
            if name in cached:
                progress(name, "cached")
    if not missing:
        return cached

    logger.info("OSM layers %s not cached for %s; fetching", missing, key[:8])
    hooks = {k: v for k, v in (("progress", progress), ("on_mirror", on_mirror),
                               ("should_cancel", should_cancel)) if v}
    fetched = fetch_osm_data(north, south, east, west, missing, simplify_tolerance, min_area,
                             **hooks)
    if "buildings" in fetched:
        if should_cancel and should_cancel():
            raise FetchCancelled("City fetch cancelled")
        if progress:
            progress("heights", "fetching")
        try:
            fetched = enhance_city_data(fetched, north, south, east, west)
        except Exception as exc:
            logger.warning("City height auto-enhancement skipped: %s", exc)
        if progress:
            progress("heights", "done")

    result = {**cached, **fetched, "cache_key": key}
    result.setdefault("city_pipeline_version", CITY_PIPELINE_VERSION)
    if not any("error" in v for v in fetched.values() if isinstance(v, dict)):
        write_osm_cache(key, result, _params(north, south, east, west,
                                             simplify_tolerance, min_area))
    return result


# ---------------------------------------------------------------------------
# Cache lookup: the exact entry, else one derived from a finer / larger entry
# ---------------------------------------------------------------------------

# Tolerances (m) and min_areas (m^2) probed for entries written before the params
# sidecar existed: their key is a hash of the values' repr, so only a guess finds
# them. Panel defaults, the SDK's and the older build's values.
_LEGACY_TOLS = (0, 0.1, 0.25, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 10)
_LEGACY_AREAS = (0, 1, 2, 5, 10, 20, 25, 30, 50, 100, 200)
#: Layers whose features depend on (tolerance, min_area); the rest are fetched as-is.
_TOL_LAYERS = ("buildings", "waterways")


def _params(north, south, east, west, tol, min_area, **extra) -> dict:
    return {"north": north, "south": south, "east": east, "west": west,
            "tol": tol, "min_area": min_area, **extra}


def _usable(payload: dict) -> bool:
    """A payload get_city_layers would serve as it is (no layer outdated)."""
    return (bool(payload) and not city_cache_missing_height_source(payload)
            and not city_cache_missing_building_parts(payload))


def _bbox4(n, s, e, w) -> tuple[float, ...]:
    return tuple(round(float(v), 4) for v in (n, s, e, w))


def _variants(v: float) -> list:
    """``v`` as a key may have seen it: ``3`` and ``3.0`` hash differently."""
    return [float(v), int(v)] if float(v).is_integer() else [float(v)]


def _bbox_area(m: dict) -> float:
    return (float(m["north"]) - float(m["south"])) * (float(m["east"]) - float(m["west"]))


def _candidates(north, south, east, west, tol, min_area) -> list[dict]:
    """Cached entries this request can be derived from, best first.

    Finer or equal (``tol`` and ``min_area`` not above the request's) and covering
    the bbox. Exact-bbox entries first, then the smallest enclosing bbox; within
    those the coarsest settings (least work, closest to what a fetch returns).
    """
    req = _bbox4(north, south, east, west)
    n, s, e, w = req
    found: dict[str, dict] = {}
    for m in list_osm_cache_params():
        try:
            mn, ms, me, mw = _bbox4(m["north"], m["south"], m["east"], m["west"])
            ok = (float(m["tol"]) <= tol and float(m["min_area"]) <= min_area
                  and mn >= n and ms <= s and me >= e and mw <= w)
        except (KeyError, TypeError, ValueError):
            continue
        if ok:
            found[m["key"]] = m
    osm_dir = _gc.CACHE_ROOT / "osm"
    for t in (x for x in (*_LEGACY_TOLS, tol) if x <= tol):
        for a in (x for x in (*_LEGACY_AREAS, min_area) if x <= min_area):
            for tv in _variants(t):
                for av in _variants(a):
                    k = osm_cache_key(north, south, east, west, tv, av)
                    if k not in found and (osm_dir / f"{k}.json.gz").exists():
                        found[k] = {**_params(n, s, e, w, float(t), float(a)), "key": k,
                                    "legacy": True}
    return sorted(found.values(),
                  key=lambda m: (_bbox4(m["north"], m["south"], m["east"], m["west"]) != req,
                                 _bbox_area(m), -float(m["tol"]), -float(m["min_area"])))


def _is_fc(v) -> bool:
    return isinstance(v, dict) and isinstance(v.get("features"), list)


def derive_city_payload(payload: dict, src_tol: float, src_min_area: float,
                        north: float, south: float, east: float, west: float,
                        tol: float, min_area: float) -> dict:
    """``payload`` (fetched at ``src_tol`` / ``src_min_area`` for a bbox covering this
    one) as a fetch at ``tol`` / ``min_area`` for this bbox would about return it.

    Features intersecting the bbox are kept whole (Overpass returns them so);
    buildings below ``min_area`` m^2 are dropped; buildings and waterways are
    re-simplified at ``tol`` m (Douglas-Peucker on the finer outline: within
    ``tol`` of a direct fetch's). Other layers do not depend on either setting.
    Areas use the equirectangular scale at the bbox centre (a fraction of a
    percent from the UTM areas ``city2stl.fetch`` uses, over a city). The fetch
    filters by area *before* dissolving touching same-height buildings, this
    after, so a small building dissolved into a neighbour stays: that only keeps
    detail.
    """
    lat = math.radians((north + south) / 2)
    m2_per_deg2 = M_PER_DEG_LAT * M_PER_DEG_LAT * math.cos(lat)
    bbox = shapely.box(west, south, east, north)
    out = {k: v for k, v in payload.items() if not _is_fc(v)}
    for name, fc in payload.items():
        if not _is_fc(fc):
            continue
        feats = [f for f in fc["features"] if f.get("geometry")]
        if not feats:
            out[name] = fc
            continue
        geoms = shapely.from_geojson([json.dumps(f["geometry"]) for f in feats], on_invalid="ignore")
        keep = ~shapely.is_missing(geoms) & shapely.intersects(geoms, bbox)
        if name == "buildings" and min_area > src_min_area:
            keep &= shapely.area(geoms) * m2_per_deg2 >= min_area
        changed = np.zeros(len(feats), bool)
        if name in _TOL_LAYERS and tol > src_tol:
            idx = np.flatnonzero(keep)
            simple = shapely.simplify(geoms[idx], tol / M_PER_DEG_LAT, preserve_topology=True)
            if name == "waterways":
                simple = shapely.make_valid(simple)
            geoms[idx] = simple
            keep[idx] &= ~shapely.is_empty(simple)
            changed[idx] = True
        new_feats = []
        for k in np.flatnonzero(keep):
            f = feats[k]
            if changed[k]:
                f = {**f, "geometry": json.loads(shapely.to_geojson(geoms[k]))}
            new_feats.append(f)
        out[name] = {**fc, "features": new_feats}
    return out


def lookup_city_layers(north: float, south: float, east: float, west: float,
                       layers: list[str], simplify_tolerance: float = 0.5,
                       min_area: float = 5.0, *, write: bool = True) -> tuple[dict, str]:
    """The cached payload for this request, never fetched: ``(payload, status)``.

    ``status``: ``"exact"`` (the entry for this key); ``"derived"`` (from a finer
    or enclosing entry, :func:`derive_city_payload`, and written under this key
    when ``write``); ``"stale_buildings"`` (the entry - or a candidate's derived
    layers - minus an outdated buildings layer, which the caller refetches); ``"stale"`` (the entry is outdated and
    nothing could be derived); ``"missing"``. Staleness rules are
    ``city2stl.cache_policy``'s, for the exact entry and every candidate alike.
    """
    key = osm_cache_key(north, south, east, west, simplify_tolerance, min_area)
    cached = read_osm_cache(key) or {}
    status = "exact" if cached else "missing"
    if cached and not city_cache_missing_height_source(cached) \
            and city_cache_stale_buildings_only(cached):
        # Only the buildings' columns changed since this payload was written:
        # keep the other layers and re-fetch buildings.
        logger.info("Re-fetching buildings of an older OSM cache payload: %s", key)
        return ({k: v for k, v in cached.items() if k not in ("buildings", "city_pipeline_version")},
                "stale_buildings")
    if cached and not _usable(cached):
        logger.info("Ignoring stale OSM cache payload: %s", key)
        cached, status = {}, "stale"
    if cached:
        return cached, status
    for cand in _candidates(north, south, east, west, simplify_tolerance, min_area):
        if cand["key"] == key:
            continue
        src = read_osm_cache(cand["key"]) or {}
        partial = (bool(src) and not city_cache_missing_height_source(src)
                   and city_cache_stale_buildings_only(src))
        if partial:   # its other layers are current: use them, refetch buildings
            src = {k: v for k, v in src.items() if k not in ("buildings", "city_pipeline_version")}
        elif not _usable(src):
            continue
        if not any(_is_fc(src.get(n)) for n in layers):
            continue
        if cand.pop("legacy", False):
            write_osm_cache_params(cand["key"], cand)   # found by probing: record it
        derived = derive_city_payload(src, float(cand["tol"]), float(cand["min_area"]),
                                      north, south, east, west,
                                      float(simplify_tolerance), float(min_area))
        derived["cache_key"] = key
        clipped = _bbox4(cand["north"], cand["south"], cand["east"], cand["west"]) \
            != _bbox4(north, south, east, west)
        logger.info("OSM cache %s derived from %s (tol %s -> %s m, min_area %s -> %s m^2%s)",
                    key[:8], cand["key"][:8], cand["tol"], simplify_tolerance,
                    cand["min_area"], min_area, ", clipped" if clipped else "")
        if partial:
            return derived, "stale_buildings"   # the caller refetches buildings and writes
        if write:
            write_osm_cache(key, derived, _params(north, south, east, west, simplify_tolerance,
                                                  min_area, derived_from=cand["key"]))
        return derived, "derived"
    return {}, status
