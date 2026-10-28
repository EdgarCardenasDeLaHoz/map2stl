"""photo_view: a pinhole photo resampled onto the Pano grid, and the roof fit on it.

A synthetic scene (box buildings on plinths, a ground plane) is rendered into a pitched, rolled
pinhole frame with a known camera; the roof fit on the resampled pano must give the boxes'
heights back, also for the boxes near the frame edge, and with per-footprint bases when the
boxes stand at different ground levels.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from city2stl.skyline import footprint_detect as fd
from city2stl.skyline import photo_view as pv
from city2stl.skyline._pano import roof_fit

LAT0, LON0 = 37.18, -3.59
KX = fd.M_PER_DEG_LAT * math.cos(math.radians(LAT0))
GROUND, BUILDING, SKY = 6, 1, fd.SKY_CLASS


def _box(cx, cy, half, z0, z1):
    return np.array([cx - half, cy - half, z0]), np.array([cx + half, cy + half, z1])


def _render(cam: pv.PinholeCamera, boxes):
    """Label image: nearest hit of each pixel's ray among ``boxes`` ((lo, hi, label)) and the
    plane z = 0 (ground); sky where nothing is hit. Camera at local origin, height cam.elev_m."""
    right, up, fwd = cam.axes()
    ys, xs = np.mgrid[0:cam.height, 0:cam.width].astype(float)
    d = (fwd[None, None] * cam.f_px + right[None, None] * (xs - cam.width / 2.0)[..., None]
         - up[None, None] * (ys - cam.height / 2.0)[..., None])
    o = np.array([0.0, 0.0, cam.elev_m])
    best = np.full(xs.shape, np.inf)
    lab = np.full(xs.shape, SKY, np.int16)
    with np.errstate(divide="ignore", invalid="ignore"):
        tg = -o[2] / d[..., 2]
    hit = (d[..., 2] < 0) & np.isfinite(tg)
    best[hit], lab[hit] = tg[hit], GROUND
    for lo, hi, label in boxes:
        with np.errstate(divide="ignore", invalid="ignore"):
            t1 = (lo[None, None] - o) / d
            t2 = (hi[None, None] - o) / d
        tmin = np.nanmax(np.minimum(t1, t2), axis=-1)
        tmax = np.nanmin(np.maximum(t1, t2), axis=-1)
        h = (tmax >= tmin) & (tmin > 0) & (tmin < best)
        best[h], lab[h] = tmin[h], label
    return lab


def _footprint(cx, cy, half, name):
    ring = np.array([[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half],
                     [cx - half, cy + half], [cx - half, cy - half]])
    return fd.Footprint(name, np.column_stack([LON0 + ring[:, 0] / KX,
                                               LAT0 + ring[:, 1] / fd.M_PER_DEG_LAT]))


def _scene(bases, heights, bearings, dists, half=12.0):
    boxes, fps = [], []
    for i, (z0, hgt, b, d) in enumerate(zip(bases, heights, bearings, dists, strict=True)):
        cx, cy = d * math.sin(math.radians(b)), d * math.cos(math.radians(b))
        if z0 > 0:                                   # plinth: ground class up to the base
            boxes.append((*_box(cx, cy, half + 6.0, 0.0, z0), GROUND))
        boxes.append((*_box(cx, cy, half, z0, z0 + hgt), BUILDING))
        fps.append(_footprint(cx, cy, half, f"b{i}"))
    return boxes, fps


CAM = pv.PinholeCamera(LAT0, LON0, 70.0, 10.0, -14.0, 1.5, 520.0, 640, 420)


def test_projection_and_grid_agree():
    """A pano pixel's direction lands where the grid says it comes from, and the centre column
    of the pano is the photo's centre column (rows identical there at zero roll)."""
    cam = pv.PinholeCamera(LAT0, LON0, 70.0, 10.0, -14.0, 0.0, 520.0, 640, 420)
    g = pv.pano_grid(cam)
    H, W = g.shape
    assert W > 2 * math.radians(30.0) * cam.f_px        # the full heading span is covered
    c = W // 2
    assert g.frame_heading[c] == pytest.approx(cam.heading_deg)
    rows = np.arange(H)
    ok = np.isfinite(g.src_y[:, c]) & (g.src_y[:, c] >= 0) & (g.src_y[:, c] < cam.height)
    np.testing.assert_allclose(g.src_x[ok, c], cam.width / 2.0, atol=1e-6)
    np.testing.assert_allclose(g.src_y[ok, c] - cam.height / 2.0, rows[ok] - H / 2.0, atol=1e-6)
    # an off-axis world point: its pano row/column (Pano row model) maps back to its pixel
    pano = fd.Pano("t", LAT0, LON0, np.zeros((H, W, 3), np.uint8), np.zeros((H, W), np.int16),
                   g.frame_heading, cam.f_px, cam.pitch_deg)
    for bear, el in [(-20.0, -10.0), (28.0, -25.0), (35.0, -3.0)]:
        b = cam.heading_deg + bear
        x, y = cam.project_dirs(pv.dirs_of(b, el))
        col = c + math.radians(bear) * cam.f_px
        row = float(pano.row_of_elevation(el))
        i, j = int(round(row)), int(round(col))
        assert abs(g.src_x[i, j] - x) < 1.5 and abs(g.src_y[i, j] - y) < 1.5


def test_reproject_nearest_keeps_labels_and_marks_outside():
    lab = np.arange(CAM.width * CAM.height, dtype=np.int32).reshape(CAM.height, CAM.width) % 7
    g = pv.pano_grid(CAM)
    out = pv.reproject(lab, g, nearest=True, fill=-1)
    inside = g.inside(CAM.width, CAM.height)
    assert set(np.unique(out[inside])) <= set(range(7))
    assert (out[~inside] == -1).all() and (~inside).any()


def _fit(cam, fps, lab, cam_h=None, ref_h=None):
    pano, _g = pv.photo_pano("t", np.zeros((cam.height, cam.width, 3), np.uint8), lab, cam)
    pose = fd.PanoPose(0.0, cam.elev_m if ref_h is None else ref_h, 0.0, 0.0, pano.width)
    ms = roof_fit.fit_roof_heights(pano, pose, fps, cam_h=cam_h)
    return {m.footprint: m.height_m for m in ms}, pano, pose


def test_roof_fit_round_trip_flat_ground():
    """Box heights come back within 6 % on the resampled pano, edge boxes included."""
    heights = [9.0, 14.0, 22.0, 11.0, 18.0]
    bearings = [CAM.heading_deg + b for b in (-29.0, -12.0, 2.0, 16.0, 30.0)]
    dists = [180.0, 260.0, 340.0, 220.0, 300.0]
    boxes, fps = _scene([0.0] * 5, heights, bearings, dists)
    lab = _render(CAM, boxes)
    got, _p, _q = _fit(CAM, fps, lab)
    assert len(got) == 5
    for i, h in enumerate(heights):
        assert got[i] == pytest.approx(h, rel=0.06), (i, got[i], h)


def test_plain_pinhole_as_pano_is_wrong_at_the_edge():
    """Control: reading the pinhole frame as a Pano row model (no resampling) misses the
    edge box by more than the resampled pano does."""
    # +26 deg: inside the 31.6 deg half-field, where pinhole and pano columns differ by ~6 px
    heights, bearings, dists = [14.0], [CAM.heading_deg + 26.0], [200.0]
    cam = pv.PinholeCamera(LAT0, LON0, 70.0, 10.0, -14.0, 0.0, 520.0, 640, 420)
    boxes, fps = _scene([0.0], heights, bearings, dists)
    lab = _render(cam, boxes)
    good, _p, _q = _fit(cam, fps, lab)
    cols = np.arange(cam.width) - cam.width / 2.0
    naive = fd.Pano("n", LAT0, LON0, np.zeros((cam.height, cam.width, 3), np.uint8), lab,
                    (cam.heading_deg + np.degrees(np.arctan(cols / cam.f_px))) % 360.0,
                    cam.f_px, cam.pitch_deg)
    ms = roof_fit.fit_roof_heights(naive, fd.PanoPose(0.0, cam.elev_m, 0.0, 0.0, cam.width), fps)
    bad = ms[0].height_m if ms else math.nan
    assert abs(good[0] - 14.0) < 1.0
    assert not abs(bad - 14.0) < abs(good[0] - 14.0)


def test_per_footprint_bases():
    """Boxes on plinths 0-25 m high: with ``cam_h`` (camera above each base) the heights come
    back; one ground plane (the default) reads the raised boxes wrong."""
    bases = [0.0, 25.0, 10.0, 18.0]
    heights = [10.0, 8.0, 16.0, 12.0]
    bearings = [CAM.heading_deg + b for b in (-22.0, -6.0, 9.0, 24.0)]
    dists = [200.0, 300.0, 250.0, 330.0]
    boxes, fps = _scene(bases, heights, bearings, dists)
    lab = _render(CAM, boxes)
    cam_h = CAM.elev_m - np.array(bases)
    got, pano, pose = _fit(CAM, fps, lab, cam_h=cam_h)
    for i, h in enumerate(heights):
        assert got[i] == pytest.approx(h, rel=0.08), (i, got[i], h)
    flat, _p, _q = _fit(CAM, fps, lab)
    assert abs(flat.get(1, 0.0) - 8.0) > 3.0
    # the base check recovers the camera height on the reference from the feet
    pose_off = fd.PanoPose(0.0, CAM.elev_m * 1.15, 0.0, 0.0, pano.width)
    chk = roof_fit.check_pose_from_bases(pano, pose_off, fps, cam_h=cam_h + CAM.elev_m * 0.15)
    if math.isfinite(chk.h_bases_m):
        assert chk.h_bases_m == pytest.approx(CAM.elev_m, rel=0.05)


def test_cam_h_length_checked():
    boxes, fps = _scene([0.0], [10.0], [CAM.heading_deg], [200.0])
    lab = _render(CAM, boxes)
    with pytest.raises(ValueError):
        _fit(CAM, fps, lab, cam_h=np.array([60.0, 60.0]))


def test_solve_from_rows_recovers_pitch_and_height():
    rng = np.random.default_rng(3)
    truth = CAM
    pts = []
    for b, d in zip(rng.uniform(-30, 30, 8), rng.uniform(150, 600, 8), strict=True):
        e, n = d * math.sin(math.radians(truth.heading_deg + b)), d * math.cos(math.radians(truth.heading_deg + b))
        z = float(rng.uniform(0, 30))
        x, y = truth.project_ll(LAT0 + n / fd.M_PER_DEG_LAT, LON0 + e / KX, z)
        pts.append(({"x": float(x), "y": float(y), "lat": LAT0 + n / fd.M_PER_DEG_LAT,
                     "lon": LON0 + e / KX}, z))
    start = pv.PinholeCamera(LAT0, LON0, 40.0, 12.0, 0.0, 0.0, 600.0, 640, 420)
    cam, res = pv.solve_from_rows(start, [p[0] for p in pts], [p[1] for p in pts])
    assert cam.pitch_deg == pytest.approx(truth.pitch_deg, abs=0.05)
    assert cam.elev_m == pytest.approx(truth.elev_m, abs=0.5)
    assert cam.f_px == pytest.approx(truth.f_px, rel=0.005)
    assert np.abs(res).max() < 0.1


def test_measure_depth_break_switch(monkeypatch):
    """``measure(depth_break=True)`` passes :data:`photo_view.DEPTH_BREAK_KW` to the roof fit,
    an explicit keyword still wins, and the default passes none of it."""
    seen = []
    monkeypatch.setattr(roof_fit, "fit_roof_heights", lambda *a, **kw: seen.append(kw) or [])
    lab = np.zeros((CAM.height, CAM.width), np.int16)
    rgb = np.zeros((CAM.height, CAM.width, 3), np.uint8)
    depth = np.ones((CAM.height, CAM.width), np.float32)
    pv.measure(rgb, lab, CAM, [], np.array([0.0]), depth_photo=depth)
    pv.measure(rgb, lab, CAM, [], np.array([0.0]), depth_photo=depth, depth_break=True)
    pv.measure(rgb, lab, CAM, [], np.array([0.0]), depth_photo=depth, depth_break=True,
               stop_ratio=0.9)
    assert not set(pv.DEPTH_BREAK_KW) & set(seen[0])
    assert all(seen[1][k] == v for k, v in pv.DEPTH_BREAK_KW.items())
    assert seen[2]["stop_ratio"] == 0.9 and seen[2]["depth_calib"] == "auto"
