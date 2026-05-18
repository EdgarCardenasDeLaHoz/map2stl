#!/usr/bin/env python3
"""Pano-level coastline-alignment demo (F-SKY11.1 Phase A inspector).

Stitches the seed's 12 spin views into a 360 deg pano + water mask,
detects the satellite coastline key points, and recovers the heading
offset that lines them up with the pano's water/non-water boundary.
One global sweep over [0, 360) deg instead of 12 per-view sweeps.

Output PDF has four pages:
  1. Satellite reference (same as script 11).
  2. Full pano + water-mask overlay + ALL 24 coastline key points
     projected at the recovered offset.
  3. Pano per-column horizon-y vs per-bearing expected-y for the
     keypoints (visual "do the curves agree?" check).
  4. Offset-sweep score curve over [0, 360) with the recovered peak
     marked.

Usage:
    PYTHONPATH=. python city2stl/skyline_cv/scripts/12_pano_coastline_demo.py \\
        --region Cartagena --seed-index 5
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_KEYPOINT_PALETTE = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe",
    "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000",
    "#aaffc3", "#808000", "#ffd8b1", "#000075", "#808080",
]


def _kp_color(i: int) -> str:
    return _KEYPOINT_PALETTE[i % len(_KEYPOINT_PALETTE)]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pano-level coastline-alignment demo (F-SKY11.1)")
    p.add_argument("--region", required=True)
    p.add_argument("--seed-index", type=int, default=5)
    p.add_argument("--out", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--spin-step-deg", type=float, default=30.0)
    p.add_argument("--fov-deg", type=float, default=75.0)
    p.add_argument("--tolerance-px", type=int, default=25)
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
                           seed_lat, seed_lon, keypoints, region, seed_index):
    """Same satellite reference as script 11, kept identical so the
    cross-script diagnostic experience is consistent."""
    fig = plt.figure(figsize=(14, 9))
    fig.suptitle(
        f"Satellite reference + 24 coastline key points  "
        f"({region} seed_{seed_index})",
        fontsize=12,
    )
    ax = fig.add_axes([0.04, 0.08, 0.92, 0.82])
    ax.imshow(sat_image)
    cyan = np.zeros((*sat_water.shape, 4), dtype=np.float32)
    cyan[..., 1] = 0.85
    cyan[..., 2] = 1.0
    cyan[..., 3] = np.where(sat_water, 0.40, 0.0)
    ax.imshow(cyan)
    seed_px = sat_project(seed_lon, seed_lat)
    ax.scatter([seed_px[0]], [seed_px[1]], c="red", marker="*", s=200,
               zorder=5, edgecolor="white")
    for i, kp in enumerate(keypoints):
        color = _kp_color(i)
        ax.scatter([kp["x_sat"]], [kp["y_sat"]], s=80, marker="o",
                   facecolor=color, edgecolor="black", linewidth=0.8,
                   zorder=6)
        ax.text(kp["x_sat"] + 6, kp["y_sat"] - 6, str(i + 1),
                fontsize=8, color=color, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                          edgecolor=color, alpha=0.85),
                zorder=6)
    ax.set_title(f"{len(keypoints)} coastline key points  "
                 "(numbered, color matched to pano page below)",
                 fontsize=9)
    ax.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


def _render_pano_page(pdf, pano_image, pano_water, headings_per_col,
                      keypoints, seed_lat, seed_lon, best_offset_deg,
                      pitch_deg, region, seed_index):
    fig = plt.figure(figsize=(16, 7))
    fig.suptitle(
        f"360-deg pano + coastline key points projected at "
        f"recovered offset = {best_offset_deg:.1f} deg  "
        f"({region} seed_{seed_index})",
        fontsize=12,
    )
    ax = fig.add_axes([0.02, 0.08, 0.96, 0.80])
    ax.imshow(pano_image)
    cyan = np.zeros((*pano_water.shape, 4), dtype=np.float32)
    cyan[..., 1] = 0.85
    cyan[..., 2] = 1.0
    cyan[..., 3] = np.where(pano_water, 0.40, 0.0)
    ax.imshow(cyan)
    H = pano_water.shape[0]
    focal_for_y = H / (2.0 * math.tan(math.radians(37.5)))
    p_rad = math.radians(pitch_deg)
    camera_h = 1.7
    n_proj = 0
    for i, kp in enumerate(keypoints):
        target = (kp["bearing_deg"] - best_offset_deg) % 360.0
        diffs = ((headings_per_col - target + 180.0) % 360.0) - 180.0
        idx = int(np.argmin(np.abs(diffs)))
        if abs(float(diffs[idx])) > 1.5:
            continue
        kp_dist = float(kp.get("distance_m", 0.0))
        if kp_dist <= 1.0:
            continue
        expected_y = (
            H / 2.0
            + camera_h / kp_dist * focal_for_y
            + math.tan(p_rad) * focal_for_y
        )
        color = _kp_color(i)
        ax.axvline(idx, color=color, linewidth=0.8, alpha=0.45)
        ax.scatter([idx], [expected_y], s=60, marker="o",
                   facecolor=color, edgecolor="black", linewidth=0.8,
                   zorder=6)
        # Number labels alternate between top and bottom of frame so
        # they don't overplot when key points cluster in one heading
        # sector.
        y_label = 8 if (i % 2 == 0) else (H - 10)
        ax.text(idx + 2, y_label, str(i + 1), color=color, fontsize=8,
                fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                          edgecolor=color, alpha=0.85),
                zorder=6)
        n_proj += 1
    ax.set_title(
        f"Pano (cyan = SegFormer water).  "
        f"{n_proj} key points projected (dots = expected y at sea level "
        f"with pitch_deg={pitch_deg:+.1f}).  "
        "Correct offset => dots land on the water/non-water boundary.",
        fontsize=9,
    )
    ax.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


def _render_horizon_curve_page(pdf, pano_water, headings_per_col, keypoints,
                               best_offset_deg, pitch_deg, region, seed_index):
    fig = plt.figure(figsize=(14, 7))
    fig.suptitle(
        f"Pano horizon-y per column vs key-point expected y  "
        f"({region} seed_{seed_index}, recovered offset "
        f"{best_offset_deg:.1f} deg)",
        fontsize=11,
    )
    H, W = pano_water.shape[:2]
    top_y_per_col = np.full(W, np.nan, dtype=np.float32)
    for c in range(W):
        rows = np.where(pano_water[:, c])[0]
        if rows.size > 0:
            top_y_per_col[c] = rows.min()
    ax = fig.add_axes([0.06, 0.12, 0.90, 0.78])
    ax.plot(np.arange(W), top_y_per_col, color=(0.10, 0.45, 0.70, 0.95),
            linewidth=0.9, label="pano horizon y (top of water mask)")
    ax.invert_yaxis()  # smaller y = higher in image; nicer reading.
    focal_for_y = H / (2.0 * math.tan(math.radians(37.5)))
    p_rad = math.radians(pitch_deg)
    camera_h = 1.7
    for i, kp in enumerate(keypoints):
        target = (kp["bearing_deg"] - best_offset_deg) % 360.0
        diffs = ((headings_per_col - target + 180.0) % 360.0) - 180.0
        idx = int(np.argmin(np.abs(diffs)))
        if abs(float(diffs[idx])) > 1.5:
            continue
        kp_dist = float(kp.get("distance_m", 0.0))
        if kp_dist <= 1.0:
            continue
        expected_y = (
            H / 2.0
            + camera_h / kp_dist * focal_for_y
            + math.tan(p_rad) * focal_for_y
        )
        color = _kp_color(i)
        ax.scatter([idx], [expected_y], s=50, color=color, edgecolor="black",
                   linewidth=0.6, zorder=4)
    ax.set_xlim(0, W - 1)
    ax.set_xlabel("pano column (px)  ~ heading", fontsize=9)
    ax.set_ylabel("y (px) — smaller = higher in pano", fontsize=9)
    ax.set_title(
        "Blue curve = pano's actual water-mask top.  "
        "Numbered dots = sat key points' expected y at the recovered offset.  "
        "Curve should pass through the dots at the correct offset.",
        fontsize=9,
    )
    ax.grid(alpha=0.25)
    ax.legend(loc="lower right", fontsize=8)
    pdf.savefig(fig)
    plt.close(fig)


def _render_score_curve_page(pdf, cand_deg, scores, best_offset_deg,
                             region, seed_index):
    fig = plt.figure(figsize=(14, 5.5))
    fig.suptitle(
        f"Offset-sweep score curve  ({region} seed_{seed_index})",
        fontsize=12,
    )
    ax = fig.add_axes([0.07, 0.18, 0.88, 0.65])
    ax.plot(cand_deg, scores, color=(0.10, 0.30, 0.55, 0.95), linewidth=1.4)
    ax.axvline(best_offset_deg, color="green", linestyle="-", linewidth=1.4,
               label=f"recovered offset = {best_offset_deg:.1f} deg")
    ax.set_xlabel("candidate heading offset (deg)", fontsize=10)
    ax.set_ylabel("alignment score", fontsize=10)
    ax.set_title(
        f"Peak = {scores.max():.3f}   "
        f"flatness (sigma) = {scores.std():.3f}   "
        "Sharp single peak => confident recovery; flat/multi-modal => "
        "coastline signal too weak.",
        fontsize=9,
    )
    ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.25)
    pdf.savefig(fig)
    plt.close(fig)


def main() -> int:
    args = build_parser().parse_args()

    from city2stl.skyline_cv.pipeline import _ensure_segformer as _ens
    sf_ok = _ens()
    print(f"[demo12] SegFormer load: ok={sf_ok}")
    if not sf_ok:
        print("[demo12] cannot proceed without SegFormer")
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
        detect_sat_water_mask, detect_coastline_keypoints,
        sweep_pano_heading_offset,
    )

    api_key = _resolve_api_key(args.api_key)
    bbox = _load_region_bbox(args.region)
    sat_image, sat_project, sat_meta = fetch_region_satellite(
        (bbox.south, bbox.west, bbox.north, bbox.east), target_m_per_px=2.0,
    )
    print(f"[demo12] sat image: zoom={sat_meta['zoom']} "
          f"shape={sat_meta['shape']}")
    sat_water = detect_sat_water_mask(sat_image)

    seed_urls = _load_site_seed_urls(args.region)
    if not seed_urls or args.seed_index < 1 or args.seed_index > len(seed_urls):
        print(f"[demo12] invalid seed-index {args.seed_index}")
        return 2
    parsed = _parse_streetview_url(seed_urls[args.seed_index - 1])
    if parsed is None:
        print("[demo12] failed to parse seed URL")
        return 3
    lat, lon, base_heading, fov, pitch, pano_id = parsed
    lat, lon, pano_id = _load_resolved_seed(
        args.region, args.seed_index, lat, lon, pano_id)
    print(f"[demo12] seed_{args.seed_index} @ {lat:.5f},{lon:.5f}  "
          f"pano={pano_id}  url_pitch={pitch:+.1f}")

    keypoints = detect_coastline_keypoints(
        sat_water, sat_project, lat, lon,
        n_bearings=24, max_range_m=2500.0, step_m=5.0, min_distance_m=30.0,
    )
    print(f"[demo12] detected {len(keypoints)} coastline key points")

    # Capture the 12 spin views + per-view masks, falling back through
    # pitch alternatives the same way the production pipeline does.
    spin_headings = np.arange(0.0, 360.0, args.spin_step_deg)
    captured = []
    for hdg in spin_headings:
        img = _streetview_image(
            api_key, lat, lon, float(hdg), fov=args.fov_deg, pitch=pitch,
            pano_id=pano_id, pano_only=(pano_id is not None),
        )
        effective_pitch = pitch
        if img is None:
            for fp in (min(pitch + 12.0, 15.0), 0.0):
                img = _streetview_image(
                    api_key, lat, lon, float(hdg), fov=args.fov_deg,
                    pitch=fp, pano_id=pano_id,
                    pano_only=(pano_id is not None),
                )
                if img is not None:
                    effective_pitch = fp
                    break
        if img is None:
            print(f"[demo12] heading {hdg:.0f}: no image, skipping")
            continue
        # Trigger SegFormer cache for this image.
        _, bmask = _neural_sky_and_building_masks(img)
        wmask = _neural_water_mask(img)
        captured.append({
            "geo_heading": float(hdg),
            "image": img,
            "building_mask": bmask,
            "water_mask": wmask,
            "effective_pitch": effective_pitch,
        })
    print(f"[demo12] captured {len(captured)} spin views")
    if len(captured) < 4:
        print("[demo12] too few views for a pano; aborting")
        return 4

    effective_pitches = sorted({v["effective_pitch"] for v in captured})
    used_pitch = float(np.mean(effective_pitches)) if effective_pitches else 0.0
    if len(effective_pitches) > 1:
        print(f"[demo12] warning: views captured at multiple pitches "
              f"{effective_pitches}; using mean {used_pitch:+.1f} deg")

    pano_rgb_result = stitch_pano_views(captured, args.fov_deg, args.spin_step_deg)
    pano_mask_result = stitch_pano_masks(captured, args.fov_deg, args.spin_step_deg)
    if pano_rgb_result is None or pano_mask_result is None:
        print("[demo12] pano stitch failed")
        return 5
    pano_image, headings_per_col = pano_rgb_result
    _, pano_water = pano_mask_result
    print(f"[demo12] pano shape: {pano_image.shape}, water frac "
          f"{float(pano_water.mean()):.3f}")

    best_offset, cand_deg, scores = sweep_pano_heading_offset(
        keypoints, pano_water, headings_per_col, lat, lon,
        pitch_deg=used_pitch, step_deg=1.0,
        tolerance_px=args.tolerance_px,
    )
    print(f"[demo12] best offset = {best_offset:.1f} deg   "
          f"peak score = {scores.max():.3f}   "
          f"flatness (sigma) = {scores.std():.3f}")

    out_pdf = Path(args.out) if args.out else (
        ROOT / "city2stl" / "skyline_cv" / "runs" / "pano_coastline_demo"
        / f"{args.region}_seed{args.seed_index}.pdf"
    )
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out_pdf) as pdf:
        _render_satellite_page(pdf, sat_image, sat_water, sat_project,
                               lat, lon, keypoints, args.region,
                               args.seed_index)
        _render_pano_page(pdf, pano_image, pano_water, headings_per_col,
                          keypoints, lat, lon, best_offset, used_pitch,
                          args.region, args.seed_index)
        _render_horizon_curve_page(pdf, pano_water, headings_per_col,
                                   keypoints, best_offset, used_pitch,
                                   args.region, args.seed_index)
        _render_score_curve_page(pdf, cand_deg, scores, best_offset,
                                 args.region, args.seed_index)
    print(f"[demo12] wrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
