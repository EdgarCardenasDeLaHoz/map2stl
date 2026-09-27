"""Evaluation harness for the micropolitan city set (raster vs polygon registration).

Prepares the 9 extracted micropolitan models for head-to-head evaluation.  These
cities are NOT in `_CITY_CONFIG`, so each needs a scale anchor (`tallest_m`) to
size the OSM fetch; the centre is geocoded automatically from the region name.
Several (Paris, Prague, Salzburg, Lisbon) are radial / non-grid — the cases where
polygon matching is expected to help most.

Usage
-----
    # list what's prepared (no network, no registration) — verifies paths load:
    python -m city2stl.registration.scripts.benchmark_micropolitan --check
    # run the full raster-vs-polygon benchmark (network; ~minutes per city):
    python -m city2stl.registration.scripts.benchmark_micropolitan [--city Paris] [--size L]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path("c:/Users/eac84/OneDrive/Documents/Projects/3D Maps/Cities/micropolitan/_extracted")

# region name (for OSM geocode) + approximate tallest-building height (m), the scale
# anchor that sizes the OSM bbox.  These are landmark heights — refine per model if a
# city misregisters (the registration SCALE is geometric 1/osm_margin; tallest_m only
# sets how much OSM area to fetch).
CITIES = {
    "Barcelona": ("Barcelona, Spain",        "Barcelona,_Spain_-_S,_M,_L,_&_XL",        144.0),
    "Bilbao":    ("Bilbao, Spain",           "Bilbao,_Spain_-_S,_M,_L,_&_XL",           165.0),
    "Lisbon":    ("Lisbon, Portugal",        "Lisbon,_Portugal_-_S,_M,_L,_&_XL",        145.0),
    "Miami":     ("Miami, FL, USA",          "Miami,_FL_-_L_&_XL",                      256.0),
    "Paris":     ("Paris, France",           "Paris,_France_-_S,_M,_L,_&_XL",           210.0),
    "Prague":    ("Prague, Czech Republic",  "Prague,_Czech_Republic_-_S,_M,_L,_&_XL",  109.0),
    "Salzburg":  ("Salzburg, Austria",       "Salzburg,_Austria_-_S,_M,_L_&_XL",         60.0),
    "Valencia":  ("Valencia, Spain",         "Valencia,_Spain_-_S,_M,_L,_&_XL",          96.0),
}


def _find_stl(folder: str, size: str) -> Path | None:
    d = _ROOT / folder
    if not d.exists():
        return None
    # Prefer "<City>_<SIZE>_Solid.stl"; fall back to any single-piece file of that size.
    for pat in (f"*_{size}_Solid.stl", f"*_{size}.stl", f"*_{size}_Solid_A1.stl"):
        hits = sorted(d.glob(pat))
        if hits:
            return hits[0]
    return None


def _run_one(region, stl, tallest_m, method, resolution):
    from city2stl.registration import register_city_stl
    t0 = time.time()
    rep = register_city_stl(stl_file=str(stl), city_name=region, resolution=resolution,
                            tallest_m=tallest_m, simplify_mode="prism",
                            regularize_footprints=True, registration_method=method,
                            out_dir=False)
    return rep, time.time() - t0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="list prepared models; no network/run")
    p.add_argument("--reports", default=None, metavar="DIR",
                   help="run the pipeline once per city (raster, prism mode) and write an "
                        "HTML report to DIR/<city>")
    p.add_argument("--city", default=None, help="restrict to one city (e.g. Paris)")
    p.add_argument("--size", default="L", help="model size S|M|L|XL (default L)")
    p.add_argument("--resolution", type=int, default=512)
    args = p.parse_args(argv)

    cities = {args.city: CITIES[args.city]} if args.city else CITIES
    if args.check:
        print(f"{'city':<12}{'tallest_m':>10}  STL (size %s)" % args.size)
        for name, (_region, folder, tall) in cities.items():
            stl = _find_stl(folder, args.size)
            print(f"{name:<12}{tall:>10.0f}  {stl.name if stl else '!! NOT FOUND'}")
        return 0

    if args.reports:
        from city2stl.registration import register_city_stl
        out_root = Path(args.reports)
        print(f"{'city':<12}{'scale':>8}{'rot':>8}{'IoU':>8}{'r':>7}{'sec':>7}  report")
        for name, (region, folder, tall) in cities.items():
            stl = _find_stl(folder, args.size)
            if stl is None:
                print(f"{name:<12} STL not found"); continue
            t0 = time.time()
            try:
                rep = register_city_stl(
                    stl_file=str(stl), city_name=region, resolution=args.resolution,
                    tallest_m=tall, simplify_mode="prism", regularize_footprints=True,
                    out_dir=str(out_root / name.lower()))
                r = rep.registration; c = rep.comparison
                print(f"{name:<12}{r.scale:>8.3f}{r.angle_deg:>8.2f}"
                      f"{getattr(c, 'footprint_iou', 0):>8.3f}{c.correlation:>7.3f}"
                      f"{time.time() - t0:>7.0f}  {out_root / name.lower()}/index.html")
            except Exception as exc:
                print(f"{name:<12} FAILED: {exc}")
        return 0

    print(f"{'city':<12}{'method':<8}{'scale':>8}{'rot':>8}{'IoU':>9}{'r':>7}{'conf':>7}{'sec':>7}")
    for name, (region, folder, tall) in cities.items():
        stl = _find_stl(folder, args.size)
        if stl is None:
            print(f"{name:<12} STL not found"); continue
        for method in ("raster", "polygon"):
            try:
                rep, dt = _run_one(region, stl, tall, method, args.resolution)
                r = rep.registration; c = rep.comparison
                print(f"{name:<12}{method:<8}{r.scale:>8.3f}{r.angle_deg:>8.2f}"
                      f"{getattr(c,'footprint_iou',0):>9.3f}{c.correlation:>7.3f}"
                      f"{r.confidence:>7.2f}{dt:>7.0f}")
            except Exception as exc:
                print(f"{name:<12}{method:<8} FAILED: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
