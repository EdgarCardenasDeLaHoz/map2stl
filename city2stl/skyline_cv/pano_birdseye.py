"""Pano → bird's-eye projection + rotation registration (F-SKY11.2).

Inverse-perspective-map the stitched 360° pano onto a top-down canvas
in the same projection as the satellite (metres around the seed),
then recover the spin's true heading by rotating the bird's-eye view
until its water shape matches the satellite's water shape.

Public surface:
  pano_to_birdseye          — IPM-render a pano mask into a top-down canvas
  crop_sat_to_seed_canvas   — crop the satellite water mask to the same canvas
  register_by_rotation      — full rotation sweep, returns (best, scores)

Design note: this is the F-SKY11.2 replacement for F-SKY11.1's
per-bearing horizon scoring. The 1-D approach lost the radial dimension
the satellite carries (where the coastline is, not just whether it's
present). The 2-D bird's-eye preserves it.

See ``docs/plans/F-SKY11.2-pano-birdseye-registration.md``.
"""

from __future__ import annotations

import math

import numpy as np


_METRES_PER_DEG_LAT = 110_540.0


def _metres_per_deg_lon(lat_deg: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat_deg))


def pano_to_birdseye(
    pano_mask: np.ndarray,
    headings_per_col: np.ndarray,
    *,
    fov_deg_for_x: float = 75.0,
    view_width_px: int = 640,
    pitch_deg: float = 0.0,
    camera_h_m: float = 1.7,
    canvas_radius_m: float = 500.0,
    m_per_px: float = 1.0,
    min_radius_m: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Inverse-perspective-map a 360° pano mask onto a top-down
    boolean canvas centred on the seed.

    Reverse-projection algorithm: for every canvas pixel within
    ``canvas_radius_m`` of the centre, compute the (bearing, elevation)
    line of sight that hits that ground point under the assumption
    that ground is at sea level and the camera is at ``camera_h_m``
    above sea level. Read the pano pixel at that (bearing, elevation)
    and stamp its water/non-water value onto the canvas.

    Compared to forward-stamping each pano pixel onto the canvas:
      - No gaps between concentric rings (every canvas pixel gets a
        sample, by construction).
      - Vectorized as one large meshgrid op, much faster than the
        per-row outer product (still ~200 ms on a 1001² canvas).
      - The relationship "near-camera pano rows have lots of canvas
        pixels each" emerges naturally because near canvas pixels all
        hit the same near pano row.

    Returns ``(canvas_mask, canvas_valid)``:
      - ``canvas_mask``  : (S, S) bool. True where pano_mask was True
        at the corresponding ray.
      - ``canvas_valid`` : (S, S) bool. True where the canvas pixel is
        within [min_radius_m, canvas_radius_m] AND the corresponding
        pano pixel is below the horizon (i.e. a valid ground point).

    Canvas is North-up, East-right; size
    ``S = 2*round(canvas_radius_m / m_per_px) + 1`` so the seed sits at
    the centre pixel.
    """
    if pano_mask.ndim != 2:
        raise ValueError("pano_mask must be 2-D")
    H, W = pano_mask.shape
    if headings_per_col.size != W:
        raise ValueError("headings_per_col length != pano width")

    # focal in pixels comes from the HORIZONTAL FOV of the per-view
    # spin capture (typically 75°) and the per-view image width
    # (typically 640). Vertical FOV is implicit (= 2·atan(H_view/(2*focal))),
    # not 75°. Using the wrong focal_y put every IPM canvas pixel ~20%
    # closer to the horizon than reality.
    focal_y = (view_width_px / 2.0) / math.tan(
        math.radians(fov_deg_for_x / 2.0))
    horizon_y = H / 2.0 - math.tan(math.radians(pitch_deg)) * focal_y

    S = 2 * int(round(canvas_radius_m / m_per_px)) + 1
    centre = S // 2

    yy, xx = np.meshgrid(
        np.arange(S, dtype=np.float32),
        np.arange(S, dtype=np.float32),
        indexing="ij",
    )
    dx_m = (xx - centre) * m_per_px
    dy_m = (centre - yy) * m_per_px  # North is up = smaller pixel y

    r_m = np.sqrt(dx_m * dx_m + dy_m * dy_m)
    # Bearing measured clockwise from north: atan2(east, north).
    theta_rad = np.arctan2(dx_m, dy_m)
    theta_deg = (np.degrees(theta_rad) + 360.0) % 360.0

    in_range = (r_m >= min_radius_m) & (r_m <= canvas_radius_m)

    # Per-canvas-pixel elevation-below-horizon and pano row.
    elev_below_rad = np.arctan2(camera_h_m, r_m)
    pano_y_f = horizon_y + focal_y * np.tan(elev_below_rad)
    pano_y_i = np.clip(np.round(pano_y_f).astype(np.int32), 0, H - 1)
    valid_y = (pano_y_f >= 0.0) & (pano_y_f < H)

    # Per-canvas-pixel pano column = the column whose recorded heading
    # is closest to this pixel's theta. headings_per_col is roughly
    # monotonically increasing modulo 360 across the stitched pano —
    # for robustness we use the full nearest-neighbour search via
    # searchsorted on a sorted copy.
    sort_idx = np.argsort(headings_per_col)
    sorted_h = headings_per_col[sort_idx]
    target = theta_deg.ravel()
    ins = np.searchsorted(sorted_h, target).clip(0, sorted_h.size)
    # Compare to both neighbours (the wrap at 360°/0° introduces an
    # edge case where the nearest might be at the opposite end of the
    # sorted array; modular distance handles it).
    prev = (ins - 1).clip(0, sorted_h.size - 1)
    curr = ins.clip(0, sorted_h.size - 1)
    d_prev = np.abs((target - sorted_h[prev] + 180.0) % 360.0 - 180.0)
    d_curr = np.abs((target - sorted_h[curr] + 180.0) % 360.0 - 180.0)
    nearest_sort_pos = np.where(d_prev <= d_curr, prev, curr)
    pano_c = sort_idx[nearest_sort_pos].astype(np.int32).reshape(S, S)
    pano_c = np.clip(pano_c, 0, W - 1)

    canvas_valid = in_range & valid_y
    canvas_mask = np.zeros((S, S), dtype=bool)
    canvas_mask[canvas_valid] = pano_mask[
        pano_y_i[canvas_valid], pano_c[canvas_valid]
    ]
    return canvas_mask, canvas_valid


def crop_sat_to_seed_canvas(
    sat_water_mask: np.ndarray,
    sat_project,
    seed_lat: float,
    seed_lon: float,
    *,
    canvas_radius_m: float = 500.0,
    m_per_px: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Crop the satellite water mask to the same square canvas the
    bird's-eye uses: ``canvas_radius_m`` metres around the seed,
    resampled to ``m_per_px`` per pixel.

    Returns ``(sat_canvas_mask, sat_canvas_valid)``. ``sat_canvas_valid``
    marks pixels that actually fell inside the satellite image — for an
    on-shore seed that's the whole canvas, but for a seed near the
    bbox edge a strip can fall outside the sat image; we mask those
    out so they don't dilute the IoU.
    """
    sat_h, sat_w = sat_water_mask.shape[:2]
    mlat = _METRES_PER_DEG_LAT
    mlon = _metres_per_deg_lon(seed_lat)
    S = 2 * int(round(canvas_radius_m / m_per_px)) + 1
    centre = S // 2

    # Per-canvas-pixel offsets in metres
    xs_m = (np.arange(S) - centre) * m_per_px            # east
    ys_m = (centre - np.arange(S)) * m_per_px            # north
    grid_x, grid_y = np.meshgrid(xs_m, ys_m)
    grid_lon = seed_lon + grid_x / mlon
    grid_lat = seed_lat + grid_y / mlat

    # Get the local Jacobian of sat_project at the seed in PIXELS PER
    # METRE so we can fill a 1001² canvas without 1M closure calls.
    # The probe offsets are in metres mapped to degrees via
    # mlon / mlat so the returned pixel deltas are per-metre directly
    # (no unit-conversion factor needed downstream — that was a bug in
    # the original implementation, off by mlon ~= 1.1e5 which made the
    # cropped canvas sample within a 1-pixel-wide column at the seed).
    px00, py00 = sat_project(seed_lon, seed_lat)
    px01, py01 = sat_project(seed_lon + 1.0 / mlon, seed_lat)
    px10, py10 = sat_project(seed_lon, seed_lat + 1.0 / mlat)
    sat_px_per_m_east = px01 - px00      # sat-x delta per metre east
    sat_py_per_m_north = py10 - py00     # sat-y delta per metre north
    sat_x = px00 + grid_x * sat_px_per_m_east
    sat_y = py00 + grid_y * sat_py_per_m_north
    sx_i = np.round(sat_x).astype(np.int32)
    sy_i = np.round(sat_y).astype(np.int32)
    in_sat = (sx_i >= 0) & (sx_i < sat_w) & (sy_i >= 0) & (sy_i < sat_h)
    sat_canvas_mask = np.zeros((S, S), dtype=bool)
    sat_canvas_mask[in_sat] = sat_water_mask[
        sy_i[in_sat], sx_i[in_sat]]
    return sat_canvas_mask, in_sat


def register_by_rotation(
    birdseye_mask: np.ndarray,
    birdseye_valid: np.ndarray,
    sat_canvas_mask: np.ndarray,
    sat_canvas_valid: np.ndarray,
    *,
    step_deg: float = 1.0,
    seed_exclude_radius_px: int = 8,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Rotate the bird's-eye mask in 1° increments around the canvas
    centre and compute IoU vs the satellite canvas mask, restricted to
    pixels both views sampled (intersect their valid masks). Returns
    ``(best_offset_deg, cand_deg_array, score_array)``.

    A tiny disc around the canvas centre is excluded — within ~8 px of
    the seed the bird's-eye projection has no resolution (the camera
    can't see straight down) and both views agree by default, which
    inflates the score uniformly across rotations. Excluding it lets
    the actual coastline drive the recovery.
    """
    from scipy.ndimage import rotate as _rot  # noqa: PLC0415
    S = birdseye_mask.shape[0]
    if (sat_canvas_mask.shape != birdseye_mask.shape
            or sat_canvas_valid.shape != birdseye_mask.shape):
        raise ValueError("birdseye and satellite canvases must match in shape")

    # Annular ROI: where both views have valid samples AND not too
    # close to the seed.
    yy, xx = np.ogrid[:S, :S]
    centre = S // 2
    r2 = (yy - centre) ** 2 + (xx - centre) ** 2
    inner_excl = r2 < (seed_exclude_radius_px ** 2)
    base_roi = sat_canvas_valid & (~inner_excl)

    n = int(360.0 / step_deg)
    cand_deg = np.linspace(0.0, 360.0, n, endpoint=False)
    scores = np.zeros(n, dtype=np.float32)

    be_u8 = birdseye_mask.astype(np.uint8)
    bv_u8 = birdseye_valid.astype(np.uint8)
    for i, theta in enumerate(cand_deg):
        rot_mask = _rot(be_u8, -float(theta), order=0,
                        reshape=False, mode="constant", cval=0) > 0
        rot_valid = _rot(bv_u8, -float(theta), order=0,
                         reshape=False, mode="constant", cval=0) > 0
        roi = base_roi & rot_valid
        if not roi.any():
            continue
        inter = (rot_mask & sat_canvas_mask & roi).sum()
        union = ((rot_mask | sat_canvas_mask) & roi).sum()
        scores[i] = float(inter) / float(union) if union > 0 else 0.0
    best_idx = int(np.argmax(scores))
    return float(cand_deg[best_idx]), cand_deg, scores


# ============================================================================
# NEW: Building-based registration (improved approach for heading refinement)
# ============================================================================

def register_by_building_correlation(
    birdseye_buildings: np.ndarray,
    birdseye_valid: np.ndarray,
    sat_buildings_mask: np.ndarray,
    sat_canvas_valid: np.ndarray,
    *,
    step_deg: float = 1.0,
    seed_exclude_radius_px: int = 12,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Register pano buildings against satellite buildings by rotating
    the pano canvas and finding the rotation that maximizes feature overlap.

    Uses normalized cross-correlation instead of IoU — better for partial
    overlaps and edge-aligned features (buildings don't need perfect fit).

    Returns (best_offset_deg, candidates_deg, correlation_scores).

    Key advantage over water-based registration:
    - Buildings visible at all ranges (no monocular depth limit)
    - Robust to partial visibility (waterfront buildings, occlusion)
    - Works for any seed (even inland)
    """
    from scipy.ndimage import rotate as _rot
    from scipy.ndimage import correlate

    S = birdseye_buildings.shape[0]
    if (sat_buildings_mask.shape != birdseye_buildings.shape
            or sat_canvas_valid.shape != birdseye_buildings.shape):
        raise ValueError("birdseye and satellite canvases must match in shape")

    # Annular ROI: exclude near-seed (low resolution)
    yy, xx = np.ogrid[:S, :S]
    centre = S // 2
    r2 = (yy - centre) ** 2 + (xx - centre) ** 2
    inner_excl = r2 < (seed_exclude_radius_px ** 2)
    base_roi = sat_canvas_valid & (~inner_excl)

    n = int(360.0 / step_deg)
    cand_deg = np.linspace(0.0, 360.0, n, endpoint=False)
    scores = np.zeros(n, dtype=np.float32)

    be_f32 = birdseye_buildings.astype(np.float32)
    bv_u8 = birdseye_valid.astype(np.uint8)
    sat_f32 = sat_buildings_mask.astype(np.float32)

    # Normalize satellite for correlation
    sat_mean = sat_f32[base_roi].mean() if base_roi.any() else 0.5
    sat_std = sat_f32[base_roi].std() if base_roi.any() else 1.0
    if sat_std < 1e-6:
        sat_std = 1.0
    sat_norm = (sat_f32 - sat_mean) / sat_std

    for i, theta in enumerate(cand_deg):
        # Rotate pano buildings and valid mask
        rot_buildings = _rot(be_f32, -float(theta), order=1,
                             reshape=False, mode="constant", cval=0.0)
        rot_valid = _rot(bv_u8, -float(theta), order=0,
                         reshape=False, mode="constant", cval=0) > 0

        roi = base_roi & rot_valid
        if not roi.any():
            continue

        # Normalize rotated buildings
        rot_in_roi = rot_buildings[roi]
        rot_mean = rot_in_roi.mean()
        rot_std = rot_in_roi.std()
        if rot_std < 1e-6:
            continue
        rot_norm = (rot_buildings - rot_mean) / rot_std

        # Normalized cross-correlation (Pearson's r)
        product = (rot_norm * sat_norm)[roi].sum()
        count = roi.sum()
        scores[i] = product / float(count) if count > 0 else 0.0

    best_idx = int(np.argmax(scores))
    best_offset = float(cand_deg[best_idx])

    return best_offset, cand_deg, scores


def compute_heading_offset(
    correlation_scores: np.ndarray,
    candidates_deg: np.ndarray,
    *,
    subpixel_refine: bool = True,
) -> tuple[float, float, float]:
    """Find the heading offset from correlation curve.

    Returns (offset_deg, peak_value, sigma).
    Optionally refines peak position via parabolic fit for sub-degree accuracy.
    """
    best_idx = int(np.argmax(correlation_scores))
    peak_value = float(correlation_scores[best_idx])
    offset_deg = float(candidates_deg[best_idx])

    if not subpixel_refine or best_idx < 1 or best_idx >= len(candidates_deg) - 1:
        # Compute sigma as width of peak
        threshold = peak_value * 0.5
        above_threshold = correlation_scores >= threshold
        if above_threshold.any():
            width_px = np.where(above_threshold)[0].max() - np.where(above_threshold)[0].min()
            sigma = float(width_px) * float(candidates_deg[1] - candidates_deg[0])
        else:
            sigma = float(candidates_deg[1] - candidates_deg[0])
        return offset_deg, peak_value, sigma

    # Parabolic refinement: fit parabola through 3 points around peak
    y0 = correlation_scores[best_idx - 1]
    y1 = correlation_scores[best_idx]
    y2 = correlation_scores[best_idx + 1]
    x0, x1, x2 = candidates_deg[best_idx - 1:best_idx + 2]

    # Quadratic through 3 points
    denom = (x0 - x1) * (x1 - x2) * (x0 - x2)
    if abs(denom) > 1e-6:
        a = (x1*y0*(x1-x2) + x0*y1*(x2-x1) + x2*y2*(x0-x1)) / denom
        b = (y0*(x1*x1 - x2*x2) + y1*(x2*x2 - x0*x0) + y2*(x0*x0 - x1*x1)) / denom
        if a != 0:
            offset_deg = -b / (2.0 * a)
            refined_peak = a * offset_deg * offset_deg + b * offset_deg
        else:
            refined_peak = peak_value
    else:
        refined_peak = peak_value

    # Estimate sigma from curve width
    threshold = peak_value * 0.5
    above_threshold = correlation_scores >= threshold
    if above_threshold.any():
        width_px = np.where(above_threshold)[0].max() - np.where(above_threshold)[0].min()
        sigma = float(width_px) * float(candidates_deg[1] - candidates_deg[0]) / 3.0
    else:
        sigma = 5.0  # Default if can't estimate

    return offset_deg, refined_peak, sigma


# ============================================================================
# Image-based registration (project images to same space, correlate raw data)
# ============================================================================

def pano_image_to_birdseye(
    pano_image: np.ndarray,
    headings_per_col: np.ndarray,
    *,
    fov_deg_for_x: float = 75.0,
    view_width_px: int = 640,
    pitch_deg: float = 0.0,
    camera_h_m: float = 1.7,
    canvas_radius_m: float = 500.0,
    m_per_px: float = 1.0,
    min_radius_m: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Inverse-perspective-map a 360° pano IMAGE onto a top-down canvas.

    Like pano_to_birdseye but for RGB image data with bilinear interpolation.
    Returns (canvas_image, canvas_valid) where canvas_image is (S, S, 3) RGB
    and canvas_valid is (S, S) bool marking valid pixels.
    """
    if pano_image.ndim != 3 or pano_image.shape[2] != 3:
        raise ValueError("pano_image must be (H, W, 3) RGB")
    H, W = pano_image.shape[:2]
    if headings_per_col.size != W:
        raise ValueError("headings_per_col length != pano width")

    focal_y = (view_width_px / 2.0) / math.tan(
        math.radians(fov_deg_for_x / 2.0))
    horizon_y = H / 2.0 - math.tan(math.radians(pitch_deg)) * focal_y

    S = 2 * int(round(canvas_radius_m / m_per_px)) + 1
    centre = S // 2

    yy, xx = np.meshgrid(
        np.arange(S, dtype=np.float32),
        np.arange(S, dtype=np.float32),
        indexing="ij",
    )
    dx_m = (xx - centre) * m_per_px
    dy_m = (centre - yy) * m_per_px

    r_m = np.sqrt(dx_m * dx_m + dy_m * dy_m)
    theta_rad = np.arctan2(dx_m, dy_m)
    theta_deg = (np.degrees(theta_rad) + 360.0) % 360.0

    in_range = (r_m >= min_radius_m) & (r_m <= canvas_radius_m)

    elev_below_rad = np.arctan2(camera_h_m, r_m)
    pano_y_f = horizon_y + focal_y * np.tan(elev_below_rad)
    valid_y = (pano_y_f >= 0.0) & (pano_y_f < H)

    # Nearest-neighbour heading lookup (same as pano_to_birdseye)
    sort_idx = np.argsort(headings_per_col)
    sorted_h = headings_per_col[sort_idx]
    target = theta_deg.ravel()
    ins = np.searchsorted(sorted_h, target).clip(0, sorted_h.size)
    prev = (ins - 1).clip(0, sorted_h.size - 1)
    curr = ins.clip(0, sorted_h.size - 1)
    d_prev = np.abs((target - sorted_h[prev] + 180.0) % 360.0 - 180.0)
    d_curr = np.abs((target - sorted_h[curr] + 180.0) % 360.0 - 180.0)
    nearest_sort_pos = np.where(d_prev <= d_curr, prev, curr)
    pano_c_f = sort_idx[nearest_sort_pos].astype(np.float32).reshape(S, S)

    canvas_valid = in_range & valid_y
    canvas_image = np.zeros((S, S, 3), dtype=pano_image.dtype)

    # Bilinear interpolation for sub-pixel accuracy
    from scipy.ndimage import map_coordinates
    pano_y_f_valid = pano_y_f[canvas_valid]
    pano_c_f_valid = pano_c_f[canvas_valid]
    coords = np.stack([pano_y_f_valid, pano_c_f_valid], axis=0)
    for c in range(3):
        interpolated = map_coordinates(
            pano_image[..., c], coords, order=1, mode="constant", cval=0.0
        )
        canvas_image[canvas_valid, c] = interpolated

    return canvas_image, canvas_valid


def satellite_image_to_birdseye(
    sat_image: np.ndarray,
    sat_project,
    seed_lat: float,
    seed_lon: float,
    *,
    canvas_radius_m: float = 500.0,
    m_per_px: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Project satellite image to the bird's-eye canvas (same frame as pano_to_birdseye).

    sat_project is a callable(lon, lat) -> (x_px, y_px) from the satellite loader.
    Returns (sat_canvas_image, sat_canvas_valid).
    """
    if sat_image.ndim != 3 or sat_image.shape[2] != 3:
        raise ValueError("sat_image must be (H, W, 3) RGB")

    S = 2 * int(round(canvas_radius_m / m_per_px)) + 1
    centre = S // 2

    yy, xx = np.meshgrid(
        np.arange(S, dtype=np.float32),
        np.arange(S, dtype=np.float32),
        indexing="ij",
    )
    dx_m = (xx - centre) * m_per_px
    dy_m = (centre - yy) * m_per_px

    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(seed_lat))
    canvas_lon = seed_lon + dx_m / mlon
    canvas_lat = seed_lat - dy_m / mlat

    # Project each canvas pixel to satellite image coordinates
    sat_x_f, sat_y_f = np.zeros_like(dx_m), np.zeros_like(dy_m)
    for i in range(S):
        for j in range(S):
            sat_x, sat_y = sat_project(canvas_lon[i, j], canvas_lat[i, j])
            sat_x_f[i, j] = sat_x
            sat_y_f[i, j] = sat_y

    # Mask for valid satellite pixels (within image bounds)
    H, W = sat_image.shape[:2]
    canvas_valid = (sat_x_f >= 0) & (sat_x_f < W - 1) & (sat_y_f >= 0) & (sat_y_f < H - 1)

    # Bilinear interpolation
    from scipy.ndimage import map_coordinates
    canvas_image = np.zeros((S, S, 3), dtype=sat_image.dtype)
    coords = np.stack([sat_y_f[canvas_valid], sat_x_f[canvas_valid]], axis=0)
    for c in range(3):
        interpolated = map_coordinates(
            sat_image[..., c], coords, order=1, mode="constant", cval=0.0
        )
        canvas_image[canvas_valid, c] = interpolated

    return canvas_image, canvas_valid


def register_by_image_correlation(
    pano_canvas_image: np.ndarray,
    pano_canvas_valid: np.ndarray,
    sat_canvas_image: np.ndarray,
    sat_canvas_valid: np.ndarray,
    *,
    step_deg: float = 1.0,
    seed_exclude_radius_px: int = 12,
    pano_content_mask: np.ndarray | None = None,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Register pano against satellite by rotating pano and computing
    normalized cross-correlation on actual image data (not masks).

    pano_content_mask should mark pixels that have actual content (e.g.,
    building or water), excluding sky and invalid areas.

    Returns (best_offset_deg, candidates_deg, correlation_scores).
    Correlation ranges from -1 to +1; higher is better aligned.
    """
    from scipy.ndimage import rotate as _rot

    S = pano_canvas_image.shape[0]
    if (sat_canvas_image.shape[:2] != pano_canvas_image.shape[:2]
            or sat_canvas_valid.shape != pano_canvas_image.shape[:2]):
        raise ValueError("pano and satellite canvases must match in size")

    # Annular ROI: exclude near-seed
    yy, xx = np.ogrid[:S, :S]
    centre = S // 2
    r2 = (yy - centre) ** 2 + (xx - centre) ** 2
    inner_excl = r2 < (seed_exclude_radius_px ** 2)
    base_roi = sat_canvas_valid & (~inner_excl)

    # If pano_content_mask is provided, use it to exclude sky/invalid pano pixels
    if pano_content_mask is not None:
        base_roi = base_roi & pano_content_mask

    n = int(360.0 / step_deg)
    cand_deg = np.linspace(0.0, 360.0, n, endpoint=False)
    scores = np.zeros(n, dtype=np.float32)

    # Normalize satellite image in ROI
    sat_in_roi = sat_canvas_image[base_roi].astype(np.float32)
    sat_mean = sat_in_roi.mean(axis=0)
    sat_std = sat_in_roi.std(axis=0)
    sat_std = np.where(sat_std < 1e-6, 1.0, sat_std)
    sat_norm = (sat_canvas_image.astype(np.float32) - sat_mean) / sat_std

    for i, theta in enumerate(cand_deg):
        # Rotate pano image and content mask
        rot_image = np.zeros_like(pano_canvas_image, dtype=np.float32)
        rot_valid = np.zeros(pano_canvas_valid.shape, dtype=bool)
        rot_content = np.zeros(pano_canvas_valid.shape, dtype=bool)

        for c in range(3):
            rot_image[..., c] = _rot(
                pano_canvas_image[..., c].astype(np.float32),
                -float(theta), order=1, reshape=False, mode="constant", cval=0.0
            )
        rot_valid = _rot(pano_canvas_valid.astype(np.uint8), -float(theta),
                        order=0, reshape=False, mode="constant", cval=0) > 0
        if pano_content_mask is not None:
            rot_content = _rot(pano_content_mask.astype(np.uint8), -float(theta),
                              order=0, reshape=False, mode="constant", cval=0) > 0
            roi = base_roi & rot_valid & rot_content
        else:
            roi = base_roi & rot_valid

        if not roi.any():
            continue

        # Normalize rotated image
        rot_in_roi = rot_image[roi].astype(np.float32)
        rot_mean = rot_in_roi.mean(axis=0)
        rot_std = rot_in_roi.std(axis=0)
        rot_std = np.where(rot_std < 1e-6, 1.0, rot_std)
        rot_norm = (rot_image.astype(np.float32) - rot_mean) / rot_std

        # Compute mean correlation across all 3 channels
        correlations = []
        for c in range(3):
            product = (rot_norm[..., c] * sat_norm[..., c])[roi].sum()
            count = float(roi.sum())
            correlations.append(product / count if count > 0 else 0.0)

        scores[i] = float(np.mean(correlations))

    best_idx = int(np.argmax(scores))
    best_offset = float(cand_deg[best_idx])

    return best_offset, cand_deg, scores
