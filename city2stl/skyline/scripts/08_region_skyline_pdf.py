#!/usr/bin/env python3
"""Generate a single PDF skyline report tied to a saved region."""

from __future__ import annotations

import faulthandler
import logging
import os

# Pin the native math thread pools before anything imports torch. A region run
# alternates SegFormer and Depth-Anything inference once per seed, and on
# Windows a seed's session intermittently segfaults (exit 139) immediately
# after "capture pano views", while the model runs. Pinning the OpenMP/MKL
# pools to one thread makes it much rarer but has not eliminated it — the crash
# is nondeterministic, so a run may still need retrying. These are set only
# when the caller has not chosen a value, so an operator who wants more threads
# still can.
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
             "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# Dump a native-level traceback if the process does die, so the next occurrence
# names the frame instead of leaving only "exit code 139" in the log. This paid
# for itself immediately: it placed one crash in pyarrow's string builder under
# osmnx, not in torch where the timing had implied.
faulthandler.enable()

import pyarrow  # noqa: E402

# pyarrow keeps thread pools of its own, independent of OMP_NUM_THREADS. A
# Miami run died with a Windows access violation inside
# ``ArrowStringArray._from_sequence`` while geopandas built the OSM tag columns,
# and the same fetch then succeeded on a retry — a nondeterministic native
# crash, which is what an unsynchronised pool looks like. Pinning is cheap and
# these pools buy nothing here: the fetch is network-bound.
pyarrow.set_cpu_count(1)
pyarrow.set_io_thread_count(1)

import torch  # noqa: E402

# The env vars above cover the intra-op (OpenMP) pool. torch's inter-op pool is
# separate and is sized from the CPU count regardless, so pin it explicitly.
torch.set_num_threads(1)
try:
    torch.set_num_interop_threads(1)
except RuntimeError:
    # Already fixed by an earlier parallel region; not fatal.
    pass

import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

from city2stl.skyline.region_pdf import run_region_pdf_report  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


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
    from city2stl.resources import wait_for_ram

    wait_for_ram()                              # a region run is a heavy job (CLAUDE.md)
    result = run_region_pdf_report(
        region_name=args.region,
        output_pdf=out,
        seed_urls=list(args.seed_url),
        explicit_api_key=args.api_key,
    )
    print(result)
    return 0


if __name__ == "__main__":
    # Library progress (skyline._pano, region_pdf) goes through logging.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
