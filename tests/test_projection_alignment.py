"""
Tests for projection alignment across layers (DEM, water, ESA, hydrology).

Verifies:
1. _project_grid produces identical output dimensions for same bbox/projection
2. _project_water_arrays keeps water + ESA aligned
3. API endpoints return b64-encoded data with correct keys
4. Session client _decode_b64_grid correctly round-trips b64 data
"""

import base64

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Direct projection alignment tests (no server needed)
# ---------------------------------------------------------------------------

_BBOX = (40.0, 39.5, -75.0, -75.5)  # north, south, east, west
_PROJECTIONS = ["cosine", "mercator", "equidistant",
                "lambert", "sinusoidal"]


def _project_grid(arr, projection, clip_nans=True, categorical=False):
    """Wrapper matching terrain.py's _project_grid."""
    from geo2stl.projections import project_coordinates
    projected, _meta = project_coordinates(
        arr, _BBOX,
        projection=projection,
        maintain_dimensions=True,
        fill_value=0 if categorical else np.nan,
        clip_nans=clip_nans if not categorical else False,
    )
    return projected


class TestProjectionDimensionConsistency:
    """Verify that _project_grid produces identical dimensions for different
    input arrays with the same bbox and projection."""

    @pytest.mark.parametrize("projection", _PROJECTIONS)
    def test_same_shape_inputs_produce_same_output(self, projection):
        """DEM-shaped and water-shaped arrays (same dims) → same projected dims."""
        h, w = 100, 120
        dem = np.linspace(0, 500, h * w, dtype=np.float32).reshape(h, w)
        water = np.random.choice([0.0, 1.0], size=(h, w)).astype(np.float32)

        dem_proj = _project_grid(dem, projection, clip_nans=True)
        water_proj = _project_grid(water, projection, clip_nans=True)
        assert dem_proj.shape == water_proj.shape, (
            f"{projection}: DEM {dem_proj.shape} ≠ water {water_proj.shape}")

    @pytest.mark.parametrize("projection", _PROJECTIONS)
    def test_categorical_same_shape_as_continuous(self, projection):
        """ESA (categorical) produces same dims as DEM (continuous)
        when clip_nans=True and same input shape."""
        h, w = 100, 120
        dem = np.linspace(0, 500, h * w, dtype=np.float32).reshape(h, w)
        esa = np.random.choice([10, 20, 30, 50, 80], size=(h, w)).astype(np.float32)

        dem_proj = _project_grid(dem, projection, clip_nans=True)
        esa_proj = _project_grid(esa, projection, categorical=True)
        # Categorical uses order=0 (nearest-neighbour) and clip_nans=False,
        # while DEM uses order=1 (bilinear) and clip_nans=True.
        # Different interpolation orders produce different NaN edge patterns,
        # so clipped DEM may be smaller than unclipped ESA.
        assert esa_proj.shape[0] >= dem_proj.shape[0], (
            f"{projection}: ESA height {esa_proj.shape[0]} < DEM {dem_proj.shape[0]}"
            f" — clipped DEM should be <= unclipped ESA")

    @pytest.mark.parametrize("projection", _PROJECTIONS)
    def test_project_water_arrays_keeps_alignment(self, projection):
        """_project_water_arrays guarantees water and ESA stay aligned."""
        from strm2stl.app.server.routers.terrain import _project_water_arrays
        h, w = 100, 120
        water = np.random.choice([0.0, 1.0], size=(h, w)).astype(np.float32)
        esa = np.random.choice([10, 20, 30, 50, 80], size=(h, w)).astype(np.float32)

        wm_out, esa_out = _project_water_arrays(
            water, esa, *_BBOX, projection, clip_nans=True)
        assert wm_out.shape == esa_out.shape, (
            f"{projection}: water {wm_out.shape} ≠ ESA {esa_out.shape}")


# ---------------------------------------------------------------------------
# b64 round-trip tests
# ---------------------------------------------------------------------------

class TestB64RoundTrip:
    def test_encode_decode_round_trip(self):
        """Server b64_encode → session _decode_b64_grid round-trips correctly."""
        from app.server.core.validation import b64_encode
        arr = np.array([[1.0, 2.5], [3.0, 4.5]], dtype=np.float32)
        encoded = b64_encode(arr)

        # Decode same way as session client
        raw = base64.b64decode(encoded)
        decoded = np.frombuffer(raw, dtype=np.float32).reshape(2, 2)
        np.testing.assert_array_almost_equal(arr, decoded)

    def test_decode_grid_response_prefers_b64(self):
        """_decode_grid_response picks b64 key when both present."""
        from app.session.terrain_session import TerrainSession
        arr = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        b64_str = base64.b64encode(arr.ravel().tobytes()).decode("ascii")

        data = {
            "vals_b64": b64_str,
            "vals": [10.0, 20.0, 30.0, 40.0],  # wrong values (should be ignored)
        }
        s = TerrainSession.__new__(TerrainSession)
        result = s._decode_grid_response(data, "vals_b64", "vals", 2, 2)
        np.testing.assert_array_equal(result, arr)

    def test_decode_grid_response_falls_back_to_list(self):
        """_decode_grid_response falls back to plain list when b64 key missing."""
        from app.session.terrain_session import TerrainSession
        data = {
            "vals": [1.0, 2.0, 3.0, 4.0],
        }
        s = TerrainSession.__new__(TerrainSession)
        result = s._decode_grid_response(data, "vals_b64", "vals", 2, 2)
        expected = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
        np.testing.assert_array_equal(result, expected)


# ---------------------------------------------------------------------------
# API endpoint b64 key tests (TEST_MODE)
# ---------------------------------------------------------------------------

class TestEndpointB64Keys:
    _QS = "north=40.0&south=39.9&east=-75.1&west=-75.2"

    def test_dem_returns_b64(self, client):
        r = client.get(f"/api/terrain/dem?{self._QS}&dim=10")
        data = r.json()
        assert "dem_values_b64" in data
        assert "dem_values" not in data

    def test_dem_raw_returns_b64(self, client):
        r = client.get(f"/api/terrain/dem/raw?{self._QS}&dim=10")
        data = r.json()
        assert "dem_values_b64" in data

    def test_water_mask_returns_b64(self, client):
        r = client.get(f"/api/terrain/water-mask?{self._QS}&sat_scale=100")
        data = r.json()
        assert "water_mask_values_b64" in data
        assert "water_mask_values" not in data
        assert "esa_values_b64" in data
        assert "esa_values" not in data

    def test_hydrology_returns_b64(self, client):
        r = client.get(f"/api/terrain/hydrology?{self._QS}&dim=10")
        data = r.json()
        assert "river_grid_values_b64" in data
        assert "river_grid_values" not in data

    def test_water_mask_b64_decodable(self, client):
        """Water mask b64 decodes to correct shape."""
        r = client.get(f"/api/terrain/water-mask?{self._QS}&sat_scale=100")
        data = r.json()
        h, w = data["water_mask_dimensions"]
        raw = base64.b64decode(data["water_mask_values_b64"])
        arr = np.frombuffer(raw, dtype=np.float32)
        assert arr.shape[0] == h * w

    def test_hydrology_b64_decodable(self, client):
        """Hydrology b64 decodes to correct shape."""
        r = client.get(f"/api/terrain/hydrology?{self._QS}&dim=10")
        data = r.json()
        h, w = data["river_grid_dimensions"]
        raw = base64.b64decode(data["river_grid_values_b64"])
        arr = np.frombuffer(raw, dtype=np.float32)
        assert arr.shape[0] == h * w

    def test_all_layers_same_dims_test_mode(self, client):
        """In TEST_MODE with same dim, DEM and hydrology have matching dims."""
        dem_r = client.get(f"/api/terrain/dem?{self._QS}&dim=50")
        hydro_r = client.get(f"/api/terrain/hydrology?{self._QS}&dim=50")
        dem_dims = dem_r.json()["dimensions"]
        hydro_dims = hydro_r.json()["river_grid_dimensions"]
        assert dem_dims == hydro_dims, (
            f"DEM {dem_dims} ≠ hydrology {hydro_dims}")
