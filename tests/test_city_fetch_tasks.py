"""
Background city fetch: POST /api/cities/start, GET /api/cities/status/{id},
GET /api/cities/result/{id}, POST /api/cities/cancel/{id}.

``fetch_osm_data`` is replaced in every test; no Overpass request is made.
"""
import threading
import time
from unittest.mock import patch

import pytest

BBOX = {"north": 39.960, "south": 39.950, "east": -75.140, "west": -75.170}
LAYERS = ["buildings", "roads", "waterways"]


def _fc(n=1):
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": None,
                          "properties": {"height_source": "osm_height"}}] * n}


def _wait(client, task_id, until=("done", "error", "cancelled"), timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/cities/status/{task_id}").json()
        if body["status"] in until:
            return body
        time.sleep(0.02)
    pytest.fail(f"task {task_id} still {body['status']}")


@pytest.fixture(autouse=True)
def _no_height_enhancement():
    with patch("app.server.core.city_data.enhance_city_data", side_effect=lambda d, *a: d):
        yield


def test_start_reports_layers_then_result_matches_sync_payload(client, tmp_data_dir):
    def fake_fetch(n, s, e, w, layers, tol, min_area, *, progress=None, on_mirror=None,
                   should_cancel=None):
        on_mirror("https://mirror.example/api")
        out = {}
        for name in layers:
            progress(name, "fetching")
            out[name] = _fc()
            progress(name, "done")
        return out

    with patch("app.server.core.city_data.fetch_osm_data", side_effect=fake_fetch):
        start = client.post("/api/cities/start", json={**BBOX, "layers": LAYERS})
        assert start.status_code == 200
        body = start.json()
        assert body["status"] in ("running", "done")
        assert [lyr["name"] for lyr in body["layers"]] == LAYERS
        final = _wait(client, body["task_id"])

    assert final["status"] == "done"
    assert {lyr["state"] for lyr in final["layers"]} == {"done"}
    assert final["mirror"] == "https://mirror.example/api"
    assert final["elapsed_s"] >= 0
    result = client.get(f"/api/cities/result/{body['task_id']}").json()
    for name in LAYERS:
        assert result[name]["type"] == "FeatureCollection"
    assert result["diagonal_km"] > 0


def test_cached_layers_report_cached_without_fetching(client, tmp_data_dir):
    with patch("app.server.core.city_data.fetch_osm_data",
               return_value={name: _fc() for name in LAYERS}):
        client.post("/api/cities", json={**BBOX, "layers": LAYERS})   # warm the cache
    with patch("app.server.core.city_data.fetch_osm_data",
               side_effect=AssertionError("must not fetch")):
        task_id = client.post("/api/cities/start", json={**BBOX, "layers": LAYERS}).json()["task_id"]
        final = _wait(client, task_id)
    assert final["status"] == "done"
    assert {lyr["state"] for lyr in final["layers"]} == {"cached"}


def test_upstream_failure_marks_unfinished_layers_failed(client, tmp_data_dir):
    def boom(*a, progress=None, **k):
        progress("buildings", "fetching")
        raise RuntimeError("All 2 Overpass mirror(s) failed")

    with patch("app.server.core.city_data.fetch_osm_data", side_effect=boom):
        task_id = client.post("/api/cities/start", json={**BBOX, "layers": LAYERS}).json()["task_id"]
        final = _wait(client, task_id)
    assert final["status"] == "error"
    assert "Overpass" in final["error"]
    assert {lyr["state"] for lyr in final["layers"]} == {"failed"}
    assert client.get(f"/api/cities/result/{task_id}").status_code == 409


def test_cancel_stops_remaining_layers_and_discards_result(client, tmp_data_dir):
    from city2stl.fetch import FetchCancelled

    first_started = threading.Event()
    release = threading.Event()
    fetched = []

    def slow_fetch(n, s, e, w, layers, tol, min_area, *, progress=None, on_mirror=None,
                   should_cancel=None):
        out = {}
        for name in layers:
            if should_cancel():
                raise FetchCancelled("cancelled")
            progress(name, "fetching")
            first_started.set()
            release.wait(2)
            fetched.append(name)
            out[name] = _fc()
            progress(name, "done")
        return out

    with patch("app.server.core.city_data.fetch_osm_data", side_effect=slow_fetch):
        task_id = client.post("/api/cities/start", json={**BBOX, "layers": LAYERS}).json()["task_id"]
        assert first_started.wait(2)
        cancelled = client.post(f"/api/cities/cancel/{task_id}").json()
        release.set()
        assert cancelled["status"] == "cancelled"
        states = {lyr["name"]: lyr["state"] for lyr in cancelled["layers"]}
        assert states["roads"] == "cancelled" and states["waterways"] == "cancelled"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(
                t.name == f"city-fetch-{task_id}" for t in threading.enumerate()):
            time.sleep(0.02)

    assert fetched == ["buildings"], "layers after the cancel are never fetched"
    assert client.get(f"/api/cities/status/{task_id}").json()["status"] == "cancelled"
    assert client.get(f"/api/cities/result/{task_id}").status_code == 409


def test_identical_running_request_joins_the_existing_task(client, tmp_data_dir):
    release = threading.Event()

    def slow_fetch(*a, **k):
        release.wait(2)
        return {name: _fc() for name in LAYERS}

    with patch("app.server.core.city_data.fetch_osm_data", side_effect=slow_fetch):
        a = client.post("/api/cities/start", json={**BBOX, "layers": LAYERS}).json()
        b = client.post("/api/cities/start", json={**BBOX, "layers": LAYERS}).json()
        release.set()
        _wait(client, a["task_id"])
    assert a["task_id"] == b["task_id"]


def test_size_guard_and_unknown_ids(client):
    big = client.post("/api/cities/start",
                      json={"north": 60.0, "south": 50.0, "east": 30.0, "west": 10.0})
    assert big.status_code == 422
    assert client.get("/api/cities/status/nope").status_code == 404
    assert client.get("/api/cities/result/nope").status_code == 404
    assert client.post("/api/cities/cancel/nope").status_code == 404
