"""
Tests for /api/terrain/* endpoints (terrain.py).

All tests run with MAP2STL_TEST_MODE=1 (set in conftest) so the DEM
endpoint returns a fast deterministic gradient with no network calls.
"""



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
        r = client.get(
            "/api/terrain/dem?north=39.0&south=40.0&east=-75.1&west=-75.2&dim=10")
        assert r.status_code == 400
        assert "north" in r.json()["error"].lower(
        ) or "south" in r.json()["error"].lower()

    def test_east_less_than_west_returns_400(self, client):
        r = client.get(
            "/api/terrain/dem?north=40.0&south=39.9&east=-76.0&west=-75.0&dim=10")
        assert r.status_code == 400

    def test_dim_too_large_returns_400(self, client):
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=9999")
        assert r.status_code == 400

    def test_dim_zero_returns_400(self, client):
        r = client.get(f"/api/terrain/dem?{_BBOX_QS}&dim=0")
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


def test_hydrology_cache_miss_returns_the_grid(monkeypatch):
    """The first (uncached) hydrology request returns the grid, not `null`."""
    import numpy as np
    from fastapi.testclient import TestClient

    import app.server.routers.terrain as terrain
    from app.server.server import app

    monkeypatch.setattr(terrain, "TEST_MODE", False)
    grid = np.zeros((60, 60), np.float32)
    grid[30, :] = -5.0
    monkeypatch.setattr(terrain, "_fetch_and_rasterize_hydrology",
                        lambda *a, **k: {"river_grid": grid, "feature_count": 1,
                                         "source": "hydrorivers"})
    r = TestClient(app).get("/api/terrain/hydrology", params={
        "north": 1.0, "south": 0.0, "east": 1.0, "west": 0.0, "dim": 60,
        "source": "natural_earth", "projection": "none"})
    assert r.status_code == 200
    body = r.json()
    assert body is not None and body["river_grid_dimensions"] == [60, 60]
    assert body["feature_count"] == 1


def test_hydrorivers_min_order_is_a_lookup_on_the_cached_order_grid(monkeypatch):
    """HydroRIVERS: min order / depth changes reuse one order grid; the response
    carries the order grid (rivers by order, open water coded) for the colour view."""
    import base64

    import numpy as np
    from fastapi.testclient import TestClient

    import app.server.routers.terrain as terrain
    from app.server.server import app

    monkeypatch.setattr(terrain, "TEST_MODE", False)
    orders = np.zeros((60, 60), np.uint8)
    orders[10, :] = 3
    orders[20, :] = 6
    water = np.zeros((60, 60), bool)
    water[:, 50:] = True
    calls = []

    def layers(*a):
        calls.append(a)
        return orders, water, {3: 7, 6: 2}

    monkeypatch.setattr(terrain, "_hydrorivers_layers", layers)
    client = TestClient(app)

    def get(min_order):
        r = client.get("/api/terrain/hydrology", params={
            "north": 1.0, "south": 0.0, "east": 1.0, "west": 0.0, "dim": 60,
            "source": "hydrorivers", "min_order": min_order, "depression_m": -5,
            "projection": "none"})
        b = r.json()
        dec = lambda k: np.frombuffer(base64.b64decode(b[k]), "<f4").reshape(60, 60)  # noqa: E731
        return b, dec("river_grid_values_b64"), dec("order_grid_b64")

    b3, depth3, ord3 = get(3)
    assert b3["feature_count"] == 9 and (depth3[10, :50] < 0).all()
    sea = ord3[:, 50:]
    assert (ord3[10, :50] == 3).all() and (sea[~np.isin(sea, (3, 6))] == terrain.WATER_CODE).all()
    b5, depth5, ord5 = get(5)
    assert b5["feature_count"] == 2 and (depth5[10, :50] == 0).all() and (ord5[10, :50] == 0).all()
    assert (depth5[20, :50] < 0).all() and (depth5[:, 50:] <= -5).all()
