"""photo-label tool: pano-pipeline seed frames (03_seeds) and panoview manifest frames."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

SERVER = Path(__file__).resolve().parents[1] / "tools" / "photo_label" / "server.py"


@pytest.fixture()
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("photo_label_server_seeds", SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    import auto_pose
    monkeypatch.setattr(auto_pose, "_dem_sampler", lambda dem: (lambda la, lo: np.full(np.shape(la), 30.0)))
    m._screen_state.clear()
    return m


def _seed(tmp_path, pose=None):
    d = tmp_path / "03_seeds" / "seed_7"
    d.mkdir(parents=True)
    for n in ("7_030.png", "7_060.png", "pano.png", "pano_seg.png", "7_030_mask.png", "7_030_seg.png"):
        Image.new("RGB", (64, 54), "gray").save(d / n)
    (d / "pano.json").write_text("{}")
    (d / "views.json").write_text(json.dumps([
        {"file": "7_030.png", "heading_deg": 30.0, "heading_raw_deg": 25.0, "pitch_deg": 5.0, "fov_deg": 90.0, "w": 64, "h": 54},
        {"file": "7_060.png", "heading_deg": 60.0, "heading_raw_deg": 55.0, "pitch_deg": 0.0, "fov_deg": 90.0, "w": 64, "h": 54}]))
    base = {"seed": 7, "pano_id": "x", "lat": 10.42, "lon": -75.55, "horizon_tilt_deg": 1.5}
    (d / "pose.json").write_text(json.dumps({**base, **(pose or {"ground_m": 4.0, "cam_height_m": 2.0})}))
    return d


def test_listing_skips_strip_masks_json(mod, tmp_path):
    _seed(tmp_path)
    cfg = SimpleNamespace(photo_roots={"seeds": tmp_path / "03_seeds"})
    names = [p.name for _, _, p in mod._photo_files(cfg)]
    assert names == ["7_030.png", "7_060.png"]


def test_seed_frame_pose(mod, tmp_path):
    d = _seed(tmp_path)
    cfg = SimpleNamespace(dem=lambda: None)
    c = mod.pano_seed(d / "7_030.png", cfg)
    assert c["heading"] == pytest.approx(30.0) and c["pitch"] == pytest.approx(5.0)
    assert c["roll"] == pytest.approx(1.5) and c["f"] == pytest.approx(32.0)
    assert c["elev"] == pytest.approx(6.0) and c["source"] == "pano_anchor" and c["elev_fitted"] is False
    assert (c["width"], c["height"]) == (64, 54)


def test_seed_elevation_from_dem_and_drone(mod, tmp_path):
    d = _seed(tmp_path, pose={"x": 1})
    c = mod.pano_seed(d / "7_060.png", SimpleNamespace(dem=lambda: object()))
    assert c["elev"] == pytest.approx(30.0 + mod.PANO_CAM_HEIGHT_M)
    assert mod.pano_seed(d / "7_060.png", SimpleNamespace(dem=lambda: None)) is None
    (d / "pose.json").write_text(json.dumps({"seed": 7, "lat": 10.4, "lon": -75.5, "cam_elev_m": 120.0,
                                              "ground_m": 4.0, "drone": True}))
    c = mod.pano_seed(d / "7_060.png", SimpleNamespace(dem=lambda: None))
    assert c["elev"] == pytest.approx(120.0) and c["elev_fitted"] is True      # not snapped to ground


def test_panoview_pose_and_candidates(mod, tmp_path):
    img = tmp_path / "pv" / "images"
    img.mkdir(parents=True)
    Image.new("RGB", (64, 64), "gray").save(img / "1_a.jpg")
    (img.parent / "manifest.json").write_text(json.dumps({"views": [
        {"name": "a", "lat": 37.17, "lon": -3.59, "heading": 353.9, "pitch": 10, "fov": 90, "file": "images/1_a.jpg"}]}))
    cfg = SimpleNamespace(dem=lambda: object())
    c = mod.pano_seed(img / "1_a.jpg", cfg)
    assert c["heading"] == pytest.approx(353.9) and c["pitch"] == pytest.approx(10.0)
    assert c["f"] == pytest.approx(32.0) and c["elev"] == pytest.approx(32.5) and c["source"] == "pano_anchor"
    Image.new("RGB", (64, 64), "gray").save(img / "2_b.jpg")
    assert mod.pano_seed(img / "2_b.jpg", cfg) is None                          # not in the manifest


def test_screen_skips_known_pose(mod, tmp_path, monkeypatch):
    d = _seed(tmp_path)
    Image.new("RGB", (480, 100), "gray").save(d / "7_090.png")                 # strip-like, but no pose entry
    cfg = SimpleNamespace(region="r", dem=lambda: None, photo_roots={"seeds": tmp_path / "03_seeds"})
    seen = []
    monkeypatch.setattr(mod.photo_screen, "screen_photo", lambda p, labels=True: seen.append(p.name) or {"reason": "auto: x"})
    flagged = []
    monkeypatch.setattr(mod, "_set_flag", lambda cfg, rel, bad, reason="", keep=False: flagged.append(rel))
    monkeypatch.setattr(mod, "load_flags", lambda cfg: {})
    mod._screen_state["r"] = {"state": "running", "done": 0, "total": 0, "flagged": [], "message": ""}
    mod._screen_region(cfg)
    assert seen == ["7_090.png"] and flagged == ["seeds/seed_7/7_090.png"]
