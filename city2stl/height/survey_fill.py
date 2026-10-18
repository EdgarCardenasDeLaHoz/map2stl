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
from collections.abc import Callable
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
                    q: float = STAT_PERCENTILE) -> dict[int, tuple[float, float]]:
    """``{key: (q-th percentile of valid cells, valid coverage)}`` per footprint.

    ``arr`` is an nDSM (row 0 = north, NaN = no data) with lon/lat ``transform``;
    ``geoms`` maps a key to a shapely (Multi)Polygon or GeoJSON geometry. Footprints
    are burnt largest first, so a small footprint inside a large one (a part on its
    outline) keeps its own cells. Each uses its cells after a one-cell inward buffer
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
        pos = q / 100.0 * (k - 1)
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


def _default_polygons(features: list[dict]) -> dict[int, object]:
    """``{index: shapely geometry}`` of the :func:`fillable` features. Polygons as they are,
    lines buffered by :data:`LINE_HALF_WIDTH_M`."""
    from shapely.geometry import shape

    out = {}
    for i, feat in enumerate(features):
        if not fillable(feat.get("properties")):
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
    ``by_source``, ``unmeasured`` and ``layers`` (``{name: {"defaults", "filled"}}``),
    plus ``skipped`` or ``error`` when the fill could not run. A failing fallback leaves
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
    src = source_for(bbox)
    if src is None:
        return layers, None
    fetch = fetch or _fetch_default
    per_layer = {n: {"defaults": sum(1 for k in polys if k[0] == n), "filled": 0}
                 for n in feats}
    stats: dict = {"source": src.primary, "fallback": src.fallback, "resolution_m": None,
                   "defaults": len(polys), "filled": 0, "by_source": {},
                   "unmeasured": len(polys),
                   "layers": {n: v for n, v in per_layer.items() if v["defaults"]}}
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
    primary = footprint_stats(got[0], got[1], polys)
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
            for k, stat in footprint_stats(got_fb[0], got_fb[1], rest).items():
                v = _measured(stat)
                if v is not None:
                    heights[k] = (v, src.fallback)

    if not heights:
        return layers, stats
    new_features = {n: list(f) for n, f in feats.items()}
    for (name, i), (v, source) in heights.items():
        feat = feats[name][i]
        props = {**(feat.get("properties") or {}),
                 "height_m": round(max(MIN_HEIGHT_M, min(MAX_HEIGHT_M, v)), 1),
                 "height_source": SOURCE, "survey_source": source}
        new_features[name][i] = {**feat, "properties": props}
        stats["by_source"][source] = stats["by_source"].get(source, 0) + 1
        per_layer[name]["filled"] += 1
    stats["filled"] = len(heights)
    stats["unmeasured"] = len(polys) - len(heights)
    logger.info("Survey height fill: %d of %d fallback heights measured (%s; %s)",
                stats["filled"], len(polys), stats["by_source"],
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
