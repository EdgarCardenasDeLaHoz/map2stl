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
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

M_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class Obs:
    """One identified building: its column in the image and its footprint centroid."""
    x_px: float
    lat: float
    lon: float
    name: str = ""


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


def solve_pose(obs: list[Obs], image_width: int, projection: str = "pinhole",
               search_radius_m: float = 8000.0, grid_step_m: float = 200.0,
               min_standoff_m: float = 150.0, f_prior_px: float | None = None) -> Pose:
    """Camera pose from >= 4 identified buildings (>= 3 when ``f_prior_px`` is given).

    The grid covers ``search_radius_m`` around the buildings' centroid, excluding points
    closer than ``min_standoff_m`` to any building (the camera is not inside a tower).
    A clear outlier (a wrong label or match) is dropped and the pose re-solved, one at a
    time; the dropped names are in ``Pose.rejected``. Why: the robust loss softens a 600 px
    mislabel but still moved the camera 77 m in the synthetic test.
    """
    need = 3 if f_prior_px else 4
    rejected: list[str] = []
    pose = _solve_once(obs, image_width, projection, search_radius_m, grid_step_m,
                       min_standoff_m, f_prior_px)
    while len(obs) > need + 1:
        r = np.abs(np.array(pose.residuals_px))
        i = int(np.argmax(r))
        if r[i] <= max(OUTLIER_PX, 4.0 * float(np.median(r))):
            break
        rejected.append(obs[i].name)
        obs = obs[:i] + obs[i + 1:]
        pose = _solve_once(obs, image_width, projection, search_radius_m, grid_step_m,
                           min_standoff_m, f_prior_px)
    if rejected:
        from dataclasses import replace
        pose = replace(pose, rejected=tuple(rejected))
    return pose


def _solve_once(obs: list[Obs], image_width: int, projection: str, search_radius_m: float,
                grid_step_m: float, min_standoff_m: float, f_prior_px: float | None) -> Pose:
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
        return project(d, f, cx, projection) - x

    best = None
    for _, gx, gy, psi, f in cands[:5]:
        # focal length bounded to 0.05-50 x the width (FOV ~2-170 deg): degenerate
        # identifications otherwise drive it to 0 (seen in skyline matching, 2026-10-04)
        lo_f, hi_f = math.log(0.05 * image_width), math.log(50.0 * image_width)
        p0 = [gx, gy, psi] + ([] if f_prior_px else [min(max(math.log(f), lo_f + 1e-6), hi_f - 1e-6)])
        bounds = ([-np.inf] * 3 + ([] if f_prior_px else [lo_f]),
                  [np.inf] * 3 + ([] if f_prior_px else [hi_f]))
        r = least_squares(resid, p0, loss="soft_l1", f_scale=15.0, bounds=bounds)
        if best is None or r.cost < best.cost:
            best = r
    p = best.x
    f = f_prior_px if f_prior_px else math.exp(p[3])
    res = resid(p)

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
                names=tuple(o.name for o in obs))
