"""tools/photo_label/screen.py (rules) and the server's auto-screening job."""

import importlib.util
import json
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

SERVER = Path(__file__).resolve().parents[1] / "tools" / "photo_label" / "server.py"


@pytest.fixture()
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("photo_label_server_screen", SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)   # never touch a GPU in tests
    m._screen_state.clear()
    return m


def _labels(ground_frac, h=100, w=100):
    lab = np.zeros((h, w), dtype=int)               # class 0 = wall: not ground
    lab[: int(h * ground_frac)] = 6                 # road
    return lab


def test_screen_from_labels_rules(mod):
    f = mod.photo_screen.screen_from_labels
    street = f(_labels(0.5), 400, 300)
    assert street["reason"].startswith("auto: street level (ground 50%")
    pano = f(_labels(0.0), 480, 100)
    assert pano["reason"] == "auto: stitched panorama (aspect 4.8)"
    assert f(_labels(0.5), 480, 100)["reason"].startswith("auto: stitched panorama")   # aspect wins
    assert f(_labels(0.1), 400, 300)["reason"] == ""
    assert f(_labels(0.25), 400, 300)["reason"] != ""                                  # >= GROUND_MAX
    none = f(None, 400, 300)
    assert none["reason"] == "" and none["ground"] is None
    assert f(None, 400, 100)["reason"].startswith("auto: stitched panorama")


def _region(mod, tmp_path, n_ok=0):
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (400, 300), "gray").save(photos / "street.png")
    Image.new("RGB", (400, 300), "gray").save(photos / "street2.png")
    Image.new("RGB", (399, 299), "gray").save(photos / "clean.png")
    Image.new("RGB", (480, 100), "gray").save(photos / "pano.png")
    for i in range(n_ok):
        Image.new("RGB", (40, 30), "gray").save(photos / f"ok{i:02d}.png")
    osm = tmp_path / "osm.json"
    osm.write_text(json.dumps({"elements": []}), encoding="utf-8")
    return mod.Config(photos, osm, tmp_path / "ann", "reg", dem_path=None)


def _run(mod, cfg):
    mod.api_photos_screen(cfg, {})
    for _ in range(200):
        st = mod.api_photos_screen_state(cfg)
        if st["state"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("screening did not finish")


def test_job_flags_unflag_sticks_and_manual_reason_kept(mod, tmp_path, monkeypatch):
    cfg = _region(mod, tmp_path)
    monkeypatch.setattr(mod.photo_screen, "_ensure_label_map",
                        lambda rgb: _labels(0.5 if rgb.shape[0] == 300 else 0.0))
    # one photo is flagged by hand beforehand: keeps its reason
    mod.api_photo_flag(cfg, {"photo": "street.png", "bad": True, "reason": "mine"})
    st = _run(mod, cfg)
    assert st["state"] == "done" and st["total"] == 4 and st["done"] == 4
    assert sorted(st["flagged"]) == ["pano.png", "street2.png"]   # street.png was already flagged
    flags = mod.load_flags(cfg)
    assert flags["street.png"]["reason"] == "mine"
    assert flags["pano.png"]["reason"].startswith("auto: stitched panorama")
    assert flags["street2.png"]["reason"].startswith("auto: street level")
    assert "clean.png" not in flags
    assert (cfg.photo_roots[""] / "pano_screen.json").is_file()

    # unflag an auto-flagged photo; a rescan must not flag it again
    mod.api_photo_flag(cfg, {"photo": "pano.png", "bad": False})
    st = _run(mod, cfg)
    assert st["total"] == 0 and st["flagged"] == []
    assert "pano.png" not in mod.load_flags(cfg)


def test_state_shape_idle_and_no_gpu_message(mod, tmp_path, monkeypatch):
    cfg = _region(mod, tmp_path, n_ok=12)
    st = mod.api_photos_screen_state(cfg)
    assert st == {"ok": True, "state": "idle", "done": 0, "total": 0, "flagged": [], "message": ""}
    calls = []
    monkeypatch.setattr(mod.photo_screen, "_ensure_label_map", lambda rgb: calls.append(1))
    st = _run(mod, cfg)
    assert set(st) == {"ok", "state", "done", "total", "flagged", "message"}
    assert calls == []                                        # >10 pending, no CUDA: no SegFormer
    assert st["message"] == "no CUDA GPU: 15 photos screened by aspect only"
    assert st["flagged"] == ["pano.png"]
    assert not (cfg.photo_roots[""] / "clean_screen.json").exists()   # left for a later GPU run


def test_geometry_rules_and_known_pose_skips_only_aspect(mod):
    ps = mod.photo_screen
    assert ps.geometry_reason(None) == ""
    assert ps.geometry_reason({"cam_height_m": 2.5, "nearest_building_m": 200}).startswith("auto: street level (camera")
    assert ps.geometry_reason({"cam_height_m": 90.0, "nearest_building_m": 18.4}) == "auto: too close (nearest building 18 m)"
    assert ps.geometry_reason({"cam_height_m": 90.0, "nearest_building_m": 225.0}) == ""
    assert ps.geometry_reason({"cam_height_m": None, "nearest_building_m": None}) == ""
    drone = {"cam_height_m": 90.0, "nearest_building_m": 300.0}
    street = {"cam_height_m": 2.5, "nearest_building_m": 20.0}
    f = ps.screen_from_labels
    assert f(None, 400, 300, drone, True)["reason"] == ""
    assert f(None, 400, 300, street, True)["reason"].startswith("auto: street level (camera")
    assert f(None, 480, 100, drone, True)["reason"] == ""                      # known pose: no aspect rule
    assert f(None, 480, 100, drone, False)["reason"].startswith("auto: stitched panorama")
    assert f(_labels(0.5), 400, 300, drone, True)["reason"].startswith("auto: street level (ground")
    assert f(_labels(0.17), 400, 300)["reason"] == "" and f(_labels(0.18), 400, 300)["reason"] != ""


def test_measure_pose_counts_visible_and_nearest(mod, monkeypatch):
    ps = mod.photo_screen
    big, small = 0.01 * 100 * 100, 1
    fake = {"buildings": [{"visible_px": big, "edge_m": 80.0}, {"visible_px": big, "edge_m": 30.0},
                          {"visible_px": small, "edge_m": 5.0}], "ids": np.ones((100, 100), np.uint16)}
    seen = {}

    def project(dem, fc, cam, **kw):
        seen.update(kw)
        return fake

    import terrain_solve
    monkeypatch.setattr(terrain_solve, "project_buildings", project)
    cam = type("C", (), {"lat": 1.0, "lon": 2.0, "elev_m": 12.0, "width": 100, "height": 100})()
    out = ps.measure_pose(lambda la, lo: 10.0, {"features": []}, cam)
    assert out["cam_height_m"] == pytest.approx(2.0)
    assert out["proj_buildings"] == 2 and out["nearest_building_m"] == 30.0     # the 1 px sliver is ignored
    assert seen["min_dist_m"] == 0.0


def test_measure_depth_metrics_synthetic(mod):
    ps = mod.photo_screen
    h = w = 100
    lab = np.zeros((h, w), int)
    lab[:40] = ps.SKY
    lab[40:70] = 1                                  # building below the sky
    lab[70:] = 6                                    # road
    far = np.full((h, w), 0.2, np.float32)          # all far
    far[70:] = 1.0
    m = ps.measure(None, lab, far)
    assert m["ground"] == pytest.approx(0.3) and m["skyline_frac"] == 1.0
    assert m["seg_buildings"] == 1 and m["near_block_frac"] == 0.0
    near = np.full((h, w), 1.0, np.float32)         # the building is the nearest thing
    near[:40] = 0.0
    m = ps.measure(None, lab, near)
    assert m["near_block_frac"] == pytest.approx(0.3) and m["skyline_frac"] == 0.0 and m["seg_buildings"] == 0
    assert ps.measure(None, lab, None)["near_block_frac"] is None


def test_job_known_pose_skips_aspect_but_gets_geometry(mod, tmp_path, monkeypatch):
    cfg = _region(mod, tmp_path)
    monkeypatch.setattr(mod.photo_screen, "_ensure_label_map", lambda rgb: _labels(0.0))
    poses = {"pano": (True, {"cam_height_m": 90.0, "nearest_building_m": 400.0}),     # known, fine, wide
             "street": (True, {"cam_height_m": 2.5, "nearest_building_m": 12.0}),
             "street2": (True, {"cam_height_m": 90.0, "nearest_building_m": 20.0})}
    monkeypatch.setattr(mod, "_pose_geometry", lambda p, c: poses.get(p.stem, (False, None)))
    st = _run(mod, cfg)
    flags = mod.load_flags(cfg)
    assert sorted(st["flagged"]) == ["street.png", "street2.png"]
    assert flags["street.png"]["reason"] == "auto: street level (camera 2 m)"
    assert flags["street2.png"]["reason"] == "auto: too close (nearest building 20 m)"
    assert "pano.png" not in flags and "clean.png" not in flags
