"""jobs.py — one status endpoint for every background job (``core/jobs.py``).

* ``GET /api/jobs/{id}`` — ``{kind, ...snapshot}`` for an export, city fetch or plate
  registration; 404 when no registry knows the id (finished jobs are swept after
  their kind's TTL).
* ``POST /api/jobs/{id}/cancel`` — cancel a running job of any kind.

The per-kind endpoints (``/api/export/status``, ``/api/cities/status``,
``/api/registration/tasks``) stay; new pages (the Tools menu) use these.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.server.core import jobs

router = APIRouter()


def _found(task_id: str):
    hit = jobs.find(task_id)
    if hit is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return hit


@router.get("/api/jobs/{task_id}")
async def job_status(task_id: str):
    kind, job = _found(task_id)
    return {"kind": kind, **job.snapshot()}


@router.post("/api/jobs/{task_id}/cancel")
async def job_cancel(task_id: str):
    kind, job = _found(task_id)
    job.cancel()
    return {"kind": kind, **job.snapshot()}
