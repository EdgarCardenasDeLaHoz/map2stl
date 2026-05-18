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
