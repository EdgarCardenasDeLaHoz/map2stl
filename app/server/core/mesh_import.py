"""
core/mesh_import.py — STL/OBJ mesh import: upload storage, heightmap
conversion, and manual point-pair registration.

Delegates the heavy lifting to city2stl.height (stl_to_heightmap,
infill_idw/infill_nearest) and numpy2stl.registration.align.transform
(apply_transform), following the guarded-import delegation pattern used
throughout city2stl/mesh.py.

Uploaded mesh files are stored under CACHE_ROOT/mesh_imports/<upload_id>/ —
not bbox-keyed like the DEM/water/satellite array caches (core/cache.py),
since an upload has no bbox until the user picks one for the heightmap step.

Mesh library (config.MICROPOLITAN_STL_DIR)
--------------------------------------------
Pre-made mesh sets such as the "micropolitan" city STL packs carry no
embedded geographic coordinates. Location is captured once by the user (via
the manual registration UI) and persisted as a sidecar `<file>.location.json`
next to each mesh. Files within the same city folder (e.g. Solid + Water +
print-bed tiles A1/A2/B1/B2) are treated as one physical model and share a
single bbox: `set_library_location()` writes the same bbox to every mesh
file's sidecar in that city folder. Computed heightmaps are cached on disk
per (file, bbox, resolution, up_axis) so re-opening a previously registered
city doesn't re-run the ray-cast.

Auto-register reports (F-REGION §5)
-----------------------------------
``auto_register`` writes numpy2stl's HTML registration report to one folder per
import source under ``CACHE_ROOT/mesh_imports/reports/<city>_<hash>/`` (re-running
overwrites it) and returns its ``/reports/files/mesh_import/...`` URL together with
the ``ComparisonResult`` score breakdown (``comparison_scores``).  The ``/reports``
browser lists that folder beside the batch registration reports.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
from pathlib import Path

import numpy as np
from numpy2stl.registration.align.transform import apply_transform as _apply_transform

from app.server.config import MAX_DIM, MICROPOLITAN_STL_DIR
from app.server.core.cache import CACHE_ROOT, make_cache_key
from city2stl.height.infill import infill_idw as _infill_idw
from city2stl.height.infill import infill_nearest as _infill_nearest
from city2stl.height.stl_import import stl_to_heightmap as _stl_to_heightmap
from city2stl.osm_raster import get_city_bbox as _get_city_bbox
from city2stl.osm_raster import get_city_center_point as _get_city_center_point
from city2stl.registration import register_city_stl as _register_city_stl
from geo2stl.geo import bbox_size_m

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".stl", ".obj"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB


class MeshImportError(ValueError):
    """Raised for invalid uploads or malformed registration input."""


def _check_grid_size(bbox: dict, resolution_m: float) -> None:
    """Reject bbox/resolution combinations that would ray-cast an oversized grid.

    Mirrors stl_to_heightmap's own grid-dimension formula so the estimate
    stays exact, and raises *before* the (expensive) ray-cast runs rather
    than timing out mid-request. Caps at MAX_DIM per side, same limit every
    other raster endpoint (DEM/water/ESA) already uses (config.MAX_DIM).
    """
    lon_m, lat_m = bbox_size_m(bbox)
    h = max(1, round(lat_m / resolution_m))
    w = max(1, round(lon_m / resolution_m))
    if h > MAX_DIM or w > MAX_DIM:
        raise MeshImportError(
            f"Heightmap grid too large ({w}x{h}px at {resolution_m}m/px) — "
            f"max is {MAX_DIM}px per side. Increase resolution_m or use a "
            f"smaller bbox.")


def _mesh_upload_root() -> Path:
    """Read CACHE_ROOT fresh on each call (not a frozen module constant) so
    tests can monkeypatch this module's CACHE_ROOT, matching the pattern
    used for app.server.routers.cities in tests/conftest.py."""
    return CACHE_ROOT / "mesh_imports"


def _upload_dir(upload_id: str) -> Path:
    return _mesh_upload_root() / upload_id


def _library_session_dir(rel_path: str) -> Path:
    """Per-library-file scratch dir for the 'last heightmap' cache, mirroring
    _upload_dir's layout but keyed by a hash of rel_path (which may contain
    slashes/spaces) rather than an opaque upload_id."""
    key = hashlib.md5(rel_path.encode()).hexdigest()
    return _mesh_upload_root() / "library_sessions" / key


def save_upload(filename: str, data: bytes) -> tuple[str, str, int]:
    """Validate and persist an uploaded mesh file.

    Returns (upload_id, detected_format, size_bytes).
    Raises MeshImportError on bad extension or oversized payload.
    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise MeshImportError(
            f"Unsupported file type {ext!r}. Only .stl and .obj are accepted.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MeshImportError(
            f"File too large ({len(data) / 1e6:.1f} MB). Max is "
            f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB.")
    if len(data) == 0:
        raise MeshImportError("Uploaded file is empty.")

    upload_id = uuid.uuid4().hex
    d = _upload_dir(upload_id)
    d.mkdir(parents=True, exist_ok=True)
    mesh_path = d / f"mesh{ext}"
    mesh_path.write_bytes(data)

    logger.info(f"Mesh upload saved: {upload_id} ({ext}, {len(data)} bytes)")
    return upload_id, ext.lstrip("."), len(data)


def _resolve_mesh_path(upload_id: str) -> Path:
    d = _upload_dir(upload_id)
    if not d.is_dir():
        raise MeshImportError(f"Unknown upload_id: {upload_id!r}")
    for ext in ALLOWED_EXTENSIONS:
        p = d / f"mesh{ext}"
        if p.is_file():
            return p
    raise MeshImportError(f"No mesh file found for upload_id: {upload_id!r}")


def upload_mesh_path(upload_id: str) -> Path:
    """On-disk path of a stored upload (raises MeshImportError if unknown)."""
    return _resolve_mesh_path(upload_id)


def delete_upload(upload_id: str) -> None:
    """Remove a stored upload and any cached derived data."""
    d = _upload_dir(upload_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


def compute_heightmap(
    upload_id: str,
    bbox: dict,
    resolution_m: float = 5.0,
    up_axis: str = "z",
    infill: str = "none",
) -> tuple[np.ndarray, np.ndarray]:
    """Convert the uploaded mesh to a (heightmap, mask) pair for the given bbox.

    Delegates to city2stl.height.stl_import.stl_to_heightmap, then
    optionally fills NaN gaps via infill_idw/infill_nearest.
    """
    _check_grid_size(bbox, resolution_m)

    mesh_path = _resolve_mesh_path(upload_id)
    heightmap, mask = _stl_to_heightmap(
        mesh_path, bbox, resolution_m=resolution_m, up_axis=up_axis)

    if infill != "none":
        if infill == "idw":
            heightmap = _infill_idw(heightmap, mask)
        elif infill == "nearest":
            heightmap = _infill_nearest(heightmap)
        else:
            raise MeshImportError(f"Unknown infill method: {infill!r}")

    _save_last_heightmap(_upload_dir(upload_id), heightmap, mask)
    return heightmap, mask


def _save_last_heightmap(session_dir: Path, heightmap: np.ndarray, mask: np.ndarray) -> None:
    """Cache the most recently computed heightmap/mask for `register` to reuse.

    Only the latest heightmap per session (upload or library file) is kept —
    recomputing (e.g. after changing resolution) invalidates the previous
    one, matching the "pairs invalidated on recompute" behaviour documented
    for the client.
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    np.save(session_dir / "last_heightmap.npy", heightmap.astype(np.float32))
    np.save(session_dir / "last_mask.npy", mask.astype(bool))


def get_last_heightmap(upload_id: str) -> tuple[np.ndarray, np.ndarray] | None:
    """Return the (heightmap, mask) most recently computed for upload_id, or None."""
    d = _upload_dir(upload_id)
    if not d.is_dir():
        raise MeshImportError(f"Unknown upload_id: {upload_id!r}")
    return _read_last_heightmap(d)


def get_last_library_heightmap(rel_path: str) -> tuple[np.ndarray, np.ndarray] | None:
    """Return the (heightmap, mask) most recently computed for a library file, or None."""
    return _read_last_heightmap(_library_session_dir(rel_path))


def _read_last_heightmap(session_dir: Path) -> tuple[np.ndarray, np.ndarray] | None:
    hm_path, mask_path = session_dir / "last_heightmap.npy", session_dir / "last_mask.npy"
    if not (hm_path.is_file() and mask_path.is_file()):
        return None
    return np.load(hm_path), np.load(mask_path)


def fit_affine_from_pairs(
    ref_points: np.ndarray,
    mesh_points: np.ndarray,
) -> np.ndarray:
    """Fit a 2x3 affine matrix mapping mesh_points -> ref_points by least squares.

    ref_points, mesh_points: (N, 2) arrays of (x, y), N >= 3.
    Returns a (2, 3) float64 array [[a, b, tx], [c, d, ty]] such that
    ref ≈ M @ [mesh_x, mesh_y, 1].
    """
    n = mesh_points.shape[0]
    if n < 3:
        raise MeshImportError("At least 3 point pairs are required to fit an affine transform.")

    # Solve for M (2x3) in the least-squares sense: A @ m = b, per output row.
    # Design matrix: each mesh point (x, y) -> row [x, y, 1]
    A = np.hstack([mesh_points, np.ones((n, 1))])  # (N, 3)
    # Solve independently for x' and y' target columns
    coeffs_x, *_ = np.linalg.lstsq(A, ref_points[:, 0], rcond=None)  # (3,)
    coeffs_y, *_ = np.linalg.lstsq(A, ref_points[:, 1], rcond=None)  # (3,)
    M = np.vstack([coeffs_x, coeffs_y])  # (2, 3)
    return M


def residuals_px(
    ref_points: np.ndarray,
    mesh_points: np.ndarray,
    M: np.ndarray,
) -> np.ndarray:
    """Per-pair Euclidean residual (px) between M @ mesh_point and ref_point."""
    n = mesh_points.shape[0]
    A = np.hstack([mesh_points, np.ones((n, 1))])
    predicted = A @ M.T  # (N, 2)
    return np.linalg.norm(predicted - ref_points, axis=1)


def register_heightmap(
    heightmap: np.ndarray,
    mask: np.ndarray,
    M: np.ndarray,
    output_shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Warp a mesh heightmap + mask into the reference canvas's pixel grid.

    M: (2, 3) affine mapping mesh-space (x, y) -> ref-space (x, y), as
    returned by fit_affine_from_pairs.
    output_shape: (rows, cols) of the reference grid.
    """
    warped = _apply_transform(
        heightmap.astype(np.float64), M, output_shape=output_shape, fill_value=np.nan)
    # Nearest-style mask warp: treat as float 0/1, threshold at 0.5 after warp.
    mask_f = mask.astype(np.float64)
    warped_mask_f = _apply_transform(
        mask_f, M, output_shape=output_shape, fill_value=0.0)
    warped_mask = warped_mask_f >= 0.5
    warped[~warped_mask] = np.nan
    return warped.astype(np.float32), warped_mask


# ---------------------------------------------------------------------------
# Mesh library (config.MICROPOLITAN_STL_DIR) — browse, per-city location
# sidecars, and a heightmap disk cache keyed by file + params.
# ---------------------------------------------------------------------------

_LOCATION_SUFFIX = ".location.json"


def _library_root() -> Path:
    if not MICROPOLITAN_STL_DIR.is_dir():
        raise MeshImportError(
            f"Mesh library directory not found: {MICROPOLITAN_STL_DIR} "
            "(set STRM2STL_MICROPOLITAN_DIR to override).")
    return MICROPOLITAN_STL_DIR


def _resolve_library_path(rel_path: str) -> Path:
    """Resolve a library-relative path, rejecting any escape outside the root."""
    root = _library_root()
    candidate = (root / rel_path).resolve()
    if root not in candidate.parents and candidate != root:
        raise MeshImportError(f"Path escapes the mesh library root: {rel_path!r}")
    return candidate


def _location_sidecar_path(mesh_path: Path) -> Path:
    """Sidecar path for a mesh file, e.g. 'Miami, FL_L_Solid.stl.location.json'."""
    return Path(str(mesh_path) + _LOCATION_SUFFIX)


def list_library() -> list[dict]:
    """Scan MICROPOLITAN_STL_DIR for STL/OBJ files, grouped by city (immediate
    parent folder). Returns a list of {city, files: [{rel_path, filename,
    size_bytes, location}]} — `location` is the sidecar bbox dict or None.
    """
    root = _library_root()
    by_city: dict[str, list[dict]] = {}
    for ext in ALLOWED_EXTENSIONS:
        for mesh_path in sorted(root.rglob(f"*{ext}")):
            rel = mesh_path.relative_to(root)
            city = rel.parts[0] if len(rel.parts) > 1 else "(root)"
            location = _read_location_sidecar(mesh_path)
            by_city.setdefault(city, []).append({
                "rel_path": rel.as_posix(),
                "filename": mesh_path.name,
                "size_bytes": mesh_path.stat().st_size,
                "location": location,
            })
    return [{"city": city, "files": files} for city, files in sorted(by_city.items())]


def _read_location_sidecar(mesh_path: Path) -> dict | None:
    sidecar = _location_sidecar_path(mesh_path)
    if not sidecar.is_file():
        return None
    try:
        return json.loads(sidecar.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning(f"Could not read location sidecar: {sidecar}")
        return None


def get_library_location(rel_path: str) -> dict | None:
    """Return the saved bbox/notes for a library-relative mesh path, or None."""
    mesh_path = _resolve_library_path(rel_path)
    return _read_location_sidecar(mesh_path)


def set_library_location(
    rel_path: str,
    bbox: dict,
    up_axis: str = "z",
    notes: str = "",
    apply_to_city: bool = True,
    placement: dict | None = None,
) -> list[str]:
    """Persist bbox (+ up_axis/notes, and an optional placement record) as a sidecar.

    ``placement`` is what the Plate registration panel saves beside the bbox: the pack
    slug, centre, turn, size and tile-consensus verdict (the bbox alone cannot say the
    plate is turned).  Omitted, the sidecar has the same three keys as before.

    When apply_to_city is True (default), the same bbox is written to every
    other mesh file in the same immediate folder — city STL packs split a
    single physical model across Solid/Water meshes and print-bed tiles
    (A1/A2/B1/B2), which share one real-world location.

    Returns the list of library-relative paths that were updated.
    """
    mesh_path = _resolve_library_path(rel_path)
    if not mesh_path.is_file():
        raise MeshImportError(f"Mesh file not found in library: {rel_path!r}")

    record = {"bbox": bbox, "up_axis": up_axis, "notes": notes}
    if placement:
        record["placement"] = placement
    targets = [mesh_path]
    if apply_to_city:
        targets = sorted(
            p for ext in ALLOWED_EXTENSIONS for p in mesh_path.parent.glob(f"*{ext}")
        )

    root = _library_root()
    updated = []
    for p in targets:
        _location_sidecar_path(p).write_text(json.dumps(record, indent=2))
        updated.append(p.relative_to(root).as_posix())
    logger.info(f"Saved mesh location for {len(updated)} file(s) in {mesh_path.parent.name}")
    return updated


# ---------------------------------------------------------------------------
# Heightmap disk cache — namespace "mesh_import" in the shared array cache,
# keyed by library-relative path + bbox + resolution/up_axis/infill.
# ---------------------------------------------------------------------------

def compute_library_heightmap(
    rel_path: str,
    bbox: dict,
    resolution_m: float = 5.0,
    up_axis: str = "z",
    infill: str = "none",
) -> tuple[np.ndarray, np.ndarray]:
    """Like compute_heightmap, but for a library file + with disk caching.

    Cache key covers the file's relative path (not upload_id, which doesn't
    apply here) plus all parameters that affect the output.
    """
    from app.server.core.cache import read_array_cache, write_array_cache

    _check_grid_size(bbox, resolution_m)

    mesh_path = _resolve_library_path(rel_path)
    if not mesh_path.is_file():
        raise MeshImportError(f"Mesh file not found in library: {rel_path!r}")

    key = make_cache_key(
        "mesh_import", bbox["north"], bbox["south"], bbox["east"], bbox["west"],
        {"rel_path": rel_path, "res": resolution_m, "up": up_axis, "infill": infill},
    )
    cached = read_array_cache("mesh_import", key)
    if cached is not None and cached[0].get("heightmap") is not None:
        logger.info(f"Mesh heightmap cache hit: {rel_path} ({key[:8]}...)")
        heightmap, mask = cached[0]["heightmap"], cached[0]["mask"].astype(bool)
        _save_last_heightmap(_library_session_dir(rel_path), heightmap, mask)
        return heightmap, mask


    heightmap, mask = _stl_to_heightmap(
        mesh_path, bbox, resolution_m=resolution_m, up_axis=up_axis)

    if infill != "none":
        heightmap = _infill_idw(heightmap, mask) if infill == "idw" else _infill_nearest(heightmap)

    write_array_cache(
        "mesh_import", key,
        {"heightmap": heightmap.astype(np.float32), "mask": mask.astype(np.float32)},
        {"rel_path": rel_path, "bbox": bbox, "resolution_m": resolution_m,
         "up_axis": up_axis, "infill": infill},
    )
    _save_last_heightmap(_library_session_dir(rel_path), heightmap, mask)
    return heightmap, mask


# ---------------------------------------------------------------------------
# Auto mode — geocode a filename/foldername, run automatic OSM registration,
# and match/create a saved region. Falls back honestly to "no result" rather
# than a silently wrong guess; the caller (router) always pairs this with the
# manual point-pair picker so a low-confidence or failed auto attempt is
# never presented as final.
# ---------------------------------------------------------------------------

def parse_city_name_from_path(name: str) -> str:
    """Best-effort city-name extraction from a library rel_path, folder name,
    or uploaded filename.

    Micropolitan-style names look like "Miami,_FL_-_L_&_XL" or
    "Barcelona,_Spain_-_S,_M,_L,_&_XL" — underscores stand in for spaces, and
    a " - " separator introduces the size-variant list. This strips the
    extension and any path components, de-underscores, collapses whitespace,
    and cuts at the first " - " (or a bare filename's "_-_") to recover just
    the place name. Not guaranteed correct for arbitrary filenames — it's a
    heuristic starting point for geocoding, always reviewed by the user via
    the confidence-gated auto-register flow.
    """
    stem = Path(name).stem
    # If this came from a library rel_path (city_folder/file.stl), the
    # immediate parent folder name is usually a cleaner signal than the
    # filename itself (which often repeats the size/tile suffix again).
    parts = Path(name).parts
    candidate = parts[-2] if len(parts) >= 2 else stem

    s = candidate.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.split(r"\s-\s", s, maxsplit=1)[0].strip()
    s = s.rstrip(",").strip()
    return s


# Micropolitan STL packs (Cities/micropolitan/) tag each file with a size
# tier — "<City>_<Tier>_Solid[_<Quadrant>].stl" — documented in each pack's
# "Instructions - Micropolitan.pdf" as the real-world footprint the model
# covers: Large ~2km per side, Medium ~1.5km, Small "samples" (no stated
# size — excluded here as unreliable), Extra Large = the *same* 2km area as
# Large split into 4 print tiles (confirmed by comparing Miami's
# XL_Solid_A1's XY extent to L_Solid's: almost exactly half per axis).
# This gives a real-world scale anchor for ANY city in the pack, not just
# the four hardcoded in city2stl.osm_raster._CITY_CONFIG.
_MICROPOLITAN_TIER_EXTENT_M = {
    "L": 2000.0,
    "M": 1500.0,
    "XL": 1000.0,  # each quadrant tile covers 1/4 of the L-tier area (half per axis)
}

# Measured "L" tier XY extent (mesh units, isotropic render) across 8 real
# Micropolitan cities: Miami 101.4, Bilbao 101.6, Barcelona 102.5, Paris
# 101.4, Prague 107.6, Valencia 108.9, Lisbon 112.4, Salzburg 79.6 (median
# 102.0, stdev 9.9). 7/8 sit within -0.7%..+10.2% of the median — consistent
# with a genuinely fixed ~2km real-world footprint per "L" tile, so the flat
# per-tier metres assumption is fine for them. Salzburg alone deviates by
# -22% (its compact historic-core crop is real-world SMALLER than the other
# cities' "L" tiles, not a differently-scaled export) — using the flat
# assumption there was confirmed to derive a scale anchor ~29% off, which
# fed a wrong OSM-fetch bbox that corrupted the registration search (see
# numpy2stl's global_search.py / hill_relief_mask work). Below this
# threshold, fall back to scaling the tier's real-world size by how far the
# mesh's own extent deviates from the reference median — cheap, no network
# call, and validated to recover Salzburg's correct footprint (~1.56km).
_MICROPOLITAN_L_REFERENCE_EXTENT_UNITS = 102.0
_MICROPOLITAN_OUTLIER_DEVIATION = 0.15  # trigger the extent-ratio correction beyond this


def parse_micropolitan_scale_m_per_unit(name: str, stl_xy_extent_units: float) -> float | None:
    """Derive a metres-per-model-unit scale from a Micropolitan filename's size
    tier, or None if no tier is recognized (e.g. "S", or a non-Micropolitan file).

    `stl_xy_extent_units` is the mesh's XY bounding-box extent in its own
    (unitless) model coordinates — the longer of its X/Y bounds spans, matching
    how numpy2stl.registration.pipeline.register_city_stl computes stl_xy_extent.

    For "L"-tier files (the tier this has real reference data for), corrects
    for outlier crops whose real-world footprint genuinely isn't the tier's
    nominal size — see _MICROPOLITAN_L_REFERENCE_EXTENT_UNITS above.
    """
    if stl_xy_extent_units <= 0:
        return None
    stem = Path(name).stem
    m = re.search(r"_(XL|L|M|S)_", f"_{stem}_")
    if not m:
        return None
    tier = m.group(1)
    extent_m = _MICROPOLITAN_TIER_EXTENT_M.get(tier)
    if extent_m is None:
        return None

    if tier == "L":
        deviation = (stl_xy_extent_units - _MICROPOLITAN_L_REFERENCE_EXTENT_UNITS) \
            / _MICROPOLITAN_L_REFERENCE_EXTENT_UNITS
        if abs(deviation) > _MICROPOLITAN_OUTLIER_DEVIATION:
            corrected_extent_m = extent_m * (stl_xy_extent_units / _MICROPOLITAN_L_REFERENCE_EXTENT_UNITS)
            logger.info(
                f"auto_register: {name!r} L-tier extent {stl_xy_extent_units:.1f} units deviates "
                f"{deviation:+.1%} from the {_MICROPOLITAN_L_REFERENCE_EXTENT_UNITS:.0f}-unit reference - "
                f"using corrected footprint {corrected_extent_m:.0f}m instead of the flat {extent_m:.0f}m tier size")
            extent_m = corrected_extent_m

    return extent_m / stl_xy_extent_units


def _report_root() -> Path:
    """Per-import registration reports; read fresh so tests can move CACHE_ROOT."""
    return _mesh_upload_root() / "reports"


def auto_report_dir(city_name: str, source_key: str) -> Path:
    """One report folder per import source: ``<city-slug>_<hash8>`` (re-runs overwrite)."""
    slug = re.sub(r"[^a-z0-9]+", "_", city_name.lower()).strip("_") or "mesh"
    digest = hashlib.md5(source_key.encode("utf-8")).hexdigest()[:8]
    return _report_root() / f"{slug}_{digest}"


def report_url_for(report_dir: Path) -> str | None:
    """``/reports/files/mesh_import/...`` URL of a report folder's index.html, if written."""
    index = report_dir / "index.html"
    if not index.is_file():
        return None
    return f"/reports/files/mesh_import/{report_dir.relative_to(_report_root()).as_posix()}/index.html"


def _finite(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def comparison_scores(cmp_) -> dict:
    """The ``ComparisonResult`` breakdown the UI shows, JSON-safe (NaN -> None).

    ``building_p95_abs_m`` is the 95th percentile of the per-building |STL - OSM| height
    difference: ``building_diff_map`` paints one value per footprint, so its distinct
    finite values are the per-building samples.
    """
    per_building = np.asarray(getattr(cmp_, "building_diff_map", np.array([])), dtype=np.float64)
    vals = np.unique(per_building[np.isfinite(per_building)]) if per_building.size else np.array([])
    return {
        "rmse_m": _finite(cmp_.rmse),
        "mae_m": _finite(cmp_.mae),
        "bias_m": _finite(cmp_.bias),
        "pearson_r": _finite(cmp_.correlation),
        "spearman_r": _finite(getattr(cmp_, "rank_correlation", None)),
        "coverage_pct": _finite(cmp_.coverage_pct),
        "footprint_iou": _finite(cmp_.footprint_iou),
        "match_score": _finite(getattr(cmp_, "match_score", None)),
        "n_buildings": int(vals.size),
        "building_p95_abs_m": _finite(np.percentile(np.abs(vals), 95)) if vals.size else None,
        "building_median_abs_m": _finite(np.median(np.abs(vals))) if vals.size else None,
        "height_scale_used": _finite(getattr(cmp_, "height_scale_used", None)),
    }


def auto_register(
    mesh_path: Path,
    filename_hint: str,
    resolution: int = 512,
    *,
    write_report: bool = True,
    source_key: str | None = None,
) -> dict:
    """Run the automatic geocode + OSM registration pipeline against a mesh.

    Returns a dict describing what was found, always including a `status`
    field so the caller can decide whether to trust it:
        status: "ok" | "geocode_failed"
        city_name: str            — the parsed/geocoded place name used
        bbox: {north,south,east,west} | None  — see note below on which bbox this is
        confidence: float | None  — raw FFT xcorr peak (see docstring caveat below)
        footprint_iou: float | None
        rmse_m: float | None

    `confidence` is NOT a calibrated match probability — it's the raw
    cross-correlation peak from numpy2stl.registration (rule-of-thumb
    ">0.4 = good" per that library's own docs, not independently validated
    here). Treat this as a hint for ranking/sorting, not a pass/fail gate;
    the manual picker remains the source of truth.

    `bbox` is the region actually registered against — a tight bbox around
    the mesh's real coverage when a scale anchor could be derived (currently:
    Micropolitan filename size tiers, via parse_micropolitan_scale_m_per_unit),
    falling back to the full city_name geocode otherwise. This matters for
    sprawling metros: a downtown-only mesh registered against a whole-city
    OSM raster searches for a small target in a frame mostly irrelevant to
    it, which was a real, measured cause of poor fit quality before this was
    added (see F-MESHIMPORT plan notes, Miami case study).

    Passes free_scale=True to register_city_stl(): the geometric scale
    anchor (1/osm_margin) assumes the OSM fetch bbox landed at exactly
    osm_margin x the STL footprint, but that's only as good as the upstream
    footprint-size estimate (e.g. a Micropolitan pack's rounded "~2km" size
    tier). Measured on Miami: the anchor (0.667x) was ~1-2% off two
    independent data-driven estimates (Dice-peak 0.675x, Fourier 0.680x)
    that agreed with each other — free_scale allows a bounded +-5% nudge
    toward a sharp, well-supported sweep peak, never an open-ended
    data-trust that could drift wildly wrong (see global_search.py's
    locked-scale branch for the full reasoning and guardrails).

    Report (F-REGION §5): with ``write_report`` (default) the full numpy2stl HTML
    report is written to ``auto_report_dir(city_name, source_key)`` under the cache —
    one folder per import source, overwritten on re-run, never into the batch
    ``Code/_reports/`` — and the result carries ``report_url`` (served by the
    ``/reports`` browser) and ``report_dir``.  It costs ~11 s of matplotlib per call
    (measured over 8 cities), so ``write_report=False`` restores the old
    ``out_dir=False`` fast path.  ``scores`` is always returned
    (``comparison_scores``): RMSE, MAE, bias, Pearson r, coverage, footprint IoU,
    match score and the per-building p95.
    """
    city_name = parse_city_name_from_path(filename_hint)
    try:
        n, s, e, w = _get_city_bbox(city_name)
    except Exception as exc:
        logger.info(f"auto_register: geocoding failed for {city_name!r}: {exc}")
        return {
            "status": "geocode_failed",
            "city_name": city_name, "bbox": None,
            "confidence": None, "footprint_iou": None, "rmse_m": None,
            "scores": None, "report_url": None, "report_dir": None,
        }

    # Without a scale anchor, register_city_stl's estimate_bbox_from_stl() can
    # only produce a tight OSM-fetch bbox for the 4 cities hardcoded in
    # city2stl.osm_raster._CITY_CONFIG (Philadelphia/NYC/Chicago/Boston) — every other
    # city (Miami included) falls back to fetching the ENTIRE city's OSM
    # buildings, then tries to register a small downtown-only mesh against
    # that whole-city raster. That's a large part of why auto-register's fit
    # quality has been poor on non-hardcoded cities: the algorithm is
    # searching for a small target in a frame dominated by irrelevant area,
    # not failing to align what it can actually see.
    # Micropolitan STL packs encode a real-world footprint size in the
    # filename's size tier (see parse_micropolitan_scale_m_per_unit) — use it
    # to give every city in the pack a real scale anchor, not just the 4.
    scale_m_per_unit = None
    try:
        import trimesh as _trimesh
        _bounds = _trimesh.load(str(mesh_path)).bounds
        _xy_extent = float(max(_bounds[1][0] - _bounds[0][0], _bounds[1][1] - _bounds[0][1]))
        scale_m_per_unit = parse_micropolitan_scale_m_per_unit(filename_hint, _xy_extent)
        if scale_m_per_unit is not None:
            logger.info(
                f"auto_register: Micropolitan tier scale anchor for {filename_hint!r}: "
                f"{scale_m_per_unit:.3f} m/unit (xy_extent={_xy_extent:.2f} units)")
    except Exception as exc:
        logger.info(f"auto_register: could not derive a scale anchor for {filename_hint!r}: {exc}")

    # A tight bbox also needs to be centered on wherever the model actually is
    # (usually downtown), not the administrative-boundary centroid get_city_bbox()
    # above returns — for a sprawling metro that centroid can land many km from
    # downtown, outside a small model's real coverage entirely.
    center = None
    if scale_m_per_unit is not None:
        center = _get_city_center_point(city_name)

    out_dir = (auto_report_dir(city_name, source_key or str(mesh_path))
               if write_report else False)
    try:
        report = _register_city_stl(
            str(mesh_path), city_name, resolution=resolution,
            scale_m_per_unit=scale_m_per_unit, center=center,
            free_scale=True, out_dir=out_dir)
    except Exception as exc:
        logger.exception(f"auto_register: registration failed for {city_name!r}")
        return {
            "status": "geocode_failed",  # geocode succeeded but registration didn't
            "city_name": city_name,
            "bbox": {"north": n, "south": s, "east": e, "west": w},
            "confidence": None, "footprint_iou": None, "rmse_m": None,
            "scores": None, "report_url": None, "report_dir": None,
            "error": str(exc),
        }

    reg = report.registration
    cmp_ = report.comparison
    # Prefer the tight bbox register_city_stl actually fetched/registered
    # against (report.osm_bbox, set whenever scale_m_per_unit/center gave it a
    # real anchor) over the full-city geocode above — the region created and
    # the DEM subsequently loaded should match what the mesh actually covers,
    # not the whole city the model is a small fragment of.
    if report.osm_bbox is not None:
        rn, rs, re_, rw = report.osm_bbox
        bbox = {"north": rn, "south": rs, "east": re_, "west": rw}
    else:
        bbox = {"north": n, "south": s, "east": e, "west": w}
    return {
        "status": "ok",
        "city_name": city_name,
        "bbox": bbox,
        "confidence": float(reg.confidence),
        "footprint_iou": float(cmp_.footprint_iou),
        "rmse_m": float(cmp_.rmse),
        "scale": float(reg.scale),
        "angle_deg": float(reg.angle_deg),
        "scores": comparison_scores(cmp_),
        "report_url": report_url_for(out_dir) if out_dir else None,
        "report_dir": str(out_dir) if out_dir else None,
    }


def auto_register_upload(
    upload_id: str, resolution: int = 512, filename_hint: str | None = None,
    write_report: bool = True,
) -> dict:
    """auto_register() for an uploaded mesh, resolving its stored path.

    filename_hint overrides the name used to derive a city (defaults to the
    upload's stored filename, e.g. "Miami, FL_L_Solid.stl").
    """
    mesh_path = _resolve_mesh_path(upload_id)
    hint = filename_hint or mesh_path.name
    return auto_register(mesh_path, hint, resolution=resolution,
                         write_report=write_report, source_key=f"upload:{upload_id}")


def auto_register_library(
    rel_path: str, resolution: int = 512, filename_hint: str | None = None,
    write_report: bool = True,
) -> dict:
    """auto_register() for a mesh library file, resolving its on-disk path.

    filename_hint overrides the name used to derive a city (defaults to
    rel_path itself, e.g. "Miami,_FL_-_L_&_XL/Miami, FL_L_Solid.stl" — the
    parent-folder segment is what parse_city_name_from_path() actually uses).
    """
    mesh_path = _resolve_library_path(rel_path)
    if not mesh_path.is_file():
        raise MeshImportError(f"Mesh file not found in library: {rel_path!r}")
    hint = filename_hint or rel_path
    return auto_register(mesh_path, hint, resolution=resolution,
                         write_report=write_report, source_key=f"library:{rel_path}")
