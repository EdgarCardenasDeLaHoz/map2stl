"""
core/export.py — STL / OBJ / 3MF / cross-section generation.

Extracted from location_picker.py (backend refactor, step 5).
Each function accepts a plain dict (pre-parsed JSON body) and returns a
FastAPI FileResponse (or raises an exception on failure).
Route handlers in location_picker.py / routers/export.py call these.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from starlette.background import BackgroundTask
from fastapi.responses import FileResponse, JSONResponse
from app.server.core.cache import make_cache_key, read_array_cache

import numpy as np
from numpy2stl import array_to_mesh, writeOBJ, write3MF
import trimesh as tm

# Optional: PIL is used only for label engraving. Import guarded below.

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Export task tracking
# ---------------------------------------------------------------------------

@dataclass
class ExportTask:
    """Tracks the state of an async export job."""
    task_id: str
    status: str = "running"          # running | complete | error
    progress: int = 0                # 0-100
    message: str = "Starting..."
    result_path: Optional[str] = None
    filename: Optional[str] = None
    media_type: str = "application/octet-stream"
    headers: Dict[str, str] = field(default_factory=dict)
    created: float = field(default_factory=time.time)

    def update(self, progress: int, message: str) -> None:
        self.progress = progress
        self.message = message

    def complete(self, result_path: str, filename: str, headers: dict = None) -> None:
        self.status = "complete"
        self.progress = 100
        self.message = "Complete"
        self.result_path = result_path
        self.filename = filename
        if headers:
            self.headers = headers

    def fail(self, message: str) -> None:
        self.status = "error"
        self.message = message


_export_tasks: Dict[str, ExportTask] = {}
_export_tasks_lock = threading.Lock()
_TASK_TTL = 300  # seconds before stale tasks are cleaned up


def _cleanup_stale_tasks() -> None:
    """Remove tasks older than _TASK_TTL seconds."""
    cutoff = time.time() - _TASK_TTL
    with _export_tasks_lock:
        stale = {tid: _export_tasks.pop(tid)
                 for tid, t in list(_export_tasks.items()) if t.created < cutoff}
    for task in stale.values():
        if task.result_path:
            try:
                os.unlink(task.result_path)
            except OSError:
                pass


def get_task_status(task_id: str) -> Optional[dict]:
    """Return progress info for a task, or None if not found."""
    with _export_tasks_lock:
        task = _export_tasks.get(task_id)
    if task is None:
        return None
    return {
        "task_id": task.task_id,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
    }


def get_task_file(task_id: str):
    """Return a FileResponse for a completed task, or None."""
    with _export_tasks_lock:
        task = _export_tasks.get(task_id)
    if not task or task.status != "complete" or not task.result_path:
        return None

    def _cleanup():
        try:
            os.unlink(task.result_path)
        except OSError:
            pass
        with _export_tasks_lock:
            _export_tasks.pop(task_id, None)

    return FileResponse(
        task.result_path,
        filename=task.filename,
        media_type=task.media_type,
        background=BackgroundTask(_cleanup),
        headers=task.headers,
    )


def start_export_task(data: dict, fmt: str) -> str:
    """Start an export in a background thread. Returns task_id."""
    _cleanup_stale_tasks()

    task_id = uuid.uuid4().hex[:12]
    task = ExportTask(task_id=task_id)
    with _export_tasks_lock:
        _export_tasks[task_id] = task

    def _run():
        try:
            if fmt == "puzzle":
                generate_puzzle_3mf(data, task=task)
            else:
                _run_export_pipeline(data, fmt, task)
        except Exception as exc:
            logger.exception("Export task %s failed", task_id)
            task.fail(str(exc))

    thread = threading.Thread(target=_run, daemon=True, name=f"export-{task_id}")
    thread.start()
    return task_id


def _run_export_pipeline(data: dict, fmt: str, task: ExportTask) -> None:
    """Execute the full export pipeline with progress updates."""
    p = ExportContext.from_request(data)

    if not p.dem_values or not p.height or not p.width:
        task.fail("Missing DEM data")
        return

    # Step 1: Prepare DEM
    task.update(10, "Preparing DEM array...")
    im, im_min, im_max = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )

    # Step 2: Optional label engraving
    if p.engrave_label and p.label_text:
        task.update(25, "Engraving label...")
        im = _apply_label_engraving(im, p.label_text, p.base_height)

    # Step 3: Optional contours
    if p.contours and p.contour_interval > 0:
        task.update(35, "Generating contours...")
        im = _apply_contour_lines(im, im_min, im_max, p.model_height,
                                  p.base_height, p.contour_interval, p.contour_style)

    # Step 4: Mesh generation (heaviest step)
    task.update(45, "Generating mesh...")
    if fmt in ("obj", "3mf"):
        vertices, faces = array_to_mesh(im, floor_val=0, walls=p.walls, floor=p.floor)
    else:
        vertices, faces = _numpy2stl_mesh(im, walls=p.walls, floor=p.floor)

    task.update(70, "Repairing mesh...")

    # Step 5: Export to file
    if fmt == "stl":
        temp_path, mesh = _repair_and_export(vertices, faces, ".stl")
        is_watertight = bool(mesh.is_watertight)
        face_count = len(mesh.faces)
        headers = {
            "X-Watertight": str(is_watertight).lower(),
            "X-Face-Count": str(face_count),
            "Access-Control-Expose-Headers": "X-Watertight, X-Face-Count",
        }
        logger.info("STL generated: %d faces, watertight=%s", face_count, is_watertight)
    elif fmt in ("obj", "3mf"):
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=f".{fmt}")
        temp_path = tf.name
        tf.close()
        (writeOBJ if fmt == "obj" else write3MF)(temp_path, {p.name: (vertices, faces)})
        headers = {}
        logger.info("%s generated: %d vertices, %d faces", fmt.upper(), len(vertices), len(faces))
    else:
        task.fail(f"Unknown format: {fmt}")
        return

    task.update(95, "Finalizing...")
    task.complete(temp_path, f"{p.name}.{fmt}", headers)

# strm2stl root dir (app/server/core/export.py → core → server → app → strm2stl)
_STRM2STL_DIR = Path(__file__).parent.parent.parent.parent
# Ensure local packages (numpy2stl, geo2stl) are importable without os.chdir.
if str(_STRM2STL_DIR) not in sys.path:
    sys.path.insert(0, str(_STRM2STL_DIR))


# ---------------------------------------------------------------------------
# Export parameter container
# ---------------------------------------------------------------------------

@dataclass
class DemCacheSettings:
    """DEM processing settings used to reconstruct the server-side cache key.

    Mirrors the key structure built by terrain.py ``get_terrain_dem()``.
    """
    dim: int = 200
    dem_source: str = "local"
    projection: str = "cosine"
    depth_scale: float = 0.5
    water_scale: float = 0.05
    subtract_water: bool = True
    maintain_dimensions: bool = True
    clip_nans: bool = False
    show_sat: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "DemCacheSettings":
        src = d.get("dem") or d
        return cls(
            dim=int(src.get("dim", 200)),
            dem_source=src.get("dem_source", "local"),
            projection=src.get("projection", "cosine"),
            depth_scale=float(src.get("depth_scale", 0.5)),
            water_scale=float(src.get("water_scale", 0.05)),
            subtract_water=bool(src.get("subtract_water", True)),
            maintain_dimensions=bool(src.get("maintain_dimensions", True)),
            clip_nans=bool(src.get("clip_nans", False)),
            show_sat=bool(src.get("show_sat", False)),
        )

    def as_cache_params(self) -> dict:
        return {
            "dim": self.dim, "src": self.dem_source, "proj": self.projection,
            "ds": self.depth_scale, "ws": self.water_scale,
            "sw": self.subtract_water, "md": self.maintain_dimensions,
            "cn": self.clip_nans, "sat": self.show_sat,
        }


def resolve_dem_from_cache(data: dict) -> tuple[list, int, int] | None:
    """Look up a cached DEM from bbox + DEM settings.

    The DEM endpoint caches processed arrays under a key derived from
    bbox + {dim, src, proj, ds, ws, sw, md, cn, sat}.  If the caller
    provides these settings instead of raw ``dem_values``, we can
    reconstruct the key and read from disk — eliminating the need to
    retransmit the (potentially multi-MB) array.

    Returns ``(dem_values_list, height, width)`` or ``None`` on cache miss.
    """
    bbox = data.get("bbox") or data
    north = bbox.get("north")
    south = bbox.get("south")
    east  = bbox.get("east")
    west  = bbox.get("west")
    if None in (north, south, east, west):
        return None

    s = DemCacheSettings.from_dict(data)
    cache_key = make_cache_key("dem", north, south, east, west, s.as_cache_params())

    cached = read_array_cache("dem", cache_key)
    if cached is None or cached[0].get("dem") is None:
        logger.debug("DEM cache miss for export (key %s)", cache_key[:8])
        return None

    dem_arr = cached[0]["dem"]  # np.ndarray (H, W)
    h, w = dem_arr.shape
    logger.info("DEM resolved from cache for export (key %s, %dx%d)", cache_key[:8], w, h)
    return dem_arr.ravel().tolist(), h, w


@dataclass
class ExportContext:
    """Typed container for parsed export parameters.

    Replaces the raw ``data`` dict, giving IDE
    autocompletion and catching typos at attribute-access time.
    """
    dem_values: List[float]
    height: int
    width: int
    model_height: float = 20.0
    base_height: float = 5.0
    exaggeration: float = 1.0
    sea_level_cap: bool = False
    walls: bool = True
    floor: bool = True
    name: str = "terrain"
    # Rendering overlays — used by _run_export_pipeline and generate_stl
    engrave_label: bool = False
    label_text: str = ""
    contours: bool = False
    contour_interval: float = 100.0
    contour_style: str = "engraved"

    @classmethod
    def from_request(cls, data: dict) -> "ExportContext":
        """Construct from an incoming request dict.

        Supports two modes:
        - **Legacy (array):** ``dem_values``, ``height``, ``width`` in the dict.
        - **Settings-only:** ``bbox`` + ``dem`` settings — DEM is read from
          the server-side disk cache (populated when the user loaded the DEM
          in the browser).  This avoids retransmitting multi-MB arrays.
        """
        dem_values = data.get("dem_values", [])
        height = data.get("height", 0)
        width = data.get("width", 0)

        # Settings-only mode: resolve DEM from cache
        if not dem_values:
            resolved = resolve_dem_from_cache(data)
            if resolved is not None:
                dem_values, height, width = resolved

        name = data.get("name", "terrain")
        return cls(
            dem_values=dem_values,
            height=height,
            width=width,
            model_height=float(data.get("model_height", 20)),
            base_height=float(data.get("base_height", 5)),
            exaggeration=float(data.get("exaggeration", 1.0)),
            sea_level_cap=bool(data.get("sea_level_cap", False)),
            walls=bool(data.get("walls", True)),
            floor=bool(data.get("floor", True)),
            name=name,
            engrave_label=bool(data.get("engrave_label", False)),
            label_text=data.get("label_text", name),
            contours=bool(data.get("contours", False)),
            contour_interval=float(data.get("contour_interval", 100)),
            contour_style=data.get("contour_style", "engraved"),
        )


@dataclass
class PuzzleContext:
    """Puzzle-splitting parameters, extracted from a request dict."""
    split_cols: int = 3
    split_rows: int = 3
    connector_mm: float = 50.0
    connectors_n: int = 10
    border_h: float = 1.0
    border_off: float = 5.0
    include_border: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "PuzzleContext":
        return cls(
            split_cols=int(d.get("split_cols", 3)),
            split_rows=int(d.get("split_rows", 3)),
            connector_mm=float(d.get("connector_size_mm", 50)),
            connectors_n=int(d.get("connectors_per_edge", 10)),
            border_h=float(d.get("border_height_mm", 1.0)),
            border_off=float(d.get("border_offset_mm", 5.0)),
            include_border=bool(d.get("include_border", True)),
        )

    def validate(self) -> str | None:
        """Return an error message if invalid, else None."""
        if self.split_cols < 1 or self.split_rows < 1:
            return "split_cols and split_rows must be >= 1"
        if self.split_cols * self.split_rows > 64:
            return "Maximum 64 pieces (cols * rows <= 64)"
        return None


def _prepare_dem_array(
    dem_values: list,
    height: int,
    width: int,
    model_height: float,
    base_height: float,
    exaggeration: float,
    sea_level_cap: bool,
) -> tuple[np.ndarray, float, float]:
    """
    Reshape, exaggerate, sea-level-clip, normalise, and add base to a DEM array.
    Returns (im, im_min_orig, im_max_orig) where im is in model-mm space with
    base added, and im_min/max are the original (pre-normalisation) extents.
    """
    im = np.array(dem_values, dtype=np.float64).reshape(height, width)
    im = im * exaggeration

    if sea_level_cap:
        im = np.minimum(im, 0.0)

    im_min = float(np.nanmin(im))
    im_max = float(np.nanmax(im))
    if im_max > im_min:
        im = (im - im_min) / (im_max - im_min) * model_height

    im = im + base_height
    return im, im_min, im_max


def _numpy2stl_mesh(im: np.ndarray, walls: bool = True, floor: bool = True) -> tuple:
    """Convert a DEM array to a (vertices, faces) mesh."""
    return array_to_mesh(im, floor_val=0, walls=walls, floor=floor)


def _repair_and_export(vertices, faces, suffix: str) -> tuple:
    """Repair mesh with trimesh and write to a temp file. Returns (path, mesh)."""
    mesh = tm.Trimesh(vertices=vertices, faces=faces, process=False)
    tm.repair.fill_holes(mesh)
    tm.repair.fix_normals(mesh)
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = tf.name
    tf.close()
    mesh.export(path, file_type=suffix.lstrip('.'))
    return path, mesh


def _temp_file_response(path: str, filename: str, extra_headers: dict | None = None) -> FileResponse:
    """Return a self-cleaning FileResponse for a temp file."""
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    if extra_headers:
        headers.update(extra_headers)
    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, path),
        headers=headers,
    )


def _apply_label_engraving(im: np.ndarray, label_text: str, base_height: float) -> np.ndarray:
    """Engrave a text label onto the bottom strip of the DEM array. Returns modified im."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        h_arr, w_arr = im.shape
        font_size = max(6, h_arr // 25)
        label_img = Image.new("L", (w_arr, h_arr), 0)
        draw = ImageDraw.Draw(label_img)
        try:
            font = ImageFont.truetype("arial.ttf", size=font_size)
        except Exception:
            font = ImageFont.load_default()
        strip_start = int(h_arr * 0.92)
        draw.text((4, strip_start), label_text[:40], fill=255, font=font)
        label_mask = np.array(label_img, dtype=np.float32) / 255.0
        engrave_depth = min(1.5, base_height * 0.3)
        im = np.maximum(im - label_mask * engrave_depth, 0.1)
        logger.info(f"Label engraved: '{label_text}' depth={engrave_depth:.2f}mm")
    except Exception as e:
        logger.warning(f"Label engraving failed (non-fatal): {e}")
    return im


def _apply_contour_lines(
    im: np.ndarray,
    im_min: float,
    im_max: float,
    model_height: float,
    base_height: float,
    contour_interval: float,
    contour_style: str,
) -> np.ndarray:
    """Engrave or emboss contour lines onto the DEM array. Returns modified im."""
    try:
        elev_range = im_max - im_min
        if elev_range <= 0:
            return im
        interval_mm = (contour_interval / elev_range) * model_height
        line_width_mm = max(0.3, interval_mm * 0.06)
        phase = ((im - base_height) % interval_mm) / interval_mm
        band_half = line_width_mm / interval_mm / 2.0
        on_contour = phase < band_half
        on_contour |= phase > (1.0 - band_half)
        index_interval_mm = interval_mm * 5.0
        index_phase = ((im - base_height) % index_interval_mm) / index_interval_mm
        index_band = (index_phase < band_half * 2) | (index_phase > (1.0 - band_half * 2))
        depth = line_width_mm * 0.8
        index_depth = depth * 2.0
        if contour_style == "engraved":
            im = np.where(index_band, np.maximum(im - index_depth, base_height * 0.5),
                          np.where(on_contour, np.maximum(im - depth, base_height * 0.5), im))
        else:
            im = np.where(index_band, im + index_depth,
                          np.where(on_contour, im + depth, im))
        logger.info(f"Contours: interval={contour_interval}m ({interval_mm:.2f}mm), style={contour_style}")
    except Exception as e:
        logger.warning(f"Contour generation failed (non-fatal): {e}")
    return im


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_stl(data: dict):
    """Generate an STL file from DEM data. Returns a FastAPI FileResponse."""
    p = ExportContext.from_request(data)

    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    im, im_min, im_max = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )

    if p.engrave_label and p.label_text:
        im = _apply_label_engraving(im, p.label_text, p.base_height)

    if p.contours and p.contour_interval > 0:
        im = _apply_contour_lines(im, im_min, im_max, p.model_height,
                                  p.base_height, p.contour_interval, p.contour_style)

    vertices, faces = _numpy2stl_mesh(im, walls=p.walls, floor=p.floor)
    temp_path, mesh = _repair_and_export(vertices, faces, ".stl")
    is_watertight = bool(mesh.is_watertight)
    face_count = len(mesh.faces)
    logger.info(f"STL generated: {face_count} faces, watertight={is_watertight}")
    return _temp_file_response(temp_path, f"{p.name}.stl", {
        "X-Watertight": str(is_watertight).lower(),
        "X-Face-Count": str(face_count),
        "Access-Control-Expose-Headers": "X-Watertight, X-Face-Count",
    })


def _generate_mesh_file(data: dict, fmt: str):
    """Shared body for OBJ and 3MF single-mesh exports."""
    p = ExportContext.from_request(data)
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)
    im, _, _ = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )
    vertices, faces = array_to_mesh(im, floor_val=0, walls=p.walls, floor=p.floor)
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=f".{fmt}")
    temp_path = tf.name
    tf.close()
    if fmt == "obj":
        writeOBJ(temp_path, {p.name: (vertices, faces)})
    else:
        write3MF(temp_path, {p.name: (vertices, faces)})
    logger.info("%s generated: %d vertices, %d faces", fmt.upper(), len(vertices), len(faces))
    return _temp_file_response(temp_path, f"{p.name}.{fmt}")


def generate_obj(data: dict):
    """Generate an OBJ file from DEM data. Returns a FastAPI FileResponse."""
    return _generate_mesh_file(data, "obj")


def generate_3mf(data: dict):
    """Generate a 3MF file from DEM data. Returns a FastAPI FileResponse."""
    return _generate_mesh_file(data, "3mf")


def generate_mesh_preview(data: dict):
    """
    Run the numpy2stl pipeline and return vertices + faces as JSON for the
    in-browser 3-D viewer.  Uses solid=False (top surface only) to keep the
    payload small; the full solid is built only when the user exports.
    """
    p = ExportContext.from_request(data)
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    im, im_min, im_max = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )

    vertices, faces = array_to_mesh(im, floor_val=0, walls=p.walls, floor=p.floor)
    logger.info(f"Preview mesh: {len(vertices)} vertices, {len(faces)} faces")

    # Round col/row to integers and z to 2 dp — sufficient for display, halves JSON size.
    v_rounded = vertices.copy()
    v_rounded[:, :2] = np.round(v_rounded[:, :2]).astype(np.int32)
    v_rounded[:, 2]  = np.round(v_rounded[:, 2], 2)

    return JSONResponse(content={
        "vertices":     v_rounded.tolist(),
        "faces":        faces.tolist(),
        "face_count":   int(len(faces)),
        "model_height": p.model_height,
        "base_height":  p.base_height,
        "z_min":        round(float(im.min()), 2),
        "z_max":        round(float(im.max()), 2),
        "cols":         int(p.width),
        "rows":         int(p.height),
        "dim":          int(data.get("dim", 600)),
    })


def generate_puzzle_3mf(data: dict, task: ExportTask | None = None):
    """Split a DEM into N×M pieces with alignment tabs and export as 3MF.

    Each piece is a watertight solid mesh. Adjacent pieces have interlocking
    tab/slot connectors on their shared edges so the printed tiles snap
    together.  All pieces are packed into a single 3MF file as separate
    named objects.

    Parameters (in *data* dict)
    ---------------------------
    dem_values, height, width, model_height, base_height, exaggeration,
    sea_level_cap, name — same as other export functions.
    split_cols : int   — columns in the puzzle grid (X).
    split_rows : int   — rows in the puzzle grid (Y).
    connector_size_mm : float — width of each tab/slot connector (mm).
    connectors_per_edge : int — number of connectors per shared edge.
    border_height_mm : float — raised lip height around each piece base.
    border_offset_mm : float — inset of lip from piece edge.
    include_border : bool — whether to add the raised lip.
    """
    def _progress(pct, msg):
        if task:
            task.update(pct, msg)

    p = ExportContext.from_request(data)
    if not p.dem_values or not p.height or not p.width:
        if task:
            task.fail("Missing DEM data")
            return None
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    pz = PuzzleContext.from_dict(data)
    err = pz.validate()
    if err:
        if task:
            task.fail(err)
            return None
        return JSONResponse(content={"error": err}, status_code=400)

    _progress(5, "Preparing DEM array...")
    im, _, _ = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )

    H, W = im.shape
    # Tab geometry in pixel space
    tab_depth_px = max(2, int(round(pz.connectors_n * 0.5)))

    models = {}
    total = pz.split_cols * pz.split_rows
    for row in range(pz.split_rows):
        for col in range(pz.split_cols):
            idx = row * pz.split_cols + col
            _progress(10 + int(80 * idx / total),
                      f"Generating piece {idx + 1}/{total}...")

            # Slice boundaries
            r0 = int(round(row * H / pz.split_rows))
            r1 = int(round((row + 1) * H / pz.split_rows))
            c0 = int(round(col * W / pz.split_cols))
            c1 = int(round((col + 1) * W / pz.split_cols))

            piece = im[r0:r1, c0:c1].copy()

            piece = _add_alignment_features(
                piece, row, col, pz.split_rows, pz.split_cols,
                tab_depth_px, p.base_height, pz.border_h if pz.include_border else 0,
            )

            vertices, faces = array_to_mesh(piece)

            # Offset vertices to world position so pieces don't overlap
            # when loaded in a slicer
            if len(vertices) > 0:
                vertices[:, 0] += c0  # X offset
                vertices[:, 1] += r0  # Y offset

            mesh = tm.Trimesh(vertices=vertices, faces=faces, process=False)
            tm.repair.fill_holes(mesh)
            tm.repair.fix_normals(mesh)

            piece_name = f"{p.name}_r{row}c{col}"
            models[piece_name] = (mesh.vertices, mesh.faces)
            logger.info("Piece %s: %d verts, %d faces",
                        piece_name, len(mesh.vertices), len(mesh.faces))

    _progress(92, "Writing 3MF...")
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".3mf")
    temp_path = tf.name
    tf.close()
    write3MF(temp_path, models)

    total_faces = sum(len(f) for _, f in models.values())
    logger.info("Puzzle 3MF: %d pieces, %d total faces", len(models), total_faces)

    puzzle_filename = f"{p.name}_puzzle.3mf"
    headers = {
        "X-Piece-Count": str(len(models)),
        "X-Total-Faces": str(total_faces),
        "Access-Control-Expose-Headers": "X-Piece-Count, X-Total-Faces",
    }
    if task:
        task.complete(temp_path, puzzle_filename, headers)
        return None
    return _temp_file_response(temp_path, puzzle_filename, headers)


def _add_alignment_features(
    piece: np.ndarray,
    row: int, col: int,
    n_rows: int, n_cols: int,
    tab_depth_px: int,
    base_height: float,
    border_height: float,
) -> np.ndarray:
    """Add tab protrusions and slot indentations to piece edges.

    Convention: even-index edges get tabs (raised), odd-index edges get
    slots (lowered).  Exterior edges are left flat.
    """
    ph, pw = piece.shape
    tab_h = base_height * 0.4      # tab protrusion height (mm)
    slot_depth = base_height * 0.35  # slot depth (mm) — slightly less for clearance
    depth = min(tab_depth_px, max(2, ph // 10), max(2, pw // 10))

    # Right edge: tab if col is even, slot if col is odd (skip last column)
    if col < n_cols - 1:
        _apply_edge_tabs(piece[:, -depth:], is_tab=(col % 2 == 0), tab_h=tab_h, slot_depth=slot_depth)

    # Left edge: match right edge of left neighbour
    if col > 0:
        _apply_edge_tabs(piece[:, :depth], is_tab=(col % 2 != 0), tab_h=tab_h, slot_depth=slot_depth)

    # Bottom edge: tab if row is even, slot if row is odd (skip last row)
    if row < n_rows - 1:
        _apply_edge_tabs(piece[-depth:, :], is_tab=(row % 2 == 0), tab_h=tab_h, slot_depth=slot_depth)

    # Top edge: match bottom edge of upper neighbour
    if row > 0:
        _apply_edge_tabs(piece[:depth, :], is_tab=(row % 2 != 0), tab_h=tab_h, slot_depth=slot_depth)

    return piece


def _apply_edge_tabs(arr_slice: np.ndarray, is_tab: bool, tab_h: float, slot_depth: float) -> None:
    """Modify a 2D slice in-place: raise for tabs (protrusions), lower for slots."""
    _h, w = arr_slice.shape
    n_tabs = max(1, min(3, w // 8))
    tab_w = max(2, w // (n_tabs * 3))
    spacing = w // (n_tabs + 1)
    for t in range(n_tabs):
        cx = spacing * (t + 1)
        x0 = max(0, cx - tab_w // 2)
        x1 = min(w, cx + tab_w // 2)
        if is_tab:
            arr_slice[:, x0:x1] += tab_h
        else:
            arr_slice[:, x0:x1] = np.maximum(arr_slice[:, x0:x1] - slot_depth, 0.1)


def _apply_edge_tabs_v(arr_slice: np.ndarray, is_tab: bool, tab_h: float, slot_depth: float) -> None:
    """Backward-compatible alias used by tests and older callers."""
    _apply_edge_tabs(arr_slice, is_tab=is_tab, tab_h=tab_h, slot_depth=slot_depth)


def _apply_edge_tabs_h(arr_slice: np.ndarray, is_tab: bool, tab_h: float, slot_depth: float) -> None:
    """Backward-compatible alias used by tests and older callers."""
    _apply_edge_tabs(arr_slice, is_tab=is_tab, tab_h=tab_h, slot_depth=slot_depth)


def generate_crosssection(data: dict):
    """Generate a cross-section STL along a lat or lon cut line. Returns a FastAPI FileResponse."""
    p = ExportContext.from_request(data)
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    north = float(data.get('north', 0))
    south = float(data.get('south', 0))
    east = float(data.get('east', 0))
    west = float(data.get('west', 0))
    cut_axis = data.get('cut_axis', 'lat')
    cut_value = float(data.get('cut_value', (north + south) / 2))
    thickness_mm = float(data.get('thickness_mm', 5))

    im = np.array(p.dem_values, dtype=np.float32).reshape(p.height, p.width) * p.exaggeration

    if cut_axis == 'lat':
        row = int(np.clip((north - cut_value) / (north - south) * p.height, 0, p.height - 1))
        profile = im[row, :]
        label_axis = f"lat{cut_value:.4f}"
    else:
        col = int(np.clip((cut_value - west) / (east - west) * p.width, 0, p.width - 1))
        profile = im[:, col]
        label_axis = f"lon{cut_value:.4f}"

    p_min = float(np.nanmin(profile))
    p_max = float(np.nanmax(profile))
    if p_max > p_min:
        profile = (profile - p_min) / (p_max - p_min) * p.model_height
    profile = profile + p.base_height

    thickness_px = max(3, int(round(thickness_mm)))
    im_cross = np.tile(profile, (thickness_px, 1)).astype(np.float32)

    vertices, faces = _numpy2stl_mesh(im_cross)
    temp_path, _ = _repair_and_export(vertices, faces, '.stl')

    fname = f"{p.name}_cross_{label_axis}.stl"
    logger.info(f"Cross-section STL: {len(profile)} profile points, {thickness_px}mm slab")
    return _temp_file_response(temp_path, fname)
