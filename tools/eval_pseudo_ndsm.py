"""
tools/eval_pseudo_ndsm.py — Evaluate the quality of building-height rasters
across the ROOF-2 evaluation cities.

For each city this script:
  1. Selects (or seeds) the region via TerrainSession.
  2. Fetches OSM building data.
  3. Fetches building-height rasters from each configured provider.
  4. Computes per-building height coverage statistics: what fraction of each
     building's footprint has a valid (non-NaN) height estimate.
  5. Computes global statistics: mean height, p50/p90, % valid pixels.
  6. Saves per-city summaries and a combined CSV to strm2stl/output/.

Usage
-----
    # from the strm2stl/ directory:
    ..\\..venv\\Scripts\\python.exe -m tools.eval_pseudo_ndsm
    # or:
    ..\\..venv\\Scripts\\python.exe tools/eval_pseudo_ndsm.py

Outputs
-------
  output/eval_pseudo_ndsm_<city>.csv     — per-building height stats
  output/eval_pseudo_ndsm_summary.csv    — city-level summary
"""

from __future__ import annotations

import argparse
import csv
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

import numpy as np  # noqa: E402

# ---------------------------------------------------------------------------
# Evaluation cities (same as seed_eval_regions.py and eval_roof_tags.py)
# ---------------------------------------------------------------------------

EVAL_CITIES: dict[str, dict] = {
    "Amsterdam_eval": {
        "north": 52.380, "south": 52.355, "east": 4.920, "west": 4.880,
    },
    "Vienna_eval": {
        "north": 48.215, "south": 48.190, "east": 16.380, "west": 16.340,
    },
    "Prague_eval": {
        "north": 50.090, "south": 50.065, "east": 14.450, "west": 14.410,
    },
    "Berlin_eval": {
        "north": 52.535, "south": 52.510, "east": 13.415, "west": 13.375,
    },
    "Rotterdam_eval": {
        "north": 51.930, "south": 51.905, "east": 4.490, "west": 4.450,
    },
}

# Height providers to evaluate in ascending priority order
DEFAULT_PROVIDERS = ["wsf3d"]  # lightweight, global; extend to ["wsf3d", "ndsm"] as needed

# ---------------------------------------------------------------------------
# Per-building statistics
# ---------------------------------------------------------------------------


def _building_coverage(
    buildings_geojson: dict,
    height_raster: "np.ndarray",
    north: float,
    south: float,
    east: float,
    west: float,
) -> list[dict]:
    """Return per-building height coverage statistics.

    For each building polygon we rasterize the footprint at the height-raster
    resolution and measure what fraction of pixels inside the footprint have a
    valid (non-NaN) height value.

    Parameters
    ----------
    buildings_geojson : GeoJSON FeatureCollection of building polygons
    height_raster : H×W float32 array (NaN where no data)
    north/south/east/west : bbox bounds

    Returns list of dicts with keys:
      feature_id, osm_height_m, n_footprint_px, n_valid_px, coverage_pct,
      mean_height_m, p50_height_m, p90_height_m
    """
    try:
        from rasterio.transform import from_bounds
        from rasterio.features import rasterize as _rasterize
        from shapely.geometry import shape
    except ImportError:
        print("  ⚠️  rasterio/shapely not available — skipping per-building stats")
        return []

    H, W = height_raster.shape
    transform = from_bounds(west, south, east, north, W, H)
    records = []

    for i, feat in enumerate(buildings_geojson.get("features") or []):
        geom = feat.get("geometry")
        if not geom:
            continue
        props = feat.get("properties") or {}
        osm_h = props.get("height_m")
        try:
            shp = shape(geom)
            mask = _rasterize([(shp, 1)], out_shape=(H, W), transform=transform,
                              fill=0, dtype="uint8")
        except Exception:
            continue

        idx = mask.astype(bool)
        n_fp = int(idx.sum())
        if n_fp == 0:
            continue

        vals = height_raster[idx]
        valid = vals[~np.isnan(vals)]
        n_valid = len(valid)
        coverage_pct = 100.0 * n_valid / n_fp if n_fp else 0.0

        records.append({
            "feature_id": i,
            "osm_height_m": float(osm_h) if osm_h is not None else None,
            "n_footprint_px": n_fp,
            "n_valid_px": n_valid,
            "coverage_pct": round(coverage_pct, 2),
            "mean_height_m": round(float(np.nanmean(vals)), 2) if n_valid > 0 else None,
            "p50_height_m": round(float(np.nanpercentile(vals, 50)), 2) if n_valid > 0 else None,
            "p90_height_m": round(float(np.nanpercentile(vals, 90)), 2) if n_valid > 0 else None,
        })

    return records


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def eval_pseudo_ndsm(
    providers: list[str] | None = None,
    output_dir: Path | None = None,
    start_server: bool = True,
    port: int = 9090,
    verbose: bool = True,
) -> dict[str, dict]:
    """Run pseudo-nDSM evaluation across all eval cities.

    Returns
    -------
    dict mapping city name → summary dict
    """
    if providers is None:
        providers = DEFAULT_PROVIDERS
    if output_dir is None:
        output_dir = _STRM2STL / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    from app.session.terrain_session import TerrainSession

    session = TerrainSession(port=port)
    if start_server:
        session.start()

    city_summaries: list[dict] = []
    results: dict[str, dict] = {}

    for name, cfg in EVAL_CITIES.items():
        if verbose:
            print(f"\n{'─' * 60}")
            print(f"  {name}")
            print(f"{'─' * 60}")

        summary: dict = {
            "city": name,
            "n_buildings": 0,
            "n_with_coverage": 0,
            "mean_coverage_pct": 0.0,
            "mean_height_m": None,
            "p50_height_m": None,
            "p90_height_m": None,
            "n_valid_pixels": 0,
            "total_pixels": 0,
            "global_coverage_pct": 0.0,
            "providers": ",".join(providers),
            "error": None,
        }

        try:
            # Create / select region
            try:
                session.create_region(
                    name=name,
                    north=cfg["north"],
                    south=cfg["south"],
                    east=cfg["east"],
                    west=cfg["west"],
                )
            except Exception:
                session.select(name)

            # Fetch OSM data
            t0 = time.perf_counter()
            session.fetch_cities()
            if session.city_data is None:
                summary["error"] = "city_data unavailable"
                results[name] = summary
                continue
            n_buildings = len(session.city_data.get("buildings", {}).get("features", []))
            summary["n_buildings"] = n_buildings
            if verbose:
                print(f"  Buildings: {n_buildings} (OSM in {time.perf_counter()-t0:.1f}s)")

            # Fetch building heights
            t0 = time.perf_counter()
            session.fetch_building_heights(providers=providers)
            dt = time.perf_counter() - t0
            if session.building_heights is None:
                summary["error"] = "building_heights unavailable"
                results[name] = summary
                continue

            hraster = session.building_heights.raster  # H×W float32
            if verbose:
                print(f"  Heights fetched in {dt:.1f}s, shape={hraster.shape}")

            # Global raster stats
            total_px = hraster.size
            valid_mask = ~np.isnan(hraster)
            n_valid = int(valid_mask.sum())
            global_cov = 100.0 * n_valid / total_px if total_px else 0.0
            summary["n_valid_pixels"] = n_valid
            summary["total_pixels"] = total_px
            summary["global_coverage_pct"] = round(global_cov, 2)
            if n_valid > 0:
                summary["mean_height_m"] = round(float(np.nanmean(hraster)), 2)
                summary["p50_height_m"] = round(float(np.nanpercentile(hraster, 50)), 2)
                summary["p90_height_m"] = round(float(np.nanpercentile(hraster, 90)), 2)

            if verbose:
                print(f"  Global coverage: {global_cov:.1f}%  "
                      f"mean={summary['mean_height_m']}m  "
                      f"p50={summary['p50_height_m']}m")

            # Per-building stats
            records = _building_coverage(
                session.city_data["buildings"],
                hraster,
                cfg["north"], cfg["south"], cfg["east"], cfg["west"],
            )

            if records:
                per_bldg_csv = output_dir / f"eval_pseudo_ndsm_{name}.csv"
                _fields = list(records[0].keys())
                with open(per_bldg_csv, "w", newline="", encoding="utf-8") as fh:
                    writer = csv.DictWriter(fh, fieldnames=_fields)
                    writer.writeheader()
                    writer.writerows(records)
                if verbose:
                    print(f"  Per-building CSV: {per_bldg_csv}")

                covered = [r for r in records if r["n_valid_px"] > 0]
                summary["n_with_coverage"] = len(covered)
                if covered:
                    summary["mean_coverage_pct"] = round(
                        sum(r["coverage_pct"] for r in covered) / len(covered), 2
                    )

        except Exception as exc:
            summary["error"] = str(exc)
            if verbose:
                print(f"  ERROR: {exc}")

        results[name] = summary
        city_summaries.append(summary)

    # Combined summary CSV
    if city_summaries:
        summary_csv = output_dir / "eval_pseudo_ndsm_summary.csv"
        fields = list(city_summaries[0].keys())
        with open(summary_csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(city_summaries)
        if verbose:
            print(f"\n{'═' * 60}")
            print(f"  Summary written to {summary_csv}")
            print(f"{'═' * 60}")
            header = (
                f"  {'city':<22}  {'n_bldg':>7}  {'cov%':>6}  "
                f"{'global%':>8}  {'mean_h':>7}  note"
            )
            print(header)
            for s in city_summaries:
                print(
                    f"  {s['city']:<22}  "
                    f"{s['n_buildings']:>7}  "
                    f"{s['mean_coverage_pct']:>6.1f}  "
                    f"{s['global_coverage_pct']:>8.1f}  "
                    f"{(s['mean_height_m'] or 0):>7.1f}  "
                    f"{s.get('error') or ''}"
                )

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate pseudo-nDSM building-height rasters for ROOF-2 eval cities."
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        default=DEFAULT_PROVIDERS,
        metavar="NAME",
        help=f"Height providers to use (default: {DEFAULT_PROVIDERS}). "
             "Options: wsf3d ndsm copernicus lidar_3dep ghsl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Directory for CSV output (default: strm2stl/output/).",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Assume server is already running.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9090,
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    eval_pseudo_ndsm(
        providers=args.providers,
        output_dir=args.output_dir,
        start_server=not args.no_server,
        port=args.port,
        verbose=not args.quiet,
    )
