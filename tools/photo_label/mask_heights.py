"""Auto-correct loop (plan F): read building heights off the pano seed frames' class masks.

Per frame, with the frame's pinhole camera (`views.json` + `pose.json`):

1. **Pose gate** (`pose_gate`): project the footprints (`terrain_solve.project_buildings`), look at
   the mask just above each visible footprint base. Outlines floating on sky / water, or a median
   base-row offset from the mask's building base over `GATE_MAX_DEG`, make the frame `bad_pose`:
   it gives no heights (the seed 6 `overhead` pose put outlines ~12 deg above the horizon).
2. **Blob match** (`assign_blobs`, F-MASKFIT): the projected boxes sit about a tower width off their
   towers, so each footprint's visible column span is matched to a connected SegFormer building
   blob (split at depth jumps when `<stem>_depth.npy` exists) by an assignment (`linear_sum_assignment`,
   cost = -overlap + `SHIFT_PENALTY` x |shift deg|), shift at most `MAX_SHIFT_DEG`; no blob is taken
   twice, and a blob that holds two footprints leaves both unshifted. The mask is read at the shifted
   span (`shift_deg` per row); the ray distance and the ground stay those of the footprint. A tall
   blob nobody claims, above the tallest claimed one, is a `missing_tower` candidate (reported, never
   written into readings).
3. **Top edge** (`column_top`): from each visible base column walk up the mask through building /
   other pixels to sky. Columns ending in vegetation or water, clipped at the frame top, or whose
   run goes on through a farther footprint's outline are dropped. With depth, the walk also stops at
   a jump to a surface farther than the base's (disparity under base / `DEPTH_FAR_RATIO` for
   `DEPTH_STOP_ROWS` rows): the near tower's silhouette ends there instead of climbing into the tower
   behind (opt-in: `analyse_frame(depth_stop=True)`, runner `--depth-stop`). A column whose base disparity is more than `DEPTH_BASE_TOL` off the frame's robust fit
   disparity = a / ray distance + b is dropped (`depth_base`). Depth is relative Depth Anything disparity (larger = nearer).
4. **Class** (`classify`): `ok` / `too_short` (mask rises above the outline) / `too_tall` (outline
   rises into sky) by the median margin in px and degrees; mask building blobs that no outline
   covers are `unmatched_building` (`unmatched_blobs`).
5. **Height** (`column_height`): elevation angle of the mask top x horizontal distance to the
   footprint's front wall + camera elevation - DEM ground at the footprint.

Across frames and seeds (`aggregate`): a height is kept only when 2+ non-weak views agree within
`ln 1.25` (`agree_group`); a `too_tall` outline whose neighbour's mask height matches its prior
height is reassigned to that neighbour (`find_reassignments`). `to_readings` writes the agreed
heights as method ``photo_mask`` in `city2stl/height/satellite/readings.py::save` format and
`merge_production` merges them into the region's satellite readings file (backup first).
`score` compares with the published Bocagrande towers and `heights.json` `published_m`.

TODO (no paid calls here): `google3d_yardstick` is the hook for scoring against the Google 3D mesh.
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

ROOT = _HERE.parents[1]
M_PER_DEG_LAT = 111_320.0

OTHER, SKY, BUILDING, WATER, VEG = 0, 1, 2, 3, 4
# Median base-row offset beyond this: bad_pose. The coordinator asked ~3 deg, but seed 1 / 5 frames that
# overlay well (1_136: 6.0 deg) read 3-6 deg because beach-front buildings missing from OSM extend the mask
# below the outline bases; seed 6's floating outlines read 9-19 deg. 8 separates them.
GATE_MAX_DEG = 8.0
GATE_FLOAT_FRAC = 0.5        # share of base columns whose pixel above the base is sky / water
GATE_MIN_FP = 3              # footprints with a usable base needed to judge a frame
MIN_COLS = 4                 # usable columns for a footprint reading
TOL_DEG = 1.0                # |margin| within this: ok
AGREE_LN = math.log(1.25)
WEAK_BRANCHES = ("overhead",)
BASE_INSET_PX = 3            # the mask is read this far above the outline base
SEARCH_DOWN_PX = 300
MAX_COL_SAMPLES = 24
NEIGHBOUR_M = 80.0
MIN_BLOB_PX = 400
BLOB_COVER = 0.15
DEFAULT_H = 9.0
MAX_SHIFT_DEG = 6.0          # footprint-to-blob sideways shift window (about one tower width)
SHIFT_PENALTY = 0.04         # assignment cost per degree of shift (overlap fraction is the gain)
MIN_OVERLAP = 0.5            # share of a footprint's visible box a blob must cover to be a candidate
BLOB_WIDTH_RATIO = 4.0       # ... and the blob may be at most this many box widths wide (a sliver does not claim a tower)
TOWER_MIN_PX = 40            # only boxes this tall (px, base to top) are matched to blobs
PEAK_PROM_PX = 20            # a skyline peak this far above its saddles is its own blob
SHARE_FRAC = 0.8             # unshifted cover by two footprints: the blob holds both, keep them put
DEPTH_FAR_RATIO = 1.5        # stop at disparity < base disparity / this (a clearly farther surface)
DEPTH_STOP_ROWS = 3          # consecutive far rows that make a stop
DEPTH_BASE_TOL = 0.6         # base disparity within this x max(fitted, floor) of the frame's a / dist + b fit
DEPTH_FLOOR_FRAC = 0.04      # floor (share of the frame's 99th disparity percentile) under which noise rules
DEPTH_SPLIT_REL = 0.3        # relative disparity change over 4 px that splits a blob


# --------------------------------------------------------------------------- inputs

def frame_camera(view: dict, pose: dict, elev_m: float | None = None):
    """The pinhole camera of one frame (`server._seed_frame_pose`: views.json heading / pitch /
    fov, pose.json lat / lon / `cam_elev_m`, roll from `horizon_tilt_deg`)."""
    from pano_camera import frame_pose_parts

    from city2stl.skyline.photo_view import PinholeCamera
    w, h = int(view["width"]), int(view["height"])
    p = frame_pose_parts(view, pose, w)
    elev = float(pose["cam_elev_m"] if elev_m is None else elev_m)
    return PinholeCamera(float(pose["lat"]), float(pose["lon"]), elev, p["heading_deg"], p["pitch_deg"],
                         p["roll_deg"], p["f_px"], w, h)


def load_mask(path) -> np.ndarray:
    from PIL import Image
    with Image.open(path) as im:
        return np.array(im.convert("L"))


def load_depth(path, image_path=None) -> np.ndarray | None:
    """`<stem>_depth.npy` as float32 (relative disparity, larger = nearer), None when missing.
    Non-finite values and, when `image_path` is given, pixels where that image is pure black (the
    pano's wavy padding, which reads as non-sky) become NaN: they never stop a walk or split a blob."""
    p = Path(path)
    if not p.is_file():
        return None
    d = np.load(p).astype(np.float32)
    d[~np.isfinite(d)] = np.nan
    if image_path is not None and Path(image_path).is_file():
        from PIL import Image
        with Image.open(image_path) as im:
            rgb = np.array(im.convert("RGB"))
        if rgb.shape[:2] == d.shape:
            d[rgb.sum(axis=2) == 0] = np.nan
    return d


def footprints(geojson_path, near: tuple[float, float] | None = None, radius_m: float | None = None) -> dict:
    """FeatureCollection for `project_buildings`: property `id` = index in the returned list (`src` the source file index),
    `height` only for measured tags (a `default` source is the pipeline's filler), plus the
    centroid. `near` + `radius_m` keep only footprints around a point."""
    data = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    feats = []
    for i, f in enumerate(data.get("features", [])):
        g = f.get("geometry") or {}
        pr = f.get("properties") or {}
        rings = ([g["coordinates"][0]] if g.get("type") == "Polygon" else
                 [p[0] for p in g["coordinates"]] if g.get("type") == "MultiPolygon" else [])
        lat, lon = pr.get("centroid_lat"), pr.get("centroid_lon")
        for ring in rings:
            if len(ring) < 4:
                continue
            if lat is None:
                lon, lat = np.asarray(ring, float)[:-1].mean(axis=0)
            if near is not None and radius_m is not None:
                d = math.hypot((lon - near[1]) * M_PER_DEG_LAT * math.cos(math.radians(near[0])),
                               (lat - near[0]) * M_PER_DEG_LAT)
                if d > radius_m:
                    continue
            h = pr.get("height_m") if pr.get("height_source") not in (None, "default") else None
            feats.append({"type": "Feature",
                          "properties": {"id": len(feats), "src": i, "name": pr.get("name"), "height": h, "levels": None,
                                         "lat": float(lat), "lon": float(lon)},
                          "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": feats}


# --------------------------------------------------------------------------- geometry

def pixel_dirs(cam, x, y) -> np.ndarray:
    """Unit directions (east, north, up) of pixels."""
    right, up, fwd = cam.axes()
    x, y = np.asarray(x, float), np.asarray(y, float)
    d = (fwd[None, :] + ((x - cam.width / 2.0) / cam.f_px)[:, None] * right[None, :]
         - ((y - cam.height / 2.0) / cam.f_px)[:, None] * up[None, :])
    return d / np.linalg.norm(d, axis=1, keepdims=True)


def ray_ring_dist(ring_xy: np.ndarray, d_en: np.ndarray) -> float | None:
    """Distance along the ray from the origin with horizontal direction `d_en` (unit) to the first
    edge of `ring_xy` ((n, 2) metres, closed or not); None when it misses."""
    p = np.asarray(ring_xy, float)
    a, b = p[:-1], p[1:]
    e = b - a
    den = d_en[0] * (-e[:, 1]) - d_en[1] * (-e[:, 0])
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (a[:, 0] * (-e[:, 1]) - a[:, 1] * (-e[:, 0])) / den
        s = (a[:, 0] * d_en[1] - a[:, 1] * d_en[0]) / -den
    ok = np.isfinite(t) & np.isfinite(s) & (t > 0) & (s >= 0) & (s <= 1)
    return float(t[ok].min()) if ok.any() else None


def _local(cam, lonlat: np.ndarray) -> np.ndarray:
    kx = M_PER_DEG_LAT * math.cos(math.radians(cam.lat))
    return np.column_stack([(lonlat[:, 0] - cam.lon) * kx, (lonlat[:, 1] - cam.lat) * M_PER_DEG_LAT])


def column_height(cam, ring_ll: np.ndarray, ground_m: float, x: float, top_row: float) -> float | None:
    """Height (m above `ground_m`) of the mask top at column `x`, row `top_row` (edge of the first
    non-building pixel): camera elevation + horizontal distance to the footprint x tan(elevation)
    - ground."""
    d = pixel_dirs(cam, [x], [top_row - 0.5])[0]
    hz = math.hypot(d[0], d[1])
    if hz < 1e-6:
        return None
    dist = ray_ring_dist(_local(cam, ring_ll), d[:2] / hz)
    if dist is None:
        return None
    return float(cam.elev_m + dist * d[2] / hz - ground_m)


# --------------------------------------------------------------------------- per-frame

def _visible_bases(ids: np.ndarray, k: int):
    """(cols, base_row, osm_top_row) of building value `k`, for columns whose base is visible (open
    ground below the lowest pixel: not behind a nearer outline, not off the frame bottom)."""
    b = ids == k
    anyc = b.any(0)
    h = ids.shape[0]
    base = h - 1 - np.argmax(b[::-1], axis=0)
    top = np.argmax(b, axis=0)
    cols = np.where(anyc & (base < h - 4))[0]
    keep = [x for x in cols if not ids[base[x] + 1:base[x] + 4, x].any()]
    cols = np.asarray(keep, int)
    return cols, base[cols], top[cols]


def _mask_base_offset(mask: np.ndarray, x: int, base_row: int):
    """(floating, offset_px): whether the pixel inside the outline base is sky / water, and the
    rows from the outline base to the mask's building base below it (positive: the outline is
    above the building); offset None when no building is found."""
    h = mask.shape[0]
    y0 = max(base_row - BASE_INSET_PX, 0)
    c0 = mask[y0, x]
    floating = c0 in (SKY, WATER)
    col = mask[:, x]
    if c0 == BUILDING:
        y = y0
        while y + 1 < h and col[y + 1] == BUILDING:
            y += 1
        return floating, int(y - base_row)
    lo, hi = y0, min(y0 + SEARCH_DOWN_PX, h)
    idx = np.where(col[lo:hi] == BUILDING)[0]
    return floating, (int(lo + idx[0] - base_row) if idx.size else None)


def pose_gate(mask: np.ndarray, ids: np.ndarray, f_px: float) -> dict:
    """Frame-level pose check (see module docstring). `bad_pose` True when most base columns sit
    on sky / water or the median offset beyond `GATE_MAX_DEG`; `checked` False (and bad_pose
    False) when fewer than `GATE_MIN_FP` footprints have a visible base."""
    offs, flo, nfp = [], [], 0
    for k in range(1, int(ids.max()) + 1):
        cols, base, _ = _visible_bases(ids, k)
        if len(cols) < MIN_COLS:
            continue
        nfp += 1
        for j in range(0, len(cols), max(1, len(cols) // 12)):
            fl, off = _mask_base_offset(mask, int(cols[j]), int(base[j]))
            flo.append(fl)
            if off is not None:
                offs.append(off)
    out = {"n_footprints": nfp, "n_cols": len(flo), "checked": nfp >= GATE_MIN_FP and len(flo) > 0,
           "floating_frac": float(np.mean(flo)) if flo else None,
           "offset_deg": float(math.degrees(math.atan(np.median(offs) / f_px))) if offs else None}
    out["bad_pose"] = bool(out["checked"] and (out["floating_frac"] > GATE_FLOAT_FRAC or
                                               (out["offset_deg"] is not None and
                                                abs(out["offset_deg"]) > GATE_MAX_DEG)))
    return out


def _smooth_depth(depth):
    """Median-filtered copy of a disparity map (NaN kept) or None."""
    if depth is None:
        return None
    from scipy import ndimage
    d = np.asarray(depth, np.float32)
    ok = np.isfinite(d)
    out = ndimage.median_filter(np.where(ok, d, 0.0), size=5)
    return np.where(ok, out, np.nan).astype(np.float32)


def _fit_disparity(pts: list, scale: float):
    """Robust line disparity = a / dist + b through the (disparity, 1 / ray distance) pairs of a
    frame's footprint bases (two trimmed least-squares passes), as (a, b, floor) where `floor` =
    `DEPTH_FLOOR_FRAC` x the frame's 99th disparity percentile; None with fewer than 8 pairs."""
    if len(pts) < 8 or scale <= 0:
        return None
    y, x = np.asarray(pts, float).T
    keep = np.ones(len(x), bool)
    a = b = 0.0
    for _ in range(3):
        if keep.sum() < 4:
            return None
        a, b = np.polyfit(x[keep], y[keep], 1)
        res = np.abs(y - (a * x + b))
        keep = res <= max(2.5 * 1.4826 * float(np.median(res[keep])), 0.04 * scale)
    return float(a), float(b), DEPTH_FLOOR_FRAC * scale


def _base_disp(depth, x: int, y0: int) -> float:
    """Median disparity of the 4 rows ending at `y0` in column `x` (NaN when none is finite)."""
    seg = depth[max(y0 - 3, 0): y0 + 1, x]
    seg = seg[np.isfinite(seg)]
    return float(np.median(seg)) if seg.size else float("nan")


def column_top(mask: np.ndarray, ids: np.ndarray, x: int, base_row: int, osm_top: int,
               depth=None, x_mask: int | None = None):
    """Mask top row of the building at column `x` or None (why: second value). Walk up from just
    above the base through building / other pixels; the run must end in sky, hold mostly building,
    and not run on through a farther footprint's outline above this outline's top.
    `x_mask` reads the mask / depth at another column (the blob matched to the footprint); `ids` stay
    at `x`. With `depth` (smoothed disparity, larger = nearer) the run also ends at the first
    `DEPTH_STOP_ROWS` rows whose disparity is under the base's / `DEPTH_FAR_RATIO`: the top is the
    row below them and the reason `depth_stop`. Without depth the result is the plain mask walk."""
    xm = x if x_mask is None else x_mask
    y0 = base_row - BASE_INSET_PX
    if y0 < 1:
        return None, "edge"
    col = mask[: y0 + 1, xm][::-1]
    bo = (col == BUILDING) | (col == OTHER)
    clipped = bool(bo.all())
    r = len(bo) if clipped else int(np.argmin(bo))      # rows of the run above y0
    why = "ok"
    if depth is not None and r > DEPTH_STOP_ROWS:
        ref = _base_disp(depth, xm, y0)
        if math.isfinite(ref):
            far = depth[y0 - r + 1: y0 + 1, xm][::-1] < ref / DEPTH_FAR_RATIO
            hit = np.where(np.convolve(far.astype(int), np.ones(DEPTH_STOP_ROWS, int), "valid")
                           == DEPTH_STOP_ROWS)[0]
            if hit.size:
                r = int(hit[0])
                why, clipped = "depth_stop", False
    if why == "ok":
        if clipped:
            return None, "clipped"
        stop = mask[y0 - r, xm]
        if stop != SKY:
            return None, "veg" if stop == VEG else "water"
    top = y0 - r + 1
    run = mask[top: y0 + 1, xm]
    if run.size == 0 or (run == BUILDING).mean() < 0.5:
        return None, "not_building"
    if why == "ok" and top < osm_top:
        above = ids[top: osm_top, x]
        if above.size and (above > 0).mean() > 0.5:
            return None, "ambiguous"
    return top, why


def classify(margin_deg: float | None) -> str:
    if margin_deg is None:
        return "no_reading"
    return "too_short" if margin_deg > TOL_DEG else "too_tall" if margin_deg < -TOL_DEG else "ok"


def unmatched_blobs(mask: np.ndarray, ids: np.ndarray, cam, shifts: dict | None = None) -> list[dict]:
    """Mask building blobs (>= `MIN_BLOB_PX`) that no projected outline covers. `shifts` ({building
    value: shift px}, from `assign_blobs`) moves each outline sideways before the test."""
    from scipy import ndimage
    lab, n = ndimage.label(mask == BUILDING)
    cov = ids > 0
    if shifts:
        cov = cov.copy()
        for k, sx in shifts.items():
            if sx:
                cov |= np.roll(ids == k, int(sx), axis=1)
    cover = ndimage.binary_dilation(cov, iterations=6)
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        m = lab[sl] == i
        a = int(m.sum())
        if a < MIN_BLOB_PX:
            continue
        frac = float((cover[sl] & m).sum()) / a
        if frac >= BLOB_COVER:
            continue
        x0, x1 = sl[1].start, sl[1].stop - 1
        cx = (x0 + x1) / 2.0
        head = float(cam.heading_deg + math.degrees(math.atan((cx - cam.width / 2) / cam.f_px)))
        out.append({"cols": [int(x0), int(x1)], "top_row": int(sl[0].start), "area_px": a,
                    "bearing_deg": head % 360.0, "covered": round(frac, 3)})
    return out


def _split_at_valleys(lab: np.ndarray, n: int) -> tuple[np.ndarray, int]:
    """Split each connected blob at the valleys of its top profile: a merged skyline (towers joined
    through the low-rise mass at their feet) gives one piece per peak that rises `PEAK_PROM_PX`
    above its saddles, cut at the lowest column between two peaks."""
    from scipy.signal import find_peaks
    h, w = lab.shape
    out, nxt = lab.copy(), n + 1
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        if xs.size < MIN_BLOB_PX:
            continue
        x0, x1 = int(xs.min()), int(xs.max())
        top = np.full(w, h)
        np.minimum.at(top, xs, ys)
        prof = (h - top[x0: x1 + 1]).astype(float)
        pk, _ = find_peaks(np.pad(prof, 1), prominence=PEAK_PROM_PX)
        pk = pk - 1
        cuts = [x0 + int(a + np.argmin(prof[a: b + 1])) for a, b in zip(pk[:-1], pk[1:])]
        if not cuts:
            continue
        piece = np.searchsorted(cuts, np.arange(w), side="left")        # column -> piece index
        for q in range(1, len(cuts) + 1):
            out[(lab == i) & (piece[None, :] == q)] = nxt
            nxt += 1
    return out, nxt - 1


def _building_labels(mask: np.ndarray, depth):
    """(label image, count) of building blobs: connected pixels, cut first where the disparity
    changes by more than `DEPTH_SPLIT_REL` (relative) over 4 px, in x or y (two towers at different
    distances), then split at the valleys of each blob's top profile (`_split_at_valleys`).
    Padding / invalid depth (NaN) never cuts."""
    from scipy import ndimage
    bm = mask == BUILDING
    if depth is not None:
        ok = np.isfinite(depth)
        d = np.where(ok, depth, 0.0)
        cut = np.zeros(bm.shape, bool)
        for ax in (0, 1):
            a, b = np.roll(d, 2, ax), np.roll(d, -2, ax)
            both = np.roll(ok, 2, ax) & np.roll(ok, -2, ax)
            rel = np.abs(a - b) / np.maximum(np.maximum(np.abs(a), np.abs(b)), 0.25)
            cut |= both & (rel > DEPTH_SPLIT_REL)
        bm = bm & ~cut
    lab, n = ndimage.label(bm)
    return _split_at_valleys(lab, n)


def assign_blobs(mask: np.ndarray, ids: np.ndarray, cam, depth=None) -> dict:
    """Match each tower-class footprint (visible box at least `TOWER_MIN_PX` tall) to a building
    blob of the mask (see the module docstring). The gain of a shift is the share of the footprint's
    visible box on the blob; cost = -gain + `SHIFT_PENALTY` x |shift deg|, solved by
    `linear_sum_assignment` with a free "stay put" option, so no blob is taken twice. Returns
    {"assign": {building value k: {blob, shift_px, shift_deg, overlap}}, "missing_tower": [...],
    "n_blobs"}; footprints missing from "assign" (small boxes) stay unshifted. `blob` None (shift 0)
    when no blob of fitting width (at most `BLOB_WIDTH_RATIO` x box width) covers `MIN_OVERLAP` of
    the box within the window. A blob that two footprints cover (>= `SHARE_FRAC`) without any shift
    holds both: they stay put. A blob nobody claims whose top rises above every claimed blob is a
    `missing_tower` candidate (bearing, top elevation angle, px area): reported only."""
    from scipy.optimize import linear_sum_assignment
    lab, n = _building_labels(mask, depth)
    area = np.bincount(lab.ravel(), minlength=n + 1)
    blobs = [b for b in range(1, n + 1) if area[b] >= MIN_BLOB_PX]
    bw = {b: int(np.ptp(np.nonzero((lab == b).any(0))[0])) + 1 for b in blobs}
    out: dict = {"assign": {}, "missing_tower": [], "n_blobs": len(blobs)}
    h, w = mask.shape
    S = int(round(cam.f_px * math.tan(math.radians(MAX_SHIFT_DEG))))
    shifts = np.arange(-S, S + 1)
    pen = SHIFT_PENALTY * np.degrees(np.arctan(np.abs(shifts) / cam.f_px))
    labp = np.pad(lab, ((0, 0), (S, S)))
    fps, ov = [], {}
    for k in range(1, int(ids.max()) + 1):
        cols, base, otop = _visible_bases(ids, k)
        if len(cols) < MIN_COLS or float(np.median(base - otop)) < TOWER_MIN_PX:
            continue
        ys, xs = np.nonzero(ids == k)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        box = ids[y0:y1, x0:x1] == k
        tab = np.zeros((len(shifts), n + 1))
        for j, sx in enumerate(shifts):
            crop = labp[y0:y1, x0 + S + sx: x1 + S + sx]
            tab[j] = np.bincount(crop[box], minlength=n + 1)
        fps.append(k)
        out["assign"][k] = {"blob": None, "shift_px": 0, "shift_deg": 0.0, "overlap": 0.0}
        for b in blobs:
            if tab[:, b].max() > 0 and bw[b] <= BLOB_WIDTH_RATIO * (x1 - x0):
                ov[(k, b)] = tab[:, b] / float(box.sum())          # share of the box on the blob, per shift
    frac = zero = {kb: float(v[S]) for kb, v in ov.items()}
    shared = {}
    for b in blobs:
        own = [k for k in fps if frac.get((k, b), 0.0) >= SHARE_FRAC]
        if len(own) >= 2:
            shared[b] = own
    fixed = {k for own in shared.values() for k in own}
    for b, own in shared.items():
        for k in own:
            if out["assign"][k]["blob"] is None:
                out["assign"][k].update(blob=b, overlap=frac[(k, b)])
    free_k = [k for k in fps if k not in fixed]
    free_b = [b for b in blobs if b not in shared and any((k, b) in ov for k in free_k)]
    chosen = set()
    if free_k and free_b:
        cost = np.ones((len(free_k), len(free_b) + len(free_k)))
        cost[:, len(free_b):] = 0.0                      # dummy: leave the footprint unshifted
        best = {}
        for i, k in enumerate(free_k):
            for j, b in enumerate(free_b):
                v = ov.get((k, b))
                if v is None:
                    continue
                c = np.where(v >= MIN_OVERLAP, -v + pen, 1.0)
                m = int(np.argmin(c))
                cost[i, j] = c[m]
                best[(i, j)] = m
        for i, j in zip(*linear_sum_assignment(cost)):
            if j < len(free_b) and cost[i, j] < 0:
                k, b = free_k[i], free_b[j]
                sx = int(shifts[best[(i, j)]])
                out["assign"][k] = {"blob": b, "shift_px": sx, "overlap": float(ov[(k, b)][best[(i, j)]]),
                                    "shift_deg": float(np.degrees(np.arctan(sx / cam.f_px)))}
                chosen.add(b)
    explained = chosen | set(shared) | {b for (k, b), v in zero.items() if v >= MIN_OVERLAP}
    tops = {}
    for b in blobs:
        ys, xs = np.nonzero(lab == b)
        colmin = np.full(w, h)
        np.minimum.at(colmin, xs, ys)
        tops[b] = (float(np.percentile(colmin[colmin < h], 10)), float(xs.mean()))
    claimed_tops = [tops[b][0] for b in chosen | set(shared)]
    if claimed_tops:
        ref = min(claimed_tops)
        for b in blobs:
            if b in explained or tops[b][0] >= ref:
                continue
            ty, cx = tops[b]
            d = pixel_dirs(cam, [cx], [ty])[0]
            out["missing_tower"].append({
                "bearing_deg": float((cam.heading_deg + math.degrees(math.atan((cx - w / 2) / cam.f_px))) % 360.0),
                "top_elev_deg": float(math.degrees(math.asin(max(-1.0, min(1.0, d[2]))))),
                "area_px": int(area[b]), "top_row": int(ty), "cx": int(cx)})
    return out


def analyse_frame(mask: np.ndarray, cam, fc: dict, dem, ground_of=None, depth=None, assign: bool = True,
                  depth_stop: bool = False) -> dict:
    """Gate, per-footprint rows, unmatched blobs and `missing_tower` candidates of one frame. `fc`:
    `footprints()` output; `depth`: `load_depth()` array (relative disparity) or None; `assign`
    False reads the mask at the unshifted spans (the pre-F-MASKFIT behaviour). Depth always splits
    blobs for `assign_blobs`; `depth_stop` True also applies the depth stop in `column_top` and the
    `depth_base` drop (off by default: on Cartagena it added too-short readings, see
    docs/decisions/photo-label.md)."""
    import terrain_solve as ts
    proj = ts.project_buildings(dem, fc, cam)
    ids = proj["ids"]
    by_id = {f["properties"]["id"]: f for f in fc["features"]}
    gate = pose_gate(mask, ids, cam.f_px)
    dep = _smooth_depth(depth)
    ab = assign_blobs(mask, ids, cam, dep) if assign and not gate["bad_pose"] else {"assign": {}, "missing_tower": []}
    rows, work = [], []
    for k, b in enumerate(proj["buildings"], start=1):
        feat = by_id[b["id"]]
        cols, base, otop = _visible_bases(ids, k)
        a = ab["assign"].get(k, {})
        row = {"id": b["id"], "dist_m": round(b["dist_m"], 1), "visible_px": b["visible_px"],
               "osm_height_m": b["height_m"], "n_base_cols": int(len(cols)), "class": "no_reading",
               "height_m": None, "n_cols": 0, "margin_px": None, "margin_deg": None, "dropped": {},
               "shift_deg": round(float(a.get("shift_deg", 0.0)), 2), "blob": a.get("blob")}
        if len(cols) < MIN_COLS or gate["bad_pose"]:
            row["class"] = "bad_pose" if gate["bad_pose"] else "no_reading"
            rows.append(row)
            continue
        ring = np.asarray(feat["geometry"]["coordinates"][0], float)
        zb = float(np.nanmin(np.asarray(ground_of(ring[:, 1], ring[:, 0]), float)))
        sx = int(a.get("shift_px", 0))
        sel = np.unique(np.linspace(0, len(cols) - 1, min(len(cols), MAX_COL_SAMPLES)).astype(int))
        ring_m = _local(cam, ring)
        cs = []                                       # (x, x read, base row, osm top row, disp x dist)
        for j in sel:
            x, br, ot = int(cols[j]), int(base[j]), int(otop[j])
            xr = min(max(x + sx, 0), mask.shape[1] - 1)
            kd = None
            if dep is not None:
                db = _base_disp(dep, xr, br - BASE_INSET_PX)
                d = pixel_dirs(cam, [x], [br])[0]
                hz = math.hypot(d[0], d[1])
                dist = ray_ring_dist(ring_m, d[:2] / hz) if hz > 1e-6 else None
                if dist is not None and math.isfinite(db):
                    kd = (db, 1.0 / dist)
            cs.append((x, xr, br, ot, kd))
        work.append((row, feat, ring, zb, cs))
        rows.append(row)
    fit = _fit_disparity([c[4] for w_ in work for c in w_[4] if c[4] is not None],
                         float(np.nanpercentile(dep, 99)) if dep is not None and np.isfinite(dep).any() else 0.0)
    for row, feat, ring, zb, cs in work:
        hs, mg, drops = [], [], {}
        for x, xr, br, ot, kd in cs:
            if depth_stop and kd is not None and fit is not None and abs(kd[0] - fit[0] * kd[1] - fit[1]) >                     DEPTH_BASE_TOL * max(fit[0] * kd[1] + fit[1], fit[2]):
                drops["depth_base"] = drops.get("depth_base", 0) + 1
                continue
            top, why = column_top(mask, ids, x, br, ot, dep if depth_stop else None, x_mask=xr)
            if top is None:
                drops[why] = drops.get(why, 0) + 1
                continue
            h = column_height(cam, ring, zb, x, top)
            if h is None or not math.isfinite(h):
                drops["no_ray"] = drops.get("no_ray", 0) + 1
                continue
            hs.append(h)
            if why == "depth_stop":
                row["n_depth_stop"] = row.get("n_depth_stop", 0) + 1
            if ot > 0:
                mg.append(ot - top)
        row["dropped"] = drops
        row["n_cols"] = len(hs)
        if len(hs) >= MIN_COLS and mg:
            row["height_m"] = float(np.median(hs))
            row["margin_px"] = float(np.median(mg))
            row["margin_deg"] = float(math.degrees(math.atan(row["margin_px"] / cam.f_px)))
            row["class"] = classify(row["margin_deg"])
            row["height_spread_ln"] = float(np.log(max(hs) / max(min(hs), 0.1))) if min(hs) > 0 else None
        row["lat"], row["lon"] = feat["properties"]["lat"], feat["properties"]["lon"]
    shifts = {k: a["shift_px"] for k, a in ab["assign"].items()}
    blobs = [] if gate["bad_pose"] else unmatched_blobs(mask, ids, cam, shifts)
    return {"gate": gate, "rows": rows, "unmatched": blobs, "missing_tower": ab["missing_tower"]}


# --------------------------------------------------------------------------- across frames

def agree_group(vals: list[float]) -> list[int]:
    """Indices of the largest set of heights within `ln 1.25` of one member (ties: the one with the
    smaller spread)."""
    best: list[int] = []
    for a in vals:
        g = [j for j, b in enumerate(vals) if a > 0 and b > 0 and abs(math.log(a / b)) <= AGREE_LN]
        if len(g) > len(best):
            best = g
    return best


def find_reassignments(rows: list[dict], prior_of) -> list[dict]:
    """Within one frame: a `too_tall` outline A (rises into sky) whose neighbour B (<= 80 m) has a
    mask height within `ln 1.25` of A's prior height: A's height belongs to B. An A that
    `assign_blobs` matched to a blob (`blob` set) is skipped: its mask was found, the outline is just
    too tall."""
    out = []
    cand = [r for r in rows if r.get("height_m")]
    for a in rows:
        if a.get("class") != "too_tall" or "lat" not in a or a.get("blob") is not None:
            continue          # a footprint matched to a blob is truly too tall; only unmatched ones lend their height
        pa = prior_of(a["id"], a)
        best = None
        for b in cand:
            if b["id"] == a["id"]:
                continue
            d = math.hypot((b["lon"] - a["lon"]) * M_PER_DEG_LAT * math.cos(math.radians(a["lat"])),
                           (b["lat"] - a["lat"]) * M_PER_DEG_LAT)
            if d <= NEIGHBOUR_M and abs(math.log(b["height_m"] / pa)) <= AGREE_LN and \
                    (best is None or d < best[0]):
                best = (d, b)
        if best:
            out.append({"from": a["id"], "to": best[1]["id"], "height_m": round(best[1]["height_m"], 1),
                        "prior_m": round(pa, 1), "dist_m": round(best[0], 1)})
    return out


def aggregate(frames: list[dict]) -> dict:
    """Per-footprint summary across frames. `frames`: dicts with `seed`, `frame`, `weak`,
    `gate`, `rows`, `reassign`. Returns {id: {views, heights, classes, agreed_m, n_agree, ...}}."""
    fp: dict = {}
    for fr in frames:
        for r in fr["rows"]:
            e = fp.setdefault(r["id"], {"views": [], "lat": r.get("lat"), "lon": r.get("lon"),
                                        "reassigned_to": [], "reassigned_from": []})
            e["views"].append({"seed": fr["seed"], "frame": fr["frame"], "weak": fr["weak"],
                               "class": r["class"], "height_m": r["height_m"],
                               "margin_deg": r["margin_deg"], "n_cols": r["n_cols"]})
            if e["lat"] is None and r.get("lat") is not None:
                e["lat"], e["lon"] = r["lat"], r["lon"]
        for ra in fr.get("reassign", []):
            fp.setdefault(ra["from"], {"views": [], "lat": None, "lon": None, "reassigned_to": [],
                                       "reassigned_from": []})["reassigned_to"].append(
                {**ra, "seed": fr["seed"], "frame": fr["frame"]})
            fp.setdefault(ra["to"], {"views": [], "lat": None, "lon": None, "reassigned_to": [],
                                     "reassigned_from": []})["reassigned_from"].append(
                {**ra, "seed": fr["seed"], "frame": fr["frame"]})
    for e in fp.values():
        good = [v for v in e["views"] if v["height_m"] and not v["weak"]]
        e["heights"] = [round(v["height_m"], 1) for v in e["views"] if v["height_m"]]
        cls = [v["class"] for v in e["views"] if v["class"] not in ("no_reading", "bad_pose")]
        e["classification"] = (max(set(cls), key=cls.count) if cls else
                               ("bad_pose" if any(v["class"] == "bad_pose" for v in e["views"]) else "no_reading"))
        e["agreed_m"], e["n_agree"], e["seeds_agree"] = None, 0, 0
        if len(good) >= 2:
            g = agree_group([v["height_m"] for v in good])
            if len(g) >= 2:
                e["agreed_m"] = round(float(math.exp(np.mean([math.log(good[i]["height_m"]) for i in g]))), 1)
                e["n_agree"] = len(g)
                e["seeds_agree"] = len({good[i]["seed"] for i in g})
    return fp


# --------------------------------------------------------------------------- ids, readings

def fid_index(reference: dict, tol_m: float = 3.0):
    """Closure `(lat, lon) -> fid | None` over `{fid: (lat, lon)}` (nearest within `tol_m`)."""
    grid: dict = {}
    for fid, (la, lo) in reference.items():
        grid.setdefault((round(la, 3), round(lo, 3)), []).append((fid, la, lo))

    def find(lat, lon):
        best = None
        for dy in (-0.001, 0, 0.001):
            for dx in (-0.001, 0, 0.001):
                for fid, la, lo in grid.get((round(lat + dy, 3), round(lon + dx, 3)), []):
                    d = math.hypot((lat - la) * M_PER_DEG_LAT, (lon - lo) * M_PER_DEG_LAT * math.cos(math.radians(lat)))
                    if d <= tol_m and (best is None or d < best[0]):
                        best = (d, fid)
        return best[1] if best else None

    return find


def to_readings(summary: dict, fid_of) -> tuple[dict, dict]:
    """`(readings, where)` for `readings.save`: a `photo_mask` `SatReading` per agreed footprint
    that has a feature id. conf 0.5 for 2 views, 0.65 for 3 or more, 0.8 when 2+ seeds agree."""
    from city2stl.height.satellite.readings import SatReading
    out, where = {}, {}
    for e in summary.values():
        if e["agreed_m"] is None or e["lat"] is None:
            continue
        fid = fid_of(e["lat"], e["lon"])
        if fid is None:
            continue
        conf = 0.8 if e["seeds_agree"] >= 2 else 0.65 if e["n_agree"] >= 3 else 0.5
        out[fid] = [SatReading("photo_mask", e["agreed_m"], conf, scene="pano_seeds", n_scenes=e["n_agree"],
                               extra={"seeds": e["seeds_agree"], "views": e["n_agree"]})]
        where[fid] = {"lat": e["lat"], "lon": e["lon"], "osm_id": None}
    return out, where


def merge_production(path, readings: dict, where: dict, backup_suffix: str = ".bak-2026-10-31") -> dict:
    """Merge `photo_mask` readings into the production readings file (never created): backup beside
    it, replace a footprint's old `photo_mask`, keep every other reading. Returns counts, or
    ``{"written": False, "expected": path}`` when the file is missing."""
    from city2stl.height.satellite import readings as sr
    p = Path(path)
    if not p.is_file():
        return {"written": False, "expected": str(p)}
    bak = p.with_name(p.name + backup_suffix)
    if not bak.exists():
        shutil.copy2(p, bak)
    meta, data = sr.load(p, calibrate=False)
    meta.pop("n_recalibrated", None)
    ver = meta.get("version")   # save() stamps READINGS_VERSION; the caller's file keeps its own
    new = skipped = 0
    rs = {fid: v["readings"] for fid, v in data.items()}
    wh = {fid: {k: v.get(k) for k in ("lat", "lon", "osm_id")} for fid, v in data.items()}
    for fid in rs:                                   # a rerun replaces the whole previous set
        rs[fid] = [r for r in rs[fid] if r.method != "photo_mask"]
    for fid, add in readings.items():
        if fid not in rs:
            rs[fid], wh[fid] = [], where[fid]
            new += 1
        rs[fid] = [r for r in rs[fid] if r.method != "photo_mask"] + add
    meta["photo_mask"] = {"n": len(readings), "source": "tools/photo_label/mask_heights.py",
                          "date": "2026-10-31"}
    sr.save(p, rs, {k: v for k, v in meta.items() if k != "version"}, wh)
    if ver is not None and ver != sr.READINGS_VERSION:
        from geo2stl.cache import atomic_write_json
        body = json.loads(p.read_text(encoding="utf-8"))
        body["_meta"]["version"] = ver
        atomic_write_json(p, body, indent=0)
    return {"written": True, "path": str(p), "backup": str(bak), "merged": len(readings),
            "new_footprints": new, "skipped": skipped, "version_before": ver}


# --------------------------------------------------------------------------- scoring

BOCAGRANDE = {"Estelar": 202.0, "Allure": 190.0, "Gran Bay": 170.0, "Portomarine": 188.0,
              "Nautica": 161.0, "Ravello": 160.0, "Palmetto": 156.0}


def score(summary: dict, fid_of, published: dict) -> dict:
    """Agreed height against `published` (`{fid: {published_m, name, lat, lon}}`, from heights.json)
    and the 7 Bocagrande towers (matched by `published_m`, then the nearest published entry)."""
    by_fid = {}
    for e in summary.values():
        if e["lat"] is not None:
            fid = fid_of(e["lat"], e["lon"])
            if fid:
                by_fid[fid] = e
    rows = []
    for fid, p in published.items():
        e = by_fid.get(fid)
        rows.append({"fid": fid, "name": p.get("name"), "published_m": p["published_m"],
                     "agreed_m": e["agreed_m"] if e else None,
                     "single_m": (e["heights"][0] if e and e["heights"] else None),
                     "n_views": len(e["heights"]) if e else 0})
    towers = []
    for name, pub in BOCAGRANDE.items():
        m = [r for r in rows if abs(r["published_m"] - pub) < 0.05]
        r = m[0] if m else None
        towers.append({"tower": name, "published_m": pub, "fid": r["fid"] if r else None,
                       "agreed_m": r["agreed_m"] if r else None, "single_m": r["single_m"] if r else None,
                       "n_views": r["n_views"] if r else 0})
    ev = [abs(math.log(r["agreed_m"] / r["published_m"])) for r in rows if r["agreed_m"]]
    return {"towers": towers, "vs_published": rows, "n_scored": len(ev),
            "median_abs_ln": float(np.median(ev)) if ev else None,
            "within_25pct": int(sum(e <= AGREE_LN for e in ev))}


def google3d_yardstick(*_a, **_k):
    """TODO: score the agreed heights against the Google 3D mesh once a render is allowed
    (no paid Google call in this module)."""
    return None
