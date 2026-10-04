"""Locate a photo by matching its skyline to the OSM-predicted tower outline (F-WEB2 step 5c).

For photos with no camera location (Miami: 215 of 300 Commons skyline photos) and no labels.
The outline of a city's towers seen from a given spot is close to unique and does not change
with lighting, season or camera, unlike image features (SIFT matched only copies of the
same shot, 2026-10-04).

    prof = photo_profile(sky_mask, building_mask)              # per column: top of building
    towers = tower_table(osm_features, min_height_m=40)        # OSM heights only
    hits = search(prof, towers, centre_latlon, fovs_deg=...)   # best (position, heading, FOV)

Model, per candidate camera position (grid around the towers):
- predicted outline over all 3600 bearings (0.1 deg): for each bearing the highest roof
  elevation angle ``atan((H - h_cam) / d)`` of any tower covering it (the skyline is the
  topmost thing), 0 where no tower stands;
- the photo outline resampled to the same angular bins for each candidate FOV (pinhole or
  cylindrical columns);
- score = the misfit after removing a constant offset (the unknown camera tilt), divided by
  the photo outline's own variance at that FOV (1 - R^2), for every heading at once via FFT;
  only photo columns whose top pixel is building count. Why relative: a raw RMS in degrees
  always preferred the narrowest FOV (a narrow FOV shrinks every angle in the photo) and far
  positions (flat outlines), 2026-10-04 first probe.

Heights come from OSM tags and levels, never from the benchmark truth, so benchmark scores of
heights measured in located photos stay independent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

M_PER_DEG_LAT = 111_320.0
BIN_DEG = 0.1
N_BINS = int(round(360 / BIN_DEG))


@dataclass(frozen=True)
class Towers:
    """Tall buildings in a local metric frame: footprint vertices and roof heights."""
    lat0: float
    lon0: float
    verts: list[np.ndarray]     # per tower, (k, 2) east/north metres
    height_m: np.ndarray
    names: list[str]

    @property
    def kx(self) -> float:
        return M_PER_DEG_LAT * math.cos(math.radians(self.lat0))

    def to_xy(self, lat, lon):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * M_PER_DEG_LAT

    def to_ll(self, x, y):
        return self.lat0 + y / M_PER_DEG_LAT, self.lon0 + x / self.kx


@dataclass(frozen=True)
class Hit:
    lat: float
    lon: float
    heading_deg: float
    hfov_deg: float
    tilt_deg: float
    rms_deg: float
    misfit: float               # 1 - R^2: 0 perfect, 1 no better than a flat outline
    n_cols: int


# --------------------------------------------------------------------------- inputs


def tower_table(features: list[dict], min_height_m: float = 40.0,
                sources: tuple[str, ...] = ("osm_tag", "osm_levels")) -> Towers:
    """Towers from OSM building features with a tagged (or levels-derived) height."""
    from shapely.geometry import shape

    rows = []
    for f in features:
        p = f.get("properties") or {}
        h = p.get("height_m")
        if not h or h < min_height_m or p.get("height_source") not in sources:
            continue
        try:
            g = shape(f["geometry"])
        except Exception:  # noqa: BLE001
            continue
        g = g if g.geom_type == "Polygon" else max(g.geoms, key=lambda q: q.area)
        rows.append((np.asarray(g.exterior.coords)[:, :2], float(h), str(p.get("name") or "")))
    if not rows:
        raise ValueError("no tower with a tagged height")
    lat0 = float(np.mean([r[0][:, 1].mean() for r in rows]))
    lon0 = float(np.mean([r[0][:, 0].mean() for r in rows]))
    kx = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    verts = [np.column_stack([(r[0][:, 0] - lon0) * kx, (r[0][:, 1] - lat0) * M_PER_DEG_LAT])
             for r in rows]
    return Towers(lat0, lon0, verts, np.array([r[1] for r in rows]), [r[2] for r in rows])


@dataclass(frozen=True)
class PhotoProfile:
    """Per column of a photo: the row of the topmost building pixel (NaN = not usable)."""
    y_top: np.ndarray
    width: int
    height: int


def photo_profile(sky: np.ndarray, building: np.ndarray, min_run: int = 3) -> PhotoProfile:
    """Skyline of a photo from SegFormer masks.

    A column counts when, going down from the top, the first non-sky pixel starts a run of at
    least ``min_run`` building pixels (a tower against the sky), not a tree, pole or cloud edge.
    """
    h, w = sky.shape
    y_top = np.full(w, np.nan)
    not_sky = ~sky
    first = np.where(not_sky.any(axis=0), not_sky.argmax(axis=0), -1)
    for x in range(w):
        y = first[x]
        if y < 0 or y >= h - min_run:
            continue
        if building[y:y + min_run, x].all():
            y_top[x] = y
    return PhotoProfile(y_top, w, h)


# --------------------------------------------------------------------------- model


def _flat(t: Towers):
    """All footprint corners in one array, with the tower each belongs to (cached on ``t``)."""
    cache = t.__dict__.get("_flat")
    if cache is None:
        xy = np.concatenate(t.verts)
        owner = np.repeat(np.arange(len(t.verts)), [len(v) for v in t.verts])
        starts = np.r_[0, np.cumsum([len(v) for v in t.verts])[:-1]]
        cache = (xy, owner, starts)
        t.__dict__["_flat"] = cache  # frozen dataclass: cache outside the fields
    return cache


def predicted_outline(t: Towers, cam_xy: tuple[float, float], h_cam: float = 2.0,
                      min_dist_m: float = 60.0, owners: bool = False):
    """Highest roof elevation (deg) per 0.1-deg bearing bin, seen from ``cam_xy``.

    With ``owners`` also returns, per bin, the index of the tower forming the skyline (-1 none).
    Vectorised over towers (one pass over all footprint corners, one ``maximum.at``): the
    per-tower Python loop it replaced made a full search 3-5 min per photo (2026-10-04).
    """
    xy, owner, starts = _flat(t)
    dx, dy = xy[:, 0] - cam_xy[0], xy[:, 1] - cam_xy[1]
    d = np.hypot(dx, dy)
    b = np.degrees(np.arctan2(dx, dy)) % 360.0
    dmin = np.minimum.reduceat(d, starts)
    ref = b[starts][owner]                                   # each tower's first corner
    rel = (b - ref + 180.0) % 360.0 - 180.0                  # handles the 0/360 wrap
    lo = b[starts] + np.minimum.reduceat(rel, starts)
    hi = b[starts] + np.maximum.reduceat(rel, starts)
    elev = np.degrees(np.arctan2(t.height_m - h_cam, dmin))
    keep = dmin >= min_dist_m
    i0 = np.floor(lo[keep] / BIN_DEG).astype(int)
    i1 = np.ceil(hi[keep] / BIN_DEG).astype(int)
    n = i1 - i0 + 1
    tower = np.repeat(np.flatnonzero(keep), n)
    offs = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    idx = (np.repeat(i0, n) + offs) % N_BINS
    ev = np.repeat(elev[keep], n)
    out = np.zeros(N_BINS)
    np.maximum.at(out, idx, ev)
    if not owners:
        return out
    own = np.full(N_BINS, -1)
    top = (ev == out[idx]) & (ev > 0)
    own[idx[top]] = tower[top]
    return out, own


def photo_angles(prof: PhotoProfile, hfov_deg: float, projection: str = "pinhole"):
    """Photo outline on 0.1-deg bins: (elevation deg per bin or NaN, bin offsets from centre).

    Vertical angles use the same focal length as horizontal (square pixels), measured from the
    image centre row; the unknown tilt is removed later as a constant offset.
    """
    w, h = prof.width, prof.height
    cx, cyy = w / 2.0, h / 2.0
    if projection == "cylindrical":
        f = (w / 2.0) / math.radians(hfov_deg / 2.0)
    else:
        f = (w / 2.0) / math.tan(math.radians(hfov_deg / 2.0))
    xs = np.arange(w) + 0.5
    if projection == "cylindrical":
        az = np.degrees((xs - cx) / f)
    else:
        az = np.degrees(np.arctan((xs - cx) / f))
    el = np.degrees(np.arctan((cyy - prof.y_top) / f))
    n = int(math.ceil(hfov_deg / BIN_DEG)) + 2
    half = n // 2
    bins = np.round(az / BIN_DEG).astype(int) + half
    acc = np.full(n, np.nan)
    for b, e in zip(bins, el, strict=True):
        if 0 <= b < n and np.isfinite(e):
            acc[b] = e if not np.isfinite(acc[b]) else max(acc[b], e)
    return acc, half


def _score_all_headings(model: np.ndarray, photo: np.ndarray, half: int):
    """RMS (deg) after a constant offset, for every circular shift; and the offsets.

    For shift ``s`` the photo bin ``j`` sits at bearing bin ``s + j - half``.
    """
    w = np.isfinite(photo).astype(float)
    p = np.where(w > 0, photo, 0.0)
    n_w = w.sum()
    if n_w < 10:
        return None
    L = len(photo)
    size = N_BINS
    # correlation sums via FFT (circular over the model)
    def xcorr(a_model, b_photo):
        fa = np.fft.rfft(a_model, size)
        fb = np.fft.rfft(np.r_[b_photo, np.zeros(size - L)], size)
        return np.fft.irfft(fa * np.conj(fb), size)

    B = xcorr(model ** 2, w)        # sum w m^2
    C = xcorr(model, w * p)         # sum w p m
    D = xcorr(model, w)             # sum w m
    A = float((w * p * p).sum())
    P = float((w * p).sum())
    ssd = A + B - 2 * C - (P - D) ** 2 / n_w
    ssd = np.maximum(ssd, 0.0)
    var_p = A - P * P / n_w         # photo outline's own spread
    if var_p <= 1e-9:
        return None
    rms = np.sqrt(ssd / n_w)
    offset = (P - D) / n_w          # photo - model, i.e. the tilt
    # shift k aligns photo bin 0 with model bin k; the centre column is at k + half
    return ssd / var_p, rms, offset, int(n_w)


def search(prof: PhotoProfile, towers: Towers, radius_m: float = 7000.0, step_m: float = 150.0,
           fovs_deg: tuple[float, ...] = (12, 16, 20, 25, 30, 36, 44, 54, 66, 80),
           projection: str = "pinhole", h_cam: float = 2.0, top: int = 5,
           min_tower_clearance_m: float = 80.0) -> list[Hit]:
    """Best camera positions, headings and FOVs for ``prof`` (lowest misfit first)."""
    cxs = np.concatenate([v[:, 0] for v in towers.verts])
    cys = np.concatenate([v[:, 1] for v in towers.verts])
    g = np.arange(-radius_m, radius_m + 1, step_m)
    photos = {fv: photo_angles(prof, fv, projection) for fv in fovs_deg}
    best: list[tuple] = []
    for gx in g:
        for gy in g:
            if np.min(np.hypot(cxs - gx, cys - gy)) < min_tower_clearance_m:
                continue
            model = predicted_outline(towers, (gx, gy), h_cam)
            if not model.any():
                continue
            for fv, (ph, half) in photos.items():
                r = _score_all_headings(model, ph, half)
                if r is None:
                    continue
                mis, rms, off, n = r
                k = int(np.argmin(mis))
                best.append((float(mis[k]), gx, gy, (k + half) * BIN_DEG % 360.0, fv,
                             float(off[k]), n, float(rms[k])))
    best.sort(key=lambda b: b[0])
    hits, seen = [], []
    for mis, gx, gy, hd, fv, off, n, rms in best:
        if any(math.hypot(gx - sx, gy - sy) < 4 * step_m and abs((hd - sh + 180) % 360 - 180) < 5
               for sx, sy, sh in seen):
            continue
        seen.append((gx, gy, hd))
        lat, lon = towers.to_ll(gx, gy)
        hits.append(Hit(lat, lon, hd, fv, off, rms, mis, n))
        if len(hits) >= top:
            break
    return hits


def refine(prof: PhotoProfile, towers: Towers, hit: Hit, radius_m: float = 600.0,
           step_m: float = 50.0, fov_steps: int = 9, fov_span: float = 0.35,
           projection: str = "pinhole", h_cam: float = 2.0) -> Hit:
    """Local search around a coarse ``hit``: finer positions and FOVs, every heading.

    Why: the coarse grid (150-300 m, FOV steps of ~20 %) leaves the true pose between cells;
    near it the misfit drops sharply, while false hits stay flat.
    """
    x0, y0 = towers.to_xy(hit.lat, hit.lon)
    fovs = hit.hfov_deg * np.exp(np.linspace(-fov_span, fov_span, fov_steps))
    photos = {float(fv): photo_angles(prof, float(fv), projection) for fv in fovs}
    g = np.arange(-radius_m, radius_m + 1, step_m)
    best = None
    for dx in g:
        for dy in g:
            model = predicted_outline(towers, (x0 + dx, y0 + dy), h_cam)
            if not model.any():
                continue
            for fv, (ph, half) in photos.items():
                r = _score_all_headings(model, ph, half)
                if r is None:
                    continue
                mis, rms, off, n = r
                k = int(np.argmin(mis))
                if best is None or mis[k] < best[0]:
                    best = (float(mis[k]), x0 + dx, y0 + dy, (k + half) * BIN_DEG % 360.0, fv,
                            float(off[k]), int(n), float(rms[k]))
    if best is None:
        return hit
    mis, x, y, hd, fv, off, n, rms = best
    lat, lon = towers.to_ll(x, y)
    return Hit(lat, lon, hd, fv, off, rms, mis, n)


def locate(prof: PhotoProfile, towers: Towers, top: int = 8, **search_kw) -> list[Hit]:
    """Coarse search, then ``refine`` the best ``top`` hits; best first."""
    projection = search_kw.get("projection", "pinhole")
    coarse = search(prof, towers, top=top, **search_kw)
    fine = [refine(prof, towers, h, projection=projection) for h in coarse]
    return sorted(fine, key=lambda h: h.misfit)


def _col_of_bin(j: float, half: int, prof: PhotoProfile, hfov_deg: float, projection: str) -> float:
    az = math.radians((j - half) * BIN_DEG)
    cx = prof.width / 2.0
    if projection == "cylindrical":
        return cx + (prof.width / 2.0) / math.radians(hfov_deg / 2.0) * az
    return cx + (prof.width / 2.0) / math.tan(math.radians(hfov_deg / 2.0)) * math.tan(az)


def identify(prof: PhotoProfile, towers: Towers, hit: Hit, projection: str = "pinhole",
             h_cam: float = 2.0, min_bins: int = 6, search_deg: float = 1.5,
             margin_bins: int = 8):
    """Towers forming the skyline at ``hit``, each aligned on its own to the photo outline.

    Returns ``camera_solver.Obs`` (photo column of the tower's centre, footprint centroid).
    Each tower's predicted outline piece (its bins plus a margin) slides by up to
    ``search_deg`` against the photo outline, independently of the others, so the columns
    carry information the coarse pose did not impose.
    """
    from .camera_solver import Obs

    x, y = towers.to_xy(hit.lat, hit.lon)
    model, own = predicted_outline(towers, (x, y), h_cam, owners=True)
    ph, half = photo_angles(prof, hit.hfov_deg, projection)
    k0 = int(round(hit.heading_deg / BIN_DEG)) - half          # model bin of photo bin 0
    S = int(round(search_deg / BIN_DEG))
    L = len(ph)
    mbins = (k0 + np.arange(L)) % N_BINS
    owner_in_view = own[mbins]
    obs = []
    for ti in np.unique(owner_in_view[owner_in_view >= 0]):
        js = np.where(owner_in_view == ti)[0]
        if len(js) < min_bins:
            continue
        lo, hi = max(0, js.min() - margin_bins), min(L - 1, js.max() + margin_bins)
        win = np.arange(lo, hi + 1)
        m = model[mbins[win]]
        best = None
        for sft in range(-S, S + 1):
            jj = win + sft
            ok = (jj >= 0) & (jj < L)
            p = np.where(ok, ph[np.clip(jj, 0, L - 1)], np.nan)
            f = np.isfinite(p)
            if f.sum() < min_bins:
                continue
            d = p[f] - m[f]
            err = float(np.median(np.abs(d - np.median(d))))
            if best is None or err < best[0]:
                best = (err, sft)
        if best is None:
            continue
        centre = (js.min() + js.max()) / 2.0 + best[1]
        col = _col_of_bin(centre, half, prof, hit.hfov_deg, projection)
        if 0 <= col < prof.width:
            cx_m, cy_m = towers.verts[ti].mean(axis=0)
            lat, lon = towers.to_ll(cx_m, cy_m)
            obs.append(Obs(col, lat, lon, towers.names[ti] or f"tower{ti}"))
    return obs


def locate_and_solve(prof: PhotoProfile, towers: Towers, top: int = 8, f_prior_px=None,
                     **search_kw):
    """``locate`` then, per hit, ``identify`` + ``camera_solver.solve_pose``.

    Returns ``(hit, pose, obs)`` in outline-misfit order (``locate``'s ranking). The solved
    pose is a check, not the ranking: with 4 identified towers and 4 unknowns any false hit
    fits exactly (0 px), and per-tower alignment is still noisy (58 px at the true pose on the
    known-answer photo), so ranking by pose RMS picked a false hit (2026-10-04). Trust a pose
    only when ``len(pose.names) >= 8`` (4 spare towers) and its RMS is small.
    """
    from .camera_solver import solve_pose

    projection = search_kw.get("projection", "pinhole")
    out = []
    for h in locate(prof, towers, top=top, **search_kw):
        obs = identify(prof, towers, h, projection)
        if len(obs) < (3 if f_prior_px else 4):
            continue
        try:
            pose = solve_pose(obs, prof.width, projection, f_prior_px=f_prior_px,
                              search_radius_m=1500.0, grid_step_m=100.0)
        except ValueError:
            continue
        out.append((h, pose, obs))
    return out
