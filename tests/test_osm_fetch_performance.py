"""Performance guards found by the Amazon / Philadelphia end-to-end run (2026-10-02).

* boxes over OSM_LAKES_MAX_AREA_KM2 take lakes from the water mask, not OSM (the Amazon
  never finished; Lake George took 4+ min);
* the same lakes asked for twice at once are fetched once (Philadelphia fetched them twice);
* an Overpass mirror that failed a real query is tried last for a while;
* a slow lakes fetch is left out after LAKES_WAIT_S and cached when it finishes;
* mirror probes run in parallel and are remembered (2026-10-03, Miami: a dead mirror cost
  a 10 s probe timeout on every fetch).
"""
import threading
import time

import numpy as np
import pytest

import city2stl.fetch as fetch
import geo2stl.osm as osm
from geo2stl.water_layers import OSM_LAKES_MAX_AREA_KM2, make_lakes_source, water_mask_lakes


@pytest.fixture(autouse=True)
def _fresh_probes(monkeypatch):
    monkeypatch.setattr(osm, "_PROBED", {})


def test_large_boxes_take_lakes_from_the_water_mask():
    """User, 2026-10-03: OSM is not the best lake source for large boxes. Above
    OSM_LAKES_MAX_AREA_KM2 the lakes come from the open-water mask; small boxes keep OSM."""
    calls, masks = [], []
    provider = make_lakes_source(lambda *a: calls.append(a) or {"features": []},
                                 water_mask=lambda *a: masks.append(a) or np.zeros(a[-1]))
    base = np.zeros((10, 12))
    out = provider(15, -19, -45, -85, 600, {}, base=base)          # Amazon, ~16.7 M km2
    assert calls == [] and len(masks) == 1 and masks[0][-1] == base.shape
    assert out.shape == base.shape and not out.any()
    provider(37.19, 37.17, -3.59, -3.61, 600, {}, base=base)      # Granada, ~4 km2
    assert len(calls) == 1 and len(masks) == 1
    assert 100 <= OSM_LAKES_MAX_AREA_KM2 <= 2_000


def test_water_mask_lakes_flattens_lakes_not_rivers():
    """A lake (flat surface, as SRTM levels lakes) is flattened below its shore, also with
    a steep shore; a river (surface falling 70 m along it) is left to the rivers layer."""
    y, x = np.mgrid[0:60, 0:80]
    base = 100.0 + 1.0 * x                                    # a slope rising east, 1 m per px
    water = np.zeros_like(base)
    water[10:20, 30:36] = 1                                   # lake, 6 px wide
    base[10:20, 30:36] = 130.0                                # its flat surface
    base[9, 29:37] = base[20, 29:37] = 400.0                  # steep north and south shores
    water[40:43, 5:75] = 1                                    # river along the slope
    grid = water_mask_lakes(water, base, 37.2, 37.1, -3.5, -3.6, depth_m=2.0, smooth=1)
    assert (grid[11:19, 31:35] <= 0).all() and grid[11:19, 31:35].min() < 0
    assert not grid[40:43].any()
    assert not grid[water == 0].any()

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


def test_probes_run_in_parallel_and_are_remembered(monkeypatch):
    calls = []

    def get(url, **kw):
        calls.append(url)
        if "kumi" in url:
            time.sleep(0.3)
            raise TimeoutError("probe")

        class Ok:
            def raise_for_status(self):
                pass
        return Ok()

    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(osm, "_FAILED_AT", {})
    t0 = time.time()
    first = osm.healthy_overpass_endpoints()
    assert time.time() - t0 < 0.3 * len(osm.OVERPASS_ENDPOINTS)       # not one after the other
    assert not any("kumi" in e for e in first)
    n = len(calls)
    assert osm.healthy_overpass_endpoints() == first and len(calls) == n   # remembered
    monkeypatch.setattr(osm, "PROBE_MEMORY_S", 0.0)
    osm.healthy_overpass_endpoints()
    assert len(calls) == 2 * n                                             # probed again once stale


def test_slow_lakes_are_skipped_then_cached(monkeypatch):
    """Lake George, 2026-10-03: the preview waited 4+ min for OSM lakes."""
    store = {}
    monkeypatch.setattr("geo2stl.cache.read_osm_cache", lambda k: store.get(k))
    monkeypatch.setattr("geo2stl.cache.write_osm_cache", lambda k, v: store.__setitem__(k, v))

    def slow_fetch(n, s, e, w, layers):
        time.sleep(0.4)
        return {"lakes": {"type": "FeatureCollection", "features": [{"id": 7}]}}

    monkeypatch.setattr(fetch, "fetch_osm_data", slow_fetch)
    with pytest.raises(TimeoutError, match="left out"):
        fetch.fetch_osm_lakes(2.0, 1.0, 2.0, 1.0, wait_s=0.05)
    time.sleep(0.6)                                       # the fetch went on in the background
    assert fetch.fetch_osm_lakes(2.0, 1.0, 2.0, 1.0, wait_s=0.05)["features"] == [{"id": 7}]
