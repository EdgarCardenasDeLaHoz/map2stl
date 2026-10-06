"""Storey count from facade floor bands (elevated panoramas; a cross-check on F-DET6 heights).

A drone height reading is ``d * (tan(e_top) - tan(e_base))``: every metre of distance error is
a proportional height error, and the footprint match that sets ``d`` is the weak step (2026-10-04,
Cartagena: the far seed read the tower behind, 13 vs 81 m). The storeys on a facade do not
depend on ``d``: the visible extent and the floor period both scale with it, so their ratio is
a count. The period itself, against the 2.6-4.5 m a floor can be, then says whether ``d`` (or the
building's identity) is right: a period of ``P`` implies the facade is really ``d * 3.1 / P`` away.

    est = estimate_floors(pano, pose, m, fd._local(pano.lat, pano.lon, ring))
    if est and est.accepted:
        h, sigma = height_from_floors(est, "residential")

Steps, per measured footprint:

- :func:`facade_ranges`: each column's bearing ray against the OSM footprint gives the facade's
  horizontal range ``d`` in that column (the first wall hit; grazing walls, incidence over
  70 deg, are dropped: a few metres of footprint error there is tens of metres of range).
- :func:`rectify_columns`: resample each column onto a uniform height grid, ``z = d tan(e)``
  metres from the camera. On ``sphere_pano``'s grid a vertical world line is one column, so
  this undoes the perspective that squeezes the upper floors seen from below (or the lower ones
  from above) and makes the floor bands strictly periodic in ``z``.
- :func:`band_profile`: per height, the median over columns of the signed vertical gradient plus
  the row energy of the horizontal gradient (mullions live only in the window band),
  high-passed. Signed, not ``|dI/dz|``: a window band and a spandrel of equal height give
  ``|dI/dz|`` peaks every half floor (the sill and the head), and the sign is what tells them
  apart.
- :func:`floor_period`: normalised autocorrelation; the period is the *shortest* lag whose peak
  reaches ``frac`` of the strongest one, so neither half-period noise (too short, too weak) nor
  the multiples (as strong, longer) win. The floor prior is a plausibility check afterwards, not
  the search window, so a duplex (6.4 m) is reported and flagged rather than folded to 3.2 m.

Refused (``accepted`` False, with the reason): under 5 px per floor (at ~20.8 px/deg, a 3.2 m
floor at 1.2 km is ~3 px), under 6 visible floors (too few periods for the autocorrelation), the
two halves or five column strips disagreeing on the period by over 8 % (a wrong range across the
facade, or two buildings in one box), or an octave ambiguity.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np

from . import footprint_detect as fd

#: What a floor can be (m); a period outside implies a wrong distance or the wrong building.
FLOOR_PRIOR_M = (2.6, 4.5)
#: Typical floor-to-floor height and the plausible period range for each building kind.
FLOOR_HEIGHT_M = {"residential": 3.0, "hotel": 3.5, "office": 3.5, "colonial": 4.2}
KIND_RANGE_M = {"residential": (2.6, 3.6), "hotel": (3.0, 4.5), "office": (3.0, 4.5),
                "colonial": (3.6, 5.0)}
#: Floor height that converts a period into an implied distance (d' = d * NOMINAL / P).
NOMINAL_FLOOR_M = 3.1
MAX_INCIDENCE_DEG = 70.0
MIN_PX_PER_FLOOR = 5.0
MIN_FLOORS = 6.0
MAX_STRIP_SPREAD = 0.08
#: Below this autocorrelation peak there is no floor pattern to count.
MIN_ACF_PEAK = 0.2
#: Roof allowance above the top floor (parapet, plant room): 0-6 m.
ROOF_ALLOWANCE_M = (0.0, 6.0)


@dataclass(frozen=True)
class PeriodFit:
    period_m: float          # NaN when no peak
    acf_peak: float          # normalised autocorrelation at the period
    alt_m: float             # the strongest other peak (NaN if none): the octave alternative
    octave_flag: bool        # the period is twice (or half) a plausible floor


@dataclass(frozen=True)
class FloorEstimate:
    n_visible: float         # visible extent / period: independent of the distance
    n_floors: int | None     # storeys when the base is visible
    lower_bound: bool        # base hidden: the count is a lower bound
    period_m: float          # at the footprint's distance; plausible only if that is right
    alt_m: float
    acf_peak: float
    octave_flag: bool
    px_per_floor: float
    strip_spread: float      # max relative period difference of the halves and column strips
    extent_m: float          # visible facade height at the footprint's distance
    dist_m: float            # median facade range
    period_plausible: bool   # within the kind's range
    implied_dist_m: float    # d * NOMINAL_FLOOR_M / period
    n_cols: int
    accepted: bool
    reason: str


# --------------------------------------------------------------------------- geometry

def facade_ranges(pano: fd.Pano, pose: fd.PanoPose, ring_xy: np.ndarray, cols,
                  max_incidence_deg: float = MAX_INCIDENCE_DEG):
    """``(d, incidence_deg)`` per column: range to the first footprint wall hit along the
    column's bearing (local metres from the camera, ``fd._local``) and the angle between the ray
    and that wall's normal. NaN on a miss or a wall hit at more than ``max_incidence_deg``."""
    cols = np.asarray(cols, int)
    b = np.radians((pano.frame_heading[cols] + pose.offset_deg) % 360.0)
    ux, uy = np.sin(b)[:, None], np.cos(b)[:, None]                   # east, north
    ring = np.asarray(ring_xy, float)
    if len(ring) > 1 and np.allclose(ring[0], ring[-1]):
        ring = ring[:-1]
    p, q = ring, np.roll(ring, -1, axis=0)
    ex, ey = (q - p)[:, 0][None, :], (q - p)[:, 1][None, :]
    px, py = p[:, 0][None, :], p[:, 1][None, :]
    den = ux * ey - uy * ex                                          # ray x edge
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (px * ey - py * ex) / den                                # range along the ray
        s = (px * uy - py * ux) / den                                # position along the edge
    hit = (np.abs(den) > 1e-12) & (t > 0) & (s >= 0) & (s <= 1)
    t = np.where(hit, t, np.inf)
    k = np.argmin(t, axis=1)
    d = t[np.arange(len(cols)), k]
    el = np.hypot(ex, ey)[0, k]
    cos_inc = np.abs(ux[:, 0] * ey[0, k] - uy[:, 0] * ex[0, k]) / np.maximum(el, 1e-12)
    inc = np.degrees(np.arccos(np.clip(cos_inc, 0.0, 1.0)))
    bad = ~np.isfinite(d) | (inc > max_incidence_deg)
    return np.where(bad, np.nan, d), np.where(np.isfinite(d), inc, np.nan)


def rectify_columns(pano: fd.Pano, pose: fd.PanoPose, gray: np.ndarray, cols, d_col,
                    z_lo: float, z_hi: float, dz: float = 0.1):
    """``(z, R, valid)``: each column resampled on the height grid ``z`` (metres above the
    camera, ``z = d tan(e)``) by linear interpolation along its rows. ``R`` is (len(z), cols);
    ``valid`` is False off the image and in columns without a range."""
    from scipy.ndimage import map_coordinates

    cols = np.asarray(cols, int)
    d = np.asarray(d_col, float)
    z = np.arange(z_lo, z_hi + 0.5 * dz, dz)
    with np.errstate(invalid="ignore"):
        rows = _rows_at(pano, pose, z[:, None], d[None, :])
    valid = np.isfinite(rows) & (rows >= 0) & (rows <= pano.height - 1)
    rr = np.where(valid, rows, 0.0)
    cc = np.broadcast_to(cols[None, :].astype(float), rr.shape)
    R = map_coordinates(np.asarray(gray, np.float32), [rr, cc], order=1, mode="nearest")
    return z, np.where(valid, R, np.nan), valid


def _rows_at(pano, pose, z, d):
    return fd._row_of(pano, pose, np.degrees(np.arctan2(z, d)))


# --------------------------------------------------------------------------- profile, period

def _hp(p: np.ndarray, w: np.ndarray, size: int) -> np.ndarray:
    """High-pass by normalised convolution: ``p`` minus its local mean over rows with ``w``."""
    from scipy.ndimage import uniform_filter1d

    num = uniform_filter1d(np.where(w, p, 0.0), size, mode="constant")
    den = uniform_filter1d(w.astype(float), size, mode="constant")
    return np.where(w, p - num / np.maximum(den, 1e-9), np.nan)


def band_profile(R: np.ndarray, valid: np.ndarray, hp_m: float = 12.0, dz: float = 0.1,
                 min_frac: float = 0.3, cols=None) -> np.ndarray:
    """One value per height: median signed ``dI/dz`` over the columns plus the mean ``|dI/dx|``
    across neighbouring columns (``cols``: pano column of each R column; non-adjacent pairs are
    skipped), each high-passed over ``hp_m`` and scaled to unit spread. NaN where under
    ``min_frac`` of the columns are valid."""
    nz, nc = R.shape
    gz = np.full((nz, nc), np.nan)
    gz[1:-1] = (R[2:] - R[:-2]) * 0.5
    gz[~valid] = np.nan
    gz[1:-1][~(valid[2:] & valid[:-2])] = np.nan
    gx = np.abs(np.diff(R, axis=1))
    pair = valid[:, 1:] & valid[:, :-1]
    if cols is not None:
        pair &= (np.diff(np.asarray(cols)) == 1)[None, :]
    gx = np.where(pair, gx, np.nan)
    ok = np.isfinite(gz).sum(1) >= max(2, min_frac * nc)
    with warnings.catch_warnings():                    # all-NaN rows: NaN, flagged below
        warnings.simplefilter("ignore", RuntimeWarning)
        vz = np.nanmedian(gz, axis=1)
        vx = np.nanmean(gx, axis=1)
    ok &= np.isfinite(vz) & np.isfinite(vx)
    size = max(3, int(round(hp_m / dz)))
    out = np.zeros(nz)
    for v in (vz, vx):
        h = _hp(np.where(ok, v, 0.0), ok, size)
        s = np.nanstd(h) if ok.any() else 0.0
        if s > 0:
            out += np.where(ok, h / s, 0.0)
    return np.where(ok, out, np.nan)


def _acf(p: np.ndarray, max_lag: int) -> np.ndarray:
    """Normalised autocorrelation of ``p`` (NaN rows skipped), each lag over its own overlap."""
    w = np.isfinite(p)
    x = np.where(w, p - np.nanmean(p), 0.0) if w.any() else np.zeros_like(p)
    wf = w.astype(float)
    n = len(p)
    max_lag = min(max_lag, n - 2)
    out = np.full(max_lag + 1, np.nan)
    for k in range(max_lag + 1):
        c = (wf[: n - k] * wf[k:]).sum()
        if c >= 10:
            out[k] = (x[: n - k] * x[k:]).sum() / c
    return out / out[0] if out[0] > 0 else out * np.nan


def floor_period(profile: np.ndarray, dz: float = 0.1, lag_min: float = 1.2,
                 lag_max: float = 9.0, frac: float = 0.85) -> PeriodFit:
    """Floor period of a band profile: the shortest autocorrelation peak in ``lag_min`` ..
    ``lag_max`` reaching ``frac`` x the strongest one, refined by a parabola through it and its
    neighbours. ``alt_m``: the strongest other peak. ``octave_flag``: the period lies outside
    :data:`FLOOR_PRIOR_M` while its half (or double) lies inside."""
    nan = PeriodFit(math.nan, math.nan, math.nan, False)
    k0, k1 = max(1, int(math.floor(lag_min / dz))), int(math.ceil(lag_max / dz))
    a = _acf(np.asarray(profile, float), k1 + 1)
    if not np.isfinite(a).any():
        return nan
    k1 = min(k1, len(a) - 2)
    peaks = [k for k in range(max(k0, 1), k1 + 1)
             if np.isfinite(a[k - 1:k + 2]).all() and a[k] >= a[k - 1] and a[k] > a[k + 1]
             and a[k] > 0]
    if not peaks:
        return nan
    top = max(a[k] for k in peaks)
    k = next(k for k in peaks if a[k] >= frac * top)
    y0, y1, y2 = a[k - 1], a[k], a[k + 1]
    den = y0 - 2 * y1 + y2
    off = 0.5 * (y0 - y2) / den if den < 0 else 0.0
    period = (k + float(np.clip(off, -0.5, 0.5))) * dz
    others = [j for j in peaks if abs(j - k) > 2]
    alt = max(others, key=lambda j: a[j]) * dz if others else math.nan
    lo, hi = FLOOR_PRIOR_M
    octave = (period > hi and lo <= period / 2 <= hi) or (period < lo and lo <= 2 * period <= hi)
    return PeriodFit(period, float(y1), alt, bool(octave))


# --------------------------------------------------------------------------- per building

def _px_per_floor(f_px: float, period_m: float, d_m: float, elev_deg: float, pitch_deg: float):
    e, c = math.radians(elev_deg), math.radians(elev_deg - pitch_deg)
    return f_px * period_m * math.cos(e) ** 2 / (d_m * math.cos(c) ** 2)


def _instance_mask(pano, pose, instances, cols, d, z):
    """Rows of the column's dominant instance (the one at the facade's middle height)."""
    rows = np.round(_rows_at(pano, pose, z[:, None], d[None, :]))
    ok = np.isfinite(rows) & (rows >= 0) & (rows <= pano.height - 1)
    lab = np.where(ok, instances[np.where(ok, rows, 0).astype(int), cols[None, :]], 0)
    mid = lab[len(z) // 4: 3 * len(z) // 4]
    vals, cnt = np.unique(mid[mid > 0], return_counts=True)
    return (lab == vals[np.argmax(cnt)]) if len(vals) else np.ones_like(ok)


def estimate_floors(pano: fd.Pano, pose: fd.PanoPose, m: fd.Measured, ring_xy: np.ndarray,
                    gray: np.ndarray | None = None, instances: np.ndarray | None = None,
                    kind: str = "residential", dz: float = 0.1, min_cols: int = 6,
                    trim: float = 0.04) -> FloorEstimate | None:
    """Storeys of one measured footprint (see module docstring), or None when too few of its
    columns hit a wall of ``ring_xy`` (``fd._local(pano.lat, pano.lon, ring)``). ``gray``:
    the pano's intensity (default: the RGB mean); ``instances``: building instance labels
    (``building_instances``), which keep the rows to the facade's own instance; ``kind``:
    the period's plausible range (:data:`KIND_RANGE_M`). The top and bottom ``trim`` of the
    extent are left out of the profile (roofline, ground or nearer roof edges)."""
    if gray is None:
        gray = pano.rgb.astype(np.float32).mean(axis=2)
    cols = np.arange(int(m.x0), int(m.x1) + 1)
    d, _inc = facade_ranges(pano, pose, ring_xy, cols)
    keep = np.isfinite(d)
    if keep.sum() < min_cols:
        return None
    cols, d = cols[keep], d[keep]
    e_top, e_bot = (float(_e) for _e in fd._elev_of(pano, pose, [m.top_row, m.bottom_row]))
    z_top = float(np.median(d * np.tan(np.radians(e_top))))
    z_bot = float(np.median(d * np.tan(np.radians(e_bot))))
    extent = z_top - z_bot
    dmed = float(np.median(d))
    e_mid = math.degrees(math.atan2(0.5 * (z_top + z_bot), dmed))
    pitch = pano.pitch_deg + pose.pitch_fix_deg
    z_lo, z_hi = z_bot + trim * extent, z_top - trim * extent
    z, R, valid = rectify_columns(pano, pose, gray, cols, d, z_lo, z_hi, dz)
    if instances is not None:
        valid &= _instance_mask(pano, pose, instances, cols, d, z)
    fit = floor_period(band_profile(R, valid, dz=dz, cols=cols), dz)

    def out(accepted, reason, spread=math.nan):
        P = fit.period_m
        good = np.isfinite(P) and P > 0
        nv = extent / P if good else math.nan
        n_fl = (max(1, int(round((extent - 0.4 * P) / P)))
                if good and m.base_visible else None)
        lo, hi = KIND_RANGE_M.get(kind, FLOOR_PRIOR_M)
        return FloorEstimate(
            nv, n_fl, not m.base_visible, P, fit.alt_m, fit.acf_peak, fit.octave_flag,
            _px_per_floor(pano.f_px, P, dmed, e_mid, pitch) if good else math.nan, spread,
            extent, dmed, bool(good and lo <= P <= hi),
            dmed * NOMINAL_FLOOR_M / P if good else math.nan, len(cols), accepted, reason)

    if not np.isfinite(fit.period_m) or fit.acf_peak < MIN_ACF_PEAK:
        return out(False, "no floor pattern")
    P = fit.period_m
    if _px_per_floor(pano.f_px, P, dmed, e_mid, pitch) < MIN_PX_PER_FLOOR:
        return out(False, f"under {MIN_PX_PER_FLOOR:.0f} px per floor")
    if extent / P < MIN_FLOORS:
        return out(False, f"under {MIN_FLOORS:.0f} visible floors")
    # the period again in each half and five column strips, near the global one
    parts = []
    half = len(z) // 2
    parts += [(R[:half], valid[:half], cols), (R[half:], valid[half:], cols)]
    for idx in np.array_split(np.arange(len(cols)), 5):
        if len(idx) >= 3:
            parts.append((R[:, idx], valid[:, idx], cols[idx]))
    ps = [floor_period(band_profile(r, v, dz=dz, cols=c), dz, 0.75 * P, 1.35 * P).period_m
          for r, v, c in parts]
    ps = [p for p in ps if np.isfinite(p)]
    spread = max(abs(p - P) / P for p in ps) if len(ps) >= 3 else math.nan
    if not np.isfinite(spread) or spread > MAX_STRIP_SPREAD:
        return out(False, "strips disagree", spread)
    if fit.octave_flag:
        return out(False, f"octave: period {P:.1f} m or {P / 2:.1f} m", spread)
    lo, hi = KIND_RANGE_M.get(kind, FLOOR_PRIOR_M)
    note = "" if lo <= P <= hi else (
        f"; period {P:.2f} m implausible for {kind}: distance "
        f"{dmed * NOMINAL_FLOOR_M / P:.0f} m?")
    return out(True, "ok" + note, spread)


def height_from_floors(est: FloorEstimate, kind: str = "residential"):
    """``(height_m, sigma_m)`` from the storey count: floors x the kind's floor height plus the
    middle of the roof allowance. Sigma adds a half-floor count error, +-10 % on the floor
    height and the allowance's spread. With the base hidden, ``n_visible`` (a lower bound)."""
    fh = FLOOR_HEIGHT_M.get(kind, FLOOR_HEIGHT_M["residential"])
    n = est.n_floors if est.n_floors is not None else est.n_visible
    a0, a1 = ROOF_ALLOWANCE_M
    h = n * fh + 0.5 * (a0 + a1)
    sigma = math.sqrt((0.5 * fh) ** 2 + (0.1 * fh * n) ** 2 + ((a1 - a0) / math.sqrt(12)) ** 2)
    return float(h), float(sigma)


# --------------------------------------------------------------------------- without a footprint

#: Storeys from which a building counts as a high-rise (the user's point, 2026-10-06: half the
#: battle is telling which plots hold towers).
HIGH_RISE_FLOORS = 10


#: Faces must agree on the storey count within this share (they share the floors, not the
#: period: two faces of one tower lie at different ranges, so their periods in tan(e) differ).
MAX_FLOOR_SPREAD = 0.10
#: A floor-implied distance may exceed the one the building's visible base gives by this factor
#: (the base row is an upper bound on the range when the base is hidden behind a nearer roof).
MAX_BASE_RATIO = 1.35


@dataclass(frozen=True)
class InstanceFloors:
    """Storeys of one building instance, found without knowing which building it is."""
    instance: int
    col: int                          # centre column
    bearing_deg: float
    floors_visible: float             # extent / period: independent of distance
    dist_m: float                     # NOMINAL_FLOOR_M / period in tan(elevation)
    dist_range_m: tuple[float, float]  # over the floor heights a residential tower can have
    period_px: float
    acf_peak: float
    spread: float                     # the agreeing strips' storey-count spread
    high_rise: bool
    accepted: bool
    reason: str
    n_strips: int = 0                 # column strips that agree on the storeys
    base_dist_m: float = math.nan     # range the visible base row gives (camera height known)


def _strip_floors(pano, pose, gray, instances, instance, cols):
    """(floors, period in tan(e), acf peak, px per floor, t_hi, t_lo) of one column strip."""
    m = instances[:, cols] == instance
    rows = np.flatnonzero(m.any(1))
    if len(rows) < 8:
        return None
    r0, r1 = int(rows[0]), int(rows[-1])
    t_hi = math.tan(math.radians(float(fd._elev_of(pano, pose, r0))))
    t_lo = math.tan(math.radians(float(fd._elev_of(pano, pose, r1))))
    e_mid = math.radians(float(fd._elev_of(pano, pose, 0.5 * (r0 + r1))))
    dt = math.cos(e_mid - math.radians(pano.pitch_deg)) ** 2 / (pano.f_px * math.cos(e_mid) ** 2)
    ones = np.ones(len(cols))
    t, R, valid = rectify_columns(pano, pose, gray, cols, ones, t_lo, t_hi, dt)
    rr = np.clip(np.round(_rows_at(pano, pose, t[:, None], ones[None, :])), 0, pano.height - 1)
    valid &= instances[rr.astype(int), cols[None, :]] == instance
    R = np.where(valid, R, np.nan)
    prof = band_profile(R, valid, hp_m=40 * dt, dz=dt, cols=cols)
    fit = floor_period(prof, dz=dt, lag_min=4 * dt, lag_max=(t_hi - t_lo) / 3.0)
    if not math.isfinite(fit.period_m):
        return None
    return ((t_hi - t_lo) / fit.period_m, fit.period_m, fit.acf_peak, fit.period_m / dt, t_hi,
            t_lo, r1, prof, dt)


def _subharmonic(prof, dt, period, acf_peak, k_max, frac=0.5, min_px=MIN_PX_PER_FLOOR):
    """The largest ``k`` (2..k_max) whose period / k is also a local autocorrelation peak at least
    ``frac`` x the found one and at least ``min_px`` long, else 1."""
    lag = period / dt
    a = _acf(np.asarray(prof, float), int(lag) + 3)
    for k in range(int(k_max), 1, -1):
        j = lag / k
        if j < min_px:
            continue
        i = int(round(j))
        win = a[max(1, i - 1): i + 2]
        if np.isfinite(win).all() and len(win) == 3 and win[1] == win.max() and \
                win[1] >= frac * acf_peak:
            return k
    return 1


def instance_floors(pano: fd.Pano, pose: fd.PanoPose, gray: np.ndarray, instances: np.ndarray,
                    instance: int, min_cols: int = 6, trim: float = 0.1,
                    n_strips: int = 3, depth: np.ndarray | None = None) -> InstanceFloors | None:
    """Storeys and distance of one MobileSAM building instance, before any footprint match.

    On one column a vertical facade at range ``d`` has ``z = d * tan(e)``, so the floor bands are
    periodic in ``t = tan(e)`` with period ``floor / d``: resampling on a uniform ``t`` grid
    (:func:`rectify_columns` with ``d = 1``) gives the visible storeys as extent / period with no
    distance at all, and the distance as ``NOMINAL_FLOOR_M`` / period (+-15 %, the spread of
    floor heights). Bearing plus that distance picks the plot (:func:`match_plot`); ten or more
    storeys mark a high-rise.

    The instance is read in ``n_strips`` column strips, and accepted when at least two agree on
    the storey count within :data:`MAX_FLOOR_SPREAD`: a tower seen on a corner shows two faces at
    different ranges, so their periods differ while their storeys do not (requiring equal
    periods rejected 28 of 35 tall instances on seed_6, 2026-10-06). With the camera height known
    (``pose.camera_h_m > 0``), a floor-implied distance farther than :data:`MAX_BASE_RATIO` x the
    range the visible base row allows is refused: that is how roof equipment read as "floors" a
    few metres away shows itself."""
    m = instances == instance
    cols = np.flatnonzero(m.sum(0) >= 8)
    if len(cols) < min_cols:
        return None
    n = len(cols)
    cut = int(n * trim)
    cols = cols[cut: n - cut] if n - 2 * cut >= min_cols else cols
    b = float((pano.frame_heading[cols[len(cols) // 2]] + pose.offset_deg) % 360.0)
    k = n_strips if len(cols) >= n_strips * max(4, min_cols // 2) else 2 if len(cols) >= 8 else 1
    parts = [p for p in np.array_split(cols, k) if len(p) >= 4]
    got = [g for g in (_strip_floors(pano, pose, gray, instances, instance, p) for p in parts)
           if g is not None and g[2] >= MIN_ACF_PEAK]
    centre = int(cols[len(cols) // 2])

    def refuse(reason, floors=math.nan, p=math.nan, acf=math.nan, ppx=math.nan, spread=math.nan,
               ns=0, dbase=math.nan):
        lo, hi = KIND_RANGE_M["residential"]
        return InstanceFloors(instance, centre, b, floors, NOMINAL_FLOOR_M / p if p else math.nan,
                              (lo / p, hi / p) if p else (math.nan, math.nan), ppx, acf, spread,
                              False, False, reason, ns, dbase)

    if not got:
        return refuse("no floor pattern")
    # the largest group of strips agreeing on the storeys
    best = []
    for g in got:
        grp = [h for h in got if abs(h[0] - g[0]) <= MAX_FLOOR_SPREAD * g[0]]
        if len(grp) > len(best):
            best = grp
    floors = float(np.median([g[0] for g in best]))
    p = float(np.median([g[1] for g in best]))
    acf = float(np.median([g[2] for g in best]))
    ppx = float(np.median([g[3] for g in best]))
    spread = (max(g[0] for g in best) - min(g[0] for g in best)) / floors if len(best) > 1 else math.nan
    d = NOMINAL_FLOOR_M / p
    d_base = math.nan
    base_seen = False
    if pose.camera_h_m > 0:
        r_b = float(np.median([g[6] for g in best]))
        e_b = float(fd._elev_of(pano, pose, r_b))
        if e_b < -0.5:
            d_base = pose.camera_h_m / math.tan(math.radians(-e_b))
            # the base row bounds the range, so a period k x too long (every k-th floor) is
            # allowed back when the profile repeats at period / k too (seed_6's tallest tower
            # read 15 floors at 86 m: every third of ~45, its base 339 m out; 2026-10-06)
            k_max = min(5, int(MAX_BASE_RATIO * d_base / d))
            if k_max >= 2:
                ks = [_subharmonic(g[7], g[8], g[1], g[2], k_max) for g in best]
                sub = int(np.median(ks))
                if sub > 1:
                    floors, p, ppx, d = floors * sub, p / sub, ppx / sub, d * sub
            # ground (not building, not sky) just under the instance: its base is in view, so the
            # base row is the range, not only a bound on it
            rb = int(round(r_b))
            below = pano.labels[min(pano.height - 1, rb + 2):min(pano.height, rb + 8), cols]
            if below.size:
                ground = (below >= 0) & ~np.isin(below, fd.BUILDING_CLASSES) & (below != fd.SKY_CLASS)
                base_seen = bool(ground.mean() >= 0.6)
            if not base_seen and depth is not None:
                # what lies under the mask is not nearer (Depth Anything inverse depth): the
                # building's own podium, not an occluder, so its base is about there (seed_6's
                # tallest tower, 2026-10-06: a 2-4 floor facade module read as 15 floors at 86 m
                # with its podium 339 m out)
                lo_r, hi_r = max(0, rb - 6), min(pano.height, rb + 8)
                inside = depth[lo_r:rb + 1, cols]
                under = depth[min(pano.height - 1, rb + 2):hi_r, cols]
                if inside.size and under.size:
                    base_seen = bool(np.nanmedian(under) <= 1.1 * np.nanmedian(inside))
    reason = []
    if len(best) < 2 and len(got) >= 1 and k > 1:
        reason.append("one strip only" if len(got) == 1 else f"strips disagree ({len(got)} read)")
    if ppx < MIN_PX_PER_FLOOR:
        reason.append(f"{ppx:.1f} px per floor")
    if floors < 3:
        reason.append(f"{floors:.1f} floors")
    if math.isfinite(d_base) and (d > MAX_BASE_RATIO * d_base
                                  or (base_seen and d < d_base / MAX_BASE_RATIO)):
        # seen base: both ways (seed_6, 2026-10-06: the tallest tower read 15 floors at 86 m
        # with its base on the street 339 m out; a port blob read 3 floors at 119 m)
        reason.append(f"floors put it {d:.0f} m away, its base {d_base:.0f} m")
    lo, hi = KIND_RANGE_M["residential"]
    return InstanceFloors(instance, centre, b, floors, float(d), (lo / p, hi / p), ppx, acf,
                          float(spread), bool(floors >= HIGH_RISE_FLOORS), not reason,
                          "; ".join(reason) or "ok", len(best), float(d_base))


def match_plot(est: InstanceFloors, pano: fd.Pano, pose: fd.PanoPose, rings_xy: list,
               tol: float = 0.2) -> list[tuple[int, float]]:
    """Footprints whose wall the instance's centre bearing hits within the floor-implied
    distance range (widened by ``tol``): ``[(index, range_m)]``, best first (nearest to
    ``est.dist_m`` in log range). ``rings_xy``: footprints in local metres (``fd._local``)."""
    if not (est.accepted and math.isfinite(est.dist_m)):
        return []
    lo, hi = est.dist_range_m[0] * (1 - tol), est.dist_range_m[1] * (1 + tol)
    out = []
    for i, ring in enumerate(rings_xy):
        d, _inc = facade_ranges(pano, pose, ring, [est.col], max_incidence_deg=89.0)
        if np.isfinite(d[0]) and lo <= d[0] <= hi:
            out.append((i, float(d[0])))
    return sorted(out, key=lambda t: abs(math.log(t[1] / est.dist_m)))
