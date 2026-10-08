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
#: A floor-implied distance may differ from the one the building's visible base (or its matched
#: footprint) gives by this factor: 4.5 m / 3.1 m, the tallest floor :data:`FLOOR_PRIOR_M` allows
#: over the nominal one. Cartagena's luxury towers really are 4.3-4.4 m a floor (Allure 190 m / 43,
#: Portomarine 188 m / 44); at 1.35 seed_5's waterfront towers (base 290-340 m, floors 210-235 m)
#: were all refused (2026-10-06).
MAX_BASE_RATIO = 1.45
#: A strip's period may be read k x too long (every k-th floor, a 2-3 floor facade module) when its
#: autocorrelation also peaks at period / k with at least this share of the found peak.
HARMONIC_FRAC = 0.5
#: Storey heights a matched footprint may imply (period in tan(e) x its range). Distance from the
#: geometry, not an assumed floor: Cartagena's towers are 4.3-4.8 m a floor (Allure 190 m / 43,
#: Portomarine 188 / 44), which the 3.1 m nominal floor and the 1.45 ratio (at most 4.5 m) refused
#: (seed_5's tower left of centre, 2026-10-07: 36.5 floors at an implied 220 m, base 338 m).
STOREY_M = (2.7, 5.0)
#: A footprint covers an instance when its walls are hit in at least this share of its columns.
MIN_COVER = 0.5
#: A matched footprint may lie this factor beyond the range the instance's lowest row gives (mask
#: bleed; a hidden base lies nearer, never farther), and, with the base seen, this factor nearer.
BASE_TOL = 1.2
#: An instance at least this many rows tall (median per column) per column of width: a flatter
#: one is a strip of road or roof, not a facade (seed_6 inst 288: 18 rows over 276 columns, 0.065).
MIN_ASPECT = 0.1


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
    storey_m: float = math.nan        # period x the matched footprint's range (NaN: no match)
    plot: int | None = None           # the matched footprint (index into rings_xy)
    plot_dist_m: float = math.nan     # its median first-wall range over the instance's columns
    #: footprints that cover the instance's columns at a plausible storey height, nearest first:
    #: ``(index, range_m, k, storey_m)`` (``k``: the period read k x too long)
    plot_cands: tuple = ()
    covering: tuple = ()              # every footprint covering the columns, nearest first
    extent_px: float = 0.0            # median rows of the instance per column
    members: tuple = ()               # instances counted together (pano_floors); () = itself
    #: ground (not building, not sky) under the mask: the base is in view. Otherwise
    #: ``floors_visible`` may count only the floors above an occluder: a lower bound. The depth
    #: "own podium" test (used above to bound the range) does not set it: Depth Anything's under /
    #: inside ratio was 0.98-1.03 on all 27 seed_6/seed_7 instances checked, the 5 the user judged
    #: partial (seed_6 33/56/139/269, seed_7 107) and the complete ones alike (2026-10-07)
    base_seen: bool = False

    @property
    def lower_bound(self) -> bool:
        """The storey count is "at least": the base is hidden (user review 2026-10-07: 7 of 16
        judged labels were too few floors, all with the base or lower floors hidden)."""
        return not self.base_seen


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


def _peak_near(a: np.ndarray, j: float, tol: float) -> float:
    """The highest local maximum of ``a`` within ``tol`` lags of ``j`` (NaN if none)."""
    best = math.nan
    for i in range(max(1, int(math.floor(j - tol))), int(math.ceil(j + tol)) + 1):
        if i + 1 >= len(a):
            break
        if np.isfinite(a[i - 1:i + 2]).all() and a[i] >= a[i - 1] and a[i] > a[i + 1] and a[i] > 0 \
                and not best >= a[i]:
            best = float(a[i])
    return best


def _subharmonic(prof, dt, period, acf_peak, k_max, frac=HARMONIC_FRAC, min_px=MIN_PX_PER_FLOOR):
    """The largest ``k`` (2..k_max) whose period / k is also a local autocorrelation peak at least
    ``frac`` x the found one and at least ``min_px`` long, else 1. The peak may sit a lag (or 4 %)
    off period / k: the period is itself refined between lags, and rounding it to the nearest one
    missed seed_6's 19-floor tower whose half-period peak was one lag up (2026-10-06)."""
    lag = period / dt
    a = _acf(np.asarray(prof, float), int(lag) + 3)
    for k in range(int(k_max), 1, -1):
        j = lag / k
        if j < min_px:
            continue
        if _peak_near(a, j, max(1.0, 0.04 * j)) >= frac * acf_peak:
            return k
    return 1


def _strip_candidates(g, k_max=4, frac=HARMONIC_FRAC, min_px=MIN_PX_PER_FLOOR):
    """``[(floors, period, acf, px per floor, k)]`` one strip may mean: its reading (k = 1) and
    each k x finer period its autocorrelation also peaks at (see :func:`_subharmonic`)."""
    out = [(g[0], g[1], g[2], g[3], 1)]
    a = _acf(np.asarray(g[7], float), int(g[3]) + 3)
    for k in range(2, k_max + 1):
        j = g[3] / k
        if j < min_px:
            break
        if _peak_near(a, j, max(1.0, 0.04 * j)) >= frac * g[2]:
            out.append((g[0] * k, g[1] / k, g[2], g[3] / k, k))
    return out


def instance_floors(pano: fd.Pano, pose: fd.PanoPose, gray: np.ndarray, instances: np.ndarray,
                    instance: int, min_cols: int = 6, trim: float = 0.1,
                    n_strips: int = 3, depth: np.ndarray | None = None,
                    rings_xy: list | None = None) -> InstanceFloors | None:
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
    few metres away shows itself.

    Each strip also counts at the k x finer periods its autocorrelation supports
    (:func:`_strip_candidates`), so a strip that locked onto every second floor still agrees with
    the others; the group needing the fewest such multiples wins. With ``rings_xy`` (footprints in
    local metres, ``fd._local``) the floor-implied range must also lie within
    :data:`MAX_BASE_RATIO` of a footprint wall on the instance's bearing, base seen or not; when
    it is too near for all of them a finer period the strips support is tried, else the reading
    is refused (seed_5, 2026-10-06: "3 floors at 58 m" with every wall on that bearing past
    450 m). On the five Cartagena seeds these changes and the 1.45 ratio took the plot-matched
    readings from 25 to 46 (seed_6 16 -> 32) and refused 7 unmatched ones (2026-10-06)."""
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
    # the largest group of strips agreeing on the storeys, each strip also read at the finer
    # periods its autocorrelation supports: one strip reading every second floor (a 2-floor
    # module, or the strongest peak a multiple) no longer outvotes the others (seed_6, 2026-10-06)
    cands = [(c, si) for si, g in enumerate(got) for c in _strip_candidates(g)]
    best, key = [], None
    for c, _si in cands:
        grp = {}
        for h, sj in cands:
            if abs(h[0] - c[0]) <= MAX_FLOOR_SPREAD * c[0] and (
                    sj not in grp or abs(h[0] - c[0]) < abs(grp[sj][0][0] - c[0])):
                grp[sj] = (h, sj)
        kk = (len(grp), -sum(h[4] for h, _ in grp.values()),
              float(np.median([h[2] for h, _ in grp.values()])))
        if key is None or kk > key:
            best, key = list(grp.values()), kk
    strips = [got[sj] for _h, sj in best]           # the agreeing strips' raw readings
    mult = [h[4] for h, _sj in best]                # and the k each is read at
    best = [h for h, _sj in best]
    floors = float(np.median([g[0] for g in best]))
    p = float(np.median([g[1] for g in best]))
    acf = float(np.median([g[2] for g in best]))
    ppx = float(np.median([g[3] for g in best]))
    spread = (max(g[0] for g in best) - min(g[0] for g in best)) / floors if len(best) > 1 else math.nan
    d = NOMINAL_FLOOR_M / p

    def finer(limit_m):
        """The agreed k (median over the strips) by which the period is too long, given that
        the range may be up to ``limit_m``."""
        k_max = min(5, int(limit_m / d))
        if k_max < 2:
            return 1
        return int(np.median([_subharmonic(g[7], g[8], g[1] / m_, g[2], k_max)
                              for g, m_ in zip(strips, mult, strict=True)]))

    d_base = math.nan
    base_seen = base_ground = False
    sub_base = 1
    if pose.camera_h_m > 0:
        r_b = float(np.median([g[6] for g in strips]))
        e_b = float(fd._elev_of(pano, pose, r_b))
        if e_b < -0.5:
            d_base = pose.camera_h_m / math.tan(math.radians(-e_b))
            # the base row bounds the range, so a period k x too long (every k-th floor) is
            # allowed back when the profile repeats at period / k too (seed_6's tallest tower
            # read 15 floors at 86 m: every third of ~45, its base 339 m out; 2026-10-06)
            sub_base = finer(MAX_BASE_RATIO * d_base)
            # ground (not building, not sky) just under the instance: its base is in view, so the
            # base row is the range, not only a bound on it
            rb = int(round(r_b))
            below = pano.labels[min(pano.height - 1, rb + 2):min(pano.height, rb + 8), cols]
            if below.size:
                ground = (below >= 0) & ~np.isin(below, fd.BUILDING_CLASSES) & (below != fd.SKY_CLASS)
                base_seen = bool(ground.mean() >= 0.6)
            base_ground = base_seen
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
    # footprints covering the instance's columns: their range is the geometry's, and the storey
    # height it implies (period x range) is what must be plausible (2026-10-07)
    cover = _covering(pano, pose, rings_xy, cols) if rings_xy is not None else []
    pcands = []
    if cover:
        ks = [1] + [kk for kk in range(2, 5) if _supports(strips, mult, kk)]
        d_occ = _occluder_range(pano, pose, instances, m, cols)
        for i, dd in cover:
            # unblocked: whatever stands under the mask down to the ground is in front of the
            # building (or its own podium), so a footprint nearer than that ground is not it
            # (seed_6: "16 fl, OSM 2", a 2-floor plot at 161 m in front of a pool deck whose
            # ground is ~200 m out)
            if dd < d_occ / BASE_TOL:
                continue
            # only ground under the mask bounds the range from below: the building's own podium
            # (base_seen from depth) puts the lowest row above the ground, so it reads too far
            # (seed_5's tower on its podium: base 346 m, its footprint 272 m)
            if math.isfinite(d_base) and (dd > BASE_TOL * d_base
                                          or (base_ground and dd < d_base / BASE_TOL)):
                continue
            for kk in ks:
                s = p / kk * dd
                if STOREY_M[0] <= s <= STOREY_M[1]:
                    pcands.append((i, dd, kk, s))
                    break
    plot, plot_d, storey = None, math.nan, math.nan
    if pcands:
        # the nearest covering footprint that fits: a farther one never wins over a nearer one
        plot, plot_d, kk, storey = pcands[0]
        floors, p, ppx, d = floors * kk, p / kk, ppx / kk, plot_d
    elif cover:
        dd = cover[0][1]
        reason.append(f"no covering footprint at {STOREY_M[0]}-{STOREY_M[1]} m a floor "
                      f"(nearest {dd:.0f} m: {p * dd:.1f} m)")
    else:
        if sub_base > 1:
            floors, p, ppx, d = floors * sub_base, p / sub_base, ppx / sub_base, d * sub_base
        if math.isfinite(d_base) and (d > MAX_BASE_RATIO * d_base
                                      or (base_seen and d < d_base / MAX_BASE_RATIO)):
            # seen base: both ways (seed_6, 2026-10-06: the tallest tower read 15 floors at 86 m
            # with its base on the street 339 m out; a port blob read 3 floors at 119 m)
            reason.append(f"floors put it {d:.0f} m away, its base {d_base:.0f} m")
        elif rings_xy is not None:
            # footprints on the bearing: the floor-implied range must land on one of their walls,
            # hidden base or not (seed_5, 2026-10-06: a slim tower read 3 floors at 58 m, every
            # wall on its bearing hundreds of metres out). Too near for all of them: the period
            # may be k x too long, so the finer periods the strips support are tried first.
            r = _bearing_ranges(pano, pose, rings_xy, centre)
            if len(r) and not _near_any(d, r):
                sub = finer(MAX_BASE_RATIO * float(r.max()))
                if sub > 1 and _near_any(d * sub, r):
                    floors, p, ppx, d = floors * sub, p / sub, ppx / sub, d * sub
                else:
                    near = float(r[np.argmin(np.abs(np.log(r / d)))])
                    reason.append(f"floors put it {d:.0f} m away, no footprint there "
                                  f"(nearest {near:.0f} m)")
    if ppx < MIN_PX_PER_FLOOR:
        reason.append(f"{ppx:.1f} px per floor")
    if floors < 3:
        reason.append(f"{floors:.1f} floors")
    lo, hi = KIND_RANGE_M["residential"]
    ext = float(np.median(m[:, cols].sum(0)))
    if ext < MIN_ASPECT * len(cols):
        # a flat strip, not a facade: seed_6 inst 288, "3 fl" on a road (18 rows over 276
        # columns; user review 2026-10-07); the flattest building accepted was 0.14
        reason.append(f"flat strip ({ext:.0f} rows over {len(cols)} columns)")
    return InstanceFloors(instance, centre, b, floors, float(d), (lo / p, hi / p), ppx, acf,
                          float(spread), bool(floors >= HIGH_RISE_FLOORS), not reason,
                          "; ".join(reason) or "ok", len(best), float(d_base), float(storey),
                          plot, float(plot_d), tuple(pcands), tuple(i for i, _ in cover), ext,
                          base_seen=bool(base_ground))


#: Ground under a mask: not one of these (ADE20K wall, building, house, skyscraper, tower, tree,
#: plant, palm) and not sky.
_STACK_CLASSES = tuple(fd.BUILDING_CLASSES) + (0, 4, 17, 72)


def _occluder_range(pano, pose, instances, m, cols, look: int = 6, gap: int = 3) -> float:
    """Range of the ground under the mask ``m``, through what stands right below it: per column,
    the first other instance within ``look`` rows under the mask's lowest row, followed down to
    its end (gaps up to ``gap`` rows); ground there instead: the mask's own base. The nearest
    end row's range, ``camera_h / tan(-e)``; 0 when unknown (no camera height, nothing usable below,
    or above the horizon). Only the instance right below counts: from above, the buildings in
    front of it run on for hundreds of rows."""
    if not pose.camera_h_m > 0:
        return 0.0
    H = pano.height
    ends = []
    for c in cols:
        r = np.flatnonzero(m[:, c])
        if not len(r):
            continue
        r0 = int(r[-1]) + 1
        below = instances[r0:min(H, r0 + look), c]
        own = instances[r[-1], c]
        hit = np.flatnonzero((below > 0) & (below != own))
        if not len(hit):
            lab = pano.labels[r0:min(H, r0 + look), c]
            if len(lab) and np.mean((lab >= 0) & ~np.isin(lab, _STACK_CLASSES)
                                    & (lab != fd.SKY_CLASS)) >= 0.6:
                ends.append(r0)
            continue
        j, row, miss = below[hit[0]], r0 + int(hit[0]), 0
        while row + 1 < H and miss <= gap:
            row += 1
            miss = 0 if instances[row, c] == j else miss + 1
        ends.append(row - miss)
    if len(ends) < max(3, 0.3 * len(cols)):
        return 0.0
    # the nearest end (90th percentile row): columns where the mask stops on a neighbour mask
    # (two faces of one tower) end high and read too far (seed_5's tower: 350 m, podium 293 m)
    e = float(fd._elev_of(pano, pose, float(np.percentile(ends, 90))))
    return pose.camera_h_m / math.tan(math.radians(-e)) if e < -0.5 else 0.0


def _supports(strips, mult, k, frac=HARMONIC_FRAC, min_px=MIN_PX_PER_FLOOR) -> bool:
    """Whether most agreeing strips' autocorrelation also peaks at their period / ``k``."""
    ok = []
    for g, m_ in zip(strips, mult, strict=True):
        lag = g[3] / m_ / k
        if lag < min_px:
            ok.append(False)
            continue
        a = _acf(np.asarray(g[7], float), int(g[3] / m_) + 3)
        ok.append(_peak_near(a, lag, max(1.0, 0.04 * lag)) >= frac * g[2])
    return bool(ok) and sum(ok) >= 0.5 * len(ok)


_EDGE_CACHE: dict = {}


def _edges(rings_xy):
    """All footprint walls as ``(x0, y0, x1, y1)`` rows and their footprint index (cached)."""
    hit = _EDGE_CACHE.get(id(rings_xy))
    if hit is not None and hit[0] is rings_xy:
        return hit[1], hit[2]
    edges, owner = [], []
    for i, ring in enumerate(rings_xy):
        ring = np.asarray(ring, float)
        if len(ring) < 3:
            continue
        if np.allclose(ring[0], ring[-1]):
            ring = ring[:-1]
        edges.append(np.concatenate([ring, np.roll(ring, -1, axis=0)], axis=1))
        owner.append(np.full(len(ring), i))
    e = np.concatenate(edges) if edges else np.zeros((0, 4))
    own = np.concatenate(owner) if owner else np.zeros(0, int)
    _EDGE_CACHE.clear()
    _EDGE_CACHE[id(rings_xy)] = (rings_xy, e, own)
    return e, own


def _covering(pano, pose, rings_xy, cols, n_samp: int = 15, min_cover: float = MIN_COVER):
    """``[(index, range_m)]`` of the footprints whose walls the bearings of at least
    ``min_cover`` of ``cols`` hit (``range_m``: their median first-wall range), nearest first."""
    e, own = _edges(rings_xy)
    if not len(e):
        return []
    cols = np.asarray(cols)
    cs = cols[np.unique(np.linspace(0, len(cols) - 1, min(n_samp, len(cols))).astype(int))]
    bb = np.radians((pano.frame_heading[cs] + pose.offset_deg) % 360.0)
    ux, uy = np.sin(bb)[:, None], np.cos(bb)[:, None]
    px, py = e[None, :, 0], e[None, :, 1]
    ex, ey = e[None, :, 2] - px, e[None, :, 3] - py
    den = ux * ey - uy * ex
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (px * ey - py * ex) / den
        s = (px * uy - py * ux) / den
    hit = (np.abs(den) > 1e-12) & (t > 0) & (s >= 0) & (s <= 1)
    cand = np.unique(own[hit.any(0)])
    t = np.where(hit, t, np.inf)
    out = []
    for i in cand:
        r = t[:, own == i].min(1)
        fin = np.isfinite(r)
        if fin.mean() >= min_cover:
            out.append((int(i), float(np.median(r[fin]))))
    return sorted(out, key=lambda q: q[1])


def _bearing_ranges(pano, pose, rings_xy, col) -> np.ndarray:
    """Range to each footprint's first wall along column ``col``'s bearing (misses dropped)."""
    edges, owner = [], []
    for i, ring in enumerate(rings_xy):
        ring = np.asarray(ring, float)
        if len(ring) < 3:
            continue
        if np.allclose(ring[0], ring[-1]):
            ring = ring[:-1]
        edges.append(np.concatenate([ring, np.roll(ring, -1, axis=0)], axis=1))
        owner.append(np.full(len(ring), i))
    if not edges:
        return np.zeros(0)
    e, own = np.concatenate(edges), np.concatenate(owner)
    bb = math.radians((pano.frame_heading[col] + pose.offset_deg) % 360.0)
    ux, uy = math.sin(bb), math.cos(bb)
    px, py, ex, ey = e[:, 0], e[:, 1], e[:, 2] - e[:, 0], e[:, 3] - e[:, 1]
    den = ux * ey - uy * ex
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (px * ey - py * ex) / den
        s = (px * uy - py * ux) / den
    hit = (np.abs(den) > 1e-12) & (t > 0) & (s >= 0) & (s <= 1)
    if not hit.any():
        return np.zeros(0)
    t, own = t[hit], own[hit]
    first = np.full(own.max() + 1, np.inf)
    np.minimum.at(first, own, t)
    return first[np.isfinite(first)]


def _near_any(d: float, ranges: np.ndarray) -> bool:
    return bool(np.any((ranges / MAX_BASE_RATIO <= d) & (d <= ranges * MAX_BASE_RATIO)))


def match_plot(est: InstanceFloors, pano: fd.Pano, pose: fd.PanoPose, rings_xy: list,
               tol: float = 0.2) -> list[tuple[int, float]]:
    """Footprints whose wall the instance's centre bearing hits within the floor-implied
    distance range (widened by ``tol``): ``[(index, range_m)]``, best first (nearest to
    ``est.dist_m`` in log range). ``rings_xy``: footprints in local metres (``fd._local``).

    An instance :func:`instance_floors` already matched (``est.plot``: the nearest footprint
    covering its columns at a plausible storey height) gives that plot first, then its other
    candidates."""
    if not (est.accepted and math.isfinite(est.dist_m)):
        return []
    if est.plot is not None:
        rest = [(c[0], float(c[1])) for c in est.plot_cands if c[0] != est.plot]
        return [(est.plot, float(est.plot_dist_m))] + rest
    lo, hi = est.dist_range_m[0] * (1 - tol), est.dist_range_m[1] * (1 + tol)
    out = []
    for i, ring in enumerate(rings_xy):
        d, _inc = facade_ranges(pano, pose, ring, [est.col], max_incidence_deg=89.0)
        if np.isfinite(d[0]) and lo <= d[0] <= hi:
            out.append((i, float(d[0])))
    return sorted(out, key=lambda t: abs(math.log(t[1] / est.dist_m)))


# --------------------------------------------------------------------------- one pano, all instances

def _on_plot(est: InstanceFloors, cand) -> InstanceFloors:
    """``est`` re-read against the plot candidate ``cand`` (index, range, k, storey)."""
    import dataclasses

    k0 = next((c[2] for c in est.plot_cands if c[0] == est.plot), 1)
    i, dd, kk, s = cand
    floors = est.floors_visible / k0 * kk
    p = est.storey_m / est.plot_dist_m * k0 / kk if est.plot is not None else s / dd
    lo, hi = KIND_RANGE_M["residential"]
    return dataclasses.replace(est, floors_visible=floors, dist_m=float(dd), plot=int(i),
                               plot_dist_m=float(dd), storey_m=float(s),
                               period_px=est.period_px * k0 / kk, dist_range_m=(lo / p, hi / p),
                               high_rise=bool(floors >= HIGH_RISE_FLOORS))


def _touch(instances, a: int, b: int, grow: int = 4, min_px: int = 20) -> bool:
    """Whether masks ``a`` and ``b`` touch (``a`` dilated by ``grow`` px meets ``min_px`` of ``b``)."""
    from scipy.ndimage import binary_dilation

    ra, ca = np.nonzero(instances == a)
    if not len(ra):
        return False
    r0, r1 = max(0, ra.min() - grow), min(instances.shape[0], ra.max() + grow + 1)
    c0, c1 = max(0, ca.min() - grow), min(instances.shape[1], ca.max() + grow + 1)
    sub = instances[r0:r1, c0:c1]
    near = binary_dilation(sub == a, iterations=grow)
    return int((near & (sub == b)).sum()) >= min_px


def pano_floors(pano: fd.Pano, pose: fd.PanoPose, gray: np.ndarray, instances: np.ndarray, ids,
                depth: np.ndarray | None = None, rings_xy: list | None = None,
                levels: list | None = None, merge: bool = True):
    """Storeys of every instance in ``ids`` with one plot per instance and one instance per plot:
    ``[(InstanceFloors, (plot, range_m) | None)]`` for the accepted ones (2026-10-07).

    - Each instance takes the nearest footprint covering its columns at a plausible storey
      height (:func:`instance_floors`). Several wanting one plot: the plot goes to the instance
      that explains it best, the largest vertical extent, scaled down by how far its floors are
      from the plot's ``levels`` (OSM ``building:levels`` per ring, None if untagged) when given;
      the others fall back to their next candidate (stable matching). seed_5: the podium under
      the tower left of centre took the tower's plot ("5 fl, OSM 42").
    - A loser left without a plot that touches the winner of its first choice is the same
      building in two masks (a tower on its podium, two faces): with ``merge`` they are counted
      together, re-read as one instance (``members``)."""
    import dataclasses

    ests = {}
    for i in ids:
        e = instance_floors(pano, pose, gray, instances, int(i), depth=depth, rings_xy=rings_xy)
        if e is not None and e.accepted:
            ests[int(i)] = e
    if rings_xy is None:
        return [(e, match_plot(e, pano, pose, []) or None) for e in ests.values()]

    def score(e, cand):
        f = e.floors_visible / next((c[2] for c in e.plot_cands if c[0] == e.plot), 1) * cand[2]
        lv = levels[cand[0]] if levels is not None else None
        w = math.exp(-2.0 * abs(math.log(f / lv))) if lv and lv > 0 and f > 0 else 1.0
        return e.extent_px * w

    def assign(ests):
        nxt = {i: 0 for i in ests}
        owner = {}
        free = sorted(ests, key=lambda i: -ests[i].extent_px)
        while free:
            i = free.pop(0)
            cands = ests[i].plot_cands
            if nxt[i] >= len(cands):
                continue
            c = cands[nxt[i]]
            nxt[i] += 1
            j = owner.get(c[0])
            if j is None:
                owner[c[0]] = (i, c)
            elif score(ests[i], c) > score(ests[j[0]], j[1]):
                owner[c[0]] = (i, c)
                free.append(j[0])
            else:
                free.append(i)
        return {i: c for i, c in owner.values()}

    got = assign(ests)
    if merge:
        first = {e.plot: i for i, e in ests.items() if i in got and got[i][0] == e.plot}
        groups = {}
        for i, e in ests.items():
            w = first.get(e.plot)
            if i not in got and e.plot is not None and w is not None and _touch(instances, w, i):
                groups.setdefault(w, [w]).append(i)
        for w, mem in groups.items():
            lab = np.where(np.isin(instances, mem), w, instances)
            u = instance_floors(pano, pose, gray, lab, w, depth=depth, rings_xy=rings_xy)
            c = next((c for c in (u.plot_cands if u is not None and u.accepted else ())
                      if c[0] == got[w][0]), None)
            if c is not None:
                ests[w] = dataclasses.replace(_on_plot(u, c), members=tuple(sorted(mem)))
                got[w] = c
                for i in mem[1:]:
                    ests.pop(i, None)
    out = []
    for i, e in ests.items():
        if i in got:
            e = _on_plot(e, got[i]) if got[i][0] != e.plot else e
            out.append((e, (e.plot, e.plot_dist_m)))
        elif e.plot is None:
            out.append((e, None))
    return out
