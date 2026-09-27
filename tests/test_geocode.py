"""
Landmark search and edge-landmark warnings (F-UX batch 2).

GET /api/geocode?q=               — geo2stl.geocode.search_places (Nominatim)
GET /api/geocode/edge-landmarks   — geo2stl.landmarks.edge_landmarks (Overpass)

Every network call is replaced: ``requests.get`` for Nominatim and
``geo2stl.osm.overpass_query`` for Overpass.
"""
from unittest.mock import patch

import pytest

from geo2stl import geocode, landmarks

NOMINATIM_HIT = {
    "place_id": 1, "osm_type": "way", "osm_id": 27000000,
    "lat": "37.1760", "lon": "-3.5881",
    "category": "tourism", "type": "attraction", "name": "Alhambra",
    "display_name": "Alhambra, Granada, Andalusia, Spain",
    "boundingbox": ["37.1735", "37.1790", "-3.5925", "-3.5835"],
}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture
def fake_nominatim(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers})
        return _Resp([NOMINATIM_HIT])

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(geocode, "MIN_INTERVAL_S", 0.0)
    return calls


# ---------------------------------------------------------------------------
# geo2stl.geocode
# ---------------------------------------------------------------------------

class TestSearchPlaces:
    def test_parses_jsonv2_and_identifies_itself(self, tmp_data_dir, fake_nominatim):
        results = geocode.search_places("Alhambra Granada")
        assert results == [{
            "name": "Alhambra", "display_name": "Alhambra, Granada, Andalusia, Spain",
            "lat": 37.176, "lon": -3.5881,
            "bbox": {"north": 37.179, "south": 37.1735, "east": -3.5835, "west": -3.5925},
            "class": "tourism", "type": "attraction", "osm_type": "way", "osm_id": 27000000,
        }]
        call = fake_nominatim[0]
        assert call["params"]["format"] == "jsonv2"
        assert call["headers"]["User-Agent"] == geocode.USER_AGENT
        assert "python-requests" not in call["headers"]["User-Agent"]

    def test_second_search_is_served_from_cache(self, tmp_data_dir, fake_nominatim):
        geocode.search_places("Alhambra")
        geocode.search_places("  alhambra ")   # same query after normalisation
        assert len(fake_nominatim) == 1

    def test_blank_query_makes_no_request(self, tmp_data_dir, fake_nominatim):
        assert geocode.search_places("   ") == []
        assert fake_nominatim == []

    def test_requests_are_spaced_at_least_the_minimum_interval(self, monkeypatch):
        sleeps = []
        clock = iter([100.0, 100.0, 100.2, 100.2])
        monkeypatch.setattr(geocode.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(geocode.time, "sleep", sleeps.append)
        monkeypatch.setattr(geocode, "_last_request", [0.0])
        geocode._wait_turn()
        geocode._wait_turn()
        assert sleeps == [pytest.approx(0.8)]


class TestGeocodeEndpoint:
    def test_returns_results(self, client, fake_nominatim):
        body = client.get("/api/geocode", params={"q": "Alhambra"}).json()
        assert body["query"] == "Alhambra"
        assert body["results"][0]["name"] == "Alhambra"
        assert body["results"][0]["bbox"]["east"] == -3.5835

    def test_upstream_failure_is_502(self, client, monkeypatch):
        def fail(*a, **k):
            raise ConnectionError("nominatim down")
        monkeypatch.setattr(geocode, "search_places", fail)
        resp = client.get("/api/geocode", params={"q": "x"})
        assert resp.status_code == 502
        assert "nominatim down" in resp.json()["error"]

    def test_query_is_required(self, client):
        assert client.get("/api/geocode").status_code == 422


# ---------------------------------------------------------------------------
# geo2stl.landmarks
# ---------------------------------------------------------------------------

# A ~1.1 km box; at 37 deg N one degree of longitude is ~88.9 km.
BOX = {"north": 37.180, "south": 37.170, "east": -3.590, "west": -3.600}
M_LON = 1 / 88_900.0   # degrees of longitude per metre at 37.175 N
M_LAT = 1 / 110_574.0


def _point(name, lat, lon):
    return {"name": name, "class": "historic", "type": "castle",
            "south": lat, "north": lat, "west": lon, "east": lon, "lat": lat, "lon": lon}


class TestEdgeProximity:
    def test_outside_east_edge(self):
        p = landmarks.edge_proximity(BOX, _point("A", 37.175, BOX["east"] + 120 * M_LON))
        assert p["position"] == "outside" and p["edge"] == "east"
        assert p["distance_m"] == pytest.approx(120, abs=2)

    def test_inside_near_north_edge(self):
        p = landmarks.edge_proximity(BOX, _point("B", BOX["north"] - 50 * M_LAT, -3.595))
        assert p["position"] == "inside" and p["edge"] == "north"
        assert p["distance_m"] == pytest.approx(50, abs=1)

    def test_outside_corner(self):
        p = landmarks.edge_proximity(
            BOX, _point("C", BOX["south"] - 30 * M_LAT, BOX["west"] - 40 * M_LON))
        assert p["position"] == "outside" and p["edge"] == "south-west"
        assert p["distance_m"] == pytest.approx(50, abs=1)

    def test_area_crossing_an_edge(self):
        feat = {"name": "Alhambra", "south": 37.174, "north": 37.176,
                "west": BOX["east"] - 100 * M_LON, "east": BOX["east"] + 300 * M_LON}
        p = landmarks.edge_proximity(BOX, feat)
        assert p["position"] == "crosses" and p["edge"] == "east"
        assert p["distance_m"] == pytest.approx(300, abs=3)

    def test_message(self):
        prox = {"position": "outside", "edge": "east", "distance_m": 120.4}
        assert landmarks.describe("Alhambra", prox) == "Alhambra is 120 m outside the east edge"


class TestEdgeLandmarks:
    FEATURES = [
        _point("Near outside", 37.175, BOX["east"] + 120 * M_LON),
        _point("Far outside", 37.175, BOX["east"] + 190 * M_LON + 100 * M_LON),
        _point("Near inside", BOX["north"] - 20 * M_LAT, -3.595),
        _point("Middle", 37.175, -3.595),
        _point("Near inside", BOX["north"] - 25 * M_LAT, -3.596),   # duplicate name
    ]

    def test_filters_to_warn_distance_nearest_first(self):
        out = landmarks.edge_landmarks(BOX, warn_m=200, features=self.FEATURES)
        assert [r["name"] for r in out] == ["Near inside", "Near outside"]
        assert out[1]["message"] == "Near outside is 120 m outside the east edge"

    def test_query_covers_four_strips_in_one_request(self):
        q = landmarks.build_query(BOX, band_m=400)
        assert q.count("nwr") == 4 * len(landmarks.NOTABLE_SELECTORS)
        assert q.startswith("[out:json]") and q.rstrip().endswith("out tags bb;")

    def test_fetch_parses_elements_and_caches(self, tmp_data_dir):
        elements = [
            {"type": "node", "id": 1, "lat": 37.175, "lon": -3.589,
             "tags": {"name": "Mirador", "tourism": "viewpoint"}},
            {"type": "way", "id": 2, "tags": {"name": "Alcazaba", "historic": "castle"},
             "bounds": {"minlat": 37.176, "minlon": -3.5924, "maxlat": 37.177, "maxlon": -3.5911}},
            {"type": "way", "id": 3, "tags": {"historic": "castle"},    # unnamed: dropped
             "bounds": {"minlat": 37.1, "minlon": -3.6, "maxlat": 37.2, "maxlon": -3.5}},
        ]
        with patch("geo2stl.osm.overpass_query", return_value=elements) as q:
            feats = landmarks.fetch_edge_features(BOX)
            again = landmarks.fetch_edge_features(BOX)
        assert q.call_count == 1
        assert feats == again
        assert [f["name"] for f in feats] == ["Mirador", "Alcazaba"]
        assert feats[1]["west"] == -3.5924 and feats[1]["class"] == "historic"


class TestEdgeLandmarksEndpoint:
    def test_returns_warnings(self, client):
        elements = [{"type": "node", "id": 1, "lat": 37.175,
                     "lon": BOX["east"] + 120 * M_LON,
                     "tags": {"name": "Alhambra", "tourism": "attraction"}}]
        with patch("geo2stl.osm.overpass_query", return_value=elements):
            body = client.get("/api/geocode/edge-landmarks", params=BOX).json()
        assert body["warn_m"] == 200 and body["band_m"] == 400
        [hit] = body["landmarks"]
        assert hit["message"] == "Alhambra is 120 m outside the east edge"
        assert hit["position"] == "outside" and hit["edge"] == "east"

    def test_large_region_is_skipped_without_a_query(self, client):
        with patch("geo2stl.osm.overpass_query", side_effect=AssertionError("no query")):
            body = client.get("/api/geocode/edge-landmarks", params={
                "north": 45.0, "south": 40.0, "east": 5.0, "west": 0.0}).json()
        assert body["landmarks"] == [] and "skipped" in body

    def test_invalid_bbox(self, client):
        resp = client.get("/api/geocode/edge-landmarks", params={
            "north": 1.0, "south": 2.0, "east": 1.0, "west": 0.0})
        assert resp.status_code == 400

    def test_upstream_failure_is_502(self, client):
        with patch("geo2stl.osm.overpass_query", side_effect=RuntimeError("429")):
            resp = client.get("/api/geocode/edge-landmarks", params=BOX)
        assert resp.status_code == 502
