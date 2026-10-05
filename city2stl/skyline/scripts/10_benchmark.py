"""10_benchmark.py - skyline heights scored against surveyed truth (F-SKYBENCH).

    python -m city2stl.skyline.scripts.10_benchmark [--regions miami chicago ...]
        [--score-only] [--refresh-truth] [--allow-single-source]

Per region:
  1. run the skyline pipeline (unless ``--score-only``) with the ``SKYLINE_CV_*``
     flags pinned to ``PINNED_FLAGS`` -> ``runs/region_reports/<region>_skyline_report/heights.json``;
  2. truth per footprint, cached at ``map2stl/runs/benchmark/<region>_truth.json``
     (built from OSM footprints + the region's survey + Google 3D Tiles when missing
     or ``--refresh-truth``; ``--score-only`` never builds truth);
  3. score (``benchmark.score_region``).

Writes ``map2stl/runs/benchmark/<date>/summary.json`` (results, flags, git hash)
and prints one table. Needs a Google API key for steps 1 and 2, none for
``--score-only`` on cached truth.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The benchmark's flag set (today's defaults), written into the environment
#: before the pipeline is imported so a developer's shell cannot leak into a run.
PINNED_FLAGS = {
    "SKYLINE_CV_F_SKY12": "0",
    "SKYLINE_CV_F_SKY13": "1",
    "SKYLINE_CV_PHASE_C": "0",
    "SKYLINE_CV_F_SKY13_SAT_BG": "0",
    "SKYLINE_CV_F_SKY5": "0",
    "SKYLINE_CV_F_SKY11_1": "0",
    "SKYLINE_CV_F_SKY1": "1",
    "SKYLINE_REFRESH_PROPOSALS": "0",
}

REPORTS_DIR = ROOT / "city2stl" / "skyline" / "runs" / "region_reports"


def _git_hash() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _report_pdf(region: str) -> Path:
    return REPORTS_DIR / f"{region}_skyline_report.pdf"


def _heights_path(region: str) -> Path:
    pdf = _report_pdf(region)
    return pdf.parent / pdf.stem / "heights.json"


def _build_truth(region: str, provider: str):
    from city2stl.skyline import benchmark
    from city2stl.skyline.region_data import (
        _load_osm_for_region,
        _load_region_bbox,
        _osm_to_building_records,
    )
    bbox = _load_region_bbox(region)
    osm_data, _src = _load_osm_for_region(bbox)
    footprints = [(r.feature_id, r.geometry) for r in _osm_to_building_records(osm_data)]
    tiles = benchmark.tiles_fetcher()
    if tiles is None:
        logging.warning("[benchmark] no Google API key: truth for %s is survey-only", region)
    doc = benchmark.build_truth_doc(
        region, (bbox.north, bbox.south, bbox.east, bbox.west), footprints,
        provider=provider, survey_fetch=benchmark.survey_fetcher(provider), tiles_fetch=tiles)
    path = benchmark.save_truth(doc)
    logging.info("[benchmark] %s truth: %s -> %s", region, doc["counts"], path)
    return doc


def build_parser() -> argparse.ArgumentParser:
    from city2stl.skyline.benchmark import BENCHMARK_REGIONS
    p = argparse.ArgumentParser(description="Skyline height benchmark on surveyed truth")
    p.add_argument("--regions", nargs="+", default=list(BENCHMARK_REGIONS),
                   help="subset of: " + ", ".join(BENCHMARK_REGIONS))
    p.add_argument("--score-only", action="store_true",
                   help="re-score existing heights.json against cached truth (no network)")
    p.add_argument("--refresh-truth", action="store_true", help="rebuild cached truth")
    p.add_argument("--allow-single-source", action="store_true",
                   help="also score survey_only / tiles_only truth rows")
    p.add_argument("--keep-env", action="store_true",
                   help="do not pin SKYLINE_CV_* flags (exploring a flag change)")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not args.keep_env:
        os.environ.update(PINNED_FLAGS)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from city2stl.skyline import benchmark
    unknown = [r for r in args.regions if r not in benchmark.BENCHMARK_REGIONS]
    if unknown:
        raise SystemExit(f"unknown regions: {', '.join(unknown)}")
    statuses = ("confirmed",) + (("survey_only", "tiles_only") if args.allow_single_source else ())

    results, skipped = [], {}
    for region in args.regions:
        if not args.score_only:
            # Through the 08 script, whose import pins the native thread pools
            # (OMP / pyarrow / torch) that a region run needs to stay up.
            run = importlib.import_module(
                "city2stl.skyline.scripts.08_region_skyline_pdf").run_region_pdf_report
            run(region, _report_pdf(region), skip_pdf=True)
        heights = _heights_path(region)
        if not heights.exists():
            skipped[region] = f"no {heights.relative_to(ROOT)}"
            continue
        truth = None if args.refresh_truth else benchmark.load_truth(region)
        if truth is None:
            if args.score_only:
                skipped[region] = "no cached truth (run without --score-only first)"
                continue
            truth = _build_truth(region, benchmark.BENCHMARK_REGIONS[region])
        results.append(benchmark.score_region(
            json.loads(heights.read_text(encoding="utf-8")), truth, statuses=statuses))

    out = benchmark.RUNS_DIR / date.today().isoformat() / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "git": _git_hash(),
        "flags": {k: os.environ.get(k) for k in PINNED_FLAGS},
        "statuses": list(statuses),
        "score_only": args.score_only,
        "skipped": skipped,
        "results": results,
    }, indent=2), encoding="utf-8")

    print(benchmark.format_table(results))
    for region, why in skipped.items():
        print(f"skipped {region}: {why}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
