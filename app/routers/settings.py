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

# ---------------------------------------------------------------------------
# Schema imports
# ---------------------------------------------------------------------------
try:
    from schemas import ColormapInfo, DatasetInfo, ProjectionInfo
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
    try:
        import sys
        from pathlib import Path as _Path
        _strm2stl = _Path(__file__).parent.parent.parent
        sys.path.append(str(_strm2stl))
        from geo2stl.projections import get_projection_info
        info = get_projection_info()
        projections = [ProjectionInfo(id=k, name=v.get("name", k), description=v.get("description", "")).model_dump()
                       for k, v in info.items()]
    except Exception:
        projections = [
            ProjectionInfo(id="none", name="None", description="No projection applied").model_dump(),
            ProjectionInfo(id="cosine", name="Cosine", description="Cosine latitude correction").model_dump(),
        ]
    return JSONResponse(content={"projections": projections})


@router.get("/api/settings/colormaps")
async def list_colormaps():
    """Return available colormaps for DEM rendering."""
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
    return JSONResponse(content={"colormaps": [c.model_dump() for c in colormaps]})


@router.get("/api/settings/datasets")
async def list_datasets():
    """Return available elevation and land-cover datasets."""
    datasets = [
        DatasetInfo(id="esa",       name="ESA WorldCover 2020",       description="10 m land cover classification",      source="ESA/WorldCover/v100/2020",          requires_auth=True),
        DatasetInfo(id="copernicus",name="Copernicus DEM GLO-30",     description="30 m global elevation model",         source="COPERNICUS/DEM/GLO30",              requires_auth=True),
        DatasetInfo(id="nasadem",   name="NASA SRTM / NASADEM",       description="30 m void-filled SRTM elevation",     source="NASA/NASADEM_HGT/001",             requires_auth=True),
        DatasetInfo(id="usgs",      name="USGS 3DEP 10 m",            description="10 m elevation (CONUS only)",         source="USGS/3DEP/10m",                    requires_auth=True),
        DatasetInfo(id="gebco",     name="GEBCO 2022",                 description="450 m global ocean bathymetry + land",source="Local GEBCO GeoTIFFs",             requires_auth=False),
        DatasetInfo(id="jrc",       name="JRC Global Surface Water",   description="Water occurrence 1984–2021",          source="JRC/GSW1_4/GlobalSurfaceWater",    requires_auth=True),
    ]
    return JSONResponse(content={"datasets": [d.model_dump() for d in datasets]})
