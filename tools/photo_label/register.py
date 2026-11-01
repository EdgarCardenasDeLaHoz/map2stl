"""Automatic photo registration against a rendered view of the Google photorealistic 3D mesh.

The browser renders the mesh from a candidate camera (colour plus a coarse grid of per-pixel
view depth); this module does the rest, with no network and no GPU:

    match(photo, render)            SIFT (CLAHE greyscale), ratio test + mutual check
    lift(pts_render, depth, cam)    render pixels -> lat, lon, ellipsoidal h from the depth grid
    estimate_geoid(...)             mesh height minus DEM, low percentile -> geoid undulation
    solve_pose(pts, lla, cam0)      solvePnPRansac over a grid of focal lengths, then least_squares
                                    (= camera_solver.solve_pnp; cam_from_dict/cam_to_dict are in photo_view)
    register_step(...)              the three above in sequence: one render -> new camera

Datums: the DEM and every camera `elev_m` are orthometric (DEM datum); the mesh and the
render camera are WGS84 ellipsoidal. `geoid_m` (about +50 m at Granada) converts:
ellipsoidal = orthometric + geoid_m. The render is made at `elev_m + geoid_m`.

Camera convention: `city2stl.skyline.photo_view.PinholeCamera` (heading clockwise from north,
pitch up positive, roll positive when the right side is higher, principal point at the image
centre, pixel coordinates as cv2 gives them). A render of a different size is the same camera
with `f_px` scaled by width ratio; photo points always come back in the photo's own pixels.

Nothing here writes renders or depth to disk (Google Map Tiles terms).
"""
from __future__ import annotations

import base64
import math
from dataclasses import replace

import numpy as np

from city2stl.photo_label.candidates import (
    GEOID_DEFAULT_M,  # 50 m (Granada): the first render guess
)
from city2stl.skyline.camera_solver import (  # noqa: F401 - re-exported: the PnP solve moved there
    F_GRID,
    MIN_INLIERS,
    RANSAC_PX,
    _cam_from_rt,
    _enu,
    _err,
    _resid,
    _rt,
)
from city2stl.skyline.camera_solver import solve_pnp as solve_pose
from city2stl.skyline.photo_view import (  # noqa: F401
    M_PER_DEG_LAT,
    PinholeCamera,
    cam_from_dict,
    cam_to_dict,
)

GEOID_PERCENTILE = 20.0       # ground sits at the low end of (mesh - DEM): roofs and trees add to it
MAX_PAIRS = 200
DEPTH_STEP = 0.08             # relative depth jump across a grid cell that marks a silhouette


# --------------------------------------------------------------------------- cameras

def render_camera(cam: PinholeCamera, rw: int, rh: int, geoid_m: float) -> PinholeCamera:
    """The camera that made a render of size ``rw`` x ``rh``: same pose, f scaled with the
    width, height ellipsoidal."""
    return replace(cam, elev_m=cam.elev_m + geoid_m, f_px=cam.f_px * rw / cam.width, width=rw, height=rh)


# --------------------------------------------------------------------------- decoding

def decode_image(b64: str) -> np.ndarray:
    """RGB uint8 from a base64 PNG/JPEG (a data-URL prefix is accepted)."""
    import cv2
    raw = base64.b64decode(b64.split(",", 1)[-1])
    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("render is not a decodable image")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def decode_depth(b64: str, w: int, h: int) -> np.ndarray:
    """(h, w) float32 view depth in metres from base64 little-endian float32; NaN = no surface."""
    a = np.frombuffer(base64.b64decode(b64), dtype="<f4")
    if a.size != w * h:
        raise ValueError(f"depth has {a.size} values, expected {w}x{h}")
    return a.reshape(h, w).astype(np.float32)


# --------------------------------------------------------------------------- matching

def match(photo_rgb, render_rgb, *, ratio: float = 0.8, max_features: int = 8000):
    """SIFT matches photo vs render. Both go greyscale + CLAHE at the render's size (the photo is
    resized to it); returns ``(pts_photo, pts_render)``, (n, 2) float, the photo points in the
    photo's own pixels. Lowe ratio test forward, kept only when the reverse nearest neighbour
    agrees (mutual)."""
    import cv2
    rh, rw = render_rgb.shape[:2]
    ph, pw = photo_rgb.shape[:2]
    sx, sy = rw / pw, rh / ph
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

    def prep(img, size=None):
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
        if size is not None and (g.shape[1], g.shape[0]) != size:
            g = cv2.resize(g, size, interpolation=cv2.INTER_AREA if size[0] < g.shape[1] else cv2.INTER_LINEAR)
        return clahe.apply(g)

    sift = cv2.SIFT_create(nfeatures=max_features, contrastThreshold=0.02)
    ka, da = sift.detectAndCompute(prep(photo_rgb, (rw, rh)), None)
    kb, db = sift.detectAndCompute(prep(render_rgb), None)
    empty = np.zeros((0, 2)), np.zeros((0, 2))
    if da is None or db is None or len(ka) < 2 or len(kb) < 2:
        return empty
    bf = cv2.BFMatcher(cv2.NORM_L2)
    fwd = bf.knnMatch(da, db, k=2)
    back = bf.match(db, da)
    a, b = [], []
    for pair in fwd:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < ratio * n.distance and back[m.trainIdx].trainIdx == m.queryIdx:
            a.append(ka[m.queryIdx].pt)
            b.append(kb[m.trainIdx].pt)
    if not a:
        return empty
    a = np.array(a, float) / np.array([sx, sy])
    return a, np.array(b, float)


# --------------------------------------------------------------------------- lifting

def sample_depth(depth: np.ndarray, pts, rw: int, rh: int, max_step: float = DEPTH_STEP):
    """Depth under render pixels ``pts`` (n, 2) from a coarse grid whose cell ``(u, v)`` is the
    depth at render pixel ``((u + .5) * rw / dw - .5, (v + .5) * rh / dh - .5)``. Bilinear, and
    NaN where a neighbouring cell has no surface or the four cells differ by more than
    ``max_step`` (relative): a silhouette, where the depth is one side or the other."""
    dh, dw = depth.shape
    pts = np.asarray(pts, float).reshape(-1, 2)
    gu = (pts[:, 0] + 0.5) * dw / rw - 0.5
    gv = (pts[:, 1] + 0.5) * dh / rh - 0.5
    u0 = np.clip(np.floor(gu).astype(int), 0, max(dw - 2, 0))
    v0 = np.clip(np.floor(gv).astype(int), 0, max(dh - 2, 0))
    u1, v1 = np.minimum(u0 + 1, dw - 1), np.minimum(v0 + 1, dh - 1)
    au, av = np.clip(gu - u0, 0, 1), np.clip(gv - v0, 0, 1)
    c = np.stack([depth[v0, u0], depth[v0, u1], depth[v1, u0], depth[v1, u1]], axis=1).astype(float)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(c).all(axis=1) & (c.min(axis=1) > 0)
        ok &= (c.max(axis=1) - c.min(axis=1)) <= max_step * np.where(ok, c.min(axis=1), 1.0)
    z = ((1 - av) * ((1 - au) * c[:, 0] + au * c[:, 1]) + av * ((1 - au) * c[:, 2] + au * c[:, 3]))
    return np.where(ok, z, np.nan)


def lift(pts_render, depth, cam: PinholeCamera):
    """Render pixels -> ``(lat, lon, h, ok)``. ``cam`` is the camera that made the render (render
    size, ellipsoidal elev). ``depth`` is view depth along the optical axis, in the same units
    as the camera's ENU frame: ``PinholeCamera.project_ll`` of the result returns the pixels."""
    pts = np.asarray(pts_render, float).reshape(-1, 2)
    z = sample_depth(depth, pts, cam.width, cam.height)
    ok = np.isfinite(z)
    right, up, fwd = cam.axes()
    zz = np.where(ok, z, 0.0)
    d = (zz[:, None] * (fwd[None] + ((pts[:, 0] - cam.width / 2.0) / cam.f_px)[:, None] * right[None]
                        - ((pts[:, 1] - cam.height / 2.0) / cam.f_px)[:, None] * up[None]))
    kx = M_PER_DEG_LAT * math.cos(math.radians(cam.lat))
    return cam.lat + d[:, 1] / M_PER_DEG_LAT, cam.lon + d[:, 0] / kx, cam.elev_m + d[:, 2], ok


def estimate_geoid(h_ell, dem_h, default: float = GEOID_DEFAULT_M, min_n: int = 30) -> tuple[float, int]:
    """``(geoid_m, n)``: low percentile of mesh height minus DEM height. Roofs and trees only
    add to the difference, so the median would be too large in a city."""
    d = np.asarray(h_ell, float) - np.asarray(dem_h, float)
    d = d[np.isfinite(d)]
    if d.size < min_n:
        return float(default), int(d.size)
    return float(np.percentile(d, GEOID_PERCENTILE)), int(d.size)


def geoid_from_render(depth, rcam: PinholeCamera, dem, default: float = GEOID_DEFAULT_M) -> tuple[float, int]:
    """``(geoid_m, n)`` from every cell of the render's depth grid, not just the SIFT matches.
    The mesh points are right whatever geoid the render used (the camera and the lift share it),
    so this works even when the start geoid is far off and few matches survive (Cartagena from
    Granada's 50 m default: 21 lifted matches, under ``estimate_geoid``'s 30)."""
    dh, dw = depth.shape
    gu, gv = np.meshgrid(np.arange(dw), np.arange(dh))
    pts = np.stack([(gu.ravel() + 0.5) * rcam.width / dw - 0.5, (gv.ravel() + 0.5) * rcam.height / dh - 0.5], axis=1)
    lat, lon, h, ok = lift(pts, depth, rcam)
    if not ok.any():
        return float(default), 0
    return estimate_geoid(h[ok], dem(lat[ok], lon[ok]), default=default)


# --------------------------------------------------------------------------- pose

# --------------------------------------------------------------------------- one iteration

def register_step(photo_rgb, render_rgb, depth, cam: PinholeCamera, *, dem=None, geoid_m: float = GEOID_DEFAULT_M,
                  fit_geoid: bool = True, user=None, user_weight: float = 3.0, max_pairs: int = MAX_PAIRS) -> dict:
    """One render -> new camera. ``cam`` is the photo's camera (photo size, orthometric elev) that
    the render was made from; ``geoid_m`` the undulation used for the render. ``dem`` is a
    callable ``dem(lat, lon)`` (photo_view.Dem) or None. ``user``: ``[(x, y, lat, lon, h_orth)]``
    user pairs, weighted ``user_weight`` in the refinement. ``fit_geoid`` False keeps ``geoid_m``
    (a region with a measured undulation): street-level views see too little bare ground for the
    estimate, which swung from -11 to +36 m on one Cartagena pano.

    Returns ``{"ok", "camera" (dict), "n_matches", "n_lifted", "n_inliers", "median_px",
    "geoid_m", "pairs" (<= max_pairs inliers: x, y, lat, lon, h = ellipsoidal mesh height,
    h_orth, err_px)}``; ``ok`` False with a ``reason`` when there is nothing to solve."""
    rh, rw = render_rgb.shape[:2]
    out = {"ok": False, "camera": cam_to_dict(cam), "n_matches": 0, "n_lifted": 0, "n_inliers": 0,
           "median_px": None, "geoid_m": float(geoid_m), "pairs": []}
    rcam = render_camera(cam, rw, rh, geoid_m)
    geoid = float(geoid_m)
    n_geoid = 0
    if dem is not None and fit_geoid:
        geoid, n_geoid = geoid_from_render(depth, rcam, dem, default=geoid_m)
    out["geoid_m"] = geoid
    pa, pb = match(photo_rgb, render_rgb)
    out["n_matches"] = len(pa)
    if len(pa) < MIN_INLIERS:
        return {**out, "reason": f"only {len(pa)} SIFT matches"}
    lat, lon, h, ok = lift(pb, depth, rcam)
    out["n_lifted"] = int(ok.sum())
    if ok.sum() < MIN_INLIERS:
        return {**out, "reason": f"only {int(ok.sum())} matches have a depth"}
    pa, lat, lon, h = pa[ok], lat[ok], lon[ok], h[ok]
    if dem is not None and fit_geoid and n_geoid < 30:                 # too little ground in the grid: the matches decide
        geoid, _ = estimate_geoid(h, dem(lat, lon), default=geoid_m)
        out["geoid_m"] = geoid
    h_orth = h - geoid
    xy, lla, wts = pa, np.stack([lat, lon, h_orth], axis=1), np.ones(len(pa))
    if user:
        u = np.asarray(user, float).reshape(-1, 5)
        xy = np.vstack([xy, u[:, :2]])
        lla = np.vstack([lla, u[:, 2:5]])
        wts = np.concatenate([wts, np.full(len(u), user_weight)])
    sol = solve_pose(xy, lla, cam, weights=wts)
    out["n_inliers"] = sol["n_inliers"]
    if not sol["n_inliers"]:
        return {**out, "reason": "no consistent pose (too few inliers)"}
    n = len(pa)
    inl = sol["inliers"][:n]
    err = sol["residuals_px"][:n]
    sel = np.where(inl)[0]
    sel = sel[np.argsort(err[sel])]
    if len(sel) > max_pairs:                              # evenly over the error ranking, not just the best
        sel = sel[np.linspace(0, len(sel) - 1, max_pairs).astype(int)]
    out.update(ok=True, camera=cam_to_dict(sol["camera"]), median_px=sol["median_px"],
               pairs=[{"x": float(pa[i, 0]), "y": float(pa[i, 1]), "lat": float(lat[i]), "lon": float(lon[i]),
                       "h": float(h[i]), "h_orth": float(h_orth[i]), "err_px": float(err[i])} for i in sel])
    return out
