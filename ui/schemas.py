"""
schemas.py — All Pydantic request/response models for the strm2stl API.

Extracted from location_picker.py (backend refactor, step 2).
Import from here; location_picker.py re-exports everything for backward compat.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# Pydantic V2: use field_validator + @classmethod; V1: fall back to validator
try:
    from pydantic import field_validator as _fv

    def _north_validator_fn(cls, v, info):
        data = getattr(info, "data", {}) or {}
        if "south" in data and v <= data["south"]:
            raise ValueError("north must be greater than south")
        return v

    _north_validator = classmethod(_fv("north", mode="after")(_north_validator_fn))
except Exception:
    from pydantic import validator as _v  # type: ignore

    def _north_validator_fn(cls, v, values):  # type: ignore[no-redef]
        if "south" in values and v <= values["south"]:
            raise ValueError("north must be greater than south")
        return v

    _north_validator = classmethod(_v("north")(_north_validator_fn))  # type: ignore


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

class BoundingBox(BaseModel):
    """Geographic bounding box using cardinal directions."""
    north: float = Field(..., ge=-90, le=90, description="Northern latitude bound")
    south: float = Field(..., ge=-90, le=90, description="Southern latitude bound")
    east:  float = Field(..., ge=-180, le=180, description="Eastern longitude bound")
    west:  float = Field(..., ge=-180, le=180, description="Western longitude bound")

    north_gt_south = _north_validator


# Legacy alias kept for backward-compatibility with older frontend code
class BoundingBoxLegacy(BaseModel):
    southWestLat: float
    southWestLng: float
    northEastLat: float
    northEastLng: float


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

class RegionParameters(BaseModel):
    """Rendering and export parameters stored with a saved region."""
    dim: int = Field(200, ge=1, le=2000, description="Grid resolution (pixels per side)")
    depth_scale: float = Field(0.5, ge=0.0, le=10.0, description="Depth scaling for ocean floor")
    water_scale: float = Field(0.05, ge=0.0, le=1.0, description="Water subtraction strength")
    height: float = Field(10.0, ge=0.0, description="Model height in mm")
    base: float = Field(2.0, ge=0.0, description="Base thickness in mm")
    subtract_water: bool = Field(True, description="Whether to subtract water bodies from terrain")
    sat_scale: int = Field(500, ge=10, description="Earth Engine scale in metres/pixel for satellite data")


class RegionCreate(BoundingBox):
    """Request body for creating or updating a saved region."""
    name: str = Field(..., min_length=1, max_length=128, description="Unique region name")
    description: Optional[str] = Field(None, max_length=512)
    label: Optional[str] = Field(None, max_length=64, description="Group/continent label for sidebar grouping")
    parameters: Optional[RegionParameters] = None


class RegionResponse(BoundingBox):
    """A saved geographic region returned by the API."""
    name: str
    description: Optional[str] = None
    label: Optional[str] = None
    parameters: Optional[RegionParameters] = None


class RegionsListResponse(BaseModel):
    regions: List[RegionResponse]


class RegionSettings(BaseModel):
    """All editable panel settings saved per region, separate from geometry."""
    dim: Optional[int] = None
    depth_scale: Optional[float] = None
    water_scale: Optional[float] = None
    height: Optional[float] = None
    base: Optional[float] = None
    subtract_water: Optional[bool] = None
    sat_scale: Optional[int] = None
    colormap: Optional[str] = None
    projection: Optional[str] = None
    rescale_min: Optional[float] = None
    rescale_max: Optional[float] = None
    gridlines_show: Optional[bool] = None
    gridlines_count: Optional[int] = None
    elevation_curve: Optional[str] = None
    elevation_curve_points: Optional[List[List[float]]] = None
    dem_source: Optional[str] = None


# ---------------------------------------------------------------------------
# Cities / OSM
# ---------------------------------------------------------------------------

class CityRequest(BoundingBox):
    """Request body for fetching OSM city data."""
    layers: Optional[List[str]] = Field(
        default=["buildings", "roads", "waterways"],
        description="Which OSM layers to fetch"
    )
    simplify_tolerance: float = Field(default=2.0, description="Polygon simplification tolerance in metres")
    min_area: float = Field(default=20.0, description="Minimum building area in square metres to keep")


# ---------------------------------------------------------------------------
# Terrain / Elevation
# ---------------------------------------------------------------------------

class DEMRequest(BoundingBox):
    """Parameters for fetching a Digital Elevation Model preview."""
    dim: int = Field(200, ge=1, le=2000, description="Target grid resolution")
    depth_scale: float = Field(0.5, ge=0.0, le=10.0)
    water_scale: float = Field(0.05, ge=0.0, le=1.0)
    height: float = Field(10.0, ge=0.0)
    base: float = Field(2.0, ge=0.0)
    subtract_water: bool = True
    dataset: str = Field("esa", description="Elevation dataset: 'esa', 'copernicus', 'nasadem', 'usgs', 'gebco'")
    colormap: str = Field("terrain", description="Matplotlib colormap name for client-side rendering")
    show_landuse: bool = Field(False, description="Include ESA land-cover overlay")


class DEMResponse(BaseModel):
    """Raw elevation data returned for client-side rendering."""
    dem_values: List[float] = Field(..., description="Flat row-major array of elevation values (metres)")
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
    ptp: float = Field(..., description="Peak-to-peak range for client-side water scale calculation")
    bbox: List[float]


class WaterMaskRequest(BoundingBox):
    """Parameters for fetching a water / land-cover mask."""
    sat_scale: int = Field(500, ge=10, description="Earth Engine resolution in metres/pixel")
    dim: int = Field(200, ge=1, le=2000)
    target_width: Optional[int] = Field(None, description="Resize output to match DEM pixel width")
    target_height: Optional[int] = Field(None, description="Resize output to match DEM pixel height")


class WaterMaskResponse(BaseModel):
    """Binary water mask and ESA land-cover data for the requested bbox."""
    water_mask_values: List[float] = Field(..., description="Flat binary array: 1 = water, 0 = land")
    water_mask_dimensions: List[int] = Field(..., description="[height_px, width_px]")
    water_pixels: int
    total_pixels: int
    water_percentage: float
    esa_values: Optional[List[float]] = Field(None, description="Raw ESA WorldCover class values")
    esa_dimensions: Optional[List[int]] = None


class SatelliteRequest(BoundingBox):
    """Parameters for fetching satellite / land-cover imagery."""
    dataset: str = Field("esa", description="'esa', 'copernicus', 'jrc'")
    dim: int = Field(200, ge=1, le=2000)
    scale: Optional[int] = Field(None, description="Earth Engine resolution in metres/pixel")


class SatelliteResponse(BaseModel):
    """Satellite or land-cover image data."""
    values: List[float]
    dimensions: List[int]
    dataset: str
    bbox: List[float]


# ---------------------------------------------------------------------------
# Export / 3D Models
# ---------------------------------------------------------------------------

class ExportRequest(BoundingBox):
    """Parameters for generating a 3D model file."""
    dem_values: List[float] = Field(..., description="Flat row-major elevation array from /api/terrain/dem")
    height: int = Field(0, description="Grid height in pixels")
    width: int = Field(0, description="Grid width in pixels")
    model_height: float = Field(20.0, ge=0.1, description="Physical model height in mm")
    base_height: float = Field(5.0, ge=0.0, description="Base plate thickness in mm")
    exaggeration: float = Field(1.0, ge=0.0, description="Vertical exaggeration multiplier")
    name: str = Field("terrain", max_length=64, description="Output file base name")


class ExportResponse(BaseModel):
    status: str
    filename: Optional[str] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

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


# Legacy alias kept so existing water-mask handler can still be used as body model
class Region(BoundingBox):
    sat_scale: Optional[int] = None
    dim: Optional[int] = None
    target_width: Optional[int] = None
    target_height: Optional[int] = None


# ---------------------------------------------------------------------------
# DEM Merge / Composite
# ---------------------------------------------------------------------------

class ProcessingSpec(BaseModel):
    """Per-layer image processing before blending."""
    smooth_sigma: float = 0.0
    sharpen: bool = False
    clip_min: Optional[float] = None
    clip_max: Optional[float] = None
    normalize: bool = False
    invert: bool = False
    extract_rivers: bool = False
    river_max_width_px: int = 8


class MergeLayerSpec(BaseModel):
    """One layer in the merge stack."""
    source: str = "local"
    dim: int = Field(300, ge=50, le=2000)
    blend_mode: str = "base"
    weight: float = Field(1.0, ge=0.0, le=10.0)
    processing: ProcessingSpec = Field(default_factory=ProcessingSpec)
    label: Optional[str] = None


class MergeRequest(BaseModel):
    """Request body for POST /api/dem/merge."""
    bbox: Dict[str, float]
    dim: int = Field(300, ge=50, le=2000)
    layers: List[MergeLayerSpec]
