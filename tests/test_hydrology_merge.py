"""geo2stl.hydrology.merge_rivers_with_dem carves relative depths."""

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
