"""skyline._core.types — extracted from pipeline.py (A1 split)."""
from __future__ import annotations
from collections import OrderedDict as _OrderedDict

import logging
import math
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks
from shapely.geometry import shape

# F-CLEAN14: the F-SKY12 depth except-branches reference ``logger`` but the
# module never defined one (latent NameError, only reachable on a depth-module
# failure). Defined here so those branches log instead of crashing.
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Viewpoint:
    name: str
    query: str
    lat: float
    lon: float
    heading: float
    pitch: float
    fov: float
    image_width: int
    image_height: int

@dataclass(frozen=True)
class BuildingRecord:
    feature_id: str
    name: str
    geometry: object
    centroid_lat: float
    centroid_lon: float
    height_tag_m: float | None
    height_source: str
    area_m2: float
    terrain_elev_m: float = 0.0

@dataclass(frozen=True)
class CapturedView:
    viewpoint: Viewpoint
    image_path: Path
    metadata_path: Path
    image: np.ndarray

@dataclass(frozen=True)
class RegisteredBuildingEstimate:
    feature_id: str
    name: str
    view_name: str
    heading_offset_deg: float
    x_px: float
    y_px: float
    forward_m: float
    estimated_height_m: float
    confidence: float
    # F-SKY1 floor-period diagnostics. All optional; populated only when the
    # building's facade has detectable horizontal floor banding (a clean
    # autocorrelation peak in the mask-band row-mean luminance). These are
    # OSM-independent: floor_period_px lets us back out distance via the
    # inverse pinhole using an assumed 3.2 m floor height, and floor count
    # × floor height gives an independent height estimate. See
    # docs/plans/F-SKY1-floor-periodicity.md.
    floor_period_px: float | None = None
    floor_confidence: float | None = None
    inferred_distance_m: float | None = None
    inferred_height_m: float | None = None
    # F-SKY12 depth-from-pano diagnostics. Populated when
    # ``augment_estimates_with_depth`` is called on a view's estimates.
    # ``depth_height_m`` is an independent height estimate from Depth
    # Anything V2 at the silhouette-top pixel, calibrated to metres using
    # the matched OSM building distances as anchors. ``depth_disagreement``
    # is True when |estimated_height_m - depth_height_m| / max(...) > 0.4.
    # Phase A: pure diagnostic, no impact on aggregated heights. See
    # docs/plans/F-SKY12-depth-from-panos.md.
    depth_height_m: float | None = None
    depth_disagreement: bool | None = None


__all__ = [
    'Viewpoint',
    'BuildingRecord',
    'CapturedView',
    'RegisteredBuildingEstimate',
]
