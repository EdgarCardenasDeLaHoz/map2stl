"""tools/photo_label request handling: 4xx for bad input, label-file ownership, cache keys, off-DEM labels."""

import importlib.util
import json
import math
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from city2stl.skyline import camera_solver as cs

SERVER = Path(__file__).resolve().parents[1] / "tools" / "photo_label" / "server.py"
LAT0, LON0 = 37.17, -3.60
KX = cs.M_PER_DEG_LAT * math.cos(math.radians(LAT0))


def _png(path, size=(40, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "gray").save(path)


def _tool(name):
    """Import a tools/photo_label module by its bare name (the server does the same)."""
    d = str(SERVER.parent)
    if d not in sys.path:
        sys.path.insert(0, d)
    return importlib.import_module(name)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("photo_label_server_q", SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def world(mod, tmp_path_factory):
    root = tmp_path_factory.mktemp("requests")
    _png(root / "ph" / "a" / "b.png")
    _png(root / "ph" / "a_b.png")
    (root / "r.json").write_text(json.dumps({"elements": []}), encoding="utf-8")
    specs = {"r": {"label": "R", "osm": str(root / "r.json"), "dem": "", "photos": {"": str(root / "ph")}}}
    reg = mod.Regions(specs, "r", annotations_dir=root / "ann", api_key="")
    httpd = mod.make_server(reg, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    httpd.shutdown()
    httpd.server_close()


def _req(base, path, data=None, headers=None):
    req = urllib.request.Request(base + path, data, headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _get(base, path):
    return _req(base, path)


def _post(base, path, obj):
    code, raw = _req(base, path, json.dumps(obj).encode(), {"Content-Type": "application/json"})
    return code, json.loads(raw)


def _raw_post(base, path, raw):
    return _req(base, path, raw, {"Content-Type": "application/json"})


# --- S2: malformed requests are 400 -------------------------------------------------------------

def test_bad_json_and_non_dict_body_are_400(world):
    base, _ = world
    assert _raw_post(base, "/api/solve", b"{not json")[0] == 400
    assert _raw_post(base, "/api/solve", b"[]")[0] == 400
    assert _raw_post(base, "/api/solve", b"3")[0] == 400


def test_missing_key_is_400(world):
    base, _ = world
    code, out = _post(base, "/api/solve", {"labels": [{}] * 4})   # no image_px
    assert code == 400 and "error" in out


def test_negative_content_length_is_400(world):
    base, _ = world
    host, port = base.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=10) as s:
        s.sendall(b"POST /api/solve HTTP/1.1\r\nHost: x\r\nContent-Length: -5\r\n\r\n")
        assert b" 400 " in s.recv(200).split(b"\r\n")[0]


# --- S3: unknown / missing photo ----------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/depth/compute", "/api/depth/fit", "/api/depth/orient", "/api/labels"])
def test_missing_photo_is_400_unknown_is_404(world, path):
    base, _ = world
    assert _post(base, path, {})[0] == 400
    assert _post(base, path, {"photo": "nope.png"})[0] == 404


def test_get_depth_and_labels_without_photo(world):
    base, _ = world
    assert _get(base, "/api/labels")[0] == 400
    assert _get(base, "/api/labels?photo=")[0] == 400
    assert _get(base, "/api/depth")[0] == 400
    assert _get(base, "/api/depth?photo=nope.png")[0] == 404


# --- S4: label-file collisions ------------------------------------------------------------------

def test_label_name_collision_is_409_and_files_stay(world):
    base, root = world
    lab = [{"label": "P", "x": 1, "y": 2, "lat": LAT0, "lon": LON0}]
    assert _post(base, "/api/labels", {"photo": "a/b.png", "labels": lab})[0] == 200
    code, out = _post(base, "/api/labels", {"photo": "a_b.png", "labels": lab})
    assert code == 409 and "another photo" in out["error"]
    assert json.loads(_get(base, "/api/labels?photo=a_b.png")[1])["labels"] == []
    assert json.loads(_get(base, "/api/labels?photo=a/b.png")[1])["labels"][0]["x"] == 1
    assert _post(base, "/api/labels", {"photo": "a/b.png", "labels": lab})[0] == 200
    assert [p.name for p in (root / "ann").iterdir()] == ["r_a_b.json"]   # no stray temp files


# --- misc: region-free routes ignore an unknown ?region= ----------------------------------------

def test_region_free_routes_ignore_unknown_region(world):
    base, _ = world
    for path in ("/api/regions", "/", "/index.html"):
        assert _get(base, path + "?region=nowhere")[0] == 200, path
    assert _get(base, "/api/photos?region=nowhere")[0] == 400


# --- S1: off-DEM labels -------------------------------------------------------------------------

def test_off_dem_labels_are_excluded_not_fatal(mod):
    from city2stl.skyline import photo_view as pv
    ts = mod.ts

    def dem(lat, lon):
        lat = np.asarray(lat, float)
        out = np.full(lat.shape, 700.0)
        out[lat > LAT0 + 0.03] = np.nan     # the far north is off the DEM
        return out

    cam = pv.PinholeCamera(LAT0, LON0, 740.0, 10.0, -6.0, 0.0, 450.0, 800, 600)
    spots = [(-300, 1500), (-100, 1400), (250, 1800), (700, 1600), (-250, 1200), (60, 2000)]
    labels = []
    for i, (e, n) in enumerate(spots):
        lat, lon = LAT0 + n / cs.M_PER_DEG_LAT, LON0 + e / KX
        x, y = cam.project_ll(lat, lon, 700.0)
        labels.append({"label": f"P{i}", "x": float(x), "y": float(y), "lat": lat, "lon": lon})
    far = dict(labels[0], label="far", lat=LAT0 + 0.05)
    seed = {"lat": LAT0, "lon": LON0, "heading_deg": 10.0, "f_px": 450.0}
    out = ts.solve_terrain(dem, [*labels, far], 800, 600, seed, label_height="ground")
    assert out is not None and out["off_dem"] == [len(labels)]
    assert len(out["residuals_px"]) == len(labels) + 1 and out["residuals_px"][-1] is None
    assert out["camera"]["heading_deg"] == pytest.approx(10.0, abs=1.5)
    assert ts.solve_terrain(dem, [far] * 6 + labels[:3], 800, 600, seed, label_height="ground") is None


# --- S5: cache keys -----------------------------------------------------------------------------

def test_model_cache_key_follows_heights_and_footprints(tmp_path):
    model_glb = _tool("model_glb")
    dem, osm, hts = tmp_path / "d.tif", tmp_path / "o.json", tmp_path / "h.json"
    for f in (dem, osm, hts):
        f.write_text("x")
    inp = {"source": "footprints"}
    k0 = model_glb.cache_key(dem, inp, osm_path=osm, heights_path=hts)
    assert k0 == model_glb.cache_key(dem, inp, osm_path=osm, heights_path=hts)
    st = hts.stat()
    os.utime(hts, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert model_glb.cache_key(dem, inp, osm_path=osm, heights_path=hts) != k0


def test_guess_key_follows_photo_mtime(tmp_path):
    solve = _tool("solve")
    photo = tmp_path / "p.png"
    photo.write_text("x")
    k0 = solve._guess_key("p.png", {}, [], 5, [], {}, files=(photo, None))
    st = photo.stat()
    os.utime(photo, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert solve._guess_key("p.png", {}, [], 5, [], {}, files=(photo, None)) != k0


# --- misc: footprints, fov ----------------------------------------------------------------------

def test_inside_accepts_3d_and_heights_skip_bad_latlon():
    fp = _tool("footprints")
    ring = [[0, 0, 5], [2, 0, 5], [2, 2, 5], [0, 2, 5], [0, 0, 5]]
    assert fp._inside(1, 1, ring) and not fp._inside(3, 1, ring)
    fc = {"features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    bad = [{"lat": float("nan"), "lon": 1, "height_m": 9}, {"lat": "x", "lon": 1, "height_m": 9},
           {"lon": 1, "height_m": 9}]
    assert fp.apply_heights(fc, bad) == 0


def test_zero_fov_is_rejected():
    with pytest.raises(ValueError):
        _tool("pano_camera").f_px_of_fov(100, 0)
