"""
Tests for city2stl.fetch mirror failover.

An empty region and a dead upstream are different answers, and the whole point of these tests is
that they never share a return value. During the 2026-08-30 Overpass outage Naples came back
HTTP 200 with zero buildings while Palermo, hitting the same outage at the same minute, correctly
failed; only the batch script's own sanity check caught the difference.
"""
import pytest

from city2stl import fetch as osm_fetch


class _FakeSettings:
    overpass_url = ""
    overpass_rate_limit = False
    requests_timeout = 0


class _FakeOx:
    """Stands in for the osmnx module inside fetch_osm_data."""

    def __init__(self):
        self.settings = _FakeSettings()


@pytest.fixture
def three_mirrors(monkeypatch):
    """Three healthy endpoints, and an osmnx that never touches the network."""
    mirrors = ["https://a.example/api", "https://b.example/api", "https://c.example/api"]
    monkeypatch.setattr(osm_fetch, "_healthy_overpass_endpoints", lambda: mirrors)
    monkeypatch.setitem(__import__("sys").modules, "osmnx", _FakeOx())
    return mirrors


def _layers(monkeypatch, per_call):
    """Make _fetch_layers return *per_call* results in order, recording the mirror used."""
    seen = []
    calls = iter(per_call)

    def fake(ox, bbox, layers, tol_deg, simplify_tolerance, min_area):
        seen.append(ox.settings.overpass_url)
        return next(calls)

    monkeypatch.setattr(osm_fetch, "_fetch_layers", fake)
    return seen


def _failed_fc(msg="502 Bad Gateway"):
    return {"type": "FeatureCollection", "features": [], "error": msg}


def _empty_fc():
    return {"type": "FeatureCollection", "features": []}


def _full_fc():
    return {"type": "FeatureCollection", "features": [{"type": "Feature"}]}


class TestMirrorExhaustion:

    def test_raises_when_every_mirror_fails(self, three_mirrors, monkeypatch):
        """The Naples case: three dead mirrors must not read as an empty city."""
        seen = _layers(monkeypatch, [{"buildings": _failed_fc()} for _ in three_mirrors])

        with pytest.raises(osm_fetch.OverpassUpstreamError) as excinfo:
            osm_fetch.fetch_osm_data(37.11, 37.10, -3.59, -3.60, ["buildings"])

        assert seen == three_mirrors, "every mirror should have been tried"
        assert "buildings" in str(excinfo.value)

    def test_second_mirror_rescues_the_fetch(self, three_mirrors, monkeypatch):
        seen = _layers(monkeypatch, [{"buildings": _failed_fc()},
                                     {"buildings": _full_fc()}])

        result = osm_fetch.fetch_osm_data(37.11, 37.10, -3.59, -3.60, ["buildings"])

        assert len(seen) == 2, "should stop at the first mirror that answers"
        assert result["buildings"]["features"]
        assert result["city_pipeline_version"] == 2

    def test_genuinely_empty_region_is_not_a_failure(self, three_mirrors, monkeypatch):
        """An empty collection with no error key is a real answer; do not retry, do not raise."""
        seen = _layers(monkeypatch, [{"buildings": _empty_fc()}])

        result = osm_fetch.fetch_osm_data(37.11, 37.10, -3.59, -3.60, ["buildings"])

        assert len(seen) == 1, "an honest empty answer costs one mirror, not three"
        assert result["buildings"]["features"] == []

    def test_only_the_requested_layers_can_fail_the_fetch(self, three_mirrors, monkeypatch):
        """Plenty of bboxes have no city walls; a failing extra layer is not worth raising over."""
        _layers(monkeypatch, [{"buildings": _full_fc(), "walls": _failed_fc()}])

        result = osm_fetch.fetch_osm_data(37.11, 37.10, -3.59, -3.60, ["buildings"])

        assert result["buildings"]["features"]

    def test_retry_refetches_only_the_failed_layers(self, three_mirrors, monkeypatch):
        """A layer the first mirror served is kept, not fetched again from the next."""
        requested = []

        def fake(ox, bbox, layers, tol_deg, simplify_tolerance, min_area):
            requested.append((ox.settings.overpass_url, list(layers)))
            if len(requested) == 1:
                return {"buildings": _failed_fc(), "roads": _full_fc()}
            return {"buildings": _full_fc()}

        monkeypatch.setattr(osm_fetch, "_fetch_layers", fake)
        result = osm_fetch.fetch_osm_data(37.11, 37.10, -3.59, -3.60, ["buildings", "roads"])

        assert requested == [(three_mirrors[0], ["buildings", "roads"]),
                             (three_mirrors[1], ["buildings"])]
        assert result["buildings"]["features"] and result["roads"]["features"]

    def test_no_healthy_mirror_raises_before_querying(self, monkeypatch):
        monkeypatch.setattr(osm_fetch, "_healthy_overpass_endpoints", lambda: [])
        monkeypatch.setitem(__import__("sys").modules, "osmnx", _FakeOx())

        with pytest.raises(RuntimeError, match="/status probe"):
            osm_fetch.fetch_osm_data(37.11, 37.10, -3.59, -3.60, ["buildings"])


def test_status_probe_sends_the_osmnx_user_agent(monkeypatch):
    """overpass-api.de answers python-requests' default user agent with 406."""
    import osmnx as ox
    import requests

    sent = []

    class _Ok:
        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        sent.append((headers or {}).get("User-Agent"))
        return _Ok()

    monkeypatch.setattr(requests, "get", fake_get)
    assert osm_fetch._healthy_overpass_endpoints() == osm_fetch._OVERPASS_ENDPOINTS
    assert sent and all(ua == ox.settings.http_user_agent for ua in sent)


class TestConcurrentLayers:
    """``_fetch_layers`` runs the layers in parallel, bounded, and reports each one."""

    ALL = ["buildings", "roads", "waterways", "pois", "walls", "towers",
           "churches", "fortifications", "green"]

    @staticmethod
    def _slow_jobs(monkeypatch, fail=None, raise_in=None):
        import threading
        import time

        state = {"now": 0, "peak": 0, "threads": set()}
        lock = threading.Lock()

        def job(name):
            def run():
                with lock:
                    state["now"] += 1
                    state["peak"] = max(state["peak"], state["now"])
                    state["threads"].add(threading.get_ident())
                time.sleep(0.05)
                with lock:
                    state["now"] -= 1
                if name == raise_in:
                    raise RuntimeError("boom")
                return _failed_fc() if name == fail else {
                    "type": "FeatureCollection", "features": [{"type": "Feature", "id": name}]}
            return run

        monkeypatch.setattr(
            osm_fetch, "_layer_jobs",
            lambda *a, **k: {name: job(name) for name in TestConcurrentLayers.ALL})
        return state

    def test_all_layers_fetched_at_most_three_at_once(self, monkeypatch):
        state = self._slow_jobs(monkeypatch)
        result = osm_fetch._fetch_layers(None, (0, 0, 1, 1), self.ALL, 0.0, 0.0, 0.0)

        assert list(result) == self.ALL, "results keep the fixed layer order"
        assert all(result[n]["features"][0]["id"] == n for n in self.ALL)
        assert state["peak"] == osm_fetch._MAX_CONCURRENT_LAYERS
        assert len(state["threads"]) > 1

    def test_only_requested_layers_are_fetched(self, monkeypatch):
        self._slow_jobs(monkeypatch)
        result = osm_fetch._fetch_layers(None, (0, 0, 1, 1), ["roads", "buildings"],
                                         0.0, 0.0, 0.0)
        assert list(result) == ["buildings", "roads"]

    def test_a_raising_layer_is_reported_without_losing_the_others(self, monkeypatch):
        self._slow_jobs(monkeypatch, fail="roads", raise_in="walls")
        result = osm_fetch._fetch_layers(None, (0, 0, 1, 1), self.ALL, 0.0, 0.0, 0.0)

        assert "boom" in result["walls"]["error"]
        assert osm_fetch._layers_failed(result, self.ALL) == ["roads", "walls"]
        assert result["buildings"]["features"]


class TestEmptyVersusFailed:
    """The layer fetchers have to tag their own empties, or failover cannot tell them apart."""

    def test_insufficient_response_carries_no_error_key(self, monkeypatch):
        from osmnx._errors import InsufficientResponseError

        class Ox:
            def features_from_bbox(self, bbox, tags):
                raise InsufficientResponseError("no matching features")

        fc = osm_fetch._fetch_buildings(Ox(), (0, 0, 1, 1), 0.0, 0.0, 0.0)
        assert fc["features"] == []
        assert "error" not in fc
        assert osm_fetch._layers_failed({"buildings": fc}, ["buildings"]) == []

    def test_upstream_error_carries_an_error_key(self, monkeypatch):
        class Ox:
            def features_from_bbox(self, bbox, tags):
                raise RuntimeError("502 Bad Gateway")

        fc = osm_fetch._fetch_buildings(Ox(), (0, 0, 1, 1), 0.0, 0.0, 0.0)
        assert fc["features"] == []
        assert "502" in fc["error"]
        assert osm_fetch._layers_failed({"buildings": fc}, ["buildings"]) == ["buildings"]


class TestBuildingPartSubQuery:
    """A bbox with buildings but no ``building:part`` must keep its buildings.

    ``_fetch_buildings`` runs two queries and merges them. osmnx raises rather than returning an
    empty frame when one matches nothing, and most bboxes have no ``building:part`` at all, so
    the raise used to reach the layer's catch-all and discard the footprints the other query had
    already found -- reported as an empty region, cached as one, and exported as one.
    """

    @staticmethod
    def _ox_with(base=None, part=None):
        from osmnx._errors import InsufficientResponseError

        class Ox:
            def features_from_bbox(self, bbox, tags):
                gdf = base if "building" in tags else part
                if gdf is None:
                    raise InsufficientResponseError("No matching features")
                return gdf

        return Ox()

    @staticmethod
    def _one_square():
        import geopandas as gpd
        from shapely.geometry import Polygon
        return gpd.GeoDataFrame(
            {"building": ["yes"]},
            geometry=[Polygon([(0, 0), (0, 1e-4), (1e-4, 1e-4), (1e-4, 0)])],
            crs="EPSG:4326",
        )

    def test_missing_building_part_keeps_the_buildings(self):
        ox = self._ox_with(base=self._one_square(), part=None)
        fc = osm_fetch._fetch_buildings(ox, (0, 0, 1, 1), 0.0, 0.0, 0.0)
        assert len(fc["features"]) == 1
        assert "error" not in fc

    def test_neither_query_matches_is_empty_without_an_error(self):
        ox = self._ox_with(base=None, part=None)
        fc = osm_fetch._fetch_buildings(ox, (0, 0, 1, 1), 0.0, 0.0, 0.0)
        assert fc["features"] == []
        assert "error" not in fc, "an empty region must not trigger mirror failover"
