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

from core.export import (
    generate_stl,
    generate_obj,
    generate_3mf,
    generate_crosssection,
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


@router.post("/api/export/crosssection")
async def export_crosssection(request: Request):
    """Generate a cross-section STL along a chosen latitude or longitude line."""
    data = await request.json()
    return generate_crosssection(data)
