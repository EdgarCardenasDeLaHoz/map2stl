"""Vector rivers: river reaches cut as solids by the feature stage instead of the raster carve.

Unit tests for the pieces (depth classes, the min-depth filter, round caps, the tiled
union, the "auto" rule and the request plumbing) and an integration test that exports
a small synthetic region with the option on (no network: the river source is replaced
by a two-reach GeoDataFrame).
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
import shapely
from shapely.geometry import LineString

from app.server.core import export as export_mod
from app.server.core.export_params import (
    VECTOR_RIVERS_AUTO_MIN_SIDE_KM,
    ExportContext,
    resolve_vector_rivers,
    vector_rivers_blocker,
)
from city2stl import city_model as cm
from geo2stl import water_layers as wl

BBOX = dict(north=37.19, south=37.172, east=-3.578, west=-3.605)   # ~2.4 x 2 km
BIG = dict(north=10.0, south=8.0, east=-60.0, west=-62.0)          # ~220 x 222 km


def _hill(h=120, w=150):
    y, x = np.mgrid[0:h, 0:w]
    return 100 + 80 * np.exp(-(((x - w / 2) / 30) ** 2 + ((y - h / 2) / 25) ** 2))


def _reaches() -> gpd.GeoDataFrame:
    """A big river across the box and a shallow brook (0.075 mm at river_depth_mm 0.6)."""
    main = LineString([(-3.603, 37.174), (-3.590, 37.181), (-3.580, 37.188)])
    brook = LineString([(-3.600, 37.188), (-3.594, 37.184)])
    return gpd.GeoDataFrame({"ORD_STRA": [5, 2], "DIS_AV_CMS": [500.0, 0.5]},
                            geometry=[main, brook], crs="EPSG:4326")


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------

class TestDepthClasses:
    def test_rounds_to_the_step_and_never_below_one_step(self):
        props = [{"depth_mm": 0.46}, {"depth_mm": 0.474}, {"depth_mm": 0.01}, {}]
        groups = cm.depth_classes(props, 0.05, 0.5)
        assert groups == {0.45: [0, 1], 0.05: [2], 0.5: [3]}

    def test_rivers_layer_is_engraved_with_round_lines(self):
        style = cm.DEFAULT_LAYERS["rivers"]
        assert style.mode == "engraved" and style.round_lines and style.depth_step_mm == 0.05
        assert "rivers" in cm.LAYER_PRIORITY


class TestMinDepthFilter:
    def test_drop_shallow_reaches(self):
        gdf = _reaches()
        d = wl.reach_carve_depth_m(gdf)
        assert d[0] > 3.0 and d[1] == pytest.approx(0.5)
        kept = wl.drop_shallow_reaches(gdf, 1.0)
        assert list(kept["ORD_STRA"]) == [5]
        assert len(wl.drop_shallow_reaches(gdf, 0.0)) == 2
        # The depth scale is part of the carve depth.
        assert len(wl.drop_shallow_reaches(gdf, 1.0, depth_scale=3.0)) == 2

    def test_min_carve_depth_inverts_the_print_rule(self):
        rule = {"ref_m": 4.0, "river_depth_mm": 0.6}
        m = wl.min_carve_depth_m(0.1, **rule)
        assert wl.reach_print_depth_mm(m, **rule) == pytest.approx(0.1)
        # Deeper than the reference is capped at the print depth.
        assert wl.reach_print_depth_mm(9.0, **rule) == pytest.approx(0.6)
        # Without river_depth_mm the depth goes through the vertical scale.
        assert wl.reach_print_depth_mm(2.0, z_mm_per_m=0.25) == pytest.approx(0.5)
        assert wl.min_carve_depth_m(0.5, z_mm_per_m=0.25) == pytest.approx(2.0)

    def test_fetch_hydrorivers_passes_the_floor(self, monkeypatch):
        from geo2stl import hydrology
        seen = {}

        def fake(n, s, e, w, min_order=3, min_depth_m=0.0, depth_scale=1.0):
            seen.update(min_order=min_order, min_depth_m=min_depth_m, depth_scale=depth_scale)

        monkeypatch.setattr(hydrology, "fetch_hydrorivers", fake)
        wl.RIVER_FETCH["hydrorivers"](1, 0, 1, 0, {"min_order": 4, "min_depth_m": 0.7,
                                                   "depth_scale": 2.0})
        assert seen == {"min_order": 4, "min_depth_m": 0.7, "depth_scale": 2.0}


class TestRoundCaps:
    def _terrain(self):
        scale = cm.choose_scale(BBOX, (120, 150), 100, 180, mm_per_px=1.0, z_mode="true")
        return cm.Terrain(np.full((120, 150), 10.0), BBOX, scale)

    def test_round_lines_reach_past_the_end_point(self):
        terrain = self._terrain()
        feat = {"type": "Feature", "properties": {"width": 100.0},
                "geometry": {"type": "LineString",
                             "coordinates": [[-3.600, 37.181], [-3.585, 37.181]]}}
        flat = cm.LayerStyle("engraved", line_width_m=100.0)
        rnd = cm.LayerStyle("engraved", line_width_m=100.0, round_lines=True)
        (pf,), _, _ = cm.feature_polygons("x", [feat], flat, terrain)
        (pr,), _, _ = cm.feature_polygons("x", [feat], rnd, terrain)
        half = float(terrain.m_to_mm(50.0))
        end_x = float(terrain.project(np.array([shapely.Point(-3.585, 37.181)]))[0].x)
        assert pf.bounds[2] == pytest.approx(end_x, abs=0.05)
        assert pr.bounds[2] == pytest.approx(end_x + half, abs=0.1)
        assert pr.area > pf.area

    def test_round_lines_change_the_polygon_cache_key_only_when_set(self):
        terrain = self._terrain()
        base = cm.LayerStyle("engraved")
        k0 = cm._polygons_key("x", [], base, terrain, "d")
        k1 = cm._polygons_key("x", [], cm.LayerStyle("engraved", depth_step_mm=0.05), terrain, "d")
        k2 = cm._polygons_key("x", [], cm.LayerStyle("engraved", round_lines=True), terrain, "d")
        assert k0 == k1 != k2


class TestTiledUnion:
    def test_matches_union_all(self, monkeypatch):
        rng = np.random.default_rng(1)
        pts = rng.uniform(-40, 160, size=(400, 2))     # negative cells too
        polys = shapely.buffer(shapely.points(pts), rng.uniform(1, 6, 400))
        monkeypatch.setattr(cm, "TILED_UNION_MIN", 10)
        tiled = cm.union_polygons(list(polys), tile_mm=25.0)
        assert tiled.area == pytest.approx(shapely.union_all(polys).area, rel=1e-9)


# ---------------------------------------------------------------------------
# The option: "auto" and the request plumbing
# ---------------------------------------------------------------------------

class TestAutoRule:
    def test_small_box_is_off_big_box_is_on(self):
        assert resolve_vector_rivers("auto", BBOX) is False
        assert resolve_vector_rivers("auto", BIG) is True
        assert resolve_vector_rivers(None, BIG) is True          # missing = auto
        assert resolve_vector_rivers("auto", None) is False

    def test_threshold_is_the_longest_side(self):
        side = VECTOR_RIVERS_AUTO_MIN_SIDE_KM / 111.32
        tall = dict(north=1.05 * side, south=0.0, east=0.1, west=0.0)   # narrow but long
        assert resolve_vector_rivers("auto", tall) is True
        short = dict(north=0.9 * side, south=0.0, east=0.9 * side, west=0.0)
        assert resolve_vector_rivers("auto", short) is False

    @pytest.mark.parametrize("value, bbox, want", [
        (True, BBOX, True), ("true", BBOX, True), ("on", BBOX, True),
        (False, BIG, False), ("false", BIG, False), ("off", BIG, False)])
    def test_explicit_values_override_auto(self, value, bbox, want):
        assert resolve_vector_rivers(value, bbox) is want

    def test_nonlinear_projection_blocks(self):
        assert vector_rivers_blocker({"projection": "none"}) is None
        assert vector_rivers_blocker({"projection": "cosine"}) is None
        assert vector_rivers_blocker({"projection": "cosine", "maintain_dimensions": True})
        assert vector_rivers_blocker({"projection": "mercator"})


class TestPlumbing:
    SPEC = [{"source": "local", "blend_mode": "base"},
            {"source": "hydrorivers", "blend_mode": "add", "weight": 2.0,
             "options": {"min_order": 4, "depth_scale": 1.5}},
            {"source": "lakes", "blend_mode": "add"}]

    def _fake_composite(self, monkeypatch, seen):
        import app.server.routers.composite as comp

        def fake(bbox, dim, layers, projection="none", clip_valid_region=True,
                 maintain_dimensions=False, *, split_carve=False, warnings=None, water_out=None):
            seen["need"] = water_out.get("need_lake_carve")
            dem = _hill(30, 40)
            carve = np.zeros_like(dem)
            carve[10:12, :] = -3.0
            water_out["lake_carve"] = np.zeros_like(dem)
            return dem, carve

        monkeypatch.setattr(comp, "compute_composite_dem", fake)

    def test_flag_and_layers_reach_the_context(self, monkeypatch):
        seen = {}
        self._fake_composite(monkeypatch, seen)
        p = ExportContext.from_request({"bbox": BBOX, "composite_layers": self.SPEC,
                                        "vector_rivers": True,
                                        "vector_rivers_min_depth_mm": 0.2})
        assert p.vector_rivers and seen["need"]
        assert p.vector_rivers_min_depth_mm == 0.2
        assert p.river_layers == [{"source": "hydrorivers", "weight": 2.0,
                                   "options": {"min_order": 4, "depth_scale": 1.5}}]
        assert p.lake_carve_m is not None and p.lake_carve_m.shape == p.carve_m.shape

    def test_auto_is_off_for_a_small_box(self, monkeypatch):
        seen = {}
        self._fake_composite(monkeypatch, seen)
        p = ExportContext.from_request({"bbox": BBOX, "composite_layers": self.SPEC})
        assert not p.vector_rivers and not seen["need"] and p.river_layers is None
        assert p.vector_rivers_min_depth_mm == 0.1

    def test_no_river_layer_or_a_curved_projection_falls_back(self, monkeypatch):
        seen = {}
        self._fake_composite(monkeypatch, seen)
        p = ExportContext.from_request({"bbox": BBOX, "vector_rivers": True,
                                        "composite_layers": [self.SPEC[0], self.SPEC[2]]})
        assert not p.vector_rivers and p.vector_rivers_note is None
        p = ExportContext.from_request({"bbox": BBOX, "vector_rivers": True,
                                        "composite_layers": self.SPEC,
                                        "dem": {"projection": "mercator"}})
        assert not p.vector_rivers and "mercator" in p.vector_rivers_note
        assert export_mod._vector_rivers_headers(p)["X-Vector-Rivers"].startswith("raster river")


# ---------------------------------------------------------------------------
# Integration: a small export with the option on
# ---------------------------------------------------------------------------

def _context(vector: bool) -> ExportContext:
    """The hill with a 4 m carve along the main river (the reference depth)."""
    dem = _hill()
    carve = np.zeros_like(dem)
    for i in range(120):
        j = int(10 + i * 1.1)
        if j < 150:
            carve[max(0, 119 - i - 1):120 - i, max(0, j - 2):j + 2] = -4.0
    return ExportContext(dem_values=dem.ravel().tolist(), height=120, width=150,
                         bbox=BBOX, z_mode="true", median_size=0, carve_m=carve,
                         river_depth_mm=0.6, vector_rivers=vector,
                         lake_carve_m=np.zeros_like(dem),
                         river_layers=[{"source": "hydrorivers", "options": {}, "weight": 1.0}]
                         if vector else None)


@pytest.fixture
def synthetic_rivers(monkeypatch):
    monkeypatch.setitem(wl.RIVER_FETCH, "hydrorivers",
                        lambda n, s, e, w, opts: wl.drop_shallow_reaches(
                            _reaches(), float(opts.get("min_depth_m", 0.0))))
    monkeypatch.setenv("MAP2STL_CITY_CACHE", "0")


class TestVectorExport:
    def test_export_cuts_the_rivers_as_solids(self, synthetic_rivers):
        raster = export_mod._prepare_export_mesh(_context(False), {})
        p = _context(True)
        vector = export_mod._prepare_export_mesh(p, {})
        assert vector.is_watertight and export_mod._watertight(vector)
        rep = p.vector_rivers_report
        assert rep["reaches"] == 1                       # the brook is under 0.1 mm
        assert rep["layer"]["depth_classes_mm"] == [0.45]
        # The channel is there: the uncut terrain holds more volume, by about
        # the channel's area x depth.
        plain = export_mod._prepare_export_mesh(
            ExportContext(**{**_context(False).__dict__, "carve_m": None}), {})
        lost = plain.volume - vector.volume
        assert lost > 50.0
        # The channel walls add faces over the uncut terrain. (Against the raster run
        # it depends on the grid: the adaptive terrain mesh spends faces on the raster
        # carve's steps, so on this small grid the raster model has more.)
        assert len(vector.faces) > len(plain.faces)
        assert raster.volume > vector.volume - 1.0
        assert export_mod._vector_rivers_headers(p)["X-Vector-Rivers"] == "vector: 1 reaches"

    def test_terrain_stage_keeps_the_reference_and_skips_the_river_carve(self, synthetic_rivers):
        p = _context(True)
        field = export_mod.terrain_stage(p, {}, vector_rivers=True)
        raster = export_mod.terrain_stage(p, {})          # previews: the raster carve
        assert field.river_ref_m == pytest.approx(4.0)
        assert (raster.z_mm < field.z_mm - 0.3).any()      # only the raster one is carved
        assert field.water is not None and (field.water > 0).any()

    def test_puzzle_takes_the_boolean_path(self, synthetic_rivers, monkeypatch, tmp_path):
        import zipfile

        from app.server.core.export_tasks import ExportTask

        p = _context(True)
        monkeypatch.setattr(export_mod, "_parse_export_params", lambda data: p)
        task = ExportTask("t")
        export_mod.generate_puzzle({"split_cols": 2, "split_rows": 1}, task)
        assert task.status == "complete", task.message
        import json
        assert json.loads(task.headers["X-Puzzle-Info"])["method"] == "boolean"
        assert task.headers["X-Vector-Rivers"] == "vector: 1 reaches"
        with zipfile.ZipFile(task.result_path) as zf:
            assert sum(n.endswith(".obj") for n in zf.namelist()) == 2
