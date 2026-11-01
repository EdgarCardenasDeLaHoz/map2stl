"""skyline.photo_view — one elevated pinhole photo as a ``footprint_detect.Pano`` (F-PHOTOVIEW).

A photo taken from a height (Granada from the Alhambra) looks down on roofs, which is the
drone roof fit's case (``_pano/roof_fit.py``), but that code takes a ``footprint_detect.Pano``:
one heading per column and rows at ``pitch + atan((H/2 - row) / f)``. A pinhole frame is not
that: off the centre column a pixel row is not a line of constant elevation, and the heading of
a column changes with the row once the camera is pitched. Reading a plain pinhole photo as a
Pano is wrong by up to ~16 % in ``tan`` at the 33 deg edge of a 67 deg frame.

So the photo is resampled (:func:`reproject`) onto a Pano grid: columns equi-angular in heading
at the photo's centre resolution (1/f rad per column), rows by the Pano row model with the
camera's pitch, every output pixel looked up in the pinhole frame through the full camera
rotation (heading, pitch, roll). RGB is bilinear; labels, instances and depth nearest-neighbour.

Camera (:class:`PinholeCamera`): position and heading from the hand labels' columns
(``camera_solver.solve_pose``); pitch, roll, focal length and the camera's absolute elevation
from the labels' rows as well (:func:`solve_from_rows`), the labels standing on the terrain
model (``dem``). The roof fit then refines elevation and pitch from building feet
(``roof_fit.check_pose_from_bases`` with per-footprint bases, :func:`refine_from_bases`).

Terrain is not one plane (Granada: Plaza Nueva ~690 m, the Albaicín to ~780 m, the camera on
the Alcazaba), so each footprint gets its own base from the terrain model
(:func:`footprint_bases`) and the roof fit takes the camera height above that base
(``roof_fit.fit_roof_heights(cam_h=)``). A constant offset of the terrain model cancels: the
camera elevation is solved against the same model.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from . import footprint_detect as fd

M_PER_DEG_LAT = fd.M_PER_DEG_LAT


# --------------------------------------------------------------------------- camera


@dataclass(frozen=True)
class PinholeCamera:
    """A pinhole camera at ``(lat, lon, elev_m)`` (absolute metres): ``heading_deg`` the bearing
    of the image centre, ``pitch_deg`` up positive, ``roll_deg`` positive when the right side of
    the frame is higher (``camera_solver``'s convention), principal point at the image centre."""
    lat: float
    lon: float
    elev_m: float
    heading_deg: float
    pitch_deg: float
    roll_deg: float
    f_px: float
    width: int
    height: int

    def axes(self):
        """``(right, up, forward)`` unit vectors in local (east, north, up)."""
        h, p, r = (math.radians(a) for a in (self.heading_deg, self.pitch_deg, self.roll_deg))
        fwd = np.array([math.sin(h) * math.cos(p), math.cos(h) * math.cos(p), math.sin(p)])
        right0 = np.array([math.cos(h), -math.sin(h), 0.0])
        up0 = np.cross(right0, fwd)
        # roll r: the scene turns by +r in the image (right side up), i.e. the axes turn by -r
        right = math.cos(r) * right0 + math.sin(r) * up0
        up = -math.sin(r) * right0 + math.cos(r) * up0
        return right, up, fwd

    def project_dirs(self, d):
        """Pixel ``(x, y)`` of directions ``d`` (..., 3) in (east, north, up); NaN behind."""
        right, up, fwd = self.axes()
        d = np.asarray(d, float)
        z = d @ fwd
        with np.errstate(divide="ignore", invalid="ignore"):
            x = self.width / 2.0 + self.f_px * (d @ right) / z
            y = self.height / 2.0 - self.f_px * (d @ up) / z
        bad = z <= 1e-9
        return np.where(bad, np.nan, x), np.where(bad, np.nan, y)

    def project_ll(self, lat, lon, elev_m):
        """Pixel ``(x, y)`` of points given by lat/lon and absolute elevation."""
        kx = M_PER_DEG_LAT * math.cos(math.radians(self.lat))
        d = np.stack([(np.asarray(lon, float) - self.lon) * kx,
                      (np.asarray(lat, float) - self.lat) * M_PER_DEG_LAT,
                      np.asarray(elev_m, float) - self.elev_m], axis=-1)
        return self.project_dirs(d)


def dirs_of(heading_deg, elev_deg):
    """Unit directions (east, north, up) of bearings and elevations (broadcast)."""
    h, e = np.radians(heading_deg), np.radians(elev_deg)
    return np.stack(np.broadcast_arrays(np.sin(h) * np.cos(e), np.cos(h) * np.cos(e), np.sin(e)),
                    axis=-1)


# --------------------------------------------------------------------------- reprojection


@dataclass(frozen=True)
class PanoGrid:
    """The Pano grid a photo is resampled onto, and where each of its pixels lies in the photo."""
    frame_heading: np.ndarray        # W, geographic bearing per column (pose offset 0)
    f_px: float
    pitch_deg: float
    src_x: np.ndarray                # H x W photo column of each pano pixel (NaN: outside)
    src_y: np.ndarray

    @property
    def shape(self):
        return self.src_x.shape

    def inside(self, width: int, height: int) -> np.ndarray:
        return (np.isfinite(self.src_x) & (self.src_x >= 0) & (self.src_x <= width - 1)
                & (self.src_y >= 0) & (self.src_y <= height - 1))


def pano_grid(cam: PinholeCamera) -> PanoGrid:
    """Columns 1/f rad apart over the photo's heading span (the centre column at
    ``heading_deg``); rows by the Pano model with the camera's pitch and focal length, as many
    as cover the photo (the pano centre row stays at H/2, so the count is set by the farther
    of the photo's top and bottom edges)."""
    f = cam.f_px
    # heading/elevation of the photo's border pixels sets the span
    right, up, fwd = cam.axes()
    bx = np.concatenate([np.arange(cam.width), np.full(cam.height, cam.width - 1.0),
                         np.arange(cam.width), np.zeros(cam.height)]).astype(float)
    by = np.concatenate([np.zeros(cam.width), np.arange(cam.height),
                         np.full(cam.width, cam.height - 1.0), np.arange(cam.height)]).astype(float)
    d = (fwd[None] * f + right[None] * (bx - cam.width / 2.0)[:, None]
         - up[None] * (by - cam.height / 2.0)[:, None])
    az = np.degrees(np.arctan2(d[:, 0], d[:, 1]))
    rel = (az - cam.heading_deg + 180.0) % 360.0 - 180.0
    el = np.degrees(np.arctan2(d[:, 2], np.hypot(d[:, 0], d[:, 1])))
    half_w = math.radians(max(abs(rel.min()), abs(rel.max())))
    n_half = int(math.ceil(half_w * f))
    W = 2 * n_half + 1
    rows_off = f * np.tan(np.radians(el - cam.pitch_deg))
    n_half_h = int(math.ceil(np.abs(rows_off).max()))
    H = 2 * n_half_h
    cols = np.arange(W) - n_half
    frame_heading = (cam.heading_deg + np.degrees(cols / f)) % 360.0
    # pixel index = coordinate on both sides, as Pano.elevation_deg(row) and camera_solver's
    # cx = width / 2 read them
    elev = cam.pitch_deg + np.degrees(np.arctan((H / 2.0 - np.arange(H)) / f))
    sx, sy = cam.project_dirs(dirs_of(frame_heading[None, :], elev[:, None]))
    return PanoGrid(frame_heading, f, cam.pitch_deg, sx, sy)


def reproject(img: np.ndarray, grid: PanoGrid, *, nearest: bool = False, fill=0):
    """``img`` (H x W [x C]) of the photo resampled onto ``grid``; ``fill`` outside the photo."""
    img = np.asarray(img)
    h, w = img.shape[:2]
    inside = grid.inside(w, h)
    sx = np.where(inside, grid.src_x, 0.0)
    sy = np.where(inside, grid.src_y, 0.0)
    if nearest:
        out = img[np.clip(np.rint(sy).astype(int), 0, h - 1), np.clip(np.rint(sx).astype(int), 0, w - 1)]
    else:
        x0 = np.clip(np.floor(sx).astype(int), 0, w - 2)
        y0 = np.clip(np.floor(sy).astype(int), 0, h - 2)
        ax, ay = sx - x0, sy - y0
        if img.ndim == 3:
            ax, ay = ax[..., None], ay[..., None]
        f = img.astype(float)
        out = ((1 - ay) * ((1 - ax) * f[y0, x0] + ax * f[y0, x0 + 1])
               + ay * ((1 - ax) * f[y0 + 1, x0] + ax * f[y0 + 1, x0 + 1]))
        out = out.astype(img.dtype) if np.issubdtype(img.dtype, np.floating) else \
            np.clip(np.rint(out), np.iinfo(img.dtype).min, np.iinfo(img.dtype).max).astype(img.dtype)
    out = np.array(out)
    out[~inside] = fill
    return out


def photo_pano(name: str, rgb: np.ndarray, labels: np.ndarray, cam: PinholeCamera):
    """``(pano, grid)``: the photo and its label map on the Pano grid (labels -1 outside)."""
    grid = pano_grid(cam)
    pano = fd.Pano(name, cam.lat, cam.lon, reproject(rgb, grid),
                   reproject(np.asarray(labels, np.int16), grid, nearest=True, fill=-1),
                   grid.frame_heading, cam.f_px, cam.pitch_deg)
    return pano, grid


# --------------------------------------------------------------------------- inputs


def load_annotation(path) -> dict:
    """The hand-label file (``tools/photo_label``): ``labels`` with ``x``, ``y``, ``lat``, ``lon``."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Dem:
    """Bilinear samples of a lon/lat terrain GeoTIFF (EPSG:4326)."""

    def __init__(self, path):
        import rasterio

        with rasterio.open(path) as r:
            self.z = r.read(1).astype(float)
            self.t = r.transform
            nod = r.nodata
        if nod is not None:
            self.z[self.z == nod] = np.nan
        self.path = str(path)

    def __call__(self, lat, lon):
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        inv = ~self.t
        c, r = inv * (lon, lat)
        c, r = np.asarray(c) - 0.5, np.asarray(r) - 0.5      # from cell centres
        h, w = self.z.shape
        c0 = np.clip(np.floor(c).astype(int), 0, w - 2)
        r0 = np.clip(np.floor(r).astype(int), 0, h - 2)
        ac, ar = np.clip(c - c0, 0, 1), np.clip(r - r0, 0, 1)
        z = self.z
        out = ((1 - ar) * ((1 - ac) * z[r0, c0] + ac * z[r0, c0 + 1])
               + ar * ((1 - ac) * z[r0 + 1, c0] + ac * z[r0 + 1, c0 + 1]))
        outside = (c < -0.5) | (r < -0.5) | (c > w - 0.5) | (r > h - 0.5)
        return np.where(outside, np.nan, out)


def footprint_bases(footprints, dem, stat: str = "min") -> np.ndarray:
    """Terrain under each footprint (m): ``stat`` ("min" or "median") of the terrain model at
    the ring's vertices and centroid. The minimum is the street at the downhill wall, the
    foot a camera above the slope sees."""
    out = np.full(len(footprints), np.nan)
    for i, fp in enumerate(footprints):
        ring = np.asarray(fp.ring, float)
        pts = np.vstack([ring, ring[:-1].mean(axis=0, keepdims=True)])
        z = dem(pts[:, 1], pts[:, 0])
        z = z[np.isfinite(z)]
        if z.size:
            out[i] = float(z.min() if stat == "min" else np.median(z))
    return out


# --------------------------------------------------------------------------- camera from labels


def solve_from_rows(cam0: PinholeCamera, labels: list[dict], label_elev_m,
                    fit=("heading_deg", "pitch_deg", "roll_deg", "f_px", "elev_m"),
                    loss: str = "soft_l1", f_scale_px: float = 4.0):
    """Refine the camera from the labels' full pixel positions (column and row), each label at
    ``label_elev_m`` (its terrain height, plus any known structure height). ``fit`` names the
    free :class:`PinholeCamera` fields; add ``"lat"``, ``"lon"`` to move the camera too (the
    column-only solve assumes a level camera, which biases its position when the photo looks
    down). Returns ``(camera, residuals (n, 2) px)``."""
    from scipy.optimize import least_squares

    lat = np.array([float(lb["lat"]) for lb in labels])
    lon = np.array([float(lb["lon"]) for lb in labels])
    xy = np.array([[float(lb["x"]), float(lb["y"])] for lb in labels])
    z = np.asarray(label_elev_m, float)
    names = list(fit)

    def cam_of(p):
        return replace(cam0, **{k: float(v) for k, v in zip(names, p, strict=True)})

    def resid(p):
        x, y = cam_of(p).project_ll(lat, lon, z)
        return np.concatenate([x - xy[:, 0], y - xy[:, 1]])

    p0 = np.array([getattr(cam0, k) for k in names], float)
    # parameter scales: ~1e-4 deg of lat/lon is ~10 m, the size of 1 deg or 1 m elsewhere
    scale = np.array([1e-4 if k in ("lat", "lon") else 1.0 for k in names])
    r = least_squares(resid, p0, loss=loss, f_scale=f_scale_px, x_scale=scale)
    cam = cam_of(r.x)
    x, y = cam.project_ll(lat, lon, z)
    return cam, np.column_stack([x - xy[:, 0], y - xy[:, 1]])


# --------------------------------------------------------------------------- roof fit


def refine_from_bases(rgb, labels_photo, cam: PinholeCamera, footprints, bases_m, depth_photo,
                      instances_fn=None, iters: int = 4, tol_m: float = 0.5, **fit_kw):
    """Iterate camera elevation and pitch from building feet: build the pano at the current
    camera, run ``roof_fit.check_pose_from_bases`` with per-footprint bases (its free fit gives
    a height and a pitch fix), move the camera, repeat until the height moves under ``tol_m``.
    ``instances_fn(pano) -> instances or None`` (MobileSAM is slow: pass None to skip it here).
    Returns ``(camera, history)``; ``history`` lists each round's check."""
    from ._pano import roof_fit

    hist = []
    for _ in range(iters):
        pano, grid = photo_pano("photo", rgb, labels_photo, cam)
        depth = None if depth_photo is None else reproject(depth_photo, grid, nearest=True)
        inst = None if instances_fn is None else instances_fn(pano)
        z_ref = float(np.nanmedian(bases_m))
        pose = fd.PanoPose(0.0, cam.elev_m - z_ref, 0.0, 0.0, pano.width)
        cam_h = cam.elev_m - np.asarray(bases_m, float)
        chk = roof_fit.check_pose_from_bases(pano, pose, footprints, depth=depth, instances=inst,
                                             cam_h=cam_h, **fit_kw)
        hist.append(chk)
        if not math.isfinite(chk.h_bases_free_m):
            break
        new_elev = z_ref + chk.h_bases_free_m
        moved = abs(new_elev - cam.elev_m)
        cam = replace(cam, elev_m=new_elev, pitch_deg=cam.pitch_deg + chk.pitch_fix_deg)
        if moved < tol_m and abs(chk.pitch_fix_deg) < 0.05:
            break
    return cam, hist


#: ``measure(depth_break=True)``: roof-fit settings that cut a roof from the next house uphill.
#: A 5 % local disparity step (the roof fit's default 15 % misses the 3-7 % jump from a roof
#: to the wall behind it at 300-500 m), and a metric cut at the footprint's far side + 5 m from
#: Depth Anything calibrated on the photo's own feet (``roof_fit.calibrate_depth``).
DEPTH_BREAK_KW = {"stop_ratio": 0.95, "depth_calib": "auto", "far_rel": 0.0, "far_abs_m": 5.0}


def measure(rgb, labels_photo, cam: PinholeCamera, footprints, bases_m, depth_photo=None,
            instances=None, depth_break: bool = False, **fit_kw):
    """Roof heights of every footprint in view (``roof_fit.fit_roof_heights``) at ``cam``, each
    footprint on its own base. ``instances``: MobileSAM map on the pano grid (or None).
    ``depth_break``: stop each column's run at a depth break (:data:`DEPTH_BREAK_KW`, which
    ``fit_kw`` overrides); needs ``depth_photo``. Returns ``(measured, pano, grid, pose, cam_h)``."""
    from ._pano import roof_fit

    if depth_break:
        fit_kw = {**DEPTH_BREAK_KW, **fit_kw}

    pano, grid = photo_pano("photo", rgb, labels_photo, cam)
    depth = None if depth_photo is None else reproject(depth_photo, grid, nearest=True)
    z_ref = float(np.nanmedian(bases_m))
    pose = fd.PanoPose(0.0, cam.elev_m - z_ref, 0.0, 0.0, pano.width)
    cam_h = cam.elev_m - np.asarray(bases_m, float)
    ms = roof_fit.fit_roof_heights(pano, pose, footprints, depth=depth, instances=instances,
                                   cam_h=cam_h, **fit_kw)
    return ms, pano, grid, pose, cam_h


# --------------------------------------------------------------------------- cameras as dicts
# (moved from tools/photo_label/register.py, F-UNIFY Phase 3)

def cam_from_dict(c: dict, w: int | None = None, h: int | None = None) -> PinholeCamera:
    """A PinholeCamera from a dict with `elev_m`/`elev`, `heading_deg`/`heading`, `pitch_deg`/
    `pitch`, `roll_deg`/`roll`, `f_px`/`f` (auto_pose spells the short forms)."""
    def g(*keys, default=None):
        for k in keys:
            if c.get(k) is not None:
                return float(c[k])
        if default is None:
            raise KeyError(keys[0])
        return default
    return PinholeCamera(g("lat"), g("lon"), g("elev_m", "elev"), g("heading_deg", "heading"),
                         g("pitch_deg", "pitch", default=0.0), g("roll_deg", "roll", default=0.0),
                         g("f_px", "f"), int(w or c["width"]), int(h or c["height"]))


def cam_to_dict(cam: PinholeCamera) -> dict:
    return {"lat": cam.lat, "lon": cam.lon, "elev_m": cam.elev_m, "heading_deg": cam.heading_deg,
            "pitch_deg": cam.pitch_deg, "roll_deg": cam.roll_deg, "f_px": cam.f_px,
            "hfov_deg": math.degrees(2 * math.atan(cam.width / 2 / cam.f_px)),
            "width": cam.width, "height": cam.height}


# --------------------------------------------------------------------------- OSM buildings in view
# (moved from tools/photo_label/terrain_solve.py, F-UNIFY Phase 3). `project_buildings`
# rasterises OSM footprints as prisms (base at the lowest terrain under the ring, top at base +
# height) with a solved camera into an id buffer and returns only the visible outlines.

DEFAULT_BUILDING_H = 9.0
LEVEL_H = 3.0
PROJECT_RADIUS_M = 2000.0
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _num(v) -> float | None:
    m = _NUM.search(str(v)) if v is not None else None
    return float(m.group().replace(",", ".")) if m else None


def building_height(props: dict) -> float:
    """OSM `height`, else `building:levels` x 3 m, else 9 m."""
    h = _num(props.get("height"))
    if h is not None and h > 0:
        return h
    lv = _num(props.get("levels"))
    if lv is not None and lv > 0:
        return lv * LEVEL_H
    return DEFAULT_BUILDING_H


NEAR_M = 1.0            # near plane of the visibility raster, m
MAX_VISIBLE = 2000      # nearest footprints in view that are rasterised (150 dropped the far hillside)
MIN_VISIBLE_PX = 30     # a building with fewer visible pixels is dropped
MIN_DIST_M = 30.0       # footprints closer than this to the camera (the one it stands on) are skipped
_CLAMP = 30000.0


def _clip_near(poly: np.ndarray) -> np.ndarray:
    """Clip a polygon of camera-space points (n, 3: right, up, forward) to forward >= NEAR_M."""
    out = []
    for i in range(len(poly)):
        a, b = poly[i - 1], poly[i]
        ia, ib = a[2] >= NEAR_M, b[2] >= NEAR_M
        if ia != ib:
            t = (NEAR_M - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
        if ib:
            out.append(b)
    return np.array(out)


def _prism_faces(pts_c: np.ndarray, zb: float, zt: float, e: np.ndarray, n: np.ndarray):
    """Visible-side faces of a prism: (kind, camera-space polygon) with kind 1 roof, 2 wall.
    `pts_c` (m, 2) ring in metres east/north of the camera, `zb`/`zt` base/top relative to the
    camera. Back-facing walls are skipped, the roof only when the camera is above it."""
    m = len(pts_c)
    area = 0.5 * float(np.sum(pts_c[:, 0] * np.roll(pts_c[:, 1], -1) - np.roll(pts_c[:, 0], -1) * pts_c[:, 1]))
    ring = pts_c if area >= 0 else pts_c[::-1]                      # counter-clockwise
    faces = []
    for i in range(m):
        a, b = ring[i], ring[(i + 1) % m]
        nx, ny = b[1] - a[1], a[0] - b[0]                           # outward normal
        if -(a[0] * nx + a[1] * ny) <= 0:                           # camera at the origin
            continue
        faces.append((2, np.array([[a[0], a[1], zb], [b[0], b[1], zb], [b[0], b[1], zt], [a[0], a[1], zt]])))
    if zt < 0:
        faces.append((1, np.column_stack([ring, np.full(m, zt)])))
    return faces


def project_buildings(dem, fc: dict, cam: PinholeCamera, radius_m: float = PROJECT_RADIUS_M,
                      limit: int = MAX_VISIBLE) -> dict:
    """What the camera would see of the OSM footprints (prisms: base at the lowest terrain under
    the ring, top at base + height). The `limit` nearest footprints in view are rasterised as
    faces, far to near, into an id buffer (near plane `NEAR_M`; faces crossing it are clipped).
    Returns {"buildings": [...], "ids": (h, w) uint16 array, index+1 of the building (0: none)}.
    Each building: id, name, height_m, dist_m, visible_px, contours (visible outline rings, px),
    roof (visible roof rings, px). Footprints within `MIN_DIST_M` of the camera and buildings under
    `MIN_VISIBLE_PX` are dropped; the list is far
    to near and the array indexes it."""
    import cv2
    from shapely.geometry import Point, Polygon

    w, h = int(cam.width), int(cam.height)
    kx = M_PER_DEG_LAT * math.cos(math.radians(cam.lat))
    right, up, fwd = cam.axes()
    cand = []
    for f in fc["features"]:
        ring = np.asarray(f["geometry"]["coordinates"][0], float)
        if len(ring) < 4:
            continue
        pts = ring[:-1]
        c = pts.mean(axis=0)
        d = math.hypot((c[0] - cam.lon) * kx, (c[1] - cam.lat) * M_PER_DEG_LAT)
        if d > radius_m:
            continue
        # The photographer's own terrace or rooftop would fill the frame from a few metres away.
        loc = np.column_stack([(pts[:, 0] - cam.lon) * kx, (pts[:, 1] - cam.lat) * M_PER_DEG_LAT])
        reach = float(np.hypot(*(loc - loc.mean(axis=0)).T).max())  # cheap test first
        if d - reach >= MIN_DIST_M or Polygon(loc).buffer(0).distance(Point(0.0, 0.0)) >= MIN_DIST_M:
            cand.append((d, pts, f["properties"]))
    empty = {"buildings": [], "ids": np.zeros((h, w), np.uint16)}
    if not cand:
        return empty
    cut = np.cumsum([0] + [len(c[1]) for c in cand])
    allp = np.vstack([c[1] for c in cand])
    z = np.asarray(dem(allp[:, 1], allp[:, 0]), float)
    bx, by = cam.project_ll(allp[:, 1], allp[:, 0], z)               # cheap in-frame pre-cull
    keep = []
    for k, (d, _pts, _pr) in enumerate(cand):
        zk = z[cut[k]:cut[k + 1]]
        if not np.isfinite(zk).any():
            continue
        sx, sy = bx[cut[k]:cut[k + 1]], by[cut[k]:cut[k + 1]]
        ok = np.isfinite(sx)
        if not ok.any():
            continue
        if ok.all() and (sx.max() < 0 or sx.min() > w or sy.max() < 0 or sy.min() > h):
            continue
        keep.append((d, k))
    keep.sort()
    keep = keep[:limit]
    ids = np.zeros((h, w), np.int32)
    kind = np.zeros((h, w), np.uint8)
    faces = []                                                      # (mean depth, building slot, kind, poly px)
    for slot, (_d, k) in enumerate(keep):
        _, pts, pr = cand[k]
        zk = z[cut[k]:cut[k + 1]]
        zb = float(zk[np.isfinite(zk)].min()) - cam.elev_m
        zt = zb + building_height(pr)
        pc = np.column_stack([(pts[:, 0] - cam.lon) * kx, (pts[:, 1] - cam.lat) * M_PER_DEG_LAT])
        for kd, poly in _prism_faces(pc, zb, zt, None, None):
            cc = np.column_stack([poly[:, 0] * right[0] + poly[:, 1] * right[1] + poly[:, 2] * right[2],
                                  poly[:, 0] * up[0] + poly[:, 1] * up[1] + poly[:, 2] * up[2],
                                  poly[:, 0] * fwd[0] + poly[:, 1] * fwd[1] + poly[:, 2] * fwd[2]])
            cc = _clip_near(cc)
            if len(cc) < 3:
                continue
            px = np.column_stack([w / 2 + cam.f_px * cc[:, 0] / cc[:, 2], h / 2 - cam.f_px * cc[:, 1] / cc[:, 2]])
            faces.append((float(cc[:, 2].mean()), slot, kd, np.clip(px, -_CLAMP, _CLAMP)))
    faces.sort(key=lambda t: -t[0])
    for _, slot, kd, px in faces:
        q = np.round(px * 16).astype(np.int32)                       # shift=4: sub-pixel edges
        cv2.fillPoly(ids, [q], int(slot + 1), lineType=cv2.LINE_8, shift=4)
        cv2.fillPoly(kind, [q], int(kd), lineType=cv2.LINE_8, shift=4)
    counts = np.bincount(ids.ravel(), minlength=len(keep) + 1)
    from scipy import ndimage
    boxes = ndimage.find_objects(ids, max_label=len(keep))

    def rings(mask, off):
        cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        res = []
        for c in cs:
            c = cv2.approxPolyDP(c, 1.0, True)[:, 0, :] + off
            if len(c) >= 3:
                res.append(c.tolist())
        return res

    out, remap = [], np.zeros(len(keep) + 1, np.uint16)
    for slot in range(len(keep) - 1, -1, -1):                        # far to near
        if counts[slot + 1] < MIN_VISIBLE_PX or boxes[slot] is None:
            continue
        d, k = keep[slot]
        pr = cand[k][2]
        sl = boxes[slot]
        sub, sk = ids[sl] == slot + 1, kind[sl]
        off = np.array([sl[1].start, sl[0].start])
        remap[slot + 1] = len(out) + 1
        out.append({"id": pr.get("id"), "name": pr.get("name"),
                    "height_m": float(building_height(pr)), "dist_m": float(d),
                    "visible_px": int(counts[slot + 1]),
                    "contours": rings(sub, off), "roof": rings(sub & (sk == 1), off)})
    return {"buildings": out, "ids": remap[ids]}
