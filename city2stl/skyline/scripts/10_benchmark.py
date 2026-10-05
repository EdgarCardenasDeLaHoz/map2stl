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

Every flag that changes the heights is pinned to ``PINNED_FLAGS`` in each region's process (the
values the 2026-10-04 baseline ran with), so a developer's shell cannot leak into a run;
``--keep-env`` lets the shell's values through, for trying a flag. ``summary.json`` records the
flags each region ran with and whether Street View signing was on (it changes the image size).
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

#: The flags that change the heights, at the values the 2026-10-04 baseline ran with
#: (production SegFormer b1 at 512 px, tag filter on). Report-only flags
#: (SKYLINE_CV_HTML_*, _PANO_LAYERED, _F_SKY13_SAT_BG) and speed-only ones
#: (SKYLINE_CV_SEGFORMER_DEVICE, _BATCH: bit-identical output) are left to the shell.
PINNED_FLAGS = {
    "SKYLINE_CV_SEGFORMER_SIZE": "b1",
    "SKYLINE_CV_SEGFORMER_INPUT_SIZE": "512",
    "SKYLINE_TAG_FILTER": "1",
    "SKYLINE_SV_TALL_FRAME": "0",
    "SKYLINE_CV_MULTIRES": "0",
    "SKYLINE_CV_PHASE_C": "0",
    "SKYLINE_CV_F_SKY1": "1",
    "SKYLINE_CV_F_SKY5": "0",
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
                 refresh_truth: bool = False) -> dict:
    name, buildings = bm.load_report(heights)
    region = region or bm.region_key(name)
    provider = bm.REGIONS.get(region)
    if provider is None:
        logging.warning("[bench] %s is not a benchmark region; scoring with 3D Tiles only",
                        region)
    truth = bm.footprint_truth(region, {b["key"]: b["footprint_lonlat"] for b in buildings},
                               provider, use_tiles=use_tiles, refresh=refresh_truth)
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
                    help="survey truth only (no 3D Tiles; nothing is 'confirmed'; not cached)")
    ap.add_argument("--refresh-truth", action="store_true",
                    help="re-measure truth for footprints already in the region's truth cache")
    ap.add_argument("--keep-env", action="store_true",
                    help="don't pin PINNED_FLAGS: run with the shell's SKYLINE_* values")
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
                                    refresh_truth=args.refresh_truth))

    summary = {
        "stamp": stamp, "git": _git_head(),
        # For --score-only these are this process's settings, not the scored reports'.
        "pinned": not args.keep_env, **_run_settings(env),
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
