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
   outline (``footprint_detect.refine_on_outline``: the seeds were 4-6 deg off); a camera whose
   tower outline still misfits, or that the outline drags to the edge of its search, is not a
   drone over water (:func:`outline_gate`: boat decks and street panos at sea level);
3. Depth Anything V2 and MobileSAM building instances (``building_instances``) on the pano,
   and every OSM footprint in view measured (``footprint_detect.measure_footprints``: a run
   never stops inside one instance; 2026-10-06 on Cartagena seeds 1/4/5, tagged readings within
   25 % of their tag 50/67/50 % -> 80/67/100 %), then a roof fit for the footprints without a
   trusted reading (:func:`measure_waterline`).

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
from . import stage_cache as _sc  # not ``sc``: overhead_pose has a local one

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
            labels[(float(hd), float(p))] = _cached_labels(img)
    return _cached_level_pano(seed.name, seed.lat, seed.lon, views, fov_deg, labels,
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
    vv = {(float(v["geo_heading"]), float(pitch_deg)): v["image"] for v in views
          if v.get("image") is not None}
    labels = {k: _cached_labels(img) for k, img in vv.items()}
    return _cached_level_pano(name, lat, lon, vv, fov_deg, labels, step_deg)


def _cached_labels(img: np.ndarray):
    """SegFormer labels of one view through the ``labels`` stage cache. SegFormer on the GPU is
    not bit-exact, so the first output of a view is stored and becomes the reference: the pano,
    its tilt and everything keyed on them then stay stable from run to run."""
    from .._core import segmentation as seg

    parts = (img, seg._SEGFORMER_MODEL_ID, seg._segformer_input_size())
    return _sc.cached("labels", LABELS_CACHE_VERSION, parts, lambda: seg._ensure_label_map(img))


def _cached_level_pano(name: str, lat: float, lon: float, views: dict, fov_deg: float,
                       labels: dict, step_deg: float) -> fd.Pano:
    """:func:`level_pano` through the ``pano`` stage cache, keyed by the views and their labels
    (pixels), the capture geometry and the stitching code."""
    keys = sorted(views)
    parts = ("sphere", name, float(lat), float(lon), float(fov_deg), float(step_deg),
             [(k, views[k], labels.get(k)) for k in keys],
             _sc.source_hash(sphere_pano, level_pano, horizon_tilt, _tilt_rotation, _camera_axes))
    return _sc.cached("pano", STITCH_CACHE_VERSION, parts,
                      lambda: level_pano(name, lat, lon, views, fov_deg, labels, step_deg),
                      kind="pickle")


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
    #: ``{Measured.footprint: ((feature_id, height_m, tag_m | None), ...)}``: the farther
    #: footprints over each trusted reading's columns and the height each would have if the
    #: reading's top row were its top (:func:`behind_map`, F-SKY26 step 6)
    behind: dict = field(default_factory=dict)
    #: floor counts of the accepted, plot-matched MobileSAM instances (:func:`seed_floors`):
    #: ``{fid, floors, base_seen, spread, n_strips, instance, name, tag_m}`` (F-SKY26 steps 4/5)
    floors: list = field(default_factory=list)


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
            # why the reading is or is not used (the seed pages' reason column);
            # elevated_estimates adds "tower_behind" after fusion
            "top_edge": str(m.top_edge), "trusted": trusted(m),
            "untrusted_reason": untrusted_reason(m),
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
                         BEHIND_TOL_PX, BEHIND_INST_DIFF, BEHIND_FILLS, BEHIND_DIST_RATIO,
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
    out = ElevatedSeed(seed.name, pf, ms, fids,
                       pano_result(seed, pano, pose, ms, fids, depth, instances),
                       _view_rows(seed, views, pano, pose, len(ms)),
                       behind=behind_map(pano, pose, fps, fids, ms), floors=floors)
    if keep:
        out.pano, out.pose, out.depth, out.instances = pano, pose, depth, instances
    return out


#: Stage-cache versions (``stage_cache``: ``runs/stage_cache/<stage>/v<N>``). Bump one when its
#: stage's code changes in a way its key does not see. Depth and instances are keyed by the
#: pano's pixels only (the labels follow from them, and are cached per view so they stay
#: stable); Depth Anything and MobileSAM took 2-9 min a seed and ran again on every review round,
#: and labels, stitching and the pose fit another ~90-125 s (2026-10-06). The pano, pose and
#: measurement keys also hash their code (``stage_cache.source_hash``). Instances v4: T42
#: building_instances_fast (memory-sized batches, 7x faster; full_cover prompts the last
#: half-window the original skipped), 2026-10-07.
DEPTH_CACHE_VERSION = 1
INSTANCES_CACHE_VERSION = 4
LABELS_CACHE_VERSION = 1
STITCH_CACHE_VERSION = 1
POSE_CACHE_VERSION = 1
MEASURED_CACHE_VERSION = 1
FLOORS_CACHE_VERSION = 1
#: The old raw-.npy cache of depth and instances: read (and converted) on a miss only.
PANO_CACHE_DIR = _sc.LEGACY_PANO_CACHE_DIR


def _waterline_camera(seed: SkylinePoint, pano: fd.Pano, pose0: fd.PanoPose, gmap, towers):
    """Camera of a drone over water: waterline position and height, then the bearing (and
    height) from the OSM towers' outline; the recorded position is kept when the towers fit it
    clearly better. Returns (moved pano, pose, PositionFit, OutlineFit or None without towers)."""
    pf = fd.fit_camera_position(pano, pose0, gmap)
    logger.info("[elevated] %s: camera %s, %+.0f m E %+.0f m N of the recorded position, %.0f m "
                "up, heading offset %.1f deg; waterline misfit %.2f -> %.2f deg, ground %.3f -> %.3f",
                seed.name, pf.source, pf.dx_m, pf.dy_m, pf.pose.camera_h_m, pf.pose.offset_deg,
                pf.waterline_at_seed_deg, pf.waterline_deg, pf.ground_at_waterline, pf.ground)
    recorded = pano
    pano = fd.moved(pano, pf.dx_m, pf.dy_m)
    pose = pf.pose
    if towers is None:
        return pano, pose, pf, None
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
    return fd.moved(pano, of.dx_m, of.dy_m), of.pose, pf, of


#: A drone over water fits the OSM towers' outline (``refine_on_outline``) within ~1.3 deg
#: (Cartagena seeds 1/4/5 spin 0.4-0.9, seed_4 hi-res 1.24; Miami seed_4 0.61). Panos from sea
#: level also pass ``MIN_ELEVATED_H_M`` (a boat deck in Miami fitted 24-30 m up, a Chicago street
#: pano 36-45 m from lake polygons) but their outline never fits: 3.96-4.63 deg after the
#: outline step dragged the camera to the edge of its search, 184-228 m (2026-10-07).
MAX_OUTLINE_MISFIT_DEG = 2.5
#: ... and the drone seeds' outline step moved the camera at most 110 m (seed_4 hi-res); the
#: search reaches 150 m (+50 m fine), so a move past this is the fit running off the edge.
MAX_OUTLINE_MOVE_M = 150.0


def outline_gate(of) -> str | None:
    """Why a waterline-fitted camera is not a drone over water (None: it is).

    Not the distance from the recorded position: a Photo Sphere's recorded position can be
    where the pilot stood (Cartagena seed_4, 340 m off, fitted by the waterline and the
    ground). Not a coastline requirement either: a lake shore comes as water polygons only
    (Chicago), and the boat-deck spheres had the coastline. The tower outline decides: only
    when at least ``refine_on_outline``'s ``min_cols`` tower columns were fitted (no towers in
    view: no verdict)."""
    if of is None or of.n_cols < 50 or not math.isfinite(of.misfit_deg):
        return None
    move = math.hypot(of.dx_m, of.dy_m)
    if of.misfit_deg > MAX_OUTLINE_MISFIT_DEG:
        return (f"the OSM tower outline fits {of.misfit_deg:.2f} deg off over {of.n_cols} "
                f"columns (> {MAX_OUTLINE_MISFIT_DEG})")
    if move > MAX_OUTLINE_MOVE_M:
        return f"the tower outline moves the camera {move:.0f} m (> {MAX_OUTLINE_MOVE_M:.0f})"
    return None


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


def untrusted_reason(m) -> str | None:
    """Why :func:`trusted` refuses a reading (None when it is trusted), for the report:
    ``roof_conf_low`` / ``roof_beyond_<N>m`` (an overhead roof fit), ``tag_behind`` (its top is a
    farther tagged tower's, :func:`_untrust_behind`), ``depth_edge`` (the run stopped at a depth
    step), ``top_<edge>`` (another top edge), ``base_hidden`` (sky-topped, base hidden, under
    :data:`SKY_SEEN_TRUST` seen). ``tower_behind`` is set later, by :func:`elevated_estimates`."""
    if trusted(m):
        return None
    if m.top_edge == "roof":
        if float(getattr(m, "confidence", 0.0)) < ROOF_TRUST:
            return "roof_conf_low"
        return f"roof_beyond_{ROOF_TRUST_MAX_DIST_M:.0f}m"
    if m.top_edge == "behind":
        return "tag_behind"
    if m.top_edge == "depth":
        return "depth_edge"
    if m.top_edge != "sky":
        return f"top_{m.top_edge}"
    return "base_hidden"


def _mark_tower_behind(seeds, behind: dict) -> None:
    """Set ``trusted`` False and ``untrusted_reason`` ``"tower_behind"`` on the report segments
    of the readings :func:`tower_behind` left out."""
    for s in seeds:
        for seg in getattr(s.pano_result, "matched_segments", None) or ():
            fid = (seg.get("matched_projection") or {}).get("feature_id")
            if (s.seed_name, fid) in behind:
                seg["trusted"] = False
                seg["untrusted_reason"] = "tower_behind"


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


#: A reading's top row within this many pixels (of the default spin pano, scaled by
#: ``footprint_detect.px_scale``) of where a farther OSM-tagged footprint over its columns puts
#: its tagged top is that footprint's top (:func:`_tag_behind`); None turns the test off.
BEHIND_TOL_PX: float | None = 4.0
#: ... a run counts only when its top-row MobileSAM instance differs from its base-row one in at
#: least this share of its core columns (the run crossed from the low building onto another).
BEHIND_INST_DIFF = 0.3
#: ... and roof-fit fills are tested too. Off: on Miami (2026-10-07, LiDAR truth) the test
#: dropped 6 wrong fills and 5 right ones (One Tequesta Point 106/95 m, b6312 151/152 m): a fill's
#: top often lines up with a tagged tower behind by chance, and fills have no instance change to
#: confirm the crossing.
BEHIND_FILLS = False
#: Farther footprints must be this much farther than the reading's (nearest vertex).
BEHIND_DIST_RATIO = 1.15


def _tag_behind(pano: fd.Pano, pose: fd.PanoPose, footprints):
    """``explained(m, pano) -> bool``: does a farther OSM-tagged footprint over at least 30 % of
    ``m``'s columns (of the narrower span) put its tagged top on ``m``'s top row (within
    :data:`BEHIND_TOL_PX`)? Then ``m`` read that tower over a lower building in front."""
    from .roof_fit import _candidates

    if BEHIND_TOL_PX is None:
        return lambda m, p: False
    h = pose.camera_h_m
    tags = [(c.dn, c.x0, c.x1, float(fd._row_of(pano, pose, math.degrees(math.atan2(
                float(footprints[c.i].osm_height_m) - h, c.dn)))))
            for c in _candidates(pano, pose, footprints, 3500.0, 1)
            if footprints[c.i].osm_height_m]
    if not tags:
        return lambda m, p: False
    arr = np.array(tags, float)
    tol = BEHIND_TOL_PX * fd.px_scale(pano)

    def explained(m, p) -> bool:
        far = arr[:, 0] >= BEHIND_DIST_RATIO * float(m.dist_m)
        ov = (np.minimum(arr[:, 2], m.x1) - np.maximum(arr[:, 1], m.x0)) / np.maximum(
            1.0, np.minimum(arr[:, 2] - arr[:, 1], m.x1 - m.x0))
        hit = far & (ov >= 0.3) & (np.abs(arr[:, 3] - float(m.top_row)) <= tol)
        return bool(hit.any())
    return explained


def _inst_change(m, instances, core: float = 0.6) -> float:
    """Share of ``m``'s core columns whose MobileSAM instance at the top row differs from the one
    at the bottom row (both labelled)."""
    if instances is None:
        return 0.0
    Hh, W = instances.shape
    xs = np.arange(m.x0, m.x1 + 1)
    cut = int(len(xs) * (1 - core) / 2)
    xs = xs[cut:len(xs) - cut]
    xs = xs[(xs >= 0) & (xs < W)]
    if not len(xs):
        return 0.0
    it = instances[min(Hh - 1, int(round(m.top_row)) + 1), xs]
    ib = instances[max(0, int(round(m.bottom_row)) - 1), xs]
    return float(np.mean((it > 0) & (ib > 0) & (it != ib)))


def _untrust_behind(m, explained, instances, pano):
    """A trusted sky-topped run whose top is a farther tagged tower's (:func:`_tag_behind`) and
    whose top instance is not its base's becomes ``top_edge="behind"`` (not :func:`trusted`).
    2026-10-07, Miami seed_3 sphere: Kaseya Center (LiDAR 43 m) read 138 m, its run climbing from
    the arena (instance 50) onto the towers of Museum Park behind it (instances 25, 20)."""
    if (m.top_edge != "sky" or not trusted(m) or not explained(m, pano)
            or _inst_change(m, instances) < BEHIND_INST_DIFF):
        return m
    return replace(m, top_edge="behind")


#: F-SKY26 step 6, the tower behind without a tag. A trusted reading on footprint F is untrusted
#: (:func:`tower_behind`) when both hold:
#: - F's satellite says low: it has readings of :data:`SAT_LOW_METHODS` with confidence >=
#:   :data:`SAT_LOW_CONF`, all under :data:`SAT_LOW_M`, and the reading is over
#:   :data:`SAT_LOW_FACTOR` x their largest (at least :data:`SAT_LOW_FLOOR_M`);
#: - a footprint G at least ``BEHIND_DIST_RATIO`` x farther over its columns
#:   (:func:`behind_map`) would be :data:`CREDIT_MIN_M` or taller with the reading's top row as
#:   its top, and G's own evidence agrees within :data:`CREDIT_TOL`: its OSM tag, any seed's
#:   trusted drone reading of G, or a satellite reading of G (same methods and confidence) of
#:   ``SAT_LOW_M`` or more.
#: Neither alone is safe. 2026-10-07, saved states of Cartagena seeds 1/4/5/6/7 and Miami seeds
#: 2/3/4 (+ spheres), every trusted reading:
#: - the geometric test alone (a G with evidence explains the top) flagged 6 of 14 Cartagena
#:   readings within 25 % of their tag and 20 of 37 Miami readings within 25 % of LiDAR, against
#:   3 / 63 / 24 wrong ones: adjacent towers explain each other's tops (the shared-top-edge check
#:   refused 2026-10-06);
#: - the satellite test alone is unsafe on 40-90 m towers whose stereo failed (b0289, tag 90 m:
#:   stereo 2 m at conf 1.0, multiview 2 m at 0.86; 9 of 16 tagged 40-80 m footprints have
#:   every confident reading under 40 m);
#: - together (with SAT_MIN_M and the shadow block): 65 readings on 61 footprints, none within
#:   25 % of a tag; 3 of 4 tag-wrong readings (b0634 69/10 m, b0628 105/42 m, b0622 74/6 m) and 51
#:   of 141 readings over 2x every satellite reading; 0 of 16 near a confident satellite reading.
#: Floors (the plan's cue: an accepted floor instance on the top matched to a farther plot)
#: matched 0 wrong readings and 3 right ones: too few instances pass the floor checks that far
#: out (0-38 a seed). Miami has no satellite readings, so nothing changes there.
SAT_LOW_M = 40.0
SAT_LOW_CONF = 0.5
SAT_LOW_FACTOR = 2.0
SAT_LOW_FLOOR_M = 5.0
SAT_LOW_METHODS = ("ls", "lean", "stereo", "multiview")
#: Readings under this are failures (ground matched), not a low roof: b0289 (tag 90 m) stereo
#: 2 m, b0075 (drone 111-118 m from two seeds) stereo 1.2 m.
SAT_MIN_M = 3.0
CREDIT_TOL = 0.25
CREDIT_MIN_M = 30.0


def behind_map(pano: fd.Pano, pose: fd.PanoPose, footprints, feature_ids, ms) -> dict:
    """For each trusted reading in ``ms``: the footprints at least ``BEHIND_DIST_RATIO`` x
    farther (nearest vertex) over >= 30 % of its columns (of the narrower span), each with the
    height its top would have on the reading's top row (camera height + range x tan(elevation)):
    ``{m.footprint: ((feature_id, height_m, osm_height_m | None), ...)}``."""
    from .roof_fit import _candidates

    ms = [m for m in ms if trusted(m)]
    if not ms:
        return {}
    cands = _candidates(pano, pose, footprints, 3500.0, 1)
    if not cands:
        return {}
    arr = np.array([(c.dn, c.i, c.x0, c.x1) for c in cands], float)
    h = float(pose.camera_h_m)
    out = {}
    for m in ms:
        far = arr[:, 0] >= BEHIND_DIST_RATIO * float(m.dist_m)
        ov = (np.minimum(arr[:, 3], m.x1) - np.maximum(arr[:, 2], m.x0)) / np.maximum(
            1.0, np.minimum(arr[:, 3] - arr[:, 2], m.x1 - m.x0))
        t = math.tan(math.radians(float(fd._elev_of(pano, pose, m.top_row))))
        got = []
        for dn, i, _x0, _x1 in arr[far & (ov >= 0.3)]:
            hg = h + float(dn) * t
            if hg >= CREDIT_MIN_M:
                f = footprints[int(i)]
                got.append((feature_ids[int(i)], hg, float(f.osm_height_m) if f.osm_height_m
                            else None))
        if got:
            out[m.footprint] = tuple(got)
    return out


def _sat_fields(r) -> tuple[str, float, float]:
    if isinstance(r, dict):
        return str(r.get("method")), float(r.get("height_m")), float(r.get("conf"))
    return str(r.method), float(r.height_m), float(r.conf)


def _sat_max(rs) -> float | None:
    """Largest confident satellite reading (:data:`SAT_LOW_METHODS`, conf >= SAT_LOW_CONF, at
    least :data:`SAT_MIN_M`); a confident shadow (a lower bound) at or over :data:`SAT_LOW_M`
    counts too, so it keeps the footprint from looking low."""
    v = [h for k, h, c in map(_sat_fields, rs or ())
         if c >= SAT_LOW_CONF and ((k in SAT_LOW_METHODS and h >= SAT_MIN_M)
                                   or (k == "shadow" and h >= SAT_LOW_M))]
    return max(v) if v else None


def tower_behind(seeds: list[ElevatedSeed], satellite_raw: dict | None) -> dict:
    """``{(seed_name, feature_id): (G feature_id, G height_m, evidence_m)}``: the trusted readings
    that read a farther tower (see :data:`SAT_LOW_M`). ``satellite_raw``: ``{feature_id:
    [SatReading or its dict]}`` (``satellite_fusion.load_region``); without it nothing is
    flagged."""
    if not satellite_raw:
        return {}
    drone: dict = {}
    for s in seeds:
        for m in s.measured:
            if trusted(m):
                drone.setdefault(s.feature_ids[m.footprint], []).append(float(m.height_m))
    smax = {f: _sat_max(rs) for f, rs in satellite_raw.items()}
    out = {}
    for s in seeds:
        for m in s.measured:
            fid = s.feature_ids[m.footprint]
            low = smax.get(fid)
            if (not trusted(m) or low is None or low >= SAT_LOW_M
                    or float(m.height_m) <= SAT_LOW_FACTOR * max(low, SAT_LOW_FLOOR_M)):
                continue
            for g, hg, tag in s.behind.get(m.footprint, ()):
                ev = ([tag] if tag else []) + drone.get(g, [])
                sg = smax.get(g)
                if sg is not None and sg >= SAT_LOW_M:
                    ev.append(sg)
                e = next((e for e in ev if e >= CREDIT_MIN_M and abs(hg - e) <= CREDIT_TOL * e),
                         None)
                if e is not None:
                    out[(s.seed_name, fid)] = (g, float(hg), float(e))
                    break
    return out


#: Footprints within this range of the camera are candidates for a floor instance's plot (the
#: harness's ``--floors-range``; floors need >= 5 px a floor, ~770 m at hi-res anyway).
FLOORS_RANGE_M = 3000.0
#: MobileSAM instances smaller than this many pixels (at 1194 px focal length, scaled by f^2) are
#: not counted (the review's size cut).
FLOORS_MIN_PX = 4000
#: Floor counts join the fusion as weighted readings (F-SKY26 step 5) when the city's storey
#: calibration is reliable (``floor_bands.StoreyCalibration.reliable``).
FLOORS_IN_FUSION = True
#: A footprint only floors read (no drone, no satellite reading kept) publishes no height from
#: them: off by the score of 2026-10-08 (Cartagena: 47 floors-only footprints, the 2 with truth
#: both lower bounds reading 37 % low; no floors-only count with its base seen had truth).
FLOORS_PUBLISH_ALONE = False   # True needs an estimate builder for floors-only footprints


def seed_floors(pano: fd.Pano, pose: fd.PanoPose, depth, instances,
                buildings: list[BuildingRecord], max_range_m: float = FLOORS_RANGE_M) -> list:
    """``floor_bands.pano_floors`` on a measured pano: one dict per accepted instance matched to
    a footprint within ``max_range_m`` (``fid``, ``floors``, ``base_seen``, ``spread``,
    ``n_strips``, ``instance``, ``name``, ``tag_m``: the OSM height tag, ``osm_tag`` source only,
    for the storey calibration). Pose-dependent only through the plot match; the counts are not."""
    from .. import floor_bands as fl

    if instances is None:
        return []
    fps, fids = footprints_from_records(buildings)
    recs = {str(b.feature_id): b for b in buildings}
    sel, rings = [], []
    for i, f in enumerate(fps):
        r = fd._local(pano.lat, pano.lon, np.asarray(f.ring, float)[:, :2])
        if float(np.hypot(*r.mean(0))) <= max_range_m:
            sel.append(i)
            rings.append(r)
    if not rings:
        return []
    gray = pano.rgb.mean(-1).astype(np.float32)
    ids, cnt = np.unique(instances[instances > 0], return_counts=True)
    min_px = int(FLOORS_MIN_PX * (pano.f_px / 1194.0) ** 2)
    out = []
    for e, hit in fl.pano_floors(pano, pose, gray, instances, [int(i) for i in ids[cnt > min_px]],
                                 depth=depth, rings_xy=rings):
        if not hit:
            continue
        fid = fids[sel[hit[0]]]
        rec = recs.get(fid)
        tag = (float(rec.height_tag_m) if rec is not None and rec.height_tag_m
               and rec.height_source == "osm_tag" else None)
        out.append({"fid": fid, "floors": float(e.floors_visible), "base_seen": bool(e.base_seen),
                    "spread": float(e.spread), "n_strips": int(e.n_strips),
                    "instance": int(e.instance), "name": fps[sel[hit[0]]].name or fid,
                    "tag_m": tag})
    return out


def floor_entries(seeds: list[ElevatedSeed]) -> list[dict]:
    """Every seed's :attr:`ElevatedSeed.floors` as ``floor_bands`` entries (``seed`` added).
    ``lower_bound`` unless the base is in view: ground under the instance's mask
    (``InstanceFloors.base_seen``) or the same seed's reading of that footprint saw its base.
    With the mask test alone 140 of Cartagena's 153 counts were lower bounds and the storey
    calibration had 2 samples; with either, 85 and 6 (storey 4.13 m, sigma_log 0.13)."""
    out = []
    for s in seeds:
        base = {s.feature_ids[m.footprint] for m in s.measured if m.base_visible}
        for e in s.floors or ():
            out.append(dict(e, seed=s.seed_name,
                            lower_bound=not (e.get("base_seen") or e["fid"] in base)))
    return out


class ElevatedEstimates(list):
    """:func:`elevated_estimates`' list of estimates, plus ``floors``: ``{fid: {high_rise_seen,
    floors, lower_bound, storey_m, storey_sigma_log, storey_reliable}}`` for every footprint a
    seed counted floors on, and ``storey`` (``floor_bands.StoreyCalibration``). The publisher's
    hook is ``floor_bands.high_rise_height(prior_m, est.floors.get(fid))``."""

    floors: dict
    storey: object

    def __init__(self, items=(), floors=None, storey=None):
        super().__init__(items)
        self.floors = floors or {}
        self.storey = storey


#: A floors reading agrees with the same seed's base-visible reading of its plot within this
#: share (:func:`drop_contradicted_floors`).
FLOORS_SAME_SEED_AGREE = 0.25


def drop_contradicted_floors(seeds: list[ElevatedSeed], readings: dict) -> tuple[dict, list]:
    """``(readings kept, dropped)``: :func:`floor_bands.floor_readings` without the counts that
    the same seed's own base-visible reading of the plot (trusted or not) contradicts by more
    than :data:`FLOORS_SAME_SEED_AGREE`. One image cannot overrule itself: the count is an
    instance matched to the plot by range (and usually takes its "base in view" from that very
    reading), so a count far from the reading is another building's facade.

    Cartagena v10 (2026-10-09): such counts anchored the fusion over trusted drone readings that
    satellite readings confirmed, and four plots lost their drone height: b0582 Hotel Cartagena
    Plaza (seed_1 66 m, shadow 66, stereo 61; seed_1's count 35 floors = 148 m), b0426 (seed_4 72,
    stereo 74.5; seed_5's count 98.5 m against its own 72 m), b0635 (seed_4 44, lean 53.5 / 1.0,
    stereo 44; seed_6's count 73 m against its own 49 m), b0075 (seed_4 67; seed_5's count 35 m
    against its own 118 m). 20 of 145 counts drop; the b1158 catch (drone 183 m) and every
    tagged plot are unchanged. ``dropped``: ``(fid, seed, floors height, own heights)``."""
    own: dict = {}
    for s in seeds:
        for m in s.measured:
            if m.base_visible:
                own.setdefault((s.seed_name, s.feature_ids[m.footprint]), []).append(
                    float(m.height_m))
    kept, dropped = {}, []
    tol = FLOORS_SAME_SEED_AGREE
    for key, rs in readings.items():
        out = []
        for d in rs:
            hs = own.get((d.get("seed"), d["footprint"]), ())
            h = float(d["height_m"])
            if hs and not any(abs(h - x) <= tol * max(h, x) for x in hs):
                dropped.append((d["footprint"], d.get("seed"), h, list(hs)))
            else:
                out.append(d)
        if out:
            kept[key] = out
    return kept, dropped


def floors_info(seeds: list[ElevatedSeed], satellite_raw: dict | None = None):
    """``(floors info per fid, storey calibration, fusion readings)`` of the seeds' floor counts
    (F-SKY26 steps 4/5): the city's storey from the counts with their base in view on
    ``osm_tag`` plots (``floor_bands.calibrate_storey``), the high-rise flag
    (``floor_bands.high_rise_plots``, >= 10 floors, a lower bound counts, never on a plot whose
    confident satellite readings are all under 40 m), and ``floor_bands.floor_readings`` less the
    counts the same seed's own reading contradicts (:func:`drop_contradicted_floors`)."""
    from .. import floor_bands as fl

    ents = floor_entries(seeds)
    cal = fl.calibrate_storey([(e["floors"], e["tag_m"]) for e in ents
                               if e.get("tag_m") and not e["lower_bound"]])
    low = set()
    for f, rs in (satellite_raw or {}).items():
        m = _sat_max(rs)
        if m is not None and m < SAT_LOW_M:
            low.add(f)
    hr = fl.high_rise_plots(ents, low=low)
    storey = cal.storey_m if cal.reliable else fl.NOMINAL_FLOOR_M
    info: dict = {}
    for e in ents:
        f = e["fid"]
        cur = info.get(f)
        if cur is None or e["floors"] > cur["floors"]:
            info[f] = {"floors": e["floors"], "lower_bound": e["lower_bound"],
                       "seeds": sorted({x["seed"] for x in ents if x["fid"] == f})}
    for f, d in info.items():
        if f in hr:
            d["floors"], d["lower_bound"] = hr[f]
        d.update(high_rise_seen=f in hr, storey_m=round(storey, 3),
                 storey_sigma_log=None if cal.sigma_log is None else round(cal.sigma_log, 4),
                 storey_reliable=cal.reliable, sat_low=f in low,
                 floors_m=round(fl.floors_height(d["floors"], storey), 1))
    readings = fl.floor_readings(ents, cal) if FLOORS_IN_FUSION else {}
    if readings:
        readings, dropped = drop_contradicted_floors(seeds, readings)
        if dropped:
            logger.info("[elevated] floors: %d readings left out of fusion (the same seed's own "
                        "reading of the plot disagrees by more than %.0f %%)", len(dropped),
                        100 * FLOORS_SAME_SEED_AGREE)
    return info, cal, readings


def _split_satellite(satellite: dict | None) -> tuple[dict | None, dict | None]:
    """(fusion readings, raw readings) from either form: ``satellite_fusion.fusion_readings``
    dicts (``kind`` keys) or the raw ``load_region`` readings (``method``)."""
    if not satellite:
        return None, None
    first = next((r for rs in satellite.values() for r in rs), None)
    if first is None:
        return None, None
    if (first.get("kind") if isinstance(first, dict) else None):
        return satellite, None
    from city2stl.height.satellite.readings import SatReading

    from ..satellite_fusion import fusion_readings

    raw = {f: [SatReading.from_json(r) if isinstance(r, dict) else r for r in rs]
           for f, rs in satellite.items()}
    return fusion_readings(raw), raw


def elevated_estimates(seeds: list[ElevatedSeed],
                       satellite: dict | None = None) -> ElevatedEstimates:
    """One estimate per footprint and seed, from :func:`trusted` readings only, for footprints
    whose seeds agree (``fuse_heights``, 25 %): a footprint the seeds disagree on gets no drone
    height at all, rather than the most reliable view's, since any of them may be the misread.

    ``satellite``: ``{feature_id: [fuse_heights dict]}`` (``satellite_fusion.fusion_readings``,
    site flag ``use_satellite_heights``). They join the fusion of the footprints a drone seed read,
    one pseudo-seed per kind (``sat_lean`` ...): weighted by sigma_log, they can outvote and so
    dispute a drone reading, or anchor which drone readings are kept. Only drone readings are
    emitted: a satellite key in ``used`` has no ``by_key`` entry and is skipped.

    Raw satellite readings (``satellite_fusion.load_region``, ``method`` keys) are converted
    here and also drive :func:`tower_behind`: the readings it flags are left out (F-SKY26 step 6;
    no re-credit to the tower behind).

    Floor counts (:attr:`ElevatedSeed.floors`, F-SKY26 steps 4/5, :func:`floors_info`): with a
    reliable storey calibration they join the fusion as ``kind`` "floors" readings (they can
    dispute a drone reading, and verify one from another seed); they are never emitted, and a
    footprint only floors read publishes nothing (:data:`FLOORS_PUBLISH_ALONE`). The result's
    ``floors`` carries the per-footprint ``high_rise_seen`` / ``floors`` / ``storey_m`` for the
    publisher (``floor_bands.high_rise_height``)."""
    satellite, raw = _split_satellite(satellite)
    behind = tower_behind(seeds, raw)
    _mark_tower_behind(seeds, behind)
    if behind:
        logger.info("[elevated] tower behind: %d trusted readings on %d footprints untrusted "
                    "(satellite low, a farther footprint's evidence explains the top)",
                    len(behind), len({f for _s, f in behind}))
    by_seed = {s.seed_name: [dict(m.__dict__, footprint=s.feature_ids[m.footprint])
                             for m in s.measured if trusted(m)
                             and (s.seed_name, s.feature_ids[m.footprint]) not in behind]
               for s in seeds}
    if satellite:
        drone_fids = {d["footprint"] for ms in by_seed.values() for d in ms}
        for fid in drone_fids:
            for d in satellite.get(fid, ()):
                by_seed.setdefault(d["kind"], []).append(d)
    finfo, cal, floor_rs = floors_info(seeds, raw)
    if finfo:
        logger.info("[elevated] floors: %d footprints counted, %d high-rise; storey %.2f m "
                    "(n %d, sigma_log %s%s); %d floors readings in fusion",
                    len(finfo), sum(d["high_rise_seen"] for d in finfo.values()), cal.storey_m,
                    cal.n, cal.sigma_log, "" if cal.reliable else ", not reliable",
                    sum(len(v) for v in floor_rs.values()))
    by_seed.update(floor_rs)
    fused = fd.fuse_heights(by_seed)
    by_key = {(s.seed_name, s.feature_ids[m.footprint]): (s, m) for s in seeds for m in s.measured}
    # the segments carry each footprint's true bearing (their columns are rolled north-centre)
    bearing = {(s.seed_name, seg["matched_projection"]["feature_id"]): seg["true_bearing_deg"]
               for s in seeds for seg in s.pano_result.matched_segments}
    out = ElevatedEstimates(floors=finfo, storey=cal)
    for fid, f in fused.items():
        if f["disputed"]:
            # seeds that saw it disagree (Cartagena 2026-10-06: seed pairs agreed within 25 % on
            # 5-36 % of shared footprints, the far or older view reading another building):
            # no drone height; the untagged prior or the tag takes over
            continue
        for seed_name in f["used"]:
            if (seed_name, fid) not in by_key:      # a satellite pseudo-seed
                continue
            s, m = by_key[(seed_name, fid)]
            b = int(round(bearing.get((seed_name, fid), 0.0))) % 360
            out.append(RegisteredBuildingEstimate(
                feature_id=fid, name=m.name, view_name=f"{seed_name}_{b:03d}",
                heading_offset_deg=float(s.position.pose.offset_deg), x_px=0.5 * (m.x0 + m.x1),
                y_px=float(m.top_row), forward_m=float(m.dist_m),
                estimated_height_m=float(m.height_m), confidence=estimate_confidence(m)))
    return out


__all__ = ["ElevatedSeed", "ElevatedEstimates", "measure_elevated_seed", "elevated_estimates",
           "seed_floors", "floors_info", "drop_contradicted_floors", "trusted", "pano_result",
           "footprints_from_records", "ground_layers", "MIN_ELEVATED_H_M", "measure_waterline",
           "outline_gate"]

