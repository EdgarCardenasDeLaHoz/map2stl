"""
DEPRECATED: Shadow-based building height estimation (do not use in production).

This provider estimates building heights from shadow lengths in satellite imagery
combined with sun position (azimuth + elevation) at acquisition time.

Formula: height = shadow_length * tan(sun_elevation)

**DEPRECATION WARNING:**
    - This method is highly error-prone in hilly or dense urban terrain.
    - It frequently produces extreme outliers and unreliable results.
    - It is now removed from the default provider list and should only be used for research or as a last resort.
    - See docs/completed/height-pipeline-plan.md for rationale and alternatives.

Requirements:
    - Satellite image (RGB) for the bbox
    - Sun elevation angle at image acquisition time
    - Building footprint mask (from OSM or Open Buildings)

Pass ``rgb`` (HÃ—WÃ—3 uint8 ndarray) to ``fetch_heights`` to activate
shadow inference. Omitting it returns an empty result (safe default).
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np

from city2stl.height import BBox, HeightResult
from app.server.core.cache import (
    make_cache_key, read_array_cache, write_array_cache,
    NAMESPACE_TTL,
)

logger = logging.getLogger(__name__)

NAMESPACE_TTL.setdefault("shadow_height", 30 * 86400)

_CONFIDENCE = 0.3  # low â€” shadow estimation has Â±3-5m accuracy
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
    return max(5, elevation)  # clamp to minimum 5Â° to avoid division by ~0


def _shadow_length_to_height(shadow_pixels: int, pixel_size_m: float,
                             sun_elevation_deg: float) -> float:
    """Convert shadow length (in pixels) to building height (metres)."""
    import math
    shadow_m = shadow_pixels * pixel_size_m
    return shadow_m * math.tan(math.radians(sun_elevation_deg))


_SAT_TARGET_M_PER_PX = 2.0  # analyse shadows at ~2 m/pixel


def _fetch_rgb_for_bbox(bbox: BBox, dim: Tuple[int, int]) -> "np.ndarray | None":
    """Fetch satellite imagery for bbox at native resolution.

    Returns the image at a resolution appropriate for shadow detection
    (~2 m/pixel), **not** downsampled to ``dim``.  The inference pipeline
    works on the high-res image and downsamples the result to ``dim``
    afterwards.

    Returns None gracefully on any error so the provider degrades to
    returning an empty result rather than raising.
    """
    try:
        import base64 as _b64
        from io import BytesIO
        from PIL import Image
        from geo2stl.sat2stl import fetch_satellite_tiles

        north, south, east, west = bbox
        # Compute a resolution that gives ~2 m/pixel
        bbox_m = (north - south) * 111_320.0
        sat_dim = max(256, min(1024, int(bbox_m / _SAT_TARGET_M_PER_PX)))

        b64 = fetch_satellite_tiles(north, south, east, west, dim=sat_dim)
        img_bytes = _b64.b64decode(b64)
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        return np.array(img, dtype=np.uint8)
    except Exception as exc:
        logger.debug("Shadow height: satellite fetch failed: %s", exc)
        return None



class ShadowHeightProvider:
    """DEPRECATED: Shadow-based building height estimation â€” do not use in production.

    This provider is deprecated due to high error rate and outlier risk. See module docstring.
    """

    name = "shadow_height"

    def covers(self, bbox: BBox) -> bool:
        """Shadow estimation is deprecated and not recommended for any region.
        Returns False unless explicitly enabled for research purposes.
        """
        return True

    def fetch_heights(
        self,
        bbox: BBox,
        dim: Tuple[int, int],
        rgb: "np.ndarray | None" = None,
    ) -> HeightResult:
        """Estimate building heights from shadow lengths in satellite imagery.

        Parameters
        ----------
        bbox:
            (north, south, east, west) in decimal degrees.
        dim:
            (height, width) of the output raster in pixels.
        rgb:
            Optional HÃ—WÃ—3 uint8 ndarray of satellite imagery for the bbox.
            If None, returns an empty result (safe default until satellite
            imagery access is wired up).
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

        if rgb is None:
            logger.debug("Shadow height: no RGB provided, fetching satellite tiles")
            rgb = _fetch_rgb_for_bbox(bbox, dim)

        if rgb is None:
            logger.debug("Shadow height: satellite unavailable - returning empty result")
            return _empty_result(dim)

        result = _infer_from_rgb(rgb, bbox, dim)

        write_array_cache(_NAMESPACE, cache_key,
                          {"raster": result.raster, "confidence": result.confidence},
                          {"resolution_m": _RESOLUTION_M})
        return result


def _infer_from_rgb(
    rgb: np.ndarray,
    bbox: BBox,
    dim: Tuple[int, int],
) -> HeightResult:
    """Run the shadow-to-height pipeline on a satellite RGB image.

    Shadow detection runs at the RGB image's native resolution, then
    the resulting height raster is resampled to ``dim`` (the output
    DEM grid size).

    Steps
    -----
    1. Detect shadow regions using HSV thresholding.
    2. Estimate sun elevation for the bbox centre.
    3. For each connected shadow component, measure its longest axis
       and convert to a height estimate.
    4. Paint the estimated height at the shadow-tip location.
    5. Resample the high-res height raster down to ``dim``.
    """
    import scipy.ndimage as ndi

    out_h, out_w = dim
    rgb_h, rgb_w = rgb.shape[:2]
    north, south, east, west = bbox

    shadow_mask = _detect_shadows(rgb)

    lat_mid = (north + south) / 2.0
    lon_mid = (east + west) / 2.0
    sun_elev = _estimate_sun_elevation(lat_mid, lon_mid)

    # Pixel scale from the RGB image resolution, not the output grid
    pixel_m = (north - south) * 111_320.0 / rgb_h

    # Work at RGB resolution, then downsample
    raster_hr = np.full((rgb_h, rgb_w), np.nan, dtype=np.float32)
    conf_hr = np.zeros((rgb_h, rgb_w), dtype=np.float32)

    # Label connected shadow components
    labelled, n_components = ndi.label(shadow_mask)
    if n_components == 0:
        logger.debug("Shadow height: no shadow regions detected")
        return _empty_result(dim)

    # Filter tiny components (< 3px) and very large ones (> 0.3% of image â€”
    # likely terrain shadows or water, not buildings)
    max_component_px = int(rgb_h * rgb_w * 0.003)
    n_valid = 0

    for label_id in range(1, n_components + 1):
        component = labelled == label_id
        rows, cols = np.where(component)
        n_px = len(rows)
        if n_px < 3 or n_px > max_component_px:
            continue

        # Bounding box of the shadow component
        row_span = int(rows.max() - rows.min()) + 1
        col_span = int(cols.max() - cols.min()) + 1

        # Reject highly elongated shadows â€” terrain shadows are long thin streaks
        # (aspect ratio > 8:1). Building shadows are more compact.
        long_side = max(row_span, col_span)
        short_side = min(row_span, col_span)
        if short_side > 0 and long_side / short_side > 8:
            continue

        shadow_length_px = long_side

        height_m = _shadow_length_to_height(shadow_length_px, pixel_m, sun_elev)

        # Clamp to realistic urban building heights (1â€“50 m).
        # 50 m â‰ˆ 15 floors â€” covers virtually all non-skyscraper buildings.
        # Taller estimates almost always come from terrain shadows or image
        # artefacts, not actual buildings in the cities this pipeline targets.
        if height_m < 1.0 or height_m > 50.0:
            continue

        # Place the height estimate at the shadow-tip pixel (top of bounding box).
        tip_row = int(rows.min())
        tip_col = int(cols[rows == rows.min()].mean())

        if 0 <= tip_row < rgb_h and 0 <= tip_col < rgb_w:
            raster_hr[tip_row, tip_col] = float(height_m)
            conf_hr[tip_row, tip_col] = _CONFIDENCE
            n_valid += 1

    logger.debug(
        "Shadow height: %d components (%d valid), sun_elev=%.1f deg, pixel_m=%.2f",
        n_components, n_valid, sun_elev, pixel_m,
    )

    # Resample to output grid
    if (rgb_h, rgb_w) == (out_h, out_w):
        return HeightResult(raster_hr, conf_hr, "shadow_height", _RESOLUTION_M)

    raster = _downsample_height(raster_hr, (out_h, out_w))
    conf = _downsample_height(conf_hr, (out_h, out_w))
    return HeightResult(raster, conf, "shadow_height", pixel_m)


def _downsample_height(arr: np.ndarray, dim: Tuple[int, int]) -> np.ndarray:
    """Downsample a sparse height raster to ``dim`` using max-pooling.

    Each output cell gets the maximum non-NaN value from its corresponding
    region in the high-res input.  Cells with no valid source pixels stay NaN.
    """
    src_h, src_w = arr.shape
    out_h, out_w = dim
    out = np.full((out_h, out_w), np.nan, dtype=np.float32)

    row_scale = src_h / out_h
    col_scale = src_w / out_w

    for r in range(out_h):
        r0 = int(r * row_scale)
        r1 = max(r0 + 1, int((r + 1) * row_scale))
        for c in range(out_w):
            c0 = int(c * col_scale)
            c1 = max(c0 + 1, int((c + 1) * col_scale))
            block = arr[r0:r1, c0:c1]
            valid = block[~np.isnan(block)]
            if valid.size > 0:
                out[r, c] = valid.max()
    return out


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="shadow_height",
        resolution_m=_RESOLUTION_M,
    )
