"""photo_solve.py — the Photo solve page's service: regions, photos, pairs, camera solve, heights.

One implementation, shared with the standalone pairing tool (``tools/photo_label/server.py``):
both call the ``city2stl.photo_label`` package (regions, photos, camera solve, start poses) on a
per-region ``Config``; this module adds only what the page needs on top (a status per photo, an upload, the heights table, a footprint
window for the map, the Commons search). No Google request is made from here: the tool's
Google 3D proxy, its 3D map mode and the render-based ``api_register_step`` are not exposed, and
the Config is built with no API key so none could be sent. Only the bad-photo screening still
runs from the tool server (``tool()``), until ``screen.py`` moves into the package too.

Environment overrides (scratch runs and tests):

    MAP2STL_PHOTO_REGIONS_FILE        regions table (default ``tools/photo_label/regions.json``)
    MAP2STL_PHOTO_ANNOTATIONS_DIR     label files, flags and heights (default the tool's ANNOTATIONS)
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import math
import os
import re
import sys
import threading
from pathlib import Path

from app.paths import REPO_ROOT
from city2stl.photo_label import candidates as _candidates
from city2stl.photo_label import photos as _photos
from city2stl.photo_label import regions as _regions_mod
from city2stl.photo_label import solve as _solve
from city2stl.skyline.camera_solver import M_PER_DEG_LAT
from city2stl.skyline.photo_view import building_height

TOOL_DIR = REPO_ROOT / "tools" / "photo_label"
MAX_UPLOAD = 40 * 1024 * 1024
UPLOAD_EXT = {".jpg", ".jpeg", ".png", ".webp"}
SOURCE = "photo"           # height_source written by "Use these heights" (Phase 5 reads it)

_lock = threading.Lock()
_tool = None
_regions = None


class UnknownRegion(LookupError):
    pass


def tool():
    """The photo_label server module, loaded once under the name ``photo_label_server`` (screening only)."""
    global _tool
    with _lock:
        if _tool is None:
            for p in (str(REPO_ROOT), str(TOOL_DIR)):
                if p not in sys.path:
                    sys.path.insert(0, p)
            spec = importlib.util.spec_from_file_location("photo_label_server", TOOL_DIR / "server.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["photo_label_server"] = mod
            spec.loader.exec_module(mod)
            _tool = mod
        return _tool


def annotations_dir() -> Path:
    return Path(os.environ.get("MAP2STL_PHOTO_ANNOTATIONS_DIR") or _regions_mod.ANNOTATIONS)


def regions_file() -> Path:
    return Path(os.environ.get("MAP2STL_PHOTO_REGIONS_FILE") or _regions_mod.REGIONS_FILE)


def regions():
    """The ``Regions`` registry, built on first use, with no Google key (``api_key=""``)."""
    global _regions
    with _lock:
        if _regions is None:
            specs = _regions_mod.load_regions(regions_file())
            _regions = _regions_mod.Regions(specs, next(iter(specs)), annotations_dir=annotations_dir(),
                                 api_key="")
        return _regions


def reset() -> None:
    """Forget the loaded regions (tests and scratch servers change the env between runs)."""
    global _regions
    with _lock:
        _regions = None


def cfg(region: str):
    try:
        return regions().get(region)
    except _regions_mod.UnknownRegion as exc:
        raise UnknownRegion(str(exc)) from exc


def list_regions() -> list[dict]:
    reg = regions()
    out = []
    for rid in reg.ids():
        c = reg.get(rid)
        out.append({"id": rid, "label": reg._specs[rid]["label"],
                    "photos": len(_photos.photo_files(c)),
                    "view": _regions_mod.start_view(c)})
    return out


# ---------- photos -----------------------------------------------------------------------------

def _label_doc(c, rel: str) -> dict | None:
    p = _photos.label_path(c, rel)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_photos(region: str) -> list[dict]:
    """Every photo of the region with its pair count and solve state, flagged ones included."""
    c = cfg(region)
    out = []
    for ph in _photos.list_photos(c, include_bad=True):
        doc = _label_doc(c, ph["path"]) or {}
        cam = doc.get("camera") or {}
        out.append({**ph, "pairs": len(doc.get("labels") or []),
                    "solved": bool(cam),
                    "rms_px": cam.get("rms_px"),
                    "camera_source": doc.get("camera_source") if cam else None})
    return out


def photo_file(region: str, rel: str) -> Path | None:
    return _photos.safe_photo(cfg(region), rel)


def get_labels(region: str, rel: str) -> dict:
    c = cfg(region)
    if _photos.safe_photo(c, rel) is None:
        raise FileNotFoundError(f"no such photo: {rel}")
    return _photos.get_labels(c, rel)


def save_labels(region: str, body: dict) -> dict:
    return _photos.save_labels(cfg(region), body)


def flag(region: str, rel: str, bad: bool, reason: str = "") -> dict:
    return _photos.photo_flag(cfg(region), {"photo": rel, "bad": bad, "reason": reason})


def screen_start(region: str) -> dict:
    return tool().api_photos_screen(cfg(region), {})


def screen_state(region: str) -> dict:
    return tool().api_photos_screen_state(cfg(region))


def _upload_root(c) -> tuple[str, Path]:
    roots = c.photo_roots
    if "" in roots:
        return "", roots[""]
    if "commons" in roots:
        return "commons", roots["commons"]
    key = next(iter(roots))
    return key, roots[key]


def add_photo(region: str, filename: str, data: bytes) -> dict:
    """Store an uploaded image as ``<root>/<name>/photo.<ext>`` and return its listing entry."""
    c = cfg(region)
    ext = Path(filename).suffix.lower()
    if ext not in UPLOAD_EXT:
        raise ValueError("Use a JPG, PNG or WebP image")
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("The image is empty or larger than 40 MB")
    import io

    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception as exc:
        raise ValueError("That file is not a readable image") from exc
    key, root = _upload_root(c)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(filename).stem).strip("_").lower()[:60] or "photo"
    folder = root / stem
    n = 2
    while folder.exists():
        folder = root / f"{stem}_{n}"
        n += 1
    folder.mkdir(parents=True)
    dest = folder / f"photo{'.jpg' if ext == '.jpeg' else ext}"
    dest.write_bytes(data)
    rel = _photos.photo_rel(key, root, dest)
    w, h = _photos.image_size(dest)
    return {"path": rel, "w": w, "h": h, "pairs": 0, "solved": False}


# ---------- camera ----------------------------------------------------------------------------

def solve(region: str, body: dict) -> dict:
    """Camera fit on the pairs, with the heights table of the buildings the pairs sit on."""
    c = cfg(region)
    res = _solve.solve(c, body)
    if not res.get("ok"):
        return res
    res["heights"] = heights_table(c, body.get("labels") or [], res)
    return res


def heights_table(c, labels: list[dict], res: dict) -> list[dict]:
    """One row per pair that sits inside an OSM footprint: the height the photo gives vs the one OSM has."""
    hs = res.get("heights_m") or []
    h0 = res.get("h0_m") or []
    inside = res.get("in_footprint") or []
    fc = c.footprints()
    rows = []
    for i, lb in enumerate(labels):
        if i >= len(hs) or not (i < len(inside) and inside[i]):
            continue
        h = hs[i]
        if h is None or not math.isfinite(float(h)):
            continue
        lat, lon = float(lb["lat"]), float(lb["lon"])
        name = _regions_mod.name_at(fc, lat, lon)
        rows.append({"index": i, "building": name or str(lb.get("label") or f"Building {i + 1}"),
                     "lat": lat, "lon": lon, "photo_m": round(float(h), 1),
                     "osm_m": round(float(h0[i]), 1) if i < len(h0) and h0[i] else None})
    return rows


def project(region: str, body: dict) -> dict:
    return _solve.project(cfg(region), body)


_cent_cache: dict[str, tuple] = {}


def _centroids(c):
    import numpy as np
    key = c.region
    hit = _cent_cache.get(key)
    fc = c.footprints()
    if hit is None or hit[0] is not fc:
        pts = np.array([np.mean(np.asarray(f["geometry"]["coordinates"][0])[:-1], axis=0)
                        for f in fc["features"]]) if fc["features"] else np.zeros((0, 2))
        hit = (fc, pts)
        _cent_cache[key] = hit
    return hit[1]


def footprints(region: str, lat: float, lon: float, radius_m: float, limit: int = 4000) -> dict:
    """Footprints whose centre lies within ``radius_m`` of a point, nearest first (for the map)."""
    import numpy as np
    c = cfg(region)
    fc = c.footprints()
    pts = _centroids(c)
    if not len(pts):
        return {"features": [], "total": 0}
    kx = M_PER_DEG_LAT * math.cos(math.radians(lat))
    d = np.hypot((pts[:, 0] - lon) * kx, (pts[:, 1] - lat) * M_PER_DEG_LAT)
    idx = np.where(d <= radius_m)[0]
    idx = idx[np.argsort(d[idx])][:limit]
    feats = []
    for i in idx:
        f = fc["features"][int(i)]
        pr = f["properties"]
        feats.append({"id": pr.get("id"), "name": pr.get("name"),
                      "height_m": round(float(building_height(pr)), 1),
                      "ring": [[round(x, 6), round(y, 6)] for x, y in f["geometry"]["coordinates"][0]]})
    return {"features": feats, "total": int(len(idx))}


def candidates(region: str, rel: str, refresh: bool = False, progress=None) -> dict:
    """Start poses for a photo with no pairs: skyline vs the DEM horizon (no Google, no render)."""
    if progress:
        progress(10, "Matching the photo's skyline to the terrain")
    return _candidates.register_candidates(cfg(region), {"photo": rel, "refresh": refresh})


# ---------- heights ---------------------------------------------------------------------------

def heights_path(region: str) -> Path:
    return annotations_dir() / f"{region}_photo_heights.json"


def load_heights(region: str) -> dict:
    p = heights_path(region)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        return {}


def save_heights(region: str, rel: str, rows: list[dict]) -> dict:
    """Keep the heights read from one photo, source ``photo``. Never touches OSM tags, survey or LiDAR."""
    c = cfg(region)
    if _photos.safe_photo(c, rel) is None:
        raise FileNotFoundError(f"no such photo: {rel}")
    clean = [{"building": str(r.get("building") or ""), "lat": float(r["lat"]), "lon": float(r["lon"]),
              "height_m": float(r["photo_m"]),
              "osm_height_m": None if r.get("osm_m") is None else float(r["osm_m"])}
             for r in rows if r.get("photo_m") is not None]
    doc = load_heights(region)
    doc.setdefault("source", SOURCE)
    doc.setdefault("photos", {})[rel] = {
        "saved": datetime.datetime.now().isoformat(timespec="seconds"), "heights": clean}
    p = heights_path(region)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "saved": len(clean), "source": SOURCE, "path": str(p)}


# ---------- Commons ---------------------------------------------------------------------------

def _bbox(c) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for f in c.footprints()["features"]:
        for x, y in f["geometry"]["coordinates"][0]:
            xs.append(x)
            ys.append(y)
    return max(ys), min(ys), max(xs), min(xs)


def commons_search(region: str, limit: int = 12) -> list[dict]:
    """Located skyline photos of the region on Wikimedia Commons (network: the Commons API only)."""
    from city2stl.skyline import commons_photos as cp
    c = cfg(region)
    label = regions()._specs[region]["label"]
    found = cp.find_skyline_photos(label, _bbox(c), max_photos=limit)
    return [{"title": p.title, "url": p.url, "page_url": p.page_url, "lat": p.lat, "lon": p.lon,
             "heading_deg": p.heading_deg, "width": p.width, "height": p.height,
             "attribution": p.attribution} for p in found]


def commons_add(region: str, url: str, title: str) -> dict:
    """Download one Commons photo (a ``upload.wikimedia.org`` URL only) into the region's photo folder."""
    import requests

    from city2stl.skyline import commons_photos as cp
    if not re.match(r"^https://upload\.wikimedia\.org/", url):
        raise ValueError("Only Wikimedia Commons images can be added this way")
    r = requests.get(url, headers={"User-Agent": cp.UA}, timeout=cp.TIMEOUT_S)
    r.raise_for_status()
    name = re.sub(r"^File:", "", title)
    out = add_photo(region, name if Path(name).suffix else name + ".jpg", r.content)
    return out
