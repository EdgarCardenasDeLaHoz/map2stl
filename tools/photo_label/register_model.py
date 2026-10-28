"""Register a photo's camera against OUR untextured city model by edge alignment.

Google's photorealistic tiles can fail (Cartagena gives 0 SIFT inliers), and our own model
(`model_glb.py` glb: OSM footprints and DEM, no texture) always exists. Texture matching is
impossible against it, so the match is geometric:

    model_wireframe(mesh)      3D crease edges (adjacent face normals differ by > CREASE_DEG: wall
                               corners, roof outlines, eaves) plus boundary edges, cached next to the glb
    render_depth(...)          z-buffer of the mesh from a camera, reduced resolution (numpy only)
    visible_samples(...)       3D points every ~STEP_PX along the projected segments, kept when they
                               pass a depth test against that z-buffer
    photo_features(rgb)        distance transform of the photo's Canny edges + skyline row per column
    refine(rgb, tris, wire, cam0, ...)
                               optimise heading, pitch, roll, log f and an east/north/up offset
                               (bounded; truncated least squares) so the projected visible samples land on photo edges, and
                               the model's top silhouette lands on the photo's sky line

The optimiser is coarse to fine: a heading grid on a heavily blurred distance transform picks
start points, then `scipy.optimize.least_squares` (truncated distances) runs on three blur levels
(the first turns the camera only; f and position join on the second, with a weak prior). Visibility is recomputed at the current pose before each stage: one on each coarse level, then
at most MAX_OUTER on the finest, stopping early when a stage barely moves the pose.
Samples come from a render wider than the frame (WIDE) so a heading change can bring edges in.

Frames and datums: the model is in east/north/up metres at `origin` (lon0, lat0), up = orthometric
height, the same datum as every camera `elev_m` (geoid_m is not involved). Camera conventions are
`city2stl.skyline.photo_view.PinholeCamera`. All pixel numbers are at the reduced scale (long side
LONG_SIDE) unless a name says otherwise; the score is the fraction of visible samples within
INLIER_PX of a photo edge.

CLI: `register_model.py --region cartagena --eval-panos` perturbs the known Street View camera
(`server.py::pano_seed`) and reports the recovered error (`evaluate`); `--eval-labels` does the same
with saved hand-solved cameras. Overlays go to runs/photo_label_model_register/.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(REPO_ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

from city2stl.skyline.photo_view import M_PER_DEG_LAT, PinholeCamera  # noqa: E402

CREASE_DEG = 30.0           # adjacent faces turning more than this make a wireframe edge
LONG_SIDE = 480             # reduced image long side, px
STEP_PX = 3.0               # sample spacing along projected segments
MAX_SAMPLES = 5000
MAX_RANGE_M = 1500.0        # segments farther than this from the camera are ignored
NEAR_M = 0.3
WIDE = (1.6, 1.3)           # samples are chosen in a frame this much wider / taller than the photo
INLIER_PX = 3.0
EDGE_PERCENTILE = 92.0      # Canny's high threshold: this percentile of the gradient magnitude (at least 40)
MAX_OUTER = 5
GROUND_CELL_M = 6.0         # wireframe edges with both ends this close to the ground are dropped
GROUND_TOL_M = 2.0
MIN_VISIBLE = 40            # fewer visible samples than this score 0
SKY_WEIGHT = 2.0            # one skyline column counts this many edge samples
SKY_TRUNC_PX = 25.0
# parameter vector: heading deg, pitch deg, roll deg, log f, east m, north m, up m
PARAM_NAMES = ("heading", "pitch", "roll", "logf", "east", "north", "up")
DEFAULT_BOUNDS = (np.array([-15.0, -8.0, -6.0, -0.4, -30.0, -30.0, -10.0]),
                  np.array([15.0, 8.0, 6.0, 0.4, 30.0, 30.0, 10.0]))
# prior: one sigma away from the start costs PRIOR_PX px of mean edge distance (heading, pitch, roll free)
PRIOR_SIGMA = np.array([1e9, 1e9, 1e9, 0.15, 15.0, 15.0, 6.0])
PRIOR_PX = 1.0
X_SCALE = np.array([1.0, 1.0, 1.0, 0.05, 3.0, 3.0, 1.0])
# (blur sigma px, truncation px) per level, coarse to fine
LEVELS = ((8.0, 60.0), (3.0, 30.0), (0.0, 15.0))


# --------------------------------------------------------------------------- camera maths

def basis(heading: float, pitch: float, roll: float):
    """(right, up, forward) in east/north/up: `PinholeCamera.axes`."""
    return PinholeCamera(0.0, 0.0, 0.0, heading, pitch, roll, 1.0, 1, 1).axes()


def cam_position(cam: PinholeCamera, lon0: float, lat0: float) -> np.ndarray:
    """East, north, up (m) of the camera in the model frame at (lon0, lat0)."""
    import model_glb
    return model_glb.lonlath_to_enu([cam.lon], [cam.lat], [cam.elev_m], lon0, lat0)[0]


def shift_camera(cam: PinholeCamera, de: float, dn: float, du: float) -> PinholeCamera:
    """The camera moved by east/north/up metres (the flat-earth inverse of `PinholeCamera.project_ll`)."""
    kx = M_PER_DEG_LAT * math.cos(math.radians(cam.lat))
    return replace(cam, lat=cam.lat + dn / M_PER_DEG_LAT, lon=cam.lon + de / kx, elev_m=cam.elev_m + du)


def apply_params(cam: PinholeCamera, p) -> PinholeCamera:
    """`cam` changed by the parameter vector `p` (see PARAM_NAMES)."""
    c = shift_camera(cam, p[4], p[5], p[6])
    return replace(c, heading_deg=(cam.heading_deg + p[0]) % 360.0, pitch_deg=cam.pitch_deg + p[1],
                   roll_deg=cam.roll_deg + p[2], f_px=cam.f_px * math.exp(p[3]))


def scaled(cam: PinholeCamera, long_side: int = LONG_SIDE) -> tuple[PinholeCamera, float]:
    """The same camera on the reduced image, and the scale factor."""
    s = min(1.0, long_side / max(cam.width, cam.height))
    return replace(cam, width=max(2, round(cam.width * s)), height=max(2, round(cam.height * s)),
                   f_px=cam.f_px * s), s


def widened(cam: PinholeCamera) -> PinholeCamera:
    """A frame larger than `cam`'s with the same f and centre ray (principal point stays centred)."""
    return replace(cam, width=int(cam.width * WIDE[0]) | 1, height=int(cam.height * WIDE[1]) | 1)


def to_camera_space(pts, pos, cam: PinholeCamera) -> np.ndarray:
    """(..., 3) X right, Y up, Z forward (m) of world points seen from `pos` with `cam`'s attitude."""
    r, u, f = basis(cam.heading_deg, cam.pitch_deg, cam.roll_deg)
    d = np.asarray(pts, float) - pos
    return np.stack([d @ r, d @ u, d @ f], axis=-1)


def project(pts, pos, cam: PinholeCamera):
    """Pixel (x, y) and view depth Z of world points (NaN x, y where Z <= NEAR_M)."""
    c = to_camera_space(pts, pos, cam)
    z = c[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        x = cam.width / 2.0 + cam.f_px * c[..., 0] / z
        y = cam.height / 2.0 - cam.f_px * c[..., 1] / z
    bad = z <= NEAR_M
    return np.where(bad, np.nan, x), np.where(bad, np.nan, y), z


# --------------------------------------------------------------------------- model

def mesh_triangles(mesh) -> np.ndarray:
    return np.asarray(mesh.triangles, dtype=np.float32)


def load_mesh(glb_path):
    """The glb as one trimesh (all layers concatenated, east/north/up metres, Z up)."""
    import trimesh
    return trimesh.load(str(glb_path), force="mesh")


def _wire_from_mesh(mesh) -> np.ndarray:
    v = np.asarray(mesh.vertices, float)
    faces = np.asarray(mesh.faces)
    keep = []
    adj = np.asarray(mesh.face_adjacency)
    if len(adj):
        ang = np.degrees(np.asarray(mesh.face_adjacency_angles))
        e = np.asarray(mesh.face_adjacency_edges)
        keep.append(e[ang > CREASE_DEG])
    # boundary edges: undirected edges used by exactly one face
    e3 = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    uniq, counts = np.unique(e3, axis=0, return_counts=True)
    keep.append(uniq[counts == 1])
    e = np.concatenate(keep) if keep else np.zeros((0, 2), int)
    if not len(e):
        return np.zeros((0, 2, 3), np.float32)
    e = np.unique(np.sort(e, axis=1), axis=0)
    seg = v[e]
    lo, hi = v[:, :2].min(0), v[:, :2].max(0)
    rim = (np.isclose(seg[:, :, 0], lo[0], atol=0.5).all(1) | np.isclose(seg[:, :, 0], hi[0], atol=0.5).all(1)
           | np.isclose(seg[:, :, 1], lo[1], atol=0.5).all(1) | np.isclose(seg[:, :, 1], hi[1], atol=0.5).all(1))
    seg = seg[~rim]
    # ground clutter: an edge with both ends within GROUND_TOL_M of the lowest vertex of its cell
    # (terrain edges, building base rings, kerbs) is hidden by cars, trees and walls in a photo
    cell = GROUND_CELL_M
    ij = np.floor((v[:, :2] - lo) / cell).astype(int)
    ng = ij.max(0) + 1
    gmin = np.full(ng, np.inf)
    np.minimum.at(gmin, (ij[:, 0], ij[:, 1]), v[:, 2])
    sij = np.clip(np.floor((seg[:, :, :2] - lo) / cell).astype(int), 0, ng - 1)
    ground = gmin[sij[:, :, 0], sij[:, :, 1]]
    low = ((seg[:, :, 2] - ground) < GROUND_TOL_M).all(1)
    return seg[~low].astype(np.float32)


def model_wireframe(mesh, cache=None) -> np.ndarray:
    """(S, 2, 3) float32 segment endpoints (model metres): crease edges (adjacent face normals
    differ by more than CREASE_DEG) and boundary edges, minus the rim of the model's box and the
    edges lying on the ground (GROUND_TOL_M). `cache` is an .npz path, read when present (the glb is
    content-keyed)."""
    if cache is not None and Path(cache).is_file():
        return np.load(cache)["seg"]
    seg = _wire_from_mesh(mesh)
    if cache is not None:
        np.savez_compressed(cache, seg=seg)
    return seg


# --------------------------------------------------------------------------- rasteriser

def _clip_near(tri_c: np.ndarray) -> list[np.ndarray]:
    """Triangle (3, 3) in camera space clipped to Z >= NEAR_M: 0, 1 or 2 triangles."""
    inside = tri_c[:, 2] > NEAR_M
    out = []
    poly = []
    for i in range(3):
        a, b = tri_c[i], tri_c[(i + 1) % 3]
        ia, ib = inside[i], inside[(i + 1) % 3]
        if ia:
            poly.append(a)
        if ia != ib:
            t = (NEAR_M - a[2]) / (b[2] - a[2])
            poly.append(a + t * (b - a))
    if len(poly) >= 3:
        out.append(np.array(poly[:3]))
        if len(poly) == 4:
            out.append(np.array([poly[0], poly[2], poly[3]]))
    return out


def render_depth(tris: np.ndarray, pos, cam: PinholeCamera, max_range: float = MAX_RANGE_M) -> np.ndarray:
    """Z-buffer (cam.height, cam.width) float32 of view depth Z in metres, inf where nothing is drawn.

    Triangles (F, 3, 3) in model metres. Perspective-correct (1/Z is interpolated in screen space).
    Small triangles are rasterised in vectorised batches; the few large ones one by one."""
    w, h = cam.width, cam.height
    cen = tris.mean(axis=1)
    near = np.linalg.norm(cen - np.asarray(pos, np.float32), axis=1) < max_range
    c = to_camera_space(tris[near].astype(np.float64), pos, cam)          # (F, 3, 3)
    z = c[..., 2]
    front = (z > NEAR_M).all(axis=1)
    cross = (z > NEAR_M).any(axis=1) & ~front
    parts = [c[front]]
    for t in c[cross]:
        parts.extend(p[None] for p in _clip_near(t))
    c = np.concatenate(parts) if parts else np.zeros((0, 3, 3))
    buf = np.full(h * w, np.inf)
    if not len(c):
        return buf.reshape(h, w).astype(np.float32)
    iz = 1.0 / c[..., 2]
    x = w / 2.0 + cam.f_px * c[..., 0] * iz
    y = h / 2.0 - cam.f_px * c[..., 1] * iz
    x0 = np.maximum(np.floor(x.min(1)), 0).astype(int)
    x1 = np.minimum(np.ceil(x.max(1)), w - 1).astype(int)
    y0 = np.maximum(np.floor(y.min(1)), 0).astype(int)
    y1 = np.minimum(np.ceil(y.max(1)), h - 1).astype(int)
    area = (x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0]) - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0])
    ok = (x1 >= x0) & (y1 >= y0) & (np.abs(area) > 1e-9)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    size = np.maximum(bw, bh)
    for lo, hi in ((0, 3), (3, 6), (6, 12), (12, 24), (24, 48), (48, 96)):
        sel = np.flatnonzero(ok & (size > lo) & (size <= hi))
        step = max(64, 600000 // (hi * hi))
        for s in range(0, len(sel), step):
            _raster_batch(buf, sel[s:s + step], hi, x, y, iz, x0, y0, bw, bh, area, w)
    for i in np.flatnonzero(ok & (size > 96)):
        _raster_one(buf, i, x, y, iz, x0, y0, bw, bh, area, w)
    return buf.reshape(h, w).astype(np.float32)


def _raster_one(buf, i, x, y, iz, x0, y0, bw, bh, area, w):
    """Draw one large triangle on its own bw x bh box (a k x k box wastes ~100x on thin long triangles)."""
    px = (x0[i] + np.arange(bw[i]))[None, :]
    py = (y0[i] + np.arange(bh[i]))[:, None]
    xa, ya, ar = x[i], y[i], area[i]
    w1 = ((px - xa[0]) * (ya[2] - ya[0]) - (xa[2] - xa[0]) * (py - ya[0])) / ar
    w2 = ((xa[1] - xa[0]) * (py - ya[0]) - (px - xa[0]) * (ya[1] - ya[0])) / ar
    w0 = 1.0 - w1 - w2
    m = (w0 >= -1e-3) & (w1 >= -1e-3) & (w2 >= -1e-3)
    if not m.any():
        return
    inv = w0 * iz[i, 0] + w1 * iz[i, 1] + w2 * iz[i, 2]
    m &= inv > 0
    flat = (py * w + px + np.zeros_like(m, int))[m]
    zz = 1.0 / inv[m]
    keep = zz < buf[flat]
    buf[flat[keep]] = zz[keep]        # one triangle: no duplicate pixels, so order does not matter


def _raster_batch(buf, idx, k, x, y, iz, x0, y0, bw, bh, area, w):
    """Draw triangles `idx` whose boxes fit in k x k pixels into the flat z-buffer."""
    off = np.arange(k)
    px = (x0[idx, None, None] + off[None, None, :]) + np.zeros((1, k, 1), int)        # (n, k, k)
    py = (y0[idx, None, None] + off[None, :, None]) + np.zeros((1, 1, k), int)
    inb = (off[None, None, :] < bw[idx, None, None]) & (off[None, :, None] < bh[idx, None, None])
    xa, ya = x[idx], y[idx]
    ar = area[idx][:, None, None]
    # barycentric weights of pixel (px, py)
    w1 = ((px - xa[:, 0, None, None]) * (ya[:, 2, None, None] - ya[:, 0, None, None])
          - (xa[:, 2, None, None] - xa[:, 0, None, None]) * (py - ya[:, 0, None, None])) / ar
    w2 = ((xa[:, 1, None, None] - xa[:, 0, None, None]) * (py - ya[:, 0, None, None])
          - (px - xa[:, 0, None, None]) * (ya[:, 1, None, None] - ya[:, 0, None, None])) / ar
    w0 = 1.0 - w1 - w2
    eps = -1e-3
    m = inb & (w0 >= eps) & (w1 >= eps) & (w2 >= eps)
    if not m.any():
        return
    i3 = iz[idx]
    inv = w0 * i3[:, 0, None, None] + w1 * i3[:, 1, None, None] + w2 * i3[:, 2, None, None]
    m &= inv > 0
    flat = (py * w + px)[m]
    zz = 1.0 / inv[m]
    keep = zz < buf[flat]
    flat, zz = flat[keep], zz[keep]
    order = np.argsort(-zz, kind="stable")           # farthest first: the last write (nearest) wins
    buf[flat[order]] = zz[order]


# --------------------------------------------------------------------------- visible samples

def visible_samples(seg: np.ndarray, pos, cam: PinholeCamera, depth: np.ndarray, step_px: float = STEP_PX,
                    max_samples: int = MAX_SAMPLES, max_range: float = MAX_RANGE_M, with_ids: bool = False):
    """(N, 3) model-metre points along the projected segments, every ~step_px, kept when they are in
    the frame of `cam` (the one `depth` was rendered with) and not hidden behind the z-buffer.
    Segments shorter than 2 px on screen are dropped. With `with_ids` also returns (segment index, position
    along it) per point, so neighbouring samples can be joined into lines."""
    pos = np.asarray(pos, float)
    a, b = seg[:, 0].astype(float), seg[:, 1].astype(float)
    mid = (a + b) / 2
    near = np.linalg.norm(mid - pos, axis=1) < max_range
    a, b = a[near], b[near]
    if not len(a):
        return (np.zeros((0, 3)),) * 3 if with_ids else np.zeros((0, 3))
    ca, cb = to_camera_space(a, pos, cam), to_camera_space(b, pos, cam)
    za, zb = ca[:, 2], cb[:, 2]
    vis = (za > NEAR_M) | (zb > NEAR_M)
    a, b, ca, cb, za, zb = a[vis], b[vis], ca[vis], cb[vis], za[vis], zb[vis]
    t0 = np.where(za > NEAR_M, 0.0, (NEAR_M - za) / (zb - za + 1e-12))
    t1 = np.where(zb > NEAR_M, 1.0, (NEAR_M - za) / (zb - za + 1e-12))
    pa, pb = a + t0[:, None] * (b - a), a + t1[:, None] * (b - a)
    xa, ya, _ = project(pa, pos, cam)
    xb, yb, _ = project(pb, pos, cam)
    ln = np.hypot(xb - xa, yb - ya)
    good = np.isfinite(ln) & (ln >= 2.0) & (ln < 20 * max(cam.width, cam.height))
    pa, pb, ln = pa[good], pb[good], ln[good]
    if not len(pa):
        return (np.zeros((0, 3)),) * 3 if with_ids else np.zeros((0, 3))
    step = max(step_px, ln.sum() / max_samples)
    n = np.maximum(2, np.ceil(ln / step).astype(int) + 1)
    seg_i = np.repeat(np.arange(len(n)), n)
    first = np.cumsum(n) - n
    k = np.arange(n.sum()) - np.repeat(first, n)
    t = k / np.repeat(n - 1, n)
    # interpolate in screen space, so the spacing is even in pixels; unproject along the 3D chord
    # (a perspective-correct parameter would be more exact; spacing need not be)
    pts = pa[seg_i] + t[:, None] * (pb[seg_i] - pa[seg_i])
    x, y, z = project(pts, pos, cam)
    xi, yi = np.rint(x), np.rint(y)
    inside = np.isfinite(x) & (xi >= 0) & (xi < cam.width) & (yi >= 0) & (yi < cam.height)
    pts, xi, yi, z = pts[inside], xi[inside].astype(int), yi[inside].astype(int), z[inside]
    sid, kk = seg_i[inside], k[inside]
    import cv2
    dmin = cv2.erode(np.where(np.isfinite(depth), depth, 1e9).astype(np.float32), np.ones((3, 3), np.uint8))
    seen = z <= dmin[yi, xi] + 0.5 + 0.03 * z
    if with_ids:
        return pts[seen], sid[seen], kk[seen]
    return pts[seen]


def skyline_points(depth: np.ndarray, pos, cam: PinholeCamera, photo_sky: np.ndarray, every: int = 2) -> np.ndarray:
    """(M, 3) model-metre points of the model's top silhouette, one per `every` columns, only where
    the model has a top edge (not touching row 0) and the photo has a sky line. `depth`/`cam` share a frame
    whose columns are the photo's (a render of the photo-sized frame)."""
    w, h = cam.width, cam.height
    fin = np.isfinite(depth)
    top = np.where(fin.any(axis=0), fin.argmax(axis=0), -1)
    cols = np.arange(0, w, every)
    cols = cols[(top[cols] > 1) & np.isfinite(photo_sky[cols])]
    if not len(cols):
        return np.zeros((0, 3))
    rows = top[cols]
    z = depth[rows, cols]
    r, u, f = basis(cam.heading_deg, cam.pitch_deg, cam.roll_deg)
    dx, dy = (cols - w / 2.0) / cam.f_px, (rows - h / 2.0) / cam.f_px
    d = f[None] + dx[:, None] * r[None] - dy[:, None] * u[None]
    return np.asarray(pos, float) + z[:, None] * d


# --------------------------------------------------------------------------- photo side

@dataclass
class PhotoFeatures:
    edges: np.ndarray           # uint8 0/255 Canny edges at the reduced scale
    dt: np.ndarray              # distance transform of `edges` (px)
    sky: np.ndarray             # photo sky-line row per reduced column (NaN unknown)
    scale: float
    gray: np.ndarray = field(repr=False, default=None)


def photo_features(rgb: np.ndarray, long_side: int = LONG_SIDE) -> PhotoFeatures:
    """Edges, their distance transform and the sky line of an RGB photo, at the reduced scale."""
    import cv2
    from auto_pose import photo_skyline

    rgb = np.ascontiguousarray(rgb[..., :3]).astype(np.uint8)
    h0, w0 = rgb.shape[:2]
    s = min(1.0, long_side / max(h0, w0))
    w, h = max(2, round(w0 * s)), max(2, round(h0 * s))
    small = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA) if s < 1 else rgb
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    g = cv2.GaussianBlur(gray, (0, 0), 1.2)
    mag = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))   # Canny's own L1 norm
    hi = float(max(40.0, np.percentile(mag, EDGE_PERCENTILE)))
    edges = cv2.Canny(g, 0.4 * hi, hi)
    dt = cv2.distanceTransform(255 - edges, cv2.DIST_L2, 3).astype(np.float64)
    sky_full = photo_skyline(rgb)
    sky = np.full(w, np.nan)
    if np.isfinite(sky_full).any():
        xs = np.clip(np.round((np.arange(w) + 0.5) / s - 0.5).astype(int), 0, w0 - 1)
        sky = sky_full[xs] * s
    return PhotoFeatures(edges, dt, sky, s, gray)


def _level_dt(dt: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return dt
    import cv2
    return cv2.GaussianBlur(dt, (0, 0), sigma)


# --------------------------------------------------------------------------- optimiser

def _sample_dt(dt: np.ndarray, x, y, trunc: float) -> np.ndarray:
    """Distance (px) at (x, y), `trunc` where outside the frame or behind the camera, capped at trunc."""
    from scipy.ndimage import map_coordinates
    h, w = dt.shape
    ok = np.isfinite(x) & np.isfinite(y) & (x >= 0) & (x <= w - 1) & (y >= 0) & (y <= h - 1)
    out = np.full(x.shape, trunc)
    if ok.any():
        out[ok] = np.minimum(map_coordinates(dt, [y[ok], x[ok]], order=1, mode="nearest"), trunc)
    return out


def _residuals(p, cam0, pos0, pts, sky_pts, feats: PhotoFeatures, dts, trunc, w_sky, p_base=None):
    """Edge-distance and skyline residuals of the pose `cam0`/`pos0` changed by `p`; each term is
    divided by the root of its sample count so both are means. A weak Gaussian prior on the focal
    length and the position (total offset `p_base + p`, see PRIOR_SIGMA) keeps the near-degenerate
    f / distance trade from drifting."""
    cam = apply_params(cam0, p)
    pos = pos0 + p[4:7]
    out = []
    if len(pts):
        x, y, _ = project(pts, pos, cam)
        out.append(_sample_dt(dts, x, y, trunc) / math.sqrt(len(pts)))
    if len(sky_pts):
        x, y, _ = project(sky_pts, pos, cam)
        ok = np.isfinite(x) & np.isfinite(y) & (x >= 0) & (x <= cam.width - 1)
        col = np.clip(np.rint(np.where(ok, x, 0)), 0, len(feats.sky) - 1).astype(int)
        tgt = feats.sky[col]
        res = np.full(len(sky_pts), SKY_TRUNC_PX)
        good = ok & np.isfinite(tgt)
        res[good] = np.clip(np.abs(y[good] - tgt[good]), 0, SKY_TRUNC_PX)
        out.append(w_sky * res / math.sqrt(len(sky_pts)))
    if p_base is not None:
        out.append(PRIOR_PX * (p_base + p) / PRIOR_SIGMA)
    return np.concatenate(out) if out else np.zeros(1)


@dataclass
class Fit:
    cam: PinholeCamera          # full-size camera
    params: np.ndarray          # total offsets from cam0 (PARAM_NAMES)
    score: float                # fraction of visible samples within INLIER_PX of a photo edge
    cost: float                 # mean truncated sample distance (px, reduced scale)
    n_samples: int
    sky_px: float               # median |model - photo| skyline row difference (px), NaN if none
    diag: dict = field(default_factory=dict)


class ModelScene:
    """A mesh prepared for repeated rendering: triangles, wireframe segments, origin."""

    def __init__(self, tris: np.ndarray, seg: np.ndarray, lon0: float, lat0: float):
        self.tris, self.seg, self.lon0, self.lat0 = tris, seg, lon0, lat0

    def near(self, pos, radius: float) -> ModelScene:
        """The part of the scene within `radius` m of `pos` (triangles by nearest vertex, segments by midpoint)."""
        pos = np.asarray(pos, np.float32)
        tm = np.linalg.norm(self.tris - pos, axis=2).min(1) < radius
        sm = np.linalg.norm((self.seg[:, 0] + self.seg[:, 1]) / 2 - pos, axis=1) < radius
        return ModelScene(self.tris[tm], self.seg[sm], self.lon0, self.lat0)

    @classmethod
    def from_glb(cls, glb_path, lon0: float, lat0: float) -> ModelScene:
        glb = Path(glb_path)
        mesh = load_mesh(glb)
        seg = model_wireframe(mesh, glb.with_suffix(".wire2.npz"))
        return cls(mesh_triangles(mesh), seg, lon0, lat0)

    @classmethod
    def from_mesh(cls, mesh, lon0: float = 0.0, lat0: float = 0.0) -> ModelScene:
        return cls(mesh_triangles(mesh), model_wireframe(mesh), lon0, lat0)


def _view_data(scene: ModelScene, cam_small: PinholeCamera, pos, photo_sky):
    """Visible edge samples (from the wide frame) and skyline points at the pose."""
    wide = widened(cam_small)
    depth_w = render_depth(scene.tris, pos, wide)
    pts = visible_samples(scene.seg, pos, wide, depth_w)
    oy, ox = (wide.height - cam_small.height) // 2, (wide.width - cam_small.width) // 2
    depth = depth_w[oy:oy + cam_small.height, ox:ox + cam_small.width]      # same f and centre: a crop
    sky_pts = skyline_points(depth, pos, cam_small, photo_sky) if photo_sky is not None else np.zeros((0, 3))
    return pts, sky_pts, depth


def evaluate_pose(scene: ModelScene, feats: PhotoFeatures, cam_small: PinholeCamera, pos) -> tuple[float, float, int, float]:
    """(inlier fraction, mean truncated distance, samples, median skyline error) at an exact pose."""
    pts, sky_pts, _ = _view_data(scene, cam_small, pos, feats.sky)
    return _score(pts, sky_pts, feats, cam_small, pos)


def _score(pts, sky_pts, feats, cam_small, pos):
    if not len(pts):
        return 0.0, float(LEVELS[-1][1]), 0, float("nan")
    x, y, _ = project(pts, pos, cam_small)
    d = _sample_dt(feats.dt, x, y, 1e3)
    score = float((d <= INLIER_PX).mean()) if len(pts) >= MIN_VISIBLE else 0.0
    cost = float(np.minimum(d, LEVELS[-1][1]).mean())
    sky = float("nan")
    if len(sky_pts):
        sx, sy, _ = project(sky_pts, pos, cam_small)
        okx = np.isfinite(sx) & np.isfinite(sy)
        col = np.clip(np.rint(np.where(okx, sx, 0)), 0, len(feats.sky) - 1).astype(int)
        e = np.where(okx, np.abs(sy - feats.sky[col]), np.nan)
        e = e[np.isfinite(e)]
        sky = float(np.median(e)) if len(e) else float("nan")
    return score, cost, len(pts), sky


def refine(rgb_or_feats, scene: ModelScene, cam0: PinholeCamera, *, bounds=None, search: bool = True,
           max_outer: int = MAX_OUTER, h_range: float = 14.0, h_step: float = 2.0, n_keep: int = 2,
           deadline: float | None = None, log=None) -> Fit:
    """Move `cam0` (full-size photo camera) so the model's edges and silhouette fit the photo.

    `rgb_or_feats` is an RGB array or the `PhotoFeatures` of one. `bounds` is (lo, hi) over
    PARAM_NAMES relative to cam0 (default DEFAULT_BOUNDS). With `search`, the start itself and the
    best (up to two) cells of a heading grid on the most blurred level are each refined,
    and the fit with the best score is kept. The grid spans +-`h_range` degrees in `h_step` steps and
    keeps `n_keep` cells. `deadline` (time.time() value) stops the work between levels and starts. A pose that loses more than half of the start pose's visible samples has
    its score scaled down by the kept fraction. Returns a `Fit` whose `cam` is full size."""
    from scipy.optimize import least_squares

    feats = rgb_or_feats if isinstance(rgb_or_feats, PhotoFeatures) else photo_features(rgb_or_feats)
    lo, hi = (np.asarray(b, float) for b in (bounds or DEFAULT_BOUNDS))
    small0, s = scaled(cam0)
    if feats.dt.shape != (small0.height, small0.width):
        small0 = replace(small0, width=feats.dt.shape[1], height=feats.dt.shape[0],
                         f_px=cam0.f_px * feats.dt.shape[1] / cam0.width)
    pos0 = cam_position(cam0, scene.lon0, scene.lat0)
    scene = scene.near(pos0, MAX_RANGE_M + 60.0)
    dts = [_level_dt(feats.dt, sg) for sg, _ in LEVELS]
    t_start = time.time()

    def say(msg):
        if log:
            log(f"  [{time.time() - t_start:5.1f}s] {msg}")

    def run(p_start, levels):
        p = np.array(p_start, float)
        for lv in levels:
            if deadline and time.time() > deadline:
                break
            cam = apply_params(small0, p)
            pos = pos0 + p[4:7]
            pts, sky_pts, _ = _view_data(scene, cam, pos, feats.sky)
            if len(pts) < 20 and not len(sky_pts):
                say(f"level {lv}: only {len(pts)} samples, stopped")
                break
            # q = offsets from the stage's start pose p; the bounds apply to the total p + q
            blo, bhi = np.minimum(lo - p, -1e-9), np.maximum(hi - p, 1e-9)
            if lv == 0:      # the blurred level only turns the camera: it would shrink f and dolly back
                blo[3:], bhi[3:] = -1e-6, 1e-6
            res = least_squares(
                lambda q: _residuals(q, cam, pos, pts, sky_pts, feats, dts[lv], LEVELS[lv][1], SKY_WEIGHT, p),  # noqa: B023 (called now)
                np.zeros(7), bounds=(blo, bhi),
                x_scale=X_SCALE, loss="linear",   # distances are already truncated

                diff_step=1e-2, max_nfev=40)
            p = p + res.x
            if lv == 2 and np.all(np.abs(res.x) < 0.02 * X_SCALE):
                break
            say(f"level {lv}: {len(pts)} samples + {len(sky_pts)} sky, cost {res.cost:.4g}, p={np.round(p, 2).tolist()}")
        return p

    starts = [np.zeros(7)]
    if search:
        starts = [np.zeros(7)] + [q for q in _grid_search(scene, feats, small0, pos0, dts[0], lo, hi, say,
                                                                  h_range, h_step, n_keep)
                                  if abs(q[0]) >= 4.0 or abs(q[3]) >= 0.15]
    n0 = len(_view_data(scene, small0, pos0, None)[0])
    best = None
    for p0 in starts:
        if deadline and time.time() > deadline and best is not None:
            break
        p = run(p0, [0, 1] + [2] * max_outer)
        cam = apply_params(small0, p)
        sc, cost, n, sky = _score(*_view_data(scene, cam, pos0 + p[4:7], feats.sky)[:2], feats, cam, pos0 + p[4:7])
        if n0:
            sc *= min(1.0, n / (0.5 * n0))
        say(f"start {np.round(p0, 1).tolist()} -> score {sc:.3f} cost {cost:.2f} samples {n}/{n0}")
        if best is None or (sc, -cost) > (best[1], -best[2]):
            best = (p, sc, cost, n, sky)
    p, sc, cost, n, sky = best
    full = apply_params(cam0, p)
    return Fit(full, p, sc, cost, n, sky, {"starts": len(starts), "seconds": round(time.time() - t_start, 1)})


def _grid_search(scene, feats, small0, pos0, dt0, lo, hi, say, h_range: float = 14.0, h_step: float = 2.0,
                 n_keep: int = 2) -> list[np.ndarray]:
    """Coarse heading grid at the start focal length (samples chosen once in a frame wide enough to rotate through)."""
    half = math.atan(small0.width / 2 / small0.f_px) + math.radians(h_range + 3.0)
    ww = min(3.5 * small0.width, 2 * small0.f_px * math.tan(min(half, math.radians(80.0))))
    wide = replace(small0, width=int(ww) | 1, height=int(small0.height * 1.4) | 1)
    depth = render_depth(scene.tris, pos0, wide)
    pts = visible_samples(scene.seg, pos0, wide, depth)
    if len(pts) < 20:
        return [np.zeros(7)]
    cand = []
    for dh in np.arange(max(lo[0], -h_range), min(hi[0], h_range) + 1e-9, h_step):
        for lf in (0.0,):
            p = np.zeros(7)
            p[0], p[3] = dh, lf
            cam = apply_params(small0, p)
            x, y, _ = project(pts, pos0, cam)
            cost = float(_sample_dt(dt0, x, y, LEVELS[0][1]).mean())
            cand.append((cost, p))
    cand.sort(key=lambda c: c[0])
    out, seen = [], []
    for _cost, p in cand:
        if all(abs(p[0] - q[0]) >= 4.0 or abs(p[3] - q[3]) >= 0.15 for q in seen):
            out.append(p)
            seen.append(p)
        if len(out) == n_keep:
            break
    say("grid starts " + ", ".join(f"(dh {p[0]:+.0f}, dlogf {p[3]:+.1f})" for p in out))
    return out


# --------------------------------------------------------------------------- evaluation

OUT_DIR = REPO_ROOT / "runs" / "photo_label_model_register"
CASES = (("none", {}), ("h-10", {"dh": -10.0}), ("h-5", {"dh": -5.0}), ("h+5", {"dh": 5.0}),
         ("h+10", {"dh": 10.0}), ("f*1.2", {"f": 1.2}), ("pos15", {"pos": 15.0}))


def perturb(cam: PinholeCamera, spec: dict, k: int = 0) -> PinholeCamera:
    """`cam` with heading shifted by dh degrees, f scaled, and moved `pos` metres on bearing 70*k deg."""
    c = replace(cam, heading_deg=(cam.heading_deg + spec.get("dh", 0.0)) % 360.0,
                f_px=cam.f_px * spec.get("f", 1.0))
    if spec.get("pos"):
        b = math.radians(70.0 * k)
        c = shift_camera(c, spec["pos"] * math.sin(b), spec["pos"] * math.cos(b), 0.0)
    return c


def pose_error(cam: PinholeCamera, truth: PinholeCamera, lon0: float, lat0: float) -> dict:
    """Heading error (deg, signed), position error (m, 3D), pitch error (deg) and f ratio."""
    return {"heading": (cam.heading_deg - truth.heading_deg + 180.0) % 360.0 - 180.0,
            "pitch": cam.pitch_deg - truth.pitch_deg,
            "pos_m": float(np.linalg.norm(cam_position(cam, lon0, lat0) - cam_position(truth, lon0, lat0))),
            "f_ratio": cam.f_px / truth.f_px}


def reduced_like(cam: PinholeCamera, feats: PhotoFeatures) -> PinholeCamera:
    """`cam` on the reduced image of `feats`."""
    h, w = feats.dt.shape
    return replace(cam, width=w, height=h, f_px=cam.f_px * w / cam.width)


def overlay(rgb: np.ndarray, scene: ModelScene, rows: list[tuple[str, PinholeCamera]], path: Path,
            scale: int = 2) -> None:
    """One PNG: the photo with the visible model edges drawn as thick red lines (the visible pieces
    of the wire segments, not the samples), one panel per (title, camera), stacked."""
    import cv2
    feats = photo_features(rgb)
    h, w = feats.dt.shape
    base = cv2.resize(np.ascontiguousarray(rgb[..., :3]), (w * scale, h * scale), interpolation=cv2.INTER_AREA)
    panels = []
    for title, cam in rows:
        small = reduced_like(cam, feats)
        pos = cam_position(cam, scene.lon0, scene.lat0)
        sc = scene.near(pos, MAX_RANGE_M + 60.0)
        wide = widened(small)
        pts, sid, kk = visible_samples(sc.seg, pos, wide, render_depth(sc.tris, pos, wide), step_px=2.0, with_ids=True)
        img = (base * 0.7).astype(np.uint8)
        x, y, _ = project(pts, pos, small)
        xy = np.stack([x, y], 1) * scale
        for j in range(len(pts) - 1):
            if sid[j] == sid[j + 1] and kk[j + 1] - kk[j] == 1 and np.isfinite(xy[j]).all() and np.isfinite(xy[j + 1]).all():
                cv2.line(img, tuple(np.rint(xy[j]).astype(int)), tuple(np.rint(xy[j + 1]).astype(int)),
                         (255, 40, 40), 2, cv2.LINE_AA)
        score, _, n, _ = _score(pts, np.zeros((0, 3)), feats, small, pos)
        cv2.putText(img, f"{title}  score {score:.2f}  samples {n}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (255, 255, 0), 2, cv2.LINE_AA)
        panels.append(img)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.concatenate(panels, axis=0)[..., ::-1])


def evaluate(items: list[tuple[str, np.ndarray, PinholeCamera]], scene: ModelScene, out_dir: Path, tag: str,
             cases=CASES, log=print) -> list[dict]:
    """Run `refine` from every perturbed start of each (name, rgb, true camera); print the table,
    write `<out_dir>/<tag>.json` and one overlay per item (truth / worst start / its refinement)."""
    rows = []
    for idx, (name, rgb, truth) in enumerate(items):
        feats = photo_features(rgb)
        truth_score = evaluate_pose(scene, feats, reduced_like(truth, feats),
                                    cam_position(truth, scene.lon0, scene.lat0))[0]
        worst = None
        for ci, (cname, spec) in enumerate(cases):
            start = perturb(truth, spec, idx + ci)
            t = time.time()
            fit = refine(feats, scene, start)
            e0 = pose_error(start, truth, scene.lon0, scene.lat0)
            e1 = pose_error(fit.cam, truth, scene.lon0, scene.lat0)
            row = {"frame": name, "case": cname, "start": e0, "final": e1, "score": fit.score,
                   "truth_score": truth_score, "samples": fit.n_samples, "sky_px": fit.sky_px,
                   "seconds": round(time.time() - t, 1)}
            rows.append(row)
            log(f"{name:34s} {cname:6s} head {e0['heading']:+6.1f} -> {e1['heading']:+6.2f}  "
                f"pos {e0['pos_m']:5.1f} -> {e1['pos_m']:5.1f} m  f {e0['f_ratio']:.2f} -> {e1['f_ratio']:.2f}  "
                f"score {fit.score:.2f} (truth {truth_score:.2f})  {row['seconds']} s")
            key = abs(e1["heading"]) + e1["pos_m"] / 5
            if worst is None or key > worst[0]:
                worst = (key, cname, start, fit.cam)
        if worst:
            _, cname, start, after = worst
            overlay(rgb, scene, [("truth", truth), (f"start {cname}", start), ("refined", after)],
                    out_dir / f"{tag}_{name}.png")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{tag}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


PANO_BOUNDS = (np.array([-25.0, -8.0, -6.0, -0.4, -10.0, -10.0, -5.0]),
               np.array([25.0, 8.0, 6.0, 0.4, 10.0, 10.0, 5.0]))


def evaluate_headings(items: list[tuple[str, np.ndarray, PinholeCamera]], scene: ModelScene, out_dir: Path,
                      tag: str, log=print) -> list[dict]:
    """Photospheres whose north is uncalibrated: the seed camera's heading is not truth. For each
    (name, rgb, seed camera) search the heading over +-25 deg (1 deg grid, then `refine`, position
    within +-10 m), report the heading offset found and the score at the seed and at the found pose,
    and write a seed / found overlay. The frames of one site (`<site>_<n>`) share one north error, so
    their offsets agreeing is the check; a per-site summary is printed and written to `<tag>.json`."""
    rows = []
    for name, rgb, seed in items:
        feats = photo_features(rgb)
        s0 = evaluate_pose(scene, feats, reduced_like(seed, feats), cam_position(seed, scene.lon0, scene.lat0))
        t = time.time()
        fit = refine(feats, scene, seed, bounds=PANO_BOUNDS, h_range=25.0, h_step=1.0, n_keep=3)
        e = pose_error(fit.cam, seed, scene.lon0, scene.lat0)
        row = {"frame": name, "site": name.rsplit("_", 1)[0], "offset_deg": e["heading"], "pitch_deg": e["pitch"],
               "move_m": e["pos_m"], "f_ratio": e["f_ratio"], "seed_score": s0[0], "seed_samples": s0[2],
               "score": fit.score, "samples": fit.n_samples, "seconds": round(time.time() - t, 1)}
        rows.append(row)
        log(f"{name:34s} offset {e['heading']:+6.1f} deg  move {e['pos_m']:4.1f} m  f x{e['f_ratio']:.2f}  "
            f"score {s0[0]:.2f} (n {s0[2]}) -> {fit.score:.2f} (n {fit.n_samples})  {row['seconds']} s")
        overlay(rgb, scene, [("seed", seed), ("found", fit.cam)], out_dir / f"{tag}_{name}.png")
    sites = {}
    for r in rows:
        sites.setdefault(r["site"], []).append(r)
    summary = {}
    for site, rs in sites.items():
        off = [r["offset_deg"] for r in rs]
        summary[site] = {"offsets": [round(o, 1) for o in off], "spread_deg": round(max(off) - min(off), 1),
                         "median_offset": round(float(np.median(off)), 1),
                         "median_score": round(float(np.median([r["score"] for r in rs])), 2),
                         "median_seed_score": round(float(np.median([r["seed_score"] for r in rs])), 2)}
        log(f"SITE {site}: {summary[site]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{tag}.json").write_text(json.dumps({"frames": rows, "sites": summary}, indent=1), encoding="utf-8")
    return rows


COMMONS_PICKS = (
    "bocagrande_and_the_historic", "buildings_in_cartagena_colombia_01", "cartagena_de_indias_desde_el_cerro",
    "cartagena_panorama_from_la_popa", "cartagena_vista_desde_la_muralla", "castillo_de_san_philipe",
    "castillo_san_felipe_casco_antiguo_from_la_popa_ctg_11_2019_1", "cityscape_cartagena",
    "ciudad_de_cartagena_vista_desde_el_convento", "colombia_cartagena_castillogrande",
    "view_from_la_popa_cartagena_colombia_24613687699", "view_from_la_popa_cartagena_colombia_24888003701",
    "view_from_la_popa_cartagena_colombia_24981318815", "view_from_our_apartment", "vista_morroscity_panoramio_10")
COMMONS_BOUNDS = (np.array([-30.0, -8.0, -6.0, -0.4, -30.0, -30.0, -10.0]),
                  np.array([30.0, 8.0, 6.0, 0.4, 30.0, 30.0, 10.0]))
PHOTO_BUDGET_S = 60.0


def commons_items(cfg, picks=COMMONS_PICKS, log=print) -> list[dict]:
    """The Commons photos to register, each {name, path, guesses: [PinholeCamera], labels, f35}. Skips (and logs,
    one line each) flagged photos, stitched panoramas / street-level screens, photos without cached start
    guesses, and any not in `picks`. Guesses: cached `<stem>_guesses.json` (`server.api_register_candidates`),
    the title-pinned one first, then the best-ranked, three in all."""
    import server
    from register import cam_from_dict  # noqa: F401  (kept: same dict shape as the labels)
    flags = {}
    fp = Path(cfg.annotations_dir) / f"{cfg.region}_photo_flags.json"
    if fp.is_file():
        flags = json.loads(fp.read_text(encoding="utf-8"))
    items = []
    for _key, _root, p in server._photo_files(cfg):
        name = p.parent.name
        scr = p.with_name(p.stem + "_screen.json")
        reason = ""
        if any(k.endswith(f"/{name}/{p.name}") for k in flags):
            reason = "flagged"
        elif scr.is_file() and (json.loads(scr.read_text(encoding="utf-8")).get("reason") or "").strip():
            reason = "screen: " + json.loads(scr.read_text(encoding="utf-8"))["reason"]
        elif not p.with_name(p.stem + "_guesses.json").is_file():
            reason = "no cached start guesses"
        elif not any(name.startswith(pk) for pk in picks):
            continue
        if reason:
            log(f"skip {name[:50]}: {reason}")
            continue
        cands = json.loads(p.with_name(p.stem + "_guesses.json").read_text(encoding="utf-8"))["response"]["candidates"]
        order = sorted(range(len(cands)), key=lambda i: (cands[i].get("source") != "title", i))[:3]
        items.append({"name": name, "path": p, "cands": [cands[i] for i in order], "f35": server.photo_focal_35mm(p)})
    return items


def evaluate_commons(items: list[dict], scene: ModelScene, out_dir: Path, tag: str, log=print) -> list[dict]:
    """Register each Commons photo from its start guesses: wide heading search (+-30 deg), position within
    +-30 m, f from EXIF when there is one. The guess with the best score wins; one overlay (start and end of
    the winner) and `<tag>.json`. No ground truth, so the score and the overlay are the only evidence."""
    rows = []
    for it in items:
        t0 = time.time()
        rgb = _load_rgb(it["path"])
        feats = photo_features(rgb)
        best = None
        tried = []
        for gi, c in enumerate(it["cands"]):
            f = c["f_px"] * rgb.shape[1] / c["width"]
            if it["f35"]:
                f = it["f35"] / 36.0 * max(rgb.shape[:2])
            cam = PinholeCamera(c["lat"], c["lon"], c["elev_m"], c["heading_deg"], c["pitch_deg"], c["roll_deg"],
                                f, rgb.shape[1], rgb.shape[0])
            lo, hi = (b.copy() for b in COMMONS_BOUNDS)
            if it["f35"]:
                lo[3], hi[3] = -0.02, 0.02
            s0 = evaluate_pose(scene, feats, reduced_like(cam, feats), cam_position(cam, scene.lon0, scene.lat0))
            if time.time() - t0 > PHOTO_BUDGET_S * 0.9:
                break
            fit = refine(feats, scene, cam, bounds=(lo, hi), h_range=30.0, h_step=2.0, n_keep=2,
                         deadline=t0 + PHOTO_BUDGET_S * (gi + 1) / len(it["cands"]))
            e = pose_error(fit.cam, cam, scene.lon0, scene.lat0)
            rec = {"guess": gi, "source": c.get("source"), "label": c.get("label"), "start_score": s0[0],
                   "start_samples": s0[2], "score": fit.score, "samples": fit.n_samples,
                   "heading_change": e["heading"], "move_m": e["pos_m"], "f_ratio": e["f_ratio"],
                   "pitch_change": e["pitch"]}
            tried.append(rec)
            if best is None or (fit.score, -fit.cost) > (best[0].score, -best[0].cost):
                best = (fit, cam, rec)
        fit, cam, rec = best
        secs = round(time.time() - t0, 1)
        rows.append({"photo": it["name"], "f35": it["f35"], **rec, "seconds": secs, "tried": tried})
        log(f"{it['name'][:46]:46s} guess {rec['guess']} ({rec['source']})  score {rec['start_score']:.2f} -> "
            f"{rec['score']:.2f}  n {rec['start_samples']} -> {rec['samples']}  head {rec['heading_change']:+.1f}  "
            f"move {rec['move_m']:.1f} m  {secs} s")
        overlay(rgb, scene, [(f"start (guess {rec['guess']})", cam), ("end", fit.cam)],
                out_dir / f"cartagena_commons_{it['name']}.png")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{tag}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def _load_rgb(path) -> np.ndarray:
    import cv2
    im = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if im is None:
        raise OSError(f"cannot read {path}")
    return im[..., ::-1].copy()


def scene_for(cfg) -> ModelScene:
    """The region's cached (or freshly built) model as a `ModelScene`."""
    import model_glb
    meta = model_glb.build_or_load(cfg.dem(), cfg.dem_path, cfg.region, cfg.model_dir,
                                   footprints=cfg.footprints, osm_path=cfg.osm_path)
    return ModelScene.from_glb(meta["glb_path"], meta["origin"]["lon"], meta["origin"]["lat"])


def pano_items(cfg) -> list[tuple[str, np.ndarray, PinholeCamera]]:
    """Street View frames of the region with their known cameras (`server.py::pano_seed`)."""
    import server
    from register import cam_from_dict
    items = []
    for _key, _root, p in server._photo_files(cfg):
        seed = server.pano_seed(p, cfg)
        if seed is not None:
            items.append((p.stem, _load_rgb(p), cam_from_dict(seed)))
    return items


def label_items(cfg) -> list[tuple[str, np.ndarray, PinholeCamera]]:
    """Photos whose saved label file carries a hand-solved pinhole `camera`."""
    import server
    from register import cam_from_dict
    items = []
    for lp in sorted(Path(cfg.annotations_dir).glob(f"{cfg.region}_*.json")):
        try:
            d = json.loads(lp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not d.get("camera") or d.get("projection", "pinhole") != "pinhole" or not d.get("file"):
            continue
        p = server.safe_photo(cfg, d["file"])
        if p is not None:
            rgb = _load_rgb(p)
            items.append((Path(d["file"]).parent.name or p.stem, rgb,
                          cam_from_dict(d["camera"], rgb.shape[1], rgb.shape[0])))
    return items


def main() -> int:
    from city2stl.resources import HEAVY_JOB_MIN_FREE_GB, scratch_guard
    ram = next((float(sys.argv[i + 1]) for i, v in enumerate(sys.argv[:-1]) if v == "--ram-gb"), HEAVY_JOB_MIN_FREE_GB)
    workers = scratch_guard(ram_gb=ram, max_workers=2)    # before numpy / cv2 do any work
    del workers
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--region", default="cartagena")
    ap.add_argument("--eval-panos", action="store_true", help="perturb the known Street View cameras")
    ap.add_argument("--eval-commons", action="store_true", help="register the Commons photos from cached start guesses")
    ap.add_argument("--eval-labels", action="store_true", help="perturb saved hand-solved cameras")
    ap.add_argument("--cases", default=None, help="comma list of: " + ",".join(c for c, _ in CASES))
    ap.add_argument("--limit", type=int, default=0, help="only the first N frames")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--ram-gb", type=float, default=HEAVY_JOB_MIN_FREE_GB, help="free RAM to wait for")
    a = ap.parse_args()
    import server
    cfg = server.build_regions(a.region).get(a.region)
    scene = scene_for(cfg)
    cases = [c for c in CASES if not a.cases or c[0] in a.cases.split(",")]
    print(f"model: {len(scene.tris)} faces, {len(scene.seg)} wire segments", flush=True)
    if a.eval_commons:
        items = commons_items(cfg)[: a.limit or None]
        print(f"{a.region}_commons: {len(items)} photos", flush=True)
        evaluate_commons(items, scene, Path(a.out), f"{a.region}_commons", log=lambda m: print(m, flush=True))
    for flag, loader, tag in ((a.eval_panos, pano_items, f"{a.region}_panos"),
                              (a.eval_labels, label_items, f"{a.region}_labels")):
        if flag:
            items = loader(cfg)[: a.limit or None]
            print(f"{tag}: {len(items)} frames", flush=True)
            run = evaluate_headings if tag.endswith("panos") else evaluate
            args = (items, scene, Path(a.out), tag) + (() if tag.endswith("panos") else (cases,))
            run(*args, log=lambda m: print(m, flush=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
