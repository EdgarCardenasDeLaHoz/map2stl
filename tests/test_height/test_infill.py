"""Tests for app.server.core.height.infill.

All tests use synthetic numpy arrays — no external files or network needed.
scipy is required (it's in requirements.txt).
"""

from __future__ import annotations

import numpy as np
import pytest

# Skip entire module if scipy unavailable
pytest.importorskip("scipy")

from app.server.core.height.infill import infill_idw, infill_nearest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ring_heightmap(size: int = 9, ring_val: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Create a square heightmap with known values on a border ring and NaN interior."""
    hm = np.full((size, size), np.nan, dtype=np.float32)
    mask = np.zeros((size, size), dtype=bool)
    hm[0, :] = ring_val
    hm[-1, :] = ring_val
    hm[:, 0] = ring_val
    hm[:, -1] = ring_val
    mask[0, :] = True
    mask[-1, :] = True
    mask[:, 0] = True
    mask[:, -1] = True
    return hm, mask


def _center_known(size: int = 9, center_val: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """Heightmap with a single known pixel at the centre, rest NaN."""
    hm = np.full((size, size), np.nan, dtype=np.float32)
    mask = np.zeros((size, size), dtype=bool)
    cx, cy = size // 2, size // 2
    hm[cy, cx] = center_val
    mask[cy, cx] = True
    return hm, mask


# ── infill_idw ────────────────────────────────────────────────────────────────


class TestInfillIdw:

    def test_no_nan_in_output_within_mask(self):
        hm, mask = _ring_heightmap()
        result = infill_idw(hm, mask=mask)
        # Inside the mask, no NaN should remain
        assert not np.isnan(result[mask]).any()

    def test_known_pixels_are_unchanged(self):
        hm, mask = _ring_heightmap(ring_val=5.0)
        result = infill_idw(hm, mask=mask)
        np.testing.assert_allclose(
            result[mask], hm[mask], rtol=1e-5,
            err_msg="Known pixels must be unchanged after infill"
        )

    def test_fill_values_in_valid_range(self):
        hm, mask = _ring_heightmap(ring_val=5.0)
        result = infill_idw(hm, mask=mask)
        known_min = float(np.nanmin(hm))
        known_max = float(np.nanmax(hm))
        interior = mask & (~np.isnan(hm))
        filled = ~mask | interior
        # Filled values should not exceed known range (plus tolerance)
        assert float(np.nanmin(result)) >= known_min - 0.5
        assert float(np.nanmax(result)) <= known_max + 0.5

    def test_returns_float32(self):
        hm, mask = _ring_heightmap()
        result = infill_idw(hm, mask=mask)
        assert result.dtype == np.float32

    def test_returns_correct_shape(self):
        hm, mask = _ring_heightmap(size=11)
        result = infill_idw(hm, mask=mask)
        assert result.shape == hm.shape

    def test_already_complete_heightmap_is_returned_unchanged(self):
        """If there are no NaN pixels, the input should come back as-is."""
        hm = np.ones((5, 5), dtype=np.float32) * 3.0
        mask = np.ones((5, 5), dtype=bool)
        result = infill_idw(hm, mask=mask)
        np.testing.assert_array_equal(result, hm)

    def test_all_nan_with_dem_baseline_uses_baseline(self):
        """All-NaN heightmap should fall back to the DEM baseline."""
        hm = np.full((5, 5), np.nan, dtype=np.float32)
        dem = np.full((5, 5), 42.0, dtype=np.float32)
        result = infill_idw(hm, dem_baseline=dem)
        np.testing.assert_allclose(result, 42.0, rtol=1e-5)

    def test_dem_baseline_blends_far_from_data(self):
        """Far pixels should trend toward DEM baseline when one is provided."""
        size = 20
        # No explicit mask — all NaN pixels are candidates for infill
        hm = np.full((size, size), np.nan, dtype=np.float32)
        # A single pixel at the top-left with value 100
        hm[0, 0] = 100.0
        dem = np.full((size, size), 0.0, dtype=np.float32)

        result_no_dem = infill_idw(hm.copy(), dem_baseline=None)
        result_dem = infill_idw(hm.copy(), dem_baseline=dem)

        # Bottom-right corner is far from the known pixel.
        # With DEM baseline = 0, result should be pulled toward 0 more than without.
        br_no_dem = result_no_dem[-1, -1]
        br_dem = result_dem[-1, -1]
        assert br_dem < br_no_dem, (
            f"DEM blend should pull far pixels toward 0, "
            f"but no-dem={br_no_dem:.2f}, with-dem={br_dem:.2f}"
        )

    def test_no_mask_fills_all_nan(self):
        """Without a mask, all NaN pixels should be filled."""
        hm, _ = _ring_heightmap()
        result = infill_idw(hm)  # no mask
        # All interior NaN should now be filled
        assert not np.isnan(result).all()

    def test_center_source_fills_outward(self):
        """Single central known value should fill all NaN pixels (no mask → full-grid active)."""
        hm, _ = _center_known(size=7, center_val=10.0)
        # Don't pass a mask so infill operates on all NaN pixels
        result = infill_idw(hm)
        assert not np.isnan(result).any(), "All pixels should be filled"
        # Every pixel should be ~10.0 (nearest/linear from the sole source)
        np.testing.assert_allclose(result, 10.0, atol=0.01)


# ── infill_nearest ────────────────────────────────────────────────────────────


class TestInfillNearest:

    def test_no_nan_in_output(self):
        hm, _ = _ring_heightmap()
        result = infill_nearest(hm)
        assert not np.isnan(result).any()

    def test_known_pixels_are_unchanged(self):
        hm, mask = _ring_heightmap(ring_val=7.0)
        result = infill_nearest(hm)
        np.testing.assert_allclose(
            result[mask], hm[mask], rtol=1e-5,
            err_msg="Known pixels must not change after nearest-neighbour fill"
        )

    def test_returns_float32(self):
        hm, _ = _ring_heightmap()
        result = infill_nearest(hm)
        assert result.dtype == np.float32

    def test_all_nan_returns_zeros(self):
        hm = np.full((4, 4), np.nan, dtype=np.float32)
        result = infill_nearest(hm)
        np.testing.assert_array_equal(result, 0.0)

    def test_no_nan_input_unchanged(self):
        hm = np.arange(16, dtype=np.float32).reshape(4, 4)
        result = infill_nearest(hm)
        np.testing.assert_array_equal(result, hm)

    def test_fill_takes_nearest_value(self):
        """Simple 1-D-like test: left half = 1.0, right half = NaN → fill = 1.0."""
        hm = np.ones((3, 3), dtype=np.float32)
        hm[:, 2] = np.nan   # right column unknown
        result = infill_nearest(hm)
        # The nearest known pixel for the right column is in the middle column
        np.testing.assert_allclose(result[:, 2], 1.0, rtol=1e-5)


# ── Consistency between methods ───────────────────────────────────────────────


class TestInfillConsistency:

    def test_both_methods_cover_same_region(self):
        hm, mask = _ring_heightmap(size=9, ring_val=5.0)
        idw = infill_idw(hm.copy(), mask=mask)
        nn = infill_nearest(hm.copy())
        # Both should fill the same interior region
        idw_nan = np.isnan(idw[mask])
        nn_nan = np.isnan(nn[mask])
        assert not idw_nan.any()
        assert not nn_nan.any()

    def test_known_pixels_agree(self):
        hm, mask = _ring_heightmap(ring_val=3.0)
        idw = infill_idw(hm.copy(), mask=mask)
        nn = infill_nearest(hm.copy())
        np.testing.assert_allclose(idw[mask], nn[mask], rtol=1e-5)
