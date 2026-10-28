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
