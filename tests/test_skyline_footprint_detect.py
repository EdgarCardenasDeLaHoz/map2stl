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


def test_ground_map_draws_roads_parks_and_the_water_around_the_camera():
    from shapely.geometry import Polygon

    shore = LineString([_ll(-3000, 200), _ll(3000, 200)])                    # land to the north
    road = LineString([_ll(-500, 400), _ll(500, 400)])
    park = Polygon([_ll(100, 500), _ll(300, 500), _ll(300, 700), _ll(100, 700)])
    g = fd.ground_map(LAT, LON, coast_lines=[shore], roads=[(road, 12.0)], green=[park],
                      half_m=1000.0, res_m=2.0)

    def at(x, y):
        return g.codes[int((g.half_m - y) / g.res_m), int((x + g.half_m) / g.res_m)]

    assert at(0, -300) == fd.G_WATER and at(0, 150) == fd.G_WATER
    assert at(0, 300) == fd.G_LAND and at(0, 400) == fd.G_ROAD and at(200, 600) == fd.G_GREEN


def test_ground_fit_recovers_the_camera_position():
    """The waterline barely fixes the position along the shore; streets and a park pin it."""
    half, res, h, true = 800.0, 2.0, 60.0, (40.0, -30.0)
    n = int(2 * half / res)
    yy, xx = np.mgrid[0:n, 0:n]
    x, y = xx * res - half + res / 2, half - yy * res - res / 2
    codes = np.full((n, n), fd.G_WATER, np.uint8)
    land = y > 120 + 0.3 * x                                   # a slanted shore
    codes[land] = fd.G_LAND
    codes[land & ((np.abs(x % 90) < 6) | (np.abs(y % 110) < 6))] = fd.G_ROAD
    codes[land & (x > 100) & (x < 220) & (y > 300) & (y < 420)] = fd.G_GREEN
    gmap = fd.GroundMap(codes, LAT, LON, half, res)
    label_of = {fd.G_WATER: WATER, fd.G_LAND: 13, fd.G_ROAD: 6, fd.G_GREEN: 9}
    labels = np.full((H, W), SKY)
    pano, _ = _pano(labels, None, offset=180.0)
    elev = np.degrees(np.arctan((H / 2.0 - np.arange(H)) / F))
    rows = np.flatnonzero(elev < -0.2)
    d = h / np.tan(np.radians(-elev[rows]))[:, None]
    b = np.radians(pano.frame_heading)[None, :]
    ci = np.clip(((true[0] + d * np.sin(b) + half) / res).astype(int), 0, n - 1)
    ri = np.clip(((half - true[1] - d * np.cos(b)) / res).astype(int), 0, n - 1)
    pano.labels[rows] = np.vectorize(label_of.get)(codes[ri, ci])
    fit = fd.fit_position_from_ground(pano, fd.PanoPose(0.0, h, 0.0, 0.0, 0), gmap, search_m=90.0)
    assert (fit.dx_m, fit.dy_m) == (pytest.approx(true[0], abs=6.0), pytest.approx(true[1], abs=6.0))
    assert fit.camera_h_m == pytest.approx(h, rel=0.05)
    assert fit.score > fit.score_at_seed


def test_a_podium_is_not_read_as_the_tower_mapped_inside_it():
    """Plaza Bocagrande: a mall footprint with a tower mapped inside it read 180 m (tag 45 m).
    The tower's columns belong to the tower; the podium is read on the rest."""
    got = _measure([("podium", 0.0, 300.0, 20.0, 40.0, True), ("tower", 0.0, 320.0, 120.0, 15.0, True)])
    assert got["podium"].height_m == pytest.approx(20.0, abs=3.0)
    assert got["tower"].height_m == pytest.approx(120.0, abs=6.0)


def test_fusion_averages_agreeing_seeds_and_outvotes_a_far_misread():
    near = {"footprint": 7, "name": "a", "height_m": 26.0, "dist_m": 600.0, "base_visible": True,
            "visible_frac": 0.9}
    far = dict(near, height_m=111.0, dist_m=1116.0)                  # read the tower behind
    close = dict(near, height_m=28.0, dist_m=700.0, base_visible=False, visible_frac=0.6)
    got = fd.fuse_heights({"seed_1": [near], "seed_5": [far], "seed_9": [close]})[7]
    assert 26.0 < got["height_m"] < 27.0 and got["disputed"] and got["used"] == ["seed_1", "seed_9"]


def test_a_tall_facade_that_drifts_farther_is_followed_to_its_roof():
    """Depth Anything lets a tall tower's facade read 15 % farther at the top than at the base;
    against the base level the stop at 0.92 cut it short (Ravello, tag 160 m, read 17 m)."""
    labels, depth, fps = _scene([("tower", 0.0, 400.0, 160.0, 10.0, True),
                                 ("behind", 0.0, 440.0, 20.0, 12.0, True)])   # 10 % farther, hidden
    col = np.flatnonzero((labels == BUILDING).any(axis=0) & (np.abs(np.arange(W) - W // 2) < 6))
    rows = np.flatnonzero(labels[:, W // 2] == BUILDING)
    ramp = np.linspace(0.85, 1.0, rows.size)                                    # top .. base
    depth[np.ix_(rows, col)] *= ramp[:, None]
    pano, depth = _pano(labels, depth, offset=180.0)
    got = {m.name: m for m in fd.measure_footprints(pano, fd.PanoPose(0.0, 60.0, 0.0, 0.0, 0), fps,
                                                    depth=depth, min_cols=4)}
    assert got["tower"].height_m == pytest.approx(160.0, abs=8.0)


def test_camera_position_found_from_the_waterline():
    """seed_4 (2026-10-05) was published ~360 m from where the drone flew. A bay with shores
    north and east pins the camera; the fit moves it back from the recorded position."""
    half, res, h, true = 4400.0, 10.0, 60.0, (-120.0, 60.0)
    n = int(2 * half / res)
    yy, xx = np.mgrid[0:n, 0:n]
    x, y = xx * res - half + res / 2, half - yy * res - res / 2
    codes = np.full((n, n), fd.G_WATER, np.uint8)
    codes[(y > 500 + 0.2 * x) | (x > 700 - 0.3 * y)] = fd.G_LAND      # north and east shores
    gmap = fd.GroundMap(codes, LAT, LON, half, res)
    labels = np.full((H, W), SKY)
    pano, _ = _pano(labels, None, offset=180.0)
    shore = gmap.shore_distances(*true)
    for col in range(W):
        d = shore[int(round(pano.frame_heading[col] / fd.BEARING_BIN_DEG)) % fd.N_BEARINGS]
        y_w = int(round(_row(-math.degrees(math.atan(h / d))))) if np.isfinite(d) else H // 2
        pano.labels[y_w:, col] = WATER
        if np.isfinite(d):
            pano.labels[int(_row(3.0)):y_w, col] = BUILDING
    pose0 = fd.fit_pose_from_waterline(pano, gmap.shore_distances(), offset_step_deg=1.0)
    fit = fd.fit_camera_position(pano, pose0, gmap, search_m=200.0, coarse_step_m=50.0,
                                 fine_half_m=40.0, fine_step_m=10.0)
    assert fit.source.startswith("waterline")
    assert fit.waterline_deg < 0.5 * fit.waterline_at_seed_deg
    assert abs(fit.dx_m - true[0]) <= 20.0 and abs(fit.dy_m - true[1]) <= 20.0
    assert fit.pose.camera_h_m == pytest.approx(h, rel=0.15)


def test_refine_on_outline_recovers_a_bearing_error():
    """A drone pano whose bearing is 4 deg off: the OSM tower outline puts it back (2026-10-05:
    the Cartagena seeds were 4-6 deg off and footprints fell on the building beside them)."""
    from city2stl.skyline import skyline_match as sm

    rng = np.random.default_rng(3)
    verts, hs = [], []
    for _ in range(40):
        cx, cy = rng.uniform(-900, 900), rng.uniform(300, 1500)
        w = rng.uniform(15, 35)
        verts.append(np.array([[cx - w, cy - w], [cx + w, cy - w], [cx + w, cy + w], [cx - w, cy + w], [cx - w, cy - w]]))
        hs.append(rng.uniform(60, 200))
    towers = sm.Towers(10.40, -75.55, verts, np.array(hs), [f"t{i}" for i in range(40)])
    lat, lon = towers.to_ll(0.0, 0.0)
    h_cam, W, H, f_px = 50.0, 1800, 900, 1800 / (2 * np.pi)
    model = sm.predicted_outline(towers, (0.0, 0.0), h_cam=h_cam)
    true_bear = np.arange(W) * 360.0 / W
    labels = np.full((H, W), 1, dtype=np.uint8)                      # building
    for x, b in enumerate(true_bear):
        e = model[int(round(b / sm.BIN_DEG)) % sm.N_BINS]
        row = int(round(H / 2 - f_px * np.tan(np.radians(max(e, -5.0)))))
        labels[:max(row, 0), x] = fd.SKY_CLASS
    pano = fd.Pano("p", lat, lon, np.zeros((H, W, 3), np.uint8), labels, (true_bear - 4.0) % 360.0, f_px, 0.0)
    pose = fd.PanoPose(0.0, h_cam, 0.0, 0.1, W)                       # offset 0: 4 deg short
    fit = fd.refine_on_outline(pano, pose, towers, move_m=0.0)
    assert abs(fit.shift_deg - 4.0) <= 0.2 and abs(fit.pose.offset_deg - 4.0) <= 0.2
    assert fit.misfit_deg < fit.misfit_before_deg - 1.0
    ok = fd.refine_on_outline(pano, fit.pose, towers, move_m=0.0)       # already right: unchanged
    assert ok.shift_deg == 0.0


def _column(step: bool):
    """One column: sky 0-9, building 10-49, water 50-59; inverse depth halves above row 30
    when ``step`` (a farther building behind)."""
    labels = np.full(60, fd.SKY_CLASS, np.int16)
    labels[10:50] = BUILDING
    labels[50:] = 21
    depth = np.ones(60)
    if step:
        depth[10:30] = 0.5
    return labels, depth


def _run(labels, depth, inst=None):
    return fd._column_run(labels, depth, 49, np.inf, 0.7, 0.3, 8, 8, inst)


def test_a_run_inside_one_instance_goes_to_its_top_past_a_depth_step():
    labels, depth = _column(step=True)
    assert _run(labels, depth)[0] == 30                       # depth alone stops at the step
    inst = np.zeros(60, np.int32)
    inst[10:50] = 1                                          # MobileSAM: one building
    top, bottom, edge = _run(labels, depth, inst)
    assert (top, bottom, edge) == (10, 49, "sky")


def test_a_run_stops_where_the_instance_changes_and_the_depth_steps():
    labels, depth = _column(step=True)
    inst = np.zeros(60, np.int32)
    inst[30:50], inst[10:30] = 1, 2
    assert _run(labels, depth, inst)[:3] == (30, 49, "depth")


def test_a_podium_instance_in_front_does_not_cut_the_tower_short():
    """Stopping at every instance change cut towers at their podium (Cartagena seed_4,
    Portomarine 180 -> 28 m): without a depth step the run continues into the next instance."""
    labels, depth = _column(step=False)
    inst = np.zeros(60, np.int32)
    inst[40:50], inst[10:40] = 1, 2
    assert _run(labels, depth, inst)[0] == 10


def test_painted_instances_let_the_smaller_mask_win():
    from city2stl.skyline.building_instances import paint

    big = np.zeros((20, 20), bool)
    big[:, :] = True
    small = np.zeros((20, 20), bool)
    small[5:15, 5:15] = True
    lab = paint([(0.9, small), (0.95, big)], (20, 20), min_px=1)
    assert lab[10, 10] != lab[0, 0] and lab[10, 10] > 0 and lab[0, 0] > 0


def test_building_instances_is_none_without_mobilesam(monkeypatch):
    from city2stl.skyline import building_instances as bi
    from city2stl.skyline._core import segmentation as sg

    monkeypatch.setattr(sg, "_ensure_mobilesam", lambda: False)
    assert bi.building_instances(np.zeros((8, 8, 3), np.uint8), np.ones((8, 8), bool)) is None
