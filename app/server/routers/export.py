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

from app.server.core.export import (
    generate_stl,
    generate_obj,
    generate_3mf,
    generate_crosssection,
    generate_mesh_preview,
    generate_puzzle_3mf,
    start_export_task,
    get_task_status,
    get_task_file,
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


@router.post("/api/export/puzzle")
async def export_puzzle(request: Request):
    """Start an async puzzle 3MF export. Returns {task_id} for polling."""
    data = await request.json()
    task_id = start_export_task(data, "puzzle")
    return JSONResponse(content={"task_id": task_id})


# ---------------------------------------------------------------------------
# Async export with progress polling
# ---------------------------------------------------------------------------

@router.post("/api/export/start")
async def export_start(request: Request):
    """Start an async export task. Returns {task_id} for polling."""
    data = await request.json()
    fmt = data.pop("format", "stl")
    if fmt not in ("stl", "obj", "3mf", "puzzle"):
        return JSONResponse(content={"error": f"Unsupported format: {fmt}"}, status_code=400)
    task_id = start_export_task(data, fmt)
    return JSONResponse(content={"task_id": task_id})


@router.get("/api/export/status/{task_id}")
async def export_status(task_id: str):
    """Poll progress of an async export task."""
    status = get_task_status(task_id)
    if status is None:
        return JSONResponse(content={"error": "Task not found"}, status_code=404)
    return JSONResponse(content=status)


@router.get("/api/export/download/{task_id}")
async def export_download(task_id: str):
    """Download the result file of a completed export task."""
    response = get_task_file(task_id)
    if response is None:
        return JSONResponse(content={"error": "Task not found or not complete"}, status_code=404)
    return response
