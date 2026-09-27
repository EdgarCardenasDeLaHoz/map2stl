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
