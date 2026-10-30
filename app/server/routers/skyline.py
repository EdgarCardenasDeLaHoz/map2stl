"""skyline.py — the Street View heights page (Tools menu, F-UNIFY Phase 4). Thin: the data,
the estimate and the run live in ``core/skyline_runs.py``.

* ``GET  /api/skyline/regions/{region}/reports`` — the region's reports (newest first) with
  tier counts, and the name the next run will get.
* ``GET  /api/skyline/reports/{dir}/map`` — one report's footprints for the map (compact).
* ``GET  /api/skyline/estimate?region=`` — panoramas needed / cached, paid fetches, and this
  month's Street View count against the cap, before anything runs.
* ``POST /api/skyline/runs`` — ``{region, allow_paid_fetches=false}``: a job on the shared
  runner (poll ``GET /api/skyline/runs/{id}`` or ``/api/jobs/{id}``; cancel with
  ``POST /api/jobs/{id}/cancel``). Without the flag the run sends **no** paid request
  (``google_usage.cache_only``); views not in the cache are skipped. One run at a time.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.server.core import jobs, skyline_runs
from app.server.core.jobs import JobRegistry

router = APIRouter()
_jobs = JobRegistry("skyline", ttl=900)


class RunRequest(BaseModel):
    region: str
    allow_paid_fetches: bool = False


def _clean_region(region: str) -> str:
    region = (region or "").strip()
    if not region or not re.fullmatch(r"[\w .\-]{1,80}", region):
        raise HTTPException(400, "Unknown region name")
    return region


@router.get("/api/skyline/regions/{region}/reports")
async def region_reports(region: str):
    region = _clean_region(region)
    return {"region": region, "reports": skyline_runs.reports_for(region),
            "next_report": skyline_runs.next_report_name(region)}


@router.get("/api/skyline/reports/{region_dir}/map")
async def report_map(region_dir: str):
    payload = skyline_runs.map_payload(region_dir)
    if payload is None:
        raise HTTPException(404, "Unknown report")
    return payload


@router.get("/api/skyline/estimate")
async def run_estimate(region: str):
    return skyline_runs.estimate(_clean_region(region))


@router.post("/api/skyline/runs")
async def start_run(req: RunRequest):
    region = _clean_region(req.region)
    for j in list(_jobs.tasks.values()):
        if j.alive() and j.params.get("region") != region:
            raise HTTPException(409, f"A run for {j.params.get('region')} is already in progress")
    name = skyline_runs.next_report_name(region)
    params = {"region": region, "allow_paid_fetches": req.allow_paid_fetches}

    def make(tid: str):
        return skyline_runs.RunJob(task_id=tid, params=params, report=name)

    def work(job: skyline_runs.RunJob) -> None:
        result = skyline_runs.run_region(job, region, name, allow_paid=req.allow_paid_fetches)
        if job.status != jobs.CANCELLED:
            job.result = result
            job.update(100, "Done")
            job.finish("done", "Done")

    job = _jobs.start(make, work, join_identical=params)
    return {"task_id": job.task_id, **job.snapshot()}


@router.get("/api/skyline/runs/{task_id}")
async def run_status(task_id: str):
    job = _jobs.get(task_id)
    if job is None:
        raise HTTPException(404, "Run not found")
    return {"task_id": job.task_id, **job.snapshot(), "result": job.result}
