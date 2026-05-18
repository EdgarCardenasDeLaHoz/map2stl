#!/usr/bin/env python3
"""Image-level coastline-alignment demo (F-SKY11 inspector).

Replaces the per-building cross-view diagnostic (script 10) with the
image-level registration approach: detect water in the satellite + the
street view, compare radial signatures, recover the best heading.

The PDF has three panel types per seed view:

  Page 1 (per seed)
    - Satellite image with overlaid water mask (cyan)
    - Coastline boundary traced from the mask
    - Seed position marked, rings at 250 m / 500 m / 1 km / 2 km
    - Radial water-distance signature on a separate axis (polar) so
      you can see which compass directions the seed has open water

  Page 2..N (per heading)
    - Top:    Street view image with SV water mask overlay (cyan)
    - Mid:    Per-column "water present at bottom" bar (lights up
              columns whose bottom band is mostly water)
    - Bottom: Heading-alignment score curve over ±180° around the
              captured heading, with the original heading and the
              best-alignment heading marked. A sharp peak means
              confident recovery; flat = no coastline structure
              visible.

Usage:
    PYTHONPATH=. python city2stl/skyline_cv/scripts/11_coastline_demo.py \\
        --region Cartagena --seed-index 5 [--heading 320]
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
        description="Coastline-alignment registration inspector (F-SKY11)")
    p.add_argument("--region", required=True)
    p.add_argument("--seed-index", type=int, default=5)
    p.add_argument("--heading", type=float, default=None,
                   help="Single heading in degrees; default = full 12-view spin")
    p.add_argument("--out", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--search-deg", type=float, default=180.0,
                   help="Heading-search range each side of captured heading "
                        "(default 180 = full sweep)")
    p.add_argument("--max-range-m", type=float, default=2500.0,
                   help="Max distance to walk each radial bearing in the "
                        "satellite when building the water signature")
    return p


def _load_resolved_seed(region: str, seed_index: int, lat: float, lon: float,
                        pano_id: str | None) -> tuple[float, float, str | None]:
    """Honor the main pipeline's resolved-pano cache so the demo's
    fetched street view actually returns pixels."""
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


_KEYPOINT_PALETTE = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe",
    "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000",
    "#aaffc3", "#808000", "#ffd8b1", "#000075", "#808080",
]


def _kp_color(i: int) -> str:
    return _KEYPOINT_PALETTE[i % len(_KEYPOINT_PALETTE)]


def _render_satellite_page(
    pdf, sat_image, sat_water_mask, sat_project,
    seed_lat, seed_lon, sat_signature, region, seed_index,
    sat_signature_zones=None, keypoints=None,
):
    fig = plt.figure(figsize=(14, 9))
    fig.suptitle(
        f"Coastline reference — {region} seed_{seed_index}  "
        f"(satellite water mask + radial signature)",
        fontsize=12,
    )

    # Left: satellite with water mask + seed + rings
    ax = fig.add_axes([0.04, 0.08, 0.55, 0.82])
    ax.imshow(sat_image)
    # Water overlay
    cyan = np.zeros((*sat_water_mask.shape, 4), dtype=np.float32)
    cyan[..., 1] = 0.85
    cyan[..., 2] = 1.0
    cyan[..., 3] = np.where(sat_water_mask, 0.40, 0.0)
    ax.imshow(cyan)

    seed_px = sat_project(seed_lon, seed_lat)
    ax.scatter([seed_px[0]], [seed_px[1]], c="red",
               marker="*", s=200, zorder=5, edgecolor="white")
    # Standoff rings in pixels: convert metres to pixel radius using a
    # local linearization at the seed (good enough for a few km).
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(seed_lat))
    ring_px_per_m = 0.5 * (
        (sat_project(seed_lon + 1.0 / mlon, seed_lat)[0] - seed_px[0])
        + (seed_px[1] - sat_project(seed_lon, seed_lat + 1.0 / mlat)[1])
    )
    for r_m, label in [(250, "250m"), (500, "500m"),
                       (1000, "1km"), (2000, "2km")]:
        r_px = abs(ring_px_per_m) * r_m
        circ = mpatches.Circle(
            (seed_px[0], seed_px[1]), r_px,
            fill=False, edgecolor=(1, 1, 1, 0.6), linewidth=0.8,
            linestyle="--",
        )
        ax.add_patch(circ)
        ax.text(seed_px[0] + r_px + 5, seed_px[1], label,
                color="white", fontsize=7, va="center")
    # Numbered coastline key points (water->land transitions per bearing)
    if keypoints:
        for i, kp in enumerate(keypoints):
            color = _kp_color(i)
            ax.scatter([kp["x_sat"]], [kp["y_sat"]], s=70, marker="o",
                       facecolor=color, edgecolor="black", linewidth=0.8,
                       zorder=6)
            # Tiny offset label so the digit isn't covered by the dot
            ax.text(kp["x_sat"] + 6, kp["y_sat"] - 6, str(i + 1),
                    fontsize=7, color=color, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                              edgecolor=color, alpha=0.85),
                    zorder=6)
    ax.set_title(
        "Satellite + water mask (cyan)  "
        "*=seed  rings=250/500/1k/2k m  "
        "numbered dots = coastline key points (water->land transitions)",
        fontsize=8,
    )
    ax.axis("off")

    # Right: polar plot of distance-to-land per bearing
    ax_polar = fig.add_axes([0.63, 0.18, 0.34, 0.66], projection="polar")
    # Polar plot convention: 0° at North, clockwise. matplotlib default
    # is 0° east, CCW — flip to compass orientation.
    ax_polar.set_theta_zero_location("N")
    ax_polar.set_theta_direction(-1)
    bearings_rad = np.radians(
        np.linspace(0.0, 360.0, sat_signature.size, endpoint=False))
    if sat_signature_zones is not None and len(sat_signature_zones) >= 2:
        near, far = sat_signature_zones[0], sat_signature_zones[1]
        ax_polar.plot(bearings_rad, near, color=(0, 0.4, 0.7),
                      linewidth=1.2, label="near 10-80 m")
        ax_polar.fill(bearings_rad, near, color=(0, 0.4, 0.7, 0.20))
        ax_polar.plot(bearings_rad, far, color=(0.85, 0.45, 0.10),
                      linewidth=1.0, label="far 150-600 m")
        ax_polar.legend(loc="lower right", fontsize=7,
                        bbox_to_anchor=(1.30, -0.05))
    else:
        ax_polar.plot(bearings_rad, sat_signature,
                      color=(0, 0.4, 0.7), linewidth=1.2)
        ax_polar.fill(bearings_rad, sat_signature, color=(0, 0.4, 0.7, 0.25))
    # Mark each key-point bearing with a small line at its color so the
    # polar plot ties back to the numbered satellite dots.
    if keypoints:
        for i, kp in enumerate(keypoints):
            theta = math.radians(kp["bearing_deg"])
            ax_polar.plot([theta, theta], [0.0, 1.02],
                          color=_kp_color(i), linewidth=1.0, alpha=0.7)
    ax_polar.set_title(
        "Water fraction per bearing  "
        "(near = 10-80 m, far = 150-600 m)",
        fontsize=9, pad=18,
    )
    ax_polar.set_ylim(0, 1.05)
    ax_polar.grid(alpha=0.30)

    pdf.savefig(fig)
    plt.close(fig)


def _render_heading_page(
    pdf, sv_image, sv_water_mask, sv_signature, scan_cand_deg, scan_scores,
    captured_heading_deg, best_heading_deg, region, seed_index,
    keypoints=None, seed_lat=None, seed_lon=None, fov_deg=75.0,
    pitch_deg=0.0,
    kp_scan_cand_deg=None, kp_scan_scores=None, kp_best_heading_deg=None,
):
    fig = plt.figure(figsize=(14, 9))
    fig.suptitle(
        f"Heading recovery — {region} seed_{seed_index}  "
        f"captured heading = {captured_heading_deg:.1f}°  "
        f"best-alignment heading = {best_heading_deg:.1f}°  "
        f"Δ={(best_heading_deg - captured_heading_deg + 540) % 360 - 180:+.1f}°",
        fontsize=11,
    )

    # Panel 1: street view + water-mask overlay + projected key points
    ax_sv = fig.add_axes([0.04, 0.55, 0.92, 0.35])
    ax_sv.imshow(sv_image)
    if sv_water_mask is not None:
        cyan = np.zeros((*sv_water_mask.shape, 4), dtype=np.float32)
        cyan[..., 1] = 0.85
        cyan[..., 2] = 1.0
        cyan[..., 3] = np.where(sv_water_mask, 0.40, 0.0)
        ax_sv.imshow(cyan)
    # Project each coastline key point into the SV at the heading the
    # SPARSE (keypoint) scorer picked — that's the heading at which the
    # keypoint overlay is supposed to land on actual coastline pixels.
    # When the sparse choice differs from the dense choice the user can
    # tell at a glance which is the better fit by reading the picture.
    proj_heading_deg = (
        kp_best_heading_deg if kp_best_heading_deg is not None
        else best_heading_deg
    )
    n_projected = 0
    if keypoints and seed_lat is not None and seed_lon is not None:
        from city2stl.skyline_cv.coastline_registration import (
            project_lonlat_to_view,
        )
        h_sv, w_sv = sv_image.shape[:2]
        for i, kp in enumerate(keypoints):
            xy = project_lonlat_to_view(
                kp["lon"], kp["lat"], seed_lat, seed_lon,
                heading_deg=proj_heading_deg, fov_deg=fov_deg,
                image_width=w_sv, image_height=h_sv,
                pitch_deg=pitch_deg,
            )
            if xy is None:
                continue
            x_px, y_px = xy
            color = _kp_color(i)
            # Vertical guide + a circular dot at the predicted (x, y)
            # so the user sees BOTH the column AND the expected horizon
            # y. A correctly-recovered heading + pitch puts the dot on
            # top of the actual far-shore line in the cyan water mask.
            ax_sv.axvline(x_px, color=color, linewidth=0.8, alpha=0.5)
            ax_sv.scatter([x_px], [y_px], s=60, marker="o",
                          facecolor=color, edgecolor="black",
                          linewidth=0.8, zorder=6)
            ax_sv.text(x_px + 2, 8, str(i + 1), color=color, fontsize=8,
                       fontweight="bold",
                       bbox=dict(boxstyle="round,pad=0.15",
                                 facecolor="white", edgecolor=color,
                                 alpha=0.85))
            n_projected += 1
    ax_sv.set_title(
        f"Street view + SegFormer water mask (cyan)  "
        f"+ {n_projected} coastline key points projected at "
        f"sparse-best heading = {proj_heading_deg:.1f} deg",
        fontsize=9,
    )
    ax_sv.axis("off")

    # Panel 2: per-column "water present at bottom" bar (very thin)
    ax_bar = fig.add_axes([0.04, 0.42, 0.92, 0.08])
    present = sv_signature["water_present"].astype(np.float32)
    cov = sv_signature["coverage_frac"]
    xs = np.arange(present.size)
    ax_bar.fill_between(xs, 0.0, cov, color=(0.2, 0.55, 0.75, 0.45),
                        linewidth=0)
    ax_bar.fill_between(xs, 0.0, present * 1.0,
                        color=(0.0, 0.7, 0.0, 0.35), linewidth=0)
    ax_bar.set_xlim(0, present.size - 1)
    ax_bar.set_ylim(0, 1.05)
    ax_bar.set_xlabel("column x (px)", fontsize=8)
    ax_bar.set_ylabel("water frac", fontsize=8)
    ax_bar.tick_params(labelsize=7)
    ax_bar.set_title(
        "Per-column water-at-bottom coverage  "
        "(blue = raw coverage,  green = ≥20% threshold)",
        fontsize=9,
    )
    ax_bar.grid(alpha=0.20)

    # Panel 3: heading-search score curve(s). Dense and (when supplied)
    # sparse-keypoint scores plotted together so the user can tell which
    # signal is informative for this view.
    ax_sc = fig.add_axes([0.04, 0.06, 0.92, 0.28])
    ax_sc.plot(scan_cand_deg, scan_scores, color=(0.10, 0.30, 0.55, 0.95),
               linewidth=1.2, label="dense (per-column water cov)")
    if kp_scan_cand_deg is not None and kp_scan_scores is not None:
        ax_sc.plot(kp_scan_cand_deg, kp_scan_scores,
                   color=(0.65, 0.10, 0.10, 0.95), linewidth=1.2,
                   label="sparse (keypoint transitions)")
    ax_sc.axvline(captured_heading_deg, color="orange", linestyle="--",
                  linewidth=1.0,
                  label=f"captured = {captured_heading_deg:.1f} deg")
    ax_sc.axvline(best_heading_deg, color="green", linestyle="-",
                  linewidth=1.2,
                  label=f"dense best = {best_heading_deg:.1f} deg")
    if kp_best_heading_deg is not None:
        ax_sc.axvline(kp_best_heading_deg, color="purple", linestyle=":",
                      linewidth=1.5,
                      label=f"sparse best = {kp_best_heading_deg:.1f} deg")
    ax_sc.set_xlabel("Candidate heading (deg)", fontsize=9)
    ax_sc.set_ylabel("Alignment score", fontsize=9)
    ax_sc.set_title(
        "Heading-alignment scores  "
        "(dense: pattern-match every column;  "
        "sparse: line up named coastline transitions)",
        fontsize=10,
    )
    ax_sc.legend(loc="best", fontsize=8, ncol=2)
    ax_sc.grid(alpha=0.25)

    pdf.savefig(fig)
    plt.close(fig)


def main() -> int:
    args = build_parser().parse_args()

    from city2stl.skyline_cv.region_pdf import (
        _resolve_api_key, _load_region_bbox, _load_site_seed_urls,
        _parse_streetview_url, _streetview_image,
    )
    from city2stl.skyline_cv.pipeline import _neural_sky_and_building_masks  # noqa: F401
    from city2stl.skyline_cv.pipeline import _neural_water_mask
    from city2stl.skyline_cv.satellite_image import fetch_region_satellite
    from city2stl.skyline_cv.coastline_registration import (
        detect_sat_water_mask, build_sat_radial_signature,
        build_sat_radial_signature_zones, build_sv_radial_signature,
        sweep_heading, sweep_heading_keypoints, detect_coastline_keypoints,
    )

    # Pre-load SegFormer early so the silent except inside
    # _neural_sky_and_building_masks doesn't catch the load failure mid-loop
    # and leave the demo running with all-None water masks.
    from city2stl.skyline_cv.pipeline import _ensure_segformer as _ens
    sf_ok = _ens()
    print(f"[demo11] SegFormer load: ok={sf_ok}")

    api_key = _resolve_api_key(args.api_key)
    bbox = _load_region_bbox(args.region)
    sat_image, sat_project, sat_meta = fetch_region_satellite(
        (bbox.south, bbox.west, bbox.north, bbox.east), target_m_per_px=2.0,
    )
    print(f"[demo11] sat image: zoom={sat_meta['zoom']} "
          f"shape={sat_meta['shape']}")

    sat_water = detect_sat_water_mask(sat_image)
    water_frac = float(sat_water.mean())
    print(f"[demo11] sat water mask: {water_frac:.1%} water pixels")

    seed_urls = _load_site_seed_urls(args.region)
    if not seed_urls or args.seed_index < 1 or args.seed_index > len(seed_urls):
        print(f"[demo11] invalid seed-index {args.seed_index}")
        return 2
    parsed = _parse_streetview_url(seed_urls[args.seed_index - 1])
    if parsed is None:
        print("[demo11] failed to parse seed URL")
        return 3
    lat, lon, base_heading, fov, pitch, pano_id = parsed
    lat, lon, pano_id = _load_resolved_seed(
        args.region, args.seed_index, lat, lon, pano_id)
    print(f"[demo11] seed_{args.seed_index} @ {lat:.5f},{lon:.5f}  pano={pano_id}")

    sat_signature = build_sat_radial_signature(
        sat_water, sat_project, lat, lon,
        n_bearings=360, near_range_m=(10.0, 80.0), step_m=5.0,
    )
    _, sat_signature_zones = build_sat_radial_signature_zones(
        sat_water, sat_project, lat, lon,
        n_bearings=360, zones_m=((10.0, 80.0), (150.0, 600.0)), step_m=8.0,
    )
    n_open_dirs = int((sat_signature > 0.5).sum())
    print(f"[demo11] radial signature: {n_open_dirs}/360 bearings have "
          f">=50%% near-range water (10-80 m window)")
    # 24 bearings = 15 deg between samples. Coarse enough that
    # numbered dots don't crowd the satellite image, dense enough that
    # the prominent coastline features (Bocagrande shore, Manga,
    # Castillo Grande tip) each land on their own bearing.
    keypoints = detect_coastline_keypoints(
        sat_water, sat_project, lat, lon,
        n_bearings=24, max_range_m=2500.0, step_m=5.0, min_distance_m=30.0,
    )
    print(f"[demo11] detected {len(keypoints)} coastline key points "
          "(water->land transitions, 1 per 15 deg of bearing)")

    out_pdf = Path(args.out) if args.out else (
        ROOT / "city2stl" / "skyline_cv" / "runs" / "coastline_demo"
        / f"{args.region}_seed{args.seed_index}.pdf"
    )
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    headings: list[float] = (
        [float(args.heading)] if args.heading is not None
        else [float(h) for h in np.arange(0.0, 360.0, 30.0)]
    )

    with PdfPages(out_pdf) as pdf:
        _render_satellite_page(
            pdf, sat_image, sat_water, sat_project, lat, lon,
            sat_signature, args.region, args.seed_index,
            sat_signature_zones=sat_signature_zones,
            keypoints=keypoints,
        )
        for hdg in headings:
            image = _streetview_image(
                api_key, lat, lon, hdg, fov=fov, pitch=pitch,
                pano_id=pano_id, pano_only=(pano_id is not None),
            )
            if image is None:
                for fallback_pitch in (min(pitch + 12.0, 15.0), 0.0):
                    image = _streetview_image(
                        api_key, lat, lon, hdg, fov=fov,
                        pitch=fallback_pitch, pano_id=pano_id,
                        pano_only=(pano_id is not None),
                    )
                    if image is not None:
                        break
            if image is None:
                print(f"[demo11] heading {hdg:.0f}: no image")
                continue
            sv_water = _neural_water_mask(image)
            if sv_water is None:
                print(f"[demo11] heading {hdg:.0f}: SegFormer water mask "
                      "unavailable (model load failed earlier)")
                continue
            sv_sig = build_sv_radial_signature(
                sv_water, image.shape[1], image.shape[0], fov, hdg,
            )
            best_deg, cand_deg, scores = sweep_heading(
                sat_signature, sv_sig, hdg,
                search_range_deg=args.search_deg, step_deg=1.0,
            )
            kp_best_deg, kp_cand_deg, kp_scores = sweep_heading_keypoints(
                keypoints, sv_water, lat, lon, hdg,
                image.shape[1], image.shape[0],
                fov_deg=fov, pitch_deg=pitch,
                search_range_deg=args.search_deg, step_deg=1.0,
                tolerance_px=25,
            )
            print(f"[demo11] heading {hdg:.0f}: "
                  f"dense best={best_deg:.1f} ({scores.max():.3f}) "
                  f"sparse best={kp_best_deg:.1f} ({kp_scores.max():.3f})")
            _render_heading_page(
                pdf, image, sv_water, sv_sig, cand_deg, scores,
                hdg, best_deg, args.region, args.seed_index,
                keypoints=keypoints, seed_lat=lat, seed_lon=lon,
                fov_deg=fov, pitch_deg=pitch,
                kp_scan_cand_deg=kp_cand_deg, kp_scan_scores=kp_scores,
                kp_best_heading_deg=kp_best_deg,
            )

    print(f"[demo11] wrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
