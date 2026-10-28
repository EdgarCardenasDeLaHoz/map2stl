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
    POST /api/solve       camera fit on the pairs: terrain fit (terrain_solve.solve_terrain, DEM
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
import os
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

from city2stl.skyline.camera_solver import M_PER_DEG_LAT, Obs, solve_pose  # noqa: E402
from geo2stl.cache import atomic_write, atomic_write_json  # noqa: E402

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import model_glb  # noqa: E402
import screen as photo_screen  # noqa: E402
import terrain_solve as ts  # noqa: E402

IMG_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_BODY = 32 * 1024 * 1024   # /api/register/step carries a render and a depth grid (base64)
DEFAULT_OSM = REPO_ROOT / "cache" / "5d947d851a9f2b205205e503611a5df310e7e87e.json"
DEFAULT_DEM = REPO_ROOT / "cache" / "opentopo" / "d6eb6dd762c161a4afb3471521b53e14.tif"
DEFAULT_PHOTOS = REPO_ROOT / "runs" / "photo"
ANNOTATIONS = REPO_ROOT / "city2stl" / "skyline" / "sites" / "annotations"
REGIONS_FILE = HERE / "regions.json"
TICK_RADIUS_M = 3000.0
MAX_TICKS = 300
TILES_HOST = "https://tile.googleapis.com"
TILES_PREFIX = "v1/3dtiles/"
KEY_VAR = "GOOGLE_MAPS_API_KEY"
MODEL_DIR = REPO_ROOT / "runs" / "photo_label_model"
TILES_CACHE_DIR = REPO_ROOT / "runs" / "photo_label_tiles"
TILES_CACHE_CAP = 2 * 1024 ** 3
TILES_CACHE_SWEEP_EVERY = 100   # writes between size checks
_AUTO = object()
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


def load_api_key(env_file: Path | None = None) -> str:
    """The Maps key from the environment, else from `.env` (KEY=value lines). "" when absent."""
    key = os.environ.get(KEY_VAR, "").strip()
    if key:
        return key
    try:
        for line in (env_file or REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            name, sep, val = line.partition("=")
            if sep and name.strip().removeprefix("export ").strip() == KEY_VAR:
                return val.strip().strip("\"'")
    except OSError:
        pass
    return ""


def _round_coords(fc: dict, nd: int = 6) -> dict:
    """A copy of a GeoJSON FeatureCollection with every coordinate rounded to `nd` decimals."""
    def rnd(c):
        return [round(x, nd) for x in c] if c and isinstance(c[0], (int, float)) else [rnd(x) for x in c]
    feats = []
    for ft in fc.get("features", []):
        g = ft.get("geometry")
        if isinstance(g, dict) and "coordinates" in g:
            g = {**g, "coordinates": rnd(g["coordinates"])}
        feats.append({**ft, "geometry": g})
    return {**fc, "features": feats}


class Config:
    """Paths and cached state of one region; tests build their own."""

    def __init__(self, photo_dir: Path, osm_path: Path, annotations_dir: Path, region: str = "granada",
                 api_key=_AUTO, dem_path=DEFAULT_DEM, model_dir=MODEL_DIR, photo_roots=None, view=None,
                 tiles_cache_dir=TILES_CACHE_DIR, landmarks=None, geoid_m=None):
        self.tiles_cache_dir = Path(tiles_cache_dir)
        self.photo_dir = Path(photo_dir)
        # key -> folder; an empty key adds no prefix to photo paths (and so to label file names)
        self.photo_roots = {k: Path(v) for k, v in (photo_roots or {"": photo_dir}).items()}
        self.osm_path = Path(osm_path)
        self.annotations_dir = Path(annotations_dir)
        self.region = region
        self.landmarks = list(landmarks or [])  # [{name, aliases, lat, lon, elev_m?}] from regions.json
        self.geoid_m = None if geoid_m is None else float(geoid_m)  # measured undulation; None = estimate per render
        self.view = view  # {lat, lon, ground_m} start of the maps, from regions.json; None = DEM centre
        self.dem_path = Path(dem_path) if dem_path else None
        self._dem = None
        self._fc: dict | None = None
        self._pts: list | None = None
        self._lock = threading.Lock()
        self._key = load_api_key() if api_key is _AUTO else (api_key or "")
        self.session = ""  # last Google tile session token seen in a tileset JSON
        self.model_dir = Path(model_dir)
        self._model: dict = {"status": "idle"}   # see api_model_info
        self._model_lock = threading.Lock()

    def footprints_bytes(self) -> tuple[bytes, bytes]:
        """(raw JSON, gzip of it) of footprints(), coordinates rounded to 6 decimals; built once."""
        with self._lock:
            cached = getattr(self, "_fc_bytes", None)
        if cached is None:
            import gzip
            raw = json.dumps(_round_coords(self.footprints()), separators=(",", ":")).encode("utf-8")
            cached = (raw, gzip.compress(raw, 6, mtime=0))
            with self._lock:
                self._fc_bytes = cached
        return cached

    @property
    def api_key(self) -> str:
        return self._key

    def dem(self):
        """`photo_view.Dem` of `dem_path`, loaded once; None when there is no DEM file."""
        with self._lock:
            if self._dem is None and self.dem_path and self.dem_path.is_file():
                from city2stl.skyline import photo_view
                self._dem = photo_view.Dem(self.dem_path)
            return self._dem

    def footprints(self) -> dict:
        with self._lock:
            if self._fc is None:
                data = json.loads(self.osm_path.read_text(encoding="utf-8"))
                self._fc = (geojson_footprints(data) if data.get("type") == "FeatureCollection"
                            else overpass_to_geojson(data))
            return self._fc

    def centroids(self) -> list[tuple[float, float, str]]:
        """(lat, lon, name) of named footprints."""
        fc = self.footprints()
        with self._lock:
            if self._pts is None:
                pts = []
                for f in fc["features"]:
                    name = f["properties"].get("name")
                    if name:
                        la, lo = _centroid(f["geometry"]["coordinates"][0])
                        pts.append((la, lo, name))
                self._pts = pts
            return self._pts


def start_view(cfg: Config) -> dict | None:
    """Start of the maps: the region's `view` from regions.json, else the DEM's centre and its
    orthometric ground height, else None (the page then keeps wherever the map already is)."""
    import numpy as np

    dem = cfg.dem()
    if cfg.view:
        return dict(cfg.view)
    if dem is None:
        return None
    h, w = dem.z.shape
    lon, lat = dem.t * (w / 2, h / 2)
    z = float(np.nanmedian(dem.z))
    return {"lat": float(lat), "lon": float(lon), "ground_m": z if np.isfinite(z) else 0.0}


def overpass_to_geojson(data: dict) -> dict:
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in data.get("elements", []) if e.get("type") == "node"}
    feats = []
    for e in data.get("elements", []):
        tags = e.get("tags") or {}
        if e.get("type") != "way" or "building" not in tags:
            continue
        ring = [nodes[n] for n in e.get("nodes", []) if n in nodes]
        if len(ring) < 3:
            continue
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        feats.append({"type": "Feature",
                      "properties": {"id": e["id"], "name": tags.get("name"),
                                     "building": tags["building"], "height": tags.get("height"),
                                     "levels": tags.get("building:levels")},
                      "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}})
    # Building multipolygons (Palacio de Carlos V, many Albaicin blocks): one footprint per closed
    # outer ring, stitched from the member ways. Inner rings (courtyards) are left out.
    ways = {e["id"]: e.get("nodes", []) for e in data.get("elements", []) if e.get("type") == "way"}
    for e in data.get("elements", []):
        tags = e.get("tags") or {}
        if e.get("type") != "relation" or "building" not in tags:
            continue
        parts = [list(ways[m["ref"]]) for m in e.get("members", [])
                 if m.get("type") == "way" and m.get("role", "outer") in ("outer", "") and m.get("ref") in ways]
        for k, ids in enumerate(_stitch_rings(parts)):
            ring = [nodes[n] for n in ids if n in nodes]
            if len(ring) < 4:
                continue
            feats.append({"type": "Feature",
                          "properties": {"id": f"r{e['id']}" + (f"_{k}" if k else ""), "name": tags.get("name"),
                                         "building": tags["building"], "height": tags.get("height"),
                                         "levels": tags.get("building:levels")},
                          "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}})
    return {"type": "FeatureCollection", "features": feats}


def _stitch_rings(parts: list[list[int]]) -> list[list[int]]:
    """Join way node lists end to end into closed rings; an open chain left over is dropped."""
    rings, parts = [], [p for p in parts if len(p) >= 2]
    while parts:
        cur = parts.pop(0)
        while cur[0] != cur[-1]:
            for i, p in enumerate(parts):
                if p[0] == cur[-1]:
                    cur += p[1:]
                elif p[-1] == cur[-1]:
                    cur += p[-2::-1]
                elif p[-1] == cur[0]:
                    cur = p[:-1] + cur
                elif p[0] == cur[0]:
                    cur = p[:0:-1] + cur
                else:
                    continue
                parts.pop(i)
                break
            else:
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
    return rings


def geojson_footprints(data: dict) -> dict:
    """Footprints from a pipeline `buildings.geojson` (runs/<city>/01_osm), in the Overpass shape.

    Only measured heights carry over: a `height_source` of "default" is the pipeline's filler and
    would hide the levels/default fallback in `terrain_solve.building_height`.
    """
    feats = []
    for i, f in enumerate(data.get("features", [])):
        g = f.get("geometry") or {}
        rings = ([g["coordinates"][0]] if g.get("type") == "Polygon" else
                 [p[0] for p in g["coordinates"]] if g.get("type") == "MultiPolygon" else [])
        pr = f.get("properties") or {}
        h = pr.get("height_m") if pr.get("height_source") not in (None, "default") else None
        for ring in rings:
            if len(ring) >= 4:
                feats.append({"type": "Feature",
                              "properties": {"id": pr.get("id", i), "name": pr.get("name"),
                                             "building": pr.get("building", "yes"), "height": h,
                                             "levels": pr.get("levels")},
                              "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": feats}


def _centroid(ring) -> tuple[float, float]:
    """(lat, lon) mean of the ring's vertices (good enough for a label tick)."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))


def _inside(lon: float, lat: float, ring) -> bool:
    c = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def name_at(fc: dict, lat: float, lon: float) -> str | None:
    for f in fc["features"]:
        name = f["properties"].get("name")
        if not name:
            continue
        ring = f["geometry"]["coordinates"][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        if min(xs) <= lon <= max(xs) and min(ys) <= lat <= max(ys) and _inside(lon, lat, ring):
            return name
    return None


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


def _photo_files(cfg: Config) -> list[tuple[str, Path, Path]]:
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
    for key, root, p in _photo_files(cfg):
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


def api_photo_flag(cfg: Config, body: dict) -> dict:
    """Mark (`bad` true) or unmark a photo as unusable; persists to `<region>_photo_flags.json`
    as {photo rel: {reason, flagged: ISO date}}. Returns {ok, bad, count_flagged}."""
    rel = body.get("photo")
    if not isinstance(rel, str) or safe_photo(cfg, rel) is None:
        return {"ok": False, "error": f"no such photo: {rel}"}
    bad = bool(body.get("bad", True))
    return {"ok": True, "bad": bad,
            "count_flagged": _set_flag(cfg, rel, bad, str(body.get("reason") or ""))}


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
        import auto_pose
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
        import auto_pose
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
    photo = safe_photo(cfg, rel)
    rgb = _photo_rgb(cfg, rel)
    dem = cfg.dem()
    if dem is None:
        return {"ok": False, "error": "no DEM loaded; cannot search for a start pose"}
    top_k = int(body.get("top_k") or 3)
    only = bool(body.get("only_viewpoints"))
    if photo is not None and not body.get("refresh") and not body.get("viewpoints"):
        ps = pano_seed(photo, cfg)           # known pose: no skyline search needed
        if ps is not None:
            cam = reg.cam_from_dict(ps)
            extra = {k: ps[k] for k in ("elev_fitted",) if k in ps}
            return {"ok": True, "candidates": [{**reg.cam_to_dict(cam), "score": 0.0, **extra,
                                                "source": ps.get("source", "pano"),
                                                "label": ps.get("label", "Street View pano (known pose)")}],
                    "geoid_m": _start_geoid(cfg), "cached": False,
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
    return ts.solve_terrain(dem, rows, w, h, seed, footprints=fc)


def _camera(c: dict, w: int, h: int):
    from city2stl.skyline import photo_view
    return photo_view.PinholeCamera(float(c["lat"]), float(c["lon"]), float(c["elev_m"]),
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
    r = ts.project_buildings(dem, cfg.footprints(), cam)
    ids = r["ids"].astype(np.int32)
    bgr = np.dstack([np.zeros_like(ids), ids >> 8, ids & 255]).astype(np.uint8)
    ok, png = cv2.imencode(".png", bgr)
    return {"ok": True, "buildings": r["buildings"], "width": int(w), "height": int(h),
            "ids_png": base64.b64encode(png.tobytes()).decode("ascii") if ok else None}


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


class UnknownRegion(ValueError):
    """The request names a region the server does not have (answered with a 400)."""


def _abs(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else REPO_ROOT / p


def load_regions(path: Path = REGIONS_FILE) -> dict[str, dict]:
    """The checked-in region table, else Granada's built-in defaults."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            return data
    except (OSError, ValueError) as exc:
        print(f"warning: cannot read {path} ({type(exc).__name__}); using the Granada defaults", flush=True)
    return {"granada": {"label": "Granada", "osm": str(DEFAULT_OSM), "dem": str(DEFAULT_DEM),
                        "photos": {"": str(DEFAULT_PHOTOS)}}}


class Regions:
    """The server's regions: a Config per region, built on first use and kept.

    The Google key and the tile session are shared: every Config gets the same key, and the tile
    proxy always works through `tiles`, the default region's Config, whatever region the page shows.
    """

    def __init__(self, specs: dict[str, dict], default: str, annotations_dir: Path = ANNOTATIONS,
                 api_key=_AUTO, model_dir=MODEL_DIR, tiles_cache_dir=TILES_CACHE_DIR):
        self.tiles_cache_dir = Path(tiles_cache_dir)
        self._key = load_api_key() if api_key is _AUTO else (api_key or "")
        self._annotations = Path(annotations_dir)
        self._model_dir = Path(model_dir)
        self._lock = threading.Lock()
        self._cfgs: dict[str, Config] = {}
        self._specs: dict[str, dict] = {}
        for rid, spec in specs.items():
            ok = self._validate(rid, spec)
            if ok is not None:
                self._specs[rid] = ok
        if not self._specs:
            raise ValueError("no usable region (every region in regions.json has a missing path)")
        self.default = default if default in self._specs else next(iter(self._specs))
        if default != self.default:
            print(f"warning: default region {default!r} is not available; using {self.default!r}", flush=True)

    @classmethod
    def single(cls, cfg: Config) -> Regions:
        """A registry holding one ready-made Config (tests, embedding)."""
        self = cls.__new__(cls)
        self._key, self._annotations, self._model_dir = cfg.api_key, cfg.annotations_dir, cfg.model_dir
        self.tiles_cache_dir = cfg.tiles_cache_dir
        self._lock = threading.Lock()
        self._cfgs = {cfg.region: cfg}
        self._specs = {cfg.region: {"label": cfg.region}}
        self.default = cfg.region
        return self

    @staticmethod
    def _validate(rid: str, spec: dict) -> dict | None:
        """The spec with absolute paths, or None (warned) when its OSM or DEM file is missing.
        A missing photo folder is dropped from the region, not the region."""
        def warn(msg):
            print(f"warning: region {rid!r}: {msg}", flush=True)
        osm = _abs(spec["osm"]) if spec.get("osm") else None
        if osm is None or not osm.is_file():
            warn(f"skipped, OSM file missing ({spec.get('osm')})")
            return None
        dem = _abs(spec["dem"]) if spec.get("dem") else None
        if dem is not None and not dem.is_file():
            warn(f"skipped, DEM missing ({spec['dem']})")
            return None
        roots = {}
        for key, folder in (spec.get("photos") or {}).items():
            if _abs(folder).is_dir():
                roots[key] = _abs(folder)
            else:
                warn(f"photo folder {key!r} missing ({folder}), left out")
        return {"label": spec.get("label") or rid, "osm": osm, "dem": dem, "photos": roots, "view": spec.get("view"),
                "landmarks": spec.get("landmarks") or [], "geoid_m": spec.get("geoid_m")}

    def ids(self) -> list[str]:
        return list(self._specs)

    def get(self, rid: str | None) -> Config:
        """The region's Config (the default when `rid` is empty); UnknownRegion for any other id."""
        rid = rid or self.default
        with self._lock:
            cfg = self._cfgs.get(rid)
            if cfg is None:
                spec = self._specs.get(rid)
                if spec is None:
                    raise UnknownRegion(f"unknown region: {rid}")
                roots = spec["photos"]
                cfg = Config(next(iter(roots.values()), DEFAULT_PHOTOS), spec["osm"], self._annotations, rid,
                             api_key=self._key, dem_path=spec["dem"], model_dir=self._model_dir,
                             photo_roots=roots, view=spec["view"], landmarks=spec.get("landmarks"),
                             geoid_m=spec.get("geoid_m"), tiles_cache_dir=self.tiles_cache_dir)
                self._cfgs[rid] = cfg
            return cfg

    @property
    def tiles(self) -> Config:
        return self.get(self.default)

    def listing(self) -> dict:
        return {"default": self.default,
                "regions": [{"id": rid, "label": spec["label"], "photos": len(_photo_files(self.get(rid)))}
                            for rid, spec in self._specs.items()]}


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
