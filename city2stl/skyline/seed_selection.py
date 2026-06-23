"""city2stl.skyline.seed_selection - seed proposal, screening, auto-replace.

Split out of region_pdf.py (F-CLEAN14, 2026-06-07). Generates auto-standoff
candidate camera positions, screens each with a 1-image Street View probe +
sky/contour quality gate, and swaps bad user seeds for nearby proposals. No
rendering. region_pdf re-imports these.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .pipeline import _neural_sky_and_building_masks, detect_skyline_contour
from .region_types import RegionBBox, SkylinePoint
from .region_data import (
    _bearing_deg,
    _distance_m,
    _feature_centroid,
    _feature_rings,
    _fetch_elevations,
)
from .streetview_io import _meta_location, _streetview_image, _streetview_metadata


_SCREEN_CACHE_DIR = Path(__file__).parent / "runs" / "screen_cache"

def _propose_standoff_locations(
    bbox: RegionBBox,
    high_rises: list[tuple[float, float, float]],
    osm_data: dict,
    n_max: int = 6,
) -> list[SkylinePoint]:
    """Generate candidate viewpoints from OSM building geometry.

    Algorithm:
    1. Compute height-weighted centroid of the tall-building cluster.
    2. Sample 8 compass directions × 3 standoff radii from that centroid.
    3. Score each by: angular spread of visible high-rises in the FOV cone
       (more spread = better), foreground building density within 200 m
       (fewer obstructions = better), water adjacency bonus × 1.4.
    4. Deduplicate candidates within 200 m of each other.
    5. Return top ``n_max`` as SkylinePoint(source="auto").

    The camera heading is always aimed at the cluster centroid so the skyline
    is centred in the frame. This replicates the geometry that made seed_1
    in Cartagena the only valid seed: open water, 600 m standoff, towers
    spread across the full FOV width.
    """
    if not high_rises:
        return []

    # 1. Height-weighted centroid of the tall-building cluster.
    lats = np.array([r[0] for r in high_rises], dtype=np.float64)
    lons = np.array([r[1] for r in high_rises], dtype=np.float64)
    heights = np.array([r[2] for r in high_rises], dtype=np.float64)
    w = heights / heights.sum()
    clat = float(np.dot(w, lats))
    clon = float(np.dot(w, lons))

    # 2. Precompute building centroid list for the foreground-density check.
    all_centroids: list[tuple[float, float]] = []
    for feat in osm_data.get("buildings", {}).get("features") or []:
        c = _feature_centroid(feat)
        if c is not None:
            all_centroids.append(c)

    # 3. Water-polygon vertex list for the adjacency bonus.
    water_verts: list[tuple[float, float]] = []
    for layer_key in ("waterways", "water", "waterway"):
        for feat in osm_data.get(layer_key, {}).get("features") or []:
            for ring in _feature_rings(feat):
                water_verts.extend((vlon, vlat) for (vlon, vlat) in ring)

    def _near_water(lat: float, lon: float, radius_m: float = 350.0) -> bool:
        return any(
            _distance_m(lat, lon, vlat, vlon) < radius_m
            for (vlon, vlat) in water_verts
        )

    def _foreground_density(
        lat: float, lon: float, heading: float,
        depth_m: float = 200.0, lat_width_m: float = 60.0,
    ) -> int:
        """Count building centroids in a forward rectangle."""
        count = 0
        for (blat, blon) in all_centroids:
            d = _distance_m(lat, lon, blat, blon)
            if d > depth_m:
                continue
            bear = _bearing_deg(lat, lon, blat, blon)
            delta = abs((bear - heading + 540.0) % 360.0 - 180.0)
            if d * math.sin(math.radians(delta)) < lat_width_m:
                count += 1
        return count

    def _angular_spread(
        lat: float, lon: float, heading: float, fov: float = 80.0,
    ) -> float:
        """Angular spread (degrees) of high-rises visible within ±fov/2."""
        bearings: list[float] = []
        for (hlat, hlon, _) in high_rises:
            d = _distance_m(lat, lon, hlat, hlon)
            if d < 50.0:
                continue
            bear = _bearing_deg(lat, lon, hlat, hlon)
            delta = abs((bear - heading + 540.0) % 360.0 - 180.0)
            if delta <= fov * 0.5:
                bearings.append(bear)
        if len(bearings) < 2:
            return 0.0
        bs = sorted(bearings)
        return float(bs[-1] - bs[0])

    def _osm_buildings_in_fov(
        lat: float, lon: float, heading: float,
        fov: float = 80.0, max_dist_m: float = 3000.0,
    ) -> int:
        """Count OSM building centroids in the forward FOV cone.

        F-DET2: viewpoints with fewer than _MIN_OSM_IN_FOV buildings
        in their cone will almost certainly produce nseg < 10 (Type 1
        failure). Reject at proposal time rather than after spending
        30–60 s on registration.
        """
        count = 0
        for (blat, blon) in all_centroids:
            d = _distance_m(lat, lon, blat, blon)
            if d > max_dist_m:
                continue
            bear = _bearing_deg(lat, lon, blat, blon)
            delta = abs((bear - heading + 540.0) % 360.0 - 180.0)
            if delta <= fov * 0.5:
                count += 1
        return count

    # 4. Sample 8 directions × 3 standoff radii from the cluster centroid.
    # The previous range started at 400 m which put proposed positions INSIDE
    # the dense Bocagrande building cluster — auto-seeds at that distance
    # showed buildings only as the camera shooting between adjacent towers,
    # heavily occluded. Skylines need standoff: a tower's full silhouette
    # becomes visible at roughly 5–10 × its own height (a 200 m tower needs
    # ≥1 km standoff to clear the foreground).
    standoffs_m = [900.0, 1400.0, 2000.0]
    dirs_deg = list(np.arange(0.0, 360.0, 45.0))
    m_per_deg_lat = 110_540.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(clat))

    # Extend bbox tolerance by ~600 m on each side so coastal/causeway
    # positions outside the strict city boundary are still proposed.
    lat_margin = 600.0 / m_per_deg_lat
    lon_margin = 600.0 / m_per_deg_lon

    candidates: list[tuple[float, SkylinePoint]] = []
    for dist_m in standoffs_m:
        for dir_deg in dirs_deg:
            dlat = (dist_m * math.cos(math.radians(dir_deg))) / m_per_deg_lat
            dlon = (dist_m * math.sin(math.radians(dir_deg))) / m_per_deg_lon
            clat_c = clat + dlat
            clon_c = clon + dlon

            if not (
                (bbox.south - lat_margin) <= clat_c <= (bbox.north + lat_margin)
                and (bbox.west - lon_margin) <= clon_c <= (bbox.east + lon_margin)
            ):
                continue

            # Camera faces the centroid so the cluster fills the frame.
            heading = _bearing_deg(clat_c, clon_c, clat, clon)
            spread = _angular_spread(clat_c, clon_c, heading)
            fg_count = _foreground_density(clat_c, clon_c, heading)
            # Hard reject: more than 12 building centroids inside the 200 m
            # foreground rectangle means the camera is INSIDE the urban
            # cluster — guaranteed occluded shot.
            if fg_count > 12:
                continue
            # Hard reject: nearest building too close — camera is essentially
            # looking at one wall rather than a skyline. A single tower at
            # 30 m fills the frame with facade; depth/OSM both signal "too
            # close" and the height estimator cannot see the roof. Skylines
            # need clearance: the building silhouette-top must be visible
            # above the horizon, which requires standoff >> building height.
            _nearest_m = min(
                (_distance_m(clat_c, clon_c, blat, blon)
                 for (blat, blon) in all_centroids),
                default=9999.0,
            )
            if _nearest_m < 50.0:
                continue
            # F-DET2: OSM-backed FOV gate.
            # A viewpoint facing open water/park with < 3 OSM building
            # centroids in its 80° cone will almost certainly produce
            # nseg < 10 at registration time (Type 1 failure). Reject
            # at proposal time so we never waste 30–60 s on it.
            _MIN_OSM_IN_FOV = 3
            _osm_in_fov = _osm_buildings_in_fov(clat_c, clon_c, heading)
            if _osm_in_fov < _MIN_OSM_IN_FOV:
                continue
            # Water adjacency is strongly correlated with clear skyline:
            # Bocagrande/Old Town views from across the bay are the
            # canonical "good" pose. Boost from ×1.4 to ×2.0 so water-side
            # candidates outrank inland ones even when their angular spread
            # is similar.
            water_bonus = 2.0 if _near_water(clat_c, clon_c) else 1.0

            base = spread - fg_count * 2.0
            # Bonus for more OSM buildings in FOV — prefers denser skylines.
            fov_bonus = _osm_in_fov / 10.0
            score = float(max(0.0, base + fov_bonus) * water_bonus)

            name = f"auto_{int(dir_deg):03d}_{int(dist_m):04d}m"
            candidates.append(
                (score, SkylinePoint(
                    name=name,
                    lat=clat_c,
                    lon=clon_c,
                    heading=heading,
                    source="auto",
                    score=score,
                    fov=80.0,
                    pitch=0.0,
                ))
            )

    # 5. Sort by score, then deduplicate within 200 m proximity.
    candidates.sort(key=lambda t: -t[0])
    kept: list[SkylinePoint] = []
    for _score, pt in candidates:
        if not any(
            _distance_m(pt.lat, pt.lon, k.lat, k.lon) < 200.0
            for k in kept
        ):
            kept.append(pt)
        if len(kept) >= n_max:
            break
    return kept

def _screen_score_from_image(image: np.ndarray, pitch: float = 0.0) -> tuple[float, str, bool, bool]:
    """Cache wrapper around the SegFormer screening gate.

    Screening runs two neural passes (``detect_skyline_contour`` +
    ``_neural_sky_and_building_masks``) per image — the dominant cost of both
    the location-screening and pano-capture steps. The result is a pure
    function of the image pixels + pitch, so we memoise it to disk keyed by a
    content hash. A re-run reads the cached image bytes, hits this cache, and
    skips re-inference entirely (turns ~150 s of capture into seconds).
    """
    try:
        key = hashlib.sha1(np.ascontiguousarray(image).tobytes()).hexdigest()
        key = f"{key}_{pitch:.1f}"
        cache_path = _SCREEN_CACHE_DIR / f"{key}.json"
        if cache_path.exists():
            d = json.loads(cache_path.read_text(encoding="utf-8"))
            return (float(d["score"]), str(d["label"]),
                    bool(d["is_aerial"]), bool(d["top_clipped"]))
    except Exception:
        cache_path = None

    result = _screen_score_from_image_uncached(image, pitch=pitch)

    if cache_path is not None:
        try:
            _SCREEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            score, label, is_aerial, top_clipped = result
            cache_path.write_text(json.dumps({
                "score": score, "label": label,
                "is_aerial": is_aerial, "top_clipped": top_clipped,
            }), encoding="utf-8")
        except Exception:
            pass
    return result

def _screen_score_from_image_uncached(image: np.ndarray, pitch: float = 0.0) -> tuple[float, str, bool, bool]:
    contour, sky_mask = detect_skyline_contour(image)
    h, w = image.shape[:2]

    # Aerial detection: URL pitch below -8° indicates an elevated/drone camera.
    is_aerial = pitch < -8.0

    # Gate A: at least 35% of the top 20% of the frame must be sky.
    # Catches walls, close rooftops, and tree canopy pressed to the top.
    top_h = max(1, int(h * 0.20))
    sky_frac_top = float(sky_mask[:top_h, :].sum()) / max(top_h * w, 1)

    # Gate B: contour peak-to-trough must span >=5% of frame height.
    # Catches flat horizons (ocean, flat roofscapes).
    # Threshold was 10% when using the noisy HSV sky mask; the neural
    # SegFormer contour is much smoother so real building skylines that
    # previously produced inflated range due to mask noise now produce
    # accurate (smaller) range values.  Truly flat views (open water) sit
    # at < 2% range; building silhouettes start at ~5%.
    contour_valid = contour[np.isfinite(contour)]
    contour_range_frac = (
        float(np.nanmax(contour_valid) - np.nanmin(contour_valid)) / h
        if contour_valid.size > 0
        else 0.0
    )

    # Loosened thresholds — the previous gates rejected legitimate
    # building views where the camera was up-close to a tall facade.
    # Gate A: top 20 % needs at least 20 % sky (was 35 %).
    # Gate B: contour must span >= 3 % of frame (was 5 %).
    if sky_frac_top < 0.20 or contour_range_frac < 0.03:
        return 0.0, "rejected", is_aerial, False

    # Gate C / D / E — distant-skyline checks against the SegFormer masks.
    _, building_mask_bool = _neural_sky_and_building_masks(image)
    if building_mask_bool is not None:
        building_frac = float(building_mask_bool.sum()) / max(h * w, 1)
        sky_frac_total = float(sky_mask.sum()) / max(h * w, 1)
        # Gate C: full close-up facade — building fills 80%+ of frame
        # (was 60%, which rejected legitimate close-range tower views).
        if building_frac > 0.80:
            return 0.0, "rejected", is_aerial, False
        # Gate E: enclosed space / underpass — almost no sky visible.
        if sky_frac_total < 0.08:  # was 0.12
            return 0.0, "rejected", is_aerial, False
    # Gate D: skyline must reach the upper 60 % of the frame.
    # (was upper 50 %; raised cap lets close-up tower views pass.)
    if contour_valid.size > 0 and float(np.nanmedian(contour_valid)) / h > 0.60:
        return 0.0, "rejected", is_aerial, False

    # Top-clipping detection: building tops are being cut off and the
    # camera should tilt upward. Two complementary triggers:
    #   (i) classic: the highest point of the skyline contour is within
    #       the top 8 % of the frame.
    #   (ii) sustained-high: ≥ 25 % of the contour columns are within the
    #       top 12 % of the frame. Catches the case where many tower tops
    #       are *near* but not exactly at the frame top (Cartagena seed_5
    #       Bocagrande row at 75° FOV / 90° tilt — every tower sits ~5-10 %
    #       below y=0 yet the original 8 %/single-point trigger never
    #       fires, so the spin never gets the +12° correction it needs).
    top_clipped = False
    if contour_valid.size > 0:
        min_y = float(np.nanmin(contour_valid))
        if min_y / h < 0.08:
            top_clipped = True
        else:
            high_cols = float(np.sum(contour_valid / h < 0.12)) / contour_valid.size
            if high_cols >= 0.25:
                top_clipped = True

    var = float(np.std(contour))
    span = float(np.nanmax(contour) - np.nanmin(contour))
    # Heuristic score: non-flat skyline gets higher confidence.
    # The divisor 80 and the thresholds below were calibrated when HSV sky
    # detection was in use — HSV false-positives inflated contour std by
    # roughly 2× relative to the precise SegFormer neural contour.
    # Thresholds are recalibrated downward to match neural contour quality:
    #   "good"   was 0.45 → 0.30  (~same building density, smoother contour)
    #   "medium" was 0.25 → 0.15
    score = float(np.clip((0.6 * var + 0.4 * span) / 80.0, 0.0, 1.0))
    label = "good" if score >= 0.30 else (
        "medium" if score >= 0.15 else "weak")
    return score, label, is_aerial, top_clipped

def _auto_replace_bad_seeds(
    seeds: list[SkylinePoint],
    auto_points: list[SkylinePoint],
    screened: list[dict],
    bad_score_threshold: float = 0.20,
    good_score_threshold: float = 0.35,
    proximity_radius_m: float = 2000.0,
    skip_names: "set[str] | None" = None,
) -> tuple[list[SkylinePoint], list[tuple[SkylinePoint, SkylinePoint]]]:
    """Swap user-supplied seeds that failed screening with the best-scoring
    nearby auto-proposal.

    Detection rules (any → "bad"):
      * Screening result missing (couldn't retrieve a pano).
      * ``coverage == "rejected"`` (Static API returned no usable image).
      * ``screen_score < bad_score_threshold`` (snapped pano shows
        grass / wall / enclosed space — no clear skyline).

    Replacement strategy: among already-screened auto-proposals with
    ``screen_score >= good_score_threshold``, pick the highest combined
    score of ``0.7 · screen_score + 0.3 · proximity_score`` where
    proximity_score = ``max(0, 1 - dist_to_bad_seed_m / proximity_radius_m)``.
    Preserves the user's geographic intent (prefer replacements near
    where the bad seed was aimed). The replacement adopts the bad seed's
    ``name`` so per-seed continuity (negative_seeds, anchor_offsets_deg,
    seed-level badge numbering) still applies.

    Returns
    -------
    (new_seeds, substitutions)
        ``new_seeds``: the seed list with bad entries replaced.
        ``substitutions``: list of ``(original_seed, replacement_seed)``
        tuples for diagnostic reporting.
    """
    if not seeds:
        return seeds, []

    by_name: dict[str, dict] = {}
    for row in screened:
        pt = row.get("point") or row.get("requested_point")
        if pt is None:
            continue
        by_name[str(pt.name)] = row

    used_auto_names: set[str] = set()
    new_seeds: list[SkylinePoint] = []
    substitutions: list[tuple[SkylinePoint, SkylinePoint]] = []

    skip = skip_names or set()
    for seed in seeds:
        if seed.name in skip:
            # Seed is intentionally bad (`negative_seeds`): user wants
            # the analysis-skip + bad-skyline labelling at this exact
            # location. Swapping it would waste a productive auto-
            # proposal and still produce no estimates.
            new_seeds.append(seed)
            continue
        scr = by_name.get(seed.name)
        is_bad = (
            scr is None
            or scr.get("coverage") == "rejected"
            or float(scr.get("screen_score", 0.0)) < bad_score_threshold
        )
        if not is_bad:
            new_seeds.append(seed)
            continue

        best_auto: SkylinePoint | None = None
        best_combined = -1.0
        for ap in auto_points:
            if ap.name in used_auto_names:
                continue
            auto_scr = by_name.get(ap.name)
            if auto_scr is None:
                continue
            if auto_scr.get("coverage") == "rejected":
                continue
            auto_score = float(auto_scr.get("screen_score", 0.0))
            if auto_score < good_score_threshold:
                continue
            dist_m = _distance_m(seed.lat, seed.lon, ap.lat, ap.lon)
            prox = max(0.0, 1.0 - dist_m / proximity_radius_m)
            combined = 0.7 * auto_score + 0.3 * prox
            if combined > best_combined:
                best_combined = combined
                best_auto = ap

        if best_auto is None:
            # No usable replacement; keep the original bad seed so the
            # report still shows the user-intended location (and gives
            # diagnostic data about why it failed).
            new_seeds.append(seed)
            continue

        used_auto_names.add(best_auto.name)
        # Effective replacement preserves seed name + fov + pitch; takes
        # lat/lon/heading/pano_id from the auto-proposal that survived
        # screening.
        replacement = SkylinePoint(
            name=seed.name,
            lat=best_auto.lat,
            lon=best_auto.lon,
            heading=best_auto.heading,
            source="auto-replaced",
            score=best_auto.score,
            fov=seed.fov,
            pitch=seed.pitch,
            pano_id=best_auto.pano_id,
        )
        new_seeds.append(replacement)
        substitutions.append((seed, replacement))

    return new_seeds, substitutions

def _screen_locations(
    points: list[SkylinePoint],
    api_key: str,
    max_snap_m: float = 200.0,
) -> list[dict]:
    """Screen each candidate location.

    For seeds with a pano_id, the API returns the exact pano (no snapping).
    For autos (and seeds without pano_id), the API snaps to the nearest pano;
    we read the snapped lat/lon from the metadata response and reject points
    where the snap is farther than ``max_snap_m`` from the requested location
    — those produce images that don't match the OSM cone we'd draw.

    The returned point in each row is rebound to the snapped location so the
    mini-map and any downstream projection see the actual camera position.
    """
    if points:
        elev_lookup = _fetch_elevations([(p.lat, p.lon) for p in points])
    else:
        elev_lookup = []

    screened: list[dict] = []
    for idx, point in enumerate(points):
        try:
            meta = _streetview_metadata(
                api_key,
                point.lat,
                point.lon,
                point.heading,
                fov=point.fov,
                pitch=point.pitch,
                pano_id=point.pano_id,
            )
            status = str(meta.get("status", "UNKNOWN"))
        except Exception as exc:
            screened.append({
                "point": point,
                "status": "ERROR",
                "screen_score": 0.0,
                "coverage": "weak",
                "image": None,
                "error": str(exc),
            })
            continue

        if status != "OK":
            screened.append({
                "point": point,
                "status": status,
                "screen_score": 0.0,
                "coverage": "weak",
                "image": None,
                "error": None,
            })
            continue

        # Rebind to the snapped location so the mini-map and bearing diagnostics
        # match where the camera actually is.
        snap = _meta_location(meta)
        snap_dist_m = (
            _distance_m(point.lat, point.lon,
                        snap[0], snap[1]) if snap else 0.0
        )
        effective_point = point
        if snap is not None:
            effective_point = SkylinePoint(
                name=point.name,
                lat=snap[0],
                lon=snap[1],
                heading=point.heading,
                source=point.source,
                score=point.score,
                fov=point.fov,
                pitch=point.pitch,
                pano_id=point.pano_id or str(
                    meta.get("pano_id") or "") or None,
            )

        # Drop autos that snapped too far — the captured image won't match the
        # heading/cone we picked, which is the bug behind unusable auto images.
        if effective_point.source == "auto" and snap_dist_m > max_snap_m:
            screened.append({
                "point": point,
                "effective_point": effective_point,
                "status": "SNAPPED_TOO_FAR",
                "snap_distance_m": snap_dist_m,
                "screen_score": 0.0,
                "coverage": "rejected",
                "image": None,
                "error": None,
            })
            continue

        image = _streetview_image(
            api_key,
            effective_point.lat,
            effective_point.lon,
            effective_point.heading,
            fov=effective_point.fov,
            pitch=effective_point.pitch,
            pano_id=effective_point.pano_id,
        )
        if image is None:
            screened.append({
                "point": effective_point,
                "status": "IMAGE_FAIL",
                "screen_score": 0.0,
                "coverage": "weak",
                "image": None,
                "error": None,
            })
            continue

        score, label, is_aerial, top_clipped = _screen_score_from_image(
            image, pitch=effective_point.pitch)

        # Pitch correction: if building tops are clipped and we're not already
        # looking steeply upward, re-request the same pano with +12° pitch.
        # This catches Photo Spheres captured with a slight downward tilt where
        # the tallest towers extend above the frame.
        if top_clipped and effective_point.pitch < 8.0 and not is_aerial:
            corrected_pitch = min(effective_point.pitch + 12.0, 15.0)
            corrected_image = _streetview_image(
                api_key,
                effective_point.lat,
                effective_point.lon,
                effective_point.heading,
                fov=effective_point.fov,
                pitch=corrected_pitch,
                pano_id=effective_point.pano_id,
                pano_only=effective_point.pano_id is not None,
            )
            if corrected_image is not None:
                c_score, c_label, c_aerial, _ = _screen_score_from_image(
                    corrected_image, pitch=corrected_pitch)
                if c_label != "rejected":
                    image = corrected_image
                    score, label, is_aerial = c_score, c_label, c_aerial
                    effective_point = SkylinePoint(
                        name=effective_point.name,
                        lat=effective_point.lat,
                        lon=effective_point.lon,
                        heading=effective_point.heading,
                        source=effective_point.source,
                        score=effective_point.score,
                        fov=effective_point.fov,
                        pitch=corrected_pitch,
                        pano_id=effective_point.pano_id,
                    )

        # Heading-rotation retry: the URL-provided heading often points the
        # camera at trees / walls / open water rather than the actual skyline.
        # When the initial heading is rejected, try ±90° and 180° offsets to
        # find an orientation that captures buildings. This is purely a
        # screening-recovery — the full 360° spin pass later doesn't depend
        # on this and runs every view regardless.
        if label == "rejected" and point.source == "seed" and not is_aerial:
            for offset_deg in (-90.0, 90.0, 180.0):
                retry_heading = (effective_point.heading + offset_deg) % 360.0
                retry_image = _streetview_image(
                    api_key,
                    effective_point.lat,
                    effective_point.lon,
                    retry_heading,
                    fov=effective_point.fov,
                    pitch=effective_point.pitch,
                    pano_id=effective_point.pano_id,
                    pano_only=effective_point.pano_id is not None,
                )
                if retry_image is None:
                    continue
                r_score, r_label, r_aerial, _ = _screen_score_from_image(
                    retry_image, pitch=effective_point.pitch)
                if r_label != "rejected":
                    image = retry_image
                    score, label, is_aerial = r_score, r_label, r_aerial
                    effective_point = SkylinePoint(
                        name=effective_point.name,
                        lat=effective_point.lat,
                        lon=effective_point.lon,
                        heading=retry_heading,
                        source=effective_point.source,
                        score=effective_point.score,
                        fov=effective_point.fov,
                        pitch=effective_point.pitch,
                        pano_id=effective_point.pano_id,
                    )
                    break

        elev_m = float(elev_lookup[idx]) if idx < len(elev_lookup) else 0.0
        screened.append(
            {
                "point": effective_point,
                "requested_point": point,
                "snap_distance_m": snap_dist_m,
                "status": "OK",
                "screen_score": score,
                "coverage": label,
                "is_aerial": is_aerial,
                "image": image,
                "elevation_m": elev_m,
                "error": None,
            }
        )

    screened.sort(key=lambda row: row["screen_score"], reverse=True)
    return screened
