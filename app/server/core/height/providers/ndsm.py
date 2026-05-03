# backward-compat shim -- implementation in city2stl.height.providers.ndsm
from city2stl.height.providers.ndsm import *  # noqa: F401, F403
from city2stl.height.providers.ndsm import (
    NDSMProvider, _tile_name_glo30, _tile_url_glo30, _tile_url_fabdem,
    _tiles_for_bbox, _crop_to_bbox, _stitch_tiles, NDSM_CONFIDENCE,
)