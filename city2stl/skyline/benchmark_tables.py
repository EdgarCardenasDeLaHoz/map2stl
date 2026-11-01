"""Part of ``city2stl.skyline.benchmark`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from geo2stl.cache import (  # noqa: F401 - file_lock re-exported (bm.file_lock)
    atomic_write_json,
    file_lock,
)
from geo2stl.geo import M_PER_DEG_SPHERE as M_PER_DEG_LAT

from . import benchmark_geometry as _geo
from .benchmark_geometry import (
    DISTANCE_BANDS,
    EUBUCCO_BANDS,
    METHOD_STAT,
    REVIEW_BANDS,
    ROOF_PERCENTILE,
    TOL_ABS_M,
    TOL_REL,
    _polygon,
)
from .benchmark_scoring import (
    CORROBORATED_TIERS,
    DISTANCE_LABELS,
    EUBUCCO_LABELS,
    READING_TIERS,
    REVIEW_LABELS,
    SINGLE_SOURCE,
    TWO_SOURCE,
    attach_roof_stats,
    band_errors,
    band_of,
    matched_truth,
    scoring_truth,
    tol_m,
    truth_mode,
)
from .benchmark_truth import TEMPORAL_UNKNOWN


def report_seed_positions(report_dir) -> dict[str, tuple[float, float]]:
    """``seed name -> (lat, lon)`` where the run placed each seed, from the report's seed pages
    (``seed_<slug>.html``, row "Lat / Lon"; ``html_report`` drops a leading ``seed_`` from the
    name, so slug ``4`` is seed ``seed_4``). An auto seed that failed screening is moved by the
    run (San Juan 2026-10-09: ``auto_180_2000m`` by 3 km), so this beats the proposal file."""
    import re

    out: dict[str, tuple[float, float]] = {}
    for f in Path(report_dir).glob("seed_*.html"):
        m = re.search(r"Lat / Lon</th><td>(-?[\d.]+),\s*(-?[\d.]+)</td>",
                      f.read_text(encoding="utf-8", errors="replace"))
        if m:
            slug = f.stem[len("seed_"):]
            out[f"seed_{slug}" if slug.isdigit() else slug] = (float(m[1]), float(m[2]))
    return out


def seed_positions(region: str, report_dir=None) -> dict[str, tuple[float, float]]:
    """``seed name -> (lat, lon)`` for the region's seeds as a run names them: where the run
    placed them (``report_seed_positions``, given ``report_dir``), else ``seed_<i>`` from the
    site's ``seed_urls`` (1-based, as ``region_pdf`` numbers them) and the persisted
    auto-proposals (``runs/auto_proposals/<region>.json``)."""
    from .streetview_io import _parse_streetview_url

    out: dict[str, tuple[float, float]] = {}
    site = Path(__file__).parent / "sites" / f"{region.lower()}.json"
    if site.exists():
        cfg = json.loads(site.read_text(encoding="utf-8-sig"))
        for i, url in enumerate(cfg.get("seed_urls") or []):
            got = _parse_streetview_url(str(url))
            if got is not None:
                out[f"seed_{i + 1}"] = (float(got[0]), float(got[1]))
    auto = _geo.BENCHMARK_ROOT.parent / "auto_proposals" / f"{region.lower()}.json"
    if auto.exists():
        d = json.loads(auto.read_text(encoding="utf-8"))
        for p in (d.get("points") if isinstance(d, dict) else d) or []:
            if p.get("name") and p.get("lat") is not None:
                out.setdefault(p["name"], (float(p["lat"]), float(p["lon"])))
    if report_dir is not None:
        out.update(report_seed_positions(report_dir))
    return out


def _dist_m(a: tuple[float, float], lat: float, lon: float) -> float:
    return math.hypot((a[0] - lat) * M_PER_DEG_LAT,
                      (a[1] - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat)))


def method_readings(b: dict, elevated=(), seeds: dict | None = None) -> list[dict]:
    """Every reading a report row carries, ``{method, value_m, dist_m}``: ``drone`` (an
    elevated seed's median) and ``street`` (another seed's; ``photo`` for web / Commons seeds)
    with the camera's distance to the building (None when the seed's position is unknown),
    ``floors`` (count x storey), the satellite readings by kind, the OSM tag (``osm_tag`` /
    ``osm_levels``) and ``prior`` on rows that publish it. ``dist_m`` is None for methods
    without a camera position."""
    from ._core.height import TAGGED_SOURCES

    seeds = seeds or {}
    lat, lon = b.get("centroid_lat"), b.get("centroid_lon")
    out = []
    for name, v in (b.get("per_seed_median_m") or {}).items():
        if v is None:
            continue
        kind = ("drone" if name in elevated else
                "photo" if name.startswith(("commons_", "web_", "flickr_")) else "street")
        pos = seeds.get(name)
        d = _dist_m(pos, lat, lon) if pos and lat is not None else None
        out.append({"method": kind, "value_m": float(v), "dist_m": d})
    if b.get("floors") and b.get("storey_m"):
        out.append({"method": "floors", "value_m": float(b["floors"]) * float(b["storey_m"]),
                    "dist_m": None})
    for kind, val in (b.get("satellite") or {}).items():
        h = val[0] if isinstance(val, (list, tuple)) else val
        if h is not None:
            out.append({"method": str(kind), "value_m": float(h), "dist_m": None})
    if b.get("height_tag_m") is not None and b.get("height_source") in TAGGED_SOURCES:
        out.append({"method": b["height_source"], "value_m": float(b["height_tag_m"]),
                    "dist_m": None})
    src = b.get("no_survey_source") or b.get("effective_height_source") or ""
    pub = b.get("no_survey_height_m", b.get("effective_height_m"))
    if pub is not None and (src.startswith("withheld:prior") or src in ("prior_gbm", "default")):
        out.append({"method": "prior", "value_m": float(pub), "dist_m": None})
    return out


def _pred_tier(b: dict) -> tuple[float | None, str]:
    """The survey-blind published height and its tier (what the headline scores)."""
    pred = b.get("no_survey_height_m", b.get("effective_height_m"))
    from ._core.tiers import canonical_tier

    return (None if pred is None else float(pred),
            canonical_tier(b.get("no_survey_tier") or b.get("tier")) or "unlabelled")


def band_tier_table(buildings: list[dict], truth: dict[str, dict], bands=REVIEW_BANDS,
                    labels=REVIEW_LABELS) -> dict:
    """``{band: {tier: band_errors, "all": band_errors}}`` (plus band ``"all"``) of the
    survey-blind published height against ``truth`` (the records ``scoring_truth`` keeps),
    banded by truth."""
    cells: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for b in buildings:
        t = truth.get(b["key"])
        pred, tier = _pred_tier(b)
        if t is None or pred is None:
            continue
        band = band_of(float(t["truth_m"]), bands, labels)
        for bk in (band, "all"):
            for tk in (tier, "all"):
                cells.setdefault((bk, tk), []).append((pred, float(t["truth_m"])))
    out: dict[str, dict] = {bk: {} for bk in (*labels, "all")}
    for (bk, tk), pairs in cells.items():
        p, tr = zip(*pairs, strict=True)
        out[bk][tk] = band_errors(p, tr)
    return out


def band_method_table(buildings: list[dict], truth: dict[str, dict], elevated=(),
                      seeds: dict | None = None, bands=REVIEW_BANDS,
                      labels=REVIEW_LABELS) -> dict:
    """``{band: {method: {distance band: errors}}}`` of every reading (``method_readings``)
    against the headline truth (p95), with the method-matched truth (``METHOD_STAT``: max for
    silhouettes, p70 for floors) beside it as ``matched`` where the stats are stored. Every
    method has distance band ``"all"``; readings with a camera position (drone, street) are
    also split by its distance to the building (``DISTANCE_BANDS``)."""
    cells: dict[tuple[str, str, str], list[tuple[float, float, float | None]]] = {}
    for b in buildings:
        t = truth.get(b["key"])
        if t is None:
            continue
        band = band_of(float(t["truth_m"]), bands, labels)
        for r in method_readings(b, elevated, seeds):
            row = (r["value_m"], float(t["truth_m"]),
                   matched_truth(t, METHOD_STAT.get(r["method"], "p95")))
            dists = ("all",) if r["dist_m"] is None else (
                band_of(r["dist_m"], DISTANCE_BANDS, DISTANCE_LABELS), "all")
            for bk in (band, "all"):
                for dk in dists:
                    cells.setdefault((bk, r["method"], dk), []).append(row)
    out: dict[str, dict] = {}
    for (bk, m, dk), rows in sorted(cells.items()):
        e = band_errors([r[0] for r in rows], [r[1] for r in rows])
        mt = [(r[0], r[2]) for r in rows if r[2] is not None]
        if mt:
            me = band_errors([p for p, _ in mt], [t for _, t in mt])
            e["matched"] = {"stat": METHOD_STAT.get(m, "p95"), "n": me["n"],
                            "mae_m": me["mae_m"], "bias_m": me["bias_m"], "tol": me["tol"]}
        out.setdefault(bk, {}).setdefault(m, {})[dk] = e
    return out


def pipeline_metrics(buildings: list[dict], truth: dict[str, dict],
                     n_footprints: int | None = None) -> dict:
    """The review's pipeline metrics (§4.4).

    - ``coverage``: share of the report's rows publishing a non-prior value (survey-blind tier
      neither ``prior`` nor unlabelled), over all rows, over the rows with truth and, given
      ``n_footprints`` (``heights.json`` ``n_building_records``), over every footprint;
    - ``false_corroborated``: corroborated rows (``CORROBORATED_TIERS``) off by more than tol;
    - ``withhold``: readings the pipeline held back (a withheld single, ``single_reading_m``;
      a Street View value on a row publishing ``withheld:*``) among all image readings
      (those plus the published ones, tiers ``READING_TIERS``). Precision: share of withheld
      readings that were wrong (off by more than tol). Recall: share of wrong readings that
      were withheld. ``single`` / ``street`` restrict the withheld side to one kind.
    """
    def covered(b):
        return _pred_tier(b)[1] not in ("prior", "unlabelled")

    with_truth = [b for b in buildings if b["key"] in truth]
    n_cov, n_cov_t = sum(map(covered, buildings)), sum(map(covered, with_truth))
    out = {"coverage": {"rows": len(buildings), "non_prior": n_cov,
                        "share": round(n_cov / len(buildings), 3) if buildings else None,
                        "rows_with_truth": len(with_truth), "non_prior_with_truth": n_cov_t,
                        "share_with_truth": (round(n_cov_t / len(with_truth), 3)
                                             if with_truth else None),
                        "footprints": n_footprints,
                        "share_of_footprints": (round(n_cov / n_footprints, 3)
                                                if n_footprints else None)}}
    corr, readings = [], []
    for b in with_truth:
        tm = float(truth[b["key"]]["truth_m"])
        tol = float(tol_m(tm))
        pred, tier = _pred_tier(b)
        if tier in CORROBORATED_TIERS and pred is not None:
            corr.append(abs(pred - tm) > tol)
        if tier in READING_TIERS and pred is not None:
            readings.append(("published", tier, abs(pred - tm) > tol))
        src = b.get("no_survey_source") or b.get("effective_height_source") or ""
        if b.get("single_reading_m") is not None:
            readings.append(("withheld", "single", abs(float(b["single_reading_m"]) - tm) > tol))
        if src.startswith("withheld:") and b.get("street_view_m") is not None:
            readings.append(("withheld", "street", abs(float(b["street_view_m"]) - tm) > tol))
    out["false_corroborated"] = {"n": len(corr), "wrong": int(sum(corr)),
                                 "rate": round(sum(corr) / len(corr), 3) if corr else None}

    def pr(rows):
        held = [w for s, _, w in rows if s == "withheld"]
        wrong = [s == "withheld" for s, _, w in rows if w]
        return {"readings": len(rows), "withheld": len(held), "withheld_wrong": int(sum(held)),
                "precision": round(sum(held) / len(held), 3) if held else None,
                "wrong": len(wrong),
                "recall": round(sum(wrong) / len(wrong), 3) if wrong else None}

    out["withhold"] = {"all": pr(readings),
                       **{k: pr([r for r in readings if r[0] == "published" or r[1] == k])
                          for k in ("single", "street")}}
    return out


def bench_tables(buildings: list[dict], truth: dict[str, dict], *, elevated=(),
                 seeds: dict | None = None, survey_cache: dict | None = None,
                 n_footprints: int | None = None,
                 nested: set[str] | frozenset = frozenset()) -> dict:
    """Every item-1 table for one report: the truth mode, band x tier (review and EUBUCCO
    bands), band x method x distance, the pipeline metrics and the truth age counts.
    ``truth``: ``footprint_truth``'s records for the report's footprints; ``survey_cache``:
    ``survey_heights.load_cache(region)``, for the stored roof statistics; ``nested``:
    ``nested_keys`` of the report's published rows, left out (counted in ``excluded``)."""
    mode = truth_mode(buildings, truth)
    keys = {b["key"] for b in buildings}
    scored, excluded = scoring_truth({k: r for k, r in truth.items() if k in keys}, mode,
                                     nested)
    if survey_cache:
        scored = attach_roof_stats(scored, survey_cache)
    keys = {b["key"] for b in buildings}
    ages: dict[str, int] = {}
    for k, r in truth.items():
        if k in keys and r.get("status") in ("confirmed", "survey_only"):
            a = r.get("temporal") or TEMPORAL_UNKNOWN
            ages[a] = ages.get(a, 0) + 1
    return {
        "truth_mode": mode,
        "truth_status": {TWO_SOURCE: "confirmed", SINGLE_SOURCE: "survey_only"}.get(mode),
        "truth_stat": f"p{ROOF_PERCENTILE:.0f}",
        "tol_rule": {"rel": TOL_REL, "abs_m": TOL_ABS_M},
        "n_scored_truth": sum(1 for b in buildings if b["key"] in scored),
        "excluded": excluded,
        "truth_age": ages,
        "with_roof_stats": sum(1 for b in buildings
                               if (scored.get(b["key"]) or {}).get("roof_m")
                               or (scored.get(b["key"]) or {}).get("survey_roof_m")),
        "band_tier": band_tier_table(buildings, scored),
        "band_tier_eubucco": band_tier_table(buildings, scored, EUBUCCO_BANDS, EUBUCCO_LABELS),
        "band_method": band_method_table(buildings, scored, elevated, seeds),
        "pipeline": pipeline_metrics(buildings, scored, n_footprints),
    }


# --------------------------------------------------------------------------- published heights

#: Words that name a kind of building, not a building: ignored when matching names.
_GENERIC_WORDS = {"hotel", "edificio", "torre", "tower", "building", "centro", "comercial",
                  "cartagena", "indias", "residencial", "apartamentos", "condominio"}


def _name_words(name: str | None) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower())
            if len(w) >= 4 and w not in _GENERIC_WORDS}


def match_known_tower(lat: float, lon: float, height_m: float, name: str, rows: list[dict],
                      radius_m: float = 60.0) -> tuple[dict | None, str]:
    """The heights.json row that a published height belongs to, and why.

    The nearest centroid is not enough: a hotel's convention-centre podium is mapped around
    its tower, and its centroid can be the nearer one (2026-10-06, Cartagena: the Estelar
    podium, read 57 m, scored against the tower's 202 m) -- and the podium can carry the
    hotel's name while the tower is unnamed. Among rows within ``radius_m``: a named building
    that is not a podium first, then an OSM tag within 35 % of the published height, then rows
    that do not contain another candidate (a podium), then a name, then distance.
    """
    from shapely.geometry import Point

    cands, rejected = [], 0
    for r in rows:
        d = math.hypot((r["centroid_lat"] - lat) * M_PER_DEG_LAT,
                       (r["centroid_lon"] - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat)))
        if d > radius_m:
            continue
        tag = r.get("height_tag_m")
        if tag and not 0.5 <= tag / height_m <= 2.0:
            rejected += 1                       # mapped with another height: not this tower
            continue                            # (Miami: Panorama Tower 249 m -> a 16 m row)
        cands.append((d, r))
    if not cands:
        return None, (f"no plausible match ({rejected} tagged far off)" if rejected
                      else "none within radius")
    polys = {id(r): _polygon(r["footprint_lonlat"]) if r.get("footprint_lonlat") else None
             for _, r in cands}
    words = _name_words(name)

    def rank(c):
        d, r = c
        named = bool(words & _name_words(r.get("name")))
        tag = r.get("height_tag_m")
        tag_ok = bool(tag) and 1 / 1.35 <= tag / height_m <= 1.35
        p = polys[id(r)]
        podium = p is not None and any(
            q is not r and p.contains(Point(q["centroid_lon"], q["centroid_lat"]))
            for _, q in cands)
        # a building named like the tower and not a podium first: tags can be wrong in a way
        # that matches a neighbour (Cartagena: Ravello tagged 160, Nautica's published height,
        # so Nautica was scored on Ravello's footprint, 2026-10-06)
        return (not (named and not podium), not tag_ok, podium, not named, d)

    best = min(cands, key=rank)
    named_tower, no_tag, podium, no_name, _d = rank(best)
    why = ("name" if not named_tower else "OSM tag" if not no_tag else "name" if not no_name else
           "nearest (not a podium)" if not podium else "nearest")
    return best[1], why


def score_known(heights_json: Path, region: str, elevated_seeds: tuple[str, ...] = ()) -> dict:
    """The report's heights against the site's published heights (``sites/<region>.json``
    ``known_heights_m``), each matched by :func:`match_known_tower`. Rows: published, the
    report's height and source, Street View, and the drone seeds' median."""
    site = Path(__file__).parent / "sites" / f"{region.lower()}.json"
    if not site.exists():
        return {}
    cfg = json.loads(site.read_text(encoding="utf-8-sig"))
    known = {k: v for k, v in (cfg.get("known_heights_m") or {}).items()
             if not k.startswith("_") and isinstance(v, dict)}
    if not known:
        return {}
    elevated = tuple(elevated_seeds) or tuple(cfg.get("elevated_seeds") or ())
    rows = [b for b in json.loads(Path(heights_json).read_text(encoding="utf-8"))["buildings"]
            if b.get("centroid_lat") is not None]
    out = []
    for name, v in known.items():
        r, why = match_known_tower(float(v["lat"]), float(v["lon"]), float(v["height_m"]), name, rows)
        row = {"name": name, "published_m": float(v["height_m"]), "match": why}
        if r is not None:
            drone = [h for s, h in (r.get("per_seed_median_m") or {}).items() if s in elevated]
            row.update(feature_id=r.get("feature_id"), osm_name=r.get("name"),
                       tag_m=r.get("height_tag_m"), report_m=r.get("effective_height_m"),
                       report_source=r.get("effective_height_source"),
                       street_view_m=r.get("street_view_m"),
                       drone_m=float(np.median(drone)) if drone else None)
        out.append(row)

    def mae(key):
        e = [abs(x[key] - x["published_m"]) for x in out if x.get(key) is not None]
        return {"n": len(e), "mae_m": float(np.mean(e)) if e else None,
                "median_ae_m": float(np.median(e)) if e else None}

    return {"towers": out, "report": mae("report_m"), "drone": mae("drone_m"),
            "street_view": mae("street_view_m"), "osm_tag": mae("tag_m")}
