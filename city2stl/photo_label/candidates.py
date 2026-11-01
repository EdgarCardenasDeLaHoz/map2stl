"""Start poses for a photo with no camera yet: known panorama poses (`pano_seed`), title/GPS
hints (`photo_hints`) and the skyline-vs-DEM search of `auto_pose`.

Moved from `tools/photo_label/server.py` (`api_register_candidates` and helpers; F-UNIFY Phase 3).
"""
from __future__ import annotations

import importlib
import json
import math
import re
from pathlib import Path

from city2stl.skyline.camera_solver import M_PER_DEG_LAT
from city2stl.skyline.photo_view import cam_from_dict, cam_to_dict

from .photos import photo_rgb, safe_photo
from .regions import Config

GEOID_DEFAULT_M = 50.0        # Granada; the first render needs a guess, later ones use the estimate


def _auto_pose():
    """`city2stl.photo_label.auto_pose`, imported on first use (scipy); tests replace it in
    `sys.modules` (None there makes the import fail, as when scipy is missing)."""
    return importlib.import_module("city2stl.photo_label.auto_pose")

def _dem_centre(dem) -> tuple[float, float]:
    h, w = dem.z.shape
    lon, lat = dem.t @ (w / 2, h / 2)
    return float(lat), float(lon)


def _norm_text(t: str) -> str:
    """Lower-case, accent-free, single-spaced text for title matching."""
    import unicodedata
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if unicodedata.category(c) != "Mn")).strip()


def _parse_coord(v) -> float | None:
    """A degree value from a float, a decimal string or a DMS string ("10 25' 12\" N")."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) if math.isfinite(v) else None
    if not isinstance(v, str):
        return None
    nums = re.findall(r"\d+(?:[.,]\d+)?", v)
    if not nums:
        return None
    parts = [float(n.replace(",", ".")) for n in nums[:3]]
    deg = sum(x / 60.0 ** i for i, x in enumerate(parts))
    if "-" in v or re.search(r"[SWOsw]\s*$", v.strip()):
        deg = -deg
    return deg


def _bearing_deg(lat1, lon1, lat2, lon2) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


_FROM = r"(?:view from|vista desde|from|desde)"


def photo_focal_35mm(photo_path) -> float | None:
    """The EXIF 35 mm-equivalent focal length (FocalLengthIn35mmFilm), or None. Only 4 of 59
    Cartagena Commons photos carry it (resized downloads drop EXIF); the bare focal length in mm
    is not used, since without the sensor size it says nothing about the field of view."""
    try:
        from PIL import Image
        with Image.open(photo_path) as im:
            f = im.getexif().get_ifd(0x8769).get(41989)
        return float(f) if f and float(f) > 0 else None
    except Exception:  # noqa: BLE001 - no EXIF, unreadable, not a JPEG: no focal hint
        return None


def photo_hints(photo_path, landmarks) -> dict:
    """{gps, title, viewpoint, subject} for a photo, from `credit.json` beside it.

    gps: [lat, lon] or None. viewpoint: the landmark the title says the photo is taken from
    ("from"/"desde"/"view from"/"vista desde" up to three words before it). subject: the first
    other landmark in the title, with `bearing_deg` from the viewpoint (else the gps; None when
    neither). Matching ignores case and accents; with no from-phrase there is no viewpoint."""
    out = {"gps": None, "title": None, "viewpoint": None, "subject": None}
    try:
        credit = json.loads((Path(photo_path).parent / "credit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(credit, dict):
        return out
    gps = credit.get("gps")
    if isinstance(gps, (list, tuple)) and len(gps) == 2:
        la, lo = _parse_coord(gps[0]), _parse_coord(gps[1])
        if la is not None and lo is not None and abs(la) <= 90 and abs(lo) <= 180:
            out["gps"] = [la, lo]
    title = credit.get("title")
    if not isinstance(title, str) or not title.strip():
        return out
    out["title"] = title
    text = _norm_text(title)
    found = []  # (position, landmark, is_viewpoint)
    for lm in landmarks or []:
        best = None
        for al in {_norm_text(a) for a in [lm.get("name", ""), *(lm.get("aliases") or [])]} - {""}:
            q = re.escape(al)
            m = re.search(rf"\b{_FROM}\s+(?:\w+\s+){{0,3}}?{q}\b", text)
            if m:
                cand = (m.start(), True)
            else:
                m = re.search(rf"\b{q}\b", text)
                cand = (m.start(), False) if m else None
            if cand and (best is None or (cand[1], -cand[0]) > (best[1], -best[0])):
                best = cand
        if best:
            found.append((best[0], lm, best[1]))
    found.sort(key=lambda t: t[0])
    vp = next((lm for _, lm, v in found if v), None)
    sub = next((lm for _, lm, _v in found if lm is not vp), None)
    if vp:
        out["viewpoint"] = {"name": vp["name"], "lat": vp["lat"], "lon": vp["lon"]}
    if sub:
        origin = (vp["lat"], vp["lon"]) if vp else out["gps"]
        brg = _bearing_deg(origin[0], origin[1], sub["lat"], sub["lon"]) if origin else None
        out["subject"] = {"name": sub["name"], "lat": sub["lat"], "lon": sub["lon"], "bearing_deg": brg}
    return out


HEADING_AGREE_DEG = 40.0


HEADING_BONUS = 0.85   # score multiplier (score = mean row error px, lower is better)


SEED_MATCH_M = 600.0


def _hav_m(la1, lo1, la2, lo2) -> float:
    kx = M_PER_DEG_LAT * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot((lo2 - lo1) * kx, (la2 - la1) * M_PER_DEG_LAT)


def _tag_candidates(cands: list[dict], seeds: list[tuple[float, float, str, str]]) -> None:
    """Set `source` and `label` on each candidate (dicts with lat, lon, source from auto_pose, best
    first): the nearest seed (lat, lon, source, label) within SEED_MATCH_M names it; a candidate
    from the DEM-grid search is "grid" / "Skyline search #n" (n counts grid candidates)."""
    n_grid = 0
    for c in cands:
        near = None
        if c.get("source") != "grid":
            for la, lo, src, lab in seeds:
                d = _hav_m(c["lat"], c["lon"], la, lo)
                if d <= SEED_MATCH_M and (near is None or d < near[0]):
                    near = (d, src, lab)
        if near:
            c["source"], c["label"] = near[1], near[2]
        else:
            n_grid += 1
            c["source"], c["label"] = "grid", f"Skyline search #{n_grid}"


PANO_CAM_HEIGHT_M = 2.5


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_camera(photo: Path, cfg, view: dict, lat, lon, heading, pitch, fov, roll=0.0,
                 elev=None, fitted=False) -> dict:
    """Candidate dict from pose parts; `elev` None = DEM ground + PANO_CAM_HEIGHT_M. `fitted`
    marks a measured elevation (the page then keeps it instead of standing on the mesh ground)."""
    from PIL import Image
    with Image.open(photo) as im:
        w, h = im.size
    if elev is None:
        dem = cfg.dem()
        if dem is None:
            raise ValueError("no DEM")
        auto_pose = _auto_pose()
        import numpy as np
        ground = float(auto_pose._dem_sampler(dem)(np.array([lat]), np.array([lon]))[0])
        if not math.isfinite(ground):
            raise ValueError("no DEM ground")
        elev = ground + PANO_CAM_HEIGHT_M
    f_px = w / 2 / math.tan(math.radians(fov) / 2)
    return {"lat": lat, "lon": lon, "elev": elev, "heading": heading % 360.0, "pitch": pitch, "roll": roll,
            "f": f_px, "width": w, "height": h, "source": "pano_anchor", "elev_fitted": fitted,
            "label": f"Pano seed {view.get('seed', '')} (known pose)".replace("  ", " ")}


def _seed_frame_pose(photo: Path, cfg) -> dict | None:
    """Frame `<run>/03_seeds/seed_<id>/<id>_<HHH>.png`: camera from `views.json` + `pose.json` beside it."""
    d = photo.parent
    views = _read_json(d / "views.json")
    v = next(x for x in views if Path(str(x["file"])).name == photo.name)
    pose = _read_json(d / "pose.json")
    elev, fitted = None, False
    if pose.get("cam_elev_m") is not None:
        elev, fitted = float(pose["cam_elev_m"]), True
    elif pose.get("ground_m") is not None and pose.get("cam_height_m") is not None:
        elev = float(pose["ground_m"]) + float(pose["cam_height_m"])
    return _seed_camera(photo, cfg, pose, float(pose["lat"]), float(pose["lon"]), float(v["heading_deg"]),
                        float(v.get("pitch_deg") or 0.0), float(v["fov_deg"]),
                        roll=float(pose.get("horizon_tilt_deg") or 0.0), elev=elev, fitted=fitted)


def _panoview_pose(photo: Path, cfg) -> dict | None:
    """Frame listed in a `manifest.json` in its folder or the one above (per view lat, lon, heading, pitch, fov)."""
    for d in (photo.parent, photo.parent.parent):
        if (d / "manifest.json").is_file():
            man = _read_json(d / "manifest.json")
            for v in man.get("views", []):
                if Path(str(v.get("file", ""))).name == photo.name:
                    return _seed_camera(photo, cfg, {"seed": v.get("name", "")}, float(v["lat"]), float(v["lon"]),
                                        float(v["heading"]), float(v.get("pitch") or 0.0), float(v["fov"]))
    return None


def pano_seed(photo_path, cfg) -> dict | None:
    """The known camera of a panorama-pipeline frame, or None. Three layouts:

    * `<run>/03_seeds/seed_<id>/<id>_<HHH>.png` (views.json + pose.json beside it): heading, pitch and
      fov from views.json, roll from pose `horizon_tilt_deg`, elevation `cam_elev_m` (fitted, flagged
      `elev_fitted`), else `ground_m + cam_height_m`, else DEM + PANO_CAM_HEIGHT_M; source `pano_anchor`.
    * a frame named in a `manifest.json` beside it or one folder up (panoview): same, from the manifest.
    * `<run>/02_streetview/<view>/<view>_<HHH>.png`, below.

    Location comes from `metadata.json` beside the photo, heading from HHH in the file name (plus the
    median `heading_offset_deg` of `<run>/04_estimates/view_estimates.json` for that view, when
    present), fov and size from `<run>/site.json` resolved_viewpoints. elev_m is DEM ground +
    PANO_CAM_HEIGHT_M, the same orthometric convention as auto_pose candidates (geoid_m is separate).
    Returns a candidate dict (source/label only for the first two layouts) or None when anything is missing."""
    photo = Path(photo_path)
    try:
        if (photo.parent / "views.json").is_file() and photo.parent.parent.name == "03_seeds":
            return _seed_frame_pose(photo, cfg)
        if (photo.parent / "manifest.json").is_file() or (photo.parent.parent / "manifest.json").is_file():
            return _panoview_pose(photo, cfg)
    except (OSError, ValueError, KeyError, TypeError, StopIteration, AttributeError):
        return None
    try:
        m = re.fullmatch(r"(.+)_(\d{1,3})", photo.stem)
        if m is None:
            return None
        view, head = m.group(1), float(m.group(2))
        loc = json.loads((photo.parent / "metadata.json").read_text(encoding="utf-8"))["location"]
        lat, lon = float(loc["lat"]), float(loc["lng"])
        run = photo.parent.parent.parent
        site = json.loads((run / "site.json").read_text(encoding="utf-8"))
        vp = next(v for v in site["resolved_viewpoints"] if v.get("name") == view)
        fov, w, h = float(vp["fov"]), int(vp["image_width"]), int(vp["image_height"])
        pitch = float(vp.get("pitch") or 0.0)
        dem = cfg.dem()
        if dem is None:
            return None
        auto_pose = _auto_pose()
        import numpy as np
        ground = float(auto_pose._dem_sampler(dem)(np.array([lat]), np.array([lon]))[0])
        if not math.isfinite(ground):
            return None
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None
    try:
        est = json.loads((run / "04_estimates" / "view_estimates.json").read_text(encoding="utf-8"))
        offs = [float(e["heading_offset_deg"]) for e in est
                if e.get("view_name") == view and e.get("heading_offset_deg") is not None]
        if offs:
            offs.sort()
            n = len(offs)
            head += offs[n // 2] if n % 2 else (offs[n // 2 - 1] + offs[n // 2]) / 2
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:                                  # the file's own size: site.json asks 1280x720, Static API caps at 640
        from PIL import Image
        with Image.open(photo) as im:
            w, h = im.size
    except OSError:
        pass
    f_px = w / 2 / math.tan(math.radians(fov) / 2)
    return {"lat": lat, "lon": lon, "elev": ground + PANO_CAM_HEIGHT_M, "heading": head % 360.0,
            "pitch": pitch, "roll": 0.0, "f": f_px, "width": w, "height": h}


def _guess_cache_path(photo: Path) -> Path:
    return photo.with_name(photo.stem + "_guesses.json")


def _guess_key(rel, body, viewpoints, top_k, landmarks, hints) -> str:
    import hashlib
    blob = json.dumps(["title-pinned", rel, viewpoints, bool(body.get("only_viewpoints")), top_k, landmarks, hints,
                       body.get("exif"), body.get("radius_m")], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def register_candidates(cfg: Config, body: dict) -> dict:
    """Start poses for a photo with no camera yet (auto_pose: skyline vs DEM horizon).

    Search seeds, in order: request `viewpoints`; unless `only_viewpoints`, the viewpoint named in
    the photo's title, the photo's GPS (as a viewpoint, never as exif: some GPS is wrong), then
    footprint viewpoints. Each candidate is tagged with the seed it came from (see _tag_candidates).
    A title subject adds a heading prior by re-ranking: auto_pose has no such input, so a candidate
    whose heading is within HEADING_AGREE_DEG of its bearing to the subject gets score * HEADING_BONUS.
    The title viewpoint, when there is one, is also searched on its own and its best pose is pinned
    first, whatever its score. The focal length is held near the photo's EXIF 35 mm value when it has
    one (photo_focal_35mm).
    Results are cached beside the photo in `<stem>_guesses.json` (not for `only_viewpoints`);
    `refresh` skips the cache."""
    try:
        auto_pose = _auto_pose()
    except ImportError as exc:
        return {"ok": False, "error": f"auto_pose unavailable: {exc}"}
    rel = body["photo"]
    photo = safe_photo(cfg, rel)
    rgb = photo_rgb(cfg, rel)
    dem = cfg.dem()
    if dem is None:
        return {"ok": False, "error": "no DEM loaded; cannot search for a start pose"}
    top_k = int(body.get("top_k") or 3)
    only = bool(body.get("only_viewpoints"))
    if photo is not None and not body.get("refresh") and not body.get("viewpoints"):
        ps = pano_seed(photo, cfg)           # known pose: no skyline search needed
        if ps is not None:
            cam = cam_from_dict(ps)
            extra = {k: ps[k] for k in ("elev_fitted",) if k in ps}
            return {"ok": True, "candidates": [{**cam_to_dict(cam), "score": 0.0, **extra,
                                                "source": ps.get("source", "pano"),
                                                "label": ps.get("label", "Street View pano (known pose)")}],
                    "geoid_m": start_geoid(cfg), "cached": False,
                    "hints": {"gps": None, "title": None, "subject": None,
                              "viewpoint": {"name": "Street View pano", "lat": ps["lat"], "lon": ps["lon"]}}}
    req_vps = [(float(a), float(b)) for a, b in body.get("viewpoints") or []]
    hints = photo_hints(photo, cfg.landmarks) if photo is not None else \
        {"gps": None, "title": None, "viewpoint": None, "subject": None}

    exif = body.get("exif")
    if exif is None and photo is not None:
        f35 = photo_focal_35mm(photo)        # focal only: a GPS position would restrict the search to it
        exif = {"focal_35mm": f35} if f35 else None
    key = _guess_key(rel, {**body, "exif": exif}, req_vps, top_k, cfg.landmarks, hints)
    cache = _guess_cache_path(photo) if photo is not None and not only else None
    if cache is not None and not body.get("refresh"):
        try:
            hit = json.loads(cache.read_text(encoding="utf-8"))
            if hit.get("key") == key and hit.get("response", {}).get("candidates"):
                return {**hit["response"], "geoid_m": start_geoid(cfg), "cached": True}
        except (OSError, ValueError, AttributeError):
            pass

    seeds: list[tuple[float, float, str, str]] = []
    for la, lo in req_vps:
        seeds.append((la, lo, "here", "Searched here"))
    if not only:
        if hints["viewpoint"]:
            v = hints["viewpoint"]
            seeds.append((v["lat"], v["lon"], "title", f"Title: from {v['name']}"))
        if hints["gps"]:
            seeds.append((hints["gps"][0], hints["gps"][1], "gps", "Photo GPS"))
        for i, (la, lo) in enumerate(auto_pose.viewpoints_from_fc(cfg.footprints()), 1):
            seeds.append((float(la), float(lo), "viewpoint", f"Footprint viewpoint #{i}"))
    if only and not seeds:
        return {"ok": False, "error": "only_viewpoints needs at least one viewpoint"}

    subj = hints["subject"]
    clat, clon = _dem_centre(dem)
    ask = top_k * (4 if only else 2 if subj else 1)   # headroom: grid hits are dropped / re-ranked after
    cands = auto_pose.candidate_poses(
        rgb, dem, center_lat=clat, center_lon=clon, radius_m=float(body.get("radius_m") or 3000.0),
        viewpoints=[(s[0], s[1]) for s in seeds], exif=exif, top_k=ask)
    cands = [dict(c) for c in cands]
    _tag_candidates(cands, seeds)
    if only:
        cands = [c for c in cands if c["source"] != "grid"]
    if subj:
        for c in cands:
            brg = _bearing_deg(c["lat"], c["lon"], subj["lat"], subj["lon"])
            if abs((c["heading"] - brg + 180) % 360 - 180) <= HEADING_AGREE_DEG and c.get("score") is not None:
                c["score"] = c["score"] * HEADING_BONUS
        cands.sort(key=lambda c: c["score"] if c.get("score") is not None else math.inf)
    if hints["viewpoint"] and not only:
        # The title names where the photo was taken from; among ~100 footprint viewpoints its skyline
        # score rarely wins (a low street-level horizon fits many places), so search that spot alone
        # and pin its best pose first.
        v = hints["viewpoint"]
        pin = auto_pose.candidate_poses(
            rgb, dem, center_lat=clat, center_lon=clon,
            exif={**(exif or {}), "lat": v["lat"], "lon": v["lon"]}, top_k=1)
        if pin:
            c = {**pin[0], "source": "title", "label": f"Title: from {v['name']}"}
            cands = [c] + [o for o in cands if _hav_m(o["lat"], o["lon"], c["lat"], c["lon"]) > SEED_MATCH_M]
    out = []
    for c in cands[:top_k]:
        cam = cam_from_dict(c, c.get("width", rgb.shape[1]), c.get("height", rgb.shape[0]))
        d = cam_to_dict(cam)
        d.setdefault("hfov_deg", math.degrees(2 * math.atan(d["width"] / 2 / d["f_px"])))
        out.append({**d, "score": c.get("score"), "source": c["source"], "label": c["label"]})
    resp = {"ok": True, "candidates": out, "geoid_m": start_geoid(cfg), "cached": False, "hints": hints,
            **({} if out else {"reason": "no sky boundary in the photo, or no DEM data at the search area"})}
    if cache is not None and out:
        try:
            cache.write_text(json.dumps({"key": key, "response": resp}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return resp


def start_geoid(cfg: Config) -> float:
    """The region's measured geoid undulation (regions.json ``geoid_m``), else Granada's default."""
    return GEOID_DEFAULT_M if cfg.geoid_m is None else cfg.geoid_m
