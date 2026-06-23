"""skyline._pano.heading — extracted from pano_registration.py (A2 split)."""
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

def _recover_pano_heading(
    seed: "SkylinePoint",
    prefetch: list[dict],
    cached_views: list[dict],
    effective_pitch: float,
    spin_step_deg: float,
    pano_recovery_state: "dict | None",
    timer: "_StepTimer | None" = None,
) -> tuple[
    "float | None", "float | None", "float | None", "float | None",
    "float | None", "int | None", "list | None", "list | None",
]:
    """F-SKY11.1 pano-coastline heading recovery (+ F-SKY18 vegetation).

    Stitches a 360° water mask from ALL prefetch views (including rejected
    ones) and sweeps keypoints (OSM in Phase C, satellite otherwise) to
    recover the pano-to-geographic heading offset. Also projects the pano
    vegetation band base per column and depth-snaps to OSM green features
    so vegetation can be overlaid as a second bearing landmark class.

    Returns
    -------
    (pano_recovered_offset, pano_recovered_peak, pano_recovered_sigma,
     pano_water_frac, pano_osm_iou, pano_osm_n_keypoints,
     pano_projected_coastline, pano_projected_vegetation)

    All values are None when pano_recovery_state is None or recovery fails.
    """
    pano_recovered_offset: "float | None" = None
    pano_recovered_sigma: "float | None" = None
    pano_recovered_peak: "float | None" = None
    pano_water_frac: "float | None" = None
    pano_osm_iou: "float | None" = None
    pano_osm_n_keypoints: "int | None" = None
    pano_projected_coastline: "list | None" = None
    pano_projected_vegetation: "list | None" = None

    if pano_recovery_state is None:
        return (
            pano_recovered_offset, pano_recovered_peak,
            pano_recovered_sigma, pano_water_frac,
            pano_osm_iou, pano_osm_n_keypoints,
            pano_projected_coastline, pano_projected_vegetation,
        )

    def _sub(label: str):
        return timer.timed(label, level=2) if timer is not None else nullcontext()

    try:
        from ..pipeline import (
            stitch_pano_masks as _stitch_masks,
            stitch_pano_views as _stitch_rgb,
        )
        from ..coastline_registration import (
            detect_coastline_keypoints, sweep_pano_heading_offset,
        )
        # Build a set of image IDs for cached (screened) views so we can
        # use id() to avoid double-inference on the same ndarray objects.
        _cached_ids = {id(cv["image"]): cv for cv in cached_views}
        # Batch the whole pano set (superset of cached_views — includes the
        # screening-rejected views the stitch still needs) through SegFormer
        # in one forward pass. The cached subset is a no-op here (already
        # prefetched in the orchestrator); the rejected extras are inferred
        # together instead of one-at-a-time in the loop below.
        from ..pipeline import prefetch_label_maps as _prefetch  # noqa: PLC0415
        with _sub("SegFormer prefetch (batched pano set)"):
            _prefetch([e.get("image") for e in prefetch])
        _spin_views_raw = []
        for entry in prefetch:
            _img = entry.get("image")
            if _img is None:
                continue
            # If this image is already in cached_views we can reuse the
            # SegFormer masks (the LRU cache in pipeline.py handles this
            # transparently, but being explicit avoids the eviction risk on
            # large spins).
            from ..pipeline import (
                _neural_sky_and_building_masks,
                _neural_water_mask,
                _neural_vegetation_mask,
            )
            with _sub("SegFormer masks (pano-recovery prefetch)"):
                # Pull water first — it only needs the SegFormer forward pass
                # and skips morphology. Building mask is still required for
                # the stitched-pano width/height geometry in stitch_pano_masks,
                # but we let the cached label_map serve the second call so
                # the (~50 ms/image) morphology cost is paid once at most.
                _wm = _neural_water_mask(_img)
                _, _bm = _neural_sky_and_building_masks(_img)
                _vm = _neural_vegetation_mask(_img)  # F-SKY18
            _spin_views_raw.append({
                "image": _img,
                "geo_heading": float(entry["geo_heading"]),
                "building_mask": _bm,
                "water_mask": _wm,
                "vegetation_mask": _vm,
            })
        with _sub("stitch pano RGB"):
            _rgb_stitched = _stitch_rgb(_spin_views_raw, seed.fov, spin_step_deg)
        with _sub("stitch pano masks"):
            _stitched = _stitch_masks(_spin_views_raw, seed.fov, spin_step_deg)
        # F-SKY18: stitch the vegetation channel with identical geometry so
        # the same headings_per_col applies. None if no view supplied one.
        from ..pipeline import stitch_pano_mask_channel as _stitch_chan
        _pveg = _stitch_chan(_spin_views_raw, seed.fov, spin_step_deg, "vegetation_mask")
        if _rgb_stitched is not None and _stitched is not None:
            _pano_img_unused, _headings_per_col = _rgb_stitched
            _pb, _pw = _stitched

            _primary = pano_recovery_state.get("primary_source", "satellite")
            keypoints: list[dict]
            _keypoint_source: str
            if _primary == "osm":
                try:
                    from ..osm_water import (  # noqa: PLC0415
                        clip_to_radius,
                        osm_keypoints_for_scoring,
                    )
                    with _sub("OSM keypoint extraction"):
                        _osm_coast = clip_to_radius(
                            pano_recovery_state.get("osm_coastline_features") or [],
                            (seed.lon, seed.lat),
                            radius_m=1000.0,
                        )
                        keypoints = osm_keypoints_for_scoring(
                            _osm_coast, (seed.lon, seed.lat),
                            spacing_m=20.0,
                        )
                    _keypoint_source = "osm"
                except Exception as _e_kp:
                    print(f"[pano_recovery] seed={seed.name} "
                          f"OSM keypoint extraction failed: {_e_kp}")
                    keypoints = []
                    _keypoint_source = "osm-failed"
            else:
                with _sub("satellite coastline keypoints"):
                    keypoints = detect_coastline_keypoints(
                        pano_recovery_state["sat_water"],
                        pano_recovery_state["sat_project"],
                        seed.lat, seed.lon,
                        n_bearings=24, max_range_m=2500.0,
                        step_m=5.0, min_distance_m=30.0,
                    )
                _keypoint_source = "satellite"

            if keypoints and _pw is not None:
                with _sub("sweep_pano_heading_offset"):
                    best, _cand, _scores = sweep_pano_heading_offset(
                        keypoints, _pw, _headings_per_col,
                        seed.lat, seed.lon,
                        pitch_deg=effective_pitch, step_deg=1.0,
                        tolerance_px=25,
                    )
                pano_recovered_offset = float(best)
                pano_recovered_peak = float(_scores.max())
                pano_recovered_sigma = float(_scores.std())
                pano_water_frac = float(_pw.mean())

                # F-SKY18 Phase 3: vegetation-keypoint co-registration.
                # OSM parks / grass / forests get sampled into bearing
                # keypoints the same way coastline does; the sweep runs on
                # the stitched vegetation mask. Score curve is blended into
                # the water curve, peak-weighted so the channel with the
                # stronger signal dominates. The blended argmax becomes
                # the final ``pano_recovered_offset``. Falls through
                # silently when OSM has no green polygons in the seed's
                # 1 km window, or no vegetation visible in the pano.
                _veg_scores: "np.ndarray | None" = None
                _veg_peak: float = 0.0
                _veg_keypoints: list[dict] = []
                if _pveg is not None and float(_pveg.mean()) > 0.005:
                    try:
                        from ..osm_water import (  # noqa: PLC0415
                            clip_to_radius as _clip_g,
                            green_keypoints_for_scoring as _green_kps,
                        )
                        with _sub("OSM green keypoint extraction"):
                            _osm_green = _clip_g(
                                pano_recovery_state.get("osm_green_features") or [],
                                (seed.lon, seed.lat),
                                radius_m=1000.0,
                            )
                            _veg_keypoints = _green_kps(
                                _osm_green, (seed.lon, seed.lat),
                                spacing_m=20.0,
                            )
                        if _veg_keypoints:
                            with _sub("sweep_pano_heading_offset (vegetation)"):
                                _v_best, _v_cand, _v_scores = sweep_pano_heading_offset(
                                    _veg_keypoints, _pveg, _headings_per_col,
                                    seed.lat, seed.lon,
                                    pitch_deg=effective_pitch, step_deg=1.0,
                                    tolerance_px=25,
                                    # Vegetation keypoints are ground-plane
                                    # park boundaries — compare to the BASE
                                    # of the vegetation column, not the top.
                                    # The water/coastline default is the top
                                    # (horizon line).
                                    use_base_y=True,
                                )
                            _veg_scores = _v_scores
                            _veg_peak = float(_v_scores.max())
                            print(
                                f"[pano_recovery] seed={seed.name}  "
                                f"vegetation_keypoints={len(_veg_keypoints)}  "
                                f"pano_veg_frac={float(_pveg.mean()):.3f}  "
                                f"veg_recovered={float(_v_best):.1f}deg  "
                                f"veg_peak={_veg_peak:.3f}"
                            )
                    except Exception as _e_veg:
                        print(f"[pano_recovery] seed={seed.name} "
                              f"vegetation sweep failed: {_e_veg}")

                if _veg_scores is not None and _veg_peak > 0:
                    # Peak-weighted blend: each channel contributes in
                    # proportion to its own argmax score, so a weak
                    # vegetation signal doesn't drag a strong water
                    # answer off-target and vice versa.
                    w_water = pano_recovered_peak
                    w_veg = _veg_peak
                    total = w_water + w_veg
                    if total > 0:
                        blended = (
                            (w_water / total) * _scores
                            + (w_veg / total) * _veg_scores
                        )
                        _blend_best_idx = int(np.argmax(blended))
                        pano_recovered_offset = float(_cand[_blend_best_idx])
                        pano_recovered_peak = float(blended.max())
                        pano_recovered_sigma = float(blended.std())
                        print(
                            f"[pano_recovery] seed={seed.name}  "
                            f"BLENDED water+veg -> offset="
                            f"{pano_recovered_offset:.1f}deg  "
                            f"peak={pano_recovered_peak:.3f}  "
                            f"(w_water={w_water/total:.2f} w_veg={w_veg/total:.2f})"
                        )

                print(
                    f"[pano_recovery] seed={seed.name}  "
                    f"source={_keypoint_source}  "
                    f"keypoints={len(keypoints)}  "
                    f"pano_views={len(_spin_views_raw)}/12  "
                    f"pano_water_frac={pano_water_frac:.3f}  "
                    f"recovered={pano_recovered_offset:.1f}deg  "
                    f"peak={pano_recovered_peak:.3f}  "
                    f"sigma={pano_recovered_sigma:.3f}"
                )

                _pano_proj_raw: list = []
                _pveg_raw: list = []
                _osm_green: list = []
                try:
                    from ..coastline_registration import (  # noqa: PLC0415
                        pano_water_top_to_lonlat,
                        snap_points_to_osm_along_bearing,
                    )
                    pano_projected_coastline = pano_water_top_to_lonlat(
                        _pw, _headings_per_col,
                        seed.lat, seed.lon,
                        column_stride=8,
                        pitch_deg=effective_pitch,
                    )
                    # F-SKY18 depth-snap: the projected ranges are unreliable
                    # (monocular 1/tan blow-up), but bearings are exact. Slide
                    # each dot along its bearing onto the nearest OSM coastline
                    # point so the overlay lands on the real coast.
                    _pano_proj_raw[:] = list(pano_projected_coastline or [])
                    if pano_projected_coastline:
                        from ..osm_water import (  # noqa: PLC0415
                            clip_to_radius as _clip_snap,
                            sample_coastline_points as _samp_snap,
                        )
                        _osm_snap = _samp_snap(
                            _clip_snap(
                                pano_recovery_state.get("osm_coastline_features") or [],
                                (seed.lon, seed.lat), radius_m=1000.0,
                            ),
                            spacing_m=20.0,
                        )
                        if _osm_snap:
                            _n_before = len(pano_projected_coastline)
                            pano_projected_coastline = snap_points_to_osm_along_bearing(
                                pano_projected_coastline, _osm_snap,
                                seed.lat, seed.lon, max_bearing_tol_deg=4.0,
                            )
                            print(f"[F-SKY18] seed={seed.name} depth-snap: "
                                  f"{_n_before} -> {len(pano_projected_coastline)} "
                                  "coastline dots snapped to OSM")
                    # F-SKY18 Phase 2: project pano vegetation base and snap
                    # to OSM green polygon boundaries. Bearings exact; ranges
                    # corrected by the snap so dots land on real green edges.
                    if _pveg is not None:
                        from ..coastline_registration import (  # noqa: PLC0415
                            pano_vegetation_base_to_lonlat,
                        )
                        from ..osm_water import (  # noqa: PLC0415
                            sample_green_points as _samp_green,
                            clip_to_radius as _clip_green,
                        )
                        pano_projected_vegetation = pano_vegetation_base_to_lonlat(
                            _pveg, _headings_per_col,
                            seed.lat, seed.lon,
                            column_stride=8,
                            pitch_deg=effective_pitch,
                        )
                        # Keep raw (un-snapped) for the joint ICP diagnostic so
                        # cost minima aren't biased toward offset 0 by snap.
                        _pveg_raw[:] = list(pano_projected_vegetation or [])
                        _osm_green[:] = _samp_green(
                            _clip_green(
                                pano_recovery_state.get("osm_green_features") or [],
                                (seed.lon, seed.lat), radius_m=1000.0,
                            ),
                            spacing_m=20.0,
                        )
                        if pano_projected_vegetation and _osm_green:
                            _vn = len(pano_projected_vegetation)
                            pano_projected_vegetation = snap_points_to_osm_along_bearing(
                                pano_projected_vegetation, _osm_green,
                                seed.lat, seed.lon, max_bearing_tol_deg=4.0,
                            )
                            print(f"[F-SKY18] seed={seed.name} vegetation: "
                                  f"{_vn} -> {len(pano_projected_vegetation)} "
                                  "green dots snapped to OSM green "
                                  f"({len(_osm_green)} OSM green pts)")
                except Exception as _e_proj:
                    print(f"[pano_recovery] seed={seed.name} "
                          f"pano-projection failed: {_e_proj}")

                # F-SKY16 Phase A (measure-only): register the pano-
                # projected coastline against OSM coastline by rotation
                # and log the ICP-recovered offset next to the keypoint
                # sweep's. NOT wired into the anchor yet — this block
                # only prints so we can compare the two recovery methods
                # against the manual-override ground truth on Cartagena.
                try:
                    from ..coastline_registration import (  # noqa: PLC0415
                        coastline_icp_offset as _icp,
                        joint_class_icp_offset as _joint_icp,
                    )
                    from ..osm_water import (  # noqa: PLC0415
                        clip_to_radius as _clip_icp,
                        sample_coastline_points as _samp_icp,
                    )
                    if _pano_proj_raw:
                        _osm_coast_icp = _clip_icp(
                            pano_recovery_state.get(
                                "osm_coastline_features") or [],
                            (seed.lon, seed.lat), radius_m=1000.0,
                        )
                        _osm_pts_icp = _samp_icp(_osm_coast_icp, spacing_m=20.0)
                        if _osm_pts_icp:
                            _icp_off, _icp_cand, _icp_cost = _icp(
                                _pano_proj_raw, _osm_pts_icp,
                                seed.lat, seed.lon,
                                search_range_deg=180.0, step_deg=2.0,
                                max_range_m=1000.0, trim_frac=0.2,
                                init_offset_deg=pano_recovered_offset or 0.0,
                            )
                            _icp_min = (
                                float(_icp_cost.min())
                                if _icp_cost.size else float("nan"))
                            print(
                                f"[F-SKY16] seed={seed.name}  "
                                f"icp_offset={_icp_off:.1f}deg  "
                                f"icp_cost={_icp_min:.2f}deg  "
                                f"(keypoint_sweep={pano_recovered_offset:.1f}deg)  "
                                f"osm_pts={len(_osm_pts_icp)}"
                            )

                            # F-SKY18 Phase 3 (measure-only): joint ICP with
                            # vegetation added as a second landmark class.
                            # Hypothesis: vegetation is asymmetric where the
                            # bay coastline is 180°-symmetric, so the joint
                            # cost has a sharper minimum at the true offset
                            # and avoids the mirror that defeats coastline-
                            # only ICP on peninsula seeds (seed_5 regression).
                            if _pveg_raw and _osm_green:
                                _j_off, _, _j_cost, _j_per = _joint_icp(
                                    [("coast", _pano_proj_raw, _osm_pts_icp, 1.0),
                                     ("veg", _pveg_raw, _osm_green, 1.0)],
                                    seed.lat, seed.lon,
                                    search_range_deg=180.0, step_deg=2.0,
                                    max_range_m=1000.0, trim_frac=0.2,
                                    init_offset_deg=pano_recovered_offset or 0.0,
                                )
                                _j_min = (float(_j_cost.min())
                                          if _j_cost.size else float("nan"))
                                _per_min = {
                                    k: float(v.min())
                                    for k, v in _j_per.items() if v.size
                                }
                                print(
                                    f"[F-SKY18-3] seed={seed.name}  "
                                    f"joint_icp_offset={_j_off:.1f}deg  "
                                    f"joint_cost={_j_min:.2f}deg  "
                                    f"per_class_min={ _per_min }  "
                                    f"veg_pts={len(_pveg_raw)} "
                                    f"osm_green_pts={len(_osm_green)}"
                                )
                            else:
                                print(
                                    f"[F-SKY18-3] seed={seed.name}  "
                                    "joint ICP skipped (no vegetation/OSM-green)"
                                )
                except Exception as _e_icp:
                    print(f"[F-SKY16] seed={seed.name} ICP compare failed: "
                          f"{_e_icp}")

                if _primary == "osm":
                    pano_osm_iou = pano_recovered_peak
                    pano_osm_n_keypoints = len(keypoints)
                    print(
                        f"[pano_recovery] seed={seed.name}  "
                        f"osm_kp={pano_osm_n_keypoints}  "
                        f"osm_iou={pano_osm_iou:.3f}  (peak=IoU)  "
                        f"projected_pts={len(pano_projected_coastline) if pano_projected_coastline else 0}"
                    )
                else:
                    try:
                        from ..osm_water import (  # noqa: PLC0415
                            clip_to_radius as _clip,
                            osm_keypoints_for_scoring as _osm_kps_fn,
                        )
                        from ..coastline_registration import (  # noqa: PLC0415
                            score_pano_offset_keypoints,
                        )
                        _osm_coast = _clip(
                            pano_recovery_state.get("osm_coastline_features") or [],
                            (seed.lon, seed.lat),
                            radius_m=1000.0,
                        )
                        _osm_kps = _osm_kps_fn(
                            _osm_coast, (seed.lon, seed.lat),
                            spacing_m=20.0,
                        )
                        if _osm_kps:
                            pano_osm_iou = float(
                                score_pano_offset_keypoints(
                                    _osm_kps, _pw, _headings_per_col,
                                    seed.lat, seed.lon,
                                    candidate_offset_deg=pano_recovered_offset,
                                    pitch_deg=effective_pitch,
                                    tolerance_px=25,
                                )
                            )
                            pano_osm_n_keypoints = len(_osm_kps)
                        print(
                            f"[pano_recovery] seed={seed.name}  "
                            f"osm_kp={pano_osm_n_keypoints}  "
                            f"osm_iou={pano_osm_iou}  "
                            f"projected_pts={len(pano_projected_coastline) if pano_projected_coastline else 0}"
                        )
                    except Exception as _e_osm:
                        print(
                            f"[pano_recovery] seed={seed.name} "
                            f"OSM diagnostic failed: {_e_osm}"
                        )
            else:
                print(f"[pano_recovery] seed={seed.name}  "
                      f"keypoints={len(keypoints)} — no recovery attempted")
    except Exception as _e:
        print(f"[pano_recovery] seed={seed.name} failed: {_e}")

    return (
        pano_recovered_offset, pano_recovered_peak,
        pano_recovered_sigma, pano_water_frac,
        pano_osm_iou, pano_osm_n_keypoints,
        pano_projected_coastline, pano_projected_vegetation,
    )

def _recover_anchor_offset(
    seed: "SkylinePoint",
    cached_views: list[dict],
    seed_buildings: list["BuildingRecord"],
    pano_recovered_offset: "float | None",
    pano_recovered_peak: "float | None",
    pano_recovered_sigma: "float | None",
    pano_recovery_state: "dict | None",
    anchor_overrides: "dict[str, float] | None",
    timer: "_StepTimer | None" = None,
) -> float:
    """Joint-optimize the pano-to-geographic heading offset across all views.

    Returns the anchor offset in degrees to be applied to every view's
    registration search centre.
    """
    from ..pipeline import (  # noqa: PLC0415
        _score_offset_semantic_iou,
        _neural_sky_and_building_masks,
        _neural_water_mask,
    )

    def _sub(label: str):
        return timer.timed(label, level=2) if timer is not None else nullcontext()

    masks_per_view: list[tuple] = []
    with _sub("SegFormer masks (anchor IoU views)"):
        for cv in cached_views:
            _, bmask = _neural_sky_and_building_masks(cv["image"])
            wmask = _neural_water_mask(cv["image"])
            masks_per_view.append((bmask, wmask, cv["cap"].viewpoint))

    def _view_weight(bmask):
        if bmask is None:
            return 0.0
        obs = np.asarray(bmask).sum(axis=0)
        return float(np.count_nonzero(obs > 5))

    weights = [_view_weight(b) for (b, _w, _v) in masks_per_view]
    total_weight = float(sum(weights))

    override = (anchor_overrides or {}).get(seed.name)
    if override is not None:
        anchor_offset = float(override)
        if pano_recovered_offset is not None:
            _delta = (pano_recovered_offset - anchor_offset + 540.0) % 360.0 - 180.0
            print(f"[pano_recovery] seed={seed.name}  "
                  f"manual={anchor_offset:.1f}deg  "
                  f"delta_to_recovered={_delta:+.1f}deg")
        return anchor_offset

    if total_weight <= 0:
        return 0.0

    def _joint_score(cand: float) -> float:
        total = 0.0
        for (bmask, wmask, vp), wt in zip(masks_per_view, weights):
            if bmask is None or wt <= 0:
                continue
            s = _score_offset_semantic_iou(seed_buildings, vp, cand, bmask, wmask)
            total += s * wt
        return total / total_weight

    _PANO_RECOVERY_SHARP_SIGMA = 0.10
    _PANO_RECOVERY_MIN_PEAK = 0.40
    # F-SKY11.1 Phase B: env flag enables automatic pano-seeding when peak is
    # sharp. Falls back to per-site "drive_anchor" config key for opt-in
    # without the global flag.
    allow_drive = _F_SKY11_1_ENABLED or bool(
        (pano_recovery_state or {}).get("drive_anchor", False)
    )
    use_pano_seed = (
        allow_drive
        and pano_recovered_offset is not None
        and pano_recovered_sigma is not None
        and pano_recovered_peak is not None
        and pano_recovered_sigma <= _PANO_RECOVERY_SHARP_SIGMA
        and pano_recovered_peak > _PANO_RECOVERY_MIN_PEAK
    )
    if use_pano_seed:
        recovered = pano_recovered_offset
        if recovered > 180.0:
            recovered -= 360.0
        fine_offsets = np.arange(recovered - 15.0, recovered + 15.0 + 0.001, 1.0)
        best_anchor_offset = recovered
        best_sum_iou = _joint_score(float(recovered))
        with _sub("anchor fine sweep (pano-seeded)"):
            for cand in fine_offsets:
                s = _joint_score(float(cand))
                if s > best_sum_iou:
                    best_sum_iou = s
                    best_anchor_offset = float(cand)
        anchor_offset = best_anchor_offset
        print(f"[pano_recovery] seed={seed.name}  "
              f"USED pano seed -> anchor={anchor_offset:.1f}deg  "
              f"(joint_iou={best_sum_iou:.3f})")
    else:
        coarse_offsets = np.arange(-180.0, 180.0, 3.0)
        h_token = float(seed.heading) if seed.heading is not None else None
        coarse_best = -float("inf")
        coarse_best_offset = h_token if h_token is not None else 0.0
        with _sub("anchor coarse sweep (3° over 360°)"):
            for cand in coarse_offsets:
                s = _joint_score(float(cand))
                if s > coarse_best:
                    coarse_best = s
                    coarse_best_offset = float(cand)
        fine_offsets = np.arange(
            coarse_best_offset - 5.0, coarse_best_offset + 5.0 + 0.001, 0.5)
        best_anchor_offset = coarse_best_offset
        best_sum_iou = coarse_best
        with _sub("anchor fine sweep (0.5° ±5°)"):
            for cand in fine_offsets:
                s = _joint_score(float(cand))
                if s > best_sum_iou:
                    best_sum_iou = s
                    best_anchor_offset = float(cand)
        anchor_offset = best_anchor_offset

    return anchor_offset


__all__ = [
    '_recover_pano_heading',
    '_recover_anchor_offset',
]
