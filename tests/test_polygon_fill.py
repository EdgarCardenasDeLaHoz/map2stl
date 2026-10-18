"""geo2stl.polygon_fill fills exactly the cells rasterio does, for polygons and segment buffers."""

import numpy as np
import pytest
import shapely
import shapely.affinity
from shapely.geometry import LineString, MultiPolygon, Polygon, box

from geo2stl.polygon_fill import burn_polygons_max, burn_segment_buffers_max, stadium_rings

burn_polygons = pytest.importorskip("numpy2stl.raster").burn_polygons

H, W = 11, 13


def _ref(polys, values, bounds):
    return burn_polygons(list(polys), (H, W), bounds=bounds, values=list(values), mode="max")


def _shapes(rng, scale):
    """Polygons whose vertices and horizontal edges often sit exactly on cell centres."""
    out = [
        box(1.5, 1.5, 4.5, 3.5),                                   # edges on centres
        box(-2, -2, 1.5, 2.5),                                     # partly off the grid
        box(2.2, 2.3, 2.4, 2.6),                                   # covers no centre
        Polygon([(0, 0.5), (3, 0.5), (1.5, 3)]),                   # bottom edge on a centre row
        Polygon([(5.3, 5.2), (7.7, 5.2), (6, 2)]),
        Polygon(box(4, 0, 9, 4).exterior, [box(5.5, 1.5, 7.5, 2.5).exterior]),  # hole on centres
        MultiPolygon([box(0, 4, 1.5, 6.5), box(2.5, 4.5, 3.5, 7)]),
        box(8.5, 3.5, 20, 20),                                     # past the far corner
    ]
    out += [shapely.reverse(p) for p in out[:6]]                   # either ring orientation
    for _ in range(150):
        pts = rng.integers(-3, 2 * W + 3, size=(rng.integers(3, 8), 2)) / 2.0
        out.append(shapely.make_valid(Polygon(pts)))
        c = rng.integers(0, 2 * W, 2) / 2.0
        d = int(rng.integers(1, 4))
        out.append(box(c[0], c[1], c[0] + 2 * d, c[1] + 2 * d).difference(
            box(c[0] + rng.integers(1, 3) / 2, c[1] + rng.integers(1, 3) / 2, c[0] + d, c[1] + d)))
        a = rng.uniform(-1, W + 1, 2)
        out.append(shapely.buffer(LineString([a, a + rng.uniform(-3, 3, 2)]),
                                  rng.uniform(0.3, 1.2), quad_segs=int(rng.integers(1, 5))))
    out = [shapely.affinity.scale(p, scale, scale, origin=(0, 0)) for p in out]
    return [p for p in out if not p.is_empty and p.geom_type in ("Polygon", "MultiPolygon")]


@pytest.mark.parametrize("scale", [1.0, 4694.123, 0.37])
def test_matches_rasterio_per_polygon(scale):
    rng = np.random.default_rng(7)
    bounds = (0.0, 0.0, W * scale, H * scale)
    for p in _shapes(rng, scale):
        got = burn_polygons_max([p], [1.0], [0], 1, (H, W), bounds)[0]
        np.testing.assert_array_equal(got, _ref([p], [1.0], bounds), err_msg=p.wkt)


def test_layers_keep_the_largest_value():
    rng = np.random.default_rng(3)
    bounds = (0.0, 0.0, float(W), float(H))
    polys = _shapes(rng, 1.0)
    vals = rng.uniform(0.5, 9.0, len(polys))
    layer = rng.integers(0, 3, len(polys))
    got = burn_polygons_max(polys, vals, layer, 3, (H, W), bounds)
    assert got.shape == (3, H, W)
    for k in range(3):
        sel = np.flatnonzero(layer == k)
        np.testing.assert_array_equal(got[k], _ref([polys[i] for i in sel], vals[sel], bounds))


def test_offset_bounds_and_empty_input():
    bounds = (100.0, -50.0, 100.0 + W * 2.0, -50.0 + H * 2.0)
    p = box(103.0, -45.0, 111.0, -37.0)
    np.testing.assert_array_equal(burn_polygons_max([p], [2.5], [0], 1, (H, W), bounds)[0],
                                  _ref([p], [2.5], bounds))
    assert not burn_polygons_max([], [], [], 2, (H, W), bounds).any()
    assert not burn_polygons_max([Polygon()], [1.0], [1], 2, (H, W), bounds).any()


def _segments(rng, scale, n=600):
    """Segments with ends often on cell centres or axis-aligned, plus degenerate ones."""
    p0 = rng.uniform(-1, W + 1, (n, 2))
    p0[: n // 3] = np.round(p0[: n // 3] * 2) / 2                 # on centres and corners
    p1 = p0 + rng.uniform(-3, 3, (n, 2))
    p1[n // 3: n // 2, 1] = p0[n // 3: n // 2, 1]                 # horizontal
    p1[n // 2: n // 2 + 20, 0] = p0[n // 2: n // 2 + 20, 0]       # vertical
    p1[-10:] = p0[-10:] + 1e-12                                    # (nearly) zero length
    r = rng.uniform(0.2, 1.5, n)
    r[: n // 6] = 0.5                                             # caps through centres
    return p0 * scale, p1 * scale, r * scale


@pytest.mark.parametrize("scale", [1.0, 4694.123, 0.37])
def test_stadium_rings_are_geos_buffers(scale):
    p0, p1, r = _segments(np.random.default_rng(5), scale)
    ok = np.hypot(*(p1 - p0).T) > 1e-3 * r
    lines = shapely.linestrings(np.stack([p0, p1], axis=1)[ok])
    geos = shapely.get_coordinates(shapely.buffer(lines, r[ok], quad_segs=2)).reshape(-1, 11, 2)
    np.testing.assert_allclose(stadium_rings(p0[ok], p1[ok], r[ok]), geos, rtol=0, atol=1e-12 * scale * 20)


@pytest.mark.parametrize("scale", [1.0, 4694.123, 0.37])
def test_segment_buffers_burn_like_geos_buffers(scale):
    rng = np.random.default_rng(11)
    p0, p1, r = _segments(rng, scale)
    vals = rng.uniform(0.5, 9.0, len(r))
    layer = rng.integers(0, 2, len(r))
    bounds = (0.0, 0.0, W * scale, H * scale)
    shapes = shapely.buffer(shapely.linestrings(np.stack([p0, p1], axis=1)), r, quad_segs=2)
    want = burn_polygons_max(shapes, vals, layer, 2, (H, W), bounds)
    acc = np.full((2, H, W), -np.inf)
    _, fragile = burn_segment_buffers_max(p0, p1, r, vals, layer, 2, (H, W), bounds, out=acc)
    assert fragile[-10:].all() and not fragile.all()
    burn_polygons_max(shapes[fragile], vals[fragile], layer[fragile], 2, (H, W), bounds, out=acc)
    acc[acc == -np.inf] = 0.0
    np.testing.assert_array_equal(acc, want)
    np.testing.assert_array_equal(want[0], _ref(shapes[layer == 0], vals[layer == 0], bounds))
