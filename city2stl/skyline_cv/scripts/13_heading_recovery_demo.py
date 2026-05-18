#!/usr/bin/env python3
"""Consolidated heading recovery demo.

Lessons from prior attempts (now retired):
  - F-SKY11.1 keypoints (script 12 still present as baseline): sharp where
    coastline keypoints exist, but only ~20 samples, mostly idle elsewhere.
  - F-SKY11.2 IPM bird's-eye (deleted): assumes flat ground, fails for
    tall buildings — they're sampled as sky, canvas collapses.
  - F-SKY11.3 dense water-fraction (deleted): all 360° but signal is
    low-frequency (a bay seen for a continuous arc), so the peak is
    broad (~50-100° HWHM).

This demo combines the working parts:
  - Dense 360° per-bearing analysis (from 11.3).
  - Multi-channel signature (water + buildings + edge derivatives) so
    high-frequency channels sharpen the low-frequency water-fraction
    peak.
  - FFT-based circular Pearson correlation per channel, then mean.
  - Sub-degree parabolic peak refinement.
  - One PDF output, one folder (``runs/heading_recovery/``).

PDF pages:
  1. Satellite reference (water mask, seed marked).
  2. Pano + water + building mask overlays, with column→bearing axis
     drawn using the recovered offset.
  3. Per-channel signature overlays (sat vs pano-aligned).
  4. Per-channel correlation curves + combined.

Usage:
    PYTHONPATH=. python city2stl/skyline_cv/scripts/13_heading_recovery_demo.py \\
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
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Consolidated heading recovery demo")
    p.add_argument("--region", required=True)
    p.add_argument("--seed-index", type=int, default=5)
    p.add_argument("--out", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--spin-step-deg", type=float, default=30.0)
    p.add_argument("--fov-deg", type=float, default=75.0)
    p.add_argument("--n-bearings", type=int, default=360)
    p.add_argument("--near-range", type=str, default="10,80",
                   help="Satellite near-range window in metres, comma-separated")
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


# ---------------------------------------------------------------------------
# Signature builders (kept inline so the demo is self-contained and we can
# easily tweak any of them per-experiment).
# ---------------------------------------------------------------------------


def _pano_per_bearing_mean(
    mask: np.ndarray, headings_per_col: np.ndarray, n_bearings: int,
    y_top: int, y_bot: int,
) -> np.ndarray:
    """Mean of `mask[y_top:y_bot, columns_in_bin]` per bearing bin."""
    H, W = mask.shape
    band = mask[y_top:y_bot, :].astype(np.float32)
    per_col = band.mean(axis=0) if band.size else np.zeros(W, dtype=np.float32)
    bearings = np.linspace(0.0, 360.0, n_bearings, endpoint=False)
    bin_w = 360.0 / n_bearings
    out = np.zeros(n_bearings, dtype=np.float32)
    h_mod = np.asarray(headings_per_col, dtype=np.float32) % 360.0
    for i, t in enumerate(bearings):
        d = np.abs(((h_mod - t + 180.0) % 360.0) - 180.0)
        m = d <= (bin_w * 0.5 + 1e-3)
        out[i] = per_col[m].mean() if m.any() else per_col[int(np.argmin(d))]
    return out


def build_pano_signatures(
    pano_water: np.ndarray, pano_building: np.ndarray,
    headings_per_col: np.ndarray,
    *, n_bearings: int, camera_h_m: float, pitch_deg: float,
    fov_deg: float, view_width_px: int,
    near_range_m: tuple[float, float] = (10.0, 80.0),
    far_range_m: tuple[float, float] = (100.0, 400.0),
) -> dict:
    """Multi-channel per-bearing pano signature.

    Two distance-matched bands (so cross-correlation against the satellite's
    near/far rings is comparing the same physical features):

      water        — fraction of pixels classified as water in the y-band
                     corresponding to near_range_m (default 10-80m).
      water_far    — same, for far_range_m (default 100-400m). Far water
                     sits just below the horizon and is high-frequency
                     because it cuts off at land bearings sharply.
      d_water      — circular derivative of `water` (edge channel).
      d_water_far  — circular derivative of `water_far`.
    """
    del camera_h_m, pitch_deg, fov_deg, view_width_px
    del near_range_m, far_range_m
    H = pano_water.shape[0]
    # Heuristic split of the lower half of the pano. The pitch-aware
    # version was too narrow and missed water when pitch metadata didn't
    # match the visual horizon (happens for half the Cartagena seeds).
    #   far band  = thin strip across the visual horizon (~46%-70% of H)
    #   near band = bottom (~70%-95% of H)
    y_mid = int(round(H * 0.50))
    yt_far = max(0, y_mid - int(round(H * 0.04)))
    yb_far = min(H, y_mid + int(round(H * 0.20)))
    yt_near = yb_far
    yb_near = min(H, int(round(H * 0.95)))

    water = _pano_per_bearing_mean(pano_water, headings_per_col,
                                    n_bearings, yt_near, yb_near)
    water_far = _pano_per_bearing_mean(pano_water, headings_per_col,
                                        n_bearings, yt_far, yb_far)
    d_water = np.roll(water, -1) - np.roll(water, 1)
    d_water_far = np.roll(water_far, -1) - np.roll(water_far, 1)
    return {
        "water": water, "water_far": water_far,
        "d_water": d_water, "d_water_far": d_water_far,
        "y_top_near": yt_near, "y_bot_near": yb_near,
        "y_top_far": yt_far, "y_bot_far": yb_far,
        # Keep legacy keys for the renderer that draws band lines.
        "y_top": min(yt_far, yt_near), "y_bot": max(yb_near, yb_far),
    }


def _ray_walk_first(
    sat_mask: np.ndarray, sat_project, seed_lat: float, seed_lon: float,
    bearing_deg: float, max_m: float, step_m: float, want_value: bool = True,
) -> float:
    """Walk along the bearing from the seed; return distance in metres where
    `sat_mask` first equals `want_value`, or `max_m` if never."""
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(seed_lat))
    br = math.radians(bearing_deg)
    sin_b, cos_b = math.sin(br), math.cos(br)
    h, w = sat_mask.shape[:2]
    n = int(max_m / step_m) + 1
    for i in range(1, n + 1):
        d = i * step_m
        lon = seed_lon + d * sin_b / mlon
        lat = seed_lat + d * cos_b / mlat
        px, py = sat_project(float(lon), float(lat))
        ix, iy = int(px), int(py)
        if not (0 <= ix < w and 0 <= iy < h):
            return max_m
        if bool(sat_mask[iy, ix]) == want_value:
            return d
    return max_m


def build_sat_signatures(
    sat_water: np.ndarray, sat_project, seed_lat: float, seed_lon: float,
    *, n_bearings: int, near_range_m: tuple[float, float], step_m: float = 5.0,
    far_water_range_m: tuple[float, float] = (100.0, 400.0),
) -> dict:
    """Multi-channel per-bearing satellite signature.

    Channels (sized n_bearings):
      water:      near-range water fraction (10-80 m by default).
      water_far:  far-range water fraction (100-400 m). Together with `water`
                  this differentiates "bay across the street" from "small
                  channel right next to me", giving spatially-distinct peaks.
      d_water:    derivative of water (edge channel; sharp at water/land
                  transitions).
      d_water_far: derivative of water_far.
    """
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(seed_lat))
    bearings = np.linspace(0.0, 360.0, n_bearings, endpoint=False)
    d_lo, d_hi = near_range_m
    f_lo, f_hi = far_water_range_m
    near_dists = np.arange(d_lo, d_hi + step_m * 0.5, step_m)
    far_dists = np.arange(f_lo, f_hi + step_m * 0.5, step_m)
    h, w = sat_water.shape[:2]

    water = np.zeros(n_bearings, dtype=np.float32)
    water_far = np.zeros(n_bearings, dtype=np.float32)
    for ai, theta in enumerate(bearings):
        br = math.radians(float(theta))
        sin_b, cos_b = math.sin(br), math.cos(br)
        for dists, out in ((near_dists, water), (far_dists, water_far)):
            hits = valid = 0
            for d in dists:
                lon = seed_lon + d * sin_b / mlon
                lat = seed_lat + d * cos_b / mlat
                px, py = sat_project(float(lon), float(lat))
                ix, iy = int(px), int(py)
                if 0 <= ix < w and 0 <= iy < h:
                    valid += 1
                    if sat_water[iy, ix]:
                        hits += 1
            out[ai] = hits / valid if valid > 0 else 0.0

    d_water = np.roll(water, -1) - np.roll(water, 1)
    d_water_far = np.roll(water_far, -1) - np.roll(water_far, 1)
    return {"water": water, "water_far": water_far,
            "d_water": d_water, "d_water_far": d_water_far}


# ---------------------------------------------------------------------------
# FFT-based circular Pearson cross-correlation. Returns shift in degrees.
# ---------------------------------------------------------------------------

def _circular_pearson_corr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = a.size
    a0 = (a - a.mean()).astype(np.float32)
    b0 = (b - b.mean()).astype(np.float32)
    sa, sb = a0.std(), b0.std()
    if sa < 1e-6 or sb < 1e-6:
        return np.zeros(n, dtype=np.float32)
    fa = np.fft.fft(a0)
    fb = np.fft.fft(b0)
    raw = np.real(np.fft.ifft(fb * np.conj(fa)))
    return (raw / (n * sa * sb)).astype(np.float32)


def correlate_all_channels(
    pano_sigs: dict, sat_sigs: dict,
    *, channels: tuple[str, ...] = ("water", "water_far", "d_water", "d_water_far"),
    weights: dict | None = None,
) -> dict:
    """Per-channel circular correlation + weighted-mean combined curve.

    Each channel's correlation is averaged with given weights. Smooth
    channels (water, water_far) give broad peaks; derivative channels
    (d_water, d_water_far) give sharp peaks at the SAME location IF the
    underlying transitions match. Combining them produces a peak with
    both robustness (smooth) and sharpness (derivative).
    """
    if weights is None:
        weights = {"water": 1.0, "water_far": 1.0,
                   "d_water": 0.6, "d_water_far": 0.6}
    n = pano_sigs[channels[0]].size
    per_channel = {}
    for ch in channels:
        per_channel[ch] = _circular_pearson_corr(pano_sigs[ch], sat_sigs[ch])
    w = np.array([weights.get(ch, 1.0) for ch in channels], dtype=np.float32)
    w_sum = float(w.sum()) if w.sum() > 0 else 1.0
    stack = np.stack([per_channel[ch] for ch in channels], axis=0)
    combined = (stack * w[:, None]).sum(axis=0) / w_sum
    bearings = np.arange(n, dtype=np.float32) * (360.0 / n)
    return {"per_channel": per_channel, "combined": combined,
            "bearings": bearings, "channels": channels, "weights": weights}


def find_peak_with_subpixel(corr: np.ndarray, bearings: np.ndarray,
                            step_deg: float) -> tuple[float, float, float]:
    """Return (offset_deg, peak_value, hwhm_deg)."""
    if corr.size == 0:
        return 0.0, 0.0, 180.0
    best_idx = int(np.argmax(corr))
    peak_value = float(corr[best_idx])
    offset = float(bearings[best_idx])
    if 0 < best_idx < (corr.size - 1):
        y0, y1, y2 = float(corr[best_idx-1]), float(corr[best_idx]), float(corr[best_idx+1])
        denom = (y0 - 2 * y1 + y2)
        if abs(denom) > 1e-9:
            delta = 0.5 * (y0 - y2) / denom
            offset = float(bearings[best_idx] + delta * step_deg)
            peak_value = y1 - 0.25 * (y0 - y2) * delta
    half = peak_value * 0.5
    above_half = corr >= half
    hwhm = float(above_half.sum() * step_deg) / 2.0 if above_half.any() else float(corr.std())
    return offset % 360.0, peak_value, hwhm


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_satellite_page(pdf, sat_image, sat_water, sat_project,
                           lat, lon, region, seed_index, near_range):
    fig = plt.figure(figsize=(11, 9))
    fig.suptitle(f"Satellite reference  ({region} seed_{seed_index})", fontsize=12)
    ax = fig.add_axes([0.04, 0.05, 0.92, 0.88])
    ax.imshow(sat_image)
    cyan = np.zeros((*sat_water.shape, 4), dtype=np.float32)
    cyan[..., 1] = 0.85; cyan[..., 2] = 1.0
    cyan[..., 3] = np.where(sat_water, 0.35, 0.0)
    ax.imshow(cyan)
    sx, sy = sat_project(lon, lat)
    ax.scatter([sx], [sy], c="red", marker="*", s=220, zorder=5,
               edgecolor="white")
    ax.set_title(
        f"Wide satellite + water mask (cyan).  red * = seed.  "
        f"near-range window: {near_range[0]:.0f}-{near_range[1]:.0f} m",
        fontsize=9)
    ax.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


def _render_pano_page(pdf, pano_image, pano_water, pano_building,
                      headings_per_col, best_offset, region, seed_index,
                      y_top, y_bot):
    H, W = pano_water.shape[:2]
    fig = plt.figure(figsize=(16, 7))
    fig.suptitle(
        f"Pano (water=cyan, building=red) + recovered offset = {best_offset:.2f}°  "
        f"({region} seed_{seed_index})",
        fontsize=11)
    ax = fig.add_axes([0.03, 0.12, 0.94, 0.76])
    ax.imshow(pano_image)
    cyan = np.zeros((H, W, 4), dtype=np.float32)
    cyan[..., 1] = 0.85; cyan[..., 2] = 1.0
    cyan[..., 3] = np.where(pano_water, 0.40, 0.0)
    ax.imshow(cyan)
    red = np.zeros((H, W, 4), dtype=np.float32)
    red[..., 0] = 1.0; red[..., 1] = 0.40
    red[..., 3] = np.where(pano_building, 0.35, 0.0)
    ax.imshow(red)
    # Show the ground band used for sampling.
    ax.axhline(y_top, color="yellow", linewidth=0.8, alpha=0.7)
    ax.axhline(y_bot, color="yellow", linewidth=0.8, alpha=0.7)
    # Column→bearing ticks for the WORLD frame (after recovered offset).
    ticks_world = np.arange(0, 361, 30, dtype=np.float32)
    sort_idx = np.argsort(headings_per_col)
    sorted_h = headings_per_col[sort_idx]
    tick_cols = []
    for t in ticks_world:
        # World bearing t → pano bearing (t - offset). Find nearest column.
        target = (t - best_offset) % 360.0
        diffs = np.abs(((sorted_h - target + 180.0) % 360.0) - 180.0)
        j = int(np.argmin(diffs))
        tick_cols.append(int(sort_idx[j]))
    ax.set_xticks(tick_cols)
    ax.set_xticklabels([f"{int(t)}°" for t in ticks_world], fontsize=8)
    ax.set_xlabel("World bearing AFTER applying recovered offset (deg, clockwise from N)",
                  fontsize=9)
    ax.set_yticks([y_top, y_bot])
    ax.set_yticklabels([f"y_top={y_top}", f"y_bot={y_bot}"], fontsize=7)
    pdf.savefig(fig)
    plt.close(fig)


def _render_signatures_page(pdf, sat_sigs, pano_sigs, best_offset,
                            n_bearings, region, seed_index, channels):
    step = 360.0 / n_bearings
    bearings = np.arange(n_bearings, dtype=np.float32) * step
    shift = int(round(best_offset / step)) % n_bearings

    n_ch = len(channels)
    fig = plt.figure(figsize=(15, 2.5 * n_ch + 1.5))
    fig.suptitle(
        f"Per-bearing signatures (sat blue vs pano red-shifted by "
        f"{best_offset:.2f}°)  ({region} seed_{seed_index})",
        fontsize=12)
    for i, ch in enumerate(channels):
        ax = fig.add_axes([0.06, 0.92 - (i + 1) * (0.85 / n_ch),
                           0.90, 0.85 / n_ch - 0.05])
        a = sat_sigs[ch]
        b = np.roll(pano_sigs[ch], -shift)
        ax.plot(bearings, a, color="#0040c0", linewidth=1.2,
                label=f"sat {ch}")
        ax.plot(bearings, b, color="#d04020", linewidth=1.2, alpha=0.85,
                label=f"pano {ch} (aligned)")
        ax.set_xlim(0, 360)
        ax.set_xticks(np.arange(0, 361, 30))
        ax.grid(alpha=0.20)
        ax.legend(loc="upper right", fontsize=8)
        ax.set_ylabel(ch, fontsize=8)
        if i < n_ch - 1:
            ax.set_xticklabels([])
        else:
            ax.set_xlabel("World bearing (deg, clockwise from N)", fontsize=10)
    pdf.savefig(fig)
    plt.close(fig)


def _render_corr_page(pdf, corr_result, best_offset, peak_value, hwhm,
                      region, seed_index):
    fig = plt.figure(figsize=(14, 8))
    fig.suptitle(
        f"Cross-correlation curves  ({region} seed_{seed_index})  "
        f"combined peak r = {peak_value:.3f}  HWHM = {hwhm:.1f}°  "
        f"offset = {best_offset:.2f}°",
        fontsize=12)
    bearings = corr_result["bearings"]
    channels = corr_result["channels"]
    weights = corr_result["weights"]
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
    ax = fig.add_axes([0.06, 0.08, 0.90, 0.80])
    for i, ch in enumerate(channels):
        ax.plot(bearings, corr_result["per_channel"][ch],
                color=colors[i % len(colors)], linewidth=1.0, alpha=0.55,
                label=f"{ch} (w={weights.get(ch, 1.0):.1f})")
    ax.plot(bearings, corr_result["combined"], color="black", linewidth=1.7,
            label="combined (weighted mean)")
    ax.axvline(best_offset, color="green", linestyle="--", linewidth=1.2,
               label=f"recovered = {best_offset:.2f}°")
    ax.axhline(0, color="grey", linewidth=0.6, alpha=0.5)
    ax.set_xlim(0, 360)
    ax.set_xlabel("Candidate offset (deg)", fontsize=10)
    ax.set_ylabel("Pearson r", fontsize=10)
    ax.grid(alpha=0.25)
    ax.legend(loc="best", fontsize=9)
    pdf.savefig(fig)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = build_parser().parse_args()

    from city2stl.skyline_cv.pipeline import _ensure_segformer as _ens
    sf_ok = _ens()
    print(f"[demo] SegFormer load: ok={sf_ok}")
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

    api_key = _resolve_api_key(args.api_key)
    bbox = _load_region_bbox(args.region)
    sat_image, sat_project, sat_meta = fetch_region_satellite(
        (bbox.south, bbox.west, bbox.north, bbox.east), target_m_per_px=2.0,
    )
    print(f"[demo] sat image: zoom={sat_meta['zoom']} shape={sat_meta['shape']}")
    sat_water = detect_sat_water_mask(sat_image)

    seed_urls = _load_site_seed_urls(args.region)
    if not seed_urls or args.seed_index < 1 or args.seed_index > len(seed_urls):
        print(f"[demo] invalid seed-index {args.seed_index}")
        return 2
    parsed = _parse_streetview_url(seed_urls[args.seed_index - 1])
    if parsed is None:
        return 3
    lat, lon, _base_heading, fov, pitch, pano_id = parsed
    lat, lon, pano_id = _load_resolved_seed(
        args.region, args.seed_index, lat, lon, pano_id)
    print(f"[demo] seed_{args.seed_index} @ {lat:.5f},{lon:.5f}  "
          f"pano={pano_id}  url_pitch={pitch:+.1f}")

    # Capture pano views + masks.
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
            "geo_heading": float(hdg), "image": img,
            "building_mask": bmask, "water_mask": wmask,
            "effective_pitch": eff_pitch,
        })
    print(f"[demo] captured {len(captured)} views")
    if len(captured) < 4:
        return 4

    eff_pitches = sorted({v["effective_pitch"] for v in captured})
    used_pitch = float(np.mean(eff_pitches)) if eff_pitches else 0.0
    view_width_px = captured[0]["image"].shape[1]
    pano_rgb = stitch_pano_views(captured, args.fov_deg, args.spin_step_deg)
    pano_masks = stitch_pano_masks(captured, args.fov_deg, args.spin_step_deg)
    if pano_rgb is None or pano_masks is None:
        print("[demo] stitch failed")
        return 5
    pano_image, headings_per_col = pano_rgb
    pano_building, pano_water = pano_masks
    print(f"[demo] pano shape: {pano_image.shape}  water_frac={float(pano_water.mean()):.3f}  "
          f"building_frac={float(pano_building.mean()):.3f}  pitch={used_pitch:+.1f}")

    # Build per-bearing signatures.
    pano_sigs = build_pano_signatures(
        pano_water, pano_building, headings_per_col,
        n_bearings=args.n_bearings, camera_h_m=1.7,
        pitch_deg=used_pitch, fov_deg=args.fov_deg, view_width_px=view_width_px,
    )
    near_range = tuple(float(x) for x in args.near_range.split(","))
    sat_sigs = build_sat_signatures(
        sat_water, sat_project, lat, lon,
        n_bearings=args.n_bearings, near_range_m=near_range,
        far_water_range_m=(100.0, 400.0),
    )

    # Per-channel diagnostic.
    channels = ("water", "water_far", "d_water", "d_water_far")
    for ch in channels:
        ps, ss = pano_sigs[ch], sat_sigs[ch]
        print(f"[demo] {ch:12s}  pano(mean={ps.mean():+.3f} std={ps.std():.3f})  "
              f"sat(mean={ss.mean():+.3f} std={ss.std():.3f})")

    # Correlate.
    corr = correlate_all_channels(pano_sigs, sat_sigs, channels=channels)
    best_offset, peak_value, hwhm = find_peak_with_subpixel(
        corr["combined"], corr["bearings"], step_deg=360.0 / args.n_bearings)
    print(f"[demo] recovered offset = {best_offset:.2f}°  combined peak r = {peak_value:.3f}  "
          f"HWHM = {hwhm:.1f}°")
    for ch in channels:
        ch_off, ch_pk, ch_hw = find_peak_with_subpixel(
            corr["per_channel"][ch], corr["bearings"],
            step_deg=360.0 / args.n_bearings)
        print(f"[demo]   {ch:10s} peak={ch_pk:+.3f} offset={ch_off:.2f}° HWHM={ch_hw:.1f}°")

    # PDF.
    out_pdf = Path(args.out) if args.out else (
        ROOT / "city2stl" / "skyline_cv" / "runs" / "heading_recovery"
        / f"{args.region}_seed{args.seed_index}.pdf"
    )
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out_pdf) as pdf:
        _render_satellite_page(pdf, sat_image, sat_water, sat_project,
                               lat, lon, args.region, args.seed_index,
                               near_range)
        _render_pano_page(pdf, pano_image, pano_water, pano_building,
                          headings_per_col, best_offset,
                          args.region, args.seed_index,
                          pano_sigs["y_top"], pano_sigs["y_bot"])
        _render_signatures_page(pdf, sat_sigs, pano_sigs, best_offset,
                                args.n_bearings, args.region, args.seed_index,
                                channels)
        _render_corr_page(pdf, corr, best_offset, peak_value, hwhm,
                          args.region, args.seed_index)
    print(f"[demo] wrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
