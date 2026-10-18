"""Lambert azimuthal equal-area (laea) and Lambert conformal conic (lcc) projections."""
import math

import numpy as np
import pytest

from geo2stl.projections import (
    expected_aspect_ratio,
    lcc_parameters,
    project_grid,
    projected_fraction,
    projected_shape,
)

# north, south, east, west: a Europe-sized box centred on 52 N, 10 E (the EPSG:3035 centre).
EUROPE = (62.0, 42.0, 30.0, -10.0)
LAT0, LON0 = 52.0, 10.0


def _laea_scalar(lat, lon, lat0=LAT0, lon0=LON0):
    """Snyder 24-2 .. 24-4 written out independently, unit sphere."""
    phi, phi0, dl = map(math.radians, (lat, lat0, lon - lon0))
    k = math.sqrt(2.0 / (1.0 + math.sin(phi0) * math.sin(phi)
                         + math.cos(phi0) * math.cos(phi) * math.cos(dl)))
    return (k * math.cos(phi) * math.sin(dl),
            k * (math.cos(phi0) * math.sin(phi)
                 - math.sin(phi0) * math.cos(phi) * math.cos(dl)))


def _tan_half(p):
    return math.tan(math.pi / 4 + p / 2)


def _lcc_scalar(lat, lon, bbox=EUROPE):
    """Snyder 15-1 .. 15-11 written out independently, unit sphere."""
    p1, p2 = (math.radians(v) for v in lcc_parameters(bbox))
    p0 = math.radians((bbox[0] + bbox[1]) / 2)
    lam0 = math.radians((bbox[2] + bbox[3]) / 2)
    n = math.log(math.cos(p1) / math.cos(p2)) / math.log(_tan_half(p2) / _tan_half(p1))
    big_f = math.cos(p1) * _tan_half(p1) ** n / n
    rho = big_f / _tan_half(math.radians(lat)) ** n
    rho0 = big_f / _tan_half(p0) ** n
    theta = n * (math.radians(lon) - lam0)
    return rho * math.sin(theta), rho0 - rho * math.cos(theta)


def _extent(fwd, bbox):
    north, south, east, west = bbox
    ts = np.linspace(0, 1, 1001)
    pts = ([fwd(south + t * (north - south), west) for t in ts]
           + [fwd(south + t * (north - south), east) for t in ts]
           + [fwd(north, west + t * (east - west)) for t in ts]
           + [fwd(south, west + t * (east - west)) for t in ts])
    xs, ys = zip(*pts, strict=True)
    return min(xs), max(xs), min(ys), max(ys)


@pytest.mark.parametrize('projection, scalar', [('laea', _laea_scalar), ('lcc', _lcc_scalar)])
def test_fraction_matches_independent_formula(projection, scalar):
    x0, x1, y0, y1 = _extent(scalar, EUROPE)
    for lat, lon in [(45.0, -5.0), (52.0, 10.0), (60.0, 25.0), (43.0, 28.0)]:
        x, y = scalar(lat, lon)
        fx, fy = projected_fraction(EUROPE, projection, lat, lon)
        assert float(fx) == pytest.approx((x - x0) / (x1 - x0), abs=1e-9)
        assert float(fy) == pytest.approx((y1 - y) / (y1 - y0), abs=1e-9)


def test_laea_textbook_values_at_the_centre():
    # At the centre k = 1 and (x, y) = (0, 0). Ten degrees east on the same parallel lies
    # east of it and, because parallels curve round the pole, above it.
    assert _laea_scalar(LAT0, LON0) == pytest.approx((0.0, 0.0), abs=1e-12)
    x, y = _laea_scalar(LAT0, LON0 + 10.0)
    assert x == pytest.approx(0.1071, abs=1e-3)
    assert y > 0.0


def test_centre_maps_to_the_middle():
    fx, fy = projected_fraction(EUROPE, 'laea', LAT0, LON0)
    assert float(fx) == pytest.approx(0.5, abs=1e-6)
    assert 0.3 < float(fy) < 0.7          # the south edge bows, so the middle is not exact


@pytest.mark.parametrize('projection', ['laea', 'lcc'])
def test_east_is_right_and_north_is_up(projection):
    fx_c, fy_c = projected_fraction(EUROPE, projection, LAT0, LON0)
    fx_e, _ = projected_fraction(EUROPE, projection, LAT0, LON0 + 10.0)
    _, fy_n = projected_fraction(EUROPE, projection, LAT0 + 5.0, LON0)
    assert float(fx_e) > float(fx_c)
    assert float(fy_n) < float(fy_c)      # y runs top-down


@pytest.mark.parametrize('projection', ['laea', 'lcc'])
def test_meridians_rotate_away_from_the_centre_meridian(projection):
    # A north step on the west meridian leans east of vertical (meridians converge northwards), on the east one leans west.
    def lean(lon):
        a = projected_fraction(EUROPE, projection, 45.0, lon)
        b = projected_fraction(EUROPE, projection, 55.0, lon)
        return float(b[0] - a[0])
    assert lean(-8.0) > 1e-3
    assert lean(28.0) < -1e-3
    assert abs(lean(LON0)) < 1e-9         # the centre meridian stays vertical


@pytest.mark.parametrize('projection', ['laea', 'lcc'])
def test_aspect_matches_shape_and_projected_grid(projection):
    aspect = expected_aspect_ratio(EUROPE, projection, (385, 600))
    assert 1.1 < aspect < 1.6
    rows, cols = projected_shape(EUROPE, projection, 600)
    out = project_grid(np.zeros((385, 600)), *EUROPE, projection,
                       clip_nans=False, target_dim=600)
    assert out.shape == (rows, cols)
    assert out.shape[1] / out.shape[0] == pytest.approx(aspect, rel=0.01)
    assert max(out.shape) == 600


@pytest.mark.parametrize('projection', ['laea', 'lcc'])
def test_projected_grid_pixels_hold_the_lat_lon_that_lands_there(projection):
    north, south, east, west = EUROPE
    m, n = 385, 600
    lat = np.linspace(north, south, m)[:, None] * np.ones((1, n))
    lon = np.ones((m, 1)) * np.linspace(west, east, n)[None, :]
    lat_out = project_grid(lat, *EUROPE, projection, clip_nans=False, target_dim=600)
    lon_out = project_grid(lon, *EUROPE, projection, clip_nans=False, target_dim=600)
    rows, cols = lat_out.shape
    for fr, fc in [(0.5, 0.5), (0.25, 0.4), (0.7, 0.3), (0.4, 0.8)]:
        r, c = int(fr * rows), int(fc * cols)
        fx, fy = projected_fraction(EUROPE, projection, lat_out[r, c], lon_out[r, c])
        assert float(fx) == pytest.approx(c / (cols - 1), abs=2.0 / cols)
        assert float(fy) == pytest.approx(r / (rows - 1), abs=2.0 / rows)
    assert np.isnan(lat_out).any()        # corners outside the box are fill


@pytest.mark.parametrize('projection', ['laea', 'lcc'])
def test_categorical_keeps_class_ids(projection):
    rng = np.random.default_rng(3)
    classes = np.array([10, 20, 30, 40, 50, 60, 80, 90], dtype=np.float64)
    arr = classes[rng.integers(0, len(classes), size=(120, 160))]
    out = project_grid(arr, *EUROPE, projection, clip_nans=False,
                       categorical=True, target_dim=200)
    vals = np.unique(out[np.isfinite(out)])
    assert set(vals).issubset(set(classes) | {0.0})   # 0 is the fill outside the box


def test_lcc_parameters_are_a_sixth_in_from_each_edge():
    p1, p2 = lcc_parameters(EUROPE)
    assert p1 == pytest.approx(42.0 + 20.0 / 6.0)
    assert p2 == pytest.approx(62.0 - 20.0 / 6.0)


def test_lcc_southern_hemisphere_has_north_up():
    bbox = (-20.0, -40.0, -50.0, -70.0)
    assert lcc_parameters(bbox) is not None
    _, fy_n = projected_fraction(bbox, 'lcc', -22.0, -60.0)
    _, fy_s = projected_fraction(bbox, 'lcc', -38.0, -60.0)
    assert float(fy_n) < float(fy_s)


@pytest.mark.parametrize('bbox', [(10.0, -10.0, 20.0, 0.0), (30.0, 30.0000001, 10.0, 0.0)])
def test_lcc_falls_back_to_equidistant_when_the_cone_is_undefined(bbox):
    assert lcc_parameters(bbox) is None
    assert expected_aspect_ratio(bbox, 'lcc', (100, 100)) == pytest.approx(
        expected_aspect_ratio(bbox, 'equidistant', (100, 100)))
    arr = np.random.default_rng(1).random((80, 100))
    a = project_grid(arr, *bbox, 'lcc', clip_nans=False, target_dim=100)
    b = project_grid(arr, *bbox, 'equidistant', clip_nans=False, target_dim=100)
    np.testing.assert_allclose(a, b, equal_nan=True)
