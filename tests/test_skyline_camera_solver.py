"""F-WEB2: camera pose from identified buildings, on synthetic scenes."""

import math

import numpy as np
import pytest

from city2stl.skyline import camera_solver as cs

LAT0, LON0 = 25.77, -80.19
KX = cs.M_PER_DEG_LAT * math.cos(math.radians(LAT0))


def _ll(e, n):
    return LAT0 + n / cs.M_PER_DEG_LAT, LON0 + e / KX


def _scene(cam_en, heading_deg, f, width, projection, n=8, noise=0.0, seed=0):
    """Buildings spread 1.2-3 km in front of the camera, observed with optional pixel noise."""
    rng = np.random.default_rng(seed)
    obs = []
    for i in range(n):
        d = math.radians(rng.uniform(-0.8, 0.8) * (25 if projection == "pinhole" else 70))
        r = rng.uniform(1200, 3000)
        b = math.radians(heading_deg) + d
        e, nn = cam_en[0] + r * math.sin(b), cam_en[1] + r * math.cos(b)
        x = float(cs.project(np.array(d), f, width / 2, projection)) + rng.normal(0, noise)
        lat, lon = _ll(e, nn)
        obs.append(cs.Obs(x, lat, lon, f"b{i}"))
    return obs


def _err_m(pose, cam_en):
    lat, lon = _ll(*cam_en)
    return math.hypot((pose.lat - lat) * cs.M_PER_DEG_LAT, (pose.lon - lon) * KX)


@pytest.mark.parametrize("projection,f,width", [("pinhole", 3000.0, 4608), ("cylindrical", 1500.0, 9000)])
def test_recovers_pose_exactly(projection, f, width):
    cam = (-2500.0, 400.0)
    obs = _scene(cam, 80.0, f, width, projection)
    pose = cs.solve_pose(obs, width, projection)
    assert _err_m(pose, cam) < 5
    assert pose.heading_deg == pytest.approx(80.0, abs=0.1)
    assert pose.f_px == pytest.approx(f, rel=0.01)
    assert pose.rms_px < 0.5


def test_noise_and_uncertainty():
    cam = (-3000.0, -500.0)
    obs = _scene(cam, 60.0, 3200.0, 4608, "pinhole", n=10, noise=3.0, seed=2)
    pose = cs.solve_pose(obs, 4608)
    err = _err_m(pose, cam)
    assert err < 150
    assert pose.sigma_m > 0 and err < 4 * pose.sigma_m + 20  # the reported sigma is honest


def test_one_wrong_label_is_absorbed():
    cam = (-2500.0, 400.0)
    obs = _scene(cam, 80.0, 3000.0, 4608, "pinhole", n=9, seed=3)
    bad = obs[4]
    obs[4] = cs.Obs(bad.x_px + 600, bad.lat, bad.lon, "mislabelled")
    pose = cs.solve_pose(obs, 4608)
    assert _err_m(pose, cam) < 5
    assert pose.rejected == ("mislabelled",) and "mislabelled" not in pose.names


def test_cylindrical_panorama_breaks_the_pinhole_model():
    cam = (-2500.0, 400.0)
    obs = _scene(cam, 80.0, 1500.0, 9000, "cylindrical", seed=4)
    good = cs.solve_pose(obs, 9000, "cylindrical")
    wrong = cs.solve_pose(obs, 9000, "pinhole")
    assert good.rms_px < 0.5 < wrong.rms_px


def test_known_focal_needs_only_three():
    cam = (-2000.0, 0.0)
    obs = _scene(cam, 90.0, 3000.0, 4608, "pinhole", n=3, seed=5)
    pose = cs.solve_pose(obs, 4608, f_prior_px=3000.0)
    assert _err_m(pose, cam) < 20
    with pytest.raises(ValueError):
        cs.solve_pose(obs, 4608)


def test_hfov_formula():
    assert cs.hfov_deg(2304.0, 4608, "pinhole") == pytest.approx(90.0)
    assert cs.hfov_deg(9000 / (2 * math.pi), 9000, "cylindrical") == pytest.approx(360.0)


def test_labels_choose_the_tower_that_fits():
    """A label naming a two-tower complex is matched to the tower the pose agrees with."""
    from city2stl.skyline.photo_localize import solve_from_labels

    cam = (-2500.0, 400.0)
    obs = _scene(cam, 80.0, 3000.0, 4608, "pinhole", n=6, seed=7)
    index = {o.name: [(o.lat, o.lon)] for o in obs}
    twin = obs[2]
    index["decoy"] = [(twin.lat + 0.004, twin.lon)]           # 450 m away: the other tower
    labels = [{"label": o.name, "osm_names": [o.name], "x": o.x_px} for o in obs]
    labels[2]["osm_names"] = ["decoy", obs[2].name]
    pose, chosen = solve_from_labels({"labels": labels, "image_px": [4608, 3072]}, index)
    assert chosen[obs[2].name] == obs[2].name and _err_m(pose, cam) < 5
