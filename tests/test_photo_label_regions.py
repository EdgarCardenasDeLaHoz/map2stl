"""tools/photo_label/server.py with several regions: routing, multi-root photos, labels, camera."""

import importlib.util
import json
import math
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from city2stl.skyline import camera_solver as cs

SERVER = Path(__file__).resolve().parents[1] / "tools" / "photo_label" / "server.py"
LAT0, LON0 = 37.17, -3.60
KX = cs.M_PER_DEG_LAT * math.cos(math.radians(LAT0))


def _overpass(name):
    els, ids = [], []
    for i, (de, dn) in enumerate([(-20, -20), (20, -20), (20, 20), (-20, 20)], 1):
        els.append({"type": "node", "id": i, "lat": LAT0 + dn / cs.M_PER_DEG_LAT, "lon": LON0 + de / KX})
        ids.append(i)
    els.append({"type": "way", "id": 100, "nodes": ids + [1], "tags": {"building": "yes", "name": name}})
    return {"elements": els}


def _png(path, size=(40, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "gray").save(path)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("photo_label_server_r", SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def world(mod, tmp_path_factory):
    """Region `alpha` (unkeyed photo root, like Granada) and `beta` (two keyed roots), plus one
    region whose OSM file is missing and so must be skipped."""
    root = tmp_path_factory.mktemp("regions")
    _png(root / "alpha_photos" / "sub" / "a.png")
    _png(root / "beta_a" / "x" / "b.png")
    _png(root / "beta_b" / "c.jpg")
    _png(root / "beta_b" / "c_depth.png")
    (root / "alpha.json").write_text(json.dumps(_overpass("Alpha Tower")), encoding="utf-8")
    (root / "beta.json").write_text(json.dumps(_overpass("Beta Tower")), encoding="utf-8")
    secret = root / "secret"
    _png(secret / "s.png")                       # outside every root, beside the roots' parent
    specs = {
        "alpha": {"label": "Alpha", "osm": str(root / "alpha.json"), "dem": "",
                  "photos": {"": str(root / "alpha_photos")}, "view": {"lat": 1.5, "lon": 2.5, "ground_m": 10}},
        "beta": {"label": "Beta", "osm": str(root / "beta.json"), "dem": "",
                 "photos": {"one": str(root / "beta_a"), "two": str(root / "beta_b"),
                            "gone": str(root / "nope")}},
        "ghost": {"label": "Ghost", "osm": str(root / "missing.json"), "dem": "", "photos": {}},
    }
    reg = mod.Regions(specs, "alpha", annotations_dir=root / "ann", api_key="")
    httpd = mod.make_server(reg, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root, reg
    httpd.shutdown()
    httpd.server_close()


def _get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(base, path, obj):
    req = urllib.request.Request(base + path, json.dumps(obj).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_regions_listing_skips_missing_osm(world):
    base, _, reg = world
    code, body = _get(base, "/api/regions")
    got = json.loads(body)
    assert code == 200 and got["default"] == "alpha"
    assert got["regions"] == [{"id": "alpha", "label": "Alpha", "photos": 1},
                              {"id": "beta", "label": "Beta", "photos": 2}]
    assert reg.ids() == ["alpha", "beta"]


def test_routing_photos_footprints_config(world):
    base, _, _ = world
    assert [p["path"] for p in json.loads(_get(base, "/api/photos")[1])] == ["sub/a.png"]       # default
    assert [p["path"] for p in json.loads(_get(base, "/api/photos?region=alpha")[1])] == ["sub/a.png"]
    assert [p["path"] for p in json.loads(_get(base, "/api/photos?region=beta")[1])] == ["one/x/b.png", "two/c.jpg"]
    names = lambda r: [f["properties"]["name"] for f in json.loads(_get(base, f"/api/footprints?region={r}")[1])["features"]]  # noqa: E731
    assert names("alpha") == ["Alpha Tower"] and names("beta") == ["Beta Tower"]
    cfg = json.loads(_get(base, "/api/config?region=alpha")[1])
    assert cfg["region"] == "alpha" and cfg["view"] == {"lat": 1.5, "lon": 2.5, "ground_m": 10}
    cfg = json.loads(_get(base, "/api/config?region=beta")[1])
    assert cfg["region"] == "beta" and cfg["view"] is None            # no view, no DEM


def test_photo_bytes_follow_the_region(world):
    base, _, _ = world
    assert _get(base, "/photo/sub/a.png?region=alpha")[0] == 200
    assert _get(base, "/photo/one/x/b.png?region=beta")[0] == 200
    assert _get(base, "/photo/two/c.jpg?region=beta")[0] == 200
    assert _get(base, "/photo/sub/a.png?region=beta")[0] == 404        # alpha's photo, beta's roots
    assert _get(base, "/photo/one/x/b.png?region=alpha")[0] == 404


def test_unknown_region_is_a_clean_400(world):
    base, _, _ = world
    for path in ("/api/photos?region=nope", "/api/footprints?region=nope", "/api/config?region=nope",
                 "/api/labels?photo=a.png&region=nope", "/api/model/info?region=nope",
                 "/api/photos?region=ghost"):                          # skipped region is unknown too
        code, body = _get(base, path)
        assert code == 400 and json.loads(body)["ok"] is False, path
    code, body = _post(base, "/api/labels", {"region": "nope", "photo": "a.png", "labels": []})
    assert code == 400 and body["ok"] is False and "nope" in body["error"]


def test_labels_keep_region_paths(world, mod):
    base, root, reg = world
    code, out = _post(base, "/api/labels", {"photo": "sub/a.png", "image_px": [40, 30], "labels": [
        {"label": "a", "x": 1, "y": 2, "lat": LAT0, "lon": LON0}]})            # no region = default
    assert code == 200 and out["ok"]
    # Unkeyed root: the file name is exactly what it was before regions existed.
    assert (root / "ann" / "alpha_sub_a.json").is_file()
    assert mod.label_path(reg.get("alpha"), "sub/a.png").name == "alpha_sub_a.json"
    code, out = _post(base, "/api/labels", {"region": "beta", "photo": "one/x/b.png", "image_px": [40, 30],
                                            "labels": []})
    assert code == 200 and (root / "ann" / "beta_one_x_b.json").is_file()
    got = json.loads(_get(base, "/api/labels?photo=sub/a.png&region=alpha")[1])
    assert got["region"] == "alpha" and len(got["labels"]) == 1 and "camera" not in got
    # Saving a photo of another region's roots fails.
    code, out = _post(base, "/api/labels", {"region": "alpha", "photo": "one/x/b.png", "image_px": [40, 30],
                                            "labels": []})
    assert code == 500 and "no such photo" in out["error"]


def test_label_camera_round_trip(world):
    base, root, _ = world
    cam = {"lat": LAT0, "lon": LON0, "elev_m": 700.0, "heading_deg": 12.0, "pitch_deg": -3.0,
           "roll_deg": 0.0, "f_px": 900.0, "hfov_deg": 40.0, "ground_m": 650.0}
    body = {"photo": "sub/a.png", "image_px": [40, 30], "labels": [], "camera": cam, "camera_source": "auto"}
    assert _post(base, "/api/labels", body)[1]["ok"]
    got = json.loads(_get(base, "/api/labels?photo=sub/a.png")[1])
    assert got["camera"] == cam and got["camera_source"] == "auto"
    body.pop("camera")
    body.pop("camera_source")
    assert _post(base, "/api/labels", body)[1]["ok"]                          # next save without a pose drops it
    got = json.loads(_get(base, "/api/labels?photo=sub/a.png")[1])
    assert "camera" not in got and got["labels"] == []
    # An old file written before the camera field existed still loads.
    old = {"region": "alpha", "file": "sub/old.png", "image_px": [40, 30], "projection": "pinhole",
           "labels": [{"label": "z", "osm_names": [], "x": 1.0, "y": 2.0, "lat": LAT0, "lon": LON0}]}
    (root / "ann" / "alpha_sub_old.json").write_text(json.dumps(old), encoding="utf-8")
    got = json.loads(_get(base, "/api/labels?photo=sub/old.png")[1])
    assert got["labels"][0]["label"] == "z" and "camera" not in got


def test_safe_photo_traversal_per_root(world, mod):
    _, root, reg = world
    cfg = reg.get("beta")
    assert mod.safe_photo(cfg, "one/x/b.png") == (root / "beta_a" / "x" / "b.png").resolve()
    for bad in ("one/../../secret/s.png", "two/../../secret/s.png", "one/../two/c.jpg", "../secret/s.png",
                "one/..", "one", "one/", "", "/etc/passwd", "one//../../secret/s.png",
                "two/c.jpg/../../../secret/s.png"):
        assert mod.safe_photo(cfg, bad) is None, bad
    alpha = reg.get("alpha")
    for bad in ("../secret/s.png", "sub/../../secret/s.png", "..", "sub"):
        assert mod.safe_photo(alpha, bad) is None, bad
    assert mod.safe_photo(alpha, "sub/a.png") is not None


def test_tiles_and_session_are_shared(world):
    _, _, reg = world
    assert reg.tiles is reg.get("alpha") and reg.get("beta").api_key == reg.tiles.api_key


def test_cli_overrides_replace_the_default_regions_entry(mod, tmp_path, monkeypatch):
    osm = tmp_path / "o.json"
    osm.write_text(json.dumps(_overpass("Only")), encoding="utf-8")
    photos = tmp_path / "ph"
    _png(photos / "p.png")
    monkeypatch.setattr(mod, "load_regions", lambda: {"alpha": {"label": "A", "osm": str(osm), "dem": "",
                                                                   "photos": {"": str(tmp_path / "other")}}})
    reg = mod.build_regions("alpha", osm=str(osm), dem="", photos=str(photos))
    cfg = reg.get("alpha")
    assert [p["path"] for p in mod.list_photos(cfg)] == ["p.png"] and cfg.dem_path is None


def test_start_view_prefers_the_regions_view(world, mod):
    _, _, reg = world
    assert mod.start_view(reg.get("alpha")) == {"lat": 1.5, "lon": 2.5, "ground_m": 10}
    assert mod.start_view(reg.get("beta")) is None
