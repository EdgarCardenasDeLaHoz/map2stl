"""photo.py — the Photo solve page (Tools menu, F-UNIFY Phase 4 part 3). Thin over
``core/photo_solve.py``, which calls the same ``api_*`` functions as the standalone pairing tool
(``tools/photo_label/server.py``).

* ``GET  /api/photo/regions``                         regions with photo count and map start
* ``GET  /api/photo/regions/{r}/photos``              photos with pair count and solve state
* ``POST /api/photo/regions/{r}/photos?name=``        add a photo (raw image bytes in the body)
* ``GET  /api/photo/regions/{r}/image?path=``         one photo's bytes
* ``GET  /api/photo/regions/{r}/labels?path=``        saved pairs (and camera) of a photo
* ``POST /api/photo/regions/{r}/labels``              save pairs (+ solved camera)
* ``POST /api/photo/regions/{r}/flag``                mark a photo unusable / usable
* ``POST /api/photo/regions/{r}/solve``               camera fit on the pairs + the heights table
* ``POST /api/photo/regions/{r}/project``             buildings the solved camera sees (outlines, ids)
* ``GET  /api/photo/regions/{r}/footprints``          footprints near a point, for the map
* ``POST /api/photo/regions/{r}/register``            auto start pose (skyline vs terrain), a job
* ``POST /api/photo/regions/{r}/screen`` / ``GET``    screen the region's photos (panorama, street level)
* ``GET/POST /api/photo/regions/{r}/heights``         read / keep the heights read from a photo
* ``POST /api/photo/regions/{r}/commons``             search Wikimedia Commons, a job
* ``POST /api/photo/regions/{r}/commons/add``         download one found photo into the region
* ``GET  /api/photo/jobs/{id}``                       job status with its result

No Google request is made by any route: jobs run inside ``google_usage.cache_only()`` and the
tool's Google 3D proxy and render-based registration are not exposed.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.server.core import photo_solve as ps
from app.server.core.jobs import Job, JobRegistry
from geo2stl import google_usage

router = APIRouter()
_jobs = JobRegistry("photo", ttl=900)


class PairIn(BaseModel):
    x: float
    y: float
    lat: float
    lon: float
    label: str | None = None
    h_ellipsoid_m: float | None = None
    auto: bool | None = None


class LabelsIn(BaseModel):
    photo: str
    labels: list[PairIn]
    image_px: list[int] | None = None
    camera: dict | None = None
    camera_source: str | None = None


class SolveIn(BaseModel):
    photo: str
    labels: list[PairIn]
    image_px: list[int] = Field(min_length=2, max_length=2)


class ProjectIn(BaseModel):
    camera: dict
    image_px: list[int] = Field(min_length=2, max_length=2)


class FlagIn(BaseModel):
    photo: str
    bad: bool = True
    reason: str = ""


class RegisterIn(BaseModel):
    photo: str
    refresh: bool = False


class HeightsIn(BaseModel):
    photo: str
    rows: list[dict]


class CommonsAddIn(BaseModel):
    url: str
    title: str


def _region(r: str) -> str:
    try:
        ps.cfg(r)
    except ps.UnknownRegion as exc:
        raise HTTPException(404, "Unknown region") from exc
    return r


def _photo(r: str, path: str) -> None:
    if ps.photo_file(r, path) is None:
        raise HTTPException(404, "No such photo")


def _snap(job: Job) -> dict:
    return {**job.snapshot(), "result": job.result if job.status == "done" else None}


def _start(op: str, params: dict, run) -> dict:
    """Run ``run(job)`` on the shared job runner; no Google request can leave from it."""
    def work(job: Job) -> None:
        try:
            with google_usage.cache_only():
                job.result = run(job)
        except Exception as exc:
            job.error = str(exc)
            job.finish("error", f"Failed: {exc}")
            return
        job.update(100, "Done")
        job.finish("done", "Done")

    p = {"op": op, **params}
    job = _jobs.start(lambda tid: Job(task_id=tid, params=p), work, join_identical=p)
    return _snap(job)


@router.get("/api/photo/regions")
def regions():
    return {"regions": ps.list_regions()}


@router.get("/api/photo/regions/{r}/photos")
def photos(r: str):
    return {"photos": ps.list_photos(_region(r))}


@router.post("/api/photo/regions/{r}/photos")
async def add_photo(r: str, request: Request, name: str = "photo.jpg"):
    _region(r)
    data = await request.body()
    try:
        return ps.add_photo(r, name, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/photo/regions/{r}/image")
def image(r: str, path: str):
    _region(r)
    p = ps.photo_file(r, path)
    if p is None:
        raise HTTPException(404, "No such photo")
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@router.get("/api/photo/regions/{r}/labels")
def get_labels(r: str, path: str):
    _region(r)
    _photo(r, path)
    return ps.get_labels(r, path)


@router.post("/api/photo/regions/{r}/labels")
def save_labels(r: str, req: LabelsIn):
    _region(r)
    _photo(r, req.photo)
    try:
        return ps.save_labels(r, req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/photo/regions/{r}/flag")
def flag(r: str, req: FlagIn):
    _region(r)
    res = ps.flag(r, req.photo, req.bad, req.reason)
    if not res.get("ok"):
        raise HTTPException(404, res.get("error", "No such photo"))
    return res


@router.post("/api/photo/regions/{r}/solve")
def solve(r: str, req: SolveIn):
    _region(r)
    _photo(r, req.photo)
    with google_usage.cache_only():
        return ps.solve(r, req.model_dump(exclude_none=True))


@router.post("/api/photo/regions/{r}/project")
def project(r: str, req: ProjectIn):
    _region(r)
    with google_usage.cache_only():
        return ps.project(r, req.model_dump())


@router.get("/api/photo/regions/{r}/footprints")
def footprints(r: str, lat: float, lon: float, radius_m: float = 1500.0):
    _region(r)
    return ps.footprints(r, lat, lon, min(max(radius_m, 50.0), 6000.0))


@router.post("/api/photo/regions/{r}/register")
def register(r: str, req: RegisterIn):
    _region(r)
    _photo(r, req.photo)
    return _start("register", {"region": r, "photo": req.photo, "refresh": req.refresh},
                  lambda job: ps.candidates(r, req.photo, req.refresh, job.update))


@router.post("/api/photo/regions/{r}/screen")
def screen_start(r: str):
    return ps.screen_start(_region(r))


@router.get("/api/photo/regions/{r}/screen")
def screen_state(r: str):
    return ps.screen_state(_region(r))


@router.get("/api/photo/regions/{r}/heights")
def get_heights(r: str):
    return ps.load_heights(_region(r))


@router.post("/api/photo/regions/{r}/heights")
def save_heights(r: str, req: HeightsIn):
    _region(r)
    try:
        return ps.save_heights(r, req.photo, req.rows)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/photo/regions/{r}/commons")
def commons(r: str):
    _region(r)
    return _start("commons", {"region": r}, lambda job: {"photos": ps.commons_search(r)})


@router.post("/api/photo/regions/{r}/commons/add")
def commons_add(r: str, req: CommonsAddIn):
    _region(r)
    try:
        return ps.commons_add(r, req.url, req.title)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/photo/jobs/{task_id}")
def job(task_id: str):
    j = _jobs.get(task_id)
    if j is None:
        raise HTTPException(404, "Unknown or expired job")
    return _snap(j)
