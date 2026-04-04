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


def _seed_db(db_path: Path) -> None:
    """Initialise a fresh SQLite DB and insert the pre-existing TestRegion."""
    import core.db as db_module
    db_module.init_db(db_path)
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT OR REPLACE INTO regions "
        "(name, label, description, north, south, east, west, "
        " dim, depth_scale, water_scale, height, base, subtract_water, sat_scale) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("TestRegion", None, "Test region",
         40.0, 39.9, -75.1, -75.2,
         100, 0.5, 0.05, 10.0, 2.0, 1, 500),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Redirect storage paths to a temporary directory so tests never touch
    real data files or the production SQLite database.

    - Points core.db.DB_PATH to a fresh tmp SQLite file with TestRegion seeded.
    - Redirects CACHE_ROOT so cache reads/writes go to tmp/.
    - Redirects legacy OSM_CACHE_PATH in the cities router.

    IMPORTANT: imports use the same short paths that server.py uses
    (e.g. `import routers.regions`, not `strm2stl.ui.routers.regions`)
    so monkeypatching hits the same module objects the app routes close over.
    """
    # Trigger the server import first so all modules are in sys.modules
    import strm2stl.ui.server  # noqa: F401 — ensures routers are imported

    import core.db as db_module
    import core.cache as cache_module
    import routers.cities as cities_router

    # Redirect SQLite to a fresh temp file with TestRegion pre-seeded
    test_db = tmp_path / "test_data.db"
    _seed_db(test_db)
    monkeypatch.setattr(db_module, "DB_PATH", test_db)

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
        "db_path": test_db,
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
