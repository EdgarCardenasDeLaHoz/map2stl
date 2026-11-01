#!/usr/bin/env python3
"""F-WEB2: a region's building heights from its Commons skyline photos, end to end.

    python -m city2stl.skyline.scripts.17_photo_pipeline --region miami --report <report dir> [--workers 8]

Thin wrapper: the routes, gates and outputs are documented in
``city2stl.skyline.commons_pipeline.pipeline`` and every flag is a ``PipelineArgs`` field.
"""

from __future__ import annotations

import argparse
import json
import logging

from city2stl.skyline.commons_pipeline.pipeline import (
    PipelineArgs,
    add_arguments,
    one_thread_per_worker,
    run_pipeline,
)

one_thread_per_worker()                 # before the pool starts: one BLAS thread per worker


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_arguments(ap)
    summary = run_pipeline(PipelineArgs(**vars(ap.parse_args())))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
