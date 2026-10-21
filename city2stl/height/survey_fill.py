"""city2stl/height/survey_fill.py — fill fallback heights from survey lidar.

Where a *validated* surveyed nDSM (``city2stl.height.providers.survey``) covers a city
bbox, every feature still on a fallback height gets a measured height. That is each
feature of the :data:`FILL_LAYERS` (``buildings``, and the landmark layers ``towers``,
``churches``, ``fortifications``, ``walls``) that is :func:`fillable`:

- ``height_source == "default"`` (no height or levels tag, or lowered by the low-default
  rule for ruins and enclosures), or
- ``height_source == "osm_tag"`` at exactly :data:`BULK_TAG_HEIGHT_M` (10.0 m) with no raw
  ``height`` / ``building:height`` property of 10 beside it. Granada's 24 such towers
  (Torre de la Vela, del Homenaje, de Comares ...) are the mappers' round-number
  estimate, not a measurement; the payload keeps no raw tag, so a bare 10.0 is read as
  that estimate. A feature the survey cannot measure keeps its tagged height.

**Survey wins when far off** (user decision 2026-10-17, extended 2026-10-20): every *other*
``osm_tag`` and ``osm_levels`` (floor-count) feature is also measured, and replaced only
when the survey height differs from it by more than :data:`TAG_OVERRIDE_MIN_DIFF_M`
(``height_source = "survey"``, the old height kept in ``osm_tag_height_m``). Within that it
keeps its height. A floor-count footprint under :data:`SMALL_FOOTPRINT_M2` is measured at
the median (:data:`SMALL_STAT_PERCENTILE`), not the 90th percentile, which on a few cells
reads the neighbour's wall or a chimney. **Landmarks are only raised** (:func:`is_landmark`):
the survey reads a cathedral's lead roof at 16 m and its spire at 20 m, and lowered 41 parts
and the outline to that; a tagged landmark height is never lowered, whatever the survey
says. ``merged`` and ``user_override`` features are never touched. Bulk-10 m tags above are
always filled.

Lines (city walls) are measured on a strip :data:`LINE_HALF_WIDTH_M` either side.
The measurement:

- **Statistic:** the :data:`STAT_PERCENTILE` (90th) percentile of the survey's valid cells
  inside the footprint, after a one-cell inward buffer (4-neighbour erosion) that drops the
  edge cells where a wall meets the street. Courtyards sit below the 90th percentile, so
  they do not pull it down. Footprints too small to erode (under 20 cells, or erosion keeps
  under 30 %) use every cell.
- **Coverage:** at least :data:`MIN_VALID_COVERAGE` of those cells must hold a value.
- **Clamp:** :data:`MIN_HEIGHT_M` .. :data:`MAX_HEIGHT_M`, as
  ``city2stl.heights.enhance_buildings_with_raster`` clamps raster heights.
- **Result:** ``height_source = "survey"`` and ``survey_source = <provider name>``.

Sources, per region (:data:`FILL_SOURCES`; the first entry whose primary covers the bbox):

- **Spain:** ``cnig_mdsn``, CNIG MDSn edificación. It is built from the lidar *building*
  class only, at 2.5 m, from the 2008-15 coverage. Over Granada it read 23,308
  levels-tagged buildings at a median |Δ| of 2.81 m against levels x 3.2 m minus the roof
  level, with every footprint cell valid.
- **Fallback, Andalucía:** ``rediam_mdhn``, REDIAM MDHN, used only where CNIG reads under
  :data:`NO_BUILDING_M` on the footprint or has no value there. Those are structures
  outside CNIG's building class (Alhambra towers, ruins) or built after its survey. REDIAM
  is a vegetation-height product: classified buildings are suppressed, so over an ordinary
  building it reads low or void (Granada: p90 8.9 m where CNIG reads 14.6 m). It is
  therefore never a primary building source.
- **Not in the fill until validated against tagged heights:** IGN LiDAR HD (France), ČÚZK
  (Czechia), USGS 3DEP (the US has its own per-footprint lidar pass,
  ``service._enhance_from_lidar``). See ``docs/decisions/building-heights.md``.

Rasters are read on the shared survey grid (``providers/_survey.py::lonlat_grid``) at
:data:`FILL_RESOLUTION_M`, coarser only when the bbox needs it so the grid stays within
4096 cells per side. Above :data:`MAX_FILL_RESOLUTION_M` the fill is skipped, because a
cell that coarse no longer resolves a house. Rasters are cached on disk per (provider,
bbox, resolution) by ``_survey.cached_ndsm``, so only the first fill of a city touches the
network.

Entry points: :func:`fill_layer_heights` (a ``{layer: FeatureCollection}`` payload, one
raster read per source) and :func:`fill_default_heights` (one collection). Both return new
collections and leave their input untouched. The per-footprint statistic is
:func:`footprint_stats`.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

SOURCE = "survey"
STAT_PERCENTILE = 90.0
MIN_VALID_COVERAGE = 0.5
#: Below this the primary survey sees no building on the footprint (CNIG holds 0 there).
NO_BUILDING_M = 1.0
MIN_HEIGHT_M = 3.0
MAX_HEIGHT_M = 300.0
FILL_RESOLUTION_M = 1.0
MAX_FILL_RESOLUTION_M = 2.5
#: Footprints with fewer cells than this keep every cell (no inward buffer).
_MIN_ERODE_CELLS = 20
#: Layers the fill measures; the others (roads, water, green ...) have no height to fill.
FILL_LAYERS: tuple[str, ...] = ("buildings", "towers", "churches", "fortifications", "walls")
#: A tagged height of exactly this is the mappers' round-number estimate (see the docstring).
BULK_TAG_HEIGHT_M = 10.0
#: An ``osm_tag`` height is replaced by the survey's only when they differ by more than this.
TAG_OVERRIDE_MIN_DIFF_M = 3.0
#: Floor-count (``osm_levels``) footprints under this area (m2) are measured at the median.
SMALL_FOOTPRINT_M2 = 30.0
SMALL_STAT_PERCENTILE = 50.0
#: A line (city wall) is measured on this strip either side of it.
LINE_HALF_WIDTH_M = 1.5
_M_PER_DEG = 111_320.0


@dataclass(frozen=True)
class FillSource:
    primary: str
    fallback: str | None = None


#: Validated survey sources for the fill, first match wins (see the module docstring).
FILL_SOURCES: tuple[FillSource, ...] = (FillSource("cnig_mdsn", fallback="rediam_mdhn"),)


def _providers():
    from city2stl.height.providers.survey import PROVIDERS
    return PROVIDERS


def source_for(bbox) -> FillSource | None:
    """The :data:`FILL_SOURCES` entry whose primary covers ``bbox`` (no network), or None."""
    providers = _providers()
    for src in FILL_SOURCES:
        p = providers.get(src.primary)
        if p is not None and p.covers(bbox):
            return src
    return None


def fill_resolution(bbox) -> float | None:
    """Grid cell size (m) for ``bbox``: :data:`FILL_RESOLUTION_M`, coarser in 0.5 m
    steps when the bbox would exceed the survey grid limit, or None above
    :data:`MAX_FILL_RESOLUTION_M`."""
    from city2stl.height.providers._survey import M_PER_DEG_LAT, MAX_GRID_SIDE, as_nsew

    n, s, e, w = as_nsew(bbox)
    side = max((n - s) * M_PER_DEG_LAT,
               (e - w) * M_PER_DEG_LAT * math.cos(math.radians((n + s) / 2)))
    res = max(FILL_RESOLUTION_M, math.ceil(side / MAX_GRID_SIDE * 2) / 2)
    return res if res <= MAX_FILL_RESOLUTION_M else None


def footprint_stats(arr: np.ndarray, transform, geoms: dict[int, object],
                    q: float | Mapping[int, float] = STAT_PERCENTILE,
                    ) -> dict[int, tuple[float, float]]:
    """``{key: (q-th percentile of valid cells, valid coverage)}`` per footprint.

    ``q`` is one percentile, or ``{key: percentile}`` (keys missing from it read
    :data:`STAT_PERCENTILE`). ``arr`` is an nDSM (row 0 = north, NaN = no data) with
    lon/lat ``transform``; ``geoms`` maps a key to a shapely (Multi)Polygon or GeoJSON
    geometry. Footprints are burnt into ONE label raster largest first, so a small
    footprint inside a large one keeps its own cells and the large one loses them:
    footprints that overlap by design (a ``building:part`` on its outline, a tower in
    the ``towers`` layer on the same building) belong in separate calls
    (:func:`fill_layer_heights` makes one per layer and one for the parts). Each uses its cells after a one-cell inward buffer
    when it has at least ``_MIN_ERODE_CELLS`` and the buffer keeps 30 % of them.
    A footprint covering no cell is absent; one with no valid cell reads ``(nan, 0.0)``.
    """
    from rasterio.features import rasterize
    from shapely.geometry import shape

    keys = list(geoms)
    if not keys or arr.size == 0:
        return {}
    shp = [g if hasattr(g, "area") else shape(g) for g in (geoms[k] for k in keys)]
    order = sorted(range(len(keys)), key=lambda i: -shp[i].area)
    lab = rasterize([(shp[i], i + 1) for i in order if not shp[i].is_empty],
                    out_shape=arr.shape, transform=transform, fill=0, dtype="int32")
    m = len(keys) + 1
    pad = np.pad(lab, 1, constant_values=0)
    same = ((pad[:-2, 1:-1] == lab) & (pad[2:, 1:-1] == lab)
            & (pad[1:-1, :-2] == lab) & (pad[1:-1, 2:] == lab))
    inner = np.where(same, lab, 0)
    n_full = np.bincount(lab.ravel(), minlength=m)
    n_inner = np.bincount(inner.ravel(), minlength=m)
    erode = (n_full >= _MIN_ERODE_CELLS) & (n_inner >= np.maximum(4, 0.3 * n_full))
    erode[0] = False
    use = np.where(erode[lab], inner, lab)

    valid = np.isfinite(arr)
    n_cells = np.bincount(use.ravel(), minlength=m)
    sel = (use > 0) & valid
    labels, values = use[sel], arr[sel].astype(np.float64)
    n_valid = np.bincount(labels, minlength=m)
    srt = np.lexsort((values, labels))
    labels, values = labels[srt], values[srt]
    starts = np.searchsorted(labels, np.arange(m))

    out: dict[int, tuple[float, float]] = {}
    for i in np.nonzero(n_cells)[0]:
        if i == 0:
            continue
        k = int(n_valid[i])
        cov = k / int(n_cells[i])
        if k == 0:
            out[keys[i - 1]] = (float("nan"), 0.0)
            continue
        qi = q.get(keys[i - 1], STAT_PERCENTILE) if isinstance(q, Mapping) else q
        pos = qi / 100.0 * (k - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, k - 1)
        a, b = values[starts[i] + lo], values[starts[i] + hi]
        out[keys[i - 1]] = (float(a + (pos - lo) * (b - a)), cov)
    return out


def _raw_tag_is_bulk(props: dict) -> bool:
    """True when a raw ``height`` / ``building:height`` property says exactly 10 m."""
    from city2stl.heights import parse_length_m

    for key in ("height", "building:height"):
        h = parse_length_m(props.get(key))
        if h is not None:
            return abs(h - BULK_TAG_HEIGHT_M) < 1e-6
    return False


def fillable(props: dict | None) -> bool:
    """Whether a feature's height is a fallback the survey may replace: source ``default``,
    or an ``osm_tag`` of exactly :data:`BULK_TAG_HEIGHT_M` with no raw tag of 10 beside it."""
    props = props or {}
    src = props.get("height_source")
    if src == "default":
        return True
    if src == "osm_tag":
        try:
            h = float(props.get("height_m"))
        except (TypeError, ValueError):
            return False
        return abs(h - BULK_TAG_HEIGHT_M) < 1e-6 and not _raw_tag_is_bulk(props)
    return False


def _is_tag(props: dict | None) -> bool:
    """A tagged height checked against the survey: ``osm_tag`` or floor-count ``osm_levels``."""
    return (props or {}).get("height_source") in ("osm_tag", "osm_levels")


_CHURCH_BUILDINGS = frozenset({"church", "cathedral", "chapel", "basilica", "mosque",
                               "synagogue", "temple", "monastery", "shrine", "minaret"})


def _t(props: dict, key: str) -> str:
    v = props.get(key)
    return "" if v is None or (isinstance(v, float) and v != v) else str(v).strip().lower()


def is_landmark(props: dict | None, layer: str = "buildings", has_parts: bool = False) -> bool:
    """Whether the survey may only RAISE this feature's tagged height, never lower it.

    The survey (CNIG MDSn, 2.5 m cells) reads a landmark's roofs, not its towers and
    spires: it put Granada Cathedral's outline at 16.5 m (tagged 45), its 41 parts at
    15-28 m (tagged 40-50) and the spire at 19.6 m (tagged 50). A landmark is:

    - a ``building:part`` row, or an outline that has parts (``has_parts``, computed by
      the caller from the geometry);
    - a tower: ``building`` / ``man_made`` = ``tower``, a ``tower:type``, or the
      ``towers`` layer;
    - a church or place of worship: ``amenity=place_of_worship``, a ``building`` of
      church / cathedral / chapel / basilica / mosque ..., or the ``churches`` layer;
    - a castle or fortification: ``historic`` castle / fort / fortification / citywalls,
      ``building=castle``, a ``castle_type``, or the ``fortifications`` layer;
    - any named building with a ``historic`` tag or ``tourism=attraction``.
    """
    from city2stl.heights import _ENCLOSURE_HISTORIC

    props = props or {}
    if has_parts or layer in ("towers", "churches", "fortifications"):
        return True
    part = _t(props, "building:part")
    if part and part not in ("no", "none"):
        return True
    building, historic = _t(props, "building"), _t(props, "historic")
    if "tower" in (building, _t(props, "man_made")) or _t(props, "tower:type"):
        return True
    if _t(props, "amenity") == "place_of_worship" or building in _CHURCH_BUILDINGS:
        return True
    if historic in _ENCLOSURE_HISTORIC or building == "castle" or _t(props, "castle_type"):
        return True
    return bool(_t(props, "name")) and (bool(historic) or _t(props, "tourism") == "attraction")


def _default_polygons(features: list[dict]) -> dict[int, object]:
    """``{index: shapely geometry}`` of the :func:`fillable` features and of every other
    ``osm_tag`` feature (checked against the survey). Polygons as they are, lines buffered
    by :data:`LINE_HALF_WIDTH_M`."""
    from shapely.geometry import shape

    out = {}
    for i, feat in enumerate(features):
        props = feat.get("properties")
        if not (fillable(props) or _is_tag(props)):
            continue
        geom = feat.get("geometry") or {}
        kind = geom.get("type")
        if kind not in ("Polygon", "MultiPolygon", "LineString", "MultiLineString"):
            continue
        try:
            g = shape(geom)
            if kind.endswith("LineString"):
                g = g.buffer(LINE_HALF_WIDTH_M / _M_PER_DEG)
        except Exception:
            continue
        if not g.is_empty:
            out[i] = g
    return out


def _has_parts(features: list[dict], rows: dict[int, object]) -> set[int]:
    """Indices (from ``rows``, ``{index: geometry}``) of the non-part features that overlap
    a ``building:part`` of ``features``: an outline whose parts are the building."""
    from shapely import STRtree
    from shapely.geometry import shape

    parts = []
    for f in features:
        if _t(f.get("properties") or {}, "building:part") not in ("", "no", "none"):
            try:
                g = shape(f.get("geometry") or {})
            except Exception:
                continue
            if not g.is_empty and g.geom_type in ("Polygon", "MultiPolygon"):
                parts.append(g)
    if not parts:
        return set()
    tree = STRtree(parts)
    out = set()
    for i, g in rows.items():
        if _t(features[i].get("properties") or {}, "building:part") not in ("", "no", "none"):
            continue
        for j in tree.query(g, predicate="intersects"):
            try:
                if g.intersection(parts[j]).area > 0:
                    out.add(i)
                    break
            except Exception:
                continue
    return out


def _area_m2(g) -> float:
    """Approximate area (m2) of a lon/lat geometry."""
    lat = g.centroid.y
    return g.area * _M_PER_DEG * _M_PER_DEG * math.cos(math.radians(lat))


def _burn_stats(arr, transform, polys: dict, parts: set, q: Mapping | None = None) -> dict:
    """:func:`footprint_stats` of ``{(layer, index): geometry}``, one burn per layer, with
    the keys in ``parts`` (``building:part`` rows) in a burn of their own, so that a part
    does not take cells from its outline and a ``towers`` / ``churches`` copy does not take
    a building's. ``q`` is the per-key percentile (default :data:`STAT_PERCENTILE`)."""
    groups: dict[tuple, list] = {}
    for k in polys:
        groups.setdefault((k[0], k in parts), []).append(k)
    q = q or {}
    out: dict = {}
    for keys in groups.values():
        got = footprint_stats(arr, transform, dict(enumerate(polys[k] for k in keys)),
                              {n: q.get(k, STAT_PERCENTILE) for n, k in enumerate(keys)})
        out.update({keys[n]: v for n, v in got.items()})
    return out


def _fetch_default(name: str, bbox, resolution_m: float):
    from city2stl.height.providers.survey import ndsm_for_bbox
    return ndsm_for_bbox(name, bbox, resolution_m)


def _measured(stat: tuple[float, float] | None) -> float | None:
    if stat is None:
        return None
    value, cov = stat
    if cov < MIN_VALID_COVERAGE or not math.isfinite(value) or value < NO_BUILDING_M:
        return None
    return value


def fill_layer_heights(layers: dict, bbox, *,
                       names: tuple[str, ...] = FILL_LAYERS,
                       fetch: Callable[[str, tuple, float], tuple | None] | None = None,
                       ) -> tuple[dict, dict | None]:
    """``(layers, stats)``: ``layers`` (``{name: FeatureCollection}``) with the
    :func:`fillable` features of ``names`` measured from survey lidar.

    ``bbox`` is ``(north, south, east, west)``. ``fetch(name, bbox, resolution_m)``
    returns ``(array, transform)`` or None (default: the survey registry, disk-cached);
    each source is read once for all layers. ``stats`` is None when no fill was tried:
    nothing fillable, or no validated survey covers ``bbox``. Otherwise it is a dict
    with ``source``, ``fallback``, ``resolution_m``, ``defaults``, ``filled``,
    ``by_source``, ``unmeasured``, ``tag_checked``, ``tag_replaced`` and ``layers``
    (``{name: {"defaults", "filled", "tag_replaced"}}``; ``defaults`` / ``filled`` /
    ``unmeasured`` count only the :func:`fillable` features, the other ``osm_tag``
    features are ``tag_checked`` and, when the survey is far off, ``tag_replaced``), plus ``skipped`` or ``error`` when the fill could not run. A failing fallback leaves
    the primary's heights in place. The input is never mutated; a new dict (with new
    collections for the layers that changed) is returned when anything changed.
    """
    feats: dict[str, list] = {}
    polys: dict[tuple[str, int], object] = {}
    for name in names:
        fc = (layers or {}).get(name)
        features = fc.get("features") if isinstance(fc, dict) else None
        if not features:
            continue
        feats[name] = features
        polys.update({(name, i): g for i, g in _default_polygons(features).items()})
    if not polys:
        return layers, None
    fill_keys = {k for k in polys if fillable(feats[k[0]][k[1]].get("properties"))}
    if not fill_keys and not any(_is_tag(feats[k[0]][k[1]].get("properties")) for k in polys):
        return layers, None
    src = source_for(bbox)
    if src is None:
        return layers, None
    fetch = fetch or _fetch_default
    per_layer = {n: {"defaults": sum(1 for k in polys if k[0] == n and k in fill_keys),
                     "filled": 0, "tag_replaced": 0} for n in feats}
    n_tag = len(polys) - len(fill_keys)
    stats: dict = {"source": src.primary, "fallback": src.fallback, "resolution_m": None,
                   "defaults": len(fill_keys), "filled": 0, "by_source": {},
                   "unmeasured": len(fill_keys), "tag_checked": n_tag, "tag_replaced": 0,
                   "layers": {n: v for n, v in per_layer.items()
                              if any(k[0] == n for k in polys)}}
    res = fill_resolution(bbox)
    if res is None:
        stats["skipped"] = f"bbox needs cells coarser than {MAX_FILL_RESOLUTION_M} m"
        return layers, stats
    stats["resolution_m"] = res

    try:
        got = fetch(src.primary, bbox, res)
    except Exception as exc:
        logger.warning("Survey height fill: %s failed: %s", src.primary, exc)
        stats["error"] = f"{src.primary}: {exc}"
        return layers, stats
    if got is None:
        stats["skipped"] = f"{src.primary} has no data over the bbox"
        return layers, stats
    part_keys = {k for k in polys
                 if _t(feats[k[0]][k[1]].get("properties") or {}, "building:part")
                 not in ("", "no", "none")}
    with_parts: set[tuple[str, int]] = set()
    if "buildings" in feats:
        rows = {k[1]: g for k, g in polys.items() if k[0] == "buildings" and k not in part_keys}
        with_parts = {("buildings", i) for i in _has_parts(feats["buildings"], rows)}
    landmark = {k for k in polys
                if is_landmark(feats[k[0]][k[1]].get("properties"), k[0], k in with_parts)}
    q = {k: SMALL_STAT_PERCENTILE for k, g in polys.items()
         if (feats[k[0]][k[1]].get("properties") or {}).get("height_source") == "osm_levels"
         and _area_m2(g) < SMALL_FOOTPRINT_M2}
    primary = _burn_stats(got[0], got[1], polys, part_keys, q)
    heights: dict[tuple[str, int], tuple[float, str]] = {}
    for k in polys:
        v = _measured(primary.get(k))
        if v is not None:
            heights[k] = (v, src.primary)

    rest = {k: g for k, g in polys.items() if k not in heights}
    fb = _providers().get(src.fallback) if src.fallback else None
    if rest and fb is not None and fb.covers(bbox):
        try:
            got_fb = fetch(src.fallback, bbox, res)
        except Exception as exc:
            logger.warning("Survey height fill: fallback %s failed: %s", src.fallback, exc)
            stats["error"] = f"{src.fallback}: {exc}"
            got_fb = None
        if got_fb is not None:
            for k, stat in _burn_stats(got_fb[0], got_fb[1], rest, part_keys, q).items():
                v = _measured(stat)
                if v is not None:
                    heights[k] = (v, src.fallback)

    # A tagged (non-fillable) row: the survey replaces the tag only when far off.
    for k in [k for k in heights if k not in fill_keys]:
        tag_h = feats[k[0]][k[1]]["properties"].get("height_m")
        shown = round(max(MIN_HEIGHT_M, min(MAX_HEIGHT_M, heights[k][0])), 1)
        if isinstance(tag_h, (int, float)) and abs(shown - tag_h) <= TAG_OVERRIDE_MIN_DIFF_M:
            del heights[k]
        elif isinstance(tag_h, (int, float)) and k in landmark and shown < tag_h:
            del heights[k]   # a landmark is only ever raised
    if not heights:
        return layers, stats
    new_features = {n: list(f) for n, f in feats.items()}
    for (name, i), (v, source) in heights.items():
        feat = feats[name][i]
        old = feat.get("properties") or {}
        props = {**old,
                 "height_m": round(max(MIN_HEIGHT_M, min(MAX_HEIGHT_M, v)), 1),
                 "height_source": SOURCE, "survey_source": source}
        if _is_tag(old):
            props["osm_tag_height_m"] = old.get("height_m")
        new_features[name][i] = {**feat, "properties": props}
        stats["by_source"][source] = stats["by_source"].get(source, 0) + 1
        per_layer[name]["tag_replaced" if (name, i) not in fill_keys else "filled"] += 1
    stats["tag_replaced"] = sum(1 for k in heights if k not in fill_keys)
    stats["filled"] = len(heights) - stats["tag_replaced"]
    stats["unmeasured"] = len(fill_keys) - stats["filled"]
    logger.info("Survey height fill: %d of %d fallback heights measured, %d of %d tagged "
                "heights replaced (%s; %s)",
                stats["filled"], len(fill_keys), stats["tag_replaced"], n_tag, stats["by_source"],
                {n: v["filled"] for n, v in per_layer.items()})
    out = dict(layers)
    for name in {k[0] for k in heights}:
        out[name] = {**layers[name], "features": new_features[name]}
    return out, stats


def fill_default_heights(fc: dict, bbox, *,
                         fetch: Callable[[str, tuple, float], tuple | None] | None = None,
                         ) -> tuple[dict, dict | None]:
    """``(fc, stats)``: one collection filled as :func:`fill_layer_heights` fills a layer
    (the buildings layer, for ``service.enhance_city_data``). Same arguments and
    ``stats`` (None when no fill was tried); the input is untouched."""
    out, stats = fill_layer_heights({"buildings": fc}, bbox, names=("buildings",),
                                    fetch=fetch)
    return out["buildings"], stats
