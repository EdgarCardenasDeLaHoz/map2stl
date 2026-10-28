"""Labels carrying lat/lon (picked on a map) solve a camera without any OSM name lookup."""

import math

import pytest

from city2stl.skyline import camera_solver as cs
from city2stl.skyline.photo_localize import solve_from_labels

LAT0, LON0 = 37.17, -3.60
KX = cs.M_PER_DEG_LAT * math.cos(math.radians(LAT0))


def test_latlon_labels_solve_synthetic_camera():
    cam_e, cam_n, heading, f, width = -1500.0, -900.0, 70.0, 2800.0, 4000
    labels = []
    for i, (dd, r) in enumerate([(-18, 1500), (-8, 2400), (-1, 1800), (6, 2900), (12, 1300), (19, 2200)]):
        b = math.radians(heading + dd)
        e, n = cam_e + r * math.sin(b), cam_n + r * math.cos(b)
        x = width / 2 + f * math.tan(math.radians(dd))
        labels.append({"label": f"b{i}", "osm_names": [], "x": x, "y": 900.0,
                       "lat": LAT0 + n / cs.M_PER_DEG_LAT, "lon": LON0 + e / KX})
    ann = {"region": "t", "file": "t.png", "image_px": [width, 3000], "projection": "pinhole", "labels": labels}
    pose, chosen = solve_from_labels(ann, {})
    err = math.hypot((pose.lat - (LAT0 + cam_n / cs.M_PER_DEG_LAT)) * cs.M_PER_DEG_LAT,
                     (pose.lon - (LON0 + cam_e / KX)) * KX)
    assert err < 5
    assert pose.heading_deg == pytest.approx(heading, abs=0.5)
    assert set(chosen) == {f"b{i}" for i in range(6)}
