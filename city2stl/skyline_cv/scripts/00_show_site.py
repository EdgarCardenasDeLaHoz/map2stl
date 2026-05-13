#!/usr/bin/env python3
"""Print the bundled Cartagena site definition and write site.json."""

from __future__ import annotations
from city2stl.skyline_cv.pipeline import RunSpec, _load_site_config, stage_site

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    site = _load_site_config("cartagena")
    run = RunSpec(site=site, root_dir=ROOT)
    payload = stage_site(run)
    print(json.dumps(payload, indent=2))
    print(f"Wrote {run.output_dir / 'site.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
