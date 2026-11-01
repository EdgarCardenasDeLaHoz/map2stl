"""city2stl.registration.osm_model: OSM elements as geometry, coverage and tag classes (offline)."""

from __future__ import annotations

import gzip
import json

import numpy as np
import pytest
from shapely.geometry import box

from city2stl.registration import osm_model
from city2stl.registration.osm_model import (
    areal,
    building_class,
    coverage,
    points,
    raised,
    road_width,
    stroked,
    sunk,
)

BBOX = (40.01, 40.0, -75.0, -75.01)   # N, S, E, W


def _pts(coords):
    return [{"lat": lat, "lon": lon} for lon, lat in coords]


def _way(coords, tags=None, id_=1):
    return {"type": "way", "id": id_, "tags": tags or {}, "geometry": _pts(coords)}


SQUARE = [(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001), (0, 0)]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call in an offline test")
    monkeypatch.setattr(osm_model._ow, "_overpass", boom)
    monkeypatch.setattr(osm_model, "_nodes", boom)
    monkeypatch.setattr("geo2stl.osm.overpass_query", boom)


# ------------------------------------------------------------------------------ elements cache

def test_elements_fetches_once_then_reads_the_gzip_cache(tmp_path, monkeypatch):
    calls = []

    def fake(selector, bbox):
        calls.append((selector, tuple(bbox)))
        return [{"type": "way", "id": 7}]

    monkeypatch.setattr(osm_model._ow, "_overpass", fake)
    cache = tmp_path / "c"
    a = osm_model.elements('["building"]', BBOX, cache, "buildings")
    b = osm_model.elements('["building"]', BBOX, cache, "buildings")
    assert a == b == [{"type": "way", "id": 7}]
    assert calls == [('["building"]', BBOX)]
    files = list(cache.iterdir())
    assert len(files) == 1 and files[0].name.startswith("osm_buildings_")
    assert files[0].name.endswith(".json.gz")
    with gzip.open(files[0], "rt", encoding="utf-8") as f:
        assert json.load(f) == a
    # A different kind is a different file.
    osm_model.elements('["building"]', BBOX, cache, "other")
    assert len(calls) == 2


def test_elements_refetches_a_truncated_cache_file(tmp_path, monkeypatch):
    monkeypatch.setattr(osm_model._ow, "_overpass", lambda s, b: [{"id": 1}])
    cache = tmp_path / "c"
    osm_model.elements("[x]", BBOX, cache, "k")
    path = next(cache.iterdir())
    path.write_bytes(b"\x1f\x8b not really gzip")
    calls = []
    monkeypatch.setattr(osm_model._ow, "_overpass", lambda s, b: calls.append(1) or [{"id": 2}])
    assert osm_model.elements("[x]", BBOX, cache, "k") == [{"id": 2}]
    assert calls == [1]
    # and the rewritten file is good again
    assert osm_model.elements("[x]", BBOX, cache, "k") == [{"id": 2}]
    assert not list(cache.glob("*.tmp"))


def test_elements_nodes_flag_uses_the_node_query(tmp_path, monkeypatch):
    monkeypatch.setattr(osm_model, "_nodes", lambda sel, bbox: [{"type": "node", "id": 3}])
    got = osm_model.elements('["natural"="tree"]', BBOX, tmp_path, "trees", nodes=True)
    assert got == [{"type": "node", "id": 3}]


# ------------------------------------------------------------------------------ geometry

def test_areal_way_polygon_and_open_way_skipped():
    els = [_way(SQUARE, {"building": "yes"}),
           _way([(0, 0), (1, 1), (2, 0)], {"building": "yes"}, id_=2),   # open
           {"type": "way", "id": 3, "geometry": []},
           {"type": "node", "id": 4}]
    out = areal(els)
    assert len(out) == 1
    geom, tags = out[0]
    assert tags == {"building": "yes"}
    assert geom.area == pytest.approx(1e-6)


def test_areal_relation_keeps_inner_ring_as_hole():
    outer = [(0, 0), (0.004, 0), (0.004, 0.004), (0, 0.004), (0, 0)]
    inner = [(0.001, 0.001), (0.003, 0.001), (0.003, 0.003), (0.001, 0.003), (0.001, 0.001)]
    rel = {"type": "relation", "tags": {"building": "yes"}, "members": [
        {"role": "outer", "geometry": _pts(outer)},
        {"role": "inner", "geometry": _pts(inner)}]}
    (geom, _), = areal([rel])
    assert geom.area == pytest.approx(0.004 ** 2 - 0.002 ** 2)
    (solid, _), = areal([rel], keep_holes=False)
    assert solid.area == pytest.approx(0.004 ** 2)


def test_areal_relation_without_outer_or_with_garbage_is_skipped():
    inner_only = {"type": "relation", "members": [{"role": "inner", "geometry": _pts(SQUARE)}]}
    broken = {"type": "relation", "members": None}
    assert areal([inner_only, broken]) == []


def test_areal_stitches_split_outer_ways():
    a = [(0, 0), (0.002, 0), (0.002, 0.002)]
    b = [(0.002, 0.002), (0, 0.002), (0, 0)]
    rel = {"type": "relation", "members": [{"role": "outer", "geometry": _pts(a)},
                                           {"role": "outer", "geometry": _pts(b)}]}
    (geom, _), = areal([rel])
    assert geom.area == pytest.approx(0.002 ** 2)


def test_stroked_buffers_to_width_in_metres_and_skips_unwidthed():
    # 100 m east-west line at lat 40: stroked to 10 m wide.
    lon_per_m = 1.0 / (111320.0 * np.cos(np.radians(40.0)))
    line = [(-75.0, 40.0), (-75.0 + 100 * lon_per_m, 40.0)]
    els = [_way(line, {"highway": "x"}), _way(line, {"highway": "skip"}, id_=2)]
    out = stroked(els, lambda t: 10.0 if t["highway"] == "x" else None)
    assert len(out) == 1
    g, tags = out[0]
    minx, miny, maxx, maxy = g.bounds
    height_m = (maxy - miny) * osm_model.M_PER_DEG
    assert height_m == pytest.approx(10.0, rel=0.02)
    assert (maxx - minx) / lon_per_m == pytest.approx(100.0, rel=0.02)   # flat caps
    assert tags == {"highway": "x"}


def test_stroked_reads_relation_members_and_ignores_short_lines():
    line = [(0, 0), (0.001, 0)]
    rel = {"type": "relation", "tags": {"railway": "rail"},
           "members": [{"geometry": _pts(line)}, {"geometry": _pts([(0, 0)])}]}
    out = stroked([rel], lambda t: 6.0)
    assert len(out) == 1


def test_points_discs_have_the_requested_radius():
    node = {"type": "node", "lat": 40.0, "lon": -75.0, "tags": {"natural": "tree"}}
    (g, tags), = points([node, {"type": "way"}], lambda t: 10.0)
    assert tags == {"natural": "tree"}
    # lat extent of the disc is 2*radius metres
    assert (g.bounds[3] - g.bounds[1]) * osm_model.M_PER_DEG == pytest.approx(20.0, rel=0.03)
    assert g.contains(g.centroid)


# ------------------------------------------------------------------------------ coverage

def test_coverage_is_fractional_and_row_zero_is_south():
    N, S, E, W = BBOX
    midlat, midlon = (N + S) / 2, (E + W) / 2
    # Cover the south-west quarter of the window.
    sw = box(W, S, midlon, midlat)
    cov = coverage([sw], BBOX, grid=8)
    assert cov.shape == (8, 8) and cov.dtype == np.float32
    assert cov[:4, :4].min() == 1.0
    assert cov[4:, :].max() == 0.0 and cov[:, 4:].max() == 0.0
    assert cov[0, 0] == 1.0 and cov[7, 0] == 0.0   # south covered, north not


def test_coverage_gives_partial_cells_a_fraction():
    N, S, E, W = BBOX
    cell_lon = (E - W) / 8
    # A strip covering exactly the western half of column 0.
    half = box(W, S, W + cell_lon / 2, N)
    cov = coverage([half], BBOX, grid=8)
    assert cov[:, 0] == pytest.approx(0.5, abs=0.13)
    assert cov[:, 1:].max() == 0.0
    assert coverage([], BBOX, grid=8).sum() == 0


# ------------------------------------------------------------------------------ tag models

def test_road_width_rules():
    assert road_width({"highway": "residential"}) == 8
    assert road_width({"highway": "residential", "width": "12 m"}) == 12.0
    assert road_width({"highway": "residential", "width": "200"}) == 8       # implausible
    assert road_width({"highway": "residential", "lanes": "4"}) == 14.0      # 3.5 * 4 > 8
    assert road_width({"highway": "primary", "lanes": "2"}) == 14            # class width wins
    assert road_width({"highway": "footway", "lanes": "4"}) == 3             # paths ignore lanes
    assert road_width({"highway": "nonsense"}) is None
    assert road_width({"building": "yes"}) is None
    assert road_width({"highway": "service", "width": "abc"}) == 5


def test_raised_and_sunk():
    assert raised({"bridge": "yes"}) and raised({"bridge": "viaduct"})
    assert not raised({"bridge": "no"})
    assert raised({"layer": "1"}) and not raised({"layer": "0"}) and not raised({"layer": "-1"})
    assert not raised({})
    assert sunk({"tunnel": "yes"}) and not sunk({"tunnel": "no"})
    assert sunk({"covered": "yes"}) and sunk({"layer": "-2"}) and sunk({"location": "underground"})
    assert not sunk({"layer": "1"}) and not sunk({})


@pytest.mark.parametrize("tags,expected", [
    ({"building": "yes"}, "building"),
    ({"building": "apartments"}, "building"),
    ({"building": "no"}, "not_a_building"),
    ({"building": "construction"}, "construction"),
    ({"building": "proposed"}, "construction"),
    ({"building": "ruins"}, "ruins"),
    ({"building": "yes", "ruins": "yes"}, "ruins"),
    ({"building": "yes", "tunnel": "yes"}, "underground"),
    ({"building": "yes", "parking": "underground"}, "underground"),
    ({"building": "carport"}, "roof"),
    ({"building": "yes", "building:levels": "0"}, "roof"),
    ({"building": "garage"}, "small"),
    ({"building": "shed"}, "small"),
    ({"building": "parking"}, "parking"),
    ({"building": "yes", "amenity": "parking"}, "parking"),
    ({"building": "bridge"}, "bridge"),
    ({"building": "yes", "man_made": "bridge"}, "bridge"),
    ({"building": "stadium"}, "stadium"),
    ({}, "building"),
])
def test_building_class(tags, expected):
    assert building_class(tags) == expected


def test_standing_classes_exclude_the_non_standing():
    for cls in ("not_a_building", "construction", "ruins", "underground"):
        assert cls not in osm_model.STANDING
    for cls in ("building", "stadium", "small", "parking", "bridge", "roof"):
        assert cls in osm_model.STANDING
