"""Camera pose from where known buildings appear in a photo (F-WEB2 step 5).

Given image columns of identified buildings, solve the camera's position, heading and
horizontal focal length (hence field of view). No camera height, GPS or EXIF is needed, so a
photo taken anywhere (a ship, a rooftop, a cropped or stitched panorama) can be placed.

    pose = solve_pose([Obs(x_px, lat, lon), ...], image_width, projection="pinhole")
    pose.lat, pose.lon, pose.heading_deg, pose.hfov_deg, pose.rms_px, pose.sigma_m

Projections (``x`` = column, ``cx`` = image centre, ``d`` = bearing minus heading):
- ``pinhole``: ``x = cx + f * tan(d)`` -- an ordinary photo (or a crop of one).
- ``cylindrical``: ``x = cx + f * d`` -- a stitched panorama; columns are equal angles.
Why both: a 3:1 or wider skyline is usually stitched, and the pinhole formula is tens of
degrees wrong at its edges; a crop keeps the pinhole model but EXIF FOV no longer applies,
which is why the focal length is solved, not read.

Method: a coarse grid over candidate camera positions (each with a closed-form linear fit of
heading and focal length on the small-angle model), then robust least squares
(``soft_l1``) from the best cells. Position uncertainty comes from the Jacobian at the
solution. Bearings are taken to footprint centroids; a 40 m wide tower 2 km away subtends
~1 deg, which the robust loss absorbs.

Roll (T19): a handheld photo is rarely level. A roll of ``r`` rotates the image about its
centre, moving a tower top that sits ``up`` px above the centre row sideways by ``up * sin r``.
Give each ``Obs`` its ``up_px`` and pass ``roll_deg`` (known roll) or ``fit_roll=True`` (solve
it, bounded by +-``MAX_ROLL_DEG``); the columns are unrolled before the pose is fitted.
Positive roll = the right side of the frame is higher (the convention of
``skyline_match.Hit.roll_deg``). Observations without ``up_px`` are left as they are, and the
defaults (roll 0, not fitted) reproduce the level-photo solution exactly.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

import numpy as np

from geo2stl.geo import M_PER_DEG_SPHERE as M_PER_DEG_LAT

from .photo_view import PinholeCamera, building_height


@dataclass(frozen=True)
class Obs:
    """One identified building: its column in the image and its footprint centroid."""
    x_px: float
    lat: float
    lon: float
    name: str = ""
    up_px: float | None = None      # outline height above the image centre row (for roll)


@dataclass(frozen=True)
class Pose:
    lat: float
    lon: float
    heading_deg: float          # bearing of the image centre column
    f_px: float
    hfov_deg: float
    projection: str
    rms_px: float
    sigma_m: float              # 1-sigma position uncertainty (largest axis)
    residuals_px: tuple[float, ...]
    names: tuple[str, ...]
    rejected: tuple[str, ...] = ()   # identifications dropped as outliers
    roll_deg: float = 0.0            # camera roll used or fitted (+: right side higher)

    def residual_table(self) -> list[tuple[str, float]]:
        return list(zip(self.names, self.residuals_px, strict=True))


def _wrap(a):
    return (a + np.pi) % (2 * np.pi) - np.pi


def project(d: np.ndarray, f: float, cx: float, projection: str) -> np.ndarray:
    """Column of a direction ``d`` (radians from the centre column)."""
    if projection == "cylindrical":
        return cx + f * d
    return cx + f * np.tan(np.clip(d, -1.45, 1.45))


def hfov_deg(f: float, width: int, projection: str) -> float:
    half = width / 2.0
    return math.degrees(2 * (half / f if projection == "cylindrical" else math.atan(half / f)))


class _Frame:
    """Local metric frame (east, north) around a reference latitude/longitude."""

    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    def to_xy(self, lat, lon):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * M_PER_DEG_LAT

    def to_ll(self, x, y):
        return self.lat0 + y / M_PER_DEG_LAT, self.lon0 + x / self.kx


def _bearings(cx_m, cy_m, bx, by):
    """Compass bearing (radians, clockwise from north) from the camera to each building."""
    return np.arctan2(bx - cx_m, by - cy_m)


def _linear_fit(alpha: np.ndarray, x: np.ndarray, cx: float):
    """Small-angle fit ``x - cx = f * (alpha - psi)``: returns (psi, f, rms) or None."""
    a0 = math.atan2(np.mean(np.sin(alpha)), np.mean(np.cos(alpha)))
    u = _wrap(alpha - a0)
    A = np.column_stack([u, -np.ones_like(u)])
    try:
        (f, fpsi), *_ = np.linalg.lstsq(A, x - cx, rcond=None)
    except np.linalg.LinAlgError:
        return None
    if f <= 0:
        return None
    psi = a0 + fpsi / f
    rms = float(np.sqrt(np.mean((f * (u - fpsi / f) - (x - cx)) ** 2)))
    return psi, float(f), rms


#: An identification is an outlier when its residual exceeds this many px and 4x the median.
OUTLIER_PX = 30.0
#: Bound on a fitted camera roll, degrees.
MAX_ROLL_DEG = 5.0


def unroll_columns(x: np.ndarray, up: np.ndarray, cx: float, roll_rad) -> np.ndarray:
    """Columns as a level camera would see them: ``x - cx`` turned back by the roll.

    ``up`` is NaN where an observation has no row (its column is kept).
    """
    out = cx + (x - cx) * np.cos(roll_rad) + np.nan_to_num(up) * np.sin(roll_rad)
    return np.where(np.isfinite(up), out, x)


def solve_pose(obs: list[Obs], image_width: int, projection: str = "pinhole",
               search_radius_m: float = 8000.0, grid_step_m: float = 200.0,
               min_standoff_m: float = 150.0, f_prior_px: float | None = None,
               roll_deg: float = 0.0, fit_roll: bool = False) -> Pose:
    """Camera pose from >= 4 identified buildings (>= 3 when ``f_prior_px`` is given).

    ``roll_deg``: known camera roll (the start value when ``fit_roll``); ``fit_roll`` solves
    it within +-``MAX_ROLL_DEG`` from the observations' ``up_px`` (ignored when fewer than
    ``need`` observations carry one).

    The grid covers ``search_radius_m`` around the buildings' centroid, excluding points
    closer than ``min_standoff_m`` to any building (the camera is not inside a tower).
    A clear outlier (a wrong label or match) is dropped and the pose re-solved, one at a
    time; the dropped names are in ``Pose.rejected``. Why: the robust loss softens a 600 px
    mislabel but still moved the camera 77 m in the synthetic test.
    """
    need = 3 if f_prior_px else 4
    fit_roll = fit_roll and sum(o.up_px is not None for o in obs) >= need
    rejected: list[str] = []
    pose = _solve_once(obs, image_width, projection, search_radius_m, grid_step_m,
                       min_standoff_m, f_prior_px, roll_deg, fit_roll)
    while len(obs) > need + 1:
        r = np.abs(np.array(pose.residuals_px))
        i = int(np.argmax(r))
        if r[i] <= max(OUTLIER_PX, 4.0 * float(np.median(r))):
            break
        rejected.append(obs[i].name)
        obs = obs[:i] + obs[i + 1:]
        pose = _solve_once(obs, image_width, projection, search_radius_m, grid_step_m,
                           min_standoff_m, f_prior_px, roll_deg, fit_roll)
    if rejected:
        pose = replace(pose, rejected=tuple(rejected))
    return pose


def _solve_once(obs: list[Obs], image_width: int, projection: str, search_radius_m: float,
                grid_step_m: float, min_standoff_m: float, f_prior_px: float | None,
                roll_deg: float = 0.0, fit_roll: bool = False) -> Pose:
    from scipy.optimize import least_squares

    if projection not in ("pinhole", "cylindrical"):
        raise ValueError(f"unknown projection {projection!r}")
    need = 3 if f_prior_px else 4
    if len(obs) < need:
        raise ValueError(f"need at least {need} identified buildings, got {len(obs)}")
    frame = _Frame(float(np.mean([o.lat for o in obs])), float(np.mean([o.lon for o in obs])))
    bx, by = frame.to_xy([o.lat for o in obs], [o.lon for o in obs])
    x = np.array([o.x_px for o in obs], float)
    cx = image_width / 2.0
    up = np.array([np.nan if o.up_px is None else o.up_px for o in obs], float)
    x_raw = x
    x = unroll_columns(x_raw, up, cx, math.radians(roll_deg))     # start value for the grid

    # 1. coarse grid: closed-form heading + focal per cell, keep the order-consistent best
    g = np.arange(-search_radius_m, search_radius_m + 1, grid_step_m)
    cands = []
    for gx in g:
        for gy in g:
            if np.min(np.hypot(bx - gx, by - gy)) < min_standoff_m:
                continue
            alpha = _bearings(gx, gy, bx, by)
            fit = _linear_fit(alpha, x, cx)
            if fit is None:
                continue
            psi, f, rms = fit
            if f_prior_px:
                rms = float(np.sqrt(np.mean((project(_wrap(alpha - psi), f_prior_px, cx,
                                                      projection) - x) ** 2)))
                f = f_prior_px
            cands.append((rms, gx, gy, psi, f))
    if not cands:
        raise ValueError("no camera position in the search area sees the buildings in order")
    cands.sort(key=lambda c: c[0])

    # 2. robust refinement from the best few cells
    def resid(p):
        ex, ny, psi = p[0], p[1], p[2]
        f = f_prior_px if f_prior_px else math.exp(p[3])
        d = _wrap(_bearings(ex, ny, bx, by) - psi)
        xs = unroll_columns(x_raw, up, cx, p[-1]) if fit_roll else x
        return project(d, f, cx, projection) - xs

    best = None
    for _, gx, gy, psi, f in cands[:5]:
        # focal length bounded to 0.05-50 x the width (FOV ~2-170 deg): degenerate
        # identifications otherwise drive it to 0 (seen in skyline matching, 2026-10-04)
        lo_f, hi_f = math.log(0.05 * image_width), math.log(50.0 * image_width)
        p0 = [gx, gy, psi] + ([] if f_prior_px else [min(max(math.log(f), lo_f + 1e-6), hi_f - 1e-6)])
        rmax = math.radians(MAX_ROLL_DEG)
        if fit_roll:
            p0 = p0 + [min(max(math.radians(roll_deg), -rmax + 1e-9), rmax - 1e-9)]
        bounds = ([-np.inf] * 3 + ([] if f_prior_px else [lo_f]) + ([-rmax] if fit_roll else []),
                  [np.inf] * 3 + ([] if f_prior_px else [hi_f]) + ([rmax] if fit_roll else []))
        r = least_squares(resid, p0, loss="soft_l1", f_scale=15.0, bounds=bounds)
        if best is None or r.cost < best.cost:
            best = r
    p = best.x
    f = f_prior_px if f_prior_px else math.exp(p[3])
    res = resid(p)
    roll_out = math.degrees(p[-1]) if fit_roll else float(roll_deg)

    # 3. position uncertainty from the Jacobian (pixel noise from the residuals)
    dof = max(1, len(x) - len(p))
    s2 = float(np.sum(res ** 2) / dof)
    try:
        cov = np.linalg.inv(best.jac.T @ best.jac) * s2
        sigma = float(math.sqrt(max(np.linalg.eigvalsh(cov[:2, :2]).max(), 0.0)))
    except np.linalg.LinAlgError:
        sigma = float("inf")
    lat, lon = frame.to_ll(p[0], p[1])
    return Pose(lat=float(lat), lon=float(lon), heading_deg=math.degrees(p[2]) % 360.0,
                f_px=float(f), hfov_deg=hfov_deg(f, image_width, projection),
                projection=projection, rms_px=float(np.sqrt(np.mean(res ** 2))),
                sigma_m=sigma, residuals_px=tuple(float(r) for r in res),
                names=tuple(o.name for o in obs), roll_deg=roll_out)


# =========================================================================== full camera
# The photo-label tool's two full-camera fits (moved from tools/photo_label/terrain_solve.py and
# register.py in F-UNIFY Phase 3, unchanged): both return a `photo_view.PinholeCamera` (lat, lon,
# orthometric elevation, heading, pitch, roll, focal length), where `solve_pose` above gives only
# position, heading and focal length.
#
#   solve_terrain(dem, labels, w, h, seed)   labelled pixels + DEM (+ OSM roof-height priors),
#                                            seeded from `solve_pose`; least_squares from a grid
#   solve_pnp(pts_photo, lla, cam0)          many 2D-3D matches (photo vs mesh render):
#                                            solvePnPRansac over a focal grid, then least_squares

ROLL_MIN_LABELS = 8
ROLL_GAIN = 0.8         # free roll must cut the median residual to this fraction to be kept
MAX_H = 120.0           # upper bound of a named label's height above the terrain, m
PRIOR_SIGMA_M = 4.0     # roof-height prior: an unnamed label's height ~ footprint height +- this, m
PRIOR_WEIGHT = 3.0      # px-equivalent weight of the prior residual (h - h0) / sigma
def is_named(label: dict) -> bool:
    """A landmark (named footprint or a user-typed name), not an unnamed `P<n>` street point."""
    return bool(label.get("osm_names")) or not re.fullmatch(r"P\d*", str(label.get("label") or "P"))


def label_priors(labels: list[dict], fc: dict | None) -> tuple[np.ndarray, np.ndarray]:
    """(h0, inside): OSM height of the footprint containing each label (0 outside) and whether
    one did."""
    n = len(labels)
    h0, inside = np.zeros(n), np.zeros(n, bool)
    if not fc or not fc.get("features") or not n:
        return h0, inside
    from shapely import STRtree
    from shapely.geometry import Point, shape
    geoms = [shape(f["geometry"]) for f in fc["features"]]
    tree = STRtree(geoms)
    for i, lb in enumerate(labels):
        p = Point(float(lb["lon"]), float(lb["lat"]))
        hit = sorted(int(j) for j in tree.query(p, predicate="intersects"))
        if hit:
            h0[i], inside[i] = building_height(fc["features"][hit[0]]["properties"]), True
    return h0, inside


def _cam(p, k_roll: bool, w: int, h: int) -> PinholeCamera:
    return PinholeCamera(p[0], p[1], p[2], p[3], p[4], p[6] if k_roll else 0.0, p[5], w, h)


def solve_terrain(dem, labels: list[dict], w: int, h: int, seed: dict | None = None,
                  n_start: int = 8, footprints: dict | None = None,
                  label_height: str = "prior") -> dict | None:
    """Fit the camera with roll 0; with 8 or more labels also with roll free, kept only when it
    cuts the median residual clearly (free roll otherwise absorbs label noise: -7 deg on the
    cathedral photo while the median gets worse)."""
    pri = label_priors(labels, footprints) if label_height == "prior" else None
    flat = _solve_terrain_once(dem, labels, w, h, seed, n_start, False, pri)
    if flat is None or len(labels) < ROLL_MIN_LABELS:
        return flat
    rolled = _solve_terrain_once(dem, labels, w, h, seed, n_start, True, pri)
    return rolled if rolled and rolled["median_px"] < ROLL_GAIN * flat["median_px"] else flat


def _solve_terrain_once(dem, labels: list[dict], w: int, h: int, seed: dict | None, n_start: int,
           roll: bool, priors: tuple[np.ndarray, np.ndarray] | None = None) -> dict | None:
    """One fit (see `solve_terrain`). `labels`: dicts with x, y, lat, lon, label[, osm_names]. `seed`: optional
    {lat, lon, heading_deg, f_px} from the column solve. Returns None when no label lies on the
    DEM, else {camera, residuals, named_heights, median_px, max_px, ...}."""
    from scipy.optimize import least_squares

    lat = np.array([float(lb["lat"]) for lb in labels])
    lon = np.array([float(lb["lon"]) for lb in labels])
    xy = np.array([[float(lb["x"]), float(lb["y"])] for lb in labels])
    zg = np.asarray(dem(lat, lon), float)
    if not np.isfinite(zg).all():
        return None
    named = np.array([is_named(lb) for lb in labels])
    h0, inside = priors if priors is not None else (np.zeros(len(labels)), np.zeros(len(labels), bool))
    free = np.ones(len(labels), bool) if priors is not None else named
    ni = np.where(free)[0]
    pri_w = np.where(~named[ni], PRIOR_WEIGHT / PRIOR_SIGMA_M, 0.0) if priors is not None else None
    nbase = 7 if roll else 6
    m = max(w, h)
    kx0 = M_PER_DEG_LAT * math.cos(math.radians(lat.mean()))

    def split(p):
        hs = np.zeros(len(labels))
        hs[ni] = p[nbase:]
        return hs

    def resid_cam(cam, hs):
        x, y = cam.project_ll(lat, lon, zg + hs)
        r = np.concatenate([x - xy[:, 0], y - xy[:, 1]])
        return np.nan_to_num(r, nan=1e4)

    def resid(p):
        r = resid_cam(_cam(p, roll, w, h), split(p))
        if pri_w is None:
            return r
        return np.concatenate([r, pri_w * (p[nbase:] - h0[ni])])

    # ---- coarse grid: score seeds without optimising
    if seed:
        la0, lo0, hd0, f0 = seed["lat"], seed["lon"], seed["heading_deg"], seed["f_px"]
        offs = [-400.0, -150.0, 0.0, 150.0, 400.0]
        heads = [hd0 + d for d in (-25, -10, 0, 10, 25)]
        fs = [f0 * s for s in (0.6, 1.0, 1.7, 2.6)]
    else:
        la0, lo0 = float(lat.mean()), float(lon.mean())
        offs = [-1500.0, -600.0, 0.0, 600.0, 1500.0]
        heads = list(range(0, 360, 30))
        fs = [0.8 * m, 1.6 * m]
    pitches = (-5.0, -15.0, -30.0)
    cand = []
    for dn in offs:
        for de in offs:
            la, lo = la0 + dn / M_PER_DEG_LAT, lo0 + de / kx0
            el = float(np.nan_to_num(dem(la, lo), nan=zg.mean())) + 20.0
            for hd in heads:
                for pt in pitches:
                    for f in fs:
                        p = [la, lo, el, hd, pt, f] + ([0.0] if roll else [])
                        hs = np.zeros(len(labels))
                        hs[ni] = np.where(named[ni], 20.0, h0[ni])
                        cam = _cam(p, roll, w, h)
                        cand.append((float(np.median(np.abs(resid_cam(cam, hs)))), p))
    cand.sort(key=lambda c: c[0])
    starts, picked = [], []
    for _, p in cand:                          # distinct starts: skip near-duplicates of a pick
        if any(abs(p[3] - q[3]) < 8 and abs(p[5] / q[5] - 1) < 0.2 and abs(p[4] - q[4]) < 1
               and abs(p[0] - q[0]) * M_PER_DEG_LAT < 100 and abs(p[1] - q[1]) * kx0 < 100
               for q in picked):
            continue
        picked.append(p)
        starts.append(p)
        if len(starts) >= n_start:
            break

    lo_b = [lat.min() - 0.03, lon.min() - 0.04, float(np.nanmin(zg)) - 5, -720, -80, 100] \
        + ([-10.0] if roll else []) + [0.0] * len(ni)
    hi_b = [lat.max() + 0.03, lon.max() + 0.04, float(np.nanmax(zg)) + 400, 720, 30, 30000] \
        + ([10.0] if roll else []) + [MAX_H] * len(ni)
    xs = [1e-4, 1e-4, 1, 1, 1, 10] + ([1.0] if roll else []) + [1.0] * len(ni)
    best = None
    for p0 in starts:
        p0 = np.clip(list(p0) + list(np.where(named[ni], 20.0, np.maximum(h0[ni], 1.0))), np.add(lo_b, 1e-9), np.subtract(hi_b, 1e-9))
        try:
            r = least_squares(resid, p0, bounds=(lo_b, hi_b), loss="soft_l1", f_scale=4,
                              x_scale=xs, max_nfev=500)
        except Exception:  # noqa: BLE001 - a failed start is just skipped
            continue
        c = float(np.median(np.abs(r.fun[:2 * len(labels)])))
        if best is None or c < best[0]:
            best = (c, r)
    if best is None:
        return None
    p = best[1].x
    cam = _cam(p, roll, w, h)
    hs = split(p)
    x, y = cam.project_ll(lat, lon, zg + hs)
    px = np.hypot(x - xy[:, 0], y - xy[:, 1])
    kx = M_PER_DEG_LAT * math.cos(math.radians(cam.lat))
    dist = np.sqrt(((lon - cam.lon) * kx) ** 2 + ((lat - cam.lat) * M_PER_DEG_LAT) ** 2
                   + (zg + hs - cam.elev_m) ** 2)
    res_m = px * dist / cam.f_px
    ground = float(dem(cam.lat, cam.lon))
    return {
        "camera": {"lat": cam.lat, "lon": cam.lon, "elev_m": cam.elev_m,
                   "ground_m": ground, "heading_deg": cam.heading_deg % 360,
                   "pitch_deg": cam.pitch_deg, "roll_deg": cam.roll_deg, "f_px": cam.f_px,
                   "hfov_deg": math.degrees(2 * math.atan(w / 2 / cam.f_px)), "width": w, "height": h},
        "residuals_px": [float(v) for v in px],
        "residuals_m": [float(v) for v in res_m],
        "heights_m": [float(hs[i]) if free[i] else None for i in range(len(labels))],
        "h0_m": [float(h0[i]) for i in range(len(labels))] if priors is not None else None,
        "in_footprint": [bool(v) for v in inside] if priors is not None else None,
        "median_px": float(np.median(px)), "max_px": float(px.max()),
        "median_m": float(np.median(res_m)),
        "n_starts": len(starts),
    }


# ----- PnP from 2D-3D matches

RANSAC_PX = 8.0               # render vs photo differ in light, season and mesh blur
F_GRID = (0.80, 0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.15, 1.20, 1.25)
MIN_INLIERS = 12


def _enu(cam: PinholeCamera, lat, lon, h):
    kx = M_PER_DEG_LAT * math.cos(math.radians(cam.lat))
    return np.stack([(np.asarray(lon, float) - cam.lon) * kx,
                     (np.asarray(lat, float) - cam.lat) * M_PER_DEG_LAT,
                     np.asarray(h, float) - cam.elev_m], axis=-1)


def _rt(cam: PinholeCamera):
    right, up, fwd = cam.axes()
    r = np.stack([right, -up, fwd])
    return r, -r @ np.array([0.0, 0.0, cam.elev_m])


def _cam_from_rt(r: np.ndarray, c: np.ndarray, base: PinholeCamera, f: float) -> PinholeCamera:
    """Camera at ENU position ``c`` (origin at ``base``'s lat/lon) with world-to-camera rotation ``r``."""
    right, fwd = r[0], r[2]
    heading = math.degrees(math.atan2(fwd[0], fwd[1]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, fwd[2]))))
    hh = math.radians(heading)
    right0 = np.array([math.cos(hh), -math.sin(hh), 0.0])
    up0 = np.cross(right0, fwd)
    roll = math.degrees(math.atan2(float(right @ up0), float(right @ right0)))
    kx = M_PER_DEG_LAT * math.cos(math.radians(base.lat))
    return replace(base, lat=base.lat + c[1] / M_PER_DEG_LAT, lon=base.lon + c[0] / kx,
                   elev_m=float(c[2]), heading_deg=heading % 360.0, pitch_deg=pitch, roll_deg=roll, f_px=float(f))


def _resid(cam: PinholeCamera, lat, lon, h, xy):
    x, y = cam.project_ll(lat, lon, h)
    return np.concatenate([x - xy[:, 0], y - xy[:, 1]])


def _err(cam: PinholeCamera, lat, lon, h, xy) -> np.ndarray:
    r = _resid(cam, lat, lon, h, xy)
    n = len(xy)
    e = np.hypot(r[:n], r[n:])
    return np.where(np.isfinite(e), e, np.inf)


def solve_pnp(pts_photo, lla, cam0: PinholeCamera, *, weights=None, thresh_px: float = RANSAC_PX,
              f_grid=F_GRID, seed: int = 0) -> dict:
    """Camera from 2D-3D matches. ``pts_photo`` (n, 2) photo pixels, ``lla`` (n, 3) lat, lon and
    orthometric h of the same points, ``cam0`` the starting camera (its width/height/f set the
    intrinsics' start). ``weights`` (n,) scale the final refinement's residuals (user pairs > 1).

    PnP RANSAC runs once per focal length in ``f_grid * cam0.f_px``; the best (most inliers,
    then lowest median) seeds a ``least_squares`` over lat, lon, elev, heading, pitch, roll, f on
    its inliers (soft_l1, then the inliers are re-taken and it runs again).
    Returns ``{"camera", "inliers" (bool n), "n_inliers", "median_px", "residuals_px"}``; the
    camera is ``cam0`` and ``n_inliers`` 0 when no pose has MIN_INLIERS."""
    import cv2
    from scipy.optimize import least_squares

    xy = np.asarray(pts_photo, float).reshape(-1, 2)
    lla = np.asarray(lla, float).reshape(-1, 3)
    n = len(xy)
    w = np.ones(n) if weights is None else np.asarray(weights, float)
    fail = {"camera": cam0, "inliers": np.zeros(n, bool), "n_inliers": 0, "median_px": float("nan"),
            "residuals_px": np.full(n, np.nan)}
    good = np.isfinite(xy).all(1) & np.isfinite(lla).all(1)
    if good.sum() < MIN_INLIERS:
        return fail
    pe = _enu(cam0, lla[:, 0], lla[:, 1], lla[:, 2])
    pe[:, 2] = lla[:, 2]                                  # absolute height; the camera sits at cam0.elev_m
    r0, t0 = _rt(cam0)
    rv0, _ = cv2.Rodrigues(r0)
    cv2.setRNGSeed(seed)
    best = None
    idx = np.where(good)[0]
    for k in f_grid:
        f = cam0.f_px * k
        K = np.array([[f, 0, cam0.width / 2.0], [0, f, cam0.height / 2.0], [0, 0, 1.0]])
        try:
            ok, rv, tv, inl = cv2.solvePnPRansac(
                pe[idx].astype(np.float64), xy[idx].astype(np.float64), K, None, iterationsCount=1500,
                reprojectionError=float(thresh_px), confidence=0.999, flags=cv2.SOLVEPNP_EPNP)
        except cv2.error:
            continue
        if not ok or inl is None or len(inl) < MIN_INLIERS:
            continue
        sel = idx[inl.ravel()]
        try:
            rv, tv = cv2.solvePnPRefineLM(pe[sel].astype(np.float64), xy[sel].astype(np.float64), K, None, rv, tv)
        except cv2.error:
            pass
        rm, _ = cv2.Rodrigues(rv)
        cam = _cam_from_rt(rm, (-rm.T @ tv).ravel(), cam0, f)
        e = _err(cam, lla[:, 0], lla[:, 1], lla[:, 2], xy)
        e[~good] = np.inf
        inliers = e < thresh_px
        cnt = int(inliers.sum())
        score = (cnt, -float(np.median(e[inliers])) if cnt else -np.inf)
        if best is None or score > best[0]:
            best = (score, cam, inliers)
    if best is None:
        return fail
    _, cam, inliers = best

    names = ("lat", "lon", "elev_m", "heading_deg", "pitch_deg", "roll_deg", "f_px")
    scale = np.array([1e-4, 1e-4, 1.0, 1.0, 1.0, 1.0, max(cam0.f_px * 0.02, 1.0)])
    roll_ref = cam0.roll_deg

    def refine(cam, inl):
        sel = np.where(inl)[0]
        p0 = np.array([getattr(cam, nm) for nm in names], float)

        def fun(p):
            c = replace(cam, **{nm: float(v) for nm, v in zip(names, p, strict=True)})
            r = _resid(c, lla[sel, 0], lla[sel, 1], lla[sel, 2], xy[sel])
            m = len(sel)
            r = np.concatenate([r[:m] * w[sel], r[m:] * w[sel]])
            r = np.where(np.isfinite(r), r, 1e3)
            return np.concatenate([r, [(p[5] - roll_ref) * 0.5]])      # weak pull of roll toward the start

        sol = least_squares(fun, p0, loss="soft_l1", f_scale=3.0, x_scale=scale, max_nfev=200)
        return replace(cam, **{nm: float(v) for nm, v in zip(names, sol.x, strict=True)})

    for _ in range(2):
        cam = refine(cam, inliers)
        e = _err(cam, lla[:, 0], lla[:, 1], lla[:, 2], xy)
        e[~good] = np.inf
        inliers = e < thresh_px
        if inliers.sum() < MIN_INLIERS:
            return fail
    cam = replace(cam, heading_deg=cam.heading_deg % 360.0)
    return {"camera": cam, "inliers": inliers, "n_inliers": int(inliers.sum()),
            "median_px": float(np.median(e[inliers])), "residuals_px": e}
