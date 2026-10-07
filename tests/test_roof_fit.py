"""T43: roof heights from a drone above the roofs, on synthetic prisms rendered with a z-buffer."""

import math

import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

from city2stl.skyline import footprint_detect as fd
from city2stl.skyline._pano import roof_fit as rf

LAT, LON = 10.40, -75.55
KX = fd.M_PER_DEG_LAT * math.cos(math.radians(LAT))
CAM_H = 185.0
W = 2880                                   # 8 px per degree
F = W / (2 * math.pi)
PITCH = -30.0
NROWS = int(round(2 * F * math.tan(math.radians(50.0))))   # +20 .. -80 deg
ROAD, BUILDING = 6, 1


def _rect(cx, cy, w, d, rot_deg=0.0):
    a = math.radians(rot_deg)
    c, s = math.cos(a), math.sin(a)
    pts = [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
    return np.array([(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts])


def _at(bearing_deg, dist_m):
    b = math.radians(bearing_deg)
    return dist_m * math.sin(b), dist_m * math.cos(b)


def _ll(xy):
    xy = np.asarray(xy, float)
    ring = np.column_stack([LON + xy[:, 0] / KX, LAT + xy[:, 1] / fd.M_PER_DEG_LAT])
    return np.vstack([ring, ring[:1]])


def _crossings(ring, bearing_deg):
    """All distances where the ray crosses the polygon, sorted (pairs: enter, leave)."""
    ring = np.vstack([ring, ring[:1]])
    p, e = ring[:-1], ring[1:] - ring[:-1]
    t = math.radians(bearing_deg)
    r = np.array([math.sin(t), math.cos(t)])
    den = r[0] * e[:, 1] - r[1] * e[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (p[:, 0] * e[:, 1] - p[:, 1] * e[:, 0]) / den
        u = (p[:, 0] * r[1] - p[:, 1] * r[0]) / den
    ok = (np.abs(den) > 1e-12) & (u >= 0) & (u < 1) & (s > 0)
    return np.sort(s[ok])


def _render(prisms, cam_xy=(0.0, 0.0), noise_px=0, seed=0):
    """Labels, instances and inverse depth of flat-roofed prisms ``(ring_xy, H, instance)`` seen
    from ``cam_xy`` at CAM_H over a flat road, by a per-pixel z-buffer."""
    rows = np.arange(NROWS) + 0.5
    el = np.radians(PITCH + np.degrees(np.arctan((NROWS / 2.0 - rows) / F)))
    tan_e = np.tan(el)
    ground = np.where(tan_e < 0, CAM_H / np.maximum(-tan_e, 1e-9), np.inf)
    labels = np.where(np.isfinite(ground), ROAD, fd.SKY_CLASS).astype(np.int16)
    labels = np.repeat(labels[:, None], W, axis=1)
    dist = np.repeat(ground[:, None], W, axis=1)
    inst = np.zeros((NROWS, W), np.int32)
    cam = np.asarray(cam_xy, float)
    local = [(np.asarray(r, float) - cam, Hm, k) for r, Hm, k in prisms]
    for x in range(W):
        b = x * 360.0 / W
        best = dist[:, x].copy()
        lab, ins = labels[:, x].copy(), inst[:, x].copy()
        for ring, Hm, k in local:
            s = _crossings(ring, b)
            for a, bb in zip(s[0::2], s[1::2], strict=True):
                z = CAM_H + a * tan_e                     # height where the ray meets the wall
                d = np.where((z >= 0) & (z <= Hm), a, np.inf)
                with np.errstate(divide="ignore"):
                    rr = np.where(tan_e < 0, (CAM_H - Hm) / -tan_e, np.inf)
                d = np.minimum(d, np.where((rr >= a) & (rr <= bb), rr, np.inf))
                hit = d < best
                best[hit], lab[hit], ins[hit] = d[hit], BUILDING, k
        dist[:, x], labels[:, x], inst[:, x] = best, lab, ins
    depth = np.where(np.isfinite(dist), 1000.0 / dist, 0.0)
    if noise_px:
        rng = np.random.default_rng(seed)
        for x in range(W):
            sh = int(rng.integers(-noise_px, noise_px + 1))
            labels[:, x], inst[:, x], depth[:, x] = (np.roll(a[:, x], sh) for a in (labels, inst, depth))
    pano = fd.Pano("synthetic", LAT, LON, np.zeros((NROWS, W, 3), np.uint8), labels,
                   np.arange(W) * 360.0 / W, F, PITCH)
    return pano, depth, inst


def _scene():
    """Flat roofs at the reviewed heights, a set-back penthouse, one block behind another."""
    towers = [(10.0, 20.0, 300.0), (60.0, 60.0, 600.0), (150.0, 100.0, 600.0),
              (183.0, 140.0, 550.0), (188.0, 180.0, 650.0), (240.0, 220.0, 600.0)]
    prisms, fps = [], []
    for k, (Hm, bear, d) in enumerate(towers, start=1):
        ring = _rect(*_at(bear, d), 30.0, 30.0, rot_deg=17.0 * k)
        prisms.append((ring, Hm, k))
        fps.append(fd.Footprint(f"H{Hm:.0f}", _ll(ring), Hm))
    main = _rect(*_at(270.0, 500.0), 44.0, 44.0, 10.0)
    prisms += [(main, 120.0, 7), (_rect(*_at(270.0, 500.0), 14.0, 14.0, 10.0), 132.0, 7)]
    fps.append(fd.Footprint("penthouse", _ll(main), 120.0))
    near, far = _rect(*_at(320.0, 400.0), 30.0, 30.0), _rect(*_at(320.0, 470.0), 30.0, 30.0)
    prisms += [(near, 40.0, 8), (far, 90.0, 9)]
    fps += [fd.Footprint("near", _ll(near), 40.0), fd.Footprint("behind", _ll(far), 90.0)]
    return prisms, fps


@pytest.fixture(scope="module")
def clean():
    prisms, fps = _scene()
    return (*_render(prisms), fps)


@pytest.fixture(scope="module")
def noisy():
    prisms, fps = _scene()
    return (*_render(prisms, noise_px=2, seed=3), fps)


def _pose(pitch_fix=0.0, h=CAM_H):
    return fd.PanoPose(0.0, h, pitch_fix, 0.0, W)


def _by_name(ms):
    return {m.name: m for m in ms}


def _tol(H):
    return max(2.0, 0.05 * H)


# --------------------------------------------------------------------------- geometry


@pytest.mark.parametrize("rot", [0.0, 23.0, 45.0, 71.0])
def test_ray_intervals_match_shapely_on_rotated_rectangles(rot):
    ring = _rect(80.0, 500.0, 60.0, 25.0, rot)
    poly = Polygon(ring)
    bears = np.linspace(0.0, 20.0, 81)
    iv = rf.ray_intervals(ring, bears)
    for b, (d0, d1) in zip(bears, iv, strict=True):
        hit = poly.intersection(LineString([(0, 0), _at(b, 5000.0)]))
        if hit.is_empty or hit.length < 1e-9:
            assert np.isnan(d0)
            continue
        dd = np.hypot(*np.asarray(hit.coords).T) if hit.geom_type == "LineString" else \
            np.hypot(*np.vstack([np.asarray(g.coords) for g in hit.geoms]).T)
        assert d0 == pytest.approx(dd.min(), abs=1e-6)
        assert d1 == pytest.approx(dd.max(), abs=1e-6)


def test_ray_intervals_l_shape_spans_both_legs():
    # an L opening towards the camera: the ray up the notch crosses four edges
    ring = np.array([(-30, 400), (30, 400), (30, 420), (-10, 420), (-10, 460), (30, 460),
                     (30, 480), (-30, 480)], float)
    iv = rf.ray_intervals(ring, [0.0, 359.0, 90.0])
    assert iv[0] == pytest.approx([400.0, 480.0])          # enters leg one, leaves leg two
    assert iv[1, 0] == pytest.approx(400.0 / math.cos(math.radians(1)), rel=1e-6)
    assert np.isnan(iv[2]).all()                            # east: misses


def test_ray_intervals_camera_inside_is_nan():
    assert np.isnan(rf.ray_intervals(_rect(0.0, 0.0, 50.0, 50.0), [0.0, 123.0])).all()


def test_invert_top_round_trip_both_branches():
    H = np.array([0.0, 10.0, 150.0, 184.9, 185.0, 185.1, 188.0, 260.0])
    d_in, d_out = 400.0, 437.0
    e = rf.top_elev(CAM_H, H, d_in, d_out)
    assert np.all(np.diff(e) > 0)                           # monotonic: one row, one height
    assert rf.invert_top(CAM_H, e, d_in, d_out) == pytest.approx(H, abs=1e-9)
    assert e[1] == pytest.approx(-math.degrees(math.atan(175.0 / d_out)))   # far roof edge
    assert e[-1] == pytest.approx(math.degrees(math.atan(75.0 / d_in)))     # near roof edge


# --------------------------------------------------------------------------- fitting


def test_flat_roofs_recovered(clean):
    pano, depth, inst, fps = clean
    got = _by_name(rf.fit_roof_heights(pano, _pose(), fps, depth=depth, instances=inst))
    for name in ("H10", "H60", "H150", "H183", "H188", "H240", "near", "behind"):
        H = next(f.osm_height_m for f in fps if f.name == name)
        assert name in got, name
        assert abs(got[name].height_m - H) <= _tol(H), (name, got[name].height_m)
        assert got[name].top_edge == "roof"


def test_penthouse_does_not_raise_p35(clean):
    pano, depth, inst, fps = clean
    m = _by_name(rf.fit_roof_heights(pano, _pose(), fps, depth=depth, instances=inst))["penthouse"]
    assert abs(m.height_m - 120.0) <= _tol(120.0)
    assert m.height_median_m >= m.height_m - 1e-9


def test_labels_only_without_instances_or_depth(clean):
    pano, _depth, _inst, fps = clean
    got = _by_name(rf.fit_roof_heights(pano, _pose(), fps))
    for name in ("H10", "H60", "H150", "H240"):
        H = next(f.osm_height_m for f in fps if f.name == name)
        assert abs(got[name].height_m - H) <= _tol(H), (name, got[name].height_m)


def _dist(fp):
    return float(np.hypot(*fd._local(LAT, LON, fp.ring).mean(axis=0)))


def test_pixel_noise(noisy):
    pano, depth, inst, fps = noisy
    got = _by_name(rf.fit_roof_heights(pano, _pose(), fps, depth=depth, instances=inst))
    for name in ("H10", "H60", "H150", "H183", "H188", "H240", "penthouse", "near", "behind"):
        H = next(f.osm_height_m for f in fps if f.name == name)
        assert abs(got[name].height_m - H) <= _tol(H), (name, got[name].height_m)


@pytest.mark.parametrize("pitch_fix", [-0.2, 0.2])
def test_noise_and_pitch_error(noisy, pitch_fix):
    """A pitch error moves every top row: ``dH = d (1 + tan^2 e) de``, 1.2 m at best for a low
    roof (at d = h - H) and 2.2 m for H60 at 600 m, so the bound adds it to the tolerance."""
    pano, depth, inst, fps = noisy
    got = _by_name(rf.fit_roof_heights(pano, _pose(pitch_fix), fps, depth=depth, instances=inst))
    for name in ("H10", "H60", "H150", "H183", "H188", "H240", "penthouse"):
        fp = next(f for f in fps if f.name == name)
        d, H = _dist(fp), fp.osm_height_m
        prop = d * (1 + ((CAM_H - H) / d) ** 2) * math.radians(abs(pitch_fix))
        assert abs(got[name].height_m - H) <= _tol(H) + prop, (name, pitch_fix, got[name].height_m)


@pytest.mark.parametrize("cam", [(20.0, 0.0), (0.0, -20.0), (14.0, 14.0)])
def test_position_error(cam):
    """Rendered from 20 m away: a radial error moves the far roof edge by ``20 (h - H) / d``,
    which no method can undo; roofs near the camera height barely move. A low roof that then
    inverts under 3 m is dropped rather than read."""
    prisms, fps = _scene()
    pano, depth, inst = _render(prisms, cam_xy=cam)
    got = _by_name(rf.fit_roof_heights(pano, _pose(), fps, depth=depth, instances=inst))
    for name in ("H10", "H60", "H150", "H183", "H188", "H240"):
        fp = next(f for f in fps if f.name == name)
        if name == "H10" and name not in got:
            continue
        bound = _tol(fp.osm_height_m) + 20.0 * abs(CAM_H - fp.osm_height_m) / _dist(fp)
        assert abs(got[name].height_m - fp.osm_height_m) <= bound, (name, cam, got[name].height_m)
    for name in ("H183", "H188"):
        H = next(f.osm_height_m for f in fps if f.name == name)
        assert abs(got[name].height_m - H) <= _tol(H), (name, cam)


def test_pose_check_from_bases(clean):
    pano, depth, inst, fps = clean
    ok = rf.check_pose_from_bases(pano, _pose(), fps, depth=depth, instances=inst)
    assert ok.h_bases_m == pytest.approx(CAM_H, rel=0.03)
    assert not ok.flagged
    bad = rf.check_pose_from_bases(pano, _pose(h=CAM_H * 1.15), fps, depth=depth, instances=inst)
    assert bad.flagged


def test_roof_reading_weight_scale_defaults_to_one():
    m = rf.RoofMeasured(0, "a", 0, 9, 1.0, 2.0, 3.0, 400.0, 50.0, 10, True, None, 1.0, "roof",
                        confidence=0.6)
    assert m.weight_scale == 1.0
    assert fd.measurement_weight(m) == pytest.approx(0.6 / 400.0 ** 2)
    from dataclasses import replace

    assert fd.measurement_weight(replace(m, weight_scale=0.5)) == pytest.approx(0.3 / 400.0 ** 2)
