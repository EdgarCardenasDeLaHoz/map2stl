"""
core/height/infill -- Backward-compatibility shim.

All implementation has moved to city2stl.height.infill.  This module
re-exports every public symbol so existing imports continue to work unchanged.
"""

from city2stl.height.infill import (  # noqa: F401
    infill_idw,
    infill_nearest,
)