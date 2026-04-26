"""
core/height/stl_import -- Backward-compatibility shim.

All implementation has moved to city2stl.height.stl_import.  This module
re-exports every public symbol so existing imports continue to work unchanged.
"""

from city2stl.height.stl_import import (  # noqa: F401
    stl_to_heightmap,
    _UP_AXIS_ROTATIONS,
)
