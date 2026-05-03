# backward-compat shim -- implementation in city2stl.height.providers.copernicus
from city2stl.height.providers.copernicus import *  # noqa: F401, F403
from city2stl.height.providers.copernicus import (
    CopernicusProvider, _is_in_europe, _parse_geotiff_bytes, _CONFIDENCE,
)