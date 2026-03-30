"""
Shared pytest fixtures for strm2stl API tests.

Sets STRM2STL_TEST_MODE=1 so the DEM endpoint returns a fast deterministic
response without any Earth Engine or network calls.
"""
import json
import os
import sys
from pathlib import Path

import pytest

# Point to strm2stl root and strm2stl/ui so imports match the app's own paths.
# server.py uses short paths like `from routers.regions import router` and
# `from core.cache import ...`.  We must patch those same module objects.
_STRM2STL_ROOT = Path(__file__).parent.parent
_UI_DIR = _STRM2STL_ROOT / "ui"
for _p in (str(_STRM2STL_ROOT.parent), str(_STRM2STL_ROOT), str(_UI_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Enable test mode before importing the app
os.environ["STRM2STL_TEST_MODE"] = "1"


@pytest.fixture()
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Redirect storage paths to a temporary directory so tests never touch
    real data files or the production SQLite database.

    - Forces JSON fallback in regions router (skips SQLite).
    - Redirects CACHE_ROOT so cache reads/writes go to tmp/.
    - Redirects legacy OSM_CACHE_PATH in the cities router.

    IMPORTANT: imports use the same short paths that server.py uses
    (e.g. `import routers.regions`, not `strm2stl.ui.routers.regions`)
    so monkeypatching hits the same module objects the app routes close over.
    """
    # Trigger the server import first so all modules are in sys.modules
    import strm2stl.ui.server  # noqa: F401 — ensures routers are imported

    import routers.regions as regions_router
    import core.cache as cache_module
    import routers.cities as cities_router

    # Use JSON fallback so tests don't depend on a production SQLite DB
    monkeypatch.setattr(regions_router, "_DB_AVAILABLE", False)

    # Coordinates file with one pre-existing region
    coords_file = tmp_path / "coordinates.json"
    coords_file.write_text(json.dumps({"regions": [
        {
            "name": "TestRegion",
            "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2,
            "description": "Test region",
            "label": None,
            "parameters": {
                "dim": 100, "depth_scale": 0.5, "water_scale": 0.05,
                "height": 10, "base": 2, "subtract_water": True, "sat_scale": 500
            }
        }
    ]}))

    settings_file = tmp_path / "region_settings.json"
    monkeypatch.setattr(regions_router, "COORDINATES_PATH", coords_file)
    monkeypatch.setattr(regions_router, "REGION_SETTINGS_PATH", settings_file)

    # Redirect disk cache so tests never write to Code/cache/
    test_cache_root = tmp_path / "cache"
    test_cache_root.mkdir()
    monkeypatch.setattr(cache_module, "CACHE_ROOT", test_cache_root)
    # Also update the CACHE_ROOT reference already imported into the cities router
    monkeypatch.setattr(cities_router, "CACHE_ROOT", test_cache_root)

    # Legacy OSM_CACHE_PATH fallback (used when _CACHE_AVAILABLE=False)
    osm_cache = tmp_path / "osm_raw_cache"
    osm_cache.mkdir()
    monkeypatch.setattr(cities_router, "OSM_CACHE_PATH", osm_cache)

    return {
        "coords_file": coords_file,
        "settings_file": settings_file,
        "osm_cache": osm_cache,
        "cache_root": test_cache_root,
        "tmp_path": tmp_path,
    }


@pytest.fixture()
def client(tmp_data_dir):
    """FastAPI TestClient using the real server app with patched paths."""
    from fastapi.testclient import TestClient
    from strm2stl.ui.server import app
    return TestClient(app)
