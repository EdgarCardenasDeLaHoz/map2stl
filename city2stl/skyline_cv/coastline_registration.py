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
    max_signal_dist_m: float = 200.0,
    use_base_y: bool = False,
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
    # Distance weighting: at default max_signal_dist_m=200 a keypoint at
    # 100 m contributes weight 1.0, at 500 m weight 0.4, at 1500 m weight
    # 0.13. Compensates for the structural flaw where far keypoints all
    # predict horizon-y (camera_h/D shrinks fast) and would otherwise
    # out-vote the few near keypoints that carry the discriminative
    # signal. Set max_signal_dist_m to a huge value to recover the
    # original equal-weight behaviour. See F-SKY-AUDIT-2026-05-24.md
    # "Underlying issue diagnosed" section for the seed_1 case study
    # that motivated this.
    weighted_sum = 0.0
    weight_total = 0.0
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
        kp_dist = float(kp.get("distance_m", 0.0))
        if kp_dist <= 1.0:
            continue
        # Distance-derived weight (1.0 for keypoints at or under
        # max_signal_dist_m; falls off linearly with distance beyond).
        weight = min(1.0, max_signal_dist_m / kp_dist)
        column = pano_water_mask[:, idx]
        water_rows = np.where(column)[0]
        if water_rows.size == 0:
            weight_total += weight  # contributes 0 to weighted_sum
            continue
        # Water/coastline keypoints sit at the horizon (top of water mask).
        # Ground-plane keypoints (e.g. OSM park boundaries) sit at the BASE
        # of the masked region instead — the bottom row where vegetation
        # still appears in that pano column. Selectable so the same scoring
        # function serves both registration channels.
        y_top_water = (
            int(water_rows.max()) if use_base_y else int(water_rows.min())
        )
        # Expected y for a sea-level point at the keypoint's distance
        # (same formula as project_lonlat_to_view's vertical term, with
        # per-pano-column focal equivalent at the local bearing).
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
        per_kp = max(0.0, 1.0 - delta / float(tolerance_px))
        weighted_sum += per_kp * weight
        weight_total += weight
    return (weighted_sum / weight_total) if weight_total > 0.0 else 0.0


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
    max_signal_dist_m: float = 200.0,
    use_base_y: bool = False,
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
            max_signal_dist_m=max_signal_dist_m,
            use_base_y=use_base_y,
        )
        for c in cand_deg
    ], dtype=np.float32)
    best_idx = int(np.argmax(scores))
    return float(cand_deg[best_idx]), cand_deg, scores


def _points_to_seed_polar(
    points: "list[tuple[float, float]]",
    seed_lat: float,
    seed_lon: float,
) -> "tuple[np.ndarray, np.ndarray]":
    """Convert (lon, lat) points to seed-relative (bearing_deg, range_m).

    Bearing is compass (0° = north, clockwise); range is planar metres
    via the local lat/lon scale. Returns (bearings, ranges) float arrays.
    Empty input → two empty arrays.
    """
    if not points:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    mlat = _METRES_PER_DEG_LAT
    mlon = _metres_per_deg_lon(seed_lat)
    lons = np.array([p[0] for p in points], dtype=np.float64)
    lats = np.array([p[1] for p in points], dtype=np.float64)
    east = (lons - seed_lon) * mlon
    north = (lats - seed_lat) * mlat
    bearings = (np.degrees(np.arctan2(east, north)) + 360.0) % 360.0
    ranges = np.hypot(east, north)
    return bearings, ranges


def pano_vegetation_base_to_lonlat(
    pano_veg_mask: "np.ndarray",
    headings_per_col: "np.ndarray",
    seed_lat: float,
    seed_lon: float,
    *,
    column_stride: int = 8,
    pitch_deg: float = 0.0,
    camera_elev_m: float = 1.7,
    max_distance_m: float = 1500.0,
    min_streak_px: int = 4,
) -> list[tuple[float, float]]:
    """Project the GROUND-CONTACT (base) of the vegetation band per column to
    sea-level ``(lon, lat)`` (F-SKY18).

    Same inverse pinhole as ``pano_water_top_to_lonlat`` but uses the
    BOTTOM-most vegetation row in each column (where greenery meets the
    ground) as the distance cue, since that's the point whose ground position
    we can estimate. Ranges share the monocular unreliability — callers should
    depth-snap the output to OSM green along bearing. ``min_streak_px`` ignores
    thin speckle columns (a few stray green pixels aren't a real region).
    """
    if pano_veg_mask is None or pano_veg_mask.size == 0:
        return []
    H, W = pano_veg_mask.shape[:2]
    if headings_per_col.size != W:
        return []
    focal_y = H / (2.0 * math.tan(math.radians(37.5)))
    cy = H / 2.0
    horizon_shift = math.tan(math.radians(pitch_deg)) * focal_y
    mlat = _METRES_PER_DEG_LAT
    mlon = _metres_per_deg_lon(seed_lat)
    out: list[tuple[float, float]] = []
    for col in range(0, W, max(1, column_stride)):
        rows = np.where(pano_veg_mask[:, col])[0]
        if rows.size < min_streak_px:
            continue
        y_base = float(rows.max())
        denom = y_base - cy - horizon_shift
        if denom <= 1.0:
            continue
        distance_m = camera_elev_m * focal_y / denom
        if not (0.0 < distance_m <= max_distance_m):
            continue
        br = math.radians(float(headings_per_col[col]))
        out.append((seed_lon + distance_m * math.sin(br) / mlon,
                    seed_lat + distance_m * math.cos(br) / mlat))
    return out


def snap_points_to_osm_along_bearing(
    pano_points: "list[tuple[float, float]]",
    osm_points: "list[tuple[float, float]]",
    seed_lat: float,
    seed_lon: float,
    *,
    max_bearing_tol_deg: float = 4.0,
) -> "list[tuple[float, float]]":
    """Correct each pano point's bad range using OSM (F-SKY18 depth-snap).

    Monocular projection gives an exact bearing but an unreliable range, so
    the pano coastline dots scatter radially off the real coast. For each pano
    point we keep its (trusted) bearing and replace its range with the range of
    the nearest OSM point IN BEARING — i.e. we slide the dot along its own ray
    until it lands on the OSM coastline. Points with no OSM feature within
    ``max_bearing_tol_deg`` are dropped (nothing to snap to on that ray).

    Returns the snapped (lon, lat) list (same order, minus unmatched points).
    """
    if not pano_points or not osm_points:
        return list(pano_points)
    pano_b, _pano_r = _points_to_seed_polar(pano_points, seed_lat, seed_lon)
    osm_b, osm_r = _points_to_seed_polar(osm_points, seed_lat, seed_lon)
    mlat = _METRES_PER_DEG_LAT
    mlon = _metres_per_deg_lon(seed_lat)
    out: list[tuple[float, float]] = []
    for b in pano_b:
        # Circular bearing distance to every OSM point.
        diff = np.abs(((osm_b - b + 180.0) % 360.0) - 180.0)
        j = int(np.argmin(diff))
        if diff[j] > max_bearing_tol_deg:
            continue
        rng = float(osm_r[j])
        br = math.radians(float(b))
        east = rng * math.sin(br)
        north = rng * math.cos(br)
        out.append((seed_lon + east / mlon, seed_lat + north / mlat))
    return out


def coastline_icp_offset(
    pano_points: "list[tuple[float, float]]",
    osm_points: "list[tuple[float, float]]",
    seed_lat: float,
    seed_lon: float,
    *,
    search_range_deg: float = 180.0,
    step_deg: float = 2.0,
    max_range_m: float = 1000.0,
    trim_frac: float = 0.2,
    init_offset_deg: float = 0.0,
) -> "tuple[float, np.ndarray, np.ndarray]":
    """Register the pano-projected coastline to the OSM coastline by a
    seed-centred rotation, returning the heading offset that best
    aligns them (F-SKY16).

    The two point sets are seed-centred, so registration is a pure
    rotation (no translation). Bearings are trusted (exact stitch
    geometry); ranges are NOT compared (the pano's radial depth is
    unreliable — that's the whole motivation). For each candidate
    offset we rotate the pano bearings, find each pano point's nearest
    OSM point IN BEARING, and accumulate a trimmed mean of the absolute
    bearing residuals. The offset minimising that residual wins.

    Robustness:
      - only OSM/pano points within ``max_range_m`` participate (far
        coastline is where the pano depth is worst and OSM is densest)
      - ``trim_frac`` drops the worst-matching fraction of pano points
        before averaging (the pano arc is noisy; OSM isn't), so a
        minority of bad correspondences can't dominate
      - search is centred on ``init_offset_deg`` (pass the URL heading
        or the keypoint-sweep result) to bias away from the 180°
        bay-symmetry mirror

    Returns ``(best_offset_deg, cand_deg, cost)`` — cost is the trimmed
    mean bearing residual in degrees (LOWER is better, unlike the
    keypoint sweep's score where higher is better). Returns
    ``(init_offset_deg, empty, empty)`` when either point set is empty.
    """
    pano_b, pano_r = _points_to_seed_polar(pano_points, seed_lat, seed_lon)
    osm_b, osm_r = _points_to_seed_polar(osm_points, seed_lat, seed_lon)
    if pano_b.size == 0 or osm_b.size == 0:
        return float(init_offset_deg), np.empty(0), np.empty(0)

    pano_mask = pano_r <= max_range_m
    osm_mask = osm_r <= max_range_m
    pano_b = pano_b[pano_mask]
    osm_b = osm_b[osm_mask]
    if pano_b.size == 0 or osm_b.size == 0:
        return float(init_offset_deg), np.empty(0), np.empty(0)

    n = int(2.0 * search_range_deg / step_deg) + 1
    cand_deg = np.linspace(
        init_offset_deg - search_range_deg,
        init_offset_deg + search_range_deg,
        n,
    )
    osm_sorted = np.sort(osm_b)

    def _trimmed_bearing_cost(offset: float) -> float:
        world_b = (pano_b + offset) % 360.0
        # Nearest OSM bearing per pano bearing via searchsorted on the
        # sorted OSM bearing array, checking both neighbours + the wrap.
        idx = np.searchsorted(osm_sorted, world_b)
        idx_lo = (idx - 1) % osm_sorted.size
        idx_hi = idx % osm_sorted.size
        d_lo = np.abs((world_b - osm_sorted[idx_lo] + 180.0) % 360.0 - 180.0)
        d_hi = np.abs((world_b - osm_sorted[idx_hi] + 180.0) % 360.0 - 180.0)
        resid = np.minimum(d_lo, d_hi)
        if trim_frac > 0.0 and resid.size > 4:
            keep = int(round(resid.size * (1.0 - trim_frac)))
            resid = np.sort(resid)[:max(1, keep)]
        return float(resid.mean())

    cost = np.array([_trimmed_bearing_cost(float(c)) for c in cand_deg],
                    dtype=np.float64)
    best_idx = int(np.argmin(cost))
    return float(cand_deg[best_idx]) % 360.0, cand_deg, cost


