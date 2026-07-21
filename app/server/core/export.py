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
from pathlib import Path
from typing import List

from starlette.background import BackgroundTask

import numpy as np

# Re-export from refactored modules for backward compatibility
from app.server.core.export_tasks import (
    ExportTask,
    get_task_file,
    get_task_status,
    start_export_task,
)
from app.server.core.export_params import (
    ExportContext,
    resolve_dem_from_cache,
    _parse_export_params,
)

logger = logging.getLogger(__name__)


def _run_export_pipeline(data: dict, fmt: str, task: ExportTask) -> None:
    """Execute the full export pipeline with progress updates."""
    p = _parse_export_params(data)
    engrave_label = bool(data.get("engrave_label", False))
    label_text = data.get("label_text", p.name)
    contours = bool(data.get("contours", False))
    contour_interval = float(data.get("contour_interval", 100))
    contour_style = data.get("contour_style", "engraved")

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
    if engrave_label and label_text:
        task.update(25, "Engraving label...")
        im = _apply_label_engraving(im, label_text, p.base_height)

    # Step 3: Optional contours
    if contours and contour_interval > 0:
        task.update(35, "Generating contours...")
        im = _apply_contour_lines(im, im_min, im_max, p.model_height,
                                  p.base_height, contour_interval, contour_style)

    # Step 4: Mesh generation (heaviest step)
    task.update(45, "Generating mesh...")
    if fmt == "obj":
        from numpy2stl import array_to_mesh, writeOBJ
        vertices, faces = array_to_mesh(im)
        vertices = _scale_xy(vertices, p.mm_per_pixel)
    elif fmt == "3mf":
        from numpy2stl import array_to_mesh, write3MF
        vertices, faces = array_to_mesh(im)
        vertices = _scale_xy(vertices, p.mm_per_pixel)
    else:
        vertices, faces = _numpy2stl_mesh(im, mm_per_pixel=p.mm_per_pixel)

    task.update(70, "Repairing mesh...")

    # Step 5: Export to file
    suffix = f".{fmt}"
    if fmt in ("stl",):
        temp_path, mesh = _repair_and_export(vertices, faces, suffix)
        is_watertight = bool(mesh.is_watertight)
        face_count = len(mesh.faces)
        headers = {
            "Content-Disposition": f"attachment; filename={p.name}.stl",
            "X-Watertight": str(is_watertight).lower(),
            "X-Face-Count": str(face_count),
            "Access-Control-Expose-Headers": "X-Watertight, X-Face-Count",
        }
        logger.info("STL generated: %d faces, watertight=%s", face_count, is_watertight)
    elif fmt == "obj":
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".obj")
        temp_path = tf.name
        tf.close()
        writeOBJ(temp_path, {p.name: (vertices, faces)})
        headers = {"Content-Disposition": f"attachment; filename={p.name}.obj"}
        logger.info("OBJ generated: %d vertices, %d faces", len(vertices), len(faces))
    elif fmt == "3mf":
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".3mf")
        temp_path = tf.name
        tf.close()
        write3MF(temp_path, {p.name: (vertices, faces)})
        headers = {"Content-Disposition": f"attachment; filename={p.name}.3mf"}
        logger.info("3MF generated: %d vertices, %d faces", len(vertices), len(faces))
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


def _scale_xy(vertices: np.ndarray, mm_per_pixel: float) -> np.ndarray:
    """Scale x/y vertex columns from pixel-grid units to millimetres.

    numpy2stl returns x/y in pixel-index space and z in mm. To make a printed
    model where 1 DEM pixel maps to ``mm_per_pixel`` mm, we multiply x/y here.
    Returns the same array (mutated) for chaining.
    """
    if mm_per_pixel != 1.0:
        vertices[:, 0] = vertices[:, 0] * mm_per_pixel
        vertices[:, 1] = vertices[:, 1] * mm_per_pixel
    return vertices


def _numpy2stl_mesh(im: np.ndarray, mm_per_pixel: float = 1.0) -> tuple:
    """Convert a DEM array to a (vertices, faces) mesh, scaled to mm."""
    from numpy2stl import array_to_mesh
    vertices, faces = array_to_mesh(im)
    return _scale_xy(vertices, mm_per_pixel), faces


def _repair_and_export(vertices, faces, suffix: str) -> str:
    """Repair mesh with trimesh and write to a temp file. Returns temp file path."""
    import trimesh as tm
    mesh = tm.Trimesh(vertices=vertices, faces=faces, process=False)
    tm.repair.fill_holes(mesh)
    tm.repair.fix_normals(mesh)
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = tf.name
    tf.close()
    mesh.export(path, file_type=suffix.lstrip('.'))
    return path, mesh



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
        index_band = index_phase < (band_half * 2) | (index_phase > (1.0 - band_half * 2))
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
    from fastapi.responses import FileResponse, JSONResponse

    p = _parse_export_params(data)
    engrave_label   = bool(data.get("engrave_label", False))
    label_text      = data.get("label_text", p.name)
    contours        = bool(data.get("contours", False))
    contour_interval = float(data.get("contour_interval", 100))
    contour_style   = data.get("contour_style", "engraved")

    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    im, im_min, im_max = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )

    if engrave_label and label_text:
        im = _apply_label_engraving(im, label_text, p.base_height)

    if contours and contour_interval > 0:
        im = _apply_contour_lines(im, im_min, im_max, p.model_height,
                                  p.base_height, contour_interval, contour_style)

    vertices, faces = _numpy2stl_mesh(im, mm_per_pixel=p.mm_per_pixel)
    temp_path, mesh = _repair_and_export(vertices, faces, ".stl")
    is_watertight = bool(mesh.is_watertight)
    face_count = len(mesh.faces)
    logger.info(f"STL generated: {face_count} faces, watertight={is_watertight}")

    return FileResponse(
        temp_path,
        filename=f"{p.name}.stl",
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, temp_path),
        headers={
            "Content-Disposition": f"attachment; filename={p.name}.stl",
            "X-Watertight": str(is_watertight).lower(),
            "X-Face-Count": str(face_count),
            "Access-Control-Expose-Headers": "X-Watertight, X-Face-Count",
        },
    )


def generate_obj(data: dict):
    """Generate an OBJ file from DEM data. Returns a FastAPI FileResponse."""
    from fastapi.responses import FileResponse, JSONResponse
    from numpy2stl import array_to_mesh
    from numpy2stl import writeOBJ

    p = _parse_export_params(data)
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    im, _, _ = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )
    vertices, faces = array_to_mesh(im)
    vertices = _scale_xy(vertices, p.mm_per_pixel)

    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".obj")
    temp_path = tf.name
    tf.close()
    writeOBJ(temp_path, {p.name: (vertices, faces)})
    logger.info(f"OBJ generated: {len(vertices)} vertices, {len(faces)} faces")

    return FileResponse(
        temp_path,
        filename=f"{p.name}.obj",
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, temp_path),
        headers={"Content-Disposition": f"attachment; filename={p.name}.obj"},
    )


def generate_3mf(data: dict):
    """Generate a 3MF file from DEM data. Returns a FastAPI FileResponse."""
    from fastapi.responses import FileResponse, JSONResponse
    from numpy2stl import array_to_mesh
    from numpy2stl import write3MF

    p = _parse_export_params(data)
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    im, _, _ = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )
    vertices, faces = array_to_mesh(im)
    vertices = _scale_xy(vertices, p.mm_per_pixel)

    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".3mf")
    temp_path = tf.name
    tf.close()
    write3MF(temp_path, {p.name: (vertices, faces)})
    logger.info(f"3MF generated: {len(vertices)} vertices, {len(faces)} faces")

    return FileResponse(
        temp_path,
        filename=f"{p.name}.3mf",
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, temp_path),
        headers={"Content-Disposition": f"attachment; filename={p.name}.3mf"},
    )


def generate_mesh_preview(data: dict):
    """
    Run the numpy2stl pipeline and return vertices + faces as JSON for the
    in-browser 3-D viewer.  Defaults to solid=False (top surface only) for a
    light payload; client can request the full solid (walls + floor) by
    passing ``solid: true`` — matches what export will produce.
    """
    from fastapi.responses import JSONResponse
    from numpy2stl import array_to_mesh

    p = _parse_export_params(data)
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(
            content={
                "error": "Missing DEM data",
                "detail": (
                    "No DEM is cached for this region yet. Load the terrain "
                    "(Explore tab → Load DEM) before generating a model."
                ),
            },
            status_code=400,
        )

    # A flat DEM (all values equal) means the source had no elevation coverage
    # for this bbox — building a mesh from it produces a blank slab and usually
    # signals the local-SRTM 'no coverage' fallback. Return a clear reason.
    _vals = p.dem_values
    if min(_vals) == max(_vals):
        return JSONResponse(
            content={
                "error": "DEM has no elevation data",
                "detail": (
                    "The DEM for this region is flat (no relief). The local "
                    "elevation tiles likely don't cover this area. Try a "
                    "smaller region, or switch the DEM source to an "
                    "OpenTopography dataset (add a free API key in the Keys "
                    "panel)."
                ),
            },
            status_code=400,
        )

    im, im_min, im_max = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )

    # Mirror the same label/contour steps as _run_export_pipeline so the live
    # 3D preview matches what the file export will actually produce, instead
    # of only showing these effects after downloading.
    engrave_label = bool(data.get("engrave_label", False))
    label_text = data.get("label_text", p.name)
    if engrave_label and label_text:
        im = _apply_label_engraving(im, label_text, p.base_height)

    contours = bool(data.get("contours", False))
    contour_interval = float(data.get("contour_interval", 100))
    contour_style = data.get("contour_style", "engraved")
    if contours and contour_interval > 0:
        im = _apply_contour_lines(im, im_min, im_max, p.model_height,
                                  p.base_height, contour_interval, contour_style)

    solid = bool(data.get("solid", False))
    vertices, faces = array_to_mesh(im, solid=solid)
    logger.info(f"Preview mesh: {len(vertices)} vertices, {len(faces)} faces")

    # Vertices come back in pixel-grid units; client multiplies by mm_per_pixel
    # to display real mm. Keep payload integer-rounded for compactness.
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
        "mm_per_pixel": p.mm_per_pixel,
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
    from fastapi.responses import FileResponse, JSONResponse
    from numpy2stl import array_to_mesh
    from numpy2stl import write3MF

    def _progress(pct, msg):
        if task:
            task.update(pct, msg)

    p = _parse_export_params(data)
    if not p.dem_values or not p.height or not p.width:
        if task:
            task.fail("Missing DEM data")
            return None
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    split_cols = int(data.get("split_cols", 3))
    split_rows = int(data.get("split_rows", 3))
    connector_mm = float(data.get("connector_size_mm", 50))
    connectors_n = int(data.get("connectors_per_edge", 10))
    border_h = float(data.get("border_height_mm", 1.0))
    border_off = float(data.get("border_offset_mm", 5.0))
    include_border = bool(data.get("include_border", True))

    if split_cols < 1 or split_rows < 1:
        msg = "split_cols and split_rows must be >= 1"
        if task:
            task.fail(msg)
            return None
        return JSONResponse(content={"error": msg}, status_code=400)
    if split_cols * split_rows > 64:
        msg = "Maximum 64 pieces (cols * rows <= 64)"
        if task:
            task.fail(msg)
            return None
        return JSONResponse(content={"error": msg}, status_code=400)

    _progress(5, "Preparing DEM array...")
    im, _, _ = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )

    H, W = im.shape
    # Tab geometry in pixel space
    tab_depth_px = max(2, int(round(connectors_n * 0.5)))
    tab_width_px = max(3, int(round(connector_mm / max(1, W / split_cols) * (W / split_cols) * 0.15)))

    models = {}
    total = split_cols * split_rows
    for row in range(split_rows):
        for col in range(split_cols):
            idx = row * split_cols + col
            _progress(10 + int(80 * idx / total),
                      f"Generating piece {idx + 1}/{total}...")

            # Slice boundaries
            r0 = int(round(row * H / split_rows))
            r1 = int(round((row + 1) * H / split_rows))
            c0 = int(round(col * W / split_cols))
            c1 = int(round((col + 1) * W / split_cols))

            piece = im[r0:r1, c0:c1].copy()
            ph, pw = piece.shape

            # --- Alignment tabs ---
            # Add tabs (protrusions) on right/bottom edges of even-index
            # pieces, and matching slots (indentations) on left/top edges
            # of odd-index neighbours.
            piece = _add_alignment_features(
                piece, row, col, split_rows, split_cols,
                tab_depth_px, p.base_height, border_h if include_border else 0,
            )

            vertices, faces = array_to_mesh(piece)

            # Offset vertices to world position so pieces don't overlap
            # when loaded in a slicer
            if len(vertices) > 0:
                vertices[:, 0] += c0  # X offset (still pixel units)
                vertices[:, 1] += r0  # Y offset
                vertices = _scale_xy(vertices, p.mm_per_pixel)

            import trimesh as tm
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

    headers = {
        "Content-Disposition": f"attachment; filename={p.name}_puzzle.3mf",
        "X-Piece-Count": str(len(models)),
        "X-Total-Faces": str(total_faces),
        "Access-Control-Expose-Headers": "X-Piece-Count, X-Total-Faces",
    }

    if task:
        task.complete(temp_path, f"{p.name}_puzzle.3mf", headers)
        return None

    return FileResponse(
        temp_path,
        filename=f"{p.name}_puzzle.3mf",
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, temp_path),
        headers=headers,
    )


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
    tab_h = base_height * 0.4  # tab protrusion height (mm)
    slot_depth = base_height * 0.35  # slot depth (mm) — slightly less for clearance

    # Determine number and size of tabs along each edge
    def _apply_edge_tabs(arr_slice, is_tab):
        """Modify a 2D slice in-place: raise for tabs, lower for slots."""
        h, w = arr_slice.shape
        n_tabs = max(1, min(3, w // 8))  # 1-3 tabs depending on edge length
        tab_w = max(2, w // (n_tabs * 3))  # each tab is ~1/3 of spacing
        spacing = w // (n_tabs + 1)
        for t in range(n_tabs):
            cx = spacing * (t + 1)
            x0 = max(0, cx - tab_w // 2)
            x1 = min(w, cx + tab_w // 2)
            if is_tab:
                arr_slice[:, x0:x1] += tab_h
            else:
                arr_slice[:, x0:x1] = np.maximum(
                    arr_slice[:, x0:x1] - slot_depth, 0.1)

    depth = min(tab_depth_px, max(2, ph // 10), max(2, pw // 10))

    # Right edge: tab if col is even, slot if col is odd (skip last column)
    if col < n_cols - 1:
        edge = piece[:, -depth:]
        _apply_edge_tabs(edge, is_tab=(col % 2 == 0))

    # Left edge: match right edge of left neighbour
    if col > 0:
        edge = piece[:, :depth]
        _apply_edge_tabs(edge, is_tab=(col % 2 != 0))

    # Bottom edge: tab if row is even, slot if row is odd (skip last row)
    if row < n_rows - 1:
        edge = piece[-depth:, :]
        _apply_edge_tabs_v(edge, is_tab=(row % 2 == 0),
                           tab_h=tab_h, slot_depth=slot_depth)

    # Top edge: match bottom edge of upper neighbour
    if row > 0:
        edge = piece[:depth, :]
        _apply_edge_tabs_v(edge, is_tab=(row % 2 != 0),
                           tab_h=tab_h, slot_depth=slot_depth)

    return piece


def _apply_edge_tabs_v(arr_slice, is_tab, tab_h, slot_depth):
    """Vertical (row) edge tabs — tabs run along columns."""
    h, w = arr_slice.shape
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
            arr_slice[:, x0:x1] = np.maximum(
                arr_slice[:, x0:x1] - slot_depth, 0.1)


def generate_crosssection(data: dict):
    """Generate a cross-section STL along a lat or lon cut line. Returns a FastAPI FileResponse."""
    from fastapi.responses import FileResponse, JSONResponse

    dem_values = data.get('dem_values', [])
    height = data.get('height', 0)
    width = data.get('width', 0)

    # Settings-only mode: resolve DEM from cache
    if not dem_values:
        resolved = resolve_dem_from_cache(data)
        if resolved is not None:
            dem_values, height, width = resolved

    north = float(data.get('north', 0))
    south = float(data.get('south', 0))
    east = float(data.get('east', 0))
    west = float(data.get('west', 0))
    cut_axis = data.get('cut_axis', 'lat')
    cut_value = float(data.get('cut_value', (north + south) / 2))
    model_height = float(data.get('model_height', 20))
    base_height = float(data.get('base_height', 3))
    exaggeration = float(data.get('exaggeration', 1.0))
    thickness_mm = float(data.get('thickness_mm', 5))
    mm_per_pixel = float(data.get('mm_per_pixel', 1.0))
    name = data.get('name', 'crosssection')

    if not dem_values or not height or not width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    im = np.array(dem_values, dtype=np.float32).reshape(height, width) * exaggeration

    if cut_axis == 'lat':
        row = int(np.clip((north - cut_value) / (north - south) * height, 0, height - 1))
        profile = im[row, :]
        label_axis = f"lat{cut_value:.4f}"
    else:
        col = int(np.clip((cut_value - west) / (east - west) * width, 0, width - 1))
        profile = im[:, col]
        label_axis = f"lon{cut_value:.4f}"

    p_min = float(np.nanmin(profile))
    p_max = float(np.nanmax(profile))
    if p_max > p_min:
        profile = (profile - p_min) / (p_max - p_min) * model_height
    profile = profile + base_height

    thickness_px = max(3, int(round(thickness_mm)))
    im_cross = np.tile(profile, (thickness_px, 1)).astype(np.float32)

    vertices, faces = _numpy2stl_mesh(im_cross, mm_per_pixel=mm_per_pixel)
    temp_path, _ = _repair_and_export(vertices, faces, '.stl')

    fname = f"{name}_cross_{label_axis}.stl"
    logger.info(f"Cross-section STL: {len(profile)} profile points, {thickness_px}mm slab")

    return FileResponse(
        temp_path,
        filename=fname,
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, temp_path),
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
