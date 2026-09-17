"""Request and response shapes.

Two rules distinguish these from v1's:

Every field is declared and validated. v1's DEM route accepted GET and POST, declared a
FastAPI signature, and then ignored it in favour of parsing request.query_params by hand —
so the declared types were documentation that the code did not honour. Here the declaration
is the parser.

The settings blob is versioned. v1's was not, and was never migrated, so two renames that
happened years ago are still visible in live data and are guessed at read time by the
client. v2 stamps SETTINGS_SCHEMA_VERSION on every blob it writes and migrates on read, in
one place, on the server.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .config import MAX_BBOX_SPAN_DEG, MAX_DIM

SETTINGS_SCHEMA_VERSION = 1

ExportFormat = Literal["stl", "obj", "3mf"]


class BBox(BaseModel):
    north: float = Field(..., ge=-90, le=90)
    south: float = Field(..., ge=-90, le=90)
    east: float = Field(..., ge=-180, le=180)
    west: float = Field(..., ge=-180, le=180)

    @model_validator(mode="after")
    def _check(self) -> BBox:
        if self.north <= self.south:
            raise ValueError("north must be greater than south")
        if self.east <= self.west:
            raise ValueError("east must be greater than west")
        return self

    @property
    def span_deg(self) -> float:
        return max(self.north - self.south, self.east - self.west)

    def as_dict(self) -> dict[str, float]:
        return {"north": self.north, "south": self.south, "east": self.east, "west": self.west}


class DemSettings(BaseModel):
    """What to fetch, and how to grid it. Everything here changes the array."""

    source: str = "local"
    dim: int = Field(600, ge=16, le=MAX_DIM)
    depthScale: float = Field(0.5, ge=0.0, le=10.0)
    waterScale: float = Field(0.05, ge=0.0, le=10.0)
    subtractWater: bool = True
    maintainDimensions: bool = False


class ProjectionSettings(BaseModel):
    """Applied after the fetch, so changing it never refetches."""

    name: str = "none"
    clipValidRegion: bool = False


class OverlaySettings(BaseModel):
    """Preview-only imagery. None of it reaches the mesh."""

    satellite: bool = False
    waterMask: bool = False
    landCover: bool = False


class ModelSettings(BaseModel):
    """How the elevation grid becomes printable millimetres."""

    modelHeight: float = Field(25.0, gt=0, le=500)
    baseHeight: float = Field(5.0, ge=0, le=200)
    exaggeration: float = Field(1.0, gt=0, le=50)
    mmPerPixel: float = Field(1.0, gt=0, le=10)
    # Clamp sub-sea elevations up to zero, so an ocean floor renders as a flat sea rather
    # than a trench. v1's tooltip promised this and its code did the opposite
    # (np.minimum instead of np.maximum), which flattened the land instead.
    seaLevelCap: bool = False


class ExportSettings(BaseModel):
    format: ExportFormat = "stl"
    engraveLabel: bool = False
    labelText: str = ""


class RegionSettings(BaseModel):
    """The whole saved state of a region. One object, saved and loaded whole."""

    schemaVersion: int = SETTINGS_SCHEMA_VERSION
    dem: DemSettings = DemSettings()
    projection: ProjectionSettings = ProjectionSettings()
    overlays: OverlaySettings = OverlaySettings()
    model: ModelSettings = ModelSettings()
    export: ExportSettings = ExportSettings()


class DemRequest(BaseModel):
    bbox: BBox
    dem: DemSettings = DemSettings()
    projection: ProjectionSettings = ProjectionSettings()

    @model_validator(mode="after")
    def _within_limits(self) -> DemRequest:
        # The span cap belongs on the fetch, not on the box. Saved regions predate any
        # limit v2 sets and must stay listable and inspectable even when they are too
        # large to render; refusing to show them would be v2 hiding v1's data.
        if self.bbox.span_deg > MAX_BBOX_SPAN_DEG:
            raise ValueError(
                f"That region spans {self.bbox.span_deg:.1f} degrees, over the "
                f"{MAX_BBOX_SPAN_DEG:.0f} degree fetch limit. Draw a smaller area."
            )
        return self


class OverlayRequest(BaseModel):
    bbox: BBox
    kind: Literal["satellite", "waterMask", "landCover"]
    dim: int = Field(600, ge=16, le=MAX_DIM)


class ExportRequest(BaseModel):
    """Note what is absent: the bounding box and the DEM settings.

    The server already knows both — they are attached to demId. Resending them is what let
    v1's two halves disagree.
    """

    demId: str
    model: ModelSettings = ModelSettings()
    export: ExportSettings = ExportSettings()
    name: str = "terrain"


class Region(BaseModel):
    name: str
    label: str | None = None
    description: str | None = None
    bbox: BBox
