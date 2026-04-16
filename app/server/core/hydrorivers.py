"""
core/hydrorivers.py — HydroRIVERS-based high-detail river rasterization.

HydroRIVERS (Lehner et al.) provides ~8.5 M river reaches globally at
15 arc-second resolution (~500 m), with Strahler order and mean discharge.

Download strategy
-----------------
Shapefiles are downloaded on-demand by region and cached permanently under
``cache/hydrorivers/``.  pyogrio's ``bbox`` parameter clips at read time so
only features intersecting the requested bbox are loaded into memory.

The download URLs are the public hydrosheds.org S3 bucket — no login required
for the "standard" resolution shapefiles.

River depression scaling
------------------------
Depression depth is scaled by Strahler order so major rivers get deep cuts
and small streams get shallow ones::

    depression = depression_base * (strahler_order / max_order) ** order_exponent

Default: base=-5 m, exponent=1.5 → order-9 Amazon ~= -5 m, order-1 stream ~= -0.04 m.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HydroRIVERS regional download URLs (public S3, no auth required)
# Standard resolution shapefiles (~500 m); compressed sizes given for info.
# ---------------------------------------------------------------------------

_REGION_URLS: dict[str, str] = {
    "af": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_af_shp.zip",   # Africa
    "ar": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_ar_shp.zip",   # Arctic
    "as": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_as_shp.zip",   # Asia
    "au": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_au_shp.zip",   # Australia
    "eu": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_eu_shp.zip",   # Europe
    "na": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_na_shp.zip",   # North America
    "sa": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_sa_shp.zip",   # South America
    "si": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_si_shp.zip",   # Siberia
}

# Coarse region bounding boxes: (west, south, east, north)
_REGION_BBOX: dict[str, Tuple[float, float, float, float]] = {
    "af": (-20,  -35,  55,  38),
    "ar": (-180,  60, 180,  90),
    "as": (  57,   5, 180,  55),
    "au": ( 112, -48, 180,  -8),
    "eu": (-25,   35,  65,  72),
    "na": (-140,   7, -52,  84),
    "sa": ( -82, -56, -34,  13),
    "si": (  57,  47, 180,  72),
}


def _regions_for_bbox(west: float, south: float, east: float, north: float) -> list[str]:
    """Return HydroRIVERS region codes that intersect the given bbox."""
    needed = []
    for code, (rw, rs, re, rn) in _REGION_BBOX.items():
        if west < re and east > rw and south < rn and north > rs:
            needed.append(code)
    return needed or ["sa"]   # fallback to South America if nothing matches


def _cache_dir() -> Path:
    """Return (and create) the local shapefile cache directory."""
    from app.server.config import _STRM2STL_DIR  # type: ignore
    d = _STRM2STL_DIR / "cache" / "hydrorivers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_region_shapefile(region: str) -> Optional[Path]:
    """
    Download and unzip the HydroRIVERS shapefile for *region* if not cached.

    Returns the path to the .shp file, or None on failure.
    """
    cache = _cache_dir()
    shp_glob = list(cache.glob(f"HydroRIVERS_v10_{region}/*.shp"))
    if shp_glob:
        return shp_glob[0]

    url = _REGION_URLS.get(region)
    if not url:
        logger.error(f"No HydroRIVERS URL for region '{region}'")
        return None

    try:
        import requests
    except ImportError:
        logger.error("requests not installed; cannot download HydroRIVERS")
        return None

    logger.info(f"Downloading HydroRIVERS region '{region}' from {url} …")
    try:
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        raw = resp.content
        logger.info(f"HydroRIVERS '{region}': downloaded {len(raw)/1e6:.1f} MB, extracting …")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            dest = cache / f"HydroRIVERS_v10_{region}"
            dest.mkdir(exist_ok=True)
            z.extractall(dest)
        shp_glob = list(dest.glob("**/*.shp"))
        if not shp_glob:
            logger.error(f"HydroRIVERS '{region}': no .shp in archive")
            return None
        logger.info(f"HydroRIVERS '{region}': ready at {shp_glob[0]}")
        return shp_glob[0]
    except Exception as e:
        logger.error(f"HydroRIVERS download failed for region '{region}': {e}")
        return None


def fetch_hydrorivers(
    north: float, south: float, east: float, west: float,
    min_order: int = 3,
) -> Optional[dict]:
    """
    Fetch HydroRIVERS features intersecting the bbox as a GeoJSON FeatureCollection.

    Args:
        north/south/east/west: bounding box in WGS-84 degrees
        min_order: minimum Strahler order to include (1=all, 3=medium+, 5=major only).
                   Higher values reduce feature count and rasterize faster.

    Returns:
        GeoJSON FeatureCollection with properties ``ORD_STRA`` and ``DIS_AV_CMS``,
        or None on failure.
    """
    try:
        import geopandas as gpd
    except ImportError:
        logger.error("geopandas not installed; cannot read HydroRIVERS")
        return None

    regions = _regions_for_bbox(west, south, east, north)
    logger.info(f"HydroRIVERS bbox ({west:.2f},{south:.2f},{east:.2f},{north:.2f}) "
                f"→ regions: {regions}, min_order={min_order}")

    gdfs = []
    for region in regions:
        shp = _ensure_region_shapefile(region)
        if shp is None:
            continue
        try:
            gdf = gpd.read_file(shp, bbox=(west, south, east, north), engine="pyogrio")
            if len(gdf):
                gdfs.append(gdf)
                logger.info(f"  {region}: {len(gdf)} features before order filter")
        except Exception as e:
            logger.error(f"HydroRIVERS read failed for region '{region}': {e}")

    if not gdfs:
        logger.info("HydroRIVERS: no features found in bbox")
        return None

    import pandas as pd
    combined = pd.concat(gdfs, ignore_index=True)

    # Filter by Strahler order
    if "ORD_STRA" in combined.columns and min_order > 1:
        combined = combined[combined["ORD_STRA"] >= min_order].reset_index(drop=True)

    logger.info(f"HydroRIVERS: {len(combined)} features after order-{min_order}+ filter")

    if len(combined) == 0:
        return None

    import json
    return json.loads(combined.to_json())


def rasterize_hydrorivers(
    geojson: dict,
    north: float, south: float, east: float, west: float,
    dim: int,
    depression_base: float = -5.0,
    order_exponent: float = 1.5,
) -> np.ndarray:
    """
    Rasterize HydroRIVERS GeoJSON to a (dim×dim) float32 depression grid.

    Depression depth is scaled by Strahler order::

        depth = depression_base * (order / 9) ** order_exponent

    So order-9 Amazon = ``depression_base``, order-1 stream ≈ 0.

    Args:
        geojson: FeatureCollection from fetch_hydrorivers()
        depression_base: depth (metres, negative) for the largest rivers
        order_exponent: controls how steeply smaller rivers are cut

    Returns:
        float32 array shape (dim, dim), 0 where no river, negative where river.
    """
    try:
        from shapely.geometry import shape, mapping
        from rasterio.features import rasterize as _rasterize
        from rasterio.transform import from_bounds
    except ImportError:
        logger.error("shapely/rasterio not installed; returning zero grid")
        return np.zeros((dim, dim), dtype=np.float32)

    transform = from_bounds(west, south, east, north, dim, dim)
    pixel_deg = (north - south) / dim
    # Buffer each line to at least 1 pixel width so thin streams are visible
    min_buf_deg = pixel_deg * 0.6

    grid = np.zeros((dim, dim), dtype=np.float32)

    features = geojson.get("features", [])
    max_order = 9  # HydroRIVERS Strahler max

    # Group features by order so we rasterize each order in one pass (faster)
    from collections import defaultdict
    by_order: dict[int, list] = defaultdict(list)
    for feat in features:
        props = feat.get("properties") or {}
        order = int(props.get("ORD_STRA") or 1)
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            s = shape(geom)
            if s.geom_type in ("LineString", "MultiLineString"):
                # Scale buffer with order so major rivers appear wider
                buf = max(min_buf_deg, min_buf_deg * order / 3)
                s = s.buffer(buf)
            if not s.is_empty:
                by_order[order].append(mapping(s))
        except Exception:
            continue

    # Rasterize lowest order (shallowest) first so higher-order rivers overwrite
    for order in sorted(by_order.keys()):
        depth = depression_base * (order / max_order) ** order_exponent
        shapes = [(geom, depth) for geom in by_order[order]]
        if not shapes:
            continue
        try:
            layer = np.zeros((dim, dim), dtype=np.float32)
            _rasterize(shapes, out=layer, transform=transform, dtype="float32")
            # Apply: keep the more-negative value (deeper river wins)
            mask = layer != 0.0
            grid[mask] = np.minimum(grid[mask], layer[mask])
        except Exception as e:
            logger.warning(f"HydroRIVERS rasterize order {order}: {e}")

    n_river = int(np.sum(grid != 0))
    logger.info(f"HydroRIVERS rasterized: {n_river} river pixels at {dim}×{dim}")
    return grid
