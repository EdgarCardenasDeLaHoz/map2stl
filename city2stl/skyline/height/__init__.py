"""Re-export shim: the height package is ``city2stl.height``.

Kept for one release so old ``from city2stl.skyline.height import X`` imports
keep working; new code imports ``city2stl.height``.
"""

from city2stl.height import (  # noqa: F401
    BUILDING_RESOLUTION_LIMIT_M,
    BUILDING_SCALE_M,
    BBox,
    HeightProvider,
    HeightResult,
    _filter_outliers,
    _resample,
    merge_height_rasters,
    provider_stats,
    resolution_priority,
)
