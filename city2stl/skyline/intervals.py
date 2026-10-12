"""Split-conformal height intervals per tier x band, and the measured OSM tag bias (audit
2026-10-09 item 9).

A published height carries no error bar. This module measures, per tier and height band, how
far the survey-blind published height sits from the surveyed truth in the four truth cities
(Miami, Honolulu, Fort Lauderdale, San Juan), and turns that into a per-row interval:

    residuals   e = ln(published / truth), one per scored row (tag-only rows too, survey-blind,
                nested footprints out), banded by the *published* height (the only height known
                when the row is published; the benchmark's tables band by truth).
    fit         per tier x band with n >= MIN_N: the bias term b = median(e) and, per level,
                the split-conformal quantile q of |e - b| (rank ceil((n + 1) * level)). The fit
                cities weigh the same (each city's rows in a cell share one unit of weight;
                weighted median and quantile): San Juan's 10,000 tags would otherwise set the
                quantile of every other city (2026-10-10: held-out coverage at 80 % passed 1 of
                11 bands row-pooled, 3 of 11 city-weighted). A cell under MIN_N falls back to the
                tier's all-band cell; a tier under MIN_N has no interval ("not testable").
    interval    truth in [published * exp(-b - q), published * exp(-b + q)] at the level.
    coverage    on a held-out city: the share of rows whose truth falls in the interval, and the
                median of e - b (the bias left after the band's term). Leave-one-city-out over
                the four cities tests the pooled fit; the published fit pools all four.

Published on each row (``annotate``): ``interval_lo_m`` / ``interval_hi_m`` at
``PUBLISHED_LEVEL`` and, on ``tag`` rows, ``tag_bias_m`` = published - published * exp(-b): the
tag's expected signed error in metres (negative: the tag sits below the likely truth). The
published value itself is not corrected: the user keeps tags until they decide (decisions,
building-heights.md). A city without truth (Cartagena) gets the pooled intervals with a note.
``survey`` rows get no interval (never scored on the survey they came from).

    python -m city2stl.skyline.intervals fit --report miami=<heights.json> ... [--out FIT]
    python -m city2stl.skyline.intervals apply <heights.json> [--fit FIT] [--note TEXT]

The fit ships in ``data/height_intervals.json`` (``FIT_PATH``).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np

from . import benchmark as bm
from ._core.tiers import canonical_tier

#: Interval levels fitted (nominal coverage).
LEVELS = (0.8, 0.9)
#: The level written on the rows.
PUBLISHED_LEVEL = 0.8
#: Rows a tier x band cell needs to be fitted or tested (audit §8.1: else "not testable").
MIN_N = 30
#: Pass marks on a held-out city (audit item 9): nominal 80 % covers 75-85 %, and the
#: median log bias after the band's term is within +-0.05.
COVERAGE_PASS = (0.75, 0.85)
BIAS_PASS = 0.05
ALL = "all"
FIT_PATH = Path(__file__).resolve().parent / "data" / "height_intervals.json"
TRUTH_CITIES = ("miami", "honolulu", "fort_lauderdale", "san_juan")


# --------------------------------------------------------------------------- residuals

def residuals(buildings: Sequence[dict], truth: dict[str, dict],
              nested: set[str] | frozenset = frozenset(), city: str = "") -> list[dict]:
    """One record per scored row: ``tier``, ``band`` (of the published height), ``pred``,
    ``truth``, ``e`` = ln(pred / truth) and ``city``. ``buildings``: survey-blind rows with
    ``key`` (``benchmark.load_report`` + ``survey_blind``); ``truth``: the region's truth
    records; the rows scored are ``scoring_truth``'s (confirmed, or survey-only less
    may-postdate), less ``nested``."""
    keys = {b["key"] for b in buildings}
    mode = bm.truth_mode(list(buildings), truth)
    scored, _ = bm.scoring_truth({k: r for k, r in truth.items() if k in keys}, mode, nested)
    out = []
    for b in buildings:
        t = scored.get(b["key"])
        pred, tier = bm._pred_tier(b)
        if t is None or pred is None or pred <= 0 or not t.get("truth_m") or t["truth_m"] <= 0:
            continue
        tm = float(t["truth_m"])
        out.append({"tier": tier, "band": bm.band_of(pred), "pred": pred, "truth": tm,
                    "e": math.log(pred / tm), "city": city})
    return out


def report_residuals(region: str, heights_json) -> list[dict]:
    """``residuals`` of a report's published rows (tag-only rows too), from the region's cached
    truth; no survey or 3D Tiles reads."""
    from .region_data import _load_site_elevated_seeds

    elevated = set(_load_site_elevated_seeds(region) or ())
    _, published = bm.load_report(Path(heights_json), include_unmeasured=True)
    published = bm.survey_blind(bm.label_tiers(published, elevated))
    truth = bm.load_truth_cache(region)
    return residuals(published, truth, bm.nested_keys(published), city=region)


# --------------------------------------------------------------------------- tag bias

def bias_table(res: Iterable[dict]) -> dict:
    """``{tier: {band: {n, median_log_bias, bias_m, mae_m, tol}}}`` (band ``all`` too) of
    residual records: the tier's median log bias, mean signed error and MAE in metres and the
    share within max(25 %, 2 m) of truth."""
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in res:
        for band in (r["band"], ALL):
            cells[(r["tier"], band)].append(r)
    out: dict[str, dict] = {}
    for (tier, band), rows in cells.items():
        e = np.array([r["e"] for r in rows])
        pred = np.array([r["pred"] for r in rows])
        tr = np.array([r["truth"] for r in rows])
        ae = np.abs(pred - tr)
        out.setdefault(tier, {})[band] = {
            "n": int(e.size), "median_log_bias": round(float(np.median(e)), 3),
            "bias_m": round(float(np.mean(pred - tr)), 2), "mae_m": round(float(ae.mean()), 2),
            "tol": round(float(np.mean(ae <= bm.tol_m(tr))), 3)}
    return out


# --------------------------------------------------------------------------- fit

def conformal_quantile(values, level: float, weights=None) -> float:
    """The split-conformal quantile of nonconformity scores ``values`` at ``level``: the
    ``ceil((n + 1) * level)``-th smallest (the largest when that exceeds n). With ``weights``
    (one per score): the smallest score whose cumulative share of the weight reaches
    ``level`` (the finite-sample step ``(n + 1) / n`` applied to the level)."""
    v = np.asarray(values, float)
    n = v.size
    if n == 0:
        raise ValueError("no scores")
    order = np.argsort(v, kind="stable")
    v = v[order]
    if weights is None:
        k = min(n, int(math.ceil((n + 1) * level)))
        return float(v[k - 1])
    w = np.asarray(weights, float)[order]
    cw = np.cumsum(w) / w.sum()
    i = int(np.searchsorted(cw, min(1.0, level * (n + 1) / n), side="left"))
    return float(v[min(i, n - 1)])


def _weights(res: Sequence[dict]) -> np.ndarray:
    """One unit of weight per city in ``res`` (rows without a city share one unit)."""
    counts = defaultdict(int)
    for r in res:
        counts[r.get("city") or ""] += 1
    return np.array([1.0 / counts[r.get("city") or ""] for r in res])


def _lvl(level: float) -> str:
    return f"{level:g}"


def fit(res: Iterable[dict], levels: Sequence[float] = LEVELS, min_n: int = MIN_N,
        cities: Sequence[str] = (), weight_cities: bool = True) -> dict:
    """The fit: ``cells[tier][band]`` = ``{n, testable, bias_log, q: {level: q}}`` per tier x
    band (band ``all`` too). A cell with fewer than ``min_n`` rows is ``testable`` False and
    carries only ``n``. ``weight_cities``: each city's rows in a cell share one unit of weight
    (weighted median and quantile); False pools the rows."""
    res = list(res)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in res:
        for band in (r["band"], ALL):
            groups[(r["tier"], band)].append(r)
    cells: dict[str, dict] = {}
    for (tier, band), rows in groups.items():
        e = np.array([r["e"] for r in rows], float)
        cell: dict = {"n": int(e.size), "testable": e.size >= min_n}
        if cell["testable"]:
            w = _weights(rows) if weight_cities else None
            b = conformal_quantile(e, 0.5, w) if w is not None else float(np.median(e))
            cell["bias_log"] = round(b, 4)
            cell["q"] = {_lvl(lv): round(conformal_quantile(np.abs(e - b), lv, w), 4)
                         for lv in levels}
            cell["cities"] = sorted({r.get("city") or "" for r in rows} - {""})
        cells.setdefault(tier, {})[band] = cell
    return {"levels": [float(lv) for lv in levels], "min_n": min_n, "bands": list(bm.REVIEW_LABELS),
            "band_by": "published height", "weight_cities": weight_cities,
            "cities": list(cities), "n": len(res), "cells": cells}


def cell_for(fit_: dict, tier: str | None, band: str | None) -> tuple[dict | None, str | None]:
    """``(cell, source)`` for a tier and band: the band's cell when testable, else the tier's
    all-band cell, else ``(None, None)``. ``source`` names the band used."""
    tiers = fit_.get("cells") or {}
    by_band = tiers.get(canonical_tier(tier) or "") or {}
    for key in (band, ALL):
        c = by_band.get(key) if key else None
        if c and c.get("testable"):
            return c, key
    return None, None


def interval(pred: float, cell: dict, level: float = PUBLISHED_LEVEL) -> tuple[float, float]:
    """``(lo, hi)`` for the truth of a row published at ``pred`` metres."""
    b, q = float(cell["bias_log"]), float(cell["q"][_lvl(level)])
    return pred * math.exp(-b - q), pred * math.exp(-b + q)


def tag_bias_m(pred: float, cell: dict) -> float:
    """The expected signed error of a published ``pred`` under the cell's bias term:
    ``pred - pred * exp(-b)`` (negative: the published value sits below the likely truth)."""
    return pred - pred * math.exp(-float(cell["bias_log"]))


# --------------------------------------------------------------------------- coverage

def coverage(fit_: dict, res: Iterable[dict], level: float = PUBLISHED_LEVEL,
             min_n: int = MIN_N) -> dict:
    """``{tier: {band: {n, covered, bias_after, testable, pass, source}}}`` of ``fit_`` on
    residual records (a held-out city): the share whose truth falls inside the interval and
    the median of ``e - b``. ``pass``: both marks (``COVERAGE_PASS``, ``BIAS_PASS``) on a
    testable band (n >= ``min_n``); None where not testable. Rows whose tier has no cell are
    counted in ``no_interval``."""
    groups: dict[tuple[str, str], list[tuple[bool, float, str]]] = defaultdict(list)
    no_interval = 0
    for r in res:
        cell, src = cell_for(fit_, r["tier"], r["band"])
        if cell is None:
            no_interval += 1
            continue
        lo, hi = interval(r["pred"], cell, level)
        hit = lo <= r["truth"] <= hi
        for band in (r["band"], ALL):
            groups[(r["tier"], band)].append((hit, r["e"] - float(cell["bias_log"]), src))
    out: dict = {"level": level, "no_interval": no_interval}
    for (tier, band), rows in groups.items():
        hits = np.array([h for h, _, _ in rows])
        bias = float(np.median([d for _, d, _ in rows]))
        n = int(hits.size)
        testable = n >= min_n
        cov = float(hits.mean())
        cell = {"n": n, "covered": round(cov, 3), "bias_after": round(bias, 3),
                "testable": testable,
                "source": sorted({s for _, _, s in rows}),
                "pass": (COVERAGE_PASS[0] <= cov <= COVERAGE_PASS[1] and abs(bias) <= BIAS_PASS)
                if testable else None}
        out.setdefault(tier, {})[band] = cell
    return out


def leave_one_out(res_by_city: dict[str, list[dict]], level: float = PUBLISHED_LEVEL,
                  levels: Sequence[float] = LEVELS, min_n: int = MIN_N,
                  weight_cities: bool = True) -> dict:
    """``{held_out_city: coverage}``: for each city, the fit on the other cities, tested on
    it."""
    out = {}
    for city, res in res_by_city.items():
        rest = [{**r, "city": c} for c, rs in res_by_city.items() if c != city for r in rs]
        f = fit(rest, levels, min_n, cities=[c for c in res_by_city if c != city],
                weight_cities=weight_cities)
        out[city] = coverage(f, res, level, min_n)
    return out


# --------------------------------------------------------------------------- publish

ROW_FIELDS = ("interval_lo_m", "interval_hi_m", "tag_bias_m")


def annotate(rows: Iterable[dict], fit_: dict, level: float = PUBLISHED_LEVEL) -> int:
    """Write ``interval_lo_m`` / ``interval_hi_m`` (and ``tag_bias_m`` on ``tag`` rows) on each
    row in place from its tier and published height; rows without a usable cell (``survey``
    rows, tiers under ``MIN_N``) lose any old values. Returns the rows given an interval."""
    n = 0
    for row in rows:
        for k in ROW_FIELDS:
            row.pop(k, None)
        tier = canonical_tier(row.get("tier"))
        pred = row.get("effective_height_m")
        if tier in (None, "survey") or pred is None or float(pred) <= 0:
            continue
        pred = float(pred)
        cell, _ = cell_for(fit_, tier, bm.band_of(pred))
        if cell is None:
            continue
        lo, hi = interval(pred, cell, level)
        row["interval_lo_m"] = round(lo, 1)
        row["interval_hi_m"] = round(hi, 1)
        if tier == "tag":
            row["tag_bias_m"] = round(tag_bias_m(pred, cell), 1)
        n += 1
    return n


def load_fit(path=FIT_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def annotate_report(heights_json, fit_: dict | None = None, level: float = PUBLISHED_LEVEL,
                    note: str | None = None, out=None) -> dict:
    """``annotate`` every row of a ``heights.json`` and write it back (or to ``out``), with an
    ``intervals`` block: the level, the fit's cities and cells (n, bias, q), ``n_rows`` given an
    interval, and ``note``. Returns the block."""
    from ._core.tiers import upgrade_tiers

    heights_json = Path(heights_json)
    fit_ = fit_ or load_fit()
    data = upgrade_tiers(json.loads(heights_json.read_text(encoding="utf-8")))
    rows = data.get("buildings") or []
    n = annotate(rows, fit_, level)
    block = {"level": level, "band_by": fit_.get("band_by"), "fit_cities": fit_.get("cities"),
             "fit_fitted": fit_.get("fitted"), "min_n": fit_.get("min_n"),
             "cells": {t: {b: c for b, c in bands.items() if c.get("testable")}
                       for t, bands in (fit_.get("cells") or {}).items()},
             "n_rows": n, "fields": list(ROW_FIELDS),
             "note": note or ("truth in [interval_lo_m, interval_hi_m] at the level on the "
                              "fit cities (split conformal, per tier x band of the published "
                              "height); tag_bias_m: the tag's expected signed error, not applied")}
    data["intervals"] = block
    Path(out or heights_json).write_text(json.dumps(data, indent=1), encoding="utf-8")
    return block


# --------------------------------------------------------------------------- tables

def _fmt(v, spec):
    return "-" if v is None else format(v, spec)


def print_bias(by_city: dict[str, dict], tier: str = "tag") -> None:
    """The tier's bias per city x band: n, median log bias, bias (m), MAE (m), tol."""
    print(f"\n{tier} tier, survey-blind published rows, banded by the published height "
          f"(cell: n / median ln(pub/truth) / bias m / MAE m / tol)")
    bands = (*bm.REVIEW_LABELS, ALL)
    print(f"{'city':16s} " + " ".join(f"{b:>26s}" for b in bands))
    for city, table in by_city.items():
        cells = []
        for b in bands:
            c = (table.get(tier) or {}).get(b)
            cells.append("-" if not c else
                         f"{c['n']:4d} {c['median_log_bias']:+6.3f} {c['bias_m']:+6.1f} "
                         f"{c['mae_m']:5.1f} {c['tol']:4.0%}")
        print(f"{city:16s} " + " ".join(f"{c:>26s}" for c in cells))


def print_coverage(loo: dict[str, dict], tier: str = "tag") -> None:
    """Held-out coverage per city x band at the published level: n, covered, bias after."""
    bands = (*bm.REVIEW_LABELS, ALL)
    print(f"\n{tier} tier, leave-one-city-out coverage (cell: n / covered / median ln bias after "
          f"the band term / pass; '-' no rows, 'nt' not testable, n < {MIN_N})")
    print(f"{'held out':16s} " + " ".join(f"{b:>22s}" for b in bands))
    for city, cov in loo.items():
        cells = []
        for b in bands:
            c = (cov.get(tier) or {}).get(b)
            if not c:
                cells.append("-")
            else:
                mark = "nt" if c["pass"] is None else ("pass" if c["pass"] else "FAIL")
                cells.append(f"{c['n']:4d} {c['covered']:5.1%} {c['bias_after']:+6.3f} {mark}")
        print(f"{city:16s} " + " ".join(f"{c:>22s}" for c in cells))


def print_fit(fit_: dict) -> None:
    """The pooled cells: per tier x band n, bias term and the q per level."""
    how = "cities weighted equally" if fit_.get("weight_cities") else "rows pooled"
    print(f"\nfit on {', '.join(fit_.get('cities') or ())} ({how}): per tier x band n, bias "
          f"term b (ln), q per level (interval = pub * exp(-b -+ q))")
    for tier, bands in sorted((fit_.get("cells") or {}).items()):
        for band in (*bm.REVIEW_LABELS, ALL):
            c = bands.get(band)
            if not c:
                continue
            if not c.get("testable"):
                print(f"  {tier:12s} {band:7s} n {c['n']:5d}  not testable")
                continue
            qs = " ".join(f"{lv}: {q:.3f}" for lv, q in c["q"].items())
            print(f"  {tier:12s} {band:7s} n {c['n']:5d}  b {c['bias_log']:+.3f}  q {qs}")


# --------------------------------------------------------------------------- CLI

def _fit_main(args) -> int:
    import datetime as dt

    reports = dict(a.split("=", 1) for a in args.report)
    res_by_city = {city: report_residuals(city, path) for city, path in reports.items()}
    by_city = {city: bias_table(res) for city, res in res_by_city.items()}
    weighted = not args.pool_rows
    loo = leave_one_out(res_by_city, args.level, weight_cities=weighted)
    pooled = fit([{**r, "city": c} for c, rs in res_by_city.items() for r in rs],
                 cities=list(res_by_city), weight_cities=weighted)
    pooled["fitted"] = dt.date.today().isoformat()
    pooled["reports"] = {c: str(p) for c, p in reports.items()}
    pooled["bias_by_city"] = by_city
    pooled["leave_one_out"] = loo
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pooled, indent=1), encoding="utf-8")
    tiers = sorted({t for rs in res_by_city.values() for r in rs for t in [r["tier"]]})
    for tier in tiers:
        print_bias(by_city, tier)
        print_coverage(loo, tier)
    print_fit(pooled)
    print(f"\nfit: {out}")
    return 0


def _apply_main(args) -> int:
    block = annotate_report(args.heights, load_fit(args.fit), args.level, note=args.note,
                            out=args.out)
    print(f"{args.heights}: {block['n_rows']} rows given an interval at {block['level']:g}")
    return 0


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit", help="fit the intervals on truth-city reports")
    f.add_argument("--report", action="append", required=True, metavar="REGION=HEIGHTS_JSON")
    f.add_argument("--out", default=str(FIT_PATH))
    f.add_argument("--level", type=float, default=PUBLISHED_LEVEL)
    f.add_argument("--pool-rows", action="store_true",
                   help="pool the cities' rows instead of weighting the cities equally")
    f.set_defaults(func=_fit_main)
    a = sub.add_parser("apply", help="write intervals on a report's rows")
    a.add_argument("heights")
    a.add_argument("--fit", default=str(FIT_PATH))
    a.add_argument("--level", type=float, default=PUBLISHED_LEVEL)
    a.add_argument("--note", default=None)
    a.add_argument("--out", default=None, help="write here instead of in place")
    a.set_defaults(func=_apply_main)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
