#!/usr/bin/env python3
"""Dense per-bearing heading recovery demo (F-SKY11.3).

Works in the pano's NATIVE cylindrical domain. For each compass bearing,
both the satellite and pano give a [0, 1] water-fraction signal. Pearson
cross-correlation over all 360 circular shifts finds the heading offset
that lines them up. Sub-degree accuracy via parabolic peak refinement.

Why this exists: the IPM bird's-eye approach (script 13) assumes ground
is at sea level, so tall buildings get sampled as sky and the bird's-eye
canvas collapses to a sparse near-camera ring. The pano is already a
cylindrical projection — there's no need to remap it to a top-down view
just to recover heading.

PDF (4 pages):
  1. Satellite reference (water mask + seed).
  2. Pano + water mask + recovered offset.
  3. Per-bearing signatures (sat vs pano shifted to best offset), overlaid.
  4. Cross-correlation curve over [0°, 360°) with sub-degree peak marked.

Usage:
    PYTHONPATH=. python city2stl/skyline_cv/scripts/14_dense_bearing_demo.py \\
        --region Cartagena --seed-index 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Dense per-bearing heading recovery demo (F-SKY11.3)")
    p.add_argument("--region", required=True)
    p.add_argument("--seed-index", type=int, default=5)
    p.add_argument("--out", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--spin-step-deg", type=float, default=30.0)
    p.add_argument("--fov-deg", type=float, default=75.0)
    p.add_argument("--n-bearings", type=int, default=360)
    p.add_argument("--near-range", type=str, default="10,80",
                   help="Satellite near-range window in metres, comma-separated")
    p.add_argument("--bottom-band-frac", type=float, default=0.20)
    return p


def _load_resolved_seed(region, seed_index, lat, lon, pano_id):
    cache_path = (
        ROOT / "city2stl" / "skyline_cv" / "runs" / "seed_resolution_cache.json"
    )
    if not cache_path.exists():
        return lat, lon, pano_id
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return lat, lon, pano_id
    key = f"seed_{seed_index}|{lat:.6f}|{lon:.6f}|{pano_id or ''}"
    entry = cache.get(key)
    if entry is None:
        return lat, lon, pano_id
    return (float(entry["lat"]), float(entry["lon"]),
            entry.get("pano_id") or pano_id)


def _render_satellite_page(pdf, sat_image, sat_water, sat_project,
                           seed_lat, seed_lon, near_range, region, seed_index):
    fig = plt.figure(figsize=(11, 9))
    fig.suptitle(f"Satellite reference  ({region} seed_{seed_index})",
                 fontsize=12)
    ax = fig.add_axes([0.04, 0.05, 0.92, 0.88])
    ax.imshow(sat_image)
    cyan = np.zeros((*sat_water.shape, 4), dtype=np.float32)
    cyan[..., 1] = 0.85
    cyan[..., 2] = 1.0
    cyan[..., 3] = np.where(sat_water, 0.40, 0.0)
    ax.imshow(cyan)
    sx, sy = sat_project(seed_lon, seed_lat)
    ax.scatter([sx], [sy], c="red", marker="*", s=220, zorder=5,
               edgecolor="white")
    ax.set_title(
        f"Wide satellite + water mask (cyan).  red * = seed.  "
        f"Near-range sampling window for bearing signature: {near_range[0]:.0f}-{near_range[1]:.0f} m",
        fontsize=9)
    ax.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


def _render_pano_page(pdf, pano_image, pano_water, headings_per_col,
                      best_offset_deg, region, seed_index):
    H, W = pano_water.shape[:2]
    fig = plt.figure(figsize=(16, 6))
    fig.suptitle(
        f"Panorama + water mask + recovered offset = {best_offset_deg:.2f}°  "
        f"({region} seed_{seed_index})",
        fontsize=12)
    ax = fig.add_axes([0.03, 0.10, 0.94, 0.78])
    ax.imshow(pano_image)
    cyan = np.zeros((H, W, 4), dtype=np.float32)
    cyan[..., 1] = 0.85
    cyan[..., 2] = 1.0
    cyan[..., 3] = np.where(pano_water, 0.45, 0.0)
    ax.imshow(cyan)

    # Draw column-to-bearing axis at the bottom.
    ticks_world = np.arange(0, 361, 30, dtype=np.float32)
    sort_idx = np.argsort(headings_per_col)
    sorted_h = headings_per_col[sort_idx]
    tick_cols = []
    for t in ticks_world:
        diffs = np.abs(((sorted_h - t + 180.0) % 360.0) - 180.0)
        j = int(np.argmin(diffs))
        tick_cols.append(int(sort_idx[j]))
    ax.set_xticks(tick_cols)
    ax.set_xticklabels([f"{int(t)}°" for t in ticks_world], fontsize=8)
    ax.set_xlabel("World bearing AFTER applying recovered offset (deg, clockwise from N)",
                  fontsize=9)
    ax.set_yticks([])
    pdf.savefig(fig)
    plt.close(fig)


def _render_signatures_page(pdf, sat_signature, pano_signature, best_offset_deg,
                            n_bearings, region, seed_index):
    bin_step = 360.0 / n_bearings
    bearings = np.arange(n_bearings, dtype=np.float32) * bin_step

    # Shift pano signature by best_offset_deg so it aligns with the satellite.
    shift = int(round(best_offset_deg / bin_step)) % n_bearings
    pano_aligned = np.roll(pano_signature, -shift)

    fig = plt.figure(figsize=(15, 6.5))
    fig.suptitle(
        f"Per-bearing radial signatures  ({region} seed_{seed_index})",
        fontsize=12)
    ax = fig.add_axes([0.06, 0.14, 0.90, 0.72])
    ax.plot(bearings, sat_signature, color="#0040c0", linewidth=1.4,
            label="satellite: water-fraction near-range")
    ax.plot(bearings, pano_aligned, color="#d04020", linewidth=1.4,
            alpha=0.85,
            label=f"pano (shifted by recovered offset {best_offset_deg:.2f}°)")
    ax.set_xlim(0, 360)
    ax.set_ylim(-0.05, 1.05)
    ax.set_xlabel("World bearing (deg, clockwise from N)", fontsize=10)
    ax.set_ylabel("Water fraction", fontsize=10)
    ax.set_title(
        "If the two curves overlap at the same bearings, the recovered "
        "offset is correct.",
        fontsize=9)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper right", fontsize=9)
    pdf.savefig(fig)
    plt.close(fig)


def _render_corr_curve_page(pdf, cand_deg, scores, best_offset_deg,
                            peak_value, sigma_deg, region, seed_index):
    fig = plt.figure(figsize=(14, 5.5))
    fig.suptitle(
        f"Cross-correlation curve  ({region} seed_{seed_index})",
        fontsize=12)
    ax = fig.add_axes([0.07, 0.18, 0.88, 0.65])
    ax.plot(cand_deg, scores, color=(0.10, 0.30, 0.55, 0.95), linewidth=1.4)
    ax.axvline(best_offset_deg, color="green", linestyle="-", linewidth=1.4,
               label=f"recovered offset = {best_offset_deg:.2f}°")
    ax.axhline(0.0, color="grey", linewidth=0.6, alpha=0.5)
    ax.set_xlabel("Candidate heading offset (deg)", fontsize=10)
    ax.set_ylabel("Pearson r", fontsize=10)
    ax.set_xlim(0, 360)
    ax.set_title(
        f"Peak r = {peak_value:.3f}   sigma (HWHM) = {sigma_deg:.2f}°.  "
        "Sharp single peak above the curve baseline => confident heading.",
        fontsize=9)
    ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.25)
    pdf.savefig(fig)
    plt.close(fig)


def main() -> int:
    args = build_parser().parse_args()

    from city2stl.skyline_cv.pipeline import _ensure_segformer as _ens
    sf_ok = _ens()
    print(f"[demo14] SegFormer load: ok={sf_ok}")
    if not sf_ok:
        return 1

    from city2stl.skyline_cv.region_pdf import (
        _resolve_api_key, _load_region_bbox, _load_site_seed_urls,
        _parse_streetview_url, _streetview_image,
    )
    from city2stl.skyline_cv.pipeline import (
        _neural_sky_and_building_masks, _neural_water_mask,
        stitch_pano_views, stitch_pano_masks,
    )
    from city2stl.skyline_cv.satellite_image import fetch_region_satellite
    from city2stl.skyline_cv.coastline_registration import (
        detect_sat_water_mask, build_sat_radial_signature,
        build_pano_radial_signature,
        register_by_dense_bearing_correlation,
    )

    api_key = _resolve_api_key(args.api_key)
    bbox = _load_region_bbox(args.region)
    sat_image, sat_project, sat_meta = fetch_region_satellite(
        (bbox.south, bbox.west, bbox.north, bbox.east), target_m_per_px=2.0,
    )
    print(f"[demo14] sat image: zoom={sat_meta['zoom']} "
          f"shape={sat_meta['shape']}")
    sat_water = detect_sat_water_mask(sat_image)

    seed_urls = _load_site_seed_urls(args.region)
    if not seed_urls or args.seed_index < 1 or args.seed_index > len(seed_urls):
        print(f"[demo14] invalid seed-index {args.seed_index}")
        return 2
    parsed = _parse_streetview_url(seed_urls[args.seed_index - 1])
    if parsed is None:
        return 3
    lat, lon, _base_heading, fov, pitch, pano_id = parsed
    lat, lon, pano_id = _load_resolved_seed(
        args.region, args.seed_index, lat, lon, pano_id)
    print(f"[demo14] seed_{args.seed_index} @ {lat:.5f},{lon:.5f}  "
          f"pano={pano_id}  url_pitch={pitch:+.1f}")

    # Capture 12 spin views with masks.
    spin_headings = np.arange(0.0, 360.0, args.spin_step_deg)
    captured = []
    for hdg in spin_headings:
        img = _streetview_image(
            api_key, lat, lon, float(hdg), fov=args.fov_deg, pitch=pitch,
            pano_id=pano_id, pano_only=(pano_id is not None),
        )
        eff_pitch = pitch
        if img is None:
            for fp in (min(pitch + 12.0, 15.0), 0.0):
                img = _streetview_image(
                    api_key, lat, lon, float(hdg), fov=args.fov_deg,
                    pitch=fp, pano_id=pano_id,
                    pano_only=(pano_id is not None),
                )
                if img is not None:
                    eff_pitch = fp
                    break
        if img is None:
            continue
        _, bmask = _neural_sky_and_building_masks(img)
        wmask = _neural_water_mask(img)
        captured.append({
            "geo_heading": float(hdg),
            "image": img,
            "building_mask": bmask,
            "water_mask": wmask,
            "effective_pitch": eff_pitch,
        })
    print(f"[demo14] captured {len(captured)} views")
    if len(captured) < 4:
        return 4

    pano_rgb_result = stitch_pano_views(captured, args.fov_deg, args.spin_step_deg)
    pano_mask_result = stitch_pano_masks(captured, args.fov_deg, args.spin_step_deg)
    if pano_rgb_result is None or pano_mask_result is None:
        print("[demo14] pano stitch failed")
        return 5
    pano_image, headings_per_col = pano_rgb_result
    _, pano_water = pano_mask_result
    print(f"[demo14] pano shape: {pano_image.shape}  "
          f"water frac: {float(pano_water.mean()):.3f}")

    # Build the two 1-D bearing signatures.
    near_range = tuple(float(x) for x in args.near_range.split(","))
    sat_sig = build_sat_radial_signature(
        sat_water, sat_project, lat, lon,
        n_bearings=args.n_bearings, near_range_m=near_range, step_m=5.0,
    )
    # Use the average effective pitch across captured views.
    eff_pitches = sorted({v["effective_pitch"] for v in captured})
    used_pitch = float(np.mean(eff_pitches)) if eff_pitches else 0.0
    view_width_px = captured[0]["image"].shape[1]
    pano_sig = build_pano_radial_signature(
        pano_water, headings_per_col,
        n_bearings=args.n_bearings,
        near_range_m=near_range,
        camera_h_m=1.7,
        pitch_deg=used_pitch,
        fov_deg=args.fov_deg,
        view_width_px=view_width_px,
    )
    print(f"[demo14] sat sig: mean={sat_sig.mean():.3f} std={sat_sig.std():.3f}")
    print(f"[demo14] pano sig: mean={pano_sig.mean():.3f} std={pano_sig.std():.3f}")

    # Cross-correlate.
    best, cand_deg, scores, peak_value, sigma_deg = (
        register_by_dense_bearing_correlation(
            pano_sig, sat_sig, step_deg=1.0, subpixel_refine=True,
        )
    )
    print(f"[demo14] recovered offset = {best:.2f}°  peak r = {peak_value:.3f}  "
          f"sigma (HWHM) = {sigma_deg:.2f}°")

    out_pdf = Path(args.out) if args.out else (
        ROOT / "city2stl" / "skyline_cv" / "runs" / "dense_bearing_demo"
        / f"{args.region}_seed{args.seed_index}.pdf"
    )
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out_pdf) as pdf:
        _render_satellite_page(pdf, sat_image, sat_water, sat_project,
                               lat, lon, near_range,
                               args.region, args.seed_index)
        _render_pano_page(pdf, pano_image, pano_water, headings_per_col,
                          best, args.region, args.seed_index)
        _render_signatures_page(pdf, sat_sig, pano_sig, best,
                                args.n_bearings, args.region, args.seed_index)
        _render_corr_curve_page(pdf, cand_deg, scores, best,
                                peak_value, sigma_deg,
                                args.region, args.seed_index)
    print(f"[demo14] wrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
