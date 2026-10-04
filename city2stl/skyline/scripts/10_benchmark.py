#!/usr/bin/env python3
"""F-SKYBENCH: run the skyline pipeline on the benchmark cities and score it on surveyed truth.

    python -m city2stl.skyline.scripts.10_benchmark                       # all regions: run + score
    python -m city2stl.skyline.scripts.10_benchmark --regions miami boston
    python -m city2stl.skyline.scripts.10_benchmark --score-only          # newest report per region
    python -m city2stl.skyline.scripts.10_benchmark --score-only --report path/to/heights.json

Each region runs ``08_region_skyline_pdf`` in its own process (a native crash in one city
must not end the batch), into ``runs/benchmark/<stamp>/``. Truth comes from
``city2stl.skyline.benchmark`` (survey nDSM + 3D Tiles, cross-checked, cached per region).
Writes ``summary.json`` beside the reports, a ``benchmark.html`` page into each scored report
(``benchmark_report.write_benchmark_page``, linked from its index), and prints one table.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from city2stl.skyline import benchmark as bm

ROOT = Path(__file__).resolve().parents[3]
REPORTS = ROOT / "city2stl" / "skyline" / "runs" / "region_reports"


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
                              capture_output=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _run_region(region: str, out_dir: Path) -> Path | None:
    """Run the production pipeline for ``region``; return its heights.json, or None."""
    out_pdf = out_dir / f"{region}_skyline_report.pdf"
    log = out_dir / f"{region}.log"
    cmd = [sys.executable, "-u", "-m", "city2stl.skyline.scripts.08_region_skyline_pdf",
           "--region", region, "--out", str(out_pdf)]
    logging.info("[bench] running %s (log: %s)", region, log)
    with log.open("w", encoding="utf-8") as fh:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"}).returncode
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


def score_report(heights: Path, region: str | None = None, use_tiles: bool = True) -> dict:
    name, buildings = bm.load_report(heights)
    region = region or bm.region_key(name)
    provider = bm.REGIONS.get(region)
    if provider is None:
        logging.warning("[bench] %s is not a benchmark region; scoring with 3D Tiles only",
                        region)
    truth = bm.footprint_truth(region, {b["key"]: b["footprint_lonlat"] for b in buildings},
                               provider, use_tiles=use_tiles)
    result = {"region": region, "report": str(heights), "survey": provider,
              **bm.score_buildings(buildings, truth)}
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--regions", nargs="*", default=list(bm.REGIONS),
                    help=f"benchmark regions (default: all of {', '.join(bm.REGIONS)})")
    ap.add_argument("--score-only", action="store_true",
                    help="no Street View: score the newest existing report per region")
    ap.add_argument("--report", action="append", default=[], type=Path,
                    help="score this heights.json (repeatable; implies --score-only)")
    ap.add_argument("--no-tiles", action="store_true",
                    help="survey truth only (no 3D Tiles; nothing is 'confirmed')")
    args = ap.parse_args()

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    out_dir = bm.BENCHMARK_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.report:
        jobs = [(None, p) for p in args.report]
    elif args.score_only:
        jobs = [(r, _newest_report(r)) for r in args.regions]
    else:
        jobs = [(r, _run_region(r, out_dir)) for r in args.regions]

    results = []
    for region, heights in jobs:
        if heights is None:
            results.append({"region": region, "error": "no report"})
            continue
        results.append(score_report(heights, region, use_tiles=not args.no_tiles))

    summary = {
        "stamp": stamp, "git": _git_head(),
        "env": {k: v for k, v in sorted(os.environ.items()) if k.startswith("SKYLINE_")},
        "truth_rule": {"percentile": bm.ROOF_PERCENTILE, "erode_m": bm.ERODE_M,
                       "agree_abs_m": bm.AGREE_ABS_M, "agree_rel": bm.AGREE_REL},
        "results": results,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print_table([r for r in results if "error" not in r])
    for r in results:
        if "error" in r:
            print(f"{r['region']}: {r['error']}")
    print(f"\nsummary: {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
