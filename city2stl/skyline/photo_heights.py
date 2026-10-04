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

Scope: only towers in ``Towers`` (OSM-tagged heights) can be identified from one photo; untagged
buildings need several photos (step C2: the owner gives the same implied height from each).
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
