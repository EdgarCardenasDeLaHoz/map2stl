"""
core/plate_registration.py — plate placement + tile-consensus verdict as background tasks.

The web app's "Plate registration" panel (F-REGION §5) runs the align tool's two
registration steps in-process, through the functions promoted into
``city2stl.registration`` on 2026-09-27 (no subprocess, no ``sys.path`` edits):

1. **Street placement** — ``street_place.place_plate``: the plate's footprints (plus
   water with bridges, plus terrain where the DEM has shape) against OSM around the
   pack's recorded pose.  Minutes when Overpass has to be asked; seconds from cache.
2. **Tile consensus** — ``consensus.score_export``: the OSM window cut into tiles, each
   correlated on its own; pass / review / fail with reasons, optionally moving the
   placement onto the position the tiles agree on (``fix``).

A "plate" here is an align-tool pack (``tools/align_tool/data/<slug>/``), which the
panel pairs with a mesh-library file (``config.MICROPOLITAN_STL_DIR``) by city name.
Nothing is written to the pack: the result is returned to the client, which saves the
placement to the library file's location sidecar through the existing
``POST /api/layers/mesh/library/{rel_path}/location`` route.

Runs on the shared job runner (``core/jobs.py``): identical running requests join,
finished tasks are kept 900 s.  Cancel marks the task
cancelled and discards its result; a placement already running finishes in its thread.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.server.core.jobs import Job, JobRegistry
from city2stl.registration.align_paths import data_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------

def _read_meta(folder: Path) -> dict | None:
    try:
        return json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_packs() -> list[dict]:
    """Every align-tool pack that can be placed or scored, with its current state."""
    root = data_dir()
    out = []
    if not root.is_dir():
        return out
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        meta = _read_meta(folder)
        if meta is None or "osm_bbox_nsew" not in meta:
            continue
        n, s, e, w = (float(v) for v in meta["osm_bbox_nsew"])
        has_rasters = all((folder / f).exists() for f in
                          ("stl_heightmap.npy", "osm_buildings.npy"))
        out.append({
            "slug": meta.get("slug") or folder.name,
            "city": meta.get("city"),
            "region": meta.get("region"),
            "window": {"north": n, "south": s, "east": e, "west": w},
            "placeable": (folder / "stl_relief.npy").exists()
                         and (folder / "stl_residual.npy").exists()
                         and bool(meta.get("pipeline_guess") or meta.get("geometric_guess")),
            "scorable": has_rasters and bool(meta.get("pipeline_guess")),
            "surveyed": meta.get("pipeline_guess") is None,
            "verdict": (meta.get("registration") or {}).get("status"),
        })
    return out


def _norm(s: str) -> str:
    return " ".join(str(s).lower().replace("_", " ").replace(",", " ").split())


def pack_for_library_path(rel_path: str) -> str | None:
    """The align pack that depicts a mesh-library file, matched by city name.

    The library folder (``Miami,_FL_-_L_&_XL``) and the pack's ``meta.city`` (``Miami``)
    are compared on their first word; a folder that says "miniature" only matches a
    miniature pack and vice versa (Paris and Philadelphia have both).  Returns None when
    no pack matches; the panel then asks the user to choose.
    """
    from app.server.core.mesh_import import parse_city_name_from_path
    folder = Path(rel_path).parts[0] if Path(rel_path).parts else rel_path
    parsed = _norm(parse_city_name_from_path(rel_path))
    if not parsed:
        return None
    first = parsed.split()[0]
    mini = "miniature" in folder.lower()
    best = None
    for pack in list_packs():
        city = _norm(pack.get("city") or "")
        is_mini = "miniature" in city or pack["slug"].endswith("_miniature")
        if not city or is_mini != mini:
            continue
        if city == parsed or city.split()[0] == first:
            # Prefer the pack whose full city name the folder spells out.
            if best is None or city == parsed:
                best = pack["slug"]
    return best


# ---------------------------------------------------------------------------
# Placement geometry
# ---------------------------------------------------------------------------

def placement_geometry(matrix, meta: dict, plate_shape: tuple[int, int],
                       footprint: np.ndarray | None = None) -> dict:
    """Where a placement matrix puts the plate, in degrees.

    ``matrix`` maps plate pixels to OSM-window pixels (both row 0 = south); the window is
    ``meta.osm_bbox_nsew`` at ``meta.resolution`` cells.  Returns the rotated plate outline
    (``corners``, [[lat, lon] x 4], for the map), its centre, turn and size in metres, and
    ``bbox``: the plate's own unrotated extent about that centre, which is what a location
    sidecar stores (``stl_to_heightmap`` maps the mesh's XY extent onto it).
    """
    M = np.asarray(matrix, dtype=np.float64)
    n, s, e, w = (float(v) for v in meta["osm_bbox_nsew"])
    res = float(meta.get("resolution") or 512)
    ph, pw = plate_shape
    if footprint is not None and footprint.any():
        rows = np.flatnonzero(footprint.any(axis=1))
        cols = np.flatnonzero(footprint.any(axis=0))
        y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    else:
        y0, y1, x0, x1 = 0, ph, 0, pw
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1],
                    [(x0 + x1) / 2.0, (y0 + y1) / 2.0, 1]], dtype=np.float64)
    osm = pts @ M.T                                  # (5, 2) window px (x, y)
    lon = w + osm[:, 0] / res * (e - w)
    lat = s + osm[:, 1] / res * (n - s)
    scale = math.hypot(M[0, 0], M[1, 0])             # window px per plate px
    turn = math.degrees(math.atan2(M[1, 0], M[0, 0]))
    cell = float(meta.get("cell_size_m") or 1.0)     # metres per window px
    width_m = (x1 - x0) * scale * cell
    height_m = (y1 - y0) * scale * cell
    clat, clon = float(lat[4]), float(lon[4])
    from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon
    dlat = 0.5 * height_m / M_PER_DEG_LAT
    dlon = 0.5 * width_m / m_per_deg_lon(clat)
    return {
        "center": {"lat": clat, "lon": clon},
        "corners": [[float(a), float(b)] for a, b in zip(lat[:4], lon[:4], strict=True)],
        "turn_deg": turn,
        "width_m": width_m,
        "height_m": height_m,
        "bbox": {"north": clat + dlat, "south": clat - dlat,
                 "east": clon + dlon, "west": clon - dlon},
    }


def _plain(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, (np.floating, np.integer)):
        return _plain(v.item())
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


_PLACEMENT_KEYS = ("lat", "lon", "turn_deg", "recorded_turn_deg", "size", "span_m", "moved_m",
                   "score", "unique", "size_margin", "agree", "agree_pack", "confident",
                   "position_confident", "channels", "channel_z", "plate_relief_m",
                   "dem_relief_m", "notes", "seconds")
_VERDICT_KEYS = ("status", "reasons", "lead", "consensus", "agreeing", "voting", "endorsing",
                 "median_dist_m", "rival_votes", "rival_dist_m", "biggest_cluster",
                 "corrected_m", "refine_r")


def run_plate_registration(slug: str, *, place: bool = True, fix: bool = True,
                           progress=None) -> dict:
    """Place one pack by its streets (optional) and check the placement by tile consensus.

    ``progress(stage, pct, message)`` is called between steps.  Read-only on disk except
    for the placement's own Overpass / DEM caches.
    """
    from city2stl.registration import consensus, street_place

    def say(stage, pct, msg):
        if progress is not None:
            progress(stage, pct, msg)

    folder = data_dir() / slug
    meta = _read_meta(folder)
    if meta is None:
        raise FileNotFoundError(f"No align pack {slug!r} under {data_dir()}")
    if meta.get("pipeline_guess") is None:
        raise ValueError(f"Pack {slug!r} is a surveyed surface with no plate to place "
                         f"(it is a reference for scoring, not a registration)")

    placement = None
    matrix = None
    if place:
        say("placing", 10, "Street placement (fetching OSM if not cached)…")
        plate = street_place.Plate(slug)
        placement = street_place.place_plate(plate)
        matrix = street_place.as_guess(placement, meta)["matrix"]
    say("verifying", 70, "Tile-consensus check…")
    scored = consensus.score_export(slug, fix=fix, matrix=matrix)
    if scored is None:
        raise FileNotFoundError(f"Pack {slug!r} has no exported rasters to score")
    final = scored["matrix"]

    relief = np.load(folder / "stl_relief.npy") if (folder / "stl_relief.npy").exists() else None
    stl = np.load(folder / "stl_heightmap.npy")
    footprint = np.isfinite(relief) if relief is not None else None
    geometry = placement_geometry(final, meta, stl.shape, footprint)

    say("done", 100, f"Verdict: {scored['status']}")
    return _plain({
        "slug": slug,
        "city": meta.get("city"),
        "region": meta.get("region"),
        "placed": bool(place),
        "placement": ({k: placement.get(k) for k in _PLACEMENT_KEYS} if placement else None),
        "verdict": {k: scored.get(k) for k in _VERDICT_KEYS},
        "matrix": [list(map(float, row)) for row in final],
        "matrix_source": ("street_place" if place else
                          (meta.get("pipeline_guess") or {}).get("source") or "pack"),
        "geometry": geometry,
        "window": dict(zip(("north", "south", "east", "west"),
                           (float(v) for v in meta["osm_bbox_nsew"]), strict=True)),
    })


# ---------------------------------------------------------------------------
# Task registry
# ---------------------------------------------------------------------------

@dataclass
class PlateTask(Job):
    """State of one background plate registration (running | done | error | cancelled)."""
    stage: str = "queued"            # queued | placing | verifying | done

    def snapshot(self) -> dict:
        return {"task_id": self.task_id, "status": self.status, "stage": self.stage,
                "progress": self.progress, "message": self.message, "error": self.error,
                "params": self.params, "elapsed_s": self.elapsed_s(),
                "result": self.result if self.status == "done" else None}


_registry = JobRegistry("plate-reg", ttl=900)


def get_task(task_id: str) -> PlateTask | None:
    return _registry.get(task_id)


def start_task(slug: str, *, place: bool = True, fix: bool = True,
               rel_path: str | None = None) -> PlateTask:
    """Start (or join an identical running) plate registration; returns its task."""
    params = {"slug": slug, "place": bool(place), "fix": bool(fix), "rel_path": rel_path}

    def run(task: PlateTask) -> None:
        def progress(stage, pct, msg):
            if task.running:
                task.stage, task.progress, task.message = stage, int(pct), msg

        try:
            result = run_plate_registration(slug, place=place, fix=fix, progress=progress)
        except Exception as exc:
            logger.warning("Plate registration %s (%s) failed: %s", task.task_id, slug, exc)
            task.error = str(exc)
            task.finish("error", f"Failed: {exc}")
            return
        if task.running:
            result["rel_path"] = rel_path
            task.result = result
            task.stage, task.progress = "done", 100
        task.finish("done")

    return _registry.start(lambda tid: PlateTask(task_id=tid, params=params), run,
                           join_identical=params)


def cancel_task(task_id: str) -> PlateTask | None:
    """Mark a running task cancelled; its result is discarded when the thread ends."""
    return _registry.cancel(task_id)
