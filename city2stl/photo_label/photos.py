"""Photo-label photos, bad-photo flags and label files of a region (`regions.Config`).

Moved from `tools/photo_label/server.py` (F-UNIFY Phase 3).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from geo2stl.cache import atomic_write_json

from .regions import IMG_EXT, Config, name_at


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


_EXT_RANK = {".png": 0, ".jpg": 1, ".jpeg": 1, ".webp": 2}


_DERIVED = re.compile(r"^(overlay|landmarks_)|_depth|_labels|_baseline", re.I)


_SEED_NONFRAME = re.compile(r"^pano(_|$)|_(mask|seg)$", re.I)   # strip / masks beside the frames of a seed folder


def _is_seed_nonframe(p: Path) -> bool:
    """True for the stitched strip, masks and segmentations inside `03_seeds/seed_*/` (not photos)."""
    return p.parent.parent.name == "03_seeds" and bool(_SEED_NONFRAME.search(p.stem))


def photo_files(cfg: Config) -> list[tuple[str, Path, Path]]:
    """(root key, root folder, file) per folder+stem across the roots (png, else jpg, else webp)."""
    best: dict[tuple, tuple[str, Path, Path]] = {}
    for key, root in cfg.photo_roots.items():
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            ext = p.suffix.lower()
            if ext not in IMG_EXT or _DERIVED.search(p.stem) or _is_seed_nonframe(p):
                continue
            k = (key, p.parent, p.stem)
            if k not in best or _EXT_RANK[ext] < _EXT_RANK[best[k][2].suffix.lower()]:
                best[k] = (key, root, p)
    return sorted(best.values(), key=lambda t: (t[0], t[2]))


def photo_rel(key: str, root: Path, p: Path) -> str:
    rel = p.relative_to(root).as_posix()
    return f"{key}/{rel}" if key else rel


_FLAGS_LOCK = threading.Lock()


def flags_path(cfg: Config) -> Path:
    """Bad-photo flags of a region, beside its label files."""
    return cfg.annotations_dir / f"{cfg.region}_photo_flags.json"


def load_flags(cfg: Config) -> dict[str, dict]:
    try:
        data = json.loads(flags_path(cfg).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def list_photos(cfg: Config, include_bad: bool = False) -> list[dict]:
    """One entry per folder+stem (png, else jpg, else webp); derived outputs are hidden.
    Photos flagged bad (api_photo_flag) are left out unless `include_bad`, which lists them
    with `bad: true` (unflagged entries keep the {path, w, h} shape)."""
    flags = load_flags(cfg)
    out = []
    for key, root, p in photo_files(cfg):
        rel = photo_rel(key, root, p)
        bad = rel in flags
        if bad and not include_bad:
            continue
        try:
            w, h = image_size(p)
        except Exception:  # noqa: BLE001 - an unreadable image is just not listed
            continue
        out.append({"path": rel, "w": w, "h": h, **({"bad": True} if bad else {})})
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


def photo_flag(cfg: Config, body: dict) -> dict:
    """Mark (`bad` true) or unmark a photo as unusable; persists to `<region>_photo_flags.json`
    as {photo rel: {reason, flagged: ISO date}}. Returns {ok, bad, count_flagged}."""
    rel = body.get("photo")
    if not isinstance(rel, str) or safe_photo(cfg, rel) is None:
        return {"ok": False, "error": f"no such photo: {rel}"}
    bad = bool(body.get("bad", True))
    return {"ok": True, "bad": bad,
            "count_flagged": _set_flag(cfg, rel, bad, str(body.get("reason") or ""))}


def safe_photo(cfg: Config, rel: str) -> Path | None:
    """The image file `rel` names inside one of the photo roots, else None.

    A keyed root owns the paths starting `<key>/`; the unkeyed root takes everything else.
    """
    cands = [(root, rel[len(key) + 1:]) for key, root in cfg.photo_roots.items()
             if key and rel.startswith(key + "/")]
    if "" in cfg.photo_roots:
        cands.append((cfg.photo_roots[""], rel))
    for root, sub in cands:
        base = root.resolve()
        p = (base / sub).resolve()
        if base in p.parents and p.suffix.lower() in IMG_EXT and p.is_file():
            return p
    return None


def label_path(cfg: Config, rel: str) -> Path:
    stem = Path(rel).with_suffix("").as_posix()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "photo"
    return cfg.annotations_dir / f"{cfg.region}_{safe}.json"


def get_labels(cfg: Config, rel: str) -> dict:
    p = label_path(cfg, rel)
    if not p.is_file():
        return {"photo": rel, "labels": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_labels(cfg: Config, body: dict) -> dict:
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


def photo_rgb(cfg: Config, rel: str):
    import cv2
    photo = safe_photo(cfg, rel)
    if photo is None:
        raise ValueError(f"no such photo: {rel}")
    img = cv2.imread(str(photo), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read photo: {rel}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
