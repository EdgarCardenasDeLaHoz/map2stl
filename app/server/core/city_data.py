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

import hashlib
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
from city2stl.fetch import FetchCancelled, OverpassUpstreamError, fetch_osm_data
from city2stl.height.service import enhance_city_data
from city2stl.height.survey_fill import fill_layer_heights, fillable
from city2stl.heights import apply_low_default_heights
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
                    should_cancel=None, allow_large: bool = False,
                    roof_shapes="auto", roof_progress=None) -> dict:
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

    ``roof_shapes`` (``"auto"`` | ``"off"``, or a bool) is the roof setting: ``"auto"``
    classifies the untagged buildings' roofs on read, after the survey fill
    (:func:`with_roof_shapes`); ``"off"`` returns the payload exactly as before.
    ``roof_progress(done, total)`` reports that classification's progress.

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
        return with_roof_shapes(
            with_survey_heights(with_low_default_heights(cached), north, south, east, west),
            north, south, east, west, roof_shapes, roof_progress)

    logger.info("OSM layers %s not cached for %s; fetching", missing, key[:8])
    hooks = {k: v for k, v in (("progress", progress), ("on_mirror", on_mirror),
                               ("should_cancel", should_cancel)) if v}
    try:
        fetched = fetch_osm_data(north, south, east, west, missing, simplify_tolerance, min_area,
                                 **hooks)
    except OverpassUpstreamError as exc:
        return _serve_stale_or_raise(exc, north, south, east, west, layers,
                                     simplify_tolerance, min_area, key)
    except RuntimeError as exc:
        if "Overpass" not in str(exc):
            raise
        return _serve_stale_or_raise(exc, north, south, east, west, layers,
                                     simplify_tolerance, min_area, key)
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
    return with_roof_shapes(
        with_survey_heights(with_low_default_heights(result), north, south, east, west),
        north, south, east, west, roof_shapes, roof_progress)


def with_survey_heights(payload: dict, north: float, south: float, east: float,
                        west: float) -> dict:
    """``payload`` with its fallback-height features measured from validated survey
    lidar (``city2stl.height.survey_fill.fill_layer_heights``: CNIG in Spain, REDIAM
    as the fallback in Andalucía), applied on read like :func:`with_low_default_heights`.

    Fills ``buildings`` and the landmark layers (``towers``, ``churches``,
    ``fortifications``, ``walls``): features on ``default``, and a bare 10.0 m
    ``osm_tag`` (``survey_fill.fillable``). The fill stats go in
    ``payload["survey_fill"]`` (``filled``, ``defaults``, ``unmeasured``, ``by_source``,
    per-layer ``layers``, ``skipped`` / ``error`` when it could not run), so a failed fill
    is visible; they are absent when no fill was tried.

    So a city cached before the fill, or a ruin the low-default rule just put back
    on ``default``, is measured without a refetch. Every reader of the cached payload
    runs it (:func:`get_city_layers`, :func:`read_city_layers_any_tolerance`, the
    pre-flight): the first read of a covered city fetches the survey raster once
    (disk-cached after), outside a covered region it costs nothing. Failures leave
    the payload as it was. The payload passed in is not mutated."""
    if not isinstance(payload, dict):
        return payload
    try:
        layers, stats = fill_layer_heights(payload, (north, south, east, west))
    except Exception as exc:
        logger.warning("Survey height fill skipped: %s", exc)
        return payload
    if not stats:
        return payload
    return {**layers, "survey_fill": stats}


#: ``roof_source`` values written by ``city2stl.roof_classifier.classify_roof_shapes``.
_CLASSIFIER_SOURCES = ("roof_model_gbm", "satellite_classify")
_ROOF_FIELDS = ("roof:shape", "roof:height", "roof_source", "roof_confidence")
_OFF_WORDS = {"off", "false", "0", "no", "none", "osm"}
_roof_memo: dict = {}
_ROOF_MEMO_SIZE = 3


def roof_shapes_mode(value) -> str:
    """The roof-shape setting as ``"auto"`` or ``"off"``.

    Unset is ``"auto"`` (the default); a bool maps True -> auto, False -> off (the
    Sloped-roofs switch ``#cityRoofShapes`` is a checkbox); a string is off when it
    is one of off / false / 0 / no / none / osm, auto otherwise."""
    if value is None:
        return "auto"
    if isinstance(value, bool):
        return "auto" if value else "off"
    return "off" if str(value).strip().lower() in _OFF_WORDS else "auto"


def _roof_key(bbox: tuple, features: list) -> tuple:
    h = hashlib.sha1()
    for f in features:
        props = (f or {}).get("properties") or {}
        h.update(json.dumps(((f or {}).get("geometry"), props.get("roof:shape")),
                            separators=(",", ":")).encode())
    return (tuple(round(v, 6) for v in bbox), len(features), h.hexdigest())


def with_roof_shapes(payload: dict, north: float, south: float, east: float,
                     west: float, mode="auto", progress=None) -> dict:
    """``payload`` with the ``roof:shape`` of its untagged ``buildings`` classified
    (``city2stl.roof_classifier.classify_roof_shapes``), applied on read after the
    survey fill, like :func:`with_survey_heights` (so no cache version bump).

    ``mode`` is :func:`roof_shapes_mode`'s: ``"off"`` returns ``payload`` itself, nothing
    imported or fetched. A tagged ``roof:shape`` is never overwritten. Classified
    features carry ``roof_source`` (``roof_model_gbm`` / ``satellite_classify``) and
    ``roof_confidence``. The stats go in ``payload["roof_shapes"]``: ``mode``, ``total``,
    ``classified``, ``unchanged`` (already tagged), ``skipped``, ``by_shape`` (histogram of
    the classified), ``cached`` (how many of the classified came from the on-disk roof
    cache), and ``reason`` when nothing could be classified.

    The classifier reads the trained checkpoint's own zoom-18 tiles (disk-cached), so it
    needs no image from the caller; with no checkpoint or no tiles it skips quietly with a
    ``reason``, never raising. Each answer is stored per footprint on disk
    (``city2stl.roof_cache``), so only footprints never seen before are classified (about
    11 ms each); the last results are also kept in memory per bbox and footprint set.
    ``progress(done, total)`` reports the classifier's progress over the uncached
    buildings. After the classifier, ``city2stl.roof_imagery.apply`` may add ridge
    direction, hips and courtyards read from the same tiles (only the switches that
    measured out as winners; stats in ``payload["roof_shapes"]["imagery"]``).
    The payload passed in is not mutated."""
    if roof_shapes_mode(mode) == "off" or not isinstance(payload, dict):
        return payload
    fc = payload.get("buildings")
    features = fc.get("features") if _is_fc(fc) else None
    if not features:
        return payload
    bbox = (north, south, east, west)
    key = _roof_key(bbox, features) + (_imagery_id(),)
    hit = _roof_memo.get(key)
    if hit is None:
        hit = _classify_buildings(fc, bbox, progress)
        hit = _add_imagery(fc, hit, bbox)
        _roof_memo[key] = hit
        while len(_roof_memo) > _ROOF_MEMO_SIZE:
            _roof_memo.pop(next(iter(_roof_memo)))
    new_fc, stats = hit
    out = {**payload, "roof_shapes": stats}
    if new_fc is not None:
        out["buildings"] = new_fc
    return out


def _imagery_id() -> str:
    """Version and settings of the imagery pass, part of the memo key; "" when it cannot load."""
    try:
        from city2stl import roof_imagery
        return f"{roof_imagery.IMAGERY_VERSION}:{roof_imagery.settings_id()}"
    except Exception:  # noqa: BLE001 - imagery detail is optional
        return ""


def _add_imagery(fc: dict, hit: tuple, bbox: tuple) -> tuple[dict | None, dict]:
    """``hit`` (the classifier's ``(collection or None, stats)``) with the imagery pass
    (``city2stl.roof_imagery.apply``) run on its result; stats under ``"imagery"``.
    Never raises; with every imagery switch off the input comes back unchanged."""
    new_fc, stats = hit
    try:
        from city2stl import roof_imagery
        res, istats = roof_imagery.apply(new_fc or fc, bbox)
    except Exception as exc:  # noqa: BLE001 - imagery detail is optional
        return new_fc, {**stats, "imagery": {"reason": f"{type(exc).__name__}: {exc}"}}
    if res is None:
        return new_fc, {**stats, "imagery": istats} if istats.get("error") else stats
    return res, {**stats, "imagery": istats}


def _roof_untagged(feat) -> bool:
    """True when the classifier may set this feature's roof (same test as
    ``classify_roof_shapes``: no ``roof:shape``, or ``unknown``)."""
    shape = ((feat or {}).get("properties") or {}).get("roof:shape")
    return str(shape or "").strip().lower() in ("", "unknown")


def _classify_buildings(fc: dict, bbox: tuple, progress=None) -> tuple[dict | None, dict]:
    """``(classified collection or None, stats)``; never raises.

    Answers already in the on-disk roof cache (``city2stl.roof_cache``) are applied
    directly; only the rest go to the classifier, and its answers are stored.
    ``progress(done, total)`` counts the buildings actually classified."""
    feats = fc.get("features") or []
    stats: dict = {"mode": "auto", "total": len(feats), "classified": 0, "cached": 0,
                   "unchanged": 0, "skipped": 0, "by_shape": {}}
    try:
        from city2stl import roof_cache, roof_model
        from city2stl.roof_classifier import classify_roof_shapes

        if roof_model.load() is None:
            stats["reason"] = "no roof model checkpoint and no satellite image"
            return None, stats
        model = roof_cache.model_id()
        todo = [i for i, f in enumerate(feats) if _roof_untagged(f)]
        keys = {i: roof_cache.feature_key(feats[i], model) for i in todo}
        hits = roof_cache.lookup(sorted(set(keys.values())))
        miss = [i for i in todo if keys[i] not in hits]
        out = list(feats)
        if miss:
            res = classify_roof_shapes({**fc, "features": [feats[i] for i in miss]}, None,
                                       bbox, use_model=True, progress=progress)
            rows = []
            for i, f in zip(miss, res.get("features") or [], strict=True):
                out[i] = f
                p = (f or {}).get("properties") or {}
                if p.get("roof_source") in _CLASSIFIER_SOURCES:
                    rows.append((keys[i], p["roof:shape"], p.get("roof_confidence"),
                                 p["roof_source"]))
            roof_cache.store(rows)
        for i in todo:
            if keys[i] in hits:
                shape, conf, source = hits[keys[i]]
                f = feats[i]
                out[i] = {**f, "properties": {**(f.get("properties") or {}), "roof:shape": shape,
                                              "roof_source": source, "roof_confidence": conf}}
    except Exception as exc:
        logger.warning("Roof classification skipped: %s", exc)
        stats["reason"] = f"error: {exc}"
        return None, stats
    by_shape: dict[str, int] = {}
    for i in todo:
        p = (out[i] or {}).get("properties") or {}
        if p.get("roof_source") in _CLASSIFIER_SOURCES:
            by_shape[p["roof:shape"]] = by_shape.get(p["roof:shape"], 0) + 1
    classified = sum(by_shape.values())
    stats.update(classified=classified, cached=len(todo) - len(miss),
                 unchanged=len(feats) - len(todo), skipped=len(todo) - classified,
                 by_shape=by_shape)
    if not classified:
        stats["reason"] = "the roof model measured no building (no tiles?)"
        return None, stats
    return {**fc, "features": out}, stats


_HEIGHT_KEYS = ("height_m", "height_source", "survey_source")


def _geom_key(feat) -> str:
    return json.dumps((feat or {}).get("geometry"), sort_keys=True, separators=(",", ":"))


def merge_layer_data(layers: dict, layer_data: dict | None, enabled=None) -> dict:
    """``layers`` with the client's ``layer_data`` collections merged onto them (a new dict).

    A client collection (the Buildings panel's edited layer, ``building-heights.js::
    buildingsWithOverrides``) used to replace the server's layer, dropping the survey
    heights the server filled (:func:`with_survey_heights`) when the client's copy
    predates them. Now the client's features and properties win, except a feature whose
    height is still a fallback (``survey_fill.fillable``, not a ``user_override``) takes
    the server's ``survey`` height when the server's matching feature has one. Features
    match by position when the collections line up (same length, same geometry at each
    index), else by geometry; a client feature with no match is kept as sent. Layers not
    in ``enabled`` (when given) are skipped; a layer the server does not have is the
    client's as it is."""
    out = dict(layers)
    for name, client in (layer_data or {}).items():
        if enabled is not None and name not in enabled:
            continue
        server = layers.get(name)
        if not (_is_fc(client) and _is_fc(server)):
            out[name] = client
            continue
        cf, sf = client.get("features") or [], server.get("features") or []
        aligned = len(cf) == len(sf) and all(
            (a or {}).get("geometry") == (b or {}).get("geometry") for a, b in zip(cf, sf, strict=True))
        by_geom = None if aligned else {_geom_key(f): f for f in sf}
        merged = []
        for i, feat in enumerate(cf):
            match = sf[i] if aligned else by_geom.get(_geom_key(feat))
            props = feat.get("properties") or {}
            sprops = (match or {}).get("properties") or {}
            if (match is not None and fillable(props) and sprops.get("height_source") == "survey"):
                props = {**props, **{k: sprops[k] for k in _HEIGHT_KEYS if k in sprops}}
                feat = {**feat, "properties": props}
            if (match is not None and sprops.get("roof_source") in _CLASSIFIER_SOURCES
                    and str(props.get("roof:shape") or "").strip().lower() in ("", "unknown")):
                # The client has no explicit roof: keep the shape the server classified.
                keep = {k: sprops[k] for k in _ROOF_FIELDS if k in sprops
                        and not (k == "roof:height" and props.get(k))}
                feat = {**feat, "properties": {**props, **keep}}
            merged.append(feat)
        out[name] = {**client, "features": merged}
    return out


def with_low_default_heights(payload: dict) -> dict:
    """``payload`` with its default-height ruins and fortress enclosures, and its
    ruins whose height came from a levels tag, at 3 m with ``height_source``
    ``"default"`` (``city2stl.heights.apply_low_default_heights``), applied on
    read so city caches written before that rule need no refetch (no cache
    version bump). Idempotent; the payload passed in is not mutated."""
    fc = payload.get("buildings") if isinstance(payload, dict) else None
    if not _is_fc(fc):
        return payload
    fc, n = apply_low_default_heights(fc)
    if not n:
        return payload
    logger.info("Lowered %d ruins / enclosures to the 3 m default", n)
    return {**payload, "buildings": fc}


def _serve_stale_or_raise(exc: Exception, north, south, east, west, layers, tol, min_area,
                          key: str) -> dict:
    """No Overpass mirror answered: serve an outdated cached payload, flagged, or re-raise.

    The payload is the exact entry or one derived from a finer / enclosing entry, taken
    whatever its pipeline version (an old payload still has the right footprints; its
    heights may be older). It carries ``stale_cache_warning`` and is never written back,
    so the next request tries Overpass again.
    """
    stale = _stale_payload(north, south, east, west, layers, tol, min_area, key)
    if not stale:
        raise exc
    version = int(stale.get("city_pipeline_version") or 0)
    msg = (f"Overpass is not answering ({exc}); using an outdated cached copy of the city "
           f"data (pipeline v{version}, current v{CITY_PIPELINE_VERSION}). Re-fetch once "
           f"Overpass is back.")
    logger.warning(msg)
    return {**stale, "cache_key": key, "stale_cache_warning": msg}


def _stale_payload(north, south, east, west, layers, tol, min_area, key: str) -> dict:
    """Any cached payload holding every requested layer, outdated or not, else ``{}``."""
    def has_all(p: dict) -> bool:
        return bool(p) and all(_is_fc(p.get(n)) for n in layers)

    exact = read_osm_cache(key) or {}
    if has_all(exact):
        return exact
    for cand in _candidates(north, south, east, west, tol, min_area):
        src = read_osm_cache(cand["key"]) or {}
        if has_all(src):
            return derive_city_payload(src, float(cand["tol"]), float(cand["min_area"]),
                                       north, south, east, west, float(tol), float(min_area))
    return {}


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


#: Upper bound on a simplification tolerance, for lookups that accept any.
_ANY_TOLERANCE_M = 1000.0


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


def read_city_layers_any_tolerance(north: float, south: float, east: float, west: float,
                                   min_area: float = 5.0) -> tuple[dict, str | None]:
    """A cached city payload for this bbox, whatever tolerance it was
    fetched at: ``(payload, cache_key)``, or ``({}, None)``. Never fetches.

    For the raster readers (composite ``osm_*`` channels, ``/api/cities/raster``,
    enhance-heights), which do not depend on the simplification tolerance. They
    used to read the exact 0.5 m key; the Cities panel fetches at 3.0 m, and
    :func:`lookup_city_layers` only derives coarser from finer, so they found
    nothing and the channels came back as zeros (audit 2026-10-05).

    The best candidate (exact bbox first, then the smallest enclosing one) is
    clipped to the bbox and filtered at ``min_area``; it is not re-simplified.
    The payload has the survey heights applied (:func:`with_survey_heights`), so the
    raster readers see the same heights as the export.
    """
    for cand in _candidates(north, south, east, west, _ANY_TOLERANCE_M, min_area):
        # Not _usable(): an older payload (e.g. without height sources) still
        # has the right geometry to rasterise, and the exact-key read this
        # replaces served it as well.
        src = read_osm_cache(cand["key"]) or {}
        if not any(_is_fc(src.get(n)) for n in ("buildings", "roads", "waterways", "walls")):
            continue
        tol = float(cand["tol"])
        if _bbox4(cand["north"], cand["south"], cand["east"], cand["west"]) \
                == _bbox4(north, south, east, west) and float(cand["min_area"]) == min_area:
            return (with_survey_heights(with_low_default_heights(src), north, south, east, west),
                    cand["key"])
        return (with_survey_heights(with_low_default_heights(derive_city_payload(
                    src, tol, float(cand["min_area"]), north, south, east, west, tol,
                    float(min_area))), north, south, east, west),
                cand["key"])
    return {}, None

