"""tools/photo_label/server.py start guesses: photo_hints, source tagging, cache, pano seed, landmarks."""
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

SERVER = Path(__file__).resolve().parents[1] / "tools" / "photo_label" / "server.py"

LMS = [
    {"name": "La Popa", "aliases": ["popa", "cerro de la popa", "la popa"], "lat": 10.4202, "lon": -75.5258},
    {"name": "Castillo San Felipe", "aliases": ["san felipe", "castillo"], "lat": 10.4227, "lon": -75.5394},
    {"name": "Mirador San Nicolás", "aliases": ["san nicolas", "san nicolás", "mirador"], "lat": 37.181, "lon": -3.5926},
]


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("photo_label_server_g", SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _photo(tmp_path, credit=None, name="photo.jpg", size=(64, 32)):
    d = tmp_path / "photos" / "slug"
    d.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "gray").save(d / name)
    if credit is not None:
        (d / "credit.json").write_text(json.dumps(credit), encoding="utf-8")
    return d / name


def test_hints_desde_and_subject(mod, tmp_path):
    p = _photo(tmp_path, {"title": "Castillo San Felipe de Barajas desde el Cerro la Popa", "gps": None})
    h = mod.photo_hints(p, LMS)
    assert h["viewpoint"]["name"] == "La Popa" and h["gps"] is None
    assert h["subject"]["name"] == "Castillo San Felipe"
    assert 240 < h["subject"]["bearing_deg"] < 300     # castle is west of La Popa


def test_hints_from_phrase_after_subject(mod, tmp_path):
    p = _photo(tmp_path, {"title": "Castillo San Felipe ... from La Popa"})
    h = mod.photo_hints(p, LMS)
    assert h["viewpoint"]["name"] == "La Popa" and h["subject"]["name"] == "Castillo San Felipe"


def test_hints_viewpoint_only_and_no_match(mod, tmp_path):
    h = mod.photo_hints(_photo(tmp_path, {"title": "Panorama from La Popa"}), LMS)
    assert h["viewpoint"]["name"] == "La Popa" and h["subject"] is None
    h = mod.photo_hints(_photo(tmp_path, {"title": "Beach at sunset"}), LMS)
    assert h["viewpoint"] is None and h["subject"] is None and h["title"] == "Beach at sunset"
    # a lone landmark without a from-phrase is a subject, never a viewpoint
    h = mod.photo_hints(_photo(tmp_path, {"title": "La Popa at dusk"}), LMS)
    assert h["viewpoint"] is None and h["subject"]["name"] == "La Popa" and h["subject"]["bearing_deg"] is None


def test_hints_accents_and_gps(mod, tmp_path):
    p = _photo(tmp_path, {"title": "VISTA DESDE EL MIRADOR SAN NICOLAS", "gps": ["37.1805", "-3.5920"]})
    h = mod.photo_hints(p, LMS)
    assert h["viewpoint"]["name"] == "Mirador San Nicolás"
    assert h["gps"] == pytest.approx([37.1805, -3.592])
    p = _photo(tmp_path, {"title": "x", "gps": ["10°25'12\" N", "75°33'0\" W"]})
    assert mod.photo_hints(p, LMS)["gps"] == pytest.approx([10.42, -75.55])
    h = mod.photo_hints(_photo(tmp_path, {"title": "Castillo", "gps": [10.0, -75.0]}), LMS)
    assert h["gps"] == [10.0, -75.0] and h["subject"]["bearing_deg"] is not None   # bearing from gps


def test_hints_no_credit(mod, tmp_path):
    h = mod.photo_hints(_photo(tmp_path), LMS)
    assert h == {"gps": None, "title": None, "viewpoint": None, "subject": None}


def test_tag_candidates(mod):
    seeds = [(10.42, -75.52, "here", "Searched here"), (10.43, -75.55, "title", "Title: from La Popa"),
             (10.40, -75.50, "viewpoint", "Footprint viewpoint #2")]
    cands = [{"lat": 10.4301, "lon": -75.5502, "source": "viewpoint"},
             {"lat": 10.4001, "lon": -75.5001, "source": "viewpoint"},
             {"lat": 10.5, "lon": -75.4, "source": "grid"},
             {"lat": 10.51, "lon": -75.4, "source": "grid"}]
    mod._tag_candidates(cands, seeds)
    assert [(c["source"], c["label"]) for c in cands] == [
        ("title", "Title: from La Popa"), ("viewpoint", "Footprint viewpoint #2"),
        ("grid", "Skyline search #1"), ("grid", "Skyline search #2")]


@pytest.fixture
def world(mod, tmp_path, monkeypatch):
    import auto_pose
    photo = _photo(tmp_path, {"title": "Castillo San Felipe desde La Popa", "gps": ["10.41", "-75.52"]})
    cfg = mod.Config(tmp_path / "photos", tmp_path / "osm.json", tmp_path / "ann", "cart",
                     dem_path=None, api_key="", landmarks=LMS)
    cfg.dem = lambda: object()
    cfg.footprints = lambda: {}
    monkeypatch.setattr(mod, "_dem_centre", lambda dem: (10.42, -75.54))
    monkeypatch.setattr(auto_pose, "viewpoints_from_fc", lambda fc: [(10.40, -75.50)])
    calls = []

    def fake(rgb, dem, **kw):
        calls.append(kw)
        base = {"elev": 100.0, "pitch": 0.0, "roll": 0.0, "f": 50.0, "width": 64, "height": 32}
        if (kw.get("exif") or {}).get("lat") is not None:          # the pinned title-viewpoint search
            return [{**base, "lat": kw["exif"]["lat"], "lon": kw["exif"]["lon"], "heading": 280.0,
                     "score": 5.0, "source": "exif"}]
        return [{**base, "lat": 10.5, "lon": -75.3, "heading": 90.0, "score": 1.0, "source": "grid"},
                {**base, "lat": 10.4202, "lon": -75.5258, "heading": 270.0, "score": 1.1, "source": "viewpoint"},
                {**base, "lat": 10.4101, "lon": -75.5201, "heading": 0.0, "score": 3.0, "source": "viewpoint"}]
    monkeypatch.setattr(auto_pose, "candidate_poses", fake)
    return cfg, photo, calls


def test_candidates_sources_rerank_and_hints(mod, world):
    cfg, photo, calls = world
    r = mod.api_register_candidates(cfg, {"photo": "slug/photo.jpg", "top_k": 3})
    assert r["ok"] and r["cached"] is False
    assert r["hints"]["viewpoint"]["name"] == "La Popa" and r["hints"]["subject"]["name"] == "Castillo San Felipe"
    seeds = calls[0]["viewpoints"]
    assert seeds[0] == (10.4202, -75.5258) and seeds[1] == (10.41, -75.52) and seeds[2] == (10.40, -75.50)
    assert calls[0]["exif"] is None and calls[0]["top_k"] == 6           # headroom for the heading re-rank
    # the title viewpoint is searched alone as well, and that pose is pinned first despite its score
    assert calls[1]["exif"] == {"lat": 10.4202, "lon": -75.5258} and calls[1]["top_k"] == 1
    srcs = [(c["source"], c["label"]) for c in r["candidates"]]
    assert srcs[0] == ("title", "Title: from La Popa") and r["candidates"][0]["heading_deg"] == 280.0
    assert [s for s in srcs if s[0] == "title"] == [srcs[0]]           # the general search's hit there is dropped
    assert ("gps", "Photo GPS") in srcs and ("grid", "Skyline search #1") in srcs
    scores = [c["score"] for c in r["candidates"][1:]]
    assert scores == sorted(scores)
    c0 = r["candidates"][0]
    assert c0["hfov_deg"] == pytest.approx(math.degrees(2 * math.atan(32 / 50)))
    assert {"lat", "lon", "elev_m", "heading_deg", "pitch_deg", "roll_deg", "f_px", "width", "height"} <= set(c0)


def test_cache_hit_and_refresh(mod, world):
    cfg, photo, calls = world
    body = {"photo": "slug/photo.jpg", "top_k": 3}
    mod.api_register_candidates(cfg, body)
    assert (photo.parent / "photo_guesses.json").is_file()
    r = mod.api_register_candidates(cfg, body)
    assert r["cached"] is True and len(calls) == 2                       # general search + pinned title search
    r = mod.api_register_candidates(cfg, {**body, "refresh": True})
    assert r["cached"] is False and len(calls) == 4
    mod.api_register_candidates(cfg, {**body, "top_k": 2})                # different key
    assert len(calls) == 6


def test_only_viewpoints_drops_grid_and_skips_cache(mod, world):
    cfg, photo, calls = world
    body = {"photo": "slug/photo.jpg", "top_k": 3, "only_viewpoints": True, "viewpoints": [[10.4202, -75.5258]]}
    r = mod.api_register_candidates(cfg, body)
    assert calls[0]["viewpoints"] == [(10.4202, -75.5258)]
    assert all(c["source"] != "grid" for c in r["candidates"])
    assert r["candidates"][0]["source"] == "here" and r["candidates"][0]["label"] == "Searched here"
    assert not (photo.parent / "photo_guesses.json").exists()
    mod.api_register_candidates(cfg, body)
    assert len(calls) == 2


def _pano_run(tmp_path, offsets=(2.0, 4.0, 9.0), other=100.0):
    run = tmp_path / "run"
    view = run / "02_streetview" / "v1"
    view.mkdir(parents=True)
    Image.new("RGB", (64, 36), "gray").save(view / "v1_095.png")
    (view / "metadata.json").write_text(json.dumps({"location": {"lat": 10.4245, "lng": -75.554}}))
    (run / "site.json").write_text(json.dumps({"resolved_viewpoints": [
        {"name": "v1", "fov": 90.0, "image_width": 1280, "image_height": 720, "pitch": 0.0}]}))
    est = run / "04_estimates"
    est.mkdir()
    rows = [{"view_name": "v1", "heading_offset_deg": o} for o in offsets]
    rows.append({"view_name": "other", "heading_offset_deg": other})
    (est / "view_estimates.json").write_text(json.dumps(rows))
    return run, view / "v1_095.png"


def test_pano_seed(mod, tmp_path, monkeypatch):
    import auto_pose
    import numpy as np
    monkeypatch.setattr(auto_pose, "_dem_sampler", lambda dem: (lambda la, lo: np.full(np.shape(la), 30.0)))
    run, photo = _pano_run(tmp_path)
    cfg = SimpleNamespace(dem=lambda: object())
    c = mod.pano_seed(photo, cfg)
    assert c["heading"] == pytest.approx(95 + 4.0)            # median offset
    assert c["elev"] == pytest.approx(32.5)
    from PIL import Image
    w, h = Image.open(photo).size                              # the file's size wins over site.json's 1280x720
    assert (c["width"], c["height"]) == (w, h) and c["f"] == pytest.approx(w / 2)   # fov 90
    assert c["lat"] == pytest.approx(10.4245) and c["lon"] == pytest.approx(-75.554)
    (run / "04_estimates" / "view_estimates.json").unlink()
    assert mod.pano_seed(photo, cfg)["heading"] == pytest.approx(95.0)
    (run / "02_streetview" / "v1" / "metadata.json").unlink()
    assert mod.pano_seed(photo, cfg) is None
    assert mod.pano_seed(tmp_path / "photos" / "x.jpg", cfg) is None


def test_pano_short_circuits_search(mod, tmp_path, monkeypatch):
    import auto_pose
    import numpy as np
    monkeypatch.setattr(auto_pose, "_dem_sampler", lambda dem: (lambda la, lo: np.full(np.shape(la), 30.0)))
    run, photo = _pano_run(tmp_path)
    calls = []
    monkeypatch.setattr(auto_pose, "candidate_poses", lambda *a, **k: calls.append(k) or [])
    cfg = mod.Config(run / "02_streetview", tmp_path / "osm.json", tmp_path / "ann", "cart",
                     dem_path=None, api_key="")
    cfg.dem = lambda: object()
    r = mod.api_register_candidates(cfg, {"photo": "v1/v1_095.png", "top_k": 3})
    assert calls == [] and len(r["candidates"]) == 1
    c = r["candidates"][0]
    assert c["source"] == "pano" and c["label"] == "Street View pano (known pose)"
    assert c["score"] == 0.0 and c["heading_deg"] == pytest.approx(99.0)
    assert r["hints"]["viewpoint"]["lat"] == pytest.approx(10.4245)
    # explicit viewpoints bypass the shortcut
    cfg.footprints = lambda: {}
    monkeypatch.setattr(mod, "_dem_centre", lambda d: (10.4, -75.5))
    mod.api_register_candidates(cfg, {"photo": "v1/v1_095.png", "viewpoints": [[10.4, -75.5]]})
    assert len(calls) == 1


def test_regions_landmarks(mod, tmp_path):
    reg = json.loads((SERVER.parent / "regions.json").read_text(encoding="utf-8"))
    assert {lm["name"] for lm in reg["cartagena"]["landmarks"]} >= {"La Popa", "Bocagrande"}
    assert reg["granada"]["landmarks"][0]["aliases"]
    osm = tmp_path / "osm.json"
    osm.write_text("{}")
    specs = {"x": {"label": "X", "osm": str(osm), "landmarks": LMS}}
    regions = mod.Regions(specs, "x", annotations_dir=tmp_path / "a", api_key="")
    assert regions.get("x").landmarks == LMS
    assert mod.Regions({"y": {"osm": str(osm)}}, "y", annotations_dir=tmp_path / "a", api_key="").get("y").landmarks == []


# --- photo flags and gzip footprints (live server) ---

@pytest.fixture
def live(mod, tmp_path):
    import threading
    _photo(tmp_path, None, "a.jpg")
    _photo(tmp_path, None, "b.jpg")
    ring = [[-3.6, 37.1], [-3.5999991234567, 37.1], [-3.5999991234567, 37.1000007654321], [-3.6, 37.1]]
    osm = tmp_path / "osm.json"
    osm.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "T"}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]}))
    cfg = mod.Config(tmp_path / "photos", osm, tmp_path / "ann", "cart", dem_path=None, api_key="")
    httpd = mod.make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield cfg, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _req(conn, method, path, body=None, headers=None):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    conn.request(method, path, data, h)
    r = conn.getresponse()
    return r, r.read()


def test_photo_flag_hides_and_all_lists_bad(mod, live):
    import http.client
    cfg, port = live
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    def paths(raw):
        return [p["path"] for p in json.loads(raw)]
    assert paths(_req(conn, "GET", "/api/photos")[1]) == ["slug/a.jpg", "slug/b.jpg"]
    r, raw = _req(conn, "POST", "/api/photo/flag", {"photo": "slug/a.jpg", "bad": True, "reason": "blurry"})
    assert json.loads(raw) == {"ok": True, "bad": True, "count_flagged": 1}
    assert paths(_req(conn, "GET", "/api/photos")[1]) == ["slug/b.jpg"]
    allp = json.loads(_req(conn, "GET", "/api/photos?all=1")[1])
    assert allp[0]["bad"] is True and "bad" not in allp[1] and set(allp[1]) == {"path", "w", "h"}
    saved = json.loads((cfg.annotations_dir / "cart_photo_flags.json").read_text(encoding="utf-8"))
    assert saved["slug/a.jpg"]["reason"] == "blurry" and saved["slug/a.jpg"]["flagged"]
    assert json.loads(_req(conn, "POST", "/api/photo/flag", {"photo": "nope.jpg"})[1])["ok"] is False
    out = json.loads(_req(conn, "POST", "/api/photo/flag", {"photo": "slug/a.jpg", "bad": False})[1])
    assert out == {"ok": True, "bad": False, "count_flagged": 0}
    assert len(json.loads(_req(conn, "GET", "/api/photos")[1])) == 2
    conn.close()


def test_footprints_gzip_cached_and_rounded(mod, live):
    import gzip
    import http.client
    cfg, port = live
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    r, raw = _req(conn, "GET", "/api/footprints", headers={"Accept-Encoding": "gzip, deflate"})
    assert r.status == 200 and r.getheader("Content-Encoding") == "gzip" and r.getheader("Connection") != "close"
    fc = json.loads(gzip.decompress(raw))
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert ring[1] == [-3.599999, 37.1] and ring[2][1] == 37.100001
    r2, raw2 = _req(conn, "GET", "/api/footprints")           # same connection, no gzip accepted
    assert r2.getheader("Content-Encoding") is None and json.loads(raw2) == fc
    assert cfg.footprints_bytes() is cfg.footprints_bytes()   # serialized once
    conn.close()


def test_photo_focal_35mm_reads_exif(mod, tmp_path):
    p = _photo(tmp_path)
    assert mod.photo_focal_35mm(p) is None                       # no EXIF
    ex = Image.Exif()
    ex.get_ifd(0x8769)[41989] = 105                              # FocalLengthIn35mmFilm
    q = p.with_name("tele.jpg")
    Image.new("RGB", (64, 32), "gray").save(q, exif=ex)
    assert mod.photo_focal_35mm(q) == 105.0
    assert mod.photo_focal_35mm(tmp_path / "missing.jpg") is None
