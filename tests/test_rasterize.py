"""Tests for city2stl/rasterize.py: building heights burned by rasterize_city_data.

Roof shapes are built as meshes and tested in tests/test_roofs.py; the raster roof
painters these tests once targeted were never implemented, so their permanently
skipped tests were removed.
"""

from __future__ import annotations

import numpy as np
import pytest

from city2stl.rasterize import rasterize_city_data


def _building_geojson(north, south, east, west, height_m, roof_shape="flat",
                      roof_height_m=None):
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "height_m": height_m,
                "roof:shape": roof_shape,
                "roof_height_m": roof_height_m,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [west, south], [east, south],
                    [east, north], [west, north],
                    [west, south],
                ]],
            },
        }],
    }


# -- Null / missing building heights --
def _plain_building(height_m):
    n, s, e, w = 40.001, 40.000, -75.000, -75.001
    fc = _building_geojson(n, s, e, w, height_m=height_m)
    empty = {"type": "FeatureCollection", "features": []}
    res = rasterize_city_data(n, s, e, w, dim=32, buildings_geojson=fc,
                              roads_geojson=empty, waterways_geojson=empty)
    return np.array(res["values"], dtype=np.float32).reshape(32, 32)


def test_rasterize_null_height_uses_default():
    """height_m=None burns the building at the default height, not dropped."""
    grid = _plain_building(None)
    assert grid.max() == pytest.approx(10.0, abs=0.01)


def test_rasterize_explicit_height_kept():
    grid = _plain_building(25.0)
    assert grid.max() == pytest.approx(25.0, abs=0.01)
