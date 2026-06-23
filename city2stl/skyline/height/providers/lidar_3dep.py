"""
US building heights via OpenTopography — Copernicus COP30 DSM minus SRTM DTM.

Replaces the old USGS 3DEP ImageServer approach, which served only a bare-earth
DTM so DSM − DTM was identically zero everywhere.

Strategy:
  DSM: Copernicus GLO-30 (COP30, 30 m) via OpenTopography global API
  DTM: NASA SRTM 30 m (SRTMGL1) via OpenTopography global API
  nDSM = COP30 − SRTM ≈ building/vegetation height above ground

Both products use the EGM2008 geoid so vertical datums are consistent.
COP30 was produced from TanDEM-X acquisitions (2011–2015) and captures
building tops; SRTM (2000) underestimates tall buildings, making the
difference a valid lower-bound nDSM.

Coverage: US (contiguous, Alaska, Hawaii); falls back to the global NDSMProvider
for non-US regions via the provider registry coverage check.
Resolution: ~30 m.
Confidence: 0.82 (US-specific, slight advantage over global SRTM-only nDSM).
Requires: OPENTOPO_API_KEY in config.json or environment variable.
"""

from __future__ import annotations

import io
import logging
from typing import Tuple

import numpy as np
import requests

from city2stl.skyline.height import BBox, HeightResult, _resample
from ._cache import (
    register_ttl, make_cache_key, read_height_result, write_height_result,
)

logger = logging.getLogger(__name__)

register_ttl("lidar_3dep", 180)

_CONFIDENCE = 0.82
_RESOLUTION_M = 30.0
_NAMESPACE = "lidar_3dep"
_TIMEOUT = 180

_OT_GLOBAL_API = "https://portal.opentopography.org/API/globaldem"


def _is_in_us(bbox: BBox) -> bool:
    north, south, east, west = bbox
    conus = south < 50 and north > 24 and west < -66 and east > -125
    alaska = south < 72 and north > 51 and west < -130 and east > -170
    hawaii = south < 23 and north > 18 and west < -154 and east > -160
    return conus or alaska or hawaii


def _get_api_key() -> str | None:
    try:
        from app.server.config import OPENTOPO_API_KEY  # noqa: PLC0415
        return OPENTOPO_API_KEY or None
    except ImportError:
        return None


# GeoTIFF parsing (rasterio→PIL fallback) is shared across DEM providers.
from ._raster import read_geotiff_bytes as _parse_tiff_bytes  # noqa: E402


def _fetch_raster(endpoint: str, params: dict) -> np.ndarray | None:
    try:
        r = requests.get(endpoint, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if "json" in ct or "xml" in ct or "html" in ct:
            logger.warning("lidar_3dep: unexpected content-type %s from %s", ct, endpoint)
            return None
        return _parse_tiff_bytes(io.BytesIO(r.content))
    except Exception as e:
        logger.warning("lidar_3dep: request to %s failed: %s", endpoint, e)
        return None


def _fetch_opentopo_dem(demtype: str, bbox: BBox, api_key: str,
                        label: str) -> np.ndarray | None:
    """Fetch a global DEM from OpenTopography (both products use same API)."""
    north, south, east, west = bbox
    logger.info("lidar_3dep: fetching %s for "
                "N=%.4f S=%.4f E=%.4f W=%.4f", label, north, south, east, west)
    params = {
        "demtype": demtype,
        "south": south, "north": north, "west": west, "east": east,
        "outputFormat": "GTiff",
        "API_Key": api_key,
    }
    return _fetch_raster(_OT_GLOBAL_API, params)


class LiDAR3DEPProvider:
    """US building heights: COP30 DSM − 3DEP10m DTM via OpenTopography (~30 m)."""

    name = "lidar_3dep"

    def covers(self, bbox: BBox) -> bool:
        return _is_in_us(bbox)

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        north, south, east, west = bbox
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   {"dim": list(dim)})

        hit = read_height_result(_NAMESPACE, cache_key, self.name, _RESOLUTION_M)
        if hit is not None:
            return hit

        api_key = _get_api_key()
        if not api_key:
            logger.warning("lidar_3dep: OPENTOPO_API_KEY not set; returning empty")
            return _empty_result(dim)

        dsm = _fetch_opentopo_dem("COP30", bbox, api_key, "COP30 DSM")
        if dsm is None:
            logger.warning("lidar_3dep: COP30 DSM unavailable")
            return _empty_result(dim)

        # SRTM (EGM2008) as DTM — same vertical datum as COP30, avoids datum mismatch
        dtm = _fetch_opentopo_dem("SRTMGL1", bbox, api_key, "SRTM DTM")
        if dtm is None:
            logger.warning("lidar_3dep: SRTM DTM unavailable; cannot compute nDSM")
            return _empty_result(dim)

        if dtm.shape != dsm.shape:
            dtm = _resample(dtm, dsm.shape)

        ndsm = np.where(dsm - dtm < 0, 0.0, dsm - dtm).astype(np.float32)

        if ndsm.shape != dim:
            ndsm = _resample(ndsm, dim)

        confidence = np.where(np.isnan(ndsm), 0.0, _CONFIDENCE).astype(np.float32)

        lat_span = north - south
        resolution_m = (lat_span * 111_000) / dim[0]

        result = HeightResult(ndsm, confidence, self.name, resolution_m)
        write_height_result(_NAMESPACE, cache_key, result)
        return result


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="lidar_3dep",
        resolution_m=_RESOLUTION_M,
    )
