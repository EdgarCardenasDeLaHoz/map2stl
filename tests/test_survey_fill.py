"""Tests for city2stl.height.survey_fill: default building heights from survey lidar.

Synthetic rasters on the shared survey grid; no network (the fetch is injected).
"""
import copy
import math

import numpy as np
import pytest
from shapely.geometry import box, mapping

from city2stl.height import service, survey_fill
from city2stl.height.providers._survey import lonlat_grid

# Central Granada (Andalucía: CNIG primary, REDIAM fallback), ~110 x 130 m.
BBOX = (37.1800, 37.1790, -3.5990, -3.6005)
# Madrid: Spain (CNIG) but outside Andalucía (no REDIAM).
MADRID = (40.4180, 40.4170, -3.7030, -3.7045)


def _grid(bbox=BBOX):
    h, w, t = lonlat_grid(bbox, 1.0)
    return h, w, t


def _cells(t, r0, r1, c0, c1):
    """Polygon covering rows r0..r1-1, cols c0..c1-1 of the grid."""
    x0, y0 = t @ (c0, r0)
    x1, y1 = t @ (c1, r1)
    return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _feature(geom, source="default", h=10.0, **props):
    return {"type": "Feature", "geometry": mapping(geom),
            "properties": {"height_m": h, "height_source": source, **props}}


# ---------------------------------------------------------------------------
# footprint_stats
# ---------------------------------------------------------------------------

def test_stat_ignores_street_edge_and_courtyard():
    h, w, t = _grid()
    arr = np.zeros((h, w), np.float32)
    arr[10:40, 10:40] = 12.0
    arr[10, 10:40] = arr[39, 10:40] = 0.5         # edge cells smeared with street
    arr[20:28, 20:28] = 0.0                       # courtyard (64 of 900 cells)
    got = survey_fill.footprint_stats(arr, t, {"a": _cells(t, 10, 40, 10, 40)})
    value, cov = got["a"]
    assert value == pytest.approx(12.0)
    assert cov == 1.0


def test_coverage_counts_nan_cells():
    h, w, t = _grid()
    arr = np.full((h, w), 9.0, np.float32)
    arr[10:40, 10:25] = np.nan
    value, cov = survey_fill.footprint_stats(arr, t, {0: _cells(t, 10, 40, 10, 40)})[0]
    assert value == pytest.approx(9.0)
    assert 0.4 < cov < 0.6


def test_small_footprint_keeps_every_cell():
    h, w, t = _grid()
    arr = np.zeros((h, w), np.float32)
    arr[5:8, 5:8] = 6.0                           # 9 cells: too small to erode
    value, cov = survey_fill.footprint_stats(arr, t, {0: _cells(t, 5, 8, 5, 8)})[0]
    assert value == pytest.approx(6.0)
    assert cov == 1.0


def test_part_inside_outline_keeps_its_own_cells():
    h, w, t = _grid()
    arr = np.full((h, w), 8.0, np.float32)
    arr[20:30, 20:30] = 30.0                      # a tower inside the block
    got = survey_fill.footprint_stats(arr, t, {
        "outline": _cells(t, 10, 50, 10, 50), "tower": _cells(t, 20, 30, 20, 30)})
    assert got["tower"][0] == pytest.approx(30.0)
    assert got["outline"][0] == pytest.approx(8.0)


def test_footprint_without_valid_cells():
    h, w, t = _grid()
    arr = np.full((h, w), np.nan, np.float32)
    value, cov = survey_fill.footprint_stats(arr, t, {0: _cells(t, 10, 40, 10, 40)})[0]
    assert math.isnan(value) and cov == 0.0


# ---------------------------------------------------------------------------
# fill_default_heights
# ---------------------------------------------------------------------------

def _rasters():
    h, w, t = _grid()
    cnig = np.zeros((h, w), np.float32)
    rediam = np.zeros((h, w), np.float32)
    cnig[10:40, 10:40] = 14.0                     # A: a building in CNIG's class
    rediam[10:40, 10:40] = 6.0                    #    REDIAM reads it low
    rediam[60:80, 10:40] = 11.0                   # B: a tower CNIG does not class as building
    cnig[10:40, 60:90] = np.nan                   # C: CNIG void, REDIAM measures it
    rediam[10:40, 60:90] = 7.5
    cnig[60:80, 60:90] = 2.0                      # D: a low wall -> clamped to 3 m
    return t, {"cnig_mdsn": (cnig, t), "rediam_mdhn": (rediam, t)}


def _fetcher(rasters, calls):
    def fetch(name, bbox, res):
        calls.append((name, res))
        return rasters.get(name)
    return fetch


def _city(t):
    return {"type": "FeatureCollection", "features": [
        _feature(_cells(t, 10, 40, 10, 40), name="A"),
        _feature(_cells(t, 60, 80, 10, 40), name="B"),
        _feature(_cells(t, 10, 40, 60, 90), name="C"),
        _feature(_cells(t, 60, 80, 60, 90), name="D"),
        _feature(_cells(t, 90, 100, 90, 100), name="E"),          # nothing stands
        _feature(_cells(t, 10, 40, 10, 40), source="osm_levels", h=12.8, name="tagged"),
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-3.6, 37.18]},
         "properties": {"height_m": 10.0, "height_source": "default", "name": "point"}},
    ]}


def _by_name(fc):
    return {f["properties"]["name"]: f["properties"] for f in fc["features"]}


def test_cnig_first_rediam_only_where_cnig_sees_no_building():
    t, rasters = _rasters()
    fc = _city(t)
    before = copy.deepcopy(fc)
    calls = []
    out, stats = survey_fill.fill_default_heights(fc, BBOX, fetch=_fetcher(rasters, calls))
    p = _by_name(out)

    assert (p["A"]["height_m"], p["A"]["height_source"], p["A"]["survey_source"]) \
        == (14.0, "survey", "cnig_mdsn")
    assert (p["B"]["height_m"], p["B"]["survey_source"]) == (11.0, "rediam_mdhn")
    assert (p["C"]["height_m"], p["C"]["survey_source"]) == (7.5, "rediam_mdhn")
    assert (p["D"]["height_m"], p["D"]["survey_source"]) == (3.0, "cnig_mdsn")
    assert p["E"]["height_source"] == "default"
    assert (p["tagged"]["height_m"], p["tagged"]["height_source"]) == (12.8, "osm_levels")
    assert p["point"]["height_source"] == "default"

    assert [c[0] for c in calls] == ["cnig_mdsn", "rediam_mdhn"]
    assert stats["by_source"] == {"cnig_mdsn": 2, "rediam_mdhn": 2}
    assert (stats["defaults"], stats["filled"], stats["unmeasured"]) == (5, 4, 1)
    assert stats["resolution_m"] == 1.0
    assert fc == before, "the input collection is not mutated"


def test_rediam_is_never_the_primary_source():
    t, rasters = _rasters()
    calls = []
    fc = {"type": "FeatureCollection", "features": [_feature(_cells(t, 10, 40, 10, 40), name="A")]}
    out, _ = survey_fill.fill_default_heights(fc, BBOX, fetch=_fetcher(rasters, calls))
    assert calls == [("cnig_mdsn", 1.0)], "CNIG measured it, so REDIAM is not read"
    assert _by_name(out)["A"]["height_m"] == 14.0


def test_no_rediam_outside_andalucia():
    h, w, t = _grid(MADRID)
    calls = []
    fc = {"type": "FeatureCollection", "features": [_feature(_cells(t, 10, 40, 10, 40), name="A")]}
    rasters = {"cnig_mdsn": (np.zeros((h, w), np.float32), t),
               "rediam_mdhn": (np.full((h, w), 9.0, np.float32), t)}
    out, stats = survey_fill.fill_default_heights(fc, MADRID, fetch=_fetcher(rasters, calls))
    assert calls == [("cnig_mdsn", 1.0)]
    assert _by_name(out)["A"]["height_source"] == "default"
    assert stats["filled"] == 0


@pytest.mark.parametrize("bbox", [
    (48.86, 48.85, 2.35, 2.34),        # Paris: IGN LiDAR HD is not validated for the fill
    (39.51, 39.43, -106.03, -106.125),  # US: the 3DEP pass handles it
])
def test_unvalidated_regions_are_left_out(bbox):
    calls = []
    fc = {"type": "FeatureCollection", "features": [_feature(box(bbox[3], bbox[1], bbox[2], bbox[0]))]}
    out, stats = survey_fill.fill_default_heights(fc, bbox, fetch=_fetcher({}, calls))
    assert stats is None and calls == [] and out is fc


def test_nothing_on_default_fetches_nothing():
    t, rasters = _rasters()
    calls = []
    fc = {"type": "FeatureCollection",
          "features": [_feature(_cells(t, 10, 40, 10, 40), source="osm_levels", h=12.0)]}
    out, stats = survey_fill.fill_default_heights(fc, BBOX, fetch=_fetcher(rasters, calls))
    assert (out, stats, calls) == (fc, None, [])


def test_failing_survey_leaves_buildings_on_default():
    t, _ = _rasters()

    def boom(name, bbox, res):
        raise RuntimeError("WCS down")

    fc = _city(t)
    out, stats = survey_fill.fill_default_heights(fc, BBOX, fetch=boom)
    assert out is fc
    assert "WCS down" in stats["error"]


def test_failing_fallback_keeps_primary_heights():
    t, rasters = _rasters()

    def fetch(name, bbox, res):
        if name == "rediam_mdhn":
            raise RuntimeError("COG down")
        return rasters[name]

    out, stats = survey_fill.fill_default_heights(_city(t), BBOX, fetch=fetch)
    assert stats["by_source"] == {"cnig_mdsn": 2}
    assert _by_name(out)["B"]["height_source"] == "default"


def test_resolution_grows_with_the_bbox_then_gives_up():
    assert survey_fill.fill_resolution(BBOX) == 1.0
    assert survey_fill.fill_resolution((37.23, 37.17, -3.55, -3.63)) == 2.0   # ~7 km wide
    assert survey_fill.fill_resolution((37.3, 37.1, -3.4, -3.7)) is None      # ~27 km wide


def test_too_large_bbox_is_skipped_without_fetching():
    bbox = (37.3, 37.1, -3.4, -3.7)
    calls = []
    fc = {"type": "FeatureCollection", "features": [_feature(box(-3.6, 37.2, -3.5999, 37.2001))]}
    out, stats = survey_fill.fill_default_heights(fc, bbox, fetch=_fetcher({}, calls))
    assert calls == [] and out is fc and "skipped" in stats


# ---------------------------------------------------------------------------
# Wiring: enhance_city_data (fetch path) and city_data.with_survey_heights (read path)
# ---------------------------------------------------------------------------

def _patch_fetch(monkeypatch, rasters):
    calls = []
    monkeypatch.setattr(survey_fill, "_fetch_default", _fetcher(rasters, calls))
    return calls


def test_enhance_city_data_measures_survey_before_rasters(monkeypatch):
    from city2stl.height.providers import lidar_3dep_copc

    t, rasters = _rasters()
    _patch_fetch(monkeypatch, rasters)
    monkeypatch.setattr(lidar_3dep_copc, "available", lambda: False)
    fetched = []

    class _Raster:
        name = "fine"

        def covers(self, bbox):
            return True

        def fetch_heights(self, bbox, dim):
            fetched.append(self.name)
            raise RuntimeError("not reached for measured buildings")

    monkeypatch.setattr(service, "_select_providers", lambda bbox, requested=None: ([_Raster()], []))
    fc = {"type": "FeatureCollection", "features": [_feature(_cells(t, 10, 40, 10, 40), name="A")]}
    out = service.enhance_city_data({"buildings": fc}, *BBOX, dim=8)

    assert _by_name(out["buildings"])["A"]["height_source"] == "survey"
    assert fetched == [], "nothing left on default, so no raster provider runs"
    info = out["height_enhancement"]
    assert info["source_name"] == "survey"
    assert info["providers_used"] == ["cnig_mdsn"]
    assert info["survey"]["filled"] == 1


def test_read_path_fills_low_default_rows(monkeypatch):
    import app.server.core.city_data as city_data

    t, rasters = _rasters()
    _patch_fetch(monkeypatch, rasters)
    # A ruin the low-default rule lowered to 3 m keeps source "default", so the fill measures it.
    ruin = _feature(_cells(t, 60, 80, 10, 40), h=3.0, name="B", ruins="yes")
    payload = {"buildings": {"type": "FeatureCollection", "features": [ruin]}, "roads": {}}
    before = copy.deepcopy(payload)
    out = city_data.with_survey_heights(payload, *BBOX)

    p = _by_name(out["buildings"])["B"]
    assert (p["height_m"], p["height_source"], p["survey_source"]) == (11.0, "survey", "rediam_mdhn")
    assert payload == before
    assert out["roads"] is payload["roads"]


def test_read_path_passes_through_when_nothing_filled(monkeypatch):
    import app.server.core.city_data as city_data

    _patch_fetch(monkeypatch, {})
    payload = {"buildings": {"type": "FeatureCollection", "features": [
        _feature(box(2.34, 48.85, 2.3401, 48.8501))]}}
    assert city_data.with_survey_heights(payload, 48.86, 48.85, 2.35, 2.34) is payload


# ---------------------------------------------------------------------------
# D1: landmark layers, bare 10.0 m tags, lines, merge, every reader
# ---------------------------------------------------------------------------

def _line(t, r, c0, c1, **kw):
    x0, y0 = t @ (c0, r)
    x1, y1 = t @ (c1, r)
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[x0, y0], [x1, y1]]},
            "properties": {"height_m": 8.0, "height_source": "default", **kw}}


@pytest.mark.parametrize("props, expect", [
    ({"height_source": "default", "height_m": 12.0}, True),
    ({"height_source": "osm_tag", "height_m": 10.0}, True),
    ({"height_source": "osm_tag", "height_m": 12.0}, False),
    ({"height_source": "osm_tag", "height_m": 10.0, "height": "10"}, False),
    ({"height_source": "osm_tag", "height_m": 10.0, "height": "10 m"}, False),
    ({"height_source": "osm_tag", "height_m": 10.0, "height": "12"}, True),
    ({"height_source": "osm_levels", "height_m": 10.0}, False),
    ({"height_source": "user_override", "height_m": 10.0}, False),
    ({"height_source": "survey", "height_m": 10.0}, False),
    (None, False),
])
def test_fillable(props, expect):
    assert survey_fill.fillable(props) is expect


def test_fill_layer_heights_covers_landmark_layers_and_lines():
    t, rasters = _rasters()
    layers = {
        "buildings": {"type": "FeatureCollection", "features": [
            _feature(_cells(t, 10, 40, 10, 40), name="A")]},
        "towers": {"type": "FeatureCollection", "features": [
            _feature(_cells(t, 60, 80, 10, 40), source="osm_tag", h=10.0, name="T10"),
            _feature(_cells(t, 60, 80, 60, 90), source="osm_tag", h=4.0, name="T12"),
            _feature(_cells(t, 10, 40, 60, 90), source="osm_tag", h=10.0, name="Tx",
                     height="10")]},
        "walls": {"type": "FeatureCollection", "features": [_line(t, 25, 10, 40, name="W")]},
        "roads": {"type": "FeatureCollection", "features": [_feature(_cells(t, 10, 40, 10, 40))]},
    }
    before = copy.deepcopy(layers)
    calls = []
    out, stats = survey_fill.fill_layer_heights(layers, BBOX, fetch=_fetcher(rasters, calls))

    assert _by_name(out["buildings"])["A"]["height_m"] == 14.0
    p = _by_name(out["towers"])
    assert (p["T10"]["height_m"], p["T10"]["height_source"], p["T10"]["survey_source"]) \
        == (11.0, "survey", "rediam_mdhn")
    assert (p["T12"]["height_m"], p["T12"]["height_source"]) == (4.0, "osm_tag")   # survey 3 m: within 3 m
    assert p["Tx"]["height_source"] == "osm_tag", "an explicit height=10 tag is kept"
    w = _by_name(out["walls"])["W"]
    assert (w["height_source"], w["height_m"]) == ("survey", 14.0)
    assert out["roads"] is layers["roads"], "layers outside FILL_LAYERS are untouched"
    assert [c[0] for c in calls] == ["cnig_mdsn", "rediam_mdhn"], "one read per source"
    assert stats["layers"] == {"buildings": {"defaults": 1, "filled": 1, "tag_replaced": 0},
                               "towers": {"defaults": 1, "filled": 1, "tag_replaced": 0},
                               "walls": {"defaults": 1, "filled": 1, "tag_replaced": 0}}
    assert (stats["defaults"], stats["filled"], stats["unmeasured"]) == (3, 3, 0)
    assert (stats["tag_checked"], stats["tag_replaced"]) == (2, 0)
    assert layers == before


def test_with_survey_heights_reports_stats_and_failures(monkeypatch):
    import app.server.core.city_data as city_data

    t, rasters = _rasters()
    _patch_fetch(monkeypatch, rasters)
    payload = {"towers": {"type": "FeatureCollection", "features": [
        _feature(_cells(t, 10, 40, 10, 40), source="osm_tag", h=10.0, name="T")]}}
    out = city_data.with_survey_heights(payload, *BBOX)
    assert _by_name(out["towers"])["T"]["height_source"] == "survey"
    assert out["survey_fill"]["filled"] == 1
    assert out["survey_fill"]["layers"]["towers"] == {"defaults": 1, "filled": 1, "tag_replaced": 0}
    assert "survey_fill" not in payload

    def boom(name, bbox, res):
        raise RuntimeError("WCS down")

    monkeypatch.setattr(survey_fill, "_fetch_default", boom)
    failed = city_data.with_survey_heights(payload, *BBOX)
    assert "WCS down" in failed["survey_fill"]["error"]
    assert failed["towers"] is payload["towers"]


def test_merge_layer_data_keeps_survey_heights_under_an_override():
    import app.server.core.city_data as city_data

    t, _ = _rasters()
    geoms = [_cells(t, 10, 40, 10, 40), _cells(t, 60, 80, 10, 40), _cells(t, 10, 40, 60, 90)]
    server = {"type": "FeatureCollection", "features": [
        _feature(geoms[0], source="survey", h=14.0, name="A", survey_source="cnig_mdsn"),
        _feature(geoms[1], source="survey", h=11.0, name="B", survey_source="rediam_mdhn"),
        _feature(geoms[2], source="osm_levels", h=9.6, name="C")]}
    # The client copy predates the fill: B is still on default; A carries the user edit.
    client = {"type": "FeatureCollection", "features": [
        _feature(geoms[0], source="user_override", h=30.0, name="A"),
        _feature(geoms[1], source="default", h=10.0, name="B", roof="x"),
        _feature(geoms[2], source="osm_levels", h=9.6, name="C")]}
    for c in (client, {**client, "features": client["features"][::-1]}):    # aligned, then by geometry
        out = city_data.merge_layer_data({"buildings": server, "roads": {"k": 1}},
                                         {"buildings": c, "water": {"x": 1}}, enabled={"buildings"})
        p = _by_name(out["buildings"])
        assert (p["A"]["height_m"], p["A"]["height_source"]) == (30.0, "user_override")
        assert (p["B"]["height_m"], p["B"]["height_source"], p["B"]["survey_source"]) \
            == (11.0, "survey", "rediam_mdhn")
        assert p["B"]["roof"] == "x", "the client's other properties are kept"
        assert p["C"]["height_source"] == "osm_levels"
        assert out["roads"] == {"k": 1} and "water" not in out
    # a layer the server lacks is the client's
    assert city_data.merge_layer_data({}, {"buildings": client})["buildings"] is client


def test_any_tolerance_read_applies_the_survey_fill(monkeypatch):
    import app.server.core.city_data as city_data

    t, rasters = _rasters()
    _patch_fetch(monkeypatch, rasters)
    n, s, e, w = BBOX
    cand = {"key": "k", "tol": 3.0, "min_area": 5.0, "north": n, "south": s, "east": e, "west": w}
    payload = {"buildings": {"type": "FeatureCollection", "features": [
        _feature(_cells(t, 10, 40, 10, 40), name="A")]}}
    monkeypatch.setattr(city_data, "_candidates", lambda *a, **k: [cand])
    monkeypatch.setattr(city_data, "read_osm_cache", lambda key: payload)
    out, key = city_data.read_city_layers_any_tolerance(n, s, e, w, min_area=5.0)
    assert key == "k"
    assert _by_name(out["buildings"])["A"]["height_source"] == "survey"
    assert out["survey_fill"]["filled"] == 1


def test_preflight_cached_layers_apply_the_survey_fill(monkeypatch):
    import app.server.core.city_data as city_data
    from app.server.core import preflight

    t, rasters = _rasters()
    _patch_fetch(monkeypatch, rasters)
    payload = {"towers": {"type": "FeatureCollection", "features": [
        _feature(_cells(t, 10, 40, 10, 40), source="osm_tag", h=10.0, name="T")]}}
    monkeypatch.setattr(city_data, "lookup_city_layers", lambda *a, **k: (payload, "ok"))
    bbox = {"north": BBOX[0], "south": BBOX[1], "east": BBOX[2], "west": BBOX[3]}
    have, missing, stale = preflight._cached_layers(bbox, ["towers"], {})
    assert _by_name(have["towers"])["T"]["height_source"] == "survey"
    assert missing == [] and stale is False


# ---------------------------------------------------------------------------
# Survey wins when far off (user decision 2026-10-17)
# ---------------------------------------------------------------------------

def _tag_case(tag_h, source="osm_tag", survey_h=14.0, **props):
    t, rasters = _rasters()
    rasters["cnig_mdsn"][0][10:40, 10:40] = survey_h
    fc = {"type": "FeatureCollection", "features": [
        _feature(_cells(t, 10, 40, 10, 40), source=source, h=tag_h, name="x", **props)]}
    out, stats = survey_fill.fill_layer_heights({"towers": fc}, BBOX,
                                                fetch=_fetcher(rasters, []))
    return fc, out, stats


def test_tag_far_from_survey_is_replaced_and_kept_aside():
    fc, out, stats = _tag_case(10.0, survey_h=20.0, height="10")   # explicit 10 m tag vs 20 m
    p = _by_name(out["towers"])["x"]
    assert (p["height_m"], p["height_source"], p["survey_source"]) == (20.0, "survey", "cnig_mdsn")
    assert p["osm_tag_height_m"] == 10.0
    assert (stats["tag_replaced"], stats["tag_checked"], stats["filled"], stats["defaults"])         == (1, 1, 0, 0)
    assert stats["layers"]["towers"]["tag_replaced"] == 1
    assert fc["features"][0]["properties"]["height_source"] == "osm_tag", "input untouched"


@pytest.mark.parametrize("tag_h, survey_h", [(10.0, 12.0), (12.0, 14.0), (12.0, 9.0), (17.0, 14.0)])
def test_tag_within_three_metres_is_kept(tag_h, survey_h):
    fc, out, stats = _tag_case(tag_h, survey_h=survey_h, height="10")
    assert out["towers"] is fc
    assert stats["tag_replaced"] == 0 and stats["tag_checked"] == 1


@pytest.mark.parametrize("source", ["osm_levels", "merged", "user_override"])
def test_other_sources_are_never_replaced(source):
    fc, out, stats = _tag_case(3.0, source=source, survey_h=20.0)
    assert out["towers"] is fc and stats is None
