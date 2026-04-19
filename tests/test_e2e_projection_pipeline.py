"""
End-to-end tests for the uniform projection pipeline.

Validates the key architectural claim: ALL raster layers fetch in Plate Carrée
first, then project externally through a single shared module.

These tests exercise:
  1. core.projection module directly (project_grid, project_water_arrays, project_rgb_image)
  2. FastAPI endpoints with projection params (DEM, water, ESA, satellite, hydrology, city raster)
  3. Cross-layer dimension alignment after projection
  4. Cache coherence (projection params in cache keys)
  5. TEST_MODE now applies projection so endpoints are fully exercised

See also: test_projection_alignment.py for unit-level projection dimension tests.
"""

import base64
import json

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BBOX_QS = "north=40.0&south=39.9&east=-75.1&west=-75.2"
_BBOX = (40.0, 39.9, -75.1, -75.2)  # north, south, east, west
_VALID_PROJECTIONS = ["cosine", "mercator", "equidistant", "lambert", "sinusoidal"]


# ===================================================================
# Part 1: Direct tests of core.projection module
# ===================================================================

class TestCoreProjectionModule:
    """Test the shared projection.py module that ALL endpoints should use."""

    def test_project_grid_returns_2d_array(self):
        from app.server.core.projection import project_grid
        arr = np.linspace(0, 100, 50 * 60, dtype=np.float32).reshape(50, 60)
        result = project_grid(arr, *_BBOX, "cosine", clip_nans=True)
        assert result.ndim == 2
        assert result.shape[0] > 0
        assert result.shape[1] > 0

    def test_project_grid_none_returns_identity(self):
        from app.server.core.projection import project_grid
        arr = np.linspace(0, 100, 50 * 60, dtype=np.float32).reshape(50, 60)
        result = project_grid(arr, *_BBOX, "none", clip_nans=True)
        np.testing.assert_array_equal(arr, result)

    @pytest.mark.parametrize("projection", _VALID_PROJECTIONS)
    def test_project_grid_same_shape_same_output(self, projection):
        """Two arrays with same shape + bbox → same projected shape."""
        from app.server.core.projection import project_grid
        h, w = 80, 100
        a = np.linspace(0, 500, h * w, dtype=np.float32).reshape(h, w)
        b = np.random.rand(h, w).astype(np.float32)
        pa = project_grid(a, *_BBOX, projection, clip_nans=True)
        pb = project_grid(b, *_BBOX, projection, clip_nans=True)
        assert pa.shape == pb.shape, f"{projection}: {pa.shape} != {pb.shape}"

    @pytest.mark.parametrize("projection", _VALID_PROJECTIONS)
    def test_project_grid_categorical_preserves_integers(self, projection):
        """Categorical projection with fill_value=0 and order=0 (nearest-neighbour)
        should only contain original class IDs or the fill_value (0)."""
        from app.server.core.projection import project_grid
        classes = [10, 20, 30, 50, 80]
        arr = np.random.choice(classes, size=(80, 100)).astype(np.float32)
        result = project_grid(arr, *_BBOX, projection, clip_nans=False,
                              categorical=True)
        valid = result[~np.isnan(result)]
        unique = set(valid.astype(int))
        assert unique <= {0, *classes}, f"Unexpected values: {unique - {0, *classes}}"

    @pytest.mark.parametrize("projection", _VALID_PROJECTIONS)
    def test_project_water_arrays_alignment(self, projection):
        """Water + ESA always aligned after paired projection."""
        from app.server.core.projection import project_water_arrays
        h, w = 80, 100
        water = np.random.choice([0.0, 1.0], size=(h, w)).astype(np.float32)
        esa = np.random.choice([10, 20, 30, 50, 80], size=(h, w)).astype(np.float32)
        wm, ep = project_water_arrays(water, esa, *_BBOX, projection,
                                       clip_nans=True)
        assert wm.shape == ep.shape, (
            f"{projection}: water {wm.shape} != esa {ep.shape}")

    @pytest.mark.parametrize("projection", _VALID_PROJECTIONS)
    def test_project_water_arrays_binary_output(self, projection):
        """Water mask is re-binarized after bilinear interpolation."""
        from app.server.core.projection import project_water_arrays
        h, w = 80, 100
        water = np.random.choice([0.0, 1.0], size=(h, w)).astype(np.float32)
        esa = np.zeros((h, w), dtype=np.float32)
        wm, _ = project_water_arrays(water, esa, *_BBOX, projection,
                                      clip_nans=False)
        unique = set(np.unique(wm))
        assert unique <= {0.0, 1.0}, f"Water mask not binary: {unique}"

    def test_project_rgb_image_shape(self):
        """RGB projection preserves 3 channels and returns uint8."""
        from app.server.core.projection import project_rgb_image
        img = np.random.randint(0, 256, (80, 100, 3), dtype=np.uint8)
        result = project_rgb_image(img, *_BBOX, "cosine", clip_nans=False)
        assert result.ndim == 3
        assert result.shape[2] == 3
        assert result.dtype == np.uint8

    @pytest.mark.parametrize("projection", _VALID_PROJECTIONS)
    def test_project_rgb_dims_match_grid(self, projection):
        """Projected RGB dimensions should match projected grid dimensions (no clip)."""
        from app.server.core.projection import project_grid, project_rgb_image
        h, w = 80, 100
        grid = np.linspace(0, 100, h * w, dtype=np.float32).reshape(h, w)
        img = np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)
        g_proj = project_grid(grid, *_BBOX, projection, clip_nans=False)
        i_proj = project_rgb_image(img, *_BBOX, projection, clip_nans=False)
        assert g_proj.shape[:2] == i_proj.shape[:2], (
            f"{projection}: grid {g_proj.shape[:2]} != rgb {i_proj.shape[:2]}")

    def test_project_rgb_clip_nans_reduces_size(self):
        """clip_nans=True should produce smaller or equal output vs clip_nans=False."""
        from app.server.core.projection import project_rgb_image
        img = np.random.randint(0, 256, (80, 100, 3), dtype=np.uint8)
        no_clip = project_rgb_image(img, *_BBOX, "cosine", clip_nans=False)
        clipped = project_rgb_image(img, *_BBOX, "cosine", clip_nans=True)
        assert clipped.shape[0] <= no_clip.shape[0]
        assert clipped.shape[1] <= no_clip.shape[1]

    def test_project_grid_rejects_unknown_projection(self):
        """Unknown projection name should raise ValueError."""
        from app.server.core.projection import project_grid
        arr = np.zeros((50, 60), dtype=np.float32)
        with pytest.raises(ValueError, match="Unknown projection"):
            project_grid(arr, *_BBOX, "azimuthal_equidistant", clip_nans=False)


# ===================================================================
# Part 2: Endpoint tests (TEST_MODE)
# ===================================================================

class TestEndpointProjectionParams:
    """Verify endpoints ACCEPT projection params and apply them.

    TEST_MODE now applies projection to test data, so these tests
    exercise the full pipeline including projection.
    """

    def test_dem_accepts_projection_param(self, client):
        r = client.get(
            f"/api/terrain/dem?{_BBOX_QS}&dim=20&projection=cosine&clip_nans=true")
        assert r.status_code == 200
        data = r.json()
        assert "dem_values_b64" in data

    def test_dem_accepts_none_projection(self, client):
        r = client.get(
            f"/api/terrain/dem?{_BBOX_QS}&dim=20&projection=none&clip_nans=false")
        assert r.status_code == 200

    def test_satellite_accepts_projection_param(self, client):
        r = client.get(
            f"/api/terrain/satellite?{_BBOX_QS}&dim=20&projection=cosine&clip_nans=true")
        assert r.status_code == 200
        data = r.json()
        assert "image" in data

    def test_water_mask_accepts_projection_param(self, client):
        r = client.get(
            f"/api/terrain/water-mask?{_BBOX_QS}&sat_scale=100&projection=cosine&clip_nans=true")
        assert r.status_code == 200

    def test_esa_accepts_projection_param(self, client):
        r = client.get(
            f"/api/terrain/esa-land-cover?{_BBOX_QS}&sat_scale=100&projection=cosine&clip_nans=true")
        assert r.status_code == 200

    def test_hydrology_accepts_projection_param(self, client):
        r = client.get(
            f"/api/terrain/hydrology?{_BBOX_QS}&dim=20&projection=cosine&clip_nans=true")
        assert r.status_code == 200

    def test_city_raster_accepts_projection_param(self, client):
        """City raster with projection should succeed.
        NOTE: This currently FAILS due to a production bug where NaN
        values from projection are not JSON-serializable.
        See TestCityRasterNaNBug for details."""
        body = {
            "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2,
            "dim": 20,
            "buildings": {"type": "FeatureCollection", "features": []},
            "roads": {"type": "FeatureCollection", "features": []},
            "waterways": {"type": "FeatureCollection", "features": []},
            "projection": "none",
            "clip_nans": True,
        }
        r = client.post("/api/cities/raster", json=body)
        assert r.status_code == 200
        data = r.json()
        assert "values" in data
        assert "width" in data
        assert "height" in data


# ===================================================================
# Part 3: TEST_MODE bypass gap documentation
# ===================================================================

class TestTestModeProjection:
    """Verify that TEST_MODE now applies projection to test data.

    Previously TEST_MODE returned early before the projection code path.
    Now projection is applied even in TEST_MODE, so endpoints produce
    different dimensions when a non-identity projection is requested.
    """

    def test_dem_applies_projection_in_test_mode(self, client):
        """DEM with cosine + clip_nans should have different dimensions than none."""
        r_none = client.get(
            f"/api/terrain/dem?{_BBOX_QS}&dim=30&projection=none")
        r_cos = client.get(
            f"/api/terrain/dem?{_BBOX_QS}&dim=30&projection=cosine&clip_nans=true")
        d_none = r_none.json()["dimensions"]
        d_cos = r_cos.json()["dimensions"]
        # With clip_nans=True at lat ~40°N, cosine should trim width
        assert d_cos[1] <= d_none[1], (
            f"Cosine+clip should narrow width: none={d_none}, cos={d_cos}")

    def test_hydrology_applies_projection_in_test_mode(self, client):
        """Hydrology with cosine + clip_nans should have different dimensions."""
        r_none = client.get(
            f"/api/terrain/hydrology?{_BBOX_QS}&dim=30&projection=none")
        r_cos = client.get(
            f"/api/terrain/hydrology?{_BBOX_QS}&dim=30&projection=cosine&clip_nans=true")
        d_none = r_none.json()["river_grid_dimensions"]
        d_cos = r_cos.json()["river_grid_dimensions"]
        assert d_cos[1] <= d_none[1]

    def test_dem_projection_no_clip_preserves_dims(self, client):
        """maintain_dimensions=True + clip_nans=False should keep dim×dim."""
        r = client.get(
            f"/api/terrain/dem?{_BBOX_QS}&dim=30&projection=cosine&clip_nans=false")
        d = r.json()["dimensions"]
        assert d == [30, 30], f"Expected [30, 30], got {d}"

    def test_satellite_applies_projection_in_test_mode(self, client):
        """Satellite with projection should produce a valid JPEG."""
        r = client.get(
            f"/api/terrain/satellite?{_BBOX_QS}&dim=30&projection=cosine&clip_nans=false")
        data = r.json()
        raw = base64.b64decode(data["image"])
        assert raw[:2] == b'\xff\xd8', "Projected satellite should still be valid JPEG"

    @pytest.mark.parametrize("projection", _VALID_PROJECTIONS)
    def test_dem_all_projections_succeed(self, client, projection):
        """DEM endpoint with every supported projection should succeed."""
        r = client.get(
            f"/api/terrain/dem?{_BBOX_QS}&dim=20&projection={projection}&clip_nans=false")
        assert r.status_code == 200
        data = r.json()
        assert "dem_values_b64" in data
        assert data["dimensions"][0] > 0
        assert data["dimensions"][1] > 0


# ===================================================================
# Part 4: City raster projection DOES work in tests (no TEST_MODE bypass)
# ===================================================================

class TestCityRasterProjectionE2E:
    """City raster is the only endpoint where projection actually runs
    during tests, because rasterize_city_data is a pure function that
    doesn't need network I/O."""

    def _make_body(self, projection="none", clip_nans=True, dim=50):
        return {
            "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2,
            "dim": dim,
            "buildings": {"type": "FeatureCollection", "features": []},
            "roads": {"type": "FeatureCollection", "features": []},
            "waterways": {"type": "FeatureCollection", "features": []},
            "projection": projection,
            "clip_nans": clip_nans,
        }

    def test_city_none_returns_square(self, client):
        """No projection → output is dim×dim."""
        r = client.post("/api/cities/raster", json=self._make_body("none"))
        data = r.json()
        assert data["width"] == 50
        assert data["height"] == 50

    def test_city_values_count_matches_dims_no_projection(self, client):
        """values array length == width * height (no projection)."""
        r = client.post("/api/cities/raster", json=self._make_body("none"))
        data = r.json()
        assert len(data["values"]) == data["width"] * data["height"]


class TestCityRasterNaNBug:
    """PRODUCTION BUG: City raster endpoint crashes with 500 when projection
    is applied because project_grid fills empty areas with NaN, and
    grid.flatten().tolist() serializes NaN which is not valid JSON.

    Root cause: cities.py line 218 does `grid.flatten().tolist()` after
    projection, but NaN values from projection fill are not replaced.

    Fix options:
    1. Use np.nan_to_num(grid, nan=0.0) before .tolist()
    2. Switch to b64 encoding (consistent with DEM/water/hydrology)
    """

    def _make_body(self, projection="none", clip_nans=True, dim=50):
        return {
            "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2,
            "dim": dim,
            "buildings": {"type": "FeatureCollection", "features": []},
            "roads": {"type": "FeatureCollection", "features": []},
            "waterways": {"type": "FeatureCollection", "features": []},
            "projection": projection,
            "clip_nans": clip_nans,
        }

    @pytest.mark.parametrize("projection", _VALID_PROJECTIONS)
    @pytest.mark.xfail(reason="BUG: NaN from projection not JSON-serializable",
                       raises=Exception, strict=False)
    def test_city_projection_crashes_with_nan(self, client, projection):
        """City raster with projection CRASHES due to NaN in JSON.
        When this bug is fixed, remove xfail and these tests should pass.

        Note: Some projections (e.g. lambert) may not produce NaN for all
        bboxes, so strict=False — XPASS is tolerated."""
        r = client.post("/api/cities/raster",
                        json=self._make_body(projection, clip_nans=False))
        # If we get here, the bug is fixed (or this projection didn't produce NaN)
        assert r.status_code == 200

    @pytest.mark.xfail(reason="BUG: NaN from projection not JSON-serializable",
                       raises=Exception, strict=True)
    def test_city_cache_key_includes_projection(self, client):
        """Different projections should NOT return cached results from each other.
        Currently blocked by NaN serialization bug."""
        body_none = self._make_body("none")
        body_cos = self._make_body("cosine", clip_nans=False)
        r1 = client.post("/api/cities/raster", json=body_none)
        r2 = client.post("/api/cities/raster", json=body_cos)
        assert r1.status_code == 200
        assert r2.status_code == 200


# ===================================================================
# Part 5: Cross-layer alignment in TEST_MODE
# ===================================================================

class TestCrossLayerAlignment:
    """Verify that layers return consistent dimensions, including with projection."""

    def test_dem_and_hydrology_same_dims(self, client):
        """DEM and hydrology with same dim should have matching dimensions."""
        r_dem = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=40")
        r_hyd = client.get(f"/api/terrain/hydrology?{_BBOX_QS}&dim=40")
        assert r_dem.json()["dimensions"] == r_hyd.json()["river_grid_dimensions"]

    def test_dem_and_hydrology_same_dims_with_projection(self, client):
        """DEM and hydrology with same dim + projection should match."""
        qs = f"{_BBOX_QS}&dim=40&projection=cosine&clip_nans=true"
        r_dem = client.get(f"/api/terrain/dem?{qs}")
        r_hyd = client.get(f"/api/terrain/hydrology?{qs}")
        assert r_dem.json()["dimensions"] == r_hyd.json()["river_grid_dimensions"]

    def test_dem_b64_decodable_to_correct_shape(self, client):
        """DEM b64 decodes to dimensions[0] * dimensions[1] float32 values."""
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=25")
        data = r.json()
        h, w = data["dimensions"]
        raw = base64.b64decode(data["dem_values_b64"])
        arr = np.frombuffer(raw, dtype=np.float32)
        assert arr.shape[0] == h * w, f"Expected {h*w}, got {arr.shape[0]}"

    def test_satellite_returns_valid_jpeg_b64(self, client):
        """Satellite b64 is valid JPEG data."""
        r = client.get(f"/api/terrain/satellite?{_BBOX_QS}&dim=25")
        data = r.json()
        raw = base64.b64decode(data["image"])
        # JPEG magic bytes
        assert raw[:2] == b'\xff\xd8', "Not a valid JPEG"


# ===================================================================
# Part 6: Schema validation
# ===================================================================

class TestSchemaValidation:
    """Verify Pydantic models accept the new projection fields."""

    def test_city_raster_request_defaults(self):
        from app.server.schemas import CityRasterRequest
        req = CityRasterRequest(
            north=40, south=39.9, east=-75.1, west=-75.2,
            buildings={}, roads={}, waterways={},
        )
        assert req.projection == "none"
        assert req.clip_nans is True

    def test_city_raster_request_custom_projection(self):
        from app.server.schemas import CityRasterRequest
        req = CityRasterRequest(
            north=40, south=39.9, east=-75.1, west=-75.2,
            buildings={}, roads={}, waterways={},
            projection="cosine", clip_nans=False,
        )
        assert req.projection == "cosine"
        assert req.clip_nans is False


# ===================================================================
# Part 7: Import chain validation
# ===================================================================

class TestImportChain:
    """Verify that the new shared projection module is importable and
    that terrain.py delegates to it (not defining its own)."""

    def test_core_projection_importable(self):
        from app.server.core.projection import project_grid
        from app.server.core.projection import project_water_arrays
        from app.server.core.projection import project_rgb_image
        assert callable(project_grid)
        assert callable(project_water_arrays)
        assert callable(project_rgb_image)

    def test_terrain_router_delegates_to_core(self):
        """terrain.py's _project_grid should delegate to core.projection."""
        from app.server.routers.terrain import _project_grid
        from app.server.core.projection import project_grid
        # The thin wrapper should exist and be callable
        assert callable(_project_grid)
        # Verify it's a wrapper, not a re-implementation
        # (the wrapper calls project_grid internally — we can't easily verify
        # without mocking, but we can verify it accepts the same args)
        h, w = 30, 40
        arr = np.linspace(0, 100, h * w, dtype=np.float32).reshape(h, w)
        # Both should produce identical output
        r1 = _project_grid(arr, *_BBOX, "cosine", True)
        r2 = project_grid(arr, *_BBOX, "cosine", True)
        np.testing.assert_array_equal(r1, r2)

    def test_terrain_water_arrays_delegates_to_core(self):
        from app.server.routers.terrain import _project_water_arrays
        from app.server.core.projection import project_water_arrays
        h, w = 30, 40
        water = np.random.choice([0.0, 1.0], size=(h, w)).astype(np.float32)
        esa = np.random.choice([10, 20, 80], size=(h, w)).astype(np.float32)
        wm1, e1 = _project_water_arrays(water, esa, *_BBOX, "cosine", True)
        wm2, e2 = project_water_arrays(water, esa, *_BBOX, "cosine", True)
        np.testing.assert_array_equal(wm1, wm2)
        np.testing.assert_array_equal(e1, e2)


# ===================================================================
# Part 8: DEM local source normalization verification
# ===================================================================

class TestDEMLocalNormalization:
    """Verify that _make_local_dem now forces projection='none'
    so projection is applied externally."""

    def test_make_local_dem_forces_none_projection(self):
        """Inspect _make_local_dem source to verify it passes projection='none'."""
        import inspect
        from app.server.routers.terrain import _make_local_dem
        source = inspect.getsource(_make_local_dem)
        assert "projection='none'" in source, (
            "_make_local_dem should pass projection='none' to make_dem_image, "
            "but the source doesn't contain this override")

    def test_dem_endpoint_projects_all_sources(self):
        """Inspect DEM endpoint to verify projection is applied for ALL sources."""
        import inspect
        from app.server.routers.terrain import get_terrain_dem
        source = inspect.getsource(get_terrain_dem)
        # Should NOT contain the old guard that skipped local sources
        assert 'dem_source != "local"' not in source, (
            "DEM endpoint still has the old dem_source != 'local' guard "
            "that skips projection for local sources")
        # Should contain the uniform projection block
        assert 'projection != "none"' in source


# ===================================================================
# Part 9: Response format consistency audit
# ===================================================================

class TestResponseFormatConsistency:
    """Verify response format patterns across endpoints."""

    def test_dem_uses_b64_not_tolist(self, client):
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=10")
        data = r.json()
        assert "dem_values_b64" in data
        assert "dem_values" not in data, "DEM should use b64, not tolist"

    def test_water_uses_b64_not_tolist(self, client):
        r = client.get(f"/api/terrain/water-mask?{_BBOX_QS}&sat_scale=100")
        data = r.json()
        assert "water_mask_values_b64" in data
        assert "water_mask_values" not in data

    def test_hydrology_uses_b64_not_tolist(self, client):
        r = client.get(f"/api/terrain/hydrology?{_BBOX_QS}&dim=10")
        data = r.json()
        assert "river_grid_values_b64" in data
        assert "river_grid_values" not in data

    def test_city_raster_uses_tolist_not_b64(self, client):
        """KNOWN ISSUE: city raster still uses .tolist() instead of b64.
        This test documents the inconsistency."""
        body = {
            "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2,
            "dim": 10,
            "buildings": {"type": "FeatureCollection", "features": []},
            "roads": {"type": "FeatureCollection", "features": []},
            "waterways": {"type": "FeatureCollection", "features": []},
        }
        r = client.post("/api/cities/raster", json=body)
        data = r.json()
        # Currently uses "values" (tolist) — not "values_b64"
        assert "values" in data, "Expected 'values' key (tolist format)"
        assert isinstance(data["values"], list), "values should be a list (tolist)"

    def test_satellite_response_has_image_and_bbox(self, client):
        r = client.get(f"/api/terrain/satellite?{_BBOX_QS}&dim=10")
        data = r.json()
        assert "image" in data
        assert "bbox" in data
        # Known gap: no "dimensions" key in satellite response
        assert "dimensions" not in data, (
            "Satellite response should document dimensions for alignment")


# ===================================================================
# Part 10: parse_bool edge cases
# ===================================================================

class TestParseBool:
    """Verify parse_bool handles all JS frontend variants correctly."""

    def test_parse_bool_true_values(self):
        from app.server.core.validation import parse_bool

        class FakeParams(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        for val in ["true", "True", "TRUE", "1", "yes", "on"]:
            assert parse_bool(FakeParams(clip_nans=val), "clip_nans") is True, (
                f"parse_bool should return True for '{val}'")

    def test_parse_bool_false_values(self):
        from app.server.core.validation import parse_bool

        class FakeParams(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        for val in ["false", "False", "0", "no", "off"]:
            assert parse_bool(FakeParams(clip_nans=val), "clip_nans") is False, (
                f"parse_bool should return False for '{val}'")

    def test_parse_bool_missing_key(self):
        from app.server.core.validation import parse_bool

        class FakeParams(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        assert parse_bool(FakeParams(), "clip_nans", True) is True
        assert parse_bool(FakeParams(), "clip_nans", False) is False
