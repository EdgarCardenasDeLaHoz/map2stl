"""Place a skyline photo by scoring every 0.1-deg column against a trusted building reference.

Why per column, not per segment: ``building_groups`` matched runs of one building (segments) and
did not place Cartagena's Commons photos with known cameras (0 of 16 within 1 km, 2026-10-06).
Two things broke it: most model heights were fallbacks or unchecked drone readings (the model
towers floated above anything in the photo), and MobileSAM splits towers differently from OSM
footprints, so segment boundaries never lined up. Scoring single columns needs no segmentation
on either side, and splitting the reference by trust keeps unreliable heights from voting:

- trusted buildings (OSM ``height`` / ``building:levels`` tags, published heights, drone
  readings that agree with a second reading) predict a skyline elevation per bin; a photo
  column whose top lies within ``tol_deg`` of it earns +1;
- untrusted buildings (every other one, with a [lo, hi] height interval) can only explain a
  photo column (cancel the "unexplained" penalty), never earn a positive score: their heights
  are wrong by up to 3x (Street View read 200 m for a 50 m block);
- a photo column showing a building where no model building reaches costs ``lam``;
- a trusted tower bin where the photo shows sky (its top well below the tower's) costs ``mu``,
  much less than ``lam``: towers can postdate the photo.

The unknown camera tilt is a constant vertical offset, fitted per heading as the mode of
(photo top - trusted model top), i.e. the offset that makes most trusted columns agree; every
heading at once with numpy sliding windows.

    ref = trusted_reference(features_from_heights(rows), rows, site)
    cols = photo_columns(y_top, width, height)
    bins = model_bins(ref, cam_xy, h_cam)
    scores = score_poses(cols, bins, (hfov,))        # [n_fov, 3600]: heading = index * 0.1 deg

Status (2026-10-06, Cartagena's 17 Commons photos with recorded GPS): no-go. Only 7 have a
checkable heading (the Baluarte photos, Hotel Estelar identified by eye); the GPS of the other
10 contradicts the photo or shows no published tower. At the GPS position, 4 of 7 headings
come out within 3 deg (EXIF compass: 0 of 6, off by 7-30 deg); among 500 random cameras the
GPS camera ranks first for 1 of 17. The photos' tower spreads are narrower than the model
predicts from the recorded position and EXIF FOV, and the trusted set is too sparse to
outvote that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import skyline_match as sm

TRUSTED_TAG_SOURCES = ("osm_tag", "osm_levels")


# --------------------------------------------------------------------------- reference


@dataclass
class Reference:
    """All buildings in one metric frame, split by how far their height can be trusted.

    ``towers.height_m`` is the trusted height, or the interval top for untrusted buildings;
    ``lo_hi[i]`` the height interval (equal ends when trusted); ``source[i]`` why.
    """
    towers: sm.Towers
    trusted: np.ndarray
    lo_hi: np.ndarray
    source: list[str]
    _subs: dict = field(default_factory=dict, repr=False)

    def __iter__(self):                       # ``towers, mask, lo_hi = trusted_reference(...)``
        return iter((self.towers, self.trusted, self.lo_hi))

    def sub(self, which: str):
        """(Towers, global index) of the trusted, untrusted-low or untrusted-high buildings."""
        if which not in self._subs:
            sel = np.flatnonzero(self.trusted if which == "trusted" else ~self.trusted)
            h = {"trusted": self.towers.height_m, "lo": self.lo_hi[:, 0],
                 "hi": self.lo_hi[:, 1]}[which][sel]
            t = (sm.Towers(self.towers.lat0, self.towers.lon0,
                           [self.towers.verts[i] for i in sel], np.asarray(h, float),
                           [self.towers.names[i] for i in sel]) if len(sel) else None)
            self._subs[which] = (t, sel)
        return self._subs[which]


def features_from_heights(rows: list[dict]) -> list[dict]:
    """GeoJSON-like features from ``heights.json`` rows (footprint, OSM tag height, id)."""
    out = []
    for r in rows:
        fp = r.get("footprint_lonlat")
        if not fp:
            continue
        out.append({"geometry": {"type": "Polygon", "coordinates": [fp]},
                    "properties": {"feature_id": r.get("feature_id"), "name": r.get("name"),
                                   "height_m": r.get("height_tag_m"),
                                   "height_source": r.get("height_source")}})
    return out


def _ring(f: dict) -> np.ndarray | None:
    g = f.get("geometry") or {}
    c = g.get("coordinates")
    if not c:
        return None
    if g.get("type") == "MultiPolygon":
        c = max(c, key=lambda p: len(p[0]))
    return np.asarray(c[0], float)[:, :2]


def trusted_reference(osm_features: list[dict], heights_rows: list[dict] = (),
                      site: dict | None = None, min_height_m: float = 25.0,
                      drone_tol: float = 0.15, max_height_m: float | None = None,
                      known_radius_m: float = 30.0, min_earn_height_m: float = 60.0) -> Reference:
    """Buildings with a trust flag and a height interval.

    Trusted, in order: a published height in ``site['known_heights_m']`` (nearest footprint
    centroid within ``known_radius_m``); an OSM ``height``/``levels`` tag; a drone reading
    (``site['elevated_seeds']`` in the row's ``per_seed_median_m``) confirmed by a second
    reading (another drone seed, or the median of the street seeds) within ``drone_tol``.
    Why confirmed: one drone seed alone read Ravello 120 m (published 144 m) and a 118 m block
    47 m. Every other building whose readings reach ``min_height_m`` is untrusted with
    [0.75 x lowest, 1.25 x highest reading] (at least ``min_height_m`` on top).

    A trusted building lower than ``min_earn_height_m`` is demoted to an explainer with
    [0.8 h, 1.2 h]: low blocks near the horizon form long flat outlines that any low photo
    outline matches after a tilt, and outvoted the towers (Cartagena Baluarte photos: 0-2 of 7
    headings within 3 deg with them earning, 4 of 7 without, 2026-10-06).
    """
    site = site or {}
    rows = {r.get("feature_id"): r for r in heights_rows}
    drone_seeds = set(site.get("elevated_seeds") or ())
    cap = max_height_m or float(site.get("max_plausible_height_m") or 250.0) * 1.1
    polys, names, lo_hi, trusted, source = [], [], [], [], []
    for f in osm_features:
        ring = _ring(f)
        if ring is None or len(ring) < 3:
            continue
        p = f.get("properties") or {}
        r = rows.get(p.get("feature_id"), {})
        tag = p.get("height_m") if p.get("height_source") in TRUSTED_TAG_SOURCES else None
        seeds = r.get("per_seed_median_m") or {}
        drone = [v for k, v in seeds.items() if k in drone_seeds and v]
        street = [v for k, v in seeds.items() if k not in drone_seeds and v]
        h, src = None, ""
        if tag:
            h, src = float(tag), str(p.get("height_source"))
        elif drone:
            d = float(np.median(drone))
            agree = (len(drone) >= 2 and (max(drone) - min(drone)) <= drone_tol * d) or (
                street and abs(d - float(np.median(street))) <= drone_tol * d)
            if agree:
                h, src = d, "drone"
        if h is not None:
            polys.append(ring)
            names.append(str(p.get("name") or p.get("feature_id") or ""))
            lo_hi.append((h, h))
            trusted.append(True)
            source.append(src)
            continue
        reads = [v for v in [*seeds.values(), r.get("effective_height_m"),
                             r.get("street_view_m")] if v]
        if not reads or max(reads) < min_height_m:
            continue
        polys.append(ring)
        names.append(str(p.get("name") or p.get("feature_id") or ""))
        lo_hi.append((max(3.0, 0.75 * min(reads)), min(cap, max(min_height_m, 1.25 * max(reads)))))
        trusted.append(False)
        source.append("untrusted")
    if not polys:
        raise ValueError("no building")
    lat0 = float(np.mean([q[:, 1].mean() for q in polys]))
    lon0 = float(np.mean([q[:, 0].mean() for q in polys]))
    kx = sm.M_PER_DEG_LAT * math.cos(math.radians(lat0))
    verts = [np.column_stack([(q[:, 0] - lon0) * kx, (q[:, 1] - lat0) * sm.M_PER_DEG_LAT])
             for q in polys]
    lo_hi = np.array(lo_hi, float)
    trusted = np.array(trusted, bool)
    # published heights override whatever the footprint had (OSM tags overstate two of seven)
    cen = np.array([v.mean(axis=0) for v in verts])
    for name, k in (site.get("known_heights_m") or {}).items():
        if name.startswith("_") or not isinstance(k, dict):
            continue
        x = (k["lon"] - lon0) * kx
        y = (k["lat"] - lat0) * sm.M_PER_DEG_LAT
        d = np.hypot(cen[:, 0] - x, cen[:, 1] - y)
        i = int(np.argmin(d))
        if d[i] <= known_radius_m:
            lo_hi[i] = float(k["height_m"])
            trusted[i] = True
            source[i] = "published"
            names[i] = name
    low = trusted & (lo_hi[:, 0] < min_earn_height_m)
    lo_hi[low] = np.column_stack([0.8 * lo_hi[low, 0], 1.2 * lo_hi[low, 0]])
    trusted &= ~low
    source = [f"{q}:low" if lw else q for q, lw in zip(source, low, strict=True)]
    towers = sm.Towers(lat0, lon0, verts, np.where(trusted, lo_hi[:, 0], lo_hi[:, 1]), names)
    return Reference(towers, trusted, lo_hi, source)


def all_osm_reference(osm_features: list[dict], heights_rows: list[dict],
                      min_height_m: float = 25.0, min_earn_height_m: float = 0.0) -> Reference:
    """Baseline: every building at its published ``effective_height_m``, all trusted."""
    rows = {r.get("feature_id"): r for r in heights_rows}
    feats = []
    for f in osm_features:
        r = rows.get((f.get("properties") or {}).get("feature_id")) or {}
        h = r.get("effective_height_m")
        if h and h >= min_height_m:
            feats.append({"geometry": f["geometry"],
                          "properties": {**f["properties"], "height_m": h,
                                         "height_source": "osm_tag"}})
    return trusted_reference(feats, (), None, min_height_m=min_height_m,
                             min_earn_height_m=min_earn_height_m)


# --------------------------------------------------------------------------- photo


@dataclass(frozen=True)
class ColProfile:
    """Per photo column: the row of the skyline top (``valid`` where it is a building)."""
    top_px: np.ndarray
    valid: np.ndarray
    width: int
    height: int

    def as_profile(self) -> sm.PhotoProfile:
        return sm.PhotoProfile(np.where(self.valid, self.top_px, np.nan), self.width, self.height)


def photo_columns(y_top: np.ndarray, width: int | None = None,
                  height: int | None = None) -> ColProfile:
    """``ColProfile`` from a cached ``profiles.npz`` row (NaN = sky, tree or unusable)."""
    y = np.asarray(y_top, float)
    w = int(width or len(y))
    if len(y) != w:
        raise ValueError(f"profile has {len(y)} columns, image {w}")
    h = int(height or (np.nanmax(y) + 1 if np.isfinite(y).any() else 1))
    return ColProfile(np.nan_to_num(y, nan=0.0), np.isfinite(y), w, h)


# --------------------------------------------------------------------------- model


@dataclass(frozen=True)
class ModelBins:
    """Per 0.1-deg bearing bin: trusted skyline elevation (NaN none) and its building
    (global index, -1 none); untrusted elevation interval (``u_hi`` 0 where none)."""
    trusted_deg: np.ndarray
    owner: np.ndarray
    u_lo: np.ndarray
    u_hi: np.ndarray


def model_bins(ref: Reference, cam_xy: tuple[float, float], h_cam: float = 2.0,
               min_dist_m: float = 60.0) -> ModelBins:
    """Skyline predicted from ``cam_xy`` by the trusted and the untrusted buildings."""
    n = sm.N_BINS
    t, sel = ref.sub("trusted")
    if t is not None:
        el, own = sm.predicted_outline(t, cam_xy, h_cam, min_dist_m, owners=True)
        owner = np.where(own >= 0, sel[np.maximum(own, 0)], -1)
        tdeg = np.where(owner >= 0, el, np.nan)
    else:
        tdeg, owner = np.full(n, np.nan), np.full(n, -1)
    lo_t, _ = ref.sub("lo")
    hi_t, _ = ref.sub("hi")
    if hi_t is not None:
        u_lo = sm.predicted_outline(lo_t, cam_xy, h_cam, min_dist_m)
        u_hi = sm.predicted_outline(hi_t, cam_xy, h_cam, min_dist_m)
    else:
        u_lo = u_hi = np.zeros(n)
    return ModelBins(tdeg, owner, u_lo, u_hi)


# --------------------------------------------------------------------------- scoring


@dataclass(frozen=True)
class PoseScores:
    """Per (FOV, heading bin): score and its parts; heading of index k = k * 0.1 deg."""
    score: np.ndarray
    n_match: np.ndarray
    n_unexplained: np.ndarray
    n_sky: np.ndarray
    tilt_deg: np.ndarray
    n_cols: np.ndarray            # valid photo bins per FOV

    def best(self):
        """(fov index, heading deg, score) of the top pose."""
        i, k = np.unravel_index(int(np.argmax(self.score)), self.score.shape)
        return int(i), k * sm.BIN_DEG, float(self.score[i, k])


def _windows(a: np.ndarray, L: int, half: int) -> np.ndarray:
    """Row k: the model bins photo bins 0..L-1 cover when the centre bearing is bin k."""
    ext = np.concatenate([a, a[:L]])
    w = np.lib.stride_tricks.sliding_window_view(ext, L)[: sm.N_BINS]
    return w[(np.arange(sm.N_BINS) - half) % sm.N_BINS]


def _mode_tilt(d: np.ndarray, tol: float, max_tilt: float, step: float = 0.05):
    """Per row of ``d`` (NaN = no pair): the offset with the most values within ``tol``,
    refined as the mean of those values; NaN where a row has no pair."""
    nb = int(round(2 * max_tilt / step)) + 1
    ok = np.isfinite(d) & (np.abs(d) <= max_tilt)
    rows, cols = np.nonzero(ok)
    idx = np.round((d[rows, cols] + max_tilt) / step).astype(int)
    hist = np.bincount(rows * nb + idx, minlength=d.shape[0] * nb).reshape(d.shape[0], nb)
    r = max(1, int(round(tol / step)))
    c = np.cumsum(np.pad(hist, ((0, 0), (r + 1, r))), axis=1)
    box = c[:, 2 * r + 1:] - c[:, : -2 * r - 1]
    t0 = np.argmax(box, axis=1) * step - max_tilt
    near = ok & (np.abs(d - t0[:, None]) <= tol)
    cnt = near.sum(axis=1)
    t = np.where(cnt > 0, np.where(near, d, 0.0).sum(axis=1) / np.maximum(cnt, 1), np.nan)
    return t


def score_poses(cols: ColProfile, bins: ModelBins, hfov_list, heading_step: float = 0.1,
                tol_deg: float = 0.5, lam: float = 0.5, mu: float = 0.1,
                vague_deg: float = 8.0, earn_min_deg: float = 0.0,
                floor_deg: float = 0.3, max_tilt_deg: float = 5.0,
                projection: str = "pinhole") -> PoseScores:
    """Score every heading (``heading_step``, a multiple of 0.1 deg) for each FOV.

    Per heading: tilt = the offset most trusted columns agree on (a match only earns where the
    trusted top is at least ``earn_min_deg`` up: a flat block on the horizon matches any low
    photo outline); then, per valid photo bin
    with top elevation ``e`` (tilt removed) against trusted ``T`` and untrusted top ``U``:
    match ``|e - T| <= tol`` (+1); sky ``e < T - tol`` (photo sky where a trusted tower
    stands, -mu); unexplained: neither, not within the untrusted interval ``[U_lo, U_hi]``
    (+-tol) and ``e > floor_deg`` (-lam). An untrusted explanation is not free: it costs
    ``lam * (U_hi - U_lo) / vague_deg`` (at most ``lam``), since a tall untrusted block close to
    the camera spans 2-30 deg and would otherwise excuse any photo column (Cartagena p40: the
    old town's blocks excused every Bocagrande tower at a heading 45 deg off). Without a
    trusted pair the tilt is 0.
    """
    stride = max(1, int(round(heading_step / sm.BIN_DEG)))
    prof = cols.as_profile()
    out = {k: [] for k in ("score", "n_match", "n_unexplained", "n_sky", "tilt_deg")}
    n_cols = []
    for fv in hfov_list:
        ph, half = sm.photo_angles(prof, float(fv), projection)
        v = np.isfinite(ph)
        n_cols.append(int(v.sum()))
        L = len(ph)
        T = _windows(bins.trusted_deg, L, half)[::stride]
        U = _windows(bins.u_hi, L, half)[::stride]
        Ulo = _windows(bins.u_lo, L, half)[::stride]
        d = ph[None, :] - T
        tilt = _mode_tilt(d, tol_deg, max_tilt_deg)
        e = ph[None, :] - np.nan_to_num(tilt, nan=0.0)[:, None]
        has_t = np.isfinite(T) & v[None, :]
        match = has_t & (np.abs(e - T) <= tol_deg)
        earn = match & (T >= earn_min_deg)
        sky = has_t & (e < T - tol_deg)
        by_u = (U > 0) & (e <= U + tol_deg) & (e >= Ulo - tol_deg)
        unexpl = v[None, :] & ~match & ~sky & ~by_u & (e > floor_deg)
        vague = np.where(v[None, :] & ~match & ~sky & by_u & (e > floor_deg),
                         np.minimum(1.0, (U - Ulo) / vague_deg), 0.0).sum(1)
        nm, nu, ns = earn.sum(1), unexpl.sum(1), sky.sum(1)
        out["score"].append(nm - lam * (nu + vague) - mu * ns)
        out["n_match"].append(nm)
        out["n_unexplained"].append(nu)
        out["n_sky"].append(ns)
        out["tilt_deg"].append(tilt)
    return PoseScores(*(np.array(out[k]) for k in out), np.array(n_cols))


def rank_cameras(cols: ColProfile, ref: Reference, cams_xy, hfov_deg: float,
                 h_cams=(2.0, 10.0, 30.0), **score_kw) -> np.ndarray:
    """Best score over heading and ``h_cams`` for each camera position, at one FOV."""
    out = np.full(len(cams_xy), -np.inf)
    for i, xy in enumerate(cams_xy):
        for h in h_cams:
            s = score_poses(cols, model_bins(ref, xy, h), (hfov_deg,), **score_kw)
            out[i] = max(out[i], float(s.score.max()))
    return out
