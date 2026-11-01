"""Local backend for hand-pairing photo points with a satellite top view (index.html).

Serves the pairing page and a few JSON endpoints:

    GET  /api/regions     {"default": id, "regions": [{id, label, photos: n}]} from regions.json
    GET  /api/photos      photos of the region (path relative to its photo root, prefixed by the
                          root's key when it has one; pixel size)
                          (frames of `03_seeds/seed_*` folders list without the strip / masks / json)
    GET  /photo/<rel>     one photo's bytes
    GET  /api/footprints  OSM building footprints as GeoJSON (from a cached Overpass JSON)
    GET  /api/labels      saved pairs for ?photo=<rel>
    POST /api/labels      save pairs to city2stl/skyline/sites/annotations/<region>_<photo>.json
    POST /api/solve       camera fit on the pairs: terrain fit (camera_solver.solve_terrain, DEM
                          from --dem) seeded by camera_solver.solve_pose, which is the answer
                          when no DEM covers the labels; plus predicted columns of named
                          footprints in view (ticks) to check the solution against the photo
    POST /api/project     {camera, image_px} -> OSM footprints in view as base/top pixel rings
    GET  /api/config      {"tiles3d": bool, "view": {lat, lon, ground_m}|null, "region": id}:
                          whether a Google Maps key is available (never the key), and where the
                          maps start (the region's `view`, else the DEM centre)
    GET  /tiles3d/<path>  proxy to https://tile.googleapis.com/<path> for Google Photorealistic 3D
                          Tiles; only v1/3dtiles/... is forwarded and the key
                          (GOOGLE_MAPS_API_KEY, env or map2stl/.env) is added here, never sent to
                          the browser. JSON responses get their child URIs rewritten to /tiles3d/...
    GET  /api/model/info  our city model for the region (model_glb.build_or_load), built in the
                          background on the first call: {"status": "building"|"ready"|"error",
                          origin, bbox, source, faces, glb_bytes, build_s, ground, ...};
                          ?rebuild=1 retries after an error
    GET  /api/model.glb   that model as glTF binary, east-north-up metres at `origin`, heights
                          orthometric (the page lifts it by the geoid offset it measures live)
    GET  /api/depth       ?photo=<rel>: monocular depth of the photo, from `<stem>_depth.npy` beside it
                          (float32, photo size, relative inverse depth, larger = nearer):
                          {ok, state: "ready"|"running"|"missing"|"error", error?, and when ready
                          w, h (<= 400 wide, block mean ignoring NaN), photo_w, photo_h,
                          disp: base64 little-endian float32 row-major w*h, NaN allowed}
    POST /api/photos/screen {region}: start a background job (once per region at a time) that
                          screens every photo lacking `<stem>_screen.json` with screen.py (aspect
                          >= 3.5 = stitched panorama; SegFormer ground share >= 0.25 = street
                          level) and flags the failures with an "auto: ..." reason through the same
                          path as /api/photo/flag; a photo with a cache file is never re-flagged,
                          so an unflag sticks. Without CUDA, SegFormer runs only for <= 10 photos
    GET  /api/photos/screen ?region=: {ok, state: "idle"|"running"|"done"|"error", done, total,
                          flagged: [rel, ...] flagged by this run, message}
    POST /api/depth/compute {photo}: run Depth Anything V2 on the GPU in a background thread and write
                          `<stem>_depth.npy` -> {ok, state: "running"|"ready"}; never on the CPU
                          (no CUDA ends in state "error" on the next GET /api/depth)
    POST /api/depth/fit   {photo, depth: base64 float32 Google z-distance in m (NaN = no hit),
                          depth_w, depth_h} on a grid covering the whole photo -> {ok, a, b, n,
                          err_median_log} with metres = a / (disp - b), or {ok: false, reason}
    POST /api/depth/orient {photo, camera, a, b, depth?, depth_w, depth_h, grid_w?, buildings?}: surface
                          orientation (ground / flat roof / pitched roof / wall per grid cell, see
                          orient.py) from the photo depth (needs a, b) and/or the Google depth grid;
                          -> {ok, photo, google, agree} with base64 uint8 cls and float32 pitch / az
                          grids and per-building summaries, or {ok: false, reason}. Also writes
                          `<stem>_orient.json` beside the photo for the heights pipeline

Google tile cache: non-JSON /tiles3d responses (status 200, Cache-Control max-age > 0, not
no-store) are kept in `runs/photo_label_tiles/<sha1[:2]>/<sha1>.bin` (+ `.json` with ctype and
expiry) and served from disk while fresh. The key is the sha1 of the path under v1/3dtiles/
without the query, so the API key and session token are never stored or part of a name. JSON
(it carries the session token) is always fetched live. Capped at 2 GB, oldest files go first.

One server serves every region of tools/photo_label/regions.json (label, osm, dem, photos: a map of
key -> folder). Each region's Config is built on first use; the Google key and tile session are
shared, and /tiles3d does not depend on the region. Every region-specific GET takes `?region=<id>`
and every POST a `"region"` in its body; missing means the server default (--region), an unknown id
is a 400. `--osm/--dem/--photos` override the default region's entry.

A label file keeps the format `photo_localize.solve_from_labels` reads (label, osm_names, x, y)
and may carry the solved `camera` (+ `camera_source`: "solve"|"auto") from the POST body, which the
page restores so buildings reappear without solving again; older files without it still load. It adds `lat`, `lon`, the point clicked on the map, which that function then uses directly.
A point clicked in the 3D view also carries `h_ellipsoid_m` (WGS84 ellipsoidal height).

Run with the working venv:
    ~/.venvs/map2stl/Scripts/python.exe tools/photo_label/server.py [--port 8767] [--region granada] [--osm <path>]
Then open http://localhost:8767/
"""
from __future__ import annotations

import argparse
import json
import math
import re
import socket
import sys
import threading
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlparse

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from city2stl.photo_label import candidates as _candidates  # noqa: E402
from city2stl.photo_label import photos as _photos  # noqa: E402
from city2stl.photo_label import solve as _solve  # noqa: E402
from city2stl.photo_label.candidates import (  # noqa: E402,F401 - moved there (F-UNIFY Phase 3)
    _FROM,
    HEADING_AGREE_DEG,
    HEADING_BONUS,
    PANO_CAM_HEIGHT_M,
    SEED_MATCH_M,
    _bearing_deg,
    _dem_centre,
    _guess_cache_path,
    _guess_key,
    _hav_m,
    _norm_text,
    _panoview_pose,
    _parse_coord,
    _read_json,
    _seed_camera,
    _seed_frame_pose,
    _tag_candidates,
    pano_seed,
    photo_focal_35mm,
    photo_hints,
)
from city2stl.photo_label.candidates import start_geoid as _start_geoid  # noqa: E402
from city2stl.photo_label.photos import (  # noqa: E402,F401 - moved there (F-UNIFY Phase 3)
    _DERIVED,
    _EXT_RANK,
    _FLAGS_LOCK,
    _SEED_NONFRAME,
    _is_seed_nonframe,
    _set_flag,
    flags_path,
    image_size,
    label_path,
    list_photos,
    load_flags,
    photo_rel,
    safe_photo,
)
from city2stl.photo_label.photos import photo_files as _photo_files  # noqa: E402
from city2stl.photo_label.photos import photo_rgb as _photo_rgb  # noqa: E402
from city2stl.photo_label.regions import (  # noqa: E402,F401 - moved there (F-UNIFY Phase 3)
    _AUTO,
    ANNOTATIONS,
    DEFAULT_DEM,
    DEFAULT_OSM,
    DEFAULT_PHOTOS,
    IMG_EXT,
    KEY_VAR,
    MODEL_DIR,
    REGIONS_FILE,
    TILES_CACHE_DIR,
    Config,
    Regions,
    UnknownRegion,
    _abs,
    _centroid,
    _inside,
    _round_coords,
    _stitch_rings,
    geojson_footprints,
    load_api_key,
    load_regions,
    name_at,
    overpass_to_geojson,
    start_view,
)
from city2stl.photo_label.solve import (  # noqa: E402,F401 - moved there (F-UNIFY Phase 3)
    MAX_TICKS,
    TICK_RADIUS_M,
    _camera,
    _solve_reply,
    _terrain,
)
from city2stl.skyline.camera_solver import M_PER_DEG_LAT  # noqa: E402
from geo2stl.cache import atomic_write  # noqa: E402

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import model_glb  # noqa: E402
import screen as photo_screen  # noqa: E402

MAX_BODY = 32 * 1024 * 1024   # /api/register/step carries a render and a depth grid (base64)
TILES_HOST = "https://tile.googleapis.com"
TILES_PREFIX = "v1/3dtiles/"
TILES_CACHE_CAP = 2 * 1024 ** 3
TILES_CACHE_SWEEP_EVERY = 100   # writes between size checks
_idle: dict[str, list] = {}     # host -> idle keep-alive HTTPS connections, shared by handler threads
_idle_lock = threading.Lock()
IDLE_PER_HOST = 16


class _PooledResponse:
    """An http.client response that hands its connection back to the pool when closed after
    being read to the end (a half-read connection is closed instead)."""

    def __init__(self, resp, conn, host):
        self._resp, self._conn, self._host = resp, conn, host
        self.status, self.reason, self.headers = resp.status, resp.reason, resp.headers

    def read(self, *a):
        return self._resp.read(*a)

    def close(self):
        if self._conn is None:
            return
        done = self._resp.isclosed()
        self._resp.close()
        with _idle_lock:
            pool = _idle.setdefault(self._host, [])
            if done and len(pool) < IDLE_PER_HOST:
                pool.append(self._conn)
                self._conn = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def pooled_urlopen(req: urllib.request.Request, timeout: float = 30):
    """GET `req` over a pooled keep-alive connection, with urlopen's contract (HTTPError on >= 400).

    A fresh TLS handshake per tile cost ~440 ms against ~70 ms on a reused connection, and a
    view needs ~1,000 tile requests, so the offscreen render never settled. A stale pooled
    connection (closed by Google while idle) is dropped and the request retried on a new one.
    """
    import http.client
    u = urlparse(req.full_url)
    path = u.path + (f"?{u.query}" if u.query else "")
    for attempt in (0, 1):
        with _idle_lock:
            pool = _idle.get(u.netloc)
            conn = pool.pop() if pool and not attempt else None
        if conn is None:
            conn = http.client.HTTPSConnection(u.netloc, timeout=timeout)
        try:
            conn.request("GET", path, headers=dict(req.header_items()))
            resp = conn.getresponse()
        except (http.client.HTTPException, OSError):
            conn.close()
            if attempt:
                raise
            continue
        out = _PooledResponse(resp, conn, u.netloc)
        if resp.status >= 400:
            raise urllib.error.HTTPError(req.full_url, resp.status, resp.reason, resp.headers, out)
        return out


urlopen = pooled_urlopen  # tests replace this; the proxy never calls urllib directly


def api_photo_flag(cfg: Config, body: dict) -> dict:
    """Mark (`bad` true) or unmark a photo as unusable; persists to `<region>_photo_flags.json`
    as {photo rel: {reason, flagged: ISO date}}. Returns {ok, bad, count_flagged}."""
    return _photos.photo_flag(cfg, body)


# ---------- auto screening ----------

SCREEN_CPU_MAX = 10   # more photos than this needing SegFormer without a GPU: aspect rule only
_screen_state: dict[str, dict] = {}   # region -> {"state", "done", "total", "flagged", "message"}
_screen_lock = threading.Lock()


def _screen_region(cfg: Config) -> None:
    """Background thread body of :func:`api_photos_screen`: screen every photo of the region
    lacking `<stem>_screen.json` (flagged ones too) and flag the ones that fail a rule."""
    st = _screen_state[cfg.region]
    gpu = False
    try:
        rels = {p: photo_rel(k, r, p) for k, r, p in _photo_files(cfg)}
        todo = [p for p in rels if not photo_screen.screen_path(p).is_file() and not _has_known_pose(p, cfg)]
        with _screen_lock:
            st["total"] = len(todo)
        try:
            import torch
            gpu = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001 - no torch means no SegFormer either
            gpu = False
        # the aspect rule is free; only the rest needs SegFormer
        pending = []
        for p in todo:
            try:
                res = photo_screen.screen_photo(p, labels=False)
            except Exception:  # noqa: BLE001 - an unreadable photo is skipped
                res = None
            if res is None:
                pending.append(p)
            else:
                _screen_flag(cfg, st, rels[p], res)
        if pending and not gpu:
            if len(pending) > SCREEN_CPU_MAX:
                with _screen_lock:
                    st["message"] = f"no CUDA GPU: {len(pending)} photos screened by aspect only"
                pending = []
        if pending:
            from city2stl.resources import wait_for_gpu
            if gpu:
                wait_for_gpu(2)
            for p in pending:
                try:
                    res = photo_screen.screen_photo(p)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    res = None
                if res is not None:
                    _screen_flag(cfg, st, rels[p], res)
        with _screen_lock:
            st["state"] = "done"
    except Exception as exc:  # noqa: BLE001 - reported to the page
        traceback.print_exc()
        with _screen_lock:
            st.update(state="error", message=f"{type(exc).__name__}: {exc}")
    finally:
        if gpu:
            try:
                from city2stl.resources import free_gpu_cache
                free_gpu_cache()
            except Exception:  # noqa: BLE001
                pass


def _has_known_pose(p: Path, cfg: Config) -> bool:
    """A photo whose camera the panorama pipeline knows (pano_seed) is never auto-flagged."""
    try:
        return pano_seed(p, cfg) is not None
    except Exception:  # noqa: BLE001 - unknown pose: screen it like any photo
        return False


def _screen_flag(cfg: Config, st: dict, rel: str, res: dict) -> None:
    """Count one screened photo; flag it when the fresh result has a reason (a photo already
    flagged keeps its flag and reason)."""
    if res.get("reason") and rel not in load_flags(cfg):
        _set_flag(cfg, rel, True, res["reason"], keep=True)
        with _screen_lock:
            st["flagged"].append(rel)
    with _screen_lock:
        st["done"] += 1


def api_photos_screen(cfg: Config, body: dict) -> dict:
    """POST /api/photos/screen: start the region's screening job unless one is running."""
    with _screen_lock:
        if _screen_state.get(cfg.region, {}).get("state") == "running":
            return {"ok": True, "state": "running"}
        _screen_state[cfg.region] = {"state": "running", "done": 0, "total": 0,
                                     "flagged": [], "message": ""}
    threading.Thread(target=_screen_region, args=(cfg,), daemon=True).start()
    return {"ok": True, "state": "running"}


def api_photos_screen_state(cfg: Config) -> dict:
    with _screen_lock:
        st = dict(_screen_state.get(cfg.region) or {"state": "idle", "done": 0, "total": 0,
                                                    "flagged": [], "message": ""})
        st["flagged"] = list(st["flagged"])
    return {"ok": True, **st}


def api_get_labels(cfg: Config, rel: str) -> dict:
    return _photos.get_labels(cfg, rel)


def api_save_labels(cfg: Config, body: dict) -> dict:
    return _photos.save_labels(cfg, body)


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
    return _candidates.register_candidates(cfg, body)


def api_register_step(cfg: Config, body: dict) -> dict:
    """One render -> new camera. The render and depth live in memory only; nothing is stored."""
    import register as reg
    try:
        rgb = _photo_rgb(cfg, body["photo"])
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    h, w = rgb.shape[:2]
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
    return reg.register_step(rgb, render, depth, cam, dem=cfg.dem(),
                             geoid_m=float(body.get("geoid_m") or _start_geoid(cfg)),
                             fit_geoid=cfg.geoid_m is None, user=user)


def api_solve(cfg: Config, body: dict) -> dict:
    return _solve.solve(cfg, body)


def api_project(cfg: Config, body: dict) -> dict:
    """What a solved camera sees of the footprints: visible outlines per building (far to near)
    plus `ids_png`, a base64 PNG id buffer (R + 256 G = 1-based index into `buildings`, 0 none)."""
    return _solve.project(cfg, body)


def rewrite_tile_uris(node, cfg: Config | None = None):
    """Point every child `uri`/`url` of a tileset JSON back through /tiles3d, in place.

    Google answers with absolute (`https://tile.googleapis.com/v1/...`) or root-relative
    (`/v1/...`) URIs; both become `/tiles3d/v1/...`. A `key` query parameter is dropped and the
    `session` token is remembered on `cfg` so tile requests that omit it can be completed.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("uri", "url") and isinstance(v, str):
                node[k] = _rewrite_uri(v, cfg)
            else:
                rewrite_tile_uris(v, cfg)
    elif isinstance(node, list):
        for v in node:
            rewrite_tile_uris(v, cfg)
    return node


def _rewrite_uri(uri: str, cfg: Config | None) -> str:
    if uri.startswith(TILES_HOST):
        uri = uri[len(TILES_HOST):]
    u = urlparse(uri)
    if cfg is not None:
        for k, v in parse_qsl(u.query):
            if k == "session" and v:
                cfg.session = v
    if u.query:
        q = urlencode([(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k != "key"])
        uri = u._replace(query=q).geturl()
    if uri.startswith("/v1/"):
        uri = "/tiles3d" + uri
    return uri


def tiles_upstream_url(cfg: Config, path: str, query: str) -> str | None:
    """Google URL for a proxied request, or None when the path is not under v1/3dtiles/."""
    rel = path.lstrip("/")
    if not rel.startswith(TILES_PREFIX) or ".." in rel.split("/") or "\\" in rel:
        return None
    q = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k.lower() != "key"]
    # root.json opens a new session and Google rejects one passed to it (400 "Unknown name
    # session"); only the child tiles it lists carry the token.
    if cfg.session and rel != TILES_PREFIX + "root.json" and not any(k == "session" for k, _ in q):
        q.append(("session", cfg.session))
    q.append(("key", cfg.api_key))
    return f"{TILES_HOST}/{rel}?{urlencode(q)}"


def _build_model(cfg: Config) -> None:
    """Background thread body of :func:`api_model_info`."""
    try:
        dem = cfg.dem()
        if dem is None:
            raise ValueError("the model needs a terrain DEM (--dem)")
        meta = model_glb.build_or_load(dem, cfg.dem_path, cfg.region, cfg.model_dir,
                                       footprints=cfg.footprints, osm_path=cfg.osm_path)
        state = {**meta, "status": "ready"}
    except Exception as exc:  # noqa: BLE001 - reported to the page
        traceback.print_exc()
        state = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    with cfg._model_lock:
        cfg._model = state


def api_model_info(cfg: Config, rebuild: bool = False) -> dict:
    """State of the region's model; starts building it (in a thread) when not built yet, or
    again after an error when ``rebuild``. The glb path stays on the server."""
    with cfg._model_lock:
        if cfg._model["status"] == "idle" or (rebuild and cfg._model["status"] == "error"):
            cfg._model = {"status": "building"}
            threading.Thread(target=_build_model, args=(cfg,), daemon=True).start()
        state = dict(cfg._model)
    state.pop("glb_path", None)
    return state


# ---------- depth view ----------

DEPTH_MAX_W = 400
_depth_state: dict[tuple[str, str], dict] = {}   # (region, photo) -> {"state", "error"}
_depth_lock = threading.Lock()


def depth_path(photo: Path) -> Path:
    return photo.with_name(photo.stem + "_depth.npy")


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
    with _depth_lock:
        st = dict(_depth_state.get((cfg.region, rel), {}))
    if st.get("state") == "running":
        return {"ok": True, "state": "running"}
    if st.get("state") == "error":
        return {"ok": True, "state": "error", "error": st.get("error", "")}
    return {"ok": True, "state": "missing"}


def _compute_depth(key: tuple[str, str], photo: Path, rgb) -> None:
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
    with _depth_lock:
        _depth_state[key] = {"state": "error", "error": err} if err else {"state": "ready"}


def api_depth_compute(cfg: Config, body: dict) -> dict:
    rel = body.get("photo", "")
    photo = safe_photo(cfg, rel)
    if photo is None:
        raise ValueError(f"no such photo: {rel}")
    if depth_path(photo).is_file():
        return {"ok": True, "state": "ready"}
    key = (cfg.region, rel)
    with _depth_lock:
        if _depth_state.get(key, {}).get("state") == "running":
            return {"ok": True, "state": "running"}
        rgb = _photo_rgb(cfg, rel)
        _depth_state[key] = {"state": "running"}
    threading.Thread(target=_compute_depth, args=(key, photo, rgb), daemon=True).start()
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


# ---------- Google tile disk cache ----------

def _cache_paths(cache_dir: Path, rel: str) -> tuple[Path, Path]:
    import hashlib
    h = hashlib.sha1(rel.encode("utf-8")).hexdigest()
    d = Path(cache_dir) / h[:2]
    return d / (h + ".bin"), d / (h + ".json")


def tile_cache_get(cache_dir: Path, rel: str, now: float | None = None) -> tuple[bytes, str] | None:
    import time
    binp, metap = _cache_paths(cache_dir, rel)
    try:
        meta = json.loads(metap.read_text(encoding="utf-8"))
        if meta["expires"] <= (time.time() if now is None else now):
            return None
        return binp.read_bytes(), meta["ctype"]
    except (OSError, ValueError, KeyError):
        return None


def _max_age(cache_control: str | None) -> int:
    if not cache_control or "no-store" in cache_control.lower():
        return 0
    m = re.search(r"max-age\s*=\s*(\d+)", cache_control, re.I)
    return int(m.group(1)) if m else 0


_tile_writes = 0
_tile_lock = threading.Lock()


def tile_cache_put(cache_dir: Path, rel: str, body: bytes, ctype: str, max_age: int,
                   now: float | None = None) -> None:
    import time
    global _tile_writes
    binp, metap = _cache_paths(cache_dir, rel)
    binp.parent.mkdir(parents=True, exist_ok=True)
    exp = (time.time() if now is None else now) + max_age
    for p, data in ((binp, body), (metap, json.dumps({"ctype": ctype, "expires": exp}).encode())):
        atomic_write(p, lambda f, data=data: f.write(data))
    with _tile_lock:
        _tile_writes += 1
        sweep = _tile_writes % TILES_CACHE_SWEEP_EVERY == 0
    if sweep:
        tile_cache_trim(cache_dir)


def tile_cache_trim(cache_dir: Path, cap: int = TILES_CACHE_CAP) -> None:
    """Delete the oldest files until the cache is under `cap` bytes."""
    files = []
    for p in Path(cache_dir).glob("*/*"):
        try:
            st = p.stat()
            files.append((st.st_mtime, st.st_size, p))
        except OSError:
            pass
    total = sum(f[1] for f in files)
    for _, size, p in sorted(files):
        if total <= cap:
            break
        try:
            p.unlink()
            total -= size
        except OSError:
            pass


class LabelHandler(BaseHTTPRequestHandler):
    # Keep-alive: a page of 3D tiles is ~1,000 requests, and each new connection to localhost cost
    # ~300 ms (six at a time), so HTTP/1.0 left tiles queued for a second each. Every response
    # therefore carries a Content-Length or closes the connection.
    protocol_version = "HTTP/1.1"
    regions: Regions

    @property
    def cfg(self) -> Config:
        """The default region (its Config also carries the shared tile session)."""
        return self.regions.tiles

    def _send(self, code: int, raw: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _proxy_tiles(self, u) -> None:
        """Forward /tiles3d/<path> to Google with the key added; stream the body back."""
        cfg = self.cfg
        if not cfg.api_key:
            self._json(503, {"ok": False, "error": f"3D needs {KEY_VAR} (set it in the environment or map2stl/.env)"})
            return
        rel = unquote(u.path[len("/tiles3d/"):])
        url = tiles_upstream_url(cfg, rel, u.query)
        if url is None:
            self._json(404, {"ok": False, "error": "only v1/3dtiles/ is proxied"})
            return
        cacheable = not rel.lower().endswith(".json")
        cdir = getattr(self.regions, "tiles_cache_dir", cfg.tiles_cache_dir)
        if cacheable:
            hit = tile_cache_get(cdir, rel)
            if hit is not None:
                self._send(200, hit[0], hit[1])
                return
        if rel == TILES_PREFIX + "root.json":  # opens a billed session; tiles under it are not counted
            from geo2stl import google_usage
            google_usage.record("tiles_root")
        try:
            resp = urlopen(urllib.request.Request(url, headers={"User-Agent": "photo-label/1"}), timeout=30)
        except urllib.error.HTTPError as exc:
            self._send(exc.code, self._scrub(exc.read()), exc.headers.get_content_type() or "text/plain")
            return
        except Exception as exc:  # noqa: BLE001 - never echo the exception text, it may hold the URL
            self._json(502, {"ok": False, "error": f"tile upstream unreachable ({type(exc).__name__})"})
            return
        with resp:
            ctype = resp.headers.get("Content-Type") or "application/octet-stream"
            code = getattr(resp, "status", 200)
            if "json" in ctype:
                body = self._scrub(resp.read())
                try:
                    body = json.dumps(rewrite_tile_uris(json.loads(body), cfg)).encode("utf-8")
                except ValueError:
                    pass
                self._send(code, body, ctype)
                return
            max_age = _max_age(resp.headers.get("Cache-Control")) if cacheable and code == 200 else 0
            length = resp.headers.get("Content-Length")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            if length:
                self.send_header("Content-Length", length)
            else:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            kept = [] if max_age > 0 else None
            while chunk := resp.read(65536):
                self.wfile.write(chunk)
                if kept is not None:
                    kept.append(chunk)
            if kept is not None:
                try:
                    tile_cache_put(cdir, rel, b"".join(kept), ctype, max_age)
                except OSError:
                    pass

    def _scrub(self, raw: bytes) -> bytes:
        k = self.cfg.api_key.encode("utf-8")
        return raw.replace(k, b"REDACTED") if k else raw

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        try:
            if u.path.startswith("/tiles3d/"):
                self._proxy_tiles(u)
                return
            cfg = self.regions.get(parse_qs(u.query).get("region", [""])[0])
            if u.path == "/api/config":
                self._json(200, {"tiles3d": bool(cfg.api_key), "view": start_view(cfg), "region": cfg.region})
            elif u.path == "/api/regions":
                self._json(200, self.regions.listing())
            elif u.path in ("/", "/index.html"):
                self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/model/info":
                self._json(200, api_model_info(cfg, parse_qs(u.query).get("rebuild") == ["1"]))
            elif u.path == "/api/model.glb":
                with cfg._model_lock:
                    glb = cfg._model.get("glb_path")
                if not glb:
                    self._json(404, {"ok": False, "error": "model not built yet (GET /api/model/info)"})
                else:
                    self._send(200, Path(glb).read_bytes(), "model/gltf-binary")
            elif u.path == "/api/depth":
                self._json(200, api_depth(cfg, parse_qs(u.query).get("photo", [""])[0]))
            elif u.path == "/api/photos/screen":
                self._json(200, api_photos_screen_state(cfg))
            elif u.path == "/api/photos":
                self._json(200, list_photos(cfg, include_bad=parse_qs(u.query).get("all") == ["1"]))
            elif u.path == "/api/footprints":
                raw, gz = cfg.footprints_bytes()
                if "gzip" in (self.headers.get("Accept-Encoding") or "").lower():
                    self._send(200, gz, "application/json", {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
                else:
                    self._send(200, raw, "application/json", {"Vary": "Accept-Encoding"})
            elif u.path == "/api/labels":
                rel = parse_qs(u.query).get("photo", [""])[0]
                self._json(200, api_get_labels(cfg, rel))
            elif u.path.startswith("/photo/"):
                p = safe_photo(cfg, unquote(u.path[len("/photo/"):]))
                if p is None:
                    self._json(404, {"ok": False, "error": "no such photo"})
                else:
                    self._send(200, p.read_bytes(), IMG_EXT[p.suffix.lower()])
            else:
                self._json(404, {"ok": False, "error": f"no such endpoint: {u.path}"})
        except UnknownRegion as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            tile = u.path.startswith("/tiles3d/")  # a tile traceback could carry the keyed URL
            if not tile:
                traceback.print_exc()
            self._json(500, {"ok": False, "error": type(exc).__name__ if tile else f"{type(exc).__name__}: {exc}"})

    def do_POST(self):  # noqa: N802
        route = {"/api/labels": api_save_labels, "/api/solve": api_solve,
                                           "/api/project": api_project,
                                           "/api/depth/compute": api_depth_compute,
                                           "/api/depth/fit": api_depth_fit,
                                           "/api/depth/orient": api_depth_orient,
                                           "/api/register/candidates": api_register_candidates,
                                           "/api/photo/flag": api_photo_flag,
                                           "/api/photos/screen": api_photos_screen,
                                           "/api/register/step": api_register_step}.get(
            urlparse(self.path).path.rstrip("/"))
        if route is None:
            self._json(404, {"ok": False, "error": f"no such endpoint: {self.path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError(f"request body too large ({length} bytes)")
            body = json.loads(self.rfile.read(length) or b"{}")
            self._json(200, route(self.regions.get(body.get("region")), body))
        except UnknownRegion as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):  # tile requests (/tiles3d/) are not logged
            super().log_message(fmt, *args)


def make_server(cfg: Config | Regions, host: str = "127.0.0.1", port: int = 8767) -> ThreadingHTTPServer:
    regions = cfg if isinstance(cfg, Regions) else Regions.single(cfg)
    handler = type("BoundLabelHandler", (LabelHandler,), {"regions": regions})
    return ThreadingHTTPServer((host, port), handler)


class _V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def loopback_v6(srv: ThreadingHTTPServer) -> ThreadingHTTPServer | None:
    """A second listener on [::1] with the same handler, for a server bound to 127.0.0.1.

    Browsers resolve `localhost` to ::1 first; with nothing there, every new connection waited
    ~300 ms for the IPv4 fallback (and the browser pane opens one per 3D tile). None when the
    server is not on 127.0.0.1 or the host has no IPv6 loopback.
    """
    host, port = srv.server_address[:2]
    if host != "127.0.0.1":
        return None
    try:
        return _V6Server(("::1", port), srv.RequestHandlerClass)
    except OSError:
        return None


def build_regions(region: str, osm: str | None = None, dem: str | None = None,
                  photos: str | None = None) -> Regions:
    """The regions.json table; --osm/--dem/--photos replace those fields of the `region` entry."""
    specs = load_regions()
    if osm is not None or dem is not None or photos is not None:
        spec = dict(specs.get(region) or {"label": region, "osm": str(DEFAULT_OSM), "dem": str(DEFAULT_DEM),
                                          "photos": {"": str(DEFAULT_PHOTOS)}})
        if osm is not None:
            spec["osm"] = str(Path(osm).resolve())
        if dem is not None:
            spec["dem"] = str(Path(dem).resolve()) if dem else ""
        if photos is not None:
            spec["photos"] = {"": str(Path(photos).resolve())}
        specs[region] = spec
    return Regions(specs, region)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--region", default="granada", help="the region the page opens with")
    ap.add_argument("--osm", default=None,
                    help="override the region's Overpass JSON or pipeline buildings.geojson")
    ap.add_argument("--dem", default=None,
                    help="override the region's terrain GeoTIFF (EPSG:4326); '' = column solve only")
    ap.add_argument("--photos", default=None, help="override the region's photo folders with this one")
    args = ap.parse_args()
    regions = build_regions(args.region, args.osm, args.dem, args.photos)
    srv = make_server(regions, args.host, args.port)
    v6 = loopback_v6(srv)
    if v6 is not None:
        threading.Thread(target=v6.serve_forever, daemon=True).start()
    print(f"photo-label server on http://{args.host}:{args.port}/ (default region {regions.default})", flush=True)
    print(f"  regions: {', '.join(regions.ids())}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
