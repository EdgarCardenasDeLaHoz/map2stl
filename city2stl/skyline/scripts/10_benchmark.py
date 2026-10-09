#!/usr/bin/env python3
"""F-SKYBENCH: run the skyline pipeline on the benchmark cities and score it on surveyed truth.

    python -m city2stl.skyline.scripts.10_benchmark                       # all regions: run + score
    python -m city2stl.skyline.scripts.10_benchmark --regions miami boston
    python -m city2stl.skyline.scripts.10_benchmark --score-only          # newest report per region
    python -m city2stl.skyline.scripts.10_benchmark --score-only --report path/to/heights.json

Each region runs ``08_region_skyline_pdf`` in its own process (a native crash in one city
must not end the batch), into ``runs/benchmark/<stamp>/``. Truth comes from
``city2stl.skyline.benchmark`` (survey nDSM + 3D Tiles, cross-checked, cached per region).
The headline scores the survey-blind height (``no_survey_height_m``, F-SKY26 2c); rows that
publish a survey height are scored against 3D Tiles alone (``survey_rows``), and every tier
separately (``tiers``). Writes ``summary.json`` beside the reports, a ``benchmark.html`` page into each scored report
(``benchmark_report.write_benchmark_page``, linked from its index), and prints one table.

Beside that headline (unchanged), each result carries ``bench`` (review 2026-10-09 item 1,
``benchmark.bench_tables``) twice: ``measured`` (the rows the run measured, the headline's set)
and ``published`` (every published row, tag-only rows too, truth from the cache only), each with
the truth mode, band x tier in the review's bands (<15 / 15-40 /
40-100 / >100 m) and EUBUCCO's (0-5 / 5-10 / 10-20 / 20+), band x method x camera-distance band,
and the pipeline metrics (coverage, false-corroborated rate, withhold precision / recall), each
cell with n, MAE, median AE, bias, P90 AE, tol (within max(25 %, 2 m)) and the shares off by > 5 m
and > 10 m. A region whose truth is survey-only (no 3D Tiles: San Juan, Honolulu, Fort Lauderdale)
is single-source: its headline scores n = 0 as before, and ``single_source`` scores its
``survey_only`` records, printed in a table of their own. ``--tables-out`` writes the printed
tables to a file too; ``--cached-truth`` scores cached truth only (no survey reads).

Every flag that changes the heights is pinned to ``PINNED_FLAGS`` in each region's process (the
values the 2026-10-04 baseline ran with), so a developer's shell cannot leak into a run;
``--keep-env`` lets the shell's values through, for trying a flag. ``summary.json`` records the
flags each region ran with and whether Street View signing was on (it changes the image size).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from city2stl.skyline import benchmark as bm

ROOT = Path(__file__).resolve().parents[3]
REPORTS = ROOT / "city2stl" / "skyline" / "runs" / "region_reports"

#: The flags that change the heights, at the values the 2026-10-04 baseline ran with
#: (production SegFormer b1 at 512 px, tag filter on). Report-only flags
#: (SKYLINE_CV_HTML_*, _PANO_LAYERED, _F_SKY13_SAT_BG) and speed-only ones
#: (SKYLINE_CV_SEGFORMER_DEVICE, _BATCH: bit-identical output) are left to the shell.
PINNED_FLAGS = {
    "SKYLINE_CV_SEGFORMER_SIZE": "b1",
    "SKYLINE_CV_SEGFORMER_INPUT_SIZE": "512",
    "SKYLINE_TAG_FILTER": "1",
    # T28: the published height withholds untagged Street View values; both are scored
    # (``street_view_unwithheld`` is the 2026-10-04 baseline's yardstick)
    "SKYLINE_WITHHOLD_UNTAGGED": "1",
    "SKYLINE_UNTAGGED_FALLBACK": "model",   # T41: the height prior, not a constant
    "SKYLINE_SV_TALL_FRAME": "0",
    "SKYLINE_CV_MULTIRES": "0",
    "SKYLINE_CV_PHASE_C": "0",
    "SKYLINE_CV_F_SKY1": "1",
    "SKYLINE_CV_F_SKY5": "1",               # MobileSAM by default (2026-10-06)
    "SKYLINE_CV_F_SKY11_1": "0",
    "SKYLINE_CV_F_SKY12": "0",
    "SKYLINE_CV_F_SKY13": "1",
}


def _region_env(keep_env: bool = False, base: dict | None = None) -> dict:
    """Environment for one region's pipeline process: ``base`` (default ``os.environ``)
    with ``PINNED_FLAGS`` on top unless ``keep_env``."""
    env = {**(os.environ if base is None else base), "PYTHONIOENCODING": "utf-8"}
    if not keep_env:
        env.update(PINNED_FLAGS)
    return env


def _run_settings(env: dict) -> dict:
    """What summary.json records about how the regions ran."""
    return {
        "env": {k: v for k, v in sorted(env.items()) if k.startswith("SKYLINE_")},
        # Signed requests get 1280 px spin views instead of 640: a different input.
        "signed_streetview": bool(env.get("GOOGLE_MAPS_SIGN_SECRET")),
    }


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
                              capture_output=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _run_region(region: str, out_dir: Path, env: dict) -> Path | None:
    """Run the production pipeline for ``region``; return its heights.json, or None."""
    out_pdf = out_dir / f"{region}_skyline_report.pdf"
    log = out_dir / f"{region}.log"
    cmd = [sys.executable, "-u", "-m", "city2stl.skyline.scripts.08_region_skyline_pdf",
           "--region", region, "--out", str(out_pdf)]
    from city2stl.resources import wait_for_ram

    wait_for_ram()                              # each region run is a heavy job (CLAUDE.md)
    logging.info("[bench] running %s (log: %s)", region, log)
    with log.open("w", encoding="utf-8") as fh:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                            env=env).returncode
    heights = out_dir / f"{region}_skyline_report" / "heights.json"
    if rc != 0 or not heights.exists():
        logging.error("[bench] %s failed (exit %s); see %s", region, rc, log)
        return None
    return heights


def _newest_report(region: str) -> Path | None:
    """Newest heights.json for ``region`` across benchmark runs and region_reports."""
    found = list((bm.BENCHMARK_ROOT).glob(f"*/{region}_skyline_report/heights.json"))
    found += [p for p in REPORTS.glob("*_skyline_report/heights.json")
              if p.parent.name.lower().startswith(region)]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def score_report(heights: Path, region: str | None = None, use_tiles: bool = True,
                 refresh_truth: bool = False, cached_truth: bool = False) -> dict:
    name, buildings = bm.load_report(heights)
    region = region or bm.region_key(name)
    provider = bm.REGIONS.get(region)
    if provider is None:
        logging.warning("[bench] %s is not a benchmark region; scoring with 3D Tiles only",
                        region)
    tags = {b["key"]: float(b["height_tag_m"]) for b in buildings
            if b.get("height_tag_m") is not None and b.get("height_source") != "default"}
    if cached_truth:  # no reads: footprints without a cached record are unmeasured
        cache = bm.load_truth_cache(region)
        truth = {b["key"]: cache[b["key"]] for b in buildings if b["key"] in cache}
    else:
        truth = bm.footprint_truth(region, {b["key"]: b["footprint_lonlat"] for b in buildings},
                                   provider, use_tiles=use_tiles, refresh=refresh_truth,
                                   tags_m=tags)
    from city2stl.skyline import survey_heights as sh
    from city2stl.skyline.region_data import _load_site_elevated_seeds

    elevated = set(_load_site_elevated_seeds(region) or ())
    # tiers for a report from before F-SKY26 2a, from the site's drone seeds
    buildings = bm.label_tiers(buildings, elevated)
    # the headline is the survey-blind answer (F-SKY26 2c): survey rows would be scored
    # against a truth they are half of; they are scored against 3D Tiles alone instead
    result = {"region": region, "report": str(heights), "survey": provider,
              **bm.score_buildings(buildings, truth, pred_field="no_survey_height_m"),
              "tiers": bm.score_by_tier(buildings, truth),
              "survey_rows": bm.score_survey_rows(buildings, truth)}
    sv = bm.street_view_buildings(buildings)
    if sv is not None:  # T28 withheld untagged Street View heights: score them too
        result["street_view_unwithheld"] = bm.score_buildings(sv, truth)
    else:  # a pre-T28 report: score what T28 would publish, with each fallback
        result["withheld_simulated"] = bm.score_buildings(
            bm.withheld_buildings(buildings, "model", region), truth)
        result["withheld_simulated_constant"] = bm.score_buildings(
            bm.withheld_buildings(buildings, "constant"), truth)
    # item 1 (review 2026-10-09): band x tier, band x method x distance, pipeline metrics, over
    # the rows the run measured (the headline's set) and over every published row (tag-only
    # rows too: the data product; their truth from the cache only, no reads)
    _, published = bm.load_report(heights, include_unmeasured=True)
    published = bm.label_tiers(published, elevated)
    cache = bm.load_truth_cache(region)
    truth_pub = {**{b["key"]: cache[b["key"]] for b in published if b["key"] in cache}, **truth}
    n_fp = json.loads(Path(heights).read_text(encoding="utf-8")).get("n_building_records")
    kw = {"elevated": elevated, "seeds": bm.seed_positions(region),
          "survey_cache": sh.load_cache(region)}
    result["bench"] = {"measured": bm.bench_tables(buildings, truth, **kw),
                       "published": bm.bench_tables(published, truth_pub, n_footprints=n_fp, **kw)}
    if result["bench"]["measured"]["truth_mode"] == bm.SINGLE_SOURCE:
        # survey-only truth (no 3D Tiles cross-check): scored, but reported apart
        scored, _ = bm.scoring_truth(truth, bm.SINGLE_SOURCE)
        result["single_source"] = bm.score_buildings(
            buildings, scored, pred_field="no_survey_height_m", statuses=("survey_only",))
    known = bm.score_known(heights, region)
    if known:  # published heights (Cartagena: the only truth; 3D Tiles have no buildings there)
        result["known"] = known
    try:  # the report's benchmark page; a plotting failure must not lose the score
        from city2stl.skyline.benchmark_report import write_benchmark_page
        write_benchmark_page(heights.parent, result, buildings, truth)
    except Exception as exc:  # noqa: BLE001
        logging.warning("[bench] benchmark page for %s failed: %s", region, exc)
    return result


def _fmt(v, spec):
    return "-" if v is None else format(v, spec)


def print_table(results: list[dict]) -> None:
    head = (f"{'region':22s} {'scored':>6s} {'truth':>5s} {'disp':>4s} {'MAE':>6s} {'medAE':>6s} "
            f"{'bias':>6s} {'<=25%':>5s} {'100m+ n':>7s} {'100m+ bias':>10s} {'pairs/view':>10s} "
            f"{'pairs/city':>10s}")
    print(head)
    print("-" * len(head))
    for r in results:
        o, tall = r.get("overall", {}), r.get("bands", {}).get("100+m", {})
        rel = r.get("relative", {})
        st = r.get("status", {})
        print(f"{r['region']:22s} {r.get('n_buildings', 0):6d} {o.get('n', 0):5d} "
              f"{st.get('disputed', 0):4d} {_fmt(o.get('mae_m'), '6.1f')} "
              f"{_fmt(o.get('median_ae_m'), '6.1f')} {_fmt(o.get('bias_m'), '+6.1f')} "
              f"{_fmt(o.get('within_25pct'), '5.0%')} {tall.get('n', 0):7d} "
              f"{_fmt(tall.get('bias_m'), '+10.1f')} "
              f"{_fmt(rel.get('per_view', {}).get('pair_order'), '10.0%')} "
              f"{_fmt(rel.get('city', {}).get('pair_order'), '10.0%')}")


def print_tiers(results: list[dict]) -> None:
    """Within 25 % per verification tier (F-SKY26): it should fall in tier order."""
    tiers = ("survey", "verified_2", "tag", "single", "prior", "unlabelled")
    print()
    print(f"{'within 25 % by tier':22s} " + " ".join(f"{t:>14s}" for t in tiers))
    for r in results:
        cells = []
        for t in tiers:
            e = r.get("tiers", {}).get(t)
            cells.append(f"{_fmt(e and e.get('within_25pct'), '5.0%')} (n {e['n']:3d})" if e
                         else f"{'-':>14s}")
        print(f"{r['region']:22s} " + " ".join(f"{c:>14s}" for c in cells))


def print_single_source(results: list[dict]) -> None:
    """The headline table for single-source regions (survey-only truth, no 3D Tiles)."""
    ss = [{**r["single_source"], "region": r["region"]} for r in results if r.get("single_source")]
    if not ss:
        return
    print()
    print("single-source regions (survey-only truth, no 3D Tiles cross-check; scored on "
          "survey_only records; buildings that may postdate the survey left out)")
    print_table(ss)


_CELL = "{n:>4} {mae:>6} {bias:>6} {tol:>5}"


def _cell(e: dict | None) -> str:
    if not e or not e.get("n"):
        return _CELL.format(n="-", mae="", bias="", tol="")
    return _CELL.format(n=e["n"], mae=_fmt(e.get("mae_m"), "6.1f"),
                        bias=_fmt(e.get("bias_m"), "+6.1f"), tol=_fmt(e.get("tol"), "5.0%"))


_TIER_ORDER = ("survey", "verified_2", "corroborated", "tag", "single", "prior", "unlabelled")


def print_band_tier(results: list[dict], key: str = "band_tier",
                    title: str = "review bands", scope: str = "published") -> None:
    """Band x tier, one block per region: n, MAE, bias and tol per cell."""
    for r in results:
        bench = (r.get("bench") or {}).get(scope) or {}
        table = bench.get(key) or {}
        seen = {t for row in table.values() for t in row}
        tiers = [t for t in _TIER_ORDER if t in seen]
        tiers += [*sorted(seen - {*tiers, "all"}), "all"]
        print()
        print(f"{r['region']}: band x tier, {title}, {scope} rows ({bench.get('truth_mode')}, truth "
              f"{bench.get('truth_status')} {bench.get('truth_stat')}; cell: n MAE bias tol)")
        print(f"{'band':8s} " + " ".join(f"{t:>23s}" for t in tiers))
        for band, row in table.items():
            print(f"{band:8s} " + " ".join(f"{_cell(row.get(t)):>23s}" for t in tiers))


def print_band_detail(results: list[dict], scope: str = "published") -> None:
    """Every metric per review band (all tiers together), and the pipeline metrics."""
    for r in results:
        bench = (r.get("bench") or {}).get(scope) or {}
        print()
        print(f"{r['region']}: review bands, all tiers, {scope} rows ({bench.get('truth_mode')}; "
              f"{bench.get('n_scored_truth')} scored, {bench.get('with_roof_stats')} with roof "
              f"stats; excluded {bench.get('excluded')}; truth age {bench.get('truth_age')})")
        print(f"{'band':8s} {'n':>5s} {'MAE':>6s} {'medAE':>6s} {'bias':>6s} {'P90AE':>6s} "
              f"{'tol':>5s} {'>5m':>5s} {'>10m':>5s}")
        for band, row in (bench.get("band_tier") or {}).items():
            e = row.get("all") or {"n": 0}
            print(f"{band:8s} {e['n']:5d} {_fmt(e.get('mae_m'), '6.1f')} "
                  f"{_fmt(e.get('median_ae_m'), '6.1f')} {_fmt(e.get('bias_m'), '+6.1f')} "
                  f"{_fmt(e.get('p90_ae_m'), '6.1f')} {_fmt(e.get('tol'), '5.0%')} "
                  f"{_fmt(e.get('over_5m'), '5.0%')} {_fmt(e.get('over_10m'), '5.0%')}")
        p = bench.get("pipeline") or {}
        cov, fc = p.get("coverage") or {}, p.get("false_corroborated") or {}
        wh = (p.get("withhold") or {}).get("all") or {}
        print(f"  coverage {cov.get('non_prior')}/{cov.get('rows')} rows = "
              f"{_fmt(cov.get('share'), '.1%')} (of {cov.get('footprints') or '-'} footprints "
              f"{_fmt(cov.get('share_of_footprints'), '.1%')}; rows with truth "
              f"{_fmt(cov.get('share_with_truth'), '.1%')}); false-corroborated "
              f"{fc.get('wrong')}/{fc.get('n')} = {_fmt(fc.get('rate'), '.1%')}; withhold "
              f"precision {_fmt(wh.get('precision'), '.1%')} ({wh.get('withheld_wrong')}/"
              f"{wh.get('withheld')}), recall {_fmt(wh.get('recall'), '.1%')} (of "
              f"{wh.get('wrong')} wrong readings)")


def print_band_method(results: list[dict], scope: str = "published") -> None:
    """Band x method x camera-distance band (review bands): n, MAE, bias, tol, and the
    method-matched truth's MAE / tol (max for silhouettes, p70 for floors) where stored."""
    for r in results:
        table = ((r.get("bench") or {}).get(scope) or {}).get("band_method") or {}
        if not table:
            continue
        print()
        print(f"{r['region']}: band x method x distance, {scope} rows "
              f"(cell: n MAE bias tol | matched stat MAE tol)")
        for band in (*bm.REVIEW_LABELS, "all"):
            for m, dists in sorted((table.get(band) or {}).items()):
                for d, e in sorted(dists.items(), key=lambda x: (x[0] == "all", x[0])):
                    mt = e.get("matched")
                    mtxt = (f" | {mt['stat']} {_fmt(mt.get('mae_m'), '6.1f')} "
                            f"{_fmt(mt.get('tol'), '5.0%')}"
                            if mt and mt.get("stat") != "p95" else "")
                    print(f"  {band:7s} {m:11s} {d:9s} {_cell(e)}{mtxt}")


def print_known(region: str, known: dict) -> None:
    """Published heights, tower by tower: the report, the drone seeds, Street View, OSM tag."""
    print()
    print(f"{region}: published heights")
    print(f"{'tower':28s} {'pub':>5s} {'report':>7s} {'source':>10s} {'drone':>6s} {'SV':>6s} "
          f"{'tag':>5s}  match")
    for t in known["towers"]:
        print(f"{t['name'][:28]:28s} {t['published_m']:5.0f} {_fmt(t.get('report_m'), '7.0f')} "
              f"{(t.get('report_source') or '-')[:10]:>10s} {_fmt(t.get('drone_m'), '6.0f')} "
              f"{_fmt(t.get('street_view_m'), '6.0f')} {_fmt(t.get('tag_m'), '5.0f')}  {t['match']}")
    for k in ("report", "drone", "street_view", "osm_tag"):
        s = known[k]
        print(f"  {k:12s} n {s['n']:2d}  MAE {_fmt(s['mae_m'], '6.1f')}  median {_fmt(s['median_ae_m'], '6.1f')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--regions", nargs="*", default=list(bm.REGIONS),
                    help=f"benchmark regions (default: all of {', '.join(bm.REGIONS)})")
    ap.add_argument("--score-only", action="store_true",
                    help="no Street View: score the newest existing report per region")
    ap.add_argument("--report", action="append", default=[], type=Path,
                    help="score this heights.json (repeatable; implies --score-only)")
    ap.add_argument("--no-tiles", action="store_true",
                    help="survey truth only (no 3D Tiles; nothing is 'confirmed'; not cached)")
    ap.add_argument("--refresh-truth", action="store_true",
                    help="re-measure truth for footprints already in the region's truth cache")
    ap.add_argument("--keep-env", action="store_true",
                    help="don't pin PINNED_FLAGS: run with the shell's SKYLINE_* values")
    ap.add_argument("--cached-truth", action="store_true",
                    help="score cached truth only: no survey or 3D Tiles reads")
    ap.add_argument("--tables-out", type=Path, default=None,
                    help="also write the printed tables to this file")
    args = ap.parse_args()
    env = _region_env(args.keep_env)

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    out_dir = bm.BENCHMARK_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.report:
        jobs = [(None, p) for p in args.report]
    elif args.score_only:
        jobs = [(r, _newest_report(r)) for r in args.regions]
    else:
        jobs = [(r, _run_region(r, out_dir, env)) for r in args.regions]

    results = []
    for region, heights in jobs:
        if heights is None:
            results.append({"region": region, "error": "no report"})
            continue
        results.append(score_report(heights, region, use_tiles=not args.no_tiles,
                                    refresh_truth=args.refresh_truth,
                                    cached_truth=args.cached_truth))

    summary = {
        "stamp": stamp, "git": _git_head(),
        # For --score-only these are this process's settings, not the scored reports'.
        "pinned": not args.keep_env, **_run_settings(env),
        "truth_rule": {"percentile": bm.ROOF_PERCENTILE, "erode_m": bm.ERODE_M,
                       "min_cells": bm.MIN_CELLS, "min_finite_fraction": bm.MIN_FINITE_FRACTION,
                       "agree_abs_m": bm.AGREE_ABS_M, "agree_rel": bm.AGREE_REL},
        "results": results,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = [r for r in results if "error" not in r]
        print_table(ok)
        print_tiers(ok)
        for r in results:
            if r.get("known"):
                print_known(r["region"], r["known"])
        print_single_source(ok)
        for scope in ("measured", "published"):
            print_band_detail(ok, scope)
            print_band_tier(ok, scope=scope)
            print_band_tier(ok, "band_tier_eubucco", "EUBUCCO bands", scope)
        print_band_method(ok)
        for r in results:
            if "error" in r:
                print(f"{r['region']}: {r['error']}")
        print(f"\nsummary: {out_dir / 'summary.json'}")
    print(buf.getvalue(), end="")
    if args.tables_out:
        args.tables_out.write_text(buf.getvalue(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
