"""city2stl.skyline.benchmark - score skyline heights against surveyed truth (F-SKYBENCH).

Two halves, both pure enough to test without network:

*Truth per footprint* (:func:`footprint_truth`). For every footprint, the p95
of an nDSM (height above ground) inside the footprint buffered inward by
``INWARD_BUFFER_M`` (drops the facade and street cells that bleed in at the
edge), from two independent sources:

* a surveyed lidar nDSM (``city2stl.height.providers.survey``), and
* Google Photorealistic 3D Tiles (``Google3DProvider``).

They are cross-checked (:func:`cross_check`): ``confirmed`` when they agree
within max(3 m, 10 %), else ``disputed`` (new construction, demolition,
vegetation, a bad ground estimate). A footprint only one source could read is
``survey_only`` / ``tiles_only``. The headline scores confirmed buildings only.
Big bboxes are fetched in tiles (``TILE_M`` core + ``TILE_MARGIN_M`` margin, so
a footprint straddling two tiles is read whole from the tile holding its
centroid); the survey grid limit is 4096 cells a side, 2 km at IGN's 0.5 m.

*Scorer* (:func:`score_region`). Joins a run's ``heights.json`` rows to truth by
footprint IoU (``feature_id`` is not stable across OSM fetches), then reports
MAE, median AE, bias and % within 15 / 25 %, overall, by height band, by number
of views and of seeds, the coverage (scored / confirmed truth) per band, and the
same metrics for the OSM tags, so the old yardstick's own error is visible.

Entry point: ``city2stl/skyline/scripts/10_benchmark.py``. Plan:
``docs/plans/active/F-SKYBENCH-height-benchmark.md``.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path

import numpy as np

from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

logger = logging.getLogger(__name__)

#: region -> survey provider (``survey.PROVIDERS`` name). The benchmark set
#: chosen by the user on 2026-10-03.
BENCHMARK_REGIONS: dict[str, str] = {
    "miami": "usgs_3dep",
    "chicago": "usgs_3dep",
    "seattle": "usgs_3dep",
    "boston": "usgs_3dep",
    "benidorm": "cnig_mdsn",
    "la_defense": "ign_lidarhd",
    "madrid_cuatro_torres": "cnig_mdsn",
    "prague_pankrac": "cuzk_dmp",
}

RUNS_DIR = Path(__file__).resolve().parents[2] / "runs" / "benchmark"

INWARD_BUFFER_M = 1.5
#: Fewer cells than this inside the buffered footprint and the p95 is noise.
MIN_CELLS = 4
#: ...and at least this fraction of them must carry a value.
MIN_FINITE_FRACTION = 0.5
TRUTH_PERCENTILE = 95.0
#: Footprints smaller than this are sheds and kiosks: skipped for truth.
MIN_AREA_M2 = 40.0
TILE_M = 1500.0
TILE_MARGIN_M = 100.0
TILES_RESOLUTION_M = 1.0
#: Cross-check tolerance: agree within max(abs, rel * taller).
AGREE_ABS_M = 3.0
AGREE_REL = 0.10
#: Minimum footprint IoU for a run row to join a truth footprint.
MIN_IOU = 0.5

BANDS: tuple[tuple[str, float, float], ...] = (
    ("<30", 0.0, 30.0),
    ("30-60", 30.0, 60.0),
    ("60-100", 60.0, 100.0),
    (">100", 100.0, math.inf),
)

#: (bbox nsew) -> (array row0=north, lon/lat Affine) | None
NdsmFetch = Callable[[tuple[float, float, float, float]], "tuple[np.ndarray, object] | None"]


# ── geometry helpers ────────────────────────────────────────────────

def _to_metres(geom, lat0: float, lon0: float):
    import shapely
    kx = m_per_deg_lon(lat0)
    return shapely.transform(
        geom, lambda c: np.column_stack(((c[:, 0] - lon0) * kx, (c[:, 1] - lat0) * M_PER_DEG_LAT)))


def _to_lonlat(geom, lat0: float, lon0: float):
    import shapely
    kx = m_per_deg_lon(lat0)
    return shapely.transform(
        geom, lambda c: np.column_stack((c[:, 0] / kx + lon0, c[:, 1] / M_PER_DEG_LAT + lat0)))


def polygon_from_ring(ring) -> object | None:
    """Shapely polygon from a ``[[lon, lat], ...]`` ring; None when degenerate."""
    from shapely.geometry import Polygon
    if not ring or len(ring) < 3:
        return None
    poly = Polygon(ring)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return None if poly.is_empty else poly


def area_m2(poly) -> float:
    c = poly.centroid
    return float(_to_metres(poly, c.y, c.x).area)


def inward_buffer(poly, buffer_m: float = INWARD_BUFFER_M):
    """``poly`` shrunk by ``buffer_m``; the original when shrinking would empty it."""
    c = poly.centroid
    shrunk = _to_metres(poly, c.y, c.x).buffer(-buffer_m)
    if shrunk.is_empty or shrunk.area < 1.0:
        return poly
    return _to_lonlat(shrunk, c.y, c.x)


def tile_bboxes(bbox_nsew, tile_m: float = TILE_M) -> list[tuple[float, float, float, float]]:
    """Split ``bbox_nsew`` into core tiles of about ``tile_m`` a side (row-major from the north)."""
    n, s, e, w = (float(v) for v in bbox_nsew)
    lat0 = (n + s) / 2
    ny = max(1, math.ceil((n - s) * M_PER_DEG_LAT / tile_m))
    nx = max(1, math.ceil((e - w) * m_per_deg_lon(lat0) / tile_m))
    dlat, dlon = (n - s) / ny, (e - w) / nx
    return [(n - i * dlat, n - (i + 1) * dlat, w + (j + 1) * dlon, w + j * dlon)
            for i in range(ny) for j in range(nx)]


def _expand(bbox_nsew, margin_m: float):
    n, s, e, w = bbox_nsew
    dlat = margin_m / M_PER_DEG_LAT
    dlon = margin_m / m_per_deg_lon((n + s) / 2)
    return n + dlat, s - dlat, e + dlon, w - dlon


def sample_p95(arr: np.ndarray, transform, poly) -> tuple[float | None, int]:
    """p95 of ``arr`` under ``poly`` (lon/lat) and the cell count; None when unreadable."""
    from rasterio.features import geometry_mask
    try:
        inside = geometry_mask([poly], out_shape=arr.shape, transform=transform, invert=True)
    except ValueError:
        return None, 0
    n = int(inside.sum())
    if n < MIN_CELLS:
        return None, n
    vals = arr[inside]
    vals = vals[np.isfinite(vals)]
    if vals.size < MIN_FINITE_FRACTION * n:
        return None, n
    return float(np.percentile(vals, TRUTH_PERCENTILE)), n


# ── truth ───────────────────────────────────────────────────────────

def cross_check(survey_m: float | None, tiles_m: float | None) -> tuple[str, float | None]:
    """(status, truth height) from the two sources. The survey value is the truth when both agree."""
    if survey_m is None and tiles_m is None:
        return "unread", None
    if tiles_m is None:
        return "survey_only", survey_m
    if survey_m is None:
        return "tiles_only", tiles_m
    tol = max(AGREE_ABS_M, AGREE_REL * max(survey_m, tiles_m))
    if abs(survey_m - tiles_m) <= tol:
        return "confirmed", survey_m
    return "disputed", None


def footprint_truth(
    footprints: Iterable[tuple[str, object]],
    bbox_nsew,
    *,
    survey_fetch: NdsmFetch | None,
    tiles_fetch: NdsmFetch | None,
    tile_m: float = TILE_M,
) -> list[dict]:
    """Truth rows for ``(key, lon/lat polygon)`` footprints inside ``bbox_nsew``.

    Each source is fetched once per tile that holds a footprint centroid; a
    fetch returning None (not covered) leaves that source empty for the tile.
    """
    items = []
    for key, poly in footprints:
        if poly is None or area_m2(poly) < MIN_AREA_M2:
            continue
        c = poly.centroid
        items.append((key, poly, c))
    rows: list[dict] = []
    for core in tile_bboxes(bbox_nsew, tile_m):
        n, s, e, w = core
        here = [(k, p, c) for k, p, c in items
                if s <= c.y < n and w <= c.x < e]
        if not here:
            continue
        fetch_box = _expand(core, TILE_MARGIN_M)
        got = {name: (f(fetch_box) if f is not None else None)
               for name, f in (("survey", survey_fetch), ("tiles", tiles_fetch))}
        for key, poly, c in here:
            inner = inward_buffer(poly)
            vals = {}
            cells = {}
            for name, res in got.items():
                if res is None:
                    vals[name], cells[name] = None, 0
                else:
                    vals[name], cells[name] = sample_p95(res[0], res[1], inner)
            status, truth = cross_check(vals["survey"], vals["tiles"])
            rows.append({
                "key": key,
                "centroid": [round(c.x, 6), round(c.y, 6)],
                "footprint_lonlat": _ring(poly),
                "survey_m": _r(vals["survey"]), "survey_cells": cells["survey"],
                "tiles_m": _r(vals["tiles"]), "tiles_cells": cells["tiles"],
                "status": status, "truth_m": _r(truth),
            })
    return rows


def _ring(poly) -> list[list[float]]:
    """Exterior ring of ``poly`` (the largest part of a MultiPolygon), rounded to ~10 cm."""
    if poly.geom_type != "Polygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return [[round(x, 6), round(y, 6)] for x, y in poly.exterior.coords]


def _r(v: float | None) -> float | None:
    return None if v is None else round(float(v), 2)


def survey_fetcher(provider: str) -> NdsmFetch:
    """A tile fetch over ``survey.ndsm_for_bbox``; endpoint failures become None (logged)."""
    from city2stl.height.providers import survey

    def fetch(bbox):
        try:
            return survey.ndsm_for_bbox(provider, bbox)
        except survey.SurveyError as exc:
            logger.warning("[benchmark] %s failed on %s: %s", provider, bbox, exc)
            return None
    return fetch


def tiles_fetcher(api_key: str | None = None) -> NdsmFetch | None:
    """A tile fetch over Google 3D Tiles at ~1 m; None when no API key is configured."""
    from city2stl.height.providers._survey import lonlat_grid
    from city2stl.height.providers.google_3d import Google3DProvider
    provider = Google3DProvider(api_key=api_key)
    if not provider.covers(None):
        return None

    def fetch(bbox):
        h, w, transform = lonlat_grid(bbox, TILES_RESOLUTION_M)
        res = provider.fetch_heights(tuple(bbox), (h, w))
        if res.raster is None or not np.isfinite(res.raster).any():
            return None
        return res.raster, transform
    return fetch


def truth_path(region: str) -> Path:
    return RUNS_DIR / f"{region}_truth.json"


def build_truth_doc(region: str, bbox_nsew, footprints, *, provider: str,
                    survey_fetch: NdsmFetch | None, tiles_fetch: NdsmFetch | None) -> dict:
    """The cached truth document for one region (rows + sources + counts)."""
    from city2stl.height.providers import survey
    rows = footprint_truth(footprints, bbox_nsew,
                           survey_fetch=survey_fetch, tiles_fetch=tiles_fetch)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    p = survey.PROVIDERS.get(provider)
    return {
        "region": region,
        "bbox_nsew": [float(v) for v in bbox_nsew],
        "created": date.today().isoformat(),
        "sources": {
            "survey": {"name": provider, "label": getattr(p, "label", provider),
                       "used": survey_fetch is not None},
            "tiles": {"name": "google3d", "used": tiles_fetch is not None},
        },
        "params": {"inward_buffer_m": INWARD_BUFFER_M, "percentile": TRUTH_PERCENTILE,
                   "agree_abs_m": AGREE_ABS_M, "agree_rel": AGREE_REL,
                   "min_area_m2": MIN_AREA_M2},
        "counts": counts,
        "rows": rows,
    }


def load_truth(region: str) -> dict | None:
    path = truth_path(region)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_truth(doc: dict) -> Path:
    path = truth_path(doc["region"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# ── scorer ──────────────────────────────────────────────────────────

def match_footprints(run_rows: list[dict], truth_rows: list[dict],
                     min_iou: float = MIN_IOU) -> list[tuple[dict, dict, float]]:
    """(run row, truth row, IoU) for every run row whose footprint overlaps a truth one."""
    import shapely
    truth_polys, truth_kept = [], []
    for t in truth_rows:
        p = polygon_from_ring(t.get("footprint_lonlat"))
        if p is not None:
            truth_polys.append(p)
            truth_kept.append(t)
    if not truth_polys:
        return []
    tree = shapely.STRtree(truth_polys)
    out = []
    for r in run_rows:
        p = polygon_from_ring(r.get("footprint_lonlat"))
        if p is None:
            continue
        best, best_iou = None, 0.0
        for k in tree.query(p):
            q = truth_polys[k]
            union = p.union(q).area
            iou = p.intersection(q).area / union if union > 0 else 0.0
            if iou > best_iou:
                best, best_iou = truth_kept[k], iou
        if best is not None and best_iou >= min_iou:
            out.append((r, best, best_iou))
    return out


def metrics(estimates: Iterable[float], truths: Iterable[float]) -> dict:
    """MAE, median AE, bias, % within 15 / 25 % (of truth), n."""
    est = np.asarray(list(estimates), dtype=np.float64)
    tru = np.asarray(list(truths), dtype=np.float64)
    if est.size == 0:
        return {"n": 0}
    err = est - tru
    rel = np.abs(err) / np.maximum(tru, 1e-6)
    return {
        "n": int(est.size),
        "mae_m": round(float(np.abs(err).mean()), 2),
        "median_ae_m": round(float(np.median(np.abs(err))), 2),
        "bias_m": round(float(err.mean()), 2),
        "within_15_pct": round(100.0 * float((rel <= 0.15).mean()), 1),
        "within_25_pct": round(100.0 * float((rel <= 0.25).mean()), 1),
    }


def band_of(height_m: float) -> str:
    for label, lo, hi in BANDS:
        if lo <= height_m < hi:
            return label
    return BANDS[-1][0]


def score_region(heights_doc: dict, truth_doc: dict, *,
                 statuses: tuple[str, ...] = ("confirmed",)) -> dict:
    """Score one run's ``heights.json`` against a truth document.

    ``statuses`` picks which truth rows count (default confirmed only; add
    ``survey_only`` / ``tiles_only`` when one source is missing for a region).
    """
    truth_rows = [t for t in truth_doc.get("rows", [])
                  if t.get("status") in statuses and t.get("truth_m") is not None]
    run_rows = [b for b in heights_doc.get("buildings", [])
                if b.get("effective_height_m") is not None]
    pairs = match_footprints(run_rows, truth_rows)

    scored = [{
        "feature_id": r.get("feature_id"), "name": r.get("name"),
        "estimate_m": float(r["effective_height_m"]), "truth_m": float(t["truth_m"]),
        "survey_m": t.get("survey_m"), "tiles_m": t.get("tiles_m"),
        "tag_m": r.get("height_tag_m"), "tag_source": r.get("height_source"),
        "n_views": int(r.get("n_views") or 0), "n_seeds": int(r.get("n_seeds") or 0),
        "iou": round(iou, 3),
    } for r, t, iou in pairs]

    def m(rows):
        return metrics([x["estimate_m"] for x in rows], [x["truth_m"] for x in rows])

    by_band, coverage = {}, {}
    for label, _lo, _hi in BANDS:
        in_band = [x for x in scored if band_of(x["truth_m"]) == label]
        by_band[label] = m(in_band)
        n_truth = sum(1 for t in truth_rows if band_of(t["truth_m"]) == label)
        coverage[label] = {"scored": len(in_band), "truth": n_truth,
                           "pct": round(100.0 * len(in_band) / n_truth, 1) if n_truth else None}

    tagged = [x for x in scored if x["tag_m"] is not None
              and x["tag_source"] in ("osm_tag", "osm_levels")]
    counts = truth_doc.get("counts", {})
    return {
        "region": truth_doc.get("region") or heights_doc.get("region"),
        "statuses": list(statuses),
        "truth_counts": counts,
        "n_run_rows": len(run_rows),
        "n_truth": len(truth_rows),
        "overall": m(scored),
        "by_band": by_band,
        "coverage": coverage,
        "by_views": {"1": m([x for x in scored if x["n_views"] <= 1]),
                     "2+": m([x for x in scored if x["n_views"] >= 2])},
        "by_seeds": {"1": m([x for x in scored if x["n_seeds"] <= 1]),
                     "2+": m([x for x in scored if x["n_seeds"] >= 2])},
        "osm_tags": {
            "tag_vs_truth": metrics([x["tag_m"] for x in tagged], [x["truth_m"] for x in tagged]),
            "estimate_vs_truth": m(tagged),
        },
        "worst": sorted(scored, key=lambda x: -abs(x["estimate_m"] - x["truth_m"]))[:15],
    }


def format_table(results: list[dict]) -> str:
    """One line per region plus the band breakdown, for the terminal and STATUS.md."""
    head = (f"{'region':<22}{'truth':>6}{'disp':>6}{'n':>5}{'MAE':>8}{'medAE':>8}"
            f"{'bias':>8}{'<=15%':>7}{'<=25%':>7}  " + "  ".join(f"{b[0]:>13}" for b in BANDS))
    lines = [head, "-" * len(head)]
    for r in results:
        o = r["overall"]
        disp = r["truth_counts"].get("disputed", 0)
        if not o.get("n"):
            lines.append(f"{r['region']:<22}{r['n_truth']:>6}{disp:>6}{0:>5}  (nothing scored)")
            continue
        bands = "  ".join(
            f"{_band_cell(r['by_band'][b[0]], r['coverage'][b[0]]):>13}" for b in BANDS)
        lines.append(f"{r['region']:<22}{r['n_truth']:>6}{disp:>6}{o['n']:>5}"
                     f"{o['mae_m']:>8.1f}{o['median_ae_m']:>8.1f}{o['bias_m']:>+8.1f}"
                     f"{o['within_15_pct']:>7.0f}{o['within_25_pct']:>7.0f}  {bands}")
    lines.append("bands: bias m (scored/truth)")
    return "\n".join(lines)


def _band_cell(mm: dict, cov: dict) -> str:
    if not mm.get("n"):
        return f"- (0/{cov['truth']})"
    return f"{mm['bias_m']:+.0f} ({cov['scored']}/{cov['truth']})"
