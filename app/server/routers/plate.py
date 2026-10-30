"""plate.py — the Plate align page (Tools menu, F-UNIFY Phase 4 part 3). Thin over
``core/plate_align.py`` (listing, images, jobs) and ``city2stl.registration.align_edit`` (the
shared save / refine / re-fetch code of the standalone drag-align tool).

* ``GET  /api/plate/plates``                       plates that can be placed, with saved flag
* ``GET  /api/plate/plates/{slug}``                shapes, window, auto result, saved placement
* ``GET  /api/plate/plates/{slug}/image/{name}``   a pack raster (stl, buildings, water, satellite)
* ``POST /api/plate/plates/{slug}/score``          overlap and mean edge gap for a matrix
* ``POST /api/plate/plates/{slug}/save``           store the placement with the plate
* ``POST /api/plate/plates/{slug}/refine``         ECC refine, a job
* ``POST /api/plate/plates/{slug}/refetch``        fresh OSM + satellite for a window, a job
* ``GET  /api/plate/jobs/{id}``                    job status with its result

The Auto placement is the existing ``/api/registration/plate/*`` job. Matrices are 2x3 affines
STL px -> OSM px. No Google request is made by any route here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.server.core import plate_align as pa

router = APIRouter()


class MatrixRequest(BaseModel):
    matrix: list[list[float]] = Field(min_length=2, max_length=2)


class RefetchRequest(MatrixRequest):
    margin: float = Field(1.5, ge=1.0, le=4.0)
    center: list[float] | None = None


class SaveRequest(BaseModel):
    payload: dict


def _need(slug: str) -> None:
    if not pa.valid_slug(slug):
        raise HTTPException(404, "Unknown plate")


@router.get("/api/plate/plates")
async def plates():
    return {"plates": pa.list_plates()}


@router.get("/api/plate/plates/{slug}")
async def plate(slug: str):
    _need(slug)
    return pa.plate_detail(slug)


@router.get("/api/plate/plates/{slug}/image/{name}")
async def image(slug: str, name: str):
    p = pa.image_path(slug, name)
    if p is None:
        raise HTTPException(404, "No such image")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.post("/api/plate/plates/{slug}/score")
async def score(slug: str, req: MatrixRequest):
    _need(slug)
    return pa.score(slug, req.matrix)


@router.post("/api/plate/plates/{slug}/save")
async def save(slug: str, req: SaveRequest):
    _need(slug)
    return pa.save_placement(slug, req.payload)


@router.post("/api/plate/plates/{slug}/refine")
async def refine(slug: str, req: MatrixRequest):
    _need(slug)
    return pa.snapshot(pa.start_refine(slug, req.matrix))


@router.post("/api/plate/plates/{slug}/refetch")
async def refetch(slug: str, req: RefetchRequest):
    _need(slug)
    return pa.snapshot(pa.start_refetch(slug, req.matrix, req.margin, req.center))


@router.get("/api/plate/jobs/{task_id}")
async def job(task_id: str):
    j = pa.jobs.get(task_id)
    if j is None:
        raise HTTPException(404, "Unknown or expired job")
    return pa.snapshot(j)
