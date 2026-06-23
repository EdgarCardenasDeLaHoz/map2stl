"""skyline._pano.detect — extracted from pano_registration.py (A2 split)."""
from __future__ import annotations
import json
import math
import os
import time
from contextlib import nullcontext
from pathlib import Path
import cv2
import numpy as np
from ..pipeline import (
    BuildingRecord,
    CapturedView,
    Viewpoint,
    _merge_silhouette_sources,
    _neural_sky_and_building_masks,
    aggregate_building_heights,
    augment_estimates_with_depth,
    detect_building_silhouettes,
    detect_buildings_from_mask,
    estimate_heights_from_registration,
    match_segments_to_buildings,
    osm_anchor_silhouettes,
    osm_sam_instance_silhouettes,
    register_view_to_osm,
)
from ..region_types import SeedViewRegistration, SkylinePoint, StitchedPanoResult
from ..region_config import (
    FLICKR_API_KEY as _FLICKR_API_KEY,
    _F_SKY1_ENABLED,
    _F_SKY11_1_ENABLED,
    _F_SKY12_ENABLED,
    _F_SKY5_ENABLED,
)
from ..region_data import _bearing_deg, _distance_m, _fetch_elevations
from ..streetview_io import _meta_location, _streetview_image, _streetview_metadata
from ..seed_selection import _screen_score_from_image
from ..region_render import _negative_seed_views, _registration_overlay

def _register_views(
    seed: "SkylinePoint",
    seed_elev: float,
    cached_views: list[dict],
    seed_buildings: list["BuildingRecord"],
    anchor_offset: float,
    cross_view_state: "dict | None",
    negative_seeds: "set[str] | None",
    max_plausible_height_m: float,
    pano_osm_iou: "float | None",
    pano_osm_n_keypoints: "int | None",
    pano_projected_coastline: "list | None",
    pano_recovered_offset: "float | None",
    pano_recovered_peak: "float | None",
    pano_recovered_sigma: "float | None",
    pano_water_frac: "float | None",
    trace=None,
    timer: "_StepTimer | None" = None,
    pano_projected_vegetation: "list | None" = None,
) -> tuple[list["SeedViewRegistration"], list]:
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

    from ..pipeline import (  # noqa: PLC0415
        _neural_sky_and_building_masks,
        _height_proxy as _hp,
        compute_building_band,
    )
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
                    compute_floor_period=_F_SKY1_ENABLED,
                )
            if _F_SKY12_ENABLED and est_for_view:
                with _sub("augment_estimates_with_depth (F-SKY12)"):
                    est_for_view = augment_estimates_with_depth(
                        image, est_for_view, cap.viewpoint, camera_height_m=1.7,
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
            from ..pipeline import (  # noqa: PLC0415
                _neural_water_mask, _neural_vegetation_mask,
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
            if _F_SKY5_ENABLED:
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
                    print(f"[cross_view] scorer build failed: {_e}")
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
            delta = (true_bearing - effective_heading + 540.0) % 360.0 - 180.0
            proxy_h = float(_hp(b))
            x_px = int(round(float(m.get("x_px", 0))))
            pred_h = float("nan")
            if 0 <= x_px < contour_arr.size:
                y_px = float(contour_arr[x_px])
                if np.isfinite(y_px):
                    ang = math.atan((cy - y_px) / f_px) + pitch_rad
                    pred_h = max(
                        0.0,
                        cam_z + float(m.get("forward_m", 0.0)) * math.tan(ang)
                        - float(getattr(b, "terrain_elev_m", 0.0))
                    )
            near = [
                p for p in all_proj_list
                if abs(float(p.get("x_px", 0)) - float(m.get("x_px", 0))) <= 15.0
            ]
            is_closest = bool(near) and min(
                float(p.get("forward_m", 1e9)) for p in near
            ) >= float(m.get("forward_m", 0.0)) - 1.0
            seg["true_bearing_deg"] = true_bearing
            seg["true_distance_m"] = true_dist
            seg["bearing_delta_deg"] = delta
            seg["bearing_in_fov"] = abs(delta) <= half_fov
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
            best_alt_fid: "str | None" = None
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
                            - float(getattr(alt_b, "terrain_elev_m", 0.0))
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
            print(
                f"[cross_verify] {cap.viewpoint.name}: "
                f"corrected {n_swapped} match(es) via post-hoc rescue"
            )
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
                print(
                    f"[heading_consistency] {cap.viewpoint.name}: "
                    f"median bearing_delta={med:+.1f}° "
                    f"MAD={mad:.1f}° n={len(deltas)} "
                    f"— {'heading offset may be biased' if abs(med) > 5.0 else 'matches scattered'}"
                )
        if wide_segs:
            ratios = [f"{int(s.get('width_ratio', 0))}×" for s in wide_segs]
            covered = [str(s.get('covered_other_projs', 0)) for s in wide_segs]
            print(
                f"[multi_building] {cap.viewpoint.name}: "
                f"{len(wide_segs)} wide segment(s) "
                f"(width_ratios={','.join(ratios)} "
                f"others_inside={','.join(covered)})"
            )

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
            terrain_z = float(getattr(b, "terrain_elev_m", 0.0)) if b is not None else 0.0
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
            overlay = _registration_overlay(image, reg, matched_segments=matched_segments)
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
                image=overlay,
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

def _smooth_matches_across_views(
    seed_view_rows: list["SeedViewRegistration"],
    min_popularity_swap: int = 2,
) -> None:
    """Promote popular OSM matches across a seed's views.

    Walks every matched segment in every view of a single seed. For each
    segment whose matched feature_id was chosen *only* by that one view
    (popularity 1), inspect its ``match_diagnostics`` for runner-up
    candidates. If a runner-up has popularity ≥ ``min_popularity_swap``
    elsewhere in the seed, swap the segment's match to that candidate —
    the per-view matcher dissented from a consensus that was reached
    from several other angles, so the cross-view majority almost
    certainly named the correct OSM polygon.

    Operates in-place on the ``matched_segments`` list of each view.
    Does not modify segments without a current match. Logs each swap
    so the diagnostic page can attribute changed badges to this pass.
    """
    fid_popularity: dict[str, int] = {}
    for sv in seed_view_rows:
        seen_in_view: set[str] = set()
        for seg in sv.matched_segments or []:
            m = seg.get("matched_projection")
            if not m:
                continue
            fid = str(m.get("feature_id", ""))
            if not fid or fid in seen_in_view:
                continue
            seen_in_view.add(fid)
            fid_popularity[fid] = fid_popularity.get(fid, 0) + 1

    swap_count = 0
    for sv in seed_view_rows:
        for seg in sv.matched_segments or []:
            m = seg.get("matched_projection")
            if not m:
                continue
            cur_fid = str(m.get("feature_id", ""))
            if fid_popularity.get(cur_fid, 0) >= min_popularity_swap:
                continue
            best_alt_fid = None
            best_alt_pop = 0
            for d in seg.get("match_diagnostics", []) or []:
                alt_fid = str(d.get("feature_id", ""))
                if not alt_fid or alt_fid == cur_fid:
                    continue
                pop = fid_popularity.get(alt_fid, 0)
                if pop >= min_popularity_swap and pop > best_alt_pop:
                    best_alt_pop = pop
                    best_alt_fid = alt_fid
            if best_alt_fid is None:
                continue
            for d in seg.get("match_diagnostics", []) or []:
                if str(d.get("feature_id", "")) == best_alt_fid:
                    seg["matched_projection_pre_smoothing"] = m
                    seg["matched_projection"] = {
                        "feature_id": best_alt_fid,
                        "x_px": d.get("x_px", m.get("x_px", 0)),
                        "x_left_px": d.get("x_left_px", m.get("x_left_px", 0)),
                        "x_right_px": d.get("x_right_px", m.get("x_right_px", 0)),
                        "forward_m": d.get("forward_m", m.get("forward_m", 0.0)),
                    }
                    seg["match_smoothed"] = True
                    swap_count += 1
                    break

    if swap_count:
        print(f"[smooth_matches] swapped {swap_count} dissenting per-view "
              f"match(es) to seed-level popular candidates")
        # Post-swap dedup (per view): the swap can leave two segments in
        # the same view pointing at the same OSM feature_id (both were
        # dissenters with the same popular alternative). Keep the
        # segment whose original match best agreed with the swapped
        # candidate — measured by the candidate's combined score in
        # ``match_diagnostics`` — and clear the losers. Mirrors the
        # F-SKY6 one-to-one constraint enforced by the per-view matcher.
        dedup_dropped = 0
        for sv in seed_view_rows:
            claimants: dict[str, list[dict]] = {}
            for seg in sv.matched_segments or []:
                m = seg.get("matched_projection")
                if not m:
                    continue
                fid = str(m.get("feature_id", ""))
                if fid:
                    claimants.setdefault(fid, []).append(seg)
            for fid, segs in claimants.items():
                if len(segs) <= 1:
                    continue
                def _score(s):
                    for d in s.get("match_diagnostics", []) or []:
                        if str(d.get("feature_id", "")) == fid:
                            return float(d.get("combined", 0.0))
                    return float(s.get("matched_combined", 0.0))
                segs.sort(key=_score, reverse=True)
                for loser in segs[1:]:
                    loser["matched_projection_pre_dedup"] = loser.get(
                        "matched_projection")
                    loser["matched_projection"] = None
                    loser.pop("seed_index", None)
                    dedup_dropped += 1
        if dedup_dropped:
            print(f"[smooth_matches] dedup cleared {dedup_dropped} duplicate "
                  f"match(es) created by the swap")

        # Rebuild seed_index across the seed so renamed buildings get
        # consistent badge numbers + colours. Walk views in capture
        # order; within each view walk segments left-to-right (already
        # the splitter's order). First-seen fid gets the lowest index.
        rebuilt: dict[str, int] = {}
        next_idx = 1
        for sv in seed_view_rows:
            for seg in sv.matched_segments or []:
                m = seg.get("matched_projection")
                if not m:
                    continue
                fid = str(m.get("feature_id", ""))
                if not fid:
                    continue
                if fid not in rebuilt:
                    rebuilt[fid] = next_idx
                    next_idx += 1
                seg["seed_index"] = rebuilt[fid]
        # Re-render the per-view registration overlay so the badge
        # numbers + colours in the image reflect the smoothed matches.
        # ``raw_image`` is the unannotated frame stashed during Pass 2.
        # Falls back to skipping the redraw when raw_image is missing
        # (older code paths), in which case the badges in the rendered
        # PNG can diverge from the badges on the map — accepted as
        # cosmetic until the upstream change lands.
        for sv in seed_view_rows:
            raw = getattr(sv, "raw_image", None)
            if raw is None:
                continue
            # Use a minimal registration dict — the contour & projections
            # were not re-validated here, so we keep them out of the
            # redraw to avoid showing stale per-view info.
            new_overlay = _registration_overlay(
                raw, {}, matched_segments=sv.matched_segments)
            # SeedViewRegistration is a frozen dataclass; use object.__setattr__
            # to mutate ``image`` after construction.
            object.__setattr__(sv, "image", new_overlay)

def _smooth_pano_matches_against_views(
    pano_result: "StitchedPanoResult",
    seed_view_rows: list["SeedViewRegistration"],
    min_popularity: int = 2,
) -> None:
    """Promote per-view popular OSM matches into the pano's match list.

    The stitched-pano matcher runs on a 360° composite and produces its
    own matched_segments. When the per-view consensus disagrees with
    one of the pano's matches (the per-view rows match feature_id A in
    several views, but the pano matched B at that bearing), the pano
    is treated as the dissenter and swapped to A if A is also a top-3
    candidate in the pano segment's ``match_diagnostics``. Mirrors the
    per-view smoothing pass for cross-view consistency.
    """
    if pano_result is None or not pano_result.matched_segments:
        return
    fid_popularity: dict[str, int] = {}
    for sv in seed_view_rows:
        seen_in_view: set[str] = set()
        for seg in sv.matched_segments or []:
            m = seg.get("matched_projection")
            if not m:
                continue
            fid = str(m.get("feature_id", ""))
            if not fid or fid in seen_in_view:
                continue
            seen_in_view.add(fid)
            fid_popularity[fid] = fid_popularity.get(fid, 0) + 1

    swap_count = 0
    for seg in pano_result.matched_segments:
        m = seg.get("matched_projection")
        if not m:
            continue
        cur_fid = str(m.get("feature_id", ""))
        if fid_popularity.get(cur_fid, 0) >= min_popularity:
            continue
        best_alt_fid = None
        best_alt_pop = 0
        for d in seg.get("match_diagnostics", []) or []:
            alt_fid = str(d.get("feature_id", ""))
            if not alt_fid or alt_fid == cur_fid:
                continue
            pop = fid_popularity.get(alt_fid, 0)
            if pop >= min_popularity and pop > best_alt_pop:
                best_alt_pop = pop
                best_alt_fid = alt_fid
        if best_alt_fid is None:
            continue
        for d in seg.get("match_diagnostics", []) or []:
            if str(d.get("feature_id", "")) == best_alt_fid:
                seg["matched_projection_pre_smoothing"] = m
                seg["matched_projection"] = {
                    "feature_id": best_alt_fid,
                    "x_px": d.get("x_px", m.get("x_px", 0)),
                    "x_left_px": d.get("x_left_px", m.get("x_left_px", 0)),
                    "x_right_px": d.get("x_right_px", m.get("x_right_px", 0)),
                    "forward_m": d.get("forward_m", m.get("forward_m", 0.0)),
                }
                seg["match_smoothed"] = True
                swap_count += 1
                break

    if swap_count:
        print(f"[smooth_matches] swapped {swap_count} pano dissenting match(es) "
              f"to per-view popular candidates")

def _multires_sam_instances(
    pano_img: np.ndarray,
    cluster_x_ranges: list[tuple[int, int]],
    band_y_top: int,
    band_y_bot: int,
    osm_centroids_in_pano: list[tuple[int, int, str]],
    refined_mask: np.ndarray,
    confidence_floor: float = 0.6,
    intersect_with_segformer: bool = True,
) -> "tuple[list[dict], float]":
    """Run MobileSAM on each multires cluster crop with OSM building
    centroids as point prompts to obtain per-instance silhouettes
    (F-SKY20).

    Each ``osm_centroids_in_pano`` entry is ``(x_pano, y_pano, fid)``.
    Only centroids that fall inside a cluster's pano-x range AND the
    band are used as prompts for that cluster. Each SAM mask above
    ``confidence_floor`` is bounded to the cluster's pano region and
    intersected with the refined SegFormer mask (so SAM bleed into
    sky/water gets clipped).

    Returns ``(instance_segments, total_inference_s)``. Instance segments
    have ``{x_left, x_right, top_y, base_y, peak_x, mid_x, source: "sam",
    sam_prompt_fid: <fid>, sam_score: <float>}`` in PANO coordinates.

    Soft fallback: when MobileSAM is unavailable returns ``([], 0.0)``
    so the pipeline still ships the multires refined mask.
    """
    try:
        from mobile_sam import sam_model_registry, SamPredictor  # noqa: PLC0415
    except Exception:
        return [], 0.0
    ckpt = os.environ.get(
        "MOBILESAM_CHECKPOINT_PATH",
        str(Path.home() / ".cache" / "mobile_sam" / "vit_t.pth"),
    )
    if not Path(ckpt).is_file():
        return [], 0.0

    try:
        model = sam_model_registry["vit_t"](checkpoint=ckpt)
        model.eval()
        predictor = SamPredictor(model)
    except Exception as exc:
        print(f"[multires_sam] load failed: {exc}")
        return [], 0.0

    import numpy as np  # noqa: PLC0415
    out: list[dict] = []
    total_t = 0.0
    for ci, (xL, xR) in enumerate(cluster_x_ranges):
        crop = pano_img[band_y_top: band_y_bot + 1, xL: xR + 1]
        if crop.size == 0:
            continue
        # OSM building centroids inside this cluster's pano range.
        prompts_in_crop: list[tuple[int, int, str]] = []
        for (gx, gy, fid) in osm_centroids_in_pano:
            if xL <= gx <= xR and band_y_top <= gy <= band_y_bot:
                prompts_in_crop.append((gx - xL, gy - band_y_top, fid))
        if not prompts_in_crop:
            continue
        try:
            t0 = time.perf_counter()
            predictor.set_image(crop)
            crop_h, crop_w = crop.shape[:2]
            cluster_refined = refined_mask[
                band_y_top: band_y_bot + 1, xL: xR + 1]
            for (px, py, fid) in prompts_in_crop:
                points = np.array([[px, py]], dtype=np.float32)
                labels = np.array([1], dtype=np.int32)
                masks, scores, _logits = predictor.predict(
                    point_coords=points,
                    point_labels=labels,
                    multimask_output=True,
                )
                best_i = int(scores.argmax())
                if float(scores[best_i]) < confidence_floor:
                    continue
                mask = masks[best_i].astype(bool)
                if intersect_with_segformer:
                    mask = mask & cluster_refined
                if not mask.any():
                    continue
                ys, xs = np.where(mask)
                if xs.size == 0:
                    continue
                # Translate cluster-crop coords to pano coords.
                out.append({
                    "x_left": int(xs.min() + xL),
                    "x_right": int(xs.max() + xL),
                    "top_y": int(ys.min() + band_y_top),
                    "base_y": int(ys.max() + band_y_top),
                    "peak_x": int(np.median(xs) + xL),
                    "mid_x": int(np.median(xs) + xL),
                    "source": "sam",
                    "sam_prompt_fid": fid,
                    "sam_score": float(scores[best_i]),
                    "cluster_idx": ci,
                })
            total_t += time.perf_counter() - t0
        except Exception as exc:
            print(f"[multires_sam] cluster {ci} predict failed: {exc}")
            continue
    return out, total_t

def _split_by_depth_discontinuity(
    refined_mask_crop: np.ndarray,
    depth_crop: np.ndarray,
    *,
    min_depth_jump: float = 0.08,
    min_run_height_px: int = 12,
) -> np.ndarray:
    """Use a depth map to break adjacent building columns that show a
    sharp depth jump — F-SKY21 instance-separation via depth.

    For each column with building pixels, compute the median depth.
    Walk left→right; when the median depth between adjacent columns
    jumps by more than ``min_depth_jump`` (depth is in [0, 1] inverse-
    relative scale), inject a 1-pixel-wide hole in the building mask
    at that column, which makes the rule-based splitter (Stage 5) emit
    two separate components instead of one.

    Returns the modified mask (out-of-place).
    """
    if refined_mask_crop is None or depth_crop is None:
        return refined_mask_crop
    if refined_mask_crop.shape[:2] != depth_crop.shape[:2]:
        return refined_mask_crop
    out = refined_mask_crop.copy()
    h_crop, w_crop = out.shape[:2]
    col_depth_median = np.full(w_crop, np.nan, dtype=np.float32)
    for x in range(w_crop):
        col_mask = out[:, x]
        if int(col_mask.sum()) < min_run_height_px:
            continue
        col_depth_median[x] = float(np.median(depth_crop[col_mask, x]))
    # Compute adjacent-column depth jumps and flag boundaries.
    last_valid = -1
    n_cuts = 0
    for x in range(w_crop):
        d = col_depth_median[x]
        if np.isnan(d):
            last_valid = -1
            continue
        if last_valid >= 0:
            prev_d = col_depth_median[last_valid]
            if abs(d - prev_d) > min_depth_jump:
                # Inject the cut at column x (clear building pixels).
                out[:, x] = False
                n_cuts += 1
                last_valid = -1
                continue
        last_valid = x
    if n_cuts:
        # Light morphology to make sure the cut survives the splitter.
        try:
            import cv2  # noqa: PLC0415
            cut_mask = (out.astype(np.uint8)) * 255
            cut_mask = cv2.morphologyEx(
                cut_mask, cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
            )
            out = cut_mask.astype(bool)
        except Exception:
            pass
    return out

def _multires_pano_refine(
    pano_img: np.ndarray,
    pano_bmask: np.ndarray,
    pano_wmask: "np.ndarray | None",
    *,
    min_cluster_width_px: int = 30,
    min_gap_px: int = 24,
    min_column_height_px: int = 30,
    fine_input_size: int = 512,
    use_depth: bool = True,
) -> "tuple[np.ndarray, list[tuple[int, int]], tuple[int, int], float]":
    """Coarse-to-fine refinement of a pano building mask (F-SKY19).

    Pipeline:
      1. Strip building pixels at or below each column's waterline
         ("buildings should not be placed on water") if a water mask
         is provided.
      2. Find connected x-clusters of "tall enough" building columns
         in the cleaned coarse mask.
      3. For each cluster, crop the pano RGB to (band_band, x_left..x_right)
         and run SegFormer at full ``fine_input_size`` on the crop —
         this dedicates the model's full receptive field to the actual
         buildings, giving sharper inter-tower gaps.
      4. Stitch the fine masks back into pano coordinates.

    Returns ``(refined_pano_mask, cluster_x_ranges, total_fine_inference_s)``.
    When SegFormer is unavailable the input mask is returned unchanged
    so the rest of the pipeline degrades gracefully.
    """
    h, w = pano_bmask.shape[:2]
    coarse = pano_bmask.copy()
    # Water cap.
    if pano_wmask is not None and pano_wmask.any():
        water_top = np.where(
            pano_wmask.any(axis=0),
            pano_wmask.argmax(axis=0),
            h,
        ).astype(np.int32)
        row_idx = np.arange(h)[:, None]
        below_water = (row_idx >= water_top[None, :])
        coarse = coarse & ~below_water
    # Column height threshold + run-length grouping → clusters.
    col_heights = coarse.sum(axis=0)
    is_bld_col = col_heights >= min_column_height_px
    runs: list[list[int]] = []
    in_run = False
    for x, v in enumerate(is_bld_col):
        if v and not in_run:
            runs.append([x, x])
            in_run = True
        elif v:
            runs[-1][1] = x
        elif in_run:
            in_run = False
    merged: list[list[int]] = []
    for run in runs:
        if merged and (run[0] - merged[-1][1]) <= min_gap_px:
            merged[-1][1] = run[1]
        else:
            merged.append(list(run))
    clusters = [(a, b) for a, b in merged if (b - a) >= min_cluster_width_px]
    if not clusters:
        return coarse, [], (0, h - 1), 0.0
    # Building band y-range (for cropping).
    rows_with = coarse.any(axis=1)
    if not rows_with.any():
        return coarse, clusters, (0, h - 1), 0.0
    ys = np.where(rows_with)[0]
    y_top = max(0, int(ys.min()) - 16)
    y_bot = min(h - 1, int(ys.max()) + 16)

    # Start from the COARSE mask, not an empty canvas. The fine SegFormer
    # pass only runs on the detected tall-tower clusters; if we started
    # from zeros, every building OUTSIDE a cluster (the shorter central
    # low-rise, isolated mid-rises) would be silently dropped even though
    # SegFormer correctly masked it in the coarse pass. Seeding from
    # coarse keeps those buildings; the per-cluster fine pass below then
    # OR-adds its sharper silhouette on top (fills glass-tower tops,
    # tightens edges) without ever removing a coarse building.
    refined = coarse.copy()
    fine_total = 0.0
    try:
        from ..pipeline import (  # noqa: PLC0415
            _ADE20K_BUILDING_CLASSES,
            _ensure_segformer,
            _segformer_device,
        )
        if not _ensure_segformer():
            return coarse, clusters, 0.0
        from transformers import SegformerImageProcessor  # noqa: PLC0415
        import torch  # noqa: PLC0415
        import torch.nn.functional as F  # noqa: PLC0415
        from PIL import Image as PILImage  # noqa: PLC0415
        from .. import pipeline as _p  # noqa: PLC0415

        model = _p._segformer_model
        processor = SegformerImageProcessor.from_pretrained(_p._SEGFORMER_MODEL_ID)
        processor.size = {"height": int(fine_input_size), "width": int(fine_input_size)}
        processor.do_resize = True

        # Optional depth predictor for F-SKY21 instance separation.
        depth_predictor = None
        if use_depth:
            try:
                from ..depth_estimation import predict_pano_depth  # noqa: PLC0415
                depth_predictor = predict_pano_depth
            except Exception as exc:
                print(f"[multires] depth unavailable: {exc}")

        depth_total = 0.0
        depth_cuts_total = 0
        for (xL, xR) in clusters:
            crop = pano_img[y_top: y_bot + 1, xL: xR + 1]
            t0 = time.perf_counter()
            pil = PILImage.fromarray(crop)
            inputs = processor(images=pil, return_tensors="pt")
            if _segformer_device != "cpu":
                inputs = {k: v.to(_segformer_device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = model(**inputs)
            upsampled = F.interpolate(
                outputs.logits, size=(crop.shape[0], crop.shape[1]),
                mode="bilinear", align_corners=False)
            labels = upsampled.squeeze(0).argmax(dim=0).cpu().numpy()
            fine_total += time.perf_counter() - t0
            fine_bld = np.isin(labels, _ADE20K_BUILDING_CLASSES)
            # F-SKY21: depth-driven instance separation. Predict depth on
            # the crop; force-cut columns where the median building-pixel
            # depth jumps sharply between neighbours so the rule-based
            # splitter emits one segment per tower instead of one merged
            # blob. Cheap (~0.5-1 s per crop on CPU).
            if depth_predictor is not None:
                try:
                    td = time.perf_counter()
                    depth_crop = depth_predictor(np.asarray(crop))
                    depth_total += time.perf_counter() - td
                    pre_count = int(fine_bld.sum())
                    fine_bld = _split_by_depth_discontinuity(
                        fine_bld, depth_crop)
                    if int(fine_bld.sum()) < pre_count:
                        depth_cuts_total += 1
                except Exception as exc:
                    print(f"[multires] depth split skipped on cluster: {exc}")
            refined[y_top: y_bot + 1, xL: xR + 1] |= fine_bld
        if depth_predictor is not None:
            print(
                f"[multires] depth refinement: {depth_total:.2f}s "
                f"(cut applied on {depth_cuts_total}/{len(clusters)} clusters)"
            )
    except Exception as exc:
        print(f"[multires] fine refinement failed: {exc}")
        return coarse, clusters, (y_top, y_bot), 0.0

    return refined, clusters, (y_top, y_bot), fine_total

def _pano_sliding_window_split(
    pano_mask: "np.ndarray | None",
    pano_image: "np.ndarray | None",
    det_fn,
    window_w: int = 360,
    stride: int = 280,
    iou_dedup: float = 0.5,
    pano_depth: "np.ndarray | None" = None,
    depth_jump_thresh: float = 0.03,
) -> list[dict]:
    """F-SKY22 — run the per-view splitter in a sliding window across the
    pano and merge results.

    ``detect_buildings_from_mask`` runs connected-component analysis
    globally and caps splits per component (``max_splits_per_component=
    24``). On a 1800-col pano where the entire skyline is one connected
    blob, that cap clips real towers and the absolute-px peak thresholds
    don't scale. Walking a window-wide slice gives each window its own
    component analysis + split budget. Dedupe by horizontal IoU since
    the windows overlap.
    """
    if pano_mask is None or pano_mask.size == 0:
        return []
    H, W = pano_mask.shape[:2]
    if W <= window_w:
        return list(det_fn(pano_mask, image=pano_image))

    segs: list[dict] = []
    x = 0
    n_windows = 0
    while True:
        x1 = min(W, x + window_w)
        m_win = pano_mask[:, x:x1]
        img_win = pano_image[:, x:x1] if pano_image is not None else None
        local = det_fn(m_win, image=img_win)
        for s in local:
            s2 = dict(s)
            s2["x_left"] = int(s["x_left"]) + x
            s2["x_right"] = int(s["x_right"]) + x
            s2["mid_x"] = int(s["mid_x"]) + x
            s2["peak_x"] = int(s["peak_x"]) + x
            segs.append(s2)
        n_windows += 1
        if x1 >= W:
            break
        x += stride

    # Dedupe overlapping segments: same tower observed in two adjacent
    # windows produces two near-identical entries. Keep the wider one
    # (more confident silhouette) when horizontal IoU >= threshold.
    segs.sort(key=lambda s: s["x_right"] - s["x_left"], reverse=True)
    kept: list[dict] = []

    def _iou(a: dict, b: dict) -> float:
        aL, aR = a["x_left"], a["x_right"]
        bL, bR = b["x_left"], b["x_right"]
        inter = max(0, min(aR, bR) - max(aL, bL))
        union = max(aR, bR) - min(aL, bL)
        return inter / union if union > 0 else 0.0

    for s in segs:
        if all(_iou(s, k) < iou_dedup for k in kept):
            kept.append(s)

    # Phantom-bbox filter: the sliding window picks up sub-threshold
    # blobs at window edges (water reflections, faint sky-edge noise).
    # Reject any segment whose mask density inside its own bbox is
    # below 15% or whose absolute mask pixel count is < 250 — real
    # towers from the SegFormer building mask are denser than that.
    filtered: list[dict] = []
    for s in kept:
        xL, xR = int(s["x_left"]), int(s["x_right"])
        yT = int(s.get("top_y", 0))
        yB = int(s.get("base_y", pano_mask.shape[0] - 1))
        if xR <= xL or yB <= yT:
            continue
        yT = max(0, min(pano_mask.shape[0] - 1, yT))
        yB = max(0, min(pano_mask.shape[0] - 1, yB))
        crop = pano_mask[yT: yB + 1, xL: xR + 1]
        if crop.size == 0:
            continue
        count = int(crop.sum())
        density = count / float(crop.size)
        if count < 250 or density < 0.15:
            continue
        filtered.append(s)

    filtered.sort(key=lambda s: s["mid_x"])

    # F-SKY24 Phase 1: depth-fused post-split. The mask-based splitter
    # can't separate adjacent towers at different DEPTHS but similar
    # heights (col_counts has no valley between them). After the main
    # splitter runs, walk each output segment; if its per-column median
    # depth has a strong jump inside it, split the segment at the jump.
    # Cheap (~per-segment slice over the depth pano).
    depth_split: list[dict] = []
    n_depth_cuts = 0
    max_grad_seen: list[float] = []  # diagnostic: per-segment max gradient
    if pano_depth is not None and pano_depth.shape[:2] == pano_mask.shape[:2]:
        for s in filtered:
            xL = int(s["x_left"])
            xR = int(s["x_right"])
            yT = max(0, int(s.get("top_y", 0)))
            yB = min(pano_mask.shape[0] - 1, int(s.get(
                "base_y", pano_mask.shape[0] - 1)))
            if xR - xL < 30 or yB <= yT:
                depth_split.append(s)
                continue
            # Per-column median depth within this segment's mask band.
            seg_mask = pano_mask[yT: yB + 1, xL: xR + 1]
            seg_depth = pano_depth[yT: yB + 1, xL: xR + 1]
            col_md = np.full(seg_mask.shape[1], np.nan, dtype=np.float32)
            for ci in range(seg_mask.shape[1]):
                col_vals = seg_depth[seg_mask[:, ci], ci]
                if col_vals.size >= 6:
                    col_md[ci] = float(np.median(col_vals))
            # Smooth and take signed gradient.
            if np.all(np.isnan(col_md)):
                depth_split.append(s)
                continue
            # Interpolate over NaN gaps so the gradient is meaningful.
            valid = ~np.isnan(col_md)
            if valid.sum() < 6:
                depth_split.append(s)
                continue
            idx = np.arange(col_md.size)
            col_md_filled = np.interp(idx, idx[valid], col_md[valid])
            # Smooth lightly then differentiate.
            try:
                from scipy.signal import find_peaks  # noqa: PLC0415
                from scipy.ndimage import gaussian_filter1d  # noqa: PLC0415
                smoothed = gaussian_filter1d(col_md_filled, sigma=2.0)
                grad = np.abs(np.diff(smoothed))
                # Diagnostic: track the max gradient inside the inner
                # band (excluding outer margin) so we can see if a
                # threshold lower than `depth_jump_thresh` would catch
                # genuine splits.
                margin = max(8, int(0.15 * (xR - xL)))
                inner = grad[margin: max(margin + 1, grad.size - margin)]
                if inner.size:
                    max_grad_seen.append(float(inner.max()))
                peaks, props = find_peaks(
                    grad, height=depth_jump_thresh, distance=margin)
                if peaks.size:
                    peaks = peaks[(peaks >= margin)
                                  & (peaks <= grad.size - margin)]
            except Exception:
                peaks = np.empty(0, dtype=np.int64)
            if peaks.size == 0:
                depth_split.append(s)
                continue
            # Split at each peak. Build sub-segments by carving the
            # original [xL, xR] range at peak offsets.
            cut_xs = sorted({int(xL + p + 1) for p in peaks})
            prev_x = xL
            for cx in cut_xs + [xR + 1]:
                if cx - prev_x < 20:  # too thin, skip
                    prev_x = cx
                    continue
                sub = dict(s)
                sub["x_left"] = int(prev_x)
                sub["x_right"] = int(cx - 1)
                sub["mid_x"] = (sub["x_left"] + sub["x_right"]) // 2
                # Use the column with peak col_counts for the new peak_x;
                # fall back to mid_x when counts are unavailable.
                inner = pano_mask[yT: yB + 1, prev_x: cx]
                if inner.size:
                    cc = inner.sum(axis=0)
                    sub["peak_x"] = int(prev_x + int(np.argmax(cc)))
                else:
                    sub["peak_x"] = sub["mid_x"]
                depth_split.append(sub)
                n_depth_cuts += 1
                prev_x = cx
            n_depth_cuts -= 1  # parent counted as 1 split
        # Re-sort after splits.
        depth_split.sort(key=lambda s: s["mid_x"])
    else:
        depth_split = filtered

    # Diagnostic: distribution of within-segment max depth-gradients.
    # Helps decide if the threshold is too high or there's just no
    # signal to cut on.
    grad_diag = ""
    if max_grad_seen:
        arr = sorted(max_grad_seen)
        n = len(arr)
        p50 = arr[n // 2]
        p90 = arr[min(n - 1, int(0.9 * n))]
        p_max = arr[-1]
        grad_diag = (
            f" | depth-grad p50={p50:.3f} p90={p90:.3f} max={p_max:.3f}"
            f" thr={depth_jump_thresh:.3f}"
        )
    print(
        f"[pano_split] sliding window: W={W} win={window_w} stride={stride} "
        f"-> {n_windows} windows, {len(segs)} raw -> {len(kept)} dedup "
        f"-> {len(filtered)} density -> {len(depth_split)} after depth "
        f"split (+{n_depth_cuts}){grad_diag}"
    )
    return depth_split

def _build_and_detect_pano(
    seed: "SkylinePoint",
    cached_views: list[dict],
    seed_buildings: list["BuildingRecord"],
    anchor_offset: float,
    spin_step_deg: float,
    prefetch_views: list[dict] | None = None,
    timer: "_StepTimer | None" = None,
    osm_green_features: "list | None" = None,
) -> "StitchedPanoResult | None":
    """Pass 3: stitch all spin views into a 360° pano and run pano-level matching.

    Returns a ``StitchedPanoResult`` on success, ``None`` on failure (the pano
    path is supplementary — failure must not break the per-view results).

    F-SKY19 (env var ``SKYLINE_CV_MULTIRES=1``): replace the per-view-
    stitched building mask with a coarse-to-fine pano refinement. The
    coarse mask is the existing per-view stitch (free, already computed);
    the fine pass runs SegFormer at full input size on cropped building
    clusters only. Cuts mask noise from per-view stitches, applies a
    water cap so boat wakes aren't tagged as buildings, and ships
    sharper per-tower silhouettes to the pano matcher.
    """
    from contextlib import nullcontext  # noqa: PLC0415

    def _sub(label: str):
        # Level-2 sub-step under the level-1 "pano detection" phase, so
        # the pano-stage work (tiled depth, OSM projection, splitter,
        # matching, bearing recovery) shows up in the timing report.
        return timer.timed(label, level=2) if timer is not None else nullcontext()

    try:
        from ..pipeline import (  # noqa: PLC0415
            stitch_pano_views,
            stitch_pano_masks,
            project_buildings_to_pano,
            detect_buildings_from_mask as _det_pano,
            match_segments_to_buildings as _match_pano,
            compute_building_band as _cbb,
            _neural_sky_and_building_masks,
            _neural_water_mask,
        )
        # Use the broader prefetch (all spin headings that returned an
        # image, including those screening rejected) when available so
        # the panorama covers a full 360° view even when only a few
        # spin directions had clear skylines. The pano matcher already
        # relies on the cached/screened views' masks for per-tower
        # accuracy; the additional prefetch views just fill out the
        # visual frame and contribute their (often empty) building
        # mask to the stitch — they can't introduce spurious matches.
        stitch_source = list(prefetch_views) if prefetch_views else list(
            cached_views)
        spin_views_for_pano: list[dict] = []
        # Late import — vegetation mask is opt-in (F-SKY18).
        try:
            from ..pipeline import _neural_vegetation_mask  # noqa: PLC0415
        except Exception:
            _neural_vegetation_mask = None
        with _sub("pano mask assembly (SegFormer cache hits/misses)"):
          # Batch the stitch_source label_maps through SegFormer in ONE
          # forward pass before the per-view mask calls below. Without
          # this, each _neural_sky_and_building_masks() call pays its own
          # transformer forward pass (12 lazy passes/seed). It's a no-op
          # when the recovery already prefetched these (non-manual seeds),
          # but for manual-anchor seeds — where we skip recovery and thus
          # its prefetch — this restores the batched fast path instead of
          # 12 separate inferences.
          from ..pipeline import prefetch_label_maps as _prefetch_lm  # noqa: PLC0415
          _prefetch_lm([cv.get("image") for cv in stitch_source
                        if cv.get("image") is not None])
          for cv in stitch_source:
            img = cv.get("image")
            if img is None:
                continue
            sky_mask_arr, bmask = _neural_sky_and_building_masks(img)
            wmask = _neural_water_mask(img)
            vmask = None
            if _neural_vegetation_mask is not None:
                try:
                    vmask = _neural_vegetation_mask(img)
                except Exception:
                    vmask = None
            spin_views_for_pano.append({
                "image": img,
                "geo_heading": (cv["geo_heading"] + anchor_offset) % 360.0,
                "building_mask": bmask,
                "water_mask": wmask,
                "sky_mask": sky_mask_arr,
                "vegetation_mask": vmask,
            })
        stitch_out = stitch_pano_views(spin_views_for_pano, seed.fov, spin_step_deg)
        mask_out = stitch_pano_masks(spin_views_for_pano, seed.fov, spin_step_deg)
        if stitch_out is None or mask_out is None:
            return None
        pano_img, pano_headings = stitch_out
        pano_bmask, pano_wmask = mask_out
        # F-SKY18 Phase 4: stitch the vegetation channel early so the
        # bearing_xcorr block can use it as an asymmetric tiebreaker against
        # 180° symmetry (the bay-coastline failure mode). Cheap concat; the
        # later stitch at composite-render time reuses the cached masks.
        try:
            from ..pipeline import stitch_pano_mask_channel as _stitch_chan_xc  # noqa: PLC0415
            pano_veg_arr_xc = _stitch_chan_xc(
                spin_views_for_pano, seed.fov, spin_step_deg, "vegetation_mask")
        except Exception:
            pano_veg_arr_xc = None
        if pano_bmask.shape[1] != pano_img.shape[1]:
            raise ValueError(
                f"pano image/mask width mismatch: "
                f"img={pano_img.shape[1]} mask={pano_bmask.shape[1]}")
        # F-SKY19 multires refinement (opt-in).
        sam_instances: list[dict] = []
        if os.environ.get("SKYLINE_CV_MULTIRES", "0").strip() in (
                "1", "true", "yes", "on"):
            refined, clusters, band_y, fine_t = _multires_pano_refine(
                pano_img, pano_bmask, pano_wmask)
            print(
                f"[multires] seed={seed.name} clusters={len(clusters)} "
                f"fine_inference={fine_t:.2f}s "
                f"refined_columns_with_building={int((refined.any(axis=0)).sum())}/"
                f"{int((pano_bmask.any(axis=0)).sum())}"
            )
            pano_bmask = refined
            # F-SKY20: MobileSAM per-instance silhouettes prompted by
            # OSM building centroids projected into the pano. Capped to
            # ``MAX_PROMPTS_PER_CLUSTER`` nearest-by-distance candidates
            # per cluster — without this, a 3000-building region produces
            # 200+ s of SAM inference (run validated this empirically).
            if os.environ.get("SKYLINE_CV_F_SKY5", "0").strip() in (
                    "1", "true", "yes", "on") and clusters:
                MAX_PROMPTS_PER_CLUSTER = 15
                _pano_projs_for_sam = project_buildings_to_pano(
                    seed_buildings, seed.lat, seed.lon, pano_headings)
                band_mid_y = (band_y[0] + band_y[1]) // 2
                # Bucket candidates per cluster, then sort by forward
                # distance (nearer first) and cap.
                per_cluster: list[list[tuple[float, int, int, str]]] = [
                    [] for _ in clusters]
                for p in _pano_projs_for_sam:
                    px = int(round(float(p.get("x_px", -1))))
                    if not (0 <= px < pano_img.shape[1]):
                        continue
                    fwd = float(p.get("forward_m", 1e9))
                    for ci, (xL, xR) in enumerate(clusters):
                        if xL <= px <= xR:
                            per_cluster[ci].append(
                                (fwd, px, band_mid_y,
                                 str(p.get("feature_id", ""))))
                            break
                osm_centroids: list[tuple[int, int, str]] = []
                for bucket in per_cluster:
                    bucket.sort(key=lambda t: t[0])
                    for (_fwd, px, py, fid) in bucket[:MAX_PROMPTS_PER_CLUSTER]:
                        osm_centroids.append((px, py, fid))
                sam_instances, sam_t = _multires_sam_instances(
                    pano_img, clusters, band_y[0], band_y[1],
                    osm_centroids, refined,
                )
                print(
                    f"[multires_sam] seed={seed.name} prompts={len(osm_centroids)} "
                    f"(capped at {MAX_PROMPTS_PER_CLUSTER}/cluster) "
                    f"instances={len(sam_instances)} inference={sam_t:.2f}s"
                )
        pano_band = _cbb(pano_bmask, slack_px=20)
        # F-SKY24 Phase 1: compute pano depth once for both the splitter's
        # depth-aware post-cut pass AND for the downstream HTML renderers
        # (depth pano + reconstruction polar plot). Cheap-ish (~5-10 s
        # CPU) but a clear win to share.
        pano_depth_arr: "np.ndarray | None" = None
        try:
            from ..depth_estimation import predict_pano_depth_tiled  # noqa: PLC0415
            t_dep = time.perf_counter()
            # Run DA2 ONLY on the building band, not the full frame. We
            # only USE depth at building-base pixels (the silhouette), so
            # the sky (top half) and far water (bottom) are wasted CPU.
            # Cropping to the band roughly halves the inference, AND the
            # band-only [0,1] normalisation no longer wastes range on the
            # huge sky↔water span — so the building depth is LESS
            # compressed (a bonus against the saturation/flatness). The
            # result is placed back into a full-size array (edge-extended
            # outside the band) so downstream base_y indexing is unchanged.
            _pi = np.asarray(pano_img)
            Hp, Wp = _pi.shape[:2]
            with _sub("pano depth (tiled DA2, band-cropped)"):
                if pano_band is not None:
                    y0, y1 = int(pano_band[0]), int(pano_band[1])
                    y0 = max(0, min(Hp - 1, y0))
                    y1 = max(y0 + 1, min(Hp - 1, y1))
                    d_crop = predict_pano_depth_tiled(_pi[y0:y1 + 1])
                    pano_depth_arr = np.empty((Hp, Wp), dtype=np.float32)
                    pano_depth_arr[y0:y1 + 1] = d_crop
                    if y0 > 0:
                        pano_depth_arr[:y0] = d_crop[0]
                    if y1 < Hp - 1:
                        pano_depth_arr[y1 + 1:] = d_crop[-1]
                else:
                    pano_depth_arr = predict_pano_depth_tiled(_pi)
            print(
                f"[pano_depth] seed={seed.name} {pano_depth_arr.shape} "
                f"band-cropped tiled-inference "
                f"{time.perf_counter() - t_dep:.2f}s"
            )
        except Exception as exc:
            print(f"[pano_depth] unavailable: {exc}")

        # F-SKY24 Phase 3: bearing recovery via silhouette × OSM
        # cross-correlation. The existing satellite-coastline recovery
        # gives the first-pass anchor; this pass refines it (or rescues
        # it when the satellite signal was weak — Chicago is the
        # canonical failure mode where the satellite anchor lands ~180°
        # off). We compute two 360-bin signals (per-degree depth-base
        # distance, per-degree nearest-OSM-building distance), find the
        # rotation that aligns them, and rebase pano_headings — but only
        # when the alignment is BOTH a big improvement over the current
        # anchor AND a distinct global optimum (guards against the
        # Cartagena failure mode where saturation makes many offsets
        # near-tie).
        #
        # NO_BUILDING_M sentinel: bearings with no building mask (sky /
        # water) or no nearby OSM building are set to a large distance
        # rather than left as NaN. This makes "I see a building here but
        # OSM says open space there" a STRONG mismatch signal (3000 vs
        # ~400 m), which is exactly what discriminates a correct
        # rotation from a saturated-tie wrong one.
        NO_BUILDING_M = 3000.0
        bearing_shift_deg = 0.0
        try:
          with _sub("bearing recovery (depth vs OSM rotation sweep)"):
            if pano_depth_arr is not None and pano_bmask is not None:
                Hb, Wb = pano_bmask.shape[:2]
                # Column lookup per integer degree.
                col_for_deg = np.empty(360, dtype=np.int32)
                for d in range(360):
                    dist = np.abs(
                        ((pano_headings - float(d) + 180.0) % 360.0)
                        - 180.0)
                    col_for_deg[d] = int(np.argmin(dist))
                # Depth silhouette with NO_BUILDING sentinel for empty
                # columns. Per column we take the median depth over the
                # LOWER half of the building-mask pixels (mode
                # "lower_median" in column_building_distance) rather than
                # the single base pixel — cuts single-pixel depth noise
                # while staying near the ground-contact distance, which
                # measurably sharpens the cross-correlation peak.
                from ..depth_estimation import (  # noqa: PLC0415
                    column_building_distance,
                )
                silh = np.full(360, NO_BUILDING_M, dtype=np.float32)
                for d in range(360):
                    col = int(col_for_deg[d])
                    dist_col = column_building_distance(
                        pano_depth_arr, pano_bmask, col,
                        scale=1450.0, mode="base")
                    if dist_col is not None:
                        silh[d] = dist_col
                # OSM-nearest per-degree from seed_buildings centroids,
                # also NO_BUILDING-filled where there's nothing nearby.
                osm_near = np.full(360, NO_BUILDING_M, dtype=np.float32)
                bucket = [float("inf")] * 360
                for b_rec in seed_buildings:
                    bb = _bearing_deg(
                        seed.lat, seed.lon,
                        b_rec.centroid_lat, b_rec.centroid_lon)
                    dd = _distance_m(
                        seed.lat, seed.lon,
                        b_rec.centroid_lat, b_rec.centroid_lon)
                    if dd <= 1500.0:
                        bi = int(round(bb)) % 360
                        if dd < bucket[bi]:
                            bucket[bi] = dd
                for d in range(360):
                    if bucket[d] < float("inf"):
                        osm_near[d] = float(bucket[d])
                # Calibrate the depth silhouette's MAGNITUDE to OSM before
                # scoring: scale the (sentinel-excluded) silhouette so its
                # median matches the OSM-nearest median. The recovery only
                # cares about the angular PATTERN, but matching the typical
                # magnitude makes the MAE dip sharp (high prominence) so a
                # confident fine-tune isn't masked by the silhouette
                # sitting at the wrong absolute level (the hardcoded-1450
                # vs OSM-anchored mismatch that made the pipeline curve
                # look flat while the report's scan looked sharp). Pure
                # internal-to-recovery calibration; does NOT change the
                # reported depth distances.
                _real_s = silh[silh < NO_BUILDING_M]
                _real_o = osm_near[osm_near < NO_BUILDING_M]
                if _real_s.size and _real_o.size:
                    _sm = float(np.median(_real_s))
                    _om = float(np.median(_real_o))
                    if _sm > 1e-6:
                        silh = np.where(
                            silh < NO_BUILDING_M, silh * (_om / _sm), silh
                        ).astype(np.float32)
                # Full-vector MAE at every rotation (no NaN now — both
                # arrays are dense thanks to the sentinel).
                scores = np.empty(360, dtype=np.float32)
                for off in range(360):
                    rs = np.roll(silh, off)
                    scores[off] = float(np.mean(np.abs(rs - osm_near)))
                pre_mae = float(scores[0])  # offset 0 = current anchor
                best_off = int(np.argmin(scores))
                best_mae = float(scores[best_off])
                shift = best_off if best_off <= 180 else best_off - 360
                improve = (pre_mae - best_mae) / max(pre_mae, 1e-6)
                # Gate on the QUALITY OF THE DESTINATION, not the shift
                # magnitude or the (possibly random) starting anchor. The
                # silhouette is median-calibrated to OSM above, so
                # best_mae is "average metres of depth↔OSM disagreement
                # AFTER rotating to the recovered bearing". A low best_mae
                # means the recovered bearing genuinely aligns depth with
                # OSM and is therefore trustworthy — regardless of how the
                # initial anchor was set or how far we had to rotate. A
                # high best_mae means even the best offset is a poor fit
                # (ambiguous / wrong), so we keep the existing anchor.
                # Empirically ~750 m separates good recoveries (Cartagena
                # 301-732, Chicago 377-688, Miami 356-665) from poor ones
                # (808-1391). ``improve > 0`` is automatic (best is the
                # argmin) but kept as a guard.
                MAE_CEILING_M = 750.0

                # F-SKY18 Phase 4/5: vegetation-agreement signal. Computed
                # BEFORE the apply decision so a strong ``favors_mirror`` can
                # veto an aggressive rotation (the auto_270_2000m failure mode
                # — 52% MAE improve looked confident, vegetation disagreed).
                v_best: "float | None" = None
                v_mirror: "float | None" = None
                veg_verdict = "no_data"
                try:
                    if pano_veg_arr_xc is not None and osm_green_features:
                        from ..osm_water import (  # noqa: PLC0415
                            clip_to_radius as _clip_xc_g,
                            sample_green_points as _samp_xc_g,
                        )
                        _g_pts = _samp_xc_g(
                            _clip_xc_g(osm_green_features,
                                       (seed.lon, seed.lat), 1500.0),
                            spacing_m=20.0,
                        )
                        if _g_pts:
                            pano_veg_per_deg = np.zeros(360, dtype=bool)
                            for d in range(360):
                                col = int(col_for_deg[d])
                                pano_veg_per_deg[d] = bool(
                                    pano_veg_arr_xc[:, col].any())
                            osm_g_per_deg = np.zeros(360, dtype=bool)
                            for (glon, glat) in _g_pts:
                                gb = _bearing_deg(
                                    seed.lat, seed.lon, glat, glon)
                                osm_g_per_deg[int(round(gb)) % 360] = True
                            v_best = float(
                                (np.roll(pano_veg_per_deg, best_off)
                                 == osm_g_per_deg).mean())
                            v_mirror = float(
                                (np.roll(pano_veg_per_deg,
                                         (best_off + 180) % 360)
                                 == osm_g_per_deg).mean())
                            veg_verdict = (
                                "supports_xcorr"
                                if v_best > v_mirror + 0.02 else
                                ("favors_mirror"
                                 if v_mirror > v_best + 0.02 else "tie")
                            )
                except Exception as _e_veg:
                    print(f"[F-SKY18-4] seed={seed.name} veg-agree "
                          f"diagnostic failed: {_e_veg}")

                # Phase 5 veto: when vegetation clearly favors the 180° mirror
                # AND the proposed rotation is large (likely a flip into the
                # mirror), don't apply it. Margin / shift thresholds tuned to
                # the Cartagena measurement: auto_270_2000m had margin 0.14
                # and shift +127°; named seeds (1, 4, 5) sit at shifts ≤16°
                # so the gate never fires on them.
                VEG_MARGIN = 0.10
                LARGE_SHIFT_DEG = 30
                veg_veto = (
                    v_best is not None and v_mirror is not None
                    and (v_mirror - v_best) >= VEG_MARGIN
                    and abs(shift) >= LARGE_SHIFT_DEG
                )

                if (abs(shift) >= 1 and improve > 0.0
                        and best_mae <= MAE_CEILING_M and not veg_veto):
                    print(
                        f"[bearing_xcorr] seed={seed.name} "
                        f"shift {shift:+d}° APPLIED "
                        f"(pre MAE {pre_mae:.0f} -> {best_mae:.0f}m, "
                        f"improve {improve*100:.0f}%)"
                    )
                    pano_headings = (pano_headings + float(shift)) % 360.0
                    bearing_shift_deg = float(shift)
                elif veg_veto:
                    print(
                        f"[bearing_xcorr] seed={seed.name} "
                        f"shift {shift:+d}° VETOED by vegetation "
                        f"(veg margin {(v_mirror - v_best):+.2f} "
                        f">= {VEG_MARGIN:.2f} favours mirror; F-SKY18-5)"
                    )
                else:
                    print(
                        f"[bearing_xcorr] seed={seed.name} "
                        f"shift {shift:+d}° SKIPPED "
                        f"(best MAE {best_mae:.0f}m > {MAE_CEILING_M:.0f}m "
                        f"— poor alignment, keeping anchor)"
                    )
                if v_best is not None:
                    print(
                        f"[F-SKY18-4] seed={seed.name} "
                        f"veg_agree@best={v_best:.2f} "
                        f"@mirror={v_mirror:.2f} "
                        f"verdict={veg_verdict}"
                    )
        except Exception as exc:
            print(f"[bearing_xcorr] skipped: {exc}")

        # F-SKY22 + F-SKY24: sliding-window pano splitter with depth-
        # fused post-cut. The mask-only splitter can't separate adjacent
        # towers at different depths but similar heights; the depth-cut
        # pass adds those splits.
        with _sub("pano splitter (sliding window + OSM-anchored)"):
            pano_segs = _pano_sliding_window_split(
                pano_bmask, pano_img, _det_pano,
                pano_depth=pano_depth_arr)
        # Too-tall filter: a segment spanning (nearly) the full pano
        # height is implausible for a matched building. A real building at
        # distance has its BASE near the horizon (~mid-frame) and its top
        # in the upper frame — it never reaches from sky-top to frame-
        # bottom. A full-height segment is a too-close wall or a mask
        # artifact ("taller than the pano"), and it creates FALSE coverage
        # by overlapping many OSM projections at once. Drop them.
        _Hp = pano_bmask.shape[0]
        _pre_tall = len(pano_segs)
        pano_segs = [
            s for s in pano_segs
            if (int(s.get("base_y", _Hp - 1)) - int(s.get("top_y", 0)))
            < 0.92 * _Hp
        ]
        if len(pano_segs) != _pre_tall:
            print(f"[pano_split] too-tall filter: {_pre_tall} -> "
                  f"{len(pano_segs)} (dropped {_pre_tall - len(pano_segs)} "
                  f"full-frame segments)")
        # Project OSM footprints to pano column coordinates BEFORE the
        # OSM-anchored split below so the splitter has the projection
        # info it needs. Same call we used to make later; just hoisted.
        with _sub("project OSM buildings to pano columns"):
            pano_projs = project_buildings_to_pano(
                seed_buildings, seed.lat, seed.lon, pano_headings)
        # F-SKY24 Phase 1b: OSM-anchored splitter for the pano path.
        # The per-view path already runs ``osm_anchor_silhouettes``
        # (Stage 7 in _seed_multiview_registration); the pano-wide path
        # didn't. When a wide pano segment contains 2+ OSM projections
        # whose x-ranges sit inside it, F-SKY2 splits the segment into
        # per-projection children. This catches the failure mode depth
        # couldn't: adjacent same-distance towers merged into one mask
        # blob.
        try:
            from ..pipeline import osm_anchor_silhouettes  # noqa: PLC0415
            pre_n = len(pano_segs)
            with _sub("OSM-anchored split (per-projection cut)"):
                pano_segs = osm_anchor_silhouettes(
                    pano_segs, pano_projs, building_mask=pano_bmask)
            post_n = len(pano_segs)
            if post_n != pre_n:
                print(
                    f"[pano_split] OSM-anchored split: {pre_n} -> {post_n} "
                    f"(+{post_n - pre_n})"
                )
        except Exception as exc:
            print(f"[pano_split] OSM-anchored split skipped: {exc}")
        # F-SKY20: prepend SAM instance segments so they're first-class
        # in the splitter output; the matcher will then prefer the SAM
        # silhouettes over rule-based ones when both exist for the same
        # column range (SAM has a per-fid hint via ``sam_prompt_fid``).
        if sam_instances:
            pano_segs = list(sam_instances) + list(pano_segs)
        bbid = {b.feature_id: b for b in seed_buildings}
        with _sub("match pano segments to OSM buildings"):
            pano_matched = _match_pano(pano_segs, pano_projs, bbid)
        # Assign a stable per-tower seed_index across all pano matched
        # segments so the per-layer bbox overlays (drawn next) and the
        # Reconstruction polar plot use a consistent colour + number per
        # OSM feature_id. First-seen fid wins index 1; subsequent
        # segments matched to the same fid reuse the same index.
        pano_index_map: dict[str, int] = {}
        next_idx = 1
        # Sort left-to-right so numbering grows across the pano.
        pano_matched.sort(key=lambda s: int(s.get(
            "peak_x", s.get("mid_x", 0))))
        for seg in pano_matched:
            px = int(seg.get("peak_x", seg.get("mid_x", 0)))
            px = max(0, min(pano_headings.size - 1, px))
            seg["true_bearing_deg"] = float(pano_headings[px])
            m = seg.get("matched_projection")
            if m is None:
                continue
            fid = str(m.get("feature_id", ""))
            if not fid:
                continue
            if fid not in pano_index_map:
                pano_index_map[fid] = next_idx
                next_idx += 1
            seg["seed_index"] = pano_index_map[fid]
            # Annotate the matched building's height so the bbox overlay
            # can label it. Prefer the OSM-tagged height; fall back to a
            # geometric estimate from the pano geometry when untagged:
            #   h = cam_height * (base_y - top_y) / (base_y - horizon_row)
            # which (with K = cam_height·f_y) is independent of f_y if we
            # only have K — so we approximate cam_height from K/f_y using
            # a nominal f_y. Stored as ``height_m`` (+ source flag).
            b = bbid.get(fid)
            h_tag = (float(b.height_tag_m)
                     if b is not None and getattr(b, "height_tag_m", None)
                     is not None else None)
            seg["height_m"] = h_tag
            seg["height_src"] = "osm" if h_tag is not None else None
        n_matched = sum(1 for s in pano_matched if s.get("matched_projection"))

        # OSM-anchored depth->distance scale, computed ONCE here so the
        # Reconstruction polar plot and the Distance scan use the same
        # calibrated value (previously the scan hardcoded 1450 while the
        # recon anchored to OSM, so the same depth read ~1000 m on one and
        # ~600 m on the other). scale = median(osm_dist / sqrt(d_inv))
        # over matched towers; sqrt because DA2 inverse-depth saturates.
        depth_scale = 1450.0
        # Horizon-pitch GEOMETRIC distance fit (F-SKY25). Physics:
        #   dist = camera_height / tan(depression)
        #        = camera_height * f_y / (base_y - horizon_row)
        #        = K / (base_y - horizon_row)
        # so 1/dist is LINEAR in base_y. We fit K and horizon_row by
        # least-squares over matched towers (1/od vs base_y), giving a
        # 2-parameter GEOMETRIC model that PREDICTS distance for every
        # column from its base row. Unlike the saturated DA2 depth value,
        # the base-row position varies strongly and cleanly with distance
        # (close buildings sit low, far ones near the horizon), so this
        # tracks the real near/far variation. Fitting K+horizon_row from
        # OSM is far less circular than a per-building depth scale — it's
        # a physical model whose SHAPE is geometry, not OSM.
        geom_K = None
        geom_horizon_row = None
        geom_pairs: list[tuple[float, float]] = []  # (base_y, osm_dist)
        if pano_depth_arr is not None:
            Hd, Wd = pano_depth_arr.shape[:2]
            ratios: list[float] = []
            for seg in pano_matched:
                m = seg.get("matched_projection")
                if not m:
                    continue
                b = bbid.get(str(m.get("feature_id", "")))
                if b is None:
                    continue
                od = _distance_m(seed.lat, seed.lon,
                                 b.centroid_lat, b.centroid_lon)
                px = max(0, min(Wd - 1, int(seg.get(
                    "peak_x", seg.get("mid_x", 0)))))
                by = max(0, min(Hd - 1, int(seg.get("base_y", Hd - 1))))
                d_inv = 1.0 - max(0.0, min(1.0, float(pano_depth_arr[by, px])))
                if d_inv > 0.05:
                    ratios.append(od / math.sqrt(d_inv))
                if od > 1.0:
                    geom_pairs.append((float(by), float(od)))
            if ratios:
                ratios.sort()
                depth_scale = float(ratios[len(ratios) // 2])
            # Fit the geometric model: 1/od = a*base_y + b  (a = 1/K,
            # b = -horizon_row/K). Need >=3 towers spanning a range of
            # base rows for a stable fit.
            if len(geom_pairs) >= 3:
                bys = np.array([p[0] for p in geom_pairs], dtype=np.float64)
                ods = np.array([p[1] for p in geom_pairs], dtype=np.float64)
                if float(bys.max() - bys.min()) >= 8.0:
                    inv_d = 1.0 / np.maximum(ods, 1e-6)
                    A = np.vstack([bys, np.ones_like(bys)]).T
                    sol, *_ = np.linalg.lstsq(A, inv_d, rcond=None)
                    a, bint = float(sol[0]), float(sol[1])
                    # Slope must be positive (lower base row = closer).
                    if a > 1e-9:
                        geom_K = 1.0 / a
                        geom_horizon_row = -bint / a
                        print(
                            f"[geom_dist] seed={seed.name} "
                            f"K={geom_K:.0f} horizon_row="
                            f"{geom_horizon_row:.0f} "
                            f"(from {len(geom_pairs)} towers)"
                        )

        # Stitch sky + vegetation as separate pano-coord channels (same
        # source view set & sort order as the building/water stitches
        # above, so the column geometry agrees with ``pano_img``).
        try:
            from ..pipeline import stitch_pano_mask_channel  # noqa: PLC0415
            pano_sky_arr = stitch_pano_mask_channel(
                spin_views_for_pano, seed.fov, spin_step_deg, "sky_mask")
            pano_veg_arr = stitch_pano_mask_channel(
                spin_views_for_pano, seed.fov, spin_step_deg, "vegetation_mask")
        except Exception:
            pano_sky_arr = None
            pano_veg_arr = None

        # Center the pano on NORTH: roll every column-indexed array (image,
        # masks, depth, per-column headings) and shift each matched
        # segment's x-coords so the column whose heading is nearest 0° ends
        # up at the horizontal centre. The stored ``true_bearing_deg`` on
        # each segment is unchanged (it's the real bearing), so the polar
        # plots are unaffected; only the strip's column origin moves.
        try:
            if pano_headings is not None and pano_img is not None:
                _W = pano_img.shape[1]
                _d0 = np.minimum(pano_headings % 360.0,
                                 360.0 - (pano_headings % 360.0))
                north_col = int(np.argmin(_d0))
                roll = (_W // 2) - north_col
                if roll != 0:
                    pano_img = np.roll(pano_img, roll, axis=1)
                    pano_headings = np.roll(pano_headings, roll)
                    pano_bmask = np.roll(pano_bmask, roll, axis=1)
                    if pano_wmask is not None:
                        pano_wmask = np.roll(pano_wmask, roll, axis=1)
                    if pano_sky_arr is not None:
                        pano_sky_arr = np.roll(pano_sky_arr, roll, axis=1)
                    if pano_veg_arr is not None:
                        pano_veg_arr = np.roll(pano_veg_arr, roll, axis=1)
                    if pano_depth_arr is not None:
                        pano_depth_arr = np.roll(pano_depth_arr, roll, axis=1)
                    for _seg in pano_matched:
                        for _k in ("x_left", "x_right", "peak_x", "mid_x"):
                            if _seg.get(_k) is not None:
                                _seg[_k] = int(_seg[_k] + roll) % _W
        except Exception as _exc:
            print(f"[pano] north-center roll skipped: {_exc}")

        return StitchedPanoResult(
            seed_name=seed.name,
            seed_lat=seed.lat,
            seed_lon=seed.lon,
            pano_image=pano_img,
            band_y=pano_band,
            matched_segments=pano_matched,
            n_segments=len(pano_segs),
            n_matched=n_matched,
            n_buildings_in_view=len(pano_projs),
            anchor_offset_deg=float(anchor_offset),
            headings_per_col=pano_headings,
            pano_building_mask=pano_bmask,
            pano_water_mask=pano_wmask,
            pano_sky_mask=pano_sky_arr,
            pano_vegetation_mask=pano_veg_arr,
            pano_depth=pano_depth_arr,
            depth_scale=depth_scale,
            geom_K=geom_K,
            geom_horizon_row=geom_horizon_row,
            bearing_shift_deg=bearing_shift_deg,
        )
    except Exception as e:
        import sys as _sys
        print(f"[pano] seed={seed.name} failed: {e}", file=_sys.stderr)
        return None


__all__ = [
    '_register_views',
    '_smooth_matches_across_views',
    '_smooth_pano_matches_against_views',
    '_multires_sam_instances',
    '_split_by_depth_discontinuity',
    '_multires_pano_refine',
    '_pano_sliding_window_split',
    '_build_and_detect_pano',
]
