"""
Shadow-based building height estimation.

Estimates building heights from shadow lengths in satellite imagery
combined with sun position (azimuth + elevation) at acquisition time.

Formula: height = shadow_length * tan(sun_elevation)

This is the zero-cost, works-everywhere approach. Accuracy is ±3-5m,
making it useful as a supplementary signal for cities like Cartagena
with no other height data available.

Requirements:
  - Satellite image (RGB) for the bbox
  - Sun elevation angle at image acquisition time
  - Building footprint mask (from OSM or Open Buildings)

This provider is a placeholder — shadow detection and sun angle
calculation will be implemented in Phase 1b.
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np

from app.server.core.height import BBox, HeightResult
from app.server.core.cache import (
    make_cache_key, read_array_cache, write_array_cache,
    NAMESPACE_TTL,
)

logger = logging.getLogger(__name__)

NAMESPACE_TTL.setdefault("shadow_height", 30 * 86400)

_CONFIDENCE = 0.3  # low — shadow estimation has ±3-5m accuracy
_RESOLUTION_M = 5.0  # depends on satellite image resolution
_NAMESPACE = "shadow_height"


def _detect_shadows(rgb: np.ndarray) -> np.ndarray:
    """Detect shadow regions in an RGB satellite image.

    Returns a boolean mask (H, W) where True = shadow pixel.

    Uses simple threshold-based detection on the HSV value channel:
    shadows are dark with low saturation.
    """
    import cv2

    if rgb.ndim != 3 or rgb.shape[2] < 3:
        return np.zeros(rgb.shape[:2], dtype=bool)

    hsv = cv2.cvtColor(rgb[:, :, :3].astype(np.uint8), cv2.COLOR_RGB2HSV)
    v_channel = hsv[:, :, 2].astype(np.float32)
    s_channel = hsv[:, :, 1].astype(np.float32)

    # Shadow heuristic: low value AND low saturation
    # Thresholds may need tuning per satellite source
    shadow_mask = (v_channel < 80) & (s_channel < 100)
    return shadow_mask


def _estimate_sun_elevation(lat: float, lon: float,
                            month: int = 6, hour: int = 10) -> float:
    """Estimate sun elevation angle (degrees) for a location and time.

    Uses a simplified solar position formula. For production use,
    we'd parse the actual image metadata for acquisition time.

    Returns elevation in degrees (0 = horizon, 90 = zenith).
    """
    import math

    # Simplified solar declination (assumes ~June for max angle)
    day_of_year = 30 * month  # rough
    declination = 23.45 * math.sin(math.radians(360 / 365 * (day_of_year - 81)))

    # Hour angle (10 AM local = 2h before solar noon)
    hour_angle = 15 * (hour - 12)  # degrees

    # Solar elevation
    lat_rad = math.radians(lat)
    dec_rad = math.radians(declination)
    ha_rad = math.radians(hour_angle)

    sin_elev = (math.sin(lat_rad) * math.sin(dec_rad) +
                math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad))
    elevation = math.degrees(math.asin(max(-1, min(1, sin_elev))))
    return max(5, elevation)  # clamp to minimum 5° to avoid division by ~0


def _shadow_length_to_height(shadow_pixels: int, pixel_size_m: float,
                             sun_elevation_deg: float) -> float:
    """Convert shadow length (in pixels) to building height (metres)."""
    import math
    shadow_m = shadow_pixels * pixel_size_m
    return shadow_m * math.tan(math.radians(sun_elevation_deg))


class ShadowHeightProvider:
    """Shadow-based building height estimation — zero-cost, global."""

    name = "shadow_height"

    def covers(self, bbox: BBox) -> bool:
        """Shadow estimation works everywhere (given satellite imagery)."""
        return True

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        """Estimate building heights from shadows.

        Currently returns empty result — full implementation requires
        satellite image access and building footprint masks.
        """
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

        # TODO: Implementation steps:
        # 1. Fetch satellite RGB for bbox (from sat.py or cached)
        # 2. Detect shadow regions
        # 3. Match shadows to building footprints (OSM or Open Buildings)
        # 4. Measure shadow length in sun direction
        # 5. Convert to height using sun elevation
        # 6. Rasterize to target grid

        logger.debug("Shadow height estimation not yet implemented")
        return _empty_result(dim)


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="shadow_height",
        resolution_m=_RESOLUTION_M,
    )
