"""
tools/seed_eval_regions.py — Create 5 ROOF-2 evaluation cities in the region
database and pre-populate their caches so the city height rasters are
immediately visible in the frontend.

What this script does for each city
------------------------------------
1. Creates the region in the SQLite database (skips if already present).
2. Calls POST /api/cities  — fetches and caches OSM building/road/waterway data.
3. Calls POST /api/composite/city-raster  — caches the city raster (frontend-visible).
4. Calls POST /api/height/fetch  — caches the building-height raster.

Usage
-----
    # from the strm2stl/ directory:
    ..\\..venv\\Scripts\\python.exe -m tools.seed_eval_regions
    # or from the repo root:
    .venv\\Scripts\\python.exe strm2stl/tools/seed_eval_regions.py

The script starts the FastAPI server automatically if it is not already running.
Pass --no-server to skip server management (server must already be running on
port 9090).

Pass --dry-run to print what would be inserted/fetched without making any
changes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_STRM2STL = _HERE.parent
_REPO_ROOT = _STRM2STL.parent
for _p in (_STRM2STL, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Evaluation city definitions
#
# Bounding boxes are small urban patches (~3 km wide) chosen for high OSM
# roof-tag coverage and variety of roof shapes.  The same bboxes are used
# by tools/eval_roof_tags.py so results are directly comparable.
# ---------------------------------------------------------------------------

EVAL_CITIES: dict[str, dict] = {
    "Amsterdam_eval": {
        "north": 52.380, "south": 52.355, "east": 4.920, "west": 4.880,
        "description": "ROOF-2 evaluation — Amsterdam historic centre",
        "source": "roof2_eval",
    },
    "Vienna_eval": {
        "north": 48.215, "south": 48.190, "east": 16.380, "west": 16.340,
        "description": "ROOF-2 evaluation — Vienna Innere Stadt",
        "source": "roof2_eval",
    },
    "Prague_eval": {
        "north": 50.090, "south": 50.065, "east": 14.450, "west": 14.410,
        "description": "ROOF-2 evaluation — Prague Old Town",
        "source": "roof2_eval",
    },
    "Berlin_eval": {
        "north": 52.535, "south": 52.510, "east": 13.415, "west": 13.375,
        "description": "ROOF-2 evaluation — Berlin Mitte",
        "source": "roof2_eval",
    },
    "Rotterdam_eval": {
        "north": 51.930, "south": 51.905, "east": 4.490, "west": 4.450,
        "description": "ROOF-2 evaluation — Rotterdam city centre",
        "source": "roof2_eval",
    },
}


# ---------------------------------------------------------------------------
# Main seeding logic
# ---------------------------------------------------------------------------

def seed_regions(
    dry_run: bool = False,
    start_server: bool = True,
    port: int = 9090,
    verbose: bool = True,
) -> dict[str, dict]:
    """Seed the five evaluation cities.

    Parameters
    ----------
    dry_run : bool
        If True, print what would be done but make no HTTP calls or DB writes.
    start_server : bool
        If True, start the FastAPI server if not already running.
    port : int
        Server port (default 9090).
    verbose : bool
        Print progress messages.

    Returns
    -------
    dict mapping city name → {
        "region_created": bool,
        "osm_cached": bool,
        "city_raster_cached": bool,
        "heights_cached": bool,
        "error": str | None,
    }
    """
    if dry_run:
        print("[dry-run] Would seed the following regions:")
        for name, cfg in EVAL_CITIES.items():
            print(f"  {name}: N={cfg['north']} S={cfg['south']} "
                  f"E={cfg['east']} W={cfg['west']}")
        return {}

    from app.session.terrain_session import TerrainSession

    session = TerrainSession(port=port)

    if start_server:
        session.start()
    else:
        # Check server is reachable
        if not session._wait_for_server_ready(max_attempts=3):
            raise RuntimeError(
                f"Server not reachable on port {port}. "
                "Start it first or omit --no-server."
            )

    results: dict[str, dict] = {}

    for name, cfg in EVAL_CITIES.items():
        if verbose:
            print(f"\n{'─' * 60}")
            print(f"  Seeding: {name}")
            print(f"{'─' * 60}")

        result: dict = {
            "region_created": False,
            "osm_cached": False,
            "city_raster_cached": False,
            "heights_cached": False,
            "error": None,
        }

        try:
            # ── 1. Create region (or select if already exists) ──────────
            try:
                session.create_region(
                    name=name,
                    north=cfg["north"],
                    south=cfg["south"],
                    east=cfg["east"],
                    west=cfg["west"],
                    description=cfg.get("description"),
                    source=cfg.get("source"),
                )
                result["region_created"] = True
            except Exception as exc:
                # Region may already exist — attempt to select it
                if verbose:
                    print(f"  create_region failed ({exc}); trying select()")
                try:
                    session.select(name)
                    if verbose:
                        print(f"  Region '{name}' already exists — selected.")
                except Exception as sel_exc:
                    raise RuntimeError(
                        f"Could not create or select region '{name}': {sel_exc}"
                    ) from sel_exc

            # ── 2. Fetch and cache OSM data ────────────────────────────
            t0 = time.perf_counter()
            session.fetch_cities()
            dt = time.perf_counter() - t0
            if session.city_data is not None:
                result["osm_cached"] = True
                if verbose:
                    print(f"  OSM cached in {dt:.1f}s")
            else:
                if verbose:
                    print(f"  OSM skipped (bbox too large or server error)")

            # ── 3. Cache city raster (frontend-visible) ─────────────────
            if session.city_data is not None:
                t0 = time.perf_counter()
                session.composite_city_raster()
                dt = time.perf_counter() - t0
                if session.city_raster is not None:
                    result["city_raster_cached"] = True
                    if verbose:
                        print(f"  City raster cached in {dt:.1f}s")

            # ── 4. Cache building-height raster ──────────────────────────
            t0 = time.perf_counter()
            try:
                session.fetch_building_heights()
                dt = time.perf_counter() - t0
                if session.building_heights is not None:
                    result["heights_cached"] = True
                    if verbose:
                        print(f"  Building heights cached in {dt:.1f}s")
            except AttributeError:
                # fetch_building_heights may not exist in all versions
                if verbose:
                    print("  fetch_building_heights() not available — skipping height cache")
            except Exception as hgt_exc:
                if verbose:
                    print(f"  Height fetch skipped: {hgt_exc}")

        except Exception as exc:
            result["error"] = str(exc)
            if verbose:
                print(f"  ERROR for {name}: {exc}")

        results[name] = result

    # ── Summary ──────────────────────────────────────────────────────────
    if verbose:
        print(f"\n{'═' * 60}")
        print("  Seed summary")
        print(f"{'═' * 60}")
        header = f"  {'name':<22}  {'region':>7}  {'osm':>5}  {'raster':>7}  {'heights':>8}  note"
        print(header)
        for name, r in results.items():
            note = r.get("error") or ""
            print(
                f"  {name:<22}  "
                f"{'✓' if r['region_created'] else '~':>7}  "
                f"{'✓' if r['osm_cached'] else '✗':>5}  "
                f"{'✓' if r['city_raster_cached'] else '✗':>7}  "
                f"{'✓' if r['heights_cached'] else '✗':>8}  "
                f"{note}"
            )

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed ROOF-2 evaluation cities into the region database and cache."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any changes.",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Do not start the server; assume it is already running.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9090,
        help="Server port (default: 9090).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    seed_regions(
        dry_run=args.dry_run,
        start_server=not args.no_server,
        port=args.port,
        verbose=not args.quiet,
    )
