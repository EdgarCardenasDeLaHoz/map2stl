"""Part of ``city2stl.skyline._pano.detect`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging
import math
import time
from contextlib import nullcontext

import numpy as np

from .._core.height import (
    _ground_elev_m,
    augment_estimates_with_depth,
    estimate_heights_from_registration,
)
from .._core.registration import (
    match_segments_to_buildings,
    osm_anchor_silhouettes,
    osm_sam_instance_silhouettes,
    register_view_to_osm,
)
from .._core.segmentation import _neural_sky_and_building_masks
from .._core.skyline import (
    _merge_silhouette_sources,
    detect_building_silhouettes,
    detect_buildings_from_mask,
)
from .._core.timing import _StepTimer
from .._core.types import BuildingRecord
from .._region_render._draw import overlay_spec
from ..config import current as _cfg
from ..region_data import _bearing_deg, _distance_m
from ..region_types import SeedViewRegistration, SkylinePoint

logger = logging.getLogger('city2stl.skyline._pano.detect')


def _register_views(
    seed: SkylinePoint,
    seed_elev: float,
    cached_views: list[dict],
    seed_buildings: list[BuildingRecord],
    anchor_offset: float,
    cross_view_state: dict | None,
    negative_seeds: set[str] | None,
    max_plausible_height_m: float,
    pano_osm_iou: float | None,
    pano_osm_n_keypoints: int | None,
    pano_projected_coastline: list | None,
    pano_recovered_offset: float | None,
    pano_recovered_peak: float | None,
    pano_recovered_sigma: float | None,
    pano_water_frac: float | None,
    trace=None,
    timer: _StepTimer | None = None,
    pano_projected_vegetation: list | None = None,
) -> tuple[list[SeedViewRegistration], list]:
    """Pass 2: per-view registration, height extraction, and diagnostics.

    Returns (view_rows_for_seed, estimates_for_seed).

    Numbering: each unique OSM ``feature_id`` matched anywhere in the
    seed's views is assigned a single seed-level index the first time
    it's seen; later views that hit the same building reuse the same
    number. So when a tower appears in 6 of 12 spin views, it carries
    the same badge in all of them, instead of being "tower #1" in one
    view and "tower #4" in another.
    """
    seed_index_map: dict[str, int] = {}
    _next_idx = [1]

    def _assign_seed_index(matched_segments: list[dict]) -> None:
        for seg in matched_segments:
            m = seg.get("matched_projection")
            if m is None:
                continue
            fid = str(m.get("feature_id", ""))
            if not fid:
                continue
            if fid not in seed_index_map:
                seed_index_map[fid] = _next_idx[0]
                _next_idx[0] += 1
            seg["seed_index"] = seed_index_map[fid]

    from .._core.registration import _height_proxy as _hp  # noqa: PLC0415
    from .._core.skyline import compute_building_band
    view_rows: list[SeedViewRegistration] = []
    estimates: list = []
    buildings_by_id = {b.feature_id: b for b in seed_buildings}
    is_negative_seed = bool(negative_seeds and seed.name in negative_seeds)

    def _sub(label: str):
        return timer.timed(label, level=2) if timer is not None else nullcontext()

    for cv in cached_views:
        geo_heading = cv["geo_heading"]
        image = cv["image"]
        cap = cv["cap"]
        sv_label = cv["sv_label"]
        with _sub("register_view_to_osm"):
            reg = register_view_to_osm(
                cap, seed_buildings,
                heading_search_deg=8.0, heading_step_deg=1.0,
                forced_center_deg=anchor_offset,
            )
        score = float(reg.get("best_score", float("inf")))
        n_matches = int(reg.get("n_matches", 0))
        if not math.isfinite(score) or n_matches < 3:
            continue

        is_aerial = cap.viewpoint.pitch < -8.0
        est_for_view: list = []
        if (not is_aerial
                and sv_label not in ("weak", "rejected")
                and not is_negative_seed):
            with _sub("estimate_heights_from_registration"):
                est_for_view = estimate_heights_from_registration(
                    cap,
                    reg,
                    seed_buildings,
                    camera_height_m=1.7,
                    camera_elev_m=float(seed_elev),
                    trace=trace,
                    max_plausible_height_m=max_plausible_height_m,
                    compute_floor_period=_cfg().f_sky1,
                )
            if _cfg().f_sky12 and est_for_view:
                # camera_ground - building_ground, per building. Zero unless
                # per-building terrain was actually fetched; the depth path
                # then matches the geometric path's own datum assumption.
                _offsets = {
                    b.feature_id: float(seed_elev) - _ground_elev_m(b, float(seed_elev))
                    for b in seed_buildings
                }
                with _sub("augment_estimates_with_depth (F-SKY12)"):
                    est_for_view = augment_estimates_with_depth(
                        image, est_for_view, cap.viewpoint, camera_height_m=1.7,
                        ground_offsets_m=_offsets,
                    )
            estimates.extend(est_for_view)

        # Segmentation 7-stage pipeline — labels mirror docs so the timing
        # table is interpretable end-to-end:
        # Stage 1: SegFormer-b0 forward pass (the only neural step).
        # Stage 2: morphology cleanup — happens inside Stage 1 helper.
        # Stage 3: skyline contour from sky mask.
        # Stage 4: contour-based silhouettes.
        # Stage 5: mask-based silhouettes (peak/valley split + gradient).
        # Stage 6: source merge (contour ∪ mask).
        # Stage 7: OSM-anchored re-cut.
        # (Stage 8: optional MobileSAM instance head — F-SKY5.)
        with _sub("Stage 1+2: SegFormer + morphology (sky/building)"):
            _smask, _bmask = _neural_sky_and_building_masks(image)
        # Water + vegetation masks are cache hits at this point (same
        # forward pass produced the label_map); cheap to grab so the HTML
        # report can render all four classes on the grayscale diagnostic.
        with _sub("Stage 1: SegFormer water+veg (cache hits)"):
            from .._core.segmentation import (  # noqa: PLC0415
                _neural_vegetation_mask,
                _neural_water_mask,
            )
            _wmask = _neural_water_mask(image)
            _vmask = _neural_vegetation_mask(image)
        with _sub("Stage 3+4: skyline contour silhouettes"):
            contour_segs = detect_building_silhouettes(
                reg.get("contour"), image)
        with _sub("Stage 5: detect_buildings_from_mask"):
            mask_segs = detect_buildings_from_mask(
                _bmask, contour=reg.get("contour"), image=image,
            )
        with _sub("Stage 6: merge silhouette sources"):
            segments = _merge_silhouette_sources(contour_segs, mask_segs)
        all_proj_list = reg.get("all_projections") or reg.get("projections", [])
        if int(reg.get("n_matches", 0)) >= 3:
            with _sub("Stage 7: osm_anchor_silhouettes"):
                segments = osm_anchor_silhouettes(
                    segments, all_proj_list, building_mask=_bmask)
            # F-SKY5: SAM instance head for segments F-SKY2 did not already
            # split (requires MobileSAM installed + checkpoint present).
            if _cfg().f_sky5:
                with _sub("Stage 8: osm_sam_instance_silhouettes (F-SKY5)"):
                    segments = osm_sam_instance_silhouettes(
                        image, segments, all_proj_list, building_mask=_bmask)

        if is_negative_seed:
            matched_segments = []
        else:
            _cv_scorer = None
            if cross_view_state is not None:
                try:
                    from ..cross_view import make_cross_view_scorer
                    with _sub("make_cross_view_scorer (F-SKY10)"):
                        _cv_scorer = make_cross_view_scorer(
                            cross_view_state["sat_image"],
                            cross_view_state["sat_project"],
                            image,
                        )
                except Exception as _e:
                    logger.warning(f"[cross_view] scorer build failed: {_e}")
                    _cv_scorer = None
            with _sub("match_segments_to_buildings"):
                matched_segments = match_segments_to_buildings(
                    segments, all_proj_list, buildings_by_id,
                    cross_view_scorer=_cv_scorer,
                )

        effective_heading = (geo_heading + float(reg.get("best_offset", 0.0))) % 360.0
        half_fov = seed.fov * 0.5
        contour_arr = np.asarray(reg.get("contour", []), dtype=np.float32)
        f_px = 0.5 * image.shape[1] / math.tan(math.radians(seed.fov) * 0.5)
        cy = image.shape[0] * 0.5
        pitch_rad = math.radians(cap.viewpoint.pitch)
        cam_z = float(seed_elev) + 1.7

        def _annotate_match_diagnostics(seg: dict) -> None:
            m = seg.get("matched_projection")
            if not m:
                return
            b = buildings_by_id.get(m["feature_id"])
            if b is None:
                return
            true_bearing = _bearing_deg(
                seed.lat, seed.lon, b.centroid_lat, b.centroid_lon)
            true_dist = _distance_m(
                seed.lat, seed.lon, b.centroid_lat, b.centroid_lon)
            delta = (true_bearing - effective_heading + 540.0) % 360.0 - 180.0  # noqa: B023
            proxy_h = float(_hp(b))
            x_px = int(round(float(m.get("x_px", 0))))
            pred_h = float("nan")
            if 0 <= x_px < contour_arr.size:  # noqa: B023
                y_px = float(contour_arr[x_px])  # noqa: B023
                if np.isfinite(y_px):
                    ang = math.atan((cy - y_px) / f_px) + pitch_rad  # noqa: B023
                    pred_h = max(
                        0.0,
                        cam_z + float(m.get("forward_m", 0.0)) * math.tan(ang)  # noqa: B023
                        - _ground_elev_m(b, float(seed_elev))
                    )
            near = [
                p for p in all_proj_list  # noqa: B023
                if abs(float(p.get("x_px", 0)) - float(m.get("x_px", 0))) <= 15.0
            ]
            is_closest = bool(near) and min(
                float(p.get("forward_m", 1e9)) for p in near
            ) >= float(m.get("forward_m", 0.0)) - 1.0
            seg["true_bearing_deg"] = true_bearing
            seg["true_distance_m"] = true_dist
            seg["bearing_delta_deg"] = delta
            seg["bearing_in_fov"] = abs(delta) <= half_fov  # noqa: B023
            seg["height_proxy_m"] = proxy_h
            seg["predicted_height_m"] = pred_h
            seg["is_closest_in_bin"] = is_closest
            seg["height_tag_m"] = (
                float(b.height_tag_m) if b.height_tag_m is not None else None)

        with _sub("annotate match diagnostics"):
            for seg in matched_segments:
                _annotate_match_diagnostics(seg)

        # Post-match cross-verification rescue
        _rescue_t0 = time.perf_counter()
        n_swapped = 0
        for seg in matched_segments:
            m = seg.get("matched_projection")
            if m is None:
                continue
            diags = seg.get("match_diagnostics") or []
            if len(diags) < 2:
                continue
            cur_fwd = float(m.get("forward_m", 1e9))
            cur_pred_h = float(seg.get("predicted_height_m", float("nan")))
            cur_in_fov = bool(seg.get("bearing_in_fov", True))
            cur_is_closest = bool(seg.get("is_closest_in_bin", True))
            cur_height_implausible = (
                not np.isfinite(cur_pred_h)
                or cur_pred_h > max_plausible_height_m * 1.25
                or cur_pred_h < 1.5
            )
            needs_swap = (
                (not cur_in_fov)
                or cur_height_implausible
                or (not cur_is_closest)
            )
            if not needs_swap:
                continue
            cur_fid = str(m.get("feature_id", ""))
            best_alt = None
            best_alt_fid: str | None = None
            for d in diags:
                alt_fid = str(d.get("feature_id", ""))
                if alt_fid == cur_fid:
                    continue
                alt_p = next(
                    (p for p in all_proj_list
                     if str(p.get("feature_id", "")) == alt_fid),
                    None,
                )
                if alt_p is None:
                    continue
                alt_fwd = float(alt_p.get("forward_m", 1e9))
                if alt_fwd >= cur_fwd - 1.0:
                    continue
                alt_b = buildings_by_id.get(alt_fid)
                if alt_b is None:
                    continue
                alt_proxy_h = float(_hp(alt_b))
                if alt_proxy_h < 5.0:
                    continue
                alt_true_bearing = _bearing_deg(
                    seed.lat, seed.lon,
                    alt_b.centroid_lat, alt_b.centroid_lon)
                alt_delta = (alt_true_bearing - effective_heading + 540.0) % 360.0 - 180.0
                if abs(alt_delta) > half_fov:
                    continue
                alt_x_px = int(round(float(alt_p.get("x_px", 0))))
                alt_pred_h = float("nan")
                if 0 <= alt_x_px < contour_arr.size:
                    y_px = float(contour_arr[alt_x_px])
                    if np.isfinite(y_px):
                        ang = math.atan((cy - y_px) / f_px) + pitch_rad
                        alt_pred_h = max(
                            0.0,
                            cam_z + alt_fwd * math.tan(ang)
                            - _ground_elev_m(alt_b, float(seed_elev))
                        )
                if (not np.isfinite(alt_pred_h)
                        or alt_pred_h > max_plausible_height_m
                        or alt_pred_h < 1.5):
                    continue
                if best_alt is None or alt_fwd < float(best_alt.get("forward_m", 1e9)):
                    best_alt = alt_p
                    best_alt_fid = alt_fid
            if best_alt is not None and best_alt_fid != cur_fid:
                already_claimed = any(
                    s is not seg
                    and s.get("matched_projection") is not None
                    and str(s["matched_projection"].get("feature_id", "")) == best_alt_fid
                    for s in matched_segments
                )
                if not already_claimed:
                    seg["matched_projection_pre_correction"] = m
                    seg["matched_projection"] = best_alt
                    seg["match_corrected"] = True
                    _annotate_match_diagnostics(seg)
                    n_swapped += 1
        if n_swapped:
            logger.info(f"[cross_verify] {cap.viewpoint.name}: "
                        f"corrected {n_swapped} match(es) via post-hoc rescue")
        if timer is not None:
            timer.record("post-match cross-verify rescue",
                         time.perf_counter() - _rescue_t0, level=2)

        # View-level cross-checks (heading consistency + multi-building)
        deltas: list[float] = []
        wide_segs: list[dict] = []
        for seg in matched_segments:
            m = seg.get("matched_projection")
            if m is None:
                continue
            bd = seg.get("bearing_delta_deg")
            if bd is not None and np.isfinite(bd):
                deltas.append(float(bd))
            seg_w = float(seg["x_right"]) - float(seg["x_left"])
            proj_w = max(1.0,
                         float(m.get("x_right_px", m.get("x_px", 0))) -
                         float(m.get("x_left_px", m.get("x_px", 0))))
            width_ratio = seg_w / proj_w
            others = []
            for p in all_proj_list:
                if str(p.get("feature_id", "")) == str(m.get("feature_id", "")):
                    continue
                px = float(p.get("x_px", -1))
                if float(seg["x_left"]) <= px <= float(seg["x_right"]):
                    others.append(p)
            seg["seg_width_px"] = seg_w
            seg["proj_width_px"] = proj_w
            seg["width_ratio"] = width_ratio
            seg["covered_other_projs"] = len(others)
            if width_ratio >= 2.5 or len(others) >= 2:
                seg["multi_building_candidate"] = True
                wide_segs.append(seg)
        if len(deltas) >= 3:
            med = float(np.median(deltas))
            res = [abs(d - med) for d in deltas]
            mad = float(np.median(res))
            if abs(med) > 5.0 or mad > 15.0:
                logger.info(f"[heading_consistency] {cap.viewpoint.name}: "
                            f"median bearing_delta={med:+.1f}° "
                            f"MAD={mad:.1f}° n={len(deltas)} "
                            f"— {'heading offset may be biased' if abs(med) > 5.0 else 'matches scattered'}")
        if wide_segs:
            ratios = [f"{int(s.get('width_ratio', 0))}×" for s in wide_segs]
            covered = [str(s.get('covered_other_projs', 0)) for s in wide_segs]
            logger.info(f"[multi_building] {cap.viewpoint.name}: "
                        f"{len(wide_segs)} wide segment(s) "
                        f"(width_ratios={','.join(ratios)} "
                        f"others_inside={','.join(covered)})")

        # Sanity check on each matched segment's base_y vs the OSM
        # building's geometric expected ground row. The expected base
        # for a building at distance ``d`` from a camera at elevation
        # ``cam_z`` and pitch ``pitch_rad`` is:
        #     y_base ≈ H/2 + (cam_z - b.terrain_elev) / d * focal
        #              + tan(pitch) * focal
        # The previous always-on cap clipped legitimate bbox bases that
        # extended a normal amount below the horizon (distant buildings'
        # bases naturally sit a few px below cy when the camera sits a
        # bit above land), producing visually-cut-off boxes. Now we cap
        # only when the mask base sits more than ``hard_clip_slack_px``
        # below the expected — that's the beach-extension-through-bbox
        # failure mode, where the mask plus hole-fill bleed down to the
        # waterline. Normal mask bases pass through untouched.
        hard_clip_slack_px = max(80, int(image.shape[0] * 0.18))
        for seg in matched_segments:
            m = seg.get("matched_projection")
            if m is None:
                continue
            dist = float(m.get("forward_m", 0.0))
            if dist < 10.0:
                continue
            b = buildings_by_id.get(m.get("feature_id"))
            terrain_z = (_ground_elev_m(b, float(seed_elev))
                         if b is not None else float(seed_elev))
            expected_base_y = (
                cy
                + (cam_z - terrain_z) / dist * f_px
                + math.tan(pitch_rad) * f_px
            )
            expected_base_y = max(0, min(image.shape[0] - 1, int(expected_base_y)))
            cur_base = int(seg.get("base_y", expected_base_y))
            if cur_base > expected_base_y + hard_clip_slack_px:
                seg["base_y"] = expected_base_y + hard_clip_slack_px
                seg["base_y_capped_geom"] = True

        _assign_seed_index(matched_segments)
        with _sub("registration overlay + band"):
            spec = overlay_spec(reg, matched_segments)
            band = compute_building_band(_bmask, slack_px=20)
        view_rows.append(
            SeedViewRegistration(
                seed_name=seed.name,
                seed_lat=seed.lat,
                seed_lon=seed.lon,
                heading=geo_heading,
                fov=seed.fov,
                registration_score=score,
                best_offset=float(reg.get("best_offset", 0.0)),
                estimates_count=len(est_for_view),
                matched_segments=matched_segments,
                image=None,
                overlay_spec=spec,
                is_aerial=is_aerial,
                iou=float(reg.get("best_iou", 0.0)),
                band_y=band,
                is_negative=is_negative_seed,
                building_mask=_bmask,
                sky_mask=_smask,
                water_mask=_wmask,
                vegetation_mask=_vmask,
                raw_image=image,
                pano_osm_iou=pano_osm_iou,
                pano_osm_n_keypoints=pano_osm_n_keypoints,
                pano_recovered_offset_deg=pano_recovered_offset,
                pano_recovered_peak=pano_recovered_peak,
                pano_recovered_sigma=pano_recovered_sigma,
                pano_water_frac=pano_water_frac,
                pano_projected_coastline=pano_projected_coastline,
                pano_projected_vegetation=pano_projected_vegetation,
                view_estimates=list(est_for_view) if est_for_view else None,
            )
        )

    return view_rows, estimates
