"""Score skyline building heights against a registered vendor plate.

The souvenir plates are physical models of a city's built form, so once one is
registered to the OSM window it becomes a height reference that owes nothing to
OSM tags — which matters, because the skyline pipeline consults those tags while
it works and cannot be honestly scored against them.

Three things have to happen before the plate can be read as metres:

*Frame.* ``data/<slug>/stl_heightmap.npy`` sits in its own pixel
grid; the affine in ``_ground_truth/<slug>.json`` (or the pipeline's own
``register_transform``) maps it into the OSM export grid, rebased by
``eval_registration.truth_in_export_frame`` because the hand alignment was
dragged in a window of a different size.

*Datum.* That raster is already height above the plate's own ground — the export
runs ``terrain_residual`` and a multi-Otsu cut before saving — so no ground
estimate is needed here, only the NaNs it leaves where there is no building.

*Scale.* It is in model units, not metres, and the vertical scale is the one
number the export never verified. ``tallest_m / z_max`` trusts the vendor's
"tallest building" figure and the plate's own peak, both of which can be off;
regressing the plate against the OSM height raster over the pixels both call
building is independent of either. For Miami the two agree (21.4 vs 21.8
m/unit), which is the evidence that the plate's z is trustworthy at all.

Usage (from the Code/ directory, with the working venv):

    python tools/align_tool/plate_height_truth.py --slug miami_fl_usa \\
        --heights strm2stl/city2stl/skyline/runs/region_reports/\\
miami_postfix_skyline_report/heights.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import paths  # noqa: E402

HERE = paths.HERE

from eval_registration import truth_in_export_frame  # noqa: E402

DATA_DIR = HERE / "data"
TRUTH_DIR = paths.GROUND_TRUTH

# A footprint has to cover at least this many plate pixels before its sampled
# height means anything. The export grid is ~5.9 m/pixel, so this is roughly a
# 25 x 25 m building — below that the sample is dominated by the neighbours the
# warp bleeds in.
MIN_PIXELS = 4
# ...and at least this fraction of them must be plate-building rather than NaN.
# A tower whose footprint is mostly gap either failed to register or is not on
# the plate at all; either way its sample is not a height.
MIN_COVERAGE = 0.34
# Height taken as this percentile of the covered pixels. The max chases the one
# pixel that caught a neighbouring tower through the warp; the median reads the
# setbacks and mechanical decks that make a roof lower than the building.
SAMPLE_PERCENTILE = 90.0


def warp_to_osm_frame(src: np.ndarray, matrix: np.ndarray,
                      shape: tuple[int, int]) -> np.ndarray:
    """Resample an STL-frame raster onto the OSM grid, keeping NaN as NaN.

    ``cv2.warpAffine`` has no notion of missing data, so the values and a
    finite-mask are warped separately and the result is renormalised by the
    mask. Without this the NaN gaps between blocks would poison every
    interpolated pixel that touches them and the plate would lose its streets.
    """
    filled = np.where(np.isfinite(src), src, 0.0)
    mask = np.isfinite(src).astype(np.float64)
    m32 = np.asarray(matrix, dtype=np.float32)
    w = cv2.warpAffine(filled, m32, (shape[1], shape[0]),
                       flags=cv2.INTER_LINEAR, borderValue=0.0)
    wm = cv2.warpAffine(mask, m32, (shape[1], shape[0]),
                        flags=cv2.INTER_LINEAR, borderValue=0.0)
    return np.where(wm > 0.5, w / np.maximum(wm, 1e-6), np.nan)


def calibrate_m_per_unit(plate_m_frame: np.ndarray,
                         osm_heights: np.ndarray) -> dict:
    """Metres per model unit, from the plate against the OSM height raster.

    Least squares through the origin over the pixels both rasters call
    building. Reported alongside the median and p99 ratios because the three
    disagreeing would mean the fit is being carried by one tower or by a
    registration offset rather than by the plate's actual scale.
    """
    both = (np.isfinite(plate_m_frame) & np.isfinite(osm_heights)
            & (plate_m_frame > 0) & (osm_heights > 0))
    n = int(both.sum())
    if n < 100:
        return {"n_pixels": n, "m_per_unit": None}
    a = plate_m_frame[both]
    b = osm_heights[both]
    return {
        "n_pixels": n,
        "m_per_unit": float((a * b).sum() / (a * a).sum()),
        "median_ratio": float(np.median(b / a)),
        "p99_ratio": float(np.percentile(b, 99) / np.percentile(a, 99)),
    }


def lonlat_to_px(lon: float, lat: float, bbox_nsew, resolution: int
                 ) -> tuple[float, float]:
    """(lon, lat) to (col, row) in the export grid. Row 0 is the south edge."""
    n, s, e, w = (float(v) for v in bbox_nsew)
    col = (lon - w) / (e - w) * resolution
    row = (lat - s) / (n - s) * resolution
    return col, row


def sample_footprint(plate_m_frame: np.ndarray, ring_lonlat, bbox_nsew,
                     resolution: int) -> tuple[float | None, int, float]:
    """Plate height under one footprint, in model units.

    Returns (value, pixels_in_footprint, covered_fraction); value is None when
    the footprint is too small or too empty to be read.
    """
    pts = np.asarray(
        [lonlat_to_px(lon, lat, bbox_nsew, resolution) for lon, lat in ring_lonlat],
        dtype=np.float32)
    if len(pts) < 3:
        return None, 0, 0.0
    mask = np.zeros(plate_m_frame.shape, dtype=np.uint8)
    cv2.fillPoly(mask, [np.round(pts).astype(np.int32)], 1)
    idx = mask.astype(bool)
    n_px = int(idx.sum())
    if n_px < MIN_PIXELS:
        return None, n_px, 0.0
    vals = plate_m_frame[idx]
    finite = vals[np.isfinite(vals) & (vals > 0)]
    coverage = float(finite.size) / float(n_px)
    if coverage < MIN_COVERAGE:
        return None, n_px, coverage
    return float(np.percentile(finite, SAMPLE_PERCENTILE)), n_px, coverage


def load_plate(slug: str, alignment: str) -> dict:
    """Everything the plate side of the comparison needs, in the OSM frame."""
    city_dir = DATA_DIR / slug
    meta = json.loads((city_dir / "meta.json").read_text(encoding="utf-8"))
    stl = np.load(city_dir / "stl_heightmap.npy").astype(np.float64)
    osm = np.load(city_dir / "osm_buildings.npy").astype(np.float64)

    if alignment == "truth":
        truth_doc = json.loads(
            (TRUTH_DIR / f"{slug}.json").read_text(encoding="utf-8"))
        matrix = truth_in_export_frame(truth_doc, meta)
    else:
        matrix = np.asarray(meta["register_transform"]["matrix"], dtype=np.float64)

    warped = warp_to_osm_frame(stl, matrix, osm.shape)
    return {
        "meta": meta,
        "osm_heights": osm,
        "plate_units": warped,
        "matrix": matrix,
        "bbox_nsew": meta["osm_bbox_nsew"],
        "resolution": int(meta["resolution"]),
        "vendor_m_per_unit": (float(meta["tallest_m"]) / float(np.nanmax(stl))
                              if meta.get("tallest_m") else None),
    }


def compare(slug: str, heights_path: Path, alignment: str,
            m_per_unit_override: float | None) -> dict:
    plate = load_plate(slug, alignment)
    cal = calibrate_m_per_unit(plate["plate_units"], plate["osm_heights"])
    m_per_unit = m_per_unit_override or cal.get("m_per_unit")
    if m_per_unit is None:
        raise SystemExit("could not calibrate the plate's vertical scale "
                         "and no --m-per-unit given")

    doc = json.loads(heights_path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for b in doc.get("buildings", []):
        ring = b.get("footprint_lonlat")
        if not ring:
            continue
        units, n_px, coverage = sample_footprint(
            plate["plate_units"], ring, plate["bbox_nsew"], plate["resolution"])
        if units is None:
            continue
        plate_m = units * m_per_unit
        est = b.get("effective_height_m")
        if est is None:
            continue
        rows.append({
            "feature_id": b["feature_id"],
            "name": b.get("name"),
            "plate_m": plate_m,
            "estimate_m": float(est),
            "error_m": float(est) - plate_m,
            "tag_m": b.get("height_tag_m"),
            "tag_source": b.get("height_source"),
            "n_seeds": b.get("n_seeds"),
            "n_views": b.get("n_views"),
            "source": b.get("effective_height_source"),
            "footprint_px": n_px,
            "plate_coverage": coverage,
        })

    return {
        "slug": slug,
        "alignment": alignment,
        "calibration": cal,
        "vendor_m_per_unit": plate["vendor_m_per_unit"],
        "m_per_unit_used": m_per_unit,
        "n_heights_rows": len(doc.get("buildings", [])),
        "n_scored": len(rows),
        "rows": rows,
    }


def summarise(result: dict, min_plate_m: float = 0.0) -> str:
    rows = [r for r in result["rows"] if r["plate_m"] >= min_plate_m]
    lines = []
    cal = result["calibration"]
    lines.append(f"plate scale: fitted {cal.get('m_per_unit')} m/unit "
                 f"(median ratio {cal.get('median_ratio')}, "
                 f"p99 {cal.get('p99_ratio')}, {cal.get('n_pixels')} px); "
                 f"vendor {result['vendor_m_per_unit']}; "
                 f"used {result['m_per_unit_used']:.3f}")
    lines.append(f"alignment: {result['alignment']}   "
                 f"scored {len(rows)} of {result['n_heights_rows']} "
                 f"aggregated buildings")
    if not rows:
        return "\n".join(lines + ["no buildings scored"])

    err = np.asarray([r["error_m"] for r in rows], dtype=np.float64)
    plate = np.asarray([r["plate_m"] for r in rows], dtype=np.float64)
    lines.append(f"MAE {np.abs(err).mean():7.2f} m    "
                 f"median |err| {np.median(np.abs(err)):6.2f} m    "
                 f"bias {err.mean():+7.2f} m    "
                 f"plate mean {plate.mean():.1f} m")

    tagged = [r for r in rows if r["tag_source"] in ("osm_tag", "osm_levels")]
    untagged = [r for r in rows if r["tag_source"] not in ("osm_tag", "osm_levels")]
    for label, group in (("tagged", tagged), ("untagged", untagged)):
        if not group:
            continue
        e = np.abs([r["error_m"] for r in group])
        lines.append(f"  {label:<9} n={len(group):<4} MAE {e.mean():6.2f} m")

    lines.append("")
    lines.append(f"{'feature':<9}{'name':<28}{'plate':>8}{'est':>8}"
                 f"{'err':>8}{'tag':>8}{'seeds':>6}{'cov':>6}")
    worst = sorted(rows, key=lambda r: -abs(r["error_m"]))[:20]
    for r in worst:
        tag = "" if r["tag_m"] is None else f"{r['tag_m']:>8.0f}"
        lines.append(f"{r['feature_id']:<9}{(r['name'] or '')[:27]:<28}"
                     f"{r['plate_m']:>8.1f}{r['estimate_m']:>8.1f}"
                     f"{r['error_m']:>+8.1f}{tag:>8}{r['n_seeds']:>6}"
                     f"{r['plate_coverage']:>6.2f}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default="miami_fl_usa")
    ap.add_argument("--heights", required=True, type=Path,
                    help="heights.json written next to a region's HTML report")
    ap.add_argument("--alignment", choices=("truth", "register"), default="truth")
    ap.add_argument("--m-per-unit", type=float, default=None,
                    help="override the fitted plate vertical scale")
    ap.add_argument("--min-plate-m", type=float, default=0.0,
                    help="only score buildings the plate says are this tall")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    result = compare(args.slug, args.heights, args.alignment, args.m_per_unit)
    print(summarise(result, min_plate_m=args.min_plate_m))
    if args.json_out:
        args.json_out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
