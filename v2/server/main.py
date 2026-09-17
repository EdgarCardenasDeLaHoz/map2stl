"""v2 HTTP surface.

Nineteen routes in v1 covered this pipeline across five routers. Here it is nine, and the
shape of the flow is visible from the route list alone:

    GET  /api/regions                     what can be rendered
    GET  /api/regions/{name}/settings     how it was last configured
    PUT  /api/regions/{name}/settings     remember that
    GET  /api/sources                     where elevation can come from, and if it works
    POST /api/dem                         fetch elevation, get an id back
    GET  /api/dem/{id}/preview.png        look at it
    POST /api/overlay                     preview imagery, never part of the mesh
    POST /api/export                      turn an id into a mesh, get a job back
    GET  /api/jobs/{id}/events            watch it (server-sent events)
    DELETE /api/jobs/{id}                 stop it
    GET  /api/jobs/{id}/download          take the file

The export route takes a DEM id, not a bounding box and a settings object. That single
choice is what removes v1's cache-key handshake, and with it the entire class of failure
where the two halves of a render disagreed about what they were rendering.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import pipeline
from . import regions as regions_repo
from .config import HOST, PORT, WEB_BUILD_DIR
from .jobs import Job, jobs
from .models import DemRequest, ExportRequest, OverlayRequest, RegionSettings
from .store import dem_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("v2")

app = FastAPI(title="3D Maps v2", version="0.1.0")
app.add_middleware(GZipMiddleware, minimum_size=1024)


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

@app.get("/api/regions")
def api_regions() -> Any:
    return {"regions": [r.model_dump() for r in regions_repo.list_regions()]}


@app.get("/api/regions/{name}/settings")
def api_get_settings(name: str) -> Any:
    if regions_repo.get_region(name) is None:
        raise HTTPException(404, f"No saved region named {name!r}")
    return regions_repo.get_settings(name).model_dump()


@app.put("/api/regions/{name}/settings")
def api_put_settings(name: str, settings: RegionSettings) -> Any:
    if regions_repo.get_region(name) is None:
        raise HTTPException(404, f"No saved region named {name!r}")
    regions_repo.save_settings(name, settings)
    return {"saved": True}


# ---------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------

@app.get("/api/sources")
def api_sources() -> Any:
    return pipeline.list_sources()


@app.post("/api/dem")
def api_dem(req: DemRequest) -> Any:
    """Fetch an elevation grid and keep it. Returns the id the export will use.

    This runs inline rather than as a job. A DEM fetch from the local store is a fraction
    of a second, and even a slow OpenTopography download is bounded; making the client
    orchestrate a job for it would reintroduce exactly the multi-step dance v2 exists to
    remove.
    """
    try:
        array, source_used, seconds = pipeline.fetch_dem(
            req.bbox.as_dict(), req.dem, req.projection
        )
    except pipeline.DemUnavailable as exc:
        # 422, not 500: the request was well formed, the chosen source just has nothing for
        # this area. The message is written to be shown to the user unedited.
        raise HTTPException(422, str(exc)) from exc
    entry = dem_store.put(
        array,
        req.bbox.as_dict(),
        {"dem": req.dem.model_dump(), "projection": req.projection.model_dump()},
        source_used=source_used,
        fetch_seconds=seconds,
    )
    return entry.describe()


@app.get("/api/dem/{dem_id}/preview.png")
def api_dem_preview(dem_id: str, colormap: str = "terrain") -> Response:
    array = dem_store.array(dem_id)
    if array is None:
        raise HTTPException(404, _dem_gone(dem_id))
    return Response(
        content=pipeline.dem_preview_png(array, colormap),
        media_type="image/png",
        # Content is immutable for a given id, so the browser can keep it as long as it likes.
        headers={"Cache-Control": "public, max-age=3600, immutable"},
    )


@app.get("/api/dem/{dem_id}/elevation.bin")
def api_dem_elevation(dem_id: str) -> Response:
    """Raw little-endian float32, row-major, for the 3D view.

    Sent as bytes rather than base64-in-JSON, which is what v1 did: a 600x600 grid is
    1.4 MB either way, but base64 inflates it by a third and costs a decode on the main
    thread before anything can be drawn.
    """
    import numpy as np

    array = dem_store.array(dem_id)
    if array is None:
        raise HTTPException(404, _dem_gone(dem_id))
    return Response(
        content=np.ascontiguousarray(array, dtype=np.float32).tobytes(),
        media_type="application/octet-stream",
        headers={"Cache-Control": "public, max-age=3600, immutable"},
    )


@app.post("/api/overlay")
def api_overlay(req: OverlayRequest) -> Any:
    try:
        return pipeline.fetch_overlay(req.bbox.as_dict(), req.kind, req.dim)
    except Exception as exc:  # noqa: BLE001
        # Overlays are decoration. A failure here says so and leaves the terrain usable,
        # rather than failing the whole view.
        logger.warning("Overlay %s failed: %s", req.kind, exc)
        raise HTTPException(502, f"Could not fetch the {req.kind} overlay: {exc}") from exc


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@app.post("/api/export")
def api_export(req: ExportRequest) -> Any:
    array = dem_store.array(req.demId)
    if array is None:
        raise HTTPException(404, _dem_gone(req.demId))

    def work(job: Job) -> None:
        jobs.update(job, 5, "Converting elevation to millimetres")
        im = pipeline.to_model_space(array, req.model)
        path, stats = pipeline.build_mesh(
            im,
            req.model,
            req.export,
            req.name,
            progress=lambda pct, msg: jobs.update(job, 5 + int(pct * 0.95), msg),
            should_cancel=lambda: jobs.is_cancelled(job),
        )
        job.file_path = path
        job.file_name = f"{req.name}.{req.export.format}"
        job.result = stats

    return jobs.start("export", work).snapshot()


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> Any:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"No job {job_id}")
    return job.snapshot()


@app.get("/api/jobs/{job_id}/events")
def api_job_events(job_id: str) -> StreamingResponse:
    """Server-sent events, one per actual change of state."""
    if jobs.get(job_id) is None:
        raise HTTPException(404, f"No job {job_id}")

    def stream():
        for snapshot in jobs.watch(job_id):
            yield f"data: {json.dumps(snapshot)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/jobs/{job_id}")
def api_job_cancel(job_id: str) -> Any:
    if jobs.get(job_id) is None:
        raise HTTPException(404, f"No job {job_id}")
    return {"cancelling": jobs.cancel(job_id)}


@app.get("/api/jobs/{job_id}/download")
def api_job_download(job_id: str) -> FileResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"No job {job_id}")
    if job.state == "running":
        raise HTTPException(409, "That export is still running")
    if job.state != "done":
        raise HTTPException(410, job.error or f"That export {job.state}")

    taken = jobs.take_file(job_id)
    if taken is None:
        raise HTTPException(410, "The exported file is no longer on disk")
    path, name = taken
    # The file survives the download here, unlike v1, which unlinked it in a background
    # task the moment the response was sent. A user whose download was interrupted had
    # to re-render; the TTL sweep is a better place to reclaim a few megabytes.
    return FileResponse(path, filename=name, media_type="application/octet-stream")


# ---------------------------------------------------------------------------
# Diagnostics and static hosting
# ---------------------------------------------------------------------------

@app.get("/api/health")
def api_health() -> Any:
    return {
        "ok": True,
        "dem": dem_store.stats(),
        "jobs": jobs.stats(),
        "webBuilt": WEB_BUILD_DIR.is_dir(),
    }


def _dem_gone(dem_id: str) -> str:
    return (
        f"No elevation grid {dem_id}. It has expired or the server restarted. "
        "Load the terrain again."
    )


@app.exception_handler(ValueError)
def _value_error(_request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


if WEB_BUILD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_BUILD_DIR), html=True), name="web")
else:
    @app.get("/")
    def _no_build() -> Any:
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "The web build is missing. Run 'npm install && npm run build' in "
                    f"{WEB_BUILD_DIR.parent}, or use the Vite dev server on port 5173."
                )
            },
        )


def run() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("v2.server.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":  # pragma: no cover
    run()
