"""Satellite imagery fetcher + lat/lon → pixel projection (F-SKY10 Phase 1).

The F-SKY10 cross-view scorer needs a high-resolution aerial / satellite
image of the region so it can sample each OSM polygon's *roof* colour and
geometry, then compare to that building's *side* appearance in a Street
View. The strm2stl project already uses ESRI World Imagery WMTS tiles
(no API key) for the main app via ``geo2stl/sat2stl.py``; this module is
a focused wrapper for the skyline_cv flow:

  - Fetch + stitch the ESRI tiles that cover a region's bbox into a single
    numpy RGB image.
  - Cache the composite to disk (PNG under ``runs/satellite_image_cache/``)
    so subsequent runs skip the ~5-50 HTTP requests. Keyed by bbox + zoom.
  - Return a closure that projects (lon, lat) → (x_px, y_px) into the
    image, accounting for the Web Mercator → linear-pixel mapping inside
    the cropped composite.

Phase 2 (``cross_view.py``) consumes the image + projection to compute
per-building roof colour / width / edge consistency scores.

Cache layout (``runs/satellite_image_cache/``):
  sat_<bbox-hash>_z<zoom>.png       — RGB composite, cropped to bbox
  sat_<bbox-hash>_z<zoom>.json      — metadata (bbox, zoom, image dims)

See ``docs/plans/F-SKY10-non-ml-cross-view-registration.md``.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from typing import Callable

import numpy as np
import requests
from PIL import Image


_CACHE_DIR = Path(__file__).parent / "runs" / "satellite_image_cache"
_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
_TILE_SIZE = 256

# ESRI's World_Imagery service maxes out at zoom 19 in well-covered metros;
# above that we'd get 404s. 18 is the safe ceiling.
_MAX_ZOOM = 18
_MIN_ZOOM = 6
# A region bigger than 64×64 tiles at the requested zoom would mean 4000+
# HTTP requests per fetch — the existing geo2stl uses the same guard.
_MAX_TILES_PER_DIM = 64
# Total-tile cap. At 256×256 px per tile the disk footprint is ~25 KB/tile
# JPEG and ~200 KB/tile raw, so 400 tiles ≈ 10 MB cached / 80 MB in memory
# composite. Picked to keep skyline_cv's per-region cache and process RSS
# at reasonable strm2stl scale. Caller can pre-narrow the bbox or raise
# ``target_m_per_px`` to get under this cap on huge regions.
_MAX_TILES_TOTAL = 400


def _lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """Web Mercator tile (x, y) at the given zoom level."""
    n = 1 << zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(max(-85.05, min(85.05, lat)))
    y = int(
        (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi)
        * 0.5 * n
    )
    return x, y


def _lon_to_global_px(lon: float, zoom: int) -> float:
    """Continuous Web Mercator pixel X at a given zoom (no tile rounding)."""
    n = 1 << zoom
    return (lon + 180.0) / 360.0 * n * _TILE_SIZE


def _lat_to_global_px(lat: float, zoom: int) -> float:
    """Continuous Web Mercator pixel Y at a given zoom (no tile rounding)."""
    n = 1 << zoom
    lat_r = math.radians(max(-85.05, min(85.05, lat)))
    return (
        (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi)
        * 0.5 * n * _TILE_SIZE
    )


def _choose_zoom(
    bbox: tuple[float, float, float, float], target_m_per_px: float
) -> int:
    """Pick the zoom level that gets closest to ``target_m_per_px`` at the
    bbox's mid-latitude without exceeding ``_MAX_TILES_PER_DIM`` tiles per
    side.

    Web Mercator: zoom z covers 40075 km / (256·2^z) m per pixel at the
    equator; cos(lat) compresses that at higher latitudes.
    """
    south, west, north, east = bbox
    mid_lat = 0.5 * (south + north)
    earth_m = 40_075_017.0
    # tiles_per_side ≈ earth_m / (256 · target_m_per_px) at equator
    raw_zoom = math.log2(earth_m / (_TILE_SIZE * target_m_per_px))
    zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, int(round(raw_zoom))))
    # Tile-count guard: walk zoom down until both per-dimension and total
    # caps are satisfied. The total cap is what catches large bboxes — a
    # Cartagena-scale bbox at 1 m/px would be 56×53 tiles (under the
    # per-dim cap) but 2968 total which busts cache/RAM budgets.
    while zoom > _MIN_ZOOM:
        tx0, ty0 = _lonlat_to_tile(west, north, zoom)
        tx1, ty1 = _lonlat_to_tile(east, south, zoom)
        n_x = abs(tx1 - tx0) + 1
        n_y = abs(ty1 - ty0) + 1
        if max(n_x, n_y) <= _MAX_TILES_PER_DIM and n_x * n_y <= _MAX_TILES_TOTAL:
            break
        zoom -= 1
    return zoom


def _bbox_hash(bbox: tuple[float, float, float, float]) -> str:
    """Short stable hash of the bbox (5-decimal precision keeps cache hits
    even across tiny floating-point rounds in the caller)."""
    key = ",".join(f"{v:.5f}" for v in bbox)
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:10]


def fetch_region_satellite(
    bbox: tuple[float, float, float, float],
    target_m_per_px: float = 2.0,
) -> tuple[np.ndarray, Callable[[float, float], tuple[float, float]], dict]:
    """Fetch the satellite image covering ``bbox`` and return it as a numpy
    RGB array plus a (lon, lat) → (x_px, y_px) projection closure.

    Parameters
    ----------
    bbox
        (south, west, north, east) in degrees. Same orientation as
        ``fetch_microsoft_buildings_for_bbox`` for consistency.
    target_m_per_px
        Desired ground sampling distance. 1.0 m/px works for skyline-CV's
        roof-colour sampling (a 25-m-wide tower → 25 px wide, ample for a
        median-colour computation). Bigger numbers = coarser image / fewer
        tiles fetched.

    Returns
    -------
    (image_rgb, project_lonlat, meta)
        - ``image_rgb`` : (H, W, 3) uint8 numpy array, cropped to the bbox.
        - ``project_lonlat(lon, lat)`` : returns (x_px, y_px) floats in
          image coordinates. Origin at top-left, y grows downward (standard
          image convention).
        - ``meta`` : {"zoom": int, "bbox": tuple, "shape": (H, W)}.

    Raises
    ------
    RuntimeError if every tile fetch fails (caller can fall back to OSM-only
    without satellite signals; cross-view scoring is opt-in).
    """
    south, west, north, east = bbox
    zoom = _choose_zoom(bbox, target_m_per_px)
    cache_key = f"sat_{_bbox_hash(bbox)}_z{zoom}"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = _CACHE_DIR / f"{cache_key}.png"
    meta_path = _CACHE_DIR / f"{cache_key}.json"

    if png_path.exists() and meta_path.exists():
        img = np.asarray(Image.open(png_path).convert("RGB"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        img, meta = _fetch_and_stitch(bbox, zoom)
        Image.fromarray(img).save(png_path, optimize=True)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # Bake the bbox's NW corner into a closure so callers don't need to
    # know the zoom or tile origin. ``crop_origin_px`` is the global
    # Web Mercator pixel coord of the cropped image's (0, 0).
    crop_origin_x = meta["crop_origin_x"]
    crop_origin_y = meta["crop_origin_y"]
    z = int(meta["zoom"])

    def project(lon: float, lat: float) -> tuple[float, float]:
        gx = _lon_to_global_px(lon, z)
        gy = _lat_to_global_px(lat, z)
        return gx - crop_origin_x, gy - crop_origin_y

    return img, project, meta


def _fetch_and_stitch(
    bbox: tuple[float, float, float, float], zoom: int,
) -> tuple[np.ndarray, dict]:
    """Pull every WMTS tile covering ``bbox`` at ``zoom``, paste into one
    composite, crop to the bbox, return (rgb_ndarray, meta).
    """
    south, west, north, east = bbox
    tx_min, ty_min = _lonlat_to_tile(west, north, zoom)
    tx_max, ty_max = _lonlat_to_tile(east, south, zoom)

    big_w = (tx_max - tx_min + 1) * _TILE_SIZE
    big_h = (ty_max - ty_min + 1) * _TILE_SIZE
    composite = Image.new("RGB", (big_w, big_h))

    session = requests.Session()
    session.headers["User-Agent"] = "strm2stl/skyline_cv/1.0"
    loaded = 0
    total = (tx_max - tx_min + 1) * (ty_max - ty_min + 1)
    last_err: Exception | None = None
    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            url = _TILE_URL.format(z=zoom, y=ty, x=tx)
            try:
                r = session.get(url, timeout=10)
                r.raise_for_status()
                tile = Image.open(io.BytesIO(r.content)).convert("RGB")
                composite.paste(
                    tile,
                    ((tx - tx_min) * _TILE_SIZE, (ty - ty_min) * _TILE_SIZE),
                )
                loaded += 1
            except Exception as e:
                last_err = e
                # Continue — partial-coverage composites still let the
                # cross-view scorer work for buildings under loaded tiles.
                continue
    if loaded == 0:
        raise RuntimeError(
            f"All {total} ESRI satellite tiles failed for bbox {bbox} at "
            f"zoom {zoom}. Last error: {last_err}"
        )

    # Crop to the actual bbox in global pixel coords.
    crop_x0 = int(math.floor(_lon_to_global_px(west, zoom)))
    crop_y0 = int(math.floor(_lat_to_global_px(north, zoom)))
    crop_x1 = int(math.ceil(_lon_to_global_px(east, zoom)))
    crop_y1 = int(math.ceil(_lat_to_global_px(south, zoom)))
    tile_origin_x = tx_min * _TILE_SIZE
    tile_origin_y = ty_min * _TILE_SIZE
    local_box = (
        max(0, crop_x0 - tile_origin_x),
        max(0, crop_y0 - tile_origin_y),
        min(big_w, crop_x1 - tile_origin_x),
        min(big_h, crop_y1 - tile_origin_y),
    )
    cropped = composite.crop(local_box)
    arr = np.asarray(cropped, dtype=np.uint8)
    meta = {
        "zoom": zoom,
        "bbox": list(bbox),
        "shape": [int(arr.shape[0]), int(arr.shape[1])],
        "crop_origin_x": tile_origin_x + local_box[0],
        "crop_origin_y": tile_origin_y + local_box[1],
        "tiles_loaded": loaded,
        "tiles_total": total,
    }
    return arr, meta


def crop_polygon_from_satellite(
    image: np.ndarray,
    project: Callable[[float, float], tuple[float, float]],
    polygon_lonlat: list[tuple[float, float]],
    padding_px: int = 4,
) -> "np.ndarray | None":
    """Return the satellite-image crop covering a polygon's projected
    pixel bounding box, with ``padding_px`` of slack on each side.

    Used by the F-SKY10 colour-consistency scorer to sample a building's
    roof pixels. Returns ``None`` when the polygon projects entirely
    outside the image (e.g. the bbox missed an edge building) so the
    caller can skip the cross-view score gracefully.

    The crop is *axis-aligned*, not polygon-clipped — for median-colour
    sampling the rectangle-vs-polygon distinction is sub-pixel noise on a
    typical 25-px-wide tower, and the matcher's tolerance for that is
    enforced by the score weighting (see plan, Signal 1).
    """
    if not polygon_lonlat:
        return None
    h, w = image.shape[:2]
    xs: list[float] = []
    ys: list[float] = []
    for lon, lat in polygon_lonlat:
        x, y = project(lon, lat)
        xs.append(x)
        ys.append(y)
    x0 = int(math.floor(min(xs))) - padding_px
    y0 = int(math.floor(min(ys))) - padding_px
    x1 = int(math.ceil(max(xs))) + padding_px
    y1 = int(math.ceil(max(ys))) + padding_px
    x0c = max(0, x0)
    y0c = max(0, y0)
    x1c = min(w, x1)
    y1c = min(h, y1)
    if x1c <= x0c or y1c <= y0c:
        return None
    return image[y0c:y1c, x0c:x1c]
