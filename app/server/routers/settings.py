"""
routers/settings.py — /api/settings/* endpoints.

Extracted from location_picker.py (backend refactor, step 6).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])


def _default_region_settings() -> dict:
    """Return the browser client default grouped settings payload."""
    return {
        "dem": {
            "dim": 600,
            "depth_scale": 0.5,
            "water_scale": 0.05,
            "subtract_water": True,
            "dem_source": "local",
            "show_sat": False,
        },
        "projection": {
            "projection": "none",
            "clip_nans": True,
        },
        "view": {
            "colormap": "terrain",
            "rescale_min": None,
            "rescale_max": None,
            "gridlines_show": True,
            "gridlines_count": 10,
            "elevation_curve": None,
            "elevation_curve_points": [],
            "elevation_curve_vmin": None,
            "elevation_curve_vmax": None,
        },
        "water": {
            "dim": 600,
            "dataset": "esa",
        },
        "esa": {
            "dim": 600,
        },
        "satellite": {
            "dim": 600,
        },
        "export": {
            "model_height": 30.0,
            "base_height": 10.0,
            "exaggeration": 1.0,
            "sea_level_cap": False,
            "floor_val": 0.0,
            "engrave_label": False,
            "label_text": "",
            "contours": False,
            "contour_interval": 100.0,
            "contour_style": "engraved",
            "puzzle_z": None,
        },
        "split": {
            "split_rows": 4,
            "split_cols": 4,
            "puzzle_m": 50,
            "puzzle_base_n": 10,
            "border_height": 1.0,
            "border_offset": 5.0,
            "include_border": True,
        },
        "city": {
            "layers": ["buildings", "roads", "waterways"],
            "simplify_tolerance": 0.5,
            "min_area": 5.0,
            "building_scale": 0.5,
            "road_depression_m": 0.0,
            "water_depression_m": -2.0,
            "simplify_terrain": True,
        },
        "hydrology": {
            "source": "hydrorivers",
            "width_factor": 0.5,
            "scale_m": 10,
            "depression_m": -5.0,
            "min_order": 3,
            "order_exponent": 1.5,
        },
    }


def _model_to_dict(model) -> dict:
    """Serialize Pydantic model instances for both v1 and v2."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _default_projections() -> list[dict]:
    return [
        _model_to_dict(
            ProjectionInfo(
                id="none",
                name="None",
                description="No projection applied",
            )
        ),
        _model_to_dict(
            ProjectionInfo(
                id="cosine",
                name="Cosine",
                description="Cosine latitude correction",
            )
        ),
    ]


def _load_projections() -> list[dict]:
    """Load projections from geo2stl; fall back to defaults on failure."""
    try:
        import sys
        from pathlib import Path as _Path

        strm2stl_root = _Path(__file__).parent.parent.parent
        root_str = str(strm2stl_root)
        if root_str not in sys.path:
            sys.path.append(root_str)

        from geo2stl.projections import get_projection_info

        info = get_projection_info()
        return [
            _model_to_dict(
                ProjectionInfo(
                    id=projection_id,
                    name=projection_meta.get("name", projection_id),
                    description=projection_meta.get("description", ""),
                )
            )
            for projection_id, projection_meta in info.items()
        ]
    except Exception:
        logger.exception("Failed to load dynamic projections; using defaults")
        return _default_projections()


def _list_colormaps() -> list[dict]:
    colormaps = [
        ColormapInfo(
            id="terrain",  description="Classic green-brown-white terrain"),
        ColormapInfo(
            id="viridis",  description="Perceptually uniform, colorblind-safe"),
        ColormapInfo(id="plasma",   description="High-contrast warm gradient"),
        ColormapInfo(
            id="magma",    description="Dark background, bright peaks"),
        ColormapInfo(
            id="inferno",  description="Black-to-yellow fire gradient"),
        ColormapInfo(id="cividis",  description="Colorblind-safe blue-yellow"),
        ColormapInfo(id="gray",     description="Grayscale hillshade"),
        ColormapInfo(id="ocean",    description="Blue depth gradient"),
        ColormapInfo(id="hot",      description="Black-red-yellow-white"),
        ColormapInfo(
            id="RdBu",     description="Diverging red-blue for anomaly maps"),
    ]
    return [_model_to_dict(c) for c in colormaps]


def _list_datasets() -> list[dict]:
    datasets = [
        DatasetInfo(id="esa",       name="ESA WorldCover 2020",       description="10 m land cover classification",
                    source="ESA/WorldCover/v100/2020",          requires_auth=True),
        DatasetInfo(id="copernicus", name="Copernicus DEM GLO-30",     description="30 m global elevation model",
                    source="COPERNICUS/DEM/GLO30",              requires_auth=True),
        DatasetInfo(id="nasadem",   name="NASA SRTM / NASADEM",       description="30 m void-filled SRTM elevation",
                    source="NASA/NASADEM_HGT/001",             requires_auth=True),
        DatasetInfo(id="usgs",      name="USGS 3DEP 10 m",            description="10 m elevation (CONUS only)",
                    source="USGS/3DEP/10m",                    requires_auth=True),
        DatasetInfo(id="gebco",     name="GEBCO 2022",                 description="450 m global ocean bathymetry + land",
                    source="Local GEBCO GeoTIFFs",             requires_auth=False),
        DatasetInfo(id="jrc",       name="JRC Global Surface Water",   description="Water occurrence 1984–2021",
                    source="JRC/GSW1_4/GlobalSurfaceWater",    requires_auth=True),
    ]
    return [_model_to_dict(d) for d in datasets]


# ---------------------------------------------------------------------------
# Schema imports
# ---------------------------------------------------------------------------
try:
    from app.server.schemas import ColormapInfo, DatasetInfo, ProjectionInfo
except ImportError:
    from pydantic import BaseModel
    from typing import Optional

    class ColormapInfo(BaseModel):
        id: str
        description: Optional[str] = None

    class DatasetInfo(BaseModel):
        id: str
        name: str
        description: str
        source: Optional[str] = None
        requires_auth: bool = False

    class ProjectionInfo(BaseModel):
        id: str
        name: str
        description: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/settings/projections")
async def list_projections():
    """Return available map projections."""
    return JSONResponse(content={"projections": _load_projections()})


@router.get("/api/settings/colormaps")
async def list_colormaps():
    """Return available colormaps for DEM rendering."""
    return JSONResponse(content={"colormaps": _list_colormaps()})


@router.get("/api/settings/datasets")
async def list_datasets():
    """Return available elevation and land-cover datasets."""
    return JSONResponse(content={"datasets": _list_datasets()})


@router.get("/api/settings/default")
async def get_default_settings():
    """Return the canonical grouped browser-client settings defaults."""
    return JSONResponse(content={"settings": _default_region_settings()})


@router.get("/api/settings")
async def get_all_settings():
    """Combined settings endpoint for SDK initialization.

    Returns all available configuration at once (projections, colormaps, datasets).
    This is a convenience endpoint for clients that need all settings during startup.
    Fine-grained clients should use the individual /api/settings/* endpoints instead.
    """
    return JSONResponse(content={
        "projections": _load_projections(),
        "colormaps": _list_colormaps(),
        "datasets": _list_datasets(),
    })
