"""
Tests for city2stl.skyline.height.providers.lidar_3dep_copc -- per-footprint
building heights from USGS 3DEP point clouds.
"""
import numpy as np
import pytest
from shapely.geometry import box

from city2stl.skyline.height.providers import lidar_3dep_copc as L

# Breckenridge, CO: 3DEP CO_Central_Western_2016 covers it.
BRECK = (39.51, 39.43, -106.03, -106.125)


def _item(item_id, lonlat_bbox):
    return {"id": item_id, "bbox": list(lonlat_bbox),
            "assets": {"data": {"href": f"https://acct.blob.core.windows.net/cont/{item_id}.copc.laz"}}}


class TestFootprintHeights:
    @pytest.fixture
    def fake_tiles(self, monkeypatch):
        """Two tiles; building 0 straddles both, building 1 sits in the second only."""
        items = [_item("a", (0.0, 0.0, 1.0, 1.0)), _item("b", (1.0, 0.0, 2.0, 1.0))]
        per_tile = {
            "a": {0: (np.array([110.0, 111.0]), np.array([100.0, 100.0]))},
            "b": {0: (np.array([112.0, 112.0, 113.0]), np.array([100.0])),
                  1: (np.array([101.0] * 5), np.array([100.0] * 5))},
        }
        read = []

        def fake_read(item, polys, tokens):
            read.append((item["id"], sorted(polys)))
            return per_tile[item["id"]], 10

        monkeypatch.setattr(L, "_search_items", lambda bbox: items)
        monkeypatch.setattr(L, "_read_tile", fake_read)
        monkeypatch.setattr(L, "_signed", lambda href, tokens: href)
        return read

    def test_returns_merge_across_tiles(self, fake_tiles):
        polys = {0: box(0.9, 0.4, 1.1, 0.6), 1: box(1.4, 0.4, 1.6, 0.6)}
        heights, stats = L.footprint_heights(polys, (1, 0, 2, 0))

        assert sorted(fake_tiles) == [("a", [0]), ("b", [0, 1])]
        roof = np.percentile([110, 111, 112, 112, 113], L._ROOF_PERCENTILE)
        assert heights[0] == pytest.approx(roof - 100, abs=0.05)
        assert 1 not in heights, "a 1 m reading is a building the survey predates, not a shed"
        assert stats["tiles"] == 2 and stats["below_min_height"] == 1

    def test_no_coverage_reports_zero_tiles(self, monkeypatch):
        monkeypatch.setattr(L, "_search_items", lambda bbox: [])
        heights, stats = L.footprint_heights({0: box(0, 0, 1, 1)}, (1, 0, 1, 0))
        assert heights == {} and stats["tiles"] == 0


def test_covers_is_us_only():
    assert L.covers(BRECK)
    assert not L.covers((10.4295, 10.3845, -75.5221, -75.5679))


@pytest.mark.integration
def test_live_breckenridge_houses():
    """A few blocks of Breckenridge measured from the live point cloud."""
    pytest.importorskip("laspy")
    if not L.available():
        pytest.skip("no LAZ backend")
    import osmnx as ox
    from shapely.geometry import shape  # noqa: F401 -- osmnx returns shapely already

    n, s, e, w = 39.4845, 39.4805, -106.043, -106.048
    gdf = ox.features_from_bbox((w, s, e, n), tags={"building": True})
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    polys = dict(enumerate(gdf.geometry))
    heights, stats = L.footprint_heights(polys, (n, s, e, w))

    assert stats["tiles"] >= 1
    assert len(heights) >= 0.8 * len(polys)
    assert 4.0 < float(np.median(list(heights.values()))) < 20.0
