#!/usr/bin/env python3
"""Apply edits from a skyline review bundle and write a corrected summary."""

from __future__ import annotations

import argparse
from pathlib import Path

from city2stl.skyline_cv.review import apply_review_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply skyline review edits")
    parser.add_argument(
        "bundle",
        nargs="?",
        default=None,
        help="Path to review_bundle.json (defaults to city2stl/skyline_cv/runs/cartagena/06_review/review_bundle.json)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.bundle:
        bundle_path = Path(args.bundle)
    else:
        bundle_path = Path(__file__).resolve(
        ).parents[3] / "runs" / "cartagena" / "06_review" / "review_bundle.json"

    result = apply_review_bundle(bundle_path)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
