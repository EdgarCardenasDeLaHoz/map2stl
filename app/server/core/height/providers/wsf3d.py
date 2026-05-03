# backward-compat shim -- implementation in city2stl.height.providers.wsf3d
from city2stl.height.providers.wsf3d import *  # noqa: F401, F403
from city2stl.height.providers.wsf3d import (
    WSF3DProvider, tile_name, tiles_for_bbox,
    _lon_label, _lat_label, _download_tile, _GAIN,
)