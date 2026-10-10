"""skyline._pano.triangulate — roof corners seen from two drone spheres give a height that needs
neither sphere's ownership of the top nor a visible base (review 2026-10-09; street-level
triangulation reaches 0.65 m RMSE in the literature, at our 0.2-3 km range the baseline between
spheres must be hundreds of metres).

1. **Corner observations** (:func:`seed_corners`, per sphere, while its pano is in memory): for
   each measured footprint, its two limb corners. The corner's OSM vertex (the one at the lowest
   and the highest bearing, as ``ownership``) only *names* the corner, so two spheres can say they
   saw the same one; the ray comes from the image. The column is the outer boundary of the
   footprint's MobileSAM instance in a band of rows just under the measured top (or a depth step
   there), searched within :data:`EDGE_WIN_PX` / :data:`EDGE_WIN_FRAC` of the projected corner
   column; none found (a block merged into one instance) is no observation. The elevation is the
   top of the building mask in the columns just inside the edge.
2. **Intersection** (:func:`intersect`): the bearing (the column's, from the pose) and elevation
   (the row's) of a corner make a ray from the sphere's camera (position, height above the
   pose's datum). Two spheres of different camera groups, at least :data:`MIN_BASELINE_M` apart,
   that saw the same vertex: the nearest approach of their rays. Height = z of the midpoint;
   uncertainty = the miss distance. Gates: horizontal intersection angle :data:`MIN_ANGLE_DEG`
   to ``180 - MIN_ANGLE_DEG``, both ranges ahead and at least :data:`MIN_RANGE_M`, miss at most
   :data:`MAX_MISS_M` (a sphere reading another building), and the midpoint within
   :data:`MAX_VERTEX_M` of the named OSM vertex (rays that meet on a different building).
3. **Reading** (:func:`triangulate`): per footprint the median over the passing corners and
   sphere pairs; kind ``triangulated``.

Independence: a triangulated reading contains the elevation and bearing of both its spheres, so
it is not independent of a single reading from either (``fuse_heights`` counts it as a source only
when its camera groups are disjoint from the drone groups kept); it is independent of a third
camera and of satellite readings. See ``docs/decisions/building-heights.md``.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .. import footprint_detect as fd

#: Gates (fixed before the Miami run, S/tri/notes.md).
MIN_BASELINE_M = 200.0
MIN_ANGLE_DEG = 10.0
MAX_MISS_M = 15.0
MAX_VERTEX_M = 40.0
MIN_RANGE_M = 20.0
#: Edge search: pixels of the default spin pano (``fd.px_scale`` scales them) and a share of the
#: footprint's width, around the projected corner column.
EDGE_WIN_PX = 8.0
EDGE_WIN_FRAC = 0.25
#: Band of rows under the measured top used for the edge (start, height), pixels of the spin pano.
BAND_START_PX = 3.0
BAND_PX = 10.0
#: Columns inside the edge (from, to) that give the corner's top row.
INSET_PX = (2, 5)
#: A mask share of a column in the band that counts as inside; the inverse-depth ratio that
#: counts as a depth step across the edge.
INSIDE_SHARE = 0.5
DEPTH_STEP = 1.15
#: Core share of a footprint's columns (as ``measure_footprints``) that names its instance.
CORE = 0.6
#: Reading kind and the weight a triangulated reading takes in ``fuse_heights``.
KIND = "triangulated"

#: Environment ``SKYLINE_TRIANGULATE`` (0/1) overrides. Off until the Miami marks and the
#: Cartagena replay are accepted.
TRIANGULATE = False


def enabled() -> bool:
    import os

    v = os.environ.get("SKYLINE_TRIANGULATE")
    if v is None or v == "":
        return TRIANGULATE
    return v.strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class CornerObs:
    """One limb corner of one footprint seen from one sphere."""
    fid: str
    side: str                  # "L" or "R"
    vertex: tuple              # (lon, lat) of the OSM vertex the corner is named by
    lat: float                 # the camera
    lon: float
    cam_h_m: float             # camera height above the pose's datum
    bearing_deg: float
    elev_deg: float
    col: float
    row: float
    dist_m: float              # nearest-vertex distance of the footprint (Measured.dist_m)


@dataclass
class TriReading:
    fid: str
    height_m: float
    miss_m: float              # median 3D miss of the passing corners
    n: int                     # passing corner triangulations
    pairs: list                # [(seed A, seed B, side, height, miss, angle, baseline, range)]
    groups: tuple              # camera groups of the spheres that gave a passing corner
    range_m: float             # mean horizontal range of the rays
    heights: list = field(default_factory=list)


# --------------------------------------------------------------------------- observations


def _first_mode(a: np.ndarray) -> int:
    v = a[a > 0]
    if not v.size:
        return 0
    vals, cnt = np.unique(v, return_counts=True)
    return int(vals[np.argmax(cnt)])


def _scan_edge(mask_band: np.ndarray, dep_band, x_start: int, step: int, x_lim: int, gap: int):
    """Walk from ``x_start`` (inside) outward in ``step`` (-1 left, +1 right) until ``x_lim``.
    Returns ``(edge column, why)``: the last inside column and ``mask`` / ``depth``, or
    ``(None, "merged")`` when the mask runs to the limit with no depth step, ``(None, "outside")``
    when the start is not inside."""
    W = mask_band.shape[1]
    share = mask_band.mean(axis=0)
    if not (0 <= x_start < W) or share[x_start] < INSIDE_SHARE:
        return None, "outside"
    x = x_start
    out_run = 0
    last_in = x
    while True:
        nx = x + step
        if not (0 <= nx < W) or (nx - x_lim) * step > 0:
            return None, "merged"
        if share[nx] >= INSIDE_SHARE:
            out_run = 0
            last_in = nx
        else:
            out_run += 1
            if out_run >= gap:
                return last_in, "mask"
        if dep_band is not None and share[nx] >= INSIDE_SHARE:
            # depth step between the 3 columns toward the core and the 3 outside, at nx
            xi = [c for c in (nx - step, nx - 2 * step, nx - 3 * step) if 0 <= c < W]
            xo = [c for c in (nx, nx + step, nx + 2 * step) if 0 <= c < W]
            if xi and xo:
                di, do = float(np.median(dep_band[xi])), float(np.median(dep_band[xo]))
                if di > 0 and do > 0 and max(di, do) / min(di, do) >= DEPTH_STEP:
                    return nx - step, "depth"
        x = nx


def seed_corners(pano: fd.Pano, pose: fd.PanoPose, footprints: list, fids: list, measured: list,
                 instances: np.ndarray | None = None, depth: np.ndarray | None = None
                 ) -> tuple[list, Counter]:
    """``(observations, stats)``: the :class:`CornerObs` of every measured footprint's limb
    corners that show an edge, and a count of why the others do not (``merged``, ``outside``,
    ``band``, ``far``, ``top``)."""
    H, W = pano.labels.shape
    k = fd.px_scale(pano)
    bu, b0 = fd.bearing_columns(pano, pose)
    isb = np.isin(pano.labels, fd.BUILDING_CLASSES)
    col_idx = np.arange(W)
    gap = max(1, int(round(k)))
    stats: Counter = Counter()
    obs: list[CornerObs] = []
    h = float(pose.camera_h_m)
    for m in measured:
        fp = footprints[m.footprint]
        ring = np.asarray(fp.ring, float)
        xy = fd._local(pano.lat, pano.lon, ring)
        bear = np.degrees(np.arctan2(xy[:, 0], xy[:, 1])) % 360.0
        rel = (bear - bear[0] + 180.0) % 360.0 - 180.0
        lo, hi = bear[0] + rel.min(), bear[0] + rel.max()
        c0, c1 = fd.column_of(lo % 360.0, bu, b0), fd.column_of(hi % 360.0, bu, b0)
        if not (np.isfinite(c0) and np.isfinite(c1)) or c1 <= c0:
            stats["outside"] += 1
            continue
        v_of = {"L": int(np.argmin(rel)), "R": int(np.argmax(rel))}
        x0, x1 = int(math.ceil(c0)), int(math.floor(c1))
        width = x1 - x0 + 1
        r0 = int(round(m.top_row + BAND_START_PX * k))
        r1 = min(int(m.bottom_row) - 1, r0 + int(round(BAND_PX * k)))
        r1 = min(r1, H - 1)
        if r0 < 1 or r1 - r0 < 2:
            stats["band"] += 1
            continue
        cut = int(width * (1 - CORE) / 2)
        core = np.arange(max(0, x0 + cut), min(W, x1 - cut + 1))
        if not core.size:
            stats["band"] += 1
            continue
        if instances is not None:
            ident = _first_mode(instances[r0:r1 + 1][:, core].ravel())
            mask_all = (instances == ident) if ident else isb
        else:
            ident, mask_all = 0, isb
        mask_band = mask_all[r0:r1 + 1]
        dep_band = None
        if depth is not None:
            dep_band = np.median(np.asarray(depth[r0:r1 + 1], float), axis=0)
        win = int(max(EDGE_WIN_PX * k, EDGE_WIN_FRAC * width))
        for side, xc, step in (("L", c0, -1), ("R", c1, 1)):
            x_start = int(round(xc)) - step * max(2, width // 6)
            x_start = min(max(x_start, 0), W - 1)
            # the start must be inside; take the first inside column toward the core otherwise
            xs = x_start
            for _ in range(max(2, width // 6)):
                if mask_band[:, xs].mean() >= INSIDE_SHARE:
                    break
                xs -= step
                if not 0 <= xs < W:
                    break
            e, why = _scan_edge(mask_band, dep_band, xs, step, int(round(xc)) + step * win, gap)
            if e is None:
                stats[why] += 1
                continue
            if abs(e - xc) > win:
                stats["far"] += 1
                continue
            # the top row of the mask in the columns just inside the edge
            tops = []
            for dx in range(INSET_PX[0], INSET_PX[1] + 1):
                x = e - step * int(round(dx * k))
                if not 0 <= x < W:
                    continue
                y, last, miss = r0, r0, 0
                if not mask_all[y, x]:
                    continue
                while y > 0:
                    y -= 1
                    if mask_all[y, x] or (instances is not None and isb[y, x] and instances[y, x] == 0):
                        last, miss = y, 0
                    else:
                        miss += 1
                        if miss > 2 * gap:
                            break
                tops.append(last - 0.5)
            if not tops:
                stats["top"] += 1
                continue
            col = e + 0.5 * step                      # the outer boundary of the edge column
            col = min(max(col, 0.0), W - 1.0)
            row = float(np.median(tops))
            b = float(np.interp(col, col_idx, bu) % 360.0)
            elev = float(fd._elev_of(pano, pose, row))
            vx = ring[v_of[side]]
            obs.append(CornerObs(str(fids[m.footprint]), side, (round(float(vx[0]), 7),
                                 round(float(vx[1]), 7)), float(pano.lat), float(pano.lon), h,
                                 b, elev, float(col), row, float(m.dist_m)))
            stats["observed"] += 1
    return obs, stats


# --------------------------------------------------------------------------- geometry


def _enu(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    kx = fd.M_PER_DEG_LAT * math.cos(math.radians(lat0))
    return (lon - lon0) * kx, (lat - lat0) * fd.M_PER_DEG_LAT


def intersect(a: CornerObs, b: CornerObs, *, min_baseline: float = MIN_BASELINE_M,
              min_angle: float = MIN_ANGLE_DEG, max_miss: float = MAX_MISS_M,
              max_vertex: float = MAX_VERTEX_M):
    """Nearest approach of two corner rays: ``(result dict, None)`` or ``(None, reason)``.
    Result: ``height_m`` (z of the midpoint), ``miss_m`` (3D gap), ``angle_deg``, ``baseline_m``,
    ``range_m`` (mean horizontal range), ``vertex_m`` (midpoint to the named OSM vertex)."""
    bx, by = _enu(a.lat, a.lon, b.lat, b.lon)
    baseline = math.hypot(bx, by)
    if baseline < min_baseline:
        return None, "baseline"
    ang = abs((a.bearing_deg - b.bearing_deg + 180.0) % 360.0 - 180.0)
    if ang < min_angle or ang > 180.0 - min_angle:
        return None, "angle"

    def unit(o: CornerObs):
        be, el = math.radians(o.bearing_deg), math.radians(o.elev_deg)
        return np.array([math.sin(be) * math.cos(el), math.cos(be) * math.cos(el), math.sin(el)])

    da, db = unit(a), unit(b)
    pa, pb = np.array([0.0, 0.0, a.cam_h_m]), np.array([bx, by, b.cam_h_m])
    w0 = pa - pb
    bb, d, e = float(da @ db), float(da @ w0), float(db @ w0)
    den = 1.0 - bb * bb
    if den < 1e-9:
        return None, "angle"
    sa, sb = (bb * e - d) / den, (e - bb * d) / den
    qa, qb = pa + sa * da, pb + sb * db
    ra, rb = sa * math.hypot(da[0], da[1]), sb * math.hypot(db[0], db[1])
    if min(ra, rb) < MIN_RANGE_M:
        return None, "behind"
    miss = float(np.linalg.norm(qa - qb))
    if miss > max_miss:
        return None, "miss"
    mid = 0.5 * (qa + qb)
    vx, vy = _enu(a.lat, a.lon, a.vertex[1], a.vertex[0])
    vd = math.hypot(mid[0] - vx, mid[1] - vy)
    if vd > max_vertex:
        return None, "vertex"
    return {"height_m": float(mid[2]), "miss_m": miss, "angle_deg": float(ang),
            "baseline_m": float(baseline), "range_m": float(0.5 * (ra + rb)),
            "vertex_m": float(vd)}, None


def triangulate(corners: dict, group=None, **gates) -> tuple[dict, Counter]:
    """``({feature_id: TriReading}, rejection counts)`` from ``corners`` ``{seed: [CornerObs]}``.
    ``group``: seed -> camera group (spheres of one camera never pair); identity by default."""
    grp = group or (lambda s: s)
    by_fid: dict = {}
    for seed, obs in corners.items():
        for o in obs:
            by_fid.setdefault(o.fid, {}).setdefault(seed, []).append(o)
    out: dict = {}
    why: Counter = Counter()
    for fid, per_seed in by_fid.items():
        seeds = sorted(per_seed)
        got = []
        for i, sa in enumerate(seeds):
            for sb in seeds[i + 1:]:
                if grp(sa) == grp(sb):
                    why["same_camera"] += 1
                    continue
                for oa in per_seed[sa]:
                    for ob in per_seed[sb]:
                        if oa.vertex != ob.vertex:
                            why["other_vertex"] += 1
                            continue
                        r, bad = intersect(oa, ob, **gates)
                        if r is None:
                            why[bad] += 1
                            continue
                        why["ok"] += 1
                        got.append((sa, sb, oa.side + ob.side, r))
        if not got:
            continue
        hs = [g[3]["height_m"] for g in got]
        ms = [g[3]["miss_m"] for g in got]
        groups = tuple(sorted({grp(g[0]) for g in got} | {grp(g[1]) for g in got}))
        out[fid] = TriReading(
            fid, float(np.median(hs)), float(np.median(ms)), len(got),
            [(g[0], g[1], g[2], g[3]["height_m"], g[3]["miss_m"], g[3]["angle_deg"],
              g[3]["baseline_m"], g[3]["range_m"]) for g in got],
            groups, float(np.mean([g[3]["range_m"] for g in got])), hs)
    return out, why


def camera_group(name: str) -> str:
    """The camera a seed name stands for: ``miami_seed_3`` and ``miami_seed_3_sphere`` are one
    (their position fits differ by tens to hundreds of metres, which is not a baseline)."""
    import re

    m = re.match(r"^(.*?(?:seed_\d+|drone_[A-Za-z0-9]+))", name)
    return m.group(1) if m else name


def fusion_reading(t: TriReading, name: str = "") -> dict:
    """A triangulated reading as a ``fuse_heights`` reading: ``dist_m`` the mean ray range,
    ``weight_scale`` 1 / (1 + miss / 5 m), fully seen, ``kind`` "triangulated" and the camera
    groups it rests on in ``seeds``."""
    return dict(footprint=t.fid, name=name or t.fid, height_m=float(t.height_m),
                dist_m=max(100.0, float(t.range_m)), base_visible=True, visible_frac=1.0,
                top_edge="triangulated", weight_scale=1.0 / (1.0 + t.miss_m / 5.0),
                kind=KIND, seeds=tuple(t.groups), miss_m=float(t.miss_m), osm_height_m=None)


__all__ = ["CornerObs", "TriReading", "seed_corners", "intersect", "triangulate", "camera_group",
           "fusion_reading", "enabled", "KIND"]
