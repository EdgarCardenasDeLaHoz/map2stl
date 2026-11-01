"""Automatic screening of unusable photos for the photo-label tool.

Rules (reasons start "auto: "), measured on 74 Cartagena photos against the user's manual flags and,
for the geometry rules, on 6 Granada Street View frames (BAD) and 48 Cartagena drone frames (GOOD),
both with known poses (``claude/scripts/screen_metrics.py`` -> ``runs/screen_metrics.json``):

* **Stitched panorama**: width / height >= ``PANORAMA_ASPECT`` (3.5). Flagged panoramas were
  4.06-4.75; the widest kept photo was 2.33. The pinhole model does not fit them. Not applied to
  a photo with a known pose (a seed strip is a panorama by design).
* **Street level, camera height** (known pose only): camera less than ``STREET_CAM_M`` (5 m) above the
  DEM ground. Street View frames sit at 2.5 m; every drone frame is 47.5-182 m. Separates 6/6 from 0/48.
* **Too close** (known pose only): the nearest projected, unoccluded footprint (>= 0.05 % of the photo
  visible) is under ``CLOSE_M`` (50 m) from the camera. Granada 4.6-36 m, drone frames 225-1905 m.
* **Street level, ground share**: pixels in the ground classes (floor, road, sidewalk, earth, person,
  path) >= ``GROUND_MAX`` (0.18). Granada frames 0.20-0.39, drone frames <= 0.011, kept Cartagena photos
  <= 0.151. It was 0.25 (the 15 earlier Street View frames read 0.34-0.51) but that missed 4/6 Granada
  frames, which are pedestrian-level and 0.20-0.23 ground.

Measured and rejected (computed in ``measure``/``measure_pose``, no rule): monocular-depth "near block"
share (wall/building/tree pixels with normalised disparity > 0.6; Granada median 0.03, kept photos 0.12,
so it points the wrong way: DA2 inverse depth is relative, a narrow street is not nearer in it), skyline
columns resting on far buildings (4/6 Granada but 8/54 kept photos, whose sky is cropped), and a
"few buildings visible" count (projected: 15/48 drone frames show <= 3 because a drone over water
sees few footprints; segmented far building segments == 0: 9/54 kept and 19/48 drone frames; SegFormer
reads a distant city as mountain / earth, e.g. two good La Popa views read 0.002 and 0.007 building).
There is deliberately no brightness rule (the user kept night shots).

Order: aspect, geometry (cheap, no model), then SegFormer on photos not yet caught. A result is cached
beside the photo as ``<stem>_screen.json`` and never recomputed (delete it after changing a threshold).
"""
from __future__ import annotations

import json
from pathlib import Path

GROUND_CLASSES = (3, 6, 11, 13, 12, 52)   # ADE20K (0-based): floor, road, sidewalk, earth, person, path
GROUND_MAX = 0.18
STREET_CAM_M = 5.0                        # known pose: camera below this above ground = street level
CLOSE_M = 50.0                            # known pose: nearest visible footprint closer than this = too close
PANORAMA_ASPECT = 3.5
MAX_SIDE = 1024                           # long side SegFormer sees


def screen_path(photo: Path) -> Path:
    return photo.with_name(photo.stem + "_screen.json")


def _aspect_reason(aspect: float) -> str:
    return f"auto: stitched panorama (aspect {aspect:.1f})" if aspect >= PANORAMA_ASPECT else ""


def geometry_reason(geometry: dict | None) -> str:
    """Reason from `measure_pose` output (known pose): camera at street height, else a footprint too close."""
    if not geometry:
        return ""
    cam, near = geometry.get("cam_height_m"), geometry.get("nearest_building_m")
    if cam is not None and cam < STREET_CAM_M:
        return f"auto: street level (camera {cam:.0f} m)"
    if near is not None and near < CLOSE_M:
        return f"auto: too close (nearest building {near:.0f} m)"
    return ""


def screen_from_labels(labels, w: int, h: int, geometry: dict | None = None, known_pose: bool = False) -> dict:
    """{ground, aspect, reason} from an ADE20K label map (or None), the photo size and, for a known pose,
    `measure_pose` output. `reason` is "" for a usable photo. Order: aspect (skipped when `known_pose`),
    geometry, ground share; with `labels` None only the first two apply."""
    aspect = w / h if h else 0.0
    reason = "" if known_pose else _aspect_reason(aspect)
    reason = reason or geometry_reason(geometry)
    ground = None
    if labels is not None:
        import numpy as np
        ground = float(np.isin(labels, GROUND_CLASSES).mean())
        if not reason and ground >= GROUND_MAX:
            reason = f"auto: street level (ground {ground * 100:.0f}%)"
    return {"ground": ground, "aspect": round(aspect, 3), "reason": reason}


def _ensure_label_map(rgb):
    from city2stl.skyline._core.segmentation import _ensure_label_map as f
    return f(rgb)


def _write(path: Path, res: dict) -> None:
    from geo2stl.cache import atomic_write_json
    atomic_write_json(path, res)


def screen_photo(path: Path, labels: bool = True, geometry: dict | None = None,
                 known_pose: bool = False) -> dict | None:
    """Screen one photo and cache the result. A cached file is returned as is. `geometry` is
    `measure_pose` output and `known_pose` says the camera is known (no aspect rule). With `labels`
    False only the aspect and geometry rules run; a photo it does not catch returns None and is not cached,
    so a later run with a GPU can still segment it."""
    cache = screen_path(path)
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    res = screen_from_labels(None, w, h, geometry, known_pose)
    if not res["reason"]:
        if not labels:
            return None
        import cv2
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"cannot read photo: {path}")
        k = MAX_SIDE / max(img.shape[:2])
        if k < 1:
            img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        lab = _ensure_label_map(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if lab is None:                   # no model available: leave it for a later run
            return None
        res = screen_from_labels(lab, w, h, geometry, known_pose)
    _write(cache, res)
    return res


# ---------- depth / geometry metrics ----------

WALL, SKY = 0, 2
BUILDING_CLASSES = (1, 25, 48, 79, 84)            # building, house, skyscraper, hovel, tower
FOLIAGE_CLASSES = (4, 9, 17, 72)                  # tree, grass, plant, palm
BLOCK_CLASSES = (WALL,) + BUILDING_CLASSES + FOLIAGE_CLASSES
NEAR_DISP = 0.6                                   # "near": inverse depth above this share of the photo's 99th percentile
FAR_DISP = 0.5                                    # "far half"
MIN_SEGMENT_FRAC = 0.002                          # a building segment smaller than this share of the photo is noise
MIN_VISIBLE_FRAC = 0.0005                         # projected footprint visible area, share of the photo


def _norm_disp(depth, shape):
    """Raw inverse depth resized to `shape` and scaled so the 99th percentile is 1 (clipped at 0)."""
    import cv2
    import numpy as np
    d = np.asarray(depth, np.float32)
    if d.shape != tuple(shape):
        d = cv2.resize(d, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR)
    top = float(np.percentile(d, 99))
    return np.clip(d / top, 0.0, None) if top > 0 else np.zeros(shape, np.float32)


def measure(labels, depth) -> dict:
    """Metrics of one photo from its ADE20K label map and raw inverse depth (None: depth metrics are None).
    near_block_frac: share of pixels that are wall / building / tree / plant AND near (normalised disparity
    > NEAR_DISP). seg_buildings: connected building segments of at least MIN_SEGMENT_FRAC of the photo whose
    disparity median is in the far half. skyline_frac: share of columns whose sky boundary rests on a
    building rather than foliage, with the pixel below it far."""
    import cv2
    import numpy as np
    lab = np.asarray(labels)
    h, w = lab.shape
    out = {"ground": float(np.isin(lab, GROUND_CLASSES).mean()), "sky": float((lab == SKY).mean()),
           "building_frac": float(np.isin(lab, BUILDING_CLASSES).mean()),
           "near_block_frac": None, "seg_buildings": None, "skyline_frac": None, "near_frac": None}
    if depth is None:
        return out
    n = _norm_disp(depth, lab.shape)
    near = n > NEAR_DISP
    out["near_frac"] = float(near.mean())
    out["near_block_frac"] = float((near & np.isin(lab, BLOCK_CLASSES)).mean())
    bm = (np.isin(lab, BUILDING_CLASSES)).astype(np.uint8)
    cnt, cc, stats, _ = cv2.connectedComponentsWithStats(bm, connectivity=8)
    seg = 0
    for i in range(1, cnt):
        if stats[i, cv2.CC_STAT_AREA] < MIN_SEGMENT_FRAC * h * w:
            continue
        if float(np.median(n[cc == i])) < FAR_DISP:
            seg += 1
    out["seg_buildings"] = seg
    sky = lab == SKY
    cols = np.flatnonzero(sky[0])                     # columns whose top pixel is sky
    far_cols = 0
    for x in cols:
        col = sky[:, x]
        r = int(np.argmin(col))                       # first non-sky row
        if col[r]:                                    # all sky
            far_cols += 1
            continue
        r2 = min(r + 2, h - 1)
        if lab[r2, x] not in FOLIAGE_CLASSES and n[r2, x] < FAR_DISP:
            far_cols += 1
    out["skyline_frac"] = far_cols / w
    return out


def measure_pose(dem, fc: dict, cam) -> dict:
    """Metrics of a photo with a known camera: height above DEM ground, buildings whose projected prism is
    visible (occluded ones do not count) with at least MIN_VISIBLE_FRAC of the photo, and the distance to
    the nearest of them (to its footprint edge)."""
    import numpy as np
    import terrain_solve as ts
    ground = float(dem(cam.lat, cam.lon))
    out = {"cam_height_m": cam.elev_m - ground if ground == ground else None,
           "proj_buildings": None, "nearest_building_m": None}
    r = ts.project_buildings(dem, fc, cam, min_dist_m=0.0)
    need = MIN_VISIBLE_FRAC * cam.width * cam.height
    vis = [b for b in r["buildings"] if b["visible_px"] >= need]
    out["proj_buildings"] = len(vis)
    out["nearest_building_m"] = min((b["edge_m"] for b in vis), default=None)
    out["proj_cover"] = float((np.asarray(r["ids"]) > 0).mean())
    return out
