"""
core/dem.py -- Backward-compatibility shim.

All implementation has moved to geo2stl.dem.  This module re-exports every
public symbol so existing imports continue to work unchanged.
"""

from geo2stl.dem import (  # noqa: F401
    fetch_layer_data,
    fetch_local_dem,
    fetch_h5_dem,
    fetch_esa_water_layer,
    fetch_opentopo_dem,
    apply_layer_processing,
    blend_layers,
    upsample_dem,
    make_dem_payload,
    compute_raw_dem,
    OPENTOPO_DATASETS,
)