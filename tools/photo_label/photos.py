"""Photo listing, bad-photo flags and label files: the data behind /api/photos, /api/photo/flag and /api/labels."""
from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from footprints import name_at
from paths import (
    _is_seed_strip,
    _photo_files,
    flags_path,
    image_size,
    label_path,
    photo_rel,
    safe_photo,
)

from geo2stl.cache import atomic_write_json

if TYPE_CHECKING:
    from regions import Config

_FLAGS_LOCK = threading.Lock()


def load_flags(cfg: Config) -> dict[str, dict]:
    try:
        data = json.loads(flags_path(cfg).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def list_photos(cfg: Config, include_bad: bool = False) -> list[dict]:
    """One entry per folder+stem (png, else jpg, else webp); derived outputs are hidden.
    Photos flagged bad (api_photo_flag) are left out unless `include_bad`, which lists them
    with `bad: true` (unflagged entries keep the {path, w, h} shape). A seed strip adds
    `projection: "pano_cylindrical"`."""
    flags = load_flags(cfg)
    out = []
    for key, root, p in _photo_files(cfg):
        rel = photo_rel(key, root, p)
        bad = rel in flags
        if bad and not include_bad:
            continue
        try:
            w, h = image_size(p)
        except Exception:  # noqa: BLE001 - an unreadable image is just not listed
            continue
        out.append({"path": rel, "w": w, "h": h, **({"bad": True} if bad else {}),
                    **({"projection": "pano_cylindrical"} if _is_seed_strip(p) else {})})
    return out


def _set_flag(cfg: Config, rel: str, bad: bool, reason: str = "", keep: bool = False) -> int:
    """Write (`bad`) or remove the flag of `rel`; with `keep` an existing flag is left as it is
    (a hand reason is never overwritten). Returns the number of flagged photos."""
    import datetime
    with _FLAGS_LOCK:
        flags = load_flags(cfg)
        if bad and not (keep and rel in flags):
            flags[rel] = {"reason": reason,
                          "flagged": datetime.datetime.now().isoformat(timespec="seconds")}
        elif not bad:
            flags.pop(rel, None)
        fp = flags_path(cfg)
        atomic_write_json(fp, flags, indent=2, ensure_ascii=False)
        return len(flags)


def api_photo_flag(cfg: Config, body: dict) -> dict:
    """Mark (`bad` true) or unmark a photo as unusable; persists to `<region>_photo_flags.json`
    as {photo rel: {reason, flagged: ISO date}}. Returns {ok, bad, count_flagged}."""
    rel = body.get("photo")
    if not isinstance(rel, str) or safe_photo(cfg, rel) is None:
        return {"ok": False, "error": f"no such photo: {rel}"}
    bad = bool(body.get("bad", True))
    return {"ok": True, "bad": bad,
            "count_flagged": _set_flag(cfg, rel, bad, str(body.get("reason") or ""))}


def api_get_labels(cfg: Config, rel: str) -> dict:
    p = label_path(cfg, rel)
    if not p.is_file():
        return {"photo": rel, "labels": []}
    return json.loads(p.read_text(encoding="utf-8"))


def api_save_labels(cfg: Config, body: dict) -> dict:
    rel = body["photo"]
    photo = safe_photo(cfg, rel)
    if photo is None:
        raise ValueError(f"no such photo: {rel}")
    w, h = body.get("image_px") or image_size(photo)
    fc = cfg.footprints()
    labels = []
    for lab in body.get("labels", []):
        lat, lon = float(lab["lat"]), float(lab["lon"])
        nm = name_at(fc, lat, lon)
        row = {"label": str(lab.get("label") or nm or "P"), "osm_names": [nm] if nm else [],
               "x": float(lab["x"]), "y": float(lab["y"]), "lat": lat, "lon": lon}
        if lab.get("h_ellipsoid_m") is not None:
            row["h_ellipsoid_m"] = float(lab["h_ellipsoid_m"])
        if lab.get("auto"):
            row["auto"] = True
        labels.append(row)
    doc = {"region": cfg.region, "file": rel, "image_px": [int(w), int(h)],
           "projection": "pinhole", "labels": labels}
    cam = body.get("camera")
    if isinstance(cam, dict) and cam:  # the solved camera, so the page can draw buildings without re-solving
        doc["camera"] = cam
        doc["camera_source"] = str(body.get("camera_source") or "solve")
    p = label_path(cfg, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "saved": str(p), "n": len(labels)}


def _photo_rgb(cfg: Config, rel: str):
    import cv2
    photo = safe_photo(cfg, rel)
    if photo is None:
        raise ValueError(f"no such photo: {rel}")
    img = cv2.imread(str(photo), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read photo: {rel}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
