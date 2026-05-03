"""Legacy compatibility shim for historical ``geo2stl.geo2stl`` imports.

The owning implementations now live in:
  - geo2stl.tiles        — tile catalog and local SRTM stitching
  - geo2stl.projections  — projection logic
  - geo2stl.grid         — notebook-oriented grid helpers
"""

from __future__ import annotations

from geo2stl.grid import mat2coor, proj_map_height
from geo2stl.projections import proj_map_geo_to_2D
from geo2stl.tiles import (
    crop_tile_np,
    get_tile_files,
    intersect_bbox,
    parse_extent_from_filename,
    stitch_tiles_no_rasterio,
    tile_files,
)
