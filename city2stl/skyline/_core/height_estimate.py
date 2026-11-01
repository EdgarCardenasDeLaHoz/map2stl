"""Part of ``city2stl.skyline._core.height`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import replace

import numpy as np

from .projection import _building_projected_x_range, _focal_length_px  # noqa: E402
from .segmentation import _neural_sky_and_building_masks  # noqa: E402
from .skyline import (  # noqa: E402
    _building_base_y_from_mask,
    _building_roof_y_from_mask,
    _floor_period_for_building,
    _footprint_roof_y_from_mask,
)
from .types import BuildingRecord, CapturedView, RegisteredBuildingEstimate, Viewpoint  # noqa: E402

# F-CLEAN14: the F-SKY12 depth except-branches reference ``logger`` but the
# module never defined one (latent NameError, only reachable on a depth-module
# failure). Defined here so those branches log instead of crashing.
logger = logging.getLogger('city2stl.skyline._core.height')


def _tag_filter_enabled() -> bool:
    """Whether per-view estimates far from the OSM height tag are dropped.

    On by default — the tag really is the strongest per-building constraint the
    pipeline has, and the filter measurably tightens the aggregate. But it also
    makes any accuracy figure computed over tagged buildings partly
    self-fulfilling, because the predictions that would have disagreed were
    removed on the strength of the same tag they are then scored against. Set
    ``SKYLINE_TAG_FILTER=0`` to measure the pipeline's unaided error, e.g. when
    scoring against an independent source such as a registered vendor plate.
    """
    from ..config import current  # noqa: PLC0415
    return current().tag_filter


def _ground_elev_m(building: BuildingRecord, camera_elev_m: float) -> float:
    """Ground elevation to subtract from a building's absolute rooftop z.

    The pinhole math works in absolute elevation: the camera sits at
    ``camera_elev_m + camera_height_m`` above sea level, and a building's
    height above its own base is ``rooftop_z - ground_z``. That only works
    when both sides use the same datum.

    Per-building terrain is expensive (one elevation lookup per centroid) and
    is currently not fetched by the region pipeline, so ``terrain_elev_m``
    is left at its 0.0 placeholder. Subtracting that placeholder from an
    absolute rooftop z adds ``camera_elev_m`` to every height — harmless at
    sea level (Cartagena), catastrophic anywhere elevated (Denver +1600 m,
    Bogotá +2640 m, where every estimate then fails the plausibility gates
    and the region yields no heights at all).

    So: use the measured terrain when we have it, and otherwise assume the
    building stands on the camera's own ground plane, which cancels
    ``camera_elev_m`` out of the arithmetic entirely.
    """
    if getattr(building, "terrain_known", False):
        return float(building.terrain_elev_m)
    return float(camera_elev_m)


def augment_estimates_with_depth(
    image_rgb: np.ndarray,
    estimates: list[RegisteredBuildingEstimate],
    viewpoint: Viewpoint,
    camera_height_m: float = 1.7,
    ground_offsets_m: dict[str, float] | None = None,
) -> list[RegisteredBuildingEstimate]:
    """F-SKY12: augment per-view estimates with depth-derived heights.

    Runs Depth Anything V2 once on the view image, calibrates relative
    depth to metres against each estimate's known ``forward_m``, and
    computes a second height estimate via pinhole geometry: the ray angle
    from the silhouette-top pixel, the distance from a facade pixel in the
    same column. Sets ``depth_height_m`` and ``depth_disagreement`` on each
    returned estimate.

    Calibration anchors sit on the **facade**, at the row where the sight
    line is horizontal, not at the silhouette top. At the horizontal row a
    z-depth *is* the ground distance, so it can be equated with
    ``forward_m`` directly. The silhouette top cannot: it is the last
    building pixel before sky, so DA2 often reports the far background
    there, and regressing background depth against near footprint
    distances mis-scales the whole map.

    ``ground_offsets_m`` maps feature_id to ``camera_ground - building_ground``
    in metres. Pass it when per-building terrain is known; omit it and the
    depth estimate assumes both stand on the same ground plane, matching
    what the geometric path does under the same conditions.

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

    fy = _focal_length_px(viewpoint)
    cy = float(viewpoint.image_height) / 2.0
    pitch_rad = math.radians(float(viewpoint.pitch))
    d_h, d_w = depth_rel.shape[:2]

    # Row whose sight line is horizontal. Solving
    # ``atan((cy - y) / fy) + pitch = 0`` gives ``y = cy + fy*tan(pitch)``.
    # Scale from viewpoint pixels into the depth map's own resolution — DA2
    # may return a different size than the source view.
    y_scale = float(d_h) / float(max(1, viewpoint.image_height))
    x_scale = float(d_w) / float(max(1, viewpoint.image_width))
    y_horizon = (cy + fy * math.tan(pitch_rad)) * y_scale

    # Build calibration anchors: (row, col, distance_m) on the facade.
    anchors: list[tuple[int, int, float]] = []
    for est in estimates:
        if est.forward_m <= 0.0 or est.x_px < 0.0 or est.y_px < 0.0:
            continue
        col = int(round(est.x_px * x_scale))
        row = int(round(y_horizon))
        if not (0 <= col < d_w and 0 <= row < d_h):
            continue
        # The anchor must land on the facade, i.e. strictly below this
        # building's own roof line. When the roof is already below the
        # horizon the horizontal row shows ground or another building, so
        # skip rather than poison the fit with a wrong distance.
        if row <= int(round(est.y_px * y_scale)) + 2:
            continue
        anchors.append((row, col, float(est.forward_m)))
    if not anchors:
        logger.debug(
            "F-SKY12: no facade anchors on the horizontal row; skipping depth")
        return estimates

    try:
        depth_m = calibrate_pano_depth(depth_rel, anchors)
    except ValueError as exc:
        logger.warning("F-SKY12 depth calibration failed: %s", exc)
        return estimates

    offsets = ground_offsets_m or {}
    horizon_row = int(round(y_horizon))
    out: list[RegisteredBuildingEstimate] = []
    for est in estimates:
        col = int(round(est.x_px * x_scale))
        roof_row = int(round(est.y_px * y_scale))
        # Angle from the roof pixel, distance from a facade pixel in the
        # same column. Sampling depth at the roof reads whatever the model
        # hallucinated for the sky just past the silhouette — the same
        # background bleed the anchor selection above avoids. Fall back to
        # the roof pixel when the horizontal row is not on this facade.
        sample = (col, horizon_row) if horizon_row > roof_row + 2 else None
        h_depth = depth_height_from_segment(
            depth_m,
            (col, roof_row),
            fy=fy * y_scale,
            cy=cy * y_scale,
            camera_height_m=camera_height_m,
            pitch_rad=pitch_rad,
            ground_offset_m=float(offsets.get(est.feature_id, 0.0)),
            depth_sample_xy=sample,
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
    # see docs/plans/done/skyline/glass-roof-height-fix-plan.md Phase 1.
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
        # Ground elevation for this building, on the same datum as cam_z.
        # Falls back to the camera's ground when per-building terrain was
        # never fetched — see _ground_elev_m.
        ground_z = _ground_elev_m(building, camera_elev_m)
        if trace is not None:
            trace(
                "building_start",
                view_name=view_name,
                feature_id=fid,
                name=building.name,
                tag_h=building.height_tag_m,
                area_m2=building.area_m2,
                terrain_elev_m=float(building.terrain_elev_m),
                terrain_known=bool(getattr(building, "terrain_known", False)),
                ground_z_m=float(ground_z),
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
        # one. _footprint_roof_y_from_mask applies its own coverage gate
        # (mean building rows per column, >= 8) and returns None when the
        # projected range lands on water or sky; `coverage` is that mean,
        # reported here for the trace only.
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
                            implied_h = cam_z + top_at_contour - ground_z
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
        # top_z = cam_z + forward*tan(angle); height_above_base = top_z - ground_z
        height_m = cam_z + top_above_camera - ground_z
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
        # Invert height_m = cam_z + forward*tan(angle) - ground_z for angle:
        #   tan(angle) = (H + ground_z - cam_z) / forward
        # The terrain term is ADDED here, not subtracted (it was subtracted
        # before, which double-counted the ground offset and squeezed the
        # gate by 2 x terrain_elev on any sloped site).
        max_top_angle = math.atan(
            (max_plausible_top + ground_z - cam_z) / forward)
        min_top_angle = math.atan(
            (min_plausible_top + ground_z - cam_z) / forward)
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
        if building.height_tag_m is not None and _tag_filter_enabled():
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
