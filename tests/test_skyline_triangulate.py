"""Cross-sphere triangulation of roof corners (2026-10-10): corner observations from a rendered
tower, ray intersection and its gates, the fusion's source count, the estimate hook."""

import math
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

from city2stl.skyline import footprint_detect as fd
from city2stl.skyline._pano import elevated as el
from city2stl.skyline._pano import triangulate as tg

LAT0, LON0 = 25.77, -80.19
KX = fd.M_PER_DEG_LAT * math.cos(math.radians(LAT0))
H, W, F_PX = 700, 2620, 417.0 * 2.0
ROOF_M, CAM_H = 80.0, 50.0


def _ll(x, y):
    return (LON0 + x / KX, LAT0 + y / fd.M_PER_DEG_LAT)


def _ring(cx, cy, side):
    h = side / 2
    return np.array([_ll(cx - h, cy - h), _ll(cx + h, cy - h), _ll(cx + h, cy + h),
                     _ll(cx - h, cy + h), _ll(cx - h, cy - h)])


def _pano(cam_xy, cam_h=CAM_H):
    lat, lon = LAT0 + cam_xy[1] / fd.M_PER_DEG_LAT, LON0 + cam_xy[0] / KX
    frame = np.arange(W) * 360.0 / W
    labels = np.full((H, W), fd.SKY_CLASS, np.int16)
    return (fd.Pano("seed_9", lat, lon, np.zeros((H, W, 3), np.uint8), labels, frame, F_PX, 0.0),
            fd.PanoPose(180.0, cam_h, 0.0, 0.1, W))      # north mid-pano (no seam)


def _render(pano, pose, rings, roof_m):
    """Paint each ring as a building (class + instance id i + 1) from base to roof, exactly: per
    column the entry distance of the ray into the ring, the near roof edge from below."""
    inst = np.zeros((H, W), np.int32)
    xy = [fd._local(pano.lat, pano.lon, r) for r in rings]
    bu, _b0 = fd.bearing_columns(pano, pose)
    order = np.argsort([-np.hypot(*q.mean(0)) for q in xy])         # far to near
    for i in order:
        poly = Polygon(xy[i])
        for x in range(W):
            b = math.radians(bu[x] % 360.0)
            seg = LineString([(0, 0), (4000 * math.sin(b), 4000 * math.cos(b))]).intersection(poly)
            if seg.is_empty:
                continue
            d = LineString([(0, 0), seg.coords[0] if seg.geom_type == "LineString" else
                            list(seg.geoms)[0].coords[0]]).length
            top = float(fd._row_of(pano, pose, math.degrees(math.atan((roof_m - pose.camera_h_m) / d))))
            bot = float(fd._row_of(pano, pose, -math.degrees(math.atan(pose.camera_h_m / d))))
            inst[max(0, int(math.ceil(top))):min(H, int(bot) + 1), x] = i + 1
    pano.labels[inst > 0] = fd.BUILDING_CLASSES[0]
    return inst


def _measured(pano, pose, fp, inst, i=0):
    xy = fd._local(pano.lat, pano.lon, fp.ring)
    bear = np.degrees(np.arctan2(xy[:, 0], xy[:, 1])) % 360.0
    rel = (bear - bear[0] + 180.0) % 360.0 - 180.0
    bu, b0 = fd.bearing_columns(pano, pose)
    c0 = fd.column_of((bear[0] + rel.min()) % 360.0, bu, b0)
    c1 = fd.column_of((bear[0] + rel.max()) % 360.0, bu, b0)
    x0, x1 = int(math.ceil(c0)), int(math.floor(c1))
    cols = np.arange(x0, x1 + 1)
    tops = [np.flatnonzero(inst[:, x] == i + 1).min() for x in cols]
    bots = [np.flatnonzero(inst[:, x] == i + 1).max() for x in cols]
    return fd.Measured(i, "tower", x0, x1, float(np.median(tops)), float(np.median(bots)),
                       float(np.median(bots)), float(np.hypot(xy[:, 0], xy[:, 1]).min()), ROOF_M, len(cols), True,
                       None, 1.0, "sky")


def _corners(cam_xy, cx=0.0, cy=1200.0, side=60.0, seed="seed_1", rings_extra=()):
    pano, pose = _pano(cam_xy)
    rings = [_ring(cx, cy, side), *rings_extra]
    inst = _render(pano, pose, rings, ROOF_M)
    fps = [fd.Footprint(f"b{i}", r) for i, r in enumerate(rings)]
    fids = [f"f{i}" for i in range(len(rings))]
    ms = [_measured(pano, pose, fps[0], inst)]
    obs, stats = tg.seed_corners(pano, pose, fps, fids, ms, instances=inst)
    return obs, stats


def test_corner_observation_names_the_vertex_and_reads_the_roof():
    obs, stats = _corners((0.0, 0.0))
    assert {o.side for o in obs} == {"L", "R"}, stats
    for o in obs:
        assert o.fid == "f0"
        # the corner's roof elevation: (80 - 50) m over its distance, to a pixel or two
        d = math.hypot(*_vertex_xy(o))
        assert o.elev_deg == pytest.approx(math.degrees(math.atan((ROOF_M - CAM_H) / d)), abs=0.35)


def _vertex_xy(o):
    return ((o.vertex[0] - o.lon) * KX, (o.vertex[1] - o.lat) * fd.M_PER_DEG_LAT)


def test_two_spheres_recover_the_roof_height():
    # both cameras on one side of the tower's north-south edges, so both see the same two limb
    # corners (a limb corner flips to the next vertex when the line of sight crosses an edge)
    a, _ = _corners((-450.0, 0.0), seed="seed_1")
    b, _ = _corners((-100.0, 0.0), seed="seed_2")
    got, why = tg.triangulate({"seed_1": a, "seed_2": b})
    assert "f0" in got, why
    t = got["f0"]
    assert t.height_m == pytest.approx(ROOF_M, abs=4.0)
    assert t.miss_m < tg.MAX_MISS_M and t.n >= 1
    assert set(t.groups) == {"seed_1", "seed_2"}


def test_one_camera_never_triangulates_itself():
    a, _ = _corners((-450.0, 0.0))
    b, _ = _corners((-100.0, 0.0))
    got, why = tg.triangulate({"miami_seed_3": a, "miami_seed_3_sphere": b},
                              group=tg.camera_group)
    assert got == {} and why["same_camera"] == 1


def test_baseline_and_angle_gates():
    a, _ = _corners((-250.0, 0.0))
    b, _ = _corners((-100.0, 0.0))                     # 150 m apart
    got, why = tg.triangulate({"seed_1": a, "seed_2": b})
    assert got == {} and why["baseline"] >= 1
    c, _ = _corners((-300.0, -300.0))
    d, _ = _corners((-100.0, -100.0))                  # 280 m apart, 7 deg between the lines
    got, why = tg.triangulate({"seed_1": c, "seed_2": d})
    assert got == {} and why["angle"] >= 1


def _ray(cam_xy, point, h=CAM_H, vertex=None, fid="f0"):
    """The exact corner observation of a 3D ``point`` (x, y, z) from a camera at ``cam_xy``."""
    dx, dy, dz = point[0] - cam_xy[0], point[1] - cam_xy[1], point[2] - h
    d = math.hypot(dx, dy)
    return tg.CornerObs(fid, "L", vertex or _ll(point[0], point[1]),
                        LAT0 + cam_xy[1] / fd.M_PER_DEG_LAT, LON0 + cam_xy[0] / KX, h,
                        math.degrees(math.atan2(dx, dy)) % 360.0,
                        math.degrees(math.atan2(dz, d)), 0.0, 0.0, d)


def test_intersection_is_exact_and_gates_reject_a_miss_and_a_wrong_building():
    p = (30.0, 900.0, 120.0)
    a, b = _ray((-400.0, 0.0), p), _ray((600.0, 100.0), p)
    r, why = tg.intersect(a, b)
    assert why is None
    assert r["height_m"] == pytest.approx(120.0, abs=0.01) and r["miss_m"] < 0.01
    # sphere B reads a different building: its ray passes 30+ m from A's
    q = (130.0, 900.0, 120.0)
    r, why = tg.intersect(a, _ray((600.0, 100.0), q, vertex=a.vertex))
    assert r is None and why in ("miss", "vertex")
    # the rays meet (a different building at the same bearing pair) but not at the named vertex
    far = _ll(p[0] + 300.0, p[1])
    r, why = tg.intersect(a, _ray((600.0, 100.0), p, vertex=far))
    assert r is not None                              # b's vertex is not the test: a's is
    r, why = tg.intersect(_ray((-400.0, 0.0), p, vertex=far), b)
    assert r is None and why == "vertex"


def test_only_the_same_vertex_pairs():
    p = (0.0, 800.0, 90.0)
    a, b = _ray((-300.0, 0.0), p), _ray((400.0, 0.0), p, vertex=_ll(50.0, 800.0))
    got, why = tg.triangulate({"seed_1": [a], "seed_2": [b]})
    assert got == {} and why["other_vertex"] == 1


def test_triangulate_takes_the_median_of_the_corners():
    p1, p2 = (0.0, 800.0, 90.0), (40.0, 800.0, 92.0)
    a = [_ray((-300.0, 0.0), p1), _ray((-300.0, 0.0), p2, vertex=_ll(40.0, 800.0))]
    b = [_ray((400.0, 0.0), p1), _ray((400.0, 0.0), p2, vertex=_ll(40.0, 800.0))]
    got, _ = tg.triangulate({"seed_1": a, "seed_2": b})
    t = got["f0"]
    assert t.n == 2 and t.height_m == pytest.approx(91.0, abs=0.05)


# ---------------------------------------------------------------------- fusion and estimates


def _m(fid, h, dist=800.0, **kw):
    return dict(footprint=fid, name=fid, height_m=h, dist_m=dist, base_visible=True,
                visible_frac=1.0, top_edge="sky", osm_height_m=None, **kw)


def _tri(h, groups, fid="f0", miss=2.0):
    t = tg.TriReading(fid, h, miss, 1, [(groups[0], groups[1], "LL", h, miss, 30.0, 700.0, 900.0)],
                      tuple(groups), 900.0)
    return tg.fusion_reading(t)


def test_fusion_counts_a_triangulated_reading_as_a_source_only_when_independent():
    # alone: one source
    f = fd.fuse_heights({"tri": [_tri(80.0, ("seed_1", "seed_2"))]})["f0"]
    assert f["used"] == ["tri"] and f["n_sources"] == 1 and not f["verified"]
    # a single from one of its two cameras is not independent of it
    f = fd.fuse_heights({"seed_1": [_m("f0", 82.0)], "tri": [_tri(80.0, ("seed_1", "seed_2"))]})["f0"]
    assert sorted(f["used"]) == ["seed_1", "tri"] and f["n_sources"] == 1 and not f["verified"]
    # a third camera's single is
    f = fd.fuse_heights({"seed_3": [_m("f0", 82.0)], "tri": [_tri(80.0, ("seed_1", "seed_2"))]})["f0"]
    assert f["n_sources"] == 2 and f["verified"]
    # its weight scale (the miss) decides who wins a disagreement: a near-exact reading
    # outvotes a far single, a poor one does not
    good = fd.fuse_heights({"seed_3": [_m("f0", 140.0, 2500.0)],
                            "tri": [_tri(80.0, ("seed_1", "seed_2"), miss=0.5)]})["f0"]
    assert good["height_m"] == pytest.approx(80.0) and good["used"] == ["tri"]


def _seed(name, fid, h_single, corners=(), trusted_single=True, off=0.0):
    m = fd.Measured(0, "tower", 100, 140, 300.0, 500.0, 500.0, 900.0, h_single, 30, True, None,
                    1.0, "sky")
    pano = SimpleNamespace(lat=LAT0, lon=LON0)
    return SimpleNamespace(
        seed_name=name, feature_ids=[fid], measured=[m] if trusted_single else [],
        position=SimpleNamespace(pose=SimpleNamespace(offset_deg=off)), corners=list(corners),
        pano_result=SimpleNamespace(matched_segments=[{
            "matched_projection": {"feature_id": fid}, "true_bearing_deg": 10.0}]),
        ownership=None, floors=[], pano=pano, behind={}, depth=None, instances=None)


def test_estimates_publish_a_triangulated_height_when_the_singles_are_the_towers_behind():
    p = (0.0, 900.0, 80.0)
    sa = _seed("seed_1", "f0", 140.0, [_ray((-300.0, 0.0), p)])
    sb = _seed("seed_2", "f0", 150.0, [_ray((500.0, 0.0), p)])
    est = el.elevated_estimates([sa, sb])
    # two spheres read 140 and 150 m alike (the tower behind), the corner rays meet at 80 m: the
    # footprint is disputed and publishes no drone height, rather than the towers behind's
    assert list(est) == [] and est.triangulated == {}
    # untrusted singles leave nothing to outvote it: it is published under the first sphere
    sa = _seed("seed_1", "f0", 140.0, [_ray((-300.0, 0.0), p)], trusted_single=False)
    sb = _seed("seed_2", "f0", 150.0, [_ray((500.0, 0.0), p)], trusted_single=False)
    sa.measured = [_with_edge(sa, "depth")]
    sb.measured = [_with_edge(sb, "depth")]
    est = el.elevated_estimates([sa, sb])
    assert [e.estimated_height_m for e in est] == [pytest.approx(80.0, abs=0.1)]
    assert est[0].view_name.startswith("seed_1_") and "f0" in est.triangulated


def _with_edge(seed, edge):
    m = fd.Measured(0, "tower", 100, 140, 300.0, 500.0, 500.0, 900.0, 140.0, 30, True, None,
                    1.0, edge)
    return m


def test_off_by_default():
    assert tg.enabled() is False
    assert el.seed_corners(None, None, [], [], []) == []
