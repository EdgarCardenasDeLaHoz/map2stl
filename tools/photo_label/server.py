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
import socket
import sys
import threading
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"  # the page's modules and stylesheet, served by LabelHandler._static
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import model_glb  # noqa: E402,F401
import screen as photo_screen  # noqa: E402,F401
import terrain_solve as ts  # noqa: E402,F401
import tiles  # noqa: E402  (the handler calls tiles.urlopen, which tests replace)
from depth import (  # noqa: E402,F401
    DEPTH_MAX_W,
    _block_mean,
    _compute_depth,
    _orient_ground,
    _orient_source,
    api_depth,
    api_depth_compute,
    api_depth_fit,
    api_depth_orient,
)
from footprints import (  # noqa: E402,F401
    WITHHELD_MIN_M,
    WITHHELD_RATIO,
    _centroid,
    _inside,
    _round_coords,
    _stitch_rings,
    apply_heights,
    geojson_footprints,
    load_heights,
    name_at,
    overpass_to_geojson,
    tag_sources,
)
from latlon import bearing_deg, dist_m  # noqa: E402,F401
from modelapi import (  # noqa: E402,F401
    _build_model,
    api_model_info,
)
from paths import (  # noqa: E402,F401
    _DERIVED,
    _EXT_RANK,
    _SEED_NONFRAME,
    ANNOTATIONS,
    DEFAULT_DEM,
    DEFAULT_OSM,
    DEFAULT_PHOTOS,
    HERE,
    IMG_EXT,
    MODEL_DIR,
    REGIONS_FILE,
    REPO_ROOT,
    TILES_CACHE_DIR,
    _abs,
    _guess_cache_path,
    _is_seed_nonframe,
    _is_seed_strip,
    _photo_files,
    _read_json,
    depth_path,
    flags_path,
    image_size,
    label_path,
    photo_rel,
    safe_photo,
)
from photos import (  # noqa: E402,F401
    _photo_rgb,
    _set_flag,
    api_get_labels,
    api_photo_flag,
    api_save_labels,
    list_photos,
    load_flags,
)
from regions import (  # noqa: E402,F401
    _AUTO,
    KEY_VAR,
    Config,
    Regions,
    UnknownRegion,
    build_regions,
    load_api_key,
    load_regions,
)
from screening import (  # noqa: E402,F401
    SCREEN_CPU_MAX,
    _pose_geometry,
    _screen_flag,
    _screen_region,
    api_photos_screen,
    api_photos_screen_state,
)
from seeds import (  # noqa: E402,F401
    _FROM,
    HEADING_AGREE_DEG,
    HEADING_BONUS,
    PANO_CAM_HEIGHT_M,
    SEED_MATCH_M,
    _norm_text,
    _panoview_pose,
    _parse_coord,
    _seed_camera,
    _seed_frame_pose,
    _seed_strip_pose,
    _tag_candidates,
    pano_seed,
    photo_focal_35mm,
    photo_hints,
)
from solve import (  # noqa: E402,F401
    MAX_TICKS,
    TICK_RADIUS_M,
    _camera,
    _dem_centre,
    _guess_key,
    _solve_pano,
    _solve_reply,
    _start_geoid,
    _terrain,
    api_project,
    api_register_candidates,
    api_register_step,
    api_solve,
    start_view,
)
from tiles import (  # noqa: E402,F401
    IDLE_PER_HOST,
    TILES_CACHE_CAP,
    TILES_CACHE_SWEEP_EVERY,
    TILES_HOST,
    TILES_PREFIX,
    _cache_paths,
    _max_age,
    _PooledResponse,
    _rewrite_uri,
    pooled_urlopen,
    rewrite_tile_uris,
    tile_cache_get,
    tile_cache_put,
    tile_cache_trim,
    tiles_upstream_url,
)

MAX_BODY = 32 * 1024 * 1024   # /api/register/step carries a render and a depth grid (base64)


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
        if "Cache-Control" not in (headers or {}):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _static(self, rel: str) -> None:
        """Serve one .js / .css file of the page (static/); anything that resolves outside it is a 404."""
        root = STATIC_DIR.resolve()
        try:
            path = (root / unquote(rel)).resolve()
        except (OSError, ValueError):
            path = root
        ctype = STATIC_TYPES.get(path.suffix)
        if not ctype or not path.is_relative_to(root) or not path.is_file():
            self._json(404, {"ok": False, "error": "no such file"})
            return
        self._send(200, path.read_bytes(), ctype, {"Cache-Control": "no-cache"})

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
            resp = tiles.urlopen(urllib.request.Request(url, headers={"User-Agent": "photo-label/1"}), timeout=30)
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
            elif u.path.startswith("/static/"):
                self._static(u.path[len("/static/"):])
            elif u.path == "/api/model/info":
                self._json(200, api_model_info(cfg, parse_qs(u.query).get("rebuild") == ["1"]))
            elif u.path == "/api/model.glb":
                glb = cfg.glb_path()
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
        route = {
            "/api/labels": api_save_labels,
            "/api/solve": api_solve,
            "/api/project": api_project,
            "/api/depth/compute": api_depth_compute,
            "/api/depth/fit": api_depth_fit,
            "/api/depth/orient": api_depth_orient,
            "/api/register/candidates": api_register_candidates,
            "/api/photo/flag": api_photo_flag,
            "/api/photos/screen": api_photos_screen,
            "/api/register/step": api_register_step,
        }.get(urlparse(self.path).path.rstrip("/"))
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
