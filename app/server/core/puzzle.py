"""Cut a finished model into interlocking jigsaw pieces: one OBJ per piece + 3MFs.

Used by both the terrain puzzle export and the city model. Two cutting paths:

* **mask** (terrain-only models, the default when the caller passes the
  heightfield): each piece is meshed straight from the heightfield inside its
  outline (``numpy2stl.applications.puzzle.heightfield_pieces``) - no boolean
  engine, walls on the exact outline.
* **boolean** (city models, or ``method: "boolean"``): intersection of the
  watertight model with the jigsaw cutter prisms (``make_jigsaw_cutters`` +
  ``cut_jigsaw``).

Both verify that the pieces account for the whole model apart from the
clearance gaps. Then, per piece: the id ("r1c2") and a north arrow engraved
into the underside, mirrored so they read with the piece flipped over.

spec: ``cols`` + ``rows``, or ``piece_mm`` (largest piece side, e.g. the print bed:
the grid is chosen so every piece fits), or explicit ``col_edges_mm`` /
``row_edges_mm`` (cut positions from the west / south edge, ``[0, ..., size]``);
optional ``knob_width_mm``, ``knob_depth_mm`` (default: scaled to the piece
size), ``knob_shape`` (``classic`` | ``dovetail`` | ``rectangular``),
``clearance_mm``, ``method`` (``auto`` | ``mask`` | ``boolean``), ``engrave``
(default true), ``engrave_depth_mm`` (0.6), ``layout`` (default false) +
``bed_mm`` ([w, h], default 220 x 220) + ``layout_gap_mm`` (5).
"""

from __future__ import annotations

import math
import os
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import shapely
import trimesh
from numpy2stl import write3MF, writeOBJ
from numpy2stl.applications.puzzle import (
    engrave_underside,
    heightfield_pieces,
    jigsaw_outlines,
    make_jigsaw_cutters,
    plate_layout,
    underside_marks,
    validate_edges,
)
from numpy2stl.processing.boolean import cut_jigsaw, mesh_volume

MAX_PIECES = 64
DEFAULT_BED_MM = (220.0, 220.0)   # Ender 220 x 220, the client default (decisions/frontend.md 2026-10-01)
ENGRAVE_DEPTH_MM = 0.6


@dataclass
class Heightfield:
    """The terrain-only model as a heightfield, for the mask path.

    ``z_mm`` row 0 north, pixel (i, j) at x = j*s, y = (H-1-i)*s; floor at z = 0.
    """

    z_mm: np.ndarray
    mm_per_px: float
    max_error: float

    @property
    def extents(self) -> tuple[float, float, float]:
        h, w = self.z_mm.shape
        return ((w - 1) * self.mm_per_px, (h - 1) * self.mm_per_px, float(self.z_mm.max()))


def plan_grid(width_mm: float, depth_mm: float, spec: dict) -> tuple[int, int]:
    """(cols, rows): explicit edges, else ``cols`` + ``rows``, else from ``piece_mm``."""
    cols = len(spec["col_edges_mm"]) - 1 if spec.get("col_edges_mm") else None
    rows = len(spec["row_edges_mm"]) - 1 if spec.get("row_edges_mm") else None
    if spec.get("cols") and spec.get("rows"):
        cols, rows = cols or int(spec["cols"]), rows or int(spec["rows"])
    piece = float(spec.get("piece_mm") or 200.0)
    return (cols or max(1, math.ceil(width_mm / piece)),
            rows or max(1, math.ceil(depth_mm / piece)))


def grid_edges(width_mm: float, depth_mm: float, spec: dict) -> tuple[np.ndarray, np.ndarray]:
    """Cut positions (validated explicit edges, else an even split) along x and y."""
    cols, rows = plan_grid(width_mm, depth_mm, spec)
    xs = (validate_edges(spec["col_edges_mm"], width_mm, cols, "col_edges_mm")
          if spec.get("col_edges_mm") else np.linspace(0.0, width_mm, cols + 1))
    ys = (validate_edges(spec["row_edges_mm"], depth_mm, rows, "row_edges_mm")
          if spec.get("row_edges_mm") else np.linspace(0.0, depth_mm, rows + 1))
    return xs, ys


def knob_size(xs: np.ndarray, ys: np.ndarray, spec: dict) -> tuple[float, float]:
    """Knob width and depth: the request's, capped to fit the smallest piece."""
    piece = float(min(np.diff(xs).min(), np.diff(ys).min()))
    knob_w = float(spec.get("knob_width_mm") or 0.3 * piece)
    knob_d = float(spec.get("knob_depth_mm") or 0.12 * piece)
    return min(knob_w, 0.3 * piece), min(knob_d, 0.15 * piece)


def choose_method(spec: dict, terrain_only: bool) -> str:
    """``mask`` for a terrain-only model unless the request forces ``boolean``."""
    method = str(spec.get("method") or "auto")
    if method not in ("auto", "mask", "boolean"):
        raise ValueError(f"Unknown puzzle method {method!r} (auto, mask or boolean)")
    if method == "mask" and not terrain_only:
        raise ValueError("Mask cutting needs a terrain-only model; use method 'boolean'")
    if method == "auto":
        return "mask" if terrain_only else "boolean"
    return method


def _bed(spec: dict) -> tuple[float, float]:
    bed = spec.get("bed_mm") or DEFAULT_BED_MM
    return float(bed[0]), float(bed[1])


def cut_pieces(spec: dict, mesh: trimesh.Trimesh | None = None,
               heightfield: Heightfield | None = None,
               progress: Callable[[int, str], None] | None = None) -> tuple[dict, dict]:
    """Pieces {key: (vertices, faces)} in the model frame (min corner at the origin), and info.

    Pass ``heightfield`` for a terrain-only model (enables the mask path) and/or
    the finished ``mesh`` (needed for the boolean path).
    """
    method = choose_method(spec, heightfield is not None)
    if method == "boolean" and mesh is None:
        raise ValueError("Boolean puzzle cutting needs the finished model mesh")
    ext = heightfield.extents if method == "mask" else tuple(float(e) for e in mesh.extents)
    xs, ys = grid_edges(ext[0], ext[1], spec)
    cols, rows = len(xs) - 1, len(ys) - 1
    if cols * rows > MAX_PIECES:
        raise ValueError(f"Maximum {MAX_PIECES} pieces (got {cols} x {rows})")
    knob_w, knob_d = knob_size(xs, ys, spec)
    shape = str(spec.get("knob_shape") or "classic")
    clearance = float(spec.get("clearance_mm", 0.3))
    if progress:
        progress(60, f"Cutting {cols} x {rows} jigsaw pieces ({method})...")
    t0 = time.perf_counter()
    kw = dict(knob_shape=shape, col_edges=xs, row_edges=ys)
    if method == "mask":
        outlines = jigsaw_outlines(ext[0], ext[1], cols, rows, knob_w, knob_d, clearance, **kw)
        pieces = heightfield_pieces(heightfield.z_mm, outlines, heightfield.max_error,
                                    mm_per_px=heightfield.mm_per_px, floor=0.0)
    else:
        outlines = jigsaw_outlines(ext[0], ext[1], cols, rows, knob_w, knob_d, clearance,
                                   border=0.0, **kw)
        cutters = make_jigsaw_cutters(ext[0], ext[1], cols, rows, knob_w, knob_d, clearance,
                                      z0=-1.0, z1=ext[2] + 10.0, **kw)
        lo = mesh.bounds[0]
        pieces = cut_jigsaw(mesh.vertices - [lo[0], lo[1], 0.0], mesh.faces, cutters)
    info = {"cols": cols, "rows": rows, "pieces": len(pieces), "method": method,
            "knob_shape": shape, "knob_width_mm": round(knob_w, 2),
            "knob_depth_mm": round(knob_d, 2), "clearance_mm": clearance,
            "col_edges_mm": [round(float(x), 2) for x in xs],
            "row_edges_mm": [round(float(y), 2) for y in ys],
            "seconds": {"cut": round(time.perf_counter() - t0, 2)}}
    info["volume"] = volume_check(pieces, mesh=mesh if method == "boolean" else None,
                                  heightfield=heightfield if method == "mask" else None)

    if spec.get("engrave", True):
        if progress:
            progress(78, "Engraving piece ids...")
        t0 = time.perf_counter()
        depth = float(spec.get("engrave_depth_mm") or ENGRAVE_DEPTH_MM)
        engraved, skipped = 0, []
        rect = shapely.box(0.0, 0.0, ext[0], ext[1])
        for key, (v, f) in pieces.items():
            marks = underside_marks(key, outlines[key].intersection(rect))
            # Skip a piece too small for legible marks, or whose base is too thin.
            if marks is None or _min_top(v, f) < depth + 0.4:
                skipped.append(key)
                continue
            pieces[key] = engrave_underside(v, f, marks, depth=depth, floor=0.0)
            engraved += 1
        info["engraved"] = {"pieces": engraved, "depth_mm": depth, "skipped": skipped}
        info["seconds"]["engrave"] = round(time.perf_counter() - t0, 2)
    return pieces, info


def _min_top(v, f) -> float:
    """Lowest point of the piece's upward-facing surface (its thinnest spot)."""
    m = trimesh.Trimesh(v, f, process=False)
    up = m.face_normals[:, 2] > 0.1
    return float(m.triangles_center[up, 2].min()) if up.any() else float(m.bounds[1, 2])


def volume_check(pieces: dict, mesh: trimesh.Trimesh | None = None,
                 heightfield: Heightfield | None = None) -> dict:
    """Volume kept by the cut (before engraving) and pieces that are not closed.

    ``kept`` is pieces / model, clearance gaps included, so it is the figure the
    ">= 99 % of the volume" success criterion reads. The cutters already refuse a
    cut that loses more than 1 % *beyond* the gaps (numpy2stl ``max_loss``).
    """
    if mesh is not None:
        model = float(mesh.volume)
    else:   # the heightfield's volume above z = 0: mean of each cell's four corners
        z, s = heightfield.z_mm, heightfield.mm_per_px
        model = float((z[:-1, :-1] + z[1:, :-1] + z[:-1, 1:] + z[1:, 1:]).sum()) * s * s / 4.0
    total, open_ = 0.0, []
    for key, (v, f) in pieces.items():
        total += abs(mesh_volume(v, f))
        e = np.sort(np.asarray(f)[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1).astype(np.int64)
        _, uses = np.unique(e[:, 0] * len(v) + e[:, 1], return_counts=True)
        if (uses % 2).any():
            open_.append(key)
    return {"model_mm3": round(model, 1), "pieces_mm3": round(total, 1),
            "kept": round(total / model, 5) if model > 0 else 0.0, "open_pieces": open_}


def cut_to_zip(mesh: trimesh.Trimesh | None, spec: dict, name: str, zf: zipfile.ZipFile,
               progress: Callable[[int, str], None] | None = None,
               heightfield: Heightfield | None = None) -> dict:
    """Write ``puzzle/<name>_r<row>c<col>.obj``, ``<name>_puzzle.3mf`` (pieces in
    place) and, with ``layout``, ``<name>_plate<N>.3mf`` (pieces spread on the bed)."""
    t0 = time.perf_counter()
    pieces, info = cut_pieces(spec, mesh=mesh, heightfield=heightfield, progress=progress)
    if progress:
        progress(85, "Writing pieces...")
    for key, vf in pieces.items():
        with zf.open(f"puzzle/{name}_{key}.obj", "w", force_zip64=True) as out:
            writeOBJ(out, {f"{name}_{key}": vf})
    _write_3mf(zf, f"{name}_puzzle.3mf", {f"{name}_{k}": vf for k, vf in pieces.items()})
    if spec.get("layout"):
        bed_w, bed_h = _bed(spec)
        plates, oversize = plate_layout(pieces, bed_w, bed_h,
                                        gap=float(spec.get("layout_gap_mm") or 5.0))
        for n, plate in enumerate(plates, start=1):
            _write_3mf(zf, f"{name}_plate{n}.3mf", {f"{name}_{k}": vf for k, vf in plate.items()})
        info["layout"] = {"bed_mm": [bed_w, bed_h], "plates": len(plates),
                          "oversize": oversize}
    info["seconds"]["total"] = round(time.perf_counter() - t0, 2)
    return info


def _write_3mf(zf: zipfile.ZipFile, arcname: str, meshes: dict) -> None:
    fd, tmp = tempfile.mkstemp(suffix=".3mf")
    os.close(fd)
    try:
        write3MF(tmp, meshes)
        zf.write(tmp, arcname, compress_type=zipfile.ZIP_STORED)   # already a zip
    finally:
        os.unlink(tmp)
