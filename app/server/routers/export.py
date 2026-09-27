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

from app.server.core.export import (  # noqa: E402
    generate_3mf,
    generate_crosssection,
    generate_mesh_preview,
    generate_obj,
    generate_stl,
)
from app.server.core.export_tasks import (  # noqa: E402
    get_task_file,
    get_task_status,
    start_export_task,
)
from app.server.core.preflight import preflight  # noqa: E402


async def _off_loop(fn, data):
    """Run a mesh build in the thread pool. A dim-1200 export is ~17 s of CPU;
    run inline it froze every other request, the progress polls included."""
    import asyncio
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, fn, data)
    except ValueError as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=400)


@router.post("/api/export/stl")
async def export_stl(request: Request):
    """Generate and download an STL file from DEM data."""
    return await _off_loop(generate_stl, await request.json())


@router.post("/api/export/obj")
async def export_obj(request: Request):
    """Generate and download an OBJ file from DEM data."""
    return await _off_loop(generate_obj, await request.json())


@router.post("/api/export/3mf")
async def export_3mf(request: Request):
    """Generate and download a 3MF file from DEM data."""
    return await _off_loop(generate_3mf, await request.json())


@router.post("/api/export/preview")
async def export_preview(request: Request):
    """Return numpy2stl vertices+faces as JSON for the in-browser 3D viewer."""
    return await _off_loop(generate_mesh_preview, await request.json())


@router.post("/api/export/preflight")
async def export_preflight(request: Request):
    """Fast estimate of a city / puzzle / terrain export before building it:
    size vs bed, scale, pieces, per-layer counts, faces, filament, time, warnings."""
    result = await _off_loop(preflight, await request.json())
    if isinstance(result, JSONResponse):   # a ValueError, already a 400
        return result
    return JSONResponse(content=result)


@router.post("/api/export/crosssection")
async def export_crosssection(request: Request):
    """Generate a cross-section STL along a chosen latitude or longitude line."""
    return await _off_loop(generate_crosssection, await request.json())


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
    if fmt not in ("stl", "obj", "3mf", "puzzle", "city"):
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
