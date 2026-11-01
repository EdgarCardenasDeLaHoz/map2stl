"""Camera solve of a labelled photo and the OSM buildings a solved camera sees.

`solve`: the column-only `camera_solver.solve_pose` (roll fitted from the label rows), then the
full terrain fit `camera_solver.solve_terrain` seeded from it when the region has a DEM.
`project`: `photo_view.project_buildings` as visible outlines plus a PNG id buffer.

Moved from `tools/photo_label/server.py` (`api_solve`, `api_project`; F-UNIFY Phase 3).
"""
from __future__ import annotations

import math

from city2stl.skyline.camera_solver import M_PER_DEG_LAT, Obs, solve_pose, solve_terrain
from city2stl.skyline.photo_view import PinholeCamera, project_buildings

from .regions import Config, name_at

TICK_RADIUS_M = 3000.0


MAX_TICKS = 300


def solve(cfg: Config, body: dict) -> dict:
    labels = body.get("labels") or []
    if len(labels) < 4:
        return {"ok": False, "error": f"need at least 4 pairs, got {len(labels)}"}
    w, h = body["image_px"]
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
    return solve_terrain(dem, rows, w, h, seed, footprints=fc)


def _camera(c: dict, w: int, h: int):
    return PinholeCamera(float(c["lat"]), float(c["lon"]), float(c["elev_m"]),
                                    float(c["heading_deg"]), float(c["pitch_deg"]),
                         float(c.get("roll_deg") or 0.0), float(c["f_px"]), int(w), int(h))


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
    return {"ok": True,
            "pose": {"lat": cam.lat, "lon": cam.lon, "elev_m": cam.elev_m, "ground_m": c["ground_m"],
                     "heading_deg": cam.heading_deg, "pitch_deg": cam.pitch_deg, "f_px": cam.f_px,
                     "hfov_deg": c["hfov_deg"], "rms_px": rms,
                     "median_px": t["median_px"], "median_m": t["median_m"],
                     "sigma_m": pose.sigma_m, "roll_deg": cam.roll_deg, "rejected": [],
                     "column_pose": {"lat": pose.lat, "lon": pose.lon, "heading_deg": pose.heading_deg}},
            "residuals_px": t["residuals_px"], "residuals_m": t["residuals_m"],
            "heights_m": t["heights_m"], "h0_m": t["h0_m"], "in_footprint": t["in_footprint"],
            "predicted_x": [None if x != x else float(x) for x in px],
            "ticks": [k for _, k in ticks[:MAX_TICKS]],
            "terrain": {"median_px": t["median_px"], "max_px": t["max_px"], "median_m": t["median_m"]}}


def project(cfg: Config, body: dict) -> dict:
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
    r = project_buildings(dem, cfg.footprints(), cam)
    ids = r["ids"].astype(np.int32)
    bgr = np.dstack([np.zeros_like(ids), ids >> 8, ids & 255]).astype(np.uint8)
    ok, png = cv2.imencode(".png", bgr)
    return {"ok": True, "buildings": r["buildings"], "width": int(w), "height": int(h),
            "ids_png": base64.b64encode(png.tobytes()).decode("ascii") if ok else None}
