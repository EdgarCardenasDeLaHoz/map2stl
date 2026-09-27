"""
routers/registration.py — /api/registration/*: plate registration tasks and the model critic.

F-REGION §5 / F-LANDMARK §6.  A thin HTTP adapter over

* ``core/plate_registration.py`` — street placement + tile-consensus verdict for an
  align-tool pack, as a background task the client polls;
* ``city2stl.registration.critic`` — score a generated model (the current city-model
  buildings, or an uploaded STL) against a registered plate pack or a 3DEP lidar nDSM.

Routes
------
GET  /api/registration/packs                      packs, each with window + state
GET  /api/registration/match?rel_path=...          pack for a mesh-library file
POST /api/registration/plate/start                 start (or join) a task -> snapshot
GET  /api/registration/plate/status/{task_id}      snapshot (result when done)
POST /api/registration/plate/cancel/{task_id}      mark cancelled
GET  /api/registration/critic/references?n&s&e&w   packs overlapping a bbox, best first
POST /api/registration/critic/score                score one model against one reference
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.server.core import plate_registration
from app.server.core.responses import error_response
from app.server.schemas import CriticScoreRequest, PlateRegistrationStartRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["registration"])


@router.get("/api/registration/packs")
async def registration_packs():
    """Align-tool packs that can be placed (street placement) or scored (tile consensus)."""
    return JSONResponse({"packs": plate_registration.list_packs()})


@router.get("/api/registration/match")
async def registration_match(rel_path: str = Query(..., min_length=1)):
    """The pack a mesh-library file depicts (by city name), or null."""
    return JSONResponse({"rel_path": rel_path,
                         "slug": plate_registration.pack_for_library_path(rel_path)})


@router.post("/api/registration/plate/start")
async def registration_plate_start(body: PlateRegistrationStartRequest):
    """Start a plate registration (placement + verdict) in the background."""
    if not any(p["slug"] == body.slug for p in plate_registration.list_packs()):
        return error_response(f"Unknown pack {body.slug!r}", status=404)
    task = plate_registration.start_task(body.slug, place=body.place, fix=body.fix,
                                         rel_path=body.rel_path)
    return JSONResponse(task.snapshot())


@router.get("/api/registration/plate/status/{task_id}")
async def registration_plate_status(task_id: str):
    task = plate_registration.get_task(task_id)
    if task is None:
        return error_response("Unknown or expired task", status=404)
    return JSONResponse(task.snapshot())


@router.post("/api/registration/plate/cancel/{task_id}")
async def registration_plate_cancel(task_id: str):
    task = plate_registration.cancel_task(task_id)
    if task is None:
        return error_response("Unknown or expired task", status=404)
    return JSONResponse(task.snapshot())


def _overlap(a: dict, b: dict) -> float:
    """Fraction of bbox ``a`` covered by bbox ``b`` (degrees; fine at city scale)."""
    h = max(0.0, min(a["north"], b["north"]) - max(a["south"], b["south"]))
    w = max(0.0, min(a["east"], b["east"]) - max(a["west"], b["west"]))
    area = (a["north"] - a["south"]) * (a["east"] - a["west"])
    return (h * w / area) if area > 0 else 0.0


@router.get("/api/registration/critic/references")
async def critic_references(north: float, south: float, east: float, west: float):
    """References a model over this bbox can be scored against: overlapping packs, best
    first, plus the 3DEP nDSM option (US only; fails cleanly elsewhere)."""
    bbox = {"north": north, "south": south, "east": east, "west": west}
    refs = []
    for p in plate_registration.list_packs():
        if not (p["scorable"] or p["surveyed"]):
            continue
        cover = _overlap(bbox, p["window"])
        if cover > 0:
            refs.append({"kind": "pack", "slug": p["slug"], "city": p["city"],
                         "surveyed": p["surveyed"], "overlap": round(cover, 3)})
    refs.sort(key=lambda r: -r["overlap"])
    refs.append({"kind": "ndsm", "slug": None, "city": "USGS 3DEP lidar nDSM (US only)",
                 "surveyed": True, "overlap": None})
    return JSONResponse({"references": refs})


def _score(body: CriticScoreRequest) -> dict:
    from app.server.core import mesh_import
    from city2stl.registration import critic

    ref_in = body.reference
    if ref_in.kind == "pack":
        if not ref_in.slug:
            raise ValueError("reference.slug is required for kind='pack'")
        ref = critic.pack_reference(ref_in.slug)
    else:
        b = ref_in.bbox or (body.model.bbox if body.model.kind == "stl" else None)
        if not b:
            raise ValueError("reference.bbox is required for kind='ndsm'")
        ref = critic.ndsm_reference((b["north"], b["south"], b["east"], b["west"]),
                                    resolution=ref_in.resolution)

    m = body.model
    if m.kind == "buildings":
        if not m.features:
            raise ValueError("model.features is empty: load the city buildings first")
        model = {"kind": "buildings", "features": m.features}
    else:
        if not (m.upload_id and m.bbox):
            raise ValueError("model.upload_id and model.bbox are required for kind='stl'")
        model = {"kind": "stl", "path": str(mesh_import.upload_mesh_path(m.upload_id)),
                 "bbox": m.bbox, "up_axis": m.up_axis, "m_per_unit": m.m_per_unit}
    return critic.score_model(model, ref)


@router.post("/api/registration/critic/score")
async def critic_score(body: CriticScoreRequest):
    """Per-building median absolute height error, footprint IoU and a roof summary."""
    from app.server.core.mesh_import import MeshImportError
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _score, body)
    except (ValueError, FileNotFoundError, LookupError, MeshImportError) as e:
        return error_response(str(e), status=400)
    except Exception as e:
        logger.exception("Critic scoring failed")
        return error_response(f"Scoring failed: {e}", status=500)
    return JSONResponse(result)
