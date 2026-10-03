"""
core/export.py — STL / OBJ / 3MF / puzzle / preview / cross-section generation.

Every mesh export runs the same two stages:

1. **Terrain stage** (:func:`terrain_stage`, raster): the DEM from the request
   (composite spec, edited values or ``dem_id``), median filter, sea-level cap,
   vertical scale, label engraving and contour lines -> one heightfield in mm.
2. **Feature stage** (``city2stl.city_model.build_on_terrain``, vector): OSM
   layers extruded / draped / cut on that heightfield and merged in 3D. A
   terrain-only export is this stage with no layers, so the STL, OBJ, 3MF,
   puzzle and city exports all share one adaptive, watertight terrain mesh.

The in-browser preview uses the terrain stage too, meshed as a plain grid (fast).
Each function accepts a plain dict (pre-parsed JSON body).
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from starlette.background import BackgroundTask

from app.server.core.export_params import (
    ExportContext,
    _parse_export_params,
    resolve_dem,
)
from app.server.core.export_tasks import ExportTask

if TYPE_CHECKING:
    from city2stl.city_model import ModelScale

logger = logging.getLogger(__name__)


def _run_export_pipeline(data: dict, fmt: str, task: ExportTask) -> None:
    """Execute the full export pipeline with progress updates."""
    p = _parse_export_params(data)
    if not p.dem_values or not p.height or not p.width:
        task.fail("Missing DEM data")
        return
    if len(p.dem_values) != p.height * p.width:
        task.fail(f"DEM has {len(p.dem_values)} values, expected "
                  f"{p.height} x {p.width}")
        return

    task.update(10, "Preparing terrain...")
    mesh = _prepare_export_mesh(p, data, progress=task.update)
    vertices, faces = mesh.vertices, mesh.faces
    if fmt == "obj":
        from numpy2stl import writeOBJ
    elif fmt == "3mf":
        from numpy2stl import write3MF
    is_watertight = _watertight(mesh)
    face_count = int(len(mesh.faces))
    if not is_watertight:
        logger.warning("%s mesh is not watertight (%d faces)", fmt.upper(), face_count)
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


# TerrainField.water value of the open sea (carve depths are 0..1).
WATER_SEA = 2.0

# Narrowest river channel on the print with river_depth_mm: a 0.4 mm nozzle cannot
# draw a 1-px (0.26 mm on the Amazon at 205 mm) channel, and the every-2-px preview
# skipped it.
RIVER_MIN_WIDTH_MM = 0.5


@dataclass
class TerrainField:
    """Output of the terrain stage: the top surface in model mm and its scale."""

    z_mm: np.ndarray       # (H, W), row 0 north, base included
    scale: ModelScale
    elev_min_m: float
    elev_max_m: float
    # Water for the viewer to colour like the Edit map: 0 dry, 0..1 relative carve
    # depth (rivers, lakes, open water), WATER_SEA on the open sea. None without a carve.
    water: np.ndarray | None = None


def terrain_stage(p: ExportContext, data: dict) -> TerrainField:
    """Stage 1 of every mesh export: the request's DEM as a heightfield in mm.

    Raster terrain modifiers only (composite / edited values are already in
    ``p.dem_values``): median filter, sea-level cap, vertical scale, then the
    label and contour engraving. Features belong to the vector stage.
    """
    masks: dict = {}
    im, im_min, im_max, scale = _prepare_dem_array(p, masks)
    label_text = data.get("label_text", p.name)
    if data.get("engrave_label") and label_text:
        im = _apply_label_engraving(im, label_text, p.base_height)
    contour_interval = float(data.get("contour_interval", 100))
    if data.get("contours") and contour_interval > 0:
        # Relief is model_height * exaggeration mm, so the contour spacing has
        # to be computed against that same height rather than model_height alone.
        im = _apply_contour_lines(im, im_min, im_max, p.model_height * p.exaggeration,
                                  p.base_height, contour_interval,
                                  data.get("contour_style", "engraved"))
    return TerrainField(im, scale, im_min, im_max, masks.get("water"))


def _prepare_dem_array(p: ExportContext, masks: dict | None = None
                       ) -> tuple[np.ndarray, float, float, ModelScale]:
    """
    Reshape, fill, median-filter, sea-level-clip and scale the DEM into model mm.

    Returns ``(im, im_min, im_max, scale)``: ``im`` is the top surface in mm with
    the base added; ``im_min``/``im_max`` are the source extents in metres (the
    contour spacing converts a contour interval in metres into millimetres with them).

    Vertical scale comes from ``city2stl.city_model.choose_scale``: ``z_mode``
    "true" is true scale x exaggeration; "fit" maps the relief to
    ``model_height * exaggeration`` mm (the exaggeration is applied after the
    normalisation - before it, it cancels out); "auto" picks true scale for
    regions under 20 km diagonal, fit above, and falls back to fit when no bbox
    is known. A 3x3 median (``median_size``) removes the blocky artefacts of an
    upsampled DEM. NaNs (projection edges, JSON nulls) are filled with the lowest
    real elevation so they print as the floor of the relief.

    Rivers / lakes (``carve_m``) carve after the median. With ``river_depth_mm`` the
    carve is applied after scaling, in print millimetres: the main river (the
    99.5th-percentile carve depth) is that many mm deep, shallower carve keeps its
    ratios and deeper carve is capped; the vertical scale then comes from the
    uncarved terrain, and channels are widened to ``RIVER_MIN_WIDTH_MM`` on the print.
    Without it the carve is in metres, added before scaling. A ``masks`` dict gets
    ``"water"`` (see :attr:`TerrainField.water`) when there is a carve.
    """
    from city2stl.city_model import choose_scale, prepare_dem

    im = np.array(p.dem_values, dtype=np.float64).reshape(p.height, p.width)
    im = prepare_dem(im, p.median_size)
    carve = p.carve_m if p.carve_m is not None and p.carve_m.shape == im.shape else None
    carve_in_mm = carve is not None and bool(p.river_depth_mm)
    if carve_in_mm:
        # Widen channels to RIVER_MIN_WIDTH_MM on the print (deepest neighbour wins).
        width_px = int(np.ceil(RIVER_MIN_WIDTH_MM / max(p.mm_per_pixel, 1e-6) - 1e-9))
        if width_px >= 2:
            from scipy.ndimage import minimum_filter
            carve = minimum_filter(carve, size=width_px, mode="nearest")
    if carve is not None and masks is not None and (carve < 0).any():
        from geo2stl.water_layers import ocean_mask
        deep = float(np.percentile(-carve[carve < 0], 99.5, method="lower"))
        water = np.clip(-carve / deep, 0.0, 1.0) if deep > 0 else (carve < 0).astype(float)
        masks["water"] = np.where(ocean_mask(im), WATER_SEA, water)
    if carve is not None and not carve_in_mm:
        # A 1-px channel is 3 of 9 cells in a 3x3 window, so smoothing after
        # carving would erase it (F-REGION).
        im = im + carve

    if p.sea_level_cap:
        # Raise everything below sea level to zero so the sea prints flat. (This was
        # np.minimum once, which flattened the land and kept only the trenches.)
        im = np.maximum(im, 0.0)

    im_min = float(im.min())
    im_max = float(im.max())
    z_mode = p.z_mode if p.bbox else "fit"
    flat = not (im_max > im_min or z_mode == "true")
    scale = choose_scale(p.bbox or {"north": 1, "south": 0, "east": 1, "west": 0},
                         im.shape, im_min, im_max, mm_per_px=p.mm_per_pixel,
                         z_mode="true" if flat else z_mode, exaggeration=p.exaggeration,
                         fit_height_mm=p.model_height, base_mm=p.base_height)
    # A genuinely flat region (a lake, a salt pan) prints as a flat plate.
    im = np.full_like(im, p.base_height) if flat else scale.z_mm(im)
    if carve_in_mm and (carve < 0).any():
        # The reference is the depth only the deepest 0.5 % of carved cells exceed
        # (the main river), not the single deepest cell: on the Amazon a few -30 m
        # open-water cells left the 11 m main channels at 0.2 mm. Deeper cells are
        # capped at the print depth.
        ref = float(np.percentile(-carve[carve < 0], 99.5, method="lower"))
        if ref > 0:
            im = np.maximum(im + np.maximum(carve / ref, -1.0) * p.river_depth_mm,
                            max(0.1, p.base_height * 0.5))
    return im, im_min, im_max, scale


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


def _prepare_export_mesh(p: ExportContext, data: dict, progress=None):
    """Both stages for a terrain-only export: heightfield, then the city model on it.

    Returns the merged ``trimesh.Trimesh`` (adaptive terrain within the print
    tolerance, watertight, north up, z = 0 floor). The city export calls
    :func:`terrain_stage` and ``build_on_terrain`` itself to add its layers.
    """
    from city2stl.city_model import build_on_terrain

    field = terrain_stage(p, data)
    if progress:
        progress(30, "Meshing terrain...")
    mesh = build_on_terrain(field.z_mm, p.bbox, field.scale, {}).merged
    if len(mesh.faces) == 0:
        raise ValueError("Mesh generation produced no faces")
    return mesh


def _watertight(mesh) -> bool:
    from city2stl.city_model import welded_watertight
    return welded_watertight(mesh)


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
    watertight = _watertight(mesh)
    if not watertight:
        logger.warning("%s mesh is not watertight (%d faces)",
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

    mesh = _prepare_export_mesh(p, data)
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

    mesh = _prepare_export_mesh(p, data)
    vertices, faces = mesh.vertices, mesh.faces

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

    mesh = _prepare_export_mesh(p, data)
    vertices, faces = mesh.vertices, mesh.faces

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

    # The export's own terrain stage, so label, contours and scale match the file.
    t0 = time.perf_counter()
    field = terrain_stage(p, data)
    im = field.z_mm
    # Default to a closed solid so the preview shows the floor and side walls
    # the exported file actually has. This defaulted to a bare top surface,
    # so the viewer rendered an open shell with nothing underneath it.
    solid = bool(data.get("solid", True))
    vertices, faces, info = _preview_mesh(im, field.scale, solid)
    info["seconds"] = round(time.perf_counter() - t0, 2)
    logger.info("Preview mesh: %d vertices, %d faces (%s)", len(vertices), len(faces), info)

    # Vertices are in pixel-grid units (x = column, y = row, row 0 north); the
    # client multiplies by mm_per_pixel to display real mm. Integer x/y keep
    # the payload compact.
    v_rounded = vertices.copy()
    v_rounded[:, :2] = np.round(v_rounded[:, :2]).astype(np.int32)
    v_rounded[:, 2]  = np.round(v_rounded[:, 2], 2)

    # Water vertices, for the viewer to colour like the Edit map: vertex index and
    # relative depth in % (WATER_SEA → 200 for the open sea).
    water = {}
    if field.water is not None and len(vertices):
        cols = np.clip(v_rounded[:, 0].astype(int), 0, im.shape[1] - 1)
        rows = np.clip(v_rounded[:, 1].astype(int), 0, im.shape[0] - 1)
        w = field.water[rows, cols]
        idx = np.flatnonzero(w > 0)
        water = {"water_idx": idx.tolist(),
                 "water_pct": np.round(w[idx] * 100).astype(int).tolist()}

    return JSONResponse(content={
        **water,
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
        "preview": info,
        # z_mm = base_mm + (elev_m - elev_min_m) * z_mm_per_m: the viewer stands the
        # quick building view (city-quickview.js) on the terrain with it.
        "scale": {"z_mm_per_m": field.scale.z_mm_per_m, "elev_min_m": field.scale.elev_min_m,
                  "base_mm": field.scale.base_mm, "sea_level_cap": p.sea_level_cap},
    })


PREVIEW_MAX_FACES = 150_000
PREVIEW_MAX_PIXELS = 250_000   # larger DEMs are strided for the preview only


def _preview_mesh(im: np.ndarray, scale: ModelScale, solid: bool = True) -> tuple:
    """Adaptive preview mesh of the heightfield ``im`` (mm), at most PREVIEW_MAX_FACES.

    The top is ``heightfield_tin_budget`` started at the export's own terrain
    tolerance and raised until the vertex budget fits. DEMs over
    PREVIEW_MAX_PIXELS are sampled every ``stride`` pixels first (preview only;
    the export always meshes every pixel). The solid adds side walls and a flat
    bottom fanned from its centre - one triangle per border edge instead of a
    copy of the top. Returns (vertices [col, row, z_mm] in pixel units, faces, info).
    """
    from numpy2stl.processing.decimate import heightfield_tin_budget

    from city2stl.city_model import terrain_tolerance

    h, w = im.shape
    stride = max(1, math.ceil(math.sqrt(h * w / PREVIEW_MAX_PIXELS)))
    rows = np.unique(np.r_[0:h:stride, h - 1])
    cols = np.unique(np.r_[0:w:stride, w - 1])
    zs = im[np.ix_(rows, cols)]
    border = 2 * (len(rows) + len(cols)) - 4
    # Faces: top ~ 2 per vertex, walls 2 and bottom 1 per border edge.
    budget = max((PREVIEW_MAX_FACES - (3 * border if solid else 0)) // 2, border + 16)
    tol = terrain_tolerance(scale)
    idx, tris, err = heightfield_tin_budget(zs, tol, seed_step=max(2, 8 // stride),
                                            max_vertices=budget, ladder=3.0)
    ii, jj = np.divmod(idx, len(cols))
    top = np.column_stack([cols[jj], rows[ii], zs.ravel()[idx]]).astype(np.float64)
    remap = np.full(zs.size, -1)
    remap[idx] = np.arange(len(idx))
    f = remap[tris]
    info = {"adaptive": True, "stride": stride, "tolerance_mm": round(tol, 3),
            "max_error_mm": round(float(err), 3)}
    if not solid:
        return top, f, info
    # Border edges, directed as in the top faces; walls down to z = 0, bottom fan.
    edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    _, inv, counts = np.unique(np.sort(edges, axis=1), axis=0,
                               return_inverse=True, return_counts=True)
    e = edges[counts[inv.ravel()] == 1]
    ring = np.unique(e)
    n = len(top)
    below = np.full(n, -1)
    below[ring] = n + np.arange(len(ring))
    centre = n + len(ring)
    bottom = np.vstack([np.column_stack([top[ring, :2], np.zeros(len(ring))]),
                        [[(w - 1) / 2, (h - 1) / 2, 0.0]]])
    a, b = e[:, 0], e[:, 1]
    faces = np.vstack([f, np.column_stack([a, below[b], b]),
                       np.column_stack([a, below[a], below[b]]),
                       np.column_stack([np.full(len(e), centre), below[b], below[a]])])
    return np.vstack([top, bottom]), faces, info


# Flat request field -> puzzle spec key (app/server/core/puzzle.py). A nested
# ``puzzle`` dict, as the city export sends, is accepted too and wins.
_PUZZLE_FIELDS = {
    "split_cols": "cols", "split_rows": "rows", "piece_mm": "piece_mm",
    "knob_width_mm": "knob_width_mm", "knob_depth_mm": "knob_depth_mm",
    "knob_shape": "knob_shape", "clearance_mm": "clearance_mm",
    "col_edges_mm": "col_edges_mm", "row_edges_mm": "row_edges_mm",
    "puzzle_method": "method", "engrave_ids": "engrave", "layout": "layout",
    "bed_mm": "bed_mm",
}


def puzzle_spec(data: dict) -> dict:
    """The puzzle spec of a ``puzzle`` export request (flat fields or ``puzzle``)."""
    spec = {"clearance_mm": 0.3}
    spec.update({key: data[f] for f, key in _PUZZLE_FIELDS.items() if data.get(f) is not None})
    if isinstance(data.get("puzzle"), dict):
        spec.update(data["puzzle"])
    return spec


def generate_puzzle(data: dict, task: ExportTask) -> None:
    """The terrain model cut into jigsaw pieces.

    Result: a zip with one OBJ per piece, a 3MF of all pieces in place and,
    with ``layout``, one 3MF per print bed (app/server/core/puzzle.py).
    Request: the usual export fields plus the puzzle spec (:func:`puzzle_spec`):
    ``split_cols``/``split_rows`` (or ``piece_mm``, or ``col_edges_mm`` /
    ``row_edges_mm``), ``knob_width_mm``, ``knob_depth_mm``, ``knob_shape``,
    ``clearance_mm``, ``puzzle_method`` (auto | mask | boolean), ``engrave_ids``,
    ``layout`` + ``bed_mm``.

    A terrain-only model is cut by the fast mask path (pieces meshed straight
    from the heightfield); ``puzzle_method: "boolean"`` builds the whole model
    and cuts it with manifold3d instead.
    """
    import zipfile

    from app.server.core.puzzle import Heightfield, choose_method, cut_to_zip
    from city2stl.city_model import build_on_terrain, terrain_tolerance

    p = _parse_export_params(data)
    if not p.dem_values or not p.height or not p.width:
        task.fail("Missing DEM data")
        return
    spec = puzzle_spec(data)
    try:
        method = choose_method(spec, terrain_only=True)
    except ValueError as exc:
        task.fail(str(exc))
        return
    task.update(5, "Terrain stage...")
    field = terrain_stage(p, data)
    mesh = heightfield = None
    if method == "mask":
        heightfield = Heightfield(field.z_mm, p.mm_per_pixel, terrain_tolerance(field.scale))
    else:
        task.update(20, "Building terrain mesh...")
        mesh = build_on_terrain(field.z_mm, p.bbox, field.scale, {}).merged
    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            info = cut_to_zip(mesh, spec, p.name, zf, progress=task.update,
                              heightfield=heightfield)
    except ValueError as exc:
        os.unlink(zip_path)
        task.fail(str(exc))
        return
    logger.info("Puzzle %s: %d pieces, %s path, %s", p.name, info["pieces"], info["method"],
                info["seconds"])
    task.complete(zip_path, f"{p.name}_puzzle.zip", {
        **_disposition(f"{p.name}_puzzle.zip"),
        "X-Piece-Count": str(info["pieces"]),
        "X-Puzzle-Info": json.dumps({k: info[k] for k in ("cols", "rows", "method", "seconds")}),
        "Access-Control-Expose-Headers": "X-Piece-Count, X-Puzzle-Info",
    })


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
