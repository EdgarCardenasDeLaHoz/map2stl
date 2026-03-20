"""
Tests for the OSM cities endpoint.

POST /api/cities     — fetch building/road/waterway data
GET  /api/cities/cached — check local cache status
"""
import json
import hashlib
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# GET /api/cities/cached
# ---------------------------------------------------------------------------

class TestCitiesCached:
    # Small bbox (Philadelphia area, ~2 km diagonal)
    BBOX = {"north": 39.960, "south": 39.950, "east": -75.140, "west": -75.170}

    def _cache_key(self):
        n, s, e, w = self.BBOX["north"], self.BBOX["south"], self.BBOX["east"], self.BBOX["west"]
        return hashlib.md5(f"{n:.4f}_{s:.4f}_{e:.4f}_{w:.4f}".encode()).hexdigest()

    def test_returns_false_when_not_cached(self, client):
        resp = client.get("/api/cities/cached", params=self.BBOX)
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is False
        assert "cache_key" in data

    def test_returns_true_when_cache_file_exists(self, client, tmp_data_dir):
        key = self._cache_key()
        cache_file = tmp_data_dir["osm_cache"] / f"{key}.json"
        cache_file.write_text(json.dumps({"buildings": {"type": "FeatureCollection", "features": []}}))

        resp = client.get("/api/cities/cached", params=self.BBOX)
        assert resp.json()["cached"] is True
        assert resp.json()["cache_key"] == key


# ---------------------------------------------------------------------------
# POST /api/cities — size guard
# ---------------------------------------------------------------------------

class TestCitiesPostSizeGuard:
    def test_rejects_bbox_larger_than_15km(self, client):
        # Huge bbox — will exceed 15 km
        resp = client.post("/api/cities", json={
            "north": 60.0, "south": 50.0, "east": 30.0, "west": 10.0
        })
        assert resp.status_code == 422
        assert "too large" in resp.json()["error"].lower()

    def test_accepts_small_bbox(self, client, tmp_data_dir):
        """A ~2 km bbox should not be rejected by the size guard."""
        # Mock osmnx so we don't need network access
        empty_fc = {"type": "FeatureCollection", "features": []}
        mock_result = {"buildings": empty_fc, "roads": empty_fc, "waterways": empty_fc}

        with patch("strm2stl.ui.location_picker._fetch_osm_data", return_value=mock_result):
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

        with patch("strm2stl.ui.location_picker._fetch_osm_data", return_value=mock_result) as mock_fn:
            client.post("/api/cities", json={**self.SMALL_BBOX, "layers": self.LAYERS})
            assert mock_fn.call_count == 1

            # Second request should be served from cache — _fetch_osm_data NOT called again
            client.post("/api/cities", json={**self.SMALL_BBOX, "layers": self.LAYERS})
            assert mock_fn.call_count == 1  # Still 1

    def test_response_includes_cache_key_and_diagonal(self, client, tmp_data_dir):
        empty_fc = {"type": "FeatureCollection", "features": []}
        mock_result = {"buildings": empty_fc, "roads": empty_fc, "waterways": empty_fc}

        with patch("strm2stl.ui.location_picker._fetch_osm_data", return_value=mock_result):
            resp = client.post("/api/cities", json={**self.SMALL_BBOX, "layers": self.LAYERS})

        body = resp.json()
        assert "cache_key" in body
        assert "diagonal_km" in body
        assert body["diagonal_km"] < 15  # It's a small bbox

    def test_response_structure_has_expected_geojson_layers(self, client, tmp_data_dir):
        empty_fc = {"type": "FeatureCollection", "features": []}
        mock_result = {"buildings": empty_fc, "roads": empty_fc, "waterways": empty_fc}

        with patch("strm2stl.ui.location_picker._fetch_osm_data", return_value=mock_result):
            resp = client.post("/api/cities", json={**self.SMALL_BBOX, "layers": self.LAYERS})

        body = resp.json()
        for layer in self.LAYERS:
            assert layer in body
            assert body[layer]["type"] == "FeatureCollection"

    def test_osmnx_not_installed_returns_500(self, client, tmp_data_dir):
        def raise_import(*args, **kwargs):
            raise RuntimeError("osmnx is not installed")

        with patch("strm2stl.ui.location_picker._fetch_osm_data", side_effect=raise_import):
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
