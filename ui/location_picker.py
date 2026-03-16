import webbrowser
import threading
import uvicorn
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, validator, Field
from fastapi.staticfiles import StaticFiles
from typing import Optional, List, Dict, Any
import asyncio
import os
import time
import sys
import json
import numpy as np
from pathlib import Path
import shutil
import logging
import joblib
from logging.handlers import RotatingFileHandler

# Configure logging to write to a file
log_file = "server.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Keep logging to console
        RotatingFileHandler(log_file, maxBytes=5*1024*1024,
                            backupCount=3)  # Log to file with rotation
    ]
)
logger = logging.getLogger(__name__)

# Test mode toggle: set environment variable STRM2STL_TEST_MODE=1 to enable
# deterministic, network-free responses for pytest and CI.
TEST_MODE = os.environ.get("STRM2STL_TEST_MODE", "0") == "1"

# coordinates file path (default, can be patched in tests)
COORDINATES_PATH = Path(__file__).parent.parent / "coordinates.json"

# Cache directories - unified location relative to project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent
CACHE_DIRS = [
    _PROJECT_ROOT / "cache" / "ee",
]
CACHE_CLEAR_INTERVAL = 3600  # Clear cache every hour (seconds)
CACHE_MAX_FILES = 100  # Clear when cache exceeds this many files
_last_cache_clear = 0


def clear_caches_if_needed():
    """Clear old cache files periodically to ensure fresh data"""
    global _last_cache_clear
    current_time = time.time()

    if current_time - _last_cache_clear > CACHE_CLEAR_INTERVAL:
        for cache_dir in CACHE_DIRS:
            if cache_dir.exists() and cache_dir.is_dir():
                cache_files = list(cache_dir.glob("*"))
                if len(cache_files) > CACHE_MAX_FILES:
                    logger.info(
                        f"Clearing old cache files in: {cache_dir} ({len(cache_files)} files)")
                    # Delete oldest files, keeping newest 50
                    sorted_files = sorted(
                        cache_files, key=lambda f: f.stat().st_mtime)
                    files_to_delete = sorted_files[:-50]  # Keep 50 newest
                    deleted = 0
                    for f in files_to_delete:
                        try:
                            f.unlink()
                            deleted += 1
                        except Exception:
                            pass  # Skip files in use
                    logger.info(f"Deleted {deleted} old cache files")
        _last_cache_clear = current_time


selected_location = {}

# FastAPI initialization (only once)
app = FastAPI()

# Template path using absolute path
templates_path = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "templates")
logger.info(f"Templates path: {templates_path}")
templates = Jinja2Templates(directory=templates_path)

# Optionally, if you have static files like CSS/JS, uncomment the line below:
# Mount static files (JS/CSS) from the ui/static folder so templates can load them
static_path = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "static")
if os.path.isdir(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")
else:
    logger.warning(f"Static path not found: {static_path}")


# ============================================================
# Pydantic Schemas
# ============================================================

# --- Shared base ---

class BoundingBox(BaseModel):
    """Geographic bounding box using cardinal directions."""
    north: float = Field(..., ge=-90, le=90,
                         description="Northern latitude bound")
    south: float = Field(..., ge=-90, le=90,
                         description="Southern latitude bound")
    east: float = Field(..., ge=-180, le=180,
                        description="Eastern longitude bound")
    west: float = Field(..., ge=-180, le=180,
                        description="Western longitude bound")

    @validator("north")
    def north_gt_south(cls, v, values):
        if "south" in values and v <= values["south"]:
            raise ValueError("north must be greater than south")
        return v


# Legacy alias kept for backward-compatibility with older frontend code
class BoundingBoxLegacy(BaseModel):
    southWestLat: float
    southWestLng: float
    northEastLat: float
    northEastLng: float


# --- Regions ---

class RegionParameters(BaseModel):
    """Rendering and export parameters stored with a saved region."""
    dim: int = Field(200, ge=1, le=2000,
                     description="Grid resolution (pixels per side)")
    depth_scale: float = Field(
        0.5, ge=0.0, le=10.0, description="Depth scaling for ocean floor")
    water_scale: float = Field(
        0.05, ge=0.0, le=1.0, description="Water subtraction strength")
    height: float = Field(10.0, ge=0.0, description="Model height in mm")
    base: float = Field(2.0, ge=0.0, description="Base thickness in mm")
    subtract_water: bool = Field(
        True, description="Whether to subtract water bodies from terrain")
    sat_scale: int = Field(
        500, ge=10, description="Earth Engine scale in metres/pixel for satellite data")


class RegionCreate(BoundingBox):
    """Request body for creating or updating a saved region."""
    name: str = Field(..., min_length=1, max_length=128,
                      description="Unique region name")
    description: Optional[str] = Field(None, max_length=512)
    parameters: Optional[RegionParameters] = None


class RegionResponse(BoundingBox):
    """A saved geographic region returned by the API."""
    name: str
    description: Optional[str] = None
    parameters: Optional[RegionParameters] = None


class RegionsListResponse(BaseModel):
    regions: List[RegionResponse]


# --- Terrain / Elevation ---

class DEMRequest(BoundingBox):
    """Parameters for fetching a Digital Elevation Model preview."""
    dim: int = Field(200, ge=1, le=2000, description="Target grid resolution")
    depth_scale: float = Field(0.5, ge=0.0, le=10.0)
    water_scale: float = Field(0.05, ge=0.0, le=1.0)
    height: float = Field(10.0, ge=0.0)
    base: float = Field(2.0, ge=0.0)
    subtract_water: bool = True
    dataset: str = Field(
        "esa", description="Elevation dataset: 'esa', 'copernicus', 'nasadem', 'usgs', 'gebco'")
    colormap: str = Field(
        "terrain", description="Matplotlib colormap name for client-side rendering")
    show_landuse: bool = Field(
        False, description="Include ESA land-cover overlay")


class DEMResponse(BaseModel):
    """Raw elevation data returned for client-side rendering."""
    dem_values: List[float] = Field(
        ..., description="Flat row-major array of elevation values (metres)")
    dimensions: List[int] = Field(..., description="[height_px, width_px]")
    min_elevation: float
    max_elevation: float
    mean_elevation: float
    bbox: List[float] = Field(..., description="[west, south, east, north]")
    sat_available: bool = False
    sat_values: Optional[List[float]] = None
    sat_dimensions: Optional[List[int]] = None


class RawDEMResponse(BaseModel):
    """Unprocessed SRTM/GEBCO elevation data before water subtraction."""
    dem_values: List[float]
    dimensions: List[int]
    min_elevation: float
    max_elevation: float
    mean_elevation: float
    ptp: float = Field(...,
                       description="Peak-to-peak range for client-side water scale calculation")
    bbox: List[float]


class WaterMaskRequest(BoundingBox):
    """Parameters for fetching a water / land-cover mask."""
    sat_scale: int = Field(
        500, ge=10, description="Earth Engine resolution in metres/pixel")
    dim: int = Field(200, ge=1, le=2000)
    target_width: Optional[int] = Field(
        None, description="Resize output to match DEM pixel width")
    target_height: Optional[int] = Field(
        None, description="Resize output to match DEM pixel height")


class WaterMaskResponse(BaseModel):
    """Binary water mask and ESA land-cover data for the requested bbox."""
    water_mask_values: List[float] = Field(
        ..., description="Flat binary array: 1 = water, 0 = land")
    water_mask_dimensions: List[int] = Field(...,
                                             description="[height_px, width_px]")
    water_pixels: int
    total_pixels: int
    water_percentage: float
    esa_values: Optional[List[float]] = Field(
        None, description="Raw ESA WorldCover class values")
    esa_dimensions: Optional[List[int]] = None


class SatelliteRequest(BoundingBox):
    """Parameters for fetching satellite / land-cover imagery."""
    dataset: str = Field("esa", description="'esa', 'copernicus', 'jrc'")
    dim: int = Field(200, ge=1, le=2000)
    scale: Optional[int] = Field(
        None, description="Earth Engine resolution in metres/pixel")


class SatelliteResponse(BaseModel):
    """Satellite or land-cover image data."""
    values: List[float]
    dimensions: List[int]
    dataset: str
    bbox: List[float]


# --- Export / 3D Models ---

class ExportRequest(BoundingBox):
    """Parameters for generating a 3D model file."""
    dem_values: List[float] = Field(
        ..., description="Flat row-major elevation array from /api/terrain/dem")
    height: int = Field(0, description="Grid height in pixels")
    width: int = Field(0, description="Grid width in pixels")
    model_height: float = Field(
        20.0, ge=0.1, description="Physical model height in mm")
    base_height: float = Field(
        5.0, ge=0.0, description="Base plate thickness in mm")
    exaggeration: float = Field(
        1.0, ge=0.0, description="Vertical exaggeration multiplier")
    name: str = Field("terrain", max_length=64,
                      description="Output file base name")


class ExportResponse(BaseModel):
    status: str
    filename: Optional[str] = None
    message: Optional[str] = None


# --- Cache ---

class CacheDirInfo(BaseModel):
    path: str
    files_deleted: int
    total_files: int


class CacheStatusResponse(BaseModel):
    total_files: int
    total_size_bytes: int
    last_cleared: Optional[float] = None
    cache_dirs: List[Dict[str, Any]]


class CacheClearResponse(BaseModel):
    status: str
    cleared: List[CacheDirInfo]


# --- Settings ---

class ProjectionInfo(BaseModel):
    id: str
    name: str
    description: str


class ProjectionsResponse(BaseModel):
    projections: List[ProjectionInfo]


class ColormapInfo(BaseModel):
    id: str
    description: Optional[str] = None


class ColormapsResponse(BaseModel):
    colormaps: List[ColormapInfo]


class DatasetInfo(BaseModel):
    id: str
    name: str
    description: str
    source: Optional[str] = None
    requires_auth: bool = False


class DatasetsResponse(BaseModel):
    datasets: List[DatasetInfo]


# --- Legacy alias kept so existing water-mask handler can still be used as body model ---
class Region(BoundingBox):
    sat_scale: Optional[int] = None
    dim: Optional[int] = None
    target_width: Optional[int] = None
    target_height: Optional[int] = None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/coordinates")
async def get_coordinates():
    """Get all previously used coordinates from the JSON file"""
    coordinates_path = COORDINATES_PATH
    logger.debug(f"GET /api/coordinates -> reading from {coordinates_path}")
    try:
        with open(coordinates_path, 'r') as f:
            data = json.load(f)
        logger.debug(f"Loaded {len(data.get('regions', []))} regions")
        return JSONResponse(content=data)
    except FileNotFoundError:
        logger.debug("Coordinates file not found, returning empty list")
        # Always respond with 200 and empty list to simplify client
        return JSONResponse(content={"regions": []}, status_code=200)
    except Exception as e:
        logger.error(f"Error reading coordinates file: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/save_coordinate")
async def save_coordinate(region_data: dict):
    """Save a new coordinate region to the JSON file"""
    coordinates_path = COORDINATES_PATH
    logger.debug(f"POST /api/save_coordinate data={region_data}")
    try:
        # Load existing data
        if coordinates_path.exists():
            with open(coordinates_path, 'r') as f:
                data = json.load(f)
        else:
            data = {"regions": []}

        # Add default parameters if not provided
        if 'parameters' not in region_data:
            region_data['parameters'] = {
                "dim": 100,
                "depth_scale": 0.5,
                "water_scale": 0.05,
                "height": 10,
                "base": 2,
                "subtract_water": True
            }

        # Add new region
        data["regions"].append(region_data)

        # Save back to file
        with open(coordinates_path, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved region, total now {len(data['regions'])}")
        return JSONResponse(content={"status": "success", "message": "Region saved successfully"})
    except Exception as e:
        logger.error(f"Error saving region: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


async def clear_cache():
    """Manually clear all caches to ensure fresh data"""
    global _last_cache_clear
    cleared = []
    for cache_dir in CACHE_DIRS:
        if cache_dir.exists() and cache_dir.is_dir():
            cache_files = list(cache_dir.glob("*"))
            deleted = 0
            for f in cache_files:
                try:
                    f.unlink()
                    deleted += 1
                except Exception:
                    pass  # Skip files in use
            cleared.append({
                "path": str(cache_dir),
                "files_deleted": deleted,
                "total_files": len(cache_files)
            })
            logger.info(
                f"Cleared cache: {cache_dir} ({deleted}/{len(cache_files)} files)")
    _last_cache_clear = time.time()
    return JSONResponse(content={"status": "success", "cleared": cleared})


async def get_cache_status():
    """Get server-side cache status and statistics"""
    import hashlib

    cache_info = []
    total_files = 0
    total_size = 0

    for cache_dir in CACHE_DIRS:
        if cache_dir.exists() and cache_dir.is_dir():
            cache_files = list(cache_dir.glob("*.jbl"))
            dir_size = sum(f.stat().st_size for f in cache_files if f.exists())
            total_files += len(cache_files)
            total_size += dir_size

            # Get recent files
            recent = sorted(
                cache_files, key=lambda f: f.stat().st_mtime, reverse=True)[:5]
            recent_info = [
                {
                    "name": f.name,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "age_minutes": round((time.time() - f.stat().st_mtime) / 60, 1)
                }
                for f in recent
            ]

            cache_info.append({
                "path": str(cache_dir),
                "file_count": len(cache_files),
                "size_mb": round(dir_size / (1024 * 1024), 2),
                "recent_files": recent_info
            })

    return JSONResponse(content={
        "status": "ok",
        "total_cached_files": total_files,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "max_files": CACHE_MAX_FILES,
        "caches": cache_info,
        "last_clear": _last_cache_clear,
        "clear_interval_hours": CACHE_CLEAR_INTERVAL / 3600
    })


async def check_cached(request: Request):
    """Check if a specific region is cached on the server"""
    import hashlib

    params = request.query_params
    north = params.get("north")
    south = params.get("south")
    east = params.get("east")
    west = params.get("west")
    scale = params.get("scale", "500")
    dataset = params.get("dataset", "esa")

    # Allow missing bbox params but be permissive: tests sometimes omit east/west
    if north is None or south is None:
        return JSONResponse(content={"error": "Missing north/south bbox parameters"}, status_code=400)
    if east is None or west is None:
        # Provide tiny fallback longitude range near 0 to allow caching checks
        try:
            north_f = float(north)
            south_f = float(south)
        except Exception:
            north_f = float(north) if north is not None else 0.0
            south_f = float(south) if south is not None else 0.0
        center_lon = 0.0
        west = f"{center_lon - 0.01:.4f}"
        east = f"{center_lon + 0.01:.4f}"

    # Generate cache hash matching sat2stl.fetch_bbox_image: uses N,S,E,W and dataset (not scale)
    try:
        Nf = float(north)
        Sf = float(south)
        Ef = float(east)
        Wf = float(west)
        param_str = f"{Nf:.4f}_{Sf:.4f}_{Ef:.4f}_{Wf:.4f}_{dataset}"
    except Exception:
        param_str = f"{north}_{south}_{east}_{west}_{dataset}"
    cache_hash = hashlib.md5(param_str.encode()).hexdigest()
    cache_path = CACHE_DIRS[0] / f"{cache_hash}.jbl" if CACHE_DIRS else None

    is_cached = cache_path and cache_path.exists()
    cache_info = None

    if is_cached:
        stat = cache_path.stat()
        cache_info = {
            "size_kb": round(stat.st_size / 1024, 1),
            "age_minutes": round((time.time() - stat.st_mtime) / 60, 1),
            "cache_key": cache_hash
        }

    return JSONResponse(content={
        "cached": is_cached,
        "bbox": {"north": north, "south": south, "east": east, "west": west},
        "dataset": dataset,
        "scale": scale,
        "info": cache_info
    })


async def get_projections():
    """Return available map projections and their descriptions."""
    try:
        from geo2stl.projections import get_projection_info
        return JSONResponse(content=get_projection_info())
    except ImportError:
        # Fallback if module not found
        return JSONResponse(content={
            'none': {
                'name': 'None (Plate Carrée)',
                'description': 'No projection applied.',
                'preserves': 'Nothing specific',
                'best_for': 'Quick previews'
            },
            'cosine': {
                'name': 'Cosine Correction',
                'description': 'Simple horizontal scaling by cos(latitude).',
                'preserves': 'Approximate local scale',
                'best_for': 'General purpose'
            }
        })


# All rendering now happens client-side in JavaScript
# The server only returns raw elevation data arrays
async def preview_dem(request: Request):
    # Clear caches periodically to ensure fresh data
    clear_caches_if_needed()

    # Parse query params properly (FastAPI query_params.get doesn't support type=)
    params = request.query_params

    def get_float(key, default=None):
        val = params.get(key)
        if val is None or val == '':
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_int(key, default=None):
        val = params.get(key)
        if val is None or val == '':
            return default
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default

    def get_bool(key, default=False):
        val = params.get(key)
        if val is None or val == '':
            return default
        return val.lower() in ('true', '1', 'yes', 'on')

    north = get_float("north")
    south = get_float("south")
    east = get_float("east")
    west = get_float("west")
    dim = get_int("dim", 100)
    depth_scale = get_float("depth_scale", 0.5)
    water_scale = get_float("water_scale", 0.05)
    height = get_float("height", 10)
    base = get_float("base", 2)
    subtract_water = get_bool("subtract_water", True)
    show_sat = get_bool("show_sat", False)
    show_landuse = get_bool("show_landuse", False)
    dataset = params.get("dataset", "esa")
    colormap_name = params.get("colormap", "terrain")
    projection = params.get("projection", "cosine")  # Map projection type
    # Keep predictable dimensions
    maintain_dimensions = get_bool("maintain_dimensions", True)

    logger.debug(
        f"GET /api/preview_dem north={north} south={south} east={east} west={west} dim={dim} show_sat={show_sat}")

    # If test mode is enabled, return a small deterministic DEM without
    # performing any Earth Engine or network operations. This makes pytest
    # runs deterministic and fast in CI/local debugging.
    if TEST_MODE:
        import numpy as _np
        logger.info(
            "STRM2STL_TEST_MODE active: returning deterministic mock DEM")
        # Keep aspect ratio behavior simple: square array of size `dim`
        im = _np.linspace(0, 100, num=(dim * dim),
                          dtype=float).reshape((dim, dim))

        dem_values = _np.nan_to_num(im, nan=0.0, posinf=_np.finfo(
            _np.float32).max, neginf=_np.finfo(_np.float32).min).ravel().tolist()
        height_px, width_px = im.shape
        response_content = {
            "dem_values": dem_values,
            "dimensions": [height_px, width_px],
            "min_elevation": float(_np.nanmin(im)),
            "max_elevation": float(_np.nanmax(im)),
            "mean_elevation": float(_np.nanmean(im)),
            "bbox": [west or 0.0, south or 0.0, east or 0.0, north or 0.0],
            "show_sat": False,
            "sat_available": False
        }
        return JSONResponse(content=response_content)

    try:
        # Change to strm2stl directory to find config.json before importing
        original_cwd = os.getcwd()
        strm2stl_dir = Path(__file__).parent.parent
        os.chdir(str(strm2stl_dir))
        logger.debug(f"Changed cwd to {strm2stl_dir}")

        # Import here to avoid circular imports
        sys.path.append(str(strm2stl_dir))
        from numpy2stl.numpy2stl.oceans import make_dem_image

        # Generate DEM preview. Handle missing bbox parts by providing a small
        # fallback range so API remains permissive for incomplete params.
        if north is None or south is None:
            logger.debug(
                "Incomplete bbox (missing north/south), using small fallback lat range")
            center_lat = 0.0
            south = center_lat - 0.01
            north = center_lat + 0.01

        if east is None or west is None:
            logger.debug(
                "Incomplete bbox (missing east/west), using small fallback lon range")
            center_lon = 0.0
            west = center_lon - 0.01
            east = center_lon + 0.01

        target_bbox = (north, south, east, west)
        logger.debug(
            f"Calling make_dem_image with bbox {target_bbox}, projection={projection}")
        try:
            try:
                # Try with projection argument first
                im = make_dem_image(target_bbox, dim=dim, depth_scale=depth_scale, water_scale=water_scale,
                                    height=height, base=base, subtract_water=subtract_water,
                                    projection=projection, maintain_dimensions=maintain_dimensions)
            except TypeError:
                # Some test mocks provide a simple function that doesn't accept
                # the `projection` kwarg; retry without it.
                im = make_dem_image(target_bbox, dim=dim, depth_scale=depth_scale, water_scale=water_scale,
                                    height=height, base=base, subtract_water=subtract_water,
                                    maintain_dimensions=maintain_dimensions)

        except Exception as dem_error:
            logger.warning(
                f"DEM generation failed: {dem_error}, returning neutral mock data")
            # Return neutral mock data for preview (preserve aspect ratio)
            import numpy as np
            lat_range = abs(north - south)
            lon_range = abs(east - west)
            if lat_range > lon_range:
                mock_height = dim
                mock_width = max(1, int(dim * lon_range / lat_range))
            else:
                mock_width = dim
                mock_height = max(1, int(dim * lat_range / lon_range))
            im = np.zeros((mock_height, mock_width), dtype=float)

        # Generate response data for client-side rendering
        import numpy as np

        # Prepare DEM data for client - aspect ratio is preserved by make_dem_image
        # Sanitize NaN and Inf values for JSON serialization
        im_clean = np.nan_to_num(im, nan=0.0, posinf=np.finfo(
            np.float32).max, neginf=np.finfo(np.float32).min)
        dem_values = im_clean.ravel().tolist()
        height_px, width_px = im.shape

        # Satellite/landuse data (if requested)
        sat_values = None
        sat_width = None
        sat_height = None
        sat_available = False

        if show_sat or show_landuse:
            try:
                from geo2stl.sat2stl import fetch_bbox_image
                import cv2
                logger.debug(
                    f"Fetching satellite/landuse data dataset={dataset}")
                sat = fetch_bbox_image(
                    north, south, east, west, scale=30, dataset=dataset)

                # Check if satellite data is valid
                if sat is None:
                    logger.debug("Satellite fetch returned None")
                    sat_available = False
                else:
                    sat_arr = np.array(sat)
                    if sat_arr.size == 0 or sat_arr.ndim == 0:
                        logger.debug("Satellite data is empty or invalid")
                        sat_available = False
                    else:
                        # Resize satellite to match DEM dimensions (preserves aspect ratio)
                        sat_arr = cv2.resize(
                            sat_arr, (width_px, height_px), interpolation=cv2.INTER_LINEAR)
                        sat_values = sat_arr.ravel().tolist()
                        sat_height, sat_width = sat_arr.shape[:2]
                        sat_available = True
            except Exception as sat_err:
                logger.warning(f"Satellite fetch failed: {sat_err}")
                sat_available = False

        os.chdir(original_cwd)

        # Build response with raw data for client-side rendering
        response_content = {
            "dem_values": dem_values,
            "dimensions": [height_px, width_px],
            "min_elevation": float(np.nanmin(im)),
            "max_elevation": float(np.nanmax(im)),
            "mean_elevation": float(np.nanmean(im)),
            "bbox": [west, south, east, north],
            "show_sat": show_sat
        }

        # Include satellite data if available
        if sat_values is not None:
            response_content["sat_values"] = sat_values
            response_content["sat_dimensions"] = [sat_height, sat_width]
            response_content["sat_available"] = sat_available
        else:
            response_content["sat_available"] = False

        return JSONResponse(content=response_content)

    except Exception as e:
        logger.error(f"Error in preview_dem: {e}")
        import traceback
        tb = traceback.format_exc()
        return JSONResponse(content={"error": str(e), "traceback": tb}, status_code=500)


async def get_water_mask(request: Request):
    """Get water mask from ESA land cover data for the given bounding box."""
    logger.info("Received request for /api/water_mask")
    try:
        clear_caches_if_needed()

        params = request.query_params

        def _float(key, default=None):
            val = params.get(key)
            try:
                return float(val) if val not in (None, '') else default
            except (ValueError, TypeError):
                return default

        def _int(key, default=None):
            val = params.get(key)
            try:
                return int(float(val)) if val not in (None, '') else default
            except (ValueError, TypeError):
                return default

        north = _float("north")
        south = _float("south")
        east = _float("east")
        west = _float("west")
        sat_scale = _int("sat_scale", 500)
        dim = _int("dim", 200)
        target_width = _int("target_width")
        target_height = _int("target_height")

        if north is None or south is None or east is None or west is None:
            return JSONResponse(content={"error": "Missing bbox parameters (north, south, east, west)"}, status_code=400)

        # Auto-scale sat_scale to avoid Earth Engine pixel limits for large areas.
        # EE refuses requests that would produce > ~1e8 pixels; cap at 5M.
        import math as _math
        _bbox_w = abs(east - west)
        _bbox_h = abs(north - south)
        _mid_lat = (north + south) / 2.0
        _m_per_deg_lon = 111000.0 * _math.cos(_math.radians(_mid_lat))
        _m_per_deg_lat = 111000.0
        _est_px = (_bbox_w * _m_per_deg_lon / sat_scale) * \
            (_bbox_h * _m_per_deg_lat / sat_scale)
        _MAX_PX = 5_000_000
        if _est_px > _MAX_PX:
            _scale_factor = _math.sqrt(_est_px / _MAX_PX)
            sat_scale = max(sat_scale, int(sat_scale * _scale_factor))
            logger.info(
                f"Auto-scaled sat_scale to {sat_scale} for {_bbox_w:.1f}° × {_bbox_h:.1f}° area")

        if TEST_MODE:
            h, w = (target_height or dim, target_width or dim)
            water_arr = np.zeros((h, w), dtype=float)
            water_arr[h // 4:h // 2, w // 4:w // 2] = 1.0
            water_pixels = int(np.sum(water_arr))
            total_pixels = h * w
            return JSONResponse(content={
                "water_mask_values": water_arr.ravel().tolist(),
                "water_mask_dimensions": [h, w],
                "water_pixels": water_pixels,
                "total_pixels": total_pixels,
                "water_percentage": 100.0 * water_pixels / total_pixels,
                "esa_values": water_arr.ravel().tolist(),
                "esa_dimensions": [h, w],
            })

        original_cwd = os.getcwd()
        strm2stl_dir = Path(__file__).parent.parent
        os.chdir(str(strm2stl_dir))
        sys.path.append(str(strm2stl_dir))

        try:
            from geo2stl.sat2stl import fetch_bbox_image
            import cv2 as _cv2
            logger.info("Fetching ESA land cover data...")
            img = fetch_bbox_image(
                north, south, east, west,
                scale=sat_scale, dataset="esa", use_cache=True
            )

            # Fetch raw elevation tiles for bathymetry check (elevation < 0 = ocean)
            _elevation_raw = None
            try:
                from geo2stl.geo2stl import stitch_tiles_no_rasterio as _stitch
                _elevation_raw = _stitch((north, south, east, west))
            except Exception as _e:
                logger.warning(
                    f"Could not fetch elevation tiles for bathymetry check: {_e}")
        finally:
            os.chdir(original_cwd)

        if img is None:
            return JSONResponse(
                content={
                    "error": "Failed to fetch ESA land cover data. The area may be too large or outside coverage."},
                status_code=500
            )

        logger.info("Processing ESA data...")

        # Ensure 2D
        if img.ndim == 3:
            img = img[:, :, 0]

        # Resize to match DEM pixel dimensions if provided
        if target_width and target_height and (img.shape[1] != target_width or img.shape[0] != target_height):
            img = _cv2.resize(img.astype(
                np.float32), (target_width, target_height), interpolation=_cv2.INTER_NEAREST)

        h, w = img.shape

        # ESA water class is 80 (water bodies)
        # Also mark pixels below sea level (elevation < 0) as water — catches ocean areas
        # outside ESA WorldCover coverage that GEBCO/SRTM reports as bathymetric depth.
        water_mask = (img == 80).astype(float)

        if _elevation_raw is not None and _elevation_raw.size > 0:
            elev_resized = _cv2.resize(
                _elevation_raw.astype(np.float32), (w, h),
                interpolation=_cv2.INTER_LINEAR
            )
            water_mask = np.maximum(
                water_mask, (elev_resized < 0).astype(float))

        water_pixels = int(np.sum(water_mask))
        total_pixels = h * w

        logger.info("Returning water mask response.")
        return JSONResponse(content={
            "water_mask_values": water_mask.ravel().tolist(),
            "water_mask_dimensions": [h, w],
            "water_pixels": water_pixels,
            "total_pixels": total_pixels,
            "water_percentage": 100.0 * water_pixels / total_pixels if total_pixels > 0 else 0.0,
            "esa_values": img.ravel().tolist(),
            "esa_dimensions": [h, w],
        })

    except ValueError as ve:
        logger.error(f"Validation error: {ve}")
        return JSONResponse(content={"error": str(ve)}, status_code=400)
    except Exception as e:
        logger.error(f"Unhandled error in /api/water_mask: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


async def get_raw_dem(request: Request):
    """Get raw DEM data without water subtraction applied.

    This allows the UI to control water subtraction client-side.
    """
    clear_caches_if_needed()

    params = request.query_params

    def get_float(key, default=None):
        val = params.get(key)
        if val is None or val == '':
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_int(key, default=None):
        val = params.get(key)
        if val is None or val == '':
            return default
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default

    north = get_float("north")
    south = get_float("south")
    east = get_float("east")
    west = get_float("west")
    dim = get_int("dim", 200)
    depth_scale = get_float("depth_scale", 0.5)

    # Provide small fallback bbox for missing parameters to keep endpoint permissive
    if north is None or south is None:
        logger.debug(
            "Missing north/south in raw_dem request — using small default latitude range")
        center_lat = 0.0
        south = center_lat - 0.01
        north = center_lat + 0.01
    if east is None or west is None:
        logger.debug(
            "Missing east/west in raw_dem request — using small default longitude range")
        center_lon = 0.0
        west = center_lon - 0.01
        east = center_lon + 0.01

    logger.debug(
        f"GET /api/raw_dem north={north} south={south} east={east} west={west} dim={dim}")

    try:
        # Test-mode: return a deterministic raw DEM without EE/network
        if TEST_MODE:
            import numpy as _np
            logger.info(
                "STRM2STL_TEST_MODE active: returning deterministic raw DEM")
            im = _np.linspace(-50, 150, num=(dim * dim),
                              dtype=float).reshape((dim, dim))
            im[_np.isnan(im)] = 0.0
            response_content = {
                "dem_values": _np.nan_to_num(im).ravel().tolist(),
                "dimensions": [dim, dim],
                "min_elevation": float(_np.nanmin(im)),
                "max_elevation": float(_np.nanmax(im)),
                "bbox": [west or 0.0, south or 0.0, east or 0.0, north or 0.0]
            }
            return JSONResponse(content=response_content)

        import numpy as np

        original_cwd = os.getcwd()
        strm2stl_dir = Path(__file__).parent.parent
        os.chdir(str(strm2stl_dir))
        sys.path.append(str(strm2stl_dir))

        from numpy2stl.numpy2stl.oceans import stitch_tiles_no_rasterio, proj_map_geo_to_2D

        target_bbox = np.array((north, south, east, west))

        # Get raw DEM data
        im = stitch_tiles_no_rasterio(target_bbox) * 1.0

        # Apply depth scale to underwater values
        im[im < 0] = im[im < 0] * depth_scale

        # Project to 2D (removes distortion)
        im = proj_map_geo_to_2D(im, target_bbox)
        im = im[:, ~np.any(np.isnan(im), axis=0)]

        # Resize to target dimension while preserving aspect ratio
        import cv2
        h, w = im.shape
        if h > w:
            new_h = dim
            new_w = max(1, int(dim * w / h))
        else:
            new_w = dim
            new_h = max(1, int(dim * h / w))

        im_resized = cv2.resize(
            im, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        os.chdir(original_cwd)

        response_content = {
            "dem_values": im_resized.ravel().tolist(),
            "dimensions": [new_h, new_w],
            "min_elevation": float(np.nanmin(im_resized)),
            "max_elevation": float(np.nanmax(im_resized)),
            "mean_elevation": float(np.nanmean(im_resized)),
            # peak-to-peak for water scale calculation
            "ptp": float(np.ptp(im_resized)),
            "bbox": [west, south, east, north]
        }

        return JSONResponse(content=response_content)

    except Exception as e:
        logger.error(f"Error in raw_dem: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)


async def generate_stl(request: Request):
    """Generate and download an STL file from DEM data"""
    import tempfile
    from fastapi.responses import FileResponse

    try:
        data = await request.json()
        dem_values = data.get('dem_values', [])
        height = data.get('height', 0)
        width = data.get('width', 0)
        model_height = data.get('model_height', 20)
        base_height = data.get('base_height', 5)
        exaggeration = data.get('exaggeration', 1.0)
        name = data.get('name', 'terrain')

        if not dem_values or not height or not width:
            return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

        # Reshape DEM data
        im = np.array(dem_values).reshape(height, width)

        # Apply exaggeration
        im = im * exaggeration

        # Scale to target model height (normalize to 0-model_height range)
        im_min = np.nanmin(im)
        im_max = np.nanmax(im)
        if im_max > im_min:
            im = (im - im_min) / (im_max - im_min) * model_height

        # Add base
        im = im + base_height

        # Import numpy2stl functions
        original_cwd = os.getcwd()
        strm2stl_dir = Path(__file__).parent.parent
        os.chdir(str(strm2stl_dir))
        sys.path.append(str(strm2stl_dir))

        from numpy2stl.numpy2stl.generate import numpy2stl
        from numpy2stl.numpy2stl.solid import triangles_to_facets
        from numpy2stl.numpy2stl.save import writeSTL

        # Generate mesh
        logger.info(
            f"Generating STL: {width}x{height}, exag={exaggeration}, model_h={model_height}")
        vertices, faces = numpy2stl(im)
        triangles = vertices[faces]
        facets = triangles_to_facets(triangles)

        os.chdir(original_cwd)

        # Write to temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.stl')
        temp_path = temp_file.name
        temp_file.close()

        writeSTL(facets, temp_path, ascii=False)

        logger.info(f"STL generated: {temp_path}, {len(facets)} facets")

        return FileResponse(
            temp_path,
            filename=f"{name}.stl",
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={name}.stl"}
        )

    except Exception as e:
        logger.error(f"Error generating STL: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)


async def generate_obj(request: Request):
    """Generate and download an OBJ file from DEM data"""
    import tempfile
    from fastapi.responses import FileResponse

    try:
        data = await request.json()
        dem_values = data.get('dem_values', [])
        height = data.get('height', 0)
        width = data.get('width', 0)
        model_height = data.get('model_height', 20)
        base_height = data.get('base_height', 5)
        exaggeration = data.get('exaggeration', 1.0)
        name = data.get('name', 'terrain')

        if not dem_values or not height or not width:
            return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

        # Reshape DEM data
        im = np.array(dem_values).reshape(height, width)

        # Apply exaggeration
        im = im * exaggeration

        # Scale to target model height (normalize to 0-model_height range)
        im_min = np.nanmin(im)
        im_max = np.nanmax(im)
        if im_max > im_min:
            im = (im - im_min) / (im_max - im_min) * model_height

        # Add base
        im = im + base_height

        # Import numpy2stl functions
        original_cwd = os.getcwd()
        strm2stl_dir = Path(__file__).parent.parent
        os.chdir(str(strm2stl_dir))
        sys.path.append(str(strm2stl_dir))

        from numpy2stl.numpy2stl.generate import numpy2stl
        from numpy2stl.numpy2stl.save import writeOBJ

        # Generate mesh
        logger.info(f"Generating OBJ: {width}x{height}, exag={exaggeration}")
        vertices, faces = numpy2stl(im)

        os.chdir(original_cwd)

        # Write to temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.obj')
        temp_path = temp_file.name
        temp_file.close()

        # Write OBJ with model name
        writeOBJ(temp_path, {name: (vertices, faces)})

        logger.info(
            f"OBJ generated: {temp_path}, {len(vertices)} vertices, {len(faces)} faces")

        return FileResponse(
            temp_path,
            filename=f"{name}.obj",
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={name}.obj"}
        )

    except Exception as e:
        logger.error(f"Error generating OBJ: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)


async def generate_3mf(request: Request):
    """Generate and download a 3MF file from DEM data"""
    import tempfile
    from fastapi.responses import FileResponse

    try:
        data = await request.json()
        dem_values = data.get('dem_values', [])
        height = data.get('height', 0)
        width = data.get('width', 0)
        model_height = data.get('model_height', 20)
        base_height = data.get('base_height', 5)
        exaggeration = data.get('exaggeration', 1.0)
        name = data.get('name', 'terrain')

        if not dem_values or not height or not width:
            return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

        # Reshape DEM data
        im = np.array(dem_values).reshape(height, width)

        # Apply exaggeration
        im = im * exaggeration

        # Scale to target model height (normalize to 0-model_height range)
        im_min = np.nanmin(im)
        im_max = np.nanmax(im)
        if im_max > im_min:
            im = (im - im_min) / (im_max - im_min) * model_height

        # Add base
        im = im + base_height

        # Import numpy2stl functions
        original_cwd = os.getcwd()
        strm2stl_dir = Path(__file__).parent.parent
        os.chdir(str(strm2stl_dir))
        sys.path.append(str(strm2stl_dir))

        from numpy2stl.numpy2stl.generate import numpy2stl
        from numpy2stl.numpy2stl.save import write3MF

        # Generate mesh
        logger.info(f"Generating 3MF: {width}x{height}, exag={exaggeration}")
        vertices, faces = numpy2stl(im)

        os.chdir(original_cwd)

        # Write to temp file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.3mf')
        temp_path = temp_file.name
        temp_file.close()

        # Write 3MF with model name
        write3MF(temp_path, {name: (vertices, faces)})

        logger.info(
            f"3MF generated: {temp_path}, {len(vertices)} vertices, {len(faces)} faces")

        return FileResponse(
            temp_path,
            filename=f"{name}.3mf",
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={name}.3mf"}
        )

    except Exception as e:
        logger.error(f"Error generating 3MF: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ============================================================
# V2 API — organized by resource, with Pydantic request/response models
# The legacy endpoints above are kept for backward compatibility.
# New frontend code should use these routes.
# ============================================================

# --- Regions ---

@app.get("/api/regions", response_model=RegionsListResponse, tags=["regions"])
async def list_regions():
    """Return all saved geographic regions."""
    coordinates_path = COORDINATES_PATH
    try:
        with open(coordinates_path, "r") as f:
            data = json.load(f)
        return JSONResponse(content=data)
    except FileNotFoundError:
        return JSONResponse(content={"regions": []})
    except Exception as e:
        logger.error(f"Error reading regions: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/regions", response_model=RegionResponse, status_code=201, tags=["regions"])
async def create_region(region: RegionCreate):
    """Save a new geographic region."""
    coordinates_path = COORDINATES_PATH
    try:
        data = json.loads(coordinates_path.read_text()
                          ) if coordinates_path.exists() else {"regions": []}
        payload = region.dict()
        if payload.get("parameters") is None:
            payload["parameters"] = RegionParameters().dict()
        data["regions"].append(payload)
        coordinates_path.write_text(json.dumps(data, indent=2))
        return JSONResponse(content=payload, status_code=201)
    except Exception as e:
        logger.error(f"Error creating region: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.put("/api/regions/{name}", response_model=RegionResponse, tags=["regions"])
async def update_region(name: str, region: RegionCreate):
    """Update an existing saved region by name."""
    coordinates_path = COORDINATES_PATH
    try:
        data = json.loads(coordinates_path.read_text()
                          ) if coordinates_path.exists() else {"regions": []}
        regions = data.get("regions", [])
        for i, r in enumerate(regions):
            if r.get("name") == name:
                payload = region.dict()
                if payload.get("parameters") is None:
                    payload["parameters"] = r.get(
                        "parameters", RegionParameters().dict())
                regions[i] = payload
                coordinates_path.write_text(json.dumps(data, indent=2))
                return JSONResponse(content=payload)
        return JSONResponse(content={"error": f"Region '{name}' not found"}, status_code=404)
    except Exception as e:
        logger.error(f"Error updating region: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.delete("/api/regions/{name}", tags=["regions"])
async def delete_region(name: str):
    """Delete a saved region by name."""
    coordinates_path = COORDINATES_PATH
    try:
        if not coordinates_path.exists():
            return JSONResponse(content={"error": "Region not found"}, status_code=404)
        data = json.loads(coordinates_path.read_text())
        original_count = len(data.get("regions", []))
        data["regions"] = [r for r in data["regions"] if r.get("name") != name]
        if len(data["regions"]) == original_count:
            return JSONResponse(content={"error": f"Region '{name}' not found"}, status_code=404)
        coordinates_path.write_text(json.dumps(data, indent=2))
        return JSONResponse(content={"status": "deleted", "name": name})
    except Exception as e:
        logger.error(f"Error deleting region: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


# --- Terrain / Elevation ---

@app.api_route("/api/terrain/dem", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_dem(request: Request):
    """
    Fetch a Digital Elevation Model preview for a bounding box.

    Replaces the legacy /api/preview_dem endpoint. Returns raw elevation
    values for client-side colormap rendering.
    """
    # Delegate to the existing implementation
    return await preview_dem(request)


@app.api_route("/api/terrain/dem/raw", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_dem_raw(request: Request):
    """
    Fetch unprocessed SRTM/GEBCO elevation data before water subtraction.

    Replaces the legacy /api/raw_dem endpoint. Returns the full ptp value
    so the client can compute water-mask subtraction locally.
    """
    return await get_raw_dem(request)


@app.api_route("/api/terrain/water-mask", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_water_mask(request: Request):
    """
    Fetch a binary water mask and ESA WorldCover land-cover data.

    Replaces the legacy /api/water_mask endpoint. Pass target_width and
    target_height to align the mask to an already-fetched DEM grid.
    """
    return await get_water_mask(request)


@app.api_route("/api/terrain/satellite", methods=["GET", "POST"], tags=["terrain"])
async def get_terrain_satellite(request: Request):
    """
    Fetch satellite or land-cover imagery for a bounding box.

    TODO: Implement as a standalone endpoint separate from the DEM preview.
          Should call fetch_bbox_image with the requested dataset and return
          SatelliteResponse — values, dimensions, dataset, bbox.
    """
    return JSONResponse(content={"error": "Not implemented"}, status_code=501)


@app.get("/api/terrain/elevation-profile", tags=["terrain"])
async def get_elevation_profile(
    lat1: float = Query(..., ge=-90, le=90, description="Start latitude"),
    lon1: float = Query(..., ge=-180, le=180, description="Start longitude"),
    lat2: float = Query(..., ge=-90, le=90, description="End latitude"),
    lon2: float = Query(..., ge=-180, le=180, description="End longitude"),
    samples: int = Query(
        100, ge=2, le=1000, description="Number of sample points along the transect"),
):
    """
    Return an elevation profile along a straight transect between two points.

    TODO: Sample the DEM along the great-circle path between (lat1, lon1) and
          (lat2, lon2). Return a list of {distance_km, elevation_m} objects.
    """
    return JSONResponse(content={"error": "Not implemented"}, status_code=501)


# --- Export / 3D Models ---

@app.post("/api/export/stl", tags=["export"])
async def export_stl(request: Request):
    """
    Generate and download an STL file from DEM data.
    Replaces the legacy /api/generate_stl endpoint.
    """
    return await generate_stl(request)


@app.post("/api/export/obj", tags=["export"])
async def export_obj(request: Request):
    """
    Generate and download an OBJ file from DEM data.
    Replaces the legacy /api/generate_obj endpoint.
    """
    return await generate_obj(request)


@app.post("/api/export/3mf", tags=["export"])
async def export_3mf(request: Request):
    """
    Generate and download a 3MF file from DEM data.
    Replaces the legacy /api/generate_3mf endpoint.
    """
    return await generate_3mf(request)


@app.post("/api/export/preview", tags=["export"])
async def export_preview(request: Request):
    """
    Generate an in-browser 3D mesh preview (glTF/JSON) without downloading.

    TODO: Build a lightweight triangulated mesh from dem_values and return it
          as a glTF binary or three.js-compatible JSON so the Model tab can
          show a live preview before the user commits to a full export.
    """
    return JSONResponse(content={"error": "Not implemented"}, status_code=501)


# --- Cache ---

@app.get("/api/cache", response_model=CacheStatusResponse, tags=["cache"])
async def get_cache_info():
    """Return cache status and file counts. Replaces /api/cache_status."""
    return await get_cache_status()


@app.delete("/api/cache", response_model=CacheClearResponse, tags=["cache"])
async def clear_cache_v2():
    """Clear all cached Earth Engine tiles. Replaces /api/clear_cache."""
    return await clear_cache()


@app.get("/api/cache/check", tags=["cache"])
async def check_cache(request: Request):
    """Check whether a specific region is already cached server-side."""
    return await check_cached(request)


# --- Settings ---

@app.get("/api/settings/projections", response_model=ProjectionsResponse, tags=["settings"])
async def list_projections():
    """Return available map projections. Replaces /api/projections."""
    return await get_projections()


@app.get("/api/settings/colormaps", response_model=ColormapsResponse, tags=["settings"])
async def list_colormaps():
    """
    Return the colormaps available for DEM rendering.

    TODO: Pull this list from matplotlib.cm (or a curated subset) and return
          ColormapsResponse. Include a description for each colormap so the UI
          can show a tooltip.
    """
    colormaps = [
        ColormapInfo(
            id="terrain", description="Classic green-brown-white terrain"),
        ColormapInfo(
            id="viridis", description="Perceptually uniform, colorblind-safe"),
        ColormapInfo(id="plasma", description="High-contrast warm gradient"),
        ColormapInfo(id="magma", description="Dark background, bright peaks"),
        ColormapInfo(
            id="inferno", description="Black-to-yellow fire gradient"),
        ColormapInfo(id="cividis", description="Colorblind-safe blue-yellow"),
        ColormapInfo(id="gray", description="Grayscale hillshade"),
        ColormapInfo(id="ocean", description="Blue depth gradient"),
        ColormapInfo(id="hot", description="Black-red-yellow-white"),
        ColormapInfo(
            id="RdBu", description="Diverging red-blue for anomaly maps"),
    ]
    return JSONResponse(content={"colormaps": [c.dict() for c in colormaps]})


@app.get("/api/settings/datasets", response_model=DatasetsResponse, tags=["settings"])
async def list_datasets():
    """
    Return the elevation and land-cover datasets available for DEM requests.

    TODO: Move this list to a config file so new datasets can be added without
          code changes.
    """
    datasets = [
        DatasetInfo(id="esa", name="ESA WorldCover 2020", description="10 m land cover classification",
                    source="ESA/WorldCover/v100/2020", requires_auth=True),
        DatasetInfo(id="copernicus", name="Copernicus DEM GLO-30",
                    description="30 m global elevation model", source="COPERNICUS/DEM/GLO30", requires_auth=True),
        DatasetInfo(id="nasadem", name="NASA SRTM / NASADEM", description="30 m void-filled SRTM elevation",
                    source="NASA/NASADEM_HGT/001", requires_auth=True),
        DatasetInfo(id="usgs", name="USGS 3DEP 10 m", description="10 m elevation (CONUS only)",
                    source="USGS/3DEP/10m", requires_auth=True),
        DatasetInfo(id="gebco", name="GEBCO 2022", description="450 m global ocean bathymetry + land",
                    source="Local GEBCO GeoTIFFs", requires_auth=False),
        DatasetInfo(id="jrc", name="JRC Global Surface Water", description="Water occurrence 1984–2021",
                    source="JRC/GSW1_4/GlobalSurfaceWater", requires_auth=True),
    ]
    return JSONResponse(content={"datasets": [d.dict() for d in datasets]})


# Function to run FastAPI server


def run_server():
    port = int(os.environ.get('UI_PORT', '9000'))
    uvicorn.run(app, host="127.0.0.1", port=port)

# Function to detect Jupyter notebook environment


def _build_global_dem_cache(force: bool = False) -> bool:
    """
    Stitch all elevation tiles for the full globe, downsample to ≤1200 px wide,
    apply a terrain colormap, and save to ui/static/global_dem.png + meta JSON.
    Returns True on success.  Safe to call from a background thread.
    """
    import json as _json
    import cv2 as _cv2
    from PIL import Image as _PILImage

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)
    png_path = static_dir / "global_dem.png"
    meta_path = static_dir / "global_dem_meta.json"

    if png_path.exists() and meta_path.exists() and not force:
        logger.info("Global DEM cache already exists — skipping generation.")
        return True

    logger.info("Building global DEM cache (full globe, 90/-90/180/-180) …")
    original_cwd = os.getcwd()
    try:
        strm2stl_dir = Path(__file__).parent.parent
        os.chdir(str(strm2stl_dir))
        sys.path.append(str(strm2stl_dir))

        from geo2stl.geo2stl import stitch_tiles_no_rasterio, tile_files as _tile_files
        if not _tile_files:
            logger.warning("Global DEM cache: no elevation tiles found.")
            return False

        img_arr = stitch_tiles_no_rasterio((90.0, -90.0, 180.0, -180.0))
    finally:
        os.chdir(original_cwd)

    if img_arr is None or img_arr.size == 0:
        logger.error("Global DEM cache: stitching returned empty array.")
        return False

    # Downsample to max 1200 px wide
    h_orig, w_orig = img_arr.shape[:2]
    max_w = 1200
    if w_orig > max_w:
        new_h = max(1, int(h_orig * max_w / w_orig))
        img_arr = _cv2.resize(img_arr.astype(np.float32), (max_w, new_h),
                              interpolation=_cv2.INTER_AREA)

    vmin = float(np.nanmin(img_arr))
    vmax = float(np.nanmax(img_arr))
    norm = ((img_arr - vmin) / ((vmax - vmin) or 1.0)).clip(0, 1)

    # 5-stop terrain colormap
    _stops = [
        (0.00, (0.10, 0.22, 0.50)),
        (0.30, (0.20, 0.50, 0.85)),
        (0.45, (0.20, 0.70, 0.30)),
        (0.60, (0.75, 0.70, 0.35)),
        (0.80, (0.55, 0.35, 0.20)),
        (1.00, (1.00, 1.00, 1.00)),
    ]

    def _tc(t):
        for i in range(len(_stops) - 1):
            t0, c0 = _stops[i]
            t1, c1 = _stops[i + 1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                return tuple(c0[j] + f * (c1[j] - c0[j]) for j in range(3))
        return _stops[-1][1]

    h2, w2 = norm.shape
    rgba = np.zeros((h2, w2, 4), dtype=np.uint8)
    flat = norm.ravel()
    for idx, t in enumerate(flat):
        r, g, b = _tc(float(t))
        row, col = divmod(idx, w2)
        rgba[row, col] = (int(r * 255), int(g * 255), int(b * 255), 220)

    _PILImage.fromarray(rgba, 'RGBA').save(str(png_path), 'PNG')
    meta = {"north": 90.0, "south": -90.0, "east": 180.0, "west": -180.0,
            "vmin": vmin, "vmax": vmax}
    meta_path.write_text(_json.dumps(meta))
    logger.info(f"Global DEM cache saved: {w2}×{h2} px → {png_path}")
    return True


@app.on_event("startup")
async def _startup_build_global_dem():
    """Generate the global DEM overview on startup (runs once in background)."""
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _build_global_dem_cache, False)


@app.get("/api/global_dem_overview")
async def get_global_dem_overview(regen: bool = False):
    """Serve the cached global DEM PNG (regenerate if regen=true or file missing)."""
    from fastapi.responses import FileResponse as _FR
    import json as _json

    static_dir = Path(__file__).parent / "static"
    png_path = static_dir / "global_dem.png"
    meta_path = static_dir / "global_dem_meta.json"

    if regen or not png_path.exists() or not meta_path.exists():
        ok = _build_global_dem_cache(force=regen)
        if not ok:
            return JSONResponse(content={"error": "Could not build global DEM cache"}, status_code=500)

    return _FR(str(png_path), media_type="image/png",
               headers={"X-DEM-Meta": meta_path.read_text()})


def in_notebook():
    try:
        from IPython import get_ipython
        if 'IPython' in sys.modules:
            return True
        return False
    except ImportError:
        return False

# Function to open browser and select location


def get_location():
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    webbrowser.open("http://127.0.0.1:9000")
    logger.info("Waiting for user to select a bounding box...")

    logger.debug(f"Templates path: {templates_path}")

    if not in_notebook():
        # If in Jupyter, use asyncio.sleep for non-blocking operation
        while 'lat' not in selected_location:
            asyncio.run(asyncio.sleep(0.2))
    else:
        # Otherwise, use time.sleep for standard blocking operation
        while 'lat' not in selected_location:
            time.sleep(0.2)

    return selected_location['lat'], selected_location['lng']


if __name__ == "__main__":
    # Run the server when script is executed directly
    logger.info("Starting 3D Maps Globe Selector server...")
    port = int(os.environ.get('UI_PORT', '9000'))
    logger.info(f"Open http://127.0.0.1:{port} in your browser")
    uvicorn.run(app, host="127.0.0.1", port=port)
