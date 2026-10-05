"""Heights of identified towers in a located photo (F-WEB2 phase 2, step C1).

Given a photo's skyline profile and its pose (from labels, GPS + refinement, or
``skyline_match.locate``), measure each tower that forms the skyline:

    ms = measure_towers(prof, towers, pose)           # columns, roof row, distance per tower
    est = loo_heights(ms, towers)                     # height per tower, leave-one-out

Identification: a photo column belongs to the tower the predicted outline puts on top in that
direction (``skyline_match.predicted_outline(owners=True)``), so a roof is credited only to the
tower that should own it. This is the change aimed at the Street View mis-assignment (untagged
buildings read +65 to +113 m too tall, 2026-10-04 baseline).

Measurement: the roof row is the median skyline row over the central ``core`` share of the
tower's columns (its edges are shared with neighbours). Elevation from the row with square
pixels (vertical focal = horizontal); distance to the footprint's nearest corner.

Calibration: ``H = h_cam + d * tan(e + tilt)``. Tilt and camera height are unknown for a
photo, so they are fitted on anchor towers (OSM heights in ``Towers``). ``loo_heights`` fits
them on all *other* towers for each tower, so no tower's own OSM height sets its estimate,
and the benchmark truth (survey + 3D Tiles) stays independent.

Scope: only towers in ``Towers`` (OSM-tagged heights) can be identified from one photo. Untagged
buildings need several photos (step C2):

    table = untagged_table(osm_features)                         # footprints with no height tag
    tilt, h = fit_tilt_height(ms, anchors)                       # per photo, on its tagged towers
    per_photo = implied_heights(prof, pose, table, tilt, h)      # {index: height} per photo
    est = agreed_heights([per_photo_a, per_photo_b, ...])        # kept where photos agree

Each photo implies a height for every untagged footprint in view, from the skyline over the
footprint's bearings. The building that forms the skyline there implies the same height from
every viewpoint; a building in front of or behind it does not (its distance differs, the
outline does not), so agreement across photos identifies it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .skyline_match import BIN_DEG, N_BINS, PhotoProfile, Towers, predicted_outline


@dataclass(frozen=True)
class PhotoPose:
    lat: float
    lon: float
    heading_deg: float
    hfov_deg: float
    projection: str = "pinhole"


@dataclass(frozen=True)
class TowerMeasure:
    index: int
    name: str
    x0: int
    x1: int
    roof_row: float
    elev_deg: float          # roof elevation from the image centre row, before tilt
    dist_m: float
    osm_height_m: float


def _focal(prof: PhotoProfile, pose: PhotoPose) -> float:
    half = math.radians(pose.hfov_deg / 2.0)
    return (prof.width / 2.0) / (half if pose.projection == "cylindrical" else math.tan(half))


def measure_towers(prof: PhotoProfile, towers: Towers, pose: PhotoPose, h_cam: float = 2.0,
                   min_cols: int = 8, core: float = 0.6) -> list[TowerMeasure]:
    """Per tower forming the skyline in the photo: its columns, roof row and distance."""
    cam = towers.to_xy(pose.lat, pose.lon)
    _, own = predicted_outline(towers, cam, h_cam, owners=True)
    f = _focal(prof, pose)
    xs = np.arange(prof.width) + 0.5 - prof.width / 2.0
    az = np.degrees(xs / f if pose.projection == "cylindrical" else np.arctan(xs / f))
    col_owner = own[np.round((pose.heading_deg + az) / BIN_DEG).astype(int) % N_BINS]
    out = []
    for ti in np.unique(col_owner[col_owner >= 0]):
        cols = np.flatnonzero(col_owner == ti)
        if len(cols) < min_cols:
            continue
        # the longest contiguous run (a tower seen through a gap can appear twice)
        runs = np.split(cols, np.flatnonzero(np.diff(cols) > 1) + 1)
        run = max(runs, key=len)
        if len(run) < min_cols:
            continue
        cut = int(len(run) * (1 - core) / 2)
        mid = run[cut:len(run) - cut]
        rows = prof.y_top[mid]
        rows = rows[np.isfinite(rows)]
        if len(rows) < max(3, len(mid) // 3):
            continue
        row = float(np.median(rows))
        elev = math.degrees(math.atan((prof.height / 2.0 - row) / f))
        v = towers.verts[ti]
        d = float(np.min(np.hypot(v[:, 0] - cam[0], v[:, 1] - cam[1])))
        out.append(TowerMeasure(int(ti), towers.names[ti] or f"tower{ti}", int(run[0]),
                                int(run[-1]), row, elev, d, float(towers.height_m[ti])))
    return out


def fit_tilt_height(ms: list[TowerMeasure], anchors_m: np.ndarray,
                    h_bounds: tuple[float, float] = (0.0, 120.0)) -> tuple[float, float]:
    """Least-squares (tilt deg, camera height m) on anchors; robust to one bad anchor.

    With fewer than 3 anchors the camera height is held at 2 m and only the tilt is fitted:
    two anchors cannot separate tilt from height when their distances are similar.
    """
    from scipy.optimize import least_squares

    d = np.array([m.dist_m for m in ms])
    e = np.radians([m.elev_deg for m in ms])

    def resid(p):
        tilt, h = p if len(p) == 2 else (p[0], 2.0)
        return h + d * np.tan(e + math.radians(tilt)) - anchors_m

    if len(ms) >= 3:
        r = least_squares(resid, [0.0, 2.0], loss="soft_l1", f_scale=10.0,
                          bounds=([-30.0, h_bounds[0]], [30.0, h_bounds[1]]))
        return float(r.x[0]), float(r.x[1])
    r = least_squares(resid, [0.0], loss="soft_l1", f_scale=10.0, bounds=([-30.0], [30.0]))
    return float(r.x[0]), 2.0


def height_of(m: TowerMeasure, tilt_deg: float, h_cam: float) -> float:
    return h_cam + m.dist_m * math.tan(math.radians(m.elev_deg + tilt_deg))


def loo_heights(ms: list[TowerMeasure], min_anchors: int = 2) -> dict[int, float]:
    """Height per measured tower with tilt and camera height fitted on the *other* towers."""
    out = {}
    if len(ms) < min_anchors + 1:
        return out
    for i, m in enumerate(ms):
        rest = ms[:i] + ms[i + 1:]
        tilt, h = fit_tilt_height(rest, np.array([r.osm_height_m for r in rest]))
        out[m.index] = height_of(m, tilt, h)
    return out


# --------------------------------------------------------------------------- untagged (C2)

#: Two photos agree on a building's height within max(AGREE_ABS_M, AGREE_REL x height).
AGREE_ABS_M = 3.0
AGREE_REL = 0.10


def untagged_table(features: list[dict], min_area_m2: float = 150.0,
                   tagged_sources: tuple[str, ...] = ("osm_tag", "osm_levels"),
                   frame: Towers | None = None) -> Towers:
    """OSM building footprints *without* a tagged height, as ``Towers`` (``height_m`` NaN).

    ``frame``: put the footprints in that table's local metric frame (the photo poses are
    converted with it). Footprints under ``min_area_m2`` (sheds, kiosks) are left out.
    """
    from shapely.geometry import shape

    rows = []
    for f in features:
        p = f.get("properties") or {}
        if p.get("height_source") in tagged_sources:
            continue
        try:
            g = shape(f["geometry"])
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        g = g if g.geom_type == "Polygon" else max(g.geoms, key=lambda q: q.area)
        rows.append((np.asarray(g.exterior.coords)[:, :2], str(p.get("name") or "")))
    if frame is not None:
        lat0, lon0 = frame.lat0, frame.lon0
    elif rows:
        lat0 = float(np.mean([r[0][:, 1].mean() for r in rows]))
        lon0 = float(np.mean([r[0][:, 0].mean() for r in rows]))
    else:
        lat0 = lon0 = 0.0
    kx = 111_320.0 * math.cos(math.radians(lat0))
    verts, names = [], []
    for ring, name in rows:
        v = np.column_stack([(ring[:, 0] - lon0) * kx, (ring[:, 1] - lat0) * 111_320.0])
        x, y = v[:, 0], v[:, 1]
        if 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) < min_area_m2:
            continue
        verts.append(v)
        names.append(name)
    return Towers(lat0, lon0, verts, np.full(len(verts), np.nan), names)


def implied_heights(prof: PhotoProfile, pose: PhotoPose, table: Towers, tilt_deg: float,
                    h_cam: float, min_cols: int = 4, core: float = 0.6,
                    max_dist_m: float = 5000.0, min_dist_m: float = 60.0) -> dict[int, float]:
    """Height each footprint of ``table`` would need to form this photo's skyline over its
    bearings: ``h_cam + d * tan(e + tilt)``, with ``e`` the skyline elevation (median over the
    central ``core`` share of the footprint's columns) and ``d`` its nearest corner's distance.

    ``tilt_deg`` and ``h_cam`` come from the photo's tagged towers (``fit_tilt_height``). Says
    nothing about *which* building forms the skyline: ``agreed_heights`` decides that across
    photos. Footprints with fewer than ``min_cols`` columns of skyline are skipped.
    """
    cam = table.to_xy(pose.lat, pose.lon)
    f = _focal(prof, pose)
    half_fov = pose.hfov_deg / 2.0
    xs = np.arange(prof.width) + 0.5 - prof.width / 2.0
    out: dict[int, float] = {}
    for i, v in enumerate(table.verts):
        dx, dy = v[:, 0] - cam[0], v[:, 1] - cam[1]
        d = float(np.min(np.hypot(dx, dy)))
        if d < min_dist_m or d > max_dist_m:
            continue
        rel = (np.degrees(np.arctan2(dx, dy)) - pose.heading_deg + 180.0) % 360.0 - 180.0
        lo, hi = float(rel.min()), float(rel.max())
        if hi - lo > 180.0 or hi < -half_fov or lo > half_fov:   # behind, or out of frame
            continue
        lo, hi = max(lo, -half_fov), min(hi, half_fov)
        if pose.projection == "cylindrical":
            c0, c1 = f * math.radians(lo), f * math.radians(hi)
        else:
            c0, c1 = f * math.tan(math.radians(lo)), f * math.tan(math.radians(hi))
        cols = np.flatnonzero((xs >= c0) & (xs <= c1))
        if len(cols) < min_cols:
            continue
        cut = int(len(cols) * (1 - core) / 2)
        mid = cols[cut:len(cols) - cut]
        rows = prof.y_top[mid]
        rows = rows[np.isfinite(rows)]
        if len(rows) < max(2, len(mid) // 2):
            continue
        elev = math.degrees(math.atan((prof.height / 2.0 - float(np.median(rows))) / f))
        out[i] = h_cam + d * math.tan(math.radians(elev + tilt_deg))
    return out


def agreed_heights(per_photo: list[dict[int, float]], min_photos: int = 2,
                   abs_m: float = AGREE_ABS_M, rel: float = AGREE_REL
                   ) -> dict[int, tuple[float, int, float]]:
    """``{index: (height, n_photos, spread)}`` for footprints whose implied heights agree.

    A footprint is kept when at least ``min_photos`` photos imply a height for it and they
    all lie within ``max(abs_m, rel x median)`` of their median; the height is that median.
    Implied heights at or below 0 m (the skyline is lower than the footprint can explain) are
    ignored.
    """
    by: dict[int, list[float]] = {}
    for ph in per_photo:
        for i, h in ph.items():
            if h > 0:
                by.setdefault(i, []).append(h)
    out = {}
    for i, hs in by.items():
        if len(hs) < min_photos:
            continue
        a = np.asarray(hs)
        med = float(np.median(a))
        if np.all(np.abs(a - med) <= max(abs_m, rel * med)):
            out[i] = (med, len(hs), float(np.ptp(a)))
    return out

