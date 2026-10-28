"""Camera fit on the terrain for the photo-label tool (pure functions, no HTTP).

`solve_terrain` fits lat, lon, elevation, heading, pitch, focal length (and roll with 8 or more
labels) to the labelled pixel positions. Clicks land mostly on roof tops, so by default (`label_height="prior"`) every label
gets a free height >= 0 above the terrain; an unnamed label is tied to the OSM height of the
footprint containing it (0 outside any) by a soft residual (`PRIOR_SIGMA_M`, `PRIOR_WEIGHT`), a
named label (a tower or roof) is free. `label_height="ground"` is the old behaviour: unnamed
labels sit on the terrain.
The search is seeded from the column-only solve (position, heading, focal length): a coarse
grid scored without optimising picks a few starts, and only those run `least_squares`.

`project_buildings` rasterises OSM footprints as prisms (base at the lowest terrain under the
ring, top at base + height) with a solved camera into an id buffer and returns only the visible
outlines (what the camera would see), far to near.
"""
from __future__ import annotations

import math
import re

import numpy as np

from city2stl.skyline import photo_view as pv

M_PER_DEG_LAT = pv.M_PER_DEG_LAT
ROLL_MIN_LABELS = 8
ROLL_GAIN = 0.8         # free roll must cut the median residual to this fraction to be kept
MAX_H = 120.0           # upper bound of a named label's height above the terrain, m
PRIOR_SIGMA_M = 4.0     # roof-height prior: an unnamed label's height ~ footprint height +- this, m
PRIOR_WEIGHT = 3.0      # px-equivalent weight of the prior residual (h - h0) / sigma
DEFAULT_BUILDING_H = 9.0
LEVEL_H = 3.0
PROJECT_RADIUS_M = 2000.0
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _num(v) -> float | None:
    m = _NUM.search(str(v)) if v is not None else None
    return float(m.group().replace(",", ".")) if m else None


def building_height(props: dict) -> float:
    """OSM `height`, else `building:levels` x 3 m, else 9 m."""
    h = _num(props.get("height"))
    if h is not None and h > 0:
        return h
    lv = _num(props.get("levels"))
    if lv is not None and lv > 0:
        return lv * LEVEL_H
    return DEFAULT_BUILDING_H


def is_named(label: dict) -> bool:
    """A landmark (named footprint or a user-typed name), not an unnamed `P<n>` street point."""
    return bool(label.get("osm_names")) or not re.fullmatch(r"P\d*", str(label.get("label") or "P"))


def label_priors(labels: list[dict], fc: dict | None) -> tuple[np.ndarray, np.ndarray]:
    """(h0, inside): OSM height of the footprint containing each label (0 outside) and whether
    one did."""
    n = len(labels)
    h0, inside = np.zeros(n), np.zeros(n, bool)
    if not fc or not fc.get("features") or not n:
        return h0, inside
    from shapely import STRtree
    from shapely.geometry import Point, shape
    geoms = [shape(f["geometry"]) for f in fc["features"]]
    tree = STRtree(geoms)
    for i, lb in enumerate(labels):
        p = Point(float(lb["lon"]), float(lb["lat"]))
        hit = sorted(int(j) for j in tree.query(p, predicate="intersects"))
        if hit:
            h0[i], inside[i] = building_height(fc["features"][hit[0]]["properties"]), True
    return h0, inside


def _cam(p, k_roll: bool, w: int, h: int) -> pv.PinholeCamera:
    return pv.PinholeCamera(p[0], p[1], p[2], p[3], p[4], p[6] if k_roll else 0.0, p[5], w, h)


def solve_terrain(dem, labels: list[dict], w: int, h: int, seed: dict | None = None,
                  n_start: int = 8, footprints: dict | None = None,
                  label_height: str = "prior") -> dict | None:
    """Fit the camera with roll 0; with 8 or more labels also with roll free, kept only when it
    cuts the median residual clearly (free roll otherwise absorbs label noise: -7 deg on the
    cathedral photo while the median gets worse)."""
    pri = label_priors(labels, footprints) if label_height == "prior" else None
    flat = _solve(dem, labels, w, h, seed, n_start, False, pri)
    if flat is None or len(labels) < ROLL_MIN_LABELS:
        return flat
    rolled = _solve(dem, labels, w, h, seed, n_start, True, pri)
    return rolled if rolled and rolled["median_px"] < ROLL_GAIN * flat["median_px"] else flat


def _solve(dem, labels: list[dict], w: int, h: int, seed: dict | None, n_start: int,
           roll: bool, priors: tuple[np.ndarray, np.ndarray] | None = None) -> dict | None:
    """One fit (see `solve_terrain`). `labels`: dicts with x, y, lat, lon, label[, osm_names]. `seed`: optional
    {lat, lon, heading_deg, f_px} from the column solve. Returns None when no label lies on the
    DEM, else {camera, residuals, named_heights, median_px, max_px, ...}."""
    from scipy.optimize import least_squares

    lat = np.array([float(lb["lat"]) for lb in labels])
    lon = np.array([float(lb["lon"]) for lb in labels])
    xy = np.array([[float(lb["x"]), float(lb["y"])] for lb in labels])
    zg = np.asarray(dem(lat, lon), float)
    if not np.isfinite(zg).all():
        return None
    named = np.array([is_named(lb) for lb in labels])
    h0, inside = priors if priors is not None else (np.zeros(len(labels)), np.zeros(len(labels), bool))
    free = np.ones(len(labels), bool) if priors is not None else named
    ni = np.where(free)[0]
    pri_w = np.where(~named[ni], PRIOR_WEIGHT / PRIOR_SIGMA_M, 0.0) if priors is not None else None
    nbase = 7 if roll else 6
    m = max(w, h)
    kx0 = M_PER_DEG_LAT * math.cos(math.radians(lat.mean()))

    def split(p):
        hs = np.zeros(len(labels))
        hs[ni] = p[nbase:]
        return hs

    def resid_cam(cam, hs):
        x, y = cam.project_ll(lat, lon, zg + hs)
        r = np.concatenate([x - xy[:, 0], y - xy[:, 1]])
        return np.nan_to_num(r, nan=1e4)

    def resid(p):
        r = resid_cam(_cam(p, roll, w, h), split(p))
        if pri_w is None:
            return r
        return np.concatenate([r, pri_w * (p[nbase:] - h0[ni])])

    # ---- coarse grid: score seeds without optimising
    if seed:
        la0, lo0, hd0, f0 = seed["lat"], seed["lon"], seed["heading_deg"], seed["f_px"]
        offs = [-400.0, -150.0, 0.0, 150.0, 400.0]
        heads = [hd0 + d for d in (-25, -10, 0, 10, 25)]
        fs = [f0 * s for s in (0.6, 1.0, 1.7, 2.6)]
    else:
        la0, lo0 = float(lat.mean()), float(lon.mean())
        offs = [-1500.0, -600.0, 0.0, 600.0, 1500.0]
        heads = list(range(0, 360, 30))
        fs = [0.8 * m, 1.6 * m]
    pitches = (-5.0, -15.0, -30.0)
    cand = []
    for dn in offs:
        for de in offs:
            la, lo = la0 + dn / M_PER_DEG_LAT, lo0 + de / kx0
            el = float(np.nan_to_num(dem(la, lo), nan=zg.mean())) + 20.0
            for hd in heads:
                for pt in pitches:
                    for f in fs:
                        p = [la, lo, el, hd, pt, f] + ([0.0] if roll else [])
                        hs = np.zeros(len(labels))
                        hs[ni] = np.where(named[ni], 20.0, h0[ni])
                        cam = _cam(p, roll, w, h)
                        cand.append((float(np.median(np.abs(resid_cam(cam, hs)))), p))
    cand.sort(key=lambda c: c[0])
    starts, picked = [], []
    for _, p in cand:                          # distinct starts: skip near-duplicates of a pick
        if any(abs(p[3] - q[3]) < 8 and abs(p[5] / q[5] - 1) < 0.2 and abs(p[4] - q[4]) < 1
               and abs(p[0] - q[0]) * M_PER_DEG_LAT < 100 and abs(p[1] - q[1]) * kx0 < 100
               for q in picked):
            continue
        picked.append(p)
        starts.append(p)
        if len(starts) >= n_start:
            break

    lo_b = [lat.min() - 0.03, lon.min() - 0.04, float(np.nanmin(zg)) - 5, -720, -80, 100] \
        + ([-10.0] if roll else []) + [0.0] * len(ni)
    hi_b = [lat.max() + 0.03, lon.max() + 0.04, float(np.nanmax(zg)) + 400, 720, 30, 30000] \
        + ([10.0] if roll else []) + [MAX_H] * len(ni)
    xs = [1e-4, 1e-4, 1, 1, 1, 10] + ([1.0] if roll else []) + [1.0] * len(ni)
    best = None
    for p0 in starts:
        p0 = np.clip(list(p0) + list(np.where(named[ni], 20.0, np.maximum(h0[ni], 1.0))), np.add(lo_b, 1e-9), np.subtract(hi_b, 1e-9))
        try:
            r = least_squares(resid, p0, bounds=(lo_b, hi_b), loss="soft_l1", f_scale=4,
                              x_scale=xs, max_nfev=500)
        except Exception:  # noqa: BLE001 - a failed start is just skipped
            continue
        c = float(np.median(np.abs(r.fun[:2 * len(labels)])))
        if best is None or c < best[0]:
            best = (c, r)
    if best is None:
        return None
    p = best[1].x
    cam = _cam(p, roll, w, h)
    hs = split(p)
    x, y = cam.project_ll(lat, lon, zg + hs)
    px = np.hypot(x - xy[:, 0], y - xy[:, 1])
    kx = M_PER_DEG_LAT * math.cos(math.radians(cam.lat))
    dist = np.sqrt(((lon - cam.lon) * kx) ** 2 + ((lat - cam.lat) * M_PER_DEG_LAT) ** 2
                   + (zg + hs - cam.elev_m) ** 2)
    res_m = px * dist / cam.f_px
    ground = float(dem(cam.lat, cam.lon))
    return {
        "camera": {"lat": cam.lat, "lon": cam.lon, "elev_m": cam.elev_m,
                   "ground_m": ground, "heading_deg": cam.heading_deg % 360,
                   "pitch_deg": cam.pitch_deg, "roll_deg": cam.roll_deg, "f_px": cam.f_px,
                   "hfov_deg": math.degrees(2 * math.atan(w / 2 / cam.f_px)), "width": w, "height": h},
        "residuals_px": [float(v) for v in px],
        "residuals_m": [float(v) for v in res_m],
        "heights_m": [float(hs[i]) if free[i] else None for i in range(len(labels))],
        "h0_m": [float(h0[i]) for i in range(len(labels))] if priors is not None else None,
        "in_footprint": [bool(v) for v in inside] if priors is not None else None,
        "median_px": float(np.median(px)), "max_px": float(px.max()),
        "median_m": float(np.median(res_m)),
        "n_starts": len(starts),
    }


NEAR_M = 1.0            # near plane of the visibility raster, m
MAX_VISIBLE = 2000      # nearest footprints in view that are rasterised (150 dropped the far hillside)
MIN_VISIBLE_PX = 30     # a building with fewer visible pixels is dropped
MIN_DIST_M = 30.0       # footprints closer than this to the camera (the one it stands on) are skipped
_CLAMP = 30000.0


def _clip_near(poly: np.ndarray) -> np.ndarray:
    """Clip a polygon of camera-space points (n, 3: right, up, forward) to forward >= NEAR_M."""
    out = []
    for i in range(len(poly)):
        a, b = poly[i - 1], poly[i]
        ia, ib = a[2] >= NEAR_M, b[2] >= NEAR_M
        if ia != ib:
            t = (NEAR_M - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
        if ib:
            out.append(b)
    return np.array(out)


def _prism_faces(pts_c: np.ndarray, zb: float, zt: float, e: np.ndarray, n: np.ndarray):
    """Visible-side faces of a prism: (kind, camera-space polygon) with kind 1 roof, 2 wall.
    `pts_c` (m, 2) ring in metres east/north of the camera, `zb`/`zt` base/top relative to the
    camera. Back-facing walls are skipped, the roof only when the camera is above it."""
    m = len(pts_c)
    area = 0.5 * float(np.sum(pts_c[:, 0] * np.roll(pts_c[:, 1], -1) - np.roll(pts_c[:, 0], -1) * pts_c[:, 1]))
    ring = pts_c if area >= 0 else pts_c[::-1]                      # counter-clockwise
    faces = []
    for i in range(m):
        a, b = ring[i], ring[(i + 1) % m]
        nx, ny = b[1] - a[1], a[0] - b[0]                           # outward normal
        if -(a[0] * nx + a[1] * ny) <= 0:                           # camera at the origin
            continue
        faces.append((2, np.array([[a[0], a[1], zb], [b[0], b[1], zb], [b[0], b[1], zt], [a[0], a[1], zt]])))
    if zt < 0:
        faces.append((1, np.column_stack([ring, np.full(m, zt)])))
    return faces


def project_buildings(dem, fc: dict, cam: pv.PinholeCamera, radius_m: float = PROJECT_RADIUS_M,
                      limit: int = MAX_VISIBLE) -> dict:
    """What the camera would see of the OSM footprints (prisms: base at the lowest terrain under
    the ring, top at base + height). The `limit` nearest footprints in view are rasterised as
    faces, far to near, into an id buffer (near plane `NEAR_M`; faces crossing it are clipped).
    Returns {"buildings": [...], "ids": (h, w) uint16 array, index+1 of the building (0: none)}.
    Each building: id, name, height_m, dist_m, visible_px, contours (visible outline rings, px),
    roof (visible roof rings, px). Footprints within `MIN_DIST_M` of the camera and buildings under
    `MIN_VISIBLE_PX` are dropped; the list is far
    to near and the array indexes it."""
    import cv2
    from shapely.geometry import Point, Polygon

    w, h = int(cam.width), int(cam.height)
    kx = M_PER_DEG_LAT * math.cos(math.radians(cam.lat))
    right, up, fwd = cam.axes()
    cand = []
    for f in fc["features"]:
        ring = np.asarray(f["geometry"]["coordinates"][0], float)
        if len(ring) < 4:
            continue
        pts = ring[:-1]
        c = pts.mean(axis=0)
        d = math.hypot((c[0] - cam.lon) * kx, (c[1] - cam.lat) * M_PER_DEG_LAT)
        if d > radius_m:
            continue
        # The photographer's own terrace or rooftop would fill the frame from a few metres away.
        loc = np.column_stack([(pts[:, 0] - cam.lon) * kx, (pts[:, 1] - cam.lat) * M_PER_DEG_LAT])
        reach = float(np.hypot(*(loc - loc.mean(axis=0)).T).max())  # cheap test first
        if d - reach >= MIN_DIST_M or Polygon(loc).buffer(0).distance(Point(0.0, 0.0)) >= MIN_DIST_M:
            cand.append((d, pts, f["properties"]))
    empty = {"buildings": [], "ids": np.zeros((h, w), np.uint16)}
    if not cand:
        return empty
    cut = np.cumsum([0] + [len(c[1]) for c in cand])
    allp = np.vstack([c[1] for c in cand])
    z = np.asarray(dem(allp[:, 1], allp[:, 0]), float)
    bx, by = cam.project_ll(allp[:, 1], allp[:, 0], z)               # cheap in-frame pre-cull
    keep = []
    for k, (d, _pts, _pr) in enumerate(cand):
        zk = z[cut[k]:cut[k + 1]]
        if not np.isfinite(zk).any():
            continue
        sx, sy = bx[cut[k]:cut[k + 1]], by[cut[k]:cut[k + 1]]
        ok = np.isfinite(sx)
        if not ok.any():
            continue
        if ok.all() and (sx.max() < 0 or sx.min() > w or sy.max() < 0 or sy.min() > h):
            continue
        keep.append((d, k))
    keep.sort()
    keep = keep[:limit]
    ids = np.zeros((h, w), np.int32)
    kind = np.zeros((h, w), np.uint8)
    faces = []                                                      # (mean depth, building slot, kind, poly px)
    for slot, (_d, k) in enumerate(keep):
        _, pts, pr = cand[k]
        zk = z[cut[k]:cut[k + 1]]
        zb = float(zk[np.isfinite(zk)].min()) - cam.elev_m
        zt = zb + building_height(pr)
        pc = np.column_stack([(pts[:, 0] - cam.lon) * kx, (pts[:, 1] - cam.lat) * M_PER_DEG_LAT])
        for kd, poly in _prism_faces(pc, zb, zt, None, None):
            cc = np.column_stack([poly[:, 0] * right[0] + poly[:, 1] * right[1] + poly[:, 2] * right[2],
                                  poly[:, 0] * up[0] + poly[:, 1] * up[1] + poly[:, 2] * up[2],
                                  poly[:, 0] * fwd[0] + poly[:, 1] * fwd[1] + poly[:, 2] * fwd[2]])
            cc = _clip_near(cc)
            if len(cc) < 3:
                continue
            px = np.column_stack([w / 2 + cam.f_px * cc[:, 0] / cc[:, 2], h / 2 - cam.f_px * cc[:, 1] / cc[:, 2]])
            faces.append((float(cc[:, 2].mean()), slot, kd, np.clip(px, -_CLAMP, _CLAMP)))
    faces.sort(key=lambda t: -t[0])
    for _, slot, kd, px in faces:
        q = np.round(px * 16).astype(np.int32)                       # shift=4: sub-pixel edges
        cv2.fillPoly(ids, [q], int(slot + 1), lineType=cv2.LINE_8, shift=4)
        cv2.fillPoly(kind, [q], int(kd), lineType=cv2.LINE_8, shift=4)
    counts = np.bincount(ids.ravel(), minlength=len(keep) + 1)
    from scipy import ndimage
    boxes = ndimage.find_objects(ids, max_label=len(keep))

    def rings(mask, off):
        cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        res = []
        for c in cs:
            c = cv2.approxPolyDP(c, 1.0, True)[:, 0, :] + off
            if len(c) >= 3:
                res.append(c.tolist())
        return res

    out, remap = [], np.zeros(len(keep) + 1, np.uint16)
    for slot in range(len(keep) - 1, -1, -1):                        # far to near
        if counts[slot + 1] < MIN_VISIBLE_PX or boxes[slot] is None:
            continue
        d, k = keep[slot]
        pr = cand[k][2]
        sl = boxes[slot]
        sub, sk = ids[sl] == slot + 1, kind[sl]
        off = np.array([sl[1].start, sl[0].start])
        remap[slot + 1] = len(out) + 1
        out.append({"id": pr.get("id"), "name": pr.get("name"),
                    "height_m": float(building_height(pr)), "dist_m": float(d),
                    "visible_px": int(counts[slot + 1]),
                    "contours": rings(sub, off), "roof": rings(sub & (sk == 1), off)})
    return {"buildings": out, "ids": remap[ids]}
