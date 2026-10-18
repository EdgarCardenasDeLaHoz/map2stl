"""River lines for the Edit map (app/server/core/river_lines.py, GET /api/terrain/river-lines).

Synthetic reaches stand in for HydroRIVERS: no downloads.
"""
import base64

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, MultiLineString

import app.server.core.river_lines as rl

N, S, E, W = 10.0, 0.0, 10.0, 0.0


def _reaches(lines, orders):
    return gpd.GeoDataFrame({"ORD_STRA": orders, "DIS_AV_CMS": [1.0] * len(orders), "geometry": lines},
                            crs="EPSG:4326")


def _decode(p):
    coords = np.frombuffer(base64.b64decode(p["coords_b64"]), dtype="<f4").reshape(-1, 2)
    offsets = np.frombuffer(base64.b64decode(p["offsets_b64"]), dtype="<u4")
    orders = np.frombuffer(base64.b64decode(p["orders_b64"]), dtype=np.uint8)
    return coords, offsets, orders


@pytest.fixture(autouse=True)
def _fresh_caches():
    rl.clear_caches()
    yield
    rl.clear_caches()


@pytest.fixture()
def fake_fetch(monkeypatch):
    calls = []

    def install(gdf):
        def fetch(north, south, east, west, min_order=3, **_kw):
            calls.append(min_order)
            return gdf[gdf["ORD_STRA"] >= min_order].reset_index(drop=True)
        monkeypatch.setattr("app.server.core.river_lines.fetch_hydrorivers", fetch)
        return calls
    return install


def test_lines_round_trip_and_are_simplified(fake_fetch):
    wiggly = LineString([(1 + 0.001 * i, 5 + (0.0001 if i % 2 else 0)) for i in range(4001)])  # 4 deg, tiny zigzag
    fake_fetch(_reaches([wiggly, LineString([(2, 1), (2, 9)])], [4, 6]))
    p = rl.river_lines_payload(N, S, E, W, min_order=3, width_px=1000, height_px=1000)
    coords, offsets, orders = _decode(p)
    assert p["count"] == 2 and len(offsets) == 3 and offsets[-1] == len(coords)
    assert sorted(orders.tolist()) == [4, 6]
    # 0.0001 deg zigzag is far below one pixel (0.01 deg): the 4001 points collapse to the ends.
    assert offsets[1] - offsets[0] <= 4 or offsets[2] - offsets[1] <= 4
    assert len(coords) < 20
    assert coords[:, 0].min() >= 0.99 and coords[:, 0].max() <= 5.01


def test_view_keeps_only_crossing_reaches_and_drops_sub_pixel_ones(fake_fetch):
    west_river = LineString([(1, 1), (1, 9)])
    east_river = LineString([(9, 1), (9, 9)])
    speck = LineString([(1.5, 5), (1.5005, 5)])               # 0.0005 deg: under 2 px even zoomed in
    fake_fetch(_reaches([west_river, east_river, speck], [3, 3, 3]))
    p = rl.river_lines_payload(N, S, E, W, min_order=3, view=(10, 0, 5, 0), width_px=500, height_px=1000)
    coords, _, _ = _decode(p)
    assert p["count"] == 1
    assert np.allclose(coords[:, 0], 1.0)


def test_order_floor_rises_to_fit_the_line_cap(fake_fetch, monkeypatch):
    monkeypatch.setattr(rl, "MAX_LINES", 5)
    lines = [LineString([(0.5 + 0.1 * i, 1), (0.5 + 0.1 * i, 9)]) for i in range(30)]
    orders = [1] * 20 + [4] * 4 + [7] * 6                     # 30 lines; orders >= 7 alone are 6
    fake_fetch(_reaches(lines, orders))
    p = rl.river_lines_payload(N, S, E, W, min_order=1)
    _, _, got = _decode(p)
    assert p["count"] == 5 and p["truncated"]
    assert p["order_floor"] == 7 and set(got.tolist()) == {7}

    monkeypatch.setattr(rl, "MAX_LINES", 10)
    rl.clear_caches()
    p = rl.river_lines_payload(N, S, E, W, min_order=1)
    _, _, got = _decode(p)
    assert p["order_floor"] == 4 and not p["truncated"] and p["count"] == 10
    assert set(got.tolist()) == {4, 7}


def test_multi_part_reaches_are_split_and_results_cached(fake_fetch):
    multi = MultiLineString([[(1, 1), (1, 4)], [(3, 6), (3, 9)]])
    calls = fake_fetch(_reaches([multi], [5]))
    a = rl.river_lines_payload(N, S, E, W, min_order=3)
    b = rl.river_lines_payload(N, S, E, W, min_order=3, view=(10, 0, 10, 0))
    _, offsets, orders = _decode(a)
    assert a["count"] == 2 and orders.tolist() == [5, 5] and len(offsets) == 3
    assert b is a and calls == [3]       # the same view is one cached payload, one fetch


def test_endpoint_returns_lines(client, fake_fetch):
    fake_fetch(_reaches([LineString([(1, 1), (8, 8)])], [5]))
    r = client.get("/api/terrain/river-lines", params={
        "north": N, "south": S, "east": E, "west": W, "min_order": 3,
        "view_north": 9, "view_south": 1, "view_east": 9, "view_west": 1, "width_px": 800, "height_px": 600})
    assert r.status_code == 200, r.text
    p = r.json()
    coords, offsets, orders = _decode(p)
    assert p["count"] == 1 and orders.tolist() == [5] and offsets.tolist() == [0, 2]
    assert np.allclose(coords, [[1, 1], [8, 8]])
    assert p["view"] == [9, 1, 9, 1]


def test_endpoint_rejects_a_bad_box(client):
    r = client.get("/api/terrain/river-lines", params={"north": 0, "south": 10, "east": 10, "west": 0})
    assert r.status_code == 400
