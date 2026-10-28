"""Depth view endpoints and the Google tile disk cache of tools/photo_label/server.py."""
import base64
import importlib.util
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

SERVER = Path(__file__).resolve().parents[1] / "tools" / "photo_label" / "server.py"
KEY = "FAKE-KEY-0123456789"


class _Resp:
    def __init__(self, body, ctype="model/gltf-binary", cc=None, status=200):
        self._b, self.status = body, status
        self.headers = {"Content-Type": ctype}
        if cc:
            self.headers["Cache-Control"] = cc

    def read(self, n=-1):
        out, self._b = (self._b, b"") if n < 0 else (self._b[:n], self._b[n:])
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def env(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("photo_label_server_d", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "photos").mkdir()
    Image.new("RGB", (800, 600), "gray").save(tmp_path / "photos" / "p.png")
    (tmp_path / "o.json").write_text('{"elements": []}')
    calls, reply = [], {}

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return reply["r"]

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod, "_depth_state", {})
    cfg = mod.Config(tmp_path / "photos", tmp_path / "o.json", tmp_path / "a", api_key=KEY, dem_path=None,
                     tiles_cache_dir=tmp_path / "cache")
    httpd = mod.make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield mod, f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path, calls, reply
    httpd.shutdown()
    httpd.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.read()


def _jget(base, path):
    return json.loads(_get(base, path)[1])


def _post(base, path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _b64(a):
    return base64.b64encode(np.asarray(a, "<f4").tobytes()).decode()


def test_depth_missing_then_ready_downsampled(env):
    _, base, tmp, _, _ = env
    assert _jget(base, "/api/depth?photo=p.png") == {"ok": True, "state": "missing"}
    d = np.random.default_rng(0).random((600, 800)).astype("f4")
    d[:2, :2] = np.nan
    np.save(tmp / "photos" / "p_depth.npy", d)
    r = _jget(base, "/api/depth?photo=p.png")
    assert r["state"] == "ready" and (r["w"], r["h"]) == (400, 300) and (r["photo_w"], r["photo_h"]) == (800, 600)
    a = np.frombuffer(base64.b64decode(r["disp"]), "<f4").reshape(300, 400)
    assert np.isclose(a[100, 100], d[200:202, 200:202].mean(), atol=1e-5)
    assert np.isnan(a[0, 0]) and np.isfinite(a[0, 1])


def _wait_state(base, want_not="running"):
    for _ in range(200):
        r = _jget(base, "/api/depth?photo=p.png")
        if r["state"] != want_not:
            return r
        time.sleep(0.05)
    return r


def test_depth_compute_background_without_gpu(env, monkeypatch):
    _, base, tmp, _, _ = env
    import city2stl.resources as res
    import city2stl.skyline.depth_estimation as de
    monkeypatch.setattr(res, "wait_for_gpu", lambda *a, **k: True)
    monkeypatch.setattr(res, "free_gpu_cache", lambda: None)
    seen = []
    monkeypatch.setattr(de, "_depth_anything_raw",
                        lambda rgb, device="cpu": seen.append((rgb.shape, device)) or np.ones(rgb.shape[:2], "f4"))
    assert _post(base, "/api/depth/compute", {"photo": "p.png"})["state"] in ("running", "ready")
    assert _wait_state(base)["state"] == "ready"
    assert seen == [((600, 800, 3), "cuda")]
    assert np.load(tmp / "photos" / "p_depth.npy").dtype == np.float32
    assert _post(base, "/api/depth/compute", {"photo": "p.png"})["state"] == "ready"


def test_depth_compute_no_cuda_is_error(env, monkeypatch):
    _, base, _, _, _ = env
    import city2stl.resources as res
    monkeypatch.setattr(res, "wait_for_gpu", lambda *a, **k: False)
    monkeypatch.setattr(res, "free_gpu_cache", lambda: None)
    _post(base, "/api/depth/compute", {"photo": "p.png"})
    r = _wait_state(base)
    assert r["state"] == "error" and "GPU" in r["error"]


def test_depth_fit_recovers_a_b(env):
    _, base, tmp, _, _ = env
    gw, gh = 80, 60
    dist = np.random.default_rng(1).uniform(30, 3000, (gh, gw))
    disp = 3000 / dist + 0.1
    big = np.repeat(np.repeat(disp, 10, 0), 10, 1).astype("f4")  # 800x600, constant per grid cell
    np.save(tmp / "photos" / "p_depth.npy", big)
    dist[:5] = np.nan
    r = _post(base, "/api/depth/fit", {"photo": "p.png", "depth": _b64(dist), "depth_w": gw, "depth_h": gh})
    assert r["ok"] and abs(r["a"] - 3000) < 5 and abs(r["b"] - 0.1) < 0.01 and r["n"] > 1000


def test_depth_fit_reasons(env):
    _, base, tmp, _, _ = env
    r = _post(base, "/api/depth/fit", {"photo": "p.png", "depth": "", "depth_w": 2, "depth_h": 2})
    assert r["ok"] is False
    np.save(tmp / "photos" / "p_depth.npy", np.ones((600, 800), "f4"))
    r = _post(base, "/api/depth/fit", {"photo": "p.png", "depth": _b64([np.nan] * 4), "depth_w": 2, "depth_h": 2})
    assert r["ok"] is False and r["reason"]


T = "/tiles3d/v1/3dtiles/datasets/a/files/b.glb"


def _cache_files(tmp):
    return [p for p in (tmp / "cache").rglob("*") if p.is_file()]


def test_tile_cache_second_request_from_disk_no_key_stored(env):
    _, base, tmp, calls, reply = env
    reply["r"] = _Resp(b"glbdata", cc="public, max-age=3600")
    assert _get(base, T + "?session=S1")[1] == b"glbdata"
    reply["r"] = None
    assert _get(base, T + "?session=OTHER")[1] == b"glbdata"
    assert len(calls) == 1
    files = _cache_files(tmp)
    assert files and all(KEY.encode() not in p.read_bytes() and KEY not in p.name for p in files)
    assert not any(b"S1" in p.read_bytes() for p in files)


def test_tile_cache_skips_no_store_json_and_no_maxage(env):
    _, base, tmp, calls, reply = env
    for path, resp in [(T, _Resp(b"x", cc="no-store, max-age=100")),
                       (T.replace("b.glb", "c.glb"), _Resp(b"x")),
                       ("/tiles3d/v1/3dtiles/root.json", _Resp(b"{}", "application/json", cc="max-age=600"))]:
        reply["r"] = resp
        _get(base, path)
    assert _cache_files(tmp) == []


def test_tile_cache_expired_refetched(env):
    mod, base, tmp, calls, reply = env
    mod.tile_cache_put(tmp / "cache", T[len("/tiles3d/"):], b"v1", "model/gltf-binary", 60,
                       now=time.time() - 120)  # already stale
    reply["r"] = _Resp(b"v2", cc="max-age=60")
    assert _get(base, T)[1] == b"v2"
    assert len(calls) == 1


def test_tile_cache_trim_removes_oldest(env):
    mod, _, tmp, _, _ = env
    for i in range(4):
        mod.tile_cache_put(tmp / "cache", f"v1/3dtiles/f{i}.glb", b"x" * 100, "a/b", 60)
    fs = sorted(_cache_files(tmp), key=lambda p: p.name)
    for i, p in enumerate(fs):
        os.utime(p, (1000 + i, 1000 + i))
    mod.tile_cache_trim(tmp / "cache", cap=300)
    assert sum(p.stat().st_size for p in _cache_files(tmp)) <= 300
    assert fs[-1].exists() and not fs[0].exists()
