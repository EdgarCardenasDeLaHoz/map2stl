"""
Tests for per-region settings persistence.

Endpoints:
  GET  /api/regions/{name}/settings
  PUT  /api/regions/{name}/settings
  DELETE /api/regions/{name}
"""
import json
import pytest


# ---------------------------------------------------------------------------
# GET /api/regions/{name}/settings
# ---------------------------------------------------------------------------

class TestGetRegionSettings:
    def test_returns_empty_when_no_settings_file(self, client):
        """No settings file → 200 with empty settings dict (not 404)."""
        resp = client.get("/api/regions/TestRegion/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "TestRegion"
        assert data["settings"] == {}

    def test_returns_empty_when_region_not_in_file(self, client, tmp_data_dir):
        """Region absent from settings file → 200 with empty settings."""
        tmp_data_dir["settings_file"].write_text(json.dumps({"OtherRegion": {"dim": 200}}))
        resp = client.get("/api/regions/TestRegion/settings")
        assert resp.status_code == 200
        assert resp.json()["settings"] == {}

    def test_returns_saved_settings(self, client, tmp_data_dir):
        saved = {"dim": 300, "colormap": "viridis", "projection": "mercator"}
        tmp_data_dir["settings_file"].write_text(json.dumps({"TestRegion": saved}))

        resp = client.get("/api/regions/TestRegion/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "TestRegion"
        assert data["settings"]["dim"] == 300
        assert data["settings"]["colormap"] == "viridis"
        assert data["settings"]["projection"] == "mercator"

    def test_name_is_url_encoded(self, client, tmp_data_dir):
        """Region names with spaces should be URL-encoded."""
        tmp_data_dir["settings_file"].write_text(json.dumps({"My Region": {"dim": 50}}))
        resp = client.get("/api/regions/My%20Region/settings")
        assert resp.status_code == 200
        assert resp.json()["settings"]["dim"] == 50


# ---------------------------------------------------------------------------
# PUT /api/regions/{name}/settings
# ---------------------------------------------------------------------------

class TestSaveRegionSettings:
    def test_creates_settings_file_when_absent(self, client, tmp_data_dir):
        assert not tmp_data_dir["settings_file"].exists()
        resp = client.put(
            "/api/regions/TestRegion/settings",
            json={"dim": 400, "colormap": "plasma"}
        )
        assert resp.status_code == 200
        assert tmp_data_dir["settings_file"].exists()

    def test_persisted_values_are_correct(self, client, tmp_data_dir):
        client.put("/api/regions/TestRegion/settings", json={"dim": 250, "projection": "cosine"})
        saved = json.loads(tmp_data_dir["settings_file"].read_text())
        assert saved["TestRegion"]["dim"] == 250
        assert saved["TestRegion"]["projection"] == "cosine"

    def test_partial_put_only_stores_non_null_fields(self, client, tmp_data_dir):
        """Sending only some fields should not store nulls for omitted ones."""
        client.put("/api/regions/TestRegion/settings", json={"dim": 100})
        saved = json.loads(tmp_data_dir["settings_file"].read_text())
        region = saved["TestRegion"]
        assert region["dim"] == 100
        # depth_scale was not sent — should not be present (not null)
        assert "depth_scale" not in region

    def test_updates_existing_settings(self, client, tmp_data_dir):
        tmp_data_dir["settings_file"].write_text(json.dumps({"TestRegion": {"dim": 100}}))
        client.put("/api/regions/TestRegion/settings", json={"dim": 999, "colormap": "gray"})
        saved = json.loads(tmp_data_dir["settings_file"].read_text())
        assert saved["TestRegion"]["dim"] == 999
        assert saved["TestRegion"]["colormap"] == "gray"

    def test_multiple_regions_coexist(self, client, tmp_data_dir):
        client.put("/api/regions/RegionA/settings", json={"dim": 100})
        client.put("/api/regions/RegionB/settings", json={"dim": 200})
        saved = json.loads(tmp_data_dir["settings_file"].read_text())
        assert "RegionA" in saved
        assert "RegionB" in saved

    def test_response_contains_saved_settings(self, client, tmp_data_dir):
        resp = client.put("/api/regions/TestRegion/settings", json={"dim": 150})
        body = resp.json()
        assert body["status"] == "saved"
        assert body["name"] == "TestRegion"
        assert body["settings"]["dim"] == 150

    def test_elevation_curve_points_round_trip(self, client, tmp_data_dir):
        points = [[0.0, 0.0], [0.5, 0.6], [1.0, 1.0]]
        client.put("/api/regions/TestRegion/settings", json={"elevation_curve_points": points})
        saved = json.loads(tmp_data_dir["settings_file"].read_text())
        assert saved["TestRegion"]["elevation_curve_points"] == points


# ---------------------------------------------------------------------------
# DELETE /api/regions/{name} — settings cleanup
# ---------------------------------------------------------------------------

class TestDeleteRegionCleansSettings:
    def test_delete_removes_settings_entry(self, client, tmp_data_dir):
        tmp_data_dir["settings_file"].write_text(json.dumps({"TestRegion": {"dim": 200}}))

        resp = client.delete("/api/regions/TestRegion")
        assert resp.status_code == 200

        remaining = json.loads(tmp_data_dir["settings_file"].read_text())
        assert "TestRegion" not in remaining

    def test_delete_tolerates_missing_settings_file(self, client, tmp_data_dir):
        """Delete should succeed even if region_settings.json doesn't exist."""
        assert not tmp_data_dir["settings_file"].exists()
        resp = client.delete("/api/regions/TestRegion")
        assert resp.status_code == 200

    def test_delete_only_removes_target_region_settings(self, client, tmp_data_dir):
        tmp_data_dir["settings_file"].write_text(
            json.dumps({"TestRegion": {"dim": 100}, "OtherRegion": {"dim": 200}})
        )
        client.delete("/api/regions/TestRegion")
        remaining = json.loads(tmp_data_dir["settings_file"].read_text())
        assert "OtherRegion" in remaining
        assert "TestRegion" not in remaining
