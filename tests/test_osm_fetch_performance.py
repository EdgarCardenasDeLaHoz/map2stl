"""Performance guards found by the Amazon / Philadelphia end-to-end run (2026-10-02).

* the OSM lakes layer is skipped for continent-sized boxes (Amazon's preview never finished);
* the same lakes asked for twice at once are fetched once (Philadelphia fetched them twice);
* an Overpass mirror that failed a real query is tried last for a while.
"""
import threading
import time

import numpy as np

import city2stl.fetch as fetch
import geo2stl.osm as osm
from geo2stl.water_layers import LAKES_MAX_AREA_KM2, make_lakes_source


def test_lakes_skipped_for_continent_sized_boxes():
    calls = []
    provider = make_lakes_source(lambda *a: calls.append(a) or {"features": []})
    base = np.zeros((10, 12))
    out = provider(15, -19, -45, -85, 600, {}, base=base)          # Amazon, ~16.7 M km²
    assert calls == []
    assert out.shape == base.shape and not out.any()
    provider(37.19, 37.17, -3.59, -3.61, 600, {}, base=base)      # Granada, ~4 km²
    assert len(calls) == 1
    assert LAKES_MAX_AREA_KM2 >= 1_000


def test_lakes_fetched_once_when_asked_twice_at_once(monkeypatch):
    store = {}
    monkeypatch.setattr("geo2stl.cache.read_osm_cache", lambda k: store.get(k))
    monkeypatch.setattr("geo2stl.cache.write_osm_cache", lambda k, v: store.__setitem__(k, v))
    calls = []

    def slow_fetch(n, s, e, w, layers):
        calls.append(layers)
        time.sleep(0.3)
        return {"lakes": {"type": "FeatureCollection", "features": [{"id": 1}]}}

    monkeypatch.setattr(fetch, "fetch_osm_data", slow_fetch)
    results = []
    threads = [threading.Thread(target=lambda: results.append(fetch.fetch_osm_lakes(1.0, 0.0, 1.0, 0.0)))
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert all(r["features"] == [{"id": 1}] for r in results)


def test_mirror_that_failed_a_query_is_tried_last(monkeypatch):
    class Ok:
        def raise_for_status(self):
            pass

    monkeypatch.setattr("requests.get", lambda *a, **k: Ok())
    monkeypatch.setattr(osm, "_FAILED_AT", {})
    first = osm.OVERPASS_ENDPOINTS[0]
    assert osm.healthy_overpass_endpoints()[0] == first
    osm.mark_overpass_failure(first)
    order = osm.healthy_overpass_endpoints()
    assert order[-1] == first and len(order) == len(osm.OVERPASS_ENDPOINTS)
    monkeypatch.setattr(osm, "FAILURE_MEMORY_S", 0.0)
    assert osm.healthy_overpass_endpoints()[0] == first
