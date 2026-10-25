"""F-PLINTH: level ground under buildings (city2stl.city_model.level_pads)."""

import numpy as np
import pytest
import trimesh
from shapely.geometry import box

from city2stl import model_cache
from city2stl.city_model import (
    ModelScale,
    Terrain,
    build_on_terrain,
    level_pads,
)

BBOX = dict(north=37.1823, south=37.1800, east=-3.5970, west=-3.6000)
H = W = 100
SLOPE = 0.2                     # mm of height per mm of run (20 %)
HEIGHT_M = 10.0
SCALE = ModelScale(mm_per_px=1.0, m_per_px=2.5, z_mode="true", z_mm_per_m=0.4,
                   exaggeration=1.0, elev_min_m=0.0, base_mm=5.0)
TOP_MM = HEIGHT_M * SCALE.z_mm_per_m   # 4 mm


def _slope(w=W, h=H):
    """A plane rising to the east, 20 %: z = 5 + 0.2 x (mm)."""
    x = np.tile(np.arange(w, dtype=float), (h, 1))
    return 5.0 + SLOPE * x


def _terrain(z=None):
    return Terrain(_slope() if z is None else z, BBOX, SCALE)


def _lonlat_box(terrain, x0, y0, x1, y1):
    pts = terrain.unproject_xy(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]))
    return {"type": "Polygon", "coordinates": [pts.tolist()]}


def _fc(terrain, rects, height=HEIGHT_M):
    return {"type": "FeatureCollection", "features": [
        {"geometry": _lonlat_box(terrain, *r), "properties": {"height_m": height, "osm_id": str(i)}}
        for i, r in enumerate(rects)]}


def _build(rects, level, z=None):
    t = _terrain(z)
    return build_on_terrain(t.z, BBOX, SCALE, {"buildings": _fc(t, rects)}, cache=False,
                            level_ground=level)


def _top_z(mesh, x, y):
    """Highest surface of ``mesh`` over (x, y)."""
    hits, _, _ = mesh.ray.intersects_location([[x, y, 1000.0]], [[0, 0, -1]], multiple_hits=True)
    return float(hits[:, 2].max())


RECT = (40.0, 40.0, 52.0, 52.0)   # 12 x 12 mm: the ground drops 2.4 mm across it


def test_cache_version_bumped():
    assert model_cache.MODEL_CACHE_VERSION >= 8


class TestLevelPads:
    def test_pad_is_flat_under_footprint_and_at_lowest_ground(self):
        t = _terrain()
        z, stats = level_pads(t, [box(*RECT)], 0.1)
        assert stats["pads"] == 1 and stats["capped"] == 0
        low = 5.0 + SLOPE * RECT[0]
        t2 = Terrain(z, BBOX, SCALE)
        xs, ys = np.meshgrid(np.linspace(RECT[0], RECT[2], 25), np.linspace(RECT[1], RECT[3], 25))
        assert np.allclose(t2.sample(xs.ravel(), ys.ravel()), low, atol=1e-9)
        lo, hi = t2.ranges_under([box(*RECT)])
        assert lo[0] == pytest.approx(low) and hi[0] == pytest.approx(low)
        # far from the building nothing changed
        assert np.array_equal(z[:, :30], t.z[:, :30])

    def test_flat_ground_and_tiny_drop_make_no_pad(self):
        flat = np.full((H, W), 8.0)
        z, stats = level_pads(_terrain(flat), [box(*RECT)], 0.1)
        assert stats["pads"] == 0 and np.array_equal(z, flat)
        # a 0.05 mm drop is under one print layer
        z, stats = level_pads(_terrain(), [box(40, 40, 40.2, 40.2)], 0.1)
        assert stats["pads"] == 0

    def test_drop_over_the_cap_keeps_the_old_seating(self):
        t = _terrain()
        z, stats = level_pads(t, [box(30, 40, 60, 52)], 0.1)   # 6 mm of drop > 3 mm
        assert stats["pads"] == 0 and stats["capped"] == 1
        assert np.array_equal(z, t.z)

    def test_overlapping_pads_share_the_lowest_level(self):
        t = _terrain()
        a, b = box(40, 40, 46, 50), box(46.5, 40, 52, 50)   # share DEM cells at the seam
        z, stats = level_pads(t, [a, b], 0.1)
        assert stats["pads"] == 2 and stats["clusters"] == 1
        t2 = Terrain(z, BBOX, SCALE)
        lo, hi = t2.ranges_under([a, b])
        low = 5.0 + SLOPE * 40
        assert np.allclose(lo, low) and np.allclose(hi, low)
        # order of the input does not matter
        z2, _ = level_pads(t, [b, a], 0.1)
        assert np.array_equal(z, z2)

    def test_cluster_that_would_sink_a_building_too_deep_is_released(self):
        t = _terrain()
        # touching buildings 12 mm apart in x: the cluster level would drop the upper one 4.4 mm
        a, b = box(40, 40, 46, 46), box(46.5, 40, 62, 46)
        z, stats = level_pads(t, [a, b], 0.1)
        assert stats["pads"] <= 1
        assert stats["max_drop_mm"] <= 3.0


class TestBuild:
    def test_wall_is_the_true_height_on_every_side(self):
        m = _build([RECT], level=True)
        assert m.report["level_ground"]["pads"] == 1
        low = 5.0 + SLOPE * RECT[0]
        ground = m.parts["terrain"]
        for x, y in [(40.3, 46), (51.7, 46), (46, 40.3), (46, 51.7), (46, 46)]:
            assert _top_z(ground, x, y) == pytest.approx(low, abs=0.02)
        top = _top_z(m.parts["buildings"], 46, 46)
        assert top == pytest.approx(low + TOP_MM, abs=0.02)
        assert m.report["merged"]["watertight"]

    def test_off_is_the_old_seating(self):
        m = _build([RECT], level=False)
        assert "level_ground" not in m.report
        high = 5.0 + SLOPE * RECT[2]
        assert _top_z(m.parts["buildings"], 46, 46) == pytest.approx(high + TOP_MM, abs=0.02)
        # the ground keeps its slope up to the wall
        assert _top_z(m.parts["terrain"], 51.7, 46) > _top_z(m.parts["terrain"], 40.3, 46) + 1.5
        assert m.report["merged"]["watertight"]

    def test_on_and_off_differ_only_around_the_building(self):
        on, off = _build([RECT], True), _build([RECT], False)
        assert on.merged.volume != pytest.approx(off.merged.volume, rel=1e-4)
        assert _top_z(on.parts["terrain"], 5, 5) == pytest.approx(_top_z(off.parts["terrain"], 5, 5))

    def test_two_buildings_stay_watertight(self):
        m = _build([RECT, (60.0, 60.0, 70.0, 70.0)], level=True)
        assert m.report["level_ground"]["pads"] == 2
        assert m.report["merged"]["watertight"]
        for part in m.parts.values():
            assert isinstance(part, trimesh.Trimesh)

    def test_road_across_the_pad_follows_the_terrace(self):
        t = _terrain()
        road = {"type": "FeatureCollection", "features": [{
            "geometry": {"type": "LineString", "coordinates":
                         t.unproject_xy(np.array([[30.0, 46.0], [62.0, 46.0]])).tolist()},
            "properties": {"road_width_m": 5}}]}
        layers = {"buildings": _fc(t, [RECT]), "roads": road}
        m = build_on_terrain(t.z, BBOX, SCALE, layers, cache=False, level_ground=True)
        low = 5.0 + SLOPE * RECT[0]
        roads = m.parts["roads"]
        assert m.report["merged"]["watertight"]
        assert roads.bounds[1][2] > low
