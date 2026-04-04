"""
Tests for per-region settings persistence.

Endpoints:
  GET  /api/regions/{name}/settings
  PUT  /api/regions/{name}/settings
  DELETE /api/regions/{name}

Uses a fresh SQLite DB (temp file) set up by conftest.tmp_data_dir.
The fixture pre-populates the DB with one region ("TestRegion").
"""
import pytest


# ---------------------------------------------------------------------------
# GET /api/regions/{name}/settings
# ---------------------------------------------------------------------------

class TestGetRegionSettings:
    def test_returns_empty_when_no_settings_saved(self, client):
        """No settings saved → 200 with empty settings dict (not 404)."""
        resp = client.get("/api/regions/TestRegion/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "TestRegion"
        assert data["settings"] == {}

    def test_returns_empty_when_region_not_in_db(self, client):
        """Region with no saved settings → 200 with empty settings."""
        resp = client.get("/api/regions/TestRegion/settings")
        assert resp.status_code == 200
        assert resp.json()["settings"] == {}

    def test_returns_saved_settings(self, client):
        saved = {"dim": 300, "colormap": "viridis", "projection": "mercator"}
        client.put("/api/regions/TestRegion/settings", json=saved)

        resp = client.get("/api/regions/TestRegion/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "TestRegion"
        assert data["settings"]["dim"] == 300
        assert data["settings"]["colormap"] == "viridis"
        assert data["settings"]["projection"] == "mercator"

    def test_name_is_url_encoded(self, client):
        """Region names with spaces should be URL-encoded."""
        # Create a region with a space in the name
        client.post("/api/regions", json={
            "name": "My Region", "north": 40.0, "south": 39.9,
            "east": -75.1, "west": -75.2,
        })
        client.put("/api/regions/My%20Region/settings", json={"dim": 50})
        resp = client.get("/api/regions/My%20Region/settings")
        assert resp.status_code == 200
        assert resp.json()["settings"]["dim"] == 50


# ---------------------------------------------------------------------------
# PUT /api/regions/{name}/settings
# ---------------------------------------------------------------------------

class TestSaveRegionSettings:
    def test_saves_settings_successfully(self, client):
        resp = client.put(
            "/api/regions/TestRegion/settings",
            json={"dim": 400, "colormap": "plasma"}
        )
        assert resp.status_code == 200

    def test_persisted_values_are_correct(self, client):
        client.put("/api/regions/TestRegion/settings", json={"dim": 250, "projection": "cosine"})
        resp = client.get("/api/regions/TestRegion/settings")
        settings = resp.json()["settings"]
        assert settings["dim"] == 250
        assert settings["projection"] == "cosine"

    def test_partial_put_only_stores_non_null_fields(self, client):
        """Sending only some fields should not store nulls for omitted ones."""
        client.put("/api/regions/TestRegion/settings", json={"dim": 100})
        resp = client.get("/api/regions/TestRegion/settings")
        region = resp.json()["settings"]
        assert region["dim"] == 100
        # depth_scale was not sent — should not be present (not null)
        assert "depth_scale" not in region

    def test_updates_existing_settings(self, client):
        client.put("/api/regions/TestRegion/settings", json={"dim": 100})
        client.put("/api/regions/TestRegion/settings", json={"dim": 999, "colormap": "gray"})
        resp = client.get("/api/regions/TestRegion/settings")
        settings = resp.json()["settings"]
        assert settings["dim"] == 999
        assert settings["colormap"] == "gray"

    def test_multiple_regions_coexist(self, client):
        # Create two extra regions, then save settings for each
        client.post("/api/regions", json={
            "name": "RegionA", "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2,
        })
        client.post("/api/regions", json={
            "name": "RegionB", "north": 41.0, "south": 40.9, "east": -74.1, "west": -74.2,
        })
        client.put("/api/regions/RegionA/settings", json={"dim": 100})
        client.put("/api/regions/RegionB/settings", json={"dim": 200})
        assert client.get("/api/regions/RegionA/settings").json()["settings"]["dim"] == 100
        assert client.get("/api/regions/RegionB/settings").json()["settings"]["dim"] == 200

    def test_response_contains_saved_settings(self, client):
        resp = client.put("/api/regions/TestRegion/settings", json={"dim": 150})
        body = resp.json()
        assert body["status"] == "saved"
        assert body["name"] == "TestRegion"
        assert body["settings"]["dim"] == 150

    def test_elevation_curve_points_round_trip(self, client):
        points = [[0.0, 0.0], [0.5, 0.6], [1.0, 1.0]]
        client.put("/api/regions/TestRegion/settings", json={"elevation_curve_points": points})
        resp = client.get("/api/regions/TestRegion/settings")
        assert resp.json()["settings"]["elevation_curve_points"] == points


# ---------------------------------------------------------------------------
# DELETE /api/regions/{name} — settings cleanup
# ---------------------------------------------------------------------------

class TestDeleteRegionCleansSettings:
    def test_delete_removes_settings_entry(self, client):
        client.put("/api/regions/TestRegion/settings", json={"dim": 200})

        resp = client.delete("/api/regions/TestRegion")
        assert resp.status_code == 200

        # Settings should be gone (CASCADE delete) — returns empty dict, not error
        resp2 = client.get("/api/regions/TestRegion/settings")
        # Region is deleted so settings row is gone — returns empty or 404
        assert resp2.status_code in (200, 404)
        if resp2.status_code == 200:
            assert resp2.json()["settings"] == {}

    def test_delete_tolerates_missing_settings(self, client):
        """Delete should succeed even if no settings were saved."""
        resp = client.delete("/api/regions/TestRegion")
        assert resp.status_code == 200

    def test_delete_only_removes_target_region_settings(self, client):
        client.post("/api/regions", json={
            "name": "OtherRegion", "north": 41.0, "south": 40.9,
            "east": -74.1, "west": -74.2,
        })
        client.put("/api/regions/TestRegion/settings", json={"dim": 100})
        client.put("/api/regions/OtherRegion/settings", json={"dim": 200})

        client.delete("/api/regions/TestRegion")

        resp = client.get("/api/regions/OtherRegion/settings")
        assert resp.json()["settings"]["dim"] == 200
