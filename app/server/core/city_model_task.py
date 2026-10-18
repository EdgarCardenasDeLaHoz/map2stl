"""Export task: terrain stage + OSM layers -> city model zip (STL, 3MF, puzzle, report).

The DEM and every terrain setting are resolved exactly as for the other mesh
exports (``ExportContext`` + ``export.mesh_terrain_stage``): composite spec, edited
values or ``dem_id``; median filter, sea-level cap, vertical scale, label and
contours. The layers are then built on that heightfield (``build_on_terrain``).

Request (``POST /api/export/start`` with ``format="city"``)::

    dem_id | dem_values | composite_layers    the terrain, as for any export
    name            base file name
    mm_per_pixel    horizontal scale (default 1: one DEM pixel = 1 mm)
    z_mode          "auto" | "true" | "fit"   exaggeration, model_height, base_height
    median_size     DEM median filter (default 3; 0/1 = off)
    vector_rivers   "auto" (default) | true | false: the composite's rivers cut as vector
                    solids, with vector_rivers_min_depth_mm (export.mesh_terrain_stage)
    (legacy names mm_per_px, fit_height_mm, base_mm are still accepted)
    layers          {layer: {enabled, mode, offset_mm, height_scale, line_width_m, ...}}
                    (trails are off unless enabled here; only then are they fetched;
                    trees, the lidar canopy, only with {"trees": {"enabled": true,
                    "preview"?: bool}}, off where no survey covers the box: wants_trees)
    layer_data      {layer: FeatureCollection} to use instead of the cached OSM layer
                    (merged onto the server's: a feature still on a fallback height keeps
                    the survey height the server measured, city_data.merge_layer_data)
    simplify_tolerance, min_area, detail
                    the Cities panel settings the OSM layers were loaded with, so the
                    build reads that exact cache entry instead of refetching (and the
                    panel's height overrides match its features). Defaults 3 m / 5 m² /
                    "full" = the panel defaults; "coarse" raises min_area as
                    /api/cities does. See city_osm_params().
    roof_shapes     "auto" (default) | "off" (a bool also works: true = auto): classify the
                    roof shape of untagged buildings (city_data.with_roof_shapes, on read,
                    after the survey fill; never overwrites an OSM roof:shape). "off" is
                    the output without the classifier, byte for byte. report.json
                    "roof_shapes" holds the stats (classified, by_shape, reason)
    landmark_overrides  {osm_id: {kind: "mesh"|"ndsm"|"height"|"osm", ...}} buildings replaced by
                    an uploaded mesh or a surveyed nDSM solid, or rescaled to a known
                    height (city2stl.landmarks;
                    resolved before the build, a bad mesh / no survey data fails the
                    task with the reason; report.json "landmarks" says what applied)
    reference_landmarks  bool, default false: swap the Alhambra's buildings for the stored
                    reference raster (city2stl.landmarks.reference_landmarks; no effect
                    outside its bbox; a landmark_overrides entry on the same id wins).
                    report.json "landmarks_reference" holds the stats
    puzzle          {piece_mm | cols+rows | col_edges_mm+row_edges_mm, knob_width_mm,
                     knob_depth_mm, knob_shape, clearance_mm, method, engrave,
                     layout, bed_mm}   (app/server/core/puzzle.py)
    bed_mm          [w, h] print bed, for the report's ``check`` block

report.json carries the build report plus ``puzzle`` (grid, method, timings) and
``check`` (size vs bed, faces, watertight, widened/clamped, filament and print
time estimates - app/server/core/preflight.py).

See city2stl/city_model.py and docs/plans/active/F-CITYMODEL-vector-city-model.md.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import zipfile

from numpy2stl import write3MF

from app.server.config import COARSE_MIN_BUILDING_AREA_M2
from app.server.core.city_data import (
    CityAreaTooLarge,
    check_city_area,
    get_city_layers,
    merge_layer_data,
)
from app.server.core.export import mesh_terrain_stage
from app.server.core.export_params import ExportContext
from app.server.core.export_tasks import ExportTask
from app.server.core.landmarks import resolve as resolve_landmarks
from app.server.core.preflight import build_check
from app.server.core.puzzle import Heightfield, cut_to_zip
from city2stl.canopy import canopy_layer
from city2stl.city_model import build_on_terrain, resolve_layers
from city2stl.landmarks import LandmarkError, reference_landmarks

logger = logging.getLogger(__name__)

OSM_LAYERS = ["buildings", "roads", "waterways", "walls", "towers", "churches",
              "fortifications", "green", "railways"]


# Cities panel defaults (FetchLayersSection.vue #citySimplifyTolerance / #cityMinArea,
# city-overlay.js fallbacks). A build used to read the OSM cache at 0.5 m / 5 m²
# whatever the panel loaded, so the first build refetched every layer.
CITY_SIMPLIFY_TOLERANCE_M = 3.0
CITY_MIN_AREA_M2 = 5.0


def _non_negative(value, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) and v >= 0 else default


def city_osm_params(data: dict) -> tuple[float, float]:
    """``(simplify_tolerance, min_area)`` of the OSM cache entry a city build reads.

    The request's values (the Cities panel's, as loaded), else the panel defaults.
    ``detail="coarse"`` raises min_area like ``/api/cities`` does, so the key matches
    the entry a coarse load wrote.
    """
    tol = _non_negative(data.get("simplify_tolerance"), CITY_SIMPLIFY_TOLERANCE_M)
    min_area = _non_negative(data.get("min_area"), CITY_MIN_AREA_M2)
    if data.get("detail") == "coarse":
        min_area = max(min_area, COARSE_MIN_BUILDING_AREA_M2)
    return tol, min_area


def _trails(bbox: dict, cached_only: bool = False) -> dict | None:
    """Ski + hiking trails for the bbox as one FeatureCollection (OSM, cached 7 days
    by ``geo2stl.trails``). ``cached_only``: None instead of a fetch on a miss."""
    from geo2stl.cache import read_json_cache
    from geo2stl.trails import TRAILS_CACHE_NAMESPACE, OsmTrailsLayer, trails_cache_key

    box = (bbox["north"], bbox["south"], bbox["east"], bbox["west"])
    if cached_only:
        got = read_json_cache(TRAILS_CACHE_NAMESPACE, trails_cache_key(*box))
        if not isinstance(got, dict):
            return None
    else:
        got = OsmTrailsLayer().fetch(*box)
    feats = [f for fc in got.values() for f in (fc or {}).get("features", [])]
    return {"type": "FeatureCollection", "features": feats}


def _stl_bytes(mesh) -> bytes:
    return mesh.export(file_type="stl")


_LEGACY_NAMES = {"mm_per_px": "mm_per_pixel", "fit_height_mm": "model_height",
                 "base_mm": "base_height"}


def normalize_request(data: dict) -> dict:
    """The city defaults (fit height 30 mm) and legacy field names applied."""
    data = {"model_height": 30.0, **data}
    for old, new in _LEGACY_NAMES.items():
        if old in data:
            data.setdefault(new, data[old])
    return data


def wants_trees(data: dict) -> bool:
    """The lidar canopy (``city2stl.canopy``) is opt-in: only ``layers.trees.enabled: true``
    builds it (the ``trees`` style is enabled by default, so a plain request must not)."""
    t = (data.get("layers") or {}).get("trees")
    return isinstance(t, dict) and t.get("enabled") is True


def run_city_model(data: dict, task: ExportTask) -> None:
    data = normalize_request(data)
    name = data.get("name") or "city"
    task.update(2, "Loading DEM...")
    p = ExportContext.from_request(data)
    if not p.dem_values or not p.height or not p.width:
        task.fail("Missing DEM data")
        return
    if not p.bbox:
        task.fail("The city model needs the region's bbox (dem_id or bbox)")
        return
    bbox = p.bbox

    styles = resolve_layers(data.get("layers"))
    enabled = [n for n, s in styles.items() if s.enabled]
    # Size guard (F-REGION): no city layers, trails included, on boxes > 25 km
    # diagonal unless the request sets allow_large_city.
    allow_large = bool(data.get("allow_large_city"))
    try:
        check_city_area(bbox["north"], bbox["south"], bbox["east"], bbox["west"],
                        [n for n in enabled if n in OSM_LAYERS or n == "trails"], allow_large)
    except CityAreaTooLarge as exc:
        task.fail(str(exc))
        return
    task.update(5, "Loading OSM layers...")
    tol, min_area = city_osm_params(data)
    trees = wants_trees(data)
    names = [n for n in enabled if n in OSM_LAYERS]
    if trees:   # the canopy mask needs the buildings (kept off) and green (kept in)
        names += [n for n in ("buildings", "green") if n not in names]
    osm = get_city_layers(bbox["north"], bbox["south"], bbox["east"], bbox["west"],
                          names, tol, min_area, allow_large=allow_large,
                          roof_shapes=data.get("roof_shapes"),
                          roof_progress=lambda d, t: task.update(
                              5, f"Classifying roofs {d:,} of {t:,}..."))
    layers = {n: osm[n] for n in enabled if isinstance(osm.get(n), dict)}
    # Layers the caller edited locally (e.g. SDK roof classification, height overrides)
    # replace the cached OSM copy, which has no record of those edits; the survey
    # heights the server filled are merged back in (city_data.merge_layer_data).
    layers = merge_layer_data(layers, data.get("layer_data"), enabled)
    trail_context = None
    if "trails" in enabled:   # optional layer: fetched only when the request enables it
        task.update(20, "Loading trails...")
        try:
            layers["trails"] = _trails(bbox)
        except Exception as exc:
            logger.warning("Trails skipped: %s", exc)
        # Where the town is, for filter_trails, even with buildings / roads not built.
        trail_context = {n: osm[n] for n in ("buildings", "roads") if isinstance(osm.get(n), dict)}

    landmark_overrides = {}
    if data.get("landmark_overrides") and "buildings" in layers:
        task.update(22, "Loading landmark overrides...")
        try:
            landmark_overrides = resolve_landmarks(data["landmark_overrides"], layers["buildings"])
        except LandmarkError as exc:
            task.fail(str(exc))
            return
    ref_stats = None
    if data.get("reference_landmarks") and "buildings" in layers:
        ref, ref_stats = reference_landmarks(bbox, enabled=True)
        landmark_overrides = {**ref, **landmark_overrides}   # the user's override wins

    task.update(25, "Terrain stage...")
    # Vector rivers (p.vector_rivers) come back as a "rivers" layer to cut.
    field, river_layers = mesh_terrain_stage(p, data)
    layers = {**layers, **river_layers}
    canopy = None
    if trees:
        task.update(28, "Loading tree canopy...")
        canopy = canopy_layer(bbox, osm.get("buildings"), osm.get("green"), field.scale,
                              preview=bool(data["layers"]["trees"].get("preview")))
    task.update(30, "Building model...")
    model = build_on_terrain(field.z_mm, bbox, field.scale, layers,
                             layer_overrides=data.get("layers"),
                             landmark_overrides=landmark_overrides,
                             trail_context=trail_context, canopy=canopy)
    report = dict(model.report)
    if osm.get("survey_fill"):
        report["survey_fill"] = osm["survey_fill"]
    if osm.get("roof_shapes"):
        report["roof_shapes"] = osm["roof_shapes"]
    if ref_stats is not None:
        report["landmarks_reference"] = ref_stats
    if p.composite_error:
        report["composite_error"] = p.composite_error
    if p.vector_rivers_report is not None:
        report["vector_rivers"] = p.vector_rivers_report
    elif p.vector_rivers_note:
        report["vector_rivers"] = {"note": p.vector_rivers_note}

    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        task.update(75, "Writing STL...")
        zf.writestr(f"{name}.stl", _stl_bytes(model.merged))
        task.update(82, "Writing 3MF...")
        fd, tmp3mf = tempfile.mkstemp(suffix=".3mf")
        os.close(fd)
        try:
            write3MF(tmp3mf, {f"{name}_{k}": (m.vertices, m.faces) for k, m in model.parts.items()})
            zf.write(tmp3mf, f"{name}.3mf", compress_type=zipfile.ZIP_STORED)   # already a zip
        finally:
            os.unlink(tmp3mf)
        if data.get("puzzle"):
            # With no layer solids the model is the terrain block: cut it the fast
            # way, straight from the heightfield, unless the request says otherwise.
            # Cut layers (water, engraved, vector rivers) make no part of their own
            # but are not in the heightfield either, so they need the boolean path.
            cuts = any(rep.get("mode") in ("engraved", "water") and rep.get("solids")
                       for rep in report["layers"].values())
            hf = (Heightfield(field.z_mm, p.mm_per_pixel, report["tolerances_mm"]["terrain"])
                  if set(model.parts) == {"terrain"} and not cuts else None)
            report["puzzle"] = cut_to_zip(model.merged, data["puzzle"], name, zf,
                                          progress=task.update, heightfield=hf)
        report["check"] = build_check(report, model.merged, data, report.get("puzzle"))
        zf.writestr("report.json", json.dumps(report, indent=2))

    fd, viewer_path = tempfile.mkstemp(suffix=".parts")
    os.close(fd)
    from numpy2stl.io import write_parts_file
    write_parts_file(model.parts, viewer_path)
    task.viewer_path = viewer_path
    task.complete(zip_path, f"{name}_city.zip",
                  {"X-City-Report": json.dumps(report["merged"]),
                   "Access-Control-Expose-Headers": "X-City-Report"})
