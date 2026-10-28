"""benchmark.py — the Benchmark page (Tools menu, F-UNIFY Phase 4). Thin: the data and the
scoring live in ``core/benchmark_scores.py``.

* ``GET  /api/benchmark/regions`` — every region with truth source, building count, last MAE
  and scored date. Read from the saved summaries; no network.
* ``GET  /api/benchmark/regions/{id}`` — headline numbers, one row per height source, the
  previous score (for the delta), the report link and the reports that can be scored again.
* ``GET  /api/benchmark/regions/{id}/gap?heights_from=`` — how many buildings have no cached
  truth and how many 3D Tiles fetches measuring them would take.
* ``POST /api/benchmark/score`` — ``{region, heights_from, allow_paid_fetches=false}``: a job
  on the shared runner (poll ``GET /api/jobs/{id}``). Cached truth only unless the caller
  allows fetches; without cached truth it fails and says how many fetches it would need.
* ``GET  /api/benchmark/usage`` — 3D Tiles root requests this month vs the free cap
  (``value`` is null while nothing counts them).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.server.core import benchmark_scores as scores
from app.server.core.jobs import Job, JobRegistry

router = APIRouter()
_jobs = JobRegistry("benchmark", ttl=900)


class ScoreRequest(BaseModel):
    region: str
    heights_from: str = "latest"
    allow_paid_fetches: bool = False


@router.get("/api/benchmark/regions")
async def list_regions():
    return {"regions": scores.list_regions()}


@router.get("/api/benchmark/regions/{region}")
async def region_detail(region: str):
    detail = scores.region_detail(region)
    if detail is None:
        raise HTTPException(404, "Unknown benchmark region")
    return detail


@router.get("/api/benchmark/regions/{region}/gap")
async def region_gap(region: str, heights_from: str = "latest"):
    if scores.region_detail(region) is None:
        raise HTTPException(404, "Unknown benchmark region")
    return {"gap": scores.gap_for(region, heights_from)}


@router.post("/api/benchmark/score")
async def start_score(req: ScoreRequest):
    if scores.region_detail(req.region) is None:
        raise HTTPException(404, "Unknown benchmark region")
    params = req.model_dump()

    def work(job: Job) -> None:
        try:
            job.result = scores.score_region(
                req.region, req.heights_from, req.allow_paid_fetches, job
            )
        except (scores.NeedsPaidFetches, ValueError) as exc:
            job.error = str(exc)
            job.finish("error", str(exc))
            return
        job.update(100, "Scored")
        job.finish("done", "Scored")

    job = _jobs.start(lambda tid: Job(task_id=tid, params=params), work, join_identical=params)
    return {"task_id": job.task_id, **job.snapshot()}


@router.get("/api/benchmark/usage")
async def usage():
    return scores.tiles_usage()
