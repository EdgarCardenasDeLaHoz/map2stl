"""
Tests for city2stl.fetch mirror failover.

An empty region and a dead upstream are different answers, and the whole point of these tests is
that they never share a return value. During the 2026-08-30 Overpass outage Naples came back
HTTP 200 with zero buildings while Palermo, hitting the same outage at the same minute, correctly
failed; only the batch script's own sanity check caught the difference.
"""
import pytest

from city2stl import fetch as osm_fetch
from city2stl.cache_policy import CITY_PIPELINE_VERSION


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
        assert result["city_pipeline_version"] == CITY_PIPELINE_VERSION

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


def test_mirror_settings_bound_connect_separately_from_the_query():
    """A mirror that will not connect fails over in seconds, not the query budget."""
    from geo2stl.osm import CONNECT_TIMEOUT_S, use_overpass_endpoint

    ox = _FakeOx()
    use_overpass_endpoint(ox, "https://a.example/api", 300)
    assert ox.settings.requests_timeout == (CONNECT_TIMEOUT_S, 300)
    assert ox.settings.overpass_settings.format(maxsize="") == "[out:json][timeout:300]"
    assert ox.settings.overpass_rate_limit is False
    use_overpass_endpoint(ox, "https://overpass-api.de/api", 300)
    assert ox.settings.overpass_rate_limit is True

    def test_progress_reports_each_layer_fetching_then_done_or_failed(self, monkeypatch):
        self._slow_jobs(monkeypatch, fail="roads")
        events = []
        osm_fetch._fetch_layers(None, (0, 0, 1, 1), ["buildings", "roads"], 0.0, 0.0, 0.0,
                                progress=lambda layer, state: events.append((layer, state)))
        assert ("buildings", "fetching") in events and ("buildings", "done") in events
        assert ("roads", "fetching") in events and ("roads", "failed") in events
        assert events.index(("roads", "fetching")) < events.index(("roads", "failed"))

    def test_cancel_skips_layers_not_started_and_raises(self, monkeypatch):
        self._slow_jobs(monkeypatch)
        started = []

        def progress(layer, state):
            if state == "fetching":
                started.append(layer)

        with pytest.raises(osm_fetch.FetchCancelled):
            osm_fetch._fetch_layers(None, (0, 0, 1, 1), self.ALL, 0.0, 0.0, 0.0,
                                    progress=progress, should_cancel=lambda: len(started) >= 1)
        assert len(started) < len(self.ALL)


class TestFetchHooks:
    """``fetch_osm_data`` names the mirror of each pass and checks for cancellation."""

    def test_on_mirror_reports_each_pass(self, three_mirrors, monkeypatch):
        mirrors_seen = []
        calls = iter([{"buildings": _failed_fc()}, {"buildings": _full_fc()}])

        def fake(ox, bbox, layers, tol_deg, simplify_tolerance, min_area, **hooks):
            return next(calls)

        monkeypatch.setattr(osm_fetch, "_fetch_layers", fake)
        osm_fetch.fetch_osm_data(37.11, 37.10, -3.59, -3.60, ["buildings"],
                                 on_mirror=mirrors_seen.append)
        assert mirrors_seen == three_mirrors[:2]

    def test_cancel_before_first_pass(self, three_mirrors, monkeypatch):
        monkeypatch.setattr(osm_fetch, "_fetch_layers",
                            lambda *a, **k: pytest.fail("no pass after cancel"))
        with pytest.raises(osm_fetch.FetchCancelled):
            osm_fetch.fetch_osm_data(37.11, 37.10, -3.59, -3.60, ["buildings"],
                                     should_cancel=lambda: True)


class TestHostPin:
    """osmnx pins the Overpass host to its IPv4 address (``_http._config_dns``); from a network
    where IPv4 to overpass-api.de is down and IPv6 works (2026-10-08) every query timed out.
    ``geo2stl.osm.pin_overpass_host`` pins the first address that connects instead."""

    V4 = "192.0.2.10"
    V6 = "2001:db8::10"

    @pytest.fixture
    def resolver(self, monkeypatch):
        """A mocked resolver: overpass-api.de has one IPv4 and one IPv6 address, IPv4 first."""
        import socket

        from geo2stl import osm

        calls = []

        def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            calls.append((host, family))
            if host == "overpass-api.de":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.V4, port)),
                        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (self.V6, port, 0, 0))]
            if host == self.V4:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.V4, port))]
            if host == self.V6:
                if family == socket.AF_INET:
                    raise socket.gaierror("address family mismatch")
                return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (self.V6, port, 0, 0))]
            raise socket.gaierror(host)

        monkeypatch.setattr(osm, "_original_getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr(osm, "_PINS", {})
        return calls

    def test_ipv4_timeout_pins_the_ipv6_address(self, resolver, monkeypatch):
        import socket

        from geo2stl import osm

        tried = []

        def connects(sockaddr, family, timeout_s):
            tried.append(sockaddr[0])
            return family == socket.AF_INET6   # IPv4 times out, IPv6 answers

        monkeypatch.setattr(osm, "_tcp_connects", connects)
        osm.pin_overpass_host("https://overpass-api.de/api")
        assert tried == [self.V4, self.V6]
        assert osm._PINS["overpass-api.de"][1] == self.V6
        got = osm._pinned_getaddrinfo("overpass-api.de", 443, 0, socket.SOCK_STREAM)
        assert [info[4][0] for info in got] == [self.V6]
        # an AF_INET-only caller cannot use the IPv6 pin: it gets the name resolved as usual
        got4 = osm._pinned_getaddrinfo("overpass-api.de", 443, socket.AF_INET)
        assert got4[0][4][0] == self.V4
        # other hosts pass straight through
        with pytest.raises(socket.gaierror):
            osm._pinned_getaddrinfo("elsewhere.example", 443)

    def test_working_ipv4_is_kept_and_remembered(self, resolver, monkeypatch):
        from geo2stl import osm

        tried = []
        monkeypatch.setattr(osm, "_tcp_connects", lambda sa, fam, t: tried.append(sa[0]) or True)
        osm.pin_overpass_host("https://overpass-api.de/api")
        osm.pin_overpass_host("https://overpass-api.de/api")   # remembered: no second probe
        assert tried == [self.V4]
        assert osm._PINS["overpass-api.de"][1] == self.V4

    def test_nothing_connects_leaves_the_host_unpinned(self, resolver, monkeypatch):
        from geo2stl import osm

        monkeypatch.setattr(osm, "_tcp_connects", lambda sa, fam, t: False)
        osm.pin_overpass_host("https://overpass-api.de/api")
        assert osm._PINS["overpass-api.de"][1] is None
        resolver.clear()
        osm._pinned_getaddrinfo("overpass-api.de", 443)
        assert resolver == [("overpass-api.de", 0)]

    def test_a_failed_query_drops_the_pin(self, resolver, monkeypatch):
        from geo2stl import osm

        monkeypatch.setattr(osm, "_tcp_connects", lambda sa, fam, t: True)
        monkeypatch.setattr(osm, "_FAILED_AT", {})
        osm.pin_overpass_host("https://overpass-api.de/api")
        osm.mark_overpass_failure("https://overpass-api.de/api")
        assert "overpass-api.de" not in osm._PINS

    def test_install_replaces_the_osmnx_ipv4_pin(self, monkeypatch):
        import socket
        import types

        from geo2stl import osm

        def osmnx_original(*a, **k):
            return []

        def _config_dns(url):  # osmnx's own pin
            pass

        http = types.SimpleNamespace(_config_dns=_config_dns,
                                     _original_getaddrinfo=osmnx_original)
        fake_ox = types.SimpleNamespace(_http=http, settings=_FakeSettings())
        monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)   # restored afterwards
        monkeypatch.setattr(osm, "_original_getaddrinfo", osm._original_getaddrinfo)
        osm.use_overpass_endpoint(fake_ox, "https://overpass-api.de/api", 300)
        assert http._config_dns is osm.pin_overpass_host
        assert socket.getaddrinfo is osm._pinned_getaddrinfo
        assert osm._original_getaddrinfo is osmnx_original
        osm.install_dns_pin(fake_ox)   # idempotent: never wraps itself
        assert osm._original_getaddrinfo is osmnx_original

    def test_stand_in_osmnx_without_http_is_left_alone(self):
        from geo2stl import osm

        ox = _FakeOx()
        osm.install_dns_pin(ox)   # no _http: nothing to patch, no error
        assert not hasattr(ox, "_http")
