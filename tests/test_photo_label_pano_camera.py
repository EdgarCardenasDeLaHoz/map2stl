"""photo-label tool: the cylindrical strip camera (`tools/photo_label/pano_camera.py`) and its use in
`terrain_solve.project_buildings`. Real-seed tests skip when `runs/cartagena/03_seeds` is absent."""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "photo_label"))
import pano_camera as pc  # noqa: E402

SEEDS = ROOT / "runs" / "cartagena" / "03_seeds"
SEED_DIRS = sorted(SEEDS.glob("seed_*")) if SEEDS.is_dir() else []
real = pytest.mark.skipif(not SEED_DIRS, reason="no runs/cartagena/03_seeds")
# horizon row = H/2 + f tan(pitch), from each seed's pano.json
HORIZON = {"seed_1": 315.2, "seed_4": 311.6, "seed_5": 323.3, "seed_6": 270.0}


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def _cam(**kw):
    a = dict(lat=10.42, lon=-75.55, elev_m=20.0, pitch_deg=5.0, f_px=417.0, width=2688, height=540)
    a.update(kw)
    return pc.PanoCamera(a["lat"], a["lon"], a["elev_m"], a["pitch_deg"], a["f_px"], a["width"], a["height"],
                         360.0 / a["width"])


def test_column_is_linear_in_heading_north_mid_strip():
    cam = _cam()
    assert cam.heading_of(0) == pytest.approx(180.0) and cam.heading_of(1344) == pytest.approx(0.0, abs=1e-9) \
        or cam.heading_of(1344) == pytest.approx(360.0)
    assert cam.column_of(0.0) == pytest.approx(1344.0)
    assert cam.column_of(90.0) == pytest.approx(1344.0 + 90 / cam.deg_per_col)
    assert _wrap(cam.heading_deg) == pytest.approx(0.0, abs=0.2)


def test_round_trip_project_backproject():
    cam = _cam()
    xs, ys = np.array([5.0, 700.5, 1344.0, 2600.0]), np.array([10.0, 270.0, 400.0, 530.0])
    d = cam.backproject(xs, ys)
    assert np.allclose(np.linalg.norm(d, axis=-1), 1.0)
    bx, by = cam.project_dirs(d)
    assert np.allclose(bx, xs, atol=1e-6) and np.allclose(by, ys, atol=1e-6)


def test_horizon_row_and_elevation_sign():
    cam = _cam(pitch_deg=6.0)
    assert cam.horizon_row == pytest.approx(270 + 417.0 * math.tan(math.radians(6.0)))
    _, y_up = cam.project_dirs(np.array([[0.0, 1.0, 0.3]]))
    _, y_hz = cam.project_dirs(np.array([[0.0, 1.0, 0.0]]))
    assert y_up[0] < y_hz[0] and y_hz[0] == pytest.approx(cam.horizon_row)


def test_dict_round_trip_and_flags():
    cam = _cam()
    c = cam.to_dict()
    assert c["projection"] == "pano_cylindrical" and cam.cylindrical is True
    back = pc.PanoCamera.from_dict(c, 2688, 540)
    assert back.col0_heading_deg == 180.0 and back.f_px == cam.f_px and back.deg_per_col == cam.deg_per_col


def test_cyl_polys_draws_seam_crossing_on_both_sides():
    import terrain_solve as ts
    cam = _cam()
    sq = np.array([[-5.0, 100.0, 0.0], [5.0, 100.0, 0.0], [5.0, 100.0, 20.0], [-5.0, 100.0, 20.0]])  # due north
    (a, b, c) = ts._cyl_polys(sq[:, [0, 1, 2]] * np.array([1, -1, 1]), cam)       # due south: col 0 / W seam
    assert a[:, 0].min() < 0 or c[:, 0].max() >= cam.width
    assert np.allclose(b[:, 0] - a[:, 0], cam.width) and np.allclose(c[:, 0] - b[:, 0], cam.width)


@real
@pytest.mark.parametrize("d", SEED_DIRS, ids=lambda p: p.name)
def test_seed_frame_centres_match_views_json(d):
    cam = pc.load_seed_camera(d)
    fit = pc.fit_col0_heading(d, cam)
    assert abs(fit["col0_heading_deg"] - 180.0) < 0.3
    cam = cam.with_col0(fit["col0_heading_deg"])
    for v in json.loads((d / "views.json").read_text(encoding="utf-8")):
        got = cam.heading_of(cam.column_of(v["heading_deg"]))
        assert abs(_wrap(got - v["heading_deg"])) < 0.3


@real
@pytest.mark.parametrize("d", SEED_DIRS, ids=lambda p: p.name)
def test_seed_horizon_row_and_weak_flag(d):
    cam = pc.load_seed_camera(d)
    if d.name in HORIZON:
        assert cam.horizon_row == pytest.approx(HORIZON[d.name], abs=6.0)
    pose = json.loads((d / "pose.json").read_text(encoding="utf-8"))
    assert cam.weak == (pose.get("pose_branch") in pc.WEAK_BRANCHES)
    assert len(pc.seed_frame_cameras(d, cam)) == len(json.loads((d / "views.json").read_text(encoding="utf-8")))


@real
def test_strip_pinhole_mapping_round_trip():
    d = SEED_DIRS[0]
    cam = pc.load_seed_camera(d)
    f = pc.seed_frame_cameras(d, cam)[3]
    from city2stl.skyline.photo_view import PinholeCamera
    pin = PinholeCamera(f["lat"], f["lon"], f["elev_m"], f["heading_deg"], f["pitch_deg"], 0.0, f["f_px"],
                        f["width"], f["height"])
    sx, sy = pc.pinhole_to_strip(cam, pin, np.array([320.0]), np.array([270.0]))
    assert abs(_wrap(cam.heading_of(sx[0]) - f["heading_deg"])) < 0.3          # frame centre -> its heading column
