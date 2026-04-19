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
from dataclasses import dataclass
from pathlib import Path
from typing import List

from starlette.background import BackgroundTask

import numpy as np

logger = logging.getLogger(__name__)

# strm2stl root dir (app/server/core/export.py → core → server → app → strm2stl)
_STRM2STL_DIR = Path(__file__).parent.parent.parent.parent
# Ensure local packages (numpy2stl, geo2stl) are importable without os.chdir.
if str(_STRM2STL_DIR) not in sys.path:
    sys.path.insert(0, str(_STRM2STL_DIR))


# ---------------------------------------------------------------------------
# Export parameter container
# ---------------------------------------------------------------------------

@dataclass
class ExportContext:
    """Typed container for parsed export parameters.

    Replaces the raw dict returned by _parse_export_params, giving IDE
    autocompletion and catching typos at attribute-access time.
    """
    dem_values: List[float]
    height: int
    width: int
    model_height: float = 20.0
    base_height: float = 5.0
    exaggeration: float = 1.0
    sea_level_cap: bool = False
    name: str = "terrain"

    @classmethod
    def from_request(cls, data: dict) -> "ExportContext":
        """Construct from an incoming request dict (the old _parse_export_params)."""
        return cls(
            dem_values=data.get("dem_values", []),
            height=data.get("height", 0),
            width=data.get("width", 0),
            model_height=float(data.get("model_height", 20)),
            base_height=float(data.get("base_height", 5)),
            exaggeration=float(data.get("exaggeration", 1.0)),
            sea_level_cap=bool(data.get("sea_level_cap", False)),
            name=data.get("name", "terrain"),
        )


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


def _numpy2stl_mesh(im: np.ndarray) -> tuple:
    """Convert a DEM array to a (vertices, faces) mesh."""
    from numpy2stl import array_to_mesh
    return array_to_mesh(im)


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


def _parse_export_params(data: dict) -> ExportContext:
    """Extract and type-cast the common export parameters from a request dict."""
    return ExportContext.from_request(data)


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

    vertices, faces = _numpy2stl_mesh(im)
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
    in-browser 3-D viewer.  Uses solid=False (top surface only) to keep the
    payload small; the full solid is built only when the user exports.
    """
    from fastapi.responses import JSONResponse
    from numpy2stl import array_to_mesh

    p = _parse_export_params(data)
    if not p.dem_values or not p.height or not p.width:
        return JSONResponse(content={"error": "Missing DEM data"}, status_code=400)

    im, im_min, im_max = _prepare_dem_array(
        p.dem_values, p.height, p.width,
        p.model_height, p.base_height, p.exaggeration, p.sea_level_cap,
    )

    vertices, faces = array_to_mesh(im, solid=False)
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
    })


def generate_crosssection(data: dict):
    """Generate a cross-section STL along a lat or lon cut line. Returns a FastAPI FileResponse."""
    from fastapi.responses import FileResponse, JSONResponse

    dem_values = data.get('dem_values', [])
    height = data.get('height', 0)
    width = data.get('width', 0)
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

    vertices, faces = _numpy2stl_mesh(im_cross)
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
