"""photo-label tool: listing order, hidden seed frames, and the read-only skyline pairs of a seed strip."""
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

SERVER = Path(__file__).resolve().parents[1] / "tools" / "photo_label" / "server.py"
LAT, LON = 10.42, -75.55
KX = 111_320.0 * math.cos(math.radians(LAT))


@pytest.fixture()
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("photo_label_server_skyline", SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    import auto_pose
    monkeypatch.setattr(auto_pose, "_dem_sampler", lambda dem: (lambda la, lo: np.full(np.shape(la), 30.0)))
    return m


def _seed(root, n=7, strip=True, source="Rep/assets/pano"):
    d = root / f"seed_{n}"
    d.mkdir(parents=True)
    names = [f"{n}_030.png", f"{n}_060.png"] + (["pano.png"] if strip else [])
    for f in names:
        Image.new("RGB", (360, 54) if f == "pano.png" else (64, 54), "gray").save(d / f)
    (d / "pano.json").write_text(json.dumps({"f_px": 40.0, "pitch_deg": 2.0, "width": 360, "height": 54,
                                             "deg_per_col": 1.0, "source": source}))
    (d / "views.json").write_text("[]")
    (d / "pose.json").write_text(json.dumps({"seed": n, "lat": LAT, "lon": LON, "ground_m": 4.0, "cam_height_m": 2.0}))
    return d


def _cfg(tmp_path, roots, **kw):
    return SimpleNamespace(photo_roots=roots, region="r", annotations_dir=tmp_path / "ann", geoid_m=-6.0,
                           dem=lambda: object(), footprints=lambda: {"type": "FeatureCollection", "features": []}, **kw)


def test_order_follows_roots_and_strip_first(mod, tmp_path):
    seeds, commons = tmp_path / "03_seeds", tmp_path / "photos"
    _seed(seeds, 2), _seed(seeds, 10)
    commons.mkdir()
    Image.new("RGB", (64, 54), "gray").save(commons / "a.png")
    got = [mod.photo_rel(k, r, p) for k, r, p in mod._photo_files(_cfg(tmp_path, {"seeds": seeds, "commons": commons}))]
    assert got == ["seeds/seed_2/pano.png", "seeds/seed_2/2_030.png", "seeds/seed_2/2_060.png",
                   "seeds/seed_10/pano.png", "seeds/seed_10/10_030.png", "seeds/seed_10/10_060.png", "commons/a.png"]
    flipped = [k for k, _, _ in mod._photo_files(_cfg(tmp_path, {"commons": commons, "seeds": seeds}))]
    assert flipped[0] == "commons"                                           # the config order wins, not the key name


def test_frames_hidden_from_list_only(mod, tmp_path):
    seeds = tmp_path / "03_seeds"
    _seed(seeds, 1)
    _seed(seeds, 2, strip=False)                                             # no strip: its frames stay listed
    cfg = _cfg(tmp_path, {"seeds": seeds})
    assert [p["path"] for p in mod.list_photos(cfg)] == ["seeds/seed_1/pano.png", "seeds/seed_2/2_030.png", "seeds/seed_2/2_060.png"]
    assert mod.list_photos(cfg)[0]["projection"] == "pano_cylindrical"
    assert "1_030.png" in [p.name for _, _, p in mod._photo_files(cfg)]       # registration and screening still see it
    assert mod.safe_photo(cfg, "seeds/seed_1/1_030.png") is not None          # and it opens by path


def _report(tmp_path, monkeypatch, buildings):
    rep = tmp_path / "reports" / "Rep"
    rep.mkdir(parents=True)
    (rep / "heights.json").write_text(json.dumps({"schema_version": 3, "buildings": buildings}))
    monkeypatch.setattr(sys.modules["seeds"], "SKYLINE_REPORTS", tmp_path / "reports")


def _bld(fid, bearing_deg, dist_m, views, tier="single"):
    b = math.radians(bearing_deg)
    return {"feature_id": fid, "tier": tier, "centroid_lat": LAT + dist_m * math.cos(b) / 111_320.0,
            "centroid_lon": LON + dist_m * math.sin(b) / KX, "views": views}


def test_skyline_pairs_project_the_top(mod, tmp_path, monkeypatch):
    seeds = tmp_path / "03_seeds"
    d = _seed(seeds, 7)
    _report(tmp_path, monkeypatch, [
        _bld("b1", 90, 200, [{"view_name": "seed_7_090", "height_m": 20.0, "confidence": 0.4},
                             {"view_name": "seed_8_090", "height_m": 20.0, "confidence": 0.4}], "corroborated"),
        _bld("b2", 270, 100, [{"view_name": "seed_7_270", "height_m": 3000.0, "confidence": 0.1}]),   # top off the strip
        _bld("b3", 0, 150, [{"view_name": "auto_000_900m_000", "height_m": 9.0, "confidence": 0.1}])])  # not a seed view
    out = sys.modules["seeds"].skyline_pairs(d / "pano.png", _cfg(tmp_path, {"seeds": seeds}))
    assert [r["feature_id"] for r in out] == ["b1"]
    r = out[0]
    horizon = 27 + 40 * math.tan(math.radians(2.0))
    assert r["x"] == pytest.approx(270.0, abs=0.01)                        # bearing 90, column 0 = 180 deg
    assert r["y"] == pytest.approx(horizon - 40 * (30 + 20 - 6.0) / 200.0, abs=0.1)   # DEM 30 + 20 m, camera 4 + 2
    assert (r["source"], r["tier"], r["label"], r["auto"], r["confidence"]) == ("skyline", "corroborated", "b1", True, 0.4)
    assert r["h_ellipsoid_m"] == pytest.approx(50.0 - 6.0)
    assert set(r) >= {"label", "osm_names", "x", "y", "lat", "lon", "h_ellipsoid_m", "auto"}   # the label-file row shape
    assert sys.modules["seeds"].skyline_pairs(d / "7_030.png", _cfg(tmp_path, {"seeds": seeds})) == []         # a frame, not the strip


def test_skyline_pairs_without_report(mod, tmp_path, monkeypatch):
    seeds = tmp_path / "03_seeds"
    d = _seed(seeds, 7, source="Missing/assets/pano")
    monkeypatch.setattr(sys.modules["seeds"], "SKYLINE_REPORTS", tmp_path / "reports")
    assert sys.modules["seeds"].skyline_pairs(d / "pano.png", _cfg(tmp_path, {"seeds": seeds})) == []
    _seed(seeds, 8, source="../../etc/x")
    assert sys.modules["seeds"].skyline_pairs(seeds / "seed_8" / "pano.png", _cfg(tmp_path, {"seeds": seeds})) == []


def test_save_drops_skyline_rows(mod, tmp_path):
    seeds = tmp_path / "03_seeds"
    _seed(seeds, 7)
    cfg = _cfg(tmp_path, {"seeds": seeds})
    row = {"label": "a", "x": 1, "y": 2, "lat": LAT, "lon": LON}
    r = mod.api_save_labels(cfg, {"photo": "seeds/seed_7/pano.png", "labels": [
        row, {**row, "label": "sky", "auto": True, "source": "skyline"}, {**row, "label": "au", "auto": True}]})
    assert r["n"] == 2
    saved = json.loads(Path(r["saved"]).read_text())
    assert [x["label"] for x in saved["labels"]] == ["a", "au"]
    saved["labels"].append({**row, "label": "sky", "source": "skyline"})        # an old file that has one: not served back
    Path(r["saved"]).write_text(json.dumps(saved))
    assert [x["label"] for x in mod.api_get_labels(cfg, "seeds/seed_7/pano.png")["labels"]] == ["a", "au"]
