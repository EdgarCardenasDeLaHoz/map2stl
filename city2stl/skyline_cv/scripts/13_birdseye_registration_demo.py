#!/usr/bin/env python3
"""Pano -> bird's-eye -> rotation registration demo (feature-based).

Inverse perspective mapping: render the pano as a top-down canvas
centred on the seed in the same projection as the satellite, then sweep
a 1-D rotation to find the heading offset that best matches building features.

Supports both water-mask IoU (original, limited depth reach) and building-mask
IoU (alternative approach). Use --use-building-mask to test building feature matching.

PDF (4 pages):
  1. Satellite reference (water mask + seed + range rings)
  2. Bird's-eye-rendered mask alone (sample-coverage shown)
  3. Side-by-side at recovered rotation: satellite vs rotated BE mask + IoU
  4. Rotation-search IoU curve

Usage:
    PYTHONPATH=. python city2stl/skyline_cv/scripts/13_birdseye_registration_demo.py \\
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Bird's-eye registration demo (F-SKY11.2)")
    p.add_argument("--region", required=True)
    p.add_argument("--seed-index", type=int, default=5)
    p.add_argument("--out", default=None)
    p.add_argument("--api-key", default=None)
    # Pano canvas: 50 m radius (limits IPM radial distortion).
    # Satellite canvas: 250 m radius (wide enough to show building context).
    # The satellite is rendered wider for visualization and context, but the
    # correlation is computed on the inner overlap where pano has valid data.
    p.add_argument("--canvas-radius-m", type=float, default=50.0,
                   help="Pano bird's-eye radius (kept small to limit IPM distortion)")
    p.add_argument("--sat-canvas-radius-m", type=float, default=250.0,
                   help="Satellite bird's-eye radius (wider for building context)")
    p.add_argument("--m-per-px", type=float, default=0.2)
    p.add_argument("--camera-h-m", type=float, default=1.7)
    p.add_argument("--spin-step-deg", type=float, default=30.0)
    p.add_argument("--fov-deg", type=float, default=75.0)
    p.add_argument("--use-building-mask", action="store_true",
                   help="IPM the BUILDING mask instead of the water mask. "
                        "Buildings are reliably classified by SegFormer at "
                        "distance (water isn't), so the IPM-canvas building "
                        "shape may correlate better with the sat's non-water "
                        "(land) shape — at the cost of a wedge artefact from "
                        "tall buildings (their tops project as foreground).")
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


def _draw_compass(ax, centre_xy, radius):
    """N/E/S/W cardinal markers around a canvas-centred plot."""
    cx, cy = centre_xy
    pad = max(8, int(radius * 0.04))
    for label, dx, dy in [("N", 0, -1), ("E", 1, 0), ("S", 0, 1), ("W", -1, 0)]:
        ax.text(cx + dx * (radius + pad), cy + dy * (radius + pad),
                label, color="white", ha="center", va="center", fontsize=11,
                fontweight="bold",
                bbox=dict(boxstyle="circle,pad=0.15", facecolor="black",
                          edgecolor="white", alpha=0.7))


def _render_satellite_page(pdf, sat_image, sat_water, sat_project,
                           seed_lat, seed_lon, region, seed_index,
                           canvas_radius_m=None):
    """Wide-area satellite + a tight crop matching the bird's-eye
    canvas, so the user sees what region the IPM is actually trying
    to recover.
    """
    fig = plt.figure(figsize=(16, 8))
    fig.suptitle(
        f"Satellite reference  ({region} seed_{seed_index})", fontsize=12)
    # Wide view (full bbox)
    ax_w = fig.add_axes([0.04, 0.05, 0.46, 0.88])
    ax_w.imshow(sat_image)
    cyan = np.zeros((*sat_water.shape, 4), dtype=np.float32)
    cyan[..., 1] = 0.85
    cyan[..., 2] = 1.0
    cyan[..., 3] = np.where(sat_water, 0.40, 0.0)
    ax_w.imshow(cyan)
    sx, sy = sat_project(seed_lon, seed_lat)
    ax_w.scatter([sx], [sy], c="red", marker="*", s=200, zorder=5,
                 edgecolor="white")
    # Mark the canvas-radius crop window on the wide view
    if canvas_radius_m is not None:
        mlat = 110_540.0
        mlon = 111_320.0 * math.cos(math.radians(seed_lat))
        d_lon = canvas_radius_m / mlon
        d_lat = canvas_radius_m / mlat
        x0, _ = sat_project(seed_lon - d_lon, seed_lat)
        x1, _ = sat_project(seed_lon + d_lon, seed_lat)
        _, y0 = sat_project(seed_lon, seed_lat + d_lat)
        _, y1 = sat_project(seed_lon, seed_lat - d_lat)
        ax_w.add_patch(mpatches.Rectangle(
            (min(x0, x1), min(y0, y1)),
            abs(x1 - x0), abs(y1 - y0),
            fill=False, edgecolor="yellow", linewidth=1.4))
    ax_w.set_title(
        f"Wide satellite + water mask (cyan).  red * = seed.  "
        + (f"yellow box = ±{canvas_radius_m:.0f} m birdseye canvas"
           if canvas_radius_m is not None else ""),
        fontsize=9)
    ax_w.axis("off")

    # Tight view (zoom into the canvas window) so the user can see
    # what's actually inside the area the bird's-eye covers.
    if canvas_radius_m is not None:
        ax_t = fig.add_axes([0.52, 0.05, 0.46, 0.88])
        from city2stl.skyline_cv.pano_birdseye import (
            crop_sat_to_seed_canvas)
        sat_canvas, sat_canvas_valid = crop_sat_to_seed_canvas(
            sat_water, sat_project, seed_lat, seed_lon,
            canvas_radius_m=canvas_radius_m, m_per_px=0.1,
        )
        S = sat_canvas.shape[0]
        centre = S // 2
        ax_t.imshow(np.full((S, S, 3), 0.92, dtype=np.float32))
        cyan = np.zeros((S, S, 4), dtype=np.float32)
        cyan[..., 1] = 0.85
        cyan[..., 2] = 1.0
        cyan[..., 3] = np.where(sat_canvas, 0.70, 0.0)
        ax_t.imshow(cyan)
        ax_t.scatter([centre], [centre], c="red", marker="*", s=160,
                     zorder=5, edgecolor="white")
        for r_m in (5, 10, 20, 30):
            r_px = r_m / 0.1
            if r_px > S / 2 + 5:
                continue
            ax_t.add_patch(mpatches.Circle(
                (centre, centre), r_px, fill=False,
                edgecolor=(0.5, 0.5, 0.5, 0.6), linewidth=0.6,
                linestyle="--"))
            ax_t.text(centre + r_px + 4, centre, f"{r_m}m",
                      fontsize=8, color=(0.4, 0.4, 0.4))
        _draw_compass(ax_t, (centre, centre), S * 0.42)
        ax_t.set_title(
            f"Tight view (±{canvas_radius_m:.0f} m) — "
            "this is what the bird's-eye registers against.",
            fontsize=9)
        ax_t.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


def _render_pano_page(pdf, pano_image, region, seed_index):
    """Render the panorama."""
    fig = plt.figure(figsize=(14, 3))
    fig.suptitle(
        f"Panorama source  ({region} seed_{seed_index})",
        fontsize=11)
    ax = fig.add_axes([0.05, 0.05, 0.90, 0.90])
    ax.imshow(pano_image)
    ax.set_title("360° stitched panorama", fontsize=10)
    ax.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


def _render_birdseye_page(pdf, pano_be_image, sat_be_image_wide, pano_be_valid,
                          sat_be_valid_wide, pano_canvas_radius_m,
                          sat_canvas_radius_m, m_per_px, region, seed_index):
    S_pano = pano_be_image.shape[0]
    S_sat = sat_be_image_wide.shape[0]
    centre_pano = S_pano // 2
    centre_sat = S_sat // 2
    fig = plt.figure(figsize=(15, 7))
    fig.suptitle(
        f"Bird's-eye projections ({region} seed_{seed_index}, "
        f"pano radius={pano_canvas_radius_m:.0f}m, sat radius={sat_canvas_radius_m:.0f}m, "
        f"{m_per_px:.2f} m/px)",
        fontsize=11,
    )

    # Left: pano bird's-eye
    ax_l = fig.add_axes([0.04, 0.08, 0.45, 0.88])
    pano_display = np.where(pano_be_valid[..., None], pano_be_image, 0)
    ax_l.imshow(pano_display)
    ax_l.scatter([centre_pano], [centre_pano], c="red", marker="*", s=120,
                 zorder=5, edgecolor="white")
    _draw_compass(ax_l, (centre_pano, centre_pano), S_pano * 0.42)
    ax_l.set_title(f"Pano bird's-eye (pano frame, ±{pano_canvas_radius_m:.0f}m)",
                   fontsize=10)
    ax_l.axis("off")

    # Right: WIDE satellite bird's-eye with pano-canvas box overlay
    ax_r = fig.add_axes([0.51, 0.08, 0.45, 0.88])
    ax_r.imshow(sat_be_image_wide.astype(np.uint8))
    ax_r.scatter([centre_sat], [centre_sat], c="red", marker="*", s=120,
                 zorder=5, edgecolor="white")
    _draw_compass(ax_r, (centre_sat, centre_sat), S_sat * 0.42)
    # Draw pano-canvas box for context
    pano_half_px = pano_canvas_radius_m / m_per_px
    ax_r.add_patch(mpatches.Rectangle(
        (centre_sat - pano_half_px, centre_sat - pano_half_px),
        2 * pano_half_px, 2 * pano_half_px,
        fill=False, edgecolor="yellow", linewidth=1.5, linestyle="--"))
    ax_r.set_title(f"Satellite bird's-eye (±{sat_canvas_radius_m:.0f}m).  "
                   f"Yellow box = pano canvas overlap (±{pano_canvas_radius_m:.0f}m)",
                   fontsize=10)
    ax_r.axis("off")

    pdf.savefig(fig)
    plt.close(fig)


def _render_sidebyside_page(pdf, pano_be_image, sat_be_image,
                            best_offset_deg, peak_score,
                            region, seed_index):
    from scipy.ndimage import rotate as _rot
    S = pano_be_image.shape[0]
    centre = S // 2

    # Rotate pano image to best alignment
    rotated = np.zeros_like(pano_be_image)
    for c in range(3):
        rotated[..., c] = _rot(
            pano_be_image[..., c].astype(np.float32),
            -float(best_offset_deg), order=1, reshape=False,
            mode="constant", cval=0.0
        )

    fig = plt.figure(figsize=(15, 5))
    fig.suptitle(
        f"At recovered rotation = {best_offset_deg:.1f} deg  "
        f"correlation = {peak_score:.3f}  ({region} seed_{seed_index})",
        fontsize=12,
    )

    # Left: satellite
    ax_l = fig.add_axes([0.04, 0.08, 0.30, 0.80])
    ax_l.imshow(sat_be_image.astype(np.uint8))
    ax_l.scatter([centre], [centre], c="red", marker="*", s=100,
                 edgecolor="white", zorder=5)
    _draw_compass(ax_l, (centre, centre), S * 0.42)
    ax_l.set_title("Satellite (geographic)", fontsize=10)
    ax_l.axis("off")

    # Middle: rotated pano
    ax_m = fig.add_axes([0.36, 0.08, 0.30, 0.80])
    ax_m.imshow(np.clip(rotated, 0, 255).astype(np.uint8))
    ax_m.scatter([centre], [centre], c="red", marker="*", s=100,
                 edgecolor="white", zorder=5)
    _draw_compass(ax_m, (centre, centre), S * 0.42)
    ax_m.set_title("Pano rotated to best", fontsize=10)
    ax_m.axis("off")

    # Right: blend
    ax_r = fig.add_axes([0.68, 0.08, 0.30, 0.80])
    blend = (sat_be_image.astype(np.float32) + np.clip(rotated, 0, 255)) / 2.0
    ax_r.imshow(blend.astype(np.uint8))
    ax_r.scatter([centre], [centre], c="red", marker="*", s=100,
                 edgecolor="white", zorder=5)
    _draw_compass(ax_r, (centre, centre), S * 0.42)
    ax_r.set_title("Blended (should look consistent)", fontsize=10)
    ax_r.axis("off")

    pdf.savefig(fig)
    plt.close(fig)


def _render_score_curve_page(pdf, cand_deg, scores, best_offset_deg,
                             region, seed_index):
    fig = plt.figure(figsize=(14, 5.5))
    fig.suptitle(
        f"Image correlation curve  ({region} seed_{seed_index})",
        fontsize=12)
    ax = fig.add_axes([0.07, 0.18, 0.88, 0.65])
    ax.plot(cand_deg, scores, color=(0.10, 0.30, 0.55, 0.95), linewidth=1.4)
    ax.axvline(best_offset_deg, color="green", linestyle="-", linewidth=1.4,
               label=f"recovered offset = {best_offset_deg:.1f} deg")
    ax.set_xlabel("candidate rotation (deg)", fontsize=10)
    ax.set_ylabel("Cross-correlation (mean across RGB)", fontsize=10)
    ax.set_title(
        f"Peak correlation = {scores.max():.3f}  "
        f"sigma = {scores.std():.3f}  "
        "Sharp single peak => confident; flat / multi-modal => poor alignment.",
        fontsize=9)
    ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.25)
    pdf.savefig(fig)
    plt.close(fig)


def main() -> int:
    args = build_parser().parse_args()

    from city2stl.skyline_cv.pipeline import _ensure_segformer as _ens
    sf_ok = _ens()
    print(f"[demo13] SegFormer load: ok={sf_ok}")
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
    from city2stl.skyline_cv.coastline_registration import detect_sat_water_mask
    from city2stl.skyline_cv.pano_birdseye import (
        pano_image_to_birdseye, satellite_image_to_birdseye,
        register_by_image_correlation, pano_to_birdseye,
    )

    api_key = _resolve_api_key(args.api_key)
    bbox = _load_region_bbox(args.region)
    sat_image, sat_project, sat_meta = fetch_region_satellite(
        (bbox.south, bbox.west, bbox.north, bbox.east), target_m_per_px=2.0,
    )
    print(f"[demo13] sat image: zoom={sat_meta['zoom']} "
          f"shape={sat_meta['shape']}")
    sat_water = detect_sat_water_mask(sat_image)

    seed_urls = _load_site_seed_urls(args.region)
    if not seed_urls or args.seed_index < 1 or args.seed_index > len(seed_urls):
        print(f"[demo13] invalid seed-index {args.seed_index}")
        return 2
    parsed = _parse_streetview_url(seed_urls[args.seed_index - 1])
    if parsed is None:
        return 3
    lat, lon, _base_heading, fov, pitch, pano_id = parsed
    lat, lon, pano_id = _load_resolved_seed(
        args.region, args.seed_index, lat, lon, pano_id)
    print(f"[demo13] seed_{args.seed_index} @ {lat:.5f},{lon:.5f}  "
          f"pano={pano_id}  url_pitch={pitch:+.1f}")

    # Capture the 12 spin views + masks, mirroring the production flow.
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
            print(f"[demo13] heading {hdg:.0f}: no image")
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
    print(f"[demo13] captured {len(captured)} views")
    if len(captured) < 4:
        return 4

    eff_pitches = sorted({v["effective_pitch"] for v in captured})
    used_pitch = float(np.mean(eff_pitches)) if eff_pitches else 0.0
    pano_rgb_result = stitch_pano_views(captured, args.fov_deg, args.spin_step_deg)
    pano_mask_result = stitch_pano_masks(captured, args.fov_deg, args.spin_step_deg)
    if pano_rgb_result is None or pano_mask_result is None:
        print("[demo13] pano stitch failed")
        return 5
    pano_image, headings_per_col = pano_rgb_result
    pano_building, pano_water = pano_mask_result
    print(f"[demo13] pano: shape={pano_image.shape}  "
          f"water_frac={float(pano_water.mean()):.3f}  "
          f"building_frac={float(pano_building.mean()):.3f}  "
          f"used_pitch={used_pitch:+.1f}")

    view_width_px = captured[0]["image"].shape[1]
    print(f"[demo13] IPM pano and satellite to bird's-eye view...")
    pano_be_image, pano_be_valid = pano_image_to_birdseye(
        pano_image, headings_per_col,
        fov_deg_for_x=args.fov_deg,
        view_width_px=view_width_px,
        pitch_deg=used_pitch,
        camera_h_m=args.camera_h_m,
        canvas_radius_m=args.canvas_radius_m,
        m_per_px=args.m_per_px,
    )
    print(f"[demo13] pano bird's-eye: shape={pano_be_image.shape}  "
          f"valid_frac={float(pano_be_valid.mean()):.3f}")

    # Render satellite at LARGER radius for context (buildings further out)
    sat_be_image_wide, sat_be_valid_wide = satellite_image_to_birdseye(
        sat_image, sat_project, lat, lon,
        canvas_radius_m=args.sat_canvas_radius_m,
        m_per_px=args.m_per_px,
    )
    print(f"[demo13] sat bird's-eye (wide): shape={sat_be_image_wide.shape}  "
          f"valid_frac={float(sat_be_valid_wide.mean()):.3f}  "
          f"radius={args.sat_canvas_radius_m}m")

    # For correlation, crop satellite to match pano canvas size (same center)
    pano_S = pano_be_image.shape[0]
    sat_S = sat_be_image_wide.shape[0]
    sat_centre = sat_S // 2
    half = pano_S // 2
    sat_be_image = sat_be_image_wide[
        sat_centre - half : sat_centre + half + 1,
        sat_centre - half : sat_centre + half + 1,
    ]
    sat_be_valid = sat_be_valid_wide[
        sat_centre - half : sat_centre + half + 1,
        sat_centre - half : sat_centre + half + 1,
    ]
    print(f"[demo13] sat bird's-eye (correlation crop): shape={sat_be_image.shape}")

    best, cand_deg, scores = register_by_image_correlation(
        pano_be_image, pano_be_valid, sat_be_image, sat_be_valid,
        step_deg=1.0,
    )
    print(f"[demo13] recovered offset = {best:.1f} deg  "
          f"peak correlation = {scores.max():.3f}  sigma = {scores.std():.3f}")

    out_pdf = Path(args.out) if args.out else (
        ROOT / "city2stl" / "skyline_cv" / "runs" / "birdseye_demo"
        / f"{args.region}_seed{args.seed_index}.pdf"
    )
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out_pdf) as pdf:
        _render_satellite_page(pdf, sat_image, sat_water, sat_project,
                               lat, lon, args.region, args.seed_index,
                               canvas_radius_m=args.canvas_radius_m)
        _render_pano_page(pdf, pano_image, args.region, args.seed_index)
        _render_birdseye_page(pdf, pano_be_image, sat_be_image_wide,
                              pano_be_valid, sat_be_valid_wide,
                              args.canvas_radius_m, args.sat_canvas_radius_m,
                              args.m_per_px, args.region, args.seed_index)
        _render_sidebyside_page(pdf, pano_be_image, sat_be_image, best,
                                float(scores.max()),
                                args.region, args.seed_index)
        _render_score_curve_page(pdf, cand_deg, scores, best,
                                 args.region, args.seed_index)
    print(f"[demo13] wrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
