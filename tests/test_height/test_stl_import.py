"""Tests for city2stl.height.stl_import.

All tests use synthetic numpy-generated meshes; no external files needed.
trimesh is required.  Tests are skipped if not installed.
"""

from __future__ import annotations

import math
import struct
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest

# ── Skip whole module if trimesh is not available ────────────────────────────
trimesh = pytest.importorskip("trimesh")

from city2stl.height.stl_import import stl_to_heightmap, _UP_AXIS_ROTATIONS

# ── STL writing helpers ───────────────────────────────────────────────────────


def _write_binary_stl(path: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    """Write a minimal binary STL to *path*."""
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 80)          # header
        fh.write(struct.pack("<I", len(faces)))
        for tri in faces:
            v0, v1, v2 = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
            # compute normal
            n = np.cross(v1 - v0, v2 - v0)
            nlen = np.linalg.norm(n)
            n = n / nlen if nlen > 0 else n
            fh.write(struct.pack("<fff", *n))
            fh.write(struct.pack("<fff", *v0))
            fh.write(struct.pack("<fff", *v1))
            fh.write(struct.pack("<fff", *v2))
            fh.write(struct.pack("<H", 0))  # attribute byte count


def _make_box_stl(
    tmp_path: Path,
    x0: float = 0.0, y0: float = 0.0, z0: float = 0.0,
    x1: float = 10.0, y1: float = 10.0, z1: float = 5.0,
    filename: str = "box.stl",
) -> Path:
    """Write a closed box (12 triangles) in the given bounding-box range."""
    verts = np.array([
        [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],  # bottom
        [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],  # top
    ], dtype=np.float32)
    faces = np.array([
        [0, 1, 2], [0, 2, 3],  # bottom
        [4, 6, 5], [4, 7, 6],  # top
        [0, 4, 5], [0, 5, 1],  # front
        [1, 5, 6], [1, 6, 2],  # right
        [2, 6, 7], [2, 7, 3],  # back
        [3, 7, 4], [3, 4, 0],  # left
    ])
    out = tmp_path / filename
    _write_binary_stl(out, verts, faces)
    return out


# A simple square bbox for tests
_BBOX = {"north": 1.0, "south": 0.0, "east": 1.0, "west": 0.0}


# ── Basic functionality ───────────────────────────────────────────────────────


class TestStlToHeightmap:

    def test_returns_correct_shape(self, tmp_path):
        stl = _make_box_stl(tmp_path)
        hm, mask = stl_to_heightmap(stl, _BBOX, resolution_m=1000.0)
        assert hm.ndim == 2
        assert mask.ndim == 2
        assert hm.shape == mask.shape

    def test_returns_float32(self, tmp_path):
        stl = _make_box_stl(tmp_path)
        hm, _ = stl_to_heightmap(stl, _BBOX, resolution_m=1000.0)
        assert hm.dtype == np.float32

    def test_valid_pixels_have_nonnan_heights(self, tmp_path):
        stl = _make_box_stl(tmp_path)
        hm, mask = stl_to_heightmap(stl, _BBOX, resolution_m=1000.0)
        assert not np.isnan(hm[mask]).any(), "Masked pixels must not be NaN"

    def test_masked_pixels_are_nan(self, tmp_path):
        stl = _make_box_stl(tmp_path)
        hm, mask = stl_to_heightmap(stl, _BBOX, resolution_m=1000.0)
        assert np.isnan(hm[~mask]).all(), "Unmasked pixels must be NaN"

    def test_max_height_matches_box_top(self, tmp_path):
        """The ray-cast should return z1 = 5.0 as the max height."""
        stl = _make_box_stl(tmp_path, z1=5.0)
        hm, mask = stl_to_heightmap(stl, _BBOX, resolution_m=2000.0)
        if mask.any():
            assert np.nanmax(hm) == pytest.approx(5.0, abs=0.1)

    def test_resolution_affects_grid_size(self, tmp_path):
        """Finer resolution should yield a larger grid (more pixels)."""
        stl = _make_box_stl(tmp_path)
        hm_coarse, _ = stl_to_heightmap(stl, _BBOX, resolution_m=50_000.0)
        hm_fine, _ = stl_to_heightmap(stl, _BBOX, resolution_m=10_000.0)
        coarse_cells = hm_coarse.shape[0] * hm_coarse.shape[1]
        fine_cells = hm_fine.shape[0] * hm_fine.shape[1]
        assert fine_cells >= coarse_cells, (
            f"Fine grid ({fine_cells}) should be >= coarse ({coarse_cells})"
        )

    def test_z_axis_is_default_and_works(self, tmp_path):
        stl = _make_box_stl(tmp_path, z1=7.0)
        hm, mask = stl_to_heightmap(stl, _BBOX, resolution_m=2000.0, up_axis="z")
        if mask.any():
            assert np.nanmax(hm) == pytest.approx(7.0, abs=0.2)

    def test_string_path_is_accepted(self, tmp_path):
        stl = _make_box_stl(tmp_path)
        hm, mask = stl_to_heightmap(str(stl), _BBOX, resolution_m=2000.0)
        assert hm.shape[0] >= 1


# ── Up-axis rotation ──────────────────────────────────────────────────────────


class TestUpAxisRotation:

    def test_all_valid_axes_are_registered(self):
        expected = {"x", "y", "z", "-x", "-y", "-z"}
        assert set(_UP_AXIS_ROTATIONS.keys()) == expected

    def test_z_rotation_is_identity(self):
        R = _UP_AXIS_ROTATIONS["z"]
        assert np.allclose(R, np.eye(3))

    def test_y_axis_raises_x_after_rotation(self):
        """Y-up: a point at Y=10 should end up at Z=10 after rotation."""
        R = _UP_AXIS_ROTATIONS["y"]
        v = np.array([0.0, 10.0, 0.0])
        rotated = R @ v
        assert abs(rotated[2] - 10.0) < 0.01, f"Expected Z=10, got {rotated}"

    def test_invalid_up_axis_raises(self, tmp_path):
        stl = _make_box_stl(tmp_path)
        with pytest.raises(ValueError, match="up_axis"):
            stl_to_heightmap(stl, _BBOX, up_axis="w")


# ── Error handling ────────────────────────────────────────────────────────────


class TestErrorHandling:

    def test_empty_stl_raises_value_error(self, tmp_path):
        empty = tmp_path / "empty.stl"
        # Write STL with zero triangles
        with open(empty, "wb") as fh:
            fh.write(b"\x00" * 80)
            fh.write(struct.pack("<I", 0))
        with pytest.raises(ValueError, match="valid mesh"):
            stl_to_heightmap(empty, _BBOX, resolution_m=1000.0)

    def test_missing_trimesh_raises_import_error(self, tmp_path, monkeypatch):
        """When trimesh is not importable, a clear ImportError is raised."""
        import builtins
        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "trimesh":
                raise ImportError("Blocked for test")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked_import)
        # Must re-import the module so the patched import path is taken
        import importlib
        import city2stl.height.stl_import as mod
        importlib.reload(mod)
        try:
            with pytest.raises(ImportError, match="trimesh"):
                mod.stl_to_heightmap(
                    tmp_path / "x.stl", _BBOX, resolution_m=1000.0
                )
        finally:
            importlib.reload(mod)  # restore
