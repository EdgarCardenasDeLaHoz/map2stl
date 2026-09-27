"""Terrain jigsaw puzzle export (app/server/core/export.generate_puzzle + core/puzzle.py)."""

import io
import os
import zipfile

import numpy as np
import pytest
import trimesh

from app.server.core.export import generate_puzzle
from app.server.core.export_tasks import ExportTask
from app.server.core.puzzle import plan_grid


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
                               engrave_ids=False)
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
