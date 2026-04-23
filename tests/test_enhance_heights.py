"""Tests for Google 3D height enhancement of OSM buildings."""

import numpy as np
import pytest

from app.server.core.osm import _fill_heights, enhance_buildings_with_raster


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_buildings_geojson(n=10, with_osm_heights=None):
    """Create a synthetic buildings GeoJSON FeatureCollection.

    Args:
        n: Number of buildings.
        with_osm_heights: List of indices that should have OSM tag heights.
            Other buildings get height_source="default".
    """
    if with_osm_heights is None:
        with_osm_heights = []

    features = []
    for i in range(n):
        lon = -75.16 + i * 0.001  # spread along longitude
        lat = 39.95 + (i % 3) * 0.001
        ring = [
            [lon, lat],
            [lon + 0.0005, lat],
            [lon + 0.0005, lat + 0.0005],
            [lon, lat + 0.0005],
            [lon, lat],  # close ring
        ]
        if i in with_osm_heights:
            height_m = 25.0 + i
            source = "osm_tag"
        else:
            height_m = 10.0
            source = "default"
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"height_m": height_m, "height_source": source},
        })
    return {"type": "FeatureCollection", "features": features}


def _make_raster(h=100, w=100, fill=20.0, bbox=(-75.17, 39.94, -75.15, 39.96)):
    """Create a uniform height raster."""
    north, south = bbox[3], bbox[1]
    east, west = bbox[2], bbox[0]
    raster = np.full((h, w), fill, dtype=np.float32)
    return raster, (north, south, east, west)


# ---------------------------------------------------------------------------
# _fill_heights tests
# ---------------------------------------------------------------------------

class TestFillHeights:
    def test_height_source_default(self):
        """Buildings without height tag get height_source='default'."""
        import geopandas as gpd
        from shapely.geometry import box
        gdf = gpd.GeoDataFrame(
            {"geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1)]},
            crs="EPSG:4326",
        )
        result = _fill_heights(gdf, default_m=10.0)
        assert list(result["height_source"]) == ["default", "default"]
        assert list(result["height_m"]) == [10.0, 10.0]

    def test_height_source_osm_tag(self):
        """Buildings with explicit height tag get height_source='osm_tag'."""
        import geopandas as gpd
        from shapely.geometry import box
        gdf = gpd.GeoDataFrame(
            {"geometry": [box(0, 0, 1, 1)], "height": ["25.5 m"]},
            crs="EPSG:4326",
        )
        result = _fill_heights(gdf, default_m=10.0)
        assert result["height_source"].iloc[0] == "osm_tag"
        assert result["height_m"].iloc[0] == 25.5

    def test_height_source_levels(self):
        """Buildings with levels get height_source='osm_levels'."""
        import geopandas as gpd
        from shapely.geometry import box
        gdf = gpd.GeoDataFrame(
            {"geometry": [box(0, 0, 1, 1)], "building:levels": [5]},
            crs="EPSG:4326",
        )
        result = _fill_heights(gdf, default_m=10.0, levels_col="building:levels")
        assert result["height_source"].iloc[0] == "osm_levels"
        assert result["height_m"].iloc[0] == 20.0  # 5 * 4.0

    def test_tag_overrides_levels(self):
        """Explicit height tag takes priority over levels."""
        import geopandas as gpd
        from shapely.geometry import box
        gdf = gpd.GeoDataFrame(
            {
                "geometry": [box(0, 0, 1, 1)],
                "height": ["30"],
                "building:levels": [5],
            },
            crs="EPSG:4326",
        )
        result = _fill_heights(gdf, default_m=10.0, levels_col="building:levels")
        assert result["height_source"].iloc[0] == "osm_tag"
        assert result["height_m"].iloc[0] == 30.0

    def test_mixed_sources(self):
        """Mix of tag, levels, and default."""
        import geopandas as gpd
        from shapely.geometry import box
        gdf = gpd.GeoDataFrame(
            {
                "geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1), box(2, 0, 3, 1)],
                "height": ["15", None, None],
                "building:levels": [None, 8, None],
            },
            crs="EPSG:4326",
        )
        result = _fill_heights(gdf, default_m=10.0, levels_col="building:levels")
        sources = list(result["height_source"])
        assert sources[0] == "osm_tag"
        assert sources[1] == "osm_levels"
        assert sources[2] == "default"


# ---------------------------------------------------------------------------
# enhance_buildings_with_raster tests
# ---------------------------------------------------------------------------

class TestEnhanceBuildingsWithRaster:
    def test_enhances_default_buildings(self):
        """Buildings with height_source='default' get raster height."""
        geojson = _make_buildings_geojson(5, with_osm_heights=[])
        raster, bbox = _make_raster(fill=30.0)
        result = enhance_buildings_with_raster(geojson, raster, bbox)

        stats = result["stats"]
        assert stats["enhanced"] == 5
        assert stats["unchanged"] == 0

        for feat in result["buildings"]["features"]:
            assert feat["properties"]["height_m"] == 30.0
            assert feat["properties"]["height_source"] == "google3d"

    def test_preserves_osm_tagged_buildings(self):
        """Buildings with OSM tag heights are NOT overwritten."""
        geojson = _make_buildings_geojson(5, with_osm_heights=[0, 2, 4])
        raster, bbox = _make_raster(fill=50.0)
        result = enhance_buildings_with_raster(geojson, raster, bbox)

        stats = result["stats"]
        assert stats["enhanced"] == 2   # indices 1, 3
        assert stats["unchanged"] == 3  # indices 0, 2, 4

        # Check OSM-tagged buildings kept their original heights
        feats = result["buildings"]["features"]
        assert feats[0]["properties"]["height_m"] == 25.0  # original
        assert feats[0]["properties"]["height_source"] == "osm_tag"
        assert feats[1]["properties"]["height_m"] == 50.0  # enhanced
        assert feats[1]["properties"]["height_source"] == "google3d"

    def test_nan_raster_not_enhanced(self):
        """NaN raster values should not overwrite defaults."""
        geojson = _make_buildings_geojson(3)
        raster, bbox = _make_raster(fill=np.nan)
        result = enhance_buildings_with_raster(geojson, raster, bbox)

        assert result["stats"]["enhanced"] == 0
        assert result["stats"]["no_data"] == 3

    def test_zero_raster_not_enhanced(self):
        """Zero/negative raster values should not overwrite defaults."""
        geojson = _make_buildings_geojson(3)
        raster, bbox = _make_raster(fill=0.0)
        result = enhance_buildings_with_raster(geojson, raster, bbox)

        assert result["stats"]["enhanced"] == 0
        assert result["stats"]["no_data"] == 3

    def test_confidence_filter(self):
        """Low-confidence pixels should be skipped."""
        geojson = _make_buildings_geojson(3)
        raster, bbox = _make_raster(fill=25.0)
        confidence = np.full_like(raster, 0.1)  # below default threshold 0.3
        result = enhance_buildings_with_raster(
            geojson, raster, bbox, confidence_raster=confidence
        )
        assert result["stats"]["enhanced"] == 0
        assert result["stats"]["no_data"] == 3

    def test_high_confidence_enhanced(self):
        """High-confidence pixels should be accepted."""
        geojson = _make_buildings_geojson(3)
        raster, bbox = _make_raster(fill=25.0)
        confidence = np.full_like(raster, 0.9)
        result = enhance_buildings_with_raster(
            geojson, raster, bbox, confidence_raster=confidence
        )
        assert result["stats"]["enhanced"] == 3

    def test_height_clamped(self):
        """Enhanced heights should be clamped to [3, 300]."""
        geojson = _make_buildings_geojson(2)
        raster, bbox = _make_raster(fill=500.0)  # above 300m
        result = enhance_buildings_with_raster(geojson, raster, bbox)
        for feat in result["buildings"]["features"]:
            assert feat["properties"]["height_m"] == 300.0

    def test_multipolygon_support(self):
        """MultiPolygon buildings should also be enhanced."""
        ring = [
            [-75.16, 39.95],
            [-75.159, 39.95],
            [-75.159, 39.951],
            [-75.16, 39.951],
            [-75.16, 39.95],
        ]
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "MultiPolygon", "coordinates": [[ring]]},
                "properties": {"height_m": 10.0, "height_source": "default"},
            }],
        }
        raster, bbox = _make_raster(fill=35.0)
        result = enhance_buildings_with_raster(geojson, raster, bbox)
        assert result["stats"]["enhanced"] == 1

    def test_empty_geojson(self):
        """Empty FeatureCollection returns zero stats."""
        geojson = {"type": "FeatureCollection", "features": []}
        raster, bbox = _make_raster()
        result = enhance_buildings_with_raster(geojson, raster, bbox)
        assert result["stats"]["total"] == 0
        assert result["stats"]["enhanced"] == 0

    def test_building_outside_bbox(self):
        """Buildings outside the raster bbox should not be enhanced."""
        # Building at lon=10, lat=50 — way outside default bbox
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 50], [10.001, 50], [10.001, 50.001], [10, 50.001], [10, 50]]],
                },
                "properties": {"height_m": 10.0, "height_source": "default"},
            }],
        }
        raster, bbox = _make_raster()
        result = enhance_buildings_with_raster(geojson, raster, bbox)
        assert result["stats"]["enhanced"] == 0
        assert result["stats"]["no_data"] == 1


# ---------------------------------------------------------------------------
# Router tests
# ---------------------------------------------------------------------------

class TestEnhanceHeightsRouter:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.server.server import app
        return TestClient(app)

    def test_google3d_available_endpoint(self, client):
        """GET /api/cities/google3d-available returns available flag."""
        resp = client.get("/api/cities/google3d-available")
        assert resp.status_code == 200
        body = resp.json()
        assert "available" in body
        assert isinstance(body["available"], bool)
