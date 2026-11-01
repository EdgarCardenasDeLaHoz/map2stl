"""Part of ``city2stl.skyline.benchmark`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import math

import numpy as np

from geo2stl.cache import (  # noqa: F401 - file_lock re-exported (bm.file_lock)
    atomic_write_json,
    file_lock,
)

from .benchmark_geometry import (
    AGREE_ABS_M,
    AGREE_REL,
    BANDS,
    DISTANCE_BANDS,
    EUBUCCO_BANDS,
    REVIEW_BANDS,
    TOL_ABS_M,
    TOL_REL,
    _polygon,
)
from .benchmark_truth import TEMPORAL_MAY_POSTDATE


def _pairs(pred: np.ndarray, truth: np.ndarray) -> tuple[int, int]:
    """``(correctly ordered, compared)`` building pairs.

    Pairs whose true heights are within the truth tolerance (max(AGREE_ABS_M, AGREE_REL x the
    taller)) are skipped: the survey cannot say which is taller.
    """
    if pred.size < 2:
        return 0, 0
    i, j = np.triu_indices(pred.size, 1)
    dt = truth[i] - truth[j]
    keep = np.abs(dt) > np.maximum(AGREE_ABS_M, AGREE_REL * np.maximum(truth[i], truth[j]))
    good = np.sign(pred[i] - pred[j])[keep] == np.sign(dt[keep])
    return int(good.sum()), int(keep.sum())


def relative_metrics(pred: np.ndarray, truth: np.ndarray) -> dict:
    """Scale-free agreement: pair order (share of building pairs in the right order) and
    Spearman rank correlation. Unchanged by any camera-height offset or overall scale, which is
    why F-WEB2 photos (unknown camera height) are judged on them."""
    from scipy.stats import spearmanr

    ok, n = _pairs(pred, truth)
    rho = float(spearmanr(pred, truth).statistic) if pred.size >= 3 else None
    return {"n": int(pred.size), "pairs": n,
            "pair_order": round(ok / n, 3) if n else None,
            "spearman": None if rho is None or math.isnan(rho) else round(rho, 3)}


def per_view_relative(buildings: list[dict], truth: dict[str, dict], min_buildings: int = 5,
                      statuses: tuple[str, ...] = ("confirmed",)) -> dict:
    """Relative metrics inside each single view (one image, one camera), pooled.

    ``pair_order`` pools pairs over all views with at least ``min_buildings`` confirmed
    buildings (``statuses``: the truth statuses scored); ``median_spearman`` is the median over
    those views.
    """
    by_view: dict[str, list[tuple[float, float]]] = {}
    for b in buildings:
        t = truth.get(b["key"])
        if not t or t["status"] not in statuses:
            continue
        for v in b.get("views") or []:
            if v.get("height_m") is not None:
                by_view.setdefault(v["view_name"], []).append((float(v["height_m"]), t["truth_m"]))
    ok = n = 0
    rhos = []
    used = 0
    for rows in by_view.values():
        if len(rows) < min_buildings:
            continue
        p, t = (np.array(c) for c in zip(*rows, strict=True))
        k, m = _pairs(p, t)
        ok, n, used = ok + k, n + m, used + 1
        r = relative_metrics(p, t)["spearman"]
        if r is not None:
            rhos.append(r)
    return {"views": used, "pairs": n, "pair_order": round(ok / n, 3) if n else None,
            "median_spearman": round(float(np.median(rhos)), 3) if rhos else None}


def _errors(pred: np.ndarray, truth: np.ndarray) -> dict:
    if pred.size == 0:
        return {"n": 0}
    err = pred - truth
    rel = np.abs(err) / np.maximum(truth, 1.0)
    return {
        "n": int(pred.size),
        "mae_m": round(float(np.mean(np.abs(err))), 2),
        "median_ae_m": round(float(np.median(np.abs(err))), 2),
        "bias_m": round(float(np.mean(err)), 2),
        "within_3m": round(float(np.mean(np.abs(err) <= 3.0)), 3),
        "within_15pct": round(float(np.mean(rel <= 0.15)), 3),
        "within_25pct": round(float(np.mean(rel <= 0.25)), 3),
    }


def street_view_buildings(buildings: list[dict]) -> list[dict] | None:
    """The run's buildings with the Street View height put back where T28 withheld it
    (``street_view_m``), so the benchmark can score both; None when nothing was withheld."""
    if not any("street_view_m" in b for b in buildings):
        return None
    return [{**b, "effective_height_m": b["street_view_m"]} if "street_view_m" in b else b
            for b in buildings]


def withheld_buildings(buildings: list[dict], fallback: str = "constant",
                       region: str | None = None) -> list[dict]:
    """A pre-T28 report's buildings as T28 would publish them: untagged ones (height
    source not ``osm_tag`` / ``osm_levels``) get the fallback, the Street View value moved
    to ``street_view_m``. Lets ``--score-only`` show the effect on old runs.

    ``fallback``: ``"constant"`` (``UNTAGGED_FALLBACK_M``) or ``"model"`` (the T41 height
    prior, trained without ``region`` so the score stays out-of-sample; neighbours are
    the report's buildings, as at run time)."""
    from ._core.height import TAGGED_SOURCES, UNTAGGED_FALLBACK_M

    heights = [UNTAGGED_FALLBACK_M] * len(buildings)
    src = "default"
    if fallback == "model":
        from .untagged_prior import predict, report_rows

        heights = [float(h) for h in predict(report_rows(buildings), exclude_city=region)]
        src = "prior_gbm"
    out = []
    for b, h in zip(buildings, heights, strict=True):
        if b.get("height_source") in TAGGED_SOURCES or "street_view_m" in b:
            out.append(b)
        else:
            out.append({**b, "street_view_m": b.get("effective_height_m"),
                        "effective_height_m": h, "effective_height_source": f"withheld:{src}"})
    return out


def score_buildings(buildings: list[dict], truth: dict[str, dict],
                    pred_field: str = "effective_height_m",
                    statuses: tuple[str, ...] = ("confirmed",)) -> dict:
    """Score a run's buildings against ``truth`` (confirmed buildings only; ``statuses``
    widens that, e.g. ``("survey_only",)`` for a single-source region, ``score_report``).

    Returns overall errors, errors per truth-height band, per view count and per seed
    count, the status counts, and the OSM-tag yardstick (how far the tags themselves
    sit from confirmed truth, and the run scored against tags).

    ``pred_field``: the height scored. The ``10_benchmark`` headline scores
    ``no_survey_height_m``, the survey-blind answer (F-SKY26 2c): a survey height is
    measured the same way as the truth, so scoring it would be circular. A row without
    ``pred_field`` (a report from before 2c) is scored on ``effective_height_m``, which was
    survey-blind then.
    """
    rows = []
    status_counts: dict[str, int] = {}
    for b in buildings:
        t = truth.get(b["key"])
        status = t["status"] if t else "unmeasured"
        status_counts[status] = status_counts.get(status, 0) + 1
        pred = b.get(pred_field, b.get("effective_height_m"))
        if status in statuses and pred is not None and t["truth_m"] is not None:
            rows.append((float(pred), float(t["truth_m"]), int(b.get("n_views") or 0),
                         int(b.get("n_seeds") or 0), b.get("height_tag_m"),
                         b.get("height_source")))
    if not rows:
        return {"n_buildings": len(buildings), "status": status_counts, "overall": {"n": 0}}
    pred = np.array([r[0] for r in rows])
    tru = np.array([r[1] for r in rows])
    nv = np.array([r[2] for r in rows])
    tg_mask = np.array([r[4] is not None and r[5] not in (None, "default") for r in rows])
    ns = np.array([r[3] for r in rows])
    out = {
        "n_buildings": len(buildings),
        "status": status_counts,
        "overall": _errors(pred, tru),
        "bands": {(f"{lo:.0f}+m" if math.isinf(hi) else f"{lo:.0f}-{hi:.0f}m"):
                  _errors(pred[(tru >= lo) & (tru < hi)], tru[(tru >= lo) & (tru < hi)])
                  for lo, hi in BANDS},
        "views": {lab: _errors(pred[m], tru[m]) for lab, m in
                  (("1", nv == 1), ("2-3", (nv >= 2) & (nv <= 3)), ("4+", nv >= 4))},
        "seeds": {lab: _errors(pred[m], tru[m]) for lab, m in
                  (("1", ns <= 1), ("2+", ns >= 2))},
        # SKYLINE_TAG_FILTER (on by default) drops per-view estimates far from a building's
        # OSM height tag, so tagged buildings are helped by their tag; untagged ones show
        # the pipeline unaided.
        "osm_tag": {lab: _errors(pred[m], tru[m]) for lab, m in
                    (("tagged", tg_mask), ("untagged", ~tg_mask))},
        # Scale-free: does the taller building come out taller? City-wide and inside each view.
        "relative": {"city": relative_metrics(pred, tru),
                     "per_view": per_view_relative(buildings, truth, statuses=statuses)},
    }
    tagged = [(r[0], r[1], float(r[4])) for r in rows
              if r[4] is not None and r[5] not in (None, "default")]
    if tagged:
        tp, tt, tg = (np.array(c) for c in zip(*tagged, strict=True))
        out["osm_tags"] = {"tag_vs_truth": _errors(tg, tt), "run_vs_tag": _errors(tp, tg),
                           "run_vs_truth_same_buildings": _errors(tp, tt)}
    return out


def label_tiers(buildings: list[dict], measured_seeds=()) -> list[dict]:
    """``buildings`` with a verification tier on rows that lack one (a report from before
    F-SKY26 2a), worked out from the row as ``withhold_untagged_street_view`` would have:
    the tag when the tag is published, the prior when a fallback is, else the drone seeds
    (``measured_seeds``) and, where Street View is still published, its seeds. No
    ``prior_disagrees``: the prior's height is not in an old report."""
    from ._core.height import TAGGED_SOURCES
    from ._core.tiers import reading, tier_fields

    out = []
    for b in buildings:
        if b.get("tier"):
            out.append(b)
            continue
        src = b.get("effective_height_source") or ""
        seeds = {k: v for k, v in (b.get("per_seed_median_m") or {}).items() if v is not None}
        drone = [reading("drone", v, k) for k, v in seeds.items() if k in measured_seeds]
        street = [reading("street", v, k) for k, v in seeds.items() if k not in measured_seeds]
        tagged = b.get("height_source") in TAGGED_SOURCES and src == b.get("height_source")
        if tagged or src == "withheld:elevated":
            readings = drone
        elif src.startswith("withheld:"):
            readings = []
        else:
            readings = drone + street
        f = tier_fields(readings, published_m=b.get("effective_height_m"),
                        tag_m=b.get("height_tag_m") if tagged else None,
                        prior_source=src.split(":", 1)[1] if src.startswith("withheld:") else None)
        out.append({**b, **f})
    return out


def survey_blind(buildings: list[dict]) -> list[dict]:
    """``buildings`` as the survey-blind run saw them (F-SKY26 2d): a row published from the survey
    (tier ``survey``) goes back to its pre-survey height, source and tier (``no_survey_*``). A
    survey row is half of its own truth, so scoring it on the survey would be circular; one
    without ``no_survey_height_m`` cannot be put back and is left out. Other rows pass through
    (the same list items). ``score_report`` scores everything on these except ``score_survey_rows``."""
    out = []
    for b in buildings:
        if b.get("tier") != "survey":
            out.append(b)
        elif b.get("no_survey_height_m") is not None:
            out.append({**b, "effective_height_m": b["no_survey_height_m"],
                        "effective_height_source": b.get("no_survey_source"),
                        "tier": b.get("no_survey_tier") or "unlabelled"})
    return out


def score_survey_rows(buildings: list[dict], truth: dict[str, dict]) -> dict:
    """Rows that publish a survey height (tier ``survey``, F-SKY26 2d), scored against the
    3D Tiles reading only (``tiles_m``, flat meshes excluded): the survey half of the truth
    is the same measurement. Success criterion: within 25 % of ``tiles_m`` 90 % of the time.
    Rows flagged stale (``survey_stale``) are counted apart (``n_stale``) and left out of
    ``vs_tiles``: the flag says the survey may not show the building as it stands."""
    rows = [(float(b["effective_height_m"]), float(t["tiles_m"])) for b in buildings
            if b.get("tier") == "survey" and b.get("effective_height_m") is not None
            and not b.get("survey_stale")
            and (t := truth.get(b["key"])) and t.get("tiles_m") is not None
            and not t.get("tiles_flat")]
    n_survey = sum(1 for b in buildings if b.get("tier") == "survey")
    n_stale = sum(1 for b in buildings if b.get("tier") == "survey" and b.get("survey_stale"))
    if not rows:
        return {"n_survey_rows": n_survey, "n_stale": n_stale, "vs_tiles": {"n": 0}}
    pred, tiles = (np.array(c) for c in zip(*rows, strict=True))
    return {"n_survey_rows": n_survey, "n_stale": n_stale, "vs_tiles": _errors(pred, tiles)}


def score_by_tier(buildings: list[dict], truth: dict[str, dict],
                  pred_field: str = "effective_height_m", tier_field: str = "tier") -> dict:
    """Errors per verification tier: do the tiers mean what they say? Within 25 % should
    fall in tier order (``corroborated`` at least 0.85). Non-survey tiers are scored on
    confirmed truth; ``survey`` rows against ``tiles_m`` only (``score_survey_rows``).
    Rows without a tier (run ``label_tiers`` first) count as ``unlabelled``."""
    from ._core.tiers import TIERS, canonical_tier

    by: dict[str, list[tuple[float, float]]] = {}
    for b in buildings:
        tier = canonical_tier(b.get(tier_field)) or "unlabelled"
        pred = b.get(pred_field, b.get("effective_height_m"))
        t = truth.get(b["key"])
        if pred is None or not t:
            continue
        if tier == "survey":
            if t.get("tiles_m") is None or t.get("tiles_flat"):
                continue
            ref = t["tiles_m"]
        elif t["status"] == "confirmed":
            ref = t["truth_m"]
        else:
            continue
        by.setdefault(tier, []).append((float(pred), float(ref)))
    out = {}
    for tier in (*TIERS, "unlabelled"):
        if tier in by:
            pred, ref = (np.array(c) for c in zip(*by[tier], strict=True))
            out[tier] = _errors(pred, ref)
    return out


# --------------------------------------------------------------------------- band tables (item 1)
# Review 2026-10-09 §3 item 1 / §4.4: a benchmark that can prove or refuse a change, by height
# band and by tier. Beside the headline (``score_buildings``, unchanged), not instead of it.

#: Truth modes of a region's scored footprints (``truth_mode``).
TWO_SOURCE = "two_source"        # survey + 3D Tiles, cross-checked: ``confirmed`` rows score


SINGLE_SOURCE = "single_source"  # survey only (no 3D Tiles read): ``survey_only`` rows score


NO_TRUTH = "none"


#: Tiers whose rows claim two independent readings agree (``corroborated``, called ``verified_2``
#: until 2026-10-09; ``canonical_tier`` maps old rows): a wrong one is a false corroboration.
CORROBORATED_TIERS = ("corroborated",)


#: Tiers that publish an image reading (``single`` and the corroborated ones).
READING_TIERS = ("single", *CORROBORATED_TIERS)


def _band_label(lo: float, hi: float, kind: str = "review") -> str:
    if kind == "review":
        return (f"<{hi:.0f}" if lo == 0 else f">{lo:.0f}" if math.isinf(hi)
                else f"{lo:.0f}-{hi:.0f}")
    return f"{lo:.0f}+" if math.isinf(hi) else f"{lo:.0f}-{hi:.0f}"


REVIEW_LABELS = tuple(_band_label(lo, hi) for lo, hi in REVIEW_BANDS)


EUBUCCO_LABELS = tuple(_band_label(lo, hi, "eubucco") for lo, hi in EUBUCCO_BANDS)


DISTANCE_LABELS = tuple(_band_label(lo, hi) for lo, hi in DISTANCE_BANDS)


def band_of(value: float, bands=REVIEW_BANDS, labels=REVIEW_LABELS) -> str | None:
    """The label of the ``[lo, hi)`` band holding ``value``."""
    for (lo, hi), lab in zip(bands, labels, strict=True):
        if lo <= value < hi:
            return lab
    return None


def tol_m(truth):
    """The "tol" yardstick at ``truth`` metres: max(``TOL_REL`` x truth, ``TOL_ABS_M``)."""
    return np.maximum(TOL_REL * np.asarray(truth, float), TOL_ABS_M)


def band_errors(pred, truth) -> dict:
    """The review's metrics (§4.4) of predictions against truth: n, MAE, median AE, signed
    bias, P90 AE, ``tol`` (share within max(25 %, 2 m) of truth) and the shares off by more
    than 5 m and 10 m."""
    pred, truth = np.asarray(pred, float), np.asarray(truth, float)
    if pred.size == 0:
        return {"n": 0}
    err = pred - truth
    ae = np.abs(err)
    return {"n": int(pred.size), "mae_m": round(float(ae.mean()), 2),
            "median_ae_m": round(float(np.median(ae)), 2), "bias_m": round(float(err.mean()), 2),
            "p90_ae_m": round(float(np.percentile(ae, 90)), 2),
            "tol": round(float(np.mean(ae <= tol_m(truth))), 3),
            "over_5m": round(float(np.mean(ae > 5.0)), 3),
            "over_10m": round(float(np.mean(ae > 10.0)), 3)}


def truth_mode(buildings: list[dict], truth: dict[str, dict]) -> str:
    """``TWO_SOURCE`` when any of the report's footprints has a 3D Tiles reading (confirmed,
    disputed or tiles-only truth), else ``SINGLE_SOURCE`` when any has survey-only truth, else
    ``NO_TRUTH``. A single-source region (San Juan, Honolulu, Fort Lauderdale: no 3D Tiles,
    monthly cap) scores its survey-only records, reported apart."""
    st = {truth[b["key"]]["status"] for b in buildings if b["key"] in truth}
    if st & {"confirmed", "disputed", "tiles_only"}:
        return TWO_SOURCE
    return SINGLE_SOURCE if "survey_only" in st else NO_TRUTH


#: A footprint with this share of its area under another footprint of the report is "nested"
#: (an OSM ``building:part`` slice inside an outline, a podium part under a tower part): the
#: survey and 3D Tiles see only the top of the stack, so neither measures the lower part's own
#: height. Honolulu 2026-10-09: 236 of 1,118 published rows with truth sit under a taller
#: footprint; without them the tag tier's MAE drops 23.1 -> 14.9 m (40-100 m) and 37.3 -> 26.2 m
#: (>100 m).
NESTED_COVER = 0.5


def nested_keys(buildings: list[dict], cover: float = NESTED_COVER) -> set[str]:
    """Keys of the footprints with at least ``cover`` of their area under another footprint of
    ``buildings`` that stands above them: a higher OSM height tag when both are tagged (inputs,
    so the set does not move with the run's answers), else a larger area. An outline holding its
    own podium part stays; the podium goes. Pass every published row (tag-only rows too)."""
    from shapely.strtree import STRtree

    polys = [_polygon(b["footprint_lonlat"]) for b in buildings]
    tags = [b.get("height_tag_m") for b in buildings]
    tree = STRtree(polys)
    out = set()
    for i, p in enumerate(polys):
        if p.is_empty or p.area <= 0:
            continue
        for j in tree.query(p):
            q = polys[j]
            if j == i or not q.intersects(p) or q.intersection(p).area < cover * p.area:
                continue
            above = (tags[j] > tags[i] if tags[i] is not None and tags[j] is not None
                     else q.area > p.area)
            if above:
                out.add(buildings[i]["key"])
                break
    return out


def scoring_truth(truth: dict[str, dict], mode: str,
                  nested: set[str] | frozenset = frozenset()) -> tuple[dict[str, dict], dict]:
    """``(truth records scored, exclusions)``. Two-source: ``confirmed`` only (the headline's
    rule). Single-source: ``survey_only``, less the records whose building may postdate the
    survey (``temporal`` ``may_postdate``, from OSM ``start_date``): the survey may show a
    construction site. Both: less ``nested`` footprints (``nested_keys``)."""
    want = "confirmed" if mode == TWO_SOURCE else "survey_only"
    out, excluded = {}, {"may_postdate": 0, "nested": 0}
    for k, r in truth.items():
        if r.get("status") != want or r.get("truth_m") is None:
            continue
        if mode == SINGLE_SOURCE and r.get("temporal") == TEMPORAL_MAY_POSTDATE:
            excluded["may_postdate"] += 1
            continue
        if k in nested:
            excluded["nested"] += 1
            continue
        out[k] = r
    return out, excluded


def attach_roof_stats(truth: dict[str, dict], survey_cache: dict[str, dict]) -> dict[str, dict]:
    """``truth`` with each record's stored roof statistics (``roof_m``) from the survey cache
    (``survey_heights``, the newest statistics: it is re-read when ``ROOF_STATS_VERSION`` moves),
    else the record's own (``roof_m`` / ``survey_roof_m``)."""
    out = {}
    for k, r in truth.items():
        if (s := survey_cache.get(k)) and s.get("roof_m"):
            r = {**r, "roof_m": s["roof_m"], "ground_p5_m": s.get("ground_p5_m")}
        out[k] = r
    return out


def matched_truth(rec: dict, stat: str) -> float | None:
    """The truth for a reading measured against roof statistic ``stat`` (``METHOD_STAT``):
    the headline ``truth_m`` moved by the survey's (stat - p95). None without stored stats."""
    if stat == "p95":
        return float(rec["truth_m"])
    roof = rec.get("roof_m") or rec.get("survey_roof_m") or {}
    if roof.get(stat) is None or roof.get("p95") is None:
        return None
    return float(rec["truth_m"]) + float(roof[stat]) - float(roof["p95"])
