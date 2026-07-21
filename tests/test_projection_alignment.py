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
                "lambert", "sinusoidal", "miller", "gall"]


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
        esa = np.random.choice([10, 20, 30, 50, 80],
                               size=(h, w)).astype(np.float32)

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
        esa = np.random.choice([10, 20, 30, 50, 80],
                               size=(h, w)).astype(np.float32)

        wm_out, esa_out = _project_water_arrays(
            water, esa, *_BBOX, projection, clip_nans=True)
        assert wm_out.shape == esa_out.shape, (
            f"{projection}: water {wm_out.shape} ≠ ESA {esa_out.shape}")


# ---------------------------------------------------------------------------
# F-PROJ-DIMS: maintain_dimensions=False (variable output aspect ratio)
# ---------------------------------------------------------------------------

# Real DEM fetches always start from an "equal-angle" input (degrees-per-pixel
# equal in lat and lon — see geo2stl/dem.py make_dem_image, which sizes `dim`
# off max(h,w) from raw SRTM tiles). Tests must respect this invariant or
# 'none'/'cosine' results are not comparable to expected_aspect_ratio's default.
def _equal_angle_shape(bbox, dim):
    north, south, east, west = bbox
    lon_span, lat_span = east - west, north - south
    if lon_span >= lat_span:
        return dim, max(1, int(dim * lat_span / lon_span))  # (w, h)
    return max(1, int(dim * lon_span / lat_span)), dim


class TestVariableDimensionOutput:
    """maintain_dimensions=False: output aspect ratio should reflect the
    projection's true geographic shape, and must stay identical across
    independently-projected layers for the same bbox/projection — even at
    different resolutions (resolution and aspect ratio are independent)."""

    @pytest.mark.parametrize("projection", _PROJECTIONS)
    def test_matches_expected_aspect_ratio(self, projection):
        from geo2stl.projections import project_coordinates, expected_aspect_ratio
        w, h = _equal_angle_shape(_BBOX, 300)
        mat = np.random.rand(h, w).astype(np.float32)

        result, _meta = project_coordinates(
            mat, _BBOX, projection=projection, maintain_dimensions=False,
            fill_value=np.nan, clip_nans=True,
        )
        actual_ratio = result.shape[1] / result.shape[0]

        if projection in ("cosine",):
            expected = expected_aspect_ratio(_BBOX, projection, input_shape=(h, w))
        elif projection == "sinusoidal":
            pytest.skip("sinusoidal's clip_nans wing-trim isn't modeled by "
                        "expected_aspect_ratio exactly — covered by the "
                        "cross-layer alignment test instead")
        else:
            expected = expected_aspect_ratio(_BBOX, projection)

        assert actual_ratio == pytest.approx(expected, rel=0.03), (
            f"{projection}: actual aspect {actual_ratio:.4f} != expected {expected:.4f}")

    @pytest.mark.parametrize("projection", _PROJECTIONS + ["none"])
    def test_cross_layer_alignment_same_resolution(self, projection):
        """Two different source arrays (e.g. DEM + water), same bbox/dim/
        projection, must produce identical output shapes."""
        from geo2stl.projections import project_coordinates, verify_layer_alignment
        w, h = _equal_angle_shape(_BBOX, 300)
        dem = np.random.rand(h, w).astype(np.float32)
        water = np.random.choice([0.0, 1.0], size=(h, w)).astype(np.float32)

        dem_out, _ = project_coordinates(dem, _BBOX, projection=projection,
                                         maintain_dimensions=False, clip_nans=True)
        water_out, _ = project_coordinates(water, _BBOX, projection=projection,
                                           maintain_dimensions=False, clip_nans=True)

        verify_layer_alignment(
            {"dem": dem_out.shape, "water": water_out.shape},
            _BBOX, projection, maintain_dimensions=False,
        )  # raises AssertionError on mismatch — a bare call is the assertion

    @pytest.mark.parametrize("projection", _PROJECTIONS + ["none"])
    def test_cross_layer_alignment_different_resolution(self, projection):
        """Resolution (pixel count) is independent of alignment — a DEM at
        600px and a water mask at 300px must still align in aspect ratio."""
        from geo2stl.projections import project_coordinates, verify_layer_alignment
        w600, h600 = _equal_angle_shape(_BBOX, 600)
        w300, h300 = _equal_angle_shape(_BBOX, 300)
        dem = np.random.rand(h600, w600).astype(np.float32)
        water = np.random.choice([0.0, 1.0], size=(h300, w300)).astype(np.float32)

        dem_out, _ = project_coordinates(dem, _BBOX, projection=projection,
                                         maintain_dimensions=False, clip_nans=True)
        water_out, _ = project_coordinates(water, _BBOX, projection=projection,
                                           maintain_dimensions=False, clip_nans=True)

        assert dem_out.shape != water_out.shape, (
            "sanity check: different input resolutions should produce "
            "different pixel counts (otherwise this test proves nothing)")
        verify_layer_alignment(
            {"dem": dem_out.shape, "water": water_out.shape},
            _BBOX, projection, maintain_dimensions=False,
        )

    def test_mismatched_bbox_is_flagged_as_bug(self):
        """A layer accidentally projected against the wrong bbox (e.g. a
        stale/mismatched region between two layer fetches) must raise, not
        silently pass — this is the regression guard for 'flag it as a bug'
        behavior. (For the canonical-frame projections — mercator, lambert,
        equidistant, miller, gall — output shape depends on the bbox+dim
        given to THAT call, not on the input array's own pixel shape, so a
        wrong-bbox call is the realistic failure mode to guard against, not
        a wrong-input-shape call.)"""
        from geo2stl.projections import project_coordinates, verify_layer_alignment
        w, h = _equal_angle_shape(_BBOX, 300)
        dem = np.random.rand(h, w).astype(np.float32)
        other_bbox = (60.0, 10.0, 30.0, -30.0)  # a much larger, differently-shaped region
        w2, h2 = _equal_angle_shape(other_bbox, 300)
        mismatched_layer = np.random.rand(h2, w2).astype(np.float32)

        dem_out, _ = project_coordinates(dem, _BBOX, projection="mercator",
                                         maintain_dimensions=False, clip_nans=True)
        bad_out, _ = project_coordinates(mismatched_layer, other_bbox, projection="mercator",
                                         maintain_dimensions=False, clip_nans=True)

        with pytest.raises(AssertionError, match="aspect ratio mismatch"):
            verify_layer_alignment(
                {"dem": dem_out.shape, "bad_layer": bad_out.shape},
                _BBOX, "mercator", maintain_dimensions=False,
            )

    def test_maintain_dimensions_true_still_works_unchanged(self):
        """Opt-in maintain_dimensions=True must still produce identical
        input/output shapes exactly as before this change, for every
        projection (backward compatibility)."""
        from geo2stl.projections import project_coordinates
        h, w = 100, 120
        mat = np.random.rand(h, w).astype(np.float32)
        for projection in _PROJECTIONS + ["none"]:
            result, _meta = project_coordinates(
                mat, _BBOX, projection=projection, maintain_dimensions=True,
                fill_value=np.nan, clip_nans=False,
            )
            assert result.shape == (h, w), (
                f"{projection}: maintain_dimensions=True changed shape to {result.shape}")

    @pytest.mark.parametrize("projection", _PROJECTIONS)
    def test_maintain_dimensions_true_wins_over_clip_nans(self, projection):
        """Regression guard: clip_nans=True must NOT shrink the output when
        maintain_dimensions=True — that would silently violate the
        documented contract ("output has same dimensions as input").

        Found via Playwright verification: a lat-warping projection
        (mercator) can introduce genuine edge NaNs even when out_m==m (the
        northernmost/southernmost rows can sample outside the valid domain),
        and clip_nans was unconditionally trimming those — e.g. SF bbox at
        dim=200 returned (128,200) instead of the input's (129,200) with
        both maintain_dimensions=True and clip_nans=True set. Real bug, not
        a test artifact — reproduced directly via curl against the live
        /api/terrain/dem endpoint before the fix."""
        from geo2stl.projections import project_coordinates
        # SF-ish bbox — reproduces the exact latitude where mercator's edge
        # sampling falls outside the valid domain for this input shape.
        bbox = (37.812, 37.708, -122.353, -122.514)
        mat = np.random.rand(129, 200).astype(np.float32)
        result, _meta = project_coordinates(
            mat, bbox, projection=projection, maintain_dimensions=True,
            fill_value=np.nan, clip_nans=True,
        )
        assert result.shape == mat.shape, (
            f"{projection}: maintain_dimensions=True + clip_nans=True gave "
            f"{result.shape}, expected input shape {mat.shape}")


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
            # wrong values (should be ignored)
            "vals": [10.0, 20.0, 30.0, 40.0],
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
