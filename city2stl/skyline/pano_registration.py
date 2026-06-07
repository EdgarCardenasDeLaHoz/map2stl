"""city2stl.skyline.pano_registration - per-seed multi-view registration.

Split out of region_pdf.py (F-CLEAN14, 2026-06-07). The core per-seed loop:
capture the 12-view spin, recover pano heading + joint anchor offset, per-view
match, cross-view smoothing, the 360 pano stitch + splitters, and the
_seed_multiview_registration orchestrator. Calls region_render for the
overlay/negative-seed helpers (one-directional; render does not call back).
region_pdf re-imports these.
"""

from __future__ import annotations

import json
import math
import os
import time
from contextlib import nullcontext
from pathlib import Path

import cv2
import numpy as np

from .pipeline import (
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
from .region_types import SeedViewRegistration, SkylinePoint, StitchedPanoResult
from .region_config import _F_SKY1_ENABLED, _F_SKY12_ENABLED, _F_SKY5_ENABLED
from .region_data import _bearing_deg, _distance_m, _fetch_elevations
from .streetview_io import _meta_location, _streetview_image, _streetview_metadata
from .seed_selection import _screen_score_from_image
from .region_render import _negative_seed_views, _registration_overlay


def _capture_pano_views(
    seed: "SkylinePoint",
    api_key: str,
    spin_headings: tuple[float, ...],
    is_photosphere: bool,
    timer: "_StepTimer | None" = None,
) -> tuple[list[dict], float, list[dict]]:
    """Pass 1: fetch every spin-view image, apply pitch correction if needed.

    Returns
    -------
    prefetch       : raw list of ``{"geo_heading", "image", ...}`` dicts for
                     ALL headings (including rejected views — pano-recovery needs
                     them). ``image`` is None when the fetch failed.
    effective_pitch: the pitch all views were captured at (may differ from
                     ``seed.pitch`` if pitch correction fired).
    cached_views   : subset of ``prefetch`` with images that passed the
                     per-view screening gate and are suitable for registration.
    """
    def _sub(label: str):
        return timer.timed(label, level=2) if timer is not None else nullcontext()

    # Pass 1a: fetch at original pitch and screen.
    prefetch: list[dict] = []
    any_needs_pitch_correction = False
    for geo_heading in spin_headings:
        with _sub("sv image fetch (cached/network)"):
            image = _streetview_image(
                api_key,
                seed.lat,
                seed.lon,
                geo_heading,
                fov=seed.fov,
                pitch=seed.pitch,
                pano_id=seed.pano_id,
                pano_only=is_photosphere,
            )
        if image is None:
            prefetch.append({"geo_heading": geo_heading, "image": None})
            continue
        with _sub("screen score (SegFormer)"):
            sv_score, sv_label, _, _tc = _screen_score_from_image(
                image, pitch=seed.pitch)
        if sv_label == "rejected":
            prefetch.append({
                "geo_heading": geo_heading,
                "image": image,
                "sv_score": 0.0,
                "sv_label": "rejected",
            })
            continue
        if seed.pitch < 8.0 and (seed.pitch < -8.0 or _tc):
            any_needs_pitch_correction = True
        prefetch.append({
            "geo_heading": geo_heading,
            "image": image,
            "sv_score": sv_score,
            "sv_label": sv_label,
        })

    # Pass 1b: pitch correction — re-fetch all views at corrected pitch.
    effective_pitch = seed.pitch
    n_original = sum(1 for e in prefetch if e["image"] is not None)
    if any_needs_pitch_correction and n_original > 0:
        corrected_pitch = min(seed.pitch + 12.0, 15.0)
        corrected_prefetch: list[dict] = []
        n_kept = 0
        for entry in prefetch:
            gh = entry["geo_heading"]
            if entry["image"] is None:
                corrected_prefetch.append({"geo_heading": gh, "image": None})
                continue
            with _sub("sv image fetch (cached/network)"):
                c_image = _streetview_image(
                    api_key, seed.lat, seed.lon, gh,
                    fov=seed.fov, pitch=corrected_pitch,
                    pano_id=seed.pano_id, pano_only=is_photosphere,
                )
            if c_image is None:
                corrected_prefetch.append({"geo_heading": gh, "image": None})
                continue
            with _sub("screen score (SegFormer)"):
                c_score, c_label, _, _ = _screen_score_from_image(
                    c_image, pitch=corrected_pitch)
            if c_label == "rejected":
                corrected_prefetch.append({
                    "geo_heading": gh,
                    "image": c_image,
                    "sv_score": 0.0,
                    "sv_label": "rejected",
                })
                continue
            corrected_prefetch.append({
                "geo_heading": gh,
                "image": c_image,
                "sv_score": c_score,
                "sv_label": c_label,
            })
            n_kept += 1
        if n_kept >= max(1, (n_original + 1) // 2):
            prefetch = corrected_prefetch
            effective_pitch = corrected_pitch
            print(
                f"[pano pitch] {seed.name} corrected to {effective_pitch:+.1f}° "
                f"({n_kept}/{n_original} views retained)")
        else:
            print(
                f"[pano pitch] {seed.name} keeping original {seed.pitch:+.1f}° "
                f"(corrected pitch retained only {n_kept}/{n_original})")

    # Build cached_views (screened views only) with Viewpoint + CapturedView.
    cached_views: list[dict] = []
    for entry in prefetch:
        image = entry["image"]
        if image is None:
            continue
        if entry.get("sv_label") == "rejected":
            continue
        geo_heading = entry["geo_heading"]
        vp = Viewpoint(
            name=f"{seed.name}_{int(round(geo_heading))%360:03d}",
            query=seed.name,
            lat=seed.lat,
            lon=seed.lon,
            heading=geo_heading,
            pitch=effective_pitch,
            fov=seed.fov,
            image_width=image.shape[1],
            image_height=image.shape[0],
        )
        cap = CapturedView(
            viewpoint=vp, image_path=Path("."),
            metadata_path=Path("."), image=image,
        )
        cached_views.append({
            "geo_heading": geo_heading,
            "image": image,
            "cap": cap,
            "sv_score": entry["sv_score"],
            "sv_label": entry["sv_label"],
            "wide_reg": None,
            "wide_score": float("inf"),
        })
    return prefetch, effective_pitch, cached_views

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
        from .pipeline import (
            stitch_pano_masks as _stitch_masks,
            stitch_pano_views as _stitch_rgb,
        )
        from .coastline_registration import (
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
        from .pipeline import prefetch_label_maps as _prefetch  # noqa: PLC0415
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
            from .pipeline import (
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
        from .pipeline import stitch_pano_mask_channel as _stitch_chan
        _pveg = _stitch_chan(_spin_views_raw, seed.fov, spin_step_deg, "vegetation_mask")
        if _rgb_stitched is not None and _stitched is not None:
            _pano_img_unused, _headings_per_col = _rgb_stitched
            _pb, _pw = _stitched

            _primary = pano_recovery_state.get("primary_source", "satellite")
            keypoints: list[dict]
            _keypoint_source: str
            if _primary == "osm":
                try:
                    from .osm_water import (  # noqa: PLC0415
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
                        from .osm_water import (  # noqa: PLC0415
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
                try:
                    from .coastline_registration import (  # noqa: PLC0415
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
                        from .osm_water import (  # noqa: PLC0415
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
                        from .coastline_registration import (  # noqa: PLC0415
                            pano_vegetation_base_to_lonlat,
                        )
                        from .osm_water import (  # noqa: PLC0415
                            sample_green_points as _samp_green,
                            clip_to_radius as _clip_green,
                        )
                        pano_projected_vegetation = pano_vegetation_base_to_lonlat(
                            _pveg, _headings_per_col,
                            seed.lat, seed.lon,
                            column_stride=8,
                            pitch_deg=effective_pitch,
                        )
                        _osm_green = _samp_green(
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
                    from .coastline_registration import (  # noqa: PLC0415
                        coastline_icp_offset as _icp,
                    )
                    from .osm_water import (  # noqa: PLC0415
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
                        from .osm_water import (  # noqa: PLC0415
                            clip_to_radius as _clip,
                            osm_keypoints_for_scoring as _osm_kps_fn,
                        )
                        from .coastline_registration import (  # noqa: PLC0415
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
    from .pipeline import (  # noqa: PLC0415
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
    allow_drive = bool((pano_recovery_state or {}).get("drive_anchor", False))
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

    from .pipeline import (  # noqa: PLC0415
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
            from .pipeline import (  # noqa: PLC0415
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
                    from .cross_view import make_cross_view_scorer
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
        from .pipeline import (  # noqa: PLC0415
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
        from . import pipeline as _p  # noqa: PLC0415

        model = _p._segformer_model
        processor = SegformerImageProcessor.from_pretrained(_p._SEGFORMER_MODEL_ID)
        processor.size = {"height": int(fine_input_size), "width": int(fine_input_size)}
        processor.do_resize = True

        # Optional depth predictor for F-SKY21 instance separation.
        depth_predictor = None
        if use_depth:
            try:
                from .depth_estimation import predict_pano_depth  # noqa: PLC0415
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

def _stitch_pano_composite(
    seed: "SkylinePoint",
    cached_views: list[dict],
    seed_buildings: list["BuildingRecord"],
    anchor_offset: float,
    spin_step_deg: float,
    prefetch_views: list[dict] | None = None,
    timer: "_StepTimer | None" = None,
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
        # Level-2 sub-step under the level-1 "stitch pano composite"
        # phase, so the new pano-stage work (tiled depth, bearing
        # recovery, sliding-window split) shows up in the timing report.
        return timer.timed(label, level=2) if timer is not None else nullcontext()

    try:
        from .pipeline import (  # noqa: PLC0415
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
            from .pipeline import _neural_vegetation_mask  # noqa: PLC0415
        except Exception:
            _neural_vegetation_mask = None
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
            from .depth_estimation import predict_pano_depth_tiled  # noqa: PLC0415
            t_dep = time.perf_counter()
            # F-SKY24 Phase 2 depth experiment: tile the pano at full
            # 518-px resolution per tile (HF pipeline's native input
            # size) instead of letting the pipeline squash a 2688-wide
            # pano down to ~518. Trades inference time for resolution.
            with _sub("pano depth (tiled DA2)"):
                pano_depth_arr = predict_pano_depth_tiled(np.asarray(pano_img))
            print(
                f"[pano_depth] seed={seed.name} {pano_depth_arr.shape} "
                f"tiled-inference {time.perf_counter() - t_dep:.2f}s"
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
        try:
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
                from .depth_estimation import (  # noqa: PLC0415
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
                # Confidence gate: apply only when rotating to the best
                # offset is a large improvement over the CURRENT anchor
                # (offset 0). Empirically (Cartagena vs Chicago) this
                # cleanly separates already-aligned seeds (improve <30%,
                # the offset-0 anchor is already near-optimal) from
                # genuinely-misaligned ones (improve >50%, e.g. Chicago
                # seeds whose satellite recovery landed ~180° off). The
                # 3000 m NO_BUILDING sentinel is what makes pre_mae
                # meaningfully large for a wrong anchor — without it,
                # saturation flattened the landscape and every seed
                # looked "improvable".
                improve = (pre_mae - best_mae) / max(pre_mae, 1e-6)
                if abs(shift) >= 15 and improve >= 0.45:
                    print(
                        f"[bearing_xcorr] seed={seed.name} "
                        f"shift {shift:+d}° APPLIED "
                        f"(pre MAE {pre_mae:.0f} -> {best_mae:.0f}m, "
                        f"improve {improve*100:.0f}%)"
                    )
                    pano_headings = (pano_headings + float(shift)) % 360.0
                else:
                    print(
                        f"[bearing_xcorr] seed={seed.name} "
                        f"shift {shift:+d}° SKIPPED "
                        f"(pre MAE {pre_mae:.0f}, best {best_mae:.0f}m, "
                        f"improve {improve*100:.0f}%) — "
                        f"keeping existing anchor"
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
        # Project OSM footprints to pano column coordinates BEFORE the
        # OSM-anchored split below so the splitter has the projection
        # info it needs. Same call we used to make later; just hoisted.
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
            from .pipeline import osm_anchor_silhouettes  # noqa: PLC0415
            pre_n = len(pano_segs)
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
        n_matched = sum(1 for s in pano_matched if s.get("matched_projection"))

        # OSM-anchored depth->distance scale, computed ONCE here so the
        # Reconstruction polar plot and the Distance scan use the same
        # calibrated value (previously the scan hardcoded 1450 while the
        # recon anchored to OSM, so the same depth read ~1000 m on one and
        # ~600 m on the other). scale = median(osm_dist / sqrt(d_inv))
        # over matched towers; sqrt because DA2 inverse-depth saturates.
        depth_scale = 1450.0
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
            if ratios:
                ratios.sort()
                depth_scale = float(ratios[len(ratios) // 2])

        # Stitch sky + vegetation as separate pano-coord channels (same
        # source view set & sort order as the building/water stitches
        # above, so the column geometry agrees with ``pano_img``).
        try:
            from .pipeline import stitch_pano_mask_channel  # noqa: PLC0415
            pano_sky_arr = stitch_pano_mask_channel(
                spin_views_for_pano, seed.fov, spin_step_deg, "sky_mask")
            pano_veg_arr = stitch_pano_mask_channel(
                spin_views_for_pano, seed.fov, spin_step_deg, "vegetation_mask")
        except Exception:
            pano_sky_arr = None
            pano_veg_arr = None
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
        )
    except Exception as e:
        import sys as _sys
        print(f"[pano] seed={seed.name} failed: {e}", file=_sys.stderr)
        return None

def _seed_multiview_registration(
    seeds: list[SkylinePoint],
    buildings: list[BuildingRecord],
    api_key: str,
    spin_step_deg: float = 30.0,
    anchor_overrides: dict[str, float] | None = None,
    negative_seeds: set[str] | None = None,
    trace=None,
    max_plausible_height_m: float = 300.0,
    cross_view_state: dict | None = None,
    pano_recovery_state: dict | None = None,
    timer: "_StepTimer | None" = None,
) -> tuple[list[SeedViewRegistration], list[dict], list["StitchedPanoResult"]]:
    """Capture a full 360° spin (every spin_step_deg) at each seed location.

    Each successful registration contributes height estimates to the aggregate.
    The original seed.heading from the URL is ignored on purpose — we want
    coverage of every direction from a seed, not a narrow fan.

    For seeds with a pano_id (parsed from the URL) we hit that exact pano so
    the captured image actually corresponds to where the user pointed.
    Without pano_id, we resolve the snapped pano via metadata once per seed
    and rebind lat/lon/pano_id so projections, mini-map, and the image all
    agree on a single camera position.

    F-CLEAN8: this function is now a thin orchestrator. The five named
    helpers implement each phase:
      _capture_pano_views        — Pass 1: fetch + pitch-correct spin views
      _recover_pano_heading      — F-SKY11.1 pano-coastline heading recovery
      _recover_anchor_offset     — joint IoU optimization across all views
      _register_views            — Pass 2: per-view registration + heights
      _stitch_pano_composite     — Pass 3: 360° pano stitch + matching
    """
    # ── Step 1: resolve every seed to a renderable pano position. ──────────
    # Cached to keep multi-run results reproducible (the Static API's
    # location-based snap returns different panos on different calls).
    _resolve_cache_path = (
        Path(__file__).parent / "runs" / "seed_resolution_cache.json"
    )
    _resolve_cache: dict = {}
    if _resolve_cache_path.exists():
        try:
            _resolve_cache = json.loads(
                _resolve_cache_path.read_text(encoding="utf-8"))
        except Exception:
            _resolve_cache = {}

    def _cache_save() -> None:
        try:
            _resolve_cache_path.parent.mkdir(parents=True, exist_ok=True)
            _resolve_cache_path.write_text(
                json.dumps(_resolve_cache, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _seed_cache_key(s: SkylinePoint) -> str:
        return f"{s.name}|{s.lat:.6f}|{s.lon:.6f}|{s.pano_id or ''}"

    resolved: list[tuple[SkylinePoint, float, bool]] = []
    seed_elevs = _fetch_elevations([(s.lat, s.lon) for s in seeds])
    for seed, seed_elev in zip(seeds, seed_elevs):
        bound: SkylinePoint | None = None
        cache_key = _seed_cache_key(seed)

        cached = _resolve_cache.get(cache_key)
        if cached is not None:
            try:
                cached_pano = cached.get("pano_id") or None
                cached_lat = float(cached["lat"])
                cached_lon = float(cached["lon"])
                probe = _streetview_image(
                    api_key, cached_lat, cached_lon, 0.0,
                    fov=seed.fov, pitch=seed.pitch,
                    pano_id=cached_pano, radius_m=50,
                    pano_only=cached_pano is not None,
                )
                if probe is not None:
                    bound = SkylinePoint(
                        name=seed.name, lat=cached_lat, lon=cached_lon,
                        heading=seed.heading, source=seed.source,
                        score=seed.score, fov=seed.fov,
                        pitch=seed.pitch, pano_id=cached_pano,
                    )
            except Exception:
                pass

        if bound is None and seed.pano_id:
            probe = _streetview_image(
                api_key, seed.lat, seed.lon, 0.0,
                fov=seed.fov, pitch=seed.pitch,
                pano_id=seed.pano_id, radius_m=200, pano_only=True,
            )
            if probe is not None:
                bound = seed

        if bound is None:
            for r in (200, 500, 1500, 3000):
                try:
                    meta = _streetview_metadata(
                        api_key, seed.lat, seed.lon, 0.0,
                        fov=seed.fov, pitch=seed.pitch,
                        pano_id=None, radius_m=r,
                    )
                except Exception:
                    continue
                if str(meta.get("status")) != "OK":
                    continue
                snap = _meta_location(meta)
                snap_pano = str(meta.get("pano_id") or "") or None
                if snap is None:
                    continue
                probe = _streetview_image(
                    api_key, snap[0], snap[1], 0.0,
                    fov=seed.fov, pitch=seed.pitch,
                    pano_id=snap_pano, radius_m=50,
                )
                if probe is None:
                    continue
                bound = SkylinePoint(
                    name=seed.name, lat=snap[0], lon=snap[1],
                    heading=seed.heading, source=seed.source,
                    score=seed.score, fov=seed.fov,
                    pitch=seed.pitch, pano_id=snap_pano,
                )
                break

        if bound is not None and cache_key not in _resolve_cache:
            _resolve_cache[cache_key] = {
                "lat": float(bound.lat),
                "lon": float(bound.lon),
                "pano_id": bound.pano_id,
            }
            _cache_save()
        if bound is None:
            continue
        is_photosphere = (
            seed.pano_id is not None and bound.pano_id == seed.pano_id)
        resolved.append((bound, float(seed_elev), is_photosphere))

    # ── Step 2: per-seed orchestration via named helpers. ───────────────────
    spin_headings = tuple(float(h)
                          for h in np.arange(0.0, 360.0, spin_step_deg))
    view_rows: list[SeedViewRegistration] = []
    pano_results: list[StitchedPanoResult] = []
    all_estimates: list = []

    def _buildings_near_seed(seed_lat: float, seed_lon: float, radius_m: float = 4500.0):
        mlat = 110_540.0
        mlon = 111_320.0 * math.cos(math.radians(seed_lat))
        rad_sq = radius_m * radius_m
        out: list[BuildingRecord] = []
        for b in buildings:
            dx = (b.centroid_lon - seed_lon) * mlon
            dy = (b.centroid_lat - seed_lat) * mlat
            if dx * dx + dy * dy <= rad_sq:
                out.append(b)
        return out

    def _phase(label: str):
        # Level-1 sub-step timer; no-op when no timer was supplied. Repeated
        # calls (once per seed) accumulate into a single summed row.
        return timer.timed(label, level=1) if timer is not None else nullcontext()

    for seed, seed_elev, is_photosphere in resolved:
        seed_buildings = _buildings_near_seed(seed.lat, seed.lon)
        # Pass 1: fetch + pitch-correct all spin views.
        with _phase("capture pano views"):
            prefetch, effective_pitch, cached_views_for_seed = _capture_pano_views(
                seed, api_key, spin_headings, is_photosphere, timer=timer,
            )
        if not cached_views_for_seed:
            continue

        # Batch every spin view through SegFormer in one (chunked) forward
        # pass up front. Each later per-view mask call (anchor IoU sweep,
        # registration, stitch) is then a cache hit on the same ndarray —
        # identical numerics, but the transformer's fixed per-call overhead
        # is paid once for the spin instead of 12×. No-op fallback to lazy
        # per-image inference if the model is unavailable.
        with _phase("SegFormer prefetch (batched spin)"):
            from .pipeline import prefetch_label_maps as _prefetch  # noqa: PLC0415
            _prefetch([cv["image"] for cv in cached_views_for_seed])

        # Negative seed: a known-bad skyline. Skip all analysis (heading
        # recovery, anchor, registration, stitch) — just keep the captured
        # frames as labelled examples for future negative-mining.
        if negative_seeds and seed.name in negative_seeds:
            view_rows.extend(_negative_seed_views(seed, cached_views_for_seed))
            print(f"[negative_seed] {seed.name}: analysis skipped, "
                  f"{len(cached_views_for_seed)} frames kept as bad-skyline example")
            continue

        # Retrieve the effective pitch from the helper's output.
        effective_pitch = cached_views_for_seed[0]["cap"].viewpoint.pitch if cached_views_for_seed else seed.pitch

        # Pano-coastline heading recovery (F-SKY11.1 / F-SKY13 Phase C
        # + F-SKY18 pano vegetation).
        with _phase("recover pano heading (F-SKY11/13)"):
            (
                pano_recovered_offset, pano_recovered_peak,
                pano_recovered_sigma, pano_water_frac,
                pano_osm_iou, pano_osm_n_keypoints,
                pano_projected_coastline, pano_projected_vegetation,
            ) = _recover_pano_heading(
                seed, prefetch, cached_views_for_seed,
                effective_pitch, spin_step_deg, pano_recovery_state,
                timer=timer,
            )

        # Joint anchor optimization across all views.
        with _phase("recover anchor offset (joint IoU)"):
            anchor_offset = _recover_anchor_offset(
                seed, cached_views_for_seed, seed_buildings,
                pano_recovered_offset, pano_recovered_peak, pano_recovered_sigma,
                pano_recovery_state, anchor_overrides,
                timer=timer,
            )

        # Pass 2: per-view registration + height extraction.
        with _phase("register views + heights"):
            seed_view_rows, seed_estimates = _register_views(
                seed, seed_elev, cached_views_for_seed, seed_buildings,
                anchor_offset, cross_view_state, negative_seeds,
                max_plausible_height_m,
                pano_osm_iou, pano_osm_n_keypoints, pano_projected_coastline,
                pano_recovered_offset, pano_recovered_peak,
                pano_recovered_sigma, pano_water_frac,
                trace=trace,
                timer=timer,
                pano_projected_vegetation=pano_projected_vegetation,
            )

        # Cross-view match smoothing (consistency vote): the per-view
        # matcher occasionally lands a single segment on a different
        # OSM building than the consensus across the seed's other views
        # see — the classic "5 views agree tower X is at this bearing,
        # one dissenter picks a neighbour". Promote popular candidates
        # from match_diagnostics where the current match was chosen by
        # only one view and a runner-up was chosen by ≥2 others.
        with _phase("cross-view match smoothing"):
            _smooth_matches_across_views(seed_view_rows)

        view_rows.extend(seed_view_rows)
        all_estimates.extend(seed_estimates)

        # Pass 3: stitched-pano detection (supplementary, non-fatal).
        is_negative_seed_for_pano = bool(negative_seeds and seed.name in negative_seeds)
        if not is_negative_seed_for_pano:
            with _phase("stitch pano composite"):
                pano_result = _stitch_pano_composite(
                    seed, cached_views_for_seed, seed_buildings,
                    anchor_offset, spin_step_deg,
                    prefetch_views=prefetch,
                    timer=timer,
                )
            if pano_result is not None:
                # Apply the same cross-view smoothing logic to the pano
                # matches. Build the per-view fid popularity from the
                # smoothed view rows and promote any pano match that
                # dissents from a popular per-view consensus. The pano
                # matcher operates on a stitched composite and has no
                # access to the per-view bearing-by-bearing consensus
                # by itself.
                _smooth_pano_matches_against_views(
                    pano_result, seed_view_rows)
                pano_results.append(pano_result)

    # F-SKY1 diagnostic summary: how many estimates carried a usable
    # floor-period reading, and the spread of the OSM-independent
    # inferred height. Confirms the facade-striation signal is live and
    # gives a feel for its reliability before it feeds the aggregate.
    if _F_SKY1_ENABLED and all_estimates:
        fp = [e for e in all_estimates
              if getattr(e, "floor_period_px", None) is not None]
        if fp:
            import statistics as _stats  # noqa: PLC0415
            ih = [float(e.inferred_height_m) for e in fp
                  if getattr(e, "inferred_height_m", None) is not None]
            idm = [float(e.inferred_distance_m) for e in fp
                   if getattr(e, "inferred_distance_m", None) is not None]
            conf = [float(e.floor_confidence) for e in fp
                    if getattr(e, "floor_confidence", None) is not None]
            print(
                f"[F-SKY1] floor-period hits: {len(fp)}/"
                f"{len(all_estimates)} estimates"
                + (f"; inferred_height med "
                   f"{_stats.median(ih):.0f}m" if ih else "")
                + (f"; inferred_distance med "
                   f"{_stats.median(idm):.0f}m" if idm else "")
                + (f"; confidence med {_stats.median(conf):.2f}"
                   if conf else "")
            )
        else:
            print(
                f"[F-SKY1] floor-period hits: 0/{len(all_estimates)} "
                f"estimates (no facade locked a period)"
            )

    agg = aggregate_building_heights(all_estimates) if all_estimates else []
    return view_rows, agg, pano_results
