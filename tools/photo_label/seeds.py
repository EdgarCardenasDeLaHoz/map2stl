"""Seed cameras for a photo: the text hints in its file name, the saved panorama seed folders (`03_seeds`) and
the candidate tagging that ranks viewpoints against them."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from latlon import bearing_deg, dist_m
from pano_camera import f_px_of_fov, frame_pose_parts, pose_elevation
from paths import _is_seed_strip, _read_json, image_size


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
        brg = bearing_deg(origin[0], origin[1], sub["lat"], sub["lon"]) if origin else None
        out["subject"] = {"name": sub["name"], "lat": sub["lat"], "lon": sub["lon"], "bearing_deg": brg}
    return out


HEADING_AGREE_DEG = 40.0
HEADING_BONUS = 0.85   # score multiplier (score = mean row error px, lower is better)
SEED_MATCH_M = 600.0


def _tag_candidates(cands: list[dict], seeds: list[tuple[float, float, str, str]]) -> None:
    """Set `source` and `label` on each candidate (dicts with lat, lon, source from auto_pose, best
    first): the nearest seed (lat, lon, source, label) within SEED_MATCH_M names it; a candidate
    from the DEM-grid search is "grid" / "Skyline search #n" (n counts grid candidates)."""
    n_grid = 0
    for c in cands:
        near = None
        if c.get("source") != "grid":
            for la, lo, src, lab in seeds:
                d = dist_m(c["lat"], c["lon"], la, lo)
                if d <= SEED_MATCH_M and (near is None or d < near[0]):
                    near = (d, src, lab)
        if near:
            c["source"], c["label"] = near[1], near[2]
        else:
            n_grid += 1
            c["source"], c["label"] = "grid", f"Skyline search #{n_grid}"


PANO_CAM_HEIGHT_M = 2.5


def _dem_ground(cfg, lat: float, lon: float) -> float:
    """Orthometric DEM ground height at the point; ValueError when there is no DEM or it has no value there."""
    dem = cfg.dem()
    if dem is None:
        raise ValueError("no DEM")
    import auto_pose
    import numpy as np
    ground = float(auto_pose._dem_sampler(dem)(np.array([lat]), np.array([lon]))[0])
    if not math.isfinite(ground):
        raise ValueError("no DEM ground")
    return ground


def _seed_camera(photo: Path, cfg, view: dict, lat, lon, heading, pitch, fov, roll=0.0,
                 elev=None, fitted=False) -> dict:
    """Candidate dict from pose parts; `elev` None = DEM ground + PANO_CAM_HEIGHT_M. `fitted`
    marks a measured elevation (the page then keeps it instead of standing on the mesh ground)."""
    from PIL import Image
    with Image.open(photo) as im:
        w, h = im.size
    if elev is None:
        elev = _dem_ground(cfg, lat, lon) + PANO_CAM_HEIGHT_M
    f_px = f_px_of_fov(w, fov)
    return {"lat": lat, "lon": lon, "elev": elev, "heading": heading % 360.0, "pitch": pitch, "roll": roll,
            "f": f_px, "width": w, "height": h, "source": "pano_anchor", "elev_fitted": fitted,
            "label": f"Pano seed {view.get('seed', '')} (known pose)".replace("  ", " ")}


def _seed_frame_pose(photo: Path, cfg) -> dict | None:
    """Frame `<run>/03_seeds/seed_<id>/<id>_<HHH>.png`: camera from `views.json` + `pose.json` beside it."""
    d = photo.parent
    views = _read_json(d / "views.json")
    v = next(x for x in views if Path(str(x["file"])).name == photo.name)
    pose = _read_json(d / "pose.json")
    elev, fitted = pose_elevation(pose)
    parts = frame_pose_parts(v, pose, image_size(photo)[0])
    return _seed_camera(photo, cfg, pose, float(pose["lat"]), float(pose["lon"]), parts["heading_deg"],
                        parts["pitch_deg"], float(v["fov_deg"]), roll=parts["roll_deg"], elev=elev, fitted=fitted)


def _seed_strip_pose(photo: Path, cfg) -> dict | None:
    """The cylindrical camera of `03_seeds/seed_<id>/pano.png` (`pano_camera.load_seed_camera`: pano.json +
    pose.json, column 0 at 180 deg) as a candidate dict: the usual keys (`heading` is the centre column)
    plus `projection` "pano_cylindrical", `deg_per_col`, `col0_heading_deg`, `weak` (pose branch
    `overhead`). Elevation as for frames: `cam_elev_m`, else ground + camera height, else DEM + PANO_CAM_HEIGHT_M."""
    import pano_camera
    d = photo.parent
    pose = _read_json(d / "pose.json")
    known, fitted = pose_elevation(pose)
    # a known elevation is read from pose.json by the loader; otherwise stand on the DEM
    elev = None if known is not None else _dem_ground(cfg, float(pose["lat"]), float(pose["lon"])) + PANO_CAM_HEIGHT_M
    cam = pano_camera.load_seed_camera(d, elev_m=elev)
    try:
        frames = pano_camera.seed_frame_cameras(d, cam)
    except (OSError, ValueError, KeyError, TypeError):
        frames = []
    return {**cam.to_dict(), "frames": frames, "elev": cam.elev_m, "heading": cam.heading_deg, "pitch": cam.pitch_deg,
            "roll": 0.0, "f": cam.f_px, "source": "pano_anchor", "elev_fitted": fitted,
            "label": f"Pano seed {pose.get('seed', '')} strip (known pose" + (", weak" if cam.weak else "") + ")"}


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
    """The known camera of a panorama-pipeline frame, or None. Four layouts:

    * `<run>/03_seeds/seed_<id>/<id>_<HHH>.png` (views.json + pose.json beside it): heading, pitch and
      fov from views.json, roll from pose `horizon_tilt_deg`, elevation `cam_elev_m` (fitted, flagged
      `elev_fitted`), else `ground_m + cam_height_m`, else DEM + PANO_CAM_HEIGHT_M; source `pano_anchor`.
    * `<run>/03_seeds/seed_<id>/pano.png`, the 360 deg strip: a `pano_cylindrical` camera (`_seed_strip_pose`).
    * a frame named in a `manifest.json` beside it or one folder up (panoview): same, from the manifest.
    * `<run>/02_streetview/<view>/<view>_<HHH>.png`, below.

    Location comes from `metadata.json` beside the photo, heading from HHH in the file name (plus the
    median `heading_offset_deg` of `<run>/04_estimates/view_estimates.json` for that view, when
    present), fov and size from `<run>/site.json` resolved_viewpoints. elev_m is DEM ground +
    PANO_CAM_HEIGHT_M, the same orthometric convention as auto_pose candidates (geoid_m is separate).
    Returns a candidate dict (source/label only for the first three layouts) or None when anything is missing."""
    photo = Path(photo_path)
    try:
        if (photo.parent / "views.json").is_file() and photo.parent.parent.name == "03_seeds":
            if _is_seed_strip(photo):
                return _seed_strip_pose(photo, cfg)
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
        ground = _dem_ground(cfg, lat, lon)
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
