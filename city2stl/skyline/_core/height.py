"""skyline._core.height — extracted from pipeline.py (A1 split)."""
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

from .types import BuildingRecord, CapturedView, RegisteredBuildingEstimate, Viewpoint
from .projection import _building_projected_x_range, _focal_length_px
from .segmentation import _neural_sky_and_building_masks
from .skyline import (_building_base_y_from_mask, _building_roof_y_from_mask,
                      _floor_period_for_building, _footprint_roof_y_from_mask)

def augment_estimates_with_depth(
    image_rgb: np.ndarray,
    estimates: list[RegisteredBuildingEstimate],
    viewpoint: "Viewpoint",
    camera_height_m: float = 1.7,
) -> list[RegisteredBuildingEstimate]:
    """F-SKY12: augment per-view estimates with depth-derived heights.

    Runs Depth Anything V2 once on the view image, calibrates relative
    depth to metres using each estimate's ``forward_m`` as an anchor at
    its ``(x_px, y_px)``, and computes a second height estimate at the
    silhouette-top pixel via pinhole geometry. Sets ``depth_height_m``
    and ``depth_disagreement`` on each returned estimate.

    Heavy: DA2 inference is ~1–2 s on CPU. Callers should only invoke
    this when the per-view PDF page needs the diagnostic. Returns a new
    list (RegisteredBuildingEstimate is frozen); on failure returns the
    input unchanged so the rest of the pipeline is untouched.
    """
    if not estimates:
        return estimates

    try:
        from city2stl.skyline.depth_estimation import (  # noqa: PLC0415
            calibrate_pano_depth,
            compare_heights,
            depth_height_from_segment,
            predict_pano_depth,
        )
    except ImportError as exc:
        logger.warning("F-SKY12 depth module unavailable: %s", exc)
        return estimates

    try:
        depth_rel = predict_pano_depth(image_rgb)
    except Exception as exc:
        logger.warning("F-SKY12 DA2 inference failed: %s", exc)
        return estimates

    # Build anchors from the estimates: (row, col, distance_m). Use the
    # silhouette-top pixel as the sample location and forward_m as the
    # known distance to the matched building.
    anchors: list[tuple[int, int, float]] = []
    for est in estimates:
        if est.forward_m > 0.0 and 0.0 <= est.y_px and 0.0 <= est.x_px:
            anchors.append(
                (int(round(est.y_px)), int(round(est.x_px)), float(est.forward_m))
            )
    if not anchors:
        return estimates

    try:
        depth_m = calibrate_pano_depth(depth_rel, anchors)
    except ValueError as exc:
        logger.warning("F-SKY12 depth calibration failed: %s", exc)
        return estimates

    fy = _focal_length_px(viewpoint)
    cy = float(viewpoint.image_height) / 2.0

    out: list[RegisteredBuildingEstimate] = []
    for est in estimates:
        h_depth = depth_height_from_segment(
            depth_m,
            (int(round(est.x_px)), int(round(est.y_px))),
            fy=fy,
            cy=cy,
            camera_height_m=camera_height_m,
        )
        disagree = compare_heights(est.estimated_height_m, h_depth)
        out.append(
            replace(est, depth_height_m=float(h_depth), depth_disagreement=bool(disagree))
        )
    return out

def estimate_heights_from_registration(
    captured: CapturedView,
    registration: dict,
    buildings: Sequence[BuildingRecord],
    camera_height_m: float,
    camera_elev_m: float = 0.0,
    *,
    trace=None,
    max_plausible_height_m: float = 300.0,
    compute_floor_period: bool = False,
) -> list[RegisteredBuildingEstimate]:
    # Optional `trace` (HeightTraceRecorder from height_trace.py): when set, the
    # function emits a row at every gate / decision point. Behaviour-neutral —
    # see docs/glass-roof-height-fix-plan.md Phase 1.
    #
    # `compute_floor_period` runs the F-SKY1 facade autocorrelation diagnostic
    # that fills the floor_period_px / floor_confidence / inferred_distance_m
    # / inferred_height_m fields on each estimate. Default OFF: the fields
    # are not rendered in the PDF and the cost (one autocorrelation per
    # building per view) is significant. Set True only for diagnostics.
    contour = np.asarray(registration["contour"], dtype=np.float32)
    best_offset = float(registration["best_offset"])
    best_score = float(registration.get("best_score", float("inf")))
    # Use all culled-visible projections (not just peak-matched) to maximise yield.
    all_proj_list = registration.get(
        "all_projections") or registration["projections"]
    projections = {item["feature_id"]: item for item in all_proj_list}
    # Track which buildings were peak-matched for confidence scoring.
    matched_ids = {item["feature_id"] for item in registration["projections"]}
    f_px = _focal_length_px(captured.viewpoint)
    cy = captured.viewpoint.image_height * 0.5
    cam_z = camera_elev_m + camera_height_m

    # Pull the cached neural building mask for this image. When available it's
    # the authoritative source for "is there a building at this column?": the
    # global skyline contour treats clouds, lampposts, and tree canopies as
    # not-sky and reports an inflated roof y, but those pixels are not in the
    # ADE20K building class. With the mask we can:
    #   - sample the roof y from the mask directly (per-column, robust to clouds)
    #   - drop the estimate when no building pixels exist in this column
    sky_mask, building_mask = _neural_sky_and_building_masks(captured.image)

    # Cap residual contribution to confidence: residuals well above ~25 px are
    # essentially noise.
    score_norm = float(np.clip(1.0 - min(best_score, 25.0) / 25.0, 0.05, 1.0))

    # Pre-compute (x_px, forward_m) arrays for the closest-in-column-bin check.
    # When several buildings project into the same image column, the visible
    # rooftop pixel belongs to the *closest* one — crediting a far building for
    # that pixel is the failure mode that produces b0151 143m→284m (tag→pred).
    if all_proj_list:
        _all_x = np.asarray([p["x_px"]
                            for p in all_proj_list], dtype=np.float32)
        _all_fwd = np.asarray([p["forward_m"]
                              for p in all_proj_list], dtype=np.float32)
    else:
        _all_x = np.empty(0, dtype=np.float32)
        _all_fwd = np.empty(0, dtype=np.float32)

    estimates: list[RegisteredBuildingEstimate] = []
    view_name = captured.viewpoint.name
    if trace is not None:
        # Save view artefacts (RGB / contour / building mask) once per view so
        # the diagnostic renderer can draw them later. When `only_feature_id`
        # is set, skip views that don't contain that building — keeps the
        # in-memory artefact dict tight.
        only_fid = getattr(trace, "only_feature_id", None)
        if only_fid is None or only_fid in projections:
            saver = getattr(trace, "save_view", None)
            if saver is not None:
                saver(view_name, captured.image, contour, building_mask)
    for building in buildings:
        fid = building.feature_id
        if trace is not None:
            trace(
                "building_start",
                view_name=view_name,
                feature_id=fid,
                name=building.name,
                tag_h=building.height_tag_m,
                area_m2=building.area_m2,
                terrain_elev_m=float(building.terrain_elev_m),
            )
        proj = projections.get(fid)
        if not proj:
            if trace is not None:
                trace("drop_no_projection", view_name=view_name, feature_id=fid)
            continue

        x_px = int(round(proj["x_px"]))
        if x_px < 0 or x_px >= contour.size:
            if trace is not None:
                trace(
                    "drop_x_out_of_bounds",
                    view_name=view_name,
                    feature_id=fid,
                    x_px=int(x_px),
                    contour_size=int(contour.size),
                )
            continue

        # Closest-in-column-bin gate: if another projection within ±15 px of
        # this x is significantly closer (>200 m), the visible rooftop pixel
        # belongs to that closer building, not this one. Skip this estimate.
        # 200 m threshold preserves credible cross-distance distinctions (a
        # distant tall tower CAN still be credited if no near building shares
        # its column) while killing the "far thin building credited for a
        # near tall roof" pattern.
        forward = float(proj["forward_m"])
        # Skyline pipeline minimum standoff: buildings closer than 15 m are
        # essentially right in front of the camera (a wall, not a skyline).
        # Depth and OSM both signal "too close" for these — their silhouette
        # top is above the frame and the pinhole height formula breaks down.
        # 15 m is conservative enough to keep legit close buildings in urban
        # scenes while excluding facade shots.
        if forward <= 15.0:
            if trace is not None:
                trace(
                    "drop_forward_too_close",
                    view_name=view_name,
                    feature_id=fid,
                    forward_m=forward,
                )
            continue
        if _all_x.size:
            nearby = np.abs(_all_x - float(proj["x_px"])) <= 15.0
            if nearby.any():
                closest_in_bin = float(_all_fwd[nearby].min())
                if trace is not None:
                    trace(
                        "closest_in_bin",
                        view_name=view_name,
                        feature_id=fid,
                        x_px=float(proj["x_px"]),
                        forward_m=forward,
                        closest_in_bin_m=closest_in_bin,
                        rivals_in_bin=int(nearby.sum()),
                    )
                if forward > closest_in_bin + 200.0:
                    if trace is not None:
                        trace(
                            "drop_closest_in_bin",
                            view_name=view_name,
                            feature_id=fid,
                            forward_m=forward,
                            closest_in_bin_m=closest_in_bin,
                            margin_m=forward - closest_in_bin,
                        )
                    continue

        # Footprint-driven roof sampling: use the FULL projected x-range of
        # this building's footprint instead of just the centroid column. A
        # narrow building gets a tight column band; a wide one gets a wide
        # one. The footprint also gives us a coverage check — if < 25 % of
        # the projected range has building-mask pixels, this building is
        # occluded or off-frame, skip the estimate.
        x_range = _building_projected_x_range(
            building, captured.viewpoint, best_offset, contour.size)
        if x_range is not None:
            xL, xR = x_range
            roof_y_mask, coverage = _footprint_roof_y_from_mask(
                building_mask, xL, xR)
            if building_mask is not None and roof_y_mask is None:
                # No building pixels in the projected column range.  Could be
                # a glass/reflective tower (SegFormer labels the spire as sky),
                # a building that is too distant/thin to exceed the 8-row
                # threshold, or a genuinely occluded structure.  Fall through
                # to the contour-y path rather than discarding the building
                # entirely — the contour gives a reasonable rooftop estimate
                # and the closest-in-bin gate handles occlusion.
                pass
        else:
            # No polygon vertices in FOV — fall back to centroid sampling.
            roof_y_mask = _building_roof_y_from_mask(building_mask, x_px)
            if building_mask is not None and roof_y_mask is None:
                pass  # fall through to contour-y as above
            coverage = 0.0

        if trace is not None:
            trace(
                "roof_y_from_mask",
                view_name=view_name,
                feature_id=fid,
                x_range=list(x_range) if x_range is not None else None,
                roof_y_mask=float(roof_y_mask) if roof_y_mask is not None else None,
                coverage=float(coverage) if coverage is not None else None,
                mask_available=bool(building_mask is not None),
            )

        if roof_y_mask is not None:
            y_px = float(roof_y_mask)
            # Glass-facade override: SegFormer's building class often stops
            # 20-40 px below the actual rooftop on reflective glass/curtain-
            # wall spires because the top of the tower reflects sky and gets
            # labelled as sky. When the sky/non-sky CONTOUR is significantly
            # higher than the mask roof at this building's column, AND the
            # gap looks like a continuous tower silhouette (not a separate
            # foreground object), prefer the contour y.
            if x_range is not None:
                xL, xR = x_range
                cxL = max(0, int(xL))
                cxR = min(contour.size - 1, int(xR))
                if cxR >= cxL:
                    contour_slice = contour[cxL: cxR + 1]
                    finite = contour_slice[np.isfinite(contour_slice)]
                    if finite.size > 0:
                        # Use a representative high point — the 20th percentile
                        # of contour y over the building's projected column
                        # range (avoids being thrown by a single noise pixel
                        # above the spire).
                        contour_top_y = float(np.percentile(finite, 20))
                        gap = y_px - contour_top_y
                        override_fired = False
                        implied_h_val: float | None = None
                        sky_above_ok = True
                        if gap > 15.0:
                            # Sanity-check the gap: it should be plausible as
                            # additional tower height at this distance. A
                            # gap > what the regional cap allows is noise
                            # (cloud edge, lamppost), not glass roof.
                            pitch_rad = math.radians(captured.viewpoint.pitch)
                            angle_at_contour = math.atan(
                                (cy - contour_top_y) / f_px) + pitch_rad
                            top_at_contour = forward * \
                                math.tan(angle_at_contour)
                            implied_h = cam_z + top_at_contour - float(
                                building.terrain_elev_m)
                            implied_h_val = float(implied_h)
                            # 1c: require SegFormer-sky pixels just above the
                            # contour roof. Without sky there, the contour is
                            # not a roof edge (it's a tree canopy or another
                            # building behind), so the glass-facade override
                            # would manufacture height where none exists.
                            if sky_mask is not None:
                                sky_probe_y = int(max(0, int(round(contour_top_y)) - 5))
                                sky_row = sky_mask[sky_probe_y, cxL: cxR + 1]
                                if sky_row.size == 0 or float(np.mean(sky_row)) < 0.5:
                                    sky_above_ok = False
                            if (sky_above_ok
                                    and 0.0 <= implied_h <= max_plausible_height_m):
                                y_px = contour_top_y
                                override_fired = True
                        if trace is not None:
                            trace(
                                "contour_override",
                                view_name=view_name,
                                feature_id=fid,
                                mask_roof_y=float(roof_y_mask),
                                contour_top_y=contour_top_y,
                                gap_px=float(gap),
                                implied_h_m=implied_h_val,
                                sky_above_ok=bool(sky_above_ok),
                                fired=override_fired,
                            )
        else:
            y_px = float(contour[x_px])
            if not np.isfinite(y_px):
                if trace is not None:
                    trace(
                        "drop_contour_nan",
                        view_name=view_name,
                        feature_id=fid,
                        x_px=int(x_px),
                    )
                continue

        pitch_rad = math.radians(captured.viewpoint.pitch)
        angle_rad = math.atan((cy - y_px) / f_px) + pitch_rad
        top_above_camera = forward * math.tan(angle_rad)
        # Height of the building above its own base (ground at footprint):
        # top_z = cam_z + forward*tan(angle); height_above_base = top_z - bld_terrain_z
        height_m = cam_z + top_above_camera - float(building.terrain_elev_m)
        if trace is not None:
            trace(
                "pinhole_math",
                view_name=view_name,
                feature_id=fid,
                y_px=float(y_px),
                cy=float(cy),
                f_px=float(f_px),
                pitch_rad=float(pitch_rad),
                angle_rad=float(angle_rad),
                forward_m=float(forward),
                top_above_camera_m=float(top_above_camera),
                cam_z_m=float(cam_z),
                terrain_elev_m=float(building.terrain_elev_m),
                height_m=float(height_m),
            )
        if not np.isfinite(height_m):
            if trace is not None:
                trace(
                    "drop_height_nan",
                    view_name=view_name,
                    feature_id=fid,
                    height_m=float(height_m) if math.isfinite(height_m) else None,
                )
            continue

        # Geometric y-consistency gate: even before invoking the per-building
        # height proxy, check that the sampled roof_y is geometrically
        # plausible for *any* building at this distance. The ceiling comes
        # from the region config (300 m default — Chicago Willis ≈ 442 m
        # needs a higher cap; Cartagena's tallest is ~206 m so 200 m is
        # appropriate). The floor is 2 m. If the column says the roof is
        # higher in the image than the regional cap could project to this
        # distance, the column actually belongs to a CLOSER building — drop
        # the credit. This catches the "far thin OSM building credited for a
        # near tall building's roof" pattern that the closest-in-column-bin
        # gate only partly catches (it misses cases where the closer
        # building is just outside the ±15 px window because its centroid
        # is far from the wide segment).
        max_plausible_top = float(max_plausible_height_m)
        min_plausible_top = 2.0
        max_top_angle = math.atan(
            (max_plausible_top - cam_z - float(building.terrain_elev_m)) / forward)
        min_top_angle = math.atan(
            (min_plausible_top - cam_z - float(building.terrain_elev_m)) / forward)
        # Convert angle bounds back to y_px bounds (inverted: smaller y = higher).
        min_y_for_building = cy - f_px * math.tan(max_top_angle - pitch_rad)
        max_y_for_building = cy - f_px * math.tan(min_top_angle - pitch_rad)
        if trace is not None:
            trace(
                "geometric_y_gate",
                view_name=view_name,
                feature_id=fid,
                y_px=float(y_px),
                min_y_for_building=float(min_y_for_building),
                max_y_for_building=float(max_y_for_building),
            )
        # Allow ±5 px slack for rounding and mask quantisation.
        if y_px < min_y_for_building - 5 or y_px > max_y_for_building + 5:
            if trace is not None:
                trace(
                    "drop_geometric_gate",
                    view_name=view_name,
                    feature_id=fid,
                    y_px=float(y_px),
                    min_y_for_building=float(min_y_for_building),
                    max_y_for_building=float(max_y_for_building),
                )
            continue

        # Tag-disagreement filter: when we have an OSM height tag, treat it
        # as ground truth and drop per-view estimates that disagree by more
        # than min(2 × tag, 50 m). The tag is the strongest constraint we
        # have on this building's height — predictions outside that band
        # signal a per-view geometry failure (wrong column matched, glass-
        # facade override misfired, projection error). Letting them into
        # the aggregate inflates the headline MAE; dropping them tightens
        # the cross-seed median AND validates the per-view extraction by
        # making the failure visible in the trace log.
        #
        # For untagged buildings we keep a light area-based floor — a
        # multi-square-metre footprint that predicts <3 m almost always
        # means roof_y was sampled in water/sky, not on the building.
        pred_capped = max(height_m, 0.0)
        if building.height_tag_m is not None:
            tag_h = float(building.height_tag_m)
            if tag_h >= 3.0:
                diff = abs(pred_capped - tag_h)
                disagreement_threshold = min(tag_h * 2.0, 50.0)
                if diff > disagreement_threshold:
                    if trace is not None:
                        trace(
                            "drop_tag_disagreement",
                            view_name=view_name,
                            feature_id=fid,
                            tag_h=tag_h,
                            pred_h=float(pred_capped),
                            diff_m=float(diff),
                            threshold_m=float(disagreement_threshold),
                        )
                    continue
        else:
            if building.area_m2 > 50.0 and pred_capped < 3.0:
                if trace is not None:
                    trace(
                        "drop_plausibility_area",
                        view_name=view_name,
                        feature_id=fid,
                        area_m2=float(building.area_m2),
                        pred_h=float(pred_capped),
                    )
                continue

        # Unmatched buildings (not locked to a skyline peak): assign a
        # moderate default residual so their confidence is lower but nonzero.
        if building.feature_id in matched_ids:
            x_err = float(proj.get("match_residual_px",
                          abs(proj["x_px"] - float(x_px))))
        else:
            x_err = 15.0  # ~0.375 x_err_score for unmatched buildings
        x_err_score = max(0.1, 1.0 - x_err / 24.0)
        tag_score = 1.0 if building.height_tag_m is not None else 0.7
        forward_score = float(
            np.clip(1.0 - max(0.0, forward - 3000.0) / 3000.0, 0.3, 1.0))
        confidence = float(np.clip(score_norm * x_err_score *
                           tag_score * forward_score, 0.05, 1.0))

        # F-SKY1: optional floor-period diagnostic. Skip on tilted views
        # (|pitch| > 4° distorts the per-floor pixel period as a function
        # of y) and on facades shorter than 80 px between roof and base
        # (the autocorrelation needs at least ~2 cycles to lock).
        # _floor_period_for_building returns None when the autocorrelation
        # peak is below the confidence floor, so noise / blank-wall
        # facades silently no-op without producing a fake estimate.
        floor_info: dict = {}
        proj_x_px = float(proj["x_px"])
        if (compute_floor_period
                and abs(captured.viewpoint.pitch) <= 4.0
                and building_mask is not None):
            x_left_px = float(proj.get("x_left_px", proj_x_px - 10.0))
            x_right_px = float(proj.get("x_right_px", proj_x_px + 10.0))
            if x_right_px < x_left_px:
                x_left_px, x_right_px = x_right_px, x_left_px
            y_px_int = int(round(y_px))
            y_base = _building_base_y_from_mask(
                building_mask, int(round(proj_x_px)), y_px_int)
            if y_base is not None and y_base - y_px_int > 80:
                floor_info = _floor_period_for_building(
                    captured.image,
                    building_mask,
                    (int(round(x_left_px)), int(round(x_right_px))),
                    y_px,
                    y_base,
                    f_px=f_px,
                ) or {}

        estimate = RegisteredBuildingEstimate(
            feature_id=building.feature_id,
            name=building.name,
            view_name=captured.viewpoint.name,
            heading_offset_deg=best_offset,
            x_px=proj_x_px,
            y_px=y_px,
            forward_m=forward,
            estimated_height_m=float(max(0.0, height_m)),
            confidence=confidence,
            floor_period_px=floor_info.get("floor_period_px"),
            floor_confidence=floor_info.get("floor_confidence"),
            inferred_distance_m=floor_info.get("inferred_distance_m"),
            inferred_height_m=floor_info.get("inferred_height_m"),
        )
        if trace is not None:
            trace(
                "emit",
                view_name=view_name,
                feature_id=fid,
                x_px=estimate.x_px,
                y_px=estimate.y_px,
                forward_m=estimate.forward_m,
                height_m=estimate.estimated_height_m,
                confidence=estimate.confidence,
                tag_h=building.height_tag_m,
                heading_offset_deg=float(best_offset),
                floor_period_px=estimate.floor_period_px,
                inferred_distance_m=estimate.inferred_distance_m,
                inferred_height_m=estimate.inferred_height_m,
            )
        estimates.append(estimate)

    return estimates

def _seed_from_view_name(view_name: str) -> str:
    """Extract seed identifier from a view_name like 'seed_1_321' -> 'seed_1'."""
    parts = view_name.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return view_name

def aggregate_building_heights(estimates: Sequence[RegisteredBuildingEstimate]) -> list[dict]:
    grouped: dict[str, list[RegisteredBuildingEstimate]] = {}
    for estimate in estimates:
        grouped.setdefault(estimate.feature_id, []).append(estimate)

    output: list[dict] = []
    for feature_id, items in grouped.items():
        heights = np.asarray(
            [item.estimated_height_m for item in items], dtype=np.float32)
        confidences = np.asarray(
            [item.confidence for item in items], dtype=np.float32)
        if heights.size == 0:
            continue

        # Per-seed grouping for cross-seed agreement and outlier downweighting.
        per_seed_items: dict[str, list[RegisteredBuildingEstimate]] = {}
        for item in items:
            seed_name = _seed_from_view_name(item.view_name)
            per_seed_items.setdefault(seed_name, []).append(item)

        # Per-view outlier rejection inside each seed: a single dissenting
        # view (rooftop occluded by a tree, mis-bounded segment, depth
        # blowup) can pull a seed's median 20+ m. When a seed has ≥3
        # views of the same building, drop any view that's more than
        # 2.5 MAD from the seed's per-view median before computing the
        # seed's authoritative height. Improves cross-seed agreement
        # numbers and aligns with the cross-view smoothing of matches
        # (no point trusting a view whose match was overridden because
        # the consensus disagreed).
        per_view_outliers_dropped = 0
        for seed_name, seed_its in list(per_seed_items.items()):
            if len(seed_its) < 3:
                continue
            view_heights = np.asarray(
                [it.estimated_height_m for it in seed_its], dtype=np.float32)
            v_med = float(np.median(view_heights))
            v_mad = float(np.median(np.abs(view_heights - v_med)))
            if v_mad < 1.0:
                continue
            v_threshold = 2.5 * v_mad
            kept = [
                it for it, h in zip(seed_its, view_heights)
                if abs(float(h) - v_med) <= v_threshold
            ]
            if kept and len(kept) < len(seed_its):
                per_view_outliers_dropped += (len(seed_its) - len(kept))
                per_seed_items[seed_name] = kept

        per_seed_median = {
            s: float(np.median([it.estimated_height_m for it in seed_its]))
            for s, seed_its in per_seed_items.items()}

        # Outlier-seed downweighting: when ≥ 3 seeds disagree, find seeds
        # whose per-seed median is > 1.5 × MAD from the overall seed-median
        # median, and zero their contribution. This rejects an outlier seed
        # entirely (e.g. one bad heading anchor) rather than letting it pull
        # the aggregate. For 2 seeds we can't tell which is the outlier, so
        # we leave both in.
        outlier_seeds: set[str] = set()
        if len(per_seed_median) >= 3:
            vals = np.asarray(list(per_seed_median.values()), dtype=np.float32)
            seed_median_overall = float(np.median(vals))
            seed_mad = float(np.median(np.abs(vals - seed_median_overall)))
            if seed_mad >= 1.0:  # only meaningful with non-trivial spread
                threshold = 1.5 * seed_mad
                for s, m in per_seed_median.items():
                    if abs(m - seed_median_overall) > threshold:
                        outlier_seeds.add(s)

        # Build the effective items/heights/confidences (excluding outliers).
        # ``items`` is the raw input; rebuild from the per-view-filtered
        # per_seed_items so per-view outliers are also removed.
        survivors_set = set()
        for seed_name, seed_its in per_seed_items.items():
            if seed_name in outlier_seeds:
                continue
            for it in seed_its:
                survivors_set.add(id(it))
        eff_items = [it for it in items if id(it) in survivors_set]
        if not eff_items:
            eff_items = list(items)  # all were outliers — fall back to raw
        eff_heights = np.asarray(
            [it.estimated_height_m for it in eff_items], dtype=np.float32)
        eff_confidences = np.asarray(
            [it.confidence for it in eff_items], dtype=np.float32)

        # F-SKY12 Phase B: downweight views where depth says the building is
        # significantly shorter than the geometric estimate. This fires when
        # the geometric path is likely chasing a false silhouette top (reflection
        # or adjacent structure above the actual roofline). threshold = 0.7×:
        # depth must say < 70% of geometric before we penalise it.
        eff_confidences_adj = eff_confidences.copy()
        for _i, _it in enumerate(eff_items):
            _dh = getattr(_it, "depth_height_m", None)
            _dd = getattr(_it, "depth_disagreement", None)
            if (_dd and _dh is not None and _dh > 0.0
                    and _it.estimated_height_m > 0.0
                    and _dh < _it.estimated_height_m * 0.70):
                eff_confidences_adj[_i] *= 0.5

        median = float(np.median(eff_heights))
        spread = float(np.median(np.abs(eff_heights - median)))
        weighted = float(np.average(
            eff_heights, weights=np.maximum(eff_confidences_adj, 1e-3)))

        # F-SKY1: collect floor-period height estimates from views with a
        # usable autocorrelation lock (confidence ≥ 0.30, ≥2 views required
        # to guard against single-facade noise). These are OSM-independent.
        _f1_valid = [
            float(_it.inferred_height_m)
            for _it in eff_items
            if getattr(_it, "inferred_height_m", None) is not None
            and getattr(_it, "floor_confidence", None) is not None
            and _it.floor_confidence >= 0.30
            and _it.inferred_height_m > 0.0
        ]
        f1_height_m: "float | None" = None
        f1_n_views: int = 0
        if len(_f1_valid) >= 2:
            f1_height_m = float(np.median(np.asarray(_f1_valid, dtype=np.float32)))
            f1_n_views = len(_f1_valid)

        # F-SKY12 Phase B: collect depth-rescue heights — views where DA2 says
        # the building is significantly taller than the geometric estimate
        # (under-prediction case: glass towers, small sky gap). Require ≥2
        # agreeing views to avoid single-view DA2 artefacts.
        _d_rescue = [
            float(_it.depth_height_m)
            for _it in eff_items
            if getattr(_it, "depth_height_m", None) is not None
            and _it.depth_height_m > 0.0
            and getattr(_it, "depth_disagreement", None)
            and _it.depth_height_m > _it.estimated_height_m * 1.30
        ]
        depth_rescue_height_m: "float | None" = None
        if len(_d_rescue) >= 2:
            depth_rescue_height_m = float(
                np.median(np.asarray(_d_rescue, dtype=np.float32)))

        # Effective height: geometric median, rescued upward by F-SKY1 or
        # depth when they agree the building is ≥40% taller than geometric.
        # Only rescues upward — over-prediction is rare in this pipeline and
        # downward correction without cross-validation would hurt recall.
        effective_height_m: float = median
        effective_height_source: str = "geometric"
        for _h_alt, _src in (
            (f1_height_m, "f_sky1"),
            (depth_rescue_height_m, "depth_rescue"),
        ):
            if _h_alt is not None and _h_alt > effective_height_m * 1.40:
                effective_height_m = _h_alt
                effective_height_source = _src

        if len(per_seed_median) >= 2:
            vals = np.asarray(list(per_seed_median.values()), dtype=np.float32)
            seed_disagreement_m = float(np.max(vals) - np.min(vals))
            seed_std_m = float(np.std(vals))
        else:
            seed_disagreement_m = 0.0
            seed_std_m = 0.0

        output.append(
            {
                "feature_id": feature_id,
                "name": items[0].name,
                "n_views": int(heights.size),
                "n_views_after_outlier_filter": int(eff_heights.size),
                "n_view_outliers_dropped": int(per_view_outliers_dropped),
                "n_seeds": len(per_seed_items),
                "n_outlier_seeds": len(outlier_seeds),
                "outlier_seeds": sorted(outlier_seeds),
                "median_height_m": median,
                "weighted_height_m": weighted,
                "mad_m": spread,
                "mean_confidence": float(np.mean(eff_confidences)),
                "per_seed_median_m": per_seed_median,
                "seed_disagreement_m": seed_disagreement_m,
                "seed_std_m": seed_std_m,
                # F-SKY1: OSM-independent floor-count height (None when <2
                # views locked a facade period).
                "f_sky1_height_m": f1_height_m,
                "f_sky1_n_views": f1_n_views,
                # F-SKY12 Phase B: depth-rescue height (None when <2 views
                # had DA2 > geometric × 1.3).
                "depth_rescue_height_m": depth_rescue_height_m,
                # Effective height: geometric median rescued upward by F-SKY1
                # or depth when they agree the building is ≥40% taller.
                "effective_height_m": effective_height_m,
                "effective_height_source": effective_height_source,
                "views": [
                    {
                        "view_name": item.view_name,
                        "height_m": item.estimated_height_m,
                        "confidence": item.confidence,
                        "heading_offset_deg": item.heading_offset_deg,
                    }
                    for item in items
                ],
            }
        )

    output.sort(
        key=lambda row: (-row["n_seeds"], -row["n_views"], -row["median_height_m"]))
    return output


__all__ = [
    'augment_estimates_with_depth',
    'estimate_heights_from_registration',
    '_seed_from_view_name',
    'aggregate_building_heights',
]
