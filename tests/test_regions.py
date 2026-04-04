"""
Tests for /api/regions/* CRUD endpoints (regions.py).

Uses a fresh SQLite DB (temp file) set up by conftest.tmp_data_dir.
The fixture pre-populates the DB with one region ("TestRegion").
"""

import pytest


# ---------------------------------------------------------------------------
# GET /api/regions
# ---------------------------------------------------------------------------

class TestListRegions:
    def test_returns_list(self, client):
        r = client.get("/api/regions")
        assert r.status_code == 200
        data = r.json()
        assert "regions" in data
        assert isinstance(data["regions"], list)

    def test_preloaded_region_is_present(self, client):
        r = client.get("/api/regions")
        names = [reg["name"] for reg in r.json()["regions"]]
        assert "TestRegion" in names

    def test_region_has_required_fields(self, client):
        r = client.get("/api/regions")
        reg = r.json()["regions"][0]
        for field in ("name", "north", "south", "east", "west"):
            assert field in reg, f"missing field: {field}"

    def test_empty_db_returns_empty_list(self, client, tmp_data_dir):
        # Delete the pre-seeded TestRegion so the list is empty
        client.delete("/api/regions/TestRegion")
        r = client.get("/api/regions")
        assert r.status_code == 200
        assert r.json()["regions"] == []


# ---------------------------------------------------------------------------
# POST /api/regions
# ---------------------------------------------------------------------------

_NEW_REGION = {
    "name": "NewRegion",
    "north": 51.5,
    "south": 51.4,
    "east": -0.1,
    "west": -0.2,
    "description": "London test",
    "label": "London",
}


class TestCreateRegion:
    def test_creates_region(self, client):
        r = client.post("/api/regions", json=_NEW_REGION)
        assert r.status_code == 201

    def test_created_region_appears_in_list(self, client):
        client.post("/api/regions", json=_NEW_REGION)
        r = client.get("/api/regions")
        names = [reg["name"] for reg in r.json()["regions"]]
        assert "NewRegion" in names

    def test_created_region_has_correct_bbox(self, client):
        client.post("/api/regions", json=_NEW_REGION)
        r = client.get("/api/regions")
        reg = next(reg for reg in r.json()["regions"] if reg["name"] == "NewRegion")
        assert reg["north"] == pytest.approx(51.5)
        assert reg["south"] == pytest.approx(51.4)
        assert reg["east"] == pytest.approx(-0.1)
        assert reg["west"] == pytest.approx(-0.2)

    def test_missing_name_returns_error(self, client):
        r = client.post("/api/regions", json={
            "north": 51.5, "south": 51.4, "east": -0.1, "west": -0.2
        })
        # Pydantic validation should fail
        assert r.status_code in (400, 422)

    def test_default_parameters_present(self, client):
        r = client.post("/api/regions", json=_NEW_REGION)
        assert r.status_code == 201
        data = r.json()
        assert "parameters" in data
        assert data["parameters"] is not None


# ---------------------------------------------------------------------------
# PUT /api/regions/{name}
# ---------------------------------------------------------------------------

class TestUpdateRegion:
    def test_updates_bbox(self, client):
        updated = dict(_NEW_REGION, north=52.0, south=51.9)
        client.post("/api/regions", json=_NEW_REGION)
        r = client.put("/api/regions/NewRegion", json=updated)
        assert r.status_code == 200
        data = r.json()
        assert data["north"] == pytest.approx(52.0)

    def test_update_nonexistent_returns_404(self, client):
        r = client.put("/api/regions/DoesNotExist", json=_NEW_REGION)
        assert r.status_code == 404

    def test_update_preserves_name(self, client):
        client.post("/api/regions", json=_NEW_REGION)
        updated = dict(_NEW_REGION, description="updated description")
        r = client.put("/api/regions/NewRegion", json=updated)
        assert r.status_code == 200
        assert r.json()["name"] == "NewRegion"


# ---------------------------------------------------------------------------
# DELETE /api/regions/{name}
# ---------------------------------------------------------------------------

class TestDeleteRegion:
    def test_deletes_existing_region(self, client):
        client.post("/api/regions", json=_NEW_REGION)
        r = client.delete("/api/regions/NewRegion")
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"

    def test_deleted_region_gone_from_list(self, client):
        client.post("/api/regions", json=_NEW_REGION)
        client.delete("/api/regions/NewRegion")
        r = client.get("/api/regions")
        names = [reg["name"] for reg in r.json()["regions"]]
        assert "NewRegion" not in names

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/api/regions/DoesNotExist")
        assert r.status_code == 404

    def test_preloaded_region_can_be_deleted(self, client):
        r = client.delete("/api/regions/TestRegion")
        assert r.status_code == 200
