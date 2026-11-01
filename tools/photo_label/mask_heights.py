"""Auto-correct loop (plan F): read building heights off the pano seed frames' class masks.

Per frame, with the frame's pinhole camera (`views.json` + `pose.json`):

1. **Pose gate** (`pose_gate`): project the footprints (`terrain_solve.project_buildings`), look at
   the mask just above each visible footprint base. Outlines floating on sky / water, or a median
   base-row offset from the mask's building base over `GATE_MAX_DEG`, make the frame `bad_pose`:
   it gives no heights (the seed 6 `overhead` pose put outlines ~12 deg above the horizon).
2. **Top edge** (`column_top`): from each visible base column walk up the mask through building /
   other pixels to sky. Columns ending in vegetation or water, clipped at the frame top, or whose
   run goes on through a farther footprint's outline are dropped.
3. **Class** (`classify`): `ok` / `too_short` (mask rises above the outline) / `too_tall` (outline
   rises into sky) by the median margin in px and degrees; mask building blobs that no outline
   covers are `unmatched_building` (`unmatched_blobs`).
4. **Height** (`column_height`): elevation angle of the mask top x horizontal distance to the
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


# --------------------------------------------------------------------------- inputs

def frame_camera(view: dict, pose: dict, elev_m: float | None = None):
    """The pinhole camera of one frame (`server._seed_frame_pose`: views.json heading / pitch /
    fov, pose.json lat / lon / `cam_elev_m`, roll from `horizon_tilt_deg`)."""
    from city2stl.skyline.photo_view import PinholeCamera
    w, h = int(view["width"]), int(view["height"])
    f_px = w / 2 / math.tan(math.radians(float(view["fov_deg"])) / 2)
    elev = float(pose["cam_elev_m"] if elev_m is None else elev_m)
    return PinholeCamera(float(pose["lat"]), float(pose["lon"]), elev, float(view["heading_deg"]),
                         float(view.get("pitch_deg") or 0.0), float(pose.get("horizon_tilt_deg") or 0.0),
                         f_px, w, h)


def load_mask(path) -> np.ndarray:
    from PIL import Image
    with Image.open(path) as im:
        return np.array(im.convert("L"))


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


def column_top(mask: np.ndarray, ids: np.ndarray, k: int, x: int, base_row: int, osm_top: int):
    """Mask top row of the building at column `x` or None (why: second value). Walk up from just
    above the base through building / other pixels; the run must end in sky, hold mostly building,
    and not run on through a farther footprint's outline above this outline's top."""
    y0 = base_row - BASE_INSET_PX
    if y0 < 1:
        return None, "edge"
    bo = (mask[: y0 + 1, x][::-1] == BUILDING) | (mask[: y0 + 1, x][::-1] == OTHER)
    if bo.all():
        return None, "clipped"
    r = int(np.argmin(bo))
    stop = mask[y0 - r, x]
    if stop != SKY:
        return None, "veg" if stop == VEG else "water"
    top = y0 - r + 1
    run = mask[top: y0 + 1, x]
    if run.size == 0 or (run == BUILDING).mean() < 0.5:
        return None, "not_building"
    if top < osm_top:
        above = ids[top: osm_top, x]
        if above.size and (above > 0).mean() > 0.5:
            return None, "ambiguous"
    return top, "ok"


def classify(margin_deg: float | None) -> str:
    if margin_deg is None:
        return "no_reading"
    return "too_short" if margin_deg > TOL_DEG else "too_tall" if margin_deg < -TOL_DEG else "ok"


def unmatched_blobs(mask: np.ndarray, ids: np.ndarray, cam) -> list[dict]:
    """Mask building blobs (>= `MIN_BLOB_PX`) that no projected outline covers."""
    from scipy import ndimage
    lab, n = ndimage.label(mask == BUILDING)
    cover = ndimage.binary_dilation(ids > 0, iterations=6)
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


def analyse_frame(mask: np.ndarray, cam, fc: dict, dem, ground_of=None) -> dict:
    """Gate, per-footprint rows and unmatched blobs of one frame. `fc`: `footprints()` output."""
    import terrain_solve as ts
    proj = ts.project_buildings(dem, fc, cam)
    ids = proj["ids"]
    by_id = {f["properties"]["id"]: f for f in fc["features"]}
    gate = pose_gate(mask, ids, cam.f_px)
    rows = []
    for k, b in enumerate(proj["buildings"], start=1):
        feat = by_id[b["id"]]
        cols, base, otop = _visible_bases(ids, k)
        row = {"id": b["id"], "dist_m": round(b["dist_m"], 1), "visible_px": b["visible_px"],
               "osm_height_m": b["height_m"], "n_base_cols": int(len(cols)), "class": "no_reading",
               "height_m": None, "n_cols": 0, "margin_px": None, "margin_deg": None, "dropped": {}}
        if len(cols) < MIN_COLS or gate["bad_pose"]:
            row["class"] = "bad_pose" if gate["bad_pose"] else "no_reading"
            rows.append(row)
            continue
        ring = np.asarray(feat["geometry"]["coordinates"][0], float)
        zb = float(np.nanmin(np.asarray(ground_of(ring[:, 1], ring[:, 0]), float)))
        sel = np.unique(np.linspace(0, len(cols) - 1, min(len(cols), MAX_COL_SAMPLES)).astype(int))
        hs, mg, drops = [], [], {}
        for j in sel:
            x, br, ot = int(cols[j]), int(base[j]), int(otop[j])
            top, why = column_top(mask, ids, k, x, br, ot)
            if top is None:
                drops[why] = drops.get(why, 0) + 1
                continue
            h = column_height(cam, ring, zb, x, top)
            if h is None or not math.isfinite(h):
                drops["no_ray"] = drops.get("no_ray", 0) + 1
                continue
            hs.append(h)
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
        rows.append({**row, "lat": feat["properties"]["lat"], "lon": feat["properties"]["lon"]})
    blobs = [] if gate["bad_pose"] else unmatched_blobs(mask, ids, cam)
    return {"gate": gate, "rows": rows, "unmatched": blobs}


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
    mask height within `ln 1.25` of A's prior height: A's height belongs to B."""
    out = []
    cand = [r for r in rows if r.get("height_m")]
    for a in rows:
        if a.get("class") != "too_tall" or "lat" not in a:
            continue
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
