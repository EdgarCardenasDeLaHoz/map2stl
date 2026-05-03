"""Backward-compatible shim for satellite + Earth Engine helpers.

All implementation has been unified into geo2stl.sat.
"""

from geo2stl.sat import (  # noqa: F401
    SAT_LAYER,
    initialize_earth_engine,
    get_aquatic_regions,
    calculate_scale_for_dimensions,
    fetch_bbox_image,
    fetch_satellite_tiles,
    fetch_water_mask,
    fetch_water_mask_images,
    fetch_sat_overlay,
    map_label_elevation,
)
