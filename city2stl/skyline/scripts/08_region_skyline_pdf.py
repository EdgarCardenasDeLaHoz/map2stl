#!/usr/bin/env python3
"""Generate a single PDF skyline report tied to a saved region."""

from __future__ import annotations
from city2stl.skyline.region_pdf import run_region_pdf_report

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Region skyline PDF report")
    parser.add_argument("--region", required=True,
                        help="Saved region name from /api/regions")
    parser.add_argument(
        "--out",
        default=None,
        help="Output PDF path (defaults to city2stl/skyline/runs/region_reports/<region>_skyline_report.pdf)",
    )
    parser.add_argument(
        "--seed-url",
        action="append",
        default=[],
        help="Google Street View URL to force-include as a seed location (repeatable)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Google Maps API key (optional if GOOGLE_MAPS_API_KEY env var is set)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    out = Path(args.out) if args.out else (
        ROOT / "city2stl" / "skyline" / "runs" /
        "region_reports" / f"{args.region}_skyline_report.pdf"
    )
    result = run_region_pdf_report(
        region_name=args.region,
        output_pdf=out,
        seed_urls=list(args.seed_url),
        explicit_api_key=args.api_key,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
