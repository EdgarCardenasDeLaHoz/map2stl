"""F-DET6: footprint-first detection for elevated panoramas, on synthetic scenes."""

import math

import numpy as np
import pytest
from shapely.geometry import LineString

from city2stl.skyline import footprint_detect as fd

LAT, LON = 10.40, -75.55
KX = fd.M_PER_DEG_LAT * math.cos(math.radians(LAT))
W, H, F = 1440, 400, 200.0           # 0.25 deg per column
WATER, SKY, BUILDING = 21, 2, 1


def _ll(x, y):
    return LON + x / KX, LAT + y / fd.M_PER_DEG_LAT


def _pano(labels, depth=None, offset=0.0):
    frame = (np.arange(W) * 360.0 / W - offset) % 360.0
    return fd.Pano("synthetic", LAT, LON, np.zeros((H, W, 3), np.uint8), labels.astype(np.int16),
                   frame, F, 0.0), depth


def _row(elev_deg):
    return H / 2.0 - F * math.tan(math.radians(elev_deg))


def test_shore_table_hits_a_straight_shore():
    shore = LineString([_ll(-3000, 400), _ll(3000, 400)])
    d = fd.shore_distance_table(LAT, LON, [shore])
    assert d[0] == pytest.approx(400.0, rel=1e-3)                  # due north
    assert d[int(60 / fd.BEARING_BIN_DEG)] == pytest.approx(800.0, rel=1e-3)   # 400 / cos 60
    assert np.isinf(d[int(180 / fd.BEARING_BIN_DEG)])               # south: open sea


def test_waterline_fit_recovers_heading_and_camera_height():
    h, offset = 60.0, 40.0
    shore = LineString([_ll(-3000, 400), _ll(3000, 400)])
    d = fd.shore_distance_table(LAT, LON, [shore])
    labels = np.full((H, W), SKY)
    for x in range(W):
        b = (x * 360.0 / W) % 360.0                     # geographic bearing of column x
        dist = d[int(round(b / fd.BEARING_BIN_DEG)) % fd.N_BEARINGS]
        dist = dist if dist <= 4000 else np.inf
        y_w = int(round(_row(-math.degrees(math.atan(h / dist)))))
        labels[y_w:, x] = WATER
        if np.isfinite(dist):
            labels[int(_row(5.0)):y_w, x] = BUILDING    # land and buildings beyond the shore
    pano, _ = _pano(labels, offset=offset)
    pose = fd.fit_pose_from_waterline(pano, d, offset_step_deg=0.5)
    assert pose.offset_deg == pytest.approx(offset, abs=1.0)
    assert pose.camera_h_m == pytest.approx(h, rel=0.15)
    assert abs(pose.pitch_fix_deg) < 0.3


def _square(cx, cy, half=10.0):
    return np.array([_ll(cx - half, cy - half), _ll(cx + half, cy - half), _ll(cx + half, cy + half),
                     _ll(cx - half, cy + half), _ll(cx - half, cy - half)])


def _two_towers(h_cam=60.0, near=(160.0, 50.0), far=(510.0, 150.0)):
    """A near building partly hiding a far tower due north; labels and inverse depth."""
    labels = np.full((H, W), SKY)
    depth = np.zeros((H, W))
    (dn, hn), (df, hf) = near, far
    y_base_n = _row(-math.degrees(math.atan(h_cam / (dn - 10))))
    y_top_n = _row(math.degrees(math.atan((hn - h_cam) / (dn - 10))))
    y_top_f = _row(math.degrees(math.atan((hf - h_cam) / (df - 10))))
    labels[int(y_base_n):, :] = WATER
    cols = np.arange(W // 2 - 8, W // 2 + 9)     # bearing 0 sits mid-pano (offset 180)
    for x in cols:
        labels[int(y_top_f):int(y_base_n), x] = BUILDING
        depth[int(y_top_f):int(y_top_n), x] = 1000.0 / df
        depth[int(y_top_n):int(y_base_n), x] = 1000.0 / dn
    fps = [fd.Footprint("near", _square(0, dn)), fd.Footprint("far", _square(0, df))]
    return labels, depth, fps


def test_measure_separates_near_building_from_far_tower_by_depth():
    labels, depth, fps = _two_towers()
    pano, depth = _pano(labels, depth, offset=180.0)
    pose = fd.PanoPose(0.0, 60.0, 0.0, 0.0, 0)   # geographic = frame + 0: north mid-pano
    got = {m.name: m for m in fd.measure_footprints(pano, pose, fps, depth=depth, min_cols=4)}
    assert got["near"].height_m == pytest.approx(50.0, abs=3.0)
    assert got["near"].base_visible
    assert got["far"].height_m == pytest.approx(150.0, abs=5.0)
    assert not got["far"].base_visible


def test_without_depth_the_near_building_takes_the_tower_top():
    """Why depth is needed: labels alone run the near building up into the tower behind it."""
    labels, _depth, fps = _two_towers()
    pano, _ = _pano(labels, None, offset=180.0)
    pose = fd.PanoPose(0.0, 60.0, 0.0, 0.0, 0)   # geographic = frame + 0: north mid-pano
    got = {m.name: m for m in fd.measure_footprints(pano, pose, fps, depth=None, min_cols=4)}
    assert got["near"].height_m > 75.0        # true 50 m: the tower's roofline seen from 160 m


def test_roof_below_the_horizon_gives_a_positive_height():
    labels, depth, fps = _two_towers(near=(160.0, 30.0))      # 30 m roof seen from 60 m up
    pano, depth = _pano(labels, depth, offset=180.0)
    pose = fd.PanoPose(0.0, 60.0, 0.0, 0.0, 0)   # geographic = frame + 0: north mid-pano
    near = next(m for m in fd.measure_footprints(pano, pose, fps, depth=depth, min_cols=4)
                if m.name == "near")
    assert near.height_m == pytest.approx(30.0, abs=3.0)


def _ring_at(bearing_deg, near_m, half=10.0):
    """Square footprint whose near face is ``near_m`` from the camera along ``bearing_deg``."""
    t = math.radians(bearing_deg)
    u, v = (math.sin(t), math.cos(t)), (math.cos(t), -math.sin(t))   # along, across
    c = near_m + half
    return np.array([_ll(u[0] * (c + a * half) + v[0] * b * half, u[1] * (c + a * half) + v[1] * b * half)
                     for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1))])


#: Base-visible towers east, south-east and west that calibrate the depth model.
CALIBRATION = [("cal_e", 90.0, 150.0, 40.0, 10.0, True), ("cal_se", 135.0, 250.0, 40.0, 10.0, True),
               ("cal_w", -90.0, 350.0, 40.0, 10.0, True)]


def _scene(buildings, h_cam=60.0):
    """Labels and inverse depth (1000 / distance) of square towers on water, nearer ones drawn
    over farther ones; north mid-pano. ``buildings``: (name, bearing_deg, near_m, height_m,
    half_m, mapped). Returns labels, depth and the mapped footprints."""
    labels = np.full((H, W), SKY)
    depth = np.zeros((H, W))
    elev = np.degrees(np.arctan((H / 2.0 - np.arange(H)) / F))
    below = elev < 0
    labels[below, :] = WATER
    depth[below, :] = (1000.0 * np.tan(np.radians(-elev[below])) / h_cam)[:, None]
    fps = []
    for name, bearing, near, height, half, mapped in sorted(buildings + CALIBRATION,
                                                             key=lambda b: -b[2]):
        ring = _ring_at(bearing, near, half)
        xy = fd._local(LAT, LON, ring)
        dn = float(np.hypot(xy[:, 0], xy[:, 1]).min())
        bear = np.degrees(np.arctan2(xy[:, 0], xy[:, 1]))
        c0, c1 = int(math.ceil((bear.min() + 180) / 0.25)), int(math.floor((bear.max() + 180) / 0.25))
        y0 = int(round(_row(math.degrees(math.atan((height - h_cam) / dn)))))
        y1 = int(round(_row(-math.degrees(math.atan(h_cam / dn)))))
        labels[y0:y1, c0:c1 + 1] = BUILDING
        depth[y0:y1, c0:c1 + 1] = 1000.0 / dn
        if mapped:
            fps.append(fd.Footprint(name, ring))
    return labels, depth, fps


def _measure(buildings):
    labels, depth, fps = _scene(buildings)
    pano, depth = _pano(labels, depth, offset=180.0)
    pose = fd.PanoPose(0.0, 60.0, 0.0, 0.0, 0)
    return {m.name: m for m in fd.measure_footprints(pano, pose, fps, depth=depth, min_cols=4)}


def test_tower_a_quarter_farther_is_not_read_as_the_near_roof():
    """A fixed 0.7 depth ratio only split surfaces 1.4x apart; Bocagrande's rows are 10-30 %
    apart, so the near building read the tower's top. The next footprint behind sets the stop."""
    got = _measure([("near", 0.0, 400.0, 50.0, 10.0, True), ("tower", 0.0, 500.0, 150.0, 8.0, True)])
    assert got["near"].height_m == pytest.approx(50.0, abs=4.0)
    assert got["near"].top_edge == "depth"
    assert got["tower"].height_m == pytest.approx(150.0, abs=6.0)


def test_unmapped_building_in_front_is_skipped():
    """No measured roof hides the target's base, but an unmapped building does: its pixels sit
    nearer than the target's depth, so the run starts above it."""
    got = _measure([("target", 0.0, 300.0, 120.0, 10.0, True),
                    ("unmapped", 0.0, 200.0, 60.0, 15.0, False)])
    assert got["target"].height_m == pytest.approx(120.0, abs=5.0)
    assert not got["target"].base_visible
    assert 0.3 < got["target"].visible_frac < 0.7


def test_a_sliver_above_a_nearer_roof_is_not_measured():
    got = _measure([("hidden", 0.0, 400.0, 45.0, 10.0, True), ("front", 0.0, 200.0, 50.0, 15.0, True)])
    assert got["front"].height_m == pytest.approx(50.0, abs=3.0)
    assert "hidden" not in got


def test_depth_model_ignores_outliers():
    d = np.linspace(100.0, 2000.0, 30)
    z = 120.0 / d + 0.02
    z[[3, 11, 20]] *= 1.8                       # bases hidden by something unmapped and nearer
    a, b = fd.fit_depth_model(d, z)
    assert a == pytest.approx(120.0, rel=0.02)
    assert b == pytest.approx(0.02, abs=0.003)
