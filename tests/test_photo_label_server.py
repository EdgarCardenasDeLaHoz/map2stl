"""tools/photo_label/server.py against a tiny photo dir and a fake Overpass file."""

import email.message
import importlib.util
import json
import math
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


def _ll(e, n):
    return LAT0 + n / cs.M_PER_DEG_LAT, LON0 + e / KX


def _overpass():
    """One named square building at (500 E, 1500 N) of the origin plus an unnamed one."""
    els, nid = [], 1
    for wid, (e, n), name in [(100, (500, 1500), "Torre Test"), (101, (-300, 1200), None)]:
        ids = []
        for de, dn in [(-20, -20), (20, -20), (20, 20), (-20, 20)]:
            lat, lon = _ll(e + de, n + dn)
            els.append({"type": "node", "id": nid, "lat": lat, "lon": lon})
            ids.append(nid)
            nid += 1
        tags = {"building": "yes"}
        if name:
            tags["name"] = name
        els.append({"type": "way", "id": wid, "nodes": ids + [ids[0]], "tags": tags})
    els.append({"type": "way", "id": 102, "nodes": [1, 2, 3, 1], "tags": {"highway": "path"}})
    return {"elements": els}


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("photo_label_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path_factory.mktemp("pl")
    (root / "photos" / "sub").mkdir(parents=True)
    Image.new("RGB", (400, 300), "gray").save(root / "photos" / "sub" / "a.png")
    Image.new("RGB", (40, 30)).save(root / "photos" / "a_depth.png")
    osm = root / "osm.json"
    osm.write_text(json.dumps(_overpass()), encoding="utf-8")
    cfg = mod.Config(root / "photos", osm, root / "ann", "testregion", dem_path=None)
    httpd = mod.make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    httpd.shutdown()
    httpd.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.read(), r.headers.get_content_type()


def _post(base, path, obj):
    req = urllib.request.Request(base + path, json.dumps(obj).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def test_photos_footprints_and_traversal(srv):
    base, _ = srv
    photos = json.loads(_get(base, "/api/photos")[0])
    assert photos == [{"path": "sub/a.png", "w": 400, "h": 300}]
    assert _get(base, "/photo/sub/a.png")[1] == "image/png"
    with pytest.raises(urllib.error.HTTPError) as ei:
        _get(base, "/photo/..%2Fosm.json")
    assert ei.value.code == 404
    fc = json.loads(_get(base, "/api/footprints")[0])
    assert len(fc["features"]) == 2
    assert [f["properties"]["name"] for f in fc["features"]] == ["Torre Test", None]


def test_labels_round_trip(srv):
    base, root = srv
    lat, lon = _ll(500, 1500)
    lat2, lon2 = _ll(0, 800)
    body = {"photo": "sub/a.png", "image_px": [400, 300], "labels": [
        {"label": "tower", "x": 10, "y": 20, "lat": lat, "lon": lon},
        {"label": "P2", "x": 30, "y": 40, "lat": lat2, "lon": lon2}]}
    assert _post(base, "/api/labels", body)["n"] == 2
    got = json.loads(_get(base, "/api/labels?photo=sub/a.png")[0])
    assert got["region"] == "testregion" and got["image_px"] == [400, 300]
    assert got["labels"][0]["osm_names"] == ["Torre Test"]
    assert got["labels"][1]["osm_names"] == []
    assert got["labels"][0]["lat"] == pytest.approx(lat)
    assert (root / "ann" / "testregion_sub_a.json").is_file()
    assert json.loads(_get(base, "/api/labels?photo=nothing.png")[0])["labels"] == []


def test_solve(srv):
    base, _ = srv
    cam_e, cam_n, heading, f, w, h = 0.0, 0.0, 10.0, 300.0, 400, 300
    labels = []
    for i, (dd, r) in enumerate([(-12, 1500), (-5, 2000), (0, 1700), (6, 2300), (11, 1400)]):
        b = math.radians(heading + dd)
        lat, lon = _ll(cam_e + r * math.sin(b), cam_n + r * math.cos(b))
        labels.append({"label": f"p{i}", "x": w / 2 + f * math.tan(math.radians(dd)), "y": 150.0,
                       "lat": lat, "lon": lon})
    out = _post(base, "/api/solve", {"photo": "sub/a.png", "image_px": [w, h], "labels": labels})
    assert out["ok"], out
    p = out["pose"]
    assert p["heading_deg"] == pytest.approx(heading, abs=0.5)
    assert p["rms_px"] < 1.0
    assert len(out["residuals_px"]) == 5 and len(out["predicted_x"]) == 5
    assert all(abs(a - b["x"]) < 2 for a, b in zip(out["predicted_x"], labels, strict=True))
    assert [t["name"] for t in out["ticks"]] == ["Torre Test"]  # bearing ~18 deg, inside the hfov
    few = _post(base, "/api/solve", {"photo": "sub/a.png", "image_px": [w, h], "labels": labels[:3]})
    assert not few["ok"] and "4" in few["error"]


def _load():
    spec = importlib.util.spec_from_file_location("photo_label_server2", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_photo_list_dedups_and_keeps_label_pairs(tmp_path):
    mod = _load()
    d = tmp_path / "photos" / "granada_x"
    d.mkdir(parents=True)
    for name in ["photo.png", "photo.webp", "overlay.png", "overlay_baseline.png", "landmarks_v1.png",
                 "photo_depth.png", "photo_labels.png", "heights_baseline.png"]:
        Image.new("RGB", (40, 30)).save(d / name)
    (d / "photo_depth.npy").write_bytes(b"x")
    Image.new("RGB", (40, 30)).save(d / "two.webp")       # webp only: listed as is
    Image.new("RGB", (40, 30)).save(d / "three.jpg")
    Image.new("RGB", (40, 30)).save(d / "three.webp")
    cfg = mod.Config(tmp_path / "photos", tmp_path / "osm.json", tmp_path / "ann", "r", dem_path=None)
    got = [p["path"] for p in mod.list_photos(cfg)]
    assert got == ["granada_x/photo.png", "granada_x/three.jpg", "granada_x/two.webp"]
    # a pair saved while the list showed photo.webp is found under photo.png (same folder+stem)
    assert mod.label_path(cfg, "granada_x/photo.webp") == mod.label_path(cfg, "granada_x/photo.png")
    (tmp_path / "osm.json").write_text(json.dumps(_overpass()), encoding="utf-8")
    lat, lon = _ll(0, 800)
    mod.api_save_labels(cfg, {"photo": "granada_x/photo.webp",
                              "labels": [{"label": "P1", "x": 1, "y": 2, "lat": lat, "lon": lon}]})
    assert [lb["label"] for lb in mod.api_get_labels(cfg, "granada_x/photo.png")["labels"]] == ["P1"]


def _flat_cfg(tmp_path, mod):
    osm = tmp_path / "osm.json"
    osm.write_text(json.dumps(_overpass()), encoding="utf-8")
    cfg = mod.Config(tmp_path / "photos", osm, tmp_path / "ann", "r", dem_path=None)
    cfg._dem = lambda lat, lon: 700.0 + 0.02 * (np.asarray(lat, float) - LAT0) * cs.M_PER_DEG_LAT  # slope north
    return cfg


def test_terrain_solve_and_project(tmp_path):
    from city2stl.skyline import photo_view as pv
    mod = _load()
    cfg = _flat_cfg(tmp_path, mod)
    cam = pv.PinholeCamera(LAT0, LON0, 740.0, 10.0, -6.0, 0.0, 450.0, 800, 600)
    spots = [(-300, 1500, "P1", 0), (-100, 1400, "P2", 0), (250, 1800, "P3", 0), (700, 1600, "P4", 0),
             (500, 1500, "Torre Test", 25), (-250, 1200, "Casa", 12), (60, 2000, "P7", 0)]
    labels = []
    for e, n, name, hgt in spots:
        lat, lon = _ll(e, n)
        x, y = cam.project_ll(lat, lon, float(cfg._dem(lat, lon)) + hgt)
        labels.append({"label": name, "x": float(x), "y": float(y), "lat": lat, "lon": lon})
    out = mod.api_solve(cfg, {"image_px": [800, 600], "labels": labels})
    assert out["ok"] and out["terrain"], out
    p = out["pose"]
    assert out["terrain"]["median_px"] < 3
    assert p["heading_deg"] == pytest.approx(10.0, abs=1.5)
    assert p["pitch_deg"] == pytest.approx(-6.0, abs=1.5)
    assert len(out["residuals_m"]) == len(labels)
    assert all(h is not None for h in out["heights_m"])      # prior mode: every label has a height
    assert out["in_footprint"][4] and out["h0_m"][4] == 9.0 and not out["in_footprint"][0]
    pr = mod.api_project(cfg, {"camera": p, "image_px": [800, 600]})
    assert pr["ok"]
    names = [b["name"] for b in pr["buildings"]]
    assert "Torre Test" in names
    b = next(b for b in pr["buildings"] if b["name"] == "Torre Test")
    assert b["height_m"] == 9.0 and b["visible_px"] >= 30 and b["contours"]
    assert pr["ids_png"] and pr["width"] == 800
    dists = [x["dist_m"] for x in pr["buildings"]]
    assert dists == sorted(dists, reverse=True)
    # behind the camera: nothing
    back = dict(p, heading_deg=190.0)
    assert mod.api_project(cfg, {"camera": back, "image_px": [800, 600]})["buildings"] == []


def _prism_fc(boxes):
    feats = []
    for i, (e0, n0, e1, n1, hgt) in enumerate(boxes):
        ring = [_ll(e0, n0), _ll(e1, n0), _ll(e1, n1), _ll(e0, n1), _ll(e0, n0)]
        feats.append({"type": "Feature", "properties": {"id": i, "height": hgt},
                      "geometry": {"type": "Polygon", "coordinates": [[[lo, la] for la, lo in ring]]}})
    return {"features": feats}


def test_project_buildings_hides_occluded_and_clips_near_plane():
    from city2stl.skyline import photo_view as pv
    _load()
    import terrain_solve as ts
    cam = pv.PinholeCamera(LAT0, LON0, 705.0, 0.0, 0.0, 0.0, 600.0, 640, 480)
    dem = lambda lat, lon: np.full(np.shape(lat), 700.0)  # noqa: E731
    # same ray: a 30 m wall in front of a 10 m building directly behind it
    fc = _prism_fc([(-20, 100, 20, 120, 30.0), (-10, 200, 10, 220, 10.0)])
    both = {b["id"]: b["visible_px"] for b in ts.project_buildings(dem, fc, cam)["buildings"]}
    alone = {b["id"]: b["visible_px"] for b in ts.project_buildings(dem, _prism_fc([(-10, 200, 10, 220, 10.0)]), cam)["buildings"]}
    assert both[0] > 0 and alone[0] > 0
    assert both.get(1, 0) < 0.2 * alone[0]
    # straddles the camera plane beside the camera: no crash, clipped at the near plane
    r = ts.project_buildings(dem, _prism_fc([(35, -30, 60, 60, 20.0)]), cam)
    assert r["ids"].shape == (480, 640) and len(r["buildings"]) <= 1
    # the footprint the camera stands on would fill the frame: skipped
    assert ts.project_buildings(dem, _prism_fc([(-20, -30, 20, 60, 20.0)]), cam)["buildings"] == []


def test_roof_height_prior_recovers_camera():
    """Labels clicked on roof tops: terrain-height projection biases the camera, the prior does not."""
    from city2stl.skyline import photo_view as pv
    ts = _load().ts
    dem = lambda lat, lon: 700.0 + 0.0 * np.asarray(lat, float)  # noqa: E731
    cam = pv.PinholeCamera(LAT0, LON0, 740.0, 10.0, -6.0, 0.0, 450.0, 800, 600)
    spots = [(-300, 1500), (-100, 1400), (250, 1800), (700, 1600), (500, 1500), (-250, 1200),
             (60, 2000), (-450, 1700), (350, 1300), (0, 1650)]
    feats, labels = [], []
    for i, (e, n) in enumerate(spots):
        ring = [list(reversed(_ll(e + de, n + dn))) for de, dn in [(-15, -15), (15, -15), (15, 15), (-15, 15), (-15, -15)]]
        feats.append({"type": "Feature", "properties": {"id": i, "name": None, "height": "20"},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})
        lat, lon = _ll(e, n)
        x, y = cam.project_ll(lat, lon, 720.0)  # a 20 m roof on 700 m terrain
        labels.append({"label": f"P{i + 1}", "x": float(x), "y": float(y), "lat": lat, "lon": lon})
    fc = {"type": "FeatureCollection", "features": feats}
    seed = {"lat": LAT0, "lon": LON0, "heading_deg": 10.0, "f_px": 450.0}
    ground = ts.solve_terrain(dem, labels, 800, 600, seed, footprints=fc, label_height="ground")
    prior = ts.solve_terrain(dem, labels, 800, 600, seed, footprints=fc)
    assert all(h is None for h in ground["heights_m"])
    assert all(prior["in_footprint"]) and prior["h0_m"] == [20.0] * len(labels)
    assert prior["heights_m"] == pytest.approx([20.0] * len(labels), abs=2.5)
    ge = abs(ground["camera"]["elev_m"] - 740.0) + abs(ground["camera"]["pitch_deg"] + 6.0)
    pe = abs(prior["camera"]["elev_m"] - 740.0) + abs(prior["camera"]["pitch_deg"] + 6.0)
    assert prior["camera"]["elev_m"] == pytest.approx(740.0, abs=6.0)
    assert prior["camera"]["pitch_deg"] == pytest.approx(-6.0, abs=1.0)
    assert pe < 0.5 * ge


def test_building_height_tags():
    ts = _load().ts
    assert ts.building_height({"height": "12 m"}) == 12.0
    assert ts.building_height({"levels": "4"}) == 12.0
    assert ts.building_height({}) == 9.0



def test_geojson_footprints_keep_only_measured_heights():
    """A pipeline buildings.geojson (Cartagena) loads like Overpass; filler heights are dropped."""
    sq = [[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0]]
    data = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [sq]},
         "properties": {"name": "Torre", "height_m": 30.0, "height_source": "osm_height"}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [sq]},
         "properties": {"height_m": 9.0, "height_source": "default"}},
        {"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": [[sq], [sq]]},
         "properties": {"height_m": None}}]}
    fc = _load().geojson_footprints(data)
    assert len(fc["features"]) == 4
    assert [f["properties"]["height"] for f in fc["features"]] == [30.0, None, None, None]
    assert fc["features"][0]["properties"]["name"] == "Torre"


def test_overpass_building_relations_stitched():
    """A building multipolygon whose outer ring is split over two ways (one reversed) becomes one
    footprint; the inner ring and a non-building relation are left out."""
    pts = {1: (0, 0), 2: (1, 0), 3: (1, 1), 4: (0, 1), 5: (.4, .4), 6: (.6, .4), 7: (.5, .6)}
    els = [{"type": "node", "id": i, "lon": x * 1e-3, "lat": y * 1e-3} for i, (x, y) in pts.items()]
    els += [{"type": "way", "id": 10, "nodes": [1, 2, 3]}, {"type": "way", "id": 11, "nodes": [1, 4, 3]},
            {"type": "way", "id": 12, "nodes": [5, 6, 7, 5]},
            {"type": "relation", "id": 50, "tags": {"type": "multipolygon", "building": "yes", "name": "Palacio"},
             "members": [{"type": "way", "ref": 10, "role": "outer"}, {"type": "way", "ref": 11, "role": "outer"},
                         {"type": "way", "ref": 12, "role": "inner"}]},
            {"type": "relation", "id": 51, "tags": {"type": "multipolygon", "landuse": "grass"},
             "members": [{"type": "way", "ref": 12, "role": "outer"}]}]
    fc = _load().overpass_to_geojson({"elements": els})
    assert [f["properties"]["id"] for f in fc["features"]] == ["r50"]
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert len(ring) == 5 and ring[0] == ring[-1] and fc["features"][0]["properties"]["name"] == "Palacio"

# ---------- Google 3D Tiles proxy (upstream faked; the network is never touched) ----------

FAKE_KEY = "FAKE-KEY-0123456789"


class _Resp:
    def __init__(self, body: bytes, ctype: str, status: int = 200):
        self._b, self.status = body, status
        self.headers = {"Content-Type": ctype}

    def read(self, n=-1):
        out, self._b = (self._b, b"") if n < 0 else (self._b[:n], self._b[n:])
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def tiles(tmp_path, monkeypatch):
    """Server module + a server whose Config holds FAKE_KEY; `calls` records upstream URLs."""
    spec = importlib.util.spec_from_file_location("photo_label_server_t", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls, reply = [], {}

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if isinstance(reply["r"], Exception):
            raise reply["r"]
        return reply["r"]

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    logs = []
    monkeypatch.setattr(mod.LabelHandler, "log_message", lambda self, fmt, *a: logs.append(fmt % a))

    def start(key):
        cfg = mod.Config(tmp_path / "p", tmp_path / "o.json", tmp_path / "a", api_key=key)
        httpd = mod.make_server(cfg, "127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}", cfg

    started = []
    yield start, calls, reply, logs
    for h in started:
        h.shutdown()
        h.server_close()


def _fetch(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, r.read(), r.headers.get_content_type()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get_content_type()


def test_tiles_rejects_paths_outside_3dtiles(tiles):
    start, calls, reply, _ = tiles
    base, _ = start(FAKE_KEY)
    for bad in ["/tiles3d/v1/other/root.json", "/tiles3d/v1/3dtiles/../geocode", "/tiles3d/v1/3dtiles/%2e%2e/x",
                "/tiles3d/maps/api/js", "/tiles3d/"]:
        assert _fetch(base, bad)[0] == 404, bad
    assert calls == []


def test_tiles_injects_key_and_strips_client_key(tiles):
    start, calls, reply, logs = tiles
    reply["r"] = _Resp(b"glbdata", "model/gltf-binary")
    base, _ = start(FAKE_KEY)
    code, body, ctype = _fetch(base, "/tiles3d/v1/3dtiles/datasets/a/files/b.glb?session=S1&key=EVIL&KEY=EVIL2")
    assert (code, body, ctype) == (200, b"glbdata", "model/gltf-binary")
    assert len(calls) == 1
    assert calls[0].startswith("https://tile.googleapis.com/v1/3dtiles/datasets/a/files/b.glb?")
    assert "session=S1" in calls[0] and f"key={FAKE_KEY}" in calls[0]
    assert "EVIL" not in calls[0] and calls[0].count("key=") == 1
    assert not any(FAKE_KEY in line for line in logs)


def test_tiles_503_without_key(tiles):
    start, calls, _, _ = tiles
    base, _ = start("")
    code, body, _ = _fetch(base, "/tiles3d/v1/3dtiles/root.json")
    assert code == 503 and b"GOOGLE_MAPS_API_KEY" in body
    cfg = json.loads(_get(base, "/api/config")[0])   # no `view` set: the page gets the DEM centre
    assert cfg["tiles3d"] is False and cfg["region"] == "granada" and cfg["view"]["lat"] > 37
    assert calls == []


def test_tiles_json_uris_rewritten_and_key_never_returned(tiles):
    start, calls, reply, logs = tiles
    doc = {"root": {"content": {"uri": f"/v1/3dtiles/datasets/x/files/c.json?session=SESS&key={FAKE_KEY}"},
                    "children": [{"content": {"uri": "https://tile.googleapis.com/v1/3dtiles/datasets/y/f.glb"}},
                                 {"content": {"uri": "rel/z.glb"}}]},
           "note": f"echo {FAKE_KEY}"}
    reply["r"] = _Resp(json.dumps(doc).encode(), "application/json")
    base, cfg = start(FAKE_KEY)
    code, body, _ = _fetch(base, "/tiles3d/v1/3dtiles/root.json")
    assert code == 200
    assert FAKE_KEY.encode() not in body
    out = json.loads(body)
    assert out["root"]["content"]["uri"] == "/tiles3d/v1/3dtiles/datasets/x/files/c.json?session=SESS"
    assert out["root"]["children"][0]["content"]["uri"] == "/tiles3d/v1/3dtiles/datasets/y/f.glb"
    assert out["root"]["children"][1]["content"]["uri"] == "rel/z.glb"
    assert cfg.session == "SESS"
    # a later tile request without a session gets the remembered one
    reply["r"] = _Resp(b"x", "application/octet-stream")
    _fetch(base, "/tiles3d/v1/3dtiles/datasets/y/f.glb")
    assert "session=SESS" in calls[-1]
    assert not any(FAKE_KEY in line for line in logs)


def test_tiles_upstream_error_forwarded_without_key(tiles):
    import io
    start, _, reply, _ = tiles
    hdrs = email.message.Message()
    hdrs["Content-Type"] = "application/json"
    reply["r"] = urllib.error.HTTPError("https://x", 403, "Forbidden", hdrs,
                                        io.BytesIO(f'{{"error":"bad {FAKE_KEY}"}}'.encode()))
    base, _ = start(FAKE_KEY)
    code, body, _ = _fetch(base, "/tiles3d/v1/3dtiles/root.json")
    assert code == 403 and FAKE_KEY.encode() not in body
    reply["r"] = urllib.error.URLError(f"cannot reach https://x?key={FAKE_KEY}")
    code, body, _ = _fetch(base, "/tiles3d/v1/3dtiles/root.json")
    assert code == 502 and FAKE_KEY.encode() not in body


def test_labels_keep_height(srv):
    base, _ = srv
    lat, lon = _ll(0, 800)
    body = {"photo": "sub/a.png", "image_px": [400, 300], "labels": [
        {"label": "a", "x": 1, "y": 2, "lat": lat, "lon": lon, "h_ellipsoid_m": 812.5},
        {"label": "b", "x": 3, "y": 4, "lat": lat, "lon": lon}]}
    _post(base, "/api/labels", body)
    got = json.loads(_get(base, "/api/labels?photo=sub/a.png")[0])["labels"]
    assert got[0]["h_ellipsoid_m"] == 812.5 and "h_ellipsoid_m" not in got[1]


def _module():
    spec = importlib.util.spec_from_file_location("photo_label_server_pool", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_local_connections_are_kept_alive(srv):
    """Two requests on one connection: the page fetches ~1,000 tiles per 3D view."""
    import http.client
    from urllib.parse import urlparse
    u = urlparse(srv[0])
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    for _ in range(2):
        conn.request("GET", "/api/regions")
        r = conn.getresponse()
        assert r.status == 200 and r.getheader("Connection") != "close"
        r.read()
    conn.close()


class _FakeResp:
    status, reason, headers = 200, "OK", {}

    def __init__(self, finished):
        self.finished, self.closed = finished, False

    def read(self, *a):
        return b""

    def isclosed(self):
        return self.finished

    def close(self):
        self.closed = True


class _FakeConn:
    closed = False

    def close(self):
        self.closed = True


def test_pooled_response_returns_only_finished_connections():
    mod = _module()
    done, half = _FakeConn(), _FakeConn()
    with mod._PooledResponse(_FakeResp(True), done, "h"):
        pass
    with mod._PooledResponse(_FakeResp(False), half, "h"):
        pass
    assert mod._idle["h"] == [done] and not done.closed
    assert half.closed


def test_loopback_v6_only_beside_ipv4_loopback():
    mod = _module()

    class _Srv:
        server_address = ("0.0.0.0", 1)
        RequestHandlerClass = object

    assert mod.loopback_v6(_Srv()) is None
