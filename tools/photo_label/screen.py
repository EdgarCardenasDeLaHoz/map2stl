"""Automatic screening of unusable photos for the photo-label tool.

Two rules, measured on 74 Cartagena photos (SegFormer ADE20K label maps) against 20 photos the
user flagged by hand:

* **Street level**: the share of pixels in the ground classes (floor, road, sidewalk, earth,
  person, path) is at least ``GROUND_MAX`` (0.25). All 15 Street View frames measured 0.34-0.51;
  every kept photo was <= 0.15. A street-level view shows no roofs or skyline to register.
* **Stitched panorama**: width / height >= ``PANORAMA_ASPECT`` (3.5). Flagged panoramas were
  4.06-4.75; the widest kept photo was 2.33. The pinhole camera model does not fit them.

Together they reproduce 18 of the 20 manual flags with no false positive. There is deliberately
no brightness rule (the user kept night shots) and no building-share rule (SegFormer labels a
distant city as mountain / earth, so two good La Popa views read 0.002 and 0.007 building).

The aspect rule runs first and needs only the image size; SegFormer runs only on photos it did
not catch. A result is cached beside the photo as ``<stem>_screen.json`` and never recomputed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

GROUND_CLASSES = (3, 6, 11, 13, 12, 52)   # ADE20K (0-based): floor, road, sidewalk, earth, person, path
GROUND_MAX = 0.25
PANORAMA_ASPECT = 3.5
MAX_SIDE = 1024                           # long side SegFormer sees


def screen_path(photo: Path) -> Path:
    return photo.with_name(photo.stem + "_screen.json")


def _aspect_reason(aspect: float) -> str:
    return f"auto: stitched panorama (aspect {aspect:.1f})" if aspect >= PANORAMA_ASPECT else ""


def screen_from_labels(labels, w: int, h: int) -> dict:
    """{ground, aspect, reason} from an ADE20K label map (or None) and the photo size.
    `reason` is "" for a usable photo. The aspect rule wins; with `labels` None only it applies."""
    aspect = w / h if h else 0.0
    reason = _aspect_reason(aspect)
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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(res), encoding="utf-8")
    os.replace(tmp, path)


def screen_photo(path: Path, labels: bool = True) -> dict | None:
    """Screen one photo and cache the result. A cached file is returned as is. With `labels`
    False only the aspect rule runs; a photo it does not catch returns None and is not cached,
    so a later run with a GPU can still segment it."""
    cache = screen_path(path)
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    res = screen_from_labels(None, w, h)
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
        res = screen_from_labels(lab, w, h)
    _write(cache, res)
    return res
