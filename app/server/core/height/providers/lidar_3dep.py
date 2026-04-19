"""
USGS 3DEP LiDAR provider — sub-metre US building heights.

Data: USGS 3D Elevation Program (3DEP)
- DSM (Digital Surface Model) and DTM (Digital Terrain Model) via REST API
- Coverage: Contiguous US, Alaska, Hawaii, territories
- Resolution: 1m (widespread), 0.5m (some areas)
- Access: USGS National Map REST API — free, no API key
- nDSM = DSM − DTM gives building/vegetation heights

The 3DEP 1-metre DSM is derived from airborne LiDAR point clouds.
This is the highest-quality public height source for US cities (Philadelphia).

API Endpoint: USGS National Map Image Server
  DSM: https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage
  DTM (bare earth): same endpoint, different rendering rule
"""

from __future__ import annotations

import io
import logging
from typing import Tuple

import numpy as np
import requests

from app.server.core.height import BBox, HeightResult, _resample
from app.server.core.cache import (
    make_cache_key, read_array_cache, write_array_cache,
    NAMESPACE_TTL,
)

logger = logging.getLogger(__name__)

NAMESPACE_TTL.setdefault("lidar_3dep", 180 * 86400)  # 180 days — data rarely changes

_CONFIDENCE = 0.95  # highest among all providers — sub-metre accuracy
_RESOLUTION_M = 1.0  # native 1m
_NAMESPACE = "lidar_3dep"
_DOWNLOAD_TIMEOUT = 120  # large requests may be slow

# USGS 3DEP ImageServer endpoint (ArcGIS REST)
_IMAGE_SERVER = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/"
    "3DEPElevation/ImageServer/exportImage"
)

# Rendering rules for DSM vs DTM
# renderingRule with rasterFunction "None" returns the raw DSM
# For bare-earth DEM, the default rendering (no rule) returns DTM
_DSM_RENDERING_RULE = '{"rasterFunction":"None"}'


def _is_in_us(bbox: BBox) -> bool:
    """Check if bbox overlaps contiguous US, Alaska, or Hawaii."""
    north, south, east, west = bbox
    # Contiguous US: 24°N–50°N, 125°W–66°W
    conus = (south < 50 and north > 24 and west < -66 and east > -125)
    # Alaska: 51°N–72°N, 172°E–130°W (wraps dateline)
    alaska = (south < 72 and north > 51 and west < -130 and east > -170)
    # Hawaii: 18°N–23°N, 160°W–154°W
    hawaii = (south < 23 and north > 18 and west < -154 and east > -160)
    return conus or alaska or hawaii


def _fetch_3dep_image(bbox: BBox, dim: Tuple[int, int],
                      rendering_rule: str | None = None) -> np.ndarray | None:
    """Fetch elevation raster from USGS 3DEP ImageServer.

    Returns float32 array (H, W) in metres, or None on failure.
    """
    north, south, east, west = bbox
    h, w = dim

    params = {
        "bbox": f"{west},{south},{east},{north}",
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{w},{h}",
        "format": "tiff",
        "pixelType": "F32",
        "noDataInterpretation": "esriNoDataMatchAny",
        "interpolation": "RSP_BilinearInterpolation",
        "f": "image",
    }
    if rendering_rule:
        params["renderingRule"] = rendering_rule

    try:
        logger.info(f"3DEP: fetching {'DSM' if rendering_rule else 'DTM'} for "
                    f"N={north:.4f} S={south:.4f} E={east:.4f} W={west:.4f}")
        r = requests.get(_IMAGE_SERVER, params=params, timeout=_DOWNLOAD_TIMEOUT)
        if r.status_code >= 400:
            logger.warning(f"3DEP returned {r.status_code}")
            return None
        r.raise_for_status()

        # Check for JSON error response
        ct = r.headers.get("Content-Type", "")
        if "json" in ct or "xml" in ct or "html" in ct:
            logger.warning(f"3DEP returned non-image content: {ct}")
            return None

        return _parse_tiff_bytes(r.content)

    except requests.RequestException as e:
        logger.warning(f"3DEP request failed: {e}")
        return None


def _parse_tiff_bytes(data: bytes) -> np.ndarray | None:
    """Parse TIFF bytes into a float32 array."""
    try:
        import rasterio
        try:
            with rasterio.open(io.BytesIO(data)) as src:
                arr = src.read(1).astype(np.float32)
                nodata = src.nodata
                if nodata is not None:
                    arr[arr == nodata] = np.nan
                return arr
        except rasterio.errors.RasterioIOError:
            logger.warning("Failed to parse 3DEP TIFF with rasterio")
            return None
    except ImportError:
        pass

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        return np.array(img, dtype=np.float32)
    except Exception as e:
        logger.warning(f"Cannot parse 3DEP TIFF: {e}")
        return None


class LiDAR3DEPProvider:
    """USGS 3DEP LiDAR — sub-metre US building heights."""

    name = "lidar_3dep"

    def covers(self, bbox: BBox) -> bool:
        """Returns True for US bounding boxes."""
        return _is_in_us(bbox)

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        """Fetch DSM and DTM from 3DEP, compute nDSM = DSM − DTM."""
        north, south, east, west = bbox
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   {"dim": list(dim)})

        # Check cache
        cached = read_array_cache(_NAMESPACE, cache_key)
        if cached is not None:
            arrays, meta = cached
            return HeightResult(
                raster=arrays["raster"],
                confidence=arrays["confidence"],
                source_name=self.name,
                resolution_m=meta.get("resolution_m", _RESOLUTION_M),
            )

        # Fetch DSM (surface model — includes buildings)
        dsm = _fetch_3dep_image(bbox, dim, rendering_rule=_DSM_RENDERING_RULE)

        # Fetch DTM (bare earth)
        dtm = _fetch_3dep_image(bbox, dim, rendering_rule=None)

        if dsm is None:
            return _empty_result(dim)

        if dtm is not None and dtm.shape == dsm.shape:
            # nDSM = DSM − DTM = building/vegetation height
            ndsm = dsm - dtm
            # Clamp negatives (survey artefacts)
            ndsm = np.where(ndsm < 0, 0.0, ndsm).astype(np.float32)
        elif dtm is not None:
            # Shape mismatch — resample
            dtm_resampled = _resample(dtm, dsm.shape)
            ndsm = dsm - dtm_resampled
            ndsm = np.where(ndsm < 0, 0.0, ndsm).astype(np.float32)
        else:
            # No DTM — can't compute nDSM
            logger.warning("3DEP: DTM not available, returning NaN")
            ndsm = np.full(dim, np.nan, dtype=np.float32)

        # Resample to target if needed
        if ndsm.shape != dim:
            ndsm = _resample(ndsm, dim)

        confidence = np.where(
            np.isnan(ndsm), 0.0, _CONFIDENCE
        ).astype(np.float32)

        # Approximate actual resolution
        lat_span = north - south
        resolution_m = (lat_span * 111_000) / dim[0]

        result = HeightResult(ndsm, confidence, self.name, resolution_m)

        # Cache
        write_array_cache(_NAMESPACE, cache_key,
                          {"raster": ndsm, "confidence": confidence},
                          {"resolution_m": resolution_m})
        return result


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="lidar_3dep",
        resolution_m=_RESOLUTION_M,
    )
