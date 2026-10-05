"""
Shared pytest fixtures for map2stl API tests.

Sets MAP2STL_TEST_MODE=1 so the DEM endpoint returns a fast deterministic
response without any Earth Engine or network calls.
"""
import os
from pathlib import Path

import pytest

# Enable test mode before importing the app
os.environ["MAP2STL_TEST_MODE"] = "1"


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
    (e.g. `import app.server.routers.regions`, not `map2stl.ui.routers.regions`)
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
    # mesh_import binds its own CACHE_ROOT name (uploads go to CACHE_ROOT/mesh_imports);
    # without this, every client test that uploads a mesh (test_landmarks_api.py)
    # left a folder in the real cache/mesh_imports.
    import app.server.core.mesh_import as mesh_import_module
    monkeypatch.setattr(mesh_import_module, "CACHE_ROOT", test_cache_root)

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


class NetworkBlocked(OSError):
    """Raised for an outbound connection from a test not marked ``integration``."""


def _proxy_addresses() -> set[tuple[str, int]]:
    """(host, port) of every configured HTTP(S) proxy. In a cloud session the
    proxy listens on 127.0.0.1, so "loopback is local" alone would let traffic out."""
    from urllib.parse import urlsplit

    out = set()
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        url = os.environ.get(var)
        if url:
            parts = urlsplit(url)
            if parts.hostname and parts.port:
                out.add((parts.hostname, parts.port))
    return out


@pytest.fixture(autouse=True)
def _block_network(request):
    """No outbound connections unless the test is marked ``integration`` or
    ``requires_network`` (those are skipped offline, see ``pytest_collection_modifyitems``).

    The default run must not depend on the internet: two skyline report tests
    used to fetch 192 live ESRI tiles (~50 s; audit 2026-10-05). Loopback
    stays allowed (live-server tests), except a proxy listening there.
    """
    if (request.node.get_closest_marker("integration")
            or request.node.get_closest_marker("requires_network")):
        yield
        return
    import socket

    proxies = _proxy_addresses()
    proxy_vars = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY")
    saved_env = {v: os.environ.pop(v) for v in proxy_vars if v in os.environ}
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
            host, port = address[0], address[1]
            local = host in ("127.0.0.1", "::1", "localhost")
            if not local or (host, port) in proxies:
                raise NetworkBlocked(
                    f"network access to {host}:{port} in {request.node.nodeid}; "
                    "stub it, or mark the test @pytest.mark.requires_network")

    def connect(sock, address):
        _check(sock, address)
        return real_connect(sock, address)

    def connect_ex(sock, address):
        _check(sock, address)
        return real_connect_ex(sock, address)

    # Patched by hand, not with ``monkeypatch``: requesting that fixture here
    # would make it outlive other fixtures' teardown, so a test's own patches
    # (e.g. a fake ``time.monotonic``) would still be live during it.
    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex = real_connect, real_connect_ex
        os.environ.update(saved_env)


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
def _isolate_disk_cache(request, tmp_path):
    """Every test gets an empty disk cache (``geo2stl.cache.CACHE_ROOT``).

    Library code now caches by default (trails, city-model layers and terrain), so
    a test that never asked for ``tmp_data_dir`` would otherwise read results a
    previous run left in ``Code/cache/`` and write its fakes there. Integration
    tests keep the real cache. ``tmp_data_dir`` (requested explicitly, so set up
    after this autouse fixture) redirects it again, to its own folder.

    Set and restored by hand, not with ``monkeypatch``: requesting it here would
    set it up before ``_isolate_export_tasks`` and so undo a test's patches only
    after that fixture's teardown ran with them (a patched ``time.monotonic``).
    """
    if request.node.get_closest_marker("integration"):
        yield
        return
    import geo2stl.cache as cache_module

    saved = cache_module.CACHE_ROOT
    cache_module.CACHE_ROOT = tmp_path / "_isolated_cache"
    yield
    cache_module.CACHE_ROOT = saved


@pytest.fixture(autouse=True)
def _no_live_height_enhancement(request, monkeypatch):
    """Keep POST /api/cities offline: height enhancement queries lidar and raster services.

    Tests of the enhancement itself, and integration tests, get the real thing.
    """
    if "test_city_height_enhance" in request.node.nodeid or request.node.get_closest_marker("integration"):
        return
    import app.server.core.city_data as city_data
    monkeypatch.setattr(city_data, "enhance_city_data", lambda result, *a, **k: result)


# ---------------------------------------------------------------------------
# Local-only resources (cloud sessions, fresh clones): see scripts/cloud-setup.sh.
# A test marked requires_cache / requires_gpu / requires_network / requires_keys is skipped,
# with the reason, when this machine lacks the resource; integration tests (live calls) also
# count as requires_network. The default run needs none of them.
# ---------------------------------------------------------------------------
_RESOURCES: dict = {}


def _have(resource: str) -> bool:
    if resource in _RESOURCES:
        return _RESOURCES[resource]
    ok = False
    if resource == "cache":
        root = Path(os.environ.get("MAP2STL_CACHE") or Path(__file__).resolve().parents[1] / "cache")
        ok = root.is_dir() and any(root.iterdir())
    elif resource == "gpu":
        try:
            import torch
            ok = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001 - no torch or a broken CUDA install: no GPU
            ok = False
    elif resource == "network":
        import socket
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=2).close()
            ok = True
        except OSError:
            ok = False
    elif resource == "keys":
        root = Path(__file__).resolve().parents[1]
        env = root / ".env"
        ok = bool(os.environ.get("GOOGLE_MAPS_API_KEY")
                  or (env.is_file() and "GOOGLE_MAPS_API_KEY" in env.read_text(errors="ignore"))
                  or (root / "config.json").is_file())
    _RESOURCES[resource] = ok
    return ok


def pytest_collection_modifyitems(config, items):
    reasons = {"cache": "no local cache (cache/ or MAP2STL_CACHE)", "gpu": "no CUDA GPU",
               "network": "no network", "keys": "no API keys (.env / config.json)"}
    for item in items:
        need = {m.name.removeprefix("requires_") for m in item.iter_markers()
                if m.name.startswith("requires_")}
        if item.get_closest_marker("integration"):
            need.add("network")
        for r in sorted(need):
            if r in reasons and not _have(r):
                item.add_marker(pytest.mark.skip(reason=reasons[r]))
                break
