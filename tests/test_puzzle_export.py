"""Tests for the B-MULTI puzzle/multi-piece export feature."""

import json
import zipfile

import numpy as np
import pytest

from app.server.core.export import (
    _prepare_dem_array,
    _add_alignment_features,
    _apply_edge_tabs_v,
    generate_puzzle_3mf,
    ExportTask,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dem_data(height=50, width=60, peak=500.0):
    """Create a simple hill DEM for testing."""
    y = np.linspace(0, 1, height)
    x = np.linspace(0, 1, width)
    xx, yy = np.meshgrid(x, y)
    dem = peak * np.exp(-((xx - 0.5)**2 + (yy - 0.5)**2) / 0.1)
    return dem.ravel().tolist(), height, width


def _puzzle_request(cols=2, rows=2, height=40, width=40, **overrides):
    """Build a minimal puzzle export request dict."""
    values, h, w = _make_dem_data(height, width)
    req = {
        "dem_values": values,
        "height": h,
        "width": w,
        "model_height": 20,
        "base_height": 5,
        "exaggeration": 1.0,
        "sea_level_cap": False,
        "name": "test_terrain",
        "split_cols": cols,
        "split_rows": rows,
        "connector_size_mm": 50,
        "connectors_per_edge": 10,
        "border_height_mm": 1.0,
        "border_offset_mm": 5.0,
        "include_border": True,
    }
    req.update(overrides)
    return req


# ---------------------------------------------------------------------------
# Unit tests — alignment features
# ---------------------------------------------------------------------------

class TestAlignmentFeatures:
    def test_interior_piece_gets_tabs(self):
        """Interior edges should be modified (tabs or slots)."""
        piece = np.full((20, 20), 10.0)
        original = piece.copy()
        result = _add_alignment_features(piece, 0, 0, 2, 2, 3, 5.0, 1.0)
        # Right and bottom edges should differ from original
        assert not np.array_equal(result[:, -3:], original[:, -3:])

    def test_exterior_edges_unchanged(self):
        """Top-left corner piece: left and top exterior edges stay flat.

        Note: the corners where two interior edges meet will be modified
        by the tab/slot features on the adjacent edges, so we only check
        the exterior-only strip (excluding the corner overlap zone).
        """
        piece = np.full((20, 20), 10.0)
        original = piece.copy()
        depth = 3  # tab_depth_px passed to _add_alignment_features
        result = _add_alignment_features(piece, 0, 0, 3, 3, depth, 5.0, 1.0)
        # Left edge column (excluding bottom corner)
        np.testing.assert_array_equal(result[:-depth, 0], original[:-depth, 0])
        # Top edge row (excluding right corner)
        np.testing.assert_array_equal(result[0, :-depth], original[0, :-depth])

    def test_tabs_raise_surface(self):
        """Tab features should raise the surface above original."""
        piece = np.full((20, 20), 10.0)
        result = _add_alignment_features(piece, 0, 0, 2, 2, 3, 5.0, 0)
        # Right edge (tab for col=0, even): some values should be > 10
        assert np.any(result[:, -3:] > 10.0)

    def test_slots_lower_surface(self):
        """Slot features should lower the surface below original."""
        piece = np.full((20, 20), 10.0)
        # col=2 is even → left edge is_tab=(2%2!=0)=False → slot
        result = _add_alignment_features(piece, 0, 2, 2, 4, 3, 5.0, 0)
        # Left edge slot: some values should be < 10
        assert np.any(result[:, :3] < 10.0)

    def test_single_piece_no_modification(self):
        """1×1 grid: no edges to modify."""
        piece = np.full((20, 20), 10.0)
        original = piece.copy()
        result = _add_alignment_features(piece, 0, 0, 1, 1, 3, 5.0, 1.0)
        np.testing.assert_array_equal(result, original)


class TestEdgeTabsV:
    def test_tab_raises(self):
        arr = np.full((4, 20), 10.0)
        _apply_edge_tabs_v(arr, is_tab=True, tab_h=2.0, slot_depth=1.5)
        assert np.any(arr > 10.0)

    def test_slot_lowers(self):
        arr = np.full((4, 20), 10.0)
        _apply_edge_tabs_v(arr, is_tab=False, tab_h=2.0, slot_depth=1.5)
        assert np.any(arr < 10.0)


# ---------------------------------------------------------------------------
# Integration tests — full pipeline
# ---------------------------------------------------------------------------

class TestGeneratePuzzle3MF:
    def test_basic_2x2(self, tmp_path):
        """2×2 puzzle produces a valid 3MF zip with 4 objects."""
        task = ExportTask(task_id="test1")
        data = _puzzle_request(cols=2, rows=2, height=40, width=40)
        generate_puzzle_3mf(data, task=task)

        assert task.status == "complete"
        assert task.result_path is not None
        assert task.result_path.endswith(".3mf")

        # Verify it's a valid ZIP/3MF
        with zipfile.ZipFile(task.result_path, 'r') as zf:
            names = zf.namelist()
            assert '3D/3dmodel.model' in names

        import os
        os.unlink(task.result_path)

    def test_1x1_single_piece(self, tmp_path):
        """1×1 grid produces a single-object 3MF."""
        task = ExportTask(task_id="test2")
        data = _puzzle_request(cols=1, rows=1)
        generate_puzzle_3mf(data, task=task)

        assert task.status == "complete"
        import os
        os.unlink(task.result_path)

    def test_3x2_six_pieces(self):
        """3×2 produces 6 pieces."""
        task = ExportTask(task_id="test3")
        data = _puzzle_request(cols=3, rows=2, height=60, width=90)
        generate_puzzle_3mf(data, task=task)

        assert task.status == "complete"
        assert "6" in (task.headers.get("X-Piece-Count", ""))

        import os
        os.unlink(task.result_path)

    def test_too_many_pieces_fails(self):
        """cols*rows > 64 should fail."""
        task = ExportTask(task_id="test4")
        data = _puzzle_request(cols=9, rows=8)  # 72 > 64
        generate_puzzle_3mf(data, task=task)
        assert task.status == "error"
        assert "64" in task.message

    def test_missing_dem_fails(self):
        """Empty DEM data should fail."""
        task = ExportTask(task_id="test5")
        data = _puzzle_request()
        data["dem_values"] = []
        generate_puzzle_3mf(data, task=task)
        assert task.status == "error"

    def test_progress_updates(self):
        """Task should report progress during generation."""
        progress_log = []
        task = ExportTask(task_id="test6")
        original_update = task.update
        def _track(pct, msg):
            progress_log.append((pct, msg))
            original_update(pct, msg)
        task.update = _track

        data = _puzzle_request(cols=2, rows=2)
        generate_puzzle_3mf(data, task=task)

        assert task.status == "complete"
        assert len(progress_log) >= 3  # at least: preparing + pieces + writing
        assert progress_log[-1][0] >= 90  # final progress near done

        import os
        os.unlink(task.result_path)


# ---------------------------------------------------------------------------
# Router integration test
# ---------------------------------------------------------------------------

class TestPuzzleRouter:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.server.server import app
        return TestClient(app)

    def test_puzzle_endpoint_returns_task_id(self, client):
        """POST /api/export/puzzle should return a task_id."""
        data = _puzzle_request(cols=2, rows=2)
        resp = client.post("/api/export/puzzle", json=data)
        assert resp.status_code == 200
        body = resp.json()
        assert "task_id" in body
        assert len(body["task_id"]) == 12

    def test_puzzle_via_start_endpoint(self, client):
        """POST /api/export/start with format=puzzle should work."""
        data = _puzzle_request(cols=2, rows=2)
        data["format"] = "puzzle"
        resp = client.post("/api/export/start", json=data)
        assert resp.status_code == 200
        body = resp.json()
        assert "task_id" in body
