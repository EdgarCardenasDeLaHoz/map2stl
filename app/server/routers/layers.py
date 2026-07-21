"""
routers/layers.py — /api/layers/mesh/* STL/OBJ import + manual registration.

Upload an STL/OBJ mesh, convert it to a geo-referenced heightmap for a
chosen bbox, then register it against the reference view (current DEM/city)
via a manually-fitted 2D affine from clicked point pairs.

All heavy lifting is in core.mesh_import; this module is a thin HTTP adapter.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import numpy as np
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from app.server.core import mesh_import
from app.server.core.responses import error_response
from app.server.core.validation import b64_encode
from app.server.routers.regions import find_or_create_region_for_bbox
from app.server.schemas import (
    MeshAutoRegisterRequest,
    MeshHeightmapRequest,
    MeshLibraryHeightmapRequest,
    MeshLibrarySetLocationRequest,
    MeshRegisterRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["layers"])


def _b64_mask(mask: np.ndarray) -> str:
    """Encode a bool mask as base64 packed 1-byte-per-pixel (1=valid, 0=invalid)."""
    return base64.b64encode(mask.astype(np.uint8).ravel().tobytes()).decode("ascii")


def _heightmap_response(heightmap: np.ndarray, mask: np.ndarray, body) -> JSONResponse:
    h, w = heightmap.shape
    valid_pct = 100.0 * float(mask.sum()) / mask.size if mask.size else 0.0
    return JSONResponse(content={
        "mesh_values_b64": b64_encode(heightmap),
        "mesh_mask_b64": _b64_mask(mask),
        "dimensions": [h, w],
        "min_elevation": float(np.nanmin(heightmap)) if mask.any() else 0.0,
        "max_elevation": float(np.nanmax(heightmap)) if mask.any() else 0.0,
        "valid_pct": valid_pct,
        "bbox": [body.west, body.south, body.east, body.north],
    })


async def _register_response(
    cache: "tuple[np.ndarray, np.ndarray] | None",
    not_found_hint: str,
    body: MeshRegisterRequest,
) -> JSONResponse:
    """Shared fit + warp + response-building for both upload and library registration."""
    if cache is None:
        return error_response(
            f"No heightmap computed yet — call the heightmap endpoint before "
            f"registering. ({not_found_hint})", status=400)
    heightmap, mask = cache

    ref_points = np.array([[p.ref_x, p.ref_y] for p in body.point_pairs], dtype=np.float64)
    mesh_points = np.array([[p.mesh_x, p.mesh_y] for p in body.point_pairs], dtype=np.float64)

    try:
        loop = asyncio.get_running_loop()
        M = mesh_import.fit_affine_from_pairs(ref_points, mesh_points)
        resid = mesh_import.residuals_px(ref_points, mesh_points, M)
        warped, warped_mask = await loop.run_in_executor(
            None, mesh_import.register_heightmap,
            heightmap, mask, M, (body.ref_height, body.ref_width),
        )
    except mesh_import.MeshImportError as e:
        return error_response(str(e), status=400)
    except Exception as e:
        logger.exception("Mesh registration failed")
        return error_response(f"Registration failed: {e}", status=500)

    h, w = warped.shape
    rms = float(np.sqrt(np.mean(resid ** 2)))
    return JSONResponse(content={
        "mesh_values_b64": b64_encode(warped),
        "mesh_mask_b64": _b64_mask(warped_mask),
        "dimensions": [h, w],
        "min_elevation": float(np.nanmin(warped)) if warped_mask.any() else 0.0,
        "max_elevation": float(np.nanmax(warped)) if warped_mask.any() else 0.0,
        "rms_residual_px": rms,
        "per_pair_residuals_px": resid.tolist(),
        "affine": M.ravel().tolist(),
    })


@router.post("/api/layers/mesh/upload")
async def upload_mesh(file: UploadFile = File(...)):
    """Accept an STL/OBJ upload (<=200MB), store it, return an upload_id."""
    try:
        data = await file.read()
        upload_id, fmt, size_bytes = mesh_import.save_upload(file.filename or "mesh", data)
        return JSONResponse(content={
            "upload_id": upload_id,
            "filename": file.filename,
            "size_bytes": size_bytes,
            "format": fmt,
        })
    except mesh_import.MeshImportError as e:
        return error_response(str(e), status=400)
    except Exception as e:
        logger.exception("Mesh upload failed")
        return error_response(f"Upload failed: {e}", status=500)


@router.post("/api/layers/mesh/{upload_id}/heightmap")
async def mesh_heightmap(upload_id: str, body: MeshHeightmapRequest):
    """Convert the uploaded mesh to a heightmap for the given bbox."""
    bbox = {"north": body.north, "south": body.south,
            "east": body.east, "west": body.west}
    try:
        loop = asyncio.get_running_loop()
        heightmap, mask = await loop.run_in_executor(
            None, mesh_import.compute_heightmap,
            upload_id, bbox, body.resolution_m, body.up_axis, body.infill,
        )
    except mesh_import.MeshImportError as e:
        return error_response(str(e), status=400)
    except Exception as e:
        logger.exception("Mesh heightmap computation failed")
        return error_response(f"Heightmap computation failed: {e}", status=500)

    return _heightmap_response(heightmap, mask, body)


@router.post("/api/layers/mesh/{upload_id}/register")
async def mesh_register(upload_id: str, body: MeshRegisterRequest):
    """Fit a 2D affine from clicked point pairs and warp the mesh heightmap
    onto the reference canvas's pixel grid.

    body.point_pairs already has min_length=3 enforced at the schema level
    (Pydantic 422s below 3 pairs before this body runs).
    """
    try:
        cache = mesh_import.get_last_heightmap(upload_id)
    except mesh_import.MeshImportError as e:
        return error_response(str(e), status=400)
    return await _register_response(cache, f"upload_id={upload_id}", body)


@router.delete("/api/layers/mesh/{upload_id}")
async def delete_mesh(upload_id: str):
    """Remove a stored mesh upload."""
    mesh_import.delete_upload(upload_id)
    return JSONResponse(content={"deleted": upload_id})


def _auto_register_response(result: dict) -> JSONResponse:
    """Attach a matched/created region (when the geocode+registration
    succeeded) and build the HTTP response for an auto-register call."""
    if result["status"] == "ok" and result.get("bbox"):
        region = find_or_create_region_for_bbox(
            result["bbox"], label_hint=result["city_name"])
        result = {**result, "region": region}
    return JSONResponse(content=result)


@router.post("/api/layers/mesh/{upload_id}/auto-register")
async def mesh_auto_register(upload_id: str, body: MeshAutoRegisterRequest):
    """Attempt automatic geocode + OSM registration for an uploaded mesh,
    then match/create a saved region for its bbox.

    Always succeeds at the HTTP level (200) even when the underlying
    geocode/registration fails or is unavailable — the response `status`
    field tells the caller what happened. This is a starting point for the
    manual point-pair picker, not a final answer: the client is expected to
    show the confidence/quality metrics and let the user confirm or refine.
    """
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, mesh_import.auto_register_upload,
            upload_id, body.resolution, body.filename_hint,
        )
    except mesh_import.MeshImportError as e:
        return error_response(str(e), status=400)
    except Exception as e:
        logger.exception("Mesh auto-register failed")
        return error_response(f"Auto-register failed: {e}", status=500)

    return _auto_register_response(result)


# ---------------------------------------------------------------------------
# Mesh library — pre-existing STL/OBJ sets on disk (config.MICROPOLITAN_STL_DIR)
# ---------------------------------------------------------------------------

@router.get("/api/layers/mesh/library")
async def list_mesh_library():
    """List STL/OBJ files under the configured mesh library dir, grouped by city."""
    try:
        cities = mesh_import.list_library()
        return JSONResponse(content={"cities": cities})
    except mesh_import.MeshImportError as e:
        return error_response(str(e), status=404)


@router.post("/api/layers/mesh/library/{rel_path:path}/location")
async def set_mesh_library_location(rel_path: str, body: MeshLibrarySetLocationRequest):
    """Persist a bbox for a library mesh file (and, by default, its city siblings)."""
    bbox = {"north": body.north, "south": body.south,
            "east": body.east, "west": body.west}
    try:
        updated = mesh_import.set_library_location(
            rel_path, bbox, up_axis=body.up_axis, notes=body.notes,
            apply_to_city=body.apply_to_city,
        )
        return JSONResponse(content={"updated": updated})
    except mesh_import.MeshImportError as e:
        return error_response(str(e), status=400)


@router.post("/api/layers/mesh/library/{rel_path:path}/heightmap")
async def library_mesh_heightmap(rel_path: str, body: MeshLibraryHeightmapRequest):
    """Compute (or fetch cached) heightmap for a library mesh file + bbox."""
    bbox = {"north": body.north, "south": body.south,
            "east": body.east, "west": body.west}
    try:
        loop = asyncio.get_running_loop()
        heightmap, mask = await loop.run_in_executor(
            None, mesh_import.compute_library_heightmap,
            rel_path, bbox, body.resolution_m, body.up_axis, body.infill,
        )
    except mesh_import.MeshImportError as e:
        return error_response(str(e), status=400)
    except Exception as e:
        logger.exception("Library mesh heightmap computation failed")
        return error_response(f"Heightmap computation failed: {e}", status=500)

    return _heightmap_response(heightmap, mask, body)


@router.post("/api/layers/mesh/library/{rel_path:path}/register")
async def library_mesh_register(rel_path: str, body: MeshRegisterRequest):
    """Fit a 2D affine from clicked point pairs and warp the library mesh's
    most recently computed heightmap onto the reference canvas's pixel grid."""
    cache = mesh_import.get_last_library_heightmap(rel_path)
    return await _register_response(cache, f"rel_path={rel_path}", body)


@router.post("/api/layers/mesh/library/{rel_path:path}/auto-register")
async def library_mesh_auto_register(rel_path: str, body: MeshAutoRegisterRequest):
    """Attempt automatic geocode + OSM registration for a library mesh file,
    then match/create a saved region for its bbox. See mesh_auto_register's
    docstring — always 200s, `status` field reports success/failure."""
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, mesh_import.auto_register_library,
            rel_path, body.resolution, body.filename_hint,
        )
    except mesh_import.MeshImportError as e:
        return error_response(str(e), status=400)
    except Exception as e:
        logger.exception("Library mesh auto-register failed")
        return error_response(f"Auto-register failed: {e}", status=500)

    return _auto_register_response(result)
