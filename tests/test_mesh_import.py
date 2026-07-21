"""
Tests for STL/OBJ mesh import: core.mesh_import + routers/layers.py.

Covers upload validation, heightmap computation, manual point-pair
registration (affine fit + warp), and the mesh library (browse + per-city
location sidecars). Uses a small synthetic box STL — no external files.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

import app.server.core.mesh_import as mesh_import


# ── STL fixture helper (mirrors tests/test_height/test_stl_import.py) ───────

def _write_binary_stl(path: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 80)
        fh.write(struct.pack("<I", len(faces)))
        for tri in faces:
            v0, v1, v2 = vertices[tri[0]], vertices[tri[1]], vertices[tri[2]]
            n = np.cross(v1 - v0, v2 - v0)
            nlen = np.linalg.norm(n)
            n = n / nlen if nlen > 0 else n
            fh.write(struct.pack("<fff", *n))
            fh.write(struct.pack("<fff", *v0))
            fh.write(struct.pack("<fff", *v1))
            fh.write(struct.pack("<fff", *v2))
            fh.write(struct.pack("<H", 0))


def _make_box_stl(path: Path, x1=10.0, y1=10.0, z1=5.0) -> bytes:
    verts = np.array([
        [0, 0, 0], [x1, 0, 0], [x1, y1, 0], [0, y1, 0],
        [0, 0, z1], [x1, 0, z1], [x1, y1, z1], [0, y1, z1],
    ], dtype=np.float32)
    faces = np.array([
        [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
        [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2],
        [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0],
    ])
    _write_binary_stl(path, verts, faces)
    return path.read_bytes()


# Small enough that resolution_m=100 (the schema's max) still yields a tiny
# grid (~600m / 100m/px = a handful of pixels per side) — keeps HTTP-route
# tests fast (the schema caps resolution_m <= 100).
_BBOX = {"north": 0.005, "south": 0.0, "east": 0.005, "west": 0.0}
_HTTP_RES_M = 100.0

# Core-level unit tests call mesh_import functions directly (no Pydantic
# schema in the way), so they can use a coarser resolution_m with a larger
# bbox to keep the ray-cast grid tiny without hitting the HTTP cap.
_CORE_BBOX = {"north": 1.0, "south": 0.0, "east": 1.0, "west": 0.0}
_CORE_RES_M = 20_000.0


@pytest.fixture()
def _redirect_cache(tmp_data_dir, monkeypatch):
    """Point mesh_import's own CACHE_ROOT name at the tmp cache dir.

    Mirrors the pattern conftest.py already uses for the cities router:
    `from ... import CACHE_ROOT` binds a module-local name, so the shared
    cache_module.CACHE_ROOT patch alone does not affect this module.
    """
    monkeypatch.setattr(mesh_import, "CACHE_ROOT", tmp_data_dir["cache_root"])
    return tmp_data_dir


# ── core.mesh_import unit tests ──────────────────────────────────────────────

class TestSaveUpload:
    def test_rejects_bad_extension(self, _redirect_cache):
        with pytest.raises(mesh_import.MeshImportError, match="Unsupported file type"):
            mesh_import.save_upload("model.gltf", b"data")

    def test_rejects_empty_file(self, _redirect_cache):
        with pytest.raises(mesh_import.MeshImportError, match="empty"):
            mesh_import.save_upload("model.stl", b"")

    def test_rejects_oversized_file(self, _redirect_cache, monkeypatch):
        monkeypatch.setattr(mesh_import, "MAX_UPLOAD_BYTES", 10)
        with pytest.raises(mesh_import.MeshImportError, match="too large"):
            mesh_import.save_upload("model.stl", b"x" * 11)

    def test_accepts_valid_stl(self, _redirect_cache, tmp_path):
        data = _make_box_stl(tmp_path / "box.stl")
        upload_id, fmt, size = mesh_import.save_upload("box.stl", data)
        assert fmt == "stl"
        assert size == len(data)
        assert len(upload_id) == 32  # uuid4().hex

    def test_accepts_valid_obj_extension(self, _redirect_cache):
        upload_id, fmt, size = mesh_import.save_upload("box.OBJ", b"v 0 0 0\n")
        assert fmt == "obj"


class TestComputeHeightmap:
    def test_produces_valid_heightmap(self, _redirect_cache, tmp_path):
        data = _make_box_stl(tmp_path / "box.stl")
        upload_id, _, _ = mesh_import.save_upload("box.stl", data)
        heightmap, mask = mesh_import.compute_heightmap(
            upload_id, _CORE_BBOX, resolution_m=_CORE_RES_M, up_axis="z")
        assert mask.any()
        assert np.nanmax(heightmap) == pytest.approx(5.0, abs=0.5)

    def test_unknown_upload_id_raises(self, _redirect_cache):
        with pytest.raises(mesh_import.MeshImportError, match="Unknown upload_id"):
            mesh_import.compute_heightmap("does-not-exist", _CORE_BBOX, resolution_m=_CORE_RES_M)

    def test_caches_last_heightmap_for_register(self, _redirect_cache, tmp_path):
        data = _make_box_stl(tmp_path / "box.stl")
        upload_id, _, _ = mesh_import.save_upload("box.stl", data)
        assert mesh_import.get_last_heightmap(upload_id) is None
        mesh_import.compute_heightmap(upload_id, _CORE_BBOX, resolution_m=_CORE_RES_M)
        cached = mesh_import.get_last_heightmap(upload_id)
        assert cached is not None
        hm, mask = cached
        assert hm.shape == mask.shape

    def test_oversized_grid_rejected_before_raycast(self, _redirect_cache, tmp_path):
        """A region-sized bbox at the default 5m resolution would ray-cast a
        100M+ pixel grid — this must be rejected fast, not hang the request."""
        data = _make_box_stl(tmp_path / "box.stl")
        upload_id, _, _ = mesh_import.save_upload("box.stl", data)
        with pytest.raises(mesh_import.MeshImportError, match="too large"):
            mesh_import.compute_heightmap(upload_id, _CORE_BBOX, resolution_m=5.0)


class TestAffineFit:
    def test_identity_pairs_give_identity_transform(self):
        pts = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]])
        M = mesh_import.fit_affine_from_pairs(pts, pts)
        np.testing.assert_allclose(M, [[1, 0, 0], [0, 1, 0]], atol=1e-8)

    def test_translation_recovered(self):
        mesh_pts = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]])
        ref_pts = mesh_pts + np.array([5.0, -3.0])
        M = mesh_import.fit_affine_from_pairs(ref_pts, mesh_pts)
        resid = mesh_import.residuals_px(ref_pts, mesh_pts, M)
        assert np.allclose(resid, 0, atol=1e-6)

    def test_fewer_than_3_pairs_raises(self):
        pts = np.array([[0.0, 0.0], [10.0, 0.0]])
        with pytest.raises(mesh_import.MeshImportError, match="At least 3"):
            mesh_import.fit_affine_from_pairs(pts, pts)


# ── router tests ──────────────────────────────────────────────────────────

@pytest.fixture()
def _box_stl_bytes(tmp_path):
    return _make_box_stl(tmp_path / "box.stl")


class TestUploadRoute:
    def test_upload_then_heightmap_then_register(self, client, tmp_data_dir, monkeypatch, _box_stl_bytes):
        monkeypatch.setattr(mesh_import, "CACHE_ROOT", tmp_data_dir["cache_root"])

        r = client.post("/api/layers/mesh/upload",
                        files={"file": ("box.stl", _box_stl_bytes, "application/octet-stream")})
        assert r.status_code == 200
        upload_id = r.json()["upload_id"]
        assert r.json()["format"] == "stl"

        r = client.post(f"/api/layers/mesh/{upload_id}/heightmap",
                        json={**_BBOX, "resolution_m": _HTTP_RES_M, "up_axis": "z", "infill": "none"})
        assert r.status_code == 200
        body = r.json()
        h, w = body["dimensions"]
        assert body["valid_pct"] > 0

        pairs = {
            "point_pairs": [
                {"ref_x": 0, "ref_y": 0, "mesh_x": 0, "mesh_y": 0},
                {"ref_x": 100, "ref_y": 0, "mesh_x": w, "mesh_y": 0},
                {"ref_x": 0, "ref_y": 100, "mesh_x": 0, "mesh_y": h},
            ],
            "ref_width": 100, "ref_height": 100,
            "mesh_width": w, "mesh_height": h,
        }
        r = client.post(f"/api/layers/mesh/{upload_id}/register", json=pairs)
        assert r.status_code == 200
        rbody = r.json()
        assert rbody["dimensions"] == [100, 100]
        assert rbody["rms_residual_px"] < 1.0

        r = client.delete(f"/api/layers/mesh/{upload_id}")
        assert r.status_code == 200

    def test_upload_rejects_bad_extension(self, client, tmp_data_dir, monkeypatch):
        monkeypatch.setattr(mesh_import, "CACHE_ROOT", tmp_data_dir["cache_root"])
        r = client.post("/api/layers/mesh/upload",
                        files={"file": ("model.gltf", b"data", "application/octet-stream")})
        assert r.status_code == 400

    def test_register_without_heightmap_returns_400(self, client, tmp_data_dir, monkeypatch, _box_stl_bytes):
        monkeypatch.setattr(mesh_import, "CACHE_ROOT", tmp_data_dir["cache_root"])
        r = client.post("/api/layers/mesh/upload",
                        files={"file": ("box.stl", _box_stl_bytes, "application/octet-stream")})
        upload_id = r.json()["upload_id"]
        pairs = {
            "point_pairs": [
                {"ref_x": 0, "ref_y": 0, "mesh_x": 0, "mesh_y": 0},
                {"ref_x": 1, "ref_y": 0, "mesh_x": 1, "mesh_y": 0},
                {"ref_x": 0, "ref_y": 1, "mesh_x": 0, "mesh_y": 1},
            ],
            "ref_width": 10, "ref_height": 10, "mesh_width": 10, "mesh_height": 10,
        }
        r = client.post(f"/api/layers/mesh/{upload_id}/register", json=pairs)
        assert r.status_code == 400

    def test_register_fewer_than_3_pairs_rejected_by_schema(self, client, tmp_data_dir, monkeypatch, _box_stl_bytes):
        """MeshRegisterRequest.point_pairs has min_length=3 — Pydantic 422s
        before the handler runs, so fewer than 3 pairs never reaches core logic."""
        monkeypatch.setattr(mesh_import, "CACHE_ROOT", tmp_data_dir["cache_root"])
        r = client.post("/api/layers/mesh/upload",
                        files={"file": ("box.stl", _box_stl_bytes, "application/octet-stream")})
        upload_id = r.json()["upload_id"]
        r = client.post(f"/api/layers/mesh/{upload_id}/register", json={
            "point_pairs": [{"ref_x": 0, "ref_y": 0, "mesh_x": 0, "mesh_y": 0}],
            "ref_width": 10, "ref_height": 10, "mesh_width": 10, "mesh_height": 10,
        })
        assert r.status_code == 422


# ── mesh library tests ───────────────────────────────────────────────────

@pytest.fixture()
def _library_dir(tmp_path, monkeypatch):
    """A tiny fake mesh library with one city folder holding 2 sibling files."""
    lib = tmp_path / "library"
    city = lib / "TestCity_Pack"
    city.mkdir(parents=True)
    _make_box_stl(city / "TestCity_Solid.stl")
    _make_box_stl(city / "TestCity_Water.stl")
    monkeypatch.setattr(mesh_import, "MICROPOLITAN_STL_DIR", lib)
    return lib


class TestMeshLibrary:
    def test_list_library_groups_by_city(self, _library_dir):
        cities = mesh_import.list_library()
        assert len(cities) == 1
        assert cities[0]["city"] == "TestCity_Pack"
        assert len(cities[0]["files"]) == 2
        assert all(f["location"] is None for f in cities[0]["files"])

    def test_set_location_applies_to_city_siblings(self, _library_dir):
        rel = "TestCity_Pack/TestCity_Solid.stl"
        updated = mesh_import.set_library_location(rel, _BBOX, up_axis="z", notes="n")
        assert len(updated) == 2

        cities = mesh_import.list_library()
        for f in cities[0]["files"]:
            assert f["location"]["bbox"] == _BBOX

    def test_set_location_single_file_only(self, _library_dir):
        rel = "TestCity_Pack/TestCity_Solid.stl"
        updated = mesh_import.set_library_location(rel, _BBOX, apply_to_city=False)
        assert updated == [rel]
        loc = mesh_import.get_library_location("TestCity_Pack/TestCity_Water.stl")
        assert loc is None

    def test_missing_library_dir_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mesh_import, "MICROPOLITAN_STL_DIR", tmp_path / "does-not-exist")
        with pytest.raises(mesh_import.MeshImportError, match="not found"):
            mesh_import.list_library()

    def test_path_traversal_rejected(self, _library_dir):
        with pytest.raises(mesh_import.MeshImportError, match="escapes"):
            mesh_import.get_library_location("../../../etc/passwd")

    def test_compute_library_heightmap_is_cached(self, _library_dir, _redirect_cache):
        rel = "TestCity_Pack/TestCity_Solid.stl"
        hm1, mask1 = mesh_import.compute_library_heightmap(
            rel, _CORE_BBOX, resolution_m=_CORE_RES_M, up_axis="z")
        assert mask1.any()
        # Second call should hit the disk cache and return identical data.
        hm2, mask2 = mesh_import.compute_library_heightmap(
            rel, _CORE_BBOX, resolution_m=_CORE_RES_M, up_axis="z")
        np.testing.assert_array_equal(mask1, mask2)
        np.testing.assert_allclose(np.nan_to_num(hm1), np.nan_to_num(hm2))


class TestLibraryRoutes:
    def test_list_and_set_location_via_http(self, client, tmp_data_dir, monkeypatch, _library_dir):
        monkeypatch.setattr(mesh_import, "CACHE_ROOT", tmp_data_dir["cache_root"])

        r = client.get("/api/layers/mesh/library")
        assert r.status_code == 200
        cities = r.json()["cities"]
        assert len(cities) == 1

        rel = cities[0]["files"][0]["rel_path"]
        r = client.post(f"/api/layers/mesh/library/{rel}/location", json={
            **_BBOX, "up_axis": "z", "notes": "", "apply_to_city": True,
        })
        assert r.status_code == 200
        assert len(r.json()["updated"]) == 2

        r = client.post(f"/api/layers/mesh/library/{rel}/heightmap",
                        json={**_BBOX, "resolution_m": _HTTP_RES_M, "up_axis": "z", "infill": "none"})
        assert r.status_code == 200
        body = r.json()
        assert body["valid_pct"] > 0
        h, w = body["dimensions"]

        pairs = {
            "point_pairs": [
                {"ref_x": 0, "ref_y": 0, "mesh_x": 0, "mesh_y": 0},
                {"ref_x": 100, "ref_y": 0, "mesh_x": w, "mesh_y": 0},
                {"ref_x": 0, "ref_y": 100, "mesh_x": 0, "mesh_y": h},
            ],
            "ref_width": 100, "ref_height": 100, "mesh_width": w, "mesh_height": h,
        }
        r = client.post(f"/api/layers/mesh/library/{rel}/register", json=pairs)
        assert r.status_code == 200
        assert r.json()["dimensions"] == [100, 100]

    def test_library_register_without_heightmap_returns_400(self, client, tmp_data_dir, monkeypatch, _library_dir):
        monkeypatch.setattr(mesh_import, "CACHE_ROOT", tmp_data_dir["cache_root"])
        r = client.get("/api/layers/mesh/library")
        rel = r.json()["cities"][0]["files"][0]["rel_path"]
        r = client.post(f"/api/layers/mesh/library/{rel}/register", json={
            "point_pairs": [
                {"ref_x": 0, "ref_y": 0, "mesh_x": 0, "mesh_y": 0},
                {"ref_x": 1, "ref_y": 0, "mesh_x": 1, "mesh_y": 0},
                {"ref_x": 0, "ref_y": 1, "mesh_x": 0, "mesh_y": 1},
            ],
            "ref_width": 10, "ref_height": 10, "mesh_width": 10, "mesh_height": 10,
        })
        assert r.status_code == 400


# ── auto mode: city-name parsing ─────────────────────────────────────────

class TestParseCityNameFromPath:
    """Filename/foldername -> city-name heuristic used to seed geocoding.

    Verified against the real micropolitan folder names (see
    docs/plans/F-MESHIMPORT-stl-obj-layer-import.md's auto-mode investigation).
    """

    @pytest.mark.parametrize("path,expected", [
        ("Barcelona,_Spain_-_S,_M,_L,_&_XL/Barcelona, Spain_L_Solid.stl", "Barcelona, Spain"),
        ("Miami,_FL_-_L_&_XL/Miami, FL_L_Solid.stl", "Miami, FL"),
        ("Paris,_France_-_S,_M,_L,_&_XL/Paris, France_XL_Water_A1.stl", "Paris, France"),
        ("Prague,_Czech_Republic_-_S,_M,_L,_&_XL/Prague_L_Solid.stl", "Prague, Czech Republic"),
        ("Philadelphia  PA - L   XL/Philadelphia PA_L_Solid.stl", "Philadelphia PA"),
    ])
    def test_extracts_city_from_library_rel_path(self, path, expected):
        assert mesh_import.parse_city_name_from_path(path) == expected

    def test_bare_filename_no_folder(self):
        # No parent folder to prefer — falls back to the filename stem.
        result = mesh_import.parse_city_name_from_path("Miami,_FL_-_L_Solid.stl")
        assert result == "Miami, FL"

    def test_upload_filename_with_underscores_only(self):
        result = mesh_import.parse_city_name_from_path("verify_box.stl")
        assert result == "verify box"


class TestAutoRegisterUnavailable:
    """When numpy2stl.registration.pipeline/applications.cities can't be
    imported, auto_register must degrade to a clear 'unavailable' status
    rather than raising."""

    def test_returns_unavailable_status_when_guarded_import_failed(self, monkeypatch):
        monkeypatch.setattr(mesh_import, "_AUTO_REGISTER_AVAILABLE", False)
        result = mesh_import.auto_register(Path("does-not-matter.stl"), "Miami, FL")
        assert result["status"] == "unavailable"
        assert result["bbox"] is None
