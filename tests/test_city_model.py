"""Vector city model (city2stl/city_model.py) and the ``city`` export task."""

import io
import json
import time
import zipfile

import numpy as np
import pytest
import shapely
import trimesh
from shapely.geometry import Polygon, box

from city2stl.city_model import (
    DEFAULT_LAYERS,
    MIN_LEFTOVER_WIDTH_MM,
    SNAP_MM,
    LayerStyle,
    assemble_parts,
    build_city_model,
    building_cover,
    choose_scale,
    drop_building_copies,
    feature_polygons,
    is_open_enclosure,
    resolve_layers,
)

BBOX = dict(north=37.19, south=37.172, east=-3.578, west=-3.605)   # ~3 km diagonal


def _hill(h=120, w=150):
    y, x = np.mgrid[0:h, 0:w]
    return 100 + 80 * np.exp(-(((x - w / 2) / 30) ** 2 + ((y - h / 2) / 25) ** 2))


def _square(lon, lat, d=0.0004):
    return {"type": "Polygon", "coordinates": [[[lon - d, lat - d], [lon + d, lat - d],
                                                 [lon + d, lat + d], [lon - d, lat + d],
                                                 [lon - d, lat - d]]]}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


LAYERS = {
    "buildings": _fc(
        {"geometry": _square(-3.588, 37.183), "properties": {"height_m": 20}},     # on the slope
        {"geometry": _square(-3.6049, 37.18), "properties": {"height_m": 12}},     # crosses west edge
        {"geometry": _square(-3.70, 37.18), "properties": {"height_m": 12}},       # outside
    ),
    "roads": _fc({"geometry": {"type": "LineString",
                               "coordinates": [[-3.60, 37.175], [-3.58, 37.187]]},
                  "properties": {"road_width_m": 10}}),
    "waterways": _fc({"geometry": {"type": "LineString",
                                   "coordinates": [[-3.603, 37.173], [-3.580, 37.176]]},
                      "properties": {"waterway": "river"}}),
}


class TestScale:
    def test_auto_is_true_scale_for_small_regions(self):
        s = choose_scale(BBOX, (200, 240), 100, 180)
        assert s.z_mode == "true"
        assert s.z_mm_per_m == pytest.approx(s.mm_per_px / s.m_per_px)

    def test_auto_fits_height_for_large_regions(self):
        big = dict(north=40.0, south=39.0, east=-105.0, west=-106.0)
        s = choose_scale(big, (600, 600), 1000, 4000, fit_height_mm=30)
        assert s.z_mode == "fit"
        assert (4000 - 1000) * s.z_mm_per_m == pytest.approx(30)

    def test_override(self):
        s = choose_scale(BBOX, (200, 240), 100, 180, z_mode="fit", fit_height_mm=10)
        assert s.z_mode == "fit"


class TestBuild:
    @pytest.fixture(scope="class")
    def model(self):
        return build_city_model(_hill(), BBOX, LAYERS)

    def test_merged_is_a_valid_solid(self, model):
        m = model.merged
        assert m.is_watertight and m.is_winding_consistent and m.volume > 0

    def test_parts_are_watertight_and_disjoint(self, model):
        parts = list(model.parts.values())
        assert all(p.is_watertight for p in parts)
        assert sum(p.volume for p in parts) == pytest.approx(model.merged.volume, rel=1e-3)

    def test_features_stay_inside_the_model(self, model):
        t = model.parts["terrain"].bounds
        b = model.parts["buildings"].bounds
        assert b[0][0] >= t[0][0] - 1e-6 and b[1][0] <= t[1][0] + 1e-6
        assert b[0][1] >= t[0][1] - 1e-6 and b[1][1] <= t[1][1] + 1e-6
        assert model.report["layers"]["buildings"]["solids"] == 2

    def test_building_sits_on_terrain_high_with_true_height(self, model):
        bodies = model.parts["buildings"].split(only_watertight=False)
        tall = max(bodies, key=lambda b: b.bounds[1][2])
        # roof = highest ground under the footprint + 20 m at the model's vertical scale
        x0, y0, _ = tall.bounds[0]
        x1, y1, _ = tall.bounds[1]
        gx, gy = np.meshgrid(np.linspace(x0, x1, 9), np.linspace(y0, y1, 9))
        terrain = model.parts["terrain"]
        rays = np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, 1000.0)])
        hits, idx, _ = terrain.ray.intersects_location(rays, np.tile([0, 0, -1.0], (len(rays), 1)))
        ground_max = hits[:, 2].max()
        roof = tall.bounds[1][2]
        assert roof == pytest.approx(ground_max + 20 * model.scale.z_mm_per_m, abs=0.25)

    def test_water_is_cut_below_the_surface(self, model):
        assert model.report["layers"]["waterways"]["solids"] == 1
        plain = build_city_model(_hill(), BBOX, {})
        assert model.parts["terrain"].volume < plain.merged.volume

    def test_layer_toggle_and_override(self):
        styles = resolve_layers({"roads": {"enabled": False}, "buildings": {"height_scale": 2}})
        assert not styles["roads"].enabled
        assert styles["buildings"].height_scale == 2
        assert styles["walls"] == DEFAULT_LAYERS["walls"]
        m = build_city_model(_hill(), BBOX, LAYERS, layer_overrides={"roads": {"enabled": False}})
        assert "roads" not in m.parts


class TestCityExportTask:
    def test_zip_contains_stl_3mf_and_report(self, client, monkeypatch):
        import app.server.core.city_model_task as task_mod

        monkeypatch.setattr(task_mod, "get_city_layers", lambda *a, **k: dict(LAYERS))
        r = client.get("/api/terrain/dem", params={**BBOX, "dim": 60, "projection": "none"})
        dem_id = r.json()["dem_id"]
        r = client.post("/api/export/start", json={
            "format": "city", "dem_id": dem_id, "name": "t",
            "layers": {"trails": {"enabled": False}}})
        task_id = r.json()["task_id"]
        for _ in range(300):
            st = client.get(f"/api/export/status/{task_id}").json()
            if st["status"] != "running":
                break
            time.sleep(0.2)
        assert st["status"] == "complete", st
        z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/export/download/{task_id}").content))
        names = set(z.namelist())
        assert {"t.stl", "t.3mf", "report.json"} <= names
        mesh = trimesh.load(io.BytesIO(z.read("t.stl")), file_type="stl")
        assert mesh.is_watertight
        report = json.loads(z.read("report.json"))
        assert report["scale"]["z_mode"] == "true"


def _line(props, lon0=-3.60, lon1=-3.58, lat=37.18):
    return {"geometry": {"type": "LineString", "coordinates": [[lon0, lat], [lon1, lat]]},
            "properties": props}


class TestSkiRuns:
    """F-PISTES: downhill run lines print 30 m wide, and the layer turns on where they exist."""

    @staticmethod
    def _width_mm(props):
        m = build_city_model(_hill(), BBOX, {"trails": _fc(_line(props))},
                             layer_overrides={"trails": {"enabled": True}})
        ys = m.parts["trails"].vertices[:, 1]
        return float(ys.max() - ys.min())

    def test_downhill_line_is_wider_than_a_footpath(self):
        run = self._width_mm({"piste:type": "downhill"})
        path = self._width_mm({"highway": "path"})
        assert run > 2 * path   # 30 m against 3 m (the path is widened to the 0.8 mm print minimum)

    def test_piste_kind(self):
        from city2stl.city_model import PISTE_LINE_WIDTH_M, _line_width_m, is_downhill_piste
        style = DEFAULT_LAYERS["trails"]
        assert PISTE_LINE_WIDTH_M == 30.0
        assert _line_width_m("trails", {"piste:type": "downhill"}, style) == 30.0
        assert _line_width_m("trails", {"route": "piste"}, style) == 30.0
        assert _line_width_m("trails", {"piste:type": "nordic", "route": "piste"}, style) == 3.0
        assert _line_width_m("trails", {"highway": "path"}, style) == 3.0
        assert _line_width_m("trails", {"piste:type": "downhill", "width": "12"}, style) == 12.0
        assert not is_downhill_piste({"piste:type": float("nan")})

    def test_default_on_only_where_pistes_exist(self, monkeypatch):
        import app.server.core.city_model_task as tm
        run, path = _fc(_line({"piste:type": "downhill"})), _fc(_line({"highway": "path"}))
        mountain, flat = [0.0, 900.0] * 8, [100.0, 120.0] * 8
        calls = []

        def fake(bbox, cached_only=False):
            calls.append(cached_only)
            return None if cached_only else fake.fc
        monkeypatch.setattr(tm, "_trails", fake)
        fake.fc = run
        assert tm.wants_ski_trails({}, BBOX, mountain)
        assert calls == [True, False]            # cache miss, then one fetch
        calls.clear()
        assert not tm.wants_ski_trails({}, BBOX, flat)
        assert calls == [True]                   # flat box: no Overpass call
        fake.fc = path
        assert not tm.wants_ski_trails({}, BBOX, mountain)   # paths alone do not count

    def test_explicit_choice_wins(self, monkeypatch):
        import app.server.core.city_model_task as tm
        monkeypatch.setattr(tm, "_trails", lambda bbox, cached_only=False: _fc(
            _line({"piste:type": "downhill"})))
        mountain = [0.0, 900.0] * 8
        assert tm.wants_ski_trails({"layers": {}}, BBOX, mountain)
        assert not tm.wants_ski_trails({"layers": {"trails": {"enabled": False}}}, BBOX, mountain)
        assert not tm.wants_ski_trails({"layers": {"trails": {"enabled": True}}}, BBOX, mountain)


class TestCityOsmParams:
    """The build reads the OSM cache entry the Cities panel loaded (tolerance/min area)."""

    def test_defaults_are_the_cities_panel_defaults(self):
        from app.server.core.city_model_task import city_osm_params
        assert city_osm_params({}) == (3.0, 5.0)

    def test_request_values_win_and_bad_values_fall_back(self):
        from app.server.core.city_model_task import city_osm_params
        assert city_osm_params({"simplify_tolerance": 0.5, "min_area": 20}) == (0.5, 20.0)
        assert city_osm_params({"simplify_tolerance": "x", "min_area": -1}) == (3.0, 5.0)
        assert city_osm_params({"simplify_tolerance": float("nan")}) == (3.0, 5.0)

    def test_coarse_detail_raises_min_area_like_the_cities_route(self):
        from app.server.config import COARSE_MIN_BUILDING_AREA_M2
        from app.server.core.city_model_task import city_osm_params
        assert city_osm_params({"min_area": 5, "detail": "coarse"}) == (
            3.0, COARSE_MIN_BUILDING_AREA_M2)

    @pytest.mark.parametrize("extra, expected", [
        ({}, (3.0, 5.0)),                                          # old clients
        ({"simplify_tolerance": 2.0, "min_area": 12.0}, (2.0, 12.0)),
    ])
    def test_build_passes_them_to_the_osm_cache(self, client, monkeypatch, extra, expected):
        import app.server.core.city_model_task as task_mod

        seen = []

        def fake(north, south, east, west, layers, simplify_tolerance=0.5, min_area=5.0, **kw):
            seen.append((simplify_tolerance, min_area))
            return {}

        monkeypatch.setattr(task_mod, "get_city_layers", fake)
        dem = _hill(20, 25)
        r = client.post("/api/export/start", json={
            "format": "city", "name": "t", "bbox": BBOX, "dem_values": dem.ravel().tolist(),
            "height": 20, "width": 25, "layers": {"trails": {"enabled": False}}, **extra})
        task_id = r.json()["task_id"]
        for _ in range(300):
            st = client.get(f"/api/export/status/{task_id}").json()
            if st["status"] != "running":
                break
            time.sleep(0.2)
        assert st["status"] == "complete", st
        assert seen == [expected]


class TestTwoStagePipeline:
    """Every mesh export: terrain stage (raster) -> city model (vector)."""

    def _city(self, client, body):
        r = client.post("/api/export/start", json={"format": "city", "name": "t", **body})
        task_id = r.json()["task_id"]
        for _ in range(300):
            st = client.get(f"/api/export/status/{task_id}").json()
            if st["status"] != "running":
                break
            time.sleep(0.2)
        assert st["status"] == "complete", st
        z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/export/download/{task_id}").content))
        return json.loads(z.read("report.json")), trimesh.load(
            io.BytesIO(z.read("t.stl")), file_type="stl")

    def test_city_build_uses_edited_dem_values(self, client, monkeypatch):
        import app.server.core.city_model_task as task_mod

        monkeypatch.setattr(task_mod, "get_city_layers", lambda *a, **k: {})
        dem = _hill(40, 50)
        body = {"bbox": BBOX, "dem_values": dem.ravel().tolist(), "height": 40, "width": 50,
                "layers": {"trails": {"enabled": False}}, "z_mode": "true", "base_height": 5}
        report, mesh = self._city(client, body)
        assert report["dem_shape"] == [40, 50]
        relief = (dem.max() - dem.min()) * report["scale"]["z_mm_per_m"]
        assert mesh.extents[2] == pytest.approx(5 + relief, abs=0.5)

    def test_city_build_applies_the_terrain_stage(self, client, monkeypatch):
        import app.server.core.city_model_task as task_mod

        monkeypatch.setattr(task_mod, "get_city_layers", lambda *a, **k: {})
        dem = _hill(40, 50) - 150            # mostly below sea level
        body = {"bbox": BBOX, "dem_values": dem.ravel().tolist(), "height": 40, "width": 50,
                "layers": {"trails": {"enabled": False}}, "z_mode": "true",
                "sea_level_cap": True, "median_size": 0}
        report, mesh = self._city(client, body)
        relief = dem.max() * report["scale"]["z_mm_per_m"]    # capped at 0 m
        assert mesh.extents[2] == pytest.approx(5 + relief, abs=0.5)

    def test_terrain_export_is_the_city_model_without_layers(self):
        from app.server.core.export import _prepare_export_mesh
        from app.server.core.export_params import ExportContext

        dem = _hill()
        data = {"bbox": BBOX, "dem_values": dem.ravel().tolist(), "height": 120, "width": 150,
                "z_mode": "true"}
        mesh = _prepare_export_mesh(ExportContext.from_request(data), data)
        city = build_city_model(dem, BBOX, {})
        assert mesh.is_watertight
        assert mesh.volume == pytest.approx(city.merged.volume, rel=1e-6)
        assert len(mesh.faces) < 2 * 119 * 149      # adaptive, not one quad per pixel

    def test_feature_channels_never_reach_the_terrain(self):
        from app.server.core.export_params import mesh_composite_layers

        spec = [{"source": "SRTMGL1"}, {"source": "water_esa"}, {"source": "osm_buildings"},
                {"source": "osm_roads"}, {"source": "osm_walls"}, {"source": "osm_waterways"}]
        assert [s["source"] for s in mesh_composite_layers(spec)] == ["SRTMGL1", "water_esa"]
        assert mesh_composite_layers([{"source": "osm_buildings"}]) is None


class TestSimplificationAtPrintScale:
    def test_terrain_tin_stays_within_tolerance(self):
        import matplotlib.tri as mtri

        from city2stl.city_model import terrain_tin

        z = _hill(80, 100) * 0.1
        idx, tris = terrain_tin(z, max_error=0.05)
        h, w = z.shape
        # Check the triangles actually returned (an independent interpolator), not a
        # re-triangulation: on a pixel grid many point sets are cocircular, so two
        # Delaunay codes may pick different diagonals.
        remap = np.full(h * w, -1)
        remap[idx] = np.arange(len(idx))
        ii, jj = np.divmod(idx, w)
        tri = mtri.Triangulation(jj.astype(float), ii.astype(float), remap[tris])
        interp = mtri.LinearTriInterpolator(tri, z.ravel()[idx])
        gj, gi = np.meshgrid(np.arange(w, dtype=float), np.arange(h, dtype=float))
        err = np.abs(np.asarray(interp(gj, gi).filled(np.nan)) - z)
        assert np.nanmax(err) <= 0.05 + 1e-9
        assert len(tris) < 2 * (h - 1) * (w - 1) / 3      # substantially fewer triangles

    def test_dense_road_network_builds(self):
        # A grid of crossing streets dissolves into one slab with many holes.
        lons = np.linspace(-3.603, -3.580, 12)
        lats = np.linspace(37.174, 37.188, 12)
        feats = [{"geometry": {"type": "LineString", "coordinates": [[x, lats[0]], [x, lats[-1]]]},
                  "properties": {"road_width_m": 8}} for x in lons]
        feats += [{"geometry": {"type": "LineString", "coordinates": [[lons[0], y], [lons[-1], y]]},
                   "properties": {"road_width_m": 8}} for y in lats]
        m = build_city_model(_hill(), BBOX, {"roads": _fc(*feats)})
        assert m.report["layers"]["roads"]["slabs"] == 1
        assert m.report["layers"]["roads"]["rejected"] == 0
        assert "roads" in m.parts and m.merged.is_watertight

    def test_draped_slab_takes_detail_from_the_terrain_mesh(self):
        road = {"geometry": {"type": "LineString", "coordinates": [[-3.60, 37.18], [-3.58, 37.18]]},
                "properties": {"road_width_m": 60}}
        flat = build_city_model(np.full((120, 150), 100.0), BBOX, {"roads": _fc(road)},
                                simplify=False)
        hill = build_city_model(_hill(), BBOX, {"roads": _fc(road)}, simplify=False)
        # Flat ground needs no interior points; the hill needs them where it curves.
        assert len(flat.parts["roads"].faces) * 2 < len(hill.parts["roads"].faces)


class TestBuildingParts:
    """Simple 3D Buildings: parts replace the outline and stack on min_height."""

    def _rect(self, w, s, e, n):
        return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}

    def test_cathedral_parts(self):
        flat = np.full((120, 150), 100.0)
        nave = {"geometry": self._rect(-3.5910, 37.1800, -3.5890, 37.1806),
                "properties": {"height_m": 60}}                      # overall height
        body = {"geometry": self._rect(-3.5910, 37.1800, -3.5895, 37.1806),
                "properties": {"height_m": 20, "building:part": "yes", "roof:shape": "gabled"}}
        tower = {"geometry": self._rect(-3.5895, 37.1801, -3.5890, 37.1805),
                 "properties": {"height_m": 45, "building:part": "yes"}}
        spire = {"geometry": self._rect(-3.5894, 37.1802, -3.5891, 37.1804),
                 "properties": {"height_m": 60, "min_height": "45", "building:part": "yes",
                                "roof:shape": "pyramidal", "roof:height": "15"}}
        m = build_city_model(flat, BBOX, {"buildings": _fc(nave, body, tower, spire)})
        rep = m.report["layers"]["buildings"]
        assert rep["parts"] == 3 and rep["outlines_replaced"] == 1
        z = m.scale.z_mm_per_m
        ground = m.merged.vertices[:, 2].min() + m.scale.base_mm
        b = m.parts["buildings"]
        assert b.bounds[1, 2] == pytest.approx(ground + 60 * z, abs=0.3)
        # the nave is not a 60 m block: most of the footprint stays near 20 m
        high = b.vertices[b.vertices[:, 2] > ground + 30 * z]
        assert np.ptp(high[:, 0]) < 0.5 * np.ptp(b.vertices[:, 0])
        assert m.report["merged"]["watertight"]


    def test_church_copy_does_not_cover_the_parts(self):
        """The churches layer's copy of the outline is dropped; the parts stay visible."""
        flat = np.full((120, 150), 100.0)
        outline = self._rect(-3.5910, 37.1800, -3.5890, 37.1806)
        nave = {"geometry": outline, "properties": {"height_m": 45}}
        body = {"geometry": self._rect(-3.5910, 37.1800, -3.5895, 37.1806),
                "properties": {"height_m": 20, "building:part": "yes"}}
        tower = {"geometry": self._rect(-3.5895, 37.1801, -3.5890, 37.1805),
                 "properties": {"height_m": 45, "building:part": "yes"}}
        copy = {"geometry": outline, "properties": {"height_m": 45, "amenity": "place_of_worship"}}
        m = build_city_model(flat, BBOX, {"buildings": _fc(nave, body, tower),
                                          "churches": _fc(copy)})
        assert m.report["layers"]["churches"]["building_copies_dropped"] == 1
        assert "churches" not in m.parts
        z = m.scale.z_mm_per_m
        ground = m.merged.vertices[:, 2].min() + m.scale.base_mm
        high = m.merged.vertices[m.merged.vertices[:, 2] > ground + 30 * z]
        assert np.ptp(high[:, 0]) < 0.5 * np.ptp(m.parts["buildings"].vertices[:, 0])

    def test_drop_building_copies_keeps_what_buildings_lack(self):
        building = {"geometry": self._rect(0, 0, 10, 10), "properties": {}}
        cover = building_cover([building])
        inside = {"geometry": self._rect(1, 1, 9, 9)}
        half_out = {"geometry": self._rect(8, 0, 18, 10)}          # 20 % inside
        elsewhere = {"geometry": self._rect(50, 50, 60, 60)}
        line = {"geometry": {"type": "LineString", "coordinates": [[1, 1], [9, 9]]}}
        kept, dropped = drop_building_copies([inside, half_out, elsewhere, line], cover)
        assert dropped == 1 and kept == [half_out, elsewhere, line]
        assert drop_building_copies([inside], None) == ([inside], 0)


class TestWallRings:
    """Wall and fortification polygons print as their outline ring, in pieces that
    follow the ground (user, 2026-10-14: "Outline ring only"; Granada's Alhambra
    printed as one slab over 84 of its 85 buildings)."""

    SLOPE = np.tile(100 + 1.5 * np.arange(150.0), (120, 1))   # 225 m of rise west -> east

    @staticmethod
    def _top_near(mesh, x, y, r):
        v = mesh.vertices
        near = (np.abs(v[:, 0] - x) < r) & (np.abs(v[:, 1] - y) < r)
        return v[near, 2].max() if near.any() else -np.inf

    @pytest.fixture(scope="class")
    def model(self):
        precinct = {"geometry": _square(-3.5915, 37.181, d=0.0035),     # ~620 m x 780 m
                    "properties": {"height_m": 12, "name": "Precinct"}}
        hall = {"geometry": _square(-3.5915, 37.181), "properties": {"height_m": 10}}
        keep = {"geometry": _square(-3.600, 37.175, d=0.0003), "properties": {"height_m": 20}}
        return build_city_model(self.SLOPE, BBOX, {"fortifications": _fc(precinct),
                                                   "buildings": _fc(hall),
                                                   "towers": _fc(keep)})

    def test_polygon_prints_as_its_outline_ring(self, model):
        f = model.parts["fortifications"]
        (x0, y0, _), (x1, y1, _) = f.bounds
        width = DEFAULT_LAYERS["fortifications"].min_width_mm     # 4 m is under it here
        inner = ((f.vertices[:, 0] > x0 + width + 0.05) & (f.vertices[:, 0] < x1 - width - 0.05)
                 & (f.vertices[:, 1] > y0 + width + 0.05) & (f.vertices[:, 1] < y1 - width - 0.05))
        assert not inner.any()                       # nothing over the open ground inside
        assert model.report["layers"]["fortifications"]["pieces"] > 1
        assert model.report["merged"]["watertight"]

    def test_building_inside_stands_above_the_ring(self, model):
        b = model.parts["buildings"]
        cx, cy = b.vertices[:, :2].mean(axis=0)
        top = b.bounds[1, 2]
        # The hall's roof is part of the merged print, not swallowed by a slab.
        assert self._top_near(model.merged, cx, cy, 3.0) == pytest.approx(top, abs=1e-3)

    def test_ring_top_follows_the_slope(self, model):
        f = model.parts["fortifications"]
        z = model.scale.z_mm_per_m
        (x0, _, _), (x1, _, _) = f.bounds
        quarter = (x1 - x0) / 4
        west = f.vertices[f.vertices[:, 0] < x0 + quarter, 2].max()
        east = f.vertices[f.vertices[:, 0] > x1 - quarter, 2].max()
        rise = self.SLOPE[0, -1] - self.SLOPE[0, 0]
        across_m = rise * (x1 - x0) / (self.SLOPE.shape[1] - 1) / model.scale.mm_per_px
        # One flat top would make both equal; pieces step down the slope with it.
        assert east - west > 0.5 * across_m * z

    def test_tower_polygons_stay_filled(self, model):
        t = model.parts["towers"]
        (x0, y0, _), (x1, y1, _) = t.bounds
        top_area = (x1 - x0) * (y1 - y0)
        assert t.volume > 0.8 * top_area * 20 * model.scale.z_mm_per_m
        assert "pieces" not in model.report["layers"]["towers"]


class TestWater:
    def test_river_follows_the_valley_and_lake_is_flat(self):
        y, x = np.mgrid[0:120, 0:150]
        dem = 100 + 1.5 * x                       # tilted plane: 225 m of fall west -> east
        river = {"geometry": {"type": "LineString", "coordinates": [[-3.604, 37.181], [-3.579, 37.181]]},
                 "properties": {"waterway": "river"}}
        m = build_city_model(dem, BBOX, {"waterways": _fc(river)})
        plain = build_city_model(dem, BBOX, {})
        cut = plain.merged.volume - m.merged.volume
        # a terrain-following 1 mm deep channel, not a trench to the lowest point
        width_mm = m.merged.extents[0]
        assert 0 < cut < width_mm * 10 * 1.5


class TestPrintability:
    def _stl_watertight(self, mesh):
        buf = io.BytesIO()
        mesh.export(buf, file_type="stl")
        buf.seek(0)
        return trimesh.load(buf, file_type="stl").is_watertight

    def test_solids_touching_at_a_corner_stay_watertight_as_stl(self):
        d = 0.0004
        sq = lambda lon, lat: {"type": "Polygon", "coordinates": [[  # noqa: E731
            [lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]]}
        lon, lat = -3.59, 37.18
        feats = [{"geometry": sq(lon, lat), "properties": {"height_m": 20}},
                 {"geometry": sq(lon + d, lat + d), "properties": {"height_m": 12}},
                 {"geometry": sq(lon + d, lat - d), "properties": {"height_m": 15}}]
        m = build_city_model(_hill(), BBOX, {"buildings": _fc(*feats)})
        assert m.report["merged"]["watertight"] and self._stl_watertight(m.merged)

    def test_thin_lines_are_widened_to_print_width(self):
        trail = {"geometry": {"type": "LineString", "coordinates": [[-3.60, 37.18], [-3.58, 37.18]]},
                 "properties": {"width": "0.5"}}
        m = build_city_model(_hill(), BBOX, {"trails": _fc(trail)},
                             layer_overrides={"trails": {"enabled": True}})
        assert m.report["layers"]["trails"]["widened"] == 1
        ys = m.parts["trails"].vertices[:, 1]
        assert ys.max() - ys.min() >= 0.8 - 1e-6

    @staticmethod
    def _house(i, height_m, lon0=-3.59, w_m=3, d_m=25):
        lat, m_lon, m_lat = 37.18, 1 / (111_320 * np.cos(np.radians(37.18))), 1 / 111_320
        x0, x1 = lon0 + i * w_m * m_lon, lon0 + (i + 1) * w_m * m_lon
        ring = [[x0, lat], [x1, lat], [x1, lat + d_m * m_lat], [x0, lat + d_m * m_lat], [x0, lat]]
        return {"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"height_m": height_m}}

    @pytest.mark.parametrize("heights", [[9] * 8, [9, 15] * 4], ids=["same height", "mixed heights"])
    def test_rowhouses_print_as_their_block(self, heights):
        """Thin touching rowhouses are not widened one by one: the row keeps its own
        outline instead of every house growing 0.3 mm into the street (2026-10-02,
        Philadelphia: 5 m houses are 0.2 mm wide). Same heights merge; mixed heights
        do not, but their cluster is wide enough to print."""
        houses = [self._house(i, h) for i, h in enumerate(heights)]      # 3 m x 25 m, touching
        m = build_city_model(np.full((120, 150), 100.0), BBOX, {"buildings": _fc(*houses)})
        mm_per_m = m.scale.mm_per_px / m.scale.m_per_px
        assert 3 * mm_per_m < DEFAULT_LAYERS["buildings"].min_width_mm      # each house is thin
        ext = m.parts["buildings"].extents
        assert ext[0] == pytest.approx(24 * mm_per_m, abs=0.1)              # the row, not +0.6 mm
        assert ext[1] == pytest.approx(25 * mm_per_m, abs=0.1)
        assert m.report["layers"]["buildings"]["widened"] == 0

    def test_small_merged_sheds_still_print(self):
        """Two touching 3 m sheds of one height merge into a group smaller than
        MIN_FOOTPRINT_MM2; the group prints as drawn, not dropped (2026-10-02: such
        groups vanished, their members counted as merged)."""
        sheds = [self._house(i, 3, d_m=3) for i in range(2)]
        m = build_city_model(np.full((120, 150), 100.0), BBOX, {"buildings": _fc(*sheds)})
        b = m.report["layers"]["buildings"]
        assert b["merged_from"] == 2 and b["merged_into"] == 1
        mm_per_m = m.scale.mm_per_px / m.scale.m_per_px
        assert m.parts["buildings"].extents[:2] == pytest.approx([6 * mm_per_m, 3 * mm_per_m], abs=0.03)

    def test_lone_thin_building_prints_as_drawn(self):
        """User, 2026-10-02: no widening; a 3 m shed (0.19 mm) keeps its outline."""
        m = build_city_model(np.full((120, 150), 100.0), BBOX,
                             {"buildings": _fc(self._house(0, 9))})
        mm_per_m = m.scale.mm_per_px / m.scale.m_per_px
        assert m.report["layers"]["buildings"]["widened"] == 0
        assert m.parts["buildings"].extents[0] == pytest.approx(3 * mm_per_m, abs=0.03)

    def test_gaps_between_buildings_stay_as_drawn(self):
        """Two same-height rows 2 m apart (0.13 mm, under a nozzle) stay two outlines."""
        a = [self._house(i, 9) for i in range(4)]                     # 12 m row
        b = [self._house(i, 9, lon0=-3.59 + 14 / (111_320 * np.cos(np.radians(37.18)))) for i in range(4)]
        m = build_city_model(np.full((120, 150), 100.0), BBOX, {"buildings": _fc(*a, *b)})
        assert m.report["layers"]["buildings"]["merged_into"] == 2

    def test_slender_towers_are_clamped(self):
        tower = {"geometry": _square(-3.59, 37.18, d=0.00002), "properties": {"height_m": 300}}
        m = build_city_model(_hill(), BBOX, {"towers": _fc(tower)})
        assert m.report["layers"]["towers"]["clamped"] == 1
        top = m.parts["towers"].vertices[:, 2].max()
        width = m.parts["towers"].extents[:2].min()
        assert top - m.merged.vertices[:, 2].min() < 300 * m.scale.z_mm_per_m
        assert m.parts["towers"].extents[2] <= 8 * width + 1.0

    def test_pitched_roof_on_concave_footprint_is_kept(self):
        ell = {"type": "Polygon", "coordinates": [[[-3.590, 37.180], [-3.589, 37.180], [-3.589, 37.1804],
                                                   [-3.5894, 37.1804], [-3.5894, 37.181],
                                                   [-3.590, 37.181], [-3.590, 37.180]]]}
        m = build_city_model(_hill(), BBOX, {"buildings": _fc(
            {"geometry": ell, "properties": {"height_m": 15, "roof:shape": "hipped"}})})
        assert m.report["layers"]["buildings"]["rejected"] == 0
        assert "buildings" in m.parts


class _MmTerrain:
    """Stand-in terrain: features are already in model mm, so projection is the identity."""

    rect = box(-50, -50, 500, 500)

    def project(self, geoms):
        return geoms

    def m_to_mm(self, metres):
        return np.asarray(metres, dtype=float)


def _holed_feature(ext, holes):
    rings = [ext, *holes]
    return {"geometry": {"type": "Polygon", "coordinates": [[list(p) for p in r] for r in rings]},
            "properties": {}}


class TestFeaturePolygonsHoles:
    # Palacio de Carlos V (OSM relation/2640220) after projection and snapping, in mm,
    # shifted to the origin. On the SNAP_MM grid a one-step negative buffer emptied it.
    CARLOS_EXT = [[26.3, 21.8], [22.07, 0.0], [0.0, 4.3], [4.23, 26.1], [26.3, 21.8]]
    CARLOS_INT = [[10.9, 18.41], [9.45, 17.43], [8.38, 16.05], [7.83, 14.56], [7.73, 12.66],
                  [8.22, 10.98], [9.2, 9.53], [10.58, 8.46], [12.29, 7.86], [13.97, 7.81],
                  [15.64, 8.3], [17.09, 9.28], [18.16, 10.66], [18.78, 12.41], [18.8, 14.05],
                  [18.32, 15.73], [17.34, 17.18], [15.96, 18.25], [14.32, 18.84], [12.57, 18.9],
                  [10.9, 18.41]]

    def test_courtyard_building_on_snap_grid_survives(self):
        feat = _holed_feature(self.CARLOS_EXT, [self.CARLOS_INT])
        src = shapely.set_precision(Polygon(self.CARLOS_EXT, [self.CARLOS_INT]), SNAP_MM)
        polys, _, counts = feature_polygons("buildings", [feat], LayerStyle("extrude"), _MmTerrain())
        assert counts["dropped"] == 0
        assert len(polys) == 1
        assert polys[0].area == pytest.approx(src.area, rel=0.01)
        assert len(polys[0].interiors) == 1

    def test_pinched_hole_is_still_opened(self):
        ext = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
        hole = [(10, 0), (18, 0.5), (2, 0.5), (10, 0)]      # shallow wedge touching the outline at (10, 0)
        assert Polygon(ext, [hole]).is_valid
        feat = _holed_feature(ext, [hole])
        polys, _, counts = feature_polygons("buildings", [feat], LayerStyle("extrude"), _MmTerrain())
        assert counts["dropped"] == 0
        total = sum(p.area for p in polys)
        assert total == pytest.approx(400 - 4, rel=0.01)
        for p in polys:
            shell = shapely.LinearRing(p.exterior)
            assert not any(shapely.LinearRing(h).intersects(shell) for h in p.interiors)


class TestSliverLeftovers:
    """An outline trimmed by its parts keeps only leftovers wide enough to print
    (Granada: 9,200 of 10,356 leftovers were under 2 m wide, ledges at outline height)."""

    def test_thin_leftover_dropped_wide_kept(self):
        outline = box(0, 0, 20, 10)
        part = box(0, 0, 18, 10)                                   # leaves a 2 mm strip: kept
        thin = box(0, 0, 19.7, 10)                                 # leaves a 0.3 mm strip: dropped
        for p, expect in ((part, 1), (thin, 0)):
            polys, props, nparts, trimmed = assemble_parts(
                [outline, p], [{"height_m": 40}, {"height_m": 20, "building:part": "yes"}])
            assert nparts == 1 and trimmed == 1
            assert sum(1 for q in props if not q.get("building:part")) == expect
            assert len(polys) == expect + 1

    def test_threshold_is_mean_width(self):
        # 2 * area / perimeter of a 0.39 x 10 strip is just under the limit, 0.45 just over.
        assert 2 * 0.39 * 10 / (2 * 10.39) < MIN_LEFTOVER_WIDTH_MM < 2 * 0.45 * 10 / (2 * 10.45)
        outline = box(0, 0, 10.45, 10)
        _, props, _, _ = assemble_parts(
            [outline, box(0, 0, 10, 10)], [{"height_m": 40}, {"building:part": "yes"}])
        assert len(props) == 2


class TestOpenEnclosure:
    """A castle / fort outline that is mostly a plaza between a few towers prints as a
    wall ring, not a filled 3 m slab (Granada Alcazaba)."""

    CASTLE = {"building": "yes", "historic": "castle", "height_m": 3.0, "height_source": "default"}

    @staticmethod
    def _towers(n, side=2.0):
        return [box(1 + 4 * i, 1, 1 + 4 * i + side, 1 + side) for i in range(n)]

    def test_predicate(self):
        outline = box(0, 0, 40, 40)
        assert is_open_enclosure(outline, self.CASTLE, self._towers(3))
        assert not is_open_enclosure(outline, self.CASTLE, self._towers(1))     # one building
        assert not is_open_enclosure(outline, self.CASTLE, [])                  # Palacio de las Columnas
        assert not is_open_enclosure(outline, {"building": "yes"}, self._towers(3))   # not an enclosure
        halls = [box(0, 0, 40, 12), box(0, 14, 40, 26)]                         # > 50 % built over
        assert not is_open_enclosure(outline, self.CASTLE, halls)
        # the outline's own twin (same footprint) never counts as an inner building
        assert is_open_enclosure(outline, self.CASTLE, [box(0, 0, 40, 40), *self._towers(3)])

    def test_assemble_parts_leaves_the_plaza_open(self):
        outline = box(0, 0, 40, 40)
        towers = self._towers(3)
        polys, props, nparts, _ = assemble_parts(
            [outline, *towers], [dict(self.CASTLE)] + [{"building:part": "yes", "height_m": 12}] * 3)
        assert nparts == 3 and len(polys) == 3
        assert all(p.get("building:part") for p in props)
        solid, sprops, _, _ = assemble_parts(                       # same shape, plain building
            [outline, *towers], [{"building": "yes", "height_m": 10}]
            + [{"building:part": "yes", "height_m": 12}] * 3)
        assert len(solid) == 4

    def test_drop_building_copies_keeps_the_open_enclosure_copy(self):
        outline = {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [40, 0], [40, 40], [0, 40], [0, 0]]]},
                   "properties": self.CASTLE}
        towers = [{"geometry": mapping_box(t), "properties": {"building:part": "yes"}}
                  for t in self._towers(3)]
        cover = building_cover([outline, *towers])
        copy = {"geometry": outline["geometry"], "properties": dict(self.CASTLE)}
        assert drop_building_copies([copy], cover) == ([copy], 0)
        solid = {"geometry": outline["geometry"], "properties": {"building": "yes", "name": "Hall"}}
        assert drop_building_copies([solid], cover) == ([], 1)
        lone = building_cover([outline])                            # nothing inside: still dropped
        assert drop_building_copies([copy], lone) == ([], 1)


def mapping_box(b):
    from shapely.geometry import mapping
    return mapping(b)
