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
import tempfile

import numpy as np
from starlette.background import BackgroundTask

from app.server.core.export_params import (
    _parse_export_params,
    resolve_dem,
)
from app.server.core.export_tasks import ExportTask

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
    if len(p.dem_values) != p.height * p.width:
        task.fail(f"DEM has {len(p.dem_values)} values, expected "
                  f"{p.height} x {p.width}")
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
        # Relief is model_height * exaggeration mm, so the contour spacing has
        # to be computed against that same height rather than model_height alone.
        im = _apply_contour_lines(im, im_min, im_max, p.model_height * p.exaggeration,
                                  p.base_height, contour_interval, contour_style)

    # Step 4: Mesh generation (heaviest step)
    task.update(45, "Generating mesh...")
    if fmt == "obj":
        from numpy2stl import writeOBJ
    elif fmt == "3mf":
        from numpy2stl import write3MF
    vertices, faces = _grid_mesh(im, mm_per_pixel=p.mm_per_pixel)

    # Repair runs for every format now. OBJ and 3MF used to be written straight
    # from array_to_mesh, so an interior boundary loop - a NaN DEM cell drops the
    # quads around it - shipped as a hole in what is supposed to be a closed
    # solid. Watertightness is measured after the repair and reported on all
    # three formats rather than STL alone.
    task.update(70, "Repairing mesh...")
    mesh = _repair_mesh(vertices, faces)
    vertices, faces = mesh.vertices, mesh.faces
    is_watertight = bool(mesh.is_watertight)
    face_count = int(len(mesh.faces))
    if not is_watertight:
        logger.warning("%s mesh is not watertight after repair (%d faces)",
                       fmt.upper(), face_count)
    mesh_headers = _quality_headers(is_watertight, face_count, p.composite_error)

    # Step 5: Export to file
    suffix = f".{fmt}"
    if fmt in ("stl",):
        temp_path = _write_mesh(mesh, suffix)
        headers = {**_disposition(f"{p.name}.stl"), **mesh_headers}
        logger.info("STL generated: %d faces, watertight=%s", face_count, is_watertight)
    elif fmt == "obj":
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".obj")
        temp_path = tf.name
        tf.close()
        writeOBJ(temp_path, {p.name: (vertices, faces)})
        headers = {**_disposition(f"{p.name}.obj"), **mesh_headers}
        logger.info("OBJ generated: %d vertices, %d faces, watertight=%s",
                    len(vertices), len(faces), is_watertight)
    elif fmt == "3mf":
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".3mf")
        temp_path = tf.name
        tf.close()
        write3MF(temp_path, {p.name: (vertices, faces)})
        headers = {**_disposition(f"{p.name}.3mf"), **mesh_headers}
        logger.info("3MF generated: %d vertices, %d faces, watertight=%s",
                    len(vertices), len(faces), is_watertight)
    else:
        task.fail(f"Unknown format: {fmt}")
        return

    task.update(95, "Finalizing...")
    task.complete(temp_path, f"{p.name}.{fmt}", headers)


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
    Reshape, sea-level-clip, normalise, exaggerate, and add base to a DEM array.

    Returns ``(im, im_min, im_max)``. ``im`` is in model-mm space with the base
    added; ``im_min``/``im_max`` are the source extents in metres, which is what
    the contour spacing needs in order to convert a contour interval in metres
    into millimetres.

    Vertical relief comes out as ``model_height * exaggeration`` millimetres.
    Exaggeration is applied *after* the normalisation, not before, because a
    positive constant cancels exactly through a min-max normalisation:
    ``(k*x - k*min) / (k*max - k*min) == (x - min) / (max - min)``. Applying it
    first, as this did, left the slider with no effect at all on the exported
    mesh. Reordering the clamp does not help either - both ``min(k*x, 0)`` and
    ``max(k*x, 0)`` equal ``k`` times the unexaggerated clamp for ``k > 0`` - so
    the multiplication has to land on the far side of the normalisation.
    """
    im = np.array(dem_values, dtype=np.float64).reshape(height, width)

    # JSON nulls arrive as NaN (projection edges, inline values). One NaN made
    # array_to_mesh's mask value NaN, every comparison against it False, and the
    # mesh empty - an 84-byte STL reported as watertight. Fill them with the
    # lowest real elevation so they print as the floor of the relief.
    nan_mask = ~np.isfinite(im)
    if nan_mask.all():
        raise ValueError("DEM contains no finite elevation values")
    if nan_mask.any():
        im[nan_mask] = float(np.nanmin(np.where(nan_mask, np.nan, im)))

    if sea_level_cap:
        # Raise everything below sea level up to zero, so the ocean floor prints
        # as a flat sea. This was np.minimum, which did the exact opposite: it
        # flattened every piece of land to zero and kept only the trenches.
        im = np.maximum(im, 0.0)

    im_min = float(np.nanmin(im))
    im_max = float(np.nanmax(im))
    if im_max > im_min:
        im = (im - im_min) / (im_max - im_min) * model_height * exaggeration
    else:
        # A genuinely flat region (a lake, a salt pan) is legitimate. Emit a
        # flat plate rather than dividing by zero.
        im = np.zeros_like(im)

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


def _north_up(vertices: np.ndarray, faces: np.ndarray, n_rows: int) -> tuple:
    """Put DEM row 0 (north) at +Y, where a slicer shows the back of the bed.

    array_to_mesh maps row index straight to y, so north landed at y = 0 and
    the printed model was the mirror image of the map, engraved label
    included. Reflecting y reverses the handedness, so the triangle winding is
    reversed with it to keep normals pointing outward. The in-browser viewer
    maps rows itself and does not go through this.
    """
    if len(vertices):
        vertices[:, 1] = (n_rows - 1) - vertices[:, 1]
        faces = np.ascontiguousarray(faces[:, ::-1])
    return vertices, faces


def _grid_mesh(im: np.ndarray, mm_per_pixel: float = 1.0) -> tuple:
    """The single DEM-to-mesh step every file export uses: build, orient, scale.

    ``floor_val=0`` puts the bottom cap at z=0. Without it array_to_mesh floors
    one unit below the surface minimum, which made the base thickness setting
    inert - every solid came out ``model_height + 1`` mm tall.
    """
    from numpy2stl import array_to_mesh
    vertices, faces = array_to_mesh(im, floor_val=0.0)
    vertices, faces = _north_up(vertices, faces, im.shape[0])
    return _scale_xy(vertices, mm_per_pixel), faces


def _repair_mesh(vertices, faces):
    """Return the mesh as a trimesh, repairing it only if it needs it.

    array_to_mesh builds a closed, consistently wound solid, so fill_holes and
    fix_normals - about half the export time at dim 1200 - almost always
    changed nothing. ``is_volume`` checks watertightness, winding consistency
    and positive volume in one pass; only a mesh that fails it is repaired.
    """
    import trimesh as tm
    mesh = tm.Trimesh(vertices=vertices, faces=faces, process=False)
    if len(mesh.faces) == 0:
        raise ValueError("Mesh generation produced no faces")
    if not mesh.is_volume:
        tm.repair.fill_holes(mesh)
        tm.repair.fix_normals(mesh)
    return mesh


def _write_mesh(mesh, suffix: str) -> str:
    """Write a trimesh to a temp file with the given suffix. Returns the path."""
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = tf.name
    tf.close()
    mesh.export(path, file_type=suffix.lstrip('.'))
    return path


def _repair_and_export(vertices, faces, suffix: str):
    """Repair a mesh and write it out. Returns (temp file path, mesh)."""
    mesh = _repair_mesh(vertices, faces)
    return _write_mesh(mesh, suffix), mesh


def _prepare_export_mesh(p, data: dict):
    """Shared DEM-to-mesh path for the direct export routes.

    generate_stl, generate_obj and generate_3mf each inlined their own copy of
    this sequence, and the OBJ and 3MF copies had drifted off it: they skipped
    the label engraving, the contour lines and the trimesh repair entirely, so
    the same request produced a different model depending on the file extension.
    Returns the repaired mesh along with its vertices and faces.
    """
    from numpy2stl import array_to_mesh  # noqa: F401

    im, im_min, im_max = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )

    engrave_label = bool(data.get("engrave_label", False))
    label_text = data.get("label_text", p.name)
    if engrave_label and label_text:
        im = _apply_label_engraving(im, label_text, p.base_height)

    contours = bool(data.get("contours", False))
    contour_interval = float(data.get("contour_interval", 100))
    contour_style = data.get("contour_style", "engraved")
    if contours and contour_interval > 0:
        im = _apply_contour_lines(im, im_min, im_max, p.model_height * p.exaggeration,
                                  p.base_height, contour_interval, contour_style)

    vertices, faces = _grid_mesh(im, mm_per_pixel=p.mm_per_pixel)
    mesh = _repair_mesh(vertices, faces)
    return mesh, mesh.vertices, mesh.faces


def _disposition(filename: str) -> dict:
    """A Content-Disposition header that survives any region name.

    Headers are latin-1, so a non-ASCII name raised a 500, and a quote or
    semicolon in the name broke the header. The plain ``filename`` gets a
    sanitised ASCII fallback; ``filename*`` carries the real name (RFC 5987).
    """
    from urllib.parse import quote
    ascii_name = "".join(
        c if (c.isascii() and (c.isalnum() or c in "._- ")) else "_"
        for c in filename
    ) or "model"
    return {"Content-Disposition":
            f"attachment; filename=\"{ascii_name}\"; "
            f"filename*=UTF-8''{quote(filename, safe='')}"}


def _quality_headers(watertight: bool, face_count: int,
                     composite_error: str | None = None) -> dict:
    """The mesh-quality headers every export sends and the client reads back."""
    headers = {
        "X-Watertight": str(bool(watertight)).lower(),
        "X-Face-Count": str(int(face_count)),
        "Access-Control-Expose-Headers":
            "X-Watertight, X-Face-Count, X-Composite-Error",
    }
    if composite_error:
        headers["X-Composite-Error"] = composite_error.encode(
            "ascii", "replace").decode("ascii").replace("\n", " ")
    return headers


def _mesh_response_headers(name: str, ext: str, mesh,
                           composite_error: str | None = None) -> dict:
    """Content-Disposition plus the watertightness figures the client reads back."""
    watertight = bool(mesh.is_watertight)
    if not watertight:
        logger.warning("%s mesh is not watertight after repair (%d faces)",
                       ext.upper(), len(mesh.faces))
    return {**_disposition(f"{name}.{ext}"),
            **_quality_headers(watertight, len(mesh.faces), composite_error)}


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
        # Parenthesise both comparisons: | binds tighter than <, so this used to
        # evaluate float | ndarray and raise a TypeError that the surrounding
        # try/except swallowed, making contours a silent no-op in every export.
        index_band = ((index_phase < (band_half * 2))
                      | (index_phase > (1.0 - band_half * 2)))
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
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    mesh, _vertices, _faces = _prepare_export_mesh(p, data)
    temp_path = _write_mesh(mesh, ".stl")
    logger.info("STL generated: %d faces, watertight=%s",
                len(mesh.faces), mesh.is_watertight)

    return FileResponse(
        temp_path,
        filename=f"{p.name}.stl",
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, temp_path),
        headers=_mesh_response_headers(p.name, "stl", mesh, p.composite_error),
    )


def generate_obj(data: dict):
    """Generate an OBJ file from DEM data. Returns a FastAPI FileResponse."""
    from fastapi.responses import FileResponse, JSONResponse
    from numpy2stl import writeOBJ

    p = _parse_export_params(data)
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    mesh, vertices, faces = _prepare_export_mesh(p, data)

    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".obj")
    temp_path = tf.name
    tf.close()
    writeOBJ(temp_path, {p.name: (vertices, faces)})
    logger.info("OBJ generated: %d vertices, %d faces, watertight=%s",
                len(vertices), len(faces), mesh.is_watertight)

    return FileResponse(
        temp_path,
        filename=f"{p.name}.obj",
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, temp_path),
        headers=_mesh_response_headers(p.name, "obj", mesh, p.composite_error),
    )


def generate_3mf(data: dict):
    """Generate a 3MF file from DEM data. Returns a FastAPI FileResponse."""
    from fastapi.responses import FileResponse, JSONResponse
    from numpy2stl import write3MF

    p = _parse_export_params(data)
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    mesh, vertices, faces = _prepare_export_mesh(p, data)

    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".3mf")
    temp_path = tf.name
    tf.close()
    write3MF(temp_path, {p.name: (vertices, faces)})
    logger.info("3MF generated: %d vertices, %d faces, watertight=%s",
                len(vertices), len(faces), mesh.is_watertight)

    return FileResponse(
        temp_path,
        filename=f"{p.name}.3mf",
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, temp_path),
        headers=_mesh_response_headers(p.name, "3mf", mesh, p.composite_error),
    )


def generate_mesh_preview(data: dict):
    """
    Run the numpy2stl pipeline and return vertices + faces as JSON for the
    in-browser 3-D viewer.  Defaults to solid=True so the preview matches the
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
    # NaN-aware and vectorised: the Python min()/max() pair raised TypeError on
    # a JSON null and walked the list twice at Python speed.
    _vals = np.asarray(p.dem_values, dtype=np.float64)
    _finite = _vals[np.isfinite(_vals)]
    if _finite.size == 0 or _finite.min() == _finite.max():
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
        im = _apply_contour_lines(im, im_min, im_max, p.model_height * p.exaggeration,
                                  p.base_height, contour_interval, contour_style)

    # Default to a closed solid so the preview shows the floor and side walls
    # the exported file actually has. This defaulted to a bare top surface,
    # so the viewer rendered an open shell with nothing underneath it.
    solid = bool(data.get("solid", True))
    # Same floor as the file exports, so the preview shows the model that will
    # actually be written rather than one a millimetre taller.
    vertices, faces = array_to_mesh(im, solid=solid, floor_val=0.0)
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
        "exaggeration": p.exaggeration,
        "composite_error": p.composite_error,
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
    from numpy2stl import array_to_mesh, write3MF

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
    _border_off = float(data.get("border_offset_mm", 5.0))
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
    _tab_width_px = max(3, int(round(connector_mm / max(1, W / split_cols) * (W / split_cols) * 0.15)))

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

            vertices, faces = array_to_mesh(piece, floor_val=0.0)  # floor at z=0 so base_height is a real thickness

            # Offset vertices to world position so pieces don't overlap
            # when loaded in a slicer, then orient the whole layout north-up
            # the same way the single-piece export is.
            if len(vertices) > 0:
                vertices[:, 0] += c0  # X offset (still pixel units)
                vertices[:, 1] += r0  # Y offset
                vertices, faces = _north_up(vertices, faces, H)
                vertices = _scale_xy(vertices, p.mm_per_pixel)

            mesh = _repair_mesh(vertices, faces)

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
        **_disposition(f"{p.name}_puzzle.3mf"),
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
        """Left/right edge tabs, spaced down the rows. Modifies in place.

        The slice here is (piece height, tab depth), so the tabs belong along
        the height. This used to index the same axis as the top/bottom variant,
        which spread them across the handful of pixels of tab depth and left one
        ridge running the full length of the edge instead of discrete tabs.
        """
        h, w = arr_slice.shape
        n_tabs = max(1, min(3, h // 8))  # 1-3 tabs depending on edge length
        tab_len = max(2, h // (n_tabs * 3))  # each tab is ~1/3 of spacing
        spacing = h // (n_tabs + 1)
        for t in range(n_tabs):
            cy = spacing * (t + 1)
            y0 = max(0, cy - tab_len // 2)
            y1 = min(h, cy + tab_len // 2)
            if is_tab:
                arr_slice[y0:y1, :] += tab_h
            else:
                arr_slice[y0:y1, :] = np.maximum(
                    arr_slice[y0:y1, :] - slot_depth, 0.1)

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
        resolved = resolve_dem(data)
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
    if north <= south or east <= west:
        return JSONResponse(content={"error": "Invalid bbox for cross-section"},
                            status_code=400)

    im = np.array(dem_values, dtype=np.float32).reshape(height, width)

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
    # Exaggeration after the normalisation, as in _prepare_dem_array; applied
    # before it, a min-max normalise cancels it exactly.
    profile = np.nan_to_num(profile, nan=p_min)
    if p_max > p_min:
        profile = (profile - p_min) / (p_max - p_min) * model_height * exaggeration
    else:
        profile = np.zeros_like(profile)
    profile = profile + base_height

    # N rows of a grid span N-1 pixels, so the slab is thickness/mm_per_pixel
    # pixels plus one row. This rounded thickness_mm itself, which ignored the
    # horizontal scale entirely.
    thickness_px = max(2, int(round(thickness_mm / max(mm_per_pixel, 1e-6))) + 1)
    im_cross = np.tile(profile, (thickness_px, 1)).astype(np.float32)

    vertices, faces = _grid_mesh(im_cross, mm_per_pixel=mm_per_pixel)
    temp_path, _ = _repair_and_export(vertices, faces, '.stl')

    fname = f"{name}_cross_{label_axis}.stl"
    logger.info(f"Cross-section STL: {len(profile)} profile points, {thickness_px}mm slab")

    return FileResponse(
        temp_path,
        filename=fname,
        media_type="application/octet-stream",
        background=BackgroundTask(os.unlink, temp_path),
        headers=_disposition(fname),
    )
