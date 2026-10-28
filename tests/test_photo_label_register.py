"""tools/photo_label/register.py (automatic registration maths) and its routes."""

import base64
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from city2stl.skyline.photo_view import M_PER_DEG_LAT, PinholeCamera

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "photo_label"
sys.path.insert(0, str(TOOLS))
import register as reg  # noqa: E402

LAT0, LON0 = 37.177, -3.592
KX = M_PER_DEG_LAT * math.cos(math.radians(LAT0))
TRUE = PinholeCamera(LAT0, LON0, 780.0, 25.0, -14.0, 1.5, 900.0, 1200, 800)


def _world(n, rng, cam=TRUE):
    """Random points 150-2500 m ahead of ``cam`` inside its frame, on terrain 700-790 m + roofs."""
    right, up, fwd = cam.axes()
    pts = []
    while len(pts) < n:
        dist = rng.uniform(150, 2500)
        x, y = rng.uniform(0, cam.width), rng.uniform(0, cam.height)
        d = fwd + (x - cam.width / 2) / cam.f_px * right - (y - cam.height / 2) / cam.f_px * up
        p = d / np.linalg.norm(d) * dist
        pts.append([cam.lat + p[1] / M_PER_DEG_LAT, cam.lon + p[0] / KX, cam.elev_m + p[2]])
    return np.array(pts)


def test_solve_pose_recovers_perturbed_camera():
    rng = np.random.default_rng(3)
    lla = _world(400, rng)
    px, py = TRUE.project_ll(lla[:, 0], lla[:, 1], lla[:, 2])
    xy = np.stack([px, py], 1) + rng.normal(0, 0.6, (400, 2))
    bad = rng.random(400) < 0.25                                   # wrong matches
    xy[bad] = rng.uniform([0, 0], [TRUE.width, TRUE.height], (bad.sum(), 2))
    cam0 = replace(TRUE, lat=TRUE.lat + 70 / M_PER_DEG_LAT, lon=TRUE.lon - 70 / KX, elev_m=TRUE.elev_m + 25,
                   heading_deg=TRUE.heading_deg + 4.0, pitch_deg=TRUE.pitch_deg + 3.0, roll_deg=0.0,
                   f_px=TRUE.f_px * 1.15)
    sol = reg.solve_pose(xy, lla, cam0)
    cam = sol["camera"]
    assert sol["n_inliers"] >= 0.7 * (~bad).sum()
    assert sol["median_px"] < 1.5
    assert abs((cam.lon - TRUE.lon) * KX) < 8 and abs((cam.lat - TRUE.lat) * M_PER_DEG_LAT) < 8
    assert abs(cam.elev_m - TRUE.elev_m) < 8
    assert abs(cam.heading_deg - TRUE.heading_deg) < 0.4
    assert abs(cam.pitch_deg - TRUE.pitch_deg) < 0.4
    assert abs(cam.roll_deg - TRUE.roll_deg) < 0.6
    assert abs(cam.f_px / TRUE.f_px - 1) < 0.02
    assert not sol["inliers"][bad].mean() > 0.2                    # outliers rejected


def test_solve_pose_f_below_start():
    rng = np.random.default_rng(4)
    lla = _world(300, rng)
    px, py = TRUE.project_ll(lla[:, 0], lla[:, 1], lla[:, 2])
    cam0 = replace(TRUE, heading_deg=TRUE.heading_deg - 5, f_px=TRUE.f_px * 0.85, elev_m=TRUE.elev_m - 15)
    sol = reg.solve_pose(np.stack([px, py], 1), lla, cam0)
    assert abs(sol["camera"].f_px / TRUE.f_px - 1) < 0.02
    assert abs(sol["camera"].heading_deg - TRUE.heading_deg) < 0.3


def test_solve_pose_too_few_returns_start():
    sol = reg.solve_pose(np.zeros((3, 2)), np.zeros((3, 3)), TRUE)
    assert sol["n_inliers"] == 0 and sol["camera"] == TRUE


def test_lift_round_trips_with_pinhole_projection():
    rng = np.random.default_rng(5)
    lla = _world(60, rng)
    cam = replace(TRUE, elev_m=TRUE.elev_m + reg.GEOID_DEFAULT_M)           # the render camera, ellipsoidal
    right, up, fwd = cam.axes()
    # depth grid made the way the browser makes it: view depth of the surface seen through each cell
    dw, dh = 120, 80
    depth = np.full((dh, dw), 900.0, np.float32)
    px = np.stack([(np.arange(dw) + .5) * cam.width / dw - .5] * dh)
    py = np.stack([(np.arange(dh) + .5) * cam.height / dh - .5] * dw, 1)
    # a plane 900 m ahead (z-depth), so every cell has the same depth
    pts = np.stack([rng.uniform(5, cam.width - 5, 50), rng.uniform(5, cam.height - 5, 50)], 1)
    lat, lon, h, ok = reg.lift(pts, depth, cam)
    assert ok.all()
    x, y = cam.project_ll(lat, lon, h)
    assert np.abs(x - pts[:, 0]).max() < 1e-6 and np.abs(y - pts[:, 1]).max() < 1e-6
    d = np.stack([(lon - cam.lon) * KX, (lat - cam.lat) * M_PER_DEG_LAT, h - cam.elev_m], 1)
    assert np.allclose(d @ fwd, 900.0)
    del px, py, lla


def test_lift_silhouette_and_sky_are_dropped():
    depth = np.full((10, 10), 500.0, np.float32)
    depth[:, 5:] = 900.0                                           # a depth jump between columns 4 and 5
    depth[0, 0] = np.nan
    cam = replace(TRUE, width=100, height=100)
    _, _, _, ok = reg.lift(np.array([[20.0, 50.0], [45.0, 50.0], [90.0, 50.0], [2.0, 2.0]]), depth, cam)
    assert ok.tolist() == [True, False, True, False]


def test_geoid_estimate_uses_low_percentile():
    rng = np.random.default_rng(1)
    dem = rng.uniform(690, 700, 400)
    h = dem + 49.0 + np.where(rng.random(400) < 0.6, rng.uniform(5, 25, 400), 0.0)   # 60 % roofs
    g, n = reg.estimate_geoid(h, dem)
    assert n == 400 and 48.0 < g < 55.0
    assert reg.estimate_geoid(h[:5], dem[:5], default=47.0) == (47.0, 5)


def _texture(rng, w=640, h=480):
    img = np.full((h, w, 3), 128, np.uint8)
    for _ in range(250):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        col = tuple(int(c) for c in rng.integers(0, 255, 3))
        if rng.random() < .5:
            cv2.circle(img, (x, y), int(rng.integers(4, 18)), col, -1)
        else:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(6, 40)), y + int(rng.integers(6, 40))), col, -1)
    return cv2.GaussianBlur(img, (0, 0), 1.0)


def test_match_finds_the_shift():
    rng = np.random.default_rng(2)
    photo = _texture(rng)
    render = np.roll(photo, (12, 30), axis=(0, 1))                 # 30 px right, 12 px down
    pa, pb = reg.match(photo, render)
    assert len(pa) > 40
    d = np.median(pb - pa, axis=0)
    assert abs(d[0] - 30) < 0.5 and abs(d[1] - 12) < 0.5


def test_match_at_a_different_scale_returns_photo_pixels():
    rng = np.random.default_rng(7)
    small = _texture(rng, 600, 400)
    photo = cv2.resize(small, (1200, 800), interpolation=cv2.INTER_CUBIC)       # photo twice the render
    render = np.roll(small, (10, 20), axis=(0, 1))
    pa, pb = reg.match(photo, render)
    assert len(pa) > 40
    d = np.median(pa - 2.0 * pb, axis=0)                           # photo px = 2 x render px (+ the shift)
    assert abs(d[0] + 40) < 1.5 and abs(d[1] + 20) < 1.5


def test_register_step_end_to_end_on_a_synthetic_view():
    """Photo and render are the same textured wall (a plane 600 m ahead) seen from cameras 20 m
    and 2 deg apart; the step must come back closer to the photo's camera."""
    rng = np.random.default_rng(11)
    wall = _texture(rng, 1600, 1200)
    photo_cam = replace(TRUE, pitch_deg=0.0, roll_deg=0.0, width=800, height=600, f_px=700.0, elev_m=780.0)
    start = replace(photo_cam, heading_deg=photo_cam.heading_deg + 2.0, elev_m=photo_cam.elev_m + 8)

    def view(cam, geoid=0.0):
        """Render of the vertical plane through a point 600 m ahead of photo_cam, seen from ``cam``."""
        right, up, fwd = photo_cam.axes()
        c0 = np.array([(photo_cam.lon - LON0) * KX, (photo_cam.lat - LAT0) * M_PER_DEG_LAT, photo_cam.elev_m])
        origin = c0 + fwd * 600.0                                   # the wall's centre
        u, v = right, up                                            # wall axes, 1 wall px = 0.5 m
        crd, cud, cfd = cam.axes()
        cc = np.array([(cam.lon - LON0) * KX, (cam.lat - LAT0) * M_PER_DEG_LAT, cam.elev_m + geoid])
        xs, ys = np.meshgrid(np.arange(cam.width), np.arange(cam.height))
        ray = (cfd[None, None] + ((xs - cam.width / 2) / cam.f_px)[..., None] * crd
               - ((ys - cam.height / 2) / cam.f_px)[..., None] * cud)
        t = ((origin - cc) @ fwd) / (ray @ fwd)
        hit = cc + ray * t[..., None] - origin
        wu, wv = (hit @ u) / 0.5 + 800, 800 - (hit @ v) / 0.5
        img = cv2.remap(wall, wu.astype(np.float32), wv.astype(np.float32), cv2.INTER_LINEAR)
        return img, (t[..., None] * ray @ cfd)

    photo, _ = view(photo_cam)
    render, zfull = view(start, geoid=reg.GEOID_DEFAULT_M)
    depth = zfull[::10, ::10].astype(np.float32)
    # ``start`` and ``photo_cam`` share lat/lon here, so the lift's ENU frame is the same as view()'s
    out = reg.register_step(photo, render, depth, start, dem=None, geoid_m=reg.GEOID_DEFAULT_M)
    assert out["ok"], out
    assert out["n_inliers"] > 40 and out["median_px"] < 3
    assert abs(out["camera"]["heading_deg"] - photo_cam.heading_deg) < 0.5
    assert len(out["pairs"]) <= reg.MAX_PAIRS and {"x", "y", "lat", "lon", "h"} <= set(out["pairs"][0])
    # a region with a measured geoid keeps it, whatever the DEM says (a DEM 100 m off would pull it)
    fixed = reg.register_step(photo, render, depth, start, dem=lambda la, lo: np.full(np.shape(la), 600.0),
                              geoid_m=reg.GEOID_DEFAULT_M, fit_geoid=False)
    assert fixed["ok"] and fixed["geoid_m"] == reg.GEOID_DEFAULT_M


def test_cam_dict_accepts_both_spellings():
    a = reg.cam_from_dict({"lat": 1, "lon": 2, "elev": 3, "heading": 4, "pitch": 5, "roll": 6, "f": 7,
                           "width": 10, "height": 20})
    b = reg.cam_from_dict({"lat": 1, "lon": 2, "elev_m": 3, "heading_deg": 4, "pitch_deg": 5, "roll_deg": 6,
                           "f_px": 7}, 10, 20)
    assert a == b and reg.cam_to_dict(a)["f_px"] == 7


# ----------------------------------------------------------------- the browser's projection maths

INDEX = TOOLS / "index.html"
NODE = shutil.which("node")


def _js_math() -> str:
    m = re.search(r"// <pinhole-math>(.*?)// </pinhole-math>", INDEX.read_text(encoding="utf-8"), re.S)
    assert m, "pinhole-math region missing from index.html"
    return m.group(1)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_camera_maths_matches_pinhole_camera():
    """The page's pinholeProject (what the Cesium render is set up from) gives PinholeCamera's pixels,
    and the Cesium basis built from cesiumView() equals PinholeCamera.axes()."""
    rng = np.random.default_rng(9)
    cams = [TRUE, replace(TRUE, heading_deg=300.0, pitch_deg=8.0, roll_deg=-4.0, width=700, height=1000),
            replace(TRUE, heading_deg=181.0, pitch_deg=-40.0, roll_deg=0.0)]
    cases = []
    for cam in cams:
        lla = _world(25, rng, cam)
        px, py = cam.project_ll(lla[:, 0], lla[:, 1], lla[:, 2])
        cases.append({"cam": {"lat": cam.lat, "lon": cam.lon, "elev_m": cam.elev_m, "heading_deg": cam.heading_deg,
                              "pitch_deg": cam.pitch_deg, "roll_deg": cam.roll_deg, "f_px": cam.f_px,
                              "width": cam.width, "height": cam.height},
                      "lla": lla.tolist(), "px": px.tolist(), "py": py.tolist(),
                      "axes": [a.tolist() for a in cam.axes()]})
    prog = _js_math() + """
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let worst = 0, worstAx = 0, worstZ = 0;
for (const c of cases) {
  c.lla.forEach((p, i) => {
    const r = pinholeProject(c.cam, p[0], p[1], p[2]);
    worst = Math.max(worst, Math.hypot(r.x - c.px[i], r.y - c.py[i]));
  });
  const v = cesiumView(c.cam, c.cam.width, c.cam.height);
  const b = cesiumBasis(v.heading, v.pitch, v.roll);
  const ax = pinholeAxes(c.cam);
  ['right', 'up', 'fwd'].forEach((k, j) => { for (let m = 0; m < 3; m++) worstAx = Math.max(worstAx, Math.abs(b[k][m] - c.axes[j][m])); });
  // the vertical field of view of the Cesium frustum reproduces the pinhole f
  const tanV = Math.tan(v.fovy / 2) * c.cam.height / 2 ; worstZ = Math.max(worstZ, Math.abs(c.cam.height / 2 / Math.tan(v.fovy / 2) - c.cam.f_px));
}
console.log(JSON.stringify({ worst, worstAx, worstZ }));
"""
    res = subprocess.run([NODE, "-e", prog], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["worst"] < 0.5, out                                  # well under the 1 px asked for
    assert out["worstAx"] < 1e-9, out
    assert out["worstZ"] < 1e-6, out


# ----------------------------------------------------------------- routes

def _load_server():
    spec = importlib.util.spec_from_file_location("photo_label_server_reg", TOOLS / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    mod = _load_server()
    root = tmp_path_factory.mktemp("plreg")
    (root / "photos").mkdir()
    rng = np.random.default_rng(21)
    cv2.imwrite(str(root / "photos" / "a.png"), _texture(rng, 400, 300))
    osm = root / "osm.json"
    osm.write_text(json.dumps({"elements": []}), encoding="utf-8")
    import rasterio
    from rasterio.transform import from_origin
    dem_tif = root / "dem.tif"
    with rasterio.open(dem_tif, "w", driver="GTiff", height=40, width=40, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(-3.62, 37.20, 0.001, 0.001)) as dst:
        dst.write(np.full((1, 40, 40), 700.0, np.float32))
    cfg = mod.Config(root / "photos", osm, root / "ann", "testregion", dem_path=dem_tif, api_key="")
    httpd = mod.make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    httpd.shutdown()
    httpd.server_close()


def _post(base, path, obj):
    req = urllib.request.Request(base + path, json.dumps(obj).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def _b64png(img):
    ok, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf.tobytes()).decode()


def test_step_route_reports_no_matches_without_failing(srv):
    base, root = srv
    blank = np.full((300, 400, 3), 90, np.uint8)
    depth = np.full((30, 40), 500.0, np.float32)
    cam = {"lat": LAT0, "lon": LON0, "elev_m": 780.0, "heading_deg": 10.0, "pitch_deg": -10.0, "roll_deg": 0.0,
           "f_px": 400.0}
    r = _post(base, "/api/register/step", {
        "photo": "a.png", "camera": cam, "render": _b64png(blank),
        "depth": base64.b64encode(depth.tobytes()).decode(), "depth_w": 40, "depth_h": 30, "geoid_m": 50})
    assert r["ok"] is False and r["n_inliers"] == 0 and "reason" in r
    assert r["camera"]["lat"] == pytest.approx(LAT0)
    assert not list(root.rglob("*.jpg")) and not list(root.rglob("*render*"))      # nothing written


def test_step_route_rejects_bad_photo_and_depth(srv):
    base, _ = srv
    r = _post(base, "/api/register/step", {"photo": "nope.png", "camera": {}, "render": "", "depth": ""})
    assert r["ok"] is False and "error" in r
    cam = {"lat": LAT0, "lon": LON0, "elev_m": 780.0, "heading_deg": 10.0, "f_px": 400.0}
    r = _post(base, "/api/register/step", {"photo": "a.png", "camera": cam,
                                           "render": _b64png(np.zeros((30, 40, 3), np.uint8)),
                                           "depth": base64.b64encode(b"\0" * 8).decode(), "depth_w": 40, "depth_h": 30})
    assert r["ok"] is False and "depth" in r["error"]


def test_candidates_route_uses_auto_pose(srv, monkeypatch):
    base, _ = srv
    fake = type(sys)("auto_pose")
    seen = {}

    def candidate_poses(rgb, dem, *, center_lat, center_lon, radius_m=3000.0, viewpoints=None, exif=None, top_k=3):
        seen.update(shape=rgb.shape, c=(center_lat, center_lon), vp=viewpoints, top_k=top_k)
        return [{"lat": 37.18, "lon": -3.59, "elev": 790.0, "heading": 200.0, "pitch": -12.0, "roll": 0.0,
                 "f": 380.0, "width": 400, "height": 300, "score": 0.9, "source": "viewpoint"}]

    fake.candidate_poses = candidate_poses
    fake.viewpoints_from_fc = lambda fc: [(37.1801, -3.5901)]      # within SEED_MATCH_M of the candidate
    monkeypatch.setitem(sys.modules, "auto_pose", fake)
    r = _post(base, "/api/register/candidates", {"photo": "a.png"})
    assert r["ok"], r
    c = r["candidates"][0]
    assert c["elev_m"] == 790.0 and c["heading_deg"] == 200.0 and c["f_px"] == 380.0 and c["width"] == 400
    assert c["source"] == "viewpoint" and r["geoid_m"] == reg.GEOID_DEFAULT_M
    assert seen["c"] == pytest.approx((37.18, -3.60), abs=1e-6)                 # DEM centre
    assert seen["shape"] == (300, 400, 3) and seen["vp"] == [(37.1801, -3.5901)] and seen["top_k"] == 3


def test_candidates_route_without_auto_pose_is_a_clean_error(srv, monkeypatch):
    base, _ = srv
    monkeypatch.setitem(sys.modules, "auto_pose", None)               # makes `import auto_pose` fail
    r = _post(base, "/api/register/candidates", {"photo": "a.png"})
    assert r["ok"] is False and "auto_pose" in r["error"]
