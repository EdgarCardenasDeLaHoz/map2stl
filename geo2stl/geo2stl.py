"""Legacy compatibility shim for historical ``geo2stl.geo2stl`` imports.

The owning implementations now live in:
  - geo2stl.tiles        — tile catalog and local SRTM stitching
  - geo2stl.projections  — projection logic
  - geo2stl.grid         — notebook-oriented grid helpers
"""

from __future__ import annotations

import numpy as np

from geo2stl.grid import mat2coor, proj_map_height
from geo2stl.projections import project_coordinates
from geo2stl.tiles import (
    crop_tile_np,
    get_tile_files,
    intersect_bbox,
    parse_extent_from_filename,
    stitch_tiles_no_rasterio,
    tile_files,
)


def proj_map_geo_to_2D(mat, NSEW, clip_out=True):
    """Legacy compatibility wrapper for the original cosine projection."""
    result, _ = project_coordinates(
        mat,
        tuple(np.array(NSEW)),
        projection='cosine',
        maintain_dimensions=False,
        fill_value=np.nan,
        clip_nans=clip_out,
    )
    return result
