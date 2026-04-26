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


def _model_to_dict(model) -> dict:
    """Return a plain dict for Pydantic v1/v2 model instances."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _default_projections() -> list[dict]:
    return [
        _model_to_dict(ProjectionInfo(
            id="none",
            name="None",
            description="No projection applied",
        )),
        _model_to_dict(ProjectionInfo(
            id="cosine",
            name="Cosine",
            description="Cosine latitude correction",
        )),
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
            _model_to_dict(ProjectionInfo(
                id=projection_id,
                name=projection_meta.get("name", projection_id),
                description=projection_meta.get("description", ""),
            ))
            for projection_id, projection_meta in info.items()
        ]
    except Exception:
        logger.exception("Failed to load dynamic projections; using defaults")
        return _default_projections()


def _list_colormaps() -> list[dict]:
    colormaps = [
        ColormapInfo(id="terrain",  description="Classic green-brown-white terrain"),
        ColormapInfo(id="viridis",  description="Perceptually uniform, colorblind-safe"),
        ColormapInfo(id="plasma",   description="High-contrast warm gradient"),
        ColormapInfo(id="magma",    description="Dark background, bright peaks"),
        ColormapInfo(id="inferno",  description="Black-to-yellow fire gradient"),
        ColormapInfo(id="cividis",  description="Colorblind-safe blue-yellow"),
        ColormapInfo(id="gray",     description="Grayscale hillshade"),
        ColormapInfo(id="ocean",    description="Blue depth gradient"),
        ColormapInfo(id="hot",      description="Black-red-yellow-white"),
        ColormapInfo(id="RdBu",     description="Diverging red-blue for anomaly maps"),
    ]
    return [_model_to_dict(c) for c in colormaps]


def _list_datasets() -> list[dict]:
    datasets = [
        DatasetInfo(id="esa",       name="ESA WorldCover 2020",       description="10 m land cover classification",      source="ESA/WorldCover/v100/2020",          requires_auth=True),
        DatasetInfo(id="copernicus",name="Copernicus DEM GLO-30",     description="30 m global elevation model",         source="COPERNICUS/DEM/GLO30",              requires_auth=True),
        DatasetInfo(id="nasadem",   name="NASA SRTM / NASADEM",       description="30 m void-filled SRTM elevation",     source="NASA/NASADEM_HGT/001",             requires_auth=True),
        DatasetInfo(id="usgs",      name="USGS 3DEP 10 m",            description="10 m elevation (CONUS only)",         source="USGS/3DEP/10m",                    requires_auth=True),
        DatasetInfo(id="gebco",     name="GEBCO 2022",                 description="450 m global ocean bathymetry + land",source="Local GEBCO GeoTIFFs",             requires_auth=False),
        DatasetInfo(id="jrc",       name="JRC Global Surface Water",   description="Water occurrence 1984–2021",          source="JRC/GSW1_4/GlobalSurfaceWater",    requires_auth=True),
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
