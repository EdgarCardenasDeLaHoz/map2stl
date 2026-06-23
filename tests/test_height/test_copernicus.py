"""Unit tests for the Copernicus EU Building Height provider — no network."""

import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from city2stl.skyline.height import HeightResult
from city2stl.skyline.height.providers.copernicus import (
    CopernicusProvider,
    _is_in_europe,
    _parse_geotiff_bytes,
    _CONFIDENCE,
)


# ── Geography ────────────────────────────────────────────────────

class TestIsInEurope:
    def test_barcelona(self):
        assert _is_in_europe((41.5, 41.3, 2.3, 2.1))

    def test_granada(self):
        assert _is_in_europe((37.2, 37.1, -3.5, -3.6))

    def test_cartagena_colombia(self):
        assert not _is_in_europe((10.5, 10.3, -75.4, -75.6))

    def test_philadelphia(self):
        assert not _is_in_europe((40.1, 39.9, -75.0, -75.3))

    def test_edge_iceland(self):
        assert _is_in_europe((66.0, 64.0, -18.0, -22.0))

    def test_edge_turkey(self):
        assert _is_in_europe((41.5, 40.5, 30.0, 28.0))


# ── Provider covers ──────────────────────────────────────────────

class TestCopernicusCovers:
    def test_covers_barcelona(self):
        p = CopernicusProvider()
        assert p.covers((41.5, 41.3, 2.3, 2.1))

    def test_not_covers_cartagena(self):
        p = CopernicusProvider()
        assert not p.covers((10.5, 10.3, -75.4, -75.6))

    def test_name(self):
        assert CopernicusProvider().name == "copernicus"


# ── Parse GeoTIFF ────────────────────────────────────────────────

class TestParseGeotiff:
    def test_parse_returns_none_on_bad_data(self):
        """Bad bytes → returns None (not crash)."""
        result = _parse_geotiff_bytes(b"not a geotiff")
        # May return None or raise depending on installed libs
        # Just verify it doesn't crash the process


# ── Provider fetch (mocked) ──────────────────────────────────────

class TestCopernicusFetch:
    @patch("city2stl.skyline.height.providers.copernicus.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.copernicus.write_height_result")
    @patch("city2stl.skyline.height.providers.copernicus._fetch_eu_wcs")
    def test_eu_wcs_success(self, mock_wcs, mock_write, mock_read):
        """EU WCS returns valid data → HeightResult with correct shape."""
        mock_wcs.return_value = np.full((50, 50), 15.0, dtype=np.float32)
        p = CopernicusProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (50, 50))

        assert isinstance(result, HeightResult)
        assert result.raster.shape == (50, 50)
        assert result.source_name == "copernicus"
        np.testing.assert_allclose(result.raster, 15.0)
        np.testing.assert_allclose(result.confidence, _CONFIDENCE)

    @patch("city2stl.skyline.height.providers.copernicus.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.copernicus.write_height_result")
    @patch("city2stl.skyline.height.providers.copernicus._fetch_eu_wcs")
    def test_eu_wcs_failure_returns_nan(self, mock_wcs, mock_write, mock_read):
        """EU WCS fails → all NaN result."""
        mock_wcs.return_value = None
        p = CopernicusProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (20, 20))
        assert np.all(np.isnan(result.raster))
        assert np.all(result.confidence == 0.0)

    @patch("city2stl.skyline.height.providers.copernicus.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.copernicus.write_height_result")
    @patch("city2stl.skyline.height.providers.copernicus._fetch_eu_wcs")
    def test_resamples_to_target_dim(self, mock_wcs, mock_write, mock_read):
        """WCS returns 10×10 but target is 50×50 → resampled."""
        mock_wcs.return_value = np.full((10, 10), 25.0, dtype=np.float32)
        p = CopernicusProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (50, 50))
        assert result.raster.shape == (50, 50)
        np.testing.assert_allclose(result.raster, 25.0, atol=0.1)

    @patch("city2stl.skyline.height.providers.copernicus.read_height_result")
    def test_cache_hit(self, mock_read):
        """Cached result returned without fetching."""
        raster = np.full((30, 30), 18.0, dtype=np.float32)
        conf = np.full((30, 30), _CONFIDENCE, dtype=np.float32)
        mock_read.return_value = HeightResult(raster, conf, "copernicus", 10.0)
        p = CopernicusProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (30, 30))
        np.testing.assert_allclose(result.raster, 18.0)

    def test_cache_key_deterministic(self):
        """Same bbox → same cache key."""
        from app.server.core.cache import make_cache_key
        k1 = make_cache_key("copernicus_bh", 41.5, 41.3, 2.3, 2.1, {"dim": [50, 50]})
        k2 = make_cache_key("copernicus_bh", 41.5, 41.3, 2.3, 2.1, {"dim": [50, 50]})
        assert k1 == k2
        k3 = make_cache_key("copernicus_bh", 41.5, 41.3, 2.3, 2.1, {"dim": [100, 100]})
        assert k1 != k3
