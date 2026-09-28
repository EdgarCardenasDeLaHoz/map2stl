"""City model: print-scale building reduction, the model caches, trails filtering and
the Triangle-free slab triangulation (city2stl/city_model.py, city2stl/model_cache.py)."""

import math

import numpy as np
import pytest
import shapely
import trimesh

import city2stl.city_model as cm
from city2stl import model_cache
from city2stl.city_model import build_city_model, triangulate_polygon
from city2stl.landmarks import LandmarkOverride
from geo2stl.trails import is_hiking_trail

BBOX = dict(north=37.19, south=37.172, east=-3.578, west=-3.605)
LAT0, LON0 = 37.181, -3.59
M_LAT = 110_574.0
M_LON = 111_320.0 * math.cos(math.radians(LAT0))
FLAT = np.full((120, 150), 100.0)


def _rect(x0_m, y0_m, w_m, h_m):
    """A rectangle in metres from (LON0, LAT0), as GeoJSON."""
    x0, y0 = LON0 + x0_m / M_LON, LAT0 + y0_m / M_LAT
    x1, y1 = x0 + w_m / M_LON, y0 + h_m / M_LAT
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _b(x0_m, w_m=60.0, h=10.0, **props):
    return {"geometry": _rect(x0_m, 0.0, w_m, 40.0), "properties": {"height_m": h, **props}}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


def _mm_per_m():
    s = cm.choose_scale(BBOX, FLAT.shape, 100, 100)
    return s.mm_per_px / s.m_per_px


def _build(*buildings, dem=FLAT, **kw):
    return build_city_model(dem, BBOX, {"buildings": _fc(*buildings)}, **kw)


# ---------------------------------------------------------------------------
# Print-scale reduction (merge_flat_roofs)
# ---------------------------------------------------------------------------

class TestFlatRoofMerge:
    def test_touching_same_height_buildings_become_one_solid(self):
        m = _build(_b(0), _b(60))
        rep = m.report["layers"]["buildings"]
        assert rep["merged_from"] == 2 and rep["merged_into"] == 1 and rep["solids"] == 1
        assert m.report["merged"]["watertight"]
        assert len(m.parts["buildings"].split(only_watertight=False)) == 1

    def test_gap_narrower_than_a_nozzle_closes(self):
        gap_m = 0.25 / _mm_per_m()                    # 0.25 mm < 0.4 mm
        m = _build(_b(0), _b(60 + gap_m))
        assert m.report["layers"]["buildings"]["solids"] == 1
        assert m.report["merged"]["watertight"]

    def test_gap_wider_than_a_nozzle_stays(self):
        gap_m = 0.6 / _mm_per_m()                     # 0.6 mm > 0.4 mm
        m = _build(_b(0), _b(60 + gap_m))
        rep = m.report["layers"]["buildings"]
        assert rep["merged_from"] == 0 and rep["solids"] == 2

    def test_different_print_layers_stay_separate(self):
        m = _build(_b(0, h=10), _b(60, h=30))
        rep = m.report["layers"]["buildings"]
        assert rep["merged_from"] == 0 and rep["solids"] == 2

    def test_pitched_roof_is_left_alone(self):
        m = _build(_b(0), _b(60, **{"roof:shape": "gabled", "roof:height": "6"}))
        assert m.report["layers"]["buildings"]["merged_from"] == 0

    def test_building_parts_are_left_alone(self):
        m = _build(_b(0, **{"building:part": "yes"}), _b(60, **{"building:part": "yes"}))
        assert m.report["layers"]["buildings"]["merged_from"] == 0

    def test_can_be_turned_off(self):
        m = _build(_b(0), _b(60), layer_overrides={"buildings": {"merge_flat": False}})
        assert m.report["layers"]["buildings"]["solids"] == 2

    def test_merged_block_keeps_volume_and_top(self):
        merged = _build(_b(0), _b(60))
        apart = _build(_b(0), _b(60), layer_overrides={"buildings": {"merge_flat": False}})
        assert merged.parts["buildings"].volume == pytest.approx(apart.parts["buildings"].volume,
                                                                 rel=1e-3)
        assert merged.parts["buildings"].bounds[1, 2] == pytest.approx(
            apart.parts["buildings"].bounds[1, 2], abs=1e-6)


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

@pytest.fixture()
def counters(monkeypatch):
    calls = {"layers": [], "tin": 0}
    real_build, real_tin = cm.build_layer, cm.tin_solid

    def build_layer(name, *a, **k):
        calls["layers"].append(name)
        return real_build(name, *a, **k)

    def tin_solid(*a, **k):
        calls["tin"] += 1
        return real_tin(*a, **k)

    monkeypatch.setattr(cm, "build_layer", build_layer)
    monkeypatch.setattr(cm, "tin_solid", tin_solid)
    return calls


def _hill(h=120, w=150):
    y, x = np.mgrid[0:h, 0:w]
    return 100 + 80 * np.exp(-(((x - w / 2) / 30) ** 2 + ((y - h / 2) / 25) ** 2))


ROAD = {"geometry": {"type": "LineString", "coordinates": [[-3.60, 37.175], [-3.58, 37.187]]},
        "properties": {"road_width_m": 10}}
LAYERS = {"buildings": _fc(_b(0), _b(200, h=20, osm_id="way/1")), "roads": _fc(ROAD)}


class TestModelCache:
    def test_second_build_skips_all_geometry(self, counters):
        first = build_city_model(_hill(), BBOX, LAYERS)
        assert sorted(counters["layers"]) == ["buildings", "roads"] and counters["tin"] == 1
        second = build_city_model(_hill(), BBOX, LAYERS)
        assert sorted(counters["layers"]) == ["buildings", "roads"] and counters["tin"] == 1
        assert all(v.get("cached") for v in second.report["layers"].values())
        assert second.report.get("model_cached") and not first.report.get("model_cached")
        assert second.report["merged"] == first.report["merged"]
        assert set(second.parts) == set(first.parts)
        assert second.merged.is_watertight
        assert second.merged.volume == pytest.approx(first.merged.volume, rel=1e-9)
        assert second.report["layers"]["buildings"]["solids"] == \
            first.report["layers"]["buildings"]["solids"]

    def test_changing_one_layer_rebuilds_only_that_layer(self, counters):
        build_city_model(_hill(), BBOX, LAYERS)
        counters["layers"].clear()
        road2 = {**ROAD, "properties": {"road_width_m": 14}}
        build_city_model(_hill(), BBOX, {**LAYERS, "roads": _fc(road2)})
        assert counters["layers"] == ["roads"]
        build_city_model(_hill(), BBOX, LAYERS, layer_overrides={"buildings": {"height_scale": 2}})
        assert counters["layers"] == ["roads", "buildings"]

    def test_changing_the_terrain_rebuilds_everything(self, counters):
        build_city_model(_hill(), BBOX, LAYERS)
        counters["layers"].clear()
        build_city_model(_hill() + 5 * np.linspace(0, 1, 150), BBOX, LAYERS)
        assert sorted(counters["layers"]) == ["buildings", "roads"] and counters["tin"] == 2

    def test_landmark_override_rebuilds_only_buildings(self, counters):
        def ov(size):
            mesh = trimesh.creation.box((size, 4.0, 2.0))
            return {"way/1": LandmarkOverride("way/1", "mesh", {}, mesh=mesh, source="mesh")}

        first = build_city_model(_hill(), BBOX, LAYERS, landmark_overrides=ov(8.0))
        counters["layers"].clear()
        again = build_city_model(_hill(), BBOX, LAYERS, landmark_overrides=ov(8.0))
        assert counters["layers"] == []
        assert again.report["landmarks"] == first.report["landmarks"]   # restored from cache
        build_city_model(_hill(), BBOX, LAYERS, landmark_overrides=ov(6.0))
        assert counters["layers"] == ["buildings"]

    def test_version_bump_invalidates(self, counters, monkeypatch):
        build_city_model(_hill(), BBOX, LAYERS)
        monkeypatch.setattr(model_cache, "MODEL_CACHE_VERSION", model_cache.MODEL_CACHE_VERSION + 1)
        counters["layers"].clear()
        build_city_model(_hill(), BBOX, LAYERS)
        assert sorted(counters["layers"]) == ["buildings", "roads"] and counters["tin"] == 2

    def test_cache_can_be_disabled(self, counters, monkeypatch):
        build_city_model(_hill(), BBOX, LAYERS)
        monkeypatch.setenv("MAP2STL_CITY_CACHE", "0")
        build_city_model(_hill(), BBOX, LAYERS)
        assert counters["tin"] == 2 and len(counters["layers"]) == 4

    def test_cached_polygons_keep_their_precision_grid(self):
        # GEOS keeps set_precision's grid on a geometry and later overlays honour
        # it; WKB drops it, and slabs built from grid-less copies failed.
        polys = [shapely.set_precision(shapely.box(0, 0, 1.234, 2), 0.01), shapely.box(5, 5, 6, 6)]
        model_cache.write_polygons("k", polys, [{}, {}], {"dropped": 0})
        got, _, _ = model_cache.read_polygons("k")
        assert list(shapely.get_precision(np.asarray(got, dtype=object))) == [0.01, 0.0]
        assert all(shapely.equals_exact(a, b, 0) for a, b in zip(got, polys, strict=True))

    def test_preflight_polygons_come_from_the_build_cache(self, monkeypatch):
        build_city_model(_hill(), BBOX, LAYERS)
        calls = []
        real = cm.feature_polygons
        monkeypatch.setattr(cm, "feature_polygons", lambda *a, **k: calls.append(1) or real(*a, **k))
        dem = cm.prepare_dem(_hill())      # as build_city_model prepares it
        scale = cm.choose_scale(BBOX, dem.shape, float(dem.min()), float(dem.max()))
        terr = cm.Terrain(scale.z_mm(dem), BBOX, scale)
        st = cm.layer_preflight("buildings", LAYERS["buildings"]["features"],
                                cm.DEFAULT_LAYERS["buildings"], terr)
        assert calls == [] and st["polygons"] == 2


# ---------------------------------------------------------------------------
# Trails: hiking paths outside town only
# ---------------------------------------------------------------------------

class TestTrails:
    def test_tag_filter(self):
        assert is_hiking_trail({"highway": "path"})
        assert is_hiking_trail({"highway": "track"})
        assert is_hiking_trail({"route": "hiking"})
        assert is_hiking_trail({"highway": "footway", "sac_scale": "hiking"})
        assert is_hiking_trail({"highway": "footway", "name": "Vereda de la Estrella"})
        assert not is_hiking_trail({"highway": "footway"})
        assert not is_hiking_trail({"highway": "footway", "footway": "sidewalk", "name": "Calle"})
        assert not is_hiking_trail({"highway": "footway", "footway": "crossing", "sac_scale": "x"})
        assert not is_hiking_trail({"highway": "steps"})
        assert not is_hiking_trail({"highway": "footway", "name": float("nan")})

    def _line(self, x0_m, y_m, x1_m, **props):
        return {"geometry": {"type": "LineString", "coordinates": [
            [LON0 + x0_m / M_LON, LAT0 + y_m / M_LAT], [LON0 + x1_m / M_LON, LAT0 + y_m / M_LAT]]},
            "properties": {"highway": "path", **props}}

    def _town(self):
        # A block of houses 0..400 m east, and a street along y = -20 m.
        houses = [{"geometry": _rect(x, 0, 30, 30), "properties": {"height_m": 10}}
                  for x in range(0, 400, 50)]
        street = {"geometry": {"type": "LineString", "coordinates": [
            [LON0 - 800 / M_LON, LAT0 - 20 / M_LAT], [LON0 + 800 / M_LON, LAT0 - 20 / M_LAT]]},
            "properties": {"road_width_m": 8}}
        return houses, street

    def test_town_paths_and_sidewalks_dropped_hill_path_kept(self):
        houses, street = self._town()
        in_town = self._line(0, 40, 350)                     # between the houses
        sidewalk = self._line(-700, -23, -500)                 # beside the street, out of town
        hill = self._line(-700, 300, -300)                     # far from both
        scale = cm.choose_scale(BBOX, FLAT.shape, 100, 100)
        terr = cm.Terrain(scale.z_mm(FLAT), BBOX, scale)
        kept, st = cm.filter_trails([in_town, sidewalk, hill], terr, houses, [street])
        assert kept == [hill]
        assert st == {"trails_kept": 1, "trails_dropped": 2, "trails_in_town": 1,
                      "trails_along_roads": 1}

    def test_build_reports_kept_and_dropped(self):
        houses, street = self._town()
        trails = _fc(self._line(0, 40, 350), self._line(-700, 300, -300))
        m = build_city_model(_hill(), BBOX, {"trails": trails},
                             layer_overrides={"trails": {"enabled": True}},
                             trail_context={"buildings": _fc(*houses), "roads": _fc(street)})
        rep = m.report["layers"]["trails"]
        assert rep["trails_kept"] == 1 and rep["trails_dropped"] == 1

    def test_trails_are_off_by_default(self):
        m = build_city_model(_hill(), BBOX, {"trails": _fc(self._line(-700, 300, -300))})
        assert "trails" not in m.report["layers"]

    def test_city_task_does_not_fetch_trails_unless_enabled(self, client, monkeypatch):
        import app.server.core.city_model_task as task_mod

        fetched = []
        monkeypatch.setattr(task_mod, "get_city_layers", lambda *a, **k: {"buildings": _fc(_b(0))})
        monkeypatch.setattr(task_mod, "_trails", lambda *a, **k: fetched.append(1) or _fc())
        r = client.get("/api/terrain/dem", params={**BBOX, "dim": 60, "projection": "none"})
        body = {"format": "city", "dem_id": r.json()["dem_id"], "name": "t"}
        from app.server.core.export_tasks import ExportTask

        task_mod.run_city_model(dict(body), ExportTask("t1"))
        assert fetched == []
        task_mod.run_city_model({**body, "layers": {"trails": {"enabled": True}}}, ExportTask("t2"))
        assert fetched == [1]


# ---------------------------------------------------------------------------
# Slab triangulation without Triangle
# ---------------------------------------------------------------------------

class TestTriangulatePolygon:
    def _check(self, poly, extra=None, inset=0.0):
        v, f = triangulate_polygon(poly, extra, inset)
        area = 0.5 * np.abs(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]))
        assert area.sum() == pytest.approx(poly.area, rel=1e-9)
        assert (area > 1e-12).all()
        # Every outline edge (after noding) is an edge of the triangulation.
        e = {tuple(sorted(map(tuple, v[[a, b]]))) for t in f for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))}
        for line in shapely.get_parts(shapely.node(poly.boundary)):
            c = np.asarray(line.coords)
            for a, b in zip(c[:-1], c[1:], strict=True):
                assert tuple(sorted([tuple(a), tuple(b)])) in e
        return v, f

    def test_thin_strip_with_interior_points(self):
        # A 0.8 mm-wide, 60 mm-long strip split every 4 mm with points inside: the
        # case that needs cavity recovery (long outline edges, points nearby).
        strip = shapely.segmentize(shapely.box(0, 0, 60, 0.8), 4.0)
        pts = np.column_stack([np.arange(0.5, 60, 1.0), np.full(60, 0.4)])
        v, f = self._check(strip, pts, 0.25)
        assert len(v) == len(shapely.get_coordinates(strip)) - 1 + 60   # no point added

    def test_hole_touching_the_outline_at_a_vertex_on_an_edge(self):
        outer = shapely.box(0, 0, 10, 10)
        hole = shapely.Polygon([(5, 0), (6, 2), (4, 2)])     # touches the bottom edge at (5, 0)
        poly = shapely.Polygon(outer.exterior.coords, [hole.exterior.coords])
        assert poly.is_valid
        self._check(poly)

    def test_face_count_is_eulers(self):
        poly = shapely.segmentize(shapely.box(0, 0, 20, 10), 1.0)
        pts = np.column_stack([np.random.default_rng(1).uniform(1, 19, 50),
                               np.random.default_rng(2).uniform(1, 9, 50)])
        v, f = self._check(poly, pts, 0.25)
        boundary = len(shapely.get_coordinates(poly)) - 1
        assert len(f) == 2 * len(v) - boundary - 2
