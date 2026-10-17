"""geo2stl.hydrology: merge_rivers_with_dem carves relative depths; the order burn."""

import numpy as np

from geo2stl.hydrology import merge_rivers_with_dem


def test_rivers_are_cut_below_their_own_terrain():
    dem = np.full((4, 4), 1200.0, np.float32)
    rivers = np.zeros((4, 4), np.float32)
    rivers[1, :] = -5.0
    out = merge_rivers_with_dem(dem, rivers)
    assert np.allclose(out[1], 1195.0)
    assert np.allclose(out[0], 1200.0)


def test_union_water_surface_lowers_sea_with_the_rivers():
    """Rivers alone notch the shore at each mouth; with the sea at the same depth the
    river runs into it with no step (no ring around the coast)."""
    from geo2stl.hydrology import union_water_surface

    rivers = np.zeros((6, 8), np.float32)
    rivers[3, 2:6] = -3.0                      # a river reaching the coast at column 6
    sea = np.zeros((6, 8), bool)
    sea[:, 6:] = True                          # open water east of the shore
    out = union_water_surface(rivers, sea, -5.0)
    assert np.all(out[:, 6:] == -5.0)          # sea lowered by the full depth
    assert np.all(out[3, 2:6] == -3.0)         # river keeps its own depth
    assert np.all(out[0, :6] == 0.0)           # land untouched
    assert union_water_surface(rivers, None, -5.0) is rivers


def _old_rasterize_orders(gdf, north, south, east, west, dim, width_factor=1.0):
    """rasterize_hydrorivers_orders before the single pass: one burn per Strahler order."""
    import shapely
    from numpy2stl.raster import burn_polygons

    pixel_deg = (north - south) / dim
    min_buf_deg = pixel_deg * 0.6 * float(width_factor)
    order_arr = gdf["ORD_STRA"].clip(lower=1, upper=9).astype(int).to_numpy()
    buffers = np.maximum(min_buf_deg, min_buf_deg * order_arr / 3.0)
    arr = gdf.geometry.simplify(pixel_deg, preserve_topology=False).to_numpy().copy()
    wide = (2 * buffers > 1.5 * pixel_deg)
    if wide.any():
        arr[wide] = shapely.buffer(arr[wide], buffers[wide], quad_segs=2)
    keep = ~shapely.is_empty(arr)
    buffered, order_arr = arr[keep], order_arr[keep]
    orders = np.zeros((dim, dim), dtype=np.uint8)
    for o in sorted(set(int(x) for x in order_arr)):
        mask = order_arr == o
        layer = burn_polygons(list(buffered[mask]), (dim, dim), bounds=(west, south, east, north),
                              values=float(o), mode="set", all_touched=True, dtype=np.float32)
        orders[layer > 0] = o
    return orders


def test_single_pass_order_burn_matches_the_per_order_passes():
    """One burn sorted by order paints the same highest-order grid as nine burns."""
    import geopandas as gpd
    from shapely.geometry import LineString

    from geo2stl.hydrology import rasterize_hydrorivers_orders

    rng = np.random.default_rng(3)
    lines, orders = [], []
    for _ in range(150):                       # short random reaches crossing in a 1 deg box
        start = rng.uniform(-0.1, 1.1, size=2)
        steps = rng.normal(0.0, 0.08, size=(int(rng.integers(1, 5)), 2))
        lines.append(LineString(np.vstack([start, start + np.cumsum(steps, axis=0)])))
        orders.append(int(rng.integers(0, 12)))  # 0 and 10-11 exercise the 1..9 clip
    # Overlapping reaches of every order along one line: the highest must win there.
    for o in (2, 9, 5, 1, 7):
        lines.append(LineString([(0.0, 0.5), (1.0, 0.5)]))
        orders.append(o)
    lines += [LineString(), None]              # empty and missing geometries are skipped
    orders += [4, 6]
    gdf = gpd.GeoDataFrame({"ORD_STRA": orders, "geometry": lines}, crs="EPSG:4326")

    # width factors: lines and buffered polygons mixed (1.0, 0.5, 2.5), no reach
    # buffered (0.3), every reach buffered (5.0)
    for dim, wf in ((64, 1.0), (128, 0.5), (97, 2.5), (64, 0.3), (80, 5.0)):
        want = _old_rasterize_orders(gdf, 1.0, 0.0, 1.0, 0.0, dim, wf)
        got = rasterize_hydrorivers_orders(gdf, 1.0, 0.0, 1.0, 0.0, dim, wf)
        assert got.dtype == np.uint8 and got.shape == (dim, dim)
        assert len(set(np.unique(want).tolist())) > 5   # many orders overlap in the grid
        np.testing.assert_array_equal(got, want)
        assert (got[dim // 2 - 1:dim // 2 + 1] == 9).any()
