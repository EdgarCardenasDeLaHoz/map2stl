#!/usr/bin/env python3
"""End-to-end height-trace diagnostic for one (or all) skyline buildings.

Re-runs the orchestration in `region_pdf.run_region_pdf_report` with a
`HeightTraceRecorder` attached, captures every gate decision of
`estimate_heights_from_registration` for the target building, and writes:

  runs/height_traces/<region>__<feature_id>.json   ← trace events
  runs/height_traces/<region>__<feature_id>.png    ← 1-page diagnostic

PDF rendering is skipped (`--skip-pdf`) so the run completes in ~30 s on cached
images instead of ~120 s for the full report. No new Street View API calls
when `runs/image_cache/` and `runs/seed_resolution_cache.json` are populated
from a prior `08_region_skyline_pdf.py` run.

Phase 1 of docs/glass-roof-height-fix-plan.md — trace ≥3 tall-tagged buildings
before deciding whether Phase 2 monocular depth is justified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from city2stl.skyline_cv.height_trace import HeightTraceRecorder  # noqa: E402
from city2stl.skyline_cv.region_pdf import run_region_pdf_report  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Skyline height-trace diagnostic")
    p.add_argument("--region", required=True, help="Saved region name")
    p.add_argument(
        "--feature-id",
        default=None,
        help="Target building feature_id (e.g. 'b0389'). If omitted, traces all buildings.",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: city2stl/skyline_cv/runs/height_traces)",
    )
    p.add_argument("--api-key", default=None,
                   help="Google Maps API key (env: GOOGLE_MAPS_API_KEY)")
    p.add_argument("--no-render", action="store_true",
                   help="Skip the diagnostic image render; write JSON only")
    p.add_argument(
        "--seed-url",
        action="append",
        default=[],
        help="Extra Street View URL to use as a seed (repeatable)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else ROOT / "city2stl" / "skyline_cv" / "runs" / "height_traces"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    tag = args.feature_id or "all"
    json_path = out_dir / f"{args.region}__{tag}.json"
    png_path = out_dir / f"{args.region}__{tag}.png"

    recorder = HeightTraceRecorder(only_feature_id=args.feature_id)

    # run_region_pdf_report writes the PDF; we suppress that with skip_pdf=True.
    # The output_pdf path is still required by the signature but is unused.
    dummy_pdf = out_dir / "_unused.pdf"
    result = run_region_pdf_report(
        region_name=args.region,
        output_pdf=dummy_pdf,
        seed_urls=list(args.seed_url),
        explicit_api_key=args.api_key,
        trace=recorder,
        skip_pdf=True,
    )

    # Persist events + the orchestration result summary.
    payload = {
        "region": args.region,
        "target_feature_id": args.feature_id,
        "summary": result,
        "stages": list(recorder.feature_ids()),
        "events": recorder.to_json_ready(),
    }
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[trace] {len(recorder.events)} events → {json_path}")
    print(f"[trace] {len(recorder.view_artifacts)} view artefacts retained")

    if not args.no_render:
        try:
            from city2stl.skyline_cv.height_trace_render import (  # noqa: PLC0415
                render_trace_diagnostic,
            )
        except ImportError as exc:
            print(f"[trace] diagnostic render skipped ({exc})")
            return 0
        render_trace_diagnostic(recorder, png_path, target=args.feature_id)
        print(f"[trace] diagnostic image → {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
