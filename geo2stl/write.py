"""geo2stl/write.py — Save terrain arrays to disk (npy or STL)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def savefile(
    out_dir: str | Path,
    name: str,
    im: np.ndarray,
    format: str = "stl",
) -> str:
    """Save *im* to ``<out_dir>/<name>/`` as ``.npy`` or ``.stl``.

    A sub-directory named *name* is created inside *out_dir*.  If the
    destination file already exists it is overwritten.

    Args:
        out_dir: Parent directory (created if absent).
        name:    Stem used for both the sub-directory and output filename.
        im:      2-D elevation array (values ≥ 0 recommended for STL).
        format:  ``"stl"`` (default) or ``"npy"``.

    Returns:
        Absolute path of the written file.
    """
    from numpy2stl import array_to_mesh, triangles_to_facets, writeSTL

    im = im.copy()
    if im.min() < 0:
        logger.warning("savefile: array contains negative values (min=%.3f)", im.min())

    dest = Path(out_dir) / name
    dest.mkdir(parents=True, exist_ok=True)

    fmt = format.lower()
    if fmt == "npy":
        filepath = dest / f"{name}.npy"
        np.save(str(filepath), im)
        logger.info("Saved npy: %s", filepath)
    else:  # stl (default)
        filepath = dest / f"{name}.stl"
        vertices, faces = array_to_mesh(im[::-1])
        facets = triangles_to_facets(vertices[faces])
        writeSTL(facets, str(filepath))
        logger.info("Saved STL: %s", filepath)

    return str(filepath)









     