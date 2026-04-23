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
    _shadow_length_to_height, _CONFIDENCE as SHADOW_CONF,
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
    def test_fetch_returns_nan_placeholder(self, mock_read):
        """Currently unimplemented — returns NaN."""
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
    def test_fetch_returns_nan_placeholder(self, mock_read):
        """Currently unimplemented — returns NaN."""
        p = ShadowHeightProvider()
        result = p.fetch_heights((10.5, 10.3, -75.4, -75.6), (20, 20))
        assert np.all(np.isnan(result.raster))
        assert result.source_name == "shadow_height"
