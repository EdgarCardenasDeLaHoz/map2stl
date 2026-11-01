"""Part of ``city2stl.skyline._pano.elevated`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging
import math
from dataclasses import replace

import numpy as np

from .. import footprint_detect as fd
from .._core.types import BuildingRecord
from ..region_types import SkylinePoint
from . import elevated_behind as _behind
from . import stage_cache as _sc  # not ``sc``: overhead_pose has a local one
from .elevated_behind import (
    BEHIND_DIST_RATIO,
    BEHIND_FILLS,
    BEHIND_INST_DIFF,
    _inst_change,
    _tag_behind,
    _untrust_behind,
    behind_map,
    seed_ownership,
)
from .elevated_floors import FLOORS_RANGE_M, seed_floors
from .elevated_ground import (
    DEPTH_CACHE_VERSION,
    FLOORS_CACHE_VERSION,
    GROUND_HALF_M,
    GROUND_RES_M,
    HEIGHT_FACTORS,
    INSTANCES_CACHE_VERSION,
    MAX_OUTLINE_MISFIT_DEG,
    MAX_OUTLINE_MOVE_M,
    MEASURED_CACHE_VERSION,
    MIN_ELEVATED_H_M,
    OVERHEAD_FROM_H_M,
    OVERHEAD_H_GRID_M,
    OVERHEAD_MIN_GROUND,
    POSE_CACHE_VERSION,
    STITCH_CACHE_VERSION,
    _towers,
    _waterline_camera,
    outline_gate,
    overhead_pose,
    trusted,
)
from .elevated_pano import ElevatedSeed, _view_rows, footprints_from_records, pano_result

logger = logging.getLogger('city2stl.skyline._pano.elevated')


def measure_elevated_seed(seed: SkylinePoint, views: list[dict], pitch_deg: float,
                          step_deg: float, buildings: list[BuildingRecord], state: dict,
                          device: str | None = None,
                          pano: fd.Pano | None = None, keep: bool = False) -> ElevatedSeed | None:
    """Measure one drone seed footprint first (see module docstring). ``state``: shapely
    geometries in lon/lat, ``coast_lines``, ``water_polys``, ``roads`` ((line, width_m)) and
    ``green``. None when the waterline does not fit or the camera is not elevated: the caller
    then runs the street-level path. ``pano``: a pano already built for the seed
    (:func:`capture_sphere_pano`); the spin views then only feed the report's view rows."""
    views = [v for v in views if v.get("image") is not None]
    if not (state.get("coast_lines") or state.get("water_polys")):
        return None
    if pano is None:
        if len(views) < 6:
            return None
        from .._core import pano as core_pano

        strip = [(float(v["geo_heading"]), v["image"]) for v in views]
        pano = _sc.cached("pano", STITCH_CACHE_VERSION,
                          ("strip", seed.name, float(seed.lat), float(seed.lon), float(seed.fov),
                           float(step_deg), float(pitch_deg), strip,
                           _sc.source_hash(fd.pano_from_views, core_pano)),
                          lambda: fd.pano_from_views(seed.name, seed.lat, seed.lon, views,
                                                     seed.fov, step_deg, pitch_deg),
                          kind="pickle")
    if device is None:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    from city2stl.resources import free_gpu_cache, wait_for_gpu

    from ..building_instances_fast import building_instances
    from ..depth_estimation import predict_pano_depth_tiled

    # depth and instances depend on the image only (a camera move changes lat/lon, not pixels),
    # so they come first: the overhead pose needs them
    def gpu_depth():
        if device == "cuda":
            wait_for_gpu(1.0)                   # Depth Anything on 518 px tiles
        out = predict_pano_depth_tiled(pano.rgb, device=device)
        free_gpu_cache()
        return out

    # prompt grid and window in degrees: the defaults suit the 75-deg capture (~7.5 px/deg);
    # at ~20.8 px/deg a 12 px grid made ~8x the prompts and ran past an hour (seed_6)
    k = max(1.0, pano.f_px / 417.0)
    win, grid = int(448 * k), int(round(12 * k))

    def gpu_instances():
        if device == "cuda":
            wait_for_gpu(1.0)                   # MobileSAM vit_t
        out = building_instances(pano.rgb, np.isin(pano.labels, fd.BUILDING_CLASSES),
                                 device=device, window_px=win, grid_px=grid)
        free_gpu_cache()
        return out

    # cached per pano image (stage cache, compact; an old runs/pano_cache .npy is read and
    # converted): re-runs (and review rounds) are CPU-only and can run side by side
    pdig = _sc.pano_digest(pano.rgb)
    depth = _sc.cached("depth", DEPTH_CACHE_VERSION, _sc.depth_parts(pdig), gpu_depth,
                       legacy_path=_sc.legacy_path(pdig, f"depth_v{DEPTH_CACHE_VERSION}"),
                       lossy_float16=_sc.depth_float16())
    instances = _sc.cached("instances", INSTANCES_CACHE_VERSION,
                           _sc.instances_parts(pdig, win, grid), gpu_instances,
                           legacy_path=_sc.legacy_path(
                               pdig, f"inst_v{INSTANCES_CACHE_VERSION}_w{win}_g{grid}"))
    fps, fids = footprints_from_records(buildings)
    from .. import skyline_match
    from . import roof_fit

    # the pose and the measurements depend on the whole pano, the ground layers, the buildings,
    # the depth/instance maps (their versions) and the code: an edit to footprint_detect,
    # roof_fit, skyline_match or the camera functions here invalidates them, no bump needed
    pose_key = _sc.stage_key("pose", POSE_CACHE_VERSION, (
        pano, state, buildings, DEPTH_CACHE_VERSION, INSTANCES_CACHE_VERSION, win, grid,
        _sc.depth_float16(), MIN_ELEVATED_H_M, OVERHEAD_FROM_H_M, HEIGHT_FACTORS, GROUND_HALF_M,
        GROUND_RES_M, OVERHEAD_H_GRID_M, OVERHEAD_MIN_GROUND,
        MAX_OUTLINE_MISFIT_DEG, MAX_OUTLINE_MOVE_M,
        _sc.source_hash(fd, roof_fit, skyline_match, _waterline_camera, outline_gate,
                        overhead_pose, _towers)))

    def fit_pose():
        gmap = fd.ground_map(pano.lat, pano.lon, coast_lines=state.get("coast_lines", ()),
                             water_polys=state.get("water_polys", ()),
                             roads=state.get("roads", ()), green=state.get("green", ()),
                             buildings=[b.geometry for b in buildings],
                             half_m=GROUND_HALF_M, res_m=GROUND_RES_M)
        try:
            pose0 = fd.fit_pose_from_waterline(pano, gmap.shore_distances())
        except ValueError as exc:
            # no column ends in water: pointed down over land (seed_6 at -10/-36/-62 deg); the
            # overhead fit below decides whether it is a drone view
            logger.warning("[elevated] %s: %s", seed.name, exc)
            pose0 = fd.PanoPose(0.0, 0.0, 0.0, math.nan, 0)
        if not MIN_ELEVATED_H_M <= pose0.camera_h_m <= OVERHEAD_FROM_H_M:
            # over land (no waterline, seed_6), or so high that the waterline and the tower
            # outline move the camera hundreds of metres to fit a few towers (seed_7: 304 m
            # up, 380 m north, then 9.4 deg of bearing at a 0.03 deg misfit, Nautica read 263 m)
            return "overhead", pose0.camera_h_m, overhead_pose(pano, gmap, fps, depth, instances)
        moved_pano, pose, pf, of = _waterline_camera(seed, pano, pose0, gmap, _towers(buildings))
        why = outline_gate(of)
        if why:
            return "rejected", pose0.camera_h_m, why
        return "waterline", pose0.camera_h_m, (moved_pano.lat, moved_pano.lon, pose, pf)

    branch, h_waterline, got = _sc.cached("pose", POSE_CACHE_VERSION, (_sc.Digest(pose_key),),
                                          fit_pose, kind="pickle")
    if branch == "rejected":
        logger.warning("[elevated] %s: waterline puts the camera %.0f m up, but %s; not a drone "
                       "view", seed.name, h_waterline, got)
        return None
    if branch == "overhead":
        if got is None:
            logger.warning("[elevated] %s: waterline puts the camera %.0f m up and the ground "
                           "does not fit an overhead camera; not a drone view",
                           seed.name, h_waterline)
            return None
        pose, score, check = got
        pf = fd.PositionFit(0.0, 0.0, pose, math.nan, math.nan, score, score,
                            "overhead: ground heading at the recorded position, height from "
                            "building bases")
        logger.info("[elevated] %s: overhead camera %.0f m up, heading offset %.1f deg (ground "
                    "%.3f); building bases say %.0f m", seed.name, pose.camera_h_m,
                    pose.offset_deg, score, check.h_bases_m)
        ms = _sc.cached("measured", MEASURED_CACHE_VERSION, (_sc.Digest(pose_key), branch),
                        lambda: roof_fit.fit_roof_heights(pano, pose, fps, depth=depth,
                                                          instances=instances),
                        kind="pickle")
    else:
        lat, lon, pose, pf = got
        pano = replace(pano, lat=lat, lon=lon)          # fd.moved: the camera moved, not pixels
        ms = _sc.cached("measured", MEASURED_CACHE_VERSION,
                        (_sc.Digest(pose_key), branch, ROOF_FILL_WEIGHT,
                         _behind.BEHIND_TOL_PX, BEHIND_INST_DIFF, BEHIND_FILLS, BEHIND_DIST_RATIO,
                         _sc.source_hash(measure_waterline, trusted, _tag_behind, _inst_change,
                                         _untrust_behind)),
                        lambda: measure_waterline(pano, pose, fps, depth=depth,
                                                  instances=instances),
                        kind="pickle")
    logger.info("[elevated] %s: %d footprints measured (%d with the base visible)%s",
                seed.name, len(ms), sum(m.base_visible for m in ms),
                "" if instances is None else f", {int(instances.max())} MobileSAM instances")
    from .. import floor_bands

    floors = _sc.cached("floors", FLOORS_CACHE_VERSION,
                        (_sc.Digest(pose_key), branch, FLOORS_RANGE_M,
                         _sc.source_hash(floor_bands, seed_floors)),
                        lambda: seed_floors(pano, pose, depth, instances, buildings),
                        kind="pickle")
    chk = floor_bands.pose_check(floors)
    if chk is not None:
        logger.info("[elevated] %s: floor-spacing pose check (log only): %d counts, storey %.2f m, "
                    "camera %.0f m off (shift E %+.0f N %+.0f, se %.0f m), residual %.0f %%",
                    seed.name, chk["n"], chk["storey_m"], chk["shift_m"], chk["shift_e_m"],
                    chk["shift_n_m"], chk["se_m"], 100 * chk["resid_rel"])
    own = seed_ownership(pano, pose, buildings, ms, depth, instances, seed.name)
    out = ElevatedSeed(seed.name, pf, ms, fids,
                       pano_result(seed, pano, pose, ms, fids, depth, instances,
                                   recredits=() if own is None else own.recredits),
                       _view_rows(seed, views, pano, pose, len(ms)),
                       behind=behind_map(pano, pose, fps, fids, ms), floors=floors,
                       ownership=own)
    if keep:
        out.pano, out.pose, out.depth, out.instances = pano, pose, depth, instances
    return out


#: Fusion weight factor of a roof fit that fills in for a footprint the street run gave no
#: trusted reading (:func:`measure_waterline`); None turns the fill off. 2026-10-07, Cartagena
#: seeds 1/4/5/6/7: the fill took trusted footprints 226 -> 273, verified by 2 seeds 11 -> 17
#: (tall 3 -> 6), the fused OSM-tag error 5.7 -> 2.8 % median and the published towers 6.7 ->
#: 4.2 % median (seed_5 Nautica 159/160, Ravello 161/160 tag) at 0.25, 0.5 or 1.0 alike; 0.25
#: is the one that moved no fused height already read by a trusted run (0.5 and 1.0 let a fill
#: outvote one: b0940 192 -> 121 m, b0217 87 -> 133 m, both untagged).
ROOF_FILL_WEIGHT: float | None = 0.25


#: ... and a fill counts only this close (m), nearer than an overhead roof fit
#: (:data:`ROOF_TRUST_MAX_DIST_M`): from a drone below the tower tops, a low roof past ~750 m
#: shows a sliver over the nearer roofs and its fitted top is the tower behind. 2026-10-07,
#: Miami LiDAR (5 waterline seeds, 53 trusted fills): within 25 % 22/53 -> 13/16; the dropped
#: ones read 100-190 m on 22-108 m buildings (The Loft 1 177/82, The Guild 183/107, Brickell
#: Key I 157/68, all 840-930 m out); right fills lost: 9, mostly towers 800-900 m out that
#: the street run or another seed also reads. Cartagena's tagged fills are all within 670 m.
FILL_MAX_DIST_M: float = 750.0


def measure_waterline(pano: fd.Pano, pose: fd.PanoPose, footprints, depth=None, instances=None,
                      fill_weight: float | None = None) -> list:
    """Every footprint in view from a drone below most roofs: ``measure_footprints``, then a
    roof fit (``roof_fit.fit_roof_heights``) for the footprints it gave no :func:`trusted`
    reading.

    The two paths have their own trust: a run is trusted by its top edge and base
    (:func:`trusted`), a roof fit by its confidence and distance (``ROOF_TRUST``,
    ``ROOF_TRUST_MAX_DIST_M``). A footprint keeps its trusted run; one without gets the roof
    fit when that is trusted and within :data:`FILL_MAX_DIST_M` (the untrusted run is then
    dropped, so a seed gives one reading per footprint), else keeps its untrusted run. A fill
    reading's fusion weight (``footprint_detect.measurement_weight``: confidence over distance
    squared) is scaled by
    ``fill_weight`` (default :data:`ROOF_FILL_WEIGHT`): it stands in where the primary method
    failed, so a trusted run from another seed outweighs it at a similar distance."""
    from . import roof_fit

    w = ROOF_FILL_WEIGHT if fill_weight is None else fill_weight
    ms = fd.measure_footprints(pano, pose, footprints, depth=depth, instances=instances)
    explained = _tag_behind(pano, pose, footprints)
    ms = [_untrust_behind(m, explained, instances, pano) for m in ms]
    if w is None:
        return ms
    have = {m.footprint for m in ms if trusted(m)}
    fills = {m.footprint: replace(m, weight_scale=float(w))
             for m in roof_fit.fit_roof_heights(pano, pose, footprints, depth=depth,
                                                instances=instances)
             if m.footprint not in have and trusted(m) and m.dist_m <= FILL_MAX_DIST_M
             and not (BEHIND_FILLS and explained(m, pano))}
    return sorted([m for m in ms if m.footprint not in fills] + list(fills.values()),
                  key=lambda m: m.dist_m)
