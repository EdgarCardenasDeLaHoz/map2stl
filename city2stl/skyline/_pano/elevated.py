"""skyline._pano.elevated — drone seeds measured footprint first (F-DET6) in the region report.

The street-level path assumes a camera 1.7 m up (``_register_views``). A drone Photo Sphere
hovers 40-100 m over the bay: its roofs sit below the horizon and every per-view height is
wrong by construction (2026-10-05 review). Seeds listed in the site's ``elevated_seeds`` go
through :func:`measure_elevated_seed` instead of heading recovery, anchor, registration and the
pano path:

1. the stitched pano and its ADE20K labels from the views already captured;
2. heading offset, camera height and pitch from the waterline, then the camera position:
   waterline over +-600 m, parks and streets nearby (``footprint_detect.fit_camera_position``;
   seed_4's recorded position was ~360 m off), then the bearing from the OSM-tagged towers'
   outline (``footprint_detect.refine_on_outline``: the seeds were 4-6 deg off);
3. Depth Anything V2 and MobileSAM building instances (``building_instances``) on the pano,
   and every OSM footprint in view measured (``footprint_detect.measure_footprints``: a run
   never stops inside one instance; 2026-10-06 on Cartagena seeds 1/4/5, tagged readings within
   25 % of their tag 50/67/50 % -> 80/67/100 %).

The report keeps working unchanged: each seed gives a ``StitchedPanoResult`` (pano page and
boxes) and frames-only view rows, and after all seeds :func:`elevated_estimates` fuses the
seeds (``footprint_detect.fuse_heights``: the nearer, better-seen view wins a disagreement) and
hands the kept measurements to ``aggregate_building_heights`` as ordinary estimates.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .. import footprint_detect as fd
from .._core.segmentation import _ADE20K_VEGETATION_CLASSES, _ADE20K_WATER_CLASSES
from .._core.types import BuildingRecord, RegisteredBuildingEstimate
from ..region_types import SeedViewRegistration, SkylinePoint, StitchedPanoResult

logger = logging.getLogger(__name__)

#: Below this waterline-fitted camera height there is no near water under the camera: either
#: a street view, or a drone high over land (its near shore below the frame, seed_6), which
#: :func:`overhead_pose` places from the ground and the building bases instead.
MIN_ELEVATED_H_M = 15.0
#: Above this waterline-fitted height the drone is placed like an overhead one (recorded
#: position, ground heading, height from building bases): the waterline + outline fit
#: overfits a few towers (seed_7, 2026-10-06).
OVERHEAD_FROM_H_M = 130.0
#: Camera height search of the tower-outline refinement, as factors of the waterline height.
HEIGHT_FACTORS = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25)
#: Ground map around the seed: the position search (+-600 m) plus 4 km of shore rays.
GROUND_HALF_M, GROUND_RES_M = 4800.0, 3.0
#: High-resolution capture of a drone seed (:func:`capture_sphere_pano`): 30-deg views (21 px
#: per degree, against 8.5 at the default 75 deg; the Photo Spheres hold ~23) in pitch rows
#: that together cover -33..+23 deg: tower tops from 36-100 m up and the near waterline. A drone
#: high over land needs rows pointed down (seed_6: -10, -36, -62 covers +5..-77 deg).
HIRES_FOV_DEG = 30.0
HIRES_PITCHES_DEG = (8.0, -18.0)
HIRES_SIZE_PX = 640


def _camera_axes(heading_deg: float, pitch_deg: float):
    """World (east, north, up) unit vectors of a camera's right, down and forward axes."""
    h, p = np.radians(heading_deg), np.radians(pitch_deg)
    fwd = np.array([np.sin(h) * np.cos(p), np.cos(h) * np.cos(p), np.sin(p)])
    right = np.array([np.cos(h), -np.sin(h), 0.0])
    down = np.array([np.sin(h) * np.sin(p), np.cos(h) * np.sin(p), -np.cos(p)])
    return right, down, fwd


def _tilt_rotation(tilt_deg: tuple[float, float] | None) -> np.ndarray:
    """Rotation taking a level direction into the frame of a sphere tilted ``amp`` deg toward
    frame heading ``phi`` (``tilt_deg = (amp, phi)``): the direction at heading ``phi`` rises by
    ``amp`` (Rodrigues about the horizontal axis ``a(phi) x up``)."""
    if not tilt_deg or not tilt_deg[0]:
        return np.eye(3)
    amp, phi = math.radians(tilt_deg[0]), math.radians(tilt_deg[1])
    k = np.array([math.cos(phi), -math.sin(phi), 0.0])
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(amp) * K + (1 - math.cos(amp)) * (K @ K)


def horizon_tilt(pano: fd.Pano, min_cols: int = 500, max_resid_deg: float = 0.5):
    """``(mean, amp, phi, n_cols, resid)`` of the open-sea horizon: per column where sky meets
    water that continues 40 rows down, its elevation, fitted as ``mean + amp cos(heading - phi)``.
    A level sphere gives amp ~ 0; seed_6's horizon swung 1.06 deg around the circle (the Photo
    Sphere was tilted, 2026-10-06), which no single pitch can fix. None without enough open sea."""
    water = np.isin(pano.labels, _ADE20K_WATER_CLASSES)
    sky = pano.labels == fd.SKY_CLASS
    top = np.argmax(water, axis=0)
    has = water.any(axis=0) & (top > 4)
    xs = []
    for x in np.flatnonzero(has):
        r = top[x]
        if sky[r - 4:r, x].all() and water[r:r + 40, x].mean() > 0.95:
            xs.append(x)
    if len(xs) < min_cols:
        return None
    xs = np.array(xs)
    e = pano.elevation_deg(top[xs].astype(float))
    a = np.radians(pano.frame_heading[xs])
    A = np.column_stack([np.ones_like(a), np.cos(a), np.sin(a)])
    coef, *_ = np.linalg.lstsq(A, e, rcond=None)
    resid = float(np.median(np.abs(e - A @ coef)))
    if resid > max_resid_deg:
        return None
    amp = math.hypot(coef[1], coef[2])
    phi = math.degrees(math.atan2(coef[2], coef[1])) % 360.0
    return float(coef[0]), float(amp), float(phi), int(len(xs)), resid


def sphere_pano(name: str, lat: float, lon: float, views: dict[tuple[float, float], np.ndarray],
                fov_deg: float, labels: dict | None = None, step_deg: float = 30.0,
                tilt_deg: tuple[float, float] | None = None) -> fd.Pano:
    """One pano from pinhole views at several headings and pitches, by exact reprojection.

    Each output column is one heading (uniform; the views' focal length in px per radian) and
    each row one elevation, following :class:`footprint_detect.Pano`'s model (a pinhole at the
    rows' mid pitch, the same for every column), so the rest of the drone path works unchanged.
    A pixel is sampled from the view that sees its direction closest to that view's axis, among
    the views at its own heading and the two beside it. Stitching pinhole crops side by side
    (``pano_from_views``) assumes a row is one elevation in every column, true only near the
    horizon: pointed 36 deg down, neighbouring views tore apart at their seams (2026-10-06,
    seed_6). ``labels``: ADE20K label maps per view (same keys), reprojected the same way.
    ``tilt_deg``: the sphere's tilt ``(amp, phi)`` (:func:`horizon_tilt`), undone so that rows
    are true elevations everywhere around the circle.
    """
    import cv2

    keys = sorted(views)
    pitches = sorted({p for _h, p in keys})
    h0, w0 = views[keys[0]].shape[:2]
    f = 0.5 * w0 / math.tan(math.radians(fov_deg) / 2)
    half_v = math.degrees(math.atan(0.5 * h0 / f))
    e_top, e_bot = max(pitches) + half_v, min(pitches) - half_v
    pv = 0.5 * (e_top + e_bot)
    H = int(round(2 * f * math.tan(math.radians(e_top - pv))))
    W = int(round(2 * math.pi * f))
    cols = np.arange(W)
    az = cols * 360.0 / W
    el = pv + np.degrees(np.arctan((H / 2.0 - (np.arange(H) + 0.5)) / f))
    rgb = np.zeros((H, W, 3), np.uint8)
    lab = np.full((H, W), -1, np.int16)
    hole = np.zeros((H, W), bool)
    for hd in sorted({hd for hd, _p in keys}):
        lo = (hd - step_deg / 2) % 360.0
        sel = cols[((az - lo) % 360.0) < step_deg]
        if not len(sel):
            continue
        a = np.radians(az[sel])[None, :]
        e = np.radians(el)[:, None]
        ray = np.stack([np.sin(a) * np.cos(e), np.cos(a) * np.cos(e),
                        np.broadcast_to(np.sin(e), (H, len(sel)))], -1).astype(np.float32)
        ray = ray @ _tilt_rotation(tilt_deg).T.astype(np.float32)
        best = np.full((H, len(sel)), -np.inf, np.float32)
        out = np.zeros((H, len(sel), 3), np.uint8)
        out_l = np.full((H, len(sel)), -1, np.int16)
        for (vh, vp) in keys:
            if abs((vh - hd + 180.0) % 360.0 - 180.0) > step_deg + 1e-6:
                continue
            img = views[(vh, vp)]
            r_ax, d_ax, f_ax = (x.astype(np.float32) for x in _camera_axes(vh, vp))
            z = ray @ f_ax
            zz = np.maximum(z, 1e-6)
            u = (f * (ray @ r_ax) / zz + img.shape[1] / 2.0).astype(np.float32)
            v = (f * (ray @ d_ax) / zz + img.shape[0] / 2.0).astype(np.float32)
            ok = (z > 0.05) & (u >= 0) & (u <= img.shape[1] - 1) & (v >= 0) & (v <= img.shape[0] - 1)
            take = ok & (z > best)
            if not take.any():
                continue
            best[take] = z[take]
            out[take] = cv2.remap(img, u, v, cv2.INTER_LINEAR)[take]
            if labels is not None and labels.get((vh, vp)) is not None:
                out_l[take] = cv2.remap(np.asarray(labels[(vh, vp)], np.int16), u, v,
                                        cv2.INTER_NEAREST)[take]
        rgb[:, sel] = out
        lab[:, sel] = out_l
        hole[:, sel] = ~np.isfinite(best)
    if hole.any():
        # slivers no view reaches (between views at the top and bottom rows): filled, so they
        # are not read as a black building or a sky edge
        from scipy.ndimage import distance_transform_edt

        rgb = cv2.inpaint(rgb, hole.astype(np.uint8), 5, cv2.INPAINT_TELEA)
        _d, (iy, ix) = distance_transform_edt(hole, return_indices=True)
        lab = lab[iy, ix]
    return fd.Pano(name, lat, lon, rgb, lab, az.astype(float), f, pv)


def capture_sphere_pano(seed: SkylinePoint, api_key: str, headings, is_photosphere: bool,
                        fov_deg: float = HIRES_FOV_DEG, pitches=HIRES_PITCHES_DEG,
                        size_px: int = HIRES_SIZE_PX) -> fd.Pano | None:
    """A drone seed's pano from 30-deg Street View views in ``pitches`` rows
    (``len(headings) * len(pitches)`` images, cached on disk like every fetch) with their
    SegFormer labels, by :func:`sphere_pano`. None when a fetch fails."""
    from .._core.segmentation import _ensure_label_map
    from ..streetview_io import _streetview_image

    views, labels = {}, {}
    for hd in headings:
        for p in pitches:
            img = _streetview_image(api_key, seed.lat, seed.lon, float(hd), fov=fov_deg,
                                    pitch=float(p), width=size_px, height=size_px,
                                    pano_id=seed.pano_id, pano_only=is_photosphere)
            if img is None:
                logger.warning("[elevated] %s: hi-res fetch failed at %.0f deg, pitch %.0f",
                               seed.name, hd, p)
                return None
            views[(float(hd), float(p))] = img
            labels[(float(hd), float(p))] = _ensure_label_map(img)
    return level_pano(seed.name, seed.lat, seed.lon, views, fov_deg, labels,
                      360.0 / len(headings))


def level_pano(name: str, lat: float, lon: float, views: dict, fov_deg: float, labels: dict,
               step_deg: float) -> fd.Pano:
    """:func:`sphere_pano`, rebuilt level when the sea horizon shows a tilt over 0.15 deg
    (:func:`horizon_tilt`); ``horizon_deg`` set from the open-sea horizon when there is one."""
    pano = sphere_pano(name, lat, lon, views, fov_deg, labels, step_deg)
    t = horizon_tilt(pano)
    if t is not None and t[1] > 0.15:
        logger.info("[elevated] %s: sphere tilted %.2f deg toward %.0f deg (horizon over %d "
                    "cols); rebuilt level", name, t[1], t[2], t[3])
        pano = sphere_pano(name, lat, lon, views, fov_deg, labels, step_deg,
                           tilt_deg=(t[1], t[2]))
        t = horizon_tilt(pano)
    if t is not None:
        pano = replace(pano, horizon_deg=t[0])
    return pano


def level_pano_from_spin(name: str, lat: float, lon: float, views: list[dict], fov_deg: float,
                         pitch_deg: float, step_deg: float) -> fd.Pano:
    """The spin views (``_capture_pano_views`` prefetch: ``geo_heading`` + ``image``, one pitch)
    reprojected onto one level sphere -- the drone path's pano, so a tilted Photo Sphere is
    levelled for the 75-deg capture too (no new images)."""
    from .._core.segmentation import _ensure_label_map

    vv = {(float(v["geo_heading"]), float(pitch_deg)): v["image"] for v in views
          if v.get("image") is not None}
    labels = {k: _ensure_label_map(img) for k, img in vv.items()}
    return level_pano(name, lat, lon, vv, fov_deg, labels, step_deg)


@dataclass
class ElevatedSeed:
    seed_name: str
    position: fd.PositionFit
    measured: list                       # list[fd.Measured]
    feature_ids: list[str]               # Measured.footprint -> feature_id
    pano_result: StitchedPanoResult
    view_rows: list[SeedViewRegistration] = field(default_factory=list)
    # the measured pano (moved to the fitted camera), its pose and the depth/instance maps:
    # kept only when asked (``keep=True``), for review renders; a region run drops them (memory)
    pano: fd.Pano | None = None
    pose: fd.PanoPose | None = None
    depth: np.ndarray | None = None
    instances: np.ndarray | None = None


def _ring(geom) -> np.ndarray | None:
    """Exterior ring (lon/lat) of a footprint, the largest part of a MultiPolygon."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    if geom.geom_type != "Polygon":
        return None
    return np.asarray(geom.exterior.coords, float)[:, :2]


def footprints_from_records(buildings: list[BuildingRecord]):
    """``(footprints, feature_ids)`` for ``footprint_detect`` from the pipeline's records."""
    fps, fids = [], []
    for b in buildings:
        ring = _ring(b.geometry)
        if ring is None or len(ring) < 4:
            continue
        fps.append(fd.Footprint(str(b.name or ""), ring, b.height_tag_m))
        fids.append(str(b.feature_id))
    return fps, fids


def _label_masks(labels: np.ndarray) -> dict:
    return {"building": np.isin(labels, fd.BUILDING_CLASSES),
            "water": np.isin(labels, _ADE20K_WATER_CLASSES),
            "sky": labels == fd.SKY_CLASS,
            "vegetation": np.isin(labels, _ADE20K_VEGETATION_CLASSES)}


def pano_result(seed: SkylinePoint, pano: fd.Pano, pose: fd.PanoPose, measured: list,
                feature_ids: list[str], depth: np.ndarray | None,
                instances: np.ndarray | None = None) -> StitchedPanoResult:
    """The report's pano result for an elevated seed: image, masks, depth and per-column
    headings rolled so north is mid-strip (as ``_build_and_detect_pano`` does), and one matched
    segment per measured footprint with its box, bearing, height and OSM feature."""
    H, W = pano.labels.shape
    headings = (pano.frame_heading + pose.offset_deg) % 360.0
    north = int(np.argmin(np.minimum(headings, 360.0 - headings)))
    roll = W // 2 - north
    masks = {k: np.roll(v, roll, axis=1) for k, v in _label_masks(pano.labels).items()}
    headings = np.roll(headings, roll)
    segs, index = [], {}
    for m in sorted(measured, key=lambda m: (m.x0 + roll) % W):
        fid = feature_ids[m.footprint]
        x0, x1 = (m.x0 + roll) % W, (m.x1 + roll) % W
        mid = (x0 + (x1 - x0) % W // 2) % W
        index.setdefault(fid, len(index) + 1)
        segs.append({
            "x_left": int(x0), "x_right": int(x1), "top_y": int(round(m.top_row)),
            "base_y": int(round(m.bottom_row)), "mid_x": int(mid), "peak_x": int(mid),
            "true_bearing_deg": float(headings[mid]), "seed_index": index[fid],
            "height_m": float(m.height_m), "height_src": "footprint",
            "base_visible": bool(m.base_visible), "visible_frac": float(m.visible_frac),
            "matched_projection": {
                "feature_id": fid, "name": m.name, "x_px": float(mid), "x_left_px": float(x0),
                "x_right_px": float(x1), "forward_m": float(m.dist_m), "lateral_m": 0.0,
                "distance_m": float(m.dist_m), "centroid_forward_m": float(m.dist_m)},
        })
    tops = [s["top_y"] for s in segs]
    bases = [s["base_y"] for s in segs]
    band = (max(0, min(tops) - 15), min(H, max(bases) + 15)) if segs else None
    horizon = float(fd._row_of(pano, pose, 0.0))
    return StitchedPanoResult(
        seed_name=seed.name, seed_lat=pano.lat, seed_lon=pano.lon,
        pano_image=np.roll(pano.rgb, roll, axis=1), band_y=band, matched_segments=segs,
        n_segments=len(segs), n_matched=len(segs), n_buildings_in_view=len(segs),
        anchor_offset_deg=float(pose.offset_deg), headings_per_col=headings,
        pano_building_mask=masks["building"], pano_water_mask=masks["water"],
        pano_sky_mask=masks["sky"], pano_vegetation_mask=masks["vegetation"],
        pano_depth=None if depth is None else np.roll(depth, roll, axis=1),
        pano_instances=None if instances is None else np.roll(instances, roll, axis=1),
        # F-SKY25's distance model for an elevated camera: a base at distance d sits
        # f * h / d rows below the horizon.
        geom_K=float(pose.camera_h_m * pano.f_px), geom_horizon_row=horizon,
        bearing_shift_deg=0.0)


def _view_rows(seed: SkylinePoint, views: list[dict], pano: fd.Pano, pose: fd.PanoPose,
               estimates_count: int) -> list[SeedViewRegistration]:
    """Frames-only rows (no per-view matching) so the seed gets its report page."""
    from .._core.segmentation import _ensure_label_map

    rows = []
    for v in views:
        img = v.get("image")
        if img is None:
            continue
        lm = _ensure_label_map(img)
        mk = _label_masks(np.asarray(lm)) if lm is not None else {}
        rows.append(SeedViewRegistration(
            seed_name=seed.name, seed_lat=pano.lat, seed_lon=pano.lon,
            heading=float((v["geo_heading"] + pose.offset_deg) % 360.0), fov=seed.fov,
            registration_score=float(pose.misfit_deg), best_offset=float(pose.offset_deg),
            estimates_count=estimates_count, image=img, matched_segments=[],
            building_mask=mk.get("building"), sky_mask=mk.get("sky"),
            water_mask=mk.get("water"), vegetation_mask=mk.get("vegetation"), raw_image=img))
    return rows


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
        pano = fd.pano_from_views(seed.name, seed.lat, seed.lon, views, seed.fov, step_deg,
                                  pitch_deg)
    gmap = fd.ground_map(pano.lat, pano.lon, coast_lines=state.get("coast_lines", ()),
                         water_polys=state.get("water_polys", ()), roads=state.get("roads", ()),
                         green=state.get("green", ()), buildings=[b.geometry for b in buildings],
                         half_m=GROUND_HALF_M, res_m=GROUND_RES_M)
    try:
        pose0 = fd.fit_pose_from_waterline(pano, gmap.shore_distances())
    except ValueError as exc:
        # no column ends in water: pointed down over land (seed_6 at -10/-36/-62 deg); the
        # overhead fit below decides whether it is a drone view
        logger.warning("[elevated] %s: %s", seed.name, exc)
        pose0 = fd.PanoPose(0.0, 0.0, 0.0, math.nan, 0)
    if device is None:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    from city2stl.resources import free_gpu_cache, wait_for_gpu

    from ..building_instances import building_instances
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

    # cached per pano image: re-runs (and review rounds) are CPU-only and can run side by side
    depth = _pano_cached(pano, f"depth_v{DEPTH_CACHE_VERSION}", gpu_depth)
    instances = _pano_cached(pano, f"inst_v{INSTANCES_CACHE_VERSION}_w{win}_g{grid}",
                             gpu_instances)
    fps, fids = footprints_from_records(buildings)
    towers = _towers(buildings)
    if not MIN_ELEVATED_H_M <= pose0.camera_h_m <= OVERHEAD_FROM_H_M:
        # over land (no waterline, seed_6), or so high that the waterline and the tower outline
        # move the camera hundreds of metres to fit a few towers (seed_7: 304 m up, 380 m north,
        # then 9.4 deg of bearing at a 0.03 deg misfit, Nautica read 263 m)
        got = overhead_pose(pano, gmap, fps, depth, instances)
        if got is None:
            logger.warning("[elevated] %s: waterline puts the camera %.0f m up and the ground "
                           "does not fit an overhead camera; not a drone view",
                           seed.name, pose0.camera_h_m)
            return None
        pose, score, check = got
        pf = fd.PositionFit(0.0, 0.0, pose, math.nan, math.nan, score, score,
                            "overhead: ground heading at the recorded position, height from "
                            "building bases")
        logger.info("[elevated] %s: overhead camera %.0f m up, heading offset %.1f deg (ground "
                    "%.3f); building bases say %.0f m", seed.name, pose.camera_h_m,
                    pose.offset_deg, score, check.h_bases_m)
        from .roof_fit import fit_roof_heights

        ms = fit_roof_heights(pano, pose, fps, depth=depth, instances=instances)
    else:
        pano, pose, pf = _waterline_camera(seed, pano, pose0, gmap, towers)
        ms = fd.measure_footprints(pano, pose, fps, depth=depth, instances=instances)
    logger.info("[elevated] %s: %d footprints measured (%d with the base visible)%s",
                seed.name, len(ms), sum(m.base_visible for m in ms),
                "" if instances is None else f", {int(instances.max())} MobileSAM instances")
    out = ElevatedSeed(seed.name, pf, ms, fids,
                       pano_result(seed, pano, pose, ms, fids, depth, instances),
                       _view_rows(seed, views, pano, pose, len(ms)))
    if keep:
        out.pano, out.pose, out.depth, out.instances = pano, pose, depth, instances
    return out


#: Bump when the depth or instance code changes, so cached maps are recomputed.
DEPTH_CACHE_VERSION = 1
INSTANCES_CACHE_VERSION = 3
#: Per-pano GPU outputs (depth, MobileSAM instances), keyed by the pano's pixels.
PANO_CACHE_DIR = Path(__file__).resolve().parents[1] / "runs" / "pano_cache"


def _pano_cached(pano: fd.Pano, what: str, compute):
    """``compute()`` cached on disk by a hash of the pano's pixels (the labels follow from them;
    SegFormer on the GPU is not bit-exact, so they are left out of the key): Depth Anything and
    MobileSAM took 2-9 min a seed and ran again on every review round (2026-10-06)."""
    import hashlib

    h = hashlib.sha1(pano.rgb.tobytes())
    path = PANO_CACHE_DIR / f"{h.hexdigest()[:20]}_{what}.npy"
    if path.exists():
        try:
            return np.load(path)
        except (OSError, ValueError):
            pass
    out = compute()
    if out is not None:
        PANO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.save(path, out)
    return out


def _waterline_camera(seed: SkylinePoint, pano: fd.Pano, pose0: fd.PanoPose, gmap, towers):
    """Camera of a drone over water: waterline position and height, then the bearing (and
    height) from the OSM towers' outline; the recorded position is kept when the towers fit it
    clearly better. Returns (moved pano, pose, PositionFit)."""
    pf = fd.fit_camera_position(pano, pose0, gmap)
    logger.info("[elevated] %s: camera %s, %+.0f m E %+.0f m N of the recorded position, %.0f m "
                "up, heading offset %.1f deg; waterline misfit %.2f -> %.2f deg, ground %.3f -> %.3f",
                seed.name, pf.source, pf.dx_m, pf.dy_m, pf.pose.camera_h_m, pf.pose.offset_deg,
                pf.waterline_at_seed_deg, pf.waterline_deg, pf.ground_at_waterline, pf.ground)
    recorded = pano
    pano = fd.moved(pano, pf.dx_m, pf.dy_m)
    pose = pf.pose
    if towers is None:
        return pano, pose, pf
    of = fd.refine_on_outline(pano, pose, towers, height_factors=HEIGHT_FACTORS)
    if (pf.dx_m, pf.dy_m) != (0.0, 0.0):
        # a high drone's waterline can pull the camera hundreds of metres (seed_7: 340 m);
        # keep the recorded position when the towers fit it better -- and on as many tower
        # columns: from 180 m up, 87 columns fitted 0.06 deg by turning to the edge of the
        # search (seed_7, 2026-10-06)
        at_rec = fd.refine_on_outline(recorded, pf.pose, towers, height_factors=HEIGHT_FACTORS)
        if at_rec.misfit_deg < of.misfit_deg - 0.3 and at_rec.n_cols >= max(150, 0.8 * of.n_cols):
            logger.info("[elevated] %s: tower outline fits the recorded position better "
                        "(%.2f vs %.2f deg)", seed.name, at_rec.misfit_deg, of.misfit_deg)
            pano, of = recorded, at_rec
            pf = replace(pf, dx_m=0.0, dy_m=0.0, source=pf.source + ", recorded by outline")
    logger.info("[elevated] %s: tower outline moves the bearing %+.1f deg and the camera "
                "%+.0f m E %+.0f m N, %.0f m up; misfit %.2f -> %.2f deg over %d cols",
                seed.name, of.shift_deg, of.dx_m, of.dy_m, of.pose.camera_h_m,
                of.misfit_before_deg, of.misfit_deg, of.n_cols)
    return fd.moved(pano, of.dx_m, of.dy_m), of.pose, pf


#: Roof-fit confidence from which an overhead reading is trusted (:func:`trusted`).
ROOF_TRUST = 0.5
#: ... and only this close (m).
ROOF_TRUST_MAX_DIST_M = 1000.0
#: Share of a sky-topped building that must show when its base is hidden (:func:`trusted`).
SKY_SEEN_TRUST = 0.5
#: Camera heights the overhead heading search tries before the bases refine it.
OVERHEAD_H_GRID_M = (60.0, 90.0, 130.0, 180.0, 260.0)
#: Below this ground score (mean IoU of water, road and green) an overhead fit is not trusted.
OVERHEAD_MIN_GROUND = 0.2


def overhead_pose(pano: fd.Pano, gmap, footprints, depth, instances, iters: int = 6):
    """Camera of a drone high over land (its near shore below the frame, so no waterline).

    Heading: full circle x a few heights, the ground cast from the recorded position (water,
    roads and parks against OSM; ``_GroundScorer``), then +-3 deg. Height: iterated from the
    building bases (``roof_fit.check_pose_from_bases``, no building height used) until it
    settles. The position stays where the sphere was recorded: letting the ground fit move it too
    put seed_6 260 m off, footprints on open water and heights 46 % off; at the recorded
    position, the heading from the ground and the height from the bases (184 m, bases agree
    within 1 %), its tagged towers read within 10 % and the 7 published heights 11 % median
    (2026-10-06). Returns (pose, ground score, last PoseCheck) or None."""
    from .roof_fit import check_pose_from_bases

    sc = fd._GroundScorer(pano, fd.PanoPose(0.0, 100.0, 0.0, 0.0, pano.width), gmap,
                          (30.0, 1500.0), 3)

    def ground(off, h):
        ex, ny, obs = sc.project(off, h)
        return sc.score(ex, ny, obs, 0.0, 0.0)

    s, off, h = max((ground(o, hh), float(o), hh) for hh in OVERHEAD_H_GRID_M
                    for o in np.arange(0.0, 360.0, 5.0))
    if s < OVERHEAD_MIN_GROUND:
        return None
    def horizon_fix(h_cam):
        """Pitch fix putting the measured sea horizon at its true dip (refraction ~8 %)."""
        if not math.isfinite(pano.horizon_deg):
            return 0.0
        dip = 0.92 * math.degrees(math.sqrt(2.0 * h_cam / 6.371e6))
        return -dip - pano.horizon_deg

    pose = fd.PanoPose(off % 360.0, h, horizon_fix(h), 0.0, pano.width)
    check = None
    for _ in range(iters):
        check = check_pose_from_bases(pano, pose, footprints, depth=depth, instances=instances)
        h_new = check.h_bases_m
        if not (h_new and math.isfinite(h_new)):
            break
        done = abs(h_new - pose.camera_h_m) <= 0.01 * pose.camera_h_m
        s, off = max((ground(o, h_new), float(o)) for o in pose.offset_deg + np.arange(-3.0, 3.01, 0.5))
        pose = replace(pose, offset_deg=off % 360.0, camera_h_m=h_new,
                       pitch_fix_deg=horizon_fix(h_new))
        if done:
            break
    return pose, float(s), check


def _towers(buildings: list[BuildingRecord]):
    """``skyline_match.Towers`` of the OSM-tagged buildings, for the outline fit; None if none."""
    from shapely.geometry import mapping

    from ..skyline_match import tower_table

    feats = [{"geometry": mapping(b.geometry),
              "properties": {"height_m": b.height_tag_m, "height_source": b.height_source,
                             "name": b.name}}
             for b in buildings if b.geometry is not None and b.height_tag_m]
    try:
        return tower_table(feats)
    except ValueError:
        return None


#: Road widths by OSM class when the fetch gave none (m).
ROAD_WIDTH_M = {"trunk": 16.0, "primary": 14.0, "secondary": 12.0, "tertiary": 10.0,
                "residential": 7.0, "unclassified": 7.0}
_LAYER_CACHE = Path(__file__).resolve().parents[1] / "runs" / "osm_layers"


def _bbox_key(bbox) -> list[float]:
    return [round(float(v), 6) for v in (bbox.north, bbox.south, bbox.east, bbox.west)]


def _cached_layers(bbox, names: list[str], region_name: str) -> dict:
    """OSM ``names`` layers for ``bbox``: from ``runs/osm_layers/<region>_<layers>.json.gz`` when
    saved for this bbox, else fetched (``fetch_osm_data``) and saved when anything came back.
    A failed fetch returns ``{}``."""
    import gzip
    import json

    path = _LAYER_CACHE / f"{region_name.lower()}_{'-'.join(sorted(names))}.json.gz"
    if path.exists():
        try:
            doc = json.loads(gzip.decompress(path.read_bytes()))
            if doc.get("bbox_nsew") == _bbox_key(bbox):
                return doc["layers"]
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("[elevated] ignoring unreadable %s: %s", path.name, exc)
    try:
        from city2stl.fetch import fetch_osm_data

        got = fetch_osm_data(bbox.north, bbox.south, bbox.east, bbox.west, list(names))
    except Exception as exc:
        logger.warning("[elevated] OSM %s fetch failed: %s", names, exc)
        return {}
    if any((got.get(k) or {}).get("features") for k in names):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(
            {"bbox_nsew": _bbox_key(bbox), "layers": got}).encode("utf-8")))
    return got


def ground_layers(osm: dict, bbox, region_name: str) -> dict:
    """``elevated_state`` for the orchestrator: OSM coastline lines, water polygons, roads
    (line, width_m) and green areas, shapely in lon/lat. Waterways, roads and green are optional
    in the region's building cache (Cartagena's held an empty waterways layer, 2026-10-05), so
    the missing ones are fetched on their own and cached per region (:func:`_cached_layers`)."""
    from shapely.geometry import shape

    from ..osm_water import extract_coastline_features, extract_water_features

    layers = dict(osm)
    missing = [k for k in ("waterways", "roads", "green")
               if not (layers.get(k) or {}).get("features")]
    if missing:
        layers.update(_cached_layers(bbox, missing, region_name))
    roads = []
    for f in (layers.get("roads") or {}).get("features") or []:
        p = f.get("properties") or {}
        hw = p.get("highway")
        hw = str(hw[0] if isinstance(hw, list) else hw).replace("_link", "")
        try:
            roads.append((shape(f["geometry"]),
                          float(p.get("road_width_m") or ROAD_WIDTH_M.get(hw, 6.0))))
        except Exception:
            continue

    def shapes(feats):
        out = []
        for f in feats:
            try:
                out.append(shape(f["geometry"]))
            except Exception:
                continue
        return out

    state = {"coast_lines": shapes(extract_coastline_features(layers)),
             "water_polys": shapes(extract_water_features(layers)),
             "roads": roads,
             "green": shapes((layers.get("green") or {}).get("features") or [])}
    logger.info("[elevated] ground layers: %d coastline, %d water, %d roads, %d green",
                *(len(state[k]) for k in ("coast_lines", "water_polys", "roads", "green")))
    return state


def estimate_confidence(m) -> float:
    """``aggregate_building_heights`` weight: seen share x (300 m / distance)^2, in 0.05..1."""
    seen = 1.0 if m.base_visible else float(m.visible_frac)
    return float(np.clip(seen * (300.0 / max(float(m.dist_m), 300.0)) ** 2, 0.05, 1.0))


def trusted(m) -> bool:
    """A drone reading whose column climbed to the sky with the building's base in view.

    Calibrated on Cartagena's OSM-tagged buildings (2026-10-05, cameras aligned on the tower
    outline): such readings were within 25 % of the tag in 88 % of cases (n 8); sky-topped ones
    in 73 % (11); runs that stopped at a depth step in 21 % (19), base hidden in 10 % (10).
    A roof fit from above (``roof_fit``, top edge ``roof``) counts when its confidence is 0.5 or
    more (seed_6, 2026-10-06: Estelar 199/202, Portomarine 189/188, Gran Bay 170/170 published or
    tagged; none of its confident readings was left out before).
    A sky-topped reading with its base hidden counts when at least half the building shows
    (``visible_frac`` >= :data:`SKY_SEEN_TRUST`): on Cartagena seeds 1/4/5/6 that took the fused
    heights from 215 to 276 buildings, the OSM-tag error from 58 % (n 7) to 12 % (n 14) median
    and the published towers from 3 to 7 (median 7 %, 6 of 7 within 25 %), 2026-10-06.
    """
    if m.top_edge == "roof":
        # and within ROOF_TRUST_MAX_DIST_M: past 1 km low buildings read the towers behind
        # them with high confidence (seed_6 b0372 104/55 m at 1.0 km, seed_7 b0265 234/50 m at
        # 1.6 km); every confident roof fit within 810 m was right (2026-10-06)
        return (float(getattr(m, "confidence", 0.0)) >= ROOF_TRUST
                and m.dist_m <= ROOF_TRUST_MAX_DIST_M)
    return m.top_edge == "sky" and (bool(m.base_visible) or m.visible_frac >= SKY_SEEN_TRUST)


def elevated_estimates(seeds: list[ElevatedSeed]) -> list[RegisteredBuildingEstimate]:
    """One estimate per footprint and seed, from :func:`trusted` readings only, for footprints
    whose seeds agree (``fuse_heights``, 25 %): a footprint the seeds disagree on gets no drone
    height at all, rather than the most reliable view's, since any of them may be the misread."""
    by_seed = {s.seed_name: [dict(m.__dict__, footprint=s.feature_ids[m.footprint])
                             for m in s.measured if trusted(m)] for s in seeds}
    fused = fd.fuse_heights(by_seed)
    by_key = {(s.seed_name, s.feature_ids[m.footprint]): (s, m) for s in seeds for m in s.measured}
    # the segments carry each footprint's true bearing (their columns are rolled north-centre)
    bearing = {(s.seed_name, seg["matched_projection"]["feature_id"]): seg["true_bearing_deg"]
               for s in seeds for seg in s.pano_result.matched_segments}
    out = []
    for fid, f in fused.items():
        if f["disputed"]:
            # seeds that saw it disagree (Cartagena 2026-10-06: seed pairs agreed within 25 % on
            # 5-36 % of shared footprints, the far or older view reading another building):
            # no drone height; the untagged prior or the tag takes over
            continue
        for seed_name in f["used"]:
            s, m = by_key[(seed_name, fid)]
            b = int(round(bearing.get((seed_name, fid), 0.0))) % 360
            out.append(RegisteredBuildingEstimate(
                feature_id=fid, name=m.name, view_name=f"{seed_name}_{b:03d}",
                heading_offset_deg=float(s.position.pose.offset_deg), x_px=0.5 * (m.x0 + m.x1),
                y_px=float(m.top_row), forward_m=float(m.dist_m),
                estimated_height_m=float(m.height_m), confidence=estimate_confidence(m)))
    return out


__all__ = ["ElevatedSeed", "measure_elevated_seed", "elevated_estimates", "trusted", "pano_result",
           "footprints_from_records", "ground_layers", "MIN_ELEVATED_H_M"]

