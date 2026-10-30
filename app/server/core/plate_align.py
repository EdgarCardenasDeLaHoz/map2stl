"""plate_align.py — the Plate align page's data: packs, saved placements, images, refine/re-fetch.

The editing logic is ``city2stl.registration.align_edit`` (shared with the standalone drag-align
tool); the auto placement is ``core/plate_registration.py``. This module only lists what the page
needs, serves a pack's rasters and wraps the two slow calls as jobs. No Google call is made: the
re-fetch uses Overpass and ESRI World Imagery.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from app.server.core import plate_registration
from app.server.core.jobs import Job, JobRegistry
from city2stl.registration import align_edit
from city2stl.registration.align_paths import data_dir, ground_truth_dir

jobs = JobRegistry("plate-align", ttl=900)

#: Rasters of a pack the page may draw, name -> file.
IMAGES = {
    "stl": "stl_heightmap.png",
    "stl_mask": "stl_mask.png",
    "buildings": "osm_buildings.png",
    "water": "osm_water.png",
    "satellite": "sat.png",
}
_SLUG = re.compile(r"^[A-Za-z0-9_.\-]+$")


def valid_slug(slug: str) -> bool:
    return bool(_SLUG.match(slug)) and (data_dir() / slug / "meta.json").is_file()


def _saved(slug: str) -> dict | None:
    r = align_edit.api_load({"slug": slug})
    return r.get("payload") if r.get("found") else None


def list_plates() -> list[dict]:
    """Packs with a plate to place (surveyed surfaces have none), and whether a placement is saved."""
    out = []
    for p in plate_registration.list_packs():
        if p["surveyed"]:
            continue
        out.append({"slug": p["slug"], "city": p["city"], "region": p["region"],
                    "verdict": p["verdict"],
                    "saved": (ground_truth_dir() / f"{p['slug']}.json").is_file()})
    return out


def plate_detail(slug: str) -> dict:
    """Everything the page needs to draw one plate: shapes, window, the auto result, the saved placement."""
    meta = align_edit._meta(slug)
    folder = data_dir() / slug
    return {
        "slug": slug,
        "city": meta.get("city"),
        "region": meta.get("region"),
        "stl_shape": meta["stl_shape"],
        "osm_shape": meta["osm_shape"],
        "bbox_nsew": meta["osm_bbox_nsew"],
        "cell_size_m": meta.get("cell_size_m"),
        "resolution": meta.get("resolution", 512),
        "guess": meta.get("pipeline_guess"),
        "images": {k: f"/api/plate/plates/{slug}/image/{k}" for k, f in IMAGES.items()
                   if (folder / f).is_file()},
        "saved": _saved(slug),
    }


def image_path(slug: str, name: str) -> Path | None:
    fname = IMAGES.get(name)
    if not fname or not valid_slug(slug):
        return None
    p = data_dir() / slug / fname
    return p if p.is_file() else None


def _rows(matrix) -> list[list[float]]:
    return [[float(v) for v in row] for row in matrix]


def start_refine(slug: str, matrix: list) -> Job:
    rows = _rows(matrix)
    params = {"op": "refine", "slug": slug, "matrix": tuple(tuple(r) for r in rows)}

    def work(job: Job) -> None:
        try:
            job.update(10, "Snapping the plate to the map (ECC)")
            job.result = align_edit.api_refine({"slug": slug, "matrix": rows})
        except Exception as exc:
            job.error = str(exc)
            job.finish("error", f"Failed: {exc}")
            return
        job.update(100, "Refined")
        job.finish("done", "Refined")

    return jobs.start(lambda tid: Job(task_id=tid, params={"op": "refine", "slug": slug}), work,
                      join_identical=params)


def start_refetch(slug: str, matrix: list, margin: float, center: list | None) -> Job:
    rows = _rows(matrix)
    body = {"slug": slug, "matrix": rows, "margin": margin}
    if center:
        body["center"] = [float(center[0]), float(center[1])]
    params = {"op": "refetch", "slug": slug, "margin": margin, "center": tuple(center or ())}

    def work(job: Job) -> None:
        try:
            job.update(10, "Fetching map data (OpenStreetMap, satellite)")
            job.result = align_edit.api_refetch(body)
        except Exception as exc:
            job.error = str(exc)
            job.finish("error", f"Failed: {exc}")
            return
        job.update(100, "Fetched")
        job.finish("done", "Fetched")

    return jobs.start(lambda tid: Job(task_id=tid, params={"op": "refetch", "slug": slug}), work,
                      join_identical=params)


def save_placement(slug: str, payload: dict) -> dict:
    return align_edit.api_save({"slug": slug, "payload": payload})


def score(slug: str, matrix: list) -> dict:
    return align_edit.api_score({"slug": slug, "matrix": _rows(matrix)})


def _finite(v):
    """JSON cannot carry NaN or infinity (ECC reports a NaN confidence when it did not converge)."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


def snapshot(job: Job) -> dict:
    return {**job.snapshot(), "result": _finite(job.result) if job.status == "done" else None}
