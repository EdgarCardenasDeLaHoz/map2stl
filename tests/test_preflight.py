"""Pre-flight report (app/server/core/preflight.py, POST /api/export/preflight) and the
puzzle upgrades: explicit edges, mask vs boolean cutting, engraved ids, plate layout."""

import io
import os
import re
import zipfile

import numpy as np
import pytest
import shapely
import trimesh

from city2stl.city_model import build_city_model, resolve_layers

BBOX = dict(north=37.19, south=37.172, east=-3.578, west=-3.605)   # ~3 km diagonal


def _hill(h=120, w=150):
    y, x = np.mgrid[0:h, 0:w]
    return 100 + 80 * np.exp(-(((x - w / 2) / 30) ** 2 + ((y - h / 2) / 25) ** 2))


def _rect(lon, lat, dx, dy):
    return {"type": "Polygon", "coordinates": [[[lon - dx, lat - dy], [lon + dx, lat - dy],
                                                 [lon + dx, lat + dy], [lon - dx, lat + dy],
                                                 [lon - dx, lat - dy]]]}


BUILDINGS = {"type": "FeatureCollection", "features": [
    {"geometry": _rect(-3.588, 37.183, 0.0004, 0.0004), "properties": {"height_m": 20}},
    # ~2 m wide at ~1:20,000: below 0.8 mm, widened
    {"geometry": _rect(-3.595, 37.178, 0.00001, 0.0003), "properties": {"height_m": 8}},
    # a 150 m tower on a ~9 m footprint: taller than 8x its width, clamped
    {"geometry": _rect(-3.585, 37.176, 0.00005, 0.00004), "properties": {"height_m": 150}},
]}
OFF = {n: {"enabled": False} for n in resolve_layers(None) if n != "buildings"}


def _body(**kw):
    dem = _hill()
    body = {"bbox": BBOX, "dem_values": dem.ravel().tolist(), "height": 120, "width": 150,
            "z_mode": "true", "base_height": 5, "bed_mm": [100, 100]}
    body.update(kw)
    return body


class TestPreflight:
    def test_city_counts_match_the_build(self, client):
        body = _body(format="city", layers=OFF, layer_data={"buildings": BUILDINGS})
        r = client.post("/api/export/preflight", json=body)
        assert r.status_code == 200, r.text
        pf = r.json()
        model = build_city_model(_hill(), BBOX, {"buildings": BUILDINGS}, base_mm=5,
                                 z_mode="true", layer_overrides=OFF)
        built = model.report["layers"]["buildings"]
        got = pf["layers"]["buildings"]
        for k in ("polygons", "widened", "clamped", "dropped"):
            assert got[k] == built[k], k
        assert got["widened"] >= 1 and got["clamped"] >= 1
        assert pf["size_mm"][:2] == [149.0, 119.0]
        assert pf["thinnest_feature_mm"] >= 0.8 - 1e-6
        assert pf["fits_bed"] is False
        assert any("does not fit" in w for w in pf["warnings"])
        assert any("widened" in w for w in pf["warnings"])
        assert any("capped" in w for w in pf["warnings"])
        est = pf["estimate"]
        assert est["filament_g"] > 0 and est["print_hours"] > 0 and "1.24" in est["formula"]
        # The estimate from the heightfield and outlines is close to the real mesh's.
        from app.server.core.preflight import print_estimate
        real = print_estimate(model.merged.volume, model.merged.area)
        assert est["filament_g"] == pytest.approx(real["filament_g"], rel=0.15)
        assert 0.3 * len(model.merged.faces) < pf["faces_est"] < 3 * len(model.merged.faces)

    def test_puzzle_grid_and_edges(self, client):
        body = _body(format="puzzle", split_cols=2, split_rows=2,
                     col_edges_mm=[0, 50, 149], row_edges_mm=[0, 70, 119])
        pf = client.post("/api/export/preflight", json=body).json()
        pz = pf["puzzle"]
        assert (pz["cols"], pz["rows"], pz["method"]) == (2, 2, "mask")
        assert pz["col_edges_mm"] == [0, 50, 149] and pz["largest_piece_mm"] == [99, 70]
        assert any("larger than the bed" in w for w in pf["warnings"])

    def test_bad_edges_are_a_warning(self, client):
        body = _body(format="puzzle", col_edges_mm=[0, 80, 60, 149])
        pf = client.post("/api/export/preflight", json=body).json()
        assert any("increasing" in w for w in pf["warnings"])

    def test_missing_dem_is_400(self, client):
        r = client.post("/api/export/preflight", json={"format": "puzzle", "dem_values": []})
        assert r.status_code == 400


def _field():
    from app.server.core.export import terrain_stage
    from app.server.core.export_params import ExportContext
    from app.server.core.puzzle import Heightfield
    from city2stl.city_model import build_on_terrain, terrain_tolerance

    data = _body()
    f = terrain_stage(ExportContext.from_request(data), data)
    hf = Heightfield(f.z_mm, 1.0, terrain_tolerance(f.scale))
    return hf, build_on_terrain(f.z_mm, BBOX, f.scale, {}).merged


class TestPuzzleUpgrades:
    def test_mask_and_boolean_paths_agree(self):
        from app.server.core.puzzle import cut_pieces

        hf, mesh = _field()
        spec = {"cols": 3, "rows": 2, "engrave": False, "knob_shape": "dovetail"}
        mask, im = cut_pieces(spec, mesh=mesh, heightfield=hf)
        boolean, ib = cut_pieces({**spec, "method": "boolean"}, mesh=mesh, heightfield=hf)
        assert (im["method"], ib["method"]) == ("mask", "boolean")
        vm = sum(trimesh.Trimesh(*vf).volume for vf in mask.values())
        vb = sum(trimesh.Trimesh(*vf).volume for vf in boolean.values())
        assert vm == pytest.approx(vb, rel=2e-3)
        assert all(trimesh.Trimesh(*vf).is_watertight for vf in mask.values())

    def test_mask_needs_a_heightfield(self):
        from app.server.core.puzzle import cut_pieces

        _, mesh = _field()
        with pytest.raises(ValueError, match="terrain-only"):
            cut_pieces({"cols": 2, "rows": 1, "method": "mask"}, mesh=mesh)

    def test_explicit_edges_place_the_cuts(self):
        from app.server.core.puzzle import cut_pieces

        hf, _ = _field()
        spec = {"col_edges_mm": [0, 40, 149], "row_edges_mm": [0, 119], "engrave": False,
                "clearance_mm": 0.0, "knob_width_mm": 10, "knob_depth_mm": 5}
        pieces, info = cut_pieces(spec, heightfield=hf)
        assert (info["cols"], info["rows"]) == (2, 1)
        assert info["col_edges_mm"] == [0, 40, 149]
        left = trimesh.Trimesh(*pieces["r0c0"]).bounds
        assert abs(left[1, 0] - 40) <= 5 + 1e-6   # the cut, give or take the knob
        assert trimesh.Trimesh(*pieces["r0c1"]).bounds[1, 0] == pytest.approx(149)

    def test_engraving_removes_about_the_text_volume(self):
        from numpy2stl.applications.puzzle import jigsaw_outlines, underside_marks

        from app.server.core.puzzle import cut_pieces

        hf, _ = _field()
        spec = {"cols": 2, "rows": 2}
        plain, _ = cut_pieces({**spec, "engrave": False}, heightfield=hf)
        marked, info = cut_pieces(spec, heightfield=hf)
        assert info["engraved"]["pieces"] == 4
        w, d, _ = hf.extents
        outlines = jigsaw_outlines(w, d, 2, 2, info["knob_width_mm"], info["knob_depth_mm"],
                                   0.3, knob_shape="classic")
        for key in plain:
            removed = trimesh.Trimesh(*plain[key]).volume - trimesh.Trimesh(*marked[key]).volume
            text = underside_marks(key, outlines[key].intersection(shapely.box(0, 0, w, d)))
            assert removed == pytest.approx(text.area * 0.6, rel=0.01)
            assert trimesh.Trimesh(*marked[key]).is_watertight

    def test_zip_has_plates_when_laid_out(self):
        from app.server.core.export import generate_puzzle
        from app.server.core.export_tasks import ExportTask

        task = ExportTask(task_id="t")
        generate_puzzle({**_body(), "name": "t", "split_cols": 2, "split_rows": 2,
                         "layout": True, "bed_mm": [100, 100]}, task)
        assert task.status == "complete", task.message
        with zipfile.ZipFile(task.result_path) as zf:
            names = zf.namelist()
            plates = [n for n in names if "_plate" in n]
            assert "t_puzzle.3mf" in names and len(plates) >= 2
            for n in plates:   # pieces on a plate stay inside the bed
                with zipfile.ZipFile(io.BytesIO(zf.read(n))) as m:
                    xml = m.read("3D/3dmodel.model").decode()
                xy = np.array([[float(a), float(b)] for a, b in
                               re.findall(r'<vertex x="([^"]+)" y="([^"]+)"', xml)])
                assert len(xy) and xy.min() >= 0 and xy.max() <= 100
        os.unlink(task.result_path)


class TestDecimatedPreview:
    def test_preview_is_adaptive_closed_and_capped(self, client, monkeypatch):
        import app.server.core.export as export

        monkeypatch.setattr(export, "PREVIEW_MAX_FACES", 4000)
        r = client.post("/api/export/preview", json=_body())
        assert r.status_code == 200, r.text
        d = r.json()
        v, f = np.array(d["vertices"]), np.array(d["faces"])
        assert d["face_count"] == len(f) <= 4000
        assert d["preview"]["adaptive"] and d["preview"]["max_error_mm"] > 0
        # Pixel units, row 0 north: x in [0, cols-1], y in [0, rows-1].
        assert v[:, 0].min() == 0 and v[:, 0].max() == 149 and v[:, 1].max() == 119
        m = trimesh.Trimesh(v, f, process=True)
        assert m.is_watertight
        # The unconstrained preview stays within the export tolerance.
        monkeypatch.setattr(export, "PREVIEW_MAX_FACES", 150_000)
        d = client.post("/api/export/preview", json=_body()).json()
        assert d["preview"]["max_error_mm"] <= d["preview"]["tolerance_mm"] + 1e-9
        assert d["face_count"] < 4 * 119 * 149
