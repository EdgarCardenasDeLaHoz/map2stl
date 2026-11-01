"""Part of ``city2stl.skyline._pano.elevated`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace

import numpy as np

from .. import footprint_detect as fd
from .._core.segmentation import _ADE20K_VEGETATION_CLASSES, _ADE20K_WATER_CLASSES
from .._core.types import BuildingRecord
from ..region_types import SeedViewRegistration, SkylinePoint, StitchedPanoResult
from . import stage_cache as _sc  # not ``sc``: overhead_pose has a local one
from .elevated_ground import (
    HIRES_FOV_DEG,
    HIRES_PITCHES_DEG,
    HIRES_SIZE_PX,
    LABELS_CACHE_VERSION,
    STITCH_CACHE_VERSION,
    trusted,
    untrusted_reason,
)

logger = logging.getLogger('city2stl.skyline._pano.elevated')


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
    #: geometric ownership of the trusted readings (``ownership.SeedOwnership``: the readings
    #: whose top is not their footprint's, and their re-credits to a farther owner), None when
    #: off (``ownership.enabled``); applied by :func:`elevated_estimates`
    ownership: object = None


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
                instances: np.ndarray | None = None, recredits=()) -> StitchedPanoResult:
    """The report's pano result for an elevated seed: image, masks, depth and per-column
    headings rolled so north is mid-strip (as ``_build_and_detect_pano`` does), and one matched
    segment per measured footprint with its box, bearing, height and OSM feature.
    ``recredits``: ``[(from footprint, Measured)]`` (``ownership.SeedOwnership.recredits``), one
    more segment each, ``height_src`` "recredit" and ``recredit_from`` the footprint read."""
    rc_ids = {id(m): f for f, m in recredits}
    measured = list(measured) + [m for _f, m in recredits]
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
        rc = rc_ids.get(id(m))
        segs.append({
            "x_left": int(x0), "x_right": int(x1), "top_y": int(round(m.top_row)),
            "base_y": int(round(m.bottom_row)), "mid_x": int(mid), "peak_x": int(mid),
            "true_bearing_deg": float(headings[mid]), "seed_index": index[fid],
            "height_m": float(m.height_m),
            "height_src": "footprint" if rc is None else "recredit",
            **({} if rc is None else {"recredit_from": feature_ids[rc]}),
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
        pano_vegetation_mask=masks["vegetation"],
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
