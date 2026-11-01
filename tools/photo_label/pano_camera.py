"""Cylindrical camera of a pano-pipeline strip (`runs/<region>/03_seeds/seed_<id>/pano.png`).

Columns are equal steps of heading, rows are `f_px * tan(elevation)` about a horizon row: the
Street View strip the pano pipeline stitches from its 12 frames. `PanoCamera` has the same
`project_ll` / `project_dirs` / `project` / `backproject` interface as
`city2stl.skyline.photo_view.PinholeCamera` (a pixel is its own coordinate: centre = size / 2),
so `terrain_solve.project_buildings` and `terrain_solve.solve_terrain` take either.

Fitted model (template match of the 12 frames on each strip, +-0.15 deg):
  heading = (col0_heading_deg + col * deg_per_col) mod 360, `col0_heading_deg` 180 (north is
  mid-strip). `pano.json`'s own `col0_heading_deg` is of the un-rolled strip and is ignored.
  elevation = atan((r0 - row) / f_px), r0 = H / 2 + f_px * tan(pitch_deg).
`fit_col0_heading` re-fits the column-0 heading from the frames so it is checked, not assumed.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

M_PER_DEG_LAT = 111_320.0
COL0_HEADING_DEG = 180.0        # fitted: north mid-strip (see module docstring)
MAX_ELEV_DEG = 85.0             # rows above/below this elevation are not on the strip
WEAK_BRANCHES = ("overhead",)   # pose.json `pose_branch` values whose position is not trusted


def _wrap(a):
    """Angle(s) in degrees wrapped to [-180, 180)."""
    return (np.asarray(a, float) + 180.0) % 360.0 - 180.0


@dataclass(frozen=True)
class PanoCamera:
    """Cylindrical camera at ``(lat, lon, elev_m)`` (orthometric metres). ``pitch_deg`` (up
    positive) puts the horizon at row ``H / 2 + f_px * tan(pitch_deg)``; ``col0_heading_deg`` is
    the bearing of column 0 and ``deg_per_col`` the heading step per column (default 360 / width)."""
    lat: float
    lon: float
    elev_m: float
    pitch_deg: float
    f_px: float
    width: int
    height: int
    deg_per_col: float | None = None
    col0_heading_deg: float = COL0_HEADING_DEG
    weak: bool = False

    cylindrical = True          # duck-type flag read by terrain_solve

    def __post_init__(self):
        if self.deg_per_col is None:
            object.__setattr__(self, "deg_per_col", 360.0 / self.width)

    @property
    def heading_deg(self) -> float:
        """Bearing of the strip's centre column (the `PinholeCamera.heading_deg` analogue)."""
        return (self.col0_heading_deg + 0.5 * self.width * self.deg_per_col) % 360.0

    @property
    def roll_deg(self) -> float:
        return 0.0

    @property
    def horizon_row(self) -> float:
        """Row of elevation 0."""
        return self.height / 2.0 + self.f_px * math.tan(math.radians(self.pitch_deg))

    @classmethod
    def from_centre(cls, lat, lon, elev_m, heading_deg, pitch_deg, f_px, width, height, deg_per_col=None):
        """A camera whose centre column looks at ``heading_deg`` (what the terrain solver fits)."""
        dpc = 360.0 / width if deg_per_col is None else deg_per_col
        return cls(lat, lon, elev_m, pitch_deg, f_px, int(width), int(height), dpc,
                   (heading_deg - 0.5 * width * dpc) % 360.0)

    def to_dict(self) -> dict:
        """The camera as the page and `server._camera` pass it around."""
        return {"projection": "pano_cylindrical", "lat": self.lat, "lon": self.lon, "elev_m": self.elev_m,
                "heading_deg": self.heading_deg, "pitch_deg": self.pitch_deg, "roll_deg": 0.0,
                "f_px": self.f_px, "width": self.width, "height": self.height,
                "deg_per_col": self.deg_per_col, "col0_heading_deg": self.col0_heading_deg,
                "hfov_deg": self.width * self.deg_per_col, "weak": self.weak}

    @classmethod
    def from_dict(cls, c: dict, w: int | None = None, h: int | None = None) -> PanoCamera:
        """Inverse of `to_dict`; `heading_deg` is used when `col0_heading_deg` is absent."""
        w, h = int(w or c["width"]), int(h or c["height"])
        dpc = float(c["deg_per_col"]) if c.get("deg_per_col") else 360.0 / w
        if c.get("col0_heading_deg") is not None:
            col0 = float(c["col0_heading_deg"])
        else:
            col0 = (float(c["heading_deg"]) - 0.5 * w * dpc) % 360.0
        return cls(float(c["lat"]), float(c["lon"]), float(c["elev_m"]), float(c.get("pitch_deg") or 0.0),
                   float(c["f_px"]), w, h, dpc, col0, bool(c.get("weak")))

    def with_col0(self, col0_heading_deg: float) -> PanoCamera:
        return replace(self, col0_heading_deg=float(col0_heading_deg) % 360.0)

    # -- projection
    def project_dirs(self, d):
        """Pixel ``(x, y)`` of directions ``d`` (..., 3) in (east, north, up); NaN at the poles."""
        d = np.asarray(d, float)
        horiz = np.hypot(d[..., 0], d[..., 1])
        head = np.degrees(np.arctan2(d[..., 0], d[..., 1]))
        elev = np.degrees(np.arctan2(d[..., 2], horiz))
        x = ((head - self.col0_heading_deg) % 360.0) / self.deg_per_col
        with np.errstate(invalid="ignore"):
            y = self.horizon_row - self.f_px * np.tan(np.radians(elev))
        bad = (horiz <= 1e-9) | (np.abs(elev) > MAX_ELEV_DEG)
        return np.where(bad, np.nan, x), np.where(bad, np.nan, y)

    def project_ll(self, lat, lon, elev_m):
        """Pixel ``(x, y)`` of points given by lat/lon and absolute elevation."""
        kx = M_PER_DEG_LAT * math.cos(math.radians(self.lat))
        d = np.stack([(np.asarray(lon, float) - self.lon) * kx,
                      (np.asarray(lat, float) - self.lat) * M_PER_DEG_LAT,
                      np.asarray(elev_m, float) - self.elev_m], axis=-1)
        return self.project_dirs(d)

    project = project_ll

    def backproject(self, x, y):
        """Unit directions (..., 3) in (east, north, up) of pixels ``(x, y)`` (inverse of `project_dirs`)."""
        head = np.radians(self.col0_heading_deg + np.asarray(x, float) * self.deg_per_col)
        elev = np.arctan((self.horizon_row - np.asarray(y, float)) / self.f_px)
        return np.stack(np.broadcast_arrays(np.sin(head) * np.cos(elev), np.cos(head) * np.cos(elev),
                                            np.sin(elev)), axis=-1)

    def heading_of(self, x):
        """Bearing (deg, 0-360) of column(s) ``x``."""
        return (self.col0_heading_deg + np.asarray(x, float) * self.deg_per_col) % 360.0

    def column_of(self, heading_deg):
        """Column (float, 0 <= x < width) of bearing(s) ``heading_deg``."""
        return ((np.asarray(heading_deg, float) - self.col0_heading_deg) % 360.0) / self.deg_per_col


# --------------------------------------------------------------------------- seed folders


def load_seed_camera(seed_dir, col0_heading_deg: float = COL0_HEADING_DEG,
                     elev_m: float | None = None) -> PanoCamera:
    """The camera of `<seed_dir>/pano.png` from `pano.json` (f_px, pitch_deg, deg_per_col, size) and
    `pose.json` (lat, lon, `cam_elev_m`, else `ground_m + cam_height_m`, else the ``elev_m`` argument). ``weak`` is set when
    `pose_branch` is in `WEAK_BRANCHES` (seed 6, `overhead`). Raises KeyError / OSError when a file or
    key is missing."""
    d = Path(seed_dir)
    pj = json.loads((d / "pano.json").read_text(encoding="utf-8"))
    pose = json.loads((d / "pose.json").read_text(encoding="utf-8"))
    if pose.get("cam_elev_m") is not None:
        elev = float(pose["cam_elev_m"])
    elif elev_m is not None and (pose.get("ground_m") is None or pose.get("cam_height_m") is None):
        elev = float(elev_m)
    else:
        elev = float(pose["ground_m"]) + float(pose["cam_height_m"])
    return PanoCamera(float(pose["lat"]), float(pose["lon"]), elev, float(pj.get("pitch_deg") or 0.0),
                      float(pj["f_px"]), int(pj["width"]), int(pj["height"]), float(pj["deg_per_col"]),
                      float(col0_heading_deg), pose.get("pose_branch") in WEAK_BRANCHES)


def _frame_template(frame_gray, view: dict, cam: PanoCamera, half_deg: float):
    """The frame resampled onto the strip's cylinder over ``+-half_deg`` of heading, plus the rows
    where it lies fully inside the frame: (template, row0, row1, n_cols)."""
    import cv2

    from city2stl.skyline import photo_view as pv

    fh, fw = frame_gray.shape
    fcam = pv.PinholeCamera(0.0, 0.0, 0.0, float(view["heading_deg"]), float(view.get("pitch_deg") or 0.0),
                            0.0, f_px_of_fov(fw, view["fov_deg"]), fw, fh)
    n = int(round(half_deg / cam.deg_per_col))
    cols = np.arange(-n, n + 1)
    head = float(view["heading_deg"]) + cols * cam.deg_per_col
    rows = np.arange(cam.height)
    elev = np.degrees(np.arctan((cam.horizon_row - rows) / cam.f_px))
    sx, sy = fcam.project_dirs(pv.dirs_of(head[None, :], elev[:, None]))
    ok = np.isfinite(sx) & (sx >= 0) & (sx <= fw - 1) & (sy >= 0) & (sy <= fh - 1)
    full = np.where(ok.all(axis=1))[0]
    if len(full) < 40:
        return None
    r0, r1 = int(full[0]), int(full[-1]) + 1
    tpl = cv2.remap(frame_gray.astype(np.float32), np.nan_to_num(sx).astype(np.float32),
                    np.nan_to_num(sy).astype(np.float32), cv2.INTER_LINEAR)
    return tpl[r0:r1], r0, r1, n


def fit_col0_heading(seed_dir, cam: PanoCamera | None = None, half_deg: float = 14.0,
                     search_deg: float = 25.0, inlier_deg: float = 1.0) -> dict:
    """Re-fit the column-0 heading of `<seed_dir>/pano.png` from its frames: each `views.json` frame is
    resampled onto the strip's cylinder (heading `heading_deg`, the strip's pitch / f for the rows) and
    template-matched along the columns (zero-mean normalised correlation, parabolic sub-pixel peak,
    wrap-around) within ``search_deg`` of the camera's current column-0 heading (bland sky / water
    frames match anywhere on the strip, so an open search returns outliers). Each frame gives ``col0 = heading_deg - column_of_centre * deg_per_col``.
    Returns {col0_heading_deg (median of the frames within ``inlier_deg`` of the median), spread_deg
    (max |inlier - median|), n_inliers, n_frames, per_frame: [{file, col0_heading_deg, score, inlier}]}; needs OpenCV and the frame PNGs."""
    import cv2

    d = Path(seed_dir)
    cam = cam or load_seed_camera(d)
    views = json.loads((d / "views.json").read_text(encoding="utf-8"))
    strip = cv2.imread(str(d / "pano.png"), cv2.IMREAD_GRAYSCALE)
    W = strip.shape[1]
    out = []
    for v in views:
        fr = cv2.imread(str(d / Path(str(v["file"])).name), cv2.IMREAD_GRAYSCALE)
        if fr is None:
            continue
        got = _frame_template(fr, v, cam, half_deg)
        if got is None:
            continue
        tpl, r0, r1, n = got
        band = strip[r0:r1].astype(np.float32)
        wide = np.concatenate([band, band[:, :2 * n + 1]], axis=1)           # wrap-around
        res = cv2.matchTemplate(wide, tpl.astype(np.float32), cv2.TM_CCOEFF_NORMED)[0][:W]
        nominal = (cam.column_of(float(v["heading_deg"])) - n) % W           # template left edge if col0 is right
        reach = int(round(search_deg / cam.deg_per_col))
        idx = (int(round(nominal)) + np.arange(-reach, reach + 1)) % W
        k = int(idx[np.argmax(res[idx])])
        a, b, c = res[(k - 1) % W], res[k], res[(k + 1) % W]
        den = a - 2 * b + c
        sub = 0.5 * (a - c) / den if den < 0 else 0.0
        centre_col = (k + sub + n) % W                                       # column of the frame's centre heading
        col0 = (float(v["heading_deg"]) - centre_col * cam.deg_per_col) % 360.0
        out.append({"file": v["file"], "col0_heading_deg": float(col0), "score": float(b)})
    if not out:
        raise ValueError(f"no frame of {d} could be matched")
    vals = np.array([o["col0_heading_deg"] for o in out])
    med = float(vals[0] + np.median(_wrap(vals - vals[0]))) % 360.0
    for _ in range(2):                           # median of the frames that agree (bland frames match anywhere)
        inl = np.abs(_wrap(vals - med)) <= inlier_deg
        if inl.sum() < 3:
            break
        med = float(med + np.median(_wrap(vals[inl] - med))) % 360.0
    inl = np.abs(_wrap(vals - med)) <= inlier_deg
    for o, ok in zip(out, inl, strict=True):
        o["inlier"] = bool(ok)
    spread = float(np.abs(_wrap(vals[inl] - med)).max()) if inl.any() else float("nan")
    return {"col0_heading_deg": med, "spread_deg": spread, "n_inliers": int(inl.sum()),
            "n_frames": len(out), "per_frame": out}


def f_px_of_fov(width: float, fov_deg: float) -> float:
    """Pinhole focal length in pixels of a frame ``width`` px wide with horizontal field of view ``fov_deg``."""
    return float(width) / 2 / math.tan(math.radians(float(fov_deg)) / 2)


def pose_elevation(pose: dict) -> tuple[float | None, bool]:
    """(camera elevation, fitted?) from a seed's `pose.json`: `cam_elev_m` (fitted), else
    `ground_m + cam_height_m`, else (None, False) and the caller falls back to the DEM."""
    if pose.get("cam_elev_m") is not None:
        return float(pose["cam_elev_m"]), True
    if pose.get("ground_m") is not None and pose.get("cam_height_m") is not None:
        return float(pose["ground_m"]) + float(pose["cam_height_m"]), False
    return None, False


def frame_pose_parts(view: dict, pose: dict, width: float) -> dict:
    """Heading, pitch, roll and focal length of one seed frame: `views.json` heading / pitch / fov
    (``width`` px wide), roll from `pose.json` `horizon_tilt_deg`."""
    return {"heading_deg": float(view["heading_deg"]), "pitch_deg": float(view.get("pitch_deg") or 0.0),
            "roll_deg": float(pose.get("horizon_tilt_deg") or 0.0), "f_px": f_px_of_fov(width, view["fov_deg"])}


def seed_frame_cameras(seed_dir, cam: PanoCamera, elev_m: float | None = None) -> list[dict]:
    """The pinhole cameras of a seed's frames (`views.json`: heading, pitch, fov, size; position and
    elevation of ``cam``) as camera dicts for the page's Google 3D renders (auto register on the strip)."""
    views = json.loads((Path(seed_dir) / "views.json").read_text(encoding="utf-8"))
    out = []
    for v in views:
        w, h = int(v.get("width") or v.get("w")), int(v.get("height") or v.get("h"))
        fov = float(v["fov_deg"])
        f = f_px_of_fov(w, fov)
        out.append({"file": v["file"], "lat": cam.lat, "lon": cam.lon, "elev_m": cam.elev_m if elev_m is None else elev_m,
                    "heading_deg": float(v["heading_deg"]), "pitch_deg": float(v.get("pitch_deg") or 0.0),
                    "roll_deg": 0.0, "f_px": f, "hfov_deg": fov, "width": w, "height": h})
    return out


def pinhole_dirs(cam, x, y):
    """Unit directions (east, north, up) of pixels ``(x, y)`` of a `PinholeCamera` (inverse of its `project_dirs`)."""
    right, up, fwd = cam.axes()
    x, y = np.asarray(x, float), np.asarray(y, float)
    d = (fwd * cam.f_px + right * (x - cam.width / 2.0)[..., None] - up * (y - cam.height / 2.0)[..., None])
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def strip_to_pinhole(strip, cam: PanoCamera, pin):
    """The strip image (H x W [x C]) resampled onto the pinhole camera ``pin`` (same position): what that
    frame would show. Pixels outside the strip's rows are black."""
    import cv2

    xs, ys = np.meshgrid(np.arange(pin.width, dtype=float), np.arange(pin.height, dtype=float))
    px, py = cam.project_dirs(pinhole_dirs(pin, xs, ys))
    W = strip.shape[1]
    wide = np.concatenate([strip, strip, strip], axis=1)                      # seam: wrap in x
    mx = np.nan_to_num(px, nan=-1e4) + W
    my = np.nan_to_num(py, nan=-1e4)
    return cv2.remap(wide, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def pinhole_to_strip(cam: PanoCamera, pin, x, y):
    """Strip pixels ``(col, row)`` of pinhole pixels ``(x, y)`` (the inverse mapping of `strip_to_pinhole`)."""
    return cam.project_dirs(pinhole_dirs(pin, x, y))
