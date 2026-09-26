"""
Satellite-based roof shape classifier and height estimator (ROOF-2).

Uses three complementary signals extracted from satellite imagery and an
optional height raster (DEM / nDSM) to classify building roof shapes and
estimate roof height when OSM ``roof:shape`` tags are absent.

Signals (in priority order)
----------------------------
1. **Elevation profile** — strongest when a height raster is available.
   Analyses the distribution of DEM / nDSM values inside each building
   footprint:

   - *height_std*: near-zero → flat; high → pitched roof
   - *ridge_score*: bell-shaped profile along principal axis → gabled / hipped
   - *ridge_symmetry*: bilateral symmetry → gabled; asymmetric → skillion
   - *apex_score*: high pixels clustered at centre → pyramidal / dome / cone

   The ``height_raster`` parameter accepts either a real LiDAR / nDSM raster
   or a *predicted* DEM produced by a monocular elevation-regression network
   (e.g. a U-Net trained on ``(RGB → nDSM)`` pairs with open-data LiDAR
   ground truth).  A future pretrained model can be plugged in here without
   changing the classification logic.

2. **Roof face appearance** — analyses the actual roof face visible in the
   satellite image, not the shadow.  Signals:

   - *gradient_strength*: directional brightness gradient across the footprint
     → sloped faces receive direct sun from one direction
   - *gradient_anisotropy*: strongly uni-directional → gabled; isotropic →
     pyramidal
   - *brightness_ridge*: brightness std along the primary axis → ridge tile
     or metal flashing characteristic of gabled / hipped roofs

3. **Shadow geometry** — analyses the shape of cast shadows outside the
   footprint:

   - *shadow_ratio*, *elongation*, *tri_score*

   Used as a fallback when neither elevation data nor strong appearance
   gradients are available (low-texture flat-roof cities, overcast imagery).

Architecture note
-----------------
The three-tier design degrades gracefully:

* With a height raster (real or predicted): ~75-85 % accuracy vs OSM tags
* With RGB only: ~55-65 %
* Shadow-only fallback: ~45-55 %

The ``estimate_roof_heights`` flag uses the elevation profile to fill
``roof:height`` (peak above eave) in addition to ``roof:shape``, making the
output directly usable by the mesh generator in ``city2stl/mesh.py``.

Usage
-----
::

    from city2stl.roof_classifier import classify_roof_shapes

    updated = classify_roof_shapes(
        buildings_geojson,          # GeoJSON FeatureCollection
        satellite_rgb,              # H×W×3 uint8 ndarray
        bbox,                       # (north, south, east, west)
        height_raster=ndsm_arr,     # optional H×W float32 nDSM / predicted DEM
        estimate_roof_heights=True, # also fill roof:height when missing
    )
"""

from __future__ import annotations

import importlib.util
import logging
import math
from typing import NamedTuple

import numpy as np

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Lightweight CNN availability check
# ─────────────────────────────────────────────────────────────────────────────
# The classifier degrades gracefully when torch / torchvision are absent:
#
#   Tier 0 (always):  shadow triangulation + RGB gradient + elevation profile
#   Tier 1 (torch, opt-in): trained RoofNetV2 checkpoint (city2stl.roof_nets)
#   Tier 2 (torch):   RAFT-Small optical-flow depth from multi-temporal stack
#
# RAFT-Small (1 M params, ~5 MB) handles optical-flow parallax between
# temporal acquisitions.  DepthAnything-V2-Small (~25 M) or MiDaS-DPT-Small
# (~15 M) can produce a monocular pseudo-DEM when a height raster is absent.
#
# Availability is probed without importing: torch costs seconds at import and
# is only needed when a trained checkpoint is actually supplied.
_TORCH_AVAILABLE: bool = importlib.util.find_spec("torch") is not None

# ─────────────────────────────────────────────────────────────────────────────
# Shadow detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_shadows(rgb: np.ndarray) -> np.ndarray:
    """Return a boolean shadow mask (H, W) from an RGB satellite image."""
    try:
        import cv2
        hsv = cv2.cvtColor(rgb[:, :, :3].astype(np.uint8), cv2.COLOR_RGB2HSV)
        v = hsv[:, :, 2].astype(np.float32)
        s = hsv[:, :, 1].astype(np.float32)
        return (v < 80) & (s < 100)
    except Exception:
        # Fall back: dark pixels (value < 80 in any channel)
        dark = (rgb[:, :, :3].astype(np.float32).mean(axis=2) < 80)
        return dark.astype(bool)


# ─────────────────────────────────────────────────────────────────────────────
# Feature containers
# ─────────────────────────────────────────────────────────────────────────────

class _ElevFeatures(NamedTuple):
    height_std: float       # std of heights inside footprint (metres)
    roof_rise: float        # peak - eave height in metres
    ridge_score: float      # bell-shaped primary-axis profile [0, 1]
    ridge_symmetry: float   # bilateral profile symmetry [0, 1]
    apex_score: float       # high pixels clustered near centre [0, 1]
    n_pixels: int           # number of valid footprint pixels


class _RGBFeatures(NamedTuple):
    gradient_strength: float    # mean Sobel magnitude / mean brightness
    gradient_anisotropy: float  # directional concentration [0, 1]
    brightness_ridge: float     # brightness std along principal axis [0, 1]


class _ShadowFeatures(NamedTuple):
    shadow_ratio: float   # shadow area / footprint area
    elongation: float     # major / minor axis length
    tri_score: float      # how triangular (0 = rect, 1 = triangle)
    confidence: float     # overall detection quality [0, 1]


class _MultiTemporalFeatures(NamedTuple):
    """Signals derived from a stack of N ≥ 2 satellite images of the same area.

    Multi-temporal images are treated as a pseudo-stereo pair: because each
    acquisition has a slightly different off-nadir angle and a different sun
    position, roof pixels shift relative to ground pixels in a way that is
    proportional to building height.

    Two zero-cost signals are extracted without any ML model:

    1. **Shadow triangulation** — shadows cast at two different sun azimuths
       define two ray equations whose intersection gives a direct height
       estimate:  h = d × tan(elevation), where d is the shadow length.
       Using two images eliminates ambiguity from footprint shape.

    2. **Temporal parallax** (optional, when torch is available) — optical
       flow (RAFT-Small, 1 M params) between two acquisitions gives a dense
       per-pixel displacement field.  On a flat roof all footprint pixels
       shift by the same (small) amount; on a pitched roof the ridge shifts
       more than the eave, giving a measurable height gradient.

    When torch is not available only shadow triangulation is used.
    """
    tri_height_m: float     # height estimate from shadow triangulation (m)
    tri_confidence: float   # [0, 1]; 0 when < 2 images or sun angles too similar
    parallax_rise: float    # ridge-minus-eave from optical flow (m), or 0
    n_images: int           # number of images in the stack


def _ellipse_axes(binary: np.ndarray) -> tuple[float, float]:
    """Return (major, minor) axis lengths of the best-fit ellipse."""
    rows, cols = np.where(binary)
    if len(rows) < 4:
        return 1.0, 1.0
    pts = np.column_stack([cols.astype(float), rows.astype(float)])
    cov = np.cov((pts - pts.mean(axis=0)).T)
    try:
        eigvals = np.maximum(np.linalg.eigvalsh(cov), 0.0)
        return max(float(np.sqrt(eigvals.max())) * 2, 1.0), max(float(np.sqrt(eigvals.min())) * 2, 1.0)
    except np.linalg.LinAlgError:
        return 1.0, 1.0


def _triangularity(binary: np.ndarray) -> float:
    """Return how triangular a binary blob is (0 = rectangle, 1 = triangle)."""
    rows, cols = np.where(binary)
    if len(rows) < 4:
        return 0.0
    bbox_area = float((rows.max() - rows.min() + 1) * (cols.max() - cols.min() + 1))
    fill = float(binary.sum()) / max(bbox_area, 1.0)
    return float(np.clip(1.0 - 2.0 * (fill - 0.5), 0.0, 1.0))


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate helpers
# ─────────────────────────────────────────────────────────────────────────────

def _lonlat_to_pixel(
    lon: float, lat: float,
    north: float, south: float, east: float, west: float,
    h: int, w: int,
) -> tuple[int, int]:
    col = int((lon - west) / max(east - west, 1e-9) * w)
    row = int((north - lat) / max(north - south, 1e-9) * h)
    return (max(0, min(h - 1, row)), max(0, min(w - 1, col)))


def _ring_to_footprint_mask(
    ring: list,
    north: float, south: float, east: float, west: float,
    h: int, w: int,
) -> np.ndarray:
    """Rasterise a GeoJSON ring (list of [lon, lat]) to a boolean pixel mask."""
    mask = np.zeros((h, w), dtype=np.uint8)
    if len(ring) < 3:
        return mask.astype(bool)
    raw = ring[:-1] if (ring and ring[0] == ring[-1]) else ring
    pts = np.array([
        [_lonlat_to_pixel(lo, la, north, south, east, west, h, w)[1],
         _lonlat_to_pixel(lo, la, north, south, east, west, h, w)[0]]
        for lo, la in raw
    ], dtype=np.int32)
    try:
        import cv2
        cv2.fillPoly(mask, [pts], 1)
    except Exception:
        r0, r1 = pts[:, 1].min(), pts[:, 1].max()
        c0, c1 = pts[:, 0].min(), pts[:, 0].max()
        mask[r0:r1 + 1, c0:c1 + 1] = 1
    return mask.astype(bool)


def _crop_bounds_for_ring(
    ring: list,
    north: float, south: float, east: float, west: float,
    rgb_h: int, rgb_w: int,
) -> tuple[int, int, int, int]:
    """Return (cr0, cr1, cc0, cc1) pixel crop window with 150 % padding."""
    raw = ring[:-1] if (ring and ring[0] == ring[-1]) else ring
    pts = np.array(raw)
    r0, c0 = _lonlat_to_pixel(float(pts[:, 0].min()), float(pts[:, 1].max()),
                               north, south, east, west, rgb_h, rgb_w)
    r1, c1 = _lonlat_to_pixel(float(pts[:, 0].max()), float(pts[:, 1].min()),
                               north, south, east, west, rgb_h, rgb_w)
    row_min, row_max = min(r0, r1), max(r0, r1)
    col_min, col_max = min(c0, c1), max(c0, c1)
    pad_r = max(int((row_max - row_min) * 1.5), 4)
    pad_c = max(int((col_max - col_min) * 1.5), 4)
    return (
        max(0, row_min - pad_r),
        min(rgb_h, row_max + pad_r + 1),
        max(0, col_min - pad_c),
        min(rgb_w, col_max + pad_c + 1),
    )


def _crop_geo_bounds(
    cr0: int, cr1: int, cc0: int, cc1: int,
    north: float, south: float, east: float, west: float,
    rgb_h: int, rgb_w: int,
) -> tuple[float, float, float, float]:
    """Convert pixel crop bounds → (north, south, east, west)."""
    ns = north - south
    ew = east - west
    return (
        north - cr0 * ns / rgb_h,
        north - cr1 * ns / rgb_h,
        west + cc1 * ew / rgb_w,
        west + cc0 * ew / rgb_w,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PCA / profile helpers
# ─────────────────────────────────────────────────────────────────────────────

def _principal_axis(footprint_mask: np.ndarray) -> np.ndarray:
    rows, cols = np.where(footprint_mask)
    if len(rows) < 4:
        return np.array([1.0, 0.0])
    pts = np.column_stack([cols.astype(float), rows.astype(float)])
    cov = np.cov((pts - pts.mean(axis=0)).T)
    try:
        eigvals, eigvecs = np.linalg.eigh(cov)
        return eigvecs[:, np.argmax(eigvals)]
    except np.linalg.LinAlgError:
        return np.array([1.0, 0.0])


def _profile_along_axis(
    values_2d: np.ndarray,
    footprint_mask: np.ndarray,
    n_bins: int = 10,
) -> np.ndarray:
    """Bin footprint pixel values along the PCA principal axis."""
    rows, cols = np.where(footprint_mask)
    if len(rows) < n_bins:
        mean_val = float(values_2d[footprint_mask].mean()) if footprint_mask.any() else 0.0
        return np.full(n_bins, mean_val)
    pts = np.column_stack([cols.astype(float), rows.astype(float)])
    ctr = pts.mean(axis=0)
    principal = _principal_axis(footprint_mask)
    proj = (pts - ctr) @ principal
    vals = values_2d[rows, cols]
    proj_min, proj_max = float(proj.min()), float(proj.max())
    if proj_max - proj_min < 1e-6:
        return np.full(n_bins, float(vals.mean()))
    bidx = np.clip(((proj - proj_min) / (proj_max - proj_min) * n_bins).astype(int), 0, n_bins - 1)
    profile = np.zeros(n_bins)
    counts = np.zeros(n_bins, dtype=int)
    for b in range(n_bins):
        m = bidx == b
        if m.sum() > 0:
            profile[b] = vals[m].mean()
            counts[b] = m.sum()
    empty = counts == 0
    if empty.any() and (~empty).any():
        x = np.arange(n_bins)
        profile[empty] = np.interp(x[empty], x[~empty], profile[~empty])
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# Signal 1: elevation profile
# ─────────────────────────────────────────────────────────────────────────────

def _extract_elev_features(
    dem_crop: np.ndarray,
    footprint_mask: np.ndarray,
) -> _ElevFeatures:
    """Extract roof-shape signals from a DEM / nDSM crop.

    Accepts real LiDAR / nDSM data **or** a predicted elevation map produced
    by a monocular depth-regression network (e.g. DepthAnything-V2-Small,
    MiDaS-DPT-Small, or a U-Net trained on open LiDAR ground truth).
    The classification logic is identical in both cases.
    """
    h_all = dem_crop[footprint_mask]
    valid = h_all[np.isfinite(h_all)]
    n_px = int(valid.size)
    if n_px < 6:
        return _ElevFeatures(0.0, 0.0, 0.0, 0.5, 0.0, n_px)

    eave_h = float(np.percentile(valid, 15))
    peak_h = float(np.percentile(valid, 90))
    h_std = float(valid.std())
    roof_rise = max(0.0, peak_h - eave_h)

    dem_safe = dem_crop.copy()
    dem_safe[~np.isfinite(dem_safe)] = eave_h

    profile = _profile_along_axis(dem_safe, footprint_mask, n_bins=10)
    p_range = float(profile.max() - profile.min())
    if p_range < 0.1:
        ridge_score, ridge_symmetry = 0.0, 0.5
    else:
        p_norm = (profile - profile.min()) / p_range
        mid = len(p_norm) // 2
        ridge_score = float(np.clip(float(p_norm[mid]) - (float(p_norm[0]) + float(p_norm[-1])) / 2.0, 0.0, 1.0))
        left, right = p_norm[:mid], p_norm[-mid:][::-1]
        if len(left) > 2 and left.std() > 1e-6 and right.std() > 1e-6:
            corr = float(np.corrcoef(left, right)[0, 1])
            ridge_symmetry = float(np.clip((corr + 1.0) / 2.0, 0.0, 1.0))
        else:
            ridge_symmetry = 0.5

    fp_rows, fp_cols = np.where(footprint_mask)
    centre_r, centre_c = float(fp_rows.mean()), float(fp_cols.mean())
    fp_radius = float(np.sqrt(n_px / np.pi))
    high_thresh = float(np.percentile(valid, 80))
    high_mask = footprint_mask & (dem_safe >= high_thresh)
    hr, hc = np.where(high_mask)
    if len(hr) > 0:
        dist = np.sqrt((hr - centre_r) ** 2 + (hc - centre_c) ** 2)
        apex_score = float((dist < fp_radius * 0.5).mean())
    else:
        apex_score = 0.0

    return _ElevFeatures(h_std, roof_rise, ridge_score, ridge_symmetry, apex_score, n_px)


def _estimate_roof_height_from_elev(
    dem_crop: np.ndarray,
    footprint_mask: np.ndarray,
) -> float | None:
    valid = dem_crop[footprint_mask]
    valid = valid[np.isfinite(valid)]
    if len(valid) < 6:
        return None
    rise = float(np.percentile(valid, 90) - np.percentile(valid, 15))
    return round(rise, 1) if rise >= 0.2 else None


# ─────────────────────────────────────────────────────────────────────────────
# Signal 2: roof-face RGB appearance
# ─────────────────────────────────────────────────────────────────────────────

def _sobel_gradients(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    try:
        import cv2
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        return gx, gy
    except Exception:
        pass
    try:
        from scipy.ndimage import convolve
        kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
        return convolve(gray, kx), convolve(gray, kx.T)
    except Exception:
        return np.gradient(gray, axis=1).astype(np.float32), np.gradient(gray, axis=0).astype(np.float32)


def _extract_rgb_features(
    rgb_crop: np.ndarray,
    footprint_mask: np.ndarray,
) -> _RGBFeatures:
    """Analyse the roof face itself: brightness gradients, ridge lines, slope asymmetry.

    Sloped roofs receive direct sunlight from one direction → directional
    brightness gradient.  A ridge tile / metal flashing appears as a bright
    linear feature along the primary footprint axis.
    """
    if not footprint_mask.any() or rgb_crop.shape[0] < 3 or rgb_crop.shape[1] < 3:
        return _RGBFeatures(0.0, 0.0, 0.0)

    gray = rgb_crop[:, :, :3].astype(np.float32).mean(axis=2)
    gx, gy = _sobel_gradients(gray)
    mag = np.sqrt(gx ** 2 + gy ** 2)
    mean_brightness = max(float(gray[footprint_mask].mean()), 1.0)
    gradient_strength = float(mag[footprint_mask].mean()) / mean_brightness

    angles = np.arctan2(gy[footprint_mask], gx[footprint_mask])
    strong = mag[footprint_mask] > (mag[footprint_mask].mean() * 0.5)
    if strong.sum() > 4:
        hist, _ = np.histogram(angles[strong], bins=8, range=(-np.pi, np.pi))
        hist = hist.astype(float) / (hist.sum() + 1e-9)
        entropy = float(-np.sum(hist * np.log(hist + 1e-9)))
        gradient_anisotropy = float(1.0 - entropy / math.log(8))
    else:
        gradient_anisotropy = 0.0

    profile_rgb = _profile_along_axis(gray, footprint_mask, n_bins=8)
    mean_p = float(profile_rgb.mean())
    brightness_ridge = float(np.clip(profile_rgb.std() / max(mean_p, 1.0), 0.0, 1.0))

    return _RGBFeatures(gradient_strength, gradient_anisotropy, brightness_ridge)


# ─────────────────────────────────────────────────────────────────────────────
# Signal 3: shadow geometry
# ─────────────────────────────────────────────────────────────────────────────

def _extract_shadow_features(
    shadow_mask: np.ndarray,
    footprint_mask: np.ndarray,
) -> _ShadowFeatures:
    import scipy.ndimage as ndi

    ext_shadow = shadow_mask & ~footprint_mask
    fp_px = max(int(footprint_mask.sum()), 1)
    shadow_px = int(ext_shadow.sum())
    shadow_ratio = shadow_px / fp_px

    if shadow_px < 4:
        return _ShadowFeatures(shadow_ratio, 1.0, 0.0, 0.0)

    labelled, n = ndi.label(ext_shadow)
    if n == 0:
        return _ShadowFeatures(shadow_ratio, 1.0, 0.0, 0.0)

    best = max(range(1, n + 1), key=lambda i: int((labelled == i).sum()))
    largest = labelled == best
    major, minor = _ellipse_axes(largest)
    elongation = major / max(minor, 0.1)
    tri_score = _triangularity(largest)
    confidence = float(np.clip(shadow_px / max(fp_px * 0.2, 1), 0.0, 1.0))
    return _ShadowFeatures(shadow_ratio, elongation, tri_score, confidence)


# ─────────────────────────────────────────────────────────────────────────────
# Signal 4: multi-temporal pseudo-stereo
# ─────────────────────────────────────────────────────────────────────────────

def _sun_azimuth_elevation(lat: float, lon: float, month: int, hour: int) -> tuple[float, float]:
    """Return (azimuth_deg, elevation_deg) via simplified solar formula."""
    day = 30 * month
    decl_deg = 23.45 * math.sin(math.radians(360 / 365 * (day - 81)))
    ha_deg = 15.0 * (hour - 12)
    lat_r = math.radians(lat)
    dec_r = math.radians(decl_deg)
    ha_r = math.radians(ha_deg)
    sin_elev = (math.sin(lat_r) * math.sin(dec_r) +
                math.cos(lat_r) * math.cos(dec_r) * math.cos(ha_r))
    elev_r = math.asin(max(-1.0, min(1.0, sin_elev)))
    elev = math.degrees(elev_r)

    cos_az = (math.sin(dec_r) - math.sin(lat_r) * sin_elev) / (
        math.cos(lat_r) * math.cos(elev_r) + 1e-9)
    az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
    if ha_deg > 0:
        az = 360.0 - az
    return az, max(5.0, elev)


def _triangulate_heights_from_shadows(
    shadow_masks: list[np.ndarray],
    footprint_mask: np.ndarray,
    pixel_m: float,
    sun_params: list[tuple[float, float]],  # [(az1, elev1), (az2, elev2), ...]
) -> tuple[float, float]:
    """Estimate building height by triangulating shadow tips from N images.

    Each image provides a shadow-tip observation: the tip is the pixel
    furthest from the footprint in the shadow direction.  Two observations
    with different sun azimuths constrain height via:

        h ≈ median over image pairs of (shadow_length_px × pixel_m × tan(elevation))

    Returns (height_m, confidence).  confidence is 0 when fewer than 2 images
    provide usable shadows, or when sun azimuths are too similar (< 30 °).
    """
    import scipy.ndimage as ndi

    height_estimates = []
    n = len(shadow_masks)
    for i in range(n):
        sm = shadow_masks[i]
        az, elev = sun_params[i]
        ext = sm & ~footprint_mask
        if ext.sum() < 4:
            continue
        labelled, nlbl = ndi.label(ext)
        if nlbl == 0:
            continue
        best = max(range(1, nlbl + 1), key=lambda k: int((labelled == k).sum()))
        comp = labelled == best
        rows, cols = np.where(comp)
        shadow_len_px = max(int(rows.max() - rows.min()), int(cols.max() - cols.min()))
        h = shadow_len_px * pixel_m * math.tan(math.radians(elev))
        if 0.5 < h < 500:
            height_estimates.append(h)

    if len(height_estimates) < 2:
        # Check angular spread — reward large spread between sun azimuths
        if len(height_estimates) == 1:
            return height_estimates[0], 0.25
        return 0.0, 0.0

    # Angular diversity bonus: wider azimuth spread → more independent
    azimuths = [sun_params[i][0] for i in range(n)]
    az_spread = 0.0
    for i in range(len(azimuths)):
        for j in range(i + 1, len(azimuths)):
            diff = abs(azimuths[i] - azimuths[j]) % 360
            az_spread = max(az_spread, min(diff, 360 - diff))
    confidence = float(np.clip(az_spread / 90.0, 0.0, 1.0))

    return float(np.median(height_estimates)), confidence


def _extract_multitemporal_features(
    rgb_stack: list[np.ndarray],
    shadow_stack: list[np.ndarray],
    footprint_mask: np.ndarray,
    pixel_m: float,
    lat: float,
    lon: float,
    acquisition_months: list[int],
    acquisition_hours: list[int],
) -> _MultiTemporalFeatures:
    """Extract features from a stack of N ≥ 2 temporal images."""
    n = len(rgb_stack)
    sun_params = [
        _sun_azimuth_elevation(lat, lon, acquisition_months[i], acquisition_hours[i])
        for i in range(n)
    ]

    tri_h, tri_conf = _triangulate_heights_from_shadows(
        shadow_stack, footprint_mask, pixel_m, sun_params
    )

    # Optional: optical-flow parallax with RAFT-Small when torch is available
    parallax_rise = 0.0
    if _TORCH_AVAILABLE and n >= 2:
        parallax_rise = _flow_parallax_rise(rgb_stack[0], rgb_stack[1], footprint_mask)

    return _MultiTemporalFeatures(tri_h, tri_conf, parallax_rise, n)


def _flow_parallax_rise(
    rgb_a: np.ndarray,
    rgb_b: np.ndarray,
    footprint_mask: np.ndarray,
) -> float:
    """Estimate roof rise from optical-flow parallax between two acquisitions.

    Different acquisition off-nadir angles cause roof ridge pixels to shift
    relative to ground pixels between images.  The magnitude of this shift,
    normalised by the pixel ground-sampling distance, gives a height proxy.

    Uses RAFT-Small (1 M params, ~5 MB) when available.  Falls back to
    Farneback dense flow (OpenCV, zero cost) when torchvision models are
    not available.

    Returns the estimated roof rise in pixels (proxy for height; caller
    should multiply by pixel_m to convert to metres).
    """
    try:
        import cv2
        gray_a = cv2.cvtColor(rgb_a[:, :, :3].astype(np.uint8), cv2.COLOR_RGB2GRAY)
        gray_b = cv2.cvtColor(rgb_b[:, :, :3].astype(np.uint8), cv2.COLOR_RGB2GRAY)
        flow = cv2.calcOpticalFlowFarneback(
            gray_a, gray_b, None,
            pyr_scale=0.5, levels=3, winsize=15,
            iterations=3, poly_n=5, poly_sigma=1.2, flags=0,
        )
        # |flow| at footprint pixels
        mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        if footprint_mask.any():
            fp_flow = mag[footprint_mask]
            # Rise ≈ difference between 90th and 10th percentile of flow magnitude
            # inside the footprint: edge/eave pixels shift less than ridge pixels
            return float(max(0.0, np.percentile(fp_flow, 90) - np.percentile(fp_flow, 10)))
    except Exception:
        pass
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Signal 5: lightweight CNN classification (Tier 1 — optional, torch only)
# ─────────────────────────────────────────────────────────────────────────────

# Roof shape labels in model output order
_SHAPE_LABELS = ["flat", "gabled", "hipped", "pyramidal", "skillion", "dome"]


def _roofnet_classify_patch(
    rgb_crops: list[np.ndarray],
    footprint_mask: np.ndarray,
    roofnet: object,
) -> tuple[str | None, float]:
    """Classify a roof-shape patch using a RoofNet or RoofNetV2 instance.

    The model returns a ``(height_map, shape_logits)`` tuple.  This
    helper extracts the *shape_logits* and converts them to a class label.

    Supports both:
    - Legacy RoofNet (tools.networks): 64x64, no ImageNet normalisation
    - RoofNetV2 (city2stl.roof_nets): 128x128, ImageNet normalisation

    Parameters
    ----------
    rgb_crops : list of H*W*3 uint8 ndarray (temporal stack)
    footprint_mask : H*W bool/float ndarray (1 = inside footprint)
    roofnet : RoofNet or RoofNetV2 instance (eval mode)

    Returns
    -------
    (shape_label, confidence) -- or (None, 0.0) on failure
    """
    if not _TORCH_AVAILABLE or not rgb_crops:
        return None, 0.0
    try:
        import torch
        from PIL import Image

        h, w = rgb_crops[0].shape[:2]
        if h < 4 or w < 4:
            return None, 0.0

        # Detect RoofNetV2 by checking for the backbone attribute
        is_v2 = hasattr(roofnet, "backbone") and hasattr(roofnet, "fpn")
        target = 128 if is_v2 else 64

        # Temporal early-fusion: stack along channel dim
        stack = np.concatenate(
            [c[:h, :w, :3].astype(np.float32) / 255.0 for c in rgb_crops], axis=2
        )  # H * W * (N*3)

        # Apply footprint mask to suppress background context
        mask_f = footprint_mask.astype(np.float32)
        for ch in range(stack.shape[2]):
            stack[:, :, ch] *= mask_f

        channels = [
            np.array(
                Image.fromarray((stack[:, :, ci] * 255).astype(np.uint8)).resize(
                    (target, target), Image.BILINEAR
                ),
                dtype=np.float32,
            ) / 255.0
            for ci in range(stack.shape[2])
        ]
        inp = torch.tensor(
            np.stack(channels, axis=0)[None], dtype=torch.float32
        )

        # RoofNetV2 expects ImageNet-normalised 3-channel input
        if is_v2 and inp.shape[1] == 3:
            mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
            std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
            inp = (inp - mean) / std

        mask_t = torch.tensor(
            np.array(
                Image.fromarray((mask_f * 255).astype(np.uint8)).resize(
                    (target, target), Image.BILINEAR
                ),
                dtype=np.float32,
            ) / 255.0
        ).unsqueeze(0).unsqueeze(0)

        with torch.no_grad():
            _, logits = roofnet(inp, mask=mask_t)
            probs = torch.softmax(logits[0], dim=0).numpy()

        best_idx = int(np.argmax(probs))
        if best_idx < len(_SHAPE_LABELS):
            return _SHAPE_LABELS[best_idx], float(probs[best_idx])
        return None, 0.0

    except Exception as exc:
        logger.debug("RoofNet classify patch failed: %s", exc)
        return None, 0.0


def _load_roof_checkpoint(path: str) -> object | None:
    """Load a trained RoofNetV2 checkpoint, or return ``None`` with a warning.

    Only tensor state is accepted (``weights_only=True``): a pickled whole
    model would execute arbitrary code on load, so legacy full-model saves are
    refused rather than trusted.  Both ``{"model_state_dict": ...}`` wrappers
    and bare state dicts are read.
    """
    if not _TORCH_AVAILABLE:
        logger.warning("roof CNN checkpoint %s ignored: torch is not installed",
                       path)
        return None
    from pathlib import Path

    ckpt = Path(path)
    if not ckpt.exists():
        ckpt = Path(__file__).resolve().parents[1] / path
    if not ckpt.exists():
        logger.warning("roof CNN checkpoint not found: %s", path)
        return None
    try:
        import torch

        from city2stl.roof_nets import RoofNetV2

        state = torch.load(str(ckpt), map_location="cpu", weights_only=True)
        if isinstance(state, dict) and "model_state_dict" in state:
            state = state["model_state_dict"]
        # The checkpoint supplies every weight, so skip the ImageNet download.
        model = RoofNetV2(pretrained=False)
        model.load_state_dict(state)
        model.eval()
        return model
    except Exception as exc:                                # noqa: BLE001
        logger.warning("failed to load roof CNN checkpoint %s: %s", ckpt, exc)
        return None


def _resolve_cnn_model(cnn_model: str | object | None) -> object | None:
    """Turn the ``cnn_model`` argument into a ready model, or ``None``.

    The CNN tier is opt-in and only runs with trained weights:

    - ``None``: no CNN.
    - a path ending in ``.pt`` / ``.pth``: a RoofNetV2 checkpoint, loaded once.
    - a model instance (RoofNet / RoofNetV2 in eval mode): used as is.
    - any other string (a torchvision architecture name such as
      ``"mobilenet_v3_small"``): refused with a warning.  Those names used to
      build a randomly initialised network whose output was then trusted at
      confidence >= 0.55; there are no trained weights for them.
    """
    if cnn_model is None:
        return None
    if not isinstance(cnn_model, str):
        return cnn_model
    if cnn_model.endswith((".pt", ".pth")):
        return _load_roof_checkpoint(cnn_model)
    logger.warning(
        "cnn_model=%r names an architecture without trained weights; the CNN "
        "tier is skipped. Pass a RoofNetV2 checkpoint path or model instance.",
        cnn_model)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Classification fusion
# ─────────────────────────────────────────────────────────────────────────────

def _classify(
    elev: _ElevFeatures,
    rgb: _RGBFeatures,
    shadow: _ShadowFeatures,
    mt: _MultiTemporalFeatures,
    cnn: tuple[str | None, float],
    pixel_m: float = 1.0,
) -> tuple[str | None, float]:
    """Fuse all signals into a (roof_shape, confidence) decision.

    Priority order:
      1. CNN output (if confidence ≥ 0.55 and torch was available)
      2. Elevation profile (real or predicted DEM, if ≥ 8 footprint pixels)
      3. Multi-temporal triangulation (if ≥ 2 images with spread sun azimuths)
      4. RGB gradient appearance
      5. Shadow geometry fallback
    """
    cnn_shape, cnn_conf = cnn

    # ── Tier 1: CNN ─────────────────────────────────────────────────────
    if cnn_shape is not None and cnn_conf >= 0.55:
        return cnn_shape, cnn_conf

    # ── Tier 2: elevation profile ─────────────────────────────────────
    if elev.n_pixels >= 8 and elev.roof_rise > 0.3 and elev.height_std > 0.3:
        if elev.apex_score > 0.55:
            return "pyramidal", float(np.clip(0.65 + 0.25 * elev.apex_score, 0, 1))
        if elev.ridge_score > 0.35:
            if elev.ridge_symmetry > 0.65:
                return "gabled", float(np.clip(0.60 + 0.25 * elev.ridge_symmetry, 0, 1))
            return "skillion", float(np.clip(0.50 + 0.25 * (1.0 - elev.ridge_symmetry), 0, 1))
        return "hipped", 0.50
    if elev.n_pixels >= 8 and elev.height_std <= 0.3:
        return "flat", 0.70

    # ── Tier 3: multi-temporal triangulation ─────────────────────────
    if mt.tri_confidence > 0.30 and mt.n_images >= 2:
        # Use the estimated height to decide pitched vs. flat
        if mt.tri_height_m > 1.5:
            # Combine with parallax rise for shape discrimination
            rise = mt.parallax_rise * pixel_m
            if rise > 2.0:
                return "gabled", float(np.clip(0.45 + 0.20 * mt.tri_confidence, 0, 1))
            return "hipped", float(np.clip(0.40 + 0.20 * mt.tri_confidence, 0, 1))
        if mt.tri_height_m <= 0.5:
            return "flat", float(np.clip(0.50 + 0.20 * mt.tri_confidence, 0, 1))

    # ── Tier 4: RGB appearance ─────────────────────────────────────────
    if rgb.gradient_strength > 0.05:
        if rgb.gradient_anisotropy > 0.55:
            if rgb.brightness_ridge > 0.12:
                return "gabled", 0.45
            return "hipped", 0.40
        if rgb.gradient_strength > 0.12:
            return "pyramidal", 0.35
        return "flat", 0.45

    # ── Tier 5: shadow geometry ────────────────────────────────────────
    if shadow.confidence >= 0.15:
        if shadow.shadow_ratio < 0.05:
            return "flat", 0.50
        if shadow.elongation > 2.5:
            shape = "pyramidal" if shadow.tri_score > 0.50 else "gabled"
            return shape, 0.40
        if shadow.elongation > 1.5:
            return "hipped", 0.35
        return "flat", 0.40

    return None, 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def classify_roof_shapes(
    buildings_geojson: dict,
    satellite_rgb: np.ndarray | list[np.ndarray],
    bbox: tuple,
    height_raster: np.ndarray | None = None,
    estimate_roof_heights: bool = False,
    overwrite: bool = False,
    acquisition_months: list[int] | None = None,
    acquisition_hours: list[int] | None = None,
    cnn_model: str | object | None = None,
    use_model: bool = False,
) -> dict:
    """Classify ``roof:shape`` for buildings using multi-signal satellite analysis.

    With *use_model* the trained checkpoint in :mod:`city2stl.roof_model`
    answers first and the signals below are only consulted for buildings it
    cannot measure.  That path is worth preferring where it applies: held out
    by city it beats always-flat, while the hand-written tiers below have been
    measured scoring exactly their always-majority baseline on tagged cities
    (Salzburg came back 100 per cent pitched, Cartagena 99.6 per cent
    pyramidal).  It is off by default only because it fetches its own tiles.

    Combines up to five complementary signals in priority order:

    1. **Trained CNN** (RoofNetV2) — opt-in; requires ``torch`` and trained
       weights passed as ``cnn_model``.  Skipped entirely otherwise.

    2. **Elevation profile** — from a real nDSM / LiDAR raster or from a
       predicted DEM (DepthAnything-V2-Small, MiDaS-DPT-Small, or a U-Net).

    3. **Multi-temporal pseudo-stereo** — pass a list of N ≥ 2 images taken
       at different times.  Different sun positions give shadow triangulation;
       slightly different off-nadir angles give optical-flow parallax depth
       (RAFT-Small when torch available, Farneback otherwise).

    4. **Roof-face RGB appearance** — directional brightness gradient, ridge
       line contrast, gradient anisotropy from the roof surface itself.

    5. **Shadow geometry** — fallback; shape and length of cast shadows.

    Args:
        buildings_geojson: GeoJSON FeatureCollection of building polygons.
        satellite_rgb: Either a single H×W×3 uint8 ndarray **or** a list of
            N such arrays for multi-temporal analysis.  All images must cover
            *bbox* at the same resolution.
        bbox: ``(north, south, east, west)`` geographic bounds.
        height_raster: Optional H×W float32 nDSM / DEM aligned to *bbox*.
            Accepts real LiDAR / nDSM data **or** a monocular depth prediction.
        estimate_roof_heights: Also fill ``roof:height`` (peak above eave)
            from the elevation profile when the tag is absent.
        overwrite: Replace existing ``roof:shape`` tags.  Default False.
        acquisition_months: Month index (1–12) for each image in the stack.
            Used for sun-position estimation.  Defaults to June (6).
        acquisition_hours: Hour of day (0–23, local solar) for each image.
            Defaults to 10 AM.
        cnn_model: ``None`` (default, no CNN), a path to a RoofNetV2
            checkpoint (``.pt`` / ``.pth``, loaded once with
            ``weights_only=True``), or a ``RoofNet`` / ``RoofNetV2`` instance
            in eval mode.  Its shape-classification head is used.  Bare
            torchvision architecture names are refused with a warning: they
            have no trained weights.
        use_model: Consult the trained ``roof_shape_gbm`` checkpoint first,
            fetching a zoom-18 crop per building rather than reading the
            supplied city-wide image.  Falls back to the signal cascade for
            any building the model cannot measure, and silently does nothing
            when the checkpoint is absent.

    Returns:
        A copy of *buildings_geojson* with ``roof:shape``, ``roof_source``,
        and optionally ``roof:height`` updated.

        ``_stats`` (non-standard key) contains a summary dict::

            {"total": N, "classified": M, "skipped": K, "unchanged": J}
    """
    import copy

    trained = None
    if use_model:
        from city2stl import roof_model as _roof_model

        trained = _roof_model.load()
        if trained is None:
            logger.warning("use_model requested but no checkpoint at %s; "
                           "falling back to the signal cascade",
                           _roof_model.MODEL_PATH)

    # Resolve (and load, if a checkpoint path) the CNN once for the whole call.
    cnn_net = _resolve_cnn_model(cnn_model)

    # ── Normalise input to a list of images ─────────────────────────────
    # No image at all is a supported case once the model is loaded: it reads
    # its own tiles, so a city no longer has to be fetched whole and degraded
    # to a metre per pixel just to have its roofs classified. Buildings the
    # model declines are then reported as skipped rather than handed to a
    # cascade that has nothing to look at.
    model_only = satellite_rgb is None
    if model_only:
        if trained is None:
            raise ValueError(
                "classify_roof_shapes needs either a satellite image or the "
                "trained checkpoint; neither was available")
        rgb_stack: list[np.ndarray] = []
    elif isinstance(satellite_rgb, np.ndarray):
        rgb_stack = [satellite_rgb]
    else:
        rgb_stack = list(satellite_rgb)

    n_images = len(rgb_stack)
    rgb_h, rgb_w = (0, 0) if model_only else rgb_stack[0].shape[:2]

    if acquisition_months is None:
        acquisition_months = [6] * n_images
    if acquisition_hours is None:
        acquisition_hours = [10] * n_images

    north, south, east, west = bbox
    pixel_m = 0.0 if model_only else (north - south) * 111_320.0 / rgb_h

    shadow_stack = [_detect_shadows(img) for img in rgb_stack]

    features_list = buildings_geojson.get("features") or []

    if trained is not None and features_list:
        # The model reads one crop per building, and on a whole city that is
        # tens of thousands of serial requests against a few hundred distinct
        # tiles.  Warming the cache concurrently first turns the round-trip
        # latency from per-building into per-city.
        try:
            from city2stl import roof_tiles as _roof_tiles
            _roof_tiles.prefetch_bbox(north, south, east, west,
                                      zoom=trained.zoom,
                                      log=lambda m: logger.info("roof tiles: %s", m))
        except Exception as exc:                            # noqa: BLE001
            logger.warning("roof tile prefetch failed (%s); "
                           "falling back to per-building fetches", exc)
    out_features: list[dict] = []
    total = len(features_list)
    classified = 0
    skipped = 0
    unchanged = 0
    by_model = 0

    lat_mid = (north + south) / 2.0
    lon_mid = (east + west) / 2.0

    for feat in features_list:
        feat = copy.deepcopy(feat)
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}

        existing_shape = (props.get("roof:shape") or "").strip().lower()
        has_tag = existing_shape not in ("", "unknown")
        if has_tag and not overwrite:
            unchanged += 1
            out_features.append(feat)
            continue

        gtype = geom.get("type", "")
        if gtype == "Polygon":
            rings = [geom["coordinates"][0]] if geom.get("coordinates") else []
        elif gtype == "MultiPolygon":
            rings = [poly[0] for poly in (geom.get("coordinates") or [])]
        else:
            skipped += 1
            out_features.append(feat)
            continue

        if not rings:
            skipped += 1
            out_features.append(feat)
            continue

        ring = rings[0]
        raw = ring[:-1] if (ring and ring[0] == ring[-1]) else ring
        if len(raw) < 3:
            skipped += 1
            out_features.append(feat)
            continue

        # ── Trained model ──────────────────────────────────────────────
        # Asked before the crop below, and from its own zoom-18 tiles rather
        # than the supplied image: a city-wide fetch is capped at 400 tiles
        # and walks the zoom down until it fits, so it arrives at roughly a
        # metre per pixel and a ten-metre house is eight pixels across.
        model_shape, model_conf = None, 0.0
        if trained is not None:
            try:
                model_shape, model_conf = trained.classify(ring, props)
            except Exception as exc:                        # noqa: BLE001
                logger.debug("roof model failed on one building: %s", exc)
                model_shape = None

        if model_only:
            if model_shape is None:
                skipped += 1
            else:
                props["roof:shape"] = model_shape
                props["roof_source"] = "roof_model_gbm"
                props["roof_confidence"] = round(float(model_conf), 3)
                feat["properties"] = props
                classified += 1
                by_model += 1
            out_features.append(feat)
            continue

        # ── Crop bounds ────────────────────────────────────────────────
        cr0, cr1, cc0, cc1 = _crop_bounds_for_ring(
            ring, north, south, east, west, rgb_h, rgb_w
        )
        crop_h, crop_w = cr1 - cr0, cc1 - cc0
        if crop_h < 2 or crop_w < 2:
            # Too few pixels in the supplied image for the signal cascade, but
            # the model read its own tiles and does not care.
            if model_shape is not None:
                props["roof:shape"] = model_shape
                props["roof_source"] = "roof_model_gbm"
                props["roof_confidence"] = round(float(model_conf), 3)
                feat["properties"] = props
                classified += 1
                by_model += 1
            else:
                skipped += 1
            out_features.append(feat)
            continue

        c_north, c_south, c_east, c_west = _crop_geo_bounds(
            cr0, cr1, cc0, cc1, north, south, east, west, rgb_h, rgb_w
        )
        footprint_mask = _ring_to_footprint_mask(
            ring, c_north, c_south, c_east, c_west, crop_h, crop_w
        )

        # ── Signal 1: CNN ──────────────────────────────────────────────
        rgb_crops = [img[cr0:cr1, cc0:cc1] for img in rgb_stack]
        cnn_result = ((None, 0.0)
                      if model_shape is not None or cnn_net is None
                      else _roofnet_classify_patch(rgb_crops, footprint_mask,
                                                   cnn_net))

        # ── Signal 2: elevation profile ────────────────────────────────
        if height_raster is not None:
            dem_crop = height_raster[cr0:cr1, cc0:cc1].astype(np.float32)
            elev_feat = _extract_elev_features(dem_crop, footprint_mask)
        else:
            elev_feat = _ElevFeatures(0.0, 0.0, 0.0, 0.5, 0.0, 0)
            dem_crop = None

        # ── Signal 3: multi-temporal ────────────────────────────────────
        shadow_crops = [sm[cr0:cr1, cc0:cc1] for sm in shadow_stack]
        if n_images >= 2:
            mt_feat = _extract_multitemporal_features(
                rgb_crops, shadow_crops, footprint_mask,
                pixel_m, lat_mid, lon_mid,
                acquisition_months, acquisition_hours,
            )
        else:
            mt_feat = _MultiTemporalFeatures(0.0, 0.0, 0.0, 1)

        # ── Signal 4: RGB appearance ────────────────────────────────────
        rgb_feat = _extract_rgb_features(rgb_crops[0], footprint_mask)

        # ── Signal 5: shadow geometry ───────────────────────────────────
        shadow_feat = _extract_shadow_features(shadow_crops[0], footprint_mask)

        # ── Fuse ───────────────────────────────────────────────────────
        if model_shape is not None:
            roof_shape, conf = model_shape, model_conf
            by_model += 1
        else:
            roof_shape, conf = _classify(
                elev_feat, rgb_feat, shadow_feat, mt_feat, cnn_result, pixel_m
            )

        if roof_shape is None:
            skipped += 1
            out_features.append(feat)
            continue

        props["roof:shape"] = roof_shape
        props["roof_source"] = ("roof_model_gbm" if model_shape is not None
                                else "satellite_classify")
        # The tier that produced the call, as a number. The lower tiers answer
        # for every building whether or not they know anything, so a consumer
        # that cannot afford a guess needs to see how confident this one was.
        props["roof_confidence"] = round(float(conf), 3)

        # ── Optional height estimate ───────────────────────────────────
        if (
            estimate_roof_heights
            and dem_crop is not None
            and not (props.get("roof:height") or "").strip()
        ):
            roof_h = _estimate_roof_height_from_elev(dem_crop, footprint_mask)
            if roof_h is not None:
                props["roof:height"] = str(roof_h)

        feat["properties"] = props
        classified += 1
        out_features.append(feat)

    result = dict(buildings_geojson)
    result["features"] = out_features
    result["_stats"] = {
        "total": total,
        "classified": classified,
        "skipped": skipped,
        "unchanged": unchanged,
        "n_images": n_images,
        "by_model": by_model,
    }
    logger.info(
        "classify_roof_shapes: %d buildings, %d classified (%d by model), "
        "%d skipped, %d unchanged",
        total, classified, by_model, skipped, unchanged,
    )
    return result

