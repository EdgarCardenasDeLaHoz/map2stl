"""Tests for the WSF3D building height provider.

Unit tests use synthetic data — no network required.
"""
import io

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from city2stl.height.providers import wsf3d_global  # noqa: E402
from city2stl.height.providers.wsf3d import (  # noqa: E402
    WSF3DProvider,
    _lat_label,
    _lon_label,
    tile_name,
    tiles_for_bbox,
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

    def test_empty_result_when_no_tile_and_no_mosaic(self, monkeypatch, tmp_path, tmp_cache_root):
        """All tiles 404 and the mosaic decoders are absent: all-NaN, no network."""

        class MockResp:
            status_code = 404
            content = b""
        monkeypatch.setattr("city2stl.height.providers.wsf3d.requests.get",
                            lambda *a, **kw: MockResp())
        monkeypatch.setattr(wsf3d_global, "available", lambda: False)

        p = WSF3DProvider()
        result = p.fetch_heights((37.2, 37.1, -3.5, -3.6), (10, 10))
        assert result.raster.shape == (10, 10)
        assert np.all(np.isnan(result.raster))
        assert np.all(result.confidence == 0.0)


class TestGlobalMosaicFallback:
    """A 404 from the tile endpoint means "not published", not "no settlements".

    Only 453 one-degree tiles exist, so most of the populated world 404s and has to come from the
    global mosaic instead. These tests drive that path with the mosaic reader stubbed out; the
    reader itself is exercised against the live host, not here.
    """

    @staticmethod
    def _no_tiles(monkeypatch, tmp_path):
        import geo2stl.cache as cache_mod
        monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path / "cache")

        class MockResp:
            status_code = 404
            content = b""
        monkeypatch.setattr("city2stl.height.providers.wsf3d.requests.get",
                            lambda *a, **kw: MockResp())
        monkeypatch.setattr(wsf3d_global, "available", lambda: True)

    def test_served_from_mosaic(self, monkeypatch, tmp_path):
        self._no_tiles(monkeypatch, tmp_path)
        grid = np.full((10, 10), 21.0, np.float32)
        grid[0, 0] = np.nan
        monkeypatch.setattr(wsf3d_global, "read_grid",
                            lambda bbox, shape: (grid.copy(), 86.5822))

        result = WSF3DProvider().fetch_heights((50.12, 50.09, 8.69, 8.65), (10, 10))
        assert result.source_name == "wsf3d"
        assert result.resolution_m == pytest.approx(86.5822)
        assert np.nanmedian(result.raster) == pytest.approx(21.0)
        assert result.confidence[0, 0] == 0.0
        assert result.confidence[5, 5] == pytest.approx(0.5)

    def test_mosaic_result_is_cached(self, monkeypatch, tmp_path):
        self._no_tiles(monkeypatch, tmp_path)
        calls = {"n": 0}

        def counted(bbox, shape):
            calls["n"] += 1
            return np.full(shape, 12.0, np.float32), 86.5822

        monkeypatch.setattr(wsf3d_global, "read_grid", counted)

        p, bbox = WSF3DProvider(), (50.12, 50.09, 8.69, 8.65)
        first = p.fetch_heights(bbox, (8, 8))
        second = p.fetch_heights(bbox, (8, 8))
        assert calls["n"] == 1
        assert np.allclose(first.raster, second.raster)
        assert second.resolution_m == pytest.approx(86.5822)

    def test_mosaic_failure_degrades_to_empty(self, monkeypatch, tmp_path):
        """A coarse fallback that raises would take the whole height merge down with it."""
        self._no_tiles(monkeypatch, tmp_path)

        def boom(bbox, shape):
            raise OSError("range request failed")

        monkeypatch.setattr(wsf3d_global, "read_grid", boom)

        result = WSF3DProvider().fetch_heights((50.12, 50.09, 8.69, 8.65), (6, 6))
        assert np.all(np.isnan(result.raster))
        assert np.all(result.confidence == 0.0)

    def test_all_nan_mosaic_is_empty_not_cached(self, monkeypatch, tmp_path):
        """Genuinely unsettled ground reads as all-zero in WSF3D, which is NaN here."""
        self._no_tiles(monkeypatch, tmp_path)
        monkeypatch.setattr(wsf3d_global, "read_grid",
                            lambda bbox, shape: (np.full(shape, np.nan, np.float32), 86.5822))

        result = WSF3DProvider().fetch_heights((-20.0, -20.05, -140.0, -140.05), (6, 6))
        assert np.all(np.isnan(result.raster))
        assert result.resolution_m == 90.0


class TestWSF3DProviderTiles:
    def test_fetch_with_synthetic_tile(self, monkeypatch, tmp_path, tmp_cache_root):
        """Build a fake GeoTIFF in memory and verify the full pipeline."""

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

    def test_cache_hit_skips_download(self, monkeypatch, tmp_path, tmp_cache_root):
        """Second call to same tile reads from cache, no HTTP."""

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
