"""Unit tests for Phase 1b height providers — GHSL, Open Buildings, Shadow."""

import numpy as np
import pytest
from unittest.mock import patch

from app.server.core.height import HeightResult
from app.server.core.height.providers.ghsl import (
    GHSLProvider, _CONFIDENCE as GHSL_CONF,
)
from app.server.core.height.providers.open_buildings import (
    OpenBuildingsProvider, _is_in_coverage,
    _CONFIDENCE as OB_CONF,
)
from app.server.core.height.providers.shadow_height import (
    ShadowHeightProvider, _estimate_sun_elevation,
    _shadow_length_to_height, _infer_from_rgb, _downsample_height,
    _CONFIDENCE as SHADOW_CONF,
)


# ── GHSL ─────────────────────────────────────────────────────────

class TestGHSL:
    def test_name(self):
        assert GHSLProvider().name == "ghsl"

    def test_covers_global(self):
        p = GHSLProvider()
        assert p.covers((41.5, 41.3, 2.3, 2.1))     # Barcelona
        assert p.covers((10.5, 10.3, -75.4, -75.6))  # Cartagena
        assert p.covers((40.1, 39.9, -75.0, -75.3))  # Philadelphia

    @patch("app.server.core.height.providers.ghsl.read_array_cache", return_value=None)
    @patch("app.server.core.height.providers.ghsl.write_array_cache")
    @patch("app.server.core.height.providers.ghsl._fetch_ghsl_wms")
    def test_fetch_success(self, mock_wms, mock_write, mock_read):
        mock_wms.return_value = np.full((30, 30), 12.0, dtype=np.float32)
        p = GHSLProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (30, 30))
        assert isinstance(result, HeightResult)
        assert result.raster.shape == (30, 30)
        np.testing.assert_allclose(result.raster, 12.0)
        np.testing.assert_allclose(result.confidence, GHSL_CONF)

    @patch("app.server.core.height.providers.ghsl.read_array_cache", return_value=None)
    @patch("app.server.core.height.providers.ghsl.write_array_cache")
    @patch("app.server.core.height.providers.ghsl._fetch_ghsl_wms")
    def test_fetch_failure_nan(self, mock_wms, mock_write, mock_read):
        mock_wms.return_value = None
        p = GHSLProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("app.server.core.height.providers.ghsl.read_array_cache")
    def test_cache_hit(self, mock_read):
        raster = np.full((20, 20), 8.0, dtype=np.float32)
        conf = np.full((20, 20), GHSL_CONF, dtype=np.float32)
        mock_read.return_value = (
            {"raster": raster, "confidence": conf},
            {"resolution_m": 100.0}
        )
        p = GHSLProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (20, 20))
        np.testing.assert_allclose(result.raster, 8.0)


# ── Open Buildings ───────────────────────────────────────────────

class TestOpenBuildings:
    def test_name(self):
        assert OpenBuildingsProvider().name == "open_buildings"

    def test_covers_cartagena(self):
        # Colombia is in Latin America coverage
        assert OpenBuildingsProvider().covers((10.5, 10.3, -75.4, -75.6))

    def test_covers_africa(self):
        assert OpenBuildingsProvider().covers((0.5, -0.5, 37.0, 36.0))

    def test_not_covers_barcelona(self):
        # Europe is NOT in Open Buildings coverage
        assert not OpenBuildingsProvider().covers((41.5, 41.3, 2.3, 2.1))

    def test_not_covers_philadelphia(self):
        assert not OpenBuildingsProvider().covers((40.1, 39.9, -75.0, -75.3))

    def test_is_in_coverage_edge_cases(self):
        # Middle East
        assert _is_in_coverage((35.0, 33.0, 36.0, 34.0))  # Beirut
        # Southeast Asia
        assert _is_in_coverage((14.0, 13.0, 101.0, 100.0))  # Bangkok

    @patch("app.server.core.height.providers.open_buildings.read_array_cache", return_value=None)
    @patch("app.server.core.height.providers.open_buildings._fetch_buildings_for_bbox", return_value=None)
    def test_fetch_returns_nan_when_no_partition_data(self, mock_fetch, mock_read):
        """No intersecting parquet rows returns an empty Open Buildings result."""
        p = OpenBuildingsProvider()
        result = p.fetch_heights((10.5, 10.3, -75.4, -75.6), (20, 20))
        assert np.all(np.isnan(result.raster))
        assert result.source_name == "open_buildings"


# ── Shadow Height ────────────────────────────────────────────────

class TestShadowHeight:
    def test_name(self):
        assert ShadowHeightProvider().name == "shadow_height"

    def test_covers_everywhere(self):
        p = ShadowHeightProvider()
        assert p.covers((41.5, 41.3, 2.3, 2.1))
        assert p.covers((10.5, 10.3, -75.4, -75.6))
        assert p.covers((-33.9, -34.0, 18.5, 18.4))  # Cape Town

    def test_sun_elevation_reasonable(self):
        # Barcelona in June at 10 AM — should be high
        elev = _estimate_sun_elevation(41.4, 2.2, month=6, hour=10)
        assert 30 < elev < 75

    def test_sun_elevation_low_winter(self):
        # High latitude in December — should be low
        elev = _estimate_sun_elevation(60.0, 25.0, month=12, hour=10)
        assert 0 < elev < 30

    def test_sun_elevation_clamped(self):
        # Extreme case — never returns below 5°
        elev = _estimate_sun_elevation(70.0, 0.0, month=12, hour=8)
        assert elev >= 5.0

    def test_shadow_to_height(self):
        # 10 pixels of shadow, 1m/pixel, 45° sun → height = 10m
        h = _shadow_length_to_height(10, 1.0, 45.0)
        assert h == pytest.approx(10.0, abs=0.1)

    def test_shadow_to_height_steep_sun(self):
        # 5 pixels, 2m/pixel, 60° sun → height = 10 * tan(60) ≈ 17.3m
        h = _shadow_length_to_height(5, 2.0, 60.0)
        assert h == pytest.approx(17.32, abs=0.1)

    @patch("app.server.core.height.providers.shadow_height.read_array_cache", return_value=None)
    @patch("app.server.core.height.providers.shadow_height._fetch_rgb_for_bbox", return_value=None)
    def test_fetch_returns_nan_when_no_satellite(self, mock_rgb, mock_read):
        """Falls back to all-NaN when satellite imagery is unavailable."""
        p = ShadowHeightProvider()
        result = p.fetch_heights((10.5, 10.3, -75.4, -75.6), (20, 20))
        assert np.all(np.isnan(result.raster))
        assert result.source_name == "shadow_height"

    def test_infer_realistic_shadow(self):
        """A 15-pixel shadow at ~1 m/pixel gives a realistic building height."""
        # Small bbox: ~556 m span, 500px → ~1.1 m/pixel
        bbox = (41.400, 41.395, 2.205, 2.200)
        rgb = np.full((500, 500, 3), 180, dtype=np.uint8)
        # 15-pixel-tall dark shadow region
        rgb[200:215, 250:260, :] = 30
        result = _infer_from_rgb(rgb, bbox, dim=(50, 50))
        non_nan = ~np.isnan(result.raster)
        assert non_nan.sum() >= 1
        heights = result.raster[non_nan]
        # Should produce a reasonable building height (10–60 m)
        assert all(5 < h < 80 for h in heights)
        assert result.raster.shape == (50, 50)

    def test_infer_filters_huge_shadows(self):
        """Terrain-scale shadows (> 1% of image) are filtered out."""
        bbox = (41.400, 41.395, 2.205, 2.200)
        rgb = np.full((200, 200, 3), 180, dtype=np.uint8)
        # Paint a giant dark area (> 1% of image = > 400 px)
        rgb[0:100, 0:100, :] = 30  # 10000 px >> 400 px limit
        result = _infer_from_rgb(rgb, bbox, dim=(50, 50))
        # Should produce no height estimates (giant shadow filtered)
        assert np.all(np.isnan(result.raster))

    def test_infer_filters_unrealistic_heights(self):
        """Heights below 1m or above 500m are filtered out."""
        bbox = (41.400, 41.395, 2.205, 2.200)
        rgb = np.full((500, 500, 3), 180, dtype=np.uint8)
        # 1-pixel shadow at ~1.1 m/px → very short shadow → < 1m height
        rgb[300, 400, :] = 30  # single pixel
        result = _infer_from_rgb(rgb, bbox, dim=(50, 50))
        non_nan = ~np.isnan(result.raster)
        # Either no estimates (filtered) or all within bounds
        if non_nan.sum() > 0:
            assert all(1 <= h <= 500 for h in result.raster[non_nan])

    def test_downsample_height_preserves_max(self):
        """Downsampling uses max-pooling for sparse height data."""
        hr = np.full((100, 100), np.nan, dtype=np.float32)
        hr[10, 20] = 15.0
        hr[50, 80] = 30.0
        out = _downsample_height(hr, (10, 10))
        assert out.shape == (10, 10)
        valid = out[~np.isnan(out)]
        assert len(valid) >= 2
        assert 15.0 in valid
        assert 30.0 in valid

    def test_downsample_same_size_noop(self):
        """When rgb and dim match, no downsampling occurs."""
        bbox = (41.400, 41.395, 2.205, 2.200)
        rgb = np.full((50, 50, 3), 180, dtype=np.uint8)
        rgb[20:25, 30:35, :] = 30
        result = _infer_from_rgb(rgb, bbox, dim=(50, 50))
        assert result.raster.shape == (50, 50)
