"""geo2stl.geo: metres-per-degree constants, bbox sizes and GeoGrid."""

import math

import numpy as np
import pytest

from geo2stl.geo import (
    M_PER_DEG_LAT,
    M_PER_DEG_LON_EQ,
    GeoGrid,
    bbox_diagonal_km,
    bbox_size_m,
    m_per_deg_lon,
)

BBOX = {"north": 41.0, "south": 40.0, "east": -73.0, "west": -75.0}


def test_constants_are_wgs84():
    assert M_PER_DEG_LAT == 110_574.0
    assert M_PER_DEG_LON_EQ == 111_320.0


def test_m_per_deg_lon_scalar_and_array():
    assert m_per_deg_lon(0.0) == pytest.approx(111_320.0)
    assert m_per_deg_lon(60.0) == pytest.approx(55_660.0)
    arr = m_per_deg_lon(np.array([0.0, 60.0, 90.0]))
    assert arr.shape == (3,)
    np.testing.assert_allclose(arr, [111_320.0, 55_660.0, 0.0], atol=1e-6)


def test_bbox_size_m_uses_mid_latitude():
    w, h = bbox_size_m(BBOX)
    assert h == pytest.approx(110_574.0)
    assert w == pytest.approx(2 * 111_320.0 * math.cos(math.radians(40.5)))


def test_bbox_size_m_order_independent():
    flipped = {"north": 40.0, "south": 41.0, "east": -75.0, "west": -73.0}
    assert bbox_size_m(flipped) == pytest.approx(bbox_size_m(BBOX))


def test_bbox_diagonal_km():
    w, h = bbox_size_m(BBOX)
    assert bbox_diagonal_km(BBOX) == pytest.approx(math.hypot(w, h) / 1000)


def test_grid_pixel_centres_north_up():
    g = GeoGrid(BBOX, (10, 20))
    lon, lat = g.px_to_lonlat(0, 0)
    assert lon == pytest.approx(-75.0 + 0.05)
    assert lat == pytest.approx(41.0 - 0.05)
    # bbox corners sit at half-pixel offsets
    col, row = g.lonlat_to_px(-75.0, 41.0)
    assert (col, row) == pytest.approx((-0.5, -0.5))
    col, row = g.lonlat_to_px(-73.0, 40.0)
    assert (col, row) == pytest.approx((19.5, 9.5))


def test_grid_south_row0():
    g = GeoGrid(BBOX, (10, 20), row0="south")
    lon, lat = g.px_to_lonlat(0, 0)
    assert lat == pytest.approx(40.0 + 0.05)
    col, row = g.lonlat_to_px(-74.0, 41.0)
    assert (col, row) == pytest.approx((9.5, 9.5))


@pytest.mark.parametrize("row0", ["north", "south"])
def test_grid_round_trip_arrays(row0):
    g = GeoGrid(BBOX, (37, 53), row0=row0)
    cols = np.array([0.0, 3.25, 52.0])
    rows = np.array([0.0, 17.5, 36.0])
    lon, lat = g.px_to_lonlat(cols, rows)
    c2, r2 = g.lonlat_to_px(lon, lat)
    np.testing.assert_allclose(c2, cols, atol=1e-9)
    np.testing.assert_allclose(r2, rows, atol=1e-9)


def test_grid_m_per_px_is_mean_of_axes():
    g = GeoGrid(BBOX, (100, 200))
    w, h = bbox_size_m(BBOX)
    assert g.m_per_px == pytest.approx((w / 200 + h / 100) / 2)


def test_grid_rejects_bad_row0():
    with pytest.raises(ValueError):
        GeoGrid(BBOX, (1, 1), row0="top")
