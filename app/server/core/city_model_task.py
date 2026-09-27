"""Export task: terrain stage + OSM layers -> city model zip (STL, 3MF, puzzle, report).

The DEM and every terrain setting are resolved exactly as for the other mesh
exports (``ExportContext`` + ``export.terrain_stage``): composite spec, edited
values or ``dem_id``; median filter, sea-level cap, vertical scale, label and
contours. The layers are then built on that heightfield (``build_on_terrain``).

Request (``POST /api/export/start`` with ``format="city"``)::

    dem_id | dem_values | composite_layers    the terrain, as for any export
    name            base file name
    mm_per_pixel    horizontal scale (default 1: one DEM pixel = 1 mm)
    z_mode          "auto" | "true" | "fit"   exaggeration, model_height, base_height
    median_size     DEM median filter (default 3; 0/1 = off)
    (legacy names mm_per_px, fit_height_mm, base_mm are still accepted)
    layers          {layer: {enabled, mode, offset_mm, height_scale, line_width_m, ...}}
    layer_data      {layer: FeatureCollection} to use instead of the cached OSM layer
    landmark_overrides  {osm_id: {kind: "mesh"|"ndsm"|"osm", ...}} buildings replaced by
                    an uploaded mesh or a surveyed nDSM solid (city2stl.landmarks;
                    resolved before the build, a bad mesh / no survey data fails the
                    task with the reason; report.json "landmarks" says what applied)
    puzzle          {piece_mm | cols+rows | col_edges_mm+row_edges_mm, knob_width_mm,
                     knob_depth_mm, knob_shape, clearance_mm, method, engrave,
                     layout, bed_mm}   (app/server/core/puzzle.py)
    bed_mm          [w, h] print bed, for the report's ``check`` block

report.json carries the build report plus ``puzzle`` (grid, method, timings) and
``check`` (size vs bed, faces, watertight, widened/clamped, filament and print
time estimates - app/server/core/preflight.py).

See city2stl/city_model.py and docs/plans/F-CITYMODEL-vector-city-model.md.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import zipfile

from numpy2stl import write3MF

from app.server.core.city_data import CityAreaTooLarge, check_city_area, get_city_layers
from app.server.core.export import terrain_stage
from app.server.core.export_params import ExportContext
from app.server.core.export_tasks import ExportTask
from app.server.core.landmarks import resolve as resolve_landmarks
from app.server.core.preflight import build_check
from app.server.core.puzzle import Heightfield, cut_to_zip
from city2stl.city_model import build_on_terrain, resolve_layers
from city2stl.landmarks import LandmarkError

logger = logging.getLogger(__name__)

OSM_LAYERS = ["buildings", "roads", "waterways", "walls", "towers", "churches",
              "fortifications", "green", "railways"]


def _trails(bbox: dict) -> dict:
    from geo2stl.trails import OsmTrailsLayer
    got = OsmTrailsLayer().fetch(bbox["north"], bbox["south"], bbox["east"], bbox["west"])
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
    osm = get_city_layers(bbox["north"], bbox["south"], bbox["east"], bbox["west"],
                          [n for n in enabled if n in OSM_LAYERS], allow_large=allow_large)
    layers = {n: osm[n] for n in enabled if isinstance(osm.get(n), dict)}
    # Layers the caller edited locally (e.g. SDK roof classification) replace the
    # cached OSM copy, which has no record of those edits.
    layers.update({n: fc for n, fc in (data.get("layer_data") or {}).items() if n in enabled})
    if "trails" in enabled:
        task.update(20, "Loading trails...")
        try:
            layers["trails"] = _trails(bbox)
        except Exception as exc:
            logger.warning("Trails skipped: %s", exc)

    landmark_overrides = {}
    if data.get("landmark_overrides") and "buildings" in layers:
        task.update(22, "Loading landmark overrides...")
        try:
            landmark_overrides = resolve_landmarks(data["landmark_overrides"], layers["buildings"])
        except LandmarkError as exc:
            task.fail(str(exc))
            return

    task.update(25, "Terrain stage...")
    field = terrain_stage(p, data)
    task.update(30, "Building model...")
    model = build_on_terrain(field.z_mm, bbox, field.scale, layers,
                             layer_overrides=data.get("layers"),
                             landmark_overrides=landmark_overrides)
    report = dict(model.report)
    if p.composite_error:
        report["composite_error"] = p.composite_error

    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        task.update(75, "Writing STL...")
        zf.writestr(f"{name}.stl", _stl_bytes(model.merged))
        task.update(82, "Writing 3MF...")
        fd, tmp3mf = tempfile.mkstemp(suffix=".3mf")
        os.close(fd)
        try:
            write3MF(tmp3mf, {f"{name}_{k}": (m.vertices, m.faces) for k, m in model.parts.items()})
            zf.write(tmp3mf, f"{name}.3mf")
        finally:
            os.unlink(tmp3mf)
        if data.get("puzzle"):
            # With no layer solids the model is the terrain block: cut it the fast
            # way, straight from the heightfield, unless the request says otherwise.
            hf = (Heightfield(field.z_mm, p.mm_per_pixel, report["tolerances_mm"]["terrain"])
                  if set(model.parts) == {"terrain"} else None)
            report["puzzle"] = cut_to_zip(model.merged, data["puzzle"], name, zf,
                                          progress=task.update, heightfield=hf)
        report["check"] = build_check(report, model.merged, data, report.get("puzzle"))
        zf.writestr("report.json", json.dumps(report, indent=2))

    task.complete(zip_path, f"{name}_city.zip",
                  {"X-City-Report": json.dumps(report["merged"]),
                   "Access-Control-Expose-Headers": "X-City-Report"})
