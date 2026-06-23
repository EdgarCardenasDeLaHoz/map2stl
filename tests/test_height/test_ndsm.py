"""Unit tests for the nDSM provider — no network calls."""

import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from city2stl.skyline.height import HeightResult
from city2stl.skyline.height.providers.ndsm import (
    NDSMProvider,
    _tile_name_glo30,
    _tile_url_glo30,
    _tile_url_fabdem,
    _tiles_for_bbox,
    _crop_to_bbox,
    _stitch_tiles,
    NDSM_CONFIDENCE,
)


# ── Tile naming ──────────────────────────────────────────────────

class TestTileNaming:
    def test_glo30_name_positive(self):
        assert _tile_name_glo30(41, 2) == "Copernicus_DSM_COG_10_N41_00_E002_00_DEM"

    def test_glo30_name_negative(self):
        assert _tile_name_glo30(-10, -75) == "Copernicus_DSM_COG_10_S10_00_W075_00_DEM"

    def test_glo30_url(self):
        url = _tile_url_glo30(41, 2)
        assert "copernicus-dem-30m.s3" in url
        assert url.endswith(".tif")

    def test_fabdem_url(self):
        url = _tile_url_fabdem(41, 2)
        assert "FABDEM" in url
        assert url.endswith(".tif")


# ── Tile selection ───────────────────────────────────────────────

class TestTilesForBbox:
    def test_single_tile(self):
        # Barcelona city center — fits in one tile
        bbox = (41.5, 41.3, 2.3, 2.1)
        tiles = _tiles_for_bbox(bbox)
        assert tiles == [(41, 2)]

    def test_multi_tile(self):
        # Spans 3 lat × 3 lon tiles (floor(40.9)=40..floor(42.1)=42, floor(1.9)=1..floor(3.1)=3)
        bbox = (42.1, 40.9, 3.1, 1.9)
        tiles = _tiles_for_bbox(bbox)
        assert len(tiles) == 9
        assert (40, 1) in tiles
        assert (41, 2) in tiles
        assert (42, 3) in tiles

    def test_negative_coords(self):
        # Cartagena, Colombia
        bbox = (10.5, 10.3, -75.4, -75.6)
        tiles = _tiles_for_bbox(bbox)
        assert (10, -76) in tiles

    def test_equator_crossing(self):
        bbox = (0.5, -0.5, 37.0, 36.0)
        tiles = _tiles_for_bbox(bbox)
        assert (-1, 36) in tiles
        assert (0, 36) in tiles


# ── Crop / stitch ────────────────────────────────────────────────

class TestCropStitch:
    def test_crop_full_tile(self):
        """Bbox covers entire tile → no cropping."""
        arr = np.ones((3600, 3600), dtype=np.float32) * 50
        cropped = _crop_to_bbox(arr, 41, 2, bbox=(42.0, 41.0, 3.0, 2.0))
        assert cropped.shape == (3600, 3600)

    def test_crop_partial_tile(self):
        """Bbox covers ~half the tile vertically."""
        arr = np.ones((100, 100), dtype=np.float32) * 10
        cropped = _crop_to_bbox(arr, 41, 2, bbox=(41.5, 41.0, 3.0, 2.0))
        assert cropped.shape[0] == 50  # half the rows
        assert cropped.shape[1] == 100  # full width

    def test_stitch_single_tile(self):
        arr = np.full((10, 10), 25.0, dtype=np.float32)
        stitched = _stitch_tiles(
            {(41, 2): arr},
            bbox=(42.0, 41.0, 3.0, 2.0)
        )
        assert stitched.shape == (10, 10)
        np.testing.assert_allclose(stitched, 25.0)

    def test_stitch_empty_returns_empty(self):
        stitched = _stitch_tiles({}, bbox=(42.0, 41.0, 3.0, 2.0))
        assert stitched.size == 0


# ── Provider covers ──────────────────────────────────────────────

class TestNDSMCovers:
    def test_covers_barcelona(self):
        p = NDSMProvider()
        assert p.covers((41.5, 41.3, 2.3, 2.1))

    def test_covers_cartagena(self):
        p = NDSMProvider()
        assert p.covers((10.5, 10.3, -75.4, -75.6))

    def test_not_covers_arctic(self):
        p = NDSMProvider()
        assert not p.covers((85.0, 82.0, 10.0, 5.0))


# ── Provider fetch (mocked tiles) ────────────────────────────────

class TestNDSMFetch:
    def _mock_get_tile(self, dsm_val: float, dtm_val: float):
        """Return a mock _get_tile that returns constant-value arrays."""
        def fake(source, lat, lon):
            if source == "glo30":
                return np.full((100, 100), dsm_val, dtype=np.float32)
            elif source == "fabdem":
                return np.full((100, 100), dtm_val, dtype=np.float32)
            return None
        return fake

    @patch("city2stl.skyline.height.providers.ndsm.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.ndsm.write_height_result")
    @patch("city2stl.skyline.height.providers.ndsm._fetch_srtm_opentopo", return_value=None)
    @patch("city2stl.skyline.height.providers.ndsm._get_tile")
    def test_basic_subtraction(self, mock_tile, mock_srtm, mock_write, mock_read):
        """DSM=150m, DTM=130m → nDSM=20m building height (SRTM off → FABDEM tiles)."""
        mock_tile.side_effect = self._mock_get_tile(150.0, 130.0)
        p = NDSMProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (50, 50))

        assert isinstance(result, HeightResult)
        assert result.raster.shape == (50, 50)
        assert result.source_name == "ndsm"
        np.testing.assert_allclose(result.raster, 20.0, atol=0.5)
        np.testing.assert_allclose(result.confidence, NDSM_CONFIDENCE)

    @patch("city2stl.skyline.height.providers.ndsm.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.ndsm.write_height_result")
    @patch("city2stl.skyline.height.providers.ndsm._fetch_srtm_opentopo", return_value=None)
    @patch("city2stl.skyline.height.providers.ndsm._get_tile")
    def test_negative_clamped_to_zero(self, mock_tile, mock_srtm, mock_write, mock_read):
        """DTM > DSM (artefact) → clamped to 0, not negative."""
        mock_tile.side_effect = self._mock_get_tile(100.0, 105.0)
        p = NDSMProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (20, 20))
        assert np.all(result.raster >= 0)

    @patch("city2stl.skyline.height.providers.ndsm.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.ndsm.write_height_result")
    @patch("city2stl.skyline.height.providers.ndsm._get_tile")
    def test_no_dsm_returns_nan(self, mock_tile, mock_write, mock_read):
        """No DSM tiles available → all NaN."""
        mock_tile.return_value = None
        p = NDSMProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("city2stl.skyline.height.providers.ndsm.read_height_result", return_value=None)
    @patch("city2stl.skyline.height.providers.ndsm.write_height_result")
    @patch("city2stl.skyline.height.providers.ndsm._fetch_srtm_opentopo", return_value=None)
    @patch("city2stl.skyline.height.providers.ndsm._get_tile")
    def test_no_fabdem_returns_nan(self, mock_tile, mock_srtm, mock_write, mock_read):
        """DSM available but no SRTM/FABDEM → nDSM is NaN (can't subtract)."""
        def fake(source, lat, lon):
            if source == "glo30":
                return np.full((100, 100), 150.0, dtype=np.float32)
            return None
        mock_tile.side_effect = fake
        p = NDSMProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (20, 20))
        assert np.all(np.isnan(result.raster))

    @patch("city2stl.skyline.height.providers.ndsm.read_height_result")
    def test_cache_hit(self, mock_read):
        """Cached result is returned without downloading."""
        raster = np.full((30, 30), 15.0, dtype=np.float32)
        conf = np.full((30, 30), NDSM_CONFIDENCE, dtype=np.float32)
        mock_read.return_value = HeightResult(raster, conf, "ndsm", 30.0)
        p = NDSMProvider()
        result = p.fetch_heights((41.5, 41.3, 2.3, 2.1), (30, 30))
        assert result.raster.shape == (30, 30)
        np.testing.assert_allclose(result.raster, 15.0)
