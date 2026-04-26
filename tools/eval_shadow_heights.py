"""
Evaluate shadow-based building height estimation for multiple cities.

Usage:
  cd strm2stl
  python -m tools.eval_shadow_heights                   # all cities, offline (synthetic)
  python -m tools.eval_shadow_heights --live             # fetch real satellite imagery
  python -m tools.eval_shadow_heights --cities Barcelona Cartagena
  python -m tools.eval_shadow_heights --live --dim 200   # higher DEM grid resolution

Each city gets a small bbox centred on its urban core. The script:
  1. Creates a synthetic RGB image with known shadow geometry (offline mode)
     OR fetches real satellite tiles via ESRI World Imagery (--live mode).
  2. Runs the ShadowHeightProvider inference pipeline.
  3. Reports: #components detected, heights, pixel scale, sun elevation.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from typing import List

import numpy as np

# Ensure project root is importable
sys.path.insert(0, ".")

from app.server.core.height.providers.shadow_height import (
    ShadowHeightProvider,
    _infer_from_rgb,
    _detect_shadows,
    _estimate_sun_elevation,
    _fetch_rgb_for_bbox,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


@dataclass
class CityEval:
    name: str
    north: float
    south: float
    east: float
    west: float


CITIES: List[CityEval] = [
    CityEval("Barcelona",    41.410, 41.370, 2.200, 2.140),   # Eixample district
    CityEval("Granada",      37.185, 37.165, -3.590, -3.620), # City centre
    CityEval("Cartagena",    10.430, 10.400, -75.520, -75.555),# Walled City
    CityEval("Philadelphia", 39.960, 39.940, -75.150, -75.180),# Center City
    CityEval("Cape Town",   -33.915, -33.935, 18.430, 18.400),# CBD
    CityEval("Tokyo",        35.690, 35.670, 139.760, 139.730),# Shinjuku
    CityEval("Dubai",        25.210, 25.185, 55.280, 55.255),  # Downtown
    CityEval("São Paulo",   -23.545, -23.570, -46.615, -46.650),# Paulista
]


def _make_synthetic_rgb(h: int, w: int, n_buildings: int = 12) -> np.ndarray:
    """Generate a synthetic satellite image with pseudo-building shadows."""
    rng = np.random.RandomState(42)
    rgb = rng.randint(140, 200, (h, w, 3), dtype=np.uint8)

    for _ in range(n_buildings):
        r = rng.randint(20, h - 40)
        c = rng.randint(20, w - 40)
        sh = rng.randint(5, 25)   # shadow length (pixels)
        sw = rng.randint(3, 10)   # shadow width
        r1, r2 = r, min(r + sh, h)
        c1, c2 = c, min(c + sw, w)
        rgb[r1:r2, c1:c2, :] = rng.randint(20, 50)  # dark shadow

    return rgb


def evaluate_city(city: CityEval, dim: int, live: bool) -> dict:
    bbox = (city.north, city.south, city.east, city.west)
    lat_span = city.north - city.south
    bbox_m = lat_span * 111_320.0
    sun_elev = _estimate_sun_elevation(
        (city.north + city.south) / 2,
        (city.east + city.west) / 2,
    )

    if live:
        rgb = _fetch_rgb_for_bbox(bbox, (dim, dim))
        if rgb is None:
            return {"city": city.name, "error": "satellite fetch failed"}
        source = f"ESRI satellite ({rgb.shape[1]}x{rgb.shape[0]})"
    else:
        # Synthetic at ~2 m/pixel
        sat_px = max(256, min(2048, int(bbox_m / 2.0)))
        rgb = _make_synthetic_rgb(sat_px, sat_px)
        source = f"synthetic ({rgb.shape[1]}x{rgb.shape[0]})"

    pixel_m = bbox_m / rgb.shape[0]
    shadow_mask = _detect_shadows(rgb)
    n_shadow_px = int(shadow_mask.sum())

    result = _infer_from_rgb(rgb, bbox, (dim, dim))
    non_nan = ~np.isnan(result.raster)
    n_estimates = int(non_nan.sum())
    heights = result.raster[non_nan].tolist() if n_estimates > 0 else []

    return {
        "city": city.name,
        "bbox_m": f"{bbox_m:.0f}",
        "pixel_m": f"{pixel_m:.2f}",
        "sun_elev": f"{sun_elev:.1f}",
        "source": source,
        "shadow_px": n_shadow_px,
        "shadow_pct": f"{100 * n_shadow_px / (rgb.shape[0] * rgb.shape[1]):.1f}%",
        "n_estimates": n_estimates,
        "heights_m": [f"{h:.1f}" for h in sorted(heights)],
        "mean_height": f"{np.mean(heights):.1f}" if heights else "-",
        "median_height": f"{np.median(heights):.1f}" if heights else "-",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="Fetch real satellite imagery instead of synthetic")
    parser.add_argument("--dim", type=int, default=100,
                        help="Output DEM grid size (default: 100)")
    parser.add_argument("--cities", nargs="+",
                        help="Subset of cities to evaluate")
    args = parser.parse_args()

    cities = CITIES
    if args.cities:
        names = {c.lower() for c in args.cities}
        cities = [c for c in CITIES if c.name.lower() in names]
        if not cities:
            print(f"No matching cities. Available: {[c.name for c in CITIES]}")
            return

    print(f"{'City':<15} {'Bbox(m)':>8} {'px/m':>6} {'Sun°':>5} "
          f"{'Shadow%':>8} {'#Est':>5} {'Mean(m)':>8} {'Median':>8}  Source")
    print("-" * 95)

    for city in cities:
        r = evaluate_city(city, args.dim, args.live)
        if "error" in r:
            print(f"{r['city']:<15} ERROR: {r['error']}")
            continue
        print(f"{r['city']:<15} {r['bbox_m']:>8} {r['pixel_m']:>6} {r['sun_elev']:>5} "
              f"{r['shadow_pct']:>8} {r['n_estimates']:>5} {r['mean_height']:>8} "
              f"{r['median_height']:>8}  {r['source']}")

    print()
    if not args.live:
        print("(Synthetic mode — use --live to fetch real satellite imagery)")


if __name__ == "__main__":
    main()
