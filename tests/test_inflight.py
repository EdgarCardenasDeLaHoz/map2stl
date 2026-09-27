"""Tests for app.server.core.inflight.dedupe (in-flight request sharing)."""

import asyncio
import gc
import logging
import os

import pytest

from app.server.core.inflight import dedupe


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=5))


def test_concurrent_callers_share_one_run():
    calls = 0

    async def main():
        registry: dict = {}
        gate = asyncio.Event()

        async def factory():
            nonlocal calls
            calls += 1
            await gate.wait()
            return {"value": 42}

        first = asyncio.create_task(dedupe(registry, "k", factory))
        await asyncio.sleep(0)
        joiners = [asyncio.create_task(dedupe(registry, "k", factory)) for _ in range(3)]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(first, *joiners)
        assert registry == {}
        return results

    results = _run(main())
    assert calls == 1
    assert all(r == {"value": 42} for r in results)


def test_exception_propagates_to_joiners():
    async def main():
        registry: dict = {}
        gate = asyncio.Event()

        async def factory():
            await gate.wait()
            raise ValueError("boom")

        first = asyncio.create_task(dedupe(registry, "k", factory))
        await asyncio.sleep(0)
        joiner = asyncio.create_task(dedupe(registry, "k", factory))
        await asyncio.sleep(0)
        gate.set()
        res = await asyncio.gather(first, joiner, return_exceptions=True)
        assert registry == {}
        return res

    res = _run(main())
    assert all(isinstance(r, ValueError) and str(r) == "boom" for r in res)


def test_first_caller_cancellation_does_not_hang_joiners():
    async def main():
        registry: dict = {}

        async def factory():
            await asyncio.sleep(3600)

        first = asyncio.create_task(dedupe(registry, "k", factory))
        await asyncio.sleep(0)
        joiner = asyncio.create_task(dedupe(registry, "k", factory))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(joiner, timeout=1)
        assert registry == {}

        # A retry after cancellation starts a fresh run.
        async def ok():
            return 7

        assert await dedupe(registry, "k", ok) == 7

    _run(main())


def test_cancelled_joiner_does_not_cancel_shared_run():
    async def main():
        registry: dict = {}
        gate = asyncio.Event()

        async def factory():
            await gate.wait()
            return "done"

        first = asyncio.create_task(dedupe(registry, "k", factory))
        await asyncio.sleep(0)
        joiner = asyncio.create_task(dedupe(registry, "k", factory))
        await asyncio.sleep(0)
        joiner.cancel()
        await asyncio.sleep(0)
        gate.set()
        assert await asyncio.wait_for(first, timeout=1) == "done"

    _run(main())


def test_unjoined_exception_is_not_logged_as_unretrieved(caplog):
    async def main():
        registry: dict = {}

        async def factory():
            raise RuntimeError("solo")

        with pytest.raises(RuntimeError):
            await dedupe(registry, "k", factory)

    with caplog.at_level(logging.ERROR, logger="asyncio"):
        _run(main())
        gc.collect()
    assert "never retrieved" not in caplog.text


def test_test_mode_skips_server_log_file_handler():
    from logging.handlers import RotatingFileHandler

    assert os.environ.get("MAP2STL_TEST_MODE") == "1"
    import app.server.server as srv

    assert not any(isinstance(h, RotatingFileHandler) for h in srv._log_handlers)


# ---------------------------------------------------------------------------
# Route-level: the non-TEST_MODE path of /api/terrain/{trails,hydrology}
# ---------------------------------------------------------------------------

_QS = "north=46.02&south=46.0&east=7.02&west=7.0&dim=16"


@pytest.fixture()
def live_terrain(monkeypatch):
    import app.server.routers.terrain as terrain

    monkeypatch.setattr(terrain, "TEST_MODE", False)
    monkeypatch.setattr(terrain, "read_array_cache", lambda *a, **k: None)
    monkeypatch.setattr(terrain, "write_array_cache", lambda *a, **k: None)
    return terrain


def test_trails_route_upstream_error_payload(client, live_terrain, monkeypatch):
    def boom(*a, **k):
        raise live_terrain.TrailsUpstreamError("overpass down")

    monkeypatch.setattr(live_terrain, "_fetch_and_rasterize_trails", boom)
    r = client.get(f"/api/terrain/trails?{_QS}")
    assert r.status_code == 200
    body = r.json()
    assert body["upstream_error"] is True
    assert body["ski_count"] == 0
    assert live_terrain._TRAILS_INFLIGHT == {}


def test_hydrology_route_error_and_empty(client, live_terrain, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("pipeline broke")

    monkeypatch.setattr(live_terrain, "_fetch_and_rasterize_hydrology", boom)
    r = client.get(f"/api/terrain/hydrology?{_QS}")
    assert r.status_code == 500
    assert r.json() == {"error": "Hydrology fetch failed"}
    assert live_terrain._HYDRO_INFLIGHT == {}

    monkeypatch.setattr(live_terrain, "_fetch_and_rasterize_hydrology",
                        lambda *a, **k: None)
    r = client.get(f"/api/terrain/hydrology?{_QS}")
    assert r.status_code == 200
    assert r.json()["error"] == "No rivers found in region"
