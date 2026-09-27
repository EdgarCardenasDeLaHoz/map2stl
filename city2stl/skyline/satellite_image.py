"""Satellite imagery fetcher + lat/lon → pixel projection (F-SKY10 Phase 1).

The F-SKY10 cross-view scorer needs a high-resolution aerial / satellite
image of the region so it can sample each OSM polygon's *roof* colour and
geometry, then compare to that building's *side* appearance in a Street
View. Tile math and stitching are ``geo2stl.imagery`` (ESRI World Imagery,
no API key); this module is a focused wrapper for the skyline flow:

  - Fetch the ESRI image that covers a region's bbox (``imagery.fetch_rgb``).
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
import json
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from geo2stl import imagery

_CACHE_DIR = Path(__file__).parent / "runs" / "satellite_image_cache"

# Total-tile cap. At 256×256 px per tile the disk footprint is ~25 KB/tile
# JPEG and ~200 KB/tile raw, so 400 tiles ≈ 10 MB cached / 80 MB in memory
# composite. Picked to keep skyline's per-region cache and process RSS
# at reasonable map2stl scale. Caller can pre-narrow the bbox or raise
# ``target_m_per_px`` to get under this cap on huge regions. The total cap
# is what catches large bboxes — a Cartagena-scale bbox at 1 m/px would be
# 56×53 tiles (under the per-dim cap) but 2968 total.
_MAX_TILES_TOTAL = 400


def _bbox_dict(bbox: tuple[float, float, float, float]) -> dict:
    south, west, north, east = bbox
    return {"north": north, "south": south, "east": east, "west": west}


def _choose_zoom(
    bbox: tuple[float, float, float, float], target_m_per_px: float
) -> int:
    """Zoom closest to ``target_m_per_px`` within the 64-per-side and
    ``_MAX_TILES_TOTAL`` tile caps (``geo2stl.imagery.choose_zoom``)."""
    return imagery.choose_zoom(_bbox_dict(bbox), target_m_per_px=target_m_per_px,
                               max_tiles_total=_MAX_TILES_TOTAL)


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
        img, meta = imagery.fetch_rgb(_bbox_dict(bbox), zoom=zoom)
        meta["bbox"] = list(bbox)
        Image.fromarray(img).save(png_path, optimize=True)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # Bake the bbox's NW corner into a closure so callers don't need to
    # know the zoom or tile origin. ``crop_origin_px`` is the global
    # Web Mercator pixel coord of the cropped image's (0, 0).
    crop_origin_x = meta["crop_origin_x"]
    crop_origin_y = meta["crop_origin_y"]
    z = int(meta["zoom"])

    def project(lon: float, lat: float) -> tuple[float, float]:
        gx = imagery.lon_to_global_px(lon, z)
        gy = imagery.lat_to_global_px(lat, z)
        return gx - crop_origin_x, gy - crop_origin_y

    return img, project, meta


def crop_polygon_from_satellite(
    image: np.ndarray,
    project: Callable[[float, float], tuple[float, float]],
    polygon_lonlat: list[tuple[float, float]],
    padding_px: int = 4,
) -> np.ndarray | None:
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
