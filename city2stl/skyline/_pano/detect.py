"""skyline._pano.detect — extracted from pano_registration.py (A2 split)."""

from __future__ import annotations

import logging

from .detect_matching import (  # noqa: F401
    _count_skyline_buildings,
    _dedup_matches,
    _smooth_matches_across_views,
    _smooth_pano_matches_against_views,
    matched_footprint_overlap,
)
from .detect_multires import (  # noqa: F401
    _multires_pano_refine,
    _multires_sam_instances,
    _pano_sliding_window_split,
    _split_by_depth_discontinuity,
)
from .detect_pano import (  # noqa: F401
    _build_and_detect_pano,
)
from .detect_views import (  # noqa: F401
    _register_views,
)

logger = logging.getLogger(__name__)

__all__ = [
    '_register_views',
    '_smooth_matches_across_views',
    '_smooth_pano_matches_against_views',
    '_count_skyline_buildings',
    '_multires_sam_instances',
    '_split_by_depth_discontinuity',
    '_multires_pano_refine',
    '_pano_sliding_window_split',
    '_build_and_detect_pano',
    'matched_footprint_overlap',
]
