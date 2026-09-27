"""Local backend for the manual drag-align tool (drag_align.html).

Serves the align tool directory as static files and adds four JSON endpoints
that a plain file:// page cannot provide:

    POST /api/save     write the current alignment to ground_truth/<slug>.json
    POST /api/load     read that file back when the city is selected again
    POST /api/refine   run an ECC registration refine seeded with the user's transform
    POST /api/refetch  re-fetch OSM buildings / water / satellite for a corrected bbox

The third one is the important one. The pipeline derives each city's OSM window
from `tallest_m / (z_max - z_min)` -- a Z *range* that includes terrain relief and
the base plate, not the tallest building -- and centres it on the centroid of the
geocoded administrative bounds. For hilly or oddly-bounded cities the resulting
window is the wrong size and/or the wrong place, so the true match can lie outside
the fetched raster entirely and no amount of dragging will find it. /api/refetch
lets the tool ask for the window the user actually wants.

Run with the working venv:
    ~/.venvs/map2stl/Scripts/python.exe tools/align_tool/server.py [--port 8766]
Then open http://localhost:8766/drag_align.html
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import paths  # noqa: E402

from geo2stl.geo import M_PER_DEG_LAT, M_PER_DEG_LON_EQ, bbox_size_m  # noqa: E402

HERE = paths.HERE

DATA = HERE / "data"
GROUND_TRUTH = paths.GROUND_TRUTH
MAX_BODY = 8 * 1024 * 1024


# --------------------------------------------------------------------------
# raster helpers
# --------------------------------------------------------------------------

def _png_data_uri(img: np.ndarray) -> str:
    import cv2
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG encode failed")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _norm_u8(hm: np.ndarray) -> np.ndarray:
    a = np.nan_to_num(np.asarray(hm, dtype=np.float64), nan=0.0)
    a = np.clip(a, 0, None)
    hi = np.percentile(a[a > 0], 99) if np.any(a > 0) else 1.0
    if hi <= 0:
        hi = 1.0
    return np.clip(a / hi * 255.0, 0, 255).astype(np.uint8)


def _load_gray(slug: str, name: str) -> np.ndarray:
    """Load a per-city raster as float64, NaNs INTACT.

    Prefers the raw .npy heightmap (real metres) the exporter writes; falls
    back to the display PNG, which is percentile-normalized to 0..255.

    The NaNs matter: building_mask(source='osm') is literally
    ~np.isnan(heightmap), so NaN is the "no building here" sentinel. Filling
    it with 0.0 makes every OSM cell a building, the mask becomes a solid
    frame, and the edge signal disappears entirely -- which silently reduces
    every registration score to zero. Never fill before segmentation; the
    signal api_refine hands to OpenCV is the binary building_edges output,
    which carries no NaN, so no filling is needed downstream either.
    """
    npy = DATA / slug / f"{name}.npy"
    if npy.exists():
        return np.load(npy).astype(np.float64)
    import cv2
    png = DATA / slug / f"{name}.png"
    if not png.exists():
        raise FileNotFoundError(f"missing raster: {png}")
    img = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError(f"could not decode {png}")
    return img.astype(np.float64)


def _meta(slug: str) -> dict:
    p = DATA / slug / "meta.json"
    if not p.exists():
        raise FileNotFoundError(f"unknown city slug: {slug}")
    return json.loads(p.read_text())


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def _apply(M, x, y):
    return (M[0][0] * x + M[0][1] * y + M[0][2],
            M[1][0] * x + M[1][1] * y + M[1][2])


def _stl_footprint_bbox(matrix, bbox_nsew, stl_shape, osm_shape):
    """Geographic bbox covered by the STL frame under the user's alignment.

    `matrix` is the tool's exported 2x3 affine mapping STL pixels -> OSM pixels
    (the pipeline convention). The four STL frame corners are pushed through it,
    the resulting OSM pixel coordinates are converted to lat/lon with the OSM
    raster's own bbox, and the axis-aligned hull is returned.
    """
    N, S, E, W = (float(v) for v in bbox_nsew)
    oh, ow = int(osm_shape[0]), int(osm_shape[1])
    sh, sw = int(stl_shape[0]), int(stl_shape[1])

    lats, lons = [], []
    for cx, cy in ((0, 0), (sw, 0), (sw, sh), (0, sh)):
        px, py = _apply(matrix, cx, cy)
        lons.append(W + (px / ow) * (E - W))
        lats.append(N - (py / oh) * (N - S))
    return max(lats), min(lats), max(lons), min(lons)


def _square_bbox(north, south, east, west, margin, center=None):
    """Expand a bbox to a square in metres, scaled by `margin`, about its centre.

    `center` optionally overrides that centre with an explicit (lat, lon). The
    side length is still taken from the input bbox, so the window keeps the size
    the caller asked for and only moves. That is what makes it possible to jump
    to a completely different part of the city, which a footprint-derived centre
    can never do.
    """
    if center is not None:
        lat_c, lon_c = float(center[0]), float(center[1])
    else:
        lat_c = (north + south) / 2.0
        lon_c = (east + west) / 2.0
    coslat = max(math.cos(math.radians(lat_c)), 1e-6)
    h_m = abs(north - south) * M_PER_DEG_LAT
    w_m = abs(east - west) * M_PER_DEG_LON_EQ * coslat
    side_m = max(h_m, w_m) * float(margin)
    d_lat = (side_m / 2.0) / M_PER_DEG_LAT
    d_lon = (side_m / 2.0) / (M_PER_DEG_LON_EQ * coslat)
    return (lat_c + d_lat, lat_c - d_lat, lon_c + d_lon, lon_c - d_lon), side_m


def _cell_size_m(north, south, east, west, resolution):
    dx, dy = bbox_size_m({"north": north, "south": south, "east": east, "west": west})
    return ((dx + dy) / 2.0) / float(resolution)


# --------------------------------------------------------------------------
# endpoint implementations
# --------------------------------------------------------------------------

def api_save(body: dict) -> dict:
    slug = str(body["slug"])
    payload = body.get("payload") or body
    GROUND_TRUTH.mkdir(parents=True, exist_ok=True)
    out = GROUND_TRUTH / f"{slug}.json"
    out.write_text(json.dumps(payload, indent=2))
    return {"ok": True, "path": str(out), "bytes": out.stat().st_size}


def api_load(body: dict) -> dict:
    """Read back a previously saved alignment, if one exists.

    Saving alone was not enough: the page rebuilt its state from the pipeline
    guess every time a city was selected, so hand-made alignments looked like
    they had never been written. This is the missing half of that round trip.
    A city that has never been saved is not an error -- it reports found=False
    and the caller falls back to the guess.
    """
    slug = str(body["slug"])
    path = GROUND_TRUTH / f"{slug}.json"
    if not path.is_file():
        return {"ok": True, "found": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {"ok": True, "found": False, "path": str(path), "error": f"unreadable: {exc}"}
    return {"ok": True, "found": True, "path": str(path), "payload": payload}


# Coarse-to-fine blur ladder. The blur width sets the capture range: a wide
# blur pulls a seed that is tens of pixels off, a narrow one sharpens a seed
# that is already close but diverges from far away. Each rung is seeded with
# the best transform found so far.
_BLUR_LADDER = ((31, 10.0), (15, 5.0), (5, 1.5))

# Guardrails. Refine is "snap what the user already lined up", so a result that
# wanders further than this is a divergence wearing a good correlation score,
# not a fine-tune.
_MAX_DRIFT_PX = 60.0
_MAX_SCALE_RATIO = 1.20
_MAX_ROT_DEG = 20.0

# A refine that moves the user's alignment must earn it. Segmentation and the
# tolerant IoU both jitter at the third decimal, so a 0.0655 -> 0.0658 "win" is
# noise; keeping it would silently nudge a hand-placed alignment for nothing.
_MIN_IOU_GAIN_ABS = 0.002
_MIN_IOU_GAIN_REL = 0.02


def _invert2x3(M) -> np.ndarray:
    return np.linalg.inv(np.vstack([np.asarray(M, dtype=np.float64), [0, 0, 1.0]]))[:2, :3]


def _ecc_once(t_img, s_img, seed_fwd, iters: int = 500):
    """One findTransformECC call in the pipeline's source->target convention.

    OpenCV's warpMatrix maps templateImage pixels to inputImage pixels. We pass
    templateImage=OSM target and inputImage=STL source, so the matrix ECC wants
    is target->source -- the inverse of the caller's matrix. Invert going in and
    coming back out. Getting this backwards is what made every previous refine
    diverge instantly.
    """
    import cv2
    warp = _invert2x3(seed_fwd).astype(np.float32).copy()
    cc, out = cv2.findTransformECC(
        templateImage=t_img, inputImage=s_img, warpMatrix=warp,
        # MOTION_EUCLIDEAN cannot express scale, and these seeds routinely carry
        # a scale far from 1.0, so it always diverges. Affine or nothing.
        motionType=cv2.MOTION_AFFINE,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, 1e-7),
        inputMask=None, gaussFiltSize=5,
    )
    return float(cc), _invert2x3(np.asarray(out, dtype=np.float64))


def api_refine(body: dict) -> dict:
    """Coarse-to-fine ECC refine seeded with the user's manual alignment.

    The input matrix is STL px -> OSM px (the pipeline convention). The refine
    runs the blur ladder, each rung seeded from the best result so far, and only
    keeps a rung whose tolerant edge-IoU actually beats the seed and which stays
    inside the drift guardrails. If nothing beats the seed the seed is returned
    unchanged and `improved` is false -- an honest "I could not do better" is
    more useful than a confident no-op.
    """
    import cv2
    from numpy2stl.raster import building_edges
    from numpy2stl.registration.align.metrics import _tolerant_iou
    from numpy2stl.registration.align.transform import apply_transform

    slug = str(body["slug"])
    init = np.asarray(body["matrix"], dtype=np.float64)
    if init.shape != (2, 3):
        raise ValueError(f"matrix must be 2x3, got {init.shape}")

    source = _load_gray(slug, "stl_heightmap")
    target = _load_gray(slug, "osm_buildings")

    # allow_forced_split=False everywhere: letting segmentation change the mask
    # structure mid-search makes the score non-comparable between rungs.
    t_edge = building_edges(target, source="osm", allow_forced_split=False)
    s_edge = building_edges(source, source="stl", allow_forced_split=False)

    def score(M) -> float:
        aligned = apply_transform(source, M, output_shape=target.shape)
        a_edge = building_edges(aligned, source="stl", allow_forced_split=False)
        return float(_tolerant_iou(a_edge, t_edge, tol_px=2))

    init_scale = math.hypot(init[0, 0], init[1, 0])
    init_rot = math.degrees(math.atan2(init[1, 0], init[0, 0]))

    def within_guardrails(M) -> str | None:
        drift = math.hypot(M[0, 2] - init[0, 2], M[1, 2] - init[1, 2])
        if drift > _MAX_DRIFT_PX:
            return f"drifted {drift:.0f}px"
        sc = math.hypot(M[0, 0], M[1, 0])
        if init_scale > 0 and not (1.0 / _MAX_SCALE_RATIO <= sc / init_scale <= _MAX_SCALE_RATIO):
            return f"scale {sc:.3f} vs seed {init_scale:.3f}"
        d_rot = abs((math.degrees(math.atan2(M[1, 0], M[0, 0])) - init_rot + 180.0) % 360.0 - 180.0)
        if d_rot > _MAX_ROT_DEG:
            return f"rotated {d_rot:.0f}deg"
        return None

    seed_iou = score(init)
    best_M, best_iou, best_cc = init, seed_iou, float("nan")
    seed_for_next = init
    trace = []

    for k, sigma in _BLUR_LADDER:
        t_img = cv2.GaussianBlur(t_edge.astype(np.float32), (k, k), sigma)
        s_img = cv2.GaussianBlur(s_edge.astype(np.float32), (k, k), sigma)
        try:
            cc, M = _ecc_once(t_img, s_img, seed_for_next)
        except cv2.error as exc:
            msg = str(exc).split("error: ")[-1].split(" in function")[0]
            trace.append(f"blur{k}: failed ({msg[:60]})")
            continue
        bad = within_guardrails(M)
        if bad is not None:
            trace.append(f"blur{k}: rejected, {bad}")
            continue
        iou = score(M)
        if iou > best_iou:
            trace.append(f"blur{k}: IoU {best_iou:.4f} -> {iou:.4f} (kept)")
            best_M, best_iou, best_cc = M, iou, cc
            seed_for_next = M
        else:
            trace.append(f"blur{k}: IoU {iou:.4f} <= {best_iou:.4f} (dropped)")

    # Intermediate rungs may be kept on any gain (they only seed the next rung),
    # but the answer handed back to the user has to clear the noise floor.
    min_gain = max(_MIN_IOU_GAIN_ABS, _MIN_IOU_GAIN_REL * seed_iou)
    improved = best_iou > seed_iou + min_gain
    if not improved and best_iou > seed_iou:
        trace.append(f"best gain {best_iou - seed_iou:.4f} < {min_gain:.4f} floor, keeping seed")
    if not improved:
        best_M, best_iou, best_cc = init, seed_iou, float("nan")

    M = np.asarray(best_M, dtype=np.float64)
    rot = float(math.degrees(math.atan2(M[1, 0], M[0, 0])))
    return {
        "ok": True,
        "matrix": M.tolist(),
        "scale": float(math.hypot(M[0, 0], M[1, 0])),
        "rot_deg": rot,
        "tx": float(M[0, 2]),
        "ty": float(M[1, 2]),
        "confidence": float(best_cc),
        "converged": bool(improved),
        "improved": bool(improved),
        "iou_before": float(seed_iou),
        "iou_after": float(best_iou),
        "signal": "building_edges",
        "motion": "affine",
        "delta_rot_deg": rot - init_rot,
        "delta_translation_px": float(math.hypot(M[0, 2] - init[0, 2], M[1, 2] - init[1, 2])),
        "trace": trace,
    }


def api_refetch(body: dict) -> dict:
    """Re-fetch OSM + satellite for the window the user's alignment implies.

    Fixes the wrong-region failure: the pipeline sizes the OSM window from the
    mesh Z range and centres it on the administrative centroid, so for most
    cities the correct match is not inside the fetched raster at all.

    After the refetch the new raster is a square `margin` times the STL
    footprint, centred on it, so the correct alignment is "OSM centred on the
    STL frame at scale = fit_scale" -- which is what the client resets to.
    """
    from city2stl.osm_raster import (
        get_osm_building_heightmap,
        get_osm_semantic_masks,
    )

    slug = str(body["slug"])
    meta = _meta(slug)
    matrix = body["matrix"]
    bbox_in = body.get("bbox_nsew") or meta["osm_bbox_nsew"]
    stl_shape = body.get("stl_shape") or meta["stl_shape"]
    osm_shape = body.get("osm_shape") or meta["osm_shape"]
    margin = float(body.get("margin", 1.5))
    resolution = int(body.get("resolution", meta.get("resolution", 512)))

    center = body.get("center")
    if center is not None:
        center = (float(center[0]), float(center[1]))

    hull = _stl_footprint_bbox(matrix, bbox_in, stl_shape, osm_shape)
    (N, S, E, W), side_m = _square_bbox(*hull, margin=margin, center=center)

    osm = get_osm_building_heightmap((N, S, E, W), resolution=resolution)
    osm_hm = np.nan_to_num(np.asarray(osm["heightmap"], dtype=np.float64), nan=0.0)
    osm_u8 = _norm_u8(osm_hm)

    try:
        sem = get_osm_semantic_masks((N, S, E, W), resolution=resolution)
        water = (np.asarray(sem["water"], dtype=bool) * 255).astype(np.uint8)
    except Exception as exc:  # semantic masks are optional
        print(f"[refetch] water mask failed: {exc}", flush=True)
        water = np.zeros_like(osm_u8)

    try:
        sat = _fetch_sat((N, S, E, W), osm_u8.shape[:2])
    except Exception as exc:
        print(f"[refetch] satellite failed: {exc}", flush=True)
        sat = np.zeros((*osm_u8.shape[:2], 3), dtype=np.uint8)

    # OSM px -> STL px scale that makes the new raster line up with the STL frame
    fit_scale = margin * max(int(stl_shape[0]), int(stl_shape[1])) / float(resolution)

    return {
        "ok": True,
        "bbox_nsew": [N, S, E, W],
        "center": [(N + S) / 2.0, (E + W) / 2.0],
        "cell_size_m": _cell_size_m(N, S, E, W, resolution),
        "side_m": side_m,
        "resolution": resolution,
        "margin": margin,
        "fit_scale": fit_scale,
        "osm_shape": list(osm_u8.shape[:2]),
        "buildings_px": int(np.count_nonzero(osm_hm > 0)),
        "images": {
            "osm_buildings": _png_data_uri(osm_u8),
            "osm_water": _png_data_uri(water),
            "sat": _png_data_uri(sat),
        },
    }


def _fetch_sat(bbox_nsew, shape_hw):
    """ESRI World Imagery resampled onto the OSM pixel grid.

    fetch_satellite_tiles hands back a normal image with row 0 = north, while
    the OSM and STL rasters are row 0 = south (mesh_to_heightmap's convention).
    The trailing vertical flip is what puts them in the same frame; see
    export_align_data.fetch_sat, which must stay in step with this.
    """
    import cv2

    from geo2stl.sat2stl import fetch_satellite_tiles

    north, south, east, west = (float(v) for v in bbox_nsew)
    b64 = fetch_satellite_tiles(north, south, east, west, dim=max(shape_hw))
    raw = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("satellite JPEG decode failed")
    h, w = shape_hw
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img[::-1])


ROUTES = {
    "/api/save": api_save,
    "/api/load": api_load,
    "/api/refine": api_refine,
    "/api/refetch": api_refetch,
}


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------

class AlignHandler(SimpleHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/api/health":
            self._json(200, {"ok": True, "server": "align", "root": str(HERE)})
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        route = ROUTES.get(self.path.split("?", 1)[0].rstrip("/"))
        if route is None:
            self._json(404, {"ok": False, "error": f"no such endpoint: {self.path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError(f"request body too large ({length} bytes)")
            body = json.loads(self.rfile.read(length) or b"{}")
            self._json(200, route(body))
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    handler = partial(AlignHandler, directory=str(HERE))
    srv = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"align server on http://{args.host}:{args.port}/drag_align.html", flush=True)
    print(f"  static root : {HERE}", flush=True)
    print(f"  ground truth: {GROUND_TRUTH}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
