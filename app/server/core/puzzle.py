"""Cut a finished model into interlocking jigsaw pieces: one OBJ per piece + a 3MF.

Used by both the terrain puzzle export and the city model. The cut is a boolean
intersection of the watertight model with numpy2stl's tongue-and-groove cutters
(``make_jigsaw_cutters`` + ``cut_jigsaw``), which also verifies that the pieces
account for the whole model apart from the clearance gaps.

spec: ``cols`` + ``rows``, or ``piece_mm`` (largest piece side, e.g. the print bed:
the grid is chosen so every piece fits); optional ``knob_width_mm``,
``knob_depth_mm`` (default: scaled to the piece size) and ``clearance_mm``.
"""

from __future__ import annotations

import math
import os
import tempfile
import zipfile
from collections.abc import Callable

import trimesh
from numpy2stl import write3MF
from numpy2stl.applications.puzzle import make_jigsaw_cutters
from numpy2stl.processing.boolean import cut_jigsaw

MAX_PIECES = 64


def plan_grid(width_mm: float, depth_mm: float, spec: dict) -> tuple[int, int]:
    if spec.get("cols") and spec.get("rows"):
        return int(spec["cols"]), int(spec["rows"])
    piece = float(spec.get("piece_mm", 200.0))
    return max(1, math.ceil(width_mm / piece)), max(1, math.ceil(depth_mm / piece))


def cut_to_zip(mesh: trimesh.Trimesh, spec: dict, name: str, zf: zipfile.ZipFile,
               progress: Callable[[int, str], None] | None = None) -> dict:
    """Write ``puzzle/<name>_r<row>c<col>.obj`` and ``<name>_puzzle.3mf`` into ``zf``."""
    ext = mesh.extents
    cols, rows = plan_grid(float(ext[0]), float(ext[1]), spec)
    if cols * rows > MAX_PIECES:
        raise ValueError(f"Maximum {MAX_PIECES} pieces (got {cols} x {rows})")
    piece = min(ext[0] / cols, ext[1] / rows)
    knob_w = float(spec.get("knob_width_mm") or 0.3 * piece)
    knob_d = float(spec.get("knob_depth_mm") or 0.12 * piece)
    knob_w, knob_d = min(knob_w, 0.3 * piece), min(knob_d, 0.15 * piece)
    lo = mesh.bounds[0]
    if progress:
        progress(60, f"Cutting {cols} x {rows} jigsaw pieces...")
    cutters = make_jigsaw_cutters(
        float(ext[0]), float(ext[1]), cols, rows, knob_width=knob_w, knob_depth=knob_d,
        clearance=float(spec.get("clearance_mm", 0.3)), z0=-1.0, z1=float(ext[2]) + 10.0)
    pieces = cut_jigsaw(mesh.vertices - [lo[0], lo[1], 0.0], mesh.faces, cutters)
    if progress:
        progress(85, "Writing pieces...")
    for key, (v, f) in pieces.items():
        zf.writestr(f"puzzle/{name}_{key}.obj",
                    trimesh.Trimesh(v, f, process=False).export(file_type="obj"))
    fd, tmp = tempfile.mkstemp(suffix=".3mf")
    os.close(fd)
    try:
        write3MF(tmp, {f"{name}_{k}": vf for k, vf in pieces.items()})
        zf.write(tmp, f"{name}_puzzle.3mf")
    finally:
        os.unlink(tmp)
    return {"cols": cols, "rows": rows, "pieces": len(pieces),
            "knob_width_mm": round(knob_w, 2), "knob_depth_mm": round(knob_d, 2)}
