"""
Tests for the OSM cities endpoint.

POST /api/cities        — fetch building/road/waterway data
GET  /api/cities/cached — check local cache status
POST /api/cities/raster — rasterize OSM features to a DEM-format height map
"""
import gzip
import json
import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _osm_key(bbox, tol=2.0, min_area=20.0):
    """Return the expected cache key for the given bbox and defaults."""
    from app.server.core.cache import osm_cache_key
    return osm_cache_key(
        bbox["north"], bbox["south"], bbox["east"], bbox["west"], tol, min_area
    )


# ---------------------------------------------------------------------------
# GET /api/cities/cached
# ---------------------------------------------------------------------------

class TestCitiesCached:
    # Small bbox (Philadelphia area, ~2 km diagonal)
    BBOX = {"north": 39.960, "south": 39.950, "east": -75.140, "west": -75.170}

    def test_returns_false_when_not_cached(self, client):
        resp = client.get("/api/cities/cached", params=self.BBOX)
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert "cache_key" in data

    def test_returns_true_when_cache_file_exists(self, client, tmp_data_dir):
        key = _osm_key(self.BBOX)
        # The new cache stores .json.gz under CACHE_ROOT/osm/
        osm_dir = tmp_data_dir["cache_root"] / "osm"
        osm_dir.mkdir(parents=True, exist_ok=True)
        cache_file = osm_dir / f"{key}.json.gz"
        cache_file.write_bytes(
            gzip.compress(json.dumps({"buildings": {"type": "FeatureCollection", "features": []}}).encode())
        )

        resp = client.get("/api/cities/cached", params=self.BBOX)
        assert resp.json()["cached"] is True
        assert resp.json()["cache_key"] == key


# ---------------------------------------------------------------------------
# POST /api/cities — size guard
# ---------------------------------------------------------------------------

class TestCitiesPostSizeGuard:
    def test_rejects_bbox_larger_than_15km(self, client):
        resp = client.post("/api/cities", json={
            "north": 60.0, "south": 50.0, "east": 30.0, "west": 10.0
        })
        assert resp.status_code == 422
        assert "too large" in resp.json()["error"].lower()

    def test_accepts_small_bbox(self, client, tmp_data_dir):
        """A ~2 km bbox should not be rejected by the size guard."""
        empty_fc = {"type": "FeatureCollection", "features": []}
        mock_result = {"buildings": empty_fc, "roads": empty_fc, "waterways": empty_fc}

        with patch("app.server.routers.cities._fetch_osm_data", return_value=mock_result):
            resp = client.post("/api/cities", json={
                "north": 39.960, "south": 39.950, "east": -75.140, "west": -75.170,
                "layers": ["buildings", "roads", "waterways"]
            })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/cities — caching behaviour
# ---------------------------------------------------------------------------

class TestCitiesPostCaching:
    SMALL_BBOX = {"north": 39.960, "south": 39.950, "east": -75.140, "west": -75.170}
    LAYERS = ["buildings", "roads", "waterways"]

    def test_result_is_cached_after_first_request(self, client, tmp_data_dir):
        empty_fc = {"type": "FeatureCollection", "features": []}
        mock_result = {"buildings": empty_fc, "roads": empty_fc, "waterways": empty_fc}

        with patch("app.server.routers.cities._fetch_osm_data", return_value=mock_result) as mock_fn:
            client.post("/api/cities", json={**self.SMALL_BBOX, "layers": self.LAYERS})
            assert mock_fn.call_count == 1

            # Second request should be served from cache — _fetch_osm_data NOT called again
            client.post("/api/cities", json={**self.SMALL_BBOX, "layers": self.LAYERS})
            assert mock_fn.call_count == 1  # Still 1

    def test_response_includes_cache_key_and_diagonal(self, client, tmp_data_dir):
        empty_fc = {"type": "FeatureCollection", "features": []}
        mock_result = {"buildings": empty_fc, "roads": empty_fc, "waterways": empty_fc}

        with patch("app.server.routers.cities._fetch_osm_data", return_value=mock_result):
            resp = client.post("/api/cities", json={**self.SMALL_BBOX, "layers": self.LAYERS})

        body = resp.json()
        assert "cache_key" in body
        assert "diagonal_km" in body
        assert body["diagonal_km"] < 15

    def test_response_structure_has_expected_geojson_layers(self, client, tmp_data_dir):
        empty_fc = {"type": "FeatureCollection", "features": []}
        mock_result = {"buildings": empty_fc, "roads": empty_fc, "waterways": empty_fc}

        with patch("app.server.routers.cities._fetch_osm_data", return_value=mock_result):
            resp = client.post("/api/cities", json={**self.SMALL_BBOX, "layers": self.LAYERS})

        body = resp.json()
        for layer in self.LAYERS:
            assert layer in body
            assert body[layer]["type"] == "FeatureCollection"

    def test_osmnx_not_installed_returns_500(self, client, tmp_data_dir):
        def raise_runtime(*args, **kwargs):
            raise RuntimeError("osmnx is not installed")

        with patch("app.server.routers.cities._fetch_osm_data", side_effect=raise_runtime):
            resp = client.post("/api/cities", json={**self.SMALL_BBOX, "layers": self.LAYERS})
        assert resp.status_code == 500
        assert "OSM fetch failed" in resp.json()["error"]


# ---------------------------------------------------------------------------
# POST /api/export/preview
# ---------------------------------------------------------------------------

class TestExportPreview:
    """Preview endpoint should delegate to DEM and return elevation values."""

    def test_returns_dem_values_in_test_mode(self, client):
        resp = client.post("/api/export/preview", params={
            "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2,
            "dim": 10
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "dem_values" in body
        assert "dimensions" in body
        assert len(body["dem_values"]) == 100  # 10×10 grid in TEST_MODE

    def test_response_has_bbox_field(self, client):
        resp = client.post("/api/export/preview", params={
            "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2,
            "dim": 5
        })
        body = resp.json()
        assert "bbox" in body
        assert len(body["bbox"]) == 4


# ---------------------------------------------------------------------------
# POST /api/cities/raster
# ---------------------------------------------------------------------------

EMPTY_FC = {"type": "FeatureCollection", "features": []}
SMALL_BBOX = {"north": 39.960, "south": 39.950, "east": -75.140, "west": -75.170}


class TestCityRaster:
    """POST /api/cities/raster — rasterize OSM features to a DEM-format height map."""

    def _payload(self, dim=10, buildings=None, roads=None, waterways=None):
        return {
            **SMALL_BBOX,
            "dim": dim,
            "buildings":  buildings  or EMPTY_FC,
            "roads":      roads      or EMPTY_FC,
            "waterways":  waterways  or EMPTY_FC,
        }

    def test_returns_200_with_empty_features(self, client, tmp_data_dir):
        resp = client.post("/api/cities/raster", json=self._payload())
        assert resp.status_code == 200

    def test_response_has_dem_format_fields(self, client, tmp_data_dir):
        resp = client.post("/api/cities/raster", json=self._payload())
        body = resp.json()
        for key in ("values", "width", "height", "vmin", "vmax", "bbox"):
            assert key in body, f"Missing field: {key}"

    def test_dimensions_match_dim_param(self, client, tmp_data_dir):
        dim = 12
        resp = client.post("/api/cities/raster", json=self._payload(dim=dim))
        body = resp.json()
        assert body["width"] == dim
        assert body["height"] == dim
        assert len(body["values"]) == dim * dim

    def test_empty_features_produce_zero_values(self, client, tmp_data_dir):
        resp = client.post("/api/cities/raster", json=self._payload())
        body = resp.json()
        assert all(v == 0.0 for v in body["values"])

    def test_building_feature_raises_nonzero_values(self, client, tmp_data_dir):
        """A building covering the full bbox should produce nonzero height values."""
        building_fc = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-75.170, 39.950], [-75.140, 39.950],
                        [-75.140, 39.960], [-75.170, 39.960],
                        [-75.170, 39.950],
                    ]]
                },
                "properties": {"height_m": 10.0}
            }]
        }
        resp = client.post("/api/cities/raster", json=self._payload(dim=10, buildings=building_fc))
        body = resp.json()
        assert any(v > 0 for v in body["values"])

    def test_cache_hit_returns_same_values(self, client, tmp_data_dir):
        """Two identical requests should return identical results (cache hit on second)."""
        payload = self._payload(dim=10)
        r1 = client.post("/api/cities/raster", json=payload).json()
        r2 = client.post("/api/cities/raster", json=payload).json()
        assert r1["values"] == r2["values"]
