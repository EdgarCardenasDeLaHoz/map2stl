"""Satellite imagery: Web Mercator (slippy) tile math and ESRI World Imagery fetch.

The one home for XYZ tile arithmetic and tile stitching. Users:

- ``geo2stl.sat2stl.fetch_satellite_tiles`` — bbox JPEG for the app (reprojected
  to plate carrée and resized to ``dim``).
- ``city2stl.roof_tiles`` — per-building zoom-18 crops with an on-disk tile cache.
- ``city2stl.skyline.satellite_image`` — cached region composite plus a
  lon/lat → pixel closure.

A bbox is a dict with ``north``, ``south``, ``east``, ``west`` in degrees (as in
``geo2stl.geo``). "Global pixels" are continuous Web Mercator pixel coordinates at
a zoom level: ``x`` grows east from the antimeridian, ``y`` grows south from
85.05° N, and one tile is ``TILE_SIZE`` pixels.

ESRI World Imagery needs no API key for reasonable use.
"""

from __future__ import annotations

import io
import logging
import math
import os
from pathlib import Path

import numpy as np

from geo2stl.geo import bbox_size_m

logger = logging.getLogger(__name__)

ESRI_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
TILE_SIZE = 256
#: Zoom range used for bbox fetches. ESRI tops out at 19 in well-covered metros
#: and 404s above that; 18 is the safe ceiling.
MIN_ZOOM = 6
MAX_ZOOM = 18
#: More than this many tiles along one side means thousands of requests.
MAX_TILES_PER_DIM = 64
#: Web Mercator latitude limit.
MAX_LAT = 85.05
#: Equatorial circumference (m); ground size of zoom 0.
EARTH_CIRCUMFERENCE_M = 40_075_016.686
USER_AGENT = "strm2stl/1.0"


# ---------------------------------------------------------------------------
# Tile math
# ---------------------------------------------------------------------------

def lon_to_global_px(lon: float, zoom: int) -> float:
    """Continuous Web Mercator pixel x of *lon* at *zoom*."""
    return (lon + 180.0) / 360.0 * TILE_SIZE * (1 << zoom)


def lat_to_global_px(lat: float, zoom: int) -> float:
    """Continuous Web Mercator pixel y of *lat* at *zoom* (clamped to ±85.05°)."""
    lat_r = math.radians(max(-MAX_LAT, min(MAX_LAT, lat)))
    merc = math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r))
    return (1.0 - merc / math.pi) / 2.0 * TILE_SIZE * (1 << zoom)


def global_px_to_lonlat(gx: float, gy: float, zoom: int) -> tuple[float, float]:
    """Inverse of :func:`lon_to_global_px` / :func:`lat_to_global_px`."""
    world = TILE_SIZE * (1 << zoom)
    lon = gx / world * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * gy / world))))
    return lon, lat


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """Tile ``(x, y)`` containing *lon*, *lat* at *zoom*."""
    return (int(lon_to_global_px(lon, zoom) // TILE_SIZE),
            int(lat_to_global_px(lat, zoom) // TILE_SIZE))


def tile_bounds(x: int, y: int, zoom: int) -> dict:
    """Bbox dict (``north``/``south``/``east``/``west``) covered by tile ``(x, y)``."""
    west, north = global_px_to_lonlat(x * TILE_SIZE, y * TILE_SIZE, zoom)
    east, south = global_px_to_lonlat((x + 1) * TILE_SIZE, (y + 1) * TILE_SIZE, zoom)
    return {"north": north, "south": south, "east": east, "west": west}


def m_per_px(lat: float, zoom: int) -> float:
    """Ground metres per pixel at *lat* and *zoom*."""
    return EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (TILE_SIZE * (1 << zoom))


def tile_range(bbox: dict, zoom: int) -> tuple[int, int, int, int]:
    """``(x_min, x_max, y_min, y_max)`` of the tiles covering *bbox*, inclusive."""
    n_max = (1 << zoom) - 1
    x0, y0 = lonlat_to_tile(bbox["west"], bbox["north"], zoom)
    x1, y1 = lonlat_to_tile(bbox["east"], bbox["south"], zoom)
    return (max(0, min(x0, n_max)), max(0, min(x1, n_max)),
            max(0, min(y0, n_max)), max(0, min(y1, n_max)))


def choose_zoom(
    bbox: dict,
    *,
    target_m_per_px: float | None = None,
    dim: int | None = None,
    max_tiles_per_dim: int = MAX_TILES_PER_DIM,
    max_tiles_total: int | None = None,
) -> int:
    """Zoom level for *bbox*: nearest to *target_m_per_px*, then walked down
    until the tile caps hold.

    Without *target_m_per_px*, the target is the bbox diagonal over
    ``dim·√2`` pixels (at least 1 m/px), so a large region does not pull
    needlessly fine tiles. Result is within ``[MIN_ZOOM, MAX_ZOOM]``.
    """
    if target_m_per_px is None:
        if not dim:
            raise ValueError("choose_zoom needs target_m_per_px or dim")
        target_m_per_px = max(1.0, math.hypot(*bbox_size_m(bbox)) / (dim * math.sqrt(2)))

    raw = math.log2(EARTH_CIRCUMFERENCE_M / (TILE_SIZE * target_m_per_px))
    zoom = max(MIN_ZOOM, min(MAX_ZOOM, int(round(raw))))
    while zoom > MIN_ZOOM:
        x0, x1, y0, y1 = tile_range(bbox, zoom)
        n_x, n_y = x1 - x0 + 1, y1 - y0 + 1
        if max(n_x, n_y) <= max_tiles_per_dim and (
                max_tiles_total is None or n_x * n_y <= max_tiles_total):
            break
        zoom -= 1
    return zoom


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def new_session(user_agent: str = USER_AGENT):
    """A ``requests.Session`` with the project User-Agent (one per thread)."""
    import requests  # noqa: PLC0415
    s = requests.Session()
    s.headers["User-Agent"] = user_agent
    return s


def tile_cache_path(cache_dir: str | os.PathLike, zoom: int, x: int, y: int) -> Path:
    """On-disk location of one cached tile: ``<cache_dir>/<z>/<x>/<y>.jpg``."""
    return Path(cache_dir) / str(zoom) / str(x) / f"{y:d}.jpg"


def fetch_tile(
    zoom: int, x: int, y: int,
    *,
    session=None,
    cache_dir: str | os.PathLike | None = None,
    timeout: float = 10,
    url: str = ESRI_TILE_URL,
):
    """One tile as an RGB ``PIL.Image``, or None if it cannot be had.

    With *cache_dir*, a cached tile is returned without a request and a
    fetched one is written there (JPEG, quality 90).
    """
    from PIL import Image  # noqa: PLC0415

    n = 1 << zoom
    if not (0 <= x < n and 0 <= y < n):
        return None
    path = tile_cache_path(cache_dir, zoom, x, y) if cache_dir is not None else None
    if path is not None and path.exists():
        try:
            return Image.open(path).convert("RGB")
        except Exception:  # noqa: BLE001 - corrupt cache entry, refetch
            path.unlink(missing_ok=True)
    try:
        r = (session or new_session()).get(url.format(z=zoom, x=x, y=y), timeout=timeout)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Satellite tile %d/%d/%d failed: %s", zoom, y, x, exc)
        return None
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path, "JPEG", quality=90)
        except Exception:  # noqa: BLE001 - cache write is best effort
            pass
    return img


def stitch_tiles(
    x_min: int, x_max: int, y_min: int, y_max: int, zoom: int,
    *,
    session=None,
    cache_dir: str | os.PathLike | None = None,
    timeout: float = 10,
):
    """Paste tiles ``x_min..x_max`` × ``y_min..y_max`` into one RGB image.

    Missing tiles stay black. Returns ``(image, tiles_loaded)``; the image's
    (0, 0) is global pixel ``(x_min·TILE_SIZE, y_min·TILE_SIZE)``.
    """
    from PIL import Image  # noqa: PLC0415

    session = session or new_session()
    composite = Image.new("RGB", ((x_max - x_min + 1) * TILE_SIZE,
                                  (y_max - y_min + 1) * TILE_SIZE))
    loaded = 0
    for tx in range(x_min, x_max + 1):
        for ty in range(y_min, y_max + 1):
            t = fetch_tile(zoom, tx, ty, session=session, cache_dir=cache_dir,
                           timeout=timeout)
            if t is None:
                continue
            composite.paste(t, ((tx - x_min) * TILE_SIZE, (ty - y_min) * TILE_SIZE))
            loaded += 1
    return composite, loaded


def fetch_rgb(
    bbox: dict,
    *,
    zoom: int | None = None,
    dim: int | None = None,
    target_m_per_px: float | None = None,
    max_tiles_per_dim: int = MAX_TILES_PER_DIM,
    max_tiles_total: int | None = None,
    session=None,
    cache_dir: str | os.PathLike | None = None,
    timeout: float = 10,
) -> tuple[np.ndarray, dict]:
    """Web Mercator RGB image of *bbox*, cropped to it.

    The zoom is *zoom* if given, else :func:`choose_zoom` from
    *target_m_per_px* or *dim*. Rows are Mercator-spaced (row 0 = north); use
    ``geo2stl.sat2stl._mercator_to_plate_carree`` for uniform latitude rows.

    Returns:
        ``(rgb, meta)``: *rgb* is ``(H, W, 3)`` uint8; *meta* holds ``zoom``,
        ``crop_origin_x`` / ``crop_origin_y`` (global pixel of the image's
        (0, 0)), ``shape``, ``tiles_loaded`` and ``tiles_total``.

    Raises:
        RuntimeError: every tile failed.
    """
    if zoom is None:
        zoom = choose_zoom(bbox, target_m_per_px=target_m_per_px, dim=dim,
                           max_tiles_per_dim=max_tiles_per_dim,
                           max_tiles_total=max_tiles_total)
    x_min, x_max, y_min, y_max = tile_range(bbox, zoom)
    total = (x_max - x_min + 1) * (y_max - y_min + 1)
    composite, loaded = stitch_tiles(x_min, x_max, y_min, y_max, zoom,
                                     session=session, cache_dir=cache_dir,
                                     timeout=timeout)
    if loaded == 0:
        raise RuntimeError(
            f"All {total} satellite tiles failed for bbox {bbox} at zoom {zoom}. "
            "Check network access to server.arcgisonline.com."
        )
    logger.info("Satellite tiles: %d/%d loaded at zoom %d", loaded, total, zoom)

    origin_x, origin_y = x_min * TILE_SIZE, y_min * TILE_SIZE
    big_w, big_h = composite.size
    box = (
        max(0, int(math.floor(lon_to_global_px(bbox["west"], zoom))) - origin_x),
        max(0, int(math.floor(lat_to_global_px(bbox["north"], zoom))) - origin_y),
        min(big_w, int(math.ceil(lon_to_global_px(bbox["east"], zoom))) - origin_x),
        min(big_h, int(math.ceil(lat_to_global_px(bbox["south"], zoom))) - origin_y),
    )
    rgb = np.asarray(composite.crop(box), dtype=np.uint8)
    meta = {
        "zoom": zoom,
        "shape": [int(rgb.shape[0]), int(rgb.shape[1])],
        "crop_origin_x": origin_x + box[0],
        "crop_origin_y": origin_y + box[1],
        "tiles_loaded": loaded,
        "tiles_total": total,
    }
    return rgb, meta
