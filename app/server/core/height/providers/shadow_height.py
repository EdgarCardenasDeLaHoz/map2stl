# backward-compat shim -- implementation in city2stl.height.providers.shadow_height
from city2stl.height.providers.shadow_height import *  # noqa: F401, F403
from city2stl.height.providers.shadow_height import (
    ShadowHeightProvider, _estimate_sun_elevation, _shadow_length_to_height,
    _infer_from_rgb, _downsample_height, _CONFIDENCE,
)