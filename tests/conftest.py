"""
Shared pytest fixtures for strm2stl API tests.

Sets STRM2STL_TEST_MODE=1 so the DEM endpoint returns a fast deterministic
response without any Earth Engine or network calls.
"""
import os
from pathlib import Path

import pytest

# Enable test mode before importing the app
os.environ["STRM2STL_TEST_MODE"] = "1"


def _seed_db(db_path: Path) -> None:
    """Initialise a fresh SQLite DB and insert the pre-existing TestRegion."""
    import app.server.core.db as db_module
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

    IMPORTANT: imports use the same short paths that server.py uses
    (e.g. `import app.server.routers.regions`, not `strm2stl.ui.routers.regions`)
    so monkeypatching hits the same module objects the app routes close over.
    """
    # Trigger the server import first so all modules are in sys.modules
    import app.server  # noqa: F401 — ensures routers are imported
    import app.server.core.db as db_module
    import app.server.core.dem_store as dem_store_module
    import app.server.routers.cities as cities_router
    import geo2stl.cache as cache_module

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
    # DEM handles are stored under a path fixed at import time
    monkeypatch.setattr(dem_store_module, "STORE_DIR", test_cache_root / "dem_handles")

    return {
        "db_path": test_db,
        "cache_root": test_cache_root,
        "tmp_path": tmp_path,
    }


@pytest.fixture()
def client(tmp_data_dir):
    """FastAPI TestClient using the real server app with patched paths."""
    from fastapi.testclient import TestClient

    from app.server.server import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_export_tasks():
    """Drain export threads started by a test and restore the task registry.

    ``start_export_task`` runs each job in a daemon thread named
    ``export-<task_id>`` and records it in ``export_tasks._export_tasks``.
    Tests that start exports without polling to completion would otherwise
    leak running threads (and registry entries) into later tests.
    """
    import sys
    import threading
    import time

    mod = sys.modules.get("app.server.core.export_tasks")
    before_threads = set(threading.enumerate())
    before_tasks = dict(getattr(mod, "_export_tasks", {}) or {}) if mod else None

    yield

    new_exports = [
        t for t in threading.enumerate()
        if t not in before_threads and t.name.startswith("export-")
    ]
    deadline = time.monotonic() + 10.0
    for t in new_exports:
        t.join(timeout=max(0.0, deadline - time.monotonic()))

    mod = sys.modules.get("app.server.core.export_tasks")
    tasks = getattr(mod, "_export_tasks", None) if mod else None
    if tasks is None:
        return
    lock = getattr(mod, "_export_tasks_lock", None)
    if lock is None:
        import contextlib
        lock = contextlib.nullcontext()
    with lock:
        leaked = [tid for tid in tasks if not before_tasks or tid not in before_tasks]
        removed = [tasks.pop(tid) for tid in leaked]
    for task in removed:
        path = getattr(task, "result_path", None)
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


@pytest.fixture(autouse=True)
def _no_live_height_enhancement(request, monkeypatch):
    """Keep POST /api/cities offline: height enhancement queries lidar and raster services.

    Tests of the enhancement itself, and integration tests, get the real thing.
    """
    if "test_city_height_enhance" in request.node.nodeid or request.node.get_closest_marker("integration"):
        return
    import app.server.core.city_data as city_data
    monkeypatch.setattr(city_data, "enhance_city_data", lambda result, *a, **k: result)
