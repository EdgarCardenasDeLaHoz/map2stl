"""Image-level cross-view registration via coastline alignment (F-SKY11.1).

The coastline is a good registration signal for water-adjacent seeds:
water vs land is a binary, high-contrast feature that exists in BOTH
the satellite (top-down) and street view (side-view) without depending
on building positions, heights, or any ML model trained on a specific
texture. The ESRI satellite mosaic is geo-referenced accurately; the
coastline traced from it is in absolute lat/lon and doesn't inherit
OSM's 2-10 m polygon noise.

Approach used by the production pipeline (region_pdf.py):

  1. `detect_sat_water_mask` produces a binary water mask from ESRI
     satellite imagery using an HSV threshold.
  2. `detect_coastline_keypoints` finds per-bearing water→land transition
     points on that mask.
  3. `sweep_pano_heading_offset` slides the keypoints against the
     stitched pano's water mask to find the heading that best lines up
     the satellite's predicted water-top y-coordinates with the pano's
     actual water-top y-coordinates.

The score curve from the sweep is a direct diagnostic: a sharp peak
means a confident heading; a flat curve means the view doesn't have
enough coastline structure to pin a heading (common for inland views
and for peninsula seeds where water is on both sides — a 180° symmetry
the sweep can't resolve alone).

A consolidated multi-channel heading-recovery demo (`scripts/13_heading_
recovery_demo.py`) explores adding asymmetric signals (RGB brightness)
to break the peninsula symmetry. The functions in THIS module are the
keypoint baseline that the production pipeline uses.

Public surface:
  detect_sat_water_mask          — HSV-based water detector for ESRI tiles
  detect_coastline_keypoints     — per-bearing water→land transition points
  project_lonlat_to_view         — pinhole project a sea-level point into a SV
  score_pano_offset_keypoints    — single-offset agreement score
  sweep_pano_heading_offset      — full offset sweep, returns (best, scores)
"""

from __future__ import annotations

import math
from typing import Callable

import cv2
import numpy as np


# Earth-projection constants. Both used for the same lat→m / lon→m
# conversion that everything else in skyline_cv uses; kept local so the
# module has no import dependency on the bigger pipeline.
_METRES_PER_DEG_LAT = 110_540.0


def _metres_per_deg_lon(lat_deg: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat_deg))


def detect_sat_water_mask(
    sat_image_rgb: np.ndarray,
    *,
    hue_min: int = 65,
    hue_max: int = 135,
    saturation_min: int = 80,
    saturation_max: int = 230,
    value_min: int = 30,
    value_max: int = 200,
) -> np.ndarray:
    """Boolean water mask for an ESRI World Imagery RGB composite.

    HSV-based: pick pixels whose hue sits in the cyan-blue range
    (OpenCV hue 65-135 = ~130-270° on the standard wheel) with
    moderate saturation and value. Range was widened from the initial
    blue-only (80-130) band after measurement on Cartagena: Caribbean
    bay water hits hue 65-80 (turquoise / green-cyan) which the
    narrower band rejected, leaving radial-signature rays terminating
    on false-land pixels less than 100 m out from the seed.

    Returns
    -------
    np.ndarray  bool, same H×W as input.
    """
    hsv = cv2.cvtColor(sat_image_rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    water = (
        (h >= hue_min) & (h <= hue_max)
        & (s >= saturation_min) & (s <= saturation_max)
        & (v >= value_min) & (v <= value_max)
    )
    # Cleanup: small openings drop scattered blue pixels on roofs;
    # closing fills wave-streak holes in open water. 5×5 keeps the
    # coastline crisp at zoom 16 (~4-5 m/px).
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    water_u8 = (water.astype(np.uint8)) * 255
    water_u8 = cv2.morphologyEx(water_u8, cv2.MORPH_OPEN, kernel)
    water_u8 = cv2.morphologyEx(water_u8, cv2.MORPH_CLOSE, kernel)
    return water_u8.astype(bool)


def detect_coastline_keypoints(
    sat_water_mask: np.ndarray,
    sat_project: Callable[[float, float], tuple[float, float]],
    seed_lat: float,
    seed_lon: float,
    *,
    n_bearings: int = 72,
    max_range_m: float = 2500.0,
    step_m: float = 5.0,
    min_distance_m: float = 30.0,
) -> list[dict]:
    """Find the first water→land transition along each compass bearing
    from the seed; return one key point per bearing that has a
    transition inside ``max_range_m`` and beyond ``min_distance_m``.

    Each key point is a dict:
      bearing_deg   — compass bearing from seed (0 N, clockwise)
      distance_m    — great-circle distance from seed to the transition
      lat, lon      — lat/lon of the transition pixel (sea-level point)
      x_sat, y_sat  — satellite-image pixel coords (handy for overlay)

    Bearings whose ray stays in water for the full ``max_range_m`` (open
    sea direction) are dropped — they don't correspond to a marker the
    street view could match against. Bearings starting on land have
    distance ≈ ``min_distance_m`` and are also dropped (the seed sits on
    a building / road; we want the actual coastline, not the curb).

    ``n_bearings=72`` (one every 5°) gives roughly the resolution the
    SV's per-column matching can verify against; finer sampling adds
    near-duplicate points that visualise badly.
    """
    h_img, w_img = sat_water_mask.shape[:2]
    mlat = _METRES_PER_DEG_LAT
    mlon = _metres_per_deg_lon(seed_lat)

    bearings_deg = np.linspace(0.0, 360.0, n_bearings, endpoint=False)
    out: list[dict] = []
    # Require at least this many consecutive water samples before the
    # first subsequent non-water sample counts as a transition. Filters
    # noise like a stray turquoise rooftop pixel that would otherwise
    # masquerade as "water" and trip a false transition one step later.
    min_water_streak = 3
    for b_deg in bearings_deg:
        b_rad = math.radians(b_deg)
        dlon_per_m = math.sin(b_rad) / mlon
        dlat_per_m = math.cos(b_rad) / mlat
        water_streak = 0
        n_steps = int(math.ceil(max_range_m / step_m))
        for k in range(1, n_steps + 1):
            d_m = k * step_m
            lon = seed_lon + d_m * dlon_per_m
            lat = seed_lat + d_m * dlat_per_m
            px, py = sat_project(lon, lat)
            ix, iy = int(px), int(py)
            if not (0 <= ix < w_img and 0 <= iy < h_img):
                break
            is_water = bool(sat_water_mask[iy, ix])
            if is_water:
                water_streak += 1
                continue
            if water_streak >= min_water_streak and d_m >= min_distance_m:
                out.append({
                    "bearing_deg": float(b_deg),
                    "distance_m": float(d_m),
                    "lat": float(lat),
                    "lon": float(lon),
                    "x_sat": float(px),
                    "y_sat": float(py),
                })
                break
            water_streak = 0
    return out


def project_lonlat_to_view(
    lon: float, lat: float,
    seed_lat: float, seed_lon: float,
    heading_deg: float,
    fov_deg: float,
    image_width: int,
    image_height: int,
    *,
    point_elev_m: float = 0.0,
    camera_elev_m: float = 1.7,
    pitch_deg: float = 0.0,
) -> tuple[float, float] | None:
    """Pinhole-project a (lon, lat) point at ``point_elev_m`` (default
    sea level) into a street view captured at ``(seed_lat, seed_lon)``
    looking ``heading_deg`` from north with ``fov_deg`` horizontal FOV
    and the camera pitched up by ``pitch_deg`` (negative = down).

    Returns ``(x_px, y_px)`` or ``None`` when the point is behind the
    camera or off the horizontal frame. Out-of-vertical-frame y values
    are still returned (the caller may want to draw the column even when
    the point projects above/below the image).

    ``pitch_deg`` shifts the horizon line by ``-tan(pitch) * focal`` —
    a downward pitch lifts sea-level features upward in the frame.
    Skipping this term puts the projected keypoints ~50 px below where
    the actual water/land boundary appears for typical Google Street
    View URLs whose tilt parameter encodes a small downward angle.
    """
    mlat = _METRES_PER_DEG_LAT
    mlon = _metres_per_deg_lon(seed_lat)
    dx_m = (lon - seed_lon) * mlon
    dy_m = (lat - seed_lat) * mlat
    h_rad = math.radians(heading_deg)
    forward = dx_m * math.sin(h_rad) + dy_m * math.cos(h_rad)
    right = dx_m * math.cos(h_rad) - dy_m * math.sin(h_rad)
    if forward <= 1.0:
        return None
    focal_px = (image_width / 2.0) / math.tan(math.radians(fov_deg / 2.0))
    x_px = image_width / 2.0 + (right / forward) * focal_px
    if x_px < 0 or x_px >= image_width:
        return None
    p_rad = math.radians(pitch_deg)
    y_px = (
        image_height / 2.0
        + (camera_elev_m - point_elev_m) / forward * focal_px
        + math.tan(p_rad) * focal_px
    )
    return float(x_px), float(y_px)




def pano_water_top_to_lonlat(
    pano_water_mask: "np.ndarray",
    headings_per_col: "np.ndarray",
    seed_lat: float,
    seed_lon: float,
    *,
    column_stride: int = 8,
    pitch_deg: float = 0.0,
    camera_elev_m: float = 1.7,
    max_distance_m: float = 1500.0,
) -> list[tuple[float, float]]:
    """Invert the pinhole projection used by ``project_lonlat_to_view``
    to recover sea-level ``(lon, lat)`` points for the top of the water
    band in each pano column.

    For each column with water present we take the topmost water row
    (the apparent water/land boundary at that bearing) and solve for the
    sea-level distance under the same camera-height + pitch model the
    forward projection uses. Distance ``d = h * focal / (y - cy -
    tan(pitch) * focal)``. Bearings come from ``headings_per_col``
    (which already encodes the per-column compass heading after pano
    stitching). Sampling every ``column_stride`` columns keeps the
    output light enough for matplotlib without losing visual detail.

    Returned points are in geographic order along the seed's horizon,
    suitable for drawing a polyline on the minimap. Points beyond
    ``max_distance_m`` (typically the 1 km consideration window times
    a safety factor) are dropped so degenerate near-horizon rows don't
    extend the polyline across the whole panel.
    """
    if pano_water_mask is None or pano_water_mask.size == 0:
        return []
    H, W = pano_water_mask.shape[:2]
    if headings_per_col.size != W:
        return []
    # 75°-FOV-equivalent focal at the pano image height — matches the
    # vertical focal used by ``score_pano_offset_keypoints`` so this
    # inverse stays self-consistent with the forward scoring.
    focal_y = H / (2.0 * math.tan(math.radians(37.5)))
    cy = H / 2.0
    p_rad = math.radians(pitch_deg)
    horizon_shift = math.tan(p_rad) * focal_y

    mlat = _METRES_PER_DEG_LAT
    mlon = _metres_per_deg_lon(seed_lat)

    out: list[tuple[float, float]] = []
    for col in range(0, W, max(1, column_stride)):
        column = pano_water_mask[:, col]
        water_rows = np.where(column)[0]
        if water_rows.size == 0:
            continue
        y_top = float(water_rows.min())
        # Solve for distance to sea-level top-of-water at this column.
        denom = y_top - cy - horizon_shift
        if denom <= 1.0:
            continue  # at or above horizon — degenerate
        distance_m = camera_elev_m * focal_y / denom
        if not (0.0 < distance_m <= max_distance_m):
            continue
        bearing_rad = math.radians(float(headings_per_col[col]))
        dx_m = distance_m * math.sin(bearing_rad)
        dy_m = distance_m * math.cos(bearing_rad)
        lon = seed_lon + dx_m / mlon
        lat = seed_lat + dy_m / mlat
        out.append((lon, lat))
    return out


def score_pano_offset_keypoints(
    keypoints: list[dict],
    pano_water_mask: np.ndarray,
    headings_per_col: np.ndarray,
    seed_lat: float,
    seed_lon: float,
    candidate_offset_deg: float,
    *,
    pitch_deg: float = 0.0,
    tolerance_px: int = 25,
    column_match_tol_deg: float = 1.0,
) -> float:
    """Score how well the satellite key points line up with the pano's
    horizon line at a candidate heading offset.

    For each keypoint we map its world bearing through the offset to
    find the pano column whose recorded heading matches; then we
    compare the column's actual top-of-water y to the keypoint's
    expected y under sea-level pinhole projection at the same column
    heading. Mean linearly-scored agreement across keypoints that
    project into a represented pano column.

    Pano horizontal mapping uses ``headings_per_col`` directly — the
    stitcher already encodes the (slightly non-uniform) per-column
    bearing within each spin view's central crop. No FOV-tangent
    re-derivation is needed on the horizontal axis. The vertical axis
    keeps the same sea-level + camera-elevation + pitch formula F-SKY11
    uses.
    """
    if not keypoints or pano_water_mask is None or pano_water_mask.size == 0:
        return 0.0
    H, W = pano_water_mask.shape[:2]
    if headings_per_col.size != W:
        return 0.0
    n = 0
    total = 0.0
    f_per_col_px = W / 360.0  # for the pitch-projection y term
    # The pano's "effective focal length" used for vertical projection
    # is W / 2π since 360° wraps to W pixels — so deg per px = 360 / W,
    # rad per px = 2π / W, and focal_for_y = (W/2) / tan(π) is ill-
    # defined. Instead we compute the local focal each column uses
    # implicitly via the keypoint's distance and pitch, mirroring
    # ``project_lonlat_to_view`` with a per-bearing focal of W/(2π)
    # equivalent. To keep this consistent with F-SKY11 we use the SAME
    # focal a 75° / 30°-crop view would have at the chosen column
    # density.
    for kp in keypoints:
        target_bearing = (kp["bearing_deg"] - candidate_offset_deg) % 360.0
        # Circular nearest-column lookup.
        diffs = ((headings_per_col - target_bearing + 180.0) % 360.0) - 180.0
        idx = int(np.argmin(np.abs(diffs)))
        if abs(float(diffs[idx])) > column_match_tol_deg:
            continue
        column = pano_water_mask[:, idx]
        water_rows = np.where(column)[0]
        if water_rows.size == 0:
            n += 1  # contributes 0 — keypoint expected but no water
            continue
        y_top_water = int(water_rows.min())
        # Expected y for a sea-level point at the keypoint's distance
        # (same formula as project_lonlat_to_view's vertical term, with
        # per-pano-column focal equivalent at the local bearing).
        kp_dist = float(kp.get("distance_m", 0.0))
        if kp_dist <= 1.0:
            continue
        # Use the focal length the equivalent F-SKY11 75°-FOV view
        # would have at the same image height. Pano H is the same as a
        # per-view image, so a 75° FOV at this H gives:
        focal_for_y = H / (2.0 * math.tan(math.radians(37.5)))
        camera_h = 1.7
        p_rad = math.radians(pitch_deg)
        expected_y = (
            H / 2.0
            + camera_h / kp_dist * focal_for_y
            + math.tan(p_rad) * focal_for_y
        )
        delta = abs(y_top_water - expected_y)
        total += max(0.0, 1.0 - delta / float(tolerance_px))
        n += 1
    return (total / n) if n > 0 else 0.0


def sweep_pano_heading_offset(
    keypoints: list[dict],
    pano_water_mask: np.ndarray,
    headings_per_col: np.ndarray,
    seed_lat: float,
    seed_lon: float,
    *,
    pitch_deg: float = 0.0,
    step_deg: float = 1.0,
    tolerance_px: int = 25,
    column_match_tol_deg: float = 1.0,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Full 360°-of-offset sweep against the pano. Returns
    (best_offset_deg, cand_offsets_array, score_array).

    The recovered offset is what to ADD to ``headings_per_col`` to bring
    the pano's column-bearing labels into agreement with the satellite
    keypoints. A recovered offset of 0° means the spin captures were
    already labelled with the true geographic heading; non-zero means
    the spin needs that much correction.
    """
    n = int(360.0 / step_deg)
    cand_deg = np.linspace(0.0, 360.0, n, endpoint=False)
    scores = np.array([
        score_pano_offset_keypoints(
            keypoints, pano_water_mask, headings_per_col,
            seed_lat, seed_lon, c,
            pitch_deg=pitch_deg, tolerance_px=tolerance_px,
            column_match_tol_deg=column_match_tol_deg,
        )
        for c in cand_deg
    ], dtype=np.float32)
    best_idx = int(np.argmax(scores))
    return float(cand_deg[best_idx]), cand_deg, scores


