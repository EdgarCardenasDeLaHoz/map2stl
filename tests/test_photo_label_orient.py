"""Orientation from depth: tools/photo_label/orient.py and POST /api/depth/orient of server.py."""
import base64
import importlib.util
import json
import math
import sys
import threading
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "photo_label"
SERVER = TOOLS / "server.py"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import orient  # noqa: E402

CAM = {"lat": 37.0, "lon": -3.0, "elev_m": 100.0, "heading_deg": 0.0, "pitch_deg": 0.0,
       "roll_deg": 0.0, "f_px": 500.0, "width": 640, "height": 480}
GW, GH = 64, 48


def _rays(cam=CAM, gw=GW, gh=GH):
    fwd, right, up = orient.camera_axes(cam)
    W, H, f = cam["width"], cam["height"], cam["f_px"]
    a = ((np.arange(gw) + .5) * W / gw - W / 2) / f
    b = ((np.arange(gh) + .5) * H / gh - H / 2) / f
    d = fwd + a[None, :, None] * right - b[:, None, None] * up
    return d, fwd


def _plane_z(n, p0, bound=None):
    """Forward depth of a plane n.(p - p0) = 0 seen from the origin (NaN when behind)."""
    n, p0 = np.asarray(n, float), np.asarray(p0, float)
    d, fwd = _rays()
    nd = d @ n
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (n @ p0) / nd
    t = np.where(t > 0, t, np.nan)
    if bound is not None:
        t = np.where(bound(d * t[..., None]), t, np.nan)
    return t * (d @ fwd)


def _run(z):
    P = orient.backproject(z, CAM)
    return P, orient.classify(orient.normals(P), P, CAM)


def test_ground_plane_is_flat():
    z = _plane_z([0, 0, 1], [0, 0, -10])          # camera 10 m above flat ground
    P, r = _run(z)
    m = np.isfinite(z)[1:-1, 1:-1]
    cls = r["cls"][1:-1, 1:-1][m]
    # near the horizon the range jumps between rows exceed max_jump, so those cells stay NaN
    got = cls[cls > 0]
    assert m.sum() > 100 and got.size > 0.5 * m.sum() and (got == orient.FLAT).all()


def test_ground_vs_roof_uses_dem():
    z = _plane_z([0, 0, 1], [0, 0, -10])
    P = orient.backproject(z, CAM)
    n = orient.normals(P)
    at = np.full(z.shape, 90.0)                    # DEM = camera elev - 10 -> plane sits on it
    r = orient.classify(n, P, CAM, at)
    assert (r["cls"] == orient.GROUND).sum() > 100 and (r["cls"] == orient.FLAT).sum() == 0
    r2 = orient.classify(n, P, CAM, np.full(z.shape, 50.0))   # 40 m above the DEM -> roof
    assert (r2["cls"] == orient.FLAT).sum() > 100 and (r2["cls"] == orient.GROUND).sum() == 0


def test_wall_faces_south():
    z = _plane_z([0, -1, 0], [0, 40, 0])
    P, r = _run(z)
    m = np.isfinite(z)[1:-1, 1:-1]
    cls = r["cls"][1:-1, 1:-1][m]
    assert (cls == orient.WALL).mean() > 0.95
    az = r["az"][1:-1, 1:-1][m][cls == orient.WALL]
    assert abs(np.median(az) - 180) < 3
    assert np.isnan(r["pitch"][1:-1, 1:-1][m][cls == orient.WALL]).all()


def test_pitched_roof_30_deg_facing_south():
    t = math.radians(30)
    z = _plane_z([0, -math.sin(t), math.cos(t)], [0, 60, -5])
    P, r = _run(z)
    m = np.isfinite(z)[1:-1, 1:-1]
    cls = r["cls"][1:-1, 1:-1][m]
    assert (cls == orient.PITCHED).mean() > 0.95
    assert abs(np.nanmedian(r["pitch"][1:-1, 1:-1][m]) - 30) < 3
    assert abs(np.nanmedian(r["az"][1:-1, 1:-1][m]) - 180) < 3


def test_occlusion_edge_is_nan():
    z = np.full((GH, GW), 40.0)
    z[:, : GW // 2] = 10.0                         # near panel left, far panel right
    n = orient.normals(orient.backproject(z, CAM))
    assert np.isnan(n[5:-5, GW // 2 - 1]).all() and np.isnan(n[5:-5, GW // 2]).all()
    assert np.isfinite(n[5:-5, 10]).all() and np.isfinite(n[5:-5, 50]).all()
    assert np.isnan(n[0]).all() and np.isnan(n[:, 0]).all()


def test_backproject_nan_and_centre():
    z = np.full((GH, GW), 20.0)
    z[3, 3] = np.nan
    z[4, 4] = -1
    P = orient.backproject(z, CAM)
    assert np.isnan(P[3, 3]).all() and np.isnan(P[4, 4]).all()
    # heading 0: forward is north, so a centred cell sits ~20 m north of the camera
    assert abs(P[GH // 2, GW // 2, 1] - 20) < 1 and abs(P[GH // 2, GW // 2, 0]) < 1


def test_rasterize_near_over_far():
    far = {"id": "far", "rings": [[[0, 0], [640, 0], [640, 480], [0, 480]]], "dist": 100}
    near = {"id": "near", "rings": [[[0, 0], [320, 0], [320, 480], [0, 480]]], "dist": 20}
    labels, ids = orient.rasterize([near, far], GW, GH, 640, 480)
    assert ids == ["near", "far"]
    assert labels[10, 5] == 0 and labels[10, 60] == 1
    labels2, _ = orient.rasterize([far, near], GW, GH, 640, 480)
    assert labels2[10, 5] == 1 and labels2[10, 60] == 0


def test_building_summary_toy():
    cls = np.zeros((4, 8), np.uint8)
    pitch = np.full((4, 8), np.nan, np.float32)
    az = np.full((4, 8), np.nan, np.float32)
    labels = np.full((4, 8), -1, np.int32)
    labels[:, :4] = 0
    labels[:, 4:] = 1
    cls[:, 0] = orient.WALL
    az[:, 0] = 181
    cls[:, 1:4] = orient.PITCHED
    pitch[:, 1:4] = 30
    cls[:2, 4:6] = orient.FLAT                     # 4 flat cells only: under the 5-cell minimum
    s = orient.building_summary(cls, pitch, az, labels)
    assert s[0]["roof"] == "pitched" and s[0]["pitch_deg"] == 30
    assert s[0]["wall_az"] == [180] or s[0]["wall_az"] == [181]
    assert s[0]["n"] == 16 and abs(s[0]["frac_wall"] - 0.25) < 1e-6
    assert s[1]["roof"] == "unknown" and s[1]["pitch_deg"] is None and s[1]["wall_az"] == []


# ---------- route ----------

@pytest.fixture()
def env(tmp_path):
    spec = importlib.util.spec_from_file_location("photo_label_server_o", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "photos").mkdir()
    Image.new("RGB", (640, 480), "gray").save(tmp_path / "photos" / "p.png")
    (tmp_path / "o.json").write_text('{"elements": []}')
    cfg = mod.Config(tmp_path / "photos", tmp_path / "o.json", tmp_path / "a", api_key="K", dem_path=None,
                     tiles_cache_dir=tmp_path / "cache")
    httpd = mod.make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()
    httpd.server_close()


def _post(base, path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _dec(s, dt, shape):
    return np.frombuffer(base64.b64decode(s), dtype=dt).reshape(shape)


def test_route_google_only(env):
    base, tmp = env
    z = _plane_z([0, -1, 0], [0, 40, 0]).astype("<f4")
    bld = [{"id": "b1", "rings": [[[100, 100], [500, 100], [500, 400], [100, 400]]], "dist": 40}]
    r = _post(base, "/api/depth/orient", {
        "photo": "p.png", "camera": CAM, "depth": base64.b64encode(z.tobytes()).decode(),
        "depth_w": GW, "depth_h": GH, "buildings": bld})
    assert r["ok"] and r["photo"] is None and r["agree"] is None
    g = r["google"]
    assert (g["w"], g["h"]) == (GW, GH)
    cls = _dec(g["cls"], "u1", (GH, GW))
    assert (cls == orient.WALL).sum() > 500
    assert _dec(g["pitch"], "<f4", (GH, GW)).shape == (GH, GW)
    assert g["buildings"]["b1"]["frac_wall"] > 0.9
    side = json.loads((tmp / "photos" / "p_orient.json").read_text())
    assert side["camera"]["f_px"] == 500 and "b1" in side["google_buildings"] and "written" in side


def test_route_both_sources_agree(env):
    base, tmp = env
    z = _plane_z([0, -1, 0], [0, 40, 0])
    a, b = 40.0, -1.0
    disp = (a / np.nan_to_num(z, nan=1e6) + b).astype("<f4")   # z = a / (disp - b)
    full = np.repeat(np.repeat(disp, 10, axis=0), 10, axis=1)
    np.save(tmp / "photos" / "p_depth.npy", full)
    r = _post(base, "/api/depth/orient", {
        "photo": "p.png", "camera": CAM, "a": a, "b": b, "grid_w": GW,
        "depth": base64.b64encode(z.astype("<f4").tobytes()).decode(), "depth_w": GW, "depth_h": GH})
    assert r["ok"] and r["photo"] and r["google"]
    assert r["agree"] is not None and r["agree"] > 0.8


def test_route_neither_source(env):
    base, _ = env
    r = _post(base, "/api/depth/orient", {"photo": "p.png", "camera": CAM})
    assert r["ok"] is False and r["reason"]
