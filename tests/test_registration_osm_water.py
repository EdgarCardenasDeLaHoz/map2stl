"""city2stl.registration.osm_water: Overpass water layers to rasters, with Overpass faked."""

from __future__ import annotations

import numpy as np
import pytest

from city2stl.registration import osm_water as ow

BBOX = (1.0, 0.0, 1.0, 0.0)   # N, S, E, W: a unit-degree window keeps pixel maths easy
GRID = 40


def _pts(coords):
    return [{"lat": lat, "lon": lon} for lon, lat in coords]


def _lake():
    """Closed way over the south-west quarter (lon 0.1-0.4, lat 0.1-0.4)."""
    ring = [(0.1, 0.1), (0.4, 0.1), (0.4, 0.4), (0.1, 0.4), (0.1, 0.1)]
    return {"type": "way", "tags": {"natural": "water"}, "geometry": _pts(ring)}


def _coast_north_at(lon):
    """A coastline running north along `lon`, in 10 segments: water on the right (east)."""
    ys = np.linspace(-0.05, 1.05, 12)
    return {"type": "way", "tags": {"natural": "coastline"},
            "geometry": _pts([(lon, float(y)) for y in ys])}


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    """Cache in tmp_path; any Overpass call that is not explicitly faked fails loudly."""
    monkeypatch.setattr(ow, "OSM_CACHE", tmp_path / "water_cache")

    def boom(*a, **k):
        raise AssertionError("network call in an offline test")
    monkeypatch.setattr(ow._osm, "overpass_query", boom)
    monkeypatch.setattr(ow._osm, "overpass_wait", boom)


def _fake_overpass(monkeypatch, by_selector, calls=None):
    def fake(selector, bbox, attempts=ow.OVERPASS_ATTEMPTS):
        if calls is not None:
            calls.append(selector)
        out = by_selector[selector]
        if isinstance(out, Exception):
            raise out
        return out
    monkeypatch.setattr(ow, "_overpass", fake)


# ------------------------------------------------------------------------------ osm_water

def test_osm_water_rasterizes_a_lake_row_zero_south(monkeypatch):
    _fake_overpass(monkeypatch, {ow.SELECTORS["coast"]: [], ow.SELECTORS["water"]: [_lake()],
                                 ow.SELECTORS["way"]: []})
    res = ow.osm_water(BBOX, GRID, verbose=False)
    assert res["missing"] == []
    filled, outline = res["filled"], res["outline"]
    assert filled.shape == outline.shape == (GRID, GRID)
    assert filled.dtype == np.float32
    # lake spans lat 0.1-0.4 -> rows 4..16 from the south, lon 0.1-0.4 -> cols 4..16
    assert filled[10, 10] == 1.0
    assert filled[35, 10] == 0.0           # far north: dry
    assert filled[10, 35] == 0.0           # far east: dry
    ys, xs = np.nonzero(filled)
    assert ys.max() < 20 and xs.max() < 20  # all in the south-west quadrant
    assert 0.8 * 0.09 * GRID * GRID < filled.sum() < 1.4 * 0.09 * GRID * GRID
    # outline includes the filled mask's edges, and not its far interior
    assert outline[ys.min(), xs.min()] == 1.0
    assert outline[10, 10] == 0.0


def test_osm_water_reports_a_failed_layer_and_caches_only_good_ones(monkeypatch, tmp_path):
    _fake_overpass(monkeypatch, {ow.SELECTORS["coast"]: RuntimeError("429"),
                                 ow.SELECTORS["water"]: [_lake()], ow.SELECTORS["way"]: []})
    res = ow.osm_water(BBOX, GRID, verbose=False)
    assert res["missing"] == ["coast"]
    assert res["filled"][10, 10] == 1.0
    names = sorted(p.name for p in (tmp_path / "water_cache").iterdir())
    assert len(names) == 2
    assert not any(n.endswith("_coast.npz") for n in names)
    assert all(n.endswith("_water.npz") or n.endswith("_way.npz") for n in names)


def test_osm_water_second_call_is_served_from_the_cache(monkeypatch):
    calls = []
    sel = {ow.SELECTORS["coast"]: [], ow.SELECTORS["water"]: [_lake()], ow.SELECTORS["way"]: []}
    _fake_overpass(monkeypatch, sel, calls)
    first = ow.osm_water(BBOX, GRID, verbose=False)
    assert len(calls) == 3
    second = ow.osm_water(BBOX, GRID, verbose=False)
    assert len(calls) == 3                       # nothing fetched again
    np.testing.assert_array_equal(first["filled"], second["filled"])
    # A different grid is a different cache key.
    ow.osm_water(BBOX, GRID + 8, verbose=False)
    assert len(calls) == 6


def test_osm_water_all_layers_down_gives_empty_masks(monkeypatch):
    err = RuntimeError("down")
    _fake_overpass(monkeypatch, {s: err for s in ow.SELECTORS.values()})
    res = ow.osm_water(BBOX, GRID, verbose=False)
    assert sorted(res["missing"]) == sorted(ow.SELECTORS)
    assert not res["filled"].any() and not res["outline"].any()


def test_osm_water_recovers_the_sea_from_a_coastline(monkeypatch):
    _fake_overpass(monkeypatch, {ow.SELECTORS["coast"]: [_coast_north_at(0.5)],
                                 ow.SELECTORS["water"]: [], ow.SELECTORS["way"]: []})
    res = ow.osm_water(BBOX, GRID, verbose=False)
    filled = res["filled"]
    # Coastline runs north with the sea on its right: the eastern half is water.
    assert filled[:, 30].min() == 1.0
    assert filled[:, 5].max() == 0.0
    assert filled.mean() == pytest.approx(0.5, abs=0.06)
    # The shoreline itself is in the outline.
    assert res["outline"][:, 18:22].max() == 1.0


def test_coast_layer_sea_on_the_other_side_when_direction_reverses(monkeypatch):
    way = _coast_north_at(0.5)
    way["geometry"] = way["geometry"][::-1]     # now heading south: sea on the west
    layer = ow._coast_layer([way], (1.0, 0.0, 1.0, 0.0), GRID)
    assert layer["filled"][:, 5].min() == 1.0
    assert layer["filled"][:, 30].max() == 0.0


def test_coast_layer_discards_a_fill_that_escapes():
    # A short stub that does not cut the window: the fill would be the whole window.
    stub = {"geometry": _pts([(0.5, 0.45), (0.5, 0.55)] * 3)}
    layer = ow._coast_layer([stub], (1.0, 0.0, 1.0, 0.0), GRID)
    assert layer["filled"].sum() == 0.0


def test_coast_layer_no_lines_is_empty_and_members_are_read():
    empty = ow._coast_layer([], (1.0, 0.0, 1.0, 0.0), GRID)
    assert empty["filled"].sum() == 0 and empty["outline"].sum() == 0
    rel = {"type": "relation", "members": [{"geometry": _coast_north_at(0.5)["geometry"]}]}
    layer = ow._coast_layer([rel], (1.0, 0.0, 1.0, 0.0), GRID)
    assert layer["filled"][:, 30].min() == 1.0


# ------------------------------------------------------------------------------ helpers

def test_tag_width_m_parses_loose_values():
    assert ow._tag_width_m({"width": "2"}) == 2.0
    assert ow._tag_width_m({"width": "2 m"}) == 2.0
    assert ow._tag_width_m({"width": "1.5m"}) == 1.5
    assert ow._tag_width_m({"est_width": "7"}) == 7.0
    assert ow._tag_width_m({"width": "wide", "est_width": "3"}) == 3.0   # falls to the next tag
    assert ow._tag_width_m({"width": "250"}) is None                     # implausible
    assert ow._tag_width_m({"width": "0"}) is None
    assert ow._tag_width_m({}) is None


def test_geometries_closed_way_is_a_polygon_open_way_a_buffered_line():
    closed = _lake()
    open_way = {"type": "way", "geometry": _pts([(0.0, 0.5), (0.5, 0.5)])}
    geoms = ow._geometries([closed, open_way])
    assert len(geoms) == 2
    assert geoms[0].geom_type == "Polygon"
    assert geoms[0].area == pytest.approx(0.09)
    assert geoms[1].geom_type == "Polygon"          # a buffered line is a polygon too
    assert geoms[1].area < 0.01                      # a thin ribbon, not an area
    ribbon_h = (geoms[1].bounds[3] - geoms[1].bounds[1]) * ow.M_PER_DEG_LAT
    assert ribbon_h == pytest.approx(2 * ow.WATER_LINE_BUFFER_M, rel=0.05)


def test_geometries_stitches_a_relation_into_one_ring():
    left = [(0.1, 0.1), (0.1, 0.4), (0.4, 0.4)]
    right = [(0.4, 0.4), (0.4, 0.1), (0.1, 0.1)]
    rel = {"type": "relation", "members": [{"geometry": _pts(left)}, {"geometry": _pts(right)}]}
    geoms = ow._geometries([rel])
    assert len(geoms) == 1 and geoms[0].area == pytest.approx(0.09)


def test_geometries_linear_mode_strokes_walls_by_barrier_kind_and_tag():
    ring = [(0.1, 0.1), (0.4, 0.1), (0.4, 0.4), (0.1, 0.4), (0.1, 0.1)]
    plain = {"geometry": _pts(ring), "tags": {"barrier": "city_wall"}}
    tagged = {"geometry": _pts(ring), "tags": {"barrier": "city_wall", "width": "20"}}
    default = {"geometry": _pts(ring), "tags": {}}
    a, b, c = (ow._geometries([e], line_width_m=3.0)[0] for e in (plain, tagged, default))
    # A closed wall circuit is a ring of structure, not the enclosed area.
    assert a.area < 0.01
    assert a.area < b.area                                 # tag width 20 m > city_wall 6 m
    assert c.area < a.area                                 # default 3 m < city_wall 6 m
    assert a.area == pytest.approx(1.2 * 6.0 / ow.M_PER_DEG_LAT, rel=0.1)


def test_rasterize_flips_to_row_zero_south():
    from shapely.geometry import box
    south_strip = box(0.0, 0.0, 1.0, 0.25)
    arr = ow._rasterize([south_strip], BBOX, 20)
    assert arr.shape == (20, 20) and arr.dtype == np.float32
    assert arr[:4].min() == 1.0
    assert arr[10:].max() == 0.0


def test_draw_line_burns_and_clips():
    img = np.zeros((10, 10), np.uint8)
    ow._draw_line(img, 0, 5, 9, 5)
    assert img[5].sum() == 10
    ow._draw_line(img, -50, 0, -40, 0)           # entirely outside: no error, no change
    assert img.sum() == 10


def test_cache_roundtrip_and_corrupt_file(tmp_path):
    layer = {"filled": np.eye(4, dtype=np.float32), "outline": np.ones((4, 4), np.float32)}
    assert ow._cache_load("water", BBOX, 4) is None
    ow._cache_save("water", BBOX, 4, layer)
    got = ow._cache_load("water", BBOX, 4)
    np.testing.assert_array_equal(got["filled"], layer["filled"])
    assert got["filled"].dtype == np.float32
    ow._cache_path("water", BBOX, 4).write_bytes(b"not an npz")
    assert ow._cache_load("water", BBOX, 4) is None
    assert str(ow._cache_path("water", BBOX, 4)).startswith(str(tmp_path))


def test_overpass_builds_one_selector_query(monkeypatch):
    seen = {}

    def fake(query, **kw):
        seen["query"], seen["kw"] = query, kw
        return [{"id": 1}]

    monkeypatch.setattr(ow._osm, "overpass_query", fake)
    out = ow._overpass('[natural="water"]', (2.0, 1.0, 4.0, 3.0), attempts=2)
    assert out == [{"id": 1}]
    q = seen["query"]
    assert 'way[natural="water"](1.0,3.0,2.0,4.0)' in q       # (S,W,N,E)
    assert 'relation[natural="water"](1.0,3.0,2.0,4.0)' in q
    assert q.endswith("out geom;")
    assert seen["kw"]["attempts"] == 2
    assert seen["kw"]["urls"] == ow.OVERPASS_URLS
