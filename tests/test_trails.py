"""
Tests for the trails layer: rasterizer, service dispatch, and the
/api/terrain/trails endpoint.

Endpoint tests run with MAP2STL_TEST_MODE=1 (set in conftest), so they return
a deterministic two-line grid without touching Overpass or the Forest Service.
Provider tests stub the network fetch instead of calling it.
"""

import base64

import numpy as np
import pytest

from geo2stl.trails import (
    CATEGORIES,
    SKI_DIFFICULTY_CLASSES,
    OsmTrailsLayer,
    TrailsLayerBase,
    TrailsService,
    TrailsUpstreamError,
    rasterize_trails,
)

_BBOX_QS = "north=40.0&south=39.9&east=-75.1&west=-75.2"
_BBOX = (-75.2, 39.9, -75.1, 40.0)   # west, south, east, north


def _line_fc(coords):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "LineString", "coordinates": coords},
        }],
    }


def _piste_fc(coords, difficulty=None):
    """One piste LineString, optionally carrying a ``piste:difficulty`` tag."""
    props = {"piste:type": "downhill"}
    if difficulty is not None:
        props["piste:difficulty"] = difficulty
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "LineString", "coordinates": coords},
        }],
    }


def _decode(b64, dims):
    raw = base64.b64decode(b64)
    return np.frombuffer(raw, dtype=np.float32).reshape(dims)


# ---------------------------------------------------------------------------
# rasterize_trails
# ---------------------------------------------------------------------------

class TestRasterizeTrails:
    def test_empty_collection_returns_zero_grid(self):
        grid = rasterize_trails({"type": "FeatureCollection", "features": []},
                                _BBOX, 32)
        assert grid.shape == (32, 32)
        assert not grid.any()

    def test_line_is_engraved_with_relief_value(self):
        grid = rasterize_trails(
            _line_fc([[-75.19, 39.95], [-75.11, 39.95]]), _BBOX, 64,
            relief_m=-2.0)
        assert grid.min() == pytest.approx(-2.0)
        assert (grid != 0).any()
        # Away from the line the grid must stay at zero, not the fill value.
        assert grid[0, 0] == 0.0

    def test_positive_relief_raises_instead_of_engraving(self):
        grid = rasterize_trails(
            _line_fc([[-75.19, 39.95], [-75.11, 39.95]]), _BBOX, 64,
            relief_m=3.0)
        assert grid.max() == pytest.approx(3.0)
        assert grid.min() == 0.0

    def test_thin_trail_survives_low_resolution(self):
        """A 1 m trail must still paint pixels at a coarse grid size.

        The width floor (two pixels) exists for exactly this case: without it a
        sub-pixel buffer rasterizes to nothing and the trail vanishes.
        """
        grid = rasterize_trails(
            _line_fc([[-75.19, 39.95], [-75.11, 39.95]]), _BBOX, 40,
            relief_m=-1.0, width_m=1.0)
        assert (grid != 0).sum() > 0

    def test_polygon_is_engraved_as_outline_not_filled(self):
        """A closed way must engrave as linework, not as a solid area.

        OSM maps many pistes as areas. Buffering such a polygon directly paints
        its whole interior, turning a ski run into a blob the size of the
        mountain face, so the rasterizer takes the boundary first.
        """
        fc = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-75.18, 39.92], [-75.12, 39.92],
                                     [-75.12, 39.98], [-75.18, 39.98],
                                     [-75.18, 39.92]]],
                },
            }],
        }
        grid = rasterize_trails(fc, _BBOX, 200, relief_m=-2.0, width_m=8.0)
        assert (grid != 0).any()                 # the edge is painted
        assert grid[100, 100] == 0.0             # the interior is not
        # An outline covers a small fraction of the box it encloses.
        assert (grid != 0).sum() < 0.2 * 200 * 200

    def test_with_areas_returns_a_filled_interior_mask(self):
        """The area mask carries what the relief grid deliberately drops."""
        fc = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-75.18, 39.92], [-75.12, 39.92],
                                     [-75.12, 39.98], [-75.18, 39.98],
                                     [-75.18, 39.92]]],
                },
            }],
        }
        grid, areas = rasterize_trails(fc, _BBOX, 200, relief_m=-2.0,
                                       width_m=8.0, with_areas=True)
        assert grid[100, 100] == 0.0          # relief still hollow
        assert areas[100, 100] == 1.0         # mask filled
        assert areas.max() == 1.0
        assert (areas != 0).sum() > 10 * (grid != 0).sum()

    def test_with_areas_leaves_the_mask_empty_for_linework(self):
        grid, areas = rasterize_trails(
            _line_fc([[-75.19, 39.95], [-75.11, 39.95]]), _BBOX, 64,
            relief_m=-2.0, with_areas=True)
        assert (grid != 0).any()
        assert not areas.any()

    def test_malformed_geometry_is_skipped(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": None},
                {"type": "Feature", "properties": {},
                 "geometry": {"type": "LineString",
                              "coordinates": [[-75.19, 39.95], [-75.11, 39.95]]}},
            ],
        }
        grid = rasterize_trails(fc, _BBOX, 32, relief_m=-2.0)
        assert grid.min() == pytest.approx(-2.0)

    def test_with_difficulty_returns_a_class_grid(self):
        grid, difficulty = rasterize_trails(
            _piste_fc([[-75.19, 39.95], [-75.11, 39.95]], "easy"),
            _BBOX, 64, with_difficulty=True)
        assert difficulty.shape == grid.shape
        assert difficulty.dtype == np.uint8
        # "easy" is the second class, so index 2 (0 means untagged).
        assert set(np.unique(difficulty)) == {0, 2}
        # The class grid must cover exactly the linework, no more and no less.
        assert ((difficulty != 0) == (grid != 0)).all()

    def test_untagged_piste_stays_class_zero(self):
        _, difficulty = rasterize_trails(
            _piste_fc([[-75.19, 39.95], [-75.11, 39.95]]),
            _BBOX, 32, with_difficulty=True)
        assert not difficulty.any()

    def test_unknown_difficulty_value_stays_class_zero(self):
        _, difficulty = rasterize_trails(
            _piste_fc([[-75.19, 39.95], [-75.11, 39.95]], "double-black"),
            _BBOX, 32, with_difficulty=True)
        assert not difficulty.any()

    def test_difficulty_tag_is_case_insensitive(self):
        _, difficulty = rasterize_trails(
            _piste_fc([[-75.19, 39.95], [-75.11, 39.95]], "  Expert "),
            _BBOX, 32, with_difficulty=True)
        assert set(np.unique(difficulty)) == {0, SKI_DIFFICULTY_CLASSES.index("expert") + 1}

    def test_harder_piste_wins_where_two_cross(self):
        easy = _piste_fc([[-75.19, 39.95], [-75.11, 39.95]], "easy")
        expert = _piste_fc([[-75.15, 39.91], [-75.15, 39.99]], "expert")
        fc = {"type": "FeatureCollection",
              "features": easy["features"] + expert["features"]}
        _, difficulty = rasterize_trails(fc, _BBOX, 64, with_difficulty=True)
        easy_cls = SKI_DIFFICULTY_CLASSES.index("easy") + 1
        expert_cls = SKI_DIFFICULTY_CLASSES.index("expert") + 1
        # Both runs are present, and the crossing belongs to the harder one:
        # the expert line's own row is unbroken where the easy line meets it.
        assert (difficulty == easy_cls).any()
        expert_col = difficulty[:, difficulty.shape[1] // 2]
        assert easy_cls not in set(np.unique(expert_col))
        assert expert_cls in set(np.unique(expert_col))

    def test_flag_order_is_relief_then_area_then_difficulty(self):
        fc = _piste_fc([[-75.19, 39.95], [-75.11, 39.95]], "easy")
        assert isinstance(rasterize_trails(fc, _BBOX, 16), np.ndarray)
        assert len(rasterize_trails(fc, _BBOX, 16, with_areas=True)) == 2
        assert len(rasterize_trails(fc, _BBOX, 16, with_difficulty=True)) == 2
        relief, area, difficulty = rasterize_trails(
            fc, _BBOX, 16, with_areas=True, with_difficulty=True)
        assert relief.dtype == np.float32
        assert area.dtype == np.float32
        assert difficulty.dtype == np.uint8

    def test_empty_collection_honours_the_difficulty_flag(self):
        relief, area, difficulty = rasterize_trails(
            {"type": "FeatureCollection", "features": []}, _BBOX, 16,
            with_areas=True, with_difficulty=True)
        assert relief.shape == area.shape == difficulty.shape == (16, 16)
        assert difficulty.dtype == np.uint8
        assert not difficulty.any()


# ---------------------------------------------------------------------------
# TrailsService dispatch
# ---------------------------------------------------------------------------

class _StubLayer(TrailsLayerBase):
    """Provider returning one horizontal line per requested category."""

    def __init__(self, name, categories):
        self.name = name
        self.categories = categories
        self.calls = []

    def fetch(self, north, south, east, west, categories=CATEGORIES):
        self.calls.append(tuple(categories))
        return {
            c: _line_fc([[west + 0.01, (north + south) / 2],
                         [east - 0.01, (north + south) / 2]])
            for c in categories if c in self.categories
        }


class TestTrailsService:
    def test_single_source_returns_both_grids(self):
        svc = TrailsService()
        svc.providers = {"osm": _StubLayer("osm", CATEGORIES)}
        result = svc.fetch_and_rasterize(40.0, 39.9, -75.1, -75.2, 32,
                                         source="osm")
        assert result is not None
        assert result["ski_grid"].shape == (32, 32)
        assert result["hiking_grid"].shape == (32, 32)
        assert result["feature_count"] == result["ski_count"] + result["hiking_count"]

    def test_all_unions_providers(self):
        svc = TrailsService()
        svc.providers = {
            "osm": _StubLayer("osm", CATEGORIES),
            "usfs": _StubLayer("usfs", ("hiking",)),
        }
        result = svc.fetch_and_rasterize(40.0, 39.9, -75.1, -75.2, 32,
                                         source="all")
        assert result is not None
        assert set(result["sources"]) == {"osm", "usfs"}
        # Both providers contribute hiking; OSM alone contributes ski.
        assert result["hiking_count"] >= result["ski_count"]

    def test_difficulty_grid_is_ski_only(self):
        class _Graded(_StubLayer):
            def fetch(self, north, south, east, west, categories=CATEGORIES):
                self.calls.append(tuple(categories))
                lat = (north + south) / 2
                coords = [[west + 0.01, lat], [east - 0.01, lat]]
                out = {}
                for c in categories:
                    if c not in self.categories:
                        continue
                    # Tag both categories, to prove the grid follows the ski
                    # features rather than whatever happened to carry a tag.
                    out[c] = _piste_fc(coords, "advanced")
                return out

        svc = TrailsService()
        svc.providers = {"osm": _Graded("osm", CATEGORIES)}
        result = svc.fetch_and_rasterize(40.0, 39.9, -75.1, -75.2, 32,
                                         source="osm")
        difficulty = result["ski_difficulty_grid"]
        assert difficulty.shape == (32, 32)
        assert difficulty.dtype == np.uint8
        expected = SKI_DIFFICULTY_CLASSES.index("advanced") + 1
        assert set(np.unique(difficulty)) == {0, expected}
        # It must line up with the ski relief, not the hiking relief.
        assert ((difficulty != 0) == (result["ski_grid"] != 0)).all()

    def test_provider_returning_nothing_yields_none(self):
        class _Empty(TrailsLayerBase):
            name = "empty"

            def fetch(self, north, south, east, west, categories=CATEGORIES):
                return {}

        svc = TrailsService()
        svc.providers = {"osm": _Empty()}
        assert svc.fetch_and_rasterize(40.0, 39.9, -75.1, -75.2, 32,
                                       source="osm") is None

    def test_provider_exception_does_not_abort_the_union(self):
        class _Broken(TrailsLayerBase):
            name = "broken"

            def fetch(self, north, south, east, west, categories=CATEGORIES):
                raise RuntimeError("overpass down")

        svc = TrailsService()
        svc.providers = {"broken": _Broken(), "osm": _StubLayer("osm", CATEGORIES)}
        result = svc.fetch_and_rasterize(40.0, 39.9, -75.1, -75.2, 32,
                                         source="all")
        assert result is not None
        assert result["sources"] == ["osm"]


# ---------------------------------------------------------------------------
# GET /api/terrain/trails
# ---------------------------------------------------------------------------

class TestTrailsEndpoint:
    def test_returns_200(self, client):
        r = client.get(f"/api/terrain/trails?{_BBOX_QS}&dim=16")
        assert r.status_code == 200

    def test_returns_both_category_grids(self, client):
        r = client.get(f"/api/terrain/trails?{_BBOX_QS}&dim=16")
        data = r.json()
        dims = data["grid_dimensions"]
        assert dims == [16, 16]
        ski = _decode(data["ski_grid_values_b64"], dims)
        hiking = _decode(data["hiking_grid_values_b64"], dims)
        assert ski.min() < 0
        assert hiking.min() < 0

    def test_sends_a_difficulty_grid_and_its_class_names(self, client):
        r = client.get(f"/api/terrain/trails?{_BBOX_QS}&dim=16")
        data = r.json()
        assert data["difficulty_classes"] == list(SKI_DIFFICULTY_CLASSES)
        difficulty = _decode(data["ski_difficulty_grid_values_b64"],
                             data["grid_dimensions"])
        # Transported as float32 like every other grid, but the values are
        # class indices, so they must survive the round trip exactly.
        assert difficulty.max() > 0
        assert (difficulty == np.round(difficulty)).all()
        assert difficulty.max() <= len(SKI_DIFFICULTY_CLASSES)

    def test_relief_m_is_echoed_and_applied(self, client):
        r = client.get(f"/api/terrain/trails?{_BBOX_QS}&dim=16&relief_m=-7.5")
        data = r.json()
        assert data["relief_m"] == pytest.approx(-7.5)
        ski = _decode(data["ski_grid_values_b64"], data["grid_dimensions"])
        assert ski.min() == pytest.approx(-7.5)

    def test_unknown_source_falls_back_to_all(self, client):
        r = client.get(f"/api/terrain/trails?{_BBOX_QS}&dim=16&source=bogus")
        assert r.status_code == 200
        assert r.json()["source"] == "all"

    def test_invalid_bbox_is_rejected(self, client):
        r = client.get("/api/terrain/trails?north=39.0&south=40.0"
                       "&east=-75.1&west=-75.2&dim=16")
        assert r.status_code >= 400

    def test_projection_shrinks_the_grid(self, client):
        plain = client.get(f"/api/terrain/trails?{_BBOX_QS}&dim=32").json()
        projected = client.get(
            f"/api/terrain/trails?{_BBOX_QS}&dim=32&projection=cosine").json()
        # Cosine projection narrows the grid at this latitude.
        assert projected["grid_dimensions"][1] < plain["grid_dimensions"][1]


class _FailingLayer(TrailsLayerBase):
    """Provider whose upstream is down."""

    name = "osm"
    categories = CATEGORIES

    def fetch(self, north, south, east, west, categories=CATEGORIES):
        raise TrailsUpstreamError("Overpass is unreachable (tried 3 mirror(s))")


class _FakeOsmnx:
    """Stand-in for the osmnx module, raising whatever the test asks for."""

    class settings:
        overpass_url = None
        overpass_rate_limit = None
        requests_timeout = None

    def __init__(self, raises):
        self._raises = raises
        self.calls = 0

    def features_from_bbox(self, bbox, tags=None):
        self.calls += 1
        raise self._raises


class TestOverpassFailure:
    """An outage and an empty region are different answers.

    They used to share one: every exception from osmnx was logged as "no
    features" and returned as an empty FeatureCollection, so an Overpass 502
    produced the same response as a bbox with no trails in it.
    """

    def test_empty_region_is_not_an_error(self):
        from osmnx._errors import InsufficientResponseError

        ox = _FakeOsmnx(InsufficientResponseError("no matching features"))
        fc, err = OsmTrailsLayer._fetch_tags(ox, _BBOX, {}, [], "ski")
        assert err is None
        assert fc["features"] == []

    def test_transport_failure_is_reported(self):
        ox = _FakeOsmnx(RuntimeError("502 Bad Gateway"))
        fc, err = OsmTrailsLayer._fetch_tags(ox, _BBOX, {}, [], "ski")
        assert err is not None
        assert "502" in err
        assert fc["features"] == []

    def test_every_mirror_is_tried_before_giving_up(self, monkeypatch):
        """A mirror that answers its status probe can still fail every query."""
        tried = []

        def _fail(ox, bbox, tags, keep_cols, label):
            tried.append(ox.settings.overpass_url)
            return {"type": "FeatureCollection", "features": []}, f"{label}: 500"

        monkeypatch.setattr(OsmTrailsLayer, "_endpoints",
                            staticmethod(lambda: ["mirror-a", "mirror-b"]))
        monkeypatch.setattr(OsmTrailsLayer, "_fetch_tags", staticmethod(_fail))
        with pytest.raises(TrailsUpstreamError):
            OsmTrailsLayer().fetch(40.0, 39.9, -75.1, -75.2, ("ski",))
        assert tried == ["mirror-a", "mirror-b"]

    def test_a_working_mirror_after_a_broken_one_is_used(self, monkeypatch):
        tried = []

        def _second_works(ox, bbox, tags, keep_cols, label):
            tried.append(ox.settings.overpass_url)
            if ox.settings.overpass_url == "mirror-a":
                return {"type": "FeatureCollection", "features": []}, "ski: 500"
            return _line_fc([[-75.19, 39.95], [-75.11, 39.95]]), None

        monkeypatch.setattr(OsmTrailsLayer, "_endpoints",
                            staticmethod(lambda: ["mirror-a", "mirror-b"]))
        monkeypatch.setattr(OsmTrailsLayer, "_fetch_tags",
                            staticmethod(_second_works))
        out = OsmTrailsLayer().fetch(40.0, 39.9, -75.1, -75.2, ("ski",))
        assert tried == ["mirror-a", "mirror-b"]
        assert len(out["ski"]["features"]) == 1

    def test_second_fetch_of_a_bbox_is_served_from_the_cache(self, monkeypatch):
        calls = []

        def _ok(ox, bbox, tags, keep_cols, label):
            calls.append(label)
            return _line_fc([[-75.19, 39.95], [-75.11, 39.95]]), None

        monkeypatch.setattr(OsmTrailsLayer, "_endpoints", staticmethod(lambda: [None]))
        monkeypatch.setattr(OsmTrailsLayer, "_fetch_tags", staticmethod(_ok))
        first = OsmTrailsLayer().fetch(40.0, 39.9, -75.1, -75.2)
        second = OsmTrailsLayer().fetch(40.0, 39.9, -75.1, -75.2)
        assert calls == ["ski", "hiking"]           # the network was asked once
        assert second == first
        # Another bbox or category set is its own entry.
        OsmTrailsLayer().fetch(40.0, 39.9, -75.1, -75.21)
        OsmTrailsLayer().fetch(40.0, 39.9, -75.1, -75.2, ("ski",))
        assert calls == ["ski", "hiking", "ski", "hiking", "ski"]

    def test_hiking_keeps_trails_not_town_footways(self, monkeypatch):
        def _mixed(ox, bbox, tags, keep_cols, label):
            fc = _line_fc([[-75.19, 39.95], [-75.11, 39.95]])
            base = fc["features"][0]
            props = [{"highway": "path"}, {"highway": "footway", "footway": "sidewalk"},
                     {"highway": "footway", "sac_scale": "hiking"}, {"highway": "steps"},
                     {"highway": "footway"}, {"route": "hiking"}]
            return {"type": "FeatureCollection",
                    "features": [{**base, "properties": p} for p in props]}, None

        monkeypatch.setattr(OsmTrailsLayer, "_endpoints", staticmethod(lambda: [None]))
        monkeypatch.setattr(OsmTrailsLayer, "_fetch_tags", staticmethod(_mixed))
        out = OsmTrailsLayer().fetch(40.0, 39.9, -75.1, -75.2, ("hiking",))
        kept = [f["properties"] for f in out["hiking"]["features"]]
        assert kept == [{"highway": "path"}, {"highway": "footway", "sac_scale": "hiking"},
                        {"route": "hiking"}]

    def test_a_failed_fetch_is_not_cached(self, monkeypatch):
        state = {"fail": True, "calls": 0}

        def _flaky(ox, bbox, tags, keep_cols, label):
            state["calls"] += 1
            if state["fail"]:
                return {"type": "FeatureCollection", "features": []}, f"{label}: 502"
            return _line_fc([[-75.19, 39.95], [-75.11, 39.95]]), None

        monkeypatch.setattr(OsmTrailsLayer, "_endpoints", staticmethod(lambda: [None]))
        monkeypatch.setattr(OsmTrailsLayer, "_fetch_tags", staticmethod(_flaky))
        with pytest.raises(TrailsUpstreamError):
            OsmTrailsLayer().fetch(40.0, 39.9, -75.1, -75.2, ("ski",))
        state["fail"] = False
        out = OsmTrailsLayer().fetch(40.0, 39.9, -75.1, -75.2, ("ski",))
        assert len(out["ski"]["features"]) == 1

    def test_service_raises_rather_than_reporting_an_empty_region(self):
        svc = TrailsService()
        svc.providers = {"osm": _FailingLayer()}
        with pytest.raises(TrailsUpstreamError):
            svc.fetch_and_rasterize(40.0, 39.9, -75.1, -75.2, 32, source="osm")

    def test_empty_region_still_returns_none(self):
        class _EmptyLayer(TrailsLayerBase):
            name = "osm"
            categories = CATEGORIES

            def fetch(self, north, south, east, west, categories=CATEGORIES):
                return {c: {"type": "FeatureCollection", "features": []}
                        for c in categories}

        svc = TrailsService()
        svc.providers = {"osm": _EmptyLayer()}
        assert svc.fetch_and_rasterize(40.0, 39.9, -75.1, -75.2, 32,
                                       source="osm") is None


def test_fetch_and_rasterize_trails_propagates_upstream_errors(monkeypatch):
    # An outage must reach the router's TrailsUpstreamError branch, not turn
    # into None ("no trails found") (audit 2026-10-05).
    import geo2stl.trails as trails

    def _down(*a, **k):
        raise TrailsUpstreamError("Overpass is unreachable")

    monkeypatch.setattr(trails.TRAILS_LAYER, "fetch_and_rasterize", _down)
    with pytest.raises(TrailsUpstreamError):
        trails.fetch_and_rasterize_trails(40.0, 39.9, -75.1, -75.2, 64)
