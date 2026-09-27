"""Pre-flight report: what a city / puzzle / terrain export will produce, before building it.

``POST /api/export/preflight`` takes the same body as ``/api/export/start``
(``format`` = ``city`` | ``puzzle`` | ``stl`` ...) and returns in seconds what the
build takes minutes to find out: model size against the bed, scale and vertical
exaggeration, the puzzle grid, per-layer feature counts with the printability
rules applied (``widened`` / ``clamped``, as the build reports them), thinnest
feature, tallest spike, estimated faces, filament and print time, and warnings.

Nothing is built: the terrain stage runs (fast), the layers go through
``city_model.layer_preflight`` (outlines and rules, no solids), and the OSM
layers are read from the cache only - a layer that is not cached is reported,
not fetched.

Estimates (stated in the response as ``estimate.formula``):

* printed volume = shell + 15 % x (volume - shell), shell = surface area x 0.9 mm
  (2 perimeters x 0.45 mm line width, applied to walls, top and bottom skins
  alike), capped at the volume;
* filament = printed volume x 1.24 g/cm^3 (PLA);
* print time = printed volume / 8 mm^3/s (a typical average volumetric rate
  for a 0.4 mm nozzle including travel; real printers vary 2x either way).

The finished build puts the same figures, from the real mesh, in
``report.json`` under ``check`` (:func:`build_check`).
"""

from __future__ import annotations

import logging
import math
import time

import numpy as np

logger = logging.getLogger(__name__)

PLA_G_PER_CM3 = 1.24
INFILL = 0.15
PERIMETERS = 2
LINE_WIDTH_MM = 0.45
FLOW_MM3_S = 8.0
DEFAULT_BED_MM = (250.0, 210.0)
FACE_WARN = 2_000_000
PRINT_HOURS_WARN = 48.0
FACE_EST_MAX_PIXELS = 120_000

FORMULA = (f"printed = shell + {INFILL:.0%} x (volume - shell), shell = area x "
           f"{PERIMETERS * LINE_WIDTH_MM:g} mm ({PERIMETERS} perimeters x {LINE_WIDTH_MM} mm); "
           f"grams = printed x {PLA_G_PER_CM3} g/cm3 (PLA); hours = printed / "
           f"{FLOW_MM3_S:g} mm3/s")


def print_estimate(volume_mm3: float, area_mm2: float) -> dict:
    """Filament (g) and print time (h) for a solid of this volume and surface area."""
    volume = max(float(volume_mm3), 0.0)
    shell = min(volume, float(area_mm2) * PERIMETERS * LINE_WIDTH_MM)
    printed = shell + INFILL * (volume - shell)
    return {"volume_cm3": round(volume / 1000, 1), "printed_cm3": round(printed / 1000, 1),
            "filament_g": round(printed / 1000 * PLA_G_PER_CM3, 1),
            "print_hours": round(printed / FLOW_MM3_S / 3600, 1), "formula": FORMULA}


def _terrain_figures(z: np.ndarray, s: float) -> dict:
    """Volume, surface area and tallest spike of the terrain block (floor z = 0)."""
    zc = (z[:-1, :-1] + z[1:, :-1] + z[:-1, 1:] + z[1:, 1:]) / 4
    gy, gx = np.gradient(z, s)
    top = float((np.sqrt(1 + gx[:-1, :-1] ** 2 + gy[:-1, :-1] ** 2)).sum()) * s * s
    rim = np.concatenate([z[0, :-1], z[:-1, -1], z[-1, 1:], z[1:, 0]])
    h, w = z.shape
    from scipy.ndimage import uniform_filter

    # Spike: height above the mean of a ~5 mm neighbourhood.
    k = max(3, int(round(5.0 / s)) | 1)
    spike = float((z - uniform_filter(z, size=k, mode="nearest")).max())
    return {"volume_mm3": float(zc.sum()) * s * s,
            "area_mm2": top + (w - 1) * (h - 1) * s * s + float(rim.sum()) * s,
            "spike_mm": spike}


def _terrain_faces_estimate(z: np.ndarray, tol: float) -> tuple[int, int]:
    """(faces, stride): adaptive terrain solid faces, from a TIN of a strided grid.

    The TIN's size follows the relief, not the pixel count, so a subsampled
    grid gives the order of magnitude; the strided TIN misses fine detail,
    which ``sqrt(stride)`` roughly makes up for (within ~40 % on real DEMs).
    """
    from numpy2stl.processing.decimate import heightfield_tin

    h, w = z.shape
    stride = max(1, math.ceil(math.sqrt(h * w / FACE_EST_MAX_PIXELS)))
    rows = np.unique(np.r_[0:h:stride, h - 1])
    cols = np.unique(np.r_[0:w:stride, w - 1])
    _, tris = heightfield_tin(z[np.ix_(rows, cols)], tol, seed_step=max(2, 8 // stride))
    top = len(tris) * math.sqrt(stride)
    # Top + a copy as the bottom (tin_solid) + walls; the coplanar bottom is then
    # merged by lossless_simplify to about the border's size.
    border = 2 * (h + w)
    return int(top + 3 * border), stride


def _cached_layers(bbox: dict, names: list[str], data: dict) -> tuple[dict, list[str], bool]:
    """The cached OSM payload for ``names`` (never fetched), the names missing, and
    whether the payload is one ``city_data.get_city_layers`` would refetch (stale).
    Reads the entry the build would (``city_model_task.city_osm_params``)."""
    from app.server.core.cache import osm_cache_key, read_osm_cache
    from app.server.core.city_model_task import city_osm_params
    from city2stl.cache_policy import (
        city_cache_missing_building_parts,
        city_cache_missing_height_source,
    )

    tol, min_area = city_osm_params(data)
    key = osm_cache_key(bbox["north"], bbox["south"], bbox["east"], bbox["west"], tol, min_area)
    cached = read_osm_cache(key) or {}
    stale = bool(cached) and (city_cache_missing_height_source(cached)
                              or city_cache_missing_building_parts(cached))
    have = {n: cached[n] for n in names if isinstance(cached.get(n), dict)}
    return have, [n for n in names if n not in have], stale


def _bed(data: dict) -> tuple[float, float]:
    bed = data.get("bed_mm") or DEFAULT_BED_MM
    return float(bed[0]), float(bed[1])


def _fits(w: float, d: float, bed: tuple[float, float]) -> bool:
    return (w <= bed[0] and d <= bed[1]) or (w <= bed[1] and d <= bed[0])


def preflight(data: dict) -> dict:
    """The pre-flight report for an export request (see the module docstring)."""
    from app.server.core.city_model_task import OSM_LAYERS, normalize_request
    from app.server.core.export import puzzle_spec, terrain_stage
    from app.server.core.export_params import ExportContext
    from app.server.core.puzzle import (
        MAX_PIECES,
        choose_method,
        grid_edges,
        knob_size,
    )
    from city2stl.city_model import Terrain, layer_preflight, resolve_layers, terrain_tolerance

    t0 = time.perf_counter()
    fmt = str(data.get("format") or "city")
    data = normalize_request(data) if fmt == "city" else data
    p = ExportContext.from_request(data)
    if not p.dem_values or not p.height or not p.width:
        raise ValueError("Missing DEM data")
    field = terrain_stage(p, data)
    z, s = field.z_mm, p.mm_per_pixel
    h, w = z.shape
    width, depth, height = (w - 1) * s, (h - 1) * s, float(z.max())
    bed = _bed(data)
    scale = field.scale.describe()
    warnings: list[str] = []
    if p.composite_error:
        warnings.append(f"Composite failed, the raw DEM will be used: {p.composite_error}")

    terrain = _terrain_figures(z, s)
    tol = terrain_tolerance(field.scale)
    faces, stride = _terrain_faces_estimate(z, tol)
    report: dict = {
        "format": fmt,
        "size_mm": [round(width, 1), round(depth, 1), round(height, 1)],
        "bed_mm": list(bed),
        "fits_bed": _fits(width, depth, bed),
        "scale": scale,
        "vertical_exaggeration": scale["vertical_exaggeration"],
        "terrain": {"tolerance_mm": round(tol, 3), "faces_est": faces,
                    "face_estimate_stride": stride,
                    "tallest_spike_mm": round(terrain["spike_mm"], 2)},
        "layers": {},
    }

    # Layers (city only)
    volume, area = terrain["volume_mm3"], terrain["area_mm2"]
    thinnest, tallest = None, None
    if fmt == "city":
        if not p.bbox:
            raise ValueError("The city model needs the region's bbox (dem_id or bbox)")
        styles = resolve_layers(data.get("layers"))
        enabled = [n for n, st in styles.items() if st.enabled]
        layers, missing, stale = _cached_layers(p.bbox, [n for n in enabled if n in OSM_LAYERS], data)
        if stale:
            warnings.append("The cached city data predates the current pipeline: the build "
                            "refetches it, so its counts may differ from these")
        layers.update({n: fc for n, fc in (data.get("layer_data") or {}).items() if n in enabled})
        missing = [n for n in missing if n not in layers]
        if missing:
            warnings.append("Not cached yet (fetched at build time, not counted here): "
                            + ", ".join(missing))
        if "trails" in enabled:
            warnings.append("Trails are fetched at build time and not counted here")
        terr = Terrain(np.asarray(z, np.float64), p.bbox, field.scale)
        tin_density = faces / 4 / max(width * depth, 1e-9)
        for name, fc in layers.items():
            feats = (fc or {}).get("features") or []
            if not feats:
                continue
            st = layer_preflight(name, feats, styles[name], terr, tin_density=tin_density)
            report["layers"][name] = st
            faces += st.get("faces_est", 0)
            volume += st.get("volume_mm3", 0.0)
            if "thinnest_mm" in st:
                thinnest = st["thinnest_mm"] if thinnest is None else min(thinnest, st["thinnest_mm"])
            area += st.get("surface_mm2", 0.0)
            if "tallest_mm" in st:
                tallest = st["tallest_mm"] if tallest is None else max(tallest, st["tallest_mm"])
            if st.get("widened"):
                warnings.append(f"{name}: {st['widened']} features narrower than "
                                f"{styles[name].min_width_mm} mm will be widened")
            if st.get("clamped"):
                warnings.append(f"{name}: {st['clamped']} features taller than "
                                f"{styles[name].max_slenderness:g}x their width will be "
                                "capped")
    report["thinnest_feature_mm"] = thinnest
    report["tallest_spike_mm"] = round(max(terrain["spike_mm"], tallest or 0.0), 2)
    report["faces_est"] = int(faces)
    report["estimate"] = print_estimate(volume, area)

    # Puzzle grid
    spec = data.get("puzzle") if fmt == "city" else (puzzle_spec(data) if fmt == "puzzle"
                                                     else None)
    if spec:
        try:
            xs, ys = grid_edges(width, depth, spec)
            kw, kd = knob_size(xs, ys, spec)
            method = choose_method(spec, terrain_only=fmt != "city" or not report["layers"])
            cols, rows = len(xs) - 1, len(ys) - 1
            report["puzzle"] = {
                "cols": cols, "rows": rows, "pieces": cols * rows, "method": method,
                "col_edges_mm": [round(float(x), 1) for x in xs],
                "row_edges_mm": [round(float(y), 1) for y in ys],
                "largest_piece_mm": [round(float(np.diff(xs).max()), 1),
                                     round(float(np.diff(ys).max()), 1)],
                "knob_mm": [round(kw, 1), round(kd, 1)],
                "knob_shape": spec.get("knob_shape") or "classic",
            }
            if cols * rows > MAX_PIECES:
                warnings.append(f"{cols * rows} pieces: the maximum is {MAX_PIECES}")
            if not _fits(float(np.diff(xs).max()) + kd, float(np.diff(ys).max()) + kd, bed):
                warnings.append("Some puzzle pieces (with knobs) are larger than the bed")
        except ValueError as exc:
            warnings.append(f"Puzzle: {exc}")
    elif not report["fits_bed"]:
        warnings.append(f"{width:.0f} x {depth:.0f} mm does not fit the {bed[0]:.0f} x "
                        f"{bed[1]:.0f} mm bed: enable the puzzle or reduce the size")

    ve = scale["vertical_exaggeration"]
    if ve > 3:
        warnings.append(f"Vertical exaggeration {ve:g}x: buildings and terrain are "
                        "stretched far beyond true scale")
    if height > 250:
        warnings.append(f"Model is {height:.0f} mm tall")
    if report["faces_est"] > FACE_WARN:
        warnings.append(f"About {report['faces_est']:,} faces: slicers slow down, "
                        "consider fewer layers or a lower resolution")
    if report["estimate"]["print_hours"] > PRINT_HOURS_WARN:
        warnings.append(f"About {report['estimate']['print_hours']:.0f} h of printing")
    if float(np.ptp(z)) <= 1e-6:
        warnings.append("The terrain is flat (no relief in the DEM)")
    report["warnings"] = warnings
    report["seconds"] = round(time.perf_counter() - t0, 2)
    return report


def build_check(model_report: dict, merged, data: dict, puzzle: dict | None = None) -> dict:
    """The ``check`` block of a finished build's report.json: the pre-flight
    figures computed from the real mesh (size vs bed, filament, print time)."""
    bed = _bed(data)
    size = [float(x) for x in merged.extents]
    check = {"size_mm": [round(x, 1) for x in size], "bed_mm": list(bed),
             "fits_bed": _fits(size[0], size[1], bed),
             "vertical_exaggeration": model_report["scale"]["vertical_exaggeration"],
             "faces": int(len(merged.faces)),
             "watertight": model_report["merged"]["watertight"],
             "estimate": print_estimate(float(merged.volume), float(merged.area))}
    layers = model_report.get("layers") or {}
    check["widened"] = {k: v.get("widened", 0) for k, v in layers.items() if v.get("widened")}
    check["clamped"] = {k: v.get("clamped", 0) for k, v in layers.items() if v.get("clamped")}
    warnings = []
    if not check["watertight"]:
        warnings.append("Merged model is not watertight")
    if not check["fits_bed"] and not puzzle:
        warnings.append("Model does not fit the bed and no puzzle was cut")
    if puzzle and puzzle.get("layout", {}).get("oversize"):
        warnings.append("Pieces larger than the bed: " + ", ".join(puzzle["layout"]["oversize"]))
    check["warnings"] = warnings
    return check
