"""Elevation in, mesh out.

This module is deliberately the only place in v2 that knows the shape of the render. In v1
the same knowledge was split between the terrain router, the export router, the export core,
the export-params helper, and three client modules; deciding what a given control actually
did meant reading all seven.

Nothing here touches HTTP, and nothing here reads global state. Every function takes what it
needs and returns what it produced, so the whole pipeline can be exercised from a test or a
notebook without a server running.
"""

from __future__ import annotations

import logging
import math
import tempfile
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from .config import OPENTOPO_API_KEY, STRM_H5_ROOT
from .models import DemSettings, ExportSettings, ModelSettings, ProjectionSettings

logger = logging.getLogger(__name__)

# A callback the caller passes in to report progress: (percent, message).
ProgressFn = Callable[[int, str], None]


class Cancelled(Exception):
    """Raised inside the pipeline when the caller's cancel check trips.

    v1 had no way to stop an export. A user who started a large render could close the
    progress bar, but the daemon thread ran to completion regardless, holding a core and
    writing a file nobody would download. Here the pipeline is asked, between stages,
    whether it should still be running.
    """


class DemUnavailable(Exception):
    """Raised when a source returns nothing usable for the requested area.

    geo2stl's local-tile reader catches its own failures and returns an array of zeros
    with a message on stdout, which no HTTP client ever sees. v1 accepted that array,
    normalised its zero relief into a flat plate and exported a watertight 6 MB slab. The
    check that raises this exception is the reason v2 says "no tiles cover this area"
    instead.
    """


def _noop(_percent: int, _message: str) -> None:
    pass


def _count_local_tiles() -> int:
    """How many SRTM tiles the on-disk store actually holds."""
    try:
        from geo2stl.tiles import get_tile_files

        return len(get_tile_files())
    except Exception:  # noqa: BLE001 — an unreadable store is an unavailable store
        logger.exception("could not enumerate the local SRTM tile store")
        return 0


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def list_sources() -> dict[str, Any]:
    """Describe every DEM source and whether it can actually be used right now.

    Availability is computed, never assumed. v1's source list was hardcoded in the page's
    markup, so a source could be advertised that the server could not serve — and the
    fastest source, the local SRTM store, was missing from the markup entirely and
    therefore unreachable from the browser no matter how the server was configured.
    """
    from geo2stl.dem import OPENTOPO_DATASETS

    h5_available = STRM_H5_ROOT.exists() and any(STRM_H5_ROOT.glob("*.h5"))
    has_key = bool(OPENTOPO_API_KEY)
    tile_count = _count_local_tiles()

    sources: list[dict[str, Any]] = [
        {
            "id": "local",
            "label": "Local SRTM tiles",
            "provider": "local",
            "resolutionM": 30,
            "available": tile_count > 0,
            "note": (
                f"Stitched from {tile_count} tiles on disk. No network."
                if tile_count
                else "No SRTM tiles found on disk, so this source cannot be used."
            ),
        },
        {
            "id": "h5_local",
            "label": "Local SRTM (HDF5)",
            "provider": "local",
            "resolutionM": 90,
            "available": h5_available,
            "note": (
                "Fastest source by an order of magnitude; best under about 15 km."
                if h5_available
                else f"No .h5 files found under {STRM_H5_ROOT}."
            ),
        },
    ]
    for demtype, info in OPENTOPO_DATASETS.items():
        sources.append({
            "id": demtype,
            "label": info["label"],
            "provider": "OpenTopography",
            "resolutionM": info["resolution_m"],
            "available": has_key,
            "note": "" if has_key else "Needs an OpenTopography API key.",
        })
    return {"sources": sources, "openTopoKeyConfigured": has_key, "h5Available": h5_available}


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _reject_degenerate(array: np.ndarray, source: str) -> None:
    """Refuse an elevation grid that carries no information.

    Two failures look identical downstream and neither is detectable once the array has
    been normalised into millimetres: an all-NaN grid, and a grid of one repeated value —
    which is what the local tile reader returns when no tile covers the request. Real
    terrain at 30 m resolution never has an exactly constant elevation over a whole
    bounding box, so treating that as a failure costs nothing and catches both.
    """
    if array.size == 0:
        raise DemUnavailable(f"The {source} source returned an empty grid for this area.")

    finite = np.isfinite(array)
    if not finite.any():
        raise DemUnavailable(
            f"The {source} source returned no valid elevations for this area."
        )

    values = array[finite]
    if float(values.min()) == float(values.max()):
        raise DemUnavailable(
            f"The {source} source has no data covering this area — every value came back "
            f"as {float(values.min()):.0f}. Pick a different elevation source."
        )


def fetch_dem(
    bbox: dict[str, float],
    dem: DemSettings,
    projection: ProjectionSettings,
    progress: ProgressFn = _noop,
) -> tuple[np.ndarray, str, float]:
    """Fetch an elevation grid and reproject it. Returns (array, source_used, seconds).

    The projection is applied here rather than being folded into the fetch, because it is a
    pure resampling of an array the fetch already produced. Keeping the two apart is what
    lets a user switch projections without paying for another download.
    """
    from geo2stl.dem import fetch_dem_from_source

    started = time.time()
    progress(10, f"Fetching elevation from {dem.source}")
    array = fetch_dem_from_source(
        dem.source,
        bbox["north"], bbox["south"], bbox["east"], bbox["west"],
        dem.dim,
        depth_scale=dem.depthScale,
        water_scale=dem.waterScale,
        subtract_water=dem.subtractWater,
        maintain_dimensions=dem.maintainDimensions,
    )
    array = np.asarray(array, dtype=np.float64)
    _reject_degenerate(array, dem.source)

    if projection.name and projection.name != "none":
        progress(60, f"Reprojecting to {projection.name}")
        from geo2stl.projections import project_grid

        array = project_grid(
            array,
            bbox["north"], bbox["south"], bbox["east"], bbox["west"],
            projection.name,
            clip_nans=projection.clipValidRegion,
            maintain_dimensions=dem.maintainDimensions,
        )

    elapsed = time.time() - started
    progress(100, f"Elevation ready ({array.shape[1]}x{array.shape[0]})")
    return array, dem.source, elapsed


def fetch_overlay(bbox: dict[str, float], kind: str, dim: int) -> dict[str, Any]:
    """Fetch one preview overlay as a base64 image. Never reaches the mesh."""
    n, s, e, w = bbox["north"], bbox["south"], bbox["east"], bbox["west"]

    if kind == "satellite":
        from geo2stl.sat2stl import fetch_satellite_tiles

        return {"kind": kind, "mime": "image/jpeg", "dataBase64": fetch_satellite_tiles(n, s, e, w, dim)}

    if kind in ("waterMask", "landCover"):
        # Both come out of the same Earth Engine call, which returns the water mask and the
        # ESA land-cover classes together. sat_scale is metres per pixel, so it is derived
        # from the requested grid width rather than passed straight through.
        from geo2stl.sat2stl import fetch_water_mask

        sat_scale = max(10, int(round(_span_metres(n, s, e, w) / max(dim, 1))))
        water, land_cover, _used = fetch_water_mask(n, s, e, w, sat_scale, "esa")
        layer = water if kind == "waterMask" else land_cover
        return {"kind": kind, "mime": "image/png", "dataBase64": _mask_to_png_base64(layer)}

    raise ValueError(f"unknown overlay: {kind}")


def _span_metres(north: float, south: float, east: float, west: float) -> float:
    """Longest side of the bounding box in metres, for picking a sensible pixel size."""
    mid_lat = math.radians((north + south) / 2.0)
    metres_per_degree = 111_320.0
    return max(
        (north - south) * metres_per_degree,
        (east - west) * metres_per_degree * math.cos(mid_lat),
    )


def _mask_to_png_base64(mask: Any) -> str:
    """Render a mask or classified layer as a transparent-background PNG."""
    import base64
    from io import BytesIO

    from PIL import Image

    arr = np.asarray(mask)
    if arr.ndim == 3:
        img = Image.fromarray(arr.astype(np.uint8))
    else:
        # Anything non-zero is "present"; everything else is fully transparent, so the
        # overlay can be stacked on the terrain without hiding it.
        alpha = (np.nan_to_num(arr) > 0).astype(np.uint8) * 200
        rgba = np.zeros((*alpha.shape, 4), dtype=np.uint8)
        rgba[..., 2] = 200  # blue
        rgba[..., 1] = 120
        rgba[..., 3] = alpha
        img = Image.fromarray(rgba, mode="RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# Elevation to millimetres
# ---------------------------------------------------------------------------

def to_model_space(array: np.ndarray, model: ModelSettings) -> np.ndarray:
    """Turn an elevation grid into printable millimetres.

    Order matters and is not arbitrary: exaggerate first so the sea-level clamp acts on the
    exaggerated surface, then normalise the full range onto the requested model height, then
    add the base so the base is a true thickness rather than a share of the height.
    """
    im = np.asarray(array, dtype=np.float64)
    im = im * model.exaggeration

    if model.seaLevelCap:
        # Raise everything below sea level up to zero, flattening ocean floor into a flat
        # sea. v1 used np.minimum here, which did the opposite: it flattened all the land
        # and kept only the trenches.
        im = np.maximum(im, 0.0)

    lo = float(np.nanmin(im))
    hi = float(np.nanmax(im))
    if hi > lo:
        im = (im - lo) / (hi - lo) * model.modelHeight
    else:
        # A perfectly flat region is legitimate (a lake, a salt pan). Emit a flat plate at
        # zero relief rather than dividing by zero.
        im = np.zeros_like(im)

    return im + model.baseHeight


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------

def build_mesh(
    im: np.ndarray,
    model: ModelSettings,
    export: ExportSettings,
    name: str,
    progress: ProgressFn = _noop,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Generate a mesh file. Returns (temp_path, stats).

    The caller owns the returned file and is responsible for deleting it.
    """
    from numpy2stl import array_to_mesh

    def check() -> None:
        if should_cancel and should_cancel():
            raise Cancelled()

    if export.engraveLabel and export.labelText:
        progress(20, "Engraving label")
        im = engrave_label(im, export.labelText, model.baseHeight)
    check()

    progress(35, "Generating mesh")
    # floor_val pins the bottom cap at z=0, which is what makes `baseHeight` a real
    # thickness. Left at its default the floor sits one unit below the lowest point of the
    # surface, so the solid was always modelHeight + 1 mm tall no matter what base was
    # asked for — v1 shipped that behaviour with a "Base thickness" slider on top of it.
    # With baseHeight at 0 there is no base to preserve, and the default floor is what
    # keeps the mesh a closed solid rather than a zero-thickness sheet.
    floor = 0.0 if model.baseHeight > 0 else None
    vertices, faces = array_to_mesh(im, floor_val=floor)
    check()

    if model.mmPerPixel != 1.0:
        # numpy2stl returns x and y in pixel-index units and z already in millimetres.
        vertices[:, 0] *= model.mmPerPixel
        vertices[:, 1] *= model.mmPerPixel

    progress(70, "Repairing mesh")
    import trimesh as tm

    mesh = tm.Trimesh(vertices=vertices, faces=faces, process=False)
    tm.repair.fill_holes(mesh)
    tm.repair.fix_normals(mesh)
    check()

    progress(88, f"Writing {export.format.upper()}")
    suffix = f".{export.format}"
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = handle.name
    handle.close()

    if export.format == "stl":
        mesh.export(path, file_type="stl")
    elif export.format == "obj":
        from numpy2stl import writeOBJ

        writeOBJ(path, {name: (vertices, faces)})
    elif export.format == "3mf":
        from numpy2stl import write3MF

        write3MF(path, {name: (vertices, faces)})
    else:
        raise ValueError(f"unknown format: {export.format}")

    stats = {
        "faceCount": int(len(mesh.faces)),
        "vertexCount": int(len(mesh.vertices)),
        "watertight": bool(mesh.is_watertight),
        "sizeMm": {
            "x": float(mesh.extents[0]),
            "y": float(mesh.extents[1]),
            "z": float(mesh.extents[2]),
        },
    }
    progress(100, "Done")
    return path, stats


def engrave_label(im: np.ndarray, text: str, base_height: float) -> np.ndarray:
    """Sink a text label into the bottom strip of the plate.

    Returns the array unchanged if PIL cannot render the text — an unlabelled model is a
    better outcome than a failed export.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:  # pragma: no cover
        logger.warning("PIL unavailable; skipping label engraving")
        return im

    h, w = im.shape
    font_size = max(6, h // 25)
    canvas = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", size=font_size)
    except OSError:
        font = ImageFont.load_default()

    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((w - (box[2] - box[0])) // 2, h - (box[3] - box[1]) - max(4, h // 60)),
        text,
        fill=255,
        font=font,
    )

    mask = np.asarray(canvas) > 128
    if not mask.any():
        return im
    out = im.copy()
    # Cut to just above the plate floor, so the engraving is visible but never perforates.
    out[mask] = np.minimum(out[mask], base_height * 0.4)
    return out


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def dem_preview_png(array: np.ndarray, colormap: str = "terrain") -> bytes:
    """Render an elevation grid as a PNG for the browser.

    v1 shipped the raw float32 grid to the client as base64 and coloured it in JavaScript.
    That is the right call when the client needs the numbers — v2's 3D view does — but the
    2D preview only needs pixels, and sending pixels is roughly forty times smaller.
    """
    from io import BytesIO

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import cm
    from matplotlib.colors import Normalize
    from PIL import Image

    arr = np.asarray(array, dtype=np.float64)
    finite = np.isfinite(arr)
    if not finite.any():
        raise ValueError("elevation grid contains no finite values")

    norm = Normalize(vmin=float(arr[finite].min()), vmax=float(arr[finite].max()))
    rgba = (cm.get_cmap(colormap)(norm(np.nan_to_num(arr))) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(finite, 255, 0)  # NaN padding stays transparent

    buf = BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()
