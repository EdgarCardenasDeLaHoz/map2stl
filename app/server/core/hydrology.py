"""
core/hydrology.py -- Natural Earth hydrology data fetching and rasterization.

Provides Natural Earth rivers, lakes, and coastlines for multi-scale hydrology rendering.
Includes adaptive buffering to prevent thin-feature aliasing during downsampling.
"""

from __future__ import annotations

import io
import json
import logging
import math
import zipfile
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def fetch_natural_earth_rivers(scale_m: int = 10) -> Optional[Dict]:
    """
    Fetch Natural Earth rivers dataset as GeoJSON.

    Args:
        scale_m: 10, 50, or 110 (1:10M, 1:50M, 1:110M)

    Returns:
        Dict with 'type'='FeatureCollection' and 'features' list, or None if failed
    """
    try:
        import geopandas as gpd
        import requests
    except ImportError:
        logger.warning(
            "geopandas or requests not installed for hydrology fetch")
        return None

    url = f"https://naciscdn.org/naturalearth/{scale_m}m/physical/ne_{scale_m}m_rivers_lake_centerlines.zip"

    try:
        logger.info(f"Fetching Natural Earth {scale_m}m rivers from {url}")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            shp_files = [f for f in z.namelist() if f.endswith('.shp')]
            if not shp_files:
                logger.warning("No .shp file in Natural Earth archive")
                return None

            import tempfile
            import os
            with tempfile.TemporaryDirectory() as tmpdir:
                z.extractall(tmpdir)
                shp_path = os.path.join(tmpdir, shp_files[0])
                gdf = gpd.read_file(shp_path)

                # Convert to GeoJSON
                geojson = json.loads(gdf.to_json())
                logger.info(
                    f"Fetched {len(gdf)} river features from Natural Earth")
                return geojson

    except Exception as e:
        logger.error(f"Natural Earth rivers fetch failed: {e}")
        return None


def filter_rivers_by_bbox(geojson: Dict, bbox: Tuple[float, float, float, float]) -> Dict:
    """
    Filter GeoJSON features to a bounding box.

    Args:
        geojson: GeoJSON FeatureCollection
        bbox: (west, south, east, north)

    Returns:
        Filtered GeoJSON FeatureCollection
    """
    west, south, east, north = bbox

    filtered_features = []
    for feature in geojson.get('features', []):
        coords = feature.get('geometry', {}).get('coordinates')
        if not coords:
            continue

        # Simple bounds check for LineString coordinates
        try:
            if feature['geometry']['type'] == 'LineString':
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                if (max(lons) >= west and min(lons) <= east and
                        max(lats) >= south and min(lats) <= north):
                    filtered_features.append(feature)
            elif feature['geometry']['type'] == 'MultiLineString':
                for line_coords in coords:
                    lons = [c[0] for c in line_coords]
                    lats = [c[1] for c in line_coords]
                    if (max(lons) >= west and min(lons) <= east and
                            max(lats) >= south and min(lats) <= north):
                        filtered_features.append(feature)
                        break
        except (KeyError, TypeError):
            continue

    return {
        'type': 'FeatureCollection',
        'features': filtered_features
    }


def rasterize_rivers_with_buffering(
    geojson: Dict,
    bbox: Tuple[float, float, float, float],
    dim: int,
    depression_m: float = -3.0,
) -> np.ndarray:
    """
    Rasterize river geometries with adaptive buffering to prevent aliasing.

    Args:
        geojson: GeoJSON FeatureCollection with LineString/MultiLineString features
        bbox: (west, south, east, north)
        dim: Output grid resolution (pixels per side)
        depression_m: Elevation depression for rivers (negative = downward)

    Returns:
        Float32 array of shape (dim, dim) with river elevation values
    """
    try:
        from shapely.geometry import shape, mapping
        from rasterio.features import rasterize as rio_rasterize
        from rasterio.transform import from_bounds
        from rasterio.enums import MergeAlg
    except ImportError:
        logger.warning(
            "shapely or rasterio not installed for hydrology rasterization")
        return np.zeros((dim, dim), dtype=np.float32)

    west, south, east, north = bbox

    # Calculate pixel size in metres (approximate)
    mid_lat = (north + south) / 2.0
    pixel_size_lon_m = (east - west) * 111_320.0 / dim
    pixel_size_lat_m = (north - south) * 111_320.0 / dim
    pixel_size_m = (pixel_size_lon_m + pixel_size_lat_m) / 2.0

    # Rivers must be at least 2 pixels wide to avoid aliasing
    min_buffer_m = pixel_size_m * 2

    logger.info(
        f"Rasterizing rivers: pixel size {pixel_size_m:.0f} m, min buffer {min_buffer_m:.0f} m")

    # Convert to degrees for buffering (approximate; best approach is UTM reprojection)
    min_buffer_deg = min_buffer_m / 111_320.0

    shapes = []
    for feature in geojson.get('features', []):
        try:
            geom = shape(feature['geometry'])

            if geom.geom_type in ('LineString', 'MultiLineString'):
                # Buffer to ensure minimum width
                buffered = geom.buffer(min_buffer_deg)
                shapes.append((mapping(buffered), depression_m))
        except Exception as e:
            logger.debug(f"Skipping river feature: {e}")
            continue

    if not shapes:
        logger.warning("No river features to rasterize")
        return np.zeros((dim, dim), dtype=np.float32)

    # Rasterize
    transform = from_bounds(west, south, east, north, dim, dim)
    try:
        # Try rasterizing with merge_alg parameter (newer rasterio versions)
        try:
            river_grid = rio_rasterize(
                shapes,
                out_shape=(dim, dim),
                transform=transform,
                fill=0.0,
                dtype=np.float32,
                merge_alg=MergeAlg.min
            )
        except (AttributeError, TypeError):
            # Fallback for older rasterio or if MergeAlg not available
            river_grid = rio_rasterize(
                shapes,
                out_shape=(dim, dim),
                transform=transform,
                fill=0.0,
                dtype=np.float32
            )

        logger.info(
            f"Rasterized {len(shapes)} river features to {dim}x{dim} grid")
        return river_grid
    except Exception as e:
        logger.error(f"Rasterization failed: {e}")
        return np.zeros((dim, dim), dtype=np.float32)


def merge_rivers_with_dem(dem: np.ndarray, rivers: np.ndarray) -> np.ndarray:
    """
    Merge river depression grid with DEM using minimum operation.

    Rivers are merged as depressions (lower elevations win).

    Args:
        dem: DEM elevation grid (float32)
        rivers: River elevation grid with depressions (float32)

    Returns:
        Merged grid (float32)
    """
    # Only apply rivers where elevation != 0
    rivers_mask = rivers != 0.0
    merged = dem.copy()
    merged[rivers_mask] = np.minimum(dem[rivers_mask], rivers[rivers_mask])
    return merged
