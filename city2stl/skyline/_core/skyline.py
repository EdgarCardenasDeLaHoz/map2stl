"""skyline._core.skyline — extracted from pipeline.py (A1 split)."""
from __future__ import annotations
from collections import OrderedDict as _OrderedDict

import logging
import math
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks
from shapely.geometry import shape

# F-CLEAN14: the F-SKY12 depth except-branches reference ``logger`` but the
# module never defined one (latent NameError, only reachable on a depth-module
# failure). Defined here so those branches log instead of crashing.
logger = logging.getLogger(__name__)

from .segmentation import _neural_sky_and_building_masks

def detect_skyline_contour(image_rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return a top-down skyline contour and a binary sky mask.

    SegFormer-b0 (ADE20K) sky segmentation is a hard dependency. The
    downstream mIoU registration objective relies on the building/water
    masks from the same model, so a colour-threshold fallback wouldn't be
    enough to keep the pipeline functional.
    """
    h, w = image_rgb.shape[:2]

    sky_neural, _ = _neural_sky_and_building_masks(image_rgb)
    if sky_neural is None:
        raise RuntimeError(
            "SegFormer-b0 unavailable — required for sky/building segmentation. "
            "Ensure `transformers` + `torch` are installed and the model "
            "weights can be downloaded from HuggingFace.")
    # Keep only sky pixels connected to the top border (drop interior
    # "sky" holes that aren't actually sky).
    raw_sky = (sky_neural.astype(np.uint8)) * 255
    _n_labels, labels, _stats, _centroids = cv2.connectedComponentsWithStats(
        raw_sky, connectivity=8)
    top_labels = {int(v) for v in labels[0, :] if int(v) != 0}
    sky_mask = np.zeros((h, w), dtype=np.uint8)
    for lab in top_labels:
        sky_mask[labels == lab] = 255
    if not np.any(sky_mask):
        sky_mask = raw_sky

    # ── Contour extraction ───────────────────────────────────────────────────
    contour = np.full(w, np.nan, dtype=np.float32)
    for x in range(w):
        column = sky_mask[:, x] > 0
        non_sky = np.where(~column)[0]
        if non_sky.size:
            contour[x] = float(non_sky[0])

    # Reject implausible boundaries too close to image top/bottom.
    y_min = float(h * 0.06)
    y_max = float(h * 0.90)
    contour = np.where((contour >= y_min) & (
        contour <= y_max), contour, np.nan)

    if np.all(np.isnan(contour)):
        contour[:] = float(h * 0.5)
    else:
        valid_idx = np.where(~np.isnan(contour))[0]
        valid_vals = contour[valid_idx]
        if valid_idx.size >= 2:
            interp = np.interp(
                np.arange(w, dtype=np.float32),
                valid_idx.astype(np.float32),
                valid_vals.astype(np.float32),
            )
            contour = interp.astype(np.float32)
        else:
            fill = np.nanmedian(contour)
            contour = np.where(np.isnan(contour), fill, contour)
        contour = median_filter(
            contour, size=9, mode="nearest").astype(np.float32)

    return contour, sky_mask > 0

def _segment_has_structure(
    image: np.ndarray,
    x_left: int,
    x_right: int,
    top_y: int,
    base_y: int,
    building_mask: "np.ndarray | None" = None,
    peak_x: int | None = None,
) -> bool:
    """Return True if the image region plausibly contains a building facade.

    Tests two cuts against the SegFormer building mask:
      1. Bounding-box fraction (≥ 10 % accept). Wide boxes around slim glass
         towers contain a lot of sky and easily fall below this even though
         the tower itself is obviously a building, so we also test...
      2. Peak-column fraction (≥ 25 % accept). A tight ±4 px column around
         ``peak_x`` restricted to the lower 60 % of the segment captures the
         facade core where the tower lives.

    A region passing either cut is kept. A region failing BOTH (bfrac < 5 %
    AND col_frac < 10 %) is rejected. Ambiguous middle band is accepted —
    the colour/texture heuristics that previously broke ties were removed
    because they almost never disagreed with the mask in practice.
    """
    h, w = image.shape[:2]
    y0 = max(0, top_y)
    y1 = min(h, base_y)
    x0 = max(0, x_left)
    x1 = min(w, x_right)
    if y1 <= y0 or x1 <= x0:
        return True  # degenerate region: don't reject

    if building_mask is None:
        # Without a mask we can't make an informed call; accept and let
        # downstream stages filter. SegFormer is a hard dependency in
        # production runs, so this branch should never fire.
        return True

    bfrac = float(building_mask[y0:y1, x0:x1].mean())
    px = int(peak_x) if peak_x is not None else (x0 + x1) // 2
    cx0 = max(0, px - 4)
    cx1 = min(w, px + 5)
    trunk_top = y0 + (y1 - y0) * 2 // 5
    col_frac = (
        float(building_mask[trunk_top:y1, cx0:cx1].mean())
        if cx1 > cx0 else 0.0
    )
    if bfrac >= 0.10 or col_frac >= 0.25:
        return True
    if bfrac < 0.05 and col_frac < 0.10:
        return False
    return True

def _footprint_roof_y_from_mask(
    building_mask: "np.ndarray | None",
    x_left: int,
    x_right: int,
    min_avg_rows: float = 8.0,
    min_run_px: int = 3,
) -> tuple[int | None, float]:
    """Sample the building mask across the projected footprint x-range and
    return (roof_y, avg_building_rows_per_column).

    Previously this used a "fraction of FULL-HEIGHT box is building" gate
    (25 %) which systematically rejected short or distant buildings whose
    silhouette only occupies a fraction of the image height — even a 30-
    story tower at 1 km projects ~30 px tall (5 % of a 540 px frame) and
    would fail. The denominator was the whole image height; that's wrong.

    The new gate is **average building rows per column** — at least 8 rows
    of building pixels per column on average. This passes any real building
    silhouette and rejects spurious matches where the projected x-range
    lands on water/sky.
    """
    if building_mask is None:
        return None, 0.0
    h, w = building_mask.shape[:2]
    x0 = max(0, x_left)
    x1 = min(w - 1, x_right)
    if x1 < x0:
        return None, 0.0
    slab = building_mask[:, x0: x1 + 1].astype(np.uint8)
    if slab.size == 0:
        return None, 0.0
    cols = x1 - x0 + 1
    rows = slab.sum(axis=1)
    avg_rows = float(slab.sum()) / float(cols)
    if avg_rows < min_avg_rows:
        return None, avg_rows
    threshold = max(1, cols // 2)
    in_run = 0
    for y in range(h):
        if rows[y] >= threshold:
            in_run += 1
            if in_run >= min_run_px:
                return int(y - min_run_px + 1), avg_rows
        else:
            in_run = 0
    return None, avg_rows

def _building_base_y_from_mask(
    building_mask: "np.ndarray | None",
    peak_x: int,
    top_y: int,
    half_width: int = 4,
    min_gap_px: int = 4,
) -> int | None:
    """Return the last row at column peak_x where the building mask is still
    majority-building (allowing small mask gaps).

    Walking the SegFormer building mask DOWN from top_y gives us the column
    where the facade actually ends — far more accurate than _estimate_building_base
    which gets confused by cloud edges, balcony rails, and window mullions.
    """
    if building_mask is None:
        return None
    h, w = building_mask.shape[:2]
    if peak_x < 0 or peak_x >= w:
        return None
    x0 = max(0, peak_x - half_width)
    x1 = min(w, peak_x + half_width + 1)
    if x1 <= x0:
        return None
    slab = building_mask[:, x0:x1].astype(np.uint8)
    row_counts = slab.sum(axis=1)
    threshold = max(1, (x1 - x0) // 2 + 1)

    last_building = -1
    consecutive_gap = 0
    for y in range(max(0, top_y), h):
        if row_counts[y] >= threshold:
            last_building = y
            consecutive_gap = 0
        else:
            consecutive_gap += 1
            if consecutive_gap >= min_gap_px and last_building >= 0:
                break
    return last_building if last_building >= 0 else None

def _floor_period_for_building(
    image_rgb: np.ndarray,
    building_mask: "np.ndarray | None",
    x_range: tuple[int, int],
    y_top: float,
    y_base: float,
    *,
    f_px: float,
    floor_height_m: float = 3.2,
    min_lag_px: int = 4,
    max_lag_px: int = 80,
    min_confidence: float = 0.20,
    min_row_coverage: float = 0.30,
) -> dict | None:
    """Detect horizontal floor banding on a building facade and back out
    an OSM-independent height + distance estimate (F-SKY1).

    Idea: window rows, balconies, and slab edges produce regular horizontal
    stripes in a tall building's mask region. The dominant pixel period of
    those stripes is one floor. Given pinhole focal length f_px and an
    assumed physical floor height (3.2 m default — mid of residential
    2.8 m and commercial 3.6 m), distance is
    ``f_px × floor_height_m / period_px`` and total height is
    ``(facade_height_px / period_px) × floor_height_m``.

    Returns a dict with ``floor_period_px``, ``floor_confidence``,
    ``inferred_distance_m``, ``inferred_floors``, ``inferred_height_m``,
    or None when no usable period is detected (mask missing, region too
    short, autocorrelation peak below confidence threshold).

    See ``docs/plans/F-SKY1-floor-periodicity.md`` for the rationale and
    where this signal is meant to slot into the height aggregate.
    """
    if building_mask is None or image_rgb is None:
        return None
    H, W = building_mask.shape[:2]
    xL = max(0, int(x_range[0]))
    xR = min(W, int(x_range[1]) + 1)
    yT = max(0, int(round(y_top)))
    yB = min(H, int(round(y_base)))
    if xR - xL < 4 or yB - yT < max_lag_px * 2:
        return None
    if f_px <= 0.0 or floor_height_m <= 0.0:
        return None

    img_band = image_rgb[yT:yB, xL:xR]
    mask_band = building_mask[yT:yB, xL:xR]
    if img_band.size == 0 or not mask_band.any():
        return None

    # Per-row mean luminance over building-mask pixels only. Rows whose
    # mask coverage is below the threshold get filled with the band's
    # overall mean so they don't introduce a step into the FFT.
    luma = (
        0.299 * img_band[..., 0].astype(np.float32)
        + 0.587 * img_band[..., 1].astype(np.float32)
        + 0.114 * img_band[..., 2].astype(np.float32)
    )
    mask_f = mask_band.astype(np.float32)
    row_cov = mask_f.mean(axis=1)
    valid_rows = row_cov >= min_row_coverage
    if int(valid_rows.sum()) < max_lag_px * 2:
        return None
    row_sums = (luma * mask_f).sum(axis=1)
    row_counts = mask_f.sum(axis=1)
    safe_counts = np.where(row_counts > 0, row_counts, 1.0)
    profile = (row_sums / safe_counts).astype(np.float32)
    band_mean = float(profile[valid_rows].mean())
    profile = np.where(valid_rows, profile, band_mean).astype(np.float32)

    # High-pass: subtract a 12-row uniform mean to suppress whole-band
    # illumination gradients (top-of-tower in shadow, bottom lit by
    # waterfront sun). A 3-tap median then damps salt-and-pepper from
    # the SegFormer mask without flattening the floor period.
    smoothed = uniform_filter1d(profile, size=12, mode="nearest")
    high = profile - smoothed
    high = median_filter(high, size=3)
    high = high - float(high.mean())

    n = high.size
    full = np.correlate(high, high, mode="full")
    autocorr = full[n - 1:]
    norm = float(autocorr[0])
    if norm <= 1e-6:
        return None
    autocorr = autocorr / norm

    if max_lag_px >= autocorr.size:
        return None
    window = autocorr[min_lag_px: max_lag_px + 1]
    if window.size < 3:
        return None

    # Fundamental-period detection via sub-harmonic descent (harmonic
    # disambiguation). The autocorrelation of a periodic facade peaks
    # at the fundamental floor period P *and* every multiple 2P, 3P, …
    # The DOMINANT peak is frequently a multiple when the building has
    # strong coarse banding (mechanical floors, every-3rd-floor balcony
    # bands), so a naive argmax returns 2-3× the true period and the
    # back-derived distance comes out 2-3× too small.
    #
    # Strategy: take the dominant peak lag L_best, then test its integer
    # sub-divisors L_best/k (k=2..6). If a divisor lag d still shows a
    # strong autocorr peak (≥ ``sub_frac`` of the dominant), d is the
    # real fundamental and we descend to it. We keep the SMALLEST such
    # divisor that holds up — that's the fundamental floor period.
    best_abs = int(min_lag_px + int(np.argmax(window)))
    best_val = float(autocorr[best_abs])
    if best_val < min_confidence:
        return None

    def _peak_val_near(lag: int, tol: int = 1) -> tuple[int, float]:
        """Best autocorr value within ±tol of `lag` (periods aren't
        exact integers); returns (lag_of_max, value)."""
        lo = max(min_lag_px, lag - tol)
        hi = min(max_lag_px, lag + tol)
        if hi < lo:
            return lag, -1.0
        seg = autocorr[lo: hi + 1]
        j = int(np.argmax(seg))
        return lo + j, float(seg[j])

    sub_frac = 0.55
    fund_abs = best_abs
    for k in (6, 5, 4, 3, 2):  # try the deepest division first
        d = int(round(best_abs / k))
        if d < min_lag_px:
            continue
        d_lag, d_val = _peak_val_near(d)
        if d_val >= sub_frac * best_val and d_val >= min_confidence:
            fund_abs = d_lag  # descend; keep iterating toward smaller k
    peak_idx_rel = fund_abs - min_lag_px
    peak_val = float(autocorr[fund_abs])
    period_px = float(fund_abs)
    if period_px <= 0.0:
        return None

    inferred_distance_m = (f_px * floor_height_m) / period_px
    inferred_floors = float(yB - yT) / period_px
    inferred_height_m = inferred_floors * floor_height_m
    return {
        "floor_period_px": period_px,
        "floor_confidence": peak_val,
        "inferred_distance_m": float(inferred_distance_m),
        "inferred_floors": float(inferred_floors),
        "inferred_height_m": float(inferred_height_m),
    }

def _estimate_building_base(
    image: np.ndarray,
    peak_x: int,
    top_y: int,
    h: int,
    building_mask: "np.ndarray | None" = None,
) -> int:
    """Estimate the row where a building's base meets the ground/water.

    Walks the SegFormer building mask down from top_y at peak_x. The Sobel/
    brightness fallback was removed — it was only invoked when the mask was
    unavailable, which doesn't happen in practice (SegFormer is a hard
    dependency on the rest of the pipeline). Falls back to 75% frame height
    when no building pixels extend at least 10 rows below the rooftop
    (truncated building, weird mask).
    """
    if building_mask is not None:
        mask_base = _building_base_y_from_mask(building_mask, peak_x, top_y)
        if mask_base is not None and mask_base > top_y + 10:
            return min(h - 1, mask_base)
    return min(h - 1, int(h * 0.75))

def detect_building_silhouettes(
    contour_y: np.ndarray,
    image: np.ndarray,
    min_width_px: int = 15,
    min_prominence_px: float = 8.0,
) -> list[dict]:
    """Identify **individual** building silhouettes in a skyline view.

    Strategy
    --------
    1. Find *building peaks* — local minima of the smoothed contour (low y =
       high in the image = tall building).  Each peak is one building.
    2. Find *valley boundaries* — local maxima of the contour (sky dipping
       between buildings) plus vertical Sobel edges.
    3. For each peak, assign the narrowest enclosing boundary segment.
    4. Filter by prominence and minimum width.
    5. Deduplicate: if two peaks fall in the same boundary segment, keep only
       the taller one.
    6. Estimate the building base row from image brightness below each rooftop.

    Returns a list of dicts compatible with ``match_segments_to_buildings``:
    ``{x_left, x_right, top_y, base_y, mid_x, peak_x}``.
    """
    from scipy.ndimage import uniform_filter1d, gaussian_filter1d  # already at module level
    c = np.asarray(contour_y, dtype=np.float32)
    h, w = image.shape[:2]

    finite = c[np.isfinite(c)]
    if finite.size == 0:
        return []

    global_min = float(np.nanmin(finite))
    global_max = float(np.nanmax(finite))
    global_median = float(np.nanmedian(finite))
    height_range = global_max - global_min

    if height_range < min_prominence_px:
        return []

    # Fill NaN for smoothing
    c_filled = np.where(np.isfinite(c), c, global_median)

    # Fine-scale smoothing to resolve individual buildings
    smooth = uniform_filter1d(c_filled, size=7)

    # ── Building peaks (individual rooftops) ─────────────────────────────
    # Invert: find_peaks on -smooth → local minima of contour → building tops.
    # Prominence floor is intentionally low: a 4-tower Bocagrande skyline has
    # ~20–50 px variation between towers, and a 0.06×range threshold (= 15 px
    # at range=250) drops most of them. 0.04×range with a 3 px floor still
    # rejects noise but keeps real tower-to-tower steps.
    peaks, _ = find_peaks(
        -smooth,
        distance=12,
        prominence=max(3.0, height_range * 0.04),
    )

    # F-SKY7: also catch local-max peaks within continuous mask regions.
    # When SegFormer's building mask spans a row of glass towers without
    # sky valleys between them, the contour stays HIGH (low y) everywhere
    # and the global-prominence filter above rejects per-tower bumps.
    # Detect bumps relative to a smoothed regional baseline (40 px window
    # ≈ 6° of FOV at W=640) so monotone-but-bumpy rooflines still
    # produce one peak per tower. The absolute prominence floor (6 px)
    # is what stops contour noise from inventing spurious peaks.
    #
    # Two baseline widths run in parallel:
    #   - 40 px wide baseline catches well-spaced 25-50 m towers
    #   - 22 px tight baseline catches the dense Bocagrande-style row of
    #     similar-height glass towers (3-4° FOV/tower) that the wide
    #     baseline averages out (the bump signal flattens when peak
    #     spacing approaches the baseline width). Higher prominence
    #     floor (8 px) compensates for the noisier tight baseline.
    # Both peak sets merge via the same de-dup pass below.
    baseline = uniform_filter1d(c_filled, size=40, mode="nearest")
    bump = baseline - smooth
    local_peaks, _ = find_peaks(
        bump,
        distance=12,
        prominence=6.0,
    )
    baseline_tight = uniform_filter1d(c_filled, size=22, mode="nearest")
    bump_tight = baseline_tight - smooth
    tight_peaks, _ = find_peaks(
        bump_tight,
        distance=10,
        prominence=8.0,
    )
    if tight_peaks.size > 0:
        if local_peaks.size > 0:
            keep_t = np.array(
                [int(abs(local_peaks - tp).min()) > 8 for tp in tight_peaks],
                dtype=bool,
            )
            tight_peaks = tight_peaks[keep_t]
        if tight_peaks.size > 0:
            local_peaks = np.sort(np.concatenate([local_peaks, tight_peaks]))
    if local_peaks.size > 0:
        if peaks.size > 0:
            # De-dup: only keep local-max peaks > 8 px from any existing peak.
            keep = np.array(
                [int(abs(peaks - lp).min()) > 8 for lp in local_peaks],
                dtype=bool,
            )
            local_peaks = local_peaks[keep]
        if local_peaks.size > 0:
            peaks = np.sort(np.concatenate([peaks, local_peaks]))

    if peaks.size == 0:
        return []

    # ── Boundary signals ─────────────────────────────────────────────────
    # 1. Valleys in contour (sky between buildings)
    valleys, _ = find_peaks(
        smooth,
        distance=10,
        prominence=max(3.0, height_range * 0.04),
    )

    # 2. Vertical Sobel edges in sky-building transition zone
    gray = cv2.cvtColor(
        image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image.astype(np.uint8)
    sobel_x = np.abs(cv2.Sobel(gray.astype(
        np.float32), cv2.CV_32F, 1, 0, ksize=3))
    zone_top = max(0, int(global_min) - 15)
    zone_bot = min(h, int(global_median) + 30)
    if zone_bot > zone_top:
        edge_col = sobel_x[zone_top:zone_bot, :].mean(
            axis=0).astype(np.float32)
        edge_smooth = gaussian_filter1d(edge_col, sigma=2.0)
        edge_thr = float(edge_smooth.mean()) + float(edge_smooth.std()) * 0.8
        edge_bounds, _ = find_peaks(edge_smooth, distance=12, height=edge_thr)
    else:
        edge_bounds = np.array([], dtype=np.int32)

    # All boundaries (include image edges)
    boundaries = np.sort(np.unique(
        np.concatenate([[0], valleys, edge_bounds, [w - 1]])
    ))

    # ── Assign each peak to its narrowest enclosing boundary segment ──────
    # Fetch neural building mask from cache (populated by detect_skyline_contour
    # if it was called with the same image object in this request cycle).
    _, _building_mask = _neural_sky_and_building_masks(image)
    raw: list[dict] = []
    for peak_x in peaks:
        top_y_raw = float(c[peak_x]) if np.isfinite(
            c[peak_x]) else float(smooth[peak_x])

        if global_median - top_y_raw < min_prominence_px:
            continue

        left_cands = boundaries[boundaries <= peak_x]
        right_cands = boundaries[boundaries >= peak_x]
        if left_cands.size == 0 or right_cands.size == 0:
            continue

        x_left = int(left_cands[-1])
        x_right = int(right_cands[0])

        if x_right - x_left < min_width_px:
            continue

        base_y = _estimate_building_base(
            image, int(peak_x), int(top_y_raw), h,
            building_mask=_building_mask,
        )

        # Validate image content — reject water, sky bleed, featureless regions.
        # Pass peak_x so the tight-column check evaluates at the actual rooftop
        # location, not the segment midpoint (which can be off-center on a slim
        # glass tower sitting at one end of a wide segment).
        if not _segment_has_structure(
            image, x_left, x_right, int(top_y_raw), base_y,
            building_mask=_building_mask, peak_x=int(peak_x),
        ):
            continue

        raw.append({
            "x_left": x_left,
            "x_right": x_right,
            "top_y": int(top_y_raw),
            "base_y": base_y,
            "mid_x": (x_left + x_right) // 2,
            "peak_x": int(peak_x),
        })

    # ── Deduplicate: same (x_left, x_right) → keep tallest ───────────────
    best: dict[tuple, dict] = {}
    for sil in raw:
        key = (sil["x_left"], sil["x_right"])
        if key not in best or sil["top_y"] < best[key]["top_y"]:
            best[key] = sil

    return sorted(best.values(), key=lambda s: s["mid_x"])

def compute_building_band(
    building_mask: "np.ndarray | None",
    min_col_frac: float = 0.01,
    slack_px: int = 5,
) -> tuple[int, int] | None:
    """Return (y_top, y_bot) — the vertical band where building pixels exist.

    A row is "building-active" if ≥ min_col_frac of its columns are building.
    The returned band spans from the topmost to bottommost active row,
    expanded by ``slack_px``. Returns None if no row passes the threshold.

    Used by detect_buildings_from_mask and the PDF renderer to crop away
    water/sky bands the building mask doesn't occupy — letting segments and
    visual displays focus on the actual skyline band.
    """
    if building_mask is None:
        return None
    bm = np.asarray(building_mask)
    if bm.size == 0:
        return None
    h, w = bm.shape[:2]
    if w == 0 or h == 0:
        return None
    row_frac = bm.sum(axis=1).astype(np.float32) / float(w)
    active = np.where(row_frac >= min_col_frac)[0]
    if active.size == 0:
        return None
    y_top = max(0, int(active[0]) - slack_px)
    y_bot = min(h - 1, int(active[-1]) + slack_px)
    if y_bot <= y_top + 5:
        return None
    return y_top, y_bot

def _component_gradient_col_signal(
    image: "np.ndarray | None", building_mask: np.ndarray
) -> "np.ndarray | None":
    """Per-column vertical-gradient signal across the frame, integrated over
    the building band (Phase A). Captures facade edges between adjacent towers
    that share a roofline. Returns None when no usable RGB image is supplied;
    the contour + col-count signals then carry the work.
    """
    if image is None or image.ndim != 3:
        return None
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32)
        sx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        # Integrate vertical edges within the BUILDING band only. Rows outside
        # the band would add noise from water/sky textures.
        band_for_grad = compute_building_band(building_mask, slack_px=8)
        if band_for_grad is not None:
            y0g, y1g = band_for_grad
            grad_col_signal = sx[y0g : y1g + 1].sum(axis=0)
        else:
            grad_col_signal = sx.sum(axis=0)
        # Mild smoothing — facade edges are thin but noise is thinner.
        return gaussian_filter1d(grad_col_signal, sigma=1.5).astype(np.float32)
    except Exception:
        return None

def _component_peak_columns(
    col_counts: np.ndarray,
    grad_col_signal: "np.ndarray | None",
    contour: "np.ndarray | None",
    x: int,
    cw: int,
    min_width_px: int,
    max_splits_per_component: int,
) -> np.ndarray:
    """Pick per-tower peak columns (component-local offsets) inside one mask
    component. Unions gradient-subdivided col_counts peaks, contour spire
    peaks, and raw col_counts peaks, then dedups (10 px), support-filters, and
    caps the count. Returns offsets in [0, cw); an empty array tells the caller
    to emit a single silhouette for the whole component. Extracted verbatim
    from detect_buildings_from_mask (F-CLEAN14).
    """
    peaks: np.ndarray = np.empty(0, dtype=np.int64)
    from scipy.signal import find_peaks  # scipy already a project dep
    # Phase A rework: gradient peaks are facade BOUNDARIES (between
    # towers), not building centers. Previously we unioned them with
    # contour and col-counts peaks, then applied a mask-height
    # support threshold — which rejected the gradient peaks because
    # facade gaps have LOW mask coverage, not high. The result was
    # zero gradient contribution on the exact case it's needed for:
    # rows of similar-height towers with flat col_counts.
    #
    # New approach:
    #   1. Find gradient peaks → use them as BOUNDARIES that
    #      subdivide the component's column range.
    #   2. Within each subrange, pick ONE peak (col_counts argmax)
    #      as that subdivision's building center.
    #   3. Union with contour peaks (still useful for spires).
    #
    # This lets the gradient drive splitting even when col_counts
    # is flat — exactly the merged-row-of-towers case.
    grad_boundaries: list[int] = []
    if grad_col_signal is not None:
        grad_slice = grad_col_signal[x : x + cw].astype(np.float32)
        grad_std = float(np.std(grad_slice))
        grad_prom = max(1.5, grad_std * 0.20)
        gp, _ = find_peaks(
            grad_slice, distance=10, prominence=grad_prom)
        grad_boundaries = [int(v) for v in gp.tolist()]

    # Build the subrange list using gradient boundaries.
    subrange_edges = [0, *sorted(grad_boundaries), cw - 1]
    # Per subrange, pick the col_counts argmax as the candidate
    # building center. Skip degenerate (too-narrow) subranges.
    grad_derived_peaks: list[int] = []
    for a, b in zip(subrange_edges[:-1], subrange_edges[1:]):
        if b - a < 6:
            continue
        sub = col_counts[a : b + 1]
        if sub.size == 0 or sub.max() < 6:
            continue
        grad_derived_peaks.append(int(np.argmax(sub)) + a)

    # Also find contour peaks (rooftop spires) — these still help
    # when towers have distinctive rooflines even within a flat row.
    contour_peaks: list[int] = []
    if contour is not None and contour.size >= x + cw:
        contour_slice = np.asarray(
            contour[x: x + cw], dtype=np.float32)
        if np.any(~np.isfinite(contour_slice)):
            fill = float(np.nanmedian(contour_slice))
            contour_slice = np.where(np.isfinite(
                contour_slice), contour_slice, fill)
        c_std = float(np.std(contour_slice))
        c_prom = max(1.5, c_std * 0.15)
        cp, _ = find_peaks(-contour_slice, distance=8, prominence=c_prom)
        contour_peaks = [int(v) for v in cp.tolist()]

    # And col_counts peaks (the original signal) — these catch
    # narrow spires that don't show up in the gradient subdivision
    # because they're entirely within one gap-bounded subrange.
    col_std = float(np.std(col_counts))
    col_prom = max(1.5, col_std * 0.15)
    cc_p, _ = find_peaks(col_counts, distance=8, prominence=col_prom)
    col_peaks = [int(v) for v in cc_p.tolist()]

    # Union all three sources.
    all_peaks = grad_derived_peaks + contour_peaks + col_peaks

    # Dedup with a 10-px tolerance — peaks within that window from
    # different signals are the same tower.
    if all_peaks:
        all_peaks.sort()
        dedup: list[int] = [all_peaks[0]]
        for p in all_peaks[1:]:
            if p - dedup[-1] >= 10:
                dedup.append(p)
        # Anti-over-split guard: require mask-height support for
        # each peak. With gradient now used to subdivide (not as a
        # peak source), the support threshold is purely a noise
        # filter on contour/col peaks. The gradient-derived peaks
        # already passed through the col_counts argmax, so they
        # implicitly have support.
        col_peak_h = float(col_counts.max())
        support_threshold = max(6.0, col_peak_h * 0.25)
        supported = [
            p for p in dedup
            if col_counts[p] >= support_threshold
        ]
        if supported:
            dedup = supported
        # Cap the number of splits to avoid degenerate over-segmentation
        # of very wide components (a 1500-px-wide row of identical
        # towers shouldn't produce 30+ tiny segments).
        if len(dedup) > max_splits_per_component:
            # Keep the top-N by col-height (tallest support wins).
            dedup = sorted(
                dedup,
                key=lambda p: float(col_counts[p]),
                reverse=True,
            )[:max_splits_per_component]
            dedup.sort()
        peaks = np.asarray(dedup, dtype=np.int64)
    return peaks

def detect_buildings_from_mask(
    building_mask: "np.ndarray | None",
    min_width_px: int = 18,
    min_height_px: int = 25,
    split_wide_components: bool = True,
    contour: "np.ndarray | None" = None,
    image: "np.ndarray | None" = None,
    max_splits_per_component: int = 24,
) -> list[dict]:
    """Identify individual buildings as connected components of the SegFormer
    building mask.

    This complements ``detect_building_silhouettes`` which depends on the
    skyline contour having a prominent valley between adjacent buildings.
    The mask-based approach catches every blob the segmenter labels as
    building, including:

    - towers that share a similar roof height (no contour valley between them)
    - buildings tucked behind a closer one whose roof barely peeks above
    - mid-rise rows where the skyline is nearly flat

    Wide components are common on long skyline panoramas (Bocagrande from
    across the bay, Old Town from Manga): adjacent towers share base pixels
    and merge into one big blob. When ``split_wide_components`` is True we
    look for local maxima of the per-column building height inside the blob
    and emit one silhouette per peak, so a single mask component covering
    "row of 10 towers" produces 10 silhouettes instead of one.

    Returns the same dict shape as ``detect_building_silhouettes`` so the
    downstream matcher can consume both sources uniformly.
    """
    if building_mask is None:
        return []
    mask = (building_mask.astype(np.uint8)) * 255
    if not mask.any():
        return []
    h, w = mask.shape[:2]

    grad_col_signal = _component_gradient_col_signal(image, building_mask)

    # Pre-crop to the building band: zero out mask rows above/below the band
    # where building pixels actually exist. Stops connected components from
    # spanning down into water reflections / boats picked up by the SegFormer
    # mask, and gives downstream peak detection a tighter target. The band is
    # computed from the same mask, so this is a no-op when buildings cover
    # most of the frame and a big help when most of the frame is water/sky.
    band = compute_building_band(building_mask)
    if band is not None:
        y_top, y_bot = band
        # Outside the band, zero the mask so connected components and per-
        # column reductions don't include water/sky reflections.
        mask = mask.copy()
        if y_top > 0:
            mask[:y_top, :] = 0
        if y_bot < h - 1:
            mask[y_bot + 1:, :] = 0

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask, connectivity=8)
    out: list[dict] = []
    for i in range(1, n_labels):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        if cw < min_width_px or ch < min_height_px:
            continue
        component = labels[y: y + ch, x: x + cw] == i
        # Per-column "building height in pixels" — the silhouette of this blob.
        col_counts = component.sum(axis=0).astype(np.float32)
        if col_counts.size == 0:
            continue

        # For each column find the topmost building row (within this component)
        # to use as the rooftop pixel of a per-peak silhouette below.
        top_rows = np.full(cw, -1, dtype=np.int32)
        for off in range(cw):
            rows = np.where(component[:, off])[0]
            if rows.size:
                top_rows[off] = int(rows[0])

        # Decide whether to emit a single silhouette for the whole component
        # or split it into multiple peaks. When the skyline ``contour`` is
        # provided we use IT for peak detection (the actual rooftop silhouette
        # captures tower-to-tower variation directly, even when adjacent towers
        # share base pixels in the mask). Otherwise we fall back to the mask's
        # per-column height profile.
        peaks = np.empty(0, dtype=np.int64)
        if split_wide_components and cw >= int(1.5 * min_width_px):
            peaks = _component_peak_columns(
                col_counts, grad_col_signal, contour, x, cw,
                min_width_px, max_splits_per_component)

        if peaks.size >= 2:
            # Split: one silhouette per peak. We bound each peak by walking left
            # and right from the peak column until col_counts drops below
            # 0.4 * peak height — that's the actual edge of THIS tower in the
            # mask. The valleys between peaks act as hard ceilings so we never
            # cross into the neighboring tower; the component edges bound the
            # outermost peaks. The previous valley-to-valley scheme used the
            # gap BETWEEN buildings as the box edge, producing boxes that
            # extended halfway to the next tower.
            valleys: list[int] = [0]
            for j in range(peaks.size - 1):
                a, b = int(peaks[j]), int(peaks[j + 1])
                if a >= b:
                    continue
                v = int(np.argmin(col_counts[a: b + 1])) + a
                valleys.append(v)
            valleys.append(cw - 1)
            edge_frac = 0.4
            for j, p in enumerate(peaks):
                p = int(p)
                peak_h = float(col_counts[p])
                if peak_h <= 0:
                    continue
                cutoff = peak_h * edge_frac
                left_bound = int(valleys[j])
                right_bound = int(valleys[j + 1])
                # Walk left from peak to first column < cutoff (or left_bound).
                xL_off = p
                k = p
                while k > left_bound and col_counts[k] >= cutoff:
                    xL_off = k
                    k -= 1
                # Walk right from peak to first column < cutoff (or right_bound).
                xR_off = p
                k = p
                while k < right_bound and col_counts[k] >= cutoff:
                    xR_off = k
                    k += 1
                xL = x + max(0, xL_off)
                xR = x + min(cw - 1, xR_off)
                if xR - xL < min_width_px:
                    continue
                # Top row at this peak's column
                top_off = int(top_rows[p]) if top_rows[p] >= 0 else 0
                top_y = int(y + top_off)
                # Tighten base_y by walking the ORIGINAL mask down from this
                # peak's top until building coverage drops out. Previously
                # base_y = y + ch - 1 (component bottom), which extended into
                # water when the SegFormer mask included reflections / boats.
                peak_x_abs = x + p
                base_walked = _building_base_y_from_mask(
                    building_mask, peak_x_abs, top_y)
                if base_walked is not None and base_walked > top_y + 5:
                    base_y = int(min(h - 1, base_walked))
                else:
                    base_y = int(min(h - 1, y + ch - 1))
                out.append({
                    "x_left": xL,
                    "x_right": xR,
                    "top_y": top_y,
                    "base_y": base_y,
                    "mid_x": (xL + xR) // 2,
                    "peak_x": peak_x_abs,
                })
        else:
            # Single-peak fallback: still tighten the segment to the actual
            # silhouette by walking left/right from the peak column until
            # col_counts drops below 0.4 * peak.
            peak_col_off = int(np.argmax(col_counts))
            peak_h = float(col_counts[peak_col_off])
            cutoff = peak_h * 0.4
            xL_off = peak_col_off
            k = peak_col_off
            while k > 0 and col_counts[k] >= cutoff:
                xL_off = k
                k -= 1
            xR_off = peak_col_off
            k = peak_col_off
            while k < cw - 1 and col_counts[k] >= cutoff:
                xR_off = k
                k += 1
            peak_x = x + peak_col_off
            top_off = int(top_rows[peak_col_off]
                          ) if top_rows[peak_col_off] >= 0 else 0
            top_y = int(y + top_off)
            base_walked = _building_base_y_from_mask(
                building_mask, peak_x, top_y)
            if base_walked is not None and base_walked > top_y + 5:
                base_y = int(min(h - 1, base_walked))
            else:
                base_y = int(min(h - 1, y + ch - 1))
            xL = x + xL_off
            xR = x + xR_off
            if xR - xL < min_width_px:
                xL = x
                xR = x + cw - 1
            out.append({
                "x_left": xL,
                "x_right": xR,
                "top_y": top_y,
                "base_y": base_y,
                "mid_x": (xL + xR) // 2,
                "peak_x": peak_x,
            })
    return out

def _merge_silhouette_sources(
    primary: list[dict],
    secondary: list[dict],
    iou_thresh: float = 0.3,
) -> list[dict]:
    """Merge two silhouette lists, deduplicating by horizontal IoU.

    A silhouette from ``secondary`` is kept only if it doesn't overlap any
    ``primary`` silhouette's x range by more than ``iou_thresh``. The peak
    column from each surviving silhouette is preserved for height extraction.
    """
    def _x_iou(a: dict, b: dict) -> float:
        ax0, ax1 = float(a["x_left"]), float(a["x_right"])
        bx0, bx1 = float(b["x_left"]), float(b["x_right"])
        inter = max(0.0, min(ax1, bx1) - max(ax0, bx0))
        union = max(ax1, bx1) - min(ax0, bx0)
        return inter / union if union > 0 else 0.0

    out = list(primary)
    for s in secondary:
        if all(_x_iou(s, p) < iou_thresh for p in out):
            out.append(s)
    return sorted(out, key=lambda s: s["mid_x"])

def _building_roof_y_from_mask(
    building_mask: "np.ndarray | None",
    x_px: int,
    half_width: int = 4,
    min_run_px: int = 3,
) -> int | None:
    """Return the topmost row whose neighborhood around x_px is dominated by
    building-class pixels.

    Compared to reading the global skyline contour at ``x_px``, this is robust
    to clouds, lampposts, or anything else that ``detect_skyline_contour`` may
    classify as non-sky: only pixels labelled as ADE20K *building* count, so a
    cloudy bay view returns None (no roof here) instead of a cloud edge y.

    Returns the y of the first pixel in a vertical run of ``min_run_px``
    building-majority rows, or None when no building presence is found.
    """
    if building_mask is None:
        return None
    h, w = building_mask.shape[:2]
    if x_px < 0 or x_px >= w:
        return None
    x0 = max(0, x_px - half_width)
    x1 = min(w, x_px + half_width + 1)
    slab = building_mask[:, x0:x1].astype(np.uint8)
    if slab.size == 0:
        return None
    row_counts = slab.sum(axis=1)
    threshold = max(1, (x1 - x0) // 2 + 1)  # majority of sampled columns
    in_run = 0
    for y in range(h):
        if row_counts[y] >= threshold:
            in_run += 1
            if in_run >= min_run_px:
                return y - min_run_px + 1
        else:
            in_run = 0
    return None


__all__ = [
    'detect_skyline_contour',
    '_segment_has_structure',
    '_footprint_roof_y_from_mask',
    '_building_base_y_from_mask',
    '_floor_period_for_building',
    '_estimate_building_base',
    'detect_building_silhouettes',
    'compute_building_band',
    '_component_gradient_col_signal',
    '_component_peak_columns',
    'detect_buildings_from_mask',
    '_merge_silhouette_sources',
    '_building_roof_y_from_mask',
]
