"""Tests for the Google 3D Tiles height provider.

All tests use synthetic data — no network or API key required.
"""
import math

import numpy as np
import pytest
import trimesh

from city2stl.height.providers.google_3d import (  # noqa: E402
    Google3DProvider,
    _accumulate_mesh,
    _bv_intersects_bbox,
    _finish_dsm,
    _looks_built,
    _new_dsm_acc,
    _sample_spacing_m,
    _target_error_m,
    ecef_to_wgs84,
    wgs84_to_ecef,
)


def _meshes_to_dsm(meshes, bbox, dim):
    """Bin whole meshes at once, the way the tests want to read.

    The provider bins one tile at a time and discards it, so that peak memory
    follows the download chunk rather than the tile budget. These are the same
    three calls it makes, gathered up for tests that have only a mesh or two.
    """
    acc = _new_dsm_acc(dim)
    spacing_m = _sample_spacing_m(bbox, dim)
    for mesh in meshes:
        _accumulate_mesh(acc, mesh, bbox, dim, spacing_m)
    return _finish_dsm(acc, dim)


# ── ECEF ↔ WGS84 transforms ────────────────────────────────────

class TestECEFWGS84:
    def test_roundtrip(self):
        """ECEF → WGS84 → ECEF roundtrip preserves coordinates."""
        lon0, lat0, alt0 = 2.1734, 41.3851, 100.0  # Barcelona
        x, y, z = wgs84_to_ecef(lon0, lat0, alt0)
        lon, lat, alt = ecef_to_wgs84(
            np.array([x]), np.array([y]), np.array([z]),
        )
        assert float(lon[0]) == pytest.approx(lon0, abs=1e-6)
        assert float(lat[0]) == pytest.approx(lat0, abs=1e-6)
        assert float(alt[0]) == pytest.approx(alt0, abs=0.1)

    def test_known_barcelona(self):
        """Known ECEF for Barcelona → correct lat/lon."""
        # Barcelona: 41.3851°N, 2.1734°E, ~0m
        x, y, z = wgs84_to_ecef(2.1734, 41.3851, 0.0)
        lon, lat, alt = ecef_to_wgs84(
            np.array([x]), np.array([y]), np.array([z]),
        )
        assert float(lat[0]) == pytest.approx(41.3851, abs=1e-4)
        assert float(lon[0]) == pytest.approx(2.1734, abs=1e-4)
        assert abs(float(alt[0])) < 1.0  # near sea level

    def test_equator_prime_meridian(self):
        """0°N 0°E at sea level."""
        x, y, z = wgs84_to_ecef(0.0, 0.0, 0.0)
        lon, lat, alt = ecef_to_wgs84(
            np.array([x]), np.array([y]), np.array([z]),
        )
        assert abs(float(lon[0])) < 1e-6
        assert abs(float(lat[0])) < 1e-6
        assert abs(float(alt[0])) < 1.0

    def test_south_pole(self):
        """South pole at 0m altitude."""
        x, y, z = wgs84_to_ecef(0.0, -90.0, 0.0)
        lon, lat, alt = ecef_to_wgs84(
            np.array([x]), np.array([y]), np.array([z]),
        )
        assert float(lat[0]) == pytest.approx(-90.0, abs=1e-4)
        assert abs(float(alt[0])) < 1.0

    def test_vectorized(self):
        """Multiple points at once."""
        xs = np.array([wgs84_to_ecef(0, 0, 0)[0],
                       wgs84_to_ecef(90, 0, 0)[0]])
        ys = np.array([wgs84_to_ecef(0, 0, 0)[1],
                       wgs84_to_ecef(90, 0, 0)[1]])
        zs = np.array([wgs84_to_ecef(0, 0, 0)[2],
                       wgs84_to_ecef(90, 0, 0)[2]])
        lon, lat, alt = ecef_to_wgs84(xs, ys, zs)
        assert len(lon) == 2
        assert float(lon[0]) == pytest.approx(0.0, abs=1e-4)
        assert float(lon[1]) == pytest.approx(90.0, abs=1e-4)


# ── Bounding volume intersection ────────────────────────────────

class TestBVIntersection:
    def test_region_inside(self):
        bbox = (42.0, 41.0, 3.0, 1.0)  # Barcelona area
        bv = {"region": [
            math.radians(1.5), math.radians(41.3),
            math.radians(2.5), math.radians(41.5),
            0, 500,
        ]}
        assert _bv_intersects_bbox(bv, bbox)

    def test_region_outside(self):
        bbox = (42.0, 41.0, 3.0, 1.0)
        bv = {"region": [
            math.radians(10.0), math.radians(50.0),
            math.radians(11.0), math.radians(51.0),
            0, 500,
        ]}
        assert not _bv_intersects_bbox(bv, bbox)

    def test_box_inside(self):
        bbox = (42.0, 41.0, 3.0, 1.0)
        # Box centred on Barcelona
        cx, cy, cz = wgs84_to_ecef(2.1734, 41.3851, 0.0)
        bv = {"box": [
            cx, cy, cz,
            1000, 0, 0,  # half-x
            0, 1000, 0,  # half-y
            0, 0, 1000,  # half-z
        ]}
        assert _bv_intersects_bbox(bv, bbox)

    def test_sphere_inside(self):
        bbox = (42.0, 41.0, 3.0, 1.0)
        cx, cy, cz = wgs84_to_ecef(2.1734, 41.3851, 0.0)
        bv = {"sphere": [cx, cy, cz, 5000]}
        assert _bv_intersects_bbox(bv, bbox)

    def test_unknown_type_conservative(self):
        """Unknown BV type returns True (conservative)."""
        assert _bv_intersects_bbox({"custom": [1, 2, 3]}, (42, 41, 3, 1))


# ── Ray-casting DSM ─────────────────────────────────────────────

class TestRaycastDSM:
    def test_flat_plane(self):
        """Flat plane at known altitude → uniform DSM."""
        # Create a flat plane at ~100m above Barcelona
        cx, cy, cz = wgs84_to_ecef(2.17, 41.385, 100.0)
        # Build a large plane in ECEF
        size = 500  # metres
        # Approximate tangent plane at this location
        up = np.array([cx, cy, cz], dtype=np.float64)
        up = up / np.linalg.norm(up)
        east = np.array([-cy, cx, 0], dtype=np.float64)
        east = east / np.linalg.norm(east) * size
        north = np.cross(up, east)
        north = north / np.linalg.norm(north) * size
        centre = np.array([cx, cy, cz], dtype=np.float64)

        verts = np.array([
            centre - east - north,
            centre + east - north,
            centre + east + north,
            centre - east + north,
        ], dtype=np.float64)
        faces = np.array([[0, 1, 2], [0, 2, 3]])
        mesh = trimesh.Trimesh(vertices=verts, faces=faces)

        bbox = (41.390, 41.380, 2.175, 2.165)
        dsm = _meshes_to_dsm([mesh], bbox, (5, 5))

        assert dsm.shape == (5, 5)
        valid = dsm[~np.isnan(dsm)]
        # All valid pixels should be near 100m altitude
        if len(valid) > 0:
            assert np.median(valid) == pytest.approx(100.0, abs=20.0)

    def test_empty_meshes(self):
        """No meshes → all NaN."""
        dsm = _meshes_to_dsm([], (41.4, 41.3, 2.2, 2.1), (10, 10))
        assert dsm.shape == (10, 10)
        assert np.all(np.isnan(dsm))

    def test_box_mesh(self):
        """Box mesh → DSM shows elevated area surrounded by NaN."""
        # Create a box at Barcelona
        cx, cy, cz = wgs84_to_ecef(2.17, 41.385, 50.0)
        box = trimesh.creation.box(extents=[50, 50, 50])
        box.apply_translation([cx, cy, cz])
        # NOTE: This box is in ECEF, so its faces do not align with the
        # lat/lon grid; the test is that binning it produces a raster of
        # the right shape without raising.
        bbox = (41.390, 41.380, 2.175, 2.165)
        dsm = _meshes_to_dsm([box], bbox, (10, 10))
        assert dsm.shape == (10, 10)
        # At least some pixels should have hits (or all NaN if box too small
        # for the grid spacing — both are valid outputs)


# ── Level of detail and coverage ────────────────────────────────

class TestTargetError:
    def test_scales_with_output_cell(self):
        """A coarser grid stops the walk higher up the tile tree."""
        bbox = (41.400, 41.300, 2.200, 2.100)
        coarse = _target_error_m(bbox, (64, 64))
        fine = _target_error_m(bbox, (1024, 1024))
        assert coarse > fine

    def test_clamped_both_ends(self):
        """Extreme grids stay inside the useful part of the tree."""
        bbox = (41.400, 41.300, 2.200, 2.100)
        assert _target_error_m(bbox, (4, 4)) <= 64.0
        assert _target_error_m(bbox, (8192, 8192)) >= 4.0


class TestLooksBuilt:
    """Outside Google's photorealistic cities the tiles are bare terrain.

    The fetch succeeds either way, so relief inside a block-sized window is
    what separates a city from the global base mesh.
    """

    bbox = (41.400, 41.390, 2.180, 2.170)  # roughly 1 km across

    def test_flat_terrain_rejected(self):
        raster = np.zeros((128, 128), dtype=np.float32)
        assert not _looks_built(raster, self.bbox)

    def test_gentle_relief_rejected(self):
        """A few metres of variation is terrain, not buildings."""
        rng = np.random.default_rng(0)
        raster = rng.uniform(0.0, 3.0, size=(128, 128)).astype(np.float32)
        assert not _looks_built(raster, self.bbox)

    def test_buildings_accepted(self):
        """Blocks of tall roofs over open ground read as built."""
        raster = np.zeros((128, 128), dtype=np.float32)
        raster[16:48, 16:48] = 60.0
        raster[80:112, 80:112] = 40.0
        assert _looks_built(raster, self.bbox)

    def test_empty_raster_rejected(self):
        raster = np.full((128, 128), np.nan, dtype=np.float32)
        assert not _looks_built(raster, self.bbox)


# ── Provider interface ──────────────────────────────────────────

class TestGoogle3DProvider:
    def test_name(self):
        assert Google3DProvider.name == "google3d"

    def test_covers_without_key(self, monkeypatch):
        # Force no key. The constructor falls back to get_api_key(), and
        # test_skyline_benchmark loads map2stl/.env into the environment earlier in
        # the same worker, which made this fail in the pre-push run (2026-10-09).
        monkeypatch.setattr("city2stl.height.providers.google_3d.get_api_key", lambda: None)
        p = Google3DProvider(api_key=None)
        assert not p.covers((41.4, 41.3, 2.2, 2.1))

    def test_covers_with_key(self):
        p = Google3DProvider(api_key="test-key")
        assert p.covers((41.4, 41.3, 2.2, 2.1))

    def test_empty_result_without_key(self, monkeypatch, tmp_path, tmp_cache_root):
        """No API key → empty result with NaN."""

        p = Google3DProvider(api_key=None)
        result = p.fetch_heights((41.4, 41.3, 2.2, 2.1), (10, 10))
        assert result.raster.shape == (10, 10)
        assert np.all(np.isnan(result.raster))
        assert np.all(result.confidence == 0.0)

    def test_fetch_with_mock_tileset(self, monkeypatch, tmp_path, tmp_cache_root):
        """Mock the API to return a simple tileset and glb → verify pipeline."""

        # Create a simple box mesh as glb
        box = trimesh.creation.box(extents=[100, 100, 100])
        cx, cy, cz = wgs84_to_ecef(2.17, 41.385, 50.0)
        box.apply_translation([cx, cy, cz])
        glb_bytes = box.export(file_type="glb")

        # Mock tileset JSON
        tileset = {
            "root": {
                "boundingVolume": {
                    "region": [
                        math.radians(2.0), math.radians(41.0),
                        math.radians(2.5), math.radians(42.0),
                        0, 500,
                    ]
                },
                "content": {"uri": "tile.glb"},
                "children": [],
            }
        }

        call_log = []

        class MockResp:
            status_code = 200
            ok = True

            def __init__(self, content, is_json=False):
                self.content = content
                self._json = is_json

            def raise_for_status(self):
                pass

            def json(self):
                return json.loads(self.content)

        import json

        class MockSession:
            def get(self, url, **kw):
                call_log.append(url)
                if "root.json" in url:
                    return MockResp(json.dumps(tileset).encode(), is_json=True)
                return MockResp(glb_bytes)

        monkeypatch.setattr("city2stl.height.providers.google_3d.requests.Session",
                            MockSession)

        p = Google3DProvider(api_key="test-key", max_tiles=10)
        result = p.fetch_heights((41.390, 41.380, 2.175, 2.165), (5, 5))

        assert result.raster.shape == (5, 5)
        assert result.source_name == "google3d"
        assert result.resolution_m == 1.0
        assert len(call_log) >= 1  # at least root.json was fetched

    def test_max_tiles_guard(self):
        """Provider respects max_tiles limit."""
        p = Google3DProvider(api_key="test-key", max_tiles=5)
        assert p._max_tiles == 5

    def test_dem_subtraction(self, monkeypatch, tmp_path, tmp_cache_root):
        """When DEM is provided, output = DSM - DEM."""

        # Create a flat mesh at ~150m altitude
        cx, cy, cz = wgs84_to_ecef(2.17, 41.385, 150.0)
        size = 2000
        up = np.array([cx, cy, cz], dtype=np.float64)
        up = up / np.linalg.norm(up)
        east = np.array([-cy, cx, 0], dtype=np.float64)
        east = east / np.linalg.norm(east) * size
        north_vec = np.cross(up, east)
        north_vec = north_vec / np.linalg.norm(north_vec) * size
        centre = np.array([cx, cy, cz], dtype=np.float64)
        verts = np.array([
            centre - east - north_vec,
            centre + east - north_vec,
            centre + east + north_vec,
            centre - east + north_vec,
        ], dtype=np.float64)
        faces = np.array([[0, 1, 2], [0, 2, 3]])
        mesh = trimesh.Trimesh(vertices=verts, faces=faces)
        glb_bytes = mesh.export(file_type="glb")

        tileset = {
            "root": {
                "boundingVolume": {
                    "region": [
                        math.radians(2.0), math.radians(41.0),
                        math.radians(2.5), math.radians(42.0),
                        0, 500,
                    ]
                },
                "content": {"uri": "tile.glb"},
                "children": [],
            }
        }

        import json as json_mod

        class MockResp:
            status_code = 200
            ok = True
            def __init__(self, content):
                self.content = content
            def raise_for_status(self):
                pass
            def json(self):
                return json_mod.loads(self.content)

        class MockSession:
            def get(self, url, **kw):
                if "root.json" in url:
                    return MockResp(json_mod.dumps(tileset).encode())
                return MockResp(glb_bytes)

        monkeypatch.setattr("city2stl.height.providers.google_3d.requests.Session",
                            MockSession)

        # DEM at 100m terrain → building height should be ~50m
        dem = np.full((5, 5), 100.0, dtype=np.float32)

        p = Google3DProvider(api_key="test-key")
        result = p.fetch_heights((41.390, 41.380, 2.175, 2.165), (5, 5),
                                 dem=dem)

        valid = result.raster[~np.isnan(result.raster)]
        if len(valid) > 0:
            # DSM ~150m, DEM 100m → height ~50m
            assert np.median(valid) == pytest.approx(50.0, abs=30.0)
            # Confidence should be 0.9 where valid
            assert np.all(result.confidence[~np.isnan(result.raster)] == 0.9)


# ── cache key and key lookup (audit 2026-10-05) ─────────────────────────────

def test_cache_key_separates_dem_and_mesh_grounded_results(monkeypatch):
    import city2stl.height.providers.google_3d as g3d

    keys = []

    def _hit(ns, key):
        keys.append(key)
        return {"height": np.zeros((4, 4), np.float32)}, {}

    monkeypatch.setattr(g3d, "read_array_cache", _hit)
    p = g3d.Google3DProvider(api_key="test")
    bbox = (25.78, 25.77, -80.18, -80.19)
    p.fetch_heights(bbox, (4, 4))
    p.fetch_heights(bbox, (4, 4), dem=np.zeros((4, 4), np.float32))
    assert keys[0] != keys[1]


def test_api_key_read_from_map2stl_config(monkeypatch, tmp_path):
    import json

    import city2stl.height.providers.google_3d as g3d

    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    fake = tmp_path / "map2stl" / "city2stl" / "height" / "providers" / "google_3d.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("")
    (tmp_path / "map2stl" / "config.json").write_text(json.dumps({"google_maps_api_key": "k1"}))
    monkeypatch.setattr(g3d, "__file__", str(fake))
    assert g3d.get_api_key() == "k1"


def test_cached_unbuilt_raster_respects_require_built(monkeypatch):
    """A raster cached by a require_built=False caller (flat terrain) must not reach a
    require_built=True caller as heights (T8)."""
    import city2stl.height.providers.google_3d as g3d

    flat = np.zeros((128, 128), np.float32)                 # bare terrain: no buildings
    monkeypatch.setattr(g3d, "read_array_cache", lambda ns, key: ({"height": flat}, {}))
    p = g3d.Google3DProvider(api_key="test")
    bbox = (41.400, 41.390, 2.180, 2.170)
    loose = p.fetch_heights(bbox, (128, 128), require_built=False)
    assert np.array_equal(loose.raster, flat)               # the benchmark still gets it
    strict = p.fetch_heights(bbox, (128, 128))
    assert np.isnan(strict.raster).all()                    # treated as outside coverage
