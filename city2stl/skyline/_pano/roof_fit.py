"""skyline._pano.roof_fit — building heights from a drone above the roofs (T43, 2026-10-06).

``footprint_detect.measure_footprints`` reads a building's top as the elevation of its run's top
row at the footprint's nearest vertex. That holds for a camera below the roofs. Cartagena
seed_6 hovers ~185 m over Bocagrande looking down (2026-10-06): most roofs sit below the
camera, so the topmost row of a building is its *far* roof edge, and the run up a facade
crosses the crease onto the roof, where Depth Anything and MobileSAM both change. On the nine
OSM-tagged towers it read a median 45 % off, low (Portomarine 113 m against a 188 m tag).

Per column, the ray from the camera crosses the footprint between ``d_in`` and ``d_out``
(:func:`ray_intervals`). A flat roof at ``H`` ends the building's silhouette at

- ``atan((H - h) / d_out)``, the far roof edge, when ``H < h`` (seen from above);
- ``atan((H - h) / d_in)``, the near roof edge, when ``H >= h``;

so the observed top inverts in closed form (:func:`invert_top`), no search. The top is looked
for only inside the band of rows a 3-260 m roof can reach (:func:`observed_top`), and what lies
above it ranks the column: sky or ground above is trusted, another building (instance or depth
step) counts, a nearer surface above only bounds the height from below and is left out.
The columns aggregate to their 35th percentile (a set-back penthouse raises the middle columns;
the median is kept beside it).

:func:`check_pose_from_bases` pools the observed base rows of all measured buildings into one
camera height that uses no building height, against the pose's (flag above 5 %).

:func:`overlay_tag_rows` draws, for tagged buildings, the predicted base, crease and far-roof
rows at the tag height and where ``footprint_detect._column_run`` stops: the diagnostic that
separates a pose error (rows consistently offset from visible edges) from a detection error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import footprint_detect as fd

#: Roof heights a building can have (m): the band of rows its top may occupy.
H_RANGE_M = (3.0, 260.0)
#: What lies above the top row: trusted edges, edges that count, edges that only bound.
TRUSTED_EDGES = ("sky", "ground")
USED_EDGES = TRUSTED_EDGES + ("depth",)
CENSORED_EDGES = ("censored", "image")
#: Pixel scales below are in degrees, so they hold for the default and the hi-res panos.
GAP_DEG, LOCAL_DEG, BASE_WIN_DEG = 0.5, 1.5, 2.5


def _px(pano: fd.Pano, deg: float) -> int:
    return max(1, int(round(deg * pano.f_px * math.pi / 180.0)))


# --------------------------------------------------------------------------- geometry


def ray_intervals(ring_xy, bearings_deg) -> np.ndarray:
    """``(n, 2)`` ``[d_in, d_out]``: where the horizontal ray from the camera (origin, local
    metres east/north) along each bearing first enters and last leaves the polygon ``ring_xy``.
    NaN where the ray misses, or when the camera stands inside the footprint (odd crossings)."""
    ring = np.asarray(ring_xy, float)
    if not np.allclose(ring[0], ring[-1]):
        ring = np.vstack([ring, ring[:1]])
    p, e = ring[:-1], ring[1:] - ring[:-1]
    t = np.radians(np.atleast_1d(np.asarray(bearings_deg, float)))
    r = np.stack([np.sin(t), np.cos(t)], axis=1)
    den = r[:, None, 0] * e[None, :, 1] - r[:, None, 1] * e[None, :, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (p[None, :, 0] * e[None, :, 1] - p[None, :, 1] * e[None, :, 0]) / den
        u = (p[None, :, 0] * r[:, None, 1] - p[None, :, 1] * r[:, None, 0]) / den
    ok = (np.abs(den) > 1e-12) & (u >= 0.0) & (u <= 1.0) & (s > 0.0)
    out = np.full((len(t), 2), np.nan)
    any_ = ok.any(axis=1)
    if any_.any():
        out[any_, 0] = np.where(ok, s, np.inf).min(axis=1)[any_]
        out[any_, 1] = np.where(ok, s, -np.inf).max(axis=1)[any_]
    # an odd number of crossings (a vertex hit counts twice: u = 0 and u = 1) means the camera
    # is inside; the parity test uses half-open segments to count a vertex once
    inside = ((ok & (u < 1.0)).sum(axis=1) % 2) == 1
    out[inside] = np.nan
    return out


def top_elev(h, H, d_in, d_out):
    """Elevation (deg) of a flat roof's silhouette top: the far roof edge when the roof is below
    the camera (``H < h``), the near edge otherwise."""
    H = np.asarray(H, float)
    return np.degrees(np.arctan((H - h) / np.where(H < h, d_out, d_in)))


def invert_top(h, e_deg, d_in, d_out):
    """Roof height from the elevation of the silhouette top (inverse of :func:`top_elev`)."""
    t = np.tan(np.radians(np.asarray(e_deg, float)))
    return h + np.where(t < 0.0, d_out, d_in) * t


# --------------------------------------------------------------------------- one column


def observed_top(labels_col, inst_col, depth_col, base_row, band, *, start_row=None,
                 gap_px: int = 6, local_px: int = 12, stop_ratio: float = 0.85,
                 near_ratio: float = 1.3, far_ratio: float | None = None,
                 base_win_px: int = 0):
    """``(row, edge)`` of one building's top in one column, or None.

    Goes up from the base (or ``start_row``: the top of a nearer building in front) to the
    first building row (within ``max(gap_px, base_win_px)`` rows: a base row off by a pose
    error), then up through this building: inside one MobileSAM instance it never
    stops (the facade-roof crease changes shade and depth); at an instance change, or with no
    instances, the depth decides: a step down to under ``stop_ratio`` x the last ``local_px``
    rows' level is the roof edge against a building behind (edge ``depth``), a step up past
    ``near_ratio`` is a nearer surface above (``censored``: the true top is higher). With
    ``far_ratio`` (``d_in / d_out`` with some slack) the step also ends the run when the new
    surface is farther than any part of this footprint can be, against the level of the run's
    first rows (the wall at ``d_in``): from 185 m up, the next row of Bocagrande blocks is only
    10-20 % farther than a roof's far edge, under the local step. Gaps of up
    to ``gap_px`` non-building rows (a rooftop pool, a mast) are crossed when the building
    resumes at its own depth. A run that ends at sky is ``sky``, at any other label ``ground``;
    at the image top ``image``.

    ``band``: ``(hi, lo)`` rows the top can occupy (heights :data:`H_RANGE_M`). A run still
    going above ``hi`` merged with something taller (None); one ending below ``lo`` is no roof.
    """
    labels_col = np.asarray(labels_col)
    n = len(labels_col)
    is_b = np.isin(labels_col, fd.BUILDING_CLASSES)
    hi, lo = band
    y = min(int(round(base_row if start_row is None else min(base_row, start_row))) - 1, n - 1)
    gap = 0
    while y >= 0 and not is_b[y]:
        gap += 1
        if gap > max(gap_px, base_win_px) or y < hi:
            return None
        y -= 1
    if y < 0:
        return None
    bottom = y
    ref = None if depth_col is None else float(np.median(depth_col[max(0, y - local_px + 1):y + 1]))

    def level(y0):
        return float(np.median(depth_col[y0:min(bottom, y0 + local_px - 1) + 1]))

    def same(a, b):
        return inst_col is not None and inst_col[a] > 0 and inst_col[a] == inst_col[b]

    edge = None
    while edge is None:
        if y == 0:
            edge = "image"
            break
        if y - 1 < hi - 1:
            return None                             # taller than any roof: merged
        nb = y - 1
        if is_b[nb]:
            if not same(nb, y) and depth_col is not None:
                loc = level(y)
                if depth_col[nb] < stop_ratio * loc or (far_ratio is not None
                                                        and depth_col[nb] < far_ratio * ref):
                    edge = "depth"
                    break
                if depth_col[nb] > near_ratio * loc:
                    edge = "censored"
                    break
            y = nb
            continue
        k = nb
        while k >= 0 and not is_b[k] and nb - k < gap_px:
            k -= 1
        if k >= 0 and is_b[k] and k >= hi - 1:
            if inst_col is not None and inst_col[y] > 0 and inst_col[k] > 0:
                resume = inst_col[k] == inst_col[y]
            elif depth_col is not None:
                loc = level(y)
                resume = (stop_ratio * loc <= depth_col[k] <= near_ratio * loc
                          and (far_ratio is None or depth_col[k] >= far_ratio * ref))
            else:
                resume = True
            if resume:
                y = k
                continue
        edge = "sky" if labels_col[nb] == fd.SKY_CLASS else "ground"
    if y > lo + 1:
        return None
    return y, edge


def observed_base(labels_col, base_row: float, win_px: int):
    """Row of a building's foot near ``base_row``: the building row closest to it with a
    non-building, non-sky row right below (the street at the wall). None when not seen."""
    is_b = np.isin(labels_col, fd.BUILDING_CLASSES)
    n = len(labels_col)
    best = None
    for y in range(max(0, int(base_row) - win_px), min(n - 1, int(base_row) + win_px)):
        if is_b[y] and not is_b[y + 1] and labels_col[y + 1] != fd.SKY_CLASS and labels_col[y + 1] >= 0:
            if best is None or abs(y + 1 - base_row) < abs(best + 1 - base_row):
                best = y
    return None if best is None else float(best + 1)       # the boundary under that row


# --------------------------------------------------------------------------- footprints


@dataclass(frozen=True)
class RoofMeasured(fd.Measured):
    height_median_m: float = math.nan
    iqr_m: float = math.nan
    n_trusted: int = 0
    n_censored: int = 0
    confidence: float = 0.0
    # fusion weight factor (``footprint_detect.measurement_weight``): below 1 for a roof fit
    # that only fills in for a missing street-run reading (``elevated.measure_waterline``)
    weight_scale: float = 1.0


@dataclass(frozen=True)
class _Cand:
    dn: float
    i: int
    x0: int
    x1: int
    xy: np.ndarray


def _candidates(pano, pose, footprints, max_dist_m, min_cols):
    bu, b0 = fd.bearing_columns(pano, pose)
    W = pano.width
    out = []
    for i, fp in enumerate(footprints):
        xy = fd._local(pano.lat, pano.lon, np.asarray(fp.ring, float))
        dn = float(np.hypot(xy[:, 0], xy[:, 1]).min())
        if dn < 20.0 or dn > max_dist_m:
            continue
        bear = np.degrees(np.arctan2(xy[:, 0], xy[:, 1])) % 360.0
        rel = (bear - bear[0] + 180.0) % 360.0 - 180.0
        c0 = fd.column_of((bear[0] + rel.min()) % 360.0, bu, b0)
        c1 = fd.column_of((bear[0] + rel.max()) % 360.0, bu, b0)
        if not (np.isfinite(c0) and np.isfinite(c1)) or c1 - c0 < min_cols:
            continue
        x0, x1 = max(0, int(math.ceil(c0))), min(W - 1, int(math.floor(c1)))
        out.append(_Cand(dn, i, x0, x1, xy))
    out.sort(key=lambda c: c.dn)
    return out


def _fit(pano, pose, footprints, depth, instances, max_dist_m=3000.0, min_cols_deg=0.7,
         core=0.7, stop_ratio=0.85, near_ratio=1.3, far_slack=0.95, min_share=1 / 3,
         h_range=H_RANGE_M):
    H, W = pano.labels.shape
    h = pose.camera_h_m
    bear_col = (pano.frame_heading + pose.offset_deg) % 360.0
    gap_px, local_px, base_win = _px(pano, GAP_DEG), _px(pano, LOCAL_DEG), _px(pano, BASE_WIN_DEG)
    dz = None
    if depth is not None:
        from scipy.ndimage import median_filter

        dz = median_filter(np.asarray(depth, float), size=(5, 1))
    occ = np.full(W, float(H))                     # rows >= occ: hidden behind nearer roofs
    out, bases = [], []
    for c in _candidates(pano, pose, footprints, max_dist_m, _px(pano, min_cols_deg)):
        xs_all = np.arange(c.x0, c.x1 + 1)
        iv = ray_intervals(c.xy, bear_col[xs_all])
        n = len(xs_all)
        cut = int(n * (1.0 - core) / 2.0)
        rows = []
        for j in range(cut, n - cut):
            x = xs_all[j]
            d_in, d_out = iv[j]
            if not np.isfinite(d_in):
                continue
            base = float(fd._row_of(pano, pose, -math.degrees(math.atan2(h, d_in))))
            hi, lo = (float(r) for r in fd._row_of(pano, pose, top_elev(h, np.array(h_range[::-1]),
                                                                          d_in, d_out)))
            if occ[x] <= hi:
                continue                            # the whole band hidden
            got = observed_top(pano.labels[:, x], None if instances is None else instances[:, x],
                               None if dz is None else dz[:, x], base, (hi, lo),
                               start_row=occ[x], gap_px=gap_px, local_px=local_px,
                               stop_ratio=stop_ratio, near_ratio=near_ratio,
                               far_ratio=far_slack * d_in / d_out, base_win_px=base_win)
            base_vis = occ[x] >= base - 0.5
            if base_vis:
                yb = observed_base(pano.labels[:, x], base, base_win)
                if yb is not None:
                    bases.append((d_in, yb))
            if got is None:
                continue
            t, edge = got
            e = float(fd._elev_of(pano, pose, t))
            rows.append((t, edge, float(invert_top(h, e, d_in, d_out)), base,
                         min(base, occ[x]), base_vis, d_in))
        used = [r for r in rows if r[1] in USED_EDGES]
        if len(used) < max(3, int(min_share * max(1, n - 2 * cut))):
            continue
        hs = np.array([r[2] for r in used])
        p35, med = float(np.percentile(hs, 35)), float(np.median(hs))
        q25, q75 = np.percentile(hs, [25, 75])
        n_tr = sum(r[1] in TRUSTED_EDGES for r in used)
        top = float(np.median([r[0] for r in used]))
        bottom = float(np.median([r[4] for r in used]))
        base = float(np.median([r[3] for r in used]))
        frac = float(np.clip((bottom - top) / max(1.0, base - top), 0.0, 1.0))
        conf = (n_tr / len(used)) * min(1.0, len(used) / 20.0) * float(
            np.clip(1.0 - (q75 - q25) / max(p35, 1.0), 0.0, 1.0))
        fp = footprints[c.i]
        out.append(RoofMeasured(
            c.i, fp.name, c.x0, c.x1, top, bottom, base, float(np.median([r[6] for r in used])),
            p35, len(used), sum(r[5] for r in used) >= len(used) / 2, fp.osm_height_m, frac,
            "roof", med, float(q75 - q25), n_tr, sum(r[1] in CENSORED_EDGES for r in rows), conf))
        # nearer-first occlusion: this building's predicted silhouette top at its fitted height
        ok = np.isfinite(iv[:, 0])
        tops = fd._row_of(pano, pose, top_elev(h, p35, iv[ok, 0], iv[ok, 1]))
        occ[xs_all[ok]] = np.minimum(occ[xs_all[ok]], tops)
    return out, bases


def fit_roof_heights(pano: fd.Pano, pose: fd.PanoPose, footprints: list, depth=None,
                     instances=None, **kw) -> list[RoofMeasured]:
    """Every footprint in view measured from above, nearest first (see module docstring).

    Per core column (``core`` share of the footprint's columns): base row from ``d_in``, the
    observed top in the band (:func:`observed_top`, starting above nearer measured buildings'
    predicted silhouettes), height by :func:`invert_top`. A footprint needs ``min_share`` of its
    core columns (and 3) with a usable edge. ``height_m`` is the 35th percentile of those
    columns, ``height_median_m`` their median; ``confidence`` = trusted share x columns (/20,
    capped) x (1 - IQR / height)."""
    return _fit(pano, pose, footprints, depth, instances, **kw)[0]


@dataclass(frozen=True)
class PoseCheck:
    h_pose_m: float
    h_bases_m: float             # camera height from the base rows, pose pitch
    h_bases_free_m: float        # ... with the pitch fitted too
    pitch_fix_deg: float         # the pitch correction of that free fit
    n_cols: int
    rel_diff: float              # h_bases / h_pose - 1
    flagged: bool


def check_pose_from_bases(pano: fd.Pano, pose: fd.PanoPose, footprints: list, depth=None,
                          instances=None, tol: float = 0.05, **kw) -> PoseCheck:
    """Height-free check of the camera height: a foot at ``d_in`` sits ``atan(h / d_in)`` below
    the horizon whatever the building's height. Pools the observed feet (:func:`observed_base`)
    of every measured building whose base is not behind a nearer one and fits ``h`` (median of
    ``-d tan e``), and ``h`` with a pitch offset (grid, median residual); flagged when the first
    is more than ``tol`` from the pose's."""
    _ms, bases = _fit(pano, pose, footprints, depth, instances, **kw)
    if len(bases) < 10:
        return PoseCheck(pose.camera_h_m, math.nan, math.nan, math.nan, len(bases), math.nan, False)
    d = np.array([b[0] for b in bases])
    e = np.asarray(fd._elev_of(pano, pose, np.array([b[1] for b in bases])), float)
    keep = e < -0.5
    d, e = d[keep], e[keep]
    h1 = float(np.median(-d * np.tan(np.radians(e))))
    best = None
    for hc in pose.camera_h_m * np.exp(np.linspace(math.log(0.6), math.log(1.6), 121)):
        r = e - (-np.degrees(np.arctan(hc / d)))
        fix = float(np.median(r))
        mis = float(np.median(np.abs(r - fix)))
        if best is None or mis < best[0]:
            best = (mis, float(hc), fix)
    rel = h1 / pose.camera_h_m - 1.0
    return PoseCheck(pose.camera_h_m, h1, best[1], -best[2], int(d.size), rel, abs(rel) > tol)


# --------------------------------------------------------------------------- diagnostic


def overlay_tag_rows(pano: fd.Pano, pose: fd.PanoPose, footprints: list, depth=None,
                     instances=None, out_png=None, min_tag_m: float = 40.0, measured=None,
                     tile_h: int = 360):
    """Diagnostic montage for the tagged buildings (``osm_height_m >= min_tag_m``), and per
    building the share of core columns whose far-edge (or near-edge) row at the tag lies within
    4 px of a visible boundary (a label, instance or depth change).

    Each tile is the image (left) and the instances (right) around one footprint, with per
    column: predicted base (green), crease at the tag (yellow), roof top at the tag (cyan), the
    stop of a ``_column_run`` from the base (red: depth step, blue: sky, magenta: other label),
    and ``measured``'s top row for the footprint, if given (white). Returns
    ``[(name, tag, dist, share_within_4px, n_cols)]``."""
    import cv2

    H, W = pano.labels.shape
    h = pose.camera_h_m
    bear_col = (pano.frame_heading + pose.offset_deg) % 360.0
    k = pano.f_px / 417.0                    # px scale against the default 75-deg capture
    dz = None
    if depth is not None:
        from scipy.ndimage import median_filter

        dz = median_filter(np.asarray(depth, float), size=(5, 1))
    mtop = {m.footprint: m for m in (measured or [])}
    is_b = np.isin(pano.labels, fd.BUILDING_CLASSES)
    tiles, stats = [], []
    for c in _candidates(pano, pose, footprints, 3000.0, _px(pano, 0.7)):
        fp = footprints[c.i]
        tag = fp.osm_height_m
        if not tag or tag < min_tag_m:
            continue
        xs = np.arange(c.x0, c.x1 + 1)
        iv = ray_intervals(c.xy, bear_col[xs])
        ok = np.isfinite(iv[:, 0])
        if ok.sum() < 3:
            continue
        xs, iv = xs[ok], iv[ok]
        base = fd._row_of(pano, pose, -np.degrees(np.arctan2(h, iv[:, 0])))
        crease = fd._row_of(pano, pose, np.degrees(np.arctan((tag - h) / iv[:, 0])))
        topr = fd._row_of(pano, pose, top_elev(h, tag, iv[:, 0], iv[:, 1]))
        runs, near = [], 0
        for j, x in enumerate(xs):
            y0 = int(round(base[j])) - 1
            got = None
            if 1 <= y0 < H:
                ref = None if dz is None else float(dz[y0, x])
                got = fd._column_run(pano.labels[:, x], None if dz is None else dz[:, x], y0,
                                     np.inf if ref is None else 1.35 * ref, 0.85,
                                     -np.inf if ref is None else 0.7 * ref, int(8 * k),
                                     int(40 * k), None if instances is None else instances[:, x])
            runs.append(got)
            # a visible boundary near the predicted top: label, instance or depth change
            t = int(round(topr[j]))
            lo, hi_ = max(1, t - 4), min(H - 1, t + 4)
            col_l = pano.labels[lo - 1:hi_ + 1, x]
            chg = (col_l[1:] != col_l[:-1]).any() or (is_b[lo - 1:hi_ + 1, x][1:] != is_b[lo - 1:hi_ + 1, x][:-1]).any()
            if instances is not None:
                ci = instances[lo - 1:hi_ + 1, x]
                chg = chg or (ci[1:] != ci[:-1]).any()
            if dz is not None and not chg:
                cd = dz[lo - 1:hi_ + 1, x]
                chg = bool((cd[1:] < 0.85 * cd[:-1]).any() | (cd[:-1] < 0.85 * cd[1:]).any())
            near += bool(chg)
        share = near / max(1, len(xs))
        stats.append((fp.name, float(tag), float(c.dn), share, int(len(xs))))
        if out_png is None:
            continue
        pad = max(20, len(xs) // 2)
        xa, xb = max(0, c.x0 - pad), min(W, c.x1 + pad + 1)
        ya = int(max(0, np.nanmin(topr) - 60 * k))
        yb = int(min(H, np.nanmax(base) + 30 * k))
        img = pano.rgb[ya:yb, xa:xb].copy()
        if instances is not None:
            inst = instances[ya:yb, xa:xb]
            rng = np.random.default_rng(0)
            pal = rng.integers(40, 255, (int(instances.max()) + 2, 3)).astype(np.uint8)
            pal[0] = 0
            right = pal[np.clip(inst, 0, None)]
        else:
            right = (np.isin(pano.labels[ya:yb, xa:xb], fd.BUILDING_CLASSES)[..., None]
                     * np.array([200, 200, 200], np.uint8))
        for panel in (img, right):
            for j, x in enumerate(xs):
                cx = x - xa
                for row, col in ((base[j], (0, 255, 0)), (crease[j], (255, 255, 0)),
                                 (topr[j], (0, 255, 255))):
                    r = int(round(row)) - ya
                    if 0 <= r < panel.shape[0]:
                        panel[r, cx] = col
                if runs[j] is not None:
                    r = runs[j][0] - ya
                    col = {"depth": (255, 0, 0), "sky": (0, 0, 255)}.get(runs[j][2], (255, 0, 255))
                    if 0 <= r < panel.shape[0]:
                        panel[max(0, r - 1):r + 2, cx] = col
            m = mtop.get(c.i)
            if m is not None:
                r = int(round(m.top_row)) - ya
                if 0 <= r < panel.shape[0]:
                    panel[r, max(0, m.x0 - xa):m.x1 - xa + 1:2] = (255, 255, 255)
        tile = np.concatenate([img, np.full((img.shape[0], 4, 3), 255, np.uint8), right], axis=1)
        s = tile_h / tile.shape[0]
        tile = cv2.resize(tile, (max(1, int(tile.shape[1] * s)), tile_h),
                          interpolation=cv2.INTER_NEAREST)
        bar = np.zeros((24, tile.shape[1], 3), np.uint8)
        label = f"{fp.name[:22]} tag {tag:.0f} d {c.dn:.0f}"
        if m is not None:
            label += f" read {m.height_m:.0f}"
        cv2.putText(bar, label, (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        tiles.append(np.concatenate([bar, tile], axis=0))
    if out_png is not None and tiles:
        wmax = 1800
        rows_img, cur, cw = [], [], 0
        for t in tiles:
            if cur and cw + t.shape[1] > wmax:
                rows_img.append(cur)
                cur, cw = [], 0
            cur.append(t)
            cw += t.shape[1] + 6
        rows_img.append(cur)
        width = max(sum(t.shape[1] + 6 for t in r) for r in rows_img)
        strips = []
        for r in rows_img:
            strip = np.zeros((tile_h + 24, width, 3), np.uint8)
            x = 0
            for t in r:
                strip[:, x:x + t.shape[1]] = t
                x += t.shape[1] + 6
            strips.append(strip)
        cv2.imwrite(str(out_png), cv2.cvtColor(np.concatenate(strips, axis=0), cv2.COLOR_RGB2BGR))
    return stats


__all__ = ["ray_intervals", "top_elev", "invert_top", "observed_top", "observed_base",
           "fit_roof_heights", "RoofMeasured", "check_pose_from_bases", "PoseCheck",
           "overlay_tag_rows", "H_RANGE_M"]
