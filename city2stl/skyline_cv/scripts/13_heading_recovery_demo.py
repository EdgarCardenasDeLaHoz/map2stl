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
import matplotlib.patches as mpatches
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


def _pano_skyline_height(
    pano_building: np.ndarray, headings_per_col: np.ndarray,
    n_bearings: int, horizon_y: float,
) -> np.ndarray:
    """Per-bearing angular height of buildings above the horizon.

    For each pano column, find the topmost row of the building mask.
    `(horizon_y - top_y)` is the building's angular height in pixels
    (positive = above horizon). Average across columns in each bearing
    bin. Higher values = taller / nearer buildings.
    """
    H, W = pano_building.shape
    # Argmax of the *first* True from the top: searchsorted-style.
    # build a per-column top_y by scanning from y=0 downward.
    top_y = np.full(W, H, dtype=np.float32)
    for x in range(W):
        ys = np.where(pano_building[:, x])[0]
        if ys.size:
            top_y[x] = float(ys[0])
    angular_height = np.clip(horizon_y - top_y, 0.0, horizon_y).astype(np.float32)

    bearings = np.linspace(0.0, 360.0, n_bearings, endpoint=False)
    bin_w = 360.0 / n_bearings
    out = np.zeros(n_bearings, dtype=np.float32)
    h_mod = np.asarray(headings_per_col, dtype=np.float32) % 360.0
    for i, t in enumerate(bearings):
        d = np.abs(((h_mod - t + 180.0) % 360.0) - 180.0)
        m = d <= (bin_w * 0.5 + 1e-3)
        out[i] = angular_height[m].mean() if m.any() else angular_height[int(np.argmin(d))]
    # Normalize to [0, 1] for cross-channel weighting comparability.
    if out.max() > 0:
        out = out / out.max()
    return out


def build_pano_signatures(
    pano_water: np.ndarray, pano_building: np.ndarray,
    headings_per_col: np.ndarray,
    *, n_bearings: int, camera_h_m: float, pitch_deg: float,
    fov_deg: float, view_width_px: int,
    near_range_m: tuple[float, float] = (10.0, 80.0),
    far_range_m: tuple[float, float] = (100.0, 400.0),
    pano_image: np.ndarray | None = None,
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
    skyline = _pano_skyline_height(pano_building, headings_per_col,
                                    n_bearings, horizon_y=y_mid)

    # Pano-RGB brightness in the FAR band (just below horizon), per bearing.
    # This pairs with the satellite-RGB brightness channel; both sample
    # *imagery*, not masks, so they break the water-symmetry that confuses
    # the mask-derived channels on peninsula seeds.
    rgb = np.zeros(n_bearings, dtype=np.float32)
    if pano_image is not None and pano_image.shape[:2] == pano_water.shape:
        gray_band = pano_image[yt_far:yb_far, :, :].astype(np.float32).mean(axis=2)
        gray_band /= 255.0
        bearings = np.linspace(0.0, 360.0, n_bearings, endpoint=False)
        bin_w = 360.0 / n_bearings
        h_mod = np.asarray(headings_per_col, dtype=np.float32) % 360.0
        per_col = gray_band.mean(axis=0) if gray_band.size else np.zeros(
            pano_water.shape[1], dtype=np.float32)
        for i, t in enumerate(bearings):
            d = np.abs(((h_mod - t + 180.0) % 360.0) - 180.0)
            m = d <= (bin_w * 0.5 + 1e-3)
            rgb[i] = per_col[m].mean() if m.any() else per_col[int(np.argmin(d))]

    d_water = np.roll(water, -1) - np.roll(water, 1)
    d_water_far = np.roll(water_far, -1) - np.roll(water_far, 1)
    d_skyline = np.roll(skyline, -1) - np.roll(skyline, 1)
    d_rgb = np.roll(rgb, -1) - np.roll(rgb, 1)
    return {
        "water": water, "water_far": water_far, "skyline": skyline,
        "rgb": rgb,
        "d_water": d_water, "d_water_far": d_water_far,
        "d_skyline": d_skyline, "d_rgb": d_rgb,
        "y_top_near": yt_near, "y_bot_near": yb_near,
        "y_top_far": yt_far, "y_bot_far": yb_far,
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
    sat_rgb: np.ndarray | None = None,
    rgb_range_m: tuple[float, float] = (50.0, 600.0),
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

    # Build "skyline proxy": for each bearing, inverse distance to first
    # land. Closer land = potentially-taller-looking buildings in the
    # pano. This is the satellite-side counterpart to pano_skyline.
    skyline = np.zeros(n_bearings, dtype=np.float32)
    skyline_range_m = 400.0
    for ai, theta in enumerate(bearings):
        dist = _ray_walk_first(
            sat_water, sat_project, seed_lat, seed_lon,
            float(theta), skyline_range_m, step_m=5.0, want_value=False,
        )
        skyline[ai] = max(0.0, 1.0 - dist / skyline_range_m)

    # Build "rgb" signal: average satellite-RGB brightness along each bearing.
    # This is the only channel that's *fundamentally not* water-derived. For
    # peninsula seeds like Cartagena's seed_5, water-only signals have a
    # ~180° ambiguity (water on both sides of the peninsula). Open ocean is
    # high-brightness/low-saturation uniform; urban directions have varied
    # textures. Cross-correlating raw brightness breaks the ambiguity.
    rgb = np.zeros(n_bearings, dtype=np.float32)
    if sat_rgb is not None:
        rgb_dists = np.arange(rgb_range_m[0], rgb_range_m[1] + 1, 10.0)
        for ai, theta in enumerate(bearings):
            br = math.radians(float(theta))
            sin_b, cos_b = math.sin(br), math.cos(br)
            samples = []
            for d in rgb_dists:
                lon = seed_lon + d * sin_b / mlon
                lat = seed_lat + d * cos_b / mlat
                px, py = sat_project(float(lon), float(lat))
                ix, iy = int(px), int(py)
                if 0 <= ix < w and 0 <= iy < h:
                    # Convert to grayscale (brightness).
                    samples.append(sat_rgb[iy, ix].astype(np.float32).mean() / 255.0)
            rgb[ai] = float(np.mean(samples)) if samples else 0.0

    d_water = np.roll(water, -1) - np.roll(water, 1)
    d_water_far = np.roll(water_far, -1) - np.roll(water_far, 1)
    d_skyline = np.roll(skyline, -1) - np.roll(skyline, 1)
    d_rgb = np.roll(rgb, -1) - np.roll(rgb, 1)
    return {"water": water, "water_far": water_far, "skyline": skyline,
            "rgb": rgb,
            "d_water": d_water, "d_water_far": d_water_far,
            "d_skyline": d_skyline, "d_rgb": d_rgb}


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
    *, channels: tuple[str, ...] = (
        "water", "water_far", "skyline", "rgb",
        "d_water", "d_water_far", "d_skyline", "d_rgb",
    ),
    weights: dict | None = None,
) -> dict:
    """Per-channel circular correlation + weighted-mean combined curve.

    Smooth channels (water, water_far, skyline) give broad peaks;
    derivative channels (d_*) give sharp peaks at the SAME location IF
    the underlying transitions match. Combining them produces a peak
    with both robustness (smooth) and sharpness (derivative). The
    `skyline` channel is the only one with a chance of working for
    inland seeds, where water has nothing to say.
    """
    if weights is None:
        # rgb / d_rgb are the only channels that are NOT water-mask-derived.
        # Peninsula seeds (like Cartagena seed_5) have water on both sides
        # of the peninsula, giving water signals a 180° ambiguity that
        # only asymmetric image-brightness can break. Verified empirically:
        # on seed_5, rgb found 233° (truth ~247°) while every water-derived
        # channel found ~97° (truth + 180°). Weight rgb heavily to outvote.
        weights = {"water": 0.4, "water_far": 0.4, "skyline": 0.4,
                   "rgb": 4.0,
                   "d_water": 0.3, "d_water_far": 0.3, "d_skyline": 0.3,
                   "d_rgb": 2.5}
    n = pano_sigs[channels[0]].size
    per_channel = {}
    active = []
    for ch in channels:
        c = _circular_pearson_corr(pano_sigs[ch], sat_sigs[ch])
        per_channel[ch] = c
        # A channel contributes only if both sides have non-trivial variance.
        if pano_sigs[ch].std() > 1e-5 and sat_sigs[ch].std() > 1e-5:
            active.append(ch)
    if not active:
        combined = np.zeros(n, dtype=np.float32)
    else:
        w = np.array([weights.get(ch, 1.0) for ch in active], dtype=np.float32)
        w_sum = float(w.sum()) if w.sum() > 0 else 1.0
        stack = np.stack([per_channel[ch] for ch in active], axis=0)
        combined = (stack * w[:, None]).sum(axis=0) / w_sum
    bearings = np.arange(n, dtype=np.float32) * (360.0 / n)
    return {"per_channel": per_channel, "combined": combined,
            "bearings": bearings, "channels": channels, "weights": weights,
            "active": active}


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
        # +shift maps pano_bearing → world_bearing.
        # (world = pano + offset, so pano value plotted at world index i
        # should come from pano_sig[(i - offset) mod N] = np.roll(arr, +shift)[i].)
        b = np.roll(pano_sigs[ch], +shift)
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


def project_pano_boundaries_to_birdseye(
    pano_water: np.ndarray, pano_building: np.ndarray,
    headings_per_col: np.ndarray,
    *, camera_h_m: float, pitch_deg: float,
    fov_deg: float, view_width_px: int,
    max_range_m: float = 400.0,
    min_range_m: float = 3.0,
) -> dict:
    """Project the per-column water-top and building-base EDGES to a
    pano-frame bird's-eye polar list. Avoids the IPM starburst issue
    because we project only ground-level boundary points (where the
    flat-ground assumption is exactly correct), not every pixel.

    For each pano column c (bearing = headings_per_col[c]):
      water_dist[c]   = IPM distance to the top of the water mask
                        (where water ends going UP). This IS the
                        water-line distance to the coastline.
      building_dist[c] = IPM distance to the bottom of the building
                        mask (where buildings meet the ground).

    Returns dict of {water_dist, building_dist, bearings_deg}. Values are
    in metres; NaN where the boundary is missing or out of range.
    """
    H, W = pano_water.shape
    focal_y = (view_width_px / 2.0) / math.tan(math.radians(fov_deg / 2.0))
    horizon_y = H / 2.0 - math.tan(math.radians(pitch_deg)) * focal_y

    water_dist = np.full(W, np.nan, dtype=np.float32)
    building_dist = np.full(W, np.nan, dtype=np.float32)

    for c in range(W):
        # Top of water (water ends going up from the bottom).
        ws = np.where(pano_water[:, c])[0]
        if ws.size:
            y_top = float(ws.min())
            if y_top > horizon_y + 1:
                d = camera_h_m * focal_y / (y_top - horizon_y)
                if min_range_m <= d <= max_range_m:
                    water_dist[c] = d
        # Bottom of building (building ends going down). For a sea-level
        # camera, the building's base is at sea level. So:
        bs = np.where(pano_building[:, c])[0]
        if bs.size:
            y_bot = float(bs.max())
            if y_bot > horizon_y + 1:
                d = camera_h_m * focal_y / (y_bot - horizon_y)
                if min_range_m <= d <= max_range_m:
                    building_dist[c] = d

    return {
        "water_dist": water_dist,
        "building_dist": building_dist,
        "bearings_deg": np.asarray(headings_per_col, dtype=np.float32),
    }


def sweep_offset_by_onboundary(
    pano_boundaries: dict, sat_water: np.ndarray, sat_project,
    seed_lat: float, seed_lon: float,
    *, canvas_radius_m: float = 300.0,
    coarse_step_deg: float = 5.0, fine_step_deg: float = 1.0,
    fine_window_deg: float = 10.0,
    near_radius_m: float = 15.0,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Brute-force sweep heading offset by GEOMETRIC alignment: how close
    the pano-derived water-edge points fall to the satellite water/land
    boundary. Doesn't share the 180° symmetry of water cross-correlation.

    Uses a distance transform of the satellite water/land boundary, so each
    pano point's "score" is exp(-d_to_boundary / `near_radius_m`); ~1 when
    close, ~0 when far. Tolerates SegFormer/IPM noise of several metres
    without losing signal entirely.

    Returns (best_offset_deg, cand_deg, scores) with scores in [0, 1].
    """
    from scipy.ndimage import distance_transform_edt, binary_erosion, binary_dilation
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(seed_lat))
    d_lon = canvas_radius_m / mlon
    d_lat = canvas_radius_m / mlat
    x0, _ = sat_project(seed_lon - d_lon, seed_lat)
    x1, _ = sat_project(seed_lon + d_lon, seed_lat)
    _, y0 = sat_project(seed_lon, seed_lat + d_lat)
    _, y1 = sat_project(seed_lon, seed_lat - d_lat)
    cx, cy = sat_project(seed_lon, seed_lat)
    px0, px1 = int(min(x0, x1)), int(max(x0, x1))
    py0, py1 = int(min(y0, y1)), int(max(y0, y1))
    H, W = sat_water.shape[:2]
    px0, px1 = max(0, px0), min(W, px1)
    py0, py1 = max(0, py0), min(H, py1)
    water_crop = sat_water[py0:py1, px0:px1].astype(bool)
    crop_w_px = max(1, px1 - px0)
    crop_h_px = max(1, py1 - py0)
    m_per_px_x = (2.0 * canvas_radius_m) / float(crop_w_px)
    m_per_px_y = (2.0 * canvas_radius_m) / float(crop_h_px)
    seed_x = cx - px0
    seed_y = cy - py0

    # Build a "is boundary" mask (1 where water meets land), then distance
    # transform: for every crop pixel, distance to the nearest boundary in
    # metres.
    if water_crop.size == 0 or water_crop.all() or not water_crop.any():
        # No boundary in crop → can't score; return zero.
        return 0.0, np.zeros(1, dtype=np.float32), np.zeros(1, dtype=np.float32)
    eroded = binary_erosion(water_crop)
    dilated = binary_dilation(water_crop)
    boundary = (water_crop & ~eroded) | (~water_crop & dilated)
    # distance_transform_edt(arr) = distance to nearest True; we want
    # distance to nearest boundary, so input ~boundary.
    dist_pixels = distance_transform_edt(~boundary)
    # Convert to metres using the avg of x and y per-pixel scale.
    m_per_px = 0.5 * (m_per_px_x + m_per_px_y)
    dist_metres = dist_pixels * m_per_px

    # Pre-filter valid pano-water-edge points to (bearing, distance) pairs.
    bearings = pano_boundaries["bearings_deg"]
    dists = pano_boundaries["water_dist"]
    finite = np.isfinite(dists)
    pano_bearings = bearings[finite].astype(np.float32)
    pano_dists = dists[finite].astype(np.float32)
    if pano_bearings.size == 0:
        return 0.0, np.zeros(1, dtype=np.float32), np.zeros(1, dtype=np.float32)

    def _score_at(offset_deg: float) -> float:
        world_bearings = np.radians((pano_bearings + offset_deg) % 360.0)
        x_px = seed_x + (pano_dists * np.sin(world_bearings)) / m_per_px_x
        y_px = seed_y - (pano_dists * np.cos(world_bearings)) / m_per_px_y
        ix = np.round(x_px).astype(np.int32)
        iy = np.round(y_px).astype(np.int32)
        valid = (ix >= 0) & (ix < crop_w_px) & (iy >= 0) & (iy < crop_h_px)
        if not np.any(valid):
            return 0.0
        d_at_points = dist_metres[iy[valid], ix[valid]]
        # Soft score: exp(-d / near_radius). 1 at distance 0, ~0 at far.
        return float(np.exp(-d_at_points / near_radius_m).mean())

    coarse_cand = np.arange(0.0, 360.0, coarse_step_deg, dtype=np.float32)
    coarse_scores = np.array([_score_at(float(o)) for o in coarse_cand],
                             dtype=np.float32)
    best_coarse_idx = int(np.argmax(coarse_scores))
    best_coarse = float(coarse_cand[best_coarse_idx])

    fine_lo = best_coarse - fine_window_deg
    fine_hi = best_coarse + fine_window_deg
    fine_cand = np.arange(fine_lo, fine_hi + 0.5 * fine_step_deg, fine_step_deg,
                          dtype=np.float32)
    fine_scores = np.array([_score_at(float(o)) for o in fine_cand],
                           dtype=np.float32)
    best_fine_idx = int(np.argmax(fine_scores))
    best = float(fine_cand[best_fine_idx]) % 360.0
    return best, coarse_cand, coarse_scores


def _ipm_distance(camera_h_m: float, focal_y: float, y: float,
                  horizon_y: float) -> float | None:
    """IPM distance for a pano y-pixel that's a ground-level point."""
    if y <= horizon_y + 1:
        return None
    return camera_h_m * focal_y / (y - horizon_y)


def _project_one(ax, pano_boundaries: dict, offset_deg: float,
                 seed_x: float, seed_y: float,
                 m_per_px_x: float, m_per_px_y: float,
                 *, scatter_size: float = 4.0,
                 zorder: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Plot pano-derived water-edge (cyan) and building-base (orange) at a
    given offset. Returns the per-bearing water (x,y) for match scoring."""
    bearings = pano_boundaries["bearings_deg"]
    water_pts = np.full((bearings.size, 2), np.nan, dtype=np.float32)
    for arr, color, lbl in [
        (pano_boundaries["water_dist"], "#00b8ff", "pano water-edge"),
        (pano_boundaries["building_dist"], "#ff7f0e", "pano building-base"),
    ]:
        xs, ys = [], []
        for c, d in enumerate(arr):
            if not np.isfinite(d):
                continue
            br = math.radians(float((bearings[c] + offset_deg) % 360.0))
            x_px = seed_x + (d * math.sin(br)) / m_per_px_x
            y_px = seed_y - (d * math.cos(br)) / m_per_px_y
            xs.append(x_px); ys.append(y_px)
            if arr is pano_boundaries["water_dist"]:
                water_pts[c, 0] = x_px
                water_pts[c, 1] = y_px
        ax.scatter(xs, ys, c=color, s=scatter_size, alpha=0.7, zorder=zorder,
                   label=lbl)
    return water_pts


def _score_match(water_pts: np.ndarray, water_crop: np.ndarray) -> dict:
    """For each finite pano water-edge point, look up the satellite water
    mask at that pixel. A "good" pano water-edge point lands near a sat
    water/land boundary (one of its 8-neighbours has the other label).

    Returns dict of {n_total, n_on_boundary, frac_on_boundary}.
    """
    if water_crop.size == 0:
        return {"n_total": 0, "n_on_boundary": 0, "frac_on_boundary": 0.0}
    H, W = water_crop.shape
    n_total = 0
    n_on_boundary = 0
    for i in range(water_pts.shape[0]):
        x, y = water_pts[i]
        if not (np.isfinite(x) and np.isfinite(y)):
            continue
        ix, iy = int(round(x)), int(round(y))
        if not (1 <= ix < W - 1 and 1 <= iy < H - 1):
            continue
        n_total += 1
        patch = water_crop[iy - 1: iy + 2, ix - 1: ix + 2]
        if patch.min() != patch.max():  # mixed → at a boundary
            n_on_boundary += 1
    frac = (n_on_boundary / n_total) if n_total else 0.0
    return {"n_total": n_total, "n_on_boundary": n_on_boundary,
            "frac_on_boundary": float(frac)}


def _render_onboundary_sweep_page(
    pdf, ob_cand: np.ndarray, ob_scores: np.ndarray, corr_offset: float,
    onboundary_peak_offset: float, region: str, seed_index: int,
):
    """Plot the geometric on-boundary score vs offset.

    Compare to the correlation-derived offset (green dashed) to see where
    the correlation answer falls on the geometric curve.
    """
    fig = plt.figure(figsize=(14, 5))
    fig.suptitle(
        f"On-boundary geometric score vs offset  ({region} seed_{seed_index})",
        fontsize=11)
    ax = fig.add_axes([0.07, 0.20, 0.88, 0.65])
    ax.plot(ob_cand, ob_scores, color="#1f77b4", linewidth=1.4,
            label="on-boundary score (mean exp(-d/15m))")
    ax.axvline(corr_offset, color="green", linestyle="--", linewidth=1.4,
               label=f"correlation answer = {corr_offset:.1f}°")
    ax.axvline(onboundary_peak_offset, color="red", linestyle=":", linewidth=1.4,
               label=f"on-boundary peak = {onboundary_peak_offset:.1f}°")
    ax.set_xlim(0, 360)
    ax.set_xlabel("Candidate offset (deg)", fontsize=10)
    ax.set_ylabel("Score (higher = pano points closer to sat coastline)",
                  fontsize=10)
    ax.set_title(
        "For peninsula seeds with water on both sides, this curve has two "
        "peaks ~180° apart — the geometric metric alone cannot break the "
        "ambiguity. The asymmetric RGB channel is what pulls the "
        "correlation answer toward the correct half.",
        fontsize=9)
    ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.25)
    pdf.savefig(fig)
    plt.close(fig)


def _render_birdseye_page(pdf, sat_image, sat_water, sat_project,
                          seed_lat, seed_lon,
                          pano_boundaries: dict, best_offset_deg: float,
                          region: str, seed_index: int,
                          canvas_radius_m: float = 300.0):
    """Top-down comparison page showing 3 candidate offsets side-by-side
    so the matching is visible:
      LEFT:   pano projected at recovered offset (what we chose)
      MIDDLE: pano projected at recovered offset + 180° (the symmetry twin)
      RIGHT:  pano projected with NO rotation (raw pano frame, for sanity)

    Each panel shows the same satellite crop with the pano boundary points
    overlaid. The title for each panel reports the % of pano water-edge
    points that land on a satellite water-LAND boundary (1-pixel
    neighbourhood) — high % = good fit; low % = points scattered into
    pure water or pure land regions.
    """
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(seed_lat))
    d_lon = canvas_radius_m / mlon
    d_lat = canvas_radius_m / mlat
    x0, _ = sat_project(seed_lon - d_lon, seed_lat)
    x1, _ = sat_project(seed_lon + d_lon, seed_lat)
    _, y0 = sat_project(seed_lon, seed_lat + d_lat)
    _, y1 = sat_project(seed_lon, seed_lat - d_lat)
    cx, cy = sat_project(seed_lon, seed_lat)
    px0, px1 = int(min(x0, x1)), int(max(x0, x1))
    py0, py1 = int(min(y0, y1)), int(max(y0, y1))
    H, W = sat_image.shape[:2]
    px0, px1 = max(0, px0), min(W, px1)
    py0, py1 = max(0, py0), min(H, py1)
    crop = sat_image[py0:py1, px0:px1]
    water_crop = sat_water[py0:py1, px0:px1]
    crop_w_px = max(1, px1 - px0)
    crop_h_px = max(1, py1 - py0)
    m_per_px_x = (2.0 * canvas_radius_m) / float(crop_w_px)
    m_per_px_y = (2.0 * canvas_radius_m) / float(crop_h_px)
    seed_x = cx - px0
    seed_y = cy - py0

    fig = plt.figure(figsize=(18, 7.2))
    fig.suptitle(
        f"Top-down comparison at three candidate offsets  "
        f"({region} seed_{seed_index}, crop ±{canvas_radius_m:.0f} m).  "
        "Best match = highest 'on boundary' % (pano water-edge dots land on "
        "the sat water/land boundary).",
        fontsize=11)

    panels = [
        (best_offset_deg, f"recovered = {best_offset_deg:.1f}°"),
        ((best_offset_deg + 180.0) % 360.0,
         f"recovered + 180° = {(best_offset_deg + 180.0) % 360.0:.1f}°"),
        (0.0, "raw pano frame (no rotation)"),
    ]

    for i, (off, label) in enumerate(panels):
        ax = fig.add_axes([0.03 + i * 0.325, 0.05, 0.30, 0.83])
        ax.imshow(crop)
        cyan_sat = np.zeros((*water_crop.shape, 4), dtype=np.float32)
        cyan_sat[..., 1] = 0.85; cyan_sat[..., 2] = 1.0
        cyan_sat[..., 3] = np.where(water_crop, 0.30, 0.0)
        ax.imshow(cyan_sat)
        # Range rings.
        for r_m in (50, 100, 200):
            r_px = r_m / m_per_px_x
            if r_px > crop_w_px / 2.0:
                continue
            ax.add_patch(mpatches.Circle(
                (seed_x, seed_y), r_px, fill=False,
                edgecolor=(0.6, 0.6, 0.6, 0.5),
                linewidth=0.5, linestyle="--"))
        water_pts = _project_one(ax, pano_boundaries, off,
                                  seed_x, seed_y, m_per_px_x, m_per_px_y)
        ax.scatter([seed_x], [seed_y], c="red", marker="*", s=160,
                   zorder=10, edgecolor="white")
        score = _score_match(water_pts, water_crop)
        ax.set_title(
            f"{label}\n"
            f"on-boundary: {score['n_on_boundary']} / {score['n_total']}  "
            f"({100 * score['frac_on_boundary']:.1f}%)",
            fontsize=9)
        if i == 0:
            ax.legend(loc="lower left", fontsize=7)
        ax.axis("off")

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
        pano_image=pano_image,
    )
    near_range = tuple(float(x) for x in args.near_range.split(","))
    sat_sigs = build_sat_signatures(
        sat_water, sat_project, lat, lon,
        n_bearings=args.n_bearings, near_range_m=near_range,
        far_water_range_m=(100.0, 400.0),
        sat_rgb=sat_image,
    )

    # Per-channel diagnostic.
    channels = ("water", "water_far", "skyline", "rgb",
                "d_water", "d_water_far", "d_skyline", "d_rgb")
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
    pano_boundaries = project_pano_boundaries_to_birdseye(
        pano_water, pano_building, headings_per_col,
        camera_h_m=1.7, pitch_deg=used_pitch,
        fov_deg=args.fov_deg, view_width_px=view_width_px,
        max_range_m=400.0, min_range_m=3.0,
    )
    water_n = int(np.isfinite(pano_boundaries["water_dist"]).sum())
    building_n = int(np.isfinite(pano_boundaries["building_dist"]).sum())
    print(f"[demo] pano boundaries: water-edge points={water_n}, "
          f"building-base points={building_n}")

    # Diagnostic: compute the geometric on-boundary score across a coarse
    # offset sweep. For peninsula seeds (water on both sides) this curve
    # has TWO peaks ~180° apart, same as the water cross-correlation —
    # the geometric metric doesn't disambiguate. So we DON'T override the
    # correlation answer; we just report the sweep for diagnosis.
    onboundary_offset, ob_cand, ob_scores = sweep_offset_by_onboundary(
        pano_boundaries, sat_water, sat_project, lat, lon,
        canvas_radius_m=300.0, coarse_step_deg=5.0, fine_step_deg=1.0,
        fine_window_deg=10.0,
    )
    print(f"[demo] correlation-based offset = {best_offset:.2f}°")
    print(f"[demo] on-boundary diagnostic: peak={onboundary_offset:.2f}° "
          f"score={ob_scores.max():.3f}; peak+180°={(onboundary_offset+180)%360:.2f}°")

    with PdfPages(out_pdf) as pdf:
        _render_satellite_page(pdf, sat_image, sat_water, sat_project,
                               lat, lon, args.region, args.seed_index,
                               near_range)
        _render_pano_page(pdf, pano_image, pano_water, pano_building,
                          headings_per_col, best_offset,
                          args.region, args.seed_index,
                          pano_sigs["y_top"], pano_sigs["y_bot"])
        _render_birdseye_page(pdf, sat_image, sat_water, sat_project,
                              lat, lon,
                              pano_boundaries, best_offset,
                              args.region, args.seed_index,
                              canvas_radius_m=300.0)
        _render_onboundary_sweep_page(pdf, ob_cand, ob_scores, best_offset,
                                       onboundary_offset,
                                       args.region, args.seed_index)
        _render_signatures_page(pdf, sat_sigs, pano_sigs, best_offset,
                                args.n_bearings, args.region, args.seed_index,
                                channels)
        _render_corr_page(pdf, corr, best_offset, peak_value, hwhm,
                          args.region, args.seed_index)
    print(f"[demo] wrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
