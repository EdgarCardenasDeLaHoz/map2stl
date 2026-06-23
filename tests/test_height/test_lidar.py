"""Unit tests for the USGS 3DEP LiDAR provider — no network."""

import numpy as np
import pytest
from unittest.mock import patch

from city2stl.skyline.height import HeightResult
from city2stl.skyline.height.providers.lidar_3dep import (
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
    # The provider computes nDSM = COP30 DSM − SRTM DTM, both fetched via
    # ``_fetch_opentopo_dem(demtype, bbox, api_key, label)`` and gated on an
    # OpenTopography API key. (Earlier 3DEP ImageServer path removed.)
    @patch("city2stl.skyline.height.providers.lidar_3dep.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.lidar_3dep.write_height_result")
    @patch("city2stl.skyline.height.providers.lidar_3dep._get_api_key", return_value="key")
    @patch("city2stl.skyline.height.providers.lidar_3dep._fetch_opentopo_dem")
    def test_ndsm_subtraction(self, mock_dem, mock_key, mock_write, mock_read):
        """DSM=50m (COP30), DTM=30m (SRTM) → nDSM=20m."""
        def fake_dem(demtype, bbox, api_key, label):
            return np.full((40, 40), 50.0 if demtype == "COP30" else 30.0, dtype=np.float32)
        mock_dem.side_effect = fake_dem

        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (40, 40))

        assert isinstance(result, HeightResult)
        assert result.raster.shape == (40, 40)
        np.testing.assert_allclose(result.raster, 20.0, atol=0.1)
        np.testing.assert_allclose(result.confidence, _CONFIDENCE)

    @patch("city2stl.skyline.height.providers.lidar_3dep.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.lidar_3dep.write_height_result")
    @patch("city2stl.skyline.height.providers.lidar_3dep._get_api_key", return_value="key")
    @patch("city2stl.skyline.height.providers.lidar_3dep._fetch_opentopo_dem")
    def test_negative_clamped(self, mock_dem, mock_key, mock_write, mock_read):
        """DTM > DSM artefact → clamp to 0."""
        def fake_dem(demtype, bbox, api_key, label):
            return np.full((20, 20), 30.0 if demtype == "COP30" else 35.0, dtype=np.float32)
        mock_dem.side_effect = fake_dem

        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (20, 20))
        assert np.all(result.raster >= 0)

    @patch("city2stl.skyline.height.providers.lidar_3dep.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.lidar_3dep.write_height_result")
    @patch("city2stl.skyline.height.providers.lidar_3dep._get_api_key", return_value="key")
    @patch("city2stl.skyline.height.providers.lidar_3dep._fetch_opentopo_dem")
    def test_no_dsm_returns_nan(self, mock_dem, mock_key, mock_write, mock_read):
        """No DSM available → all NaN."""
        mock_dem.return_value = None
        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("city2stl.skyline.height.providers.lidar_3dep.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.lidar_3dep.write_height_result")
    @patch("city2stl.skyline.height.providers.lidar_3dep._get_api_key", return_value="key")
    @patch("city2stl.skyline.height.providers.lidar_3dep._fetch_opentopo_dem")
    def test_no_dtm_returns_nan(self, mock_dem, mock_key, mock_write, mock_read):
        """DSM available but no DTM → can't compute nDSM → NaN."""
        def fake_dem(demtype, bbox, api_key, label):
            return np.full((20, 20), 50.0, dtype=np.float32) if demtype == "COP30" else None
        mock_dem.side_effect = fake_dem

        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("city2stl.skyline.height.providers.lidar_3dep.read_height_result")
    def test_cache_hit(self, mock_read):
        """Cached result returned without fetching."""
        raster = np.full((30, 30), 12.0, dtype=np.float32)
        conf = np.full((30, 30), _CONFIDENCE, dtype=np.float32)
        mock_read.return_value = HeightResult(raster, conf, "lidar_3dep", 1.0)
        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (30, 30))
        np.testing.assert_allclose(result.raster, 12.0)
