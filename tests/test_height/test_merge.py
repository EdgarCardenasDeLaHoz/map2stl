"""Segment tests for height package scaffolding (Phase 1a.0).

Tests HeightResult dataclass, merge_height_rasters(), and _resample().
No network, no external dependencies beyond numpy.
"""

import numpy as np
import pytest

from app.server.core.height import HeightResult, merge_height_rasters, _resample


# ── Helpers ──────────────────────────────────────────────────────

def _make(h: int, w: int, value: float, conf: float,
          name: str = "test", res: float = 10.0,
          nan_mask: np.ndarray | None = None) -> HeightResult:
    """Build a HeightResult filled with *value* (optionally NaN-masked)."""
    raster = np.full((h, w), value, dtype=np.float32)
    confidence = np.full((h, w), conf, dtype=np.float32)
    if nan_mask is not None:
        raster[nan_mask] = np.nan
        confidence[nan_mask] = 0.0
    return HeightResult(raster=raster, confidence=confidence,
                        source_name=name, resolution_m=res)


# ── HeightResult construction ────────────────────────────────────

class TestHeightResult:
    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError, match="shape"):
            HeightResult(
                raster=np.zeros((5, 5)),
                confidence=np.zeros((3, 3)),
                source_name="bad",
                resolution_m=10.0,
            )

    def test_dtype_coercion(self):
        hr = HeightResult(
            raster=np.zeros((4, 4), dtype=np.float64),
            confidence=np.ones((4, 4), dtype=np.float64),
            source_name="coerce",
            resolution_m=5.0,
        )
        assert hr.raster.dtype == np.float32
        assert hr.confidence.dtype == np.float32


# ── Resample ─────────────────────────────────────────────────────

class TestResample:
    def test_noop_same_shape(self):
        arr = np.arange(12, dtype=np.float32).reshape(3, 4)
        out = _resample(arr, (3, 4))
        np.testing.assert_array_equal(out, arr)

    def test_upsample_preserves_range(self):
        arr = np.array([[0, 10], [20, 30]], dtype=np.float32)
        out = _resample(arr, (10, 10))
        assert out.shape == (10, 10)
        assert np.nanmin(out) >= 0
        assert np.nanmax(out) <= 30

    def test_downsample_preserves_range(self):
        arr = np.random.default_rng(42).uniform(5, 50, (20, 20)).astype(np.float32)
        out = _resample(arr, (5, 5))
        assert out.shape == (5, 5)
        assert np.nanmin(out) >= 5
        assert np.nanmax(out) <= 50

    def test_nan_excluded_from_interpolation(self):
        arr = np.array([[10, np.nan], [10, 10]], dtype=np.float32)
        out = _resample(arr, (4, 4))
        # All valid pixels are 10 — result should be close to 10 everywhere
        valid = out[~np.isnan(out)]
        assert len(valid) > 0
        np.testing.assert_allclose(valid, 10.0, atol=0.01)


# ── Merge: priority ─────────────────────────────────────────────

class TestMergePriority:
    def test_osm_wins_over_copernicus(self):
        """OSM says 25m (conf=1.0), Copernicus says 20m (conf=0.7) → 25m."""
        osm = _make(4, 4, 25.0, 1.0, "osm", 5.0)
        cop = _make(4, 4, 20.0, 0.7, "copernicus", 10.0)
        merged = merge_height_rasters([osm, cop])
        np.testing.assert_allclose(merged.raster, 25.0)
        np.testing.assert_allclose(merged.confidence, 1.0)

    def test_higher_confidence_wins_regardless_of_order(self):
        """Even if low-conf comes first, high-conf overwrites."""
        low = _make(4, 4, 10.0, 0.3, "low")
        high = _make(4, 4, 50.0, 0.9, "high")
        merged = merge_height_rasters([low, high])
        np.testing.assert_allclose(merged.raster, 50.0)

    def test_same_confidence_first_wins(self):
        """Equal confidence — first in list wins."""
        a = _make(4, 4, 15.0, 0.7, "a")
        b = _make(4, 4, 20.0, 0.7, "b")
        merged = merge_height_rasters([a, b])
        np.testing.assert_allclose(merged.raster, 15.0)


# ── Merge: gap filling ──────────────────────────────────────────

class TestMergeGapFilling:
    def test_fills_nan_from_lower_priority(self):
        """OSM has NaN in top-left, Copernicus has 20m → 20m fills gap."""
        nan_mask = np.zeros((4, 4), dtype=bool)
        nan_mask[0, 0] = True
        osm = _make(4, 4, 25.0, 1.0, "osm", nan_mask=nan_mask)
        cop = _make(4, 4, 20.0, 0.7, "copernicus")
        merged = merge_height_rasters([osm, cop])
        assert merged.raster[0, 0] == pytest.approx(20.0)
        assert merged.raster[1, 1] == pytest.approx(25.0)

    def test_all_nan_stays_nan(self):
        """No provider has data → result stays NaN, not default 10m."""
        nan_all = np.ones((4, 4), dtype=bool)
        a = _make(4, 4, 0.0, 0.8, "a", nan_mask=nan_all)
        b = _make(4, 4, 0.0, 0.5, "b", nan_mask=nan_all)
        merged = merge_height_rasters([a, b])
        assert np.all(np.isnan(merged.raster))

    def test_partial_overlap(self):
        """Provider A has left half, B has right half → merged has both."""
        r_a = np.full((4, 4), np.nan, dtype=np.float32)
        r_a[:, :2] = 10.0
        c_a = np.where(np.isnan(r_a), 0.0, 0.8).astype(np.float32)
        a = HeightResult(r_a, c_a, "left", 10.0)

        r_b = np.full((4, 4), np.nan, dtype=np.float32)
        r_b[:, 2:] = 20.0
        c_b = np.where(np.isnan(r_b), 0.0, 0.6).astype(np.float32)
        b = HeightResult(r_b, c_b, "right", 10.0)

        merged = merge_height_rasters([a, b])
        np.testing.assert_allclose(merged.raster[:, :2], 10.0)
        np.testing.assert_allclose(merged.raster[:, 2:], 20.0)
        assert not np.any(np.isnan(merged.raster))


# ── Merge: confidence tracking ───────────────────────────────────

class TestMergeConfidence:
    def test_confidence_reflects_source(self):
        """Merged confidence should be the winning source's confidence."""
        nan_top = np.zeros((4, 4), dtype=bool)
        nan_top[:2, :] = True
        high = _make(4, 4, 30.0, 0.95, "lidar", nan_mask=nan_top)
        low = _make(4, 4, 25.0, 0.5, "wsf3d")
        merged = merge_height_rasters([high, low])
        # Bottom half: lidar wins (0.95)
        np.testing.assert_allclose(merged.confidence[2:, :], 0.95)
        # Top half: wsf3d fills (0.5)
        np.testing.assert_allclose(merged.confidence[:2, :], 0.5)

    def test_source_name_is_merged(self):
        merged = merge_height_rasters([_make(2, 2, 5.0, 0.8, "a")])
        assert merged.source_name == "merged"


# ── Merge: resolution resampling ─────────────────────────────────

class TestMergeResolution:
    def test_different_resolutions_resampled(self):
        """Copernicus 10m (2×2), target 4×4 → resampled before merge."""
        small = _make(2, 2, 20.0, 0.7, "copernicus", 10.0)
        merged = merge_height_rasters([small], target_shape=(4, 4))
        assert merged.raster.shape == (4, 4)
        np.testing.assert_allclose(merged.raster, 20.0)

    def test_resolution_m_uses_best(self):
        a = _make(4, 4, 10.0, 0.8, "coarse", 30.0)
        b = _make(4, 4, 15.0, 0.5, "fine", 5.0)
        merged = merge_height_rasters([a, b])
        assert merged.resolution_m == 5.0

    def test_target_shape_overrides(self):
        hr = _make(10, 10, 5.0, 0.6, "src")
        merged = merge_height_rasters([hr], target_shape=(20, 20))
        assert merged.raster.shape == (20, 20)


# ── Edge cases ───────────────────────────────────────────────────

class TestMergeEdgeCases:
    def test_empty_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            merge_height_rasters([])

    def test_single_result_passthrough(self):
        hr = _make(4, 4, 42.0, 0.9, "solo")
        merged = merge_height_rasters([hr])
        np.testing.assert_allclose(merged.raster, 42.0)
        np.testing.assert_allclose(merged.confidence, 0.9)

    def test_many_providers(self):
        """Five providers with decreasing confidence, increasing gaps."""
        providers = []
        for i in range(5):
            mask = np.zeros((8, 8), dtype=bool)
            mask[:i + 1, :] = True  # progressively more NaN rows
            providers.append(
                _make(8, 8, float(10 * (i + 1)), 1.0 - i * 0.2,
                      f"p{i}", nan_mask=mask)
            )
        merged = merge_height_rasters(providers)
        # Row 0: all providers are NaN → stays NaN
        assert np.all(np.isnan(merged.raster[0, :]))
        # Last row: all providers have data → highest confidence (p0) wins
        np.testing.assert_allclose(merged.raster[7, :], 10.0)
