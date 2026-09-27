"""CLI entry point for city STL → OSM registration.

Mirrors city2stl/skyline/scripts/08_region_skyline_pdf.py in structure.

Usage
-----
python -m city2stl.registration.scripts.run_registration \\
    --stl path/to/city.stl \\
    --region "Philadelphia, PA, USA" \\
    [--resolution 512] \\
    [--height-scale 0.001] \\
    [--out path/to/report/folder] \\
    [--z-axis 2]

Default output: the gitignored Code/_reports/{region}/
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("registration.run")


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Register a city STL against OSM building heights and produce an HTML report.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--stl", required=True, metavar="PATH",
                   help="Path to the city STL file.")
    p.add_argument("--region", required=True, metavar="NAME",
                   help='City name for OSM fetch, e.g. "Philadelphia, PA, USA", '
                        'or a (N,S,E,W) bbox as "40.06,39.86,-74.95,-75.28".')
    p.add_argument("--resolution", type=int, default=1024, metavar="N",
                   help="Output grid resolution for the comparison/report heightmaps "
                        "(square).  The registration search is fixed at 512 and "
                        "resolution-independent, so this only sharpens the footprint/"
                        "agreement images; higher = more detail, slightly slower.")
    p.add_argument("--height-scale", type=float, default=None, metavar="SCALE",
                   help="STL model units → metres. Omit for auto-estimate.")
    p.add_argument("--max-scale-ratio", type=float, default=5.0, metavar="R",
                   help="Maximum spatial scale search range.")
    p.add_argument("--z-axis", type=int, default=2, choices=[0, 1, 2], metavar="AXIS",
                   help="Which mesh axis is elevation (0=X, 1=Y, 2=Z).")
    p.add_argument("--default-height", type=float, default=10.0, metavar="M",
                   help="Fallback OSM building height in metres.")
    p.add_argument("--scale", type=float, default=None, metavar="SCALE",
                   help="Override the physical anchor scale (e.g. 0.5 or 0.667). "
                        "Default: auto-derived as 1/osm_margin=0.667.")
    p.add_argument("--out", default=None, metavar="DIR",
                   help="Output folder. Defaults to the gitignored Code/_reports/{region}/")
    # City-agnostic overrides (for cities not in the built-in config)
    p.add_argument("--center", default=None, metavar="LAT,LON",
                   help="Downtown centre (lat,lon) for the OSM bbox. Omit to use "
                        "the built-in city config, else geocode the city centre.")
    p.add_argument("--tallest-m", type=float, default=None, metavar="M",
                   help="Tallest building height (m) — scale anchor for an unlisted city.")
    p.add_argument("--scale-m-per-unit", type=float, default=None, metavar="S",
                   help="STL-units→metres scale directly (overrides --tallest-m).")
    p.add_argument("--detect-resolution-factor", type=int, default=2, metavar="K",
                   help="Detect/vectorize STL footprints at K× the working resolution "
                        "(separates merged buildings); 1 disables.")
    p.add_argument("--height-source", default="osm", choices=["osm", "lidar"],
                   help="Per-building height source: OSM tags (default) or measured "
                        "3DEP lidar nDSM (needs pdal+py3dep; US only).")
    p.add_argument("--rotation", type=float, default=None, metavar="DEG",
                   help="Force the registration rotation (degrees). Use for non-grid "
                        "cities (Boston/Paris) where orientation can't be auto-detected. "
                        "Default: auto (gradient histogram, biased to 0°).")
    # Mesh simplification + improved segmentation
    p.add_argument("--simplify-mesh", action="store_true",
                   help="Alias for --simplify-mode decimate (footprint-preserving quadric "
                        "decimation within the metres deviation budget).")
    p.add_argument("--simplify-mode", choices=["off", "decimate", "prism"], default="off",
                   help="Mesh simplification before segmentation. 'decimate' = quadric within "
                        "the deviation budget; 'prism' = decompose the STL into a sum of "
                        "extruded prisms (reverse of the OSM render).")
    p.add_argument("--simplify-tol-m", type=float, default=3.5, metavar="M",
                   help="Surface-deviation budget (metres) for --simplify-mesh (default 3.5).")
    p.add_argument("--save-simplified", default=None, metavar="PATH",
                   help="Write the simplified mesh here (STL/3MF/OBJ by extension).")
    p.add_argument("--regularize-footprints", action="store_true",
                   help="Watershed-split merged buildings + snap footprint polygons to "
                        "rectilinear edges (cleaner, OSM-comparable polygons).")
    p.add_argument("--refine-polygons", action="store_true",
                   help="Polygon-matched ICP fine-tuning of the registration (only when the "
                        "coarse footprint Dice > 0.95).")
    p.add_argument("--registration-method", choices=["raster", "polygon"], default="raster",
                   help="'raster' (gradient + xcorr + edge-IoU, default) or 'polygon' "
                        "(match building footprints directly; falls back to raster if "
                        "match confidence is low).")
    p.add_argument("--free-scale", action="store_true",
                   help="Un-lock scale: refine it within ±10%% of the geometric anchor by "
                        "polygon edge-IoU. The scale is geometrically exact (1/osm_margin), so "
                        "this usually drifts off and degrades the result — off by default.")
    p.add_argument("--decimation-curve", action="store_true",
                   help="Compute + plot the decimation trade-off curve (deviation vs faces "
                        "kept) in the report. Re-decimates a few times; adds time.")
    return p.parse_args(argv)


def _parse_center(s):
    if not s:
        return None
    lat, lon = (float(x.strip()) for x in s.split(","))
    return (lat, lon)


def _parse_region(region_str: str):
    """Return a city-name string, or parse '40.06,39.86,-74.95,-75.28' → tuple."""
    parts = region_str.split(",")
    if len(parts) == 4:
        try:
            return tuple(float(x.strip()) for x in parts)
        except ValueError:
            pass
    return region_str


def main(argv=None):
    args = _parse_args(argv)

    stl_path = Path(args.stl)
    if not stl_path.exists():
        logger.error("STL file not found: %s", stl_path)
        sys.exit(1)

    city = _parse_region(args.region)
    out_dir = Path(args.out) if args.out else None

    logger.info("STL       : %s", stl_path)
    logger.info("Region    : %s", city)
    logger.info("Resolution: %d", args.resolution)
    logger.info("Z axis    : %d", args.z_axis)
    logger.info("Output    : %s", out_dir or "(default runs/ folder)")

    from city2stl.registration import register_city_stl

    if args.scale is not None:
        logger.info("Scale     : %.4f (manual override)", args.scale)

    report = register_city_stl(
        stl_file=str(stl_path),
        city_name=city,
        resolution=args.resolution,
        height_scale=args.height_scale,
        max_scale_ratio=args.max_scale_ratio,
        stl_z_axis=args.z_axis,
        default_height=args.default_height,
        out_dir=out_dir,
        forced_scale=args.scale,
        center=_parse_center(args.center),
        tallest_m=args.tallest_m,
        scale_m_per_unit=args.scale_m_per_unit,
        detect_resolution_factor=args.detect_resolution_factor,
        height_source=args.height_source,
        forced_rotation=args.rotation,
        simplify_mesh=args.simplify_mesh,
        simplify_mode=args.simplify_mode,
        simplify_tol_m=args.simplify_tol_m,
        save_simplified=args.save_simplified,
        regularize_footprints=args.regularize_footprints,
        refine_polygons=args.refine_polygons,
        decimation_curve=args.decimation_curve,
        free_scale=args.free_scale,
        registration_method=args.registration_method,
    )

    print("\n--- Registration summary ----------------------------")
    print(f"  Confidence (ECC) : {report.registration.confidence:.4f}")
    print(f"  Scale found      : {report.registration.scale:.5f}x")
    print(f"  Rotation found   : {report.registration.angle_deg:.2f} deg")
    print(f"  Converged        : {report.registration.converged}")
    print(f"  Height fit (Z)   : stl_m = {report.comparison.height_scale_used:.4f}*stl "
          f"{report.comparison.height_offset_used:+.2f} m")
    print("--- Comparison (spatial) ----------------------------")
    print(f"  Dice score       : {report.comparison.dice_score:.4f}  (building footprint match)")
    print(f"  Coverage         : {report.comparison.coverage_pct:.1f}%  (STL covers OSM buildings)")
    print("--- Comparison (height values) ----------------------")
    print(f"  Correlation (r)  : {report.comparison.correlation:.4f}  (Pearson, poor metric)")
    print(f"  Rank corr (rho)  : {report.comparison.rank_correlation:.4f}  (Spearman, robust)")
    print(f"  MAPE             : {report.comparison.mape:.1f}%  (median height error)")
    print(f"  Height ratio     : {report.comparison.height_ratio_mean:.3f} ± {report.comparison.height_ratio_std:.3f}  (STL/OSM)")
    print(f"  RMSE             : {report.comparison.rmse:.2f} m")
    print(f"  MAE              : {report.comparison.mae:.2f} m")
    print(f"  Bias             : {report.comparison.bias:+.2f} m")
    print(f"  Overlap pixels   : {report.comparison.n_overlap:,}")
    print("--- Timing ------------------------------------------")
    for step, t in report.step_timings:
        safe = step.encode(sys.stdout.encoding or "ascii", "replace").decode(sys.stdout.encoding or "ascii")
        print(f"  {safe:<30} {t:.2f} s")
    print("-----------------------------------------------------\n")

    return report


if __name__ == "__main__":
    main()
