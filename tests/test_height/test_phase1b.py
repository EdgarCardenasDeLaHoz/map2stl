"""Unit tests for Phase 1b height providers — GHSL, Open Buildings."""

from unittest.mock import patch

import numpy as np

from city2stl.height import HeightResult
from city2stl.height.providers.ghsl import (
    _CONFIDENCE as GHSL_CONF,
)
from city2stl.height.providers.ghsl import (
    GHSLProvider,
)
from city2stl.height.providers.open_buildings import (
    OpenBuildingsProvider,
    _is_in_coverage,
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

    @patch("city2stl.height.providers.ghsl.read_height_result", return_value=None)
    @patch("city2stl.height.providers.ghsl.write_height_result")
    @patch("city2stl.height.providers.ghsl._fetch_ghsl_wms")
    def test_fetch_success(self, mock_wms, mock_write, mock_read):
        mock_wms.return_value = np.full((30, 30), 12.0, dtype=np.float32)
        p = GHSLProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (30, 30))
        assert isinstance(result, HeightResult)
        assert result.raster.shape == (30, 30)
        np.testing.assert_allclose(result.raster, 12.0)
        np.testing.assert_allclose(result.confidence, GHSL_CONF)

    @patch("city2stl.height.providers.ghsl.read_height_result", return_value=None)
    @patch("city2stl.height.providers.ghsl.write_height_result")
    @patch("city2stl.height.providers.ghsl._fetch_ghsl_wms")
    def test_fetch_failure_nan(self, mock_wms, mock_write, mock_read):
        mock_wms.return_value = None
        p = GHSLProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("city2stl.height.providers.ghsl.read_height_result")
    def test_cache_hit(self, mock_read):
        raster = np.full((20, 20), 8.0, dtype=np.float32)
        conf = np.full((20, 20), GHSL_CONF, dtype=np.float32)
        mock_read.return_value = HeightResult(raster, conf, "ghsl", 100.0)
        p = GHSLProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (20, 20))
        np.testing.assert_allclose(result.raster, 8.0)


# ── Open Buildings ───────────────────────────────────────────────

class TestOpenBuildings:
    def test_name(self):
        assert OpenBuildingsProvider().name == "open_buildings"

    def test_covers_cartagena(self):
        assert OpenBuildingsProvider().covers((10.5, 10.3, -75.4, -75.6))

    def test_covers_africa(self):
        assert OpenBuildingsProvider().covers((0.5, -0.5, 37.0, 36.0))

    def test_covers_europe_and_north_america(self):
        """Overture publishes globally, so nothing is out of coverage.

        These two were asserted *not* covered while the module gated on
        Google Open Buildings' footprint, which is not the source it fetches.
        """
        assert OpenBuildingsProvider().covers((41.5, 41.3, 2.3, 2.1))    # Barcelona
        assert OpenBuildingsProvider().covers((40.1, 39.9, -75.0, -75.3))  # Philadelphia

    def test_is_in_coverage_everywhere(self):
        assert _is_in_coverage((35.0, 33.0, 36.0, 34.0))     # Beirut
        assert _is_in_coverage((14.0, 13.0, 101.0, 100.0))   # Bangkok
        assert _is_in_coverage((60.2, 59.8, 11.0, 10.5))     # Oslo
        assert _is_in_coverage((-33.8, -34.0, 151.3, 151.1))  # Sydney

    @patch("city2stl.height.providers.open_buildings.write_height_result")
    @patch("city2stl.height.providers.open_buildings._fetch_buildings_for_bbox",
           return_value=None)  # offline: no Overture STAC/S3 access
    @patch("city2stl.height.providers.open_buildings.read_height_result", return_value=None)
    def test_fetch_returns_nan_placeholder(self, mock_read, mock_fetch, mock_write):
        """Returns sparse mock data with NaN-dominated raster (non-built areas)."""
        p = OpenBuildingsProvider()
        result = p.fetch_heights((10.5, 10.3, -75.4, -75.6), (20, 20))
        # Most pixels are NaN (non-built); a small fraction has heights
        nan_fraction = np.isnan(result.raster).mean()
        assert nan_fraction > 0.5
        assert result.source_name == "open_buildings"
