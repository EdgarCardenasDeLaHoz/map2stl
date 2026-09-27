"""Vector city model (city2stl/city_model.py) and the ``city`` export task."""

import io
import json
import time
import zipfile

import numpy as np
import pytest
import trimesh

from city2stl.city_model import (
    DEFAULT_LAYERS,
    build_city_model,
    choose_scale,
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
        m = build_city_model(_hill(), BBOX, {"trails": _fc(trail)})
        assert m.report["layers"]["trails"]["widened"] == 1
        ys = m.parts["trails"].vertices[:, 1]
        assert ys.max() - ys.min() >= 0.8 - 1e-6

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
