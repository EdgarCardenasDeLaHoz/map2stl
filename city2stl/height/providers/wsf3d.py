"""
WSF3D Building Height provider — DLR World Settlement Footprint 3D.

Data: GeoTIFF raster, ~90 m resolution, global coverage.
Source: https://download.geoservice.dlr.de/WSF3D/files/
License: CC-BY-4.0
Values: Int16 with gain factor 0.1 (i.e. stored_value * 0.1 = metres).
Tiles: 1° × 1° grids named by upper-left / lower-right corners.

Provides average building height per ~90 m cell.  Best used as a
coarse-resolution baseline; finer sources (Google 3D, LiDAR) override it.
"""

from __future__ import annotations

import io
import logging
from math import floor, ceil
from typing import Tuple

import numpy as np
import requests
import rasterio

from city2stl.height import BBox, HeightProvider, HeightResult, _resample
from app.server.core.cache import (
    CACHE_ROOT, make_cache_key,
    write_array_cache, read_array_cache,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://download.geoservice.dlr.de/WSF3D/files/tiles"
_GAIN = 0.1  # Int16 → metres
_CONFIDENCE = 0.5  # mid-range: 90 m is coarse
_RESOLUTION_M = 90.0
_NAMESPACE = "wsf3d"
_DOWNLOAD_TIMEOUT = 60  # seconds per tile

# Register TTL if not already present
from app.server.core import cache as _cache_mod
_cache_mod.NAMESPACE_TTL.setdefault(_NAMESPACE, 90 * 86400)


# ── tile naming helpers ──────────────────────────────────────────

def _lon_label(deg: int) -> str:
    """e.g. -75 → 'w075', 3 → 'e003'."""
    if deg < 0:
        return f"w{abs(deg):03d}"
    return f"e{deg:03d}"


def _lat_label(deg: int) -> str:
    """e.g. 40 → 'n40', -5 → 's05'."""
    if deg < 0:
        return f"s{abs(deg):02d}"
    return f"n{deg:02d}"


def tile_name(lon_west: int, lat_south: int) -> str:
    """Build the WSF3D tile directory + file stem for a 1°×1° cell.

    WSF3D uses *upper-left / lower-right* naming:
      UL corner = (lon_west,  lat_south + 1)
      LR corner = (lon_west + 1, lat_south)
    Example: lon_west=-75, lat_south=9 → "w075_n10_w074_n09"
    """
    ul_lon = _lon_label(lon_west)
    ul_lat = _lat_label(lat_south + 1)
    lr_lon = _lon_label(lon_west + 1)
    lr_lat = _lat_label(lat_south)
    return f"{ul_lon}_{ul_lat}_{lr_lon}_{lr_lat}"


def tiles_for_bbox(bbox: BBox) -> list[tuple[int, int]]:
    """Return (lon_west, lat_south) for every 1° tile overlapping *bbox*.

    bbox = (north, south, east, west).
    """
    north, south, east, west = bbox
    lon_min = floor(west)
    lon_max = ceil(east) - 1       # tile starts at integer lon
    lat_min = floor(south)
    lat_max = ceil(north) - 1      # tile starts at integer lat south
    tiles = []
    for lon in range(lon_min, lon_max + 1):
        for lat in range(lat_min, lat_max + 1):
            tiles.append((lon, lat))
    return tiles


def _tile_url(lon_west: int, lat_south: int) -> str:
    name = tile_name(lon_west, lat_south)
    return f"{_BASE_URL}/{name}/WSF3D_V02_{name}_BuildingHeight.tif"


# ── download + cache ────────────────────────────────────────────

def _download_tile(lon_west: int, lat_south: int) -> np.ndarray | None:
    """Download a single WSF3D BuildingHeight tile, return as float32 metres.

    Returns None if the tile does not exist on the server (HTTP 404).
    Caches the decoded array under the ``wsf3d`` cache namespace.
    """
    key = make_cache_key(_NAMESPACE, lat_south + 1, lat_south,
                         lon_west + 1, lon_west)
    hit = read_array_cache(_NAMESPACE, key)
    if hit is not None:
        arrays, _meta = hit
        logger.debug("wsf3d cache hit: %s", tile_name(lon_west, lat_south))
        return arrays.get("height")

    url = _tile_url(lon_west, lat_south)
    logger.info("Downloading WSF3D tile: %s", url)
    try:
        r = requests.get(url, timeout=_DOWNLOAD_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("WSF3D download failed: %s", exc)
        return None
    if r.status_code == 404:
        logger.debug("WSF3D tile not found (no settlements): %s",
                      tile_name(lon_west, lat_south))
        return None
    r.raise_for_status()

    with rasterio.open(io.BytesIO(r.content)) as src:
        raw = src.read(1)  # Int16
    arr = raw.astype(np.float32) * _GAIN
    arr[raw <= 0] = np.nan  # 0 or negative → no building data

    write_array_cache(_NAMESPACE, key, {"height": arr},
                      metadata={"tile": tile_name(lon_west, lat_south),
                                "resolution_m": _RESOLUTION_M})
    return arr


# ── HeightProvider implementation ────────────────────────────────

class WSF3DProvider:
    """DLR WSF3D global building height (~90 m resolution)."""

    name = "wsf3d"

    def covers(self, bbox: BBox) -> bool:
        """WSF3D has global coverage; always returns True."""
        return True

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        """Fetch and mosaic WSF3D tiles for *bbox*, resample to *dim* = (H, W)."""
        north, south, east, west = bbox
        tiles = tiles_for_bbox(bbox)
        if not tiles:
            return _empty_result(dim)

        # Determine mosaic dimensions
        lon_min = min(t[0] for t in tiles)
        lat_min = min(t[1] for t in tiles)
        lon_max = max(t[0] for t in tiles) + 1
        lat_max = max(t[1] for t in tiles) + 1

        tile_arrays: dict[tuple[int, int], np.ndarray] = {}
        for lon_w, lat_s in tiles:
            arr = _download_tile(lon_w, lat_s)
            if arr is not None:
                tile_arrays[(lon_w, lat_s)] = arr

        if not tile_arrays:
            return _empty_result(dim)

        # Mosaic tiles into a single array covering the full tile extent
        # All tiles should be the same size (1° at same resolution)
        sample = next(iter(tile_arrays.values()))
        tile_h, tile_w = sample.shape

        n_tiles_x = lon_max - lon_min
        n_tiles_y = lat_max - lat_min
        mosaic = np.full((n_tiles_y * tile_h, n_tiles_x * tile_w),
                         np.nan, dtype=np.float32)

        for (lon_w, lat_s), arr in tile_arrays.items():
            # Tiles are north-up: row 0 = highest latitude
            col = lon_w - lon_min
            row = (lat_max - 1) - lat_s   # flip: highest lat → top row
            r0 = row * tile_h
            c0 = col * tile_w
            # Handle tiles with slightly different dimensions
            th = min(arr.shape[0], mosaic.shape[0] - r0)
            tw = min(arr.shape[1], mosaic.shape[1] - c0)
            mosaic[r0:r0 + th, c0:c0 + tw] = arr[:th, :tw]

        # Crop mosaic to the requested bbox
        mosaic_north = lat_max
        mosaic_south = lat_min
        mosaic_west = lon_min
        mosaic_east = lon_max

        full_h, full_w = mosaic.shape
        # Pixel coordinates of the bbox within the mosaic
        px_west = int((west - mosaic_west) / (mosaic_east - mosaic_west) * full_w)
        px_east = int((east - mosaic_west) / (mosaic_east - mosaic_west) * full_w)
        px_north = int((mosaic_north - north) / (mosaic_north - mosaic_south) * full_h)
        px_south = int((mosaic_north - south) / (mosaic_north - mosaic_south) * full_h)

        px_west = max(0, min(px_west, full_w - 1))
        px_east = max(px_west + 1, min(px_east, full_w))
        px_north = max(0, min(px_north, full_h - 1))
        px_south = max(px_north + 1, min(px_south, full_h))

        cropped = mosaic[px_north:px_south, px_west:px_east]

        # Resample to target dim
        raster = _resample(cropped, dim)
        confidence = np.where(np.isnan(raster), 0.0, _CONFIDENCE).astype(np.float32)

        return HeightResult(
            raster=raster,
            confidence=confidence,
            source_name=self.name,
            resolution_m=_RESOLUTION_M,
        )


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="wsf3d",
        resolution_m=_RESOLUTION_M,
    )
