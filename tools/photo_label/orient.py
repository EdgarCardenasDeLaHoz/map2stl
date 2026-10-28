"""Surface orientation from a depth grid: ground / flat roof / pitched roof / wall, per grid cell.

Serves POST /api/depth/orient of server.py. Input is a forward-depth grid ``z`` (metres along the
camera's forward axis, NaN = no value) and a camera dict (lat, lon, elev_m, heading_deg, pitch_deg,
roll_deg, f_px, width, height). Everything is pure numpy (PIL only for :func:`rasterize`).

Pipeline: :func:`backproject` turns the grid into camera-relative east/north/up points (metres),
:func:`normals` takes central differences on lightly smoothed points (NaN at occlusion edges and
grid borders), :func:`classify` labels each normal by its tilt from horizontal, and
:func:`building_summary` aggregates the labels over per-building cell masks from :func:`rasterize`.

Conventions: grid arrays are (gh, gw) row-major; cell (i, j) is centred on photo pixel
((j + .5) * W / gw, (i + .5) * H / gh). Points are ENU offsets from the camera. Azimuth is the
compass direction the surface faces (0 = N, 90 = E): a wall seen from the south facing the camera
has az 180. Class codes: 0 none, 1 ground, 2 flat roof, 3 pitched roof, 4 wall.
"""
from __future__ import annotations

import math

import numpy as np

FLAT_MAX_DEG = 12.0       # tilt below this is flat (ground or flat roof)
WALL_MIN_DEG = 70.0       # tilt above this is a wall; in between is a pitched roof
GROUND_MAX_ABOVE_M = 2.5  # a flat cell this far above the DEM or less is ground
MIN_ROOF_CELLS = 5        # a building needs this many roof cells to be called flat/pitched
AZ_BINS = 16
AZ_BIN_FRACTION = 0.15    # azimuth bins holding at least this share of wall cells are reported

NONE, GROUND, FLAT, PITCHED, WALL = 0, 1, 2, 3, 4


def camera_axes(cam: dict):
    """(fwd, right, up) unit vectors in ENU, as index.html pinholeAxes."""
    h, p = math.radians(cam["heading_deg"]), math.radians(cam["pitch_deg"])
    r = math.radians(cam.get("roll_deg") or 0.0)
    fwd = np.array([math.sin(h) * math.cos(p), math.cos(h) * math.cos(p), math.sin(p)])
    r0 = np.array([math.cos(h), -math.sin(h), 0.0])
    u0 = np.cross(r0, fwd)
    right = math.cos(r) * r0 + math.sin(r) * u0
    up = -math.sin(r) * r0 + math.cos(r) * u0
    return fwd, right, up


def backproject(z, cam: dict):
    """(gh, gw, 3) ENU offsets from the camera of each cell centre at forward depth ``z``;
    NaN where z is NaN or <= 0."""
    z = np.asarray(z, dtype=np.float64)
    gh, gw = z.shape
    fwd, right, up = camera_axes(cam)
    W, H, f = float(cam["width"]), float(cam["height"]), float(cam["f_px"])
    u = (np.arange(gw) + 0.5) * W / gw
    v = (np.arange(gh) + 0.5) * H / gh
    a = (u - W / 2) / f
    b = (v - H / 2) / f
    d = (fwd[None, None, :] + a[None, :, None] * right[None, None, :]
         - b[:, None, None] * up[None, None, :])
    zz = np.where(np.isfinite(z) & (z > 0), z, np.nan)
    return zz[..., None] * d


def _shifts(a, pad_val=np.nan):
    """The 8 neighbours of every cell of (gh, gw[, c]) ``a`` as a list (padding with NaN)."""
    pw = [(1, 1), (1, 1)] + [(0, 0)] * (a.ndim - 2)
    p = np.pad(a, pw, constant_values=pad_val)
    gh, gw = a.shape[:2]
    return [p[1 + di:1 + di + gh, 1 + dj:1 + dj + gw]
            for di in (-1, 0, 1) for dj in (-1, 0, 1) if (di, dj) != (0, 0)]


def normals(P, max_jump: float = 0.15):
    """Unit surface normals (gh, gw, 3) facing the camera; NaN at borders, next to missing cells
    and where the range jumps by more than ``max_jump`` (relative) between neighbours.

    The points are first smoothed by a 3x3 mean over the neighbours within ``max_jump`` of the
    centre's range, so smoothing never blends across an occlusion edge."""
    P = np.asarray(P, dtype=np.float64)
    gh, gw = P.shape[:2]
    out = np.full((gh, gw, 3), np.nan)
    if gh < 3 or gw < 3:
        return out
    rng = np.linalg.norm(P, axis=2)
    rng[~np.isfinite(P).all(axis=2)] = np.nan
    S = P.copy()
    acc, cnt = np.where(np.isfinite(P), P, 0.0), np.isfinite(rng).astype(float)
    for sp, sr in zip(_shifts(P), _shifts(rng)):
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(sr) & np.isfinite(rng) & (np.abs(sr - rng) <= max_jump * np.minimum(sr, rng))
        acc = acc + np.where(ok[..., None], sp, 0.0)
        cnt = cnt + ok
    with np.errstate(invalid="ignore", divide="ignore"):
        S = np.where((cnt > 0)[..., None], acc / np.maximum(cnt, 1)[..., None], np.nan)
    S[~np.isfinite(rng)] = np.nan
    dx = S[1:-1, 2:] - S[1:-1, :-2]
    dy = S[2:, 1:-1] - S[:-2, 1:-1]
    n = np.cross(dx, dy)
    ln = np.linalg.norm(n, axis=2)
    with np.errstate(invalid="ignore", divide="ignore"):
        n = n / ln[..., None]
    n[ln < 1e-12] = np.nan
    # occlusion edges: any of the 4 neighbours (unsmoothed range) jumps relative to the centre
    c = rng[1:-1, 1:-1]
    bad = ~np.isfinite(c)
    for nb in (rng[1:-1, :-2], rng[1:-1, 2:], rng[:-2, 1:-1], rng[2:, 1:-1]):
        with np.errstate(invalid="ignore"):
            bad |= ~np.isfinite(nb) | (np.abs(nb - c) > max_jump * np.minimum(nb, c))
    n[bad] = np.nan
    cen = P[1:-1, 1:-1]
    flip = np.sum(n * -cen, axis=2) < 0
    n = np.where(flip[..., None], -n, n)
    out[1:-1, 1:-1] = n
    return out


def classify(n, P, cam: dict, ground_h=None) -> dict:
    """{'cls': uint8, 'pitch': float32 deg, 'az': float32 deg} per cell (see module docstring).

    ``ground_h`` (gh, gw) is the DEM elevation under each cell; with it, flat cells less than
    GROUND_MAX_ABOVE_M above the DEM are ground, else every flat cell is a flat roof."""
    n = np.asarray(n, dtype=np.float64)
    valid = np.isfinite(n).all(axis=2)
    nz = np.clip(np.where(valid, n[..., 2], 0.0), -1, 1)
    tilt = np.degrees(np.arccos(np.abs(nz)))
    cls = np.zeros(valid.shape, np.uint8)
    flat = valid & (tilt < FLAT_MAX_DEG)
    wall = valid & (tilt > WALL_MIN_DEG)
    pitched = valid & ~flat & ~wall
    cls[flat] = FLAT
    cls[pitched] = PITCHED
    cls[wall] = WALL
    if ground_h is not None:
        elev = float(cam.get("elev_m") or 0.0) + np.asarray(P, dtype=np.float64)[..., 2]
        with np.errstate(invalid="ignore"):
            cls[flat & ((elev - np.asarray(ground_h, dtype=np.float64)) < GROUND_MAX_ABOVE_M)] = GROUND
    pitch = np.where(valid & ~wall, tilt, np.nan).astype(np.float32)
    az = np.degrees(np.arctan2(n[..., 0], n[..., 1])) % 360.0
    az = np.where(wall | pitched, az, np.nan).astype(np.float32)
    return {"cls": cls, "pitch": pitch, "az": az}


def building_summary(cls, pitch, az, labels) -> dict:
    """Per building label (int >= 0 in ``labels``; -1 = none) -> {n, frac_wall, frac_flat,
    frac_pitched, roof, pitch_deg, wall_az}. Fractions are of the building's classified cells
    (ground counts as classified but in none of the three); roof is 'flat' / 'pitched' by the
    majority of roof cells, 'unknown' under MIN_ROOF_CELLS of them."""
    out = {}
    for lab in np.unique(labels[labels >= 0]):
        m = labels == lab
        c = cls[m]
        n_cls = int((c > 0).sum())
        nw, nf, npi = int((c == WALL).sum()), int((c == FLAT).sum()), int((c == PITCHED).sum())

        def fr(k, n=n_cls):
            return round(k / n, 4) if n else 0.0
        roof = "unknown"
        if nf + npi >= MIN_ROOF_CELLS:
            roof = "flat" if nf >= npi else "pitched"
        pd = None
        if npi:
            pv = pitch[m][c == PITCHED]
            pv = pv[np.isfinite(pv)]
            if pv.size:
                pd = float(np.median(pv))
        wall_az = []
        if nw:
            a = az[m][c == WALL]
            a = a[np.isfinite(a)]
            if a.size:
                width = 360.0 / AZ_BINS
                # bins centred on multiples of 22.5 deg, so N / E / S / W land mid-bin
                idx = (np.floor((a + width / 2) / width).astype(int)) % AZ_BINS
                hist = np.bincount(idx, minlength=AZ_BINS)
                for b in np.argsort(-hist)[:4]:
                    if hist[b] >= AZ_BIN_FRACTION * a.size:
                        sel = a[idx == b]
                        ang = np.degrees(np.arctan2(np.sin(np.radians(sel)).mean(),
                                                    np.cos(np.radians(sel)).mean())) % 360.0
                        wall_az.append(int(round(ang)) % 360)
        out[int(lab)] = {"n": int(m.sum()), "frac_wall": fr(nw), "frac_flat": fr(nf),
                         "frac_pitched": fr(npi), "roof": roof, "pitch_deg": pd, "wall_az": wall_az}
    return out


def rasterize(buildings, gw: int, gh: int, W: float, H: float):
    """Label grid (gh, gw) int32 (-1 none) and the id list; label k is ``ids[k]``.

    ``buildings``: [{id, rings: [[[x, y], ...], ...] in photo pixels, dist (m)}]. Drawn far to
    near so nearer buildings overwrite farther ones; a missing dist counts as far."""
    from PIL import Image, ImageDraw
    img = Image.new("I", (gw, gh), -1)
    dr = ImageDraw.Draw(img)
    order = sorted(range(len(buildings)),
                   key=lambda k: -(buildings[k].get("dist") if buildings[k].get("dist") is not None
                                   else float("inf")))
    sx, sy = gw / float(W), gh / float(H)
    for k in order:
        for ring in buildings[k].get("rings") or []:
            pts = [(float(x) * sx - 0.5, float(y) * sy - 0.5) for x, y in ring]
            if len(pts) >= 3:
                dr.polygon(pts, fill=k)
    labels = np.array(img, dtype=np.int32).reshape(gh, gw)
    return labels, [b.get("id") for b in buildings]
