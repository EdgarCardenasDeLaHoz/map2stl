"""
schemas.py — All Pydantic request/response models for the strm2stl API.

Extracted from location_picker.py (backend refactor, step 2).
Import from here; location_picker.py re-exports everything for backward compat.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# Pydantic V2: use field_validator + @classmethod; V1: fall back to validator
try:
    from pydantic import field_validator as _fv

    def _north_validator_fn(cls, v, info):
        data = getattr(info, "data", {}) or {}
        if "south" in data and v <= data["south"]:
            raise ValueError("north must be greater than south")
        return v

    _north_validator = classmethod(
        _fv("north", mode="after")(_north_validator_fn))
except Exception:
    from pydantic import validator as _v  # type: ignore

    def _north_validator_fn(cls, v, values):  # type: ignore[no-redef]
        if "south" in values and v <= values["south"]:
            raise ValueError("north must be greater than south")
        return v

    _north_validator = classmethod(
        _v("north", allow_reuse=True)(_north_validator_fn))  # type: ignore


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

class BoundingBox(BaseModel):
    """Geographic bounding box using cardinal directions."""
    north: float = Field(..., ge=-90, le=90,
                         description="Northern latitude bound")
    south: float = Field(..., ge=-90, le=90,
                         description="Southern latitude bound")
    east:  float = Field(..., ge=-180, le=180,
                         description="Eastern longitude bound")
    west:  float = Field(..., ge=-180, le=180,
                         description="Western longitude bound")

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
    description: str | None = Field(None, max_length=512)
    label: str | None = Field(
        None, max_length=64, description="Group/continent label for sidebar grouping")
    parameters: RegionParameters | None = None


class RegionResponse(BoundingBox):
    """A saved geographic region returned by the API."""
    name: str
    description: str | None = None
    label: str | None = None
    parameters: RegionParameters | None = None


class RegionsListResponse(BaseModel):
    regions: list[RegionResponse]


class RegionSettings(BaseModel):
    """
    All editable panel settings saved per region, stored as a free-form JSON blob.
    Structure mirrors terrain_session.py _DEFAULT_SETTINGS: grouped by dem, projection,
    view, water, satellite, city, export, split, hydrology.
    Also accepts the legacy flat shape for backwards compatibility.
    """
    model_config = {"extra": "allow"}

    # Accept any field — the full grouped blob is stored verbatim as JSON.
    # Typed fields below are kept for backwards-compat with old flat saves.
    dim: int | None = None
    depth_scale: float | None = None
    water_scale: float | None = None
    height: float | None = None
    base: float | None = None
    subtract_water: bool | None = None
    sat_scale: int | None = None
    colormap: str | None = None
    projection: str | None = None
    rescale_min: float | None = None
    rescale_max: float | None = None
    gridlines_show: bool | None = None
    gridlines_count: int | None = None
    elevation_curve: str | None = None
    elevation_curve_points: list[list[float]] | None = None
    dem_source: str | None = None


# ---------------------------------------------------------------------------
# Cities / OSM
# ---------------------------------------------------------------------------

class CityRequest(BoundingBox):
    """Request body for fetching OSM city data."""
    layers: list[str] | None = Field(
        default=["buildings", "roads", "waterways"],
        description="Which OSM layers to fetch"
    )
    simplify_tolerance: float = Field(
        default=0.5, description="Polygon simplification tolerance in metres")
    min_area: float = Field(
        default=5.0, description="Minimum building area in square metres to keep")
    detail: Literal["full", "coarse"] = Field(
        default="full",
        description="'full' = existing 10-15km per-building tier (walls, small buildings, "
                    "all layers). 'coarse' = 25km tier: roads + waterways + large buildings "
                    "only (no walls, area-filtered), for regions too large for full detail."
    )


class EnhanceHeightsRequest(BoundingBox):
    """Request body for POST /api/cities/enhance-heights.

    Buildings GeoJSON can be omitted — the endpoint reads from the OSM
    disk cache populated by a prior ``POST /api/cities`` call.
    """
    buildings: dict[str, Any] | None = Field(
        None, description="GeoJSON FeatureCollection of buildings (resolved from OSM cache if omitted)")
    dim: int = Field(512, ge=64, le=2048,
                     description="Height raster resolution (dim x dim)")


class CityRasterRequest(BaseModel):
    """Request body for POST /api/cities/raster — burns OSM features onto a height-map grid."""
    north: float
    south: float
    east: float
    west: float
    dim: int = Field(200, ge=10, le=2000,
                     description="Output grid dimension (dim × dim pixels)")
    buildings: dict[str, Any] = Field(
        default_factory=dict, description="GeoJSON FeatureCollection")
    roads: dict[str, Any] = Field(
        default_factory=dict, description="GeoJSON FeatureCollection")
    waterways: dict[str, Any] = Field(
        default_factory=dict, description="GeoJSON FeatureCollection")
    building_scale: float = Field(
        1.0, ge=0.0, description="Multiplier applied to height_m when burning buildings")
    road_depression_m: float = Field(
        0.0, description="Road surface height relative to 0 (negative = depressed)")
    water_depression_m: float = Field(-2.0,
                                      description="Waterway surface height relative to 0")
    projection: str = Field(
        "none", description="Map projection to apply after rasterisation ('none', 'cosine', 'mercator', etc.)")
    clip_valid_region: bool | None = Field(
        True,
        description="Clip projection padding to the valid data extent.",
    )
    clip_nans: bool = Field(
        True,
        description="Deprecated alias for clip_valid_region.",
    )
    maintain_dimensions: bool = Field(
        False,
        description="Keep input grid shape after projection (legacy/opt-in). "
                    "Default False: output reflects the projection's true aspect ratio.",
    )


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
    dataset: str = Field(
        "esa", description="Elevation dataset: 'esa', 'copernicus', 'nasadem', 'usgs', 'gebco'")
    colormap: str = Field(
        "terrain", description="Matplotlib colormap name for client-side rendering")


class DEMResponse(BaseModel):
    """Raw elevation data returned for client-side rendering."""
    dem_values: list[float] = Field(
        ..., description="Flat row-major array of elevation values (metres)")
    dimensions: list[int] = Field(..., description="[height_px, width_px]")
    min_elevation: float
    max_elevation: float
    mean_elevation: float
    bbox: list[float] = Field(..., description="[west, south, east, north]")
    sat_available: bool = False
    sat_values: list[float] | None = None
    sat_dimensions: list[int] | None = None


class RawDEMResponse(BaseModel):
    """Unprocessed SRTM/GEBCO elevation data before water subtraction."""
    dem_values: list[float]
    dimensions: list[int]
    min_elevation: float
    max_elevation: float
    mean_elevation: float
    ptp: float = Field(...,
                       description="Peak-to-peak range for client-side water scale calculation")
    bbox: list[float]


class WaterMaskRequest(BoundingBox):
    """Parameters for fetching a water / land-cover mask."""
    sat_scale: int = Field(
        500, ge=10, description="Earth Engine resolution in metres/pixel")
    dim: int = Field(200, ge=1, le=2000)
    target_width: int | None = Field(
        None, description="Resize output to match DEM pixel width")
    target_height: int | None = Field(
        None, description="Resize output to match DEM pixel height")


class WaterMaskResponse(BaseModel):
    """Binary water mask and ESA land-cover data for the requested bbox."""
    water_mask_values: list[float] = Field(
        ..., description="Flat binary array: 1 = water, 0 = land")
    water_mask_dimensions: list[int] = Field(...,
                                             description="[height_px, width_px]")
    water_pixels: int
    total_pixels: int
    water_percentage: float
    esa_values: list[float] | None = Field(
        None, description="Raw ESA WorldCover class values")
    esa_dimensions: list[int] | None = None


class SatelliteRequest(BoundingBox):
    """Parameters for fetching satellite / land-cover imagery."""
    dataset: str = Field("esa", description="'esa', 'copernicus', 'jrc'")
    dim: int = Field(200, ge=1, le=2000)
    scale: int | None = Field(
        None, description="Earth Engine resolution in metres/pixel")


class SatelliteResponse(BaseModel):
    """Satellite or land-cover image data."""
    values: list[float]
    dimensions: list[int]
    dataset: str
    bbox: list[float]


# ---------------------------------------------------------------------------
# Export / 3D Models
# ---------------------------------------------------------------------------

class ExportRequest(BoundingBox):
    """Parameters for generating a 3D model file.

    DEM data is resolved from the server-side disk cache using bbox + dem
    settings.  Legacy callers may still pass ``dem_values`` directly.
    """
    dem_values: list[float] | None = Field(
        None, description="Flat row-major elevation array (omit to resolve from server cache)")
    height: int = Field(0, description="Grid height in pixels")
    width: int = Field(0, description="Grid width in pixels")
    bbox: dict[str, float] | None = Field(
        None, description="Bounding box for DEM cache lookup")
    dem: dict[str, Any] | None = Field(
        None, description="DEM settings for cache lookup (dim, projection, etc.)")
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
    filename: str | None = None
    message: str | None = None


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
    last_cleared: float | None = None
    cache_dirs: list[dict[str, Any]]


class CacheClearResponse(BaseModel):
    status: str
    cleared: list[CacheDirInfo]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class ProjectionInfo(BaseModel):
    id: str
    name: str
    description: str


class ProjectionsResponse(BaseModel):
    projections: list[ProjectionInfo]


class ColormapInfo(BaseModel):
    id: str
    description: str | None = None


class ColormapsResponse(BaseModel):
    colormaps: list[ColormapInfo]


class DatasetInfo(BaseModel):
    id: str
    name: str
    description: str
    source: str | None = None
    requires_auth: bool = False


class DatasetsResponse(BaseModel):
    datasets: list[DatasetInfo]


# Legacy alias kept so existing water-mask handler can still be used as body model
class Region(BoundingBox):
    sat_scale: int | None = None
    dim: int | None = None
    target_width: int | None = None
    target_height: int | None = None


# ---------------------------------------------------------------------------
# DEM Merge / Composite
# ---------------------------------------------------------------------------

class ProcessingSpec(BaseModel):
    """Per-layer image processing before blending."""
    smooth_sigma: float = 0.0
    sharpen: bool = False
    clip_min: float | None = None
    clip_max: float | None = None
    normalize: bool = False
    invert: bool = False
    extract_rivers: bool = False
    river_max_width_px: int = 8


class MergeLayerSpec(BaseModel):
    """One layer in the merge stack.

    ``source`` is either a built-in geo2stl source ("local", "h5_local",
    "water_esa", an OpenTopography dataset key) or a name the server has
    registered through ``geo2stl.dem.register_layer_source`` -
    "osm_buildings", "osm_roads", "osm_waterways", "osm_walls".

    ``add`` and ``rivers`` are the additive and subtractive pair: a layer
    that raises the terrain adds ``layer * weight``, one that cuts into it
    subtracts the same quantity.

    ``options`` is a free-form parameter bag passed to a registered source
    for anything a scalar weight cannot express - the OSM detail tier, or
    a land-cover class-to-height table.
    """
    source: str = "local"
    dim: int = Field(600, ge=50, le=2000)
    blend_mode: Literal["base", "replace", "add",
                        "blend", "rivers", "max", "min"] = "base"
    weight: float = Field(1.0, ge=0.0, le=100.0)
    processing: ProcessingSpec = Field(default_factory=ProcessingSpec)
    options: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class MergeRequest(BaseModel):
    """Request body for POST /api/composite/dem-merge.

    The projection fields mirror every other raster layer request, so a
    composite lands on the same grid as the DEM it is meant to replace.
    """
    bbox: dict[str, float]
    dim: int = Field(600, ge=50, le=2000)
    layers: list[MergeLayerSpec]
    projection: str = "none"
    clip_valid_region: bool | None = None
    clip_nans: bool = True
    maintain_dimensions: bool = False


class HydrologyMergeRequest(BaseModel):
    """Request body for POST /api/composite/hydrology-merge.

    Both arrays can be omitted — the endpoint resolves them from the
    server-side DEM and hydrology caches when bbox + dem settings are
    provided instead.
    """
    dem_values: list[float] | None = None
    dem_dimensions: Annotated[list[int], Field(min_length=2, max_length=2)] | None = None
    river_grid_values: list[float] | None = None
    river_grid_dimensions: Annotated[list[int], Field(min_length=2, max_length=2)] | None = None
    # Settings-only mode fields
    bbox: dict[str, float] | None = None
    dem: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Mesh import (STL/OBJ layer)
# ---------------------------------------------------------------------------

class MeshUploadResponse(BaseModel):
    """Response for POST /api/layers/mesh/upload."""
    upload_id: str
    filename: str
    size_bytes: int
    format: Literal["stl", "obj"]


class MeshHeightmapRequest(BoundingBox):
    """Request body for POST /api/layers/mesh/{upload_id}/heightmap."""
    resolution_m: float = Field(5.0, gt=0, le=100,
                                description="Target pixel resolution in metres")
    up_axis: Literal["x", "y", "z", "-x", "-y", "-z"] = Field(
        "z", description="Mesh axis that represents vertical height")
    infill: Literal["none", "idw", "nearest"] = Field(
        "none", description="Gap-fill method for NaN cells with no ray hit")


class MeshHeightmapResponse(BaseModel):
    """Response for POST /api/layers/mesh/{upload_id}/heightmap."""
    mesh_values_b64: str = Field(
        ..., description="Base64 little-endian float32 heightmap, row-major")
    mesh_mask_b64: str = Field(
        ..., description="Base64 packed bool mask (1 byte/px, 1=valid), row-major")
    dimensions: Annotated[list[int], Field(min_length=2, max_length=2)]
    min_elevation: float
    max_elevation: float
    valid_pct: float
    bbox: Annotated[list[float], Field(min_length=4, max_length=4)]


class MeshPointPair(BaseModel):
    """One matched point pair from the manual registration picker.

    Coordinates are pixel offsets in each canvas's own *native* (unzoomed)
    pixel space — the client normalizes out any pan/zoom before sending.
    """
    ref_x: float
    ref_y: float
    mesh_x: float
    mesh_y: float


class MeshRegisterRequest(BaseModel):
    """Request body for POST /api/layers/mesh/{upload_id}/register."""
    point_pairs: list[MeshPointPair] = Field(..., min_length=3)
    ref_width: int = Field(..., gt=0)
    ref_height: int = Field(..., gt=0)
    mesh_width: int = Field(..., gt=0)
    mesh_height: int = Field(..., gt=0)


class MeshRegisterResponse(BaseModel):
    """Response for POST /api/layers/mesh/{upload_id}/register."""
    mesh_values_b64: str = Field(
        ..., description="Warped heightmap, resampled onto the reference (ref_width x ref_height) grid")
    mesh_mask_b64: str
    dimensions: Annotated[list[int], Field(min_length=2, max_length=2)]
    min_elevation: float
    max_elevation: float
    rms_residual_px: float
    per_pair_residuals_px: list[float]
    affine: Annotated[list[float], Field(min_length=6, max_length=6)] = Field(
        ..., description="2x3 affine matrix, row-major [a,b,tx,c,d,ty]")


class MeshLibrarySetLocationRequest(BoundingBox):
    """Request body for POST /api/layers/mesh/library/{rel_path:path}/location."""
    up_axis: Literal["x", "y", "z", "-x", "-y", "-z"] = "z"
    notes: str = ""
    apply_to_city: bool = Field(
        True, description="Also apply this bbox to sibling files in the same city folder")


class MeshLibraryHeightmapRequest(BoundingBox):
    """Request body for POST /api/layers/mesh/library/{rel_path:path}/heightmap."""
    resolution_m: float = Field(5.0, gt=0, le=100)
    up_axis: Literal["x", "y", "z", "-x", "-y", "-z"] = "z"
    infill: Literal["none", "idw", "nearest"] = "none"


class MeshAutoRegisterRequest(BaseModel):
    """Request body for POST /api/layers/mesh/{upload_id}/auto-register and
    the library equivalent."""
    filename_hint: str | None = Field(
        None, description="Override the name used to derive a city — defaults to "
                          "the upload's original filename or the library rel_path")
    resolution: int = Field(512, ge=128, le=2048,
                            description="OSM building-heightmap raster resolution")
    min_region_iou: float = Field(
        0.5, ge=0, le=1,
        description="Minimum bbox IoU against a saved region to reuse it instead of creating a new one")


class MeshAutoRegisterResponse(BaseModel):
    """Response for the auto-register routes."""
    status: Literal["ok", "geocode_failed", "unavailable"]
    city_name: str | None = None
    bbox: dict[str, float] | None = None
    confidence: float | None = None
    footprint_iou: float | None = None
    rmse_m: float | None = None
    scale: float | None = None
    angle_deg: float | None = None
    region: dict[str, object] | None = Field(
        None, description="{name, created, iou} — the matched or newly created region")
    infill: Literal["none", "idw", "nearest"] = "none"
