"""Unit tests for the USGS 3DEP LiDAR provider — no network."""

from unittest.mock import patch

import numpy as np
import pytest

from city2stl.height import HeightResult
from city2stl.height.providers.lidar_3dep import (
    _CONFIDENCE,
    LiDAR3DEPProvider,
    _is_in_us,
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

    def test_hawaii_honolulu_and_niihau(self):
        assert _is_in_us((21.295, 21.264, -157.815, -157.848))    # Waikiki benchmark bbox
        assert _is_in_us((22.03, 21.77, -160.05, -160.25))        # Niihau, west of -160

    def test_alaska_west_of_170(self):
        # St. Lawrence Island (AK_NativeAKVillages_7_D22) was outside the old box
        assert _is_in_us((63.70, 63.63, -170.39, -170.61))

    def test_puerto_rico_san_juan(self):
        # The San Juan benchmark bbox: USGS_LPC_PR_PRVI_E_2018 covers it, but the EPT
        # reader's coverage pre-check returned False and every read came back None.
        assert _is_in_us((18.472, 18.432, -65.996, -66.125))

    def test_puerto_rico_mona_and_culebra(self):
        assert _is_in_us((18.12, 18.05, -67.85, -67.95))          # Mona
        assert _is_in_us((18.34, 18.28, -65.22, -65.34))          # Culebra

    def test_us_virgin_islands(self):
        assert _is_in_us((18.37, 18.30, -64.85, -65.05))          # St. Thomas
        assert _is_in_us((17.79, 17.67, -64.56, -64.90))          # St. Croix

    def test_guam_and_northern_marianas(self):
        assert _is_in_us((13.66, 13.22, 144.97, 144.60))          # Guam_2012
        assert _is_in_us((15.30, 14.10, 145.84, 145.11))          # PI_CNMI_*_2019

    def test_caribbean_neighbours_not_us(self):
        assert not _is_in_us((18.50, 18.45, -69.85, -69.95))      # Santo Domingo
        assert not _is_in_us((23.15, 23.10, -82.30, -82.40))      # Havana
        assert not _is_in_us((13.10, 13.05, -59.58, -59.65))      # Bridgetown

    @pytest.mark.requires_cache
    def test_every_ept_project_is_us(self):
        """Each project in the local USGS EPT boundary index meets a US box. The one
        exception is an index entry whose geometry sits at 0 N 85 E (``UT_Ogden-FEMA_2011``)."""
        import json
        import os
        from pathlib import Path

        from shapely.geometry import shape

        root = Path(os.environ.get("MAP2STL_CACHE") or Path(__file__).resolve().parents[2] / "cache")
        path = root / "usgs_ept" / "resources.geojson"
        if not path.exists():
            pytest.skip("USGS EPT boundary index not cached")
        out = []
        for feat in json.loads(path.read_text(encoding="utf-8"))["features"]:
            w, s, e, n = shape(feat["geometry"]).bounds
            if not _is_in_us((n, s, e, w)):
                out.append(feat["properties"]["name"])
        assert out in ([], ["UT_Ogden-FEMA_2011"])

    def test_ept_reader_covers_san_juan(self):
        from city2stl.height.providers import lidar_3dep_copc, lidar_3dep_ept_laspy

        san_juan = (18.472, 18.432, -65.996, -66.125)
        assert lidar_3dep_ept_laspy.covers(san_juan)
        assert lidar_3dep_copc.covers(san_juan)


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
    @patch("city2stl.height.providers.lidar_3dep.read_height_result", return_value=None)
    @patch("city2stl.height.providers.lidar_3dep.write_height_result")
    @patch("city2stl.height.providers.lidar_3dep._get_api_key", return_value="key")
    @patch("city2stl.height.providers.lidar_3dep._fetch_opentopo_dem")
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

    @patch("city2stl.height.providers.lidar_3dep.read_height_result", return_value=None)
    @patch("city2stl.height.providers.lidar_3dep.write_height_result")
    @patch("city2stl.height.providers.lidar_3dep._get_api_key", return_value="key")
    @patch("city2stl.height.providers.lidar_3dep._fetch_opentopo_dem")
    def test_negative_clamped(self, mock_dem, mock_key, mock_write, mock_read):
        """DTM > DSM artefact → clamp to 0."""
        def fake_dem(demtype, bbox, api_key, label):
            return np.full((20, 20), 30.0 if demtype == "COP30" else 35.0, dtype=np.float32)
        mock_dem.side_effect = fake_dem

        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (20, 20))
        assert np.all(result.raster >= 0)

    @patch("city2stl.height.providers.lidar_3dep.read_height_result", return_value=None)
    @patch("city2stl.height.providers.lidar_3dep.write_height_result")
    @patch("city2stl.height.providers.lidar_3dep._get_api_key", return_value="key")
    @patch("city2stl.height.providers.lidar_3dep._fetch_opentopo_dem")
    def test_no_dsm_returns_nan(self, mock_dem, mock_key, mock_write, mock_read):
        """No DSM available → all NaN."""
        mock_dem.return_value = None
        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("city2stl.height.providers.lidar_3dep.read_height_result", return_value=None)
    @patch("city2stl.height.providers.lidar_3dep.write_height_result")
    @patch("city2stl.height.providers.lidar_3dep._get_api_key", return_value="key")
    @patch("city2stl.height.providers.lidar_3dep._fetch_opentopo_dem")
    def test_no_dtm_returns_nan(self, mock_dem, mock_key, mock_write, mock_read):
        """DSM available but no DTM → can't compute nDSM → NaN."""
        def fake_dem(demtype, bbox, api_key, label):
            return np.full((20, 20), 50.0, dtype=np.float32) if demtype == "COP30" else None
        mock_dem.side_effect = fake_dem

        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("city2stl.height.providers.lidar_3dep.read_height_result")
    def test_cache_hit(self, mock_read):
        """Cached result returned without fetching."""
        raster = np.full((30, 30), 12.0, dtype=np.float32)
        conf = np.full((30, 30), _CONFIDENCE, dtype=np.float32)
        mock_read.return_value = HeightResult(raster, conf, "lidar_3dep", 1.0)
        p = LiDAR3DEPProvider()
        result = p.fetch_heights((40.0, 39.9, -75.0, -75.1), (30, 30))
        np.testing.assert_allclose(result.raster, 12.0)
