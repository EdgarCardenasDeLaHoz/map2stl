"""
Compatibility wrapper: re-exports raster-scale utilities from geo2stl.raster.

Deprecated. Use geo2stl.raster directly.
"""

from geo2stl.raster import bbox_longer_side_m, clamp_esa_scale, derive_sat_scale

__all__ = ["bbox_longer_side_m", "clamp_esa_scale", "derive_sat_scale"]
