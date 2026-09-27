"""OpenTopography global DEM API: key, dataset table, request and cached fetch.

- ``get_api_key()`` / ``set_api_key(key)`` — the key used when a caller passes
  none. It starts from ``$OPENTOPO_API_KEY``, else ``opentopo_api_key`` in the
  strm2stl ``config.json``; the app calls ``set_api_key`` when the user saves a
  new key, so downloads pick it up without a restart.
- ``OPENTOPO_DATASETS`` — the global DEM types the app offers.
- ``request_geotiff(demtype, ...)`` — one ``/API/globaldem`` GeoTIFF download.
- ``fetch_opentopo_dem(...)`` — the same, cached on disk and read at a target size.

Height providers (nDSM, 3DEP) use ``request_geotiff``; the terrain DEM path
uses ``fetch_opentopo_dem`` through ``geo2stl.dem.fetch_dem``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import numpy as np
import requests

from geo2stl.raster import read_geotiff

logger = logging.getLogger(__name__)

GLOBALDEM_URL = "https://portal.opentopography.org/API/globaldem"

#: Global DEM types offered as DEM sources.
OPENTOPO_DATASETS: dict[str, dict] = {
    "SRTMGL1":    {"label": "SRTM 30m (Global)",          "resolution_m": 30},
    "SRTMGL3":    {"label": "SRTM 90m (Global)",          "resolution_m": 90},
    "AW3D30":     {"label": "ALOS World 3D 30m",          "resolution_m": 30},
    "COP30":      {"label": "Copernicus DSM 30m",         "resolution_m": 30},
    "COP90":      {"label": "Copernicus DSM 90m",         "resolution_m": 90},
    "SRTM15Plus": {"label": "SRTM15+ (Bathymetry+Land)", "resolution_m": 500},
}

# strm2stl root (geo2stl/opentopo.py -> geo2stl -> strm2stl)
_STRM2STL_DIR = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _STRM2STL_DIR / "config.json"

#: Downloaded GeoTIFFs, keyed by (demtype, bbox).
CACHE_PATH: Path = _STRM2STL_DIR / "cache" / "opentopo"


def _load_api_key() -> str | None:
    key = os.environ.get("OPENTOPO_API_KEY")
    if key:
        return key
    try:
        if _CONFIG_PATH.exists():
            return json.loads(_CONFIG_PATH.read_text()).get("opentopo_api_key") or None
    except Exception:  # noqa: BLE001 - an unreadable config means no key
        logger.debug("Could not read opentopo key from config.json", exc_info=True)
    return None


_api_key: str | None = _load_api_key()
if not _api_key:
    logger.warning(
        "No OpenTopography API key found. "
        "Set the OPENTOPO_API_KEY environment variable to enable DEM downloads."
    )


def get_api_key() -> str | None:
    """The OpenTopography key used when a caller passes none (or None)."""
    return _api_key


def set_api_key(key: str | None) -> None:
    """Replace the default OpenTopography key (e.g. after the user saves one)."""
    global _api_key
    _api_key = key or None


def request_geotiff(
    demtype: str,
    north: float, south: float, east: float, west: float,
    *,
    api_key: str | None = None,
    timeout: float = 120,
) -> bytes:
    """Download one global-DEM GeoTIFF for the bbox and return its bytes.

    *api_key* defaults to :func:`get_api_key`.

    Raises:
        RuntimeError: ``"OpenTopography API error (<status>): <text>"`` on an
            HTTP error or a non-GeoTIFF (JSON / XML / HTML) response.
        requests.RequestException: the request itself failed.
    """
    params = {
        "demtype": demtype,
        "south": south, "north": north, "west": west, "east": east,
        "outputFormat": "GTiff",
    }
    key = api_key or _api_key
    if key:
        params["API_Key"] = key
    resp = requests.get(GLOBALDEM_URL, params=params, timeout=timeout)
    content_type = resp.headers.get("Content-Type", "")
    if resp.status_code != 200 or any(t in content_type for t in ("json", "xml", "html")):
        try:
            err_text = resp.text[:500]
        except Exception:  # noqa: BLE001
            err_text = f"HTTP {resp.status_code}"
        raise RuntimeError(f"OpenTopography API error ({resp.status_code}): {err_text}")
    return resp.content


def fetch_opentopo_dem(
    north: float, south: float, east: float, west: float,
    demtype: str, api_key: str | None, dim: int,
) -> np.ndarray:
    """
    Download a GeoTIFF from OpenTopography's global DEM API and return a
    (height, width) float64 array of elevation values (metres), longer side
    *dim*, no-data as NaN.

    Responses are cached under :data:`CACHE_PATH`. *api_key* None means
    :func:`get_api_key`.

    Raises:
        RuntimeError  if the API returns an error or the GeoTIFF cannot be read.
    """
    # NOTE: `dim` is deliberately NOT part of this key. The GeoTIFF that
    # OpenTopography returns depends only on (demtype, bbox) — `dim` never
    # reaches the API, it only sets `fit_dim` on the read below.
    # Including it here meant every resolution change re-downloaded a
    # byte-identical file over the network (measured: 11.9 s for a 0.2 deg
    # bbox, versus 0.4 s once the tile is on disk).
    cache_key = hashlib.md5(
        f"{demtype}_{north:.5f}_{south:.5f}_{east:.5f}_{west:.5f}".encode()
    ).hexdigest()
    CACHE_PATH.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_PATH / f"{cache_key}.tif"

    if not cache_file.exists():
        logger.info(
            f"Fetching OpenTopography DEM: {demtype} bbox=({north},{south},{east},{west})")
        cache_file.write_bytes(
            request_geotiff(demtype, north, south, east, west, api_key=api_key))
        logger.info(f"Cached OpenTopography response to {cache_file}")

    try:
        data = read_geotiff(cache_file, fit_dim=dim, dtype=np.float64)[0]
    except ValueError as exc:
        raise RuntimeError(f"Cannot read the OpenTopography GeoTIFF: {exc}") from exc
    if data.size == 0:
        raise RuntimeError(
            "OpenTopography returned an empty raster for this bbox.")
    return data
