#!/usr/bin/env python3
"""Run the complete Cartagena skyline CV demo end-to-end."""

from __future__ import annotations
from city2stl.skyline_cv.pipeline import run_demo

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    result = run_demo(ROOT, site_name="cartagena", include_review_bundle=True)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
