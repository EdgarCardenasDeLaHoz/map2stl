"""Image-level cross-view registration via coastline alignment (F-SKY11).

Per-building colour matching (F-SKY10) was fragile: a 14×N px street-view
roof strip vs a 25×25 px satellite roof crop is too small a signal to
distinguish a glass tower from any other glass tower, and OSM/MS polygon
position drift further confounds the match.

The coastline is the right registration signal because:

  * **Unambiguous and shared.** Water vs land is a binary, high-contrast
    feature that exists in BOTH the satellite (top) and street view
    (side) without depending on building positions, heights, or any
    ML model trained on a specific texture.
  * **Robust to OSM drift.** The ESRI satellite mosaic is geo-referenced
    accurately; the coastline traced from it is in absolute lat/lon and
    doesn't inherit OSM's 2-10 m polygon noise.
  * **Solves the actual problem.** The joint anchor optimizer's job is
    to find the heading where the camera is *actually* pointing.
    Aligning the street-view water/land boundary against the satellite
    water/land boundary at every candidate heading is exactly that
    problem, expressed without building polygons in the loop.

The signal we extract is a **radial water signature**: for each azimuth
bearing θ from the seed (0..360°), what's the great-circle distance to
the first non-water pixel? That's a 1-D array of length 360 derived
purely from the satellite water mask + the seed lat/lon. It's the
shape of the local "water visible from this point" function.

We compute the same signature *from the street view* by reading the
SegFormer water class along the bottom of the frame — each column maps
to a known bearing (via heading + per-column FOV angle), and "water
present at bottom" vs "land present at bottom" tells us where the local
water boundary is from the camera's POV.

Aligning the two signatures by sliding the SV signature over the
satellite signature gives the best heading. The score curve is a direct
diagnostic: a sharp peak means a confident heading; a flat curve means
the view doesn't have enough coastline structure to pin a heading
(common for inland views).

This is meant to be A) inspectable in the demo PDF and B) usable as an
independent verification of the existing joint-anchor IoU optimizer's
output. When the two disagree, the coastline alignment is the more
trustworthy answer for water-adjacent seeds.

Public surface:
  detect_sat_water_mask          — HSV-based water detector for ESRI tiles
  build_sat_radial_signature     — 360°-of-bearing near-range water fraction
  build_sat_radial_signature_zones — concat-stacked near+far signature
  detect_coastline_keypoints     — per-bearing water→land transition points
  build_sv_radial_signature      — per-column water coverage in bottom band
  score_heading_alignment        — single-heading score (1 - MAD)
  sweep_heading                  — full search; returns (best_deg, score_curve)
  project_lonlat_to_view         — pinhole project a point at sea level into a SV
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


def build_sat_radial_signature(
    sat_water_mask: np.ndarray,
    sat_project: Callable[[float, float], tuple[float, float]],
    seed_lat: float,
    seed_lon: float,
    *,
    n_bearings: int = 360,
    near_range_m: tuple[float, float] = (10.0, 80.0),
    step_m: float = 5.0,
) -> np.ndarray:
    """For each compass bearing, the fraction of NEAR pixels (along the
    bearing line, distance in the ``near_range_m`` window) that are
    classified as water in ``sat_water_mask``. Returns a [0, 1] array of
    length ``n_bearings``.

    Why a near-range fraction instead of distance-to-first-land
    ---------------------------------------------------------
    The street-view water mask reports "is there water at the bottom of
    column x?", which corresponds to looking ~10-50 m forward from the
    camera. The satellite signature has to answer the same question:
    "if I shoot a ray from the seed in this direction, is there water
    visible immediately?". A distance-to-first-land metric breaks on
    peninsula seeds (every bearing hits its own landmass within 20 m
    of the camera, even bearings that look across a kilometre of bay
    immediately after that). The near-range fraction picks the relevant
    window: enough samples (~15 by default) to be a meaningful average,
    short enough to reflect the actual camera POV.

    Tune ``near_range_m`` higher (e.g. (50, 300)) when the camera looks
    across larger water bodies and the immediate near-camera is land.

    Returns
    -------
    np.ndarray  shape (n_bearings,), float in [0, 1].
    """
    h_img, w_img = sat_water_mask.shape[:2]
    mlat = _METRES_PER_DEG_LAT
    mlon = _metres_per_deg_lon(seed_lat)

    bearings_deg = np.linspace(0.0, 360.0, n_bearings, endpoint=False)
    bearings_rad = np.radians(bearings_deg)
    dlon_per_m = np.sin(bearings_rad) / mlon
    dlat_per_m = np.cos(bearings_rad) / mlat

    d_lo, d_hi = near_range_m
    sample_dists = np.arange(d_lo, d_hi + step_m * 0.5, step_m)
    n_samples = sample_dists.size
    out = np.zeros(n_bearings, dtype=np.float32)

    # Build a (n_samples, n_bearings) grid of sample lon/lat, then
    # convert to pixel coords via the project closure (vectorising is
    # awkward because project is a closure; per-sample cost is small).
    for ai in range(n_bearings):
        hits = 0
        valid = 0
        for d in sample_dists:
            lon = seed_lon + d * dlon_per_m[ai]
            lat = seed_lat + d * dlat_per_m[ai]
            px, py = sat_project(float(lon), float(lat))
            ix, iy = int(px), int(py)
            if 0 <= ix < w_img and 0 <= iy < h_img:
                valid += 1
                if sat_water_mask[iy, ix]:
                    hits += 1
        out[ai] = hits / valid if valid > 0 else 0.0
    return out


def build_sat_radial_signature_zones(
    sat_water_mask: np.ndarray,
    sat_project: Callable[[float, float], tuple[float, float]],
    seed_lat: float,
    seed_lon: float,
    *,
    n_bearings: int = 360,
    zones_m: tuple[tuple[float, float], ...] = ((10.0, 80.0), (150.0, 600.0)),
    step_m: float = 8.0,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Multi-zone radial water-fraction signature.

    Computes ``build_sat_radial_signature`` for each (lo, hi) range in
    ``zones_m`` and returns:
      - the *concatenated* signature of length ``n_bearings * len(zones_m)``
        (ready for direct use as the alignment input — concatenation gives
        the scorer a single 1-D vector to compare against the SV's
        per-column signature, with the SV side also stacked similarly)
      - the list of per-zone arrays for inspection / plotting

    Why two zones
    -------------
    Single-zone near-range alone is ambiguous for peninsula seeds: bearings
    looking down a bay and bearings looking across a bay both show
    water at the bottom of the SV. Adding a far-range zone breaks that
    tie — across-bay bearings hit the far shore inside 100-600 m, while
    down-bay bearings stay in water through that range.
    """
    parts: list[np.ndarray] = []
    for lo, hi in zones_m:
        parts.append(build_sat_radial_signature(
            sat_water_mask, sat_project, seed_lat, seed_lon,
            n_bearings=n_bearings, near_range_m=(lo, hi), step_m=step_m,
        ))
    stacked = np.concatenate(parts, axis=0)
    return stacked, parts


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


def build_sv_radial_signature(
    sv_water_mask: np.ndarray,
    image_width: int,
    image_height: int,
    fov_deg: float,
    heading_deg: float,
    *,
    bottom_band_frac: float = 0.15,
    presence_threshold: float = 0.20,
) -> dict:
    """Per-column "water present at the bottom of the frame" signal.

    Each column of the street view maps to a known bearing via the
    pinhole intrinsics: ``bearing = heading + atan((x - W/2) / focal)``.
    Reading the SegFormer water mask across the bottom ``bottom_band_frac``
    of the column tells us whether the camera is looking toward water
    at that bearing. The result is a sparse {bearing_deg: True/False}
    representation that we'll compare against the satellite radial
    signature.

    Returns
    -------
    dict with keys:
      ``bearings_deg`` — 1-D float array (one bearing per column).
      ``water_present`` — 1-D bool array (True where bottom band has
        ≥ ``presence_threshold`` water pixels).
      ``coverage_frac`` — 1-D float array (raw fraction per column).
    """
    h, w = sv_water_mask.shape[:2]
    focal_px = (w / 2.0) / math.tan(math.radians(fov_deg / 2.0))
    cols = np.arange(image_width, dtype=np.float32)
    angle_off = np.degrees(np.arctan((cols - image_width / 2.0) / focal_px))
    bearings = (heading_deg + angle_off) % 360.0

    y0 = int(image_height * (1.0 - bottom_band_frac))
    band = sv_water_mask[y0:image_height, :].astype(np.float32)
    cov = band.mean(axis=0) if band.size else np.zeros(image_width)
    present = cov >= presence_threshold
    return {
        "bearings_deg": bearings,
        "water_present": present,
        "coverage_frac": cov,
    }


def score_heading_alignment(
    sat_signature: np.ndarray,
    sv_alpha_bearings: np.ndarray,
    sv_water_coverage: np.ndarray,
    candidate_heading_deg: float,
) -> float:
    """Agreement score in [0, 1] between the satellite radial signature
    and the street view's per-column water coverage at a candidate
    heading.

    Both signatures are now [0, 1] fractions:
      sat_signature[θ]  = fraction-water in the satellite near-range
                          along bearing θ from the seed
      sv_water_coverage[c] = fraction-water in the bottom band of column c

    The score is 1 - mean absolute difference, after mapping each SV
    column's per-camera-frame α offset (bearing relative to the
    captured heading) onto a world-frame bearing
    ``candidate_heading_deg + α`` and looking up the satellite value.

    Caller passes ``sv_alpha_bearings`` already stripped of the
    captured heading (so this routine just adds candidate_heading_deg);
    that keeps the heading-sweep loop cheap.
    """
    if sv_alpha_bearings.size == 0 or sv_water_coverage.size == 0:
        return 0.0
    n_sat = sat_signature.size
    bearings_world = (sv_alpha_bearings + candidate_heading_deg) % 360.0
    sat_idx = (
        np.round(bearings_world * n_sat / 360.0).astype(np.int32)
        % n_sat
    )
    sat_at_columns = sat_signature[sat_idx]
    # Mean absolute difference in [0, 1] → 1 - MAD = agreement.
    mad = float(np.mean(np.abs(sv_water_coverage - sat_at_columns)))
    return 1.0 - mad


def score_heading_keypoints(
    keypoints: list[dict],
    sv_water_mask: np.ndarray,
    seed_lat: float,
    seed_lon: float,
    candidate_heading_deg: float,
    fov_deg: float,
    image_width: int,
    image_height: int,
    *,
    tolerance_px: int = 20,
    pitch_deg: float = 0.0,
) -> float:
    """Horizon-match alignment score: each coastline key point projects
    to a sub-pixel (x_kp, y_kp) in the SV at the candidate heading. A
    correctly-recovered heading puts that point at the same y where
    the SV's water mask's TOP edge sits at column x_kp — that's the
    column's actual "highest water pixel" = the visible far-shore /
    horizon. The score per keypoint falls off linearly with the
    absolute pixel difference, capped at ``tolerance_px``.

    Why this is better than the per-column-coverage delta
    -----------------------------------------------------
    The per-column coverage in the SV bottom band stays at 1.0 across
    most columns when the camera looks at water (no horizontal
    structure to discriminate headings against). The water-mask top
    edge per column, on the other hand, encodes the visible *far shore*
    geometry directly — which is exactly the feature a coastline key
    point represents. A correct heading places projected dots on top
    of the visible far-shore line in the SV; a wrong heading scatters
    them across sky or building pixels.
    """
    if not keypoints or sv_water_mask is None or sv_water_mask.size == 0:
        return 0.0
    h, w = sv_water_mask.shape[:2]
    n = 0
    total = 0.0
    for kp in keypoints:
        xy = project_lonlat_to_view(
            kp["lon"], kp["lat"], seed_lat, seed_lon,
            heading_deg=candidate_heading_deg, fov_deg=fov_deg,
            image_width=image_width, image_height=image_height,
            pitch_deg=pitch_deg,
        )
        if xy is None:
            continue
        x_kp, y_kp = xy
        x_col = int(round(x_kp))
        if not (0 <= x_col < w):
            continue
        column = sv_water_mask[:, x_col]
        water_rows = np.where(column)[0]
        if water_rows.size == 0:
            # No water in this column at all — the projected keypoint
            # shouldn't have been visible, but we're testing a wrong
            # heading. Penalise mildly.
            n += 1
            continue
        y_top_water = int(water_rows.min())
        delta = abs(y_top_water - y_kp)
        per_kp = max(0.0, 1.0 - delta / float(tolerance_px))
        total += per_kp
        n += 1
    return (total / n) if n > 0 else 0.0


def sweep_heading_keypoints(
    keypoints: list[dict],
    sv_water_mask: np.ndarray,
    seed_lat: float,
    seed_lon: float,
    original_heading_deg: float,
    image_width: int,
    image_height: int,
    *,
    fov_deg: float = 75.0,
    pitch_deg: float = 0.0,
    search_range_deg: float = 180.0,
    step_deg: float = 1.0,
    tolerance_px: int = 20,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Heading sweep using the horizon-match keypoint score (cf.
    ``score_heading_keypoints``). Returns (best_deg, cand_deg, scores).
    """
    n = int(2.0 * search_range_deg / step_deg) + 1
    cand_deg = np.linspace(
        original_heading_deg - search_range_deg,
        original_heading_deg + search_range_deg,
        n,
    )
    scores = np.array([
        score_heading_keypoints(
            keypoints, sv_water_mask, seed_lat, seed_lon, c,
            fov_deg, image_width, image_height,
            tolerance_px=tolerance_px,
            pitch_deg=pitch_deg,
        )
        for c in cand_deg
    ], dtype=np.float32)
    best_idx = int(np.argmax(scores))
    return float(cand_deg[best_idx]) % 360.0, cand_deg, scores


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


def sweep_heading(
    sat_signature: np.ndarray,
    sv_signature: dict,
    original_heading_deg: float,
    *,
    search_range_deg: float = 180.0,
    step_deg: float = 1.0,
    water_distance_threshold_m: float = 100.0,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Search candidate headings ±``search_range_deg`` around the
    captured-view heading and return (best_heading_deg, candidate_deg_array,
    score_array) so the caller can plot the full curve.

    The score is the fraction-of-columns-agree metric from
    ``score_heading_alignment``. A sharp peak in the returned curve
    means a confident heading recovery; a flat or multi-modal curve
    means the view doesn't have enough coastline structure to lock in
    a heading on its own and the matcher should fall back to the
    existing IoU optimizer.
    """
    bearings_alpha = sv_signature["bearings_deg"] - original_heading_deg
    # Score against the per-column water-coverage fraction now that the
    # satellite signature is a fraction-based metric.
    coverage = sv_signature["coverage_frac"]
    n = int(2.0 * search_range_deg / step_deg) + 1
    cand_deg = np.linspace(
        original_heading_deg - search_range_deg,
        original_heading_deg + search_range_deg,
        n,
    )
    scores = np.array([
        score_heading_alignment(sat_signature, bearings_alpha, coverage, c)
        for c in cand_deg
    ], dtype=np.float32)
    best_idx = int(np.argmax(scores))
    return float(cand_deg[best_idx]) % 360.0, cand_deg, scores


# ============================================================================
# Dense per-bearing radial signature registration (F-SKY11.3)
#
# Works in the pano's NATIVE cylindrical domain (column = bearing) instead of
# inverse-perspective-mapping to a 2-D bird's-eye canvas. Avoids the flat-
# ground assumption that breaks for tall buildings. Uses ALL 360 bearings
# (vs. ~20 keypoints in F-SKY11.1) and Pearson cross-correlation (vs. MAD,
# which is biased toward signals near their common mean).
# ============================================================================


def build_pano_radial_signature(
    pano_water_mask: np.ndarray,
    headings_per_col: np.ndarray,
    *,
    n_bearings: int = 360,
    near_range_m: tuple[float, float] = (10.0, 80.0),
    camera_h_m: float = 1.7,
    pitch_deg: float = 0.0,
    fov_deg: float = 75.0,
    view_width_px: int = 640,
    bottom_band_frac: float | None = None,
) -> np.ndarray:
    """Per-bearing water coverage from the stitched pano.

    Two modes:

    1. **Distance-matched band** (default, when ``bottom_band_frac`` is None):
       use camera geometry to pick the pano y-rows corresponding to the
       given ``near_range_m`` window. This matches the satellite signature's
       sampling distance, which is the right thing for cross-correlation.
       Sea-level pinhole: ``y = horizon_y + camera_h * focal_y / dist``.

    2. **Fixed band** (when ``bottom_band_frac`` is provided): use the bottom
       fraction of the pano regardless of distance. Simpler but tends to
       saturate at 1.0 when the camera is at sea level near water, since
       the very near-camera rows are all water in that case.

    Returns
    -------
    np.ndarray, shape (n_bearings,), float in [0, 1].
    """
    if pano_water_mask.ndim != 2:
        raise ValueError("pano_water_mask must be 2-D")
    H, W = pano_water_mask.shape
    if headings_per_col.size != W:
        raise ValueError("headings_per_col length != pano width")

    if bottom_band_frac is None:
        # Pitch-aware band: from a small margin BELOW the horizon down to
        # the bottom of the pano. Avoids the very-near-camera rows that
        # saturate on sea-level cameras, but is wide enough to be robust to
        # pitch/focal errors. The whole band always sits below the horizon,
        # which is the physically meaningful ground/water region.
        focal_y = (view_width_px / 2.0) / math.tan(math.radians(fov_deg / 2.0))
        horizon_y = H / 2.0 - math.tan(math.radians(pitch_deg)) * focal_y
        # Start ~3 px below horizon (skip the line itself, which is noisy).
        y_top = max(0, int(round(horizon_y + 3)))
        # Stop ~5% above the very bottom (avoid car-bonnet/dashboard).
        y_bot = min(H, int(round(H * 0.95)))
        if y_bot <= y_top:
            y_bot = min(H, y_top + 1)
        band = pano_water_mask[y_top:y_bot, :].astype(np.float32)
    else:
        y0 = int(H * (1.0 - bottom_band_frac))
        band = pano_water_mask[y0:H, :].astype(np.float32)
    per_col_cov = band.mean(axis=0) if band.size else np.zeros(W, dtype=np.float32)

    bearings_deg = np.linspace(0.0, 360.0, n_bearings, endpoint=False)
    bin_width = 360.0 / n_bearings
    out = np.zeros(n_bearings, dtype=np.float32)
    headings_mod = np.asarray(headings_per_col, dtype=np.float32) % 360.0

    for i, target in enumerate(bearings_deg):
        # Circular distance from each column's bearing to the target bin centre.
        d = np.abs(((headings_mod - target + 180.0) % 360.0) - 180.0)
        mask = d <= (bin_width * 0.5 + 1e-3)
        if mask.any():
            out[i] = per_col_cov[mask].mean()
        else:
            # Fall back to nearest column if no column fell in the bin.
            j = int(np.argmin(d))
            out[i] = per_col_cov[j]
    return out


def _pearson_circular_correlation(
    sig_a: np.ndarray,
    sig_b: np.ndarray,
    *,
    step_deg: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute Pearson r for every circular shift of `sig_a` against `sig_b`.

    Both signals must be 1-D arrays of equal length n covering [0°, 360°).
    Returns (cand_deg, scores) where cand_deg[i] = i * step_deg and scores[i]
    is the Pearson correlation after rotating sig_a by cand_deg[i].

    Uses FFT-based circular correlation for speed (O(n log n) total).
    """
    if sig_a.shape != sig_b.shape or sig_a.ndim != 1:
        raise ValueError("sig_a and sig_b must be 1-D arrays of equal length")
    n = sig_a.size
    if step_deg <= 0:
        raise ValueError("step_deg must be > 0")

    a = (sig_a - sig_a.mean()).astype(np.float32)
    b = (sig_b - sig_b.mean()).astype(np.float32)
    sa = a.std()
    sb = b.std()
    if sa < 1e-6 or sb < 1e-6:
        return (np.arange(n, dtype=np.float32) * (360.0 / n),
                np.zeros(n, dtype=np.float32))

    # Circular cross-correlation: ifft(fft(b) * conj(fft(a))) gives the
    # raw correlation at every integer shift in the same direction as
    # "shift a by k, compare to b". Normalise by n*sa*sb for Pearson r.
    fa = np.fft.fft(a)
    fb = np.fft.fft(b)
    raw = np.real(np.fft.ifft(fb * np.conj(fa)))
    r = raw / (n * sa * sb)

    # The native FFT step is 360/n degrees per index. If the caller wants
    # a different step, we re-sample r linearly.
    native_step = 360.0 / n
    if abs(step_deg - native_step) < 1e-6:
        cand_deg = np.arange(n, dtype=np.float32) * native_step
        return cand_deg, r.astype(np.float32)
    # Re-sample (interpolate) r onto the requested step grid.
    n_req = int(round(360.0 / step_deg))
    cand_deg = np.arange(n_req, dtype=np.float32) * step_deg
    idx_f = cand_deg / native_step
    idx0 = np.floor(idx_f).astype(np.int32) % n
    idx1 = (idx0 + 1) % n
    frac = idx_f - np.floor(idx_f)
    scores = (1.0 - frac) * r[idx0] + frac * r[idx1]
    return cand_deg, scores.astype(np.float32)


def register_by_dense_bearing_correlation(
    pano_signature: np.ndarray,
    sat_signature: np.ndarray,
    *,
    step_deg: float = 1.0,
    subpixel_refine: bool = True,
) -> tuple[float, np.ndarray, np.ndarray, float, float]:
    """Find the heading offset that aligns the pano's per-bearing radial
    signature with the satellite's. Both signatures must have the same
    length (360 entries by default), covering [0°, 360°) uniformly.

    Uses FFT-based Pearson cross-correlation across all circular shifts,
    optionally refined to sub-degree accuracy via a parabolic fit through
    the three points around the peak.

    Returns
    -------
    (best_offset_deg, cand_deg, scores, peak_value, sigma_deg)
        best_offset_deg: heading to ADD to pano column bearings to align them
                         with true north.
        cand_deg: the full 360°/step_deg candidate grid.
        scores: Pearson r at each candidate offset.
        peak_value: refined peak correlation.
        sigma_deg: estimate of peak width (lower = more confident).
    """
    cand_deg, scores = _pearson_circular_correlation(
        pano_signature, sat_signature, step_deg=step_deg)
    if scores.size == 0:
        return 0.0, cand_deg, scores, 0.0, 360.0

    best_idx = int(np.argmax(scores))
    best_offset = float(cand_deg[best_idx])
    peak_value = float(scores[best_idx])

    if subpixel_refine and 0 < best_idx < (scores.size - 1):
        y0 = float(scores[best_idx - 1])
        y1 = float(scores[best_idx])
        y2 = float(scores[best_idx + 1])
        denom = (y0 - 2.0 * y1 + y2)
        if abs(denom) > 1e-9:
            delta = 0.5 * (y0 - y2) / denom
            best_offset = float(cand_deg[best_idx] + delta * step_deg)
            peak_value = y1 - 0.25 * (y0 - y2) * delta

    # Sigma: half-width at half-maximum of the peak, expressed in degrees.
    # Fall back to score std if the peak is too noisy to fit.
    half = peak_value * 0.5
    above_half = scores >= half
    if above_half.any():
        sigma_deg = float(above_half.sum() * step_deg) / 2.0
    else:
        sigma_deg = float(scores.std())

    return float(best_offset % 360.0), cand_deg, scores, peak_value, sigma_deg
