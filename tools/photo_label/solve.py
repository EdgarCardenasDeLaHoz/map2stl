"""The solving routes: candidate viewpoints, registration steps, the terrain camera fit, projection of footprints
into a photo, and the start view of the maps."""
from __future__ import annotations

import json
import math
from types import SimpleNamespace
from typing import TYPE_CHECKING

import terrain_solve as ts
from footprints import name_at
from latlon import bearing_deg, dist_m
from paths import _guess_cache_path, safe_photo
from photos import _photo_rgb
from seeds import (
    HEADING_AGREE_DEG,
    HEADING_BONUS,
    SEED_MATCH_M,
    _tag_candidates,
    pano_seed,
    photo_focal_35mm,
    photo_hints,
)

from city2stl.skyline.camera_solver import M_PER_DEG_LAT, Obs, solve_pose

if TYPE_CHECKING:
    from regions import Config

TICK_RADIUS_M = 3000.0
MAX_TICKS = 300


def start_view(cfg: Config) -> dict | None:
    """Start of the maps: the region's `view` from regions.json, else the DEM's centre and its
    orthometric ground height, else None (the page then keeps wherever the map already is)."""
    import numpy as np

    dem = cfg.dem()
    if cfg.view:
        return dict(cfg.view)
    if dem is None:
        return None
    lat, lon = _dem_centre(dem)
    z = float(np.nanmedian(dem.z))
    return {"lat": float(lat), "lon": float(lon), "ground_m": z if np.isfinite(z) else 0.0}


def _dem_centre(dem) -> tuple[float, float]:
    h, w = dem.z.shape
    lon, lat = dem.t @ (w / 2, h / 2)
    return float(lat), float(lon)


def _guess_key(rel, body, viewpoints, top_k, landmarks, hints) -> str:
    import hashlib
    blob = json.dumps(["title-pinned", rel, viewpoints, bool(body.get("only_viewpoints")), top_k, landmarks, hints,
                       body.get("exif"), body.get("radius_m")], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def api_register_candidates(cfg: Config, body: dict) -> dict:
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
        import auto_pose
    except ImportError as exc:
        return {"ok": False, "error": f"auto_pose unavailable: {exc}"}
    import register as reg
    rel = body["photo"]
    rgb = _photo_rgb(cfg, rel)          # raises ValueError for a photo that does not exist
    photo = safe_photo(cfg, rel)
    dem = cfg.dem()
    if dem is None:
        return {"ok": False, "error": "no DEM loaded; cannot search for a start pose"}
    top_k = int(body.get("top_k") or 3)
    only = bool(body.get("only_viewpoints"))
    if not body.get("refresh") and not body.get("viewpoints"):
        ps = pano_seed(photo, cfg)           # known pose: no skyline search needed
        if ps is not None:
            if ps.get("projection"):                      # a strip: its own camera dict, not a pinhole
                cdict = {k: ps[k] for k in ("lat", "lon", "elev_m", "heading_deg", "pitch_deg", "roll_deg", "f_px",
                                            "hfov_deg", "width", "height", "projection", "deg_per_col",
                                            "col0_heading_deg", "weak", "frames")}
            else:
                cdict = reg.cam_to_dict(reg.cam_from_dict(ps))
            extra = {k: ps[k] for k in ("elev_fitted",) if k in ps}
            return {"ok": True, "candidates": [{**cdict, "score": 0.0, **extra,
                                                "source": ps.get("source", "pano"),
                                                "label": ps.get("label", "Street View pano (known pose)")}],
                    "geoid_m": _start_geoid(cfg), "cached": False,
                    "hints": {"gps": None, "title": None, "subject": None,
                              "viewpoint": {"name": "Street View pano", "lat": ps["lat"], "lon": ps["lon"]}}}
    req_vps = [(float(a), float(b)) for a, b in body.get("viewpoints") or []]
    hints = photo_hints(photo, cfg.landmarks)

    exif = body.get("exif")
    if exif is None:
        f35 = photo_focal_35mm(photo)        # focal only: a GPS position would restrict the search to it
        exif = {"focal_35mm": f35} if f35 else None
    key = _guess_key(rel, {**body, "exif": exif}, req_vps, top_k, cfg.landmarks, hints)
    cache = None if only else _guess_cache_path(photo)
    if cache is not None and not body.get("refresh"):
        try:
            hit = json.loads(cache.read_text(encoding="utf-8"))
            if hit.get("key") == key and hit.get("response", {}).get("candidates"):
                return {**hit["response"], "geoid_m": _start_geoid(cfg), "cached": True}
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
            brg = bearing_deg(c["lat"], c["lon"], subj["lat"], subj["lon"])
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
            cands = [c] + [o for o in cands if dist_m(o["lat"], o["lon"], c["lat"], c["lon"]) > SEED_MATCH_M]
    out = []
    for c in cands[:top_k]:
        cam = reg.cam_from_dict(c, c.get("width", rgb.shape[1]), c.get("height", rgb.shape[0]))
        d = reg.cam_to_dict(cam)
        d.setdefault("hfov_deg", math.degrees(2 * math.atan(d["width"] / 2 / d["f_px"])))
        out.append({**d, "score": c.get("score"), "source": c["source"], "label": c["label"]})
    resp = {"ok": True, "candidates": out, "geoid_m": _start_geoid(cfg), "cached": False, "hints": hints,
            **({} if out else {"reason": "no sky boundary in the photo, or no DEM data at the search area"})}
    if cache is not None and out:
        try:
            cache.write_text(json.dumps({"key": key, "response": resp}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return resp


def _start_geoid(cfg: Config) -> float:
    """The region's measured geoid undulation (regions.json ``geoid_m``), else Granada's default."""
    import register as reg
    return reg.GEOID_DEFAULT_M if cfg.geoid_m is None else cfg.geoid_m


def api_register_step(cfg: Config, body: dict) -> dict:
    """One render -> new camera. The render and depth live in memory only; nothing is stored.

    On a seed strip (`body["strip"]` = the strip's `pano_cylindrical` camera dict, `body["camera"]` the
    pinhole camera of one frame) the strip is resampled to that frame's view (`pano_camera.strip_to_pinhole`),
    `register.register_step` runs unchanged on it, and the returned `pairs` x, y are mapped back onto strip
    pixels (`pano_camera.pinhole_to_strip`); the returned `camera` is the refined frame camera."""
    import register as reg
    try:
        rgb = _photo_rgb(cfg, body["photo"])
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    h, w = rgb.shape[:2]
    pano = None
    if body.get("strip"):
        try:
            import pano_camera
            pano = pano_camera.PanoCamera.from_dict(body["strip"], w, h)
            fc = body["camera"]
            w, h = int(fc["width"]), int(fc["height"])
            rgb = pano_camera.strip_to_pinhole(rgb, pano, reg.cam_from_dict(fc, w, h))
        except Exception as exc:  # noqa: BLE001 - bad request, reported to the page
            return {"ok": False, "error": f"bad strip or frame camera: {type(exc).__name__}: {exc}"}
    try:
        cam = reg.cam_from_dict(body["camera"], w, h)
        render = reg.decode_image(body["render"])
    except Exception as exc:
        return {"ok": False, "error": f"bad camera or render: {type(exc).__name__}: {exc}"}
    try:
        depth = reg.decode_depth(body["depth"], int(body["depth_w"]), int(body["depth_h"]))
    except Exception as exc:
        return {"ok": False, "error": f"bad depth grid: {type(exc).__name__}: {exc}"}
    user = [(float(u["x"]), float(u["y"]), float(u["lat"]), float(u["lon"]), float(u["h_orth"]))
            for u in body.get("user") or [] if u.get("h_orth") is not None]
    out = reg.register_step(rgb, render, depth, cam, dem=cfg.dem(),
                            geoid_m=float(body.get("geoid_m") or _start_geoid(cfg)),
                            fit_geoid=cfg.geoid_m is None, user=user)
    if pano is not None and out.get("pairs"):
        import numpy as np
        px = np.array([[p["x"], p["y"]] for p in out["pairs"]])
        sx, sy = pano_camera.pinhole_to_strip(pano, cam, px[:, 0], px[:, 1])   # frame px -> strip px
        out["pairs"] = [{**p, "x": float(a), "y": float(b), "frame_x": p["x"], "frame_y": p["y"]}
                        for p, a, b in zip(out["pairs"], sx, sy, strict=True) if a == a and b == b]
    return out


def api_solve(cfg: Config, body: dict) -> dict:
    labels = body.get("labels") or []
    if len(labels) < 4:
        return {"ok": False, "error": f"need at least 4 pairs, got {len(labels)}"}
    w, h = body["image_px"]
    if (body.get("camera") or {}).get("projection") == "pano_cylindrical":
        return _solve_pano(cfg, labels, body["camera"], int(w), int(h))
    obs = [Obs(float(lb["x"]), float(lb["lat"]), float(lb["lon"]), str(lb.get("label") or f"P{i + 1}"),
               up_px=h / 2 - float(lb["y"])) for i, lb in enumerate(labels)]
    try:
        pose = solve_pose(obs, int(w), "pinhole", fit_roll=True)
    except Exception as exc:  # noqa: BLE001 - report solver failures to the page
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    terrain = _terrain(cfg, labels, int(w), int(h), pose)
    if terrain is not None:
        return _solve_reply(cfg, labels, pose, terrain)
    cx = w / 2
    f = pose.f_px
    kx = M_PER_DEG_LAT * math.cos(math.radians(pose.lat))

    def col(lat, lon):
        e, n = (lon - pose.lon) * kx, (lat - pose.lat) * M_PER_DEG_LAT
        d = math.degrees(math.atan2(e, n)) - pose.heading_deg
        return (d + 180) % 360 - 180, math.hypot(e, n)

    half = pose.hfov_deg / 2
    predicted = []
    for lb in labels:
        d, _ = col(float(lb["lat"]), float(lb["lon"]))
        predicted.append(cx + f * math.tan(math.radians(d)) if abs(d) < 89 else None)
    ticks = []
    for lat, lon, name in cfg.centroids():
        d, dist = col(lat, lon)
        if dist <= TICK_RADIUS_M and abs(d) <= half:
            ticks.append((dist, {"x": cx + f * math.tan(math.radians(d)), "name": name}))
    ticks.sort(key=lambda t: t[0])
    res = dict(zip(pose.names, pose.residuals_px, strict=False))
    return {"ok": True,
            "pose": {"lat": pose.lat, "lon": pose.lon, "heading_deg": pose.heading_deg,
                     "f_px": pose.f_px, "hfov_deg": pose.hfov_deg, "rms_px": pose.rms_px,
                     "sigma_m": pose.sigma_m, "roll_deg": pose.roll_deg,
                     "rejected": list(pose.rejected)},
            "residuals_px": [res.get(o.name) for o in obs],
            "predicted_x": predicted,
            "ticks": [t for _, t in ticks[:MAX_TICKS]],
            "terrain": None}


def _solve_pano(cfg: Config, labels: list[dict], c: dict, w: int, h: int) -> dict:
    """`api_solve` for a cylindrical strip: the terrain fit with the cylindrical camera, started from the
    strip's known pose `c` (no column solve: a strip is not a pinhole). Heading offset, pitch, height
    (and position / f) are fitted; the reply is `_solve_reply`'s."""
    dem = cfg.dem()
    if dem is None:
        return {"ok": False, "error": "no DEM loaded; cannot fit a strip"}
    fc = cfg.footprints()
    rows = []
    for lb in labels:
        nm = name_at(fc, float(lb["lat"]), float(lb["lon"]))
        rows.append({**lb, "osm_names": [nm] if nm else []})
    seed = {"lat": float(c["lat"]), "lon": float(c["lon"]), "heading_deg": float(c["heading_deg"]),
            "f_px": float(c["f_px"]), "pitch_deg": float(c.get("pitch_deg") or 0.0)}
    t = ts.solve_terrain(dem, rows, w, h, seed, footprints=fc, pano=True)
    if t is None:
        return {"ok": False, "error": "no label lies on the DEM"}
    stand_in = SimpleNamespace(lat=seed["lat"], lon=seed["lon"], heading_deg=seed["heading_deg"], sigma_m=None)
    return _solve_reply(cfg, labels, stand_in, t)


def _terrain(cfg: Config, labels: list[dict], w: int, h: int, pose) -> dict | None:
    """Terrain fit seeded from the column pose; None when no DEM covers the labels."""
    dem = cfg.dem()
    if dem is None:
        return None
    fc = cfg.footprints()
    rows = []
    for lb in labels:
        nm = name_at(fc, float(lb["lat"]), float(lb["lon"]))
        rows.append({**lb, "osm_names": [nm] if nm else []})
    seed = {"lat": pose.lat, "lon": pose.lon, "heading_deg": pose.heading_deg, "f_px": pose.f_px}
    return ts.solve_terrain(dem, rows, w, h, seed, footprints=fc)


def _camera(c: dict, w: int, h: int):
    """A PinholeCamera from a camera dict (long or short keys, see `register.cam_from_dict`), or a `pano_camera.PanoCamera` when `projection` is "pano_cylindrical"."""
    if c.get("projection") == "pano_cylindrical":
        import pano_camera
        return pano_camera.PanoCamera.from_dict(c, w, h)
    from register import cam_from_dict
    return cam_from_dict(c, w, h)


def _solve_reply(cfg: Config, labels, pose, t: dict) -> dict:
    c = t["camera"]
    cam = _camera(c, c["width"], c["height"])
    dem = cfg.dem()
    zs = [float(dem(float(lb["lat"]), float(lb["lon"]))) + (hh or 0.0)
          for lb, hh in zip(labels, t["heights_m"], strict=True)]
    px, _ = cam.project_ll([float(lb["lat"]) for lb in labels], [float(lb["lon"]) for lb in labels], zs)
    ticks = []
    kx = M_PER_DEG_LAT * math.cos(math.radians(cam.lat))
    for lat, lon, name in cfg.centroids():
        z = dem(lat, lon)
        if not (z == z):
            continue
        x, _y = cam.project_ll(lat, lon, float(z) + 10.0)
        d = math.hypot((lon - cam.lon) * kx, (lat - cam.lat) * M_PER_DEG_LAT)
        if x == x and 0 <= x <= cam.width and d <= TICK_RADIUS_M:
            ticks.append((d, {"x": float(x), "name": name}))
    ticks.sort(key=lambda k: k[0])
    rms = math.sqrt(sum(v * v for v in t["residuals_px"]) / len(labels))
    strip = ({k: c[k] for k in ("projection", "width", "height", "deg_per_col", "col0_heading_deg")}
             if getattr(cam, "cylindrical", False) else {})
    return {"ok": True,
            "pose": {"lat": cam.lat, "lon": cam.lon, "elev_m": cam.elev_m, "ground_m": c["ground_m"],
                     "heading_deg": cam.heading_deg, "pitch_deg": cam.pitch_deg, "f_px": cam.f_px,
                     "hfov_deg": c["hfov_deg"], "rms_px": rms,
                     "median_px": t["median_px"], "median_m": t["median_m"],
                     "sigma_m": pose.sigma_m, "roll_deg": cam.roll_deg, "rejected": [], **strip,
                     "column_pose": {"lat": pose.lat, "lon": pose.lon, "heading_deg": pose.heading_deg}},
            "residuals_px": t["residuals_px"], "residuals_m": t["residuals_m"],
            "heights_m": t["heights_m"], "h0_m": t["h0_m"], "in_footprint": t["in_footprint"],
            "predicted_x": [None if x != x else float(x) for x in px],
            "ticks": [k for _, k in ticks[:MAX_TICKS]],
            "terrain": {"median_px": t["median_px"], "max_px": t["max_px"], "median_m": t["median_m"]}}


def api_project(cfg: Config, body: dict) -> dict:
    """What a solved camera sees of the footprints: visible outlines per building (far to near)
    plus `ids_png`, a base64 PNG id buffer (R + 256 G = 1-based index into `buildings`, 0 none)."""
    dem = cfg.dem()
    if dem is None:
        return {"ok": False, "error": "no DEM available for projecting buildings"}
    w, h = body["image_px"]
    cam = _camera(body["camera"], w, h)
    import base64

    import cv2
    import numpy as np
    r = ts.project_buildings(dem, cfg.footprints(), cam, measured=body.get("heights") == "measured")
    ids = r["ids"].astype(np.int32)
    bgr = np.dstack([np.zeros_like(ids), ids >> 8, ids & 255]).astype(np.uint8)
    ok, png = cv2.imencode(".png", bgr)
    return {"ok": True, "buildings": r["buildings"], "width": int(w), "height": int(h),
            "ids_png": base64.b64encode(png.tobytes()).decode("ascii") if ok else None}
