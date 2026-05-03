"""Tests for the WSF3D building height provider.

Unit tests use synthetic data — no network required.
"""
import io
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

import sys
from pathlib import Path

_STRM2STL_ROOT = Path(__file__).parent.parent.parent
for _p in (str(_STRM2STL_ROOT.parent), str(_STRM2STL_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from city2stl.height.providers.wsf3d import (
    WSF3DProvider,
    tile_name,
    tiles_for_bbox,
    _lon_label,
    _lat_label,
    _download_tile,
    _GAIN,
)


# ── Tile naming ──────────────────────────────────────────────────

class TestTileNaming:
    def test_positive_coords(self):
        # lon_west=3, lat_south=36 → UL=(3,37), LR=(4,36)
        assert tile_name(3, 36) == "e003_n37_e004_n36"

    def test_negative_lon(self):
        # lon_west=-75, lat_south=9 → UL=(-75,10), LR=(-74,9)
        assert tile_name(-75, 9) == "w075_n10_w074_n09"

    def test_negative_lat(self):
        # lon_west=18, lat_south=-15 → UL=(18,-14), LR=(19,-15)
        assert tile_name(18, -15) == "e018_s14_e019_s15"

    def test_zero_crossing(self):
        # lon_west=-1, lat_south=-1 → UL=(-1,0), LR=(0,-1)
        assert tile_name(-1, -1) == "w001_n00_e000_s01"

    def test_lon_labels(self):
        assert _lon_label(0) == "e000"
        assert _lon_label(3) == "e003"
        assert _lon_label(-75) == "w075"
        assert _lon_label(123) == "e123"

    def test_lat_labels(self):
        assert _lat_label(0) == "n00"
        assert _lat_label(37) == "n37"
        assert _lat_label(-5) == "s05"
        assert _lat_label(9) == "n09"


# ── Tile enumeration ────────────────────────────────────────────

class TestTilesForBbox:
    def test_single_tile(self):
        # bbox entirely within one 1° cell
        bbox = (37.2, 37.1, 3.5, 3.2)  # N,S,E,W — Granada
        tiles = tiles_for_bbox(bbox)
        assert len(tiles) == 1
        assert (3, 37) in tiles

    def test_spans_two_tiles_longitude(self):
        bbox = (37.2, 37.1, 4.1, 3.8)  # crosses 4°E
        tiles = tiles_for_bbox(bbox)
        assert (3, 37) in tiles
        assert (4, 37) in tiles
        assert len(tiles) == 2

    def test_spans_four_tiles(self):
        bbox = (37.5, 36.5, 3.5, 2.5)  # 2×2 tiles
        tiles = tiles_for_bbox(bbox)
        assert len(tiles) == 4

    def test_negative_bbox(self):
        # StatenIsland: N=40.67, S=40.48, E=-74.04, W=-74.27
        bbox = (40.67, 40.48, -74.04, -74.27)
        tiles = tiles_for_bbox(bbox)
        assert len(tiles) == 1
        assert (-75, 40) in tiles

    def test_exactly_on_boundary(self):
        bbox = (38.0, 37.0, 4.0, 3.0)  # exact 1° tile
        tiles = tiles_for_bbox(bbox)
        assert (3, 37) in tiles
        assert len(tiles) == 1


# ── Provider interface ──────────────────────────────────────────

class TestWSF3DProvider:
    def test_covers_always_true(self):
        p = WSF3DProvider()
        assert p.covers((40.0, 39.0, -74.0, -75.0))
        assert p.covers((0.0, -1.0, 1.0, 0.0))

    def test_name(self):
        assert WSF3DProvider.name == "wsf3d"

    def test_empty_result_on_404(self, monkeypatch, tmp_path):
        """When all tiles return 404, fetch_heights returns all-NaN."""
        import app.server.core.cache as cache_mod
        monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path / "cache")

        # Mock requests.get to always return 404
        class MockResp:
            status_code = 404
            content = b""
        monkeypatch.setattr("city2stl.height.providers.wsf3d.requests.get",
                            lambda *a, **kw: MockResp())

        p = WSF3DProvider()
        result = p.fetch_heights((37.2, 37.1, -3.5, -3.6), (10, 10))
        assert result.raster.shape == (10, 10)
        assert np.all(np.isnan(result.raster))
        assert np.all(result.confidence == 0.0)

    def test_fetch_with_synthetic_tile(self, monkeypatch, tmp_path):
        """Build a fake GeoTIFF in memory and verify the full pipeline."""
        import app.server.core.cache as cache_mod
        monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path / "cache")

        # Create a synthetic GeoTIFF (Int16, 40×40 pixels, 1° tile)
        tile_h, tile_w = 40, 40
        raw = np.full((tile_h, tile_w), 150, dtype=np.int16)  # 150 * 0.1 = 15.0 m
        raw[0:10, 0:10] = 0  # no-data corner

        buf = io.BytesIO()
        transform = from_bounds(-4.0, 37.0, -3.0, 38.0, tile_w, tile_h)
        with rasterio.open(
            buf, "w", driver="GTiff", height=tile_h, width=tile_w,
            count=1, dtype="int16", crs="EPSG:4326", transform=transform,
        ) as dst:
            dst.write(raw, 1)
        tif_bytes = buf.getvalue()

        class MockResp:
            status_code = 200
            content = tif_bytes
            ok = True
            def raise_for_status(self):
                pass

        monkeypatch.setattr("city2stl.height.providers.wsf3d.requests.get",
                            lambda *a, **kw: MockResp())

        p = WSF3DProvider()
        bbox = (37.9, 37.1, -3.1, -3.9)  # within the tile
        result = p.fetch_heights(bbox, (20, 20))

        assert result.raster.shape == (20, 20)
        assert result.source_name == "wsf3d"
        assert result.resolution_m == 90.0
        # Most pixels should be ~15.0 m
        valid = result.raster[~np.isnan(result.raster)]
        assert len(valid) > 0
        assert np.nanmedian(valid) == pytest.approx(15.0, abs=1.0)
        # Confidence is 0.5 where data exists, 0 where NaN
        assert np.all(result.confidence[~np.isnan(result.raster)] == 0.5)

    def test_cache_hit_skips_download(self, monkeypatch, tmp_path):
        """Second call to same tile reads from cache, no HTTP."""
        import app.server.core.cache as cache_mod
        monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path / "cache")

        tile_h, tile_w = 10, 10
        raw = np.full((tile_h, tile_w), 200, dtype=np.int16)

        buf = io.BytesIO()
        transform = from_bounds(-4.0, 37.0, -3.0, 38.0, tile_w, tile_h)
        with rasterio.open(
            buf, "w", driver="GTiff", height=tile_h, width=tile_w,
            count=1, dtype="int16", crs="EPSG:4326", transform=transform,
        ) as dst:
            dst.write(raw, 1)
        tif_bytes = buf.getvalue()

        call_count = 0

        class MockResp:
            status_code = 200
            content = tif_bytes
            ok = True
            def raise_for_status(self):
                pass

        def mock_get(*a, **kw):
            nonlocal call_count
            call_count += 1
            return MockResp()

        monkeypatch.setattr("city2stl.height.providers.wsf3d.requests.get",
                            mock_get)

        p = WSF3DProvider()
        bbox = (37.5, 37.1, -3.2, -3.8)

        p.fetch_heights(bbox, (5, 5))
        assert call_count == 1

        p.fetch_heights(bbox, (5, 5))
        assert call_count == 1  # cache hit — no second download
