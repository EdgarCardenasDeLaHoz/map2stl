"""skyline._pano.orchestrator — extracted from pano_registration.py (A2 split)."""
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

from .capture import _capture_pano_views
from .heading import _recover_pano_heading, _recover_anchor_offset
from .detect import (_register_views, _smooth_matches_across_views,
                     _smooth_pano_matches_against_views, _build_and_detect_pano)

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
    web_image_cache: "dict[str, np.ndarray] | None" = None,
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
      _build_and_detect_pano     — Pass 3: 360° pano stitch + matching
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

    # Per-region building-coverage baseline from the USER (manual) seeds —
    # they define what a "good skyline" looks like for THIS region. A
    # near-skyline region (Cartagena: towers fill the frame) has high
    # user-seed coverage; a far-skyline region (Chicago Loop: small
    # distant towers) has low user-seed coverage. So an auto-seed is
    # judged RELATIVE to that baseline, not an absolute %, which doesn't
    # transfer (Chicago's rich autos sit at the same 8-10% pixel coverage
    # as Cartagena's weak one). Populated as user seeds are processed.
    _user_seed_covs: list[float] = []

    def _best_building_coverage(views: list[dict]) -> float:
        best = 0.0
        for cv in views:
            _, _bm = _neural_sky_and_building_masks(cv["image"])
            if _bm is not None and _bm.size:
                best = max(best, float(_bm.mean()))
        return best

    # Track resolved pano_ids to skip duplicates. Multiple auto-proposed seeds
    # (or user seeds at similar positions) often snap to the same Street View
    # pano — processing the same 360° spin twice wastes compute and floods the
    # report with identical panos. Dedup by the RESOLVED pano_id, not the
    # seed's nominal location, because snapping is what causes the collision.
    _seen_pano_ids: set[str] = set()

    for seed, seed_elev, is_photosphere in resolved:
        if seed.pano_id and seed.pano_id in _seen_pano_ids:
            print(f"[dedup] {seed.name}: pano_id={seed.pano_id!r} already "
                  "processed by an earlier seed — skipping duplicate")
            continue
        if seed.pano_id:
            _seen_pano_ids.add(seed.pano_id)

        seed_buildings = _buildings_near_seed(seed.lat, seed.lon)
        # Pass 1: fetch + pitch-correct all spin views.
        with _phase("capture pano views"):
            prefetch, effective_pitch, cached_views_for_seed = _capture_pano_views(
                seed, api_key, spin_headings, is_photosphere, timer=timer,
                web_image_cache=web_image_cache,
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
            from ..pipeline import prefetch_label_maps as _prefetch  # noqa: PLC0415
            _prefetch([cv["image"] for cv in cached_views_for_seed])

        # Negative seed: a known-bad skyline. Skip all analysis (heading
        # recovery, anchor, registration, stitch) — just keep the captured
        # frames as labelled examples for future negative-mining.
        if negative_seeds and seed.name in negative_seeds:
            view_rows.extend(_negative_seed_views(seed, cached_views_for_seed))
            print(f"[negative_seed] {seed.name}: analysis skipped, "
                  f"{len(cached_views_for_seed)} frames kept as bad-skyline example")
            continue

        # PANO SCREEN — reject bad panos right after the cheap cached
        # SegFormer pass, BEFORE the ~30-40s recovery+anchor+register+
        # detect. A rejected pano is marked bad ("is_negative") and KEPT,
        # frames-only, as a labelled bad example for future training; it
        # is shown in the report but runs no further steps. Two gates:
        #   * HARD 5% floor on building coverage for EVERY pano (incl.
        #     URL/user seeds) — catches discovered seeds that face water /
        #     a blank wall (e.g. Toronto's auto-discovered vantages).
        #   * ADAPTIVE cut for auto-seeds, relative to the region's GOOD
        #     user-seed coverage (0.35× median) — absolute thresholds
        #     don't transfer (Chicago's rich far-skyline autos sit at the
        #     same 8-10% as Cartagena's weak near one), so judge per
        #     region. Only good user seeds feed the baseline.
        _best_cov = _best_building_coverage(cached_views_for_seed)
        _is_auto = seed.name.startswith("auto")
        _cut = 0.05  # hard floor, all panos
        if _is_auto and _user_seed_covs:
            _cut = max(0.05, 0.35 * float(np.median(_user_seed_covs)))
        print(f"[pano_screen] {seed.name}: building coverage "
              f"{_best_cov*100:.1f}% (reject < {_cut*100:.1f}%)")
        if _best_cov < _cut:
            print(f"[pano_screen] {seed.name}: BAD PANO — coverage "
                  f"{_best_cov*100:.1f}% < {_cut*100:.1f}%; kept as bad "
                  f"example, no recovery/anchor/register/detect")
            view_rows.extend(_negative_seed_views(
                seed, cached_views_for_seed, reason=(
                    f"low building coverage {_best_cov*100:.0f}%")))
            continue
        if not _is_auto:
            _user_seed_covs.append(_best_cov)

        # F-DET1: blob-count early-out.
        # Count cv2 connected building-mask components across the best 3 views
        # (SegFormer already batched above = cache hits, ~1 ms each).
        # If total blobs < threshold, the camera almost certainly isn't facing
        # a skyline — skip the entire 30–60 s recovery+anchor+register chain.
        _FDET1_MIN_BLOBS = 6
        _top3_views = sorted(
            cached_views_for_seed,
            key=lambda _cv: _cv.get("sv_score", 0.0),
            reverse=True,
        )[:3]
        _total_blobs = 0
        for _cv3 in _top3_views:
            _, _bm3 = _neural_sky_and_building_masks(_cv3["image"])
            if _bm3 is not None and _bm3.any():
                _n_labels, _ = cv2.connectedComponents(
                    (_bm3 > 0).astype(np.uint8))
                _total_blobs += max(0, _n_labels - 1)  # 0 is background
        print(f"[F-DET1] {seed.name}: building blobs (top-3 views) = {_total_blobs}")
        if _total_blobs < _FDET1_MIN_BLOBS:
            print(f"[F-DET1] {seed.name}: EARLY-OUT — "
                  f"{_total_blobs} blobs < {_FDET1_MIN_BLOBS}; "
                  "no skyline detected, kept as bad example")
            view_rows.extend(_negative_seed_views(
                seed, cached_views_for_seed,
                reason=f"low building detection ({_total_blobs} blobs)"))
            continue

        # Retrieve the effective pitch from the helper's output.
        effective_pitch = cached_views_for_seed[0]["cap"].viewpoint.pitch if cached_views_for_seed else seed.pitch

        # Pano-coastline heading recovery (F-SKY11.1 / F-SKY13 Phase C
        # + F-SKY18 pano vegetation).
        # Skip the expensive coastline/vegetation heading recovery when a
        # manual ``anchor_offsets_deg`` override exists for this seed: the
        # recovery's result would be DISCARDED by _recover_anchor_offset
        # (the override wins), so running it just burns the rejected-view
        # SegFormer + the 360° vegetation sweep for nothing. Recovery
        # diagnostics (IoU, projected coastline) are then unavailable for
        # those seeds — purely cosmetic.
        _has_manual_anchor = (anchor_overrides or {}).get(seed.name) is not None
        if _has_manual_anchor:
            (
                pano_recovered_offset, pano_recovered_peak,
                pano_recovered_sigma, pano_water_frac,
                pano_osm_iou, pano_osm_n_keypoints,
                pano_projected_coastline, pano_projected_vegetation,
            ) = (None, None, None, None, None, None, None, None)
            print(f"[recover] {seed.name}: skipped heading recovery "
                  f"(manual anchor override present)")
        else:
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
            with _phase("pano detection (stitch+depth+split+match)"):
                pano_result = _build_and_detect_pano(
                    seed, cached_views_for_seed, seed_buildings,
                    anchor_offset, spin_step_deg,
                    prefetch_views=prefetch,
                    timer=timer,
                    osm_green_features=(
                        pano_recovery_state.get("osm_green_features")
                        if pano_recovery_state else None),
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


__all__ = [
    '_seed_multiview_registration',
]
