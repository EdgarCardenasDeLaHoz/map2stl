"""
schemas.py — All Pydantic request/response models for the map2stl API.

Routers import their models from here.
"""

from __future__ import annotations

from typing import Any, Literal

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


class LandmarksRequest(BaseModel):
    """Request body for POST /api/cities/landmarks (F-LANDMARK §5)."""
    buildings: dict[str, Any] = Field(..., description="Buildings GeoJSON FeatureCollection "
                                      "(the loaded city data)")
    tallest_n: int = Field(10, ge=0, le=100, description="Also list the N tallest other buildings")
    region: str | None = Field(None, description="Region whose stored overrides to return")


class LandmarkPreviewRequest(BaseModel):
    """Request body for POST /api/cities/landmarks/preview."""
    buildings: dict[str, Any] = Field(..., description="Buildings FeatureCollection holding the "
                                      "landmark (at least its outline and parts)")
    osm_id: str = Field(..., description='The landmark\'s OSM id, e.g. "way/123"')
    override: dict[str, Any] | None = Field(
        None, description='{"kind": "osm"|"mesh"|"ndsm", ...} (city2stl.landmarks); '
                          'omitted = as OSM tags build it')


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
    clip_valid_region: bool = Field(
        True,
        description="Clip projection padding to the valid data extent.",
    )
    maintain_dimensions: bool = Field(
        False,
        description="Keep input grid shape after projection (legacy/opt-in). "
                    "Default False: output reflects the projection's true aspect ratio.",
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class ProjectionInfo(BaseModel):
    id: str
    name: str
    description: str


class ColormapInfo(BaseModel):
    id: str
    description: str | None = None


class DatasetInfo(BaseModel):
    id: str
    name: str
    description: str
    source: str | None = None
    requires_auth: bool = False


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
    "osm_buildings", "osm_roads", "osm_waterways", "osm_walls", and the
    terrain-relative water sources "hydrorivers", "natural_earth_rivers",
    "lakes" (negative metres below the ground; use ``add``).

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
    clip_valid_region: bool = True
    maintain_dimensions: bool = False


# ---------------------------------------------------------------------------
# Mesh import (STL/OBJ layer)
# ---------------------------------------------------------------------------

class MeshHeightmapRequest(BoundingBox):
    """Request body for POST /api/layers/mesh/{upload_id}/heightmap."""
    resolution_m: float = Field(5.0, gt=0, le=100,
                                description="Target pixel resolution in metres")
    up_axis: Literal["x", "y", "z", "-x", "-y", "-z"] = Field(
        "z", description="Mesh axis that represents vertical height")
    infill: Literal["none", "idw", "nearest"] = Field(
        "none", description="Gap-fill method for NaN cells with no ray hit")


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


class MeshLibrarySetLocationRequest(BoundingBox):
    """Request body for POST /api/layers/mesh/library/{rel_path:path}/location."""
    up_axis: Literal["x", "y", "z", "-x", "-y", "-z"] = "z"
    notes: str = ""
    apply_to_city: bool = Field(
        True, description="Also apply this bbox to sibling files in the same city folder")
    placement: dict[str, Any] | None = Field(
        None, description="Optional street-placement record (pack slug, centre, turn, "
                          "size, verdict) stored beside the bbox in the sidecar")


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
    write_report: bool = Field(
        True, description="Write the numpy2stl HTML report to a per-import folder under the "
                          "cache (browsable at /reports); ~10 s of matplotlib per call")


# ---------------------------------------------------------------------------
# Plate registration panel and model critic (F-REGION §5, F-LANDMARK §6)
# ---------------------------------------------------------------------------

class PlateRegistrationStartRequest(BaseModel):
    """Request body for POST /api/registration/plate/start."""
    slug: str = Field(..., min_length=1, max_length=120,
                      description="Align-tool pack (tools/align_tool/data/<slug>)")
    place: bool = Field(True, description="Run the street placement first; False scores "
                                          "the pack's current placement only")
    fix: bool = Field(True, description="Let the tile consensus move the placement onto "
                                        "the position the tiles agree on")
    rel_path: str | None = Field(None, description="Mesh-library file this run is for "
                                                    "(echoed back for the sidecar save)")


class CriticReference(BaseModel):
    """What a model is scored against."""
    kind: Literal["pack", "ndsm"] = "pack"
    slug: str | None = Field(None, description="kind=pack: align-tool pack slug")
    bbox: dict[str, float] | None = Field(None, description="kind=ndsm: {north,south,east,west}")
    resolution: int = Field(512, ge=64, le=2048, description="kind=ndsm: grid cells per side")


class CriticModel(BaseModel):
    """The generated model being scored."""
    kind: Literal["buildings", "stl"] = "buildings"
    features: list[dict[str, Any]] | None = Field(
        None, description="kind=buildings: GeoJSON building features (properties.height_m)")
    upload_id: str | None = Field(None, description="kind=stl: id from /api/layers/mesh/upload")
    bbox: dict[str, float] | None = Field(None, description="kind=stl: the bbox the STL spans")
    up_axis: Literal["x", "y", "z", "-x", "-y", "-z"] = "z"
    m_per_unit: float | None = Field(None, gt=0, description="kind=stl: metres per model "
                                                             "unit; omitted = fitted")


class CriticScoreRequest(BaseModel):
    """Request body for POST /api/registration/critic/score."""
    reference: CriticReference
    model: CriticModel
