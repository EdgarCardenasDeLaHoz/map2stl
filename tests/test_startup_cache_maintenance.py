"""Server startup prunes the cache unless ``MAP2STL_NO_CACHE_MAINTENANCE`` is set."""
import time

from fastapi.testclient import TestClient

from app.server import server


def _start(monkeypatch, env):
    calls = []
    monkeypatch.setattr(server, "prune_all_caches", lambda: calls.append("prune") or {})
    monkeypatch.setattr(server, "migrate_osm_plain_json", lambda *_: calls.append("migrate"))
    if env is None:
        monkeypatch.delenv("MAP2STL_NO_CACHE_MAINTENANCE", raising=False)
    else:
        monkeypatch.setenv("MAP2STL_NO_CACHE_MAINTENANCE", env)
    with TestClient(server.app):
        time.sleep(0.5)  # maintenance runs in an executor thread
    return calls


def test_startup_prunes_by_default(monkeypatch):
    assert "prune" in _start(monkeypatch, None)


def test_switch_skips_pruning_and_migration(monkeypatch):
    assert _start(monkeypatch, "1") == []


def test_zero_keeps_maintenance_on(monkeypatch):
    assert "prune" in _start(monkeypatch, "0")
