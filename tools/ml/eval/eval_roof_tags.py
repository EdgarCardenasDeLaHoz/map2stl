"""
Evaluate OSM roof tag coverage for several cities.

Usage:
  cd strm2stl
  python -m tools.eval_roof_tags                          # default city list
  python -m tools.eval_roof_tags --cities Amsterdam Prague Vienna Berlin
  python -m tools.eval_roof_tags --cities Barcelona --verbose

For each city the script:
  1. Fetches OSM building polygons via osmnx (or uses cached data).
  2. Reports the percentage of buildings that carry each of the key roof tags.
  3. Shows the distribution of roof:shape values.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import Dict, List

sys.path.insert(0, ".")

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)

_ROOF_TAGS = [
    "roof:shape",
    "roof:height",
    "roof:levels",
    "roof:direction",
    "roof:orientation",
    "roof:colour",
    "roof:material",
]

_DEFAULT_CITIES = [
    "Amsterdam",
    "Vienna",
    "Prague",
    "Berlin",
    "Rotterdam",
]

# Small urban bboxes for each city (north, south, east, west)
_CITY_BBOXES: Dict[str, tuple] = {
    "Amsterdam":  (52.380, 52.355, 4.920, 4.880),
    "Vienna":     (48.215, 48.190, 16.380, 16.340),
    "Prague":     (50.090, 50.065, 14.450, 14.410),
    "Berlin":     (52.535, 52.510, 13.415, 13.375),
    "Rotterdam":  (51.930, 51.905, 4.490, 4.450),
    "Barcelona":  (41.400, 41.375, 2.185, 2.145),
    "Cartagena":  (37.615, 37.590, -0.975, -1.015),
    "Granada":    (37.185, 37.160, -3.590, -3.630),
}


@dataclass
class CityResult:
    name: str
    total_buildings: int = 0
    tag_coverage: Dict[str, int] = field(default_factory=dict)
    shape_distribution: Dict[str, int] = field(default_factory=dict)
    height_sources: Dict[str, int] = field(default_factory=dict)
    error: str = ""


_BUILDING_3D_TAGS = [
    "building:levels", "building:material", "building:colour",
    "height", "min_height", "building:part",
]


def _evaluate_city(city: str, bbox: tuple, verbose: bool = False) -> CityResult:
    """Fetch raw OSM buildings (bypassing fetch.py keep filter) and count tags."""
    result = CityResult(name=city)

    try:
        import osmnx as ox
    except ImportError:
        result.error = "osmnx not installed"
        return result

    north, south, east, west = bbox
    ox_bbox = (west, south, east, north)

    try:
        gdf = ox.features_from_bbox(ox_bbox, tags={"building": True})
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
    except Exception as exc:
        result.error = str(exc)
        return result

    result.total_buildings = len(gdf)
    if result.total_buildings == 0:
        result.error = "no buildings returned"
        return result

    all_check_tags = _ROOF_TAGS + _BUILDING_3D_TAGS

    # Count tag coverage directly on the GeoDataFrame (raw OSM data)
    for tag in all_check_tags:
        if tag in gdf.columns:
            non_null = gdf[tag].dropna()
            non_null = non_null[non_null.astype(str).str.strip().isin(["", "nan"]) == False]  # noqa: E712
            result.tag_coverage[tag] = len(non_null)
        else:
            result.tag_coverage[tag] = 0

    # Shape distribution
    if "roof:shape" in gdf.columns:
        shapes = gdf["roof:shape"].dropna()
        shapes = shapes[shapes.astype(str).str.strip() != ""]
        for shape in shapes.astype(str):
            result.shape_distribution[shape] = result.shape_distribution.get(shape, 0) + 1
    # Count untagged
    n_tagged = result.tag_coverage.get("roof:shape", 0)
    if result.total_buildings > n_tagged:
        result.shape_distribution["(untagged)"] = result.total_buildings - n_tagged

    # Height source analysis (how many have explicit height vs levels vs nothing)
    try:
        from city2stl.heights import _fill_heights
        gdf_h = _fill_heights(gdf.copy(), default_m=10.0, lo=3.0, hi=300.0,
                              levels_col="building:levels")
        if "height_source" in gdf_h.columns:
            from collections import Counter as _C
            result.height_sources = dict(_C(gdf_h["height_source"].astype(str)))
    except Exception:
        pass

    if verbose and n_tagged > 0:
        print(f"  Sample tagged buildings in {city}:")
        tagged_mask = gdf.get("roof:shape", default=None)
        if tagged_mask is not None:
            for _, row in gdf[tagged_mask.notna()].head(5).iterrows():
                vals = {t: row.get(t) for t in all_check_tags
                        if t in gdf.columns and row.get(t) is not None
                        and str(row.get(t)).strip() not in ("", "nan")}
                if vals:
                    print(f"    {vals}")

    return result


def _print_report(results: List[CityResult]) -> None:
    # Roof tag coverage
    print()
    print("=" * 78)
    print(f"  {'City':<12} {'#Bldg':>7}  {'r:shape':>8} {'r:height':>8} "
          f"{'r:levels':>8}  {'height':>7} {'b:levels':>8}")
    print("-" * 78)

    for r in results:
        if r.error:
            print(f"  {r.name:<12} {'ERROR':>7}  {r.error}")
            continue
        n = r.total_buildings

        def _pct(tag):
            c = r.tag_coverage.get(tag, 0)
            return f"{100*c/n:.1f}%" if n else "-"

        print(f"  {r.name:<12} {n:>7,}  {_pct('roof:shape'):>8} {_pct('roof:height'):>8} "
              f"{_pct('roof:levels'):>8}  {_pct('height'):>7} {_pct('building:levels'):>8}")

    # Height source breakdown
    print()
    print("Height source breakdown:")
    for r in results:
        if r.error or not getattr(r, "height_sources", None):
            continue
        src = r.height_sources
        parts = ", ".join(f"{k}: {v}" for k, v in sorted(src.items()))
        print(f"  {r.name:<12}: {parts}")

    # Shape distribution for each city
    print()
    print("roof:shape distribution:")
    for r in results:
        if r.error or not r.shape_distribution:
            continue
        dist = sorted(r.shape_distribution.items(), key=lambda x: -x[1])
        top = ", ".join(f"{k}={v}" for k, v in dist[:8])
        print(f"  {r.name:<14}: {top}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate OSM roof tag coverage across cities."
    )
    parser.add_argument(
        "--cities", nargs="+", metavar="CITY",
        help=f"City names to evaluate. Available: {list(_CITY_BBOXES)}",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print per-city fetch details.",
    )
    args = parser.parse_args(argv)

    city_names = args.cities or _DEFAULT_CITIES
    unknown = [c for c in city_names if c not in _CITY_BBOXES]
    if unknown:
        print(f"Unknown cities: {unknown}")
        print(f"Available: {list(_CITY_BBOXES)}")
        return 1

    results: List[CityResult] = []
    for city in city_names:
        bbox = _CITY_BBOXES[city]
        print(f"Fetching {city}...", end=" ", flush=True)
        r = _evaluate_city(city, bbox, verbose=args.verbose)
        if r.error:
            print(f"WARN: {r.error}")
        else:
            pct = 100 * r.tag_coverage.get("roof:shape", 0) / max(r.total_buildings, 1)
            print(f"OK  {r.total_buildings:,} buildings  ({pct:.1f}% have roof:shape)")
        results.append(r)

    _print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
