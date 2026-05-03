"""Unit tests for the USGS 3DEP LiDAR provider — no network."""

import numpy as np
import pytest
from unittest.mock import patch

from app.server.core.height import HeightResult
from city2stl.height.providers.lidar_3dep import (
    LiDAR3DEPProvider,
    _is_in_us,
    _CONFIDENCE,
)


# ── Geography ────────────────────────────────────────────────────

class TestIsInUS:
    def test_philadelphia(self):
        assert _is_in_us((40.1, 39.9, -75.0, -75.3))

    def test_new_york(self):
        assert _is_in_us((40.9, 40.6, -73.8, -74.1))

    def test_barcelona_not_us(self):
        assert not _is_in_us((41.5, 41.3, 2.3, 2.1))

    def test_cartagena_not_us(self):
        assert not _is_in_us((10.5, 10.3, -75.4, -75.6))

    def test_hawaii(self):
        assert _is_in_us((21.5, 19.5, -155.0, -156.0))

    def test_alaska(self):
        assert _is_in_us((65.0, 60.0, -148.0, -152.0))


# ── Provider covers ──────────────────────────────────────────────

class TestLiDARCovers:
    def test_covers_philadelphia(self):
        p = LiDAR3DEPProvider()
        assert p.covers((40.1, 39.9, -75.0, -75.3))

    def test_not_covers_barcelona(self):
        p = LiDAR3DEPProvider()
        assert not p.covers((41.5, 41.3, 2.3, 2.1))

    def test_name(self):
        assert LiDAR3DEPProvider().name == "lidar_3dep"


# ── Fetch (mocked) ──────────────────────────────────────────────

class TestLiDARFetch:
    @patch("city2stl.height.providers.lidar_3dep.read_array_cache", return_value=None)
    @patch("city2stl.height.providers.lidar_3dep.write_array_cache")
    @patch("city2stl.height.providers.lidar_3dep._fetch_3dep_image")
    def test_ndsm_subtraction(self, mock_fetch, mock_write, mock_read):
        """DSM=50m, DTM=30m → nDSM=20m."""
        def fake_fetch(bbox, dim, rendering_rule=None):
            if rendering_rule:  # DSM
                return np.full(dim, 50.0, dtype=np.float32)
            else:  # DTM
                return np.full(dim, 30.0, dtype=np.float32)
        mock_fetch.side_effect = fake_fetch

        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (40, 40))

        assert isinstance(result, HeightResult)
        assert result.raster.shape == (40, 40)
        np.testing.assert_allclose(result.raster, 20.0, atol=0.1)
        np.testing.assert_allclose(result.confidence, _CONFIDENCE)

    @patch("city2stl.height.providers.lidar_3dep.read_array_cache", return_value=None)
    @patch("city2stl.height.providers.lidar_3dep.write_array_cache")
    @patch("city2stl.height.providers.lidar_3dep._fetch_3dep_image")
    def test_negative_clamped(self, mock_fetch, mock_write, mock_read):
        """DTM > DSM artefact → clamp to 0."""
        def fake_fetch(bbox, dim, rendering_rule=None):
            if rendering_rule:
                return np.full(dim, 30.0, dtype=np.float32)
            else:
                return np.full(dim, 35.0, dtype=np.float32)
        mock_fetch.side_effect = fake_fetch

        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (20, 20))
        assert np.all(result.raster >= 0)

    @patch("city2stl.height.providers.lidar_3dep.read_array_cache", return_value=None)
    @patch("city2stl.height.providers.lidar_3dep.write_array_cache")
    @patch("city2stl.height.providers.lidar_3dep._fetch_3dep_image")
    def test_no_dsm_returns_nan(self, mock_fetch, mock_write, mock_read):
        """No DSM available → all NaN."""
        mock_fetch.return_value = None
        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("city2stl.height.providers.lidar_3dep.read_array_cache", return_value=None)
    @patch("city2stl.height.providers.lidar_3dep.write_array_cache")
    @patch("city2stl.height.providers.lidar_3dep._fetch_3dep_image")
    def test_no_dtm_returns_nan(self, mock_fetch, mock_write, mock_read):
        """DSM available but no DTM → can't compute nDSM → NaN."""
        def fake_fetch(bbox, dim, rendering_rule=None):
            if rendering_rule:
                return np.full(dim, 50.0, dtype=np.float32)
            return None
        mock_fetch.side_effect = fake_fetch

        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("city2stl.height.providers.lidar_3dep.read_array_cache")
    def test_cache_hit(self, mock_read):
        """Cached result returned without fetching."""
        raster = np.full((30, 30), 12.0, dtype=np.float32)
        conf = np.full((30, 30), _CONFIDENCE, dtype=np.float32)
        mock_read.return_value = (
            {"raster": raster, "confidence": conf},
            {"resolution_m": 1.0}
        )
        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (30, 30))
        np.testing.assert_allclose(result.raster, 12.0)
