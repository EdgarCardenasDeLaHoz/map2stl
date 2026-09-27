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
Requires: an OpenTopography API key, passed as ``LiDAR3DEPProvider(api_key=...)``
or ``geo2stl.opentopo.get_api_key()`` ($OPENTOPO_API_KEY / config.json).
"""

from __future__ import annotations

import logging

import numpy as np

from city2stl.height import BBox, HeightResult, _resample
from geo2stl import opentopo

from ._cache import (
    make_cache_key,
    read_height_result,
    register_ttl,
    write_height_result,
)

logger = logging.getLogger(__name__)

register_ttl("lidar_3dep", 180)

_CONFIDENCE = 0.82
_RESOLUTION_M = 30.0
_NAMESPACE = "lidar_3dep"
_TIMEOUT = 180


def _is_in_us(bbox: BBox) -> bool:
    north, south, east, west = bbox
    conus = south < 50 and north > 24 and west < -66 and east > -125
    alaska = south < 72 and north > 51 and west < -130 and east > -170
    hawaii = south < 23 and north > 18 and west < -154 and east > -160
    return conus or alaska or hawaii


def _get_api_key() -> str | None:
    return opentopo.get_api_key()


# GeoTIFF parsing (rasterio→PIL fallback) is shared across DEM providers.
from ._raster import read_geotiff_bytes as _parse_tiff_bytes  # noqa: E402


def _fetch_opentopo_dem(demtype: str, bbox: BBox, api_key: str,
                        label: str) -> np.ndarray | None:
    """Fetch a global DEM from OpenTopography (both products use same API)."""
    north, south, east, west = bbox
    logger.info("lidar_3dep: fetching %s for "
                "N=%.4f S=%.4f E=%.4f W=%.4f", label, north, south, east, west)
    try:
        data = opentopo.request_geotiff(demtype, north, south, east, west,
                                        api_key=api_key, timeout=_TIMEOUT)
    except Exception as e:
        logger.warning("lidar_3dep: %s request failed: %s", label, e)
        return None
    return _parse_tiff_bytes(data)


class LiDAR3DEPProvider:
    """US building heights: COP30 DSM − 3DEP10m DTM via OpenTopography (~30 m)."""

    name = "lidar_3dep"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def covers(self, bbox: BBox) -> bool:
        return _is_in_us(bbox)

    def fetch_heights(self, bbox: BBox, dim: tuple[int, int]) -> HeightResult:
        north, south, east, west = bbox
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   {"dim": list(dim)})

        hit = read_height_result(_NAMESPACE, cache_key, self.name, _RESOLUTION_M)
        if hit is not None:
            return hit

        api_key = self.api_key or _get_api_key()
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

        # The reported resolution is the source products' (COP30 minus SRTM),
        # not the output grid spacing. Sampling a 30 m difference onto a 512-cell
        # grid does not make it finer, and the merge ranks on this field.
        result = HeightResult(ndsm, confidence, self.name, _RESOLUTION_M)
        write_height_result(_NAMESPACE, cache_key, result)
        return result


def _empty_result(dim: tuple[int, int]) -> HeightResult:
    return HeightResult.empty(dim, "lidar_3dep", _RESOLUTION_M)
