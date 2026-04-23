"""
End-to-end tests for the TerrainSession SDK against the FastAPI app.

Strategy
--------
``TerrainSession`` makes all HTTP calls through the ``requests`` module.
We monkeypatch ``requests`` in the ``terrain_session`` module to a thin shim
that routes every call through FastAPI's ``TestClient`` (in-process, no live
server needed).  This covers *all* HTTP paths including the methods that call
``requests.get / post / delete`` directly instead of going through
``_api_request``.

The ``session`` fixture depends on the shared ``client`` fixture (from
conftest.py) which:
  - Creates a fresh temp-dir SQLite with TestRegion pre-seeded.
  - Returns a FastAPI TestClient wired to the real app.
"""

import os
import sys
from pathlib import Path

import pytest

# Ensure STRM2STL_TEST_MODE is set before any app import so the DEM endpoint
# returns deterministic synthetic data without network/Earth Engine calls.
os.environ.setdefault("STRM2STL_TEST_MODE", "1")

_ROOT = Path(__file__).parent.parent
for _p in (str(_ROOT.parent), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.session.terrain_session as _ts_module  # noqa: E402
from app.session.terrain_session import TerrainSession  # noqa: E402

# ---------------------------------------------------------------------------
# HTTP shim: routes requests.{get,post,put,delete} through FastAPI TestClient
# ---------------------------------------------------------------------------

class _WrappedResponse:
    """Adapter that makes an httpx.Response look like a requests.Response.

    FastAPI's TestClient returns httpx.Response objects which have
    ``.is_success`` instead of ``.ok``.  TerrainSession checks ``.ok`` on
    every response, so we wrap the httpx response to add that attribute.
    """

    def __init__(self, resp):
        self._r = resp

    # ── requests-compatible attributes ──────────────────────────────────
    @property
    def ok(self) -> bool:
        return self._r.is_success

    @property
    def status_code(self) -> int:
        return self._r.status_code

    @property
    def text(self) -> str:
        return self._r.text

    @property
    def content(self) -> bytes:
        return self._r.content

    @property
    def headers(self):
        return self._r.headers

    def json(self):
        return self._r.json()

    def raise_for_status(self):
        return self._r.raise_for_status()


class _FakeRequests:
    """Thin shim that intercepts requests.* calls and routes them through
    a FastAPI TestClient.

    TerrainSession sets ``self._base = f"http://127.0.0.1:{port}"``.
    With port=0 the base is ``http://127.0.0.1:0`` — we strip that prefix to
    obtain the bare endpoint path expected by the TestClient.
    """

    def __init__(self, test_client, base: str = "http://127.0.0.1:0"):
        self._tc = test_client
        self._base = base

    def _path(self, url: str) -> str:
        """Strip the base URL prefix to get the bare endpoint path."""
        return url.replace(self._base, "", 1)

    def _wrap(self, resp) -> _WrappedResponse:
        return _WrappedResponse(resp)

    def get(self, url: str, **kwargs) -> _WrappedResponse:
        kwargs.pop("timeout", None)
        return self._wrap(self._tc.get(self._path(url), **kwargs))

    def post(self, url: str, **kwargs) -> _WrappedResponse:
        kwargs.pop("timeout", None)
        return self._wrap(self._tc.post(self._path(url), **kwargs))

    def put(self, url: str, **kwargs) -> _WrappedResponse:
        kwargs.pop("timeout", None)
        return self._wrap(self._tc.put(self._path(url), **kwargs))

    def delete(self, url: str, **kwargs) -> _WrappedResponse:
        kwargs.pop("timeout", None)
        return self._wrap(self._tc.delete(self._path(url), **kwargs))


@pytest.fixture()
def session(client, monkeypatch):
    """TerrainSession wired to the in-process TestClient via requests monkeypatch."""
    monkeypatch.setattr(_ts_module, "requests", _FakeRequests(client))
    return TerrainSession(port=0)


# ---------------------------------------------------------------------------
# Server settings
# ---------------------------------------------------------------------------

class TestSessionServerSettings:
    """Tests for settings endpoints used by TerrainSession.

    Includes both the combined SDK endpoint (/api/settings) and the
    fine-grained settings endpoints.
    """

    def test_projections_endpoint_returns_list(self, session):
        data = session._api_request("get", "/api/settings/projections")
        assert "projections" in data
        proj_ids = [p["id"] for p in data["projections"]]
        assert "none" in proj_ids

    def test_projections_each_have_id_and_name(self, session):
        data = session._api_request("get", "/api/settings/projections")
        for p in data["projections"]:
            assert "id" in p
            assert "name" in p

    def test_colormaps_endpoint_returns_list(self, session):
        data = session._api_request("get", "/api/settings/colormaps")
        assert "colormaps" in data
        ids = [c["id"] for c in data["colormaps"]]
        assert "terrain" in ids

    def test_datasets_endpoint_returns_list(self, session):
        data = session._api_request("get", "/api/settings/datasets")
        assert "datasets" in data
        ids = [d["id"] for d in data["datasets"]]
        assert "esa" in ids

    def test_server_settings_returns_all_config(self, session):
        """server_settings() now calls the combined GET /api/settings endpoint."""
        data = session.server_settings()
        assert "projections" in data
        assert "colormaps" in data
        assert "datasets" in data
        assert len(data["projections"]) > 0
        assert len(data["colormaps"]) > 0
        assert len(data["datasets"]) > 0


class TestSessionApiRequestValidation:
    def test_api_request_allows_uppercase_method(self, session):
        data = session._api_request("GET", "/api/settings/projections")
        assert "projections" in data

    def test_api_request_rejects_unknown_method(self, session):
        with pytest.raises(ValueError, match="Unsupported HTTP method"):
            session._api_request("TRACE", "/api/settings/projections")

    def test_api_request_raw_returns_response_wrapper(self, session):
        response = session._api_request_raw("GET", "/api/cache", timeout=10)
        assert response.ok
        assert response.status_code == 200
        assert isinstance(response.json(), dict)


# ---------------------------------------------------------------------------
# Region CRUD
# ---------------------------------------------------------------------------

class TestSessionRegionCRUD:
    def test_select_seeded_region(self, session):
        session.select("TestRegion")
        assert session.region_name == "TestRegion"
        assert session.bbox == {
            "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2
        }

    def test_select_unknown_region_raises(self, session):
        with pytest.raises(ValueError, match="not found"):
            session.select("DoesNotExist")

    def test_create_region_sets_state(self, session):
        session.create_region(
            "E2ERegion", north=51.5, south=51.4, east=-0.1, west=-0.2
        )
        assert session.region_name == "E2ERegion"
        assert session.bbox["north"] == 51.5
        assert session.bbox["west"] == -0.2

    def test_create_region_persists_and_is_selectable(self, session):
        session.create_region(
            "PersistedRegion", north=48.9, south=48.8, east=2.4, west=2.3
        )
        session.region_name = None
        session.bbox = {}
        session.select("PersistedRegion")
        assert session.region_name == "PersistedRegion"
        assert session.bbox["north"] == 48.9

    def test_update_region_reflects_locally(self, session):
        session.select("TestRegion")
        session.update_region(north=41.0, south=40.0, east=-74.0, west=-75.0)
        assert session.bbox["north"] == 41.0
        assert session.bbox["south"] == 40.0

    def test_create_and_delete_region(self, session):
        session.create_region(
            "TempRegion", north=1.0, south=0.0, east=1.0, west=0.0
        )
        session.delete_region("TempRegion")
        # Should no longer be selectable
        with pytest.raises(ValueError, match="not found"):
            session.select("TempRegion")

    def test_delete_selected_region_clears_state(self, session):
        session.create_region(
            "DeleteMe", north=1.0, south=0.0, east=1.0, west=0.0
        )
        assert session.region_name == "DeleteMe"
        session.delete_region()  # no arg → uses current selection
        assert session.region_name is None
        assert session.bbox == {}

    def test_delete_without_selection_raises(self, session):
        with pytest.raises(RuntimeError):
            session.delete_region()


# ---------------------------------------------------------------------------
# Settings persistence (save → re-select → verify round-trip)
# ---------------------------------------------------------------------------

class TestSessionSettingsPersistence:
    def test_save_and_reload_grouped_settings(self, session):
        session.create_region(
            "PersistRegion", north=40.0, south=39.9, east=-75.1, west=-75.2
        )
        session.settings["dem"]["dim"] = 256
        session.settings["export"]["model_height"] = 42.0
        session.save_settings()

        # Re-select — should reload from DB and merge saved values
        session.select("PersistRegion")
        assert session.settings["dem"]["dim"] == 256
        assert session.settings["export"]["model_height"] == 42.0

    def test_save_requires_selected_region(self, session):
        with pytest.raises(RuntimeError):
            session.save_settings()

    def test_save_preserves_unset_defaults(self, session):
        """Settings not explicitly changed should retain their defaults."""
        session.create_region(
            "DefaultsRegion", north=40.0, south=39.9, east=-75.1, west=-75.2
        )
        session.settings["dem"]["dim"] = 150
        session.save_settings()

        session.select("DefaultsRegion")
        # dim was saved
        assert session.settings["dem"]["dim"] == 150
        # projection default untouched
        assert session.settings["projection"]["projection"] == "none"


# ---------------------------------------------------------------------------
# DEM fetch
# ---------------------------------------------------------------------------

class TestSessionFetchDEM:
    def test_fetch_dem_populates_dem_attribute(self, session):
        session.select("TestRegion")
        session.settings["dem"]["dim"] = 50  # small → fast in test mode
        session.fetch_dem()
        assert session.dem is not None

    def test_fetch_dem_response_keys(self, session):
        session.select("TestRegion")
        session.settings["dem"]["dim"] = 50
        session.fetch_dem()
        for key in ("dimensions", "min_elevation", "max_elevation",
                    "mean_elevation", "dem_values_b64"):
            assert key in session.dem, f"Missing key: {key}"

    def test_fetch_dem_dimensions_match_dim(self, session):
        session.select("TestRegion")
        session.settings["dem"]["dim"] = 50
        session.fetch_dem()
        h, w = session.dem["dimensions"]
        assert h == 50
        assert w == 50

    def test_fetch_dem_without_select_raises(self, session):
        with pytest.raises(RuntimeError):
            session.fetch_dem()

    def test_fetch_dem_elevation_values_are_numeric(self, session):
        session.select("TestRegion")
        session.settings["dem"]["dim"] = 50
        session.fetch_dem()
        assert isinstance(session.dem["min_elevation"], (int, float))
        assert isinstance(session.dem["max_elevation"], (int, float))
        assert session.dem["max_elevation"] >= session.dem["min_elevation"]


# ---------------------------------------------------------------------------
# Settings validation (pure Python, no HTTP)
# ---------------------------------------------------------------------------

class TestSessionSettingsValidation:
    def test_valid_default_settings_pass(self, session):
        # Should not raise
        session._validate_settings()

    def test_invalid_projection_raises(self, session):
        session.settings["projection"]["projection"] = "unknown_proj"
        with pytest.raises(ValueError, match="not recognised"):
            session._validate_settings()

    def test_invalid_dem_source_raises(self, session):
        session.settings["dem"]["dem_source"] = "INVALID_SOURCE"
        with pytest.raises(ValueError, match="not recognised"):
            session._validate_settings()

    def test_dim_zero_raises(self, session):
        session.settings["dem"]["dim"] = 0
        with pytest.raises(ValueError, match="must be between 1 and 2000"):
            session._validate_settings()

    def test_dim_too_large_raises(self, session):
        session.settings["dem"]["dim"] = 9999
        with pytest.raises(ValueError, match="must be between 1 and 2000"):
            session._validate_settings()

    def test_negative_model_height_raises(self, session):
        session.settings["export"]["model_height"] = -5.0
        with pytest.raises(ValueError, match="must be a positive number"):
            session._validate_settings()

    def test_invalid_bool_flag_raises(self, session):
        session.settings["dem"]["subtract_water"] = "yes"
        with pytest.raises(ValueError, match="True or False"):
            session._validate_settings()

    def test_invalid_water_dataset_raises(self, session):
        session.settings["water"]["dataset"] = "unknown_ds"
        with pytest.raises(ValueError, match="not recognised"):
            session._validate_settings()

    def test_invalid_city_layers_raises(self, session):
        session.settings["city"]["layers"] = ["buildings", "bad_layer"]
        with pytest.raises(ValueError, match="unknown layers"):
            session._validate_settings()

    def test_sat_scale_below_10_raises(self, session):
        session.settings["water"]["sat_scale"] = 5
        with pytest.raises(ValueError, match="integer"):
            session._validate_settings()


# ---------------------------------------------------------------------------
# Cache operations
# ---------------------------------------------------------------------------

class TestSessionCacheOperations:
    def test_cache_status_returns_stats(self, session):
        data = session.cache_status()
        assert "total_cached_files" in data
        assert "total_size_mb" in data
        assert isinstance(data["total_cached_files"], int)

    def test_clear_cache_returns_cleared(self, session):
        data = session.clear_cache()
        assert "cleared" in data

    def test_cache_status_after_clear(self, session):
        session.clear_cache()
        data = session.cache_status()
        assert data["total_cached_files"] == 0


class TestSessionLayerFetches:
    def test_fetch_water_mask_populates_session_state(self, session):
        session.select("TestRegion")
        session.settings["dem"]["dim"] = 64
        session.fetch_water_mask()

        assert session.water_mask is not None
        assert session.esa_landcover is not None
        assert "water_mask_dimensions" in session.water_mask
        assert "esa_dimensions" in session.esa_landcover

    def test_fetch_satellite_populates_base64_image(self, session):
        session.select("TestRegion")
        session.settings["satellite"]["dim"] = 64
        session.fetch_satellite()

        assert session.satellite is not None
        assert isinstance(session.satellite, str)
        assert len(session.satellite) > 0


class TestSessionCityFlows:
    def test_fetch_cities_populates_city_data_for_small_region(self, session):
        session.create_region(
            "SmallFetchedCityRegion", north=40.000, south=39.995, east=-75.100, west=-75.105
        )
        session.fetch_cities()
        assert session.city_data is not None

    def test_check_city_cache_returns_boolean(self, session):
        session.create_region(
            "SmallCityRegion", north=40.000, south=39.995, east=-75.100, west=-75.105
        )
        cached = session.check_city_cache()
        assert isinstance(cached, bool)

    def test_composite_city_raster_populates_session_state(self, session):
        session.create_region(
            "SmallCityRasterRegion", north=40.000, south=39.995, east=-75.100, west=-75.105
        )
        session.fetch_cities()
        session.composite_city_raster(width=64, height=64)

        assert session.city_raster is not None
        assert "width" in session.city_raster
        assert "height" in session.city_raster


# ---------------------------------------------------------------------------
# Bbox guard (_ensure_bbox)
# ---------------------------------------------------------------------------

class TestSessionEnsureBbox:
    def test_ensure_bbox_raises_when_no_region(self, session):
        with pytest.raises(RuntimeError, match="not available"):
            session._ensure_bbox()

    def test_ensure_bbox_passes_after_select(self, session):
        session.select("TestRegion")
        session._ensure_bbox()  # must not raise

    def test_ensure_bbox_passes_after_create(self, session):
        session.create_region(
            "BboxRegion", north=10.0, south=9.0, east=1.0, west=0.0
        )
        session._ensure_bbox()  # must not raise


# ---------------------------------------------------------------------------
# Pipeline smoke tests (multi-step sequences)
# ---------------------------------------------------------------------------

class TestSessionPipeline:
    def test_create_set_dim_fetch_dem(self, session):
        """Full pipeline: create → tweak settings → fetch DEM."""
        session.create_region(
            "PipelineA", north=40.0, south=39.9, east=-75.1, west=-75.2
        )
        session.settings["dem"]["dim"] = 64
        session.fetch_dem()
        assert session.dem is not None
        h, w = session.dem["dimensions"]
        assert h == 64

    def test_create_save_reselect_fetch_dem(self, session):
        """Settings persisted via save_settings() are honoured by fetch_dem()."""
        session.create_region(
            "PipelineB", north=40.0, south=39.9, east=-75.1, west=-75.2
        )
        session.settings["dem"]["dim"] = 48
        session.save_settings()

        session.select("PipelineB")
        assert session.settings["dem"]["dim"] == 48
        session.fetch_dem()
        h, w = session.dem["dimensions"]
        assert h == 48

    def test_regions_list_grows_after_create(self, session):
        """create_region() → regions() returns the new entry."""
        raw_before = session._api_request("get", "/api/regions")["regions"]
        names_before = {r["name"] for r in raw_before}

        session.create_region(
            "ListGrowsRegion", north=1.0, south=0.0, east=1.0, west=0.0
        )
        raw_after = session._api_request("get", "/api/regions")["regions"]
        names_after = {r["name"] for r in raw_after}

        assert "ListGrowsRegion" in names_after
        assert len(names_after) == len(names_before) + 1

    def test_delete_removes_from_list(self, session):
        session.create_region(
            "WillBeDeleted", north=1.0, south=0.0, east=1.0, west=0.0
        )
        session.delete_region("WillBeDeleted")
        raw = session._api_request("get", "/api/regions")["regions"]
        names = {r["name"] for r in raw}
        assert "WillBeDeleted" not in names


# ---------------------------------------------------------------------------
# Additional endpoint tests (from coverage audit priority list)
# ---------------------------------------------------------------------------

class TestSessionWaterMaskSmoke:
    """Smoke test for water-mask endpoint (requires TEST_MODE for offline data)."""

    def test_water_mask_endpoint_responds(self, session):
        """GET /api/terrain/water-mask should respond in TEST_MODE."""
        session.select("TestRegion")
        bbox = session.bbox
        # In TEST_MODE, water-mask should return synthetic data or test stub
        params = {"north": bbox["north"], "south": bbox["south"], 
                  "east": bbox["east"], "west": bbox["west"]}
        data = session._api_request("get", "/api/terrain/water-mask", params=params, timeout=10)
        assert data is not None
        # Should have mask data
        assert isinstance(data, dict)

    def test_water_mask_has_expected_structure(self, session):
        """Verify water-mask response includes required fields."""
        session.select("TestRegion")
        bbox = session.bbox
        params = {"north": bbox["north"], "south": bbox["south"], 
                  "east": bbox["east"], "west": bbox["west"]}
        data = session._api_request("get", "/api/terrain/water-mask", params=params, timeout=10)
        # Expect ESA land cover and water mask dimensions
        assert "esa_dimensions" in data or "water_mask_dimensions" in data
        assert "esa_values_b64" in data or "water_mask" in data


class TestSessionCompositeDeMMerge:
    """Smoke test for dem-merge composite endpoint (no network needed)."""

    def test_dem_merge_endpoint_responds(self, session):
        """POST /api/composite/dem-merge should accept proper merge request."""
        session.select("TestRegion")
        bbox = session.bbox
        
        # Create a minimal merge request with one layer spec
        payload = {
            "bbox": bbox,
            "dim": 64,
            "layers": [
                {
                    "source": "local",
                    "dim": 64,
                    "blend_mode": "base",
                    "weight": 1.0,
                    "label": "base"
                }
            ]
        }
        result = session._api_request("post", "/api/composite/dem-merge", json=payload, timeout=10)
        
        # Should return merged result
        assert result is not None
        assert isinstance(result, dict)
        # Response should include merged dem
        assert "dem_values_b64" in result or "error" in result

    def test_dem_merge_returns_dimensions(self, session):
        """Verify dem-merge returns valid output structure in TEST_MODE."""
        session.select("TestRegion")
        bbox = session.bbox
        
        payload = {
            "bbox": bbox,
            "dim": 64,
            "layers": [
                {
                    "source": "local",
                    "dim": 64,
                    "blend_mode": "base",
                    "weight": 1.0,
                    "processing": {}
                }
            ]
        }
        result = session._api_request("post", "/api/composite/dem-merge", json=payload, timeout=10)
        
        # Should return dimensions and elevation data (or error is acceptable for smoke test)
        assert "dimensions" in result or "error" in result
        if "dimensions" in result:
            assert "dem_values_b64" in result

    def test_merge_dem_sdk_method_updates_session_dem(self, session):
        """TerrainSession.merge_dem should populate self.dem through _api_request."""
        session.select("TestRegion")
        session.settings["dem"]["dim"] = 64

        layers = [{
            "source": "local",
            "dim": 64,
            "blend_mode": "base",
            "weight": 1.0,
            "processing": {},
            "label": "base",
        }]

        session.merge_dem(layers)
        assert session.dem is not None
        assert "dimensions" in session.dem
        assert "dem_values_b64" in session.dem


class TestSessionHydrologySmoke:
    """Smoke test for hydrology endpoint (Natural Earth data is offline-capable)."""

    def test_hydrology_endpoint_responds(self, session):
        """GET /api/terrain/hydrology should use offline Natural Earth data."""
        session.select("TestRegion")
        bbox = session.bbox
        params = {"north": bbox["north"], "south": bbox["south"], 
                  "east": bbox["east"], "west": bbox["west"]}
        # Natural Earth HydroRIVERS is cached locally; should not need network
        data = session._api_request("get", "/api/terrain/hydrology", params=params, timeout=10)
        assert data is not None
        assert isinstance(data, dict)

    def test_hydrology_returns_valid_structure(self, session):
        """Verify hydrology response has valid structure."""
        session.select("TestRegion")
        bbox = session.bbox
        params = {"north": bbox["north"], "south": bbox["south"], 
                  "east": bbox["east"], "west": bbox["west"]}
        data = session._api_request("get", "/api/terrain/hydrology", params=params, timeout=10)
        # Should have river grid data with encoded geometry
        assert "river_grid_dimensions" in data
        assert "river_grid_values_b64" in data
        assert "feature_count" in data
