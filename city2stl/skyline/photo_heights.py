"""Heights of identified towers in a located photo (F-WEB2 phase 2, step C1).

Given a photo's skyline profile and its pose (from labels, GPS + refinement, or
``skyline_match.locate``), measure each tower that forms the skyline:

    ms = measure_towers(prof, towers, pose)           # columns, roof row, distance per tower
    est = loo_heights(ms, towers)                     # height per tower, leave-one-out

Identification: a photo column belongs to the tower the predicted outline puts on top in that
direction (``skyline_match.predicted_outline(owners=True)``), so a roof is credited only to the
tower that should own it. This is the change aimed at the Street View mis-assignment (untagged
buildings read +65 to +113 m too tall, 2026-10-04 baseline).

Measurement: the roof row is the median skyline row over the central ``core`` share of the
tower's columns (its edges are shared with neighbours). Elevation from the row with square
pixels (vertical focal = horizontal); distance to the footprint's nearest corner.

Calibration: ``H = h_cam + d * tan(e + tilt)``. Tilt and camera height are unknown for a
photo, so they are fitted on anchor towers (OSM heights in ``Towers``). ``loo_heights`` fits
them on all *other* towers for each tower, so no tower's own OSM height sets its estimate,
and the benchmark truth (survey + 3D Tiles) stays independent.

Scope: only towers in ``Towers`` (OSM-tagged heights) can be identified from one photo. Untagged
buildings need several photos (step C2):

    table = untagged_table(osm_features)                         # footprints with no height tag
    tilt, h = fit_tilt_height(ms, anchors)                       # per photo, on its tagged towers
    sp = {}
    imp = implied_heights(prof, pose, table, tilt, h, max_height_m=cap,
                          occluders=ms, spans=sp)                # {index: height} per photo
    est = agreed_with_occlusion([imp_a, imp_b, ...], [sp_a, sp_b, ...],
                                cams_xy=[cam_a, cam_b, ...], centroids=...)

Each photo implies a height for every untagged footprint in view, from the skyline over the
footprint's bearings. The building that forms the skyline there implies the same height from
every viewpoint; a building in front of or behind it does not (its distance differs, the
outline does not), so agreement across photos identifies it. Guards (Miami review,
2026-10-05): candidates within ``UNTAGGED_MAX_DIST_M``; implied heights above a plausible
maximum dropped; agreement tolerance capped at ``AGREE_MAX_M``; columns a nearer tagged tower
explains, or a nearer *agreed* building owns, are not a farther footprint's; where a farther
tagged tower explains the footprint's columns the implied height is only a cap; agreement
needs 2+ viewpoints (not files) and 15 deg of view-direction spread at the footprint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .skyline_match import BIN_DEG, N_BINS, PhotoProfile, Towers, predicted_outline


@dataclass(frozen=True)
class PhotoPose:
    lat: float
    lon: float
    heading_deg: float
    hfov_deg: float
    projection: str = "pinhole"


@dataclass(frozen=True)
class TowerMeasure:
    index: int
    name: str
    x0: int
    x1: int
    roof_row: float
    elev_deg: float          # roof elevation from the image centre row, before tilt
    dist_m: float
    osm_height_m: float


def _focal(prof: PhotoProfile, pose: PhotoPose) -> float:
    half = math.radians(pose.hfov_deg / 2.0)
    return (prof.width / 2.0) / (half if pose.projection == "cylindrical" else math.tan(half))


def measure_towers(prof: PhotoProfile, towers: Towers, pose: PhotoPose, h_cam: float = 2.0,
                   min_cols: int = 8, core: float = 0.6) -> list[TowerMeasure]:
    """Per tower forming the skyline in the photo: its columns, roof row and distance."""
    cam = towers.to_xy(pose.lat, pose.lon)
    _, own = predicted_outline(towers, cam, h_cam, owners=True)
    f = _focal(prof, pose)
    xs = np.arange(prof.width) + 0.5 - prof.width / 2.0
    az = np.degrees(xs / f if pose.projection == "cylindrical" else np.arctan(xs / f))
    col_owner = own[np.round((pose.heading_deg + az) / BIN_DEG).astype(int) % N_BINS]
    out = []
    for ti in np.unique(col_owner[col_owner >= 0]):
        cols = np.flatnonzero(col_owner == ti)
        if len(cols) < min_cols:
            continue
        # the longest contiguous run (a tower seen through a gap can appear twice)
        runs = np.split(cols, np.flatnonzero(np.diff(cols) > 1) + 1)
        run = max(runs, key=len)
        if len(run) < min_cols:
            continue
        cut = int(len(run) * (1 - core) / 2)
        mid = run[cut:len(run) - cut]
        rows = prof.y_top[mid]
        rows = rows[np.isfinite(rows)]
        if len(rows) < max(3, len(mid) // 3):
            continue
        row = float(np.median(rows))
        elev = math.degrees(math.atan((prof.height / 2.0 - row) / f))
        v = towers.verts[ti]
        d = float(np.min(np.hypot(v[:, 0] - cam[0], v[:, 1] - cam[1])))
        out.append(TowerMeasure(int(ti), towers.names[ti] or f"tower{ti}", int(run[0]),
                                int(run[-1]), row, elev, d, float(towers.height_m[ti])))
    return out


def fit_tilt_height(ms: list[TowerMeasure], anchors_m: np.ndarray,
                    h_bounds: tuple[float, float] = (0.0, 120.0)) -> tuple[float, float]:
    """Least-squares (tilt deg, camera height m) on anchors; robust to one bad anchor.

    With fewer than 3 anchors the camera height is held at 2 m and only the tilt is fitted:
    two anchors cannot separate tilt from height when their distances are similar.
    """
    from scipy.optimize import least_squares

    d = np.array([m.dist_m for m in ms])
    e = np.radians([m.elev_deg for m in ms])

    def resid(p):
        tilt, h = p if len(p) == 2 else (p[0], 2.0)
        return h + d * np.tan(e + math.radians(tilt)) - anchors_m

    if len(ms) >= 3:
        r = least_squares(resid, [0.0, 2.0], loss="soft_l1", f_scale=10.0,
                          bounds=([-30.0, h_bounds[0]], [30.0, h_bounds[1]]))
        return float(r.x[0]), float(r.x[1])
    r = least_squares(resid, [0.0], loss="soft_l1", f_scale=10.0, bounds=([-30.0], [30.0]))
    return float(r.x[0]), 2.0


def height_of(m: TowerMeasure, tilt_deg: float, h_cam: float) -> float:
    return h_cam + m.dist_m * math.tan(math.radians(m.elev_deg + tilt_deg))


def loo_heights(ms: list[TowerMeasure], min_anchors: int = 2) -> dict[int, float]:
    """Height per measured tower with tilt and camera height fitted on the *other* towers."""
    out = {}
    if len(ms) < min_anchors + 1:
        return out
    for i, m in enumerate(ms):
        rest = ms[:i] + ms[i + 1:]
        tilt, h = fit_tilt_height(rest, np.array([r.osm_height_m for r in rest]))
        out[m.index] = height_of(m, tilt, h)
    return out


# --------------------------------------------------------------------------- untagged (C2)

#: Photos agree on a building's height within max(AGREE_ABS_M, min(AGREE_REL x height,
#: AGREE_MAX_M)). The cap keeps the window from growing with height: at 10 % a 1000 m
#: implied height (a footprint far behind the skyline) let two photos agree by coincidence
#: (Miami, 2026-10-05: 47 "agreed", median 249 m, p90 1109 m).
AGREE_ABS_M = 3.0
AGREE_REL = 0.10
AGREE_MAX_M = 10.0
#: Candidates farther than this are not measured: past ~2 km a pixel row is metres of height
#: and a footprint behind the real skyline-former implies a huge one.
UNTAGGED_MAX_DIST_M = 2000.0
#: A footprint loses a photo when nearer agreed buildings cover more than this share of its
#: columns there (``agreed_with_occlusion``).
OCCLUDED_SHARE = 0.5
#: In a photo where a tagged tower *behind* a footprint explains at least this share of the
#: footprint's columns, its implied height is only an upper bound, not a reading.
BEHIND_SHARE = 0.5
#: Agreement needs this many separate viewpoints (cameras within VIEW_RADIUS_M are one) and
#: this spread of view directions at the footprint. From one spot every footprint along a
#: sight line implies a consistent height (Miami review: 7 of 16 agreements came from photos
#: at the same spot, both wrong confirmed ones had 0-7 deg of spread).
MIN_VIEWPOINTS = 2
MIN_SPREAD_DEG = 15.0
VIEW_RADIUS_M = 100.0
#: The report rounds ``footprint_lonlat`` (the truth cache's key source) to 6 decimals.
KEY_DECIMALS = 6


@dataclass(frozen=True)
class UntaggedTable(Towers):
    """``Towers`` of untagged footprints, plus each one's original lon/lat ring and its
    ``benchmark.footprint_key``. The key is computed on the ring rounded to ``KEY_DECIMALS``,
    as the skyline report writes ``footprint_lonlat``, so it matches the truth cache; a raw
    7-decimal OSM ring matched none of Miami's untagged truth (2026-10-05)."""
    rings: tuple = ()
    keys: tuple = ()


def untagged_table(features: list[dict], min_area_m2: float = 150.0,
                   tagged_sources: tuple[str, ...] = ("osm_tag", "osm_levels"),
                   frame: Towers | None = None,
                   only_keys: set[str] | None = None) -> UntaggedTable:
    """OSM building footprints *without* a tagged height (``height_m`` NaN).

    ``frame``: put the footprints in that table's local metric frame (the photo poses are
    converted with it). Footprints under ``min_area_m2`` (sheds, kiosks) are left out.
    ``only_keys``: keep only footprints whose key is in this set (e.g. those the benchmark
    truth cache already holds, so C2 can be scored without fetching truth).
    """
    from shapely.geometry import shape

    from .benchmark import footprint_key

    rows = []
    for f in features:
        p = f.get("properties") or {}
        if p.get("height_source") in tagged_sources:
            continue
        try:
            g = shape(f["geometry"])
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        g = g if g.geom_type == "Polygon" else max(g.geoms, key=lambda q: q.area)
        ring = [[float(x), float(y)] for x, y in np.asarray(g.exterior.coords)[:, :2]]
        key = footprint_key([[round(x, KEY_DECIMALS), round(y, KEY_DECIMALS)]
                             for x, y in ring])
        if only_keys is not None and key not in only_keys:
            continue
        rows.append((np.asarray(ring), str(p.get("name") or ""), ring, key))
    if frame is not None:
        lat0, lon0 = frame.lat0, frame.lon0
    elif rows:
        lat0 = float(np.mean([r[0][:, 1].mean() for r in rows]))
        lon0 = float(np.mean([r[0][:, 0].mean() for r in rows]))
    else:
        lat0 = lon0 = 0.0
    kx = 111_320.0 * math.cos(math.radians(lat0))
    verts, names, rings, keys = [], [], [], []
    for ring, name, ll, key in rows:
        v = np.column_stack([(ring[:, 0] - lon0) * kx, (ring[:, 1] - lat0) * 111_320.0])
        x, y = v[:, 0], v[:, 1]
        if 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) < min_area_m2:
            continue
        verts.append(v)
        names.append(name)
        rings.append(ll)
        keys.append(key)
    return UntaggedTable(lat0, lon0, verts, np.full(len(verts), np.nan), names,
                         tuple(rings), tuple(keys))


def _agree_tol(h: float, abs_m: float = AGREE_ABS_M, rel: float = AGREE_REL,
               max_m: float = AGREE_MAX_M) -> float:
    return max(abs_m, min(rel * abs(h), max_m))


def implied_heights(prof: PhotoProfile, pose: PhotoPose, table: Towers, tilt_deg: float,
                    h_cam: float, min_cols: int = 4, core: float = 0.6,
                    max_dist_m: float = UNTAGGED_MAX_DIST_M, min_dist_m: float = 60.0,
                    max_height_m: float | None = None,
                    occluders: list[TowerMeasure] | None = None,
                    spans: dict | None = None, caps: dict | None = None) -> dict[int, float]:
    """Height each footprint of ``table`` would need to form this photo's skyline over its
    bearings: ``h_cam + d * tan(e + tilt)``, with ``e`` the skyline elevation (median over the
    central ``core`` share of the footprint's free columns) and ``d`` its nearest corner's
    distance.

    ``tilt_deg`` and ``h_cam`` come from the photo's tagged towers (``fit_tilt_height``). Says
    nothing about *which* building forms the skyline: ``agreed_with_occlusion`` decides that
    across photos. Footprints with fewer than ``min_cols`` free columns of skyline are skipped.

    - ``max_height_m``: implied heights above it are dropped, not clipped (clipping would make
      far footprints agree at the cap).
    - ``occluders``: the photo's measured tagged towers. A column a *nearer* tagged tower
      explains (its OSM roof reaches the skyline there, within the agreement tolerance) is
      not free for footprints behind it: only the nearest plausible owner forms the skyline.
      Where a *farther* tagged tower explains at least ``BEHIND_SHARE`` of a footprint's
      columns, the footprint's implied height is only an upper bound (the tower may be the
      skyline there): it goes to ``caps``, not to the result.
    - ``spans``: if given, filled with ``{index: (first col, last col, distance)}`` for every
      footprint returned, for ``agreed_with_occlusion``.
    """
    cam = table.to_xy(pose.lat, pose.lon)
    f = _focal(prof, pose)
    half_fov = pose.hfov_deg / 2.0
    xs = np.arange(prof.width) + 0.5 - prof.width / 2.0
    elev_col = np.degrees(np.arctan((prof.height / 2.0 - prof.y_top) / f))   # NaN where no sky
    # per column, the distance of the nearest tagged tower that explains the skyline there
    # (nearest and farthest, for the "behind" test)
    claimed = np.full(prof.width, np.inf)
    behind = np.full(prof.width, -np.inf)
    for m in occluders or ():
        c = np.arange(max(m.x0, 0), min(m.x1, prof.width - 1) + 1)
        with np.errstate(invalid="ignore"):
            need = h_cam + m.dist_m * np.tan(np.radians(elev_col[c] + tilt_deg))
            ok = np.isfinite(need) & (need <= m.osm_height_m + _agree_tol(m.osm_height_m))
        claimed[c[ok]] = np.minimum(claimed[c[ok]], m.dist_m)
        behind[c[ok]] = np.maximum(behind[c[ok]], m.dist_m)
    out: dict[int, float] = {}
    for i, v in enumerate(table.verts):
        dx, dy = v[:, 0] - cam[0], v[:, 1] - cam[1]
        d = float(np.min(np.hypot(dx, dy)))
        if d < min_dist_m or d > max_dist_m:
            continue
        rel = (np.degrees(np.arctan2(dx, dy)) - pose.heading_deg + 180.0) % 360.0 - 180.0
        lo, hi = float(rel.min()), float(rel.max())
        if hi - lo > 180.0 or hi < -half_fov or lo > half_fov:   # behind, or out of frame
            continue
        lo, hi = max(lo, -half_fov), min(hi, half_fov)
        if pose.projection == "cylindrical":
            c0, c1 = f * math.radians(lo), f * math.radians(hi)
        else:
            c0, c1 = f * math.tan(math.radians(lo)), f * math.tan(math.radians(hi))
        span = np.flatnonzero((xs >= c0) & (xs <= c1))
        cols = span[claimed[span] >= d]                       # not explained by a nearer tower
        if len(cols) < min_cols:
            continue
        cut = int(len(cols) * (1 - core) / 2)
        mid = cols[cut:len(cols) - cut]
        rows = prof.y_top[mid]
        rows = rows[np.isfinite(rows)]
        if len(rows) < max(2, len(mid) // 2):
            continue
        elev = math.degrees(math.atan((prof.height / 2.0 - float(np.median(rows))) / f))
        h = h_cam + d * math.tan(math.radians(elev + tilt_deg))
        if max_height_m is not None and h > max_height_m:
            continue
        if np.mean(behind[span] > d) >= BEHIND_SHARE:
            if caps is not None:
                caps[i] = h
            continue
        out[i] = h
        if spans is not None:
            spans[i] = (int(span[0]), int(span[-1]), d)
    return out


def viewpoints(cams_xy: list[tuple[float, float]], radius_m: float = VIEW_RADIUS_M) -> list[int]:
    """Viewpoint id per camera: cameras within ``radius_m`` of each other (transitively) are
    one viewpoint. Linked photos inherit a located photo's camera, so they collapse here."""
    n = len(cams_xy)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a in range(n):
        for b in range(a + 1, n):
            if math.dist(cams_xy[a], cams_xy[b]) <= radius_m:
                parent[find(a)] = find(b)
    roots = {r: k for k, r in enumerate(dict.fromkeys(find(a) for a in range(n)))}
    return [roots[find(a)] for a in range(n)]


def view_spread_deg(target_xy: tuple[float, float], cams_xy: list[tuple[float, float]]) -> float:
    """Largest angle between the directions from ``target_xy`` to the cameras (degrees)."""
    az = [math.atan2(cx - target_xy[0], cy - target_xy[1]) for cx, cy in cams_xy]
    best = 0.0
    for a in range(len(az)):
        for b in range(a + 1, len(az)):
            diff = abs((az[a] - az[b] + math.pi) % (2 * math.pi) - math.pi)
            best = max(best, diff)
    return math.degrees(best)


def agreed_heights(per_photo: list[dict[int, float]], min_photos: int = 2,
                   abs_m: float = AGREE_ABS_M, rel: float = AGREE_REL,
                   max_m: float = AGREE_MAX_M, *, cams_xy: list | None = None,
                   centroids: dict | None = None, min_viewpoints: int = MIN_VIEWPOINTS,
                   min_spread_deg: float = MIN_SPREAD_DEG, stats: dict | None = None
                   ) -> dict[int, tuple[float, int, float]]:
    """``{index: (height, n_photos, spread)}`` for footprints whose implied heights agree.

    A footprint is kept when at least ``min_photos`` photos imply a height for it and they
    all lie within ``max(abs_m, min(rel x median, max_m))`` of their median; the height is
    that median. Implied heights at or below 0 m (the skyline is lower than the footprint can
    explain) are ignored.

    With ``cams_xy`` (one camera per photo, in the table's frame) and ``centroids``
    (``{index: (x, y)}``), agreement also needs ``min_viewpoints`` separate viewpoints
    (``viewpoints``) and ``min_spread_deg`` of view directions at the footprint
    (``view_spread_deg``): an implied height is a measurement only when the footprint is seen
    from different directions. ``stats`` counts what each rule removed.
    """
    st = {"candidates": 0, "one_photo": 0, "disagree": 0, "one_viewpoint": 0,
          "narrow_spread": 0, "agreed": 0}
    vp = viewpoints(cams_xy) if cams_xy is not None else None
    by: dict[int, list[tuple[float, int]]] = {}
    for k, ph in enumerate(per_photo):
        for i, h in ph.items():
            if h > 0:
                by.setdefault(i, []).append((h, k))
    out = {}
    for i, hk in by.items():
        st["candidates"] += 1
        if len(hk) < min_photos:
            st["one_photo"] += 1
            continue
        a = np.asarray([h for h, _ in hk])
        med = float(np.median(a))
        if not np.all(np.abs(a - med) <= _agree_tol(med, abs_m, rel, max_m)):
            st["disagree"] += 1
            continue
        if vp is not None and centroids is not None and i in centroids:
            photos = [k for _, k in hk]
            if len({vp[k] for k in photos}) < min_viewpoints:
                st["one_viewpoint"] += 1
                continue
            if view_spread_deg(centroids[i], [cams_xy[k] for k in photos]) < min_spread_deg:
                st["narrow_spread"] += 1
                continue
        st["agreed"] += 1
        out[i] = (med, len(hk), float(np.ptp(a)))
    if stats is not None:
        stats.clear()
        stats.update(st)
    return out


def agreed_with_occlusion(per_photo: list[dict[int, float]], spans: list[dict],
                          max_rounds: int = 4, **kw) -> dict[int, tuple[float, int, float]]:
    """``agreed_heights`` where, in each photo, a footprint behind an agreed building is not
    a candidate over the columns that building owns.

    Within one photo the nearer and the farther footprint both imply *a* height for the same
    skyline, so nearest-wins would credit the roof to whatever stands in front. Across photos
    only the real skyline-former agrees; once it has, a footprint whose columns it covers
    (more than ``OCCLUDED_SHARE``) from nearer up is dropped from that photo, agreed or not
    (two photos can agree by coincidence on a footprint behind), and agreement is re-run
    until nothing changes. ``spans[k]`` is photo ``k``'s ``implied_heights(spans=)``.
    Other keywords go to ``agreed_heights``; ``stats`` gets its first-pass counts plus
    ``occluded`` (agreed at first, dropped by occlusion) and the final ``agreed``.
    """
    per = [dict(p) for p in per_photo]
    stats = kw.pop("stats", None)
    first: dict = {}
    est = agreed_heights(per, **kw, stats=first)
    est0 = set(est)
    for _ in range(max_rounds):
        changed = False
        for p, sp in zip(per, spans, strict=True):
            owners = {j: sp[j] for j in est if j in p and j in sp}
            for i in list(p):
                if i not in sp:
                    continue
                c0, c1, d = sp[i]
                cover = np.zeros(c1 - c0 + 1, bool)
                for j, (a, b, dj) in owners.items():
                    if j != i and dj < d:
                        cover[max(a, c0) - c0:min(b, c1) - c0 + 1] = True
                if cover.mean() > OCCLUDED_SHARE:
                    del p[i]
                    changed = True
        if not changed:
            break
        est = agreed_heights(per, **kw)
    if stats is not None:
        stats.clear()
        stats.update(first)
        stats["occluded"] = len(est0 - set(est))
        stats["agreed"] = len(est)
    return est
