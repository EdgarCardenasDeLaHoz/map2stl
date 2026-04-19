"""
Tests for /api/terrain/* endpoints (terrain.py).

All tests run with STRM2STL_TEST_MODE=1 (set in conftest) so the DEM
endpoint returns a fast deterministic gradient with no network calls.
"""

import pytest


_BBOX = {"north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2}
_BBOX_QS = "north=40.0&south=39.9&east=-75.1&west=-75.2"


# ---------------------------------------------------------------------------
# GET /api/terrain/dem — happy path
# ---------------------------------------------------------------------------

class TestTerrainDem:
    def test_returns_200(self, client):
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=10")
        assert r.status_code == 200

    def test_response_has_dem_values(self, client):
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=10")
        data = r.json()
        assert "dem_values_b64" in data
        assert isinstance(data["dem_values_b64"], str)
        assert len(data["dem_values_b64"]) > 0

    def test_dimensions_match_dim_param(self, client):
        import base64
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=10")
        data = r.json()
        h, w = data["dimensions"]
        n_floats = len(base64.b64decode(data["dem_values_b64"])) // 4
        assert h * w == n_floats

    def test_response_has_bbox(self, client):
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=10")
        data = r.json()
        assert "bbox" in data
        assert len(data["bbox"]) == 4

    def test_response_has_elevation_stats(self, client):
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=10")
        data = r.json()
        assert "min_elevation" in data
        assert "max_elevation" in data
        assert data["min_elevation"] <= data["max_elevation"]

    def test_test_mode_values_are_deterministic(self, client):
        """Same request twice returns identical values."""
        r1 = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=5")
        r2 = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=5")
        assert r1.json()["dem_values_b64"] == r2.json()["dem_values_b64"]

    def test_post_request_also_works(self, client):
        r = client.post("/api/terrain/dem", params={**_BBOX, "dim": 10})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestTerrainDemValidation:
    def test_missing_bbox_returns_400(self, client):
        r = client.get("/api/terrain/dem?dim=10")
        assert r.status_code == 400

    def test_north_less_than_south_returns_400(self, client):
        r = client.get("/api/terrain/dem?north=39.0&south=40.0&east=-75.1&west=-75.2&dim=10")
        assert r.status_code == 400
        assert "north" in r.json()["error"].lower() or "south" in r.json()["error"].lower()

    def test_east_less_than_west_returns_400(self, client):
        r = client.get("/api/terrain/dem?north=40.0&south=39.9&east=-76.0&west=-75.0&dim=10")
        assert r.status_code == 400

    def test_dim_too_large_returns_400(self, client):
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=9999")
        assert r.status_code == 400

    def test_dim_zero_returns_400(self, client):
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=0")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/terrain/dem/raw
# ---------------------------------------------------------------------------

class TestTerrainDemRaw:
    def test_returns_200(self, client):
        r = client.get(f"/api/terrain/dem/raw?{_BBOX_QS}&dim=10")
        assert r.status_code == 200

    def test_response_structure(self, client):
        r = client.get(f"/api/terrain/dem/raw?{_BBOX_QS}&dim=10")
        data = r.json()
        assert "dem_values_b64" in data
        assert "dimensions" in data
        assert "bbox" in data

    def test_missing_bbox_returns_400(self, client):
        r = client.get("/api/terrain/dem/raw?dim=10")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/terrain/sources
# ---------------------------------------------------------------------------

class TestTerrainSources:
    def test_returns_200(self, client):
        r = client.get("/api/terrain/sources")
        assert r.status_code == 200

    def test_response_is_list_or_dict(self, client):
        r = client.get("/api/terrain/sources")
        assert isinstance(r.json(), (list, dict))
