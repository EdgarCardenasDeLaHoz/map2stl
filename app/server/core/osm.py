"""
core/osm.py -- shim: re-exports all public symbols from city2stl sub-modules.

Implementation has been split into focused library files:
  - city2stl/heights.py  : _reduce_buildings, _fill_heights, enhance_buildings_with_raster
  - city2stl/rasterize.py: _count_verts, _empty_fc, rasterize_city_data
  - city2stl/fetch.py    : fetch_osm_data (and internal fetch helpers)

This file exists so that existing router and test imports continue to work:
  from app.server.core.osm import fetch_osm_data, rasterize_city_data
  from app.server.core.osm import enhance_buildings_with_raster
  from app.server.core.osm import _fill_heights, enhance_buildings_with_raster
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from city2stl.heights import (   # noqa: F401
    _reduce_buildings,
    _fill_heights,
    enhance_buildings_with_raster,
)

from city2stl.rasterize import (  # noqa: F401
    _count_verts,
    _empty_fc,
    rasterize_city_data,
)

from city2stl.fetch import (  # noqa: F401
    fetch_osm_data,
)