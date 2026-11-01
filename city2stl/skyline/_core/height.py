"""skyline._core.height — extracted from pipeline.py (A1 split)."""

from __future__ import annotations

import logging

from .height_aggregate import (  # noqa: F401
    aggregate_building_heights,
)
from .height_estimate import (  # noqa: F401
    _ground_elev_m,
    _tag_filter_enabled,
    augment_estimates_with_depth,
    estimate_heights_from_registration,
)
from .height_withhold import (  # noqa: F401
    TAGGED_SOURCES,
    UNTAGGED_FALLBACK_M,
    _high_rise_height,
    _prefer_tags_enabled,
    _satellite_low,
    _seed_from_view_name,
    _withhold_single_enabled,
    _withhold_untagged_enabled,
    untagged_fallback,
    withhold_untagged_street_view,
)

# F-CLEAN14: the F-SKY12 depth except-branches reference ``logger`` but the
# module never defined one (latent NameError, only reachable on a depth-module
# failure). Defined here so those branches log instead of crashing.
logger = logging.getLogger(__name__)

__all__ = [
    'untagged_fallback',
    'withhold_untagged_street_view',
    'augment_estimates_with_depth',
    'estimate_heights_from_registration',
    '_seed_from_view_name',
    'aggregate_building_heights',
]
