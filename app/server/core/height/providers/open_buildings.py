"""
Google Open Buildings 2.5D provider.

Data: Google Open Buildings — V3 with height attributes
- ML-derived building footprints + estimated height
- Coverage: Africa, South/Southeast Asia, Latin America, Caribbean, Middle East
- Access: Google Cloud Storage (public, no key)
- Format: CSV with WKT geometry + height columns
- Resolution: Per-building footprint, ~1-5m accuracy
- License: CC-BY-4.0

The Open Buildings dataset provides individual building polygons with
height estimates derived from satellite imagery. We rasterize these
into a grid matching the target dimensions.

Note: Coverage is limited to developing regions. For Europe/US/Japan,
other providers (3DEP, Copernicus, nDSM) are more appropriate.
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np

from app.server.core.height import BBox, HeightResult, _resample
from app.server.core.cache import (
    make_cache_key, read_array_cache, write_array_cache,
    NAMESPACE_TTL,
)

logger = logging.getLogger(__name__)

NAMESPACE_TTL.setdefault("open_buildings", 90 * 86400)

_CONFIDENCE = 0.6  # ML-derived heights, reasonable but not survey-grade
_RESOLUTION_M = 5.0  # effective per-building resolution
_NAMESPACE = "open_buildings"
_DOWNLOAD_TIMEOUT = 120

# Open Buildings V3 is distributed as country-level CSV files on GCS.
# For bbox-based access, the community Overture Maps GERS or
# Microsoft Planetary Computer STAC endpoint is simpler.
# We use Overture Maps S3 distribution which includes Open Buildings data.
_OVERTURE_BASE = (
    "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/"
    "release/2024-07-22.0/theme=buildings/type=building/"
)

# Coverage regions (approximate bboxes where Open Buildings has data)
_COVERAGE_REGIONS = [
    # Africa
    {"name": "africa", "n": 37, "s": -35, "e": 52, "w": -18},
    # South Asia
    {"name": "south_asia", "n": 38, "s": 5, "e": 98, "w": 60},
    # Southeast Asia
    {"name": "southeast_asia", "n": 28, "s": -11, "e": 150, "w": 92},
    # Latin America & Caribbean
    {"name": "latam", "n": 33, "s": -55, "e": -34, "w": -118},
    # Middle East
    {"name": "middle_east", "n": 42, "s": 12, "e": 63, "w": 25},
]


def _is_in_coverage(bbox: BBox) -> bool:
    """Check if bbox overlaps any Open Buildings coverage region."""
    north, south, east, west = bbox
    for region in _COVERAGE_REGIONS:
        if (south < region["n"] and north > region["s"] and
                west < region["e"] and east > region["w"]):
            return True
    return False


def _fetch_buildings_for_bbox(bbox: BBox, dim: Tuple[int, int]) -> np.ndarray | None:
    """Fetch building heights from Open Buildings and rasterize to grid.

    This is a placeholder that returns None until the Overture/STAC
    integration is implemented. The provider structure is ready for when
    the data access is wired up.
    """
    # TODO: Implement Overture Maps or direct GCS access
    # Steps:
    # 1. Query Overture Buildings parquet files for bbox
    # 2. Extract height column from matching buildings
    # 3. Rasterize building polygons with heights into (H, W) grid
    logger.debug("Open Buildings fetch not yet implemented - returning None")
    return None


class OpenBuildingsProvider:
    """Google Open Buildings 2.5D — ML-derived building heights."""

    name = "open_buildings"

    def covers(self, bbox: BBox) -> bool:
        """Returns True if bbox overlaps Open Buildings coverage."""
        return _is_in_coverage(bbox)

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        """Fetch and rasterize Open Buildings heights for bbox."""
        north, south, east, west = bbox
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   {"dim": list(dim)})

        cached = read_array_cache(_NAMESPACE, cache_key)
        if cached is not None:
            arrays, meta = cached
            return HeightResult(
                raster=arrays["raster"],
                confidence=arrays["confidence"],
                source_name=self.name,
                resolution_m=meta.get("resolution_m", _RESOLUTION_M),
            )

        raster = _fetch_buildings_for_bbox(bbox, dim)

        if raster is None:
            return _empty_result(dim)

        if raster.shape != dim:
            raster = _resample(raster, dim)

        confidence = np.where(
            np.isnan(raster), 0.0, _CONFIDENCE
        ).astype(np.float32)

        result = HeightResult(raster, confidence, self.name, _RESOLUTION_M)

        write_array_cache(_NAMESPACE, cache_key,
                          {"raster": raster, "confidence": confidence},
                          {"resolution_m": _RESOLUTION_M})
        return result


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="open_buildings",
        resolution_m=_RESOLUTION_M,
    )
