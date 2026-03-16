from skimage import transform
import joblib  # Recommended for numpy arrays: pip install joblib
import hashlib
import json
import time
import os
import ee
import requests
import numpy as np
from PIL import Image
from io import BytesIO
import logging

"""
10: Tree cover
20: Shrubland
30: Grassland
40: Cropland
50: Built-up
60: Bare / sparse vegetation
70: Snow and Ice
80: Permanent water bodies
90: Herbaceous Wetland
95: Mangrove
100: Moss and lichen 
"""

map_labels = [[10, 5],
              [20, 0],
              [30, 0],
              [40, 0],
              [50, 10],
              [60, 0],
              [70, 0],
              [80, -10],
              [90, -5]]


def initialize_earth_engine():
    """Initialize Earth Engine."""
    try:
        ee.Initialize()
    except Exception as e:
        print(f"Earth Engine initialization failed: {e}")
        print("Run: earthengine set_project YOUR_PROJECT_ID")
        raise


def get_aquatic_regions(N, S, E, W, dataset="esa", scale=None, use_cache=True, target_dim=500):
    """
    Get aquatic regions from Earth Engine.

    Args:
        N, S, E, W: Bounding box coordinates
        dataset: 'esa' or 'jrc'
        scale: Meters per pixel. If None, auto-calculated from target_dim.
        use_cache: Whether to use disk cache
        target_dim: Target dimension for auto-scale calculation
    """
    initialize_earth_engine()
    img = fetch_bbox_image(N, S, E, W, scale=scale,
                           dataset=dataset, use_cache=use_cache, target_dim=target_dim)
    print("...")

    if img is None:
        return None

    if dataset == "esa":
        # Water class is 80, no data (0) often also water
        # Handle both 2D and 3D arrays
        if img.ndim == 3:
            img[img[:, :, 1] == 0, 0] = 0
            img = img[:, :, 0]
        # Set water pixels to 0
        img = img.copy()
        img[img == 80] = 0

    elif dataset == "jrc":
        # Water class is >0
        pass

    return img


# Create a cache directory - unified location relative to project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(_PROJECT_ROOT, "cache", "ee")
os.makedirs(CACHE_DIR, exist_ok=True)


def calculate_scale_for_dimensions(N, S, E, W, target_dim=500):
    """
    Calculate the appropriate Earth Engine scale (meters/pixel) to achieve
    approximately target_dim pixels in the largest dimension.

    Also ensures we stay within Earth Engine's hard limit of 32768 pixels per dimension.

    Args:
        N, S, E, W: Bounding box coordinates
        target_dim: Desired maximum dimension in pixels

    Returns:
        scale in meters per pixel
    """
    import math

    # Earth Engine hard limit
    EE_MAX_PIXELS = 32768

    # Calculate approximate dimensions of the bounding box in meters
    lat_center = (N + S) / 2
    lat_range = abs(N - S)
    lon_range = abs(E - W)

    # Convert degrees to meters
    # Latitude: 1 degree ≈ 111 km
    height_meters = lat_range * 111000

    # Longitude: 1 degree ≈ 111 km * cos(latitude)
    width_meters = lon_range * 111000 * math.cos(math.radians(lat_center))

    # Calculate scale for target dimension
    max_meters = max(height_meters, width_meters)
    target_scale = max_meters / target_dim

    # Calculate minimum scale to stay within EE pixel limit
    # Both dimensions must be <= 32768
    min_scale_for_height = height_meters / EE_MAX_PIXELS
    min_scale_for_width = width_meters / EE_MAX_PIXELS
    ee_limit_scale = max(min_scale_for_height, min_scale_for_width)

    # Use the larger scale (coarser resolution) to stay within limits
    scale = max(target_scale, ee_limit_scale)

    # Clamp to reasonable values (10m to 10000m per pixel)
    scale = max(10, min(10000, scale))

    return int(scale)


def fetch_bbox_image(N, S, E, W, scale=None, dataset="copernicus", use_cache=True, target_dim=None):
    """
    Fetch satellite/elevation data for a bounding box from Earth Engine.

    Args:
        N, S, E, W: Bounding box coordinates
        scale: Meters per pixel. If None, auto-calculated from target_dim.
        dataset: One of 'esa', 'jrc', 'copernicus', 'nasadem', 'usgs', 'gebco'
        use_cache: Whether to use disk cache
        target_dim: If provided and scale is None, calculate scale to achieve
                   approximately this many pixels in the largest dimension.

    Returns:
        numpy array of image data, or None on failure
    """
    import logging
    logger = logging.getLogger("fetch_bbox_image")

    logger.info(
        f"Fetching data for bbox: N={N}, S={S}, E={E}, W={W}, scale={scale}, dataset={dataset}")

    # Auto-calculate scale if not provided
    if scale is None:
        if target_dim is not None:
            scale = calculate_scale_for_dimensions(N, S, E, W, target_dim)
        else:
            # Default to 500 pixel target
            scale = calculate_scale_for_dimensions(N, S, E, W, 500)
    logger.debug(f"Calculated scale: {scale}")

    # Test-mode: allow deterministic, network-free responses for tests
    if os.environ.get("STRM2STL_TEST_MODE", "0") == "1":
        td = target_dim or 100
        if dataset in ("esa", "jrc"):
            logger.debug("Returning test-mode categorical data.")
            return np.zeros((td, td), dtype=np.uint8)
        logger.debug("Returning test-mode elevation data.")
        return np.zeros((td, td), dtype=np.int16)

    bbox_str = f"{N}_{S}_{E}_{W}_{scale}_{dataset}"
    cache_hash = hashlib.md5(bbox_str.encode()).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{cache_hash}.jbl")
    meta_path = os.path.join(CACHE_DIR, f"{cache_hash}.meta")

    logger.debug(f"Cache paths: data={cache_path}, meta={meta_path}")

    if use_cache and os.path.exists(cache_path) and os.path.exists(meta_path):
        try:
            with open(meta_path, 'r') as f:
                meta = json.load(f)
            cached_scale = meta.get('scale', float('inf'))
            if cached_scale <= scale:
                logger.info(
                    f"Loading data from cache (scale={cached_scale}, requested={scale})")
                try:
                    arr = joblib.load(cache_path)
                    if not isinstance(arr, np.ndarray) or arr.size == 0:
                        raise ValueError("Cache file is invalid or empty")
                    return arr
                except Exception as cache_load_err:
                    logger.error(
                        f"Cache load error: {cache_load_err}, deleting cache and refetching...")
                    try:
                        if os.path.exists(cache_path):
                            os.remove(cache_path)
                        if os.path.exists(meta_path):
                            os.remove(meta_path)
                    except Exception as e:
                        logger.warning(f"Failed to delete cache files: {e}")
                    raise cache_load_err
            else:
                logger.info(
                    f"Cache outdated (cached scale={cached_scale}, need {scale}), fetching higher resolution.")
        except Exception as e:
            logger.error(f"Cache meta read error: {e}, refetching...")

    initialize_earth_engine()

    crs = "EPSG:4326"
    region = ee.Geometry.Rectangle([W, S, E, N], proj=crs, geodesic=False)

    # Dataset Registry
    datasets = {
        "esa": ("ESA/WorldCover/v100/2020", "Map"),
        "jrc": ("JRC/GSW1_4/GlobalSurfaceWater", "occurrence"),
        "copernicus": ("COPERNICUS/DEM/GLO30", "DEM"),
        "nasadem": ("NASA/NASADEM_HGT/001", "elevation"),
        "usgs": ("USGS/3DEP/10m", "elevation"),
        "gebco": ("projects/sat-io/open-datasets/gebco/gebco_2023_grid", "elevation")
    }

    if dataset not in datasets:
        logger.error(f"Dataset not recognized: {dataset}")
        raise ValueError(
            f"Dataset not recognized. Choose from: {list(datasets.keys())}")

    dataset_id, band = datasets[dataset]
    logger.debug(f"Using dataset: {dataset_id}, band: {band}")

    try:
        if dataset == "copernicus":
            # We mosaic the collection into one image
            img = ee.ImageCollection(dataset_id).mosaic().select(band)
        elif dataset == "jrc":
            # JRC GlobalSurfaceWater is an ImageCollection - mosaic it
            img = ee.Image(dataset_id).select(band)
        else:
            # Standard images (ESA, NASADEM)
            img = ee.Image(dataset_id).select(band)

        # Use appropriate data type based on dataset
        if dataset in ['esa', 'jrc']:
            image = img.toUint8().clip(region)  # These are categorical/percentage
        else:
            image = img.toInt16().clip(region)  # These are elevation data
        # 3. Fetch from Earth Engine

        url = image.getThumbURL({
            "scale": scale,
            "region": region,
            "format": "GEO_TIFF",
            "crs": crs
        })
        logger.info(f"Generated Earth Engine URL: {url}")

        response = requests.get(url)
        if response.status_code != 200:
            logger.error(
                f"Earth Engine request failed: status={response.status_code}")
            return None

        img_array = np.array(Image.open(BytesIO(response.content)))
        logger.info(f"Fetched image data with shape: {img_array.shape}")

        if use_cache:
            logger.info(f"Caching data at: {cache_path}")
            try:
                joblib.dump(img_array, cache_path)
                meta = {
                    'scale': scale,
                    'bbox': {'N': N, 'S': S, 'E': E, 'W': W},
                    'dataset': dataset,
                    'shape': list(img_array.shape),
                    'timestamp': time.time()
                }
                with open(meta_path, 'w') as f:
                    json.dump(meta, f)
            except Exception as e:
                logger.error(f"Failed to cache data: {e}")

        return img_array

    except requests.exceptions.RequestException as e:
        logger.error(f"Earth Engine request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching tile: {e}")
        return None


def map_label_elevation(img, im, size=500):

    if img is None:
        return im*0

    img_map = img*0.0
    for x in map_labels:
        img_map[img == x[0]] = x[1]

    img_map2 = img_map * 1.0
    img_map2 = img_map2 + \
        transform.resize(im, img_map2.shape, anti_aliasing=True)
    # scale shape to make maximum dims 1000
    shape_out = img_map2.shape
    outsize = np.array(shape_out)/max(shape_out)*size
    img_map2 = transform.resize(img_map2, outsize, anti_aliasing=True)
    img_map2 = img_map2.round(0).clip(0.1)

    return img_map2
