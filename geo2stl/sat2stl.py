"""
geo2stl/sat.py — Satellite and water-mask imagery fetching.

Contains:
  - fetch_water_mask        — binary water mask from ESA/JRC + SRTM bathymetry
  - fetch_water_mask_images — raw image fetch (ESA, JRC, elevation); call via run_in_executor
  - fetch_sat_overlay       — Google Earth Engine satellite overlay
  - fetch_satellite_tiles   — ESRI World Imagery WMTS tile stitcher (no API key required)

All functions are pure computation with no HTTP framework dependencies and
can be called from route handlers via asyncio.run_in_executor.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np

try:
    import joblib
except ImportError:
    joblib = None

METRES_PER_DEGREE: float = 111_320.0

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ESRI World Imagery WMTS tile constants + Web Mercator helpers
# ---------------------------------------------------------------------------

_SAT_TILE_URL: str = (
    "https://server.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
_SAT_TILE_SIZE: int = 256  # pixels per tile side (standard WMTS)
# Maximum tiles in one dimension before capping zoom (avoids 4000+ tiles for huge bboxes)
_MAX_SAT_TILES: int = 64
_MIN_SAT_ZOOM: int = 6  # Minimum zoom level (world overview)
_MAX_SAT_ZOOM: int = 18  # Maximum zoom level (highest detail available)


def _calculate_optimal_zoom(
    north: float, south: float, east: float, west: float,
    requested_dim: int, target_m_per_px: float = None
) -> int:
    """
    Calculate optimal zoom level for satellite tiles based on geographic scale and requested resolution.

    Strategy:
      1. Calculate bounding box dimensions in meters at the given latitude
      2. Determine target m/pixel based on bbox size (larger regions -> coarser resolution)
      3. Clamp zoom to avoid fetching excessive tiles (>64 tiles per dimension)
      4. Return zoom level that achieves ~requested_dim pixels output without tile explosion

    Args:
        north, south, east, west: Bounding box in degrees
        requested_dim: Target output dimension (pixels)
        target_m_per_px: Optional fixed m/pixel target; if None, auto-calculate

    Returns:
        Zoom level (6-18) optimized for the bbox scale and requested resolution
    """
    # Calculate bbox dimensions in meters
    mid_lat = (north + south) / 2.0
    m_per_deg_lon = METRES_PER_DEGREE * math.cos(math.radians(mid_lat))
    m_per_deg_lat = METRES_PER_DEGREE

    bbox_w_m = abs(east - west) * m_per_deg_lon
    bbox_h_m = abs(north - south) * m_per_deg_lat
    bbox_diag_m = math.sqrt(bbox_w_m**2 + bbox_h_m**2)

    # If no fixed target, auto-calculate based on bbox size vs. requested output resolution
    if target_m_per_px is None:
        # Heuristic: for a bbox_diag_m distance, aim for requested_dim pixels
        # This ensures large regions don't pull unnecessarily hi-res tiles
        target_m_per_px = max(1, bbox_diag_m / (requested_dim * math.sqrt(2)))

    # Zoom 0 = 40075 km / 256 px = ~156.5 km/px
    # Zoom z = 40075 km / (256 * 2^z) px
    # Solve: 40075000 m / (256 * 2^z) ~= target_m_per_px
    # => 2^z ~= 40075000 / (256 * target_m_per_px)
    # => z ~= log2(40075000 / (256 * target_m_per_px))
    earth_circumference_m = 40075000.0
    tiles_per_side_needed = earth_circumference_m / (256.0 * target_m_per_px)
    zoom = max(_MIN_SAT_ZOOM, min(_MAX_SAT_ZOOM,
               math.log2(tiles_per_side_needed)))
    zoom = int(round(zoom))

    # Secondary check: clamp zoom if it would request too many tiles
    n = 2 ** zoom
    max_tiles_per_dim = max(
        abs(_wm_lon_to_tile(east, n) - _wm_lon_to_tile(west, n)) + 1,
        abs(_wm_lat_to_tile(north, n) - _wm_lat_to_tile(south, n)) + 1
    )
    while max_tiles_per_dim > _MAX_SAT_TILES and zoom > _MIN_SAT_ZOOM:
        zoom -= 1
        n = 2 ** zoom
        max_tiles_per_dim = max(
            abs(_wm_lon_to_tile(east, n) - _wm_lon_to_tile(west, n)) + 1,
            abs(_wm_lat_to_tile(north, n) - _wm_lat_to_tile(south, n)) + 1
        )

    logger.debug(f"Satellite zoom: bbox={bbox_diag_m:.0f}m, target={target_m_per_px:.0f}m/px, "
                 f"zoom={zoom}, max_tiles_per_dim={max_tiles_per_dim}")
    return zoom


def _wm_lon_to_tile(lon: float, n: int) -> int:
    """Return the X tile index for a longitude at zoom level with *n* = 2**zoom tiles."""
    return int((lon + 180.0) / 360.0 * n)


def _wm_lat_to_tile(lat: float, n: int) -> int:
    """Return the Y tile index for a latitude at zoom level with *n* = 2**zoom tiles.

    Uses the Web Mercator (EPSG:3857) tile formula. Clamps lat to +-85.05 degrees.
    """
    lat_r = math.radians(max(-85.05, min(85.05, lat)))
    return int(
        (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi)
        / 2.0 * n
    )


def _mercator_to_plate_carree(img, north: float, south: float):
    """Resample a Web Mercator image to Plate Carree (equirectangular).

    In Web Mercator, pixel rows are spaced uniformly in Mercator-y, not in
    geographic latitude.  This function builds a row-mapping from uniform
    latitude spacing (Plate Carree) back to Mercator-y pixel positions and
    resamples using bilinear interpolation.

    Parameters
    ----------
    img : PIL.Image
        Cropped Mercator image covering *north* to *south*.
    north, south : float
        Geographic latitude bounds (degrees) of the image.

    Returns
    -------
    PIL.Image
        Same width, same height, resampled to uniform latitude spacing.
    """
    from PIL import Image
    cw, ch = img.size
    if ch < 2 or cw < 1:
        return img

    # Mercator-y for the bbox edges (used to normalise)
    def _lat_to_merc_y(lat):
        lat_r = math.radians(max(-85.05, min(85.05, lat)))
        return math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r))

    merc_north = _lat_to_merc_y(north)
    merc_south = _lat_to_merc_y(south)
    merc_span = merc_north - merc_south
    if abs(merc_span) < 1e-12:
        return img

    img_arr = np.array(img)  # (ch, cw, 3) uint8

    # For each output row (uniform lat), find the fractional source row (Mercator)
    out_lats = np.linspace(north, south, ch)  # row 0 = north
    merc_ys = np.array([_lat_to_merc_y(lat) for lat in out_lats])
    # Normalise to [0, ch-1] pixel range: merc_north -> row 0, merc_south -> row ch-1
    src_rows = (merc_north - merc_ys) / merc_span * (ch - 1)
    src_rows = np.clip(src_rows, 0, ch - 1)

    # Bilinear interpolation along the y-axis
    row_floor = np.floor(src_rows).astype(int)
    row_ceil = np.minimum(row_floor + 1, ch - 1)
    frac = (src_rows - row_floor).reshape(-1, 1)  # broadcast over width (and channels)
    if img_arr.ndim == 3:
        frac = frac[:, :, np.newaxis]

    result = (img_arr[row_floor] * (1.0 - frac) + img_arr[row_ceil] * frac)
    result = np.clip(result, 0, 255).astype(np.uint8)

    return Image.fromarray(result)


def fetch_satellite_tiles(north: float, south: float, east: float, west: float, dim: int = 600) -> str:
    """
    Stitch ESRI World Imagery WMTS tiles into a bbox-cropped JPEG and return as base64.

    Uses dynamic zoom level selection based on geographic scale and requested resolution:
      - Small regions (<100km): Requests high-res tiles (zoom 14-18)
      - Large regions (1000km+): Uses coarser tiles (zoom 8-12) to avoid fetching thousands of tiles
      - Medium regions: Intermediate zoom levels

    Automatically limits to max 64 tiles per dimension to prevent excessive network requests
    (e.g., Amazon region would fail with naive zoom, but succeeds with adaptive selection).

    No API key required -- ESRI World Imagery tiles are publicly accessible for reasonable use.

    Returns a base64-encoded JPEG string, or raises on failure.
    """
    import base64
    import requests
    from PIL import Image
    from io import BytesIO

    # Use intelligent zoom calculation instead of fixed loop
    zoom = _calculate_optimal_zoom(north, south, east, west, dim)

    n = 2 ** zoom

    tx_min = _wm_lon_to_tile(west, n)
    tx_max = _wm_lon_to_tile(east, n)
    ty_min = _wm_lat_to_tile(north, n)
    ty_max = _wm_lat_to_tile(south, n)

    max_t = n - 1
    tx_min = max(0, min(tx_min, max_t))
    tx_max = max(0, min(tx_max, max_t))
    ty_min = max(0, min(ty_min, max_t))
    ty_max = max(0, min(ty_max, max_t))

    img_w = (tx_max - tx_min + 1) * _SAT_TILE_SIZE
    img_h = (ty_max - ty_min + 1) * _SAT_TILE_SIZE
    composite = Image.new("RGB", (img_w, img_h))

    session = requests.Session()
    session.headers["User-Agent"] = "strm2stl/1.0"

    tiles_loaded = 0
    tiles_total = (tx_max - tx_min + 1) * (ty_max - ty_min + 1)
    last_tile_err = None

    for tx in range(tx_min, tx_max + 1):
        for ty in range(ty_min, ty_max + 1):
            url = _SAT_TILE_URL.format(z=zoom, y=ty, x=tx)
            try:
                resp = session.get(url, timeout=8)
                resp.raise_for_status()
                tile = Image.open(BytesIO(resp.content)).convert("RGB")
                composite.paste(
                    tile, ((tx - tx_min) * _SAT_TILE_SIZE, (ty - ty_min) * _SAT_TILE_SIZE))
                tiles_loaded += 1
            except Exception as tile_err:
                last_tile_err = tile_err
                logger.debug(
                    f"Satellite tile {zoom}/{ty}/{tx} failed: {tile_err}")

    if tiles_loaded == 0:
        raise RuntimeError(
            f"All {tiles_total} satellite tiles failed to load. "
            f"Last error: {last_tile_err}. "
            "Check network access to server.arcgisonline.com."
        )
    logger.info(
        f"Satellite tiles: {tiles_loaded}/{tiles_total} loaded at zoom {zoom}")

    def _lon2px(lon):
        return int((lon + 180.0) / 360.0 * n * _SAT_TILE_SIZE) - tx_min * _SAT_TILE_SIZE

    def _lat2py(lat):
        lat_r = math.radians(max(-85.05, min(85.05, lat)))
        return int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n * _SAT_TILE_SIZE) - ty_min * _SAT_TILE_SIZE

    crop = composite.crop((
        max(0, _lon2px(west)),
        max(0, _lat2py(north)),
        min(img_w, _lon2px(east)),
        min(img_h, _lat2py(south)),
    ))

    # De-project from Web Mercator to Plate Carree (equirectangular)
    # The cropped image has non-uniform latitude spacing per pixel row
    # (Mercator stretches high latitudes). Resample to uniform geographic
    # latitude spacing so that downstream map projections work correctly.
    crop = _mercator_to_plate_carree(crop, north, south)

    cw, ch = crop.size
    if cw >= ch:
        out_w, out_h = dim, max(1, round(dim * ch / cw))
    else:
        out_w, out_h = max(1, round(dim * cw / ch)), dim
    crop = crop.resize((out_w, out_h), Image.BILINEAR)

    buf = BytesIO()
    crop.save(buf, format="JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def fetch_water_mask_images(north, south, east, west, sat_scale, water_dataset):
    """Fetch ESA/JRC images and optional elevation for bathymetry. Call via run_in_executor.
    Returns (img, jrc_img_or_None, elevation_raw_or_None) at native sat_scale resolution.

    Raises the underlying exception (e.g. Earth Engine not authenticated)
    rather than swallowing it to a bare None — callers previously had no way
    to distinguish "EE isn't set up" from any other failure, so the route
    always reported a generic "Failed to fetch ESA land cover data" instead
    of the actionable auth message.
    """
    logger.info(f"fetch_water_mask_images: fetching ESA WorldCover layer "
                f"(bbox {abs(east-west):.1f}deg x {abs(north-south):.1f}deg, scale={sat_scale}m/px)")
    try:
        img = fetch_bbox_image(north, south, east, west,
                               scale=sat_scale, dataset="esa", use_cache=True)
    except Exception as e:
        logger.error(f"fetch_water_mask_images: ESA WorldCover layer failed "
                     f"(scale={sat_scale}m/px, bbox {abs(east-west):.1f}deg x {abs(north-south):.1f}deg): {e}")
        msg = str(e)
        if "authorize" in msg.lower() or "authenticate" in msg.lower():
            msg = ("Earth Engine is not authenticated on this server. Run "
                   "'earthengine authenticate' (see the \U0001f511 Keys panel) "
                   "to enable water mask / ESA land cover.")
        raise RuntimeError(msg) from e
    jrc_img = None
    if water_dataset == "jrc":
        logger.info(f"fetch_water_mask_images: fetching JRC Global Surface Water layer "
                    f"(scale={sat_scale}m/px)")
        try:
            jrc_img = fetch_bbox_image(north, south, east, west,
                                       scale=sat_scale, dataset="jrc", use_cache=True)
        except Exception as e:
            logger.warning(
                f"fetch_water_mask_images: JRC layer failed (scale={sat_scale}m/px): {e}")
    elevation_raw = None
    try:
        from geo2stl.tiles import stitch_tiles_no_rasterio as _stitch
        elevation_raw = _stitch((north, south, east, west))
    except Exception:
        pass
    if img is not None and img.ndim == 3:
        img = img[:, :, 0]
    return img, jrc_img, elevation_raw


def fetch_water_mask(
    north: float, south: float, east: float, west: float,
    sat_scale: int, dataset: str
) -> tuple:
    """Fetch and build a binary water mask for a bounding box.

    Auto-scales *sat_scale* upward to satisfy both Earth Engine limits:
      - 50 MB request size limit (~2 bytes/px GeoTIFF -> ~25M pixel budget)
      - 32768px max grid dimension on either axis

    Returns:
        (water_mask, esa_img, sat_scale_used)
        - water_mask:   float32 (H x W), 0 = land, 1 = water
        - esa_img:      uint8 (H x W) ESA WorldCover class values
        - sat_scale_used: int, the (possibly auto-scaled) sat_scale actually used
    """
    import cv2 as _cv2

    bbox_w = abs(east - west)
    bbox_h = abs(north - south)
    mid_lat = (north + south) / 2.0
    m_per_deg_lon = METRES_PER_DEGREE * math.cos(math.radians(mid_lat))
    m_per_deg_lat = METRES_PER_DEGREE

    bbox_w_m = bbox_w * m_per_deg_lon
    bbox_h_m = bbox_h * m_per_deg_lat

    # Constraint 1: 50 MB request size -- GeoTIFF is ~2 bytes/px for uint8.
    # Keep pixel count <= 25M to stay within the 50 331 648-byte EE limit.
    _MAX_ESA_PX = 50_331_648 // 2
    est_px = (bbox_w_m / sat_scale) * (bbox_h_m / sat_scale)
    if est_px > _MAX_ESA_PX:
        sat_scale = max(sat_scale, int(
            math.ceil(math.sqrt(bbox_w_m * bbox_h_m / _MAX_ESA_PX))))
        logger.info(f"fetch_water_mask (ESA/water layer): pixel limit clamp -> sat_scale={sat_scale} "
                    f"(bbox {bbox_w:.1f}deg x {bbox_h:.1f}deg, est {est_px/1e6:.1f}M px)")

    # Constraint 2: 32768px max grid dimension on either axis.
    max_dim_px = 32768
    min_scale_w = math.ceil(bbox_w_m / max_dim_px)
    min_scale_h = math.ceil(bbox_h_m / max_dim_px)
    min_safe_dim = max(int(min_scale_w), int(min_scale_h), 1)
    if sat_scale < min_safe_dim:
        logger.info(f"fetch_water_mask (ESA/water layer): dimension limit clamp -> sat_scale={min_safe_dim} "
                    f"(was {sat_scale}, bbox {bbox_w:.1f}deg x {bbox_h:.1f}deg)")
        sat_scale = min_safe_dim

    img, jrc_img, elevation_raw = fetch_water_mask_images(
        north, south, east, west, sat_scale, dataset)

    if img is None:
        raise RuntimeError("Failed to fetch ESA land cover data")

    h, w = img.shape

    # Build water mask from selected dataset
    if dataset == "jrc" and jrc_img is not None:
        if jrc_img.ndim == 3:
            jrc_img = jrc_img[:, :, 0]
        if jrc_img.shape != (h, w):
            jrc_img = _cv2.resize(jrc_img.astype(np.float32), (w, h),
                                  interpolation=_cv2.INTER_LINEAR)
        water_mask = (jrc_img > 50).astype(np.float32)
    else:
        water_mask = (img == 80).astype(np.float32)

    # SRTM bathymetry augmentation -- only for larger regions (> 30 km diagonal)
    # to avoid misclassifying low-lying coastal land at city scale.
    bbox_diag_km = math.sqrt(
        (bbox_h * m_per_deg_lat) ** 2 + (bbox_w * m_per_deg_lon) ** 2
    ) / 1000.0
    if elevation_raw is not None and elevation_raw.size > 0 and bbox_diag_km > 30:
        elev_r = _cv2.resize(elevation_raw.astype(np.float32), (w, h),
                             interpolation=_cv2.INTER_LINEAR)
        water_mask = np.maximum(water_mask, (elev_r < -2).astype(np.float32))

    return water_mask, img, sat_scale


def fetch_sat_overlay(north, south, east, west, dataset, width_px, height_px, dim):
    """Fetch + resize satellite overlay. Returns (values_list, w, h) or None."""
    import cv2 as _cv2
    import numpy as _np

    target_dim = max(width_px, dim or width_px)
    sat_scale = max(30, calculate_scale_for_dimensions(north, south, east, west, target_dim))
    logger.info(f"fetch_sat_overlay ({dataset}): scale={sat_scale}m/px "
                f"(bbox {abs(east-west):.1f}deg x {abs(north-south):.1f}deg, target_dim={target_dim})")
    sat = fetch_bbox_image(north, south, east, west,
                           scale=sat_scale, dataset=dataset)
    if sat is None:
        return None
    sat_arr = _np.array(sat)
    if sat_arr.size == 0:
        return None
    sat_tw = max(width_px, dim or width_px)
    sat_th = max(height_px, dim or height_px)
    sat_arr = _cv2.resize(sat_arr, (sat_tw, sat_th),
                          interpolation=_cv2.INTER_LINEAR)
    return (sat_arr.ravel().tolist(), sat_arr.shape[1], sat_arr.shape[0])


# ---------------------------------------------------------------------------
# Earth Engine-backed raster helpers (migrated from sat2stl.py)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = _PROJECT_ROOT / "cache" / "ee"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

map_labels = [
    [10, 5],
    [20, 0],
    [30, 0],
    [40, 0],
    [50, 10],
    [60, 0],
    [70, 0],
    [80, -10],
    [90, -5],
]


_EE_STATUS_TTL_S = 300  # re-check at most every 5 minutes
_ee_status_cache: dict = {"ok": None, "error": None, "checked_at": 0.0}


def initialize_earth_engine():
    """Initialize Earth Engine lazily to avoid import-time hard dependency.

    ee.Initialize() does real network I/O (credential check/refresh) even when
    it's about to fail — on a machine with no EE credentials this takes ~3s
    EVERY call. Without memoization, a single "load all layers" action (which
    fires several EE-backed fetches in parallel/succession, e.g. water mask +
    ESA land cover + the combined water/hydrology layer) pays that 3s tax
    repeatedly and stacks up into many seconds of pure waiting before the
    (unavoidable) failure. Cache the outcome for a few minutes so repeated
    calls fail fast; a successful init is cheap to repeat so it isn't cached.
    """
    now = time.time()
    if (_ee_status_cache["ok"] is False
            and now - _ee_status_cache["checked_at"] < _EE_STATUS_TTL_S):
        raise RuntimeError(_ee_status_cache["error"])

    try:
        import ee
        ee.Initialize()
        _ee_status_cache.update(ok=True, error=None, checked_at=now)
    except Exception as e:
        logger.error("Earth Engine initialization failed: %s", e)
        _ee_status_cache.update(ok=False, error=str(e), checked_at=now)
        raise


def reset_earth_engine_status_cache():
    """Clear the memoized EE status so the next call re-checks for real.

    Call this right after a fresh `ee.Authenticate()` completes (see
    app/server/routers/auth.py) — otherwise the negative result cached before
    authentication would keep reporting "not authenticated" for up to
    _EE_STATUS_TTL_S seconds even though credentials now exist.
    """
    _ee_status_cache.update(ok=None, error=None, checked_at=0.0)


def calculate_scale_for_dimensions(N, S, E, W, target_dim=500):
    """Calculate Earth Engine scale (m/px) for target output dimensions."""
    EE_MAX_PIXELS = 32768
    lat_center = (N + S) / 2
    lat_range = abs(N - S)
    lon_range = abs(E - W)

    height_meters = lat_range * 111000
    width_meters = lon_range * 111000 * math.cos(math.radians(lat_center))

    max_meters = max(height_meters, width_meters)
    target_scale = max_meters / target_dim

    min_scale_for_height = height_meters / EE_MAX_PIXELS
    min_scale_for_width = width_meters / EE_MAX_PIXELS
    ee_limit_scale = max(min_scale_for_height, min_scale_for_width)

    scale = max(target_scale, ee_limit_scale)
    scale = max(10, min(10000, scale))
    return int(scale)


def fetch_bbox_image(N, S, E, W, scale=None, dataset="copernicus", use_cache=True, target_dim=None):
    """Fetch Earth Engine raster for bbox and dataset."""
    import requests
    from PIL import Image
    from io import BytesIO

    if scale is None:
        if target_dim is not None:
            scale = calculate_scale_for_dimensions(N, S, E, W, target_dim)
        else:
            scale = calculate_scale_for_dimensions(N, S, E, W, 500)

    ee_max_bytes = 50_331_648
    bytes_per_px = 2
    ee_max_px = ee_max_bytes // bytes_per_px
    lat_center = (N + S) / 2.0
    bbox_w_m = abs(E - W) * 111_320 * math.cos(math.radians(lat_center))
    bbox_h_m = abs(N - S) * 111_320
    est_px = (bbox_w_m / scale) * (bbox_h_m / scale)

    if est_px > ee_max_px:
        min_scale = int(math.ceil(math.sqrt(bbox_w_m * bbox_h_m / ee_max_px)))
        logger.warning(
            "Requested scale=%s exceeds EE limit (~%.1fM px). Clamping to scale=%s.",
            scale,
            est_px / 1e6,
            min_scale,
        )
        scale = min_scale

    if os.environ.get("STRM2STL_TEST_MODE", "0") == "1":
        td = target_dim or 100
        if dataset in ("esa", "jrc"):
            return np.zeros((td, td), dtype=np.uint8)
        return np.zeros((td, td), dtype=np.int16)

    bbox_str = f"{N}_{S}_{E}_{W}_{scale}_{dataset}"
    cache_hash = hashlib.md5(bbox_str.encode()).hexdigest()
    cache_path = CACHE_DIR / f"{cache_hash}.jbl"
    meta_path = CACHE_DIR / f"{cache_hash}.meta"

    if use_cache and cache_path.exists() and meta_path.exists() and joblib is not None:
        try:
            meta = json.loads(meta_path.read_text())
            cached_scale = meta.get("scale", float("inf"))
            if cached_scale <= scale:
                arr = joblib.load(cache_path)
                if isinstance(arr, np.ndarray) and arr.size > 0:
                    return arr
        except Exception as e:
            logger.warning("EE cache read failed, refetching: %s", e)

    initialize_earth_engine()
    import ee

    crs = "EPSG:4326"
    region = ee.Geometry.Rectangle([W, S, E, N], proj=crs, geodesic=False)

    datasets = {
        "esa": ("ESA/WorldCover/v100/2020", "Map"),
        "jrc": ("JRC/GSW1_4/GlobalSurfaceWater", "occurrence"),
        "copernicus": ("COPERNICUS/DEM/GLO30", "DEM"),
        "nasadem": ("NASA/NASADEM_HGT/001", "elevation"),
        "usgs": ("USGS/3DEP/10m", "elevation"),
        "gebco": ("projects/sat-io/open-datasets/gebco/gebco_2023_grid", "elevation"),
    }
    if dataset not in datasets:
        raise ValueError(f"Dataset not recognized. Choose from: {list(datasets.keys())}")

    dataset_id, band = datasets[dataset]
    if dataset == "copernicus":
        img = ee.ImageCollection(dataset_id).mosaic().select(band)
    else:
        img = ee.Image(dataset_id).select(band)

    if dataset in ["esa", "jrc"]:
        image = img.toUint8().clip(region)
    else:
        image = img.toInt16().clip(region)

    url = image.getThumbURL({
        "scale": scale,
        "region": region,
        "format": "GEO_TIFF",
        "crs": crs,
    })

    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        logger.error("Earth Engine request failed: status=%s", response.status_code)
        return None

    img_array = np.array(Image.open(BytesIO(response.content)))

    if use_cache and joblib is not None:
        try:
            joblib.dump(img_array, cache_path)
            meta = {
                "scale": scale,
                "bbox": {"N": N, "S": S, "E": E, "W": W},
                "dataset": dataset,
                "shape": list(img_array.shape),
                "timestamp": time.time(),
            }
            meta_path.write_text(json.dumps(meta))
        except Exception as e:
            logger.warning("Failed to cache EE raster: %s", e)

    return img_array


def get_aquatic_regions(N, S, E, W, dataset="esa", scale=None, use_cache=True, target_dim=500):
    """Get aquatic regions from Earth Engine datasets."""
    img = fetch_bbox_image(N, S, E, W, scale=scale,
                           dataset=dataset, use_cache=use_cache, target_dim=target_dim)
    if img is None:
        return None

    if dataset == "esa":
        if img.ndim == 3:
            img[img[:, :, 1] == 0, 0] = 0
            img = img[:, :, 0]
        img = img.copy()
        img[img == 80] = 0

    return img


def map_label_elevation(img, im, size=500):
    """Map categorical labels to elevation offsets and blend with DEM."""
    from skimage import transform

    if img is None:
        return im * 0

    img_map = img * 0.0
    for x in map_labels:
        img_map[img == x[0]] = x[1]

    img_map2 = img_map * 1.0
    img_map2 = img_map2 + transform.resize(im, img_map2.shape, anti_aliasing=True)
    shape_out = img_map2.shape
    outsize = np.array(shape_out) / max(shape_out) * size
    img_map2 = transform.resize(img_map2, outsize, anti_aliasing=True)
    img_map2 = img_map2.round(0).clip(0.1)
    return img_map2

# Legacy compatibility object for older call sites that still expect
# SAT_LAYER.fetch_* attributes (e.g., notebooks/older adapters).
SAT_LAYER = SimpleNamespace(
    name="satellite",
    fetch_satellite_tiles=fetch_satellite_tiles,
    fetch_water_mask_images=fetch_water_mask_images,
    fetch_water_mask=fetch_water_mask,
    fetch_sat_overlay=fetch_sat_overlay,
    initialize_earth_engine=initialize_earth_engine,
    calculate_scale_for_dimensions=calculate_scale_for_dimensions,
    fetch_bbox_image=fetch_bbox_image,
    get_aquatic_regions=get_aquatic_regions,
)
