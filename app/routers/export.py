"""
routers/export.py — /api/export/* endpoints.

Extracted from location_picker.py (backend refactor, step 6).
Delegates all generation logic to core/export.py.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["export"])

from app.core.export import (
    generate_stl,
    generate_obj,
    generate_3mf,
    generate_crosssection,
    generate_mesh_preview,
)


@router.post("/api/export/stl")
async def export_stl(request: Request):
    """Generate and download an STL file from DEM data."""
    data = await request.json()
    return generate_stl(data)


@router.post("/api/export/obj")
async def export_obj(request: Request):
    """Generate and download an OBJ file from DEM data."""
    data = await request.json()
    return generate_obj(data)


@router.post("/api/export/3mf")
async def export_3mf(request: Request):
    """Generate and download a 3MF file from DEM data."""
    data = await request.json()
    return generate_3mf(data)


@router.post("/api/export/preview")
async def export_preview(request: Request):
    """Return numpy2stl vertices+faces as JSON for the in-browser 3D viewer."""
    import asyncio
    data = await request.json()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, generate_mesh_preview, data)


@router.post("/api/export/crosssection")
async def export_crosssection(request: Request):
    """Generate a cross-section STL along a chosen latitude or longitude line."""
    data = await request.json()
    return generate_crosssection(data)
