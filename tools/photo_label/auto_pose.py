"""Start pose for a photo without clicked pairs: match the photo's skyline to the DEM horizon.

F-PHOTOVIEW step 7. :func:`candidate_poses` proposes camera poses (position, heading, pitch,
focal length) for a photo whose camera is unknown, scoring each against the terrain horizon
seen from the candidate position.

* :func:`photo_skyline`: per column, the first row below the sky (sky = blue or near-white
  pixels connected to the top edge); NaN where the column shows no sky boundary.
* :func:`dem_horizon`: elevation angle of the terrain skyline at every azimuth from a point
  (ray march over the DEM, earth curvature with refraction).
* Search: candidate positions (EXIF, viewpoints, a grid over locally high cells) x heading
  (3 deg) x pitch x field of view, coarse; then a fine local grid (heading, pitch, f, camera
  height above ground) on the best candidates; then a small position polish.
* Score: asymmetric mean row error in px. Buildings and trees can only raise the photo's
  skyline above the bare-earth horizon, never lower it, so a photo skyline below the model
  horizon costs full, one above it ``ABOVE_WEIGHT``.

The photo's skyline is only usable where there is sky. A photo with no sky (a view down on a
city) yields no candidates; the caller must then fall back to clicked pairs.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import map_coordinates

from geo2stl.geo import EARTH_RADIUS_M as EARTH_R
from geo2stl.geo import M_PER_DEG_SPHERE as M_PER_DEG_LAT

REFRACTION_K = 0.13
CANOPY_M = 10.0             # buildings and trees stand on the bare-earth DEM, added to every ray sample
OPEN_RANGE_M = 3000.0
OPEN_MIN_EL = 0.5
LOSS_CAP = 0.1             # per-column loss is clipped at this fraction of the frame height
FREE_COST = 0.012           # fraction of the frame height: price of a column the terrain cannot judge
PITCH_UP_PX = 0.4           # prior: px added per degree of upward pitch (photos from a height look down)
MIN_CLOSED = 0.3            # fraction of columns that must see terrain inside the DEM, see _loss
ABOVE_WEIGHT = 0.8          # cost of a photo skyline above the model horizon vs below it
AZ_STEP = 0.5               # deg, horizon profile resolution
CAM_ABOVE_GROUND = 2.0      # m
ELEV_OFFSETS = (2.0, 12.0, 25.0)   # camera height above the DEM tried in the fine stage, m
COARSE_PITCH = (-24.0, -14.0, -4.0, 6.0)
COARSE_HFOV = (30.0, 42.0, 56.0, 72.0, 92.0)
COARSE_HEADING_STEP = 3.0
N_POLISH = 8                # fine-stage winners that get the position polish
N_FINE = 36                       # coarse poses (best score) that get the fine stage
N_COARSE_KEEP = 3                 # coarse poses kept per cell, at least 9 deg apart in heading
MIN_VALID_COLS = 12


# --------------------------------------------------------------------------- photo skyline


def photo_skyline(rgb: np.ndarray) -> np.ndarray:
    """Per-column row of the sky/ground boundary (float, NaN where unknown).

    Sky is blue (hue 95-130, some saturation, bright) or near white and bright, connected to
    the top edge. Hazy distant mountains are darker and greyer than the sky beside them and
    stay ground, which is what is wanted: they are the terrain horizon. Columns whose top
    pixel is not sky, that are sky all the way down, or whose boundary lies in the top 3 % of
    the frame are NaN. A frame with sky in under 8 % of its columns is all NaN.
    """
    import cv2

    rgb = np.ascontiguousarray(rgb[..., :3]).astype(np.uint8)
    h, w = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.int32)
    hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = ((hh >= 95) & (hh <= 130) & (ss >= 30) & (vv >= 150)) | ((ss < 25) & (vv >= 215))
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    _, lab = cv2.connectedComponents(m, connectivity=4)
    top = np.unique(lab[0])
    top = top[top > 0]
    sky = np.isin(lab, top) if top.size else np.zeros((h, w), bool)

    out = np.full(w, np.nan)
    for x in range(w):
        col = sky[:, x]
        if not col[0]:
            continue
        nonsky = np.flatnonzero(~col)
        if nonsky.size:
            out[x] = float(nonsky[0])
    out[(out < 0.03 * h) | (out > 0.95 * h)] = np.nan
    if np.isfinite(out).mean() < 0.08:
        return np.full(w, np.nan)
    return _nan_median(out, 9)


def _nan_median(v: np.ndarray, size: int) -> np.ndarray:
    """Running median over the finite values in a window; NaN stays NaN."""
    out = np.full_like(v, np.nan)
    r = size // 2
    for i in np.flatnonzero(np.isfinite(v)):
        win = v[max(0, i - r): i + r + 1]
        out[i] = np.nanmedian(win)
    return out


# --------------------------------------------------------------------------- DEM horizon


def _dem_sampler(dem):
    """Function (lat, lon) -> terrain m with NaN outside the model, on a north-up raster."""
    z = np.where(np.isfinite(dem.z), dem.z, np.nan)
    fill = np.where(np.isfinite(z), z, np.nanmin(z))
    t = dem.t
    h, w = z.shape

    def sample(lat, lon):
        c = (np.asarray(lon, float) - t.c) / t.a - 0.5
        r = (np.asarray(lat, float) - t.f) / t.e - 0.5
        v = map_coordinates(fill, [r.ravel(), c.ravel()], order=1, mode="nearest").reshape(c.shape)
        bad = (c < -0.5) | (r < -0.5) | (c > w - 0.5) | (r > h - 0.5)
        return np.where(bad, np.nan, v)

    return sample


def dem_horizon(dem, lat: float, lon: float, elev_m: float, *, max_range_m: float = 15000.0,
                az_step: float = AZ_STEP, sampler=None, canopy_m: float = CANOPY_M):
    """Terrain skyline seen from ``(lat, lon, elev_m)``: ``(az_deg, el_deg, open)`` arrays.

    ``el_deg[i]`` is the largest elevation angle over the ray at ``az_deg[i]``, from samples
    every 30 m to 3 km and every 75 m beyond, raised by up to ``canopy_m``, ramping in over 60-300 m (the photo's skyline is
    roofs and trees, the DEM bare earth) and lowered by earth curvature (with refraction). Rays
    that leave the model keep what they saw before; a ray with no terrain at all is -90.
    ``open[i]`` is True where the horizon is not pinned down by nearby terrain: the highest
    angle comes from beyond ``OPEN_RANGE_M`` or is under ``OPEN_MIN_EL`` (the DEM tile is only
    ~12 km wide, so a distant range or plain beyond its edge may stand higher: the real horizon
    is then at least ``el_deg``).
    """
    sampler = sampler or _dem_sampler(dem)
    az = np.arange(0.0, 360.0, az_step)
    d = np.concatenate([np.arange(30.0, 3000.0, 30.0), np.arange(3000.0, max_range_m, 75.0)])
    a = np.radians(az)[:, None]
    kx = M_PER_DEG_LAT * math.cos(math.radians(lat))
    la = lat + d[None, :] * np.cos(a) / M_PER_DEG_LAT
    lo = lon + d[None, :] * np.sin(a) / kx
    z = sampler(la, lo)
    drop = (1.0 - REFRACTION_K) * d ** 2 / (2.0 * EARTH_R)
    ramp = np.clip((d - 60.0) / 240.0, 0.0, 1.0)      # no canopy right at the camera
    ang = np.degrees(np.arctan2(z + canopy_m * ramp[None, :] - elev_m - drop[None, :], d[None, :]))
    ang = np.where(np.isfinite(ang), ang, -90.0)
    el = ang.max(axis=1)
    far = d[ang.argmax(axis=1)] > OPEN_RANGE_M
    return az, el, far | (el < OPEN_MIN_EL)


# --------------------------------------------------------------------------- projection


def horizon_rows(el_h: np.ndarray, heading, pitch, f, xs, width: int, height: int,
                 iters: int = 5, az_step: float = AZ_STEP, open_h: np.ndarray | None = None):
    """Image row of the horizon profile ``el_h`` (elevation per azimuth, step ``az_step``) in
    the columns ``xs`` for cameras with heading/pitch (deg) and focal length f (px), roll 0.

    ``heading``, ``pitch``, ``f`` broadcast to shape (N,); the result is (N, len(xs)). The row of
    a column is where the ray's elevation equals the horizon's at the ray's azimuth, found by a
    fixed-point iteration (the row moves the elevation by about f px per radian). Rows can lie
    outside the frame; NaN where the ray points backwards. With ``open_h`` (bool per azimuth,
    see :func:`dem_horizon`) the result is ``(rows, open)``, ``open`` (N, len(xs)) the flag at
    each column's ray.
    """
    h = np.radians(np.asarray(heading, float))[:, None]
    p = np.radians(np.asarray(pitch, float))[:, None]
    f = np.asarray(f, float)[:, None]
    xn = (np.asarray(xs, float)[None, :] - width / 2.0) / f
    sh, ch, sp, cp = np.sin(h), np.cos(h), np.sin(p), np.cos(p)
    prof = np.concatenate([el_h, el_h[:1]])
    grid = np.arange(prof.size) * az_step

    def hor(az_deg):
        return np.interp(np.mod(az_deg, 360.0), grid, prof)

    def ray(yn):
        e = xn * ch - yn * sh * sp + sh * cp
        n = -xn * sh - yn * ch * sp + ch * cp
        u = yn * cp + sp
        return np.degrees(np.arctan2(e, n)), np.arctan2(u, np.hypot(e, n)), e * (sh * cp) + n * (ch * cp) + u * sp

    # start: pitch-only estimate
    az0 = np.degrees(h) + np.degrees(np.arctan(xn))
    yn = np.tan(np.radians(hor(az0)) - p)
    for _ in range(iters):
        az, el, _fw = ray(yn)
        yn = yn + (np.radians(hor(az)) - el)
    az, _el, fwd = ray(yn)
    rows = np.where(fwd > 0.05, height / 2.0 - f * yn, np.nan)
    if open_h is None:
        return rows
    idx = np.round(np.mod(az, 360.0) / az_step).astype(int) % open_h.size
    return rows, open_h[idx]


def _loss(photo_rows: np.ndarray, model_rows: np.ndarray, height: int,
          open_cols: np.ndarray | None = None) -> np.ndarray:
    """Asymmetric mean |row error| per pose, in px.

    Photo below the model horizon costs full, above it ``ABOVE_WEIGHT`` (buildings and trees),
    each clipped at ``LOSS_CAP`` x height (a median cannot tell a flat skyline's many equal fits
    from the one that also follows the slopes). Where the ray leaves the DEM (``open_cols``) the
    model horizon is only a lower bound, so a photo skyline above it costs the flat ``FREE_COST`` x height; and a pose
    whose frame is under ``MIN_CLOSED`` such columns pays up to the clip for the shortfall (it
    would otherwise win by seeing no terrain at all).
    """
    e = photo_rows[None, :] - model_rows           # + : photo skyline lower than model
    loss = np.where(e > 0, e, -ABOVE_WEIGHT * e)
    free = np.zeros(loss.shape, bool)
    if open_cols is not None:
        free = open_cols & (e <= 0)
        loss = np.where(free, FREE_COST * height, loss)
    cap = LOSS_CAP * height
    loss = np.where(np.isfinite(loss), np.minimum(loss, cap), cap)
    short = np.maximum(0.0, MIN_CLOSED - (1.0 - free.mean(axis=1))) / MIN_CLOSED
    return loss.mean(axis=1) + cap * short


def _f_of_hfov(hfov_deg, width):
    return width / 2.0 / np.tan(np.radians(np.asarray(hfov_deg, float)) / 2.0)


def _pick_cols(sky: np.ndarray, k: int) -> np.ndarray:
    idx = np.flatnonzero(np.isfinite(sky))
    if idx.size <= k:
        return idx
    return idx[np.linspace(0, idx.size - 1, k).round().astype(int)]


# --------------------------------------------------------------------------- candidates


def viewpoints_from_fc(fc: dict) -> list[tuple[float, float]]:
    """(lat, lon) of ``tourism=viewpoint`` features and tall named landmarks in a GeoJSON
    FeatureCollection (points, or the centroid of polygons). A landmark is a feature with
    ``properties.tourism == "viewpoint"``, or a ``name`` plus a ``height`` of 25 m or more
    (a tower), or ``man_made`` in tower/bell_tower/observation. Features without those tags
    are ignored."""
    out: list[tuple[float, float]] = []
    for ft in (fc or {}).get("features", []):
        pr = ft.get("properties") or {}
        tall = False
        try:
            tall = bool(pr.get("name")) and float(str(pr.get("height")).split()[0].replace(",", ".")) >= 25.0
        except (TypeError, ValueError):
            tall = False
        if not (pr.get("tourism") == "viewpoint"
                or pr.get("man_made") in ("tower", "bell_tower", "observation") or tall):
            continue
        g = ft.get("geometry") or {}
        pts = _geom_points(g)
        if pts.size:
            lon, lat = pts.mean(axis=0)
            out.append((float(lat), float(lon)))
    return out


def viewpoints_from_overpass(data: dict) -> list[tuple[float, float]]:
    """(lat, lon) of ``tourism=viewpoint`` and ``man_made`` tower/observation nodes in a raw
    Overpass JSON (``server.overpass_to_geojson`` keeps only buildings, so it has none)."""
    out = []
    for e in (data or {}).get("elements", []):
        tg = e.get("tags") or {}
        if e.get("type") == "node" and (tg.get("tourism") == "viewpoint"
                                        or tg.get("man_made") in ("tower", "observation")):
            out.append((float(e["lat"]), float(e["lon"])))
    return out


def _geom_points(g: dict) -> np.ndarray:
    t, c = g.get("type"), g.get("coordinates")
    if t == "Point":
        return np.array([c[:2]], float)
    if t == "Polygon":
        return np.array([p[:2] for p in c[0]], float)
    if t == "MultiPolygon":
        return np.array([p[:2] for p in c[0][0]], float)
    return np.empty((0, 2))


def _grid_candidates(sampler, lat0, lon0, radius_m, step_m=150.0):
    """Cells every ``step_m`` within ``radius_m`` that are locally high: above the 60th
    percentile of the terrain on rings at 250 m and 500 m around them."""
    kx = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    n = int(radius_m // step_m)
    ij = np.arange(-n, n + 1) * step_m
    ee, nn = np.meshgrid(ij, ij)
    keep = np.hypot(ee, nn) <= radius_m
    ee, nn = ee[keep], nn[keep]
    lat = lat0 + nn / M_PER_DEG_LAT
    lon = lon0 + ee / kx
    z = sampler(lat, lon)
    ang = np.radians(np.arange(0, 360, 30.0))
    lower = np.zeros(z.shape)
    cnt = 0
    for r in (250.0, 500.0):
        for a in ang:
            zz = sampler(lat + r * np.cos(a) / M_PER_DEG_LAT, lon + r * np.sin(a) / kx)
            lower += np.where(np.isfinite(zz), zz < z, 0)
            cnt += 1
    ok = np.isfinite(z) & (lower / cnt >= 0.6)
    return lat[ok], lon[ok]


def _dist_m(lat1, lon1, lat2, lon2):
    kx = M_PER_DEG_LAT * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * M_PER_DEG_LAT)


# --------------------------------------------------------------------------- search


class _Search:
    def __init__(self, sky, width, height, sampler, dem, f_range=None):
        self.sky, self.W, self.H = sky, width, height
        self.sampler, self.dem = sampler, dem
        self.f_range = f_range
        self.cols_c = _pick_cols(sky, 12)
        self.cols_f = _pick_cols(sky, 64)

    def horizon(self, lat, lon, off):
        """(terrain at the point, (elevation profile, open flags)) with the camera ``off`` above it."""
        z0 = float(self.sampler(np.array([lat]), np.array([lon]))[0])
        _, el, op = dem_horizon(self.dem, lat, lon, z0 + off, sampler=self.sampler)
        return z0, (el, op)

    def _score(self, hor, H_, P_, F_, cols, iters):
        rows, op = horizon_rows(hor[0], H_, P_, F_, cols, self.W, self.H, iters=iters, open_h=hor[1])
        s = _loss(self.sky[cols], rows, self.H, op) + PITCH_UP_PX * np.maximum(0.0, P_)
        i = int(np.argmin(s))
        return float(s[i]), float(H_[i]) % 360.0, float(P_[i]), float(F_[i])

    def coarse(self, hor):
        """Best ``N_COARSE_KEEP`` (score, heading, pitch, f), at least 9 deg apart in heading,
        over heading x pitch x field of view, where each pose's
        pitch is then refined for free: the median row error is a vertical shift, which is a pitch
        change of atan(shift / f) (bounded to +-6 deg)."""
        hs = np.arange(0.0, 360.0, COARSE_HEADING_STEP)
        hf = np.array(COARSE_HFOV)
        if self.f_range is not None:
            fx = _f_of_hfov(hf, self.W)
            hf = hf[(fx >= self.f_range[0] * 0.6) & (fx <= self.f_range[1] * 1.6)]
            if hf.size == 0:
                hf = np.array(COARSE_HFOV)
        H_, P_, F_ = (a.ravel() for a in np.meshgrid(hs, np.array(COARSE_PITCH),
                                                    _f_of_hfov(hf, self.W), indexing="ij"))
        cols = self.cols_c
        rows, op = horizon_rows(hor[0], H_, P_, F_, cols, self.W, self.H, iters=2, open_h=hor[1])
        err = self.sky[cols][None, :] - rows
        with np.errstate(all="ignore"):
            shift = np.nan_to_num(np.nanmedian(err, axis=1))
        lim = F_ * math.tan(math.radians(6.0))
        shift = np.clip(shift, -lim, lim)
        s = _loss(self.sky[cols], rows + shift[:, None], self.H, op)
        p_new = P_ + np.degrees(np.arctan(shift / F_))
        s = s + PITCH_UP_PX * np.maximum(0.0, p_new)
        picks: list[tuple[float, float, float, float]] = []
        for i in np.argsort(s):
            if all(abs((H_[i] - q[1] + 180) % 360 - 180) >= 9.0 for q in picks):
                picks.append((float(s[i]), float(H_[i]), float(p_new[i]), float(F_[i])))
                if len(picks) >= N_COARSE_KEEP:
                    break
        return picks

    def fine(self, hor, h0, p0, f0, dh=3.0, dp=6.0, hstep=1.0, pstep=2.0, nf=7, fspan=(0.8, 1.25),
             cols=None, iters=4):
        hs = h0 + np.arange(-dh, dh + 1e-9, hstep)
        ps = p0 + np.arange(-dp, dp + 1e-9, pstep)
        fs = f0 * np.geomspace(fspan[0], fspan[1], nf)
        H_, P_, F_ = (a.ravel() for a in np.meshgrid(hs, ps, fs, indexing="ij"))
        return self._score(hor, H_, P_, F_, self.cols_f if cols is None else cols, iters)


def candidate_poses(rgb: np.ndarray, dem, *, center_lat: float, center_lon: float,
                    radius_m: float = 3000.0, viewpoints: list[tuple[float, float]] | None = None,
                    exif: dict | None = None, top_k: int = 3) -> list[dict]:
    """Best ``top_k`` distinct camera poses for the photo, lowest ``score`` first.

    Each dict: lat, lon, elev (orthometric m in the DEM's datum: terrain + camera height),
    heading, pitch, roll (0), f (px), width, height, score (mean row error in px, lower is
    better), source ("exif" | "viewpoint" | "grid"). ``exif`` may hold ``lat``, ``lon`` and
    ``focal_35mm`` (or ``f_px``): with a GPS fix only that position (and a small polish
    around it) is searched, and the focal length is held near the EXIF value. Returns [] when
    the photo shows no sky boundary (no skyline to match) or the DEM has no data there.
    """
    height, width = rgb.shape[:2]
    sky = photo_skyline(rgb)
    if np.isfinite(sky).sum() < MIN_VALID_COLS:
        return []
    sampler = _dem_sampler(dem)
    f_range = None
    if exif:
        fx = exif.get("f_px") or (exif.get("focal_35mm") and exif["focal_35mm"] / 36.0 * width)
        if fx:
            f_range = (float(fx) * 0.85, float(fx) * 1.15)
    S = _Search(sky, width, height, sampler, dem, f_range)

    cands: list[tuple[float, float, str]] = []
    if exif and exif.get("lat") is not None and exif.get("lon") is not None:
        cands.append((float(exif["lat"]), float(exif["lon"]), "exif"))
    else:
        for la, lo in viewpoints or []:
            cands.append((float(la), float(lo), "viewpoint"))
        gl, go = _grid_candidates(sampler, center_lat, center_lon, radius_m)
        cands += [(float(a), float(b), "grid") for a, b in zip(gl, go)]
    scored = []
    for la, lo, src in cands:
        z0 = float(sampler(np.array([la]), np.array([lo]))[0])
        if not np.isfinite(z0):
            continue
        _, hor = S.horizon(la, lo, CAM_ABOVE_GROUND)
        for s, h, p, f in S.coarse(hor):
            scored.append((s, la, lo, src, h, p, f))
    scored.sort(key=lambda t: t[0])

    results = []
    for _s0, la, lo, src, h, p, f in scored[:N_FINE]:
        best = None
        for off in ELEV_OFFSETS:
            z0, hor = S.horizon(la, lo, off)
            s, hh, pp, ff = S.fine(hor, h, p, f)
            if best is None or s < best[0]:
                best = (s, hh, pp, ff, off, z0, hor)
        s, hh, pp, ff = S.fine(best[6], *best[1:4], dh=1.0, dp=2.0, hstep=0.5, pstep=0.5,
                               fspan=(0.92, 1.08), iters=6)
        results.append(((s, hh, pp, ff, best[4], best[5]), la, lo, src))
    results.sort(key=lambda r: r[0][0])
    results = [_polish(S, la, lo, src, *best) for best, la, lo, src in results[:N_POLISH]]
    results.sort(key=lambda r: r["score"])

    out: list[dict] = []
    for r in results:
        if any(_dist_m(r["lat"], r["lon"], o["lat"], o["lon"]) < 100.0
               and abs((r["heading"] - o["heading"] + 180) % 360 - 180) < 5.0 for o in out):
            continue
        out.append(r)
        if len(out) >= top_k:
            break
    return out


def _polish(S: _Search, la, lo, src, s, h, p, f, off, z0) -> dict:
    """Hill-climb the position (75 m, then 35 m, 8 neighbours) with a small local pose grid."""
    kx = M_PER_DEG_LAT * math.cos(math.radians(la))
    best = (s, la, lo, h, p, f, z0)
    for step in (75.0, 35.0):
        moved = True
        for _ in range(6):
            if not moved:
                break
            moved = False
            for de, dn in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                la2 = best[1] + dn * step / M_PER_DEG_LAT
                lo2 = best[2] + de * step / kx
                zz = float(S.sampler(np.array([la2]), np.array([lo2]))[0])
                if not np.isfinite(zz):
                    continue
                _, hor = S.horizon(la2, lo2, off)
                s2, h2, p2, f2 = S.fine(hor, best[3], best[4], best[5], dh=1.0, dp=1.0,
                                        hstep=0.5, pstep=0.5, nf=3, fspan=(0.96, 1.04))
                if s2 < best[0] - 1e-6:
                    best = (s2, la2, lo2, h2, p2, f2, zz)
                    moved = True
    s, la, lo, h, p, f, z0 = best
    _, hor = S.horizon(la, lo, off)       # heading, pitch and f trade off: widen the pose grid once more
    s2, h2, p2, f2 = S.fine(hor, h, p, f, dh=4.0, dp=3.0, hstep=0.5, pstep=0.5, nf=9,
                            fspan=(0.8, 1.25), iters=5)
    if s2 < s:
        s, h, p, f = s2, h2, p2, f2
    return {"lat": la, "lon": lo, "elev": z0 + off, "heading": h, "pitch": p, "roll": 0.0,
            "f": f, "width": S.W, "height": S.H, "score": s, "source": src}
