"""Monocular depth routes (/api/depth, /api/depth/compute, /api/depth/fit, /api/depth/orient): read or compute
`<stem>_depth.npy`, fit metres to it, and classify surface orientation."""
from __future__ import annotations

import json
import math
import threading
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from paths import depth_path, safe_photo
from photos import _photo_rgb

from city2stl.skyline.camera_solver import M_PER_DEG_LAT
from geo2stl.cache import atomic_write

if TYPE_CHECKING:
    from regions import Config

DEPTH_MAX_W = 400


def _block_mean(a, w: int, h: int):
    """Downsample to w x h: mean of the finite pixels in each block (NaN when a block has none)."""
    import numpy as np
    H, W = a.shape
    ys = np.linspace(0, H, h + 1).astype(int)
    xs = np.linspace(0, W, w + 1).astype(int)
    ys[1:] = np.maximum(ys[1:], ys[:-1] + 1)
    xs[1:] = np.maximum(xs[1:], xs[:-1] + 1)
    fin = np.isfinite(a)
    v = np.where(fin, a, 0.0).astype(np.float64)
    cs_v = np.pad(v, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    cs_n = np.pad(fin.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def box(c):
        return (c[np.ix_(ys[1:], xs[1:])] - c[np.ix_(ys[:-1], xs[1:])]
                - c[np.ix_(ys[1:], xs[:-1])] + c[np.ix_(ys[:-1], xs[:-1])])
    n = box(cs_n)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = box(cs_v) / n
    out[n < 0.5] = np.nan
    return out.astype("<f4")


def api_depth(cfg: Config, rel: str) -> dict:
    import base64

    import numpy as np
    photo = safe_photo(cfg, rel)
    if photo is None:
        raise ValueError(f"no such photo: {rel}")
    f = depth_path(photo)
    if f.is_file():
        d = np.load(f).astype(np.float32)
        ph, pw = d.shape
        w = min(pw, DEPTH_MAX_W)
        h = max(1, round(ph * w / pw))
        small = _block_mean(d, w, h) if (w, h) != (pw, ph) else d.astype("<f4")
        return {"ok": True, "state": "ready", "w": w, "h": h, "photo_w": pw, "photo_h": ph,
                "disp": base64.b64encode(np.ascontiguousarray(small).tobytes()).decode("ascii")}
    with cfg.depth_lock:
        st = dict(cfg.depth_state.get((cfg.region, rel), {}))
    if st.get("state") == "running":
        return {"ok": True, "state": "running"}
    if st.get("state") == "error":
        return {"ok": True, "state": "error", "error": st.get("error", "")}
    return {"ok": True, "state": "missing"}


def _compute_depth(cfg: Config, key: tuple[str, str], photo: Path, rgb) -> None:
    """Background thread body of :func:`api_depth_compute` (GPU only)."""
    import numpy as np

    from city2stl.resources import free_gpu_cache, wait_for_gpu
    err = None
    try:
        if not wait_for_gpu(2):
            raise RuntimeError("no CUDA GPU available; depth is not run on the CPU")
        from city2stl.skyline.depth_estimation import _depth_anything_raw
        d = np.asarray(_depth_anything_raw(rgb, device="cuda"), dtype=np.float32)
        f = depth_path(photo)
        atomic_write(f, lambda fh: np.save(fh, d))
    except Exception as exc:  # noqa: BLE001 - reported to the page
        traceback.print_exc()
        err = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            free_gpu_cache()
        except Exception:  # noqa: BLE001
            pass
    with cfg.depth_lock:
        cfg.depth_state[key] = {"state": "error", "error": err} if err else {"state": "ready"}


def api_depth_compute(cfg: Config, body: dict) -> dict:
    rel = body.get("photo", "")
    photo = safe_photo(cfg, rel)
    if photo is None:
        raise ValueError(f"no such photo: {rel}")
    if depth_path(photo).is_file():
        return {"ok": True, "state": "ready"}
    key = (cfg.region, rel)
    with cfg.depth_lock:
        if cfg.depth_state.get(key, {}).get("state") == "running":
            return {"ok": True, "state": "running"}
        rgb = _photo_rgb(cfg, rel)
        cfg.depth_state[key] = {"state": "running"}
    threading.Thread(target=_compute_depth, args=(cfg, key, photo, rgb), daemon=True).start()
    return {"ok": True, "state": "running"}


def api_depth_fit(cfg: Config, body: dict) -> dict:
    """Fit metres = a / (disp - b) of the photo's disparity to Google z-distances on a grid.

    The disparity (photo size) is resampled to the grid by block mean ignoring NaN (nearest when
    the grid is finer than the depth map); pairs finite on both sides with 20 <= d <= 5000 m are
    kept, at most 5000 of them at evenly spaced indices (deterministic)."""
    import base64

    import numpy as np

    from city2stl.skyline._pano.roof_fit import fit_inverse_depth
    rel = body.get("photo", "")
    photo = safe_photo(cfg, rel)
    if photo is None:
        raise ValueError(f"no such photo: {rel}")
    f = depth_path(photo)
    if not f.is_file():
        return {"ok": False, "reason": "no depth for this photo yet"}
    gw, gh = int(body.get("depth_w", 0)), int(body.get("depth_h", 0))
    if gw < 1 or gh < 1:
        return {"ok": False, "reason": "bad depth grid size"}
    dist = np.frombuffer(base64.b64decode(body.get("depth", "")), dtype="<f4").astype(np.float64)
    if dist.size != gw * gh:
        return {"ok": False, "reason": f"depth grid has {dist.size} values, expected {gw * gh}"}
    disp = np.load(f).astype(np.float32)
    ph, pw = disp.shape
    if (gw, gh) != (pw, ph):
        gw2, gh2 = min(gw, pw), min(gh, ph)
        disp = _block_mean(disp, gw2, gh2)
        if (gw2, gh2) != (gw, gh):
            yi = np.minimum(np.arange(gh) * gh2 // gh, gh2 - 1)
            xi = np.minimum(np.arange(gw) * gw2 // gw, gw2 - 1)
            disp = disp[np.ix_(yi, xi)]
    disp = disp.astype(np.float64).ravel()
    ok = np.isfinite(disp) & np.isfinite(dist) & (dist >= 20) & (dist <= 5000)
    idx = np.flatnonzero(ok)
    if idx.size > 5000:
        idx = idx[np.linspace(0, idx.size - 1, 5000).astype(int)]
    if idx.size < 10:
        return {"ok": False, "reason": f"only {idx.size} usable depth pairs (need 10)"}
    cal = fit_inverse_depth(disp[idx], dist[idx])
    if cal is None:
        return {"ok": False, "reason": "no positive-scale fit"}
    return {"ok": True, "a": cal.a, "b": cal.b, "n": cal.n, "err_median_log": cal.mad_rel}


def _orient_source(orient, z, cam: dict, dem, buildings) -> dict:
    """Backproject, take normals, classify (ground vs roof from the DEM when there is one) and
    summarise the buildings of one depth grid ``z`` (gh, gw)."""
    import base64

    import numpy as np
    gh, gw = z.shape
    P = orient.backproject(z, cam)
    res = orient.classify(orient.normals(P), P, cam, _orient_ground(P, cam, dem))
    summ = {}
    if buildings:
        labels, ids = orient.rasterize(buildings, gw, gh, cam["width"], cam["height"])
        for k, v in orient.building_summary(res["cls"], res["pitch"], res["az"], labels).items():
            summ[str(ids[k])] = v
    b64 = lambda a: base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")  # noqa: E731
    return {"w": gw, "h": gh, "cls": b64(res["cls"].astype("u1")),
            "pitch": b64(res["pitch"].astype("<f4")), "az": b64(res["az"].astype("<f4")),
            "buildings": summ, "_cls": res["cls"]}


def _orient_ground(P, cam: dict, dem):
    """DEM elevation (gh, gw) under the back-projected points, or None without a DEM."""
    import numpy as np
    if dem is None:
        return None
    lat0, lon0 = float(cam["lat"]), float(cam["lon"])
    lat = lat0 + P[..., 1] / M_PER_DEG_LAT
    lon = lon0 + P[..., 0] / (M_PER_DEG_LAT * math.cos(math.radians(lat0)))
    with np.errstate(invalid="ignore"):
        return np.asarray(dem(lat, lon), dtype=np.float64)


def api_depth_orient(cfg: Config, body: dict) -> dict:
    """Surface orientation of the photo (from `<stem>_depth.npy` + the a/b fit) and/or of the Google
    depth grid; see orient.py. Writes `<stem>_orient.json` beside the photo."""
    import base64
    import time

    import numpy as np
    import orient
    rel = body.get("photo", "")
    photo = safe_photo(cfg, rel)
    if photo is None:
        raise ValueError(f"no such photo: {rel}")
    cam = body.get("camera")
    if not isinstance(cam, dict) or not all(k in cam for k in ("lat", "lon", "heading_deg", "pitch_deg",
                                                                  "f_px", "width", "height")):
        return {"ok": False, "reason": "no camera"}
    cam = dict(cam)
    cam.setdefault("elev_m", 0.0)
    buildings = body.get("buildings") or []
    dem = cfg.dem()
    a, b = body.get("a"), body.get("b")
    gw0 = max(8, min(int(body.get("grid_w") or 320), 2000))
    out = {"photo": None, "google": None, "agree": None}
    pz = gz = None
    f = depth_path(photo)
    if f.is_file() and a is not None and b is not None:
        disp = np.load(f).astype(np.float32)
        ph, pw = disp.shape
        w = min(gw0, pw)
        h = max(1, round(ph * w / pw))
        d = (_block_mean(disp, w, h) if (w, h) != (pw, ph) else disp).astype(np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            pz = float(a) / (d - float(b))
        pz[~np.isfinite(pz) | (pz <= 0) | (pz > 5000)] = np.nan
    gw, gh = int(body.get("depth_w") or 0), int(body.get("depth_h") or 0)
    if body.get("depth") and gw >= 1 and gh >= 1:
        g = np.frombuffer(base64.b64decode(body["depth"]), dtype="<f4").astype(np.float64)
        if g.size == gw * gh:
            gz = g.reshape(gh, gw)
    if pz is None and gz is None:
        return {"ok": False, "reason": "no usable depth: need the photo depth with a, b, or a Google depth grid"}
    if pz is not None:
        out["photo"] = _orient_source(orient, pz, cam, dem, buildings)
    if gz is not None:
        out["google"] = _orient_source(orient, gz, cam, dem, buildings)
    if out["photo"] and out["google"]:
        pc, gc = out["photo"]["_cls"], out["google"]["_cls"]
        yi = np.minimum(np.arange(pc.shape[0]) * gc.shape[0] // pc.shape[0], gc.shape[0] - 1)
        xi = np.minimum(np.arange(pc.shape[1]) * gc.shape[1] // pc.shape[1], gc.shape[1] - 1)
        gr = gc[np.ix_(yi, xi)]
        both = (pc > 0) & (gr > 0)
        out["agree"] = float((pc[both] == gr[both]).mean()) if both.any() else None
    for k in ("photo", "google"):
        if out[k]:
            out[k].pop("_cls")
    try:
        f.with_name(f.stem.removesuffix("_depth") + "_orient.json").write_text(json.dumps({
            "camera": cam, "a": a, "b": b,
            "photo_buildings": (out["photo"] or {}).get("buildings"),
            "google_buildings": (out["google"] or {}).get("buildings"),
            "written": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
    except OSError:
        pass
    return {"ok": True, **out}
