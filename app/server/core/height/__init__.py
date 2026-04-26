"""
core/height — Backward-compatibility shim.

All implementation has moved to city2stl.height.  This module re-exports
every public symbol so existing imports continue to work unchanged.
"""

from city2stl.height import (  # noqa: F401
    BBox,
    HeightResult,
    HeightProvider,
    merge_height_rasters,
    _resample,
)

