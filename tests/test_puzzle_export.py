"""Terrain jigsaw puzzle export (app/server/core/export.generate_puzzle + core/puzzle.py)."""

import io
import os
import zipfile

import numpy as np
import pytest
import trimesh

from app.server.core.export import generate_puzzle
from app.server.core.export_tasks import ExportTask
from app.server.core.puzzle import (
    CHAMFER_MM,
    CORNER_RADIUS_MM,
    DEFAULT_CLEARANCE_MM,
    FIT_TEST_CLEARANCES_MM,
    cut_pieces,
    fit_test_mesh,
    knob_size,
    plan_grid,
)


def _make_dem_data(height=50, width=60, peak=500.0):
    """A simple hill DEM."""
    y = np.linspace(0, 1, height)
    x = np.linspace(0, 1, width)
    xx, yy = np.meshgrid(x, y)
    dem = peak * np.exp(-((xx - 0.5) ** 2 + (yy - 0.5) ** 2) / 0.1)
    return dem.ravel().tolist(), height, width


def _puzzle_request(cols=2, rows=2, height=60, width=60, **overrides):
    values, h, w = _make_dem_data(height, width)
    req = {
        "dem_values": values, "height": h, "width": w,
        "model_height": 20, "base_height": 5, "exaggeration": 1.0,
        "sea_level_cap": False, "name": "test_terrain",
        "split_cols": cols, "split_rows": rows,
    }
    req.update(overrides)
    return req


def _run(data):
    task = ExportTask(task_id="t")
    generate_puzzle(data, task)
    return task


class TestPlanGrid:
    def test_explicit_grid(self):
        assert plan_grid(400, 300, {"cols": 3, "rows": 2}) == (3, 2)

    def test_pieces_fit_the_bed(self):
        assert plan_grid(796, 574, {"piece_mm": 200}) == (4, 3)


class TestGeneratePuzzle:
    def test_2x2_zip_has_watertight_pieces_and_3mf(self):
        task = _run(_puzzle_request(cols=2, rows=2))
        assert task.status == "complete", task.message
        assert task.headers["X-Piece-Count"] == "4"
        with zipfile.ZipFile(task.result_path) as zf:
            objs = [n for n in zf.namelist() if n.endswith(".obj")]
            assert len(objs) == 4
            assert "test_terrain_puzzle.3mf" in zf.namelist()
            pieces = [trimesh.load(io.BytesIO(zf.read(n)), file_type="obj", force="mesh")
                      for n in objs]
        assert all(p.is_watertight for p in pieces)
        os.unlink(task.result_path)

    def test_pieces_conserve_the_model(self):
        """Pieces add up to the whole terrain apart from the clearance gaps."""
        from app.server.core.export import _prepare_export_mesh
        from app.server.core.export_params import _parse_export_params

        data = _puzzle_request(cols=3, rows=2, height=60, width=90, clearance_mm=0.0,
                               corner_radius_mm=0.0, chamfer_mm=0.0, engrave_ids=False)
        whole = _prepare_export_mesh(_parse_export_params(data), data)
        task = _run(data)
        assert task.status == "complete", task.message
        with zipfile.ZipFile(task.result_path) as zf:
            vol = sum(trimesh.load(io.BytesIO(zf.read(n)), file_type="obj", force="mesh").volume
                      for n in zf.namelist() if n.endswith(".obj"))
        assert vol == pytest.approx(whole.volume, rel=1e-3)
        os.unlink(task.result_path)

    def test_too_many_pieces_fails(self):
        task = _run(_puzzle_request(cols=9, rows=8))
        assert task.status == "error"
        assert "64" in task.message

    def test_missing_dem_fails(self):
        data = _puzzle_request()
        data["dem_values"] = []
        assert _run(data).status == "error"

    def test_progress_updates(self):
        log = []
        task = ExportTask(task_id="t")
        original = task.update
        task.update = lambda pct, msg: (log.append(pct), original(pct, msg))
        generate_puzzle(_puzzle_request(), task)
        assert task.status == "complete"
        assert len(log) >= 3 and max(log) >= 80
        os.unlink(task.result_path)


class TestPuzzleRouter:
    # Export threads started here are drained by the autouse fixture in conftest.

    def test_puzzle_endpoint_returns_task_id(self, client):
        resp = client.post("/api/export/puzzle", json=_puzzle_request())
        assert resp.status_code == 200
        assert len(resp.json()["task_id"]) == 12

    def test_puzzle_via_start_endpoint(self, client):
        data = {**_puzzle_request(), "format": "puzzle"}
        resp = client.post("/api/export/start", json=data)
        assert resp.status_code == 200
        assert "task_id" in resp.json()


class TestPrintability:
    """FDM fit defaults: 0.45 mm clearance, 0.5 mm corner radius, 0.4 mm bottom chamfer."""

    def test_defaults_reach_the_cut(self):
        from app.server.core.export import puzzle_spec
        from app.server.core.puzzle import Heightfield

        z = np.full((61, 91), 6.0)
        hf = Heightfield(z_mm=z, mm_per_px=1.0, max_error=0.05)
        pieces, info = cut_pieces(puzzle_spec({"split_cols": 3, "split_rows": 2,
                                               "engrave_ids": False}), heightfield=hf)
        assert (info["clearance_mm"], info["corner_radius_mm"], info["chamfer_mm"]) == (
            DEFAULT_CLEARANCE_MM, CORNER_RADIUS_MM, CHAMFER_MM)
        assert info["volume"]["open_pieces"] == []
        for v, _ in pieces.values():   # the bevel pulls the bottom ring in by the chamfer
            v = np.asarray(v)
            bottom, mid = v[v[:, 2] < 1e-6], v[np.abs(v[:, 2] - CHAMFER_MM) < 1e-6]
            assert len(mid) and bottom[:, 0].min() > mid[:, 0].min() + CHAMFER_MM / 2

    def test_zero_turns_them_off(self):
        from app.server.core.puzzle import Heightfield

        hf = Heightfield(z_mm=np.full((61, 91), 6.0), mm_per_px=1.0, max_error=0.05)
        pieces, _ = cut_pieces({"cols": 3, "rows": 2, "engrave": False, "clearance_mm": 0,
                                "corner_radius_mm": 0, "chamfer_mm": 0}, heightfield=hf)
        total = sum(trimesh.Trimesh(v, f).volume for v, f in pieces.values())
        assert total == pytest.approx(90 * 60 * 6.0, rel=1e-6)

    def test_notched_tab_spans_most_of_the_edge(self):
        edges = np.array([0.0, 100.0, 200.0])
        w, d = knob_size(edges, edges, {"knob_shape": "notched", "clearance_mm": 0.3})
        assert (round(w, 6), d) == (58.8, 15.0)
        w, d = knob_size(edges, edges, {"knob_shape": "dovetail"})
        assert (w, d) == (30.0, 15.0)
        # notched is the default
        assert knob_size(edges, edges, {}) == knob_size(edges, edges, {"knob_shape": "notched"})

    @pytest.mark.parametrize("shape", ["dovetail", "classic", "rectangular", "notched"])
    def test_fit_test_coupon_is_one_closed_joint_per_clearance(self, shape):
        mesh = fit_test_mesh({"knob_shape": shape})
        bodies = mesh.split(only_watertight=True)
        assert len(bodies) == 2 * len(FIT_TEST_CLEARANCES_MM)
        assert mesh.extents[0] < 220 and mesh.extents[1] < 220     # one Ender bed

    def test_fit_test_endpoint_returns_an_stl(self, client):
        r = client.post("/api/export/fit-test", json={"knob_shape": "notched"})
        assert r.status_code == 200
        mesh = trimesh.load(io.BytesIO(r.content), file_type="stl")
        assert len(mesh.split(only_watertight=True)) == 2 * len(FIT_TEST_CLEARANCES_MM)


def test_volume_check_reports_kept_fraction_and_open_pieces():
    from app.server.core.puzzle import Heightfield, volume_check

    box = trimesh.creation.box(extents=(10.0, 10.0, 2.0))
    half = trimesh.creation.box(extents=(5.0, 10.0, 2.0))
    ok = volume_check({"a": (half.vertices, half.faces), "b": (half.vertices, half.faces)},
                      mesh=box)
    assert ok["kept"] == pytest.approx(1.0) and ok["open_pieces"] == []
    holed = volume_check({"a": (half.vertices, half.faces[:-1])}, mesh=box)
    assert holed["open_pieces"] == ["a"] and holed["kept"] < 0.99
    hf = Heightfield(np.full((11, 11), 2.0), 1.0, 0.01)   # 10 x 10 mm, 2 mm tall
    assert volume_check({"a": (box.vertices, box.faces)}, heightfield=hf)["kept"] == pytest.approx(1.0)
