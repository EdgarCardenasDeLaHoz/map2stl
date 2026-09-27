"""Export task: the loaded DEM + OSM layers -> city model zip (STL, 3MF, puzzle, report).

Request (``POST /api/export/start`` with ``format="city"``)::

    dem_id          handle from /api/terrain/dem (the grid the user sees)
    name            base file name
    mm_per_px       horizontal scale (default 1: one DEM pixel = 1 mm)
    z_mode          "auto" | "true" | "fit"      exaggeration, fit_height_mm, base_mm
    median_size     DEM median filter (default 3; 0/1 = off)
    layers          {layer: {enabled, mode, offset_mm, height_scale, line_width_m, ...}}
    layer_data      {layer: FeatureCollection} to use instead of the cached OSM layer
    puzzle          {piece_mm | cols+rows, knob_width_mm, knob_depth_mm, clearance_mm}

See city2stl/city_model.py and docs/plans/F-CITYMODEL-vector-city-model.md.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import zipfile

import numpy as np
from numpy2stl import write3MF

from app.server.core.city_data import get_city_layers
from app.server.core.dem_store import dem_store
from app.server.core.export_tasks import ExportTask
from app.server.core.puzzle import cut_to_zip
from city2stl.city_model import build_city_model, resolve_layers

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


def run_city_model(data: dict, task: ExportTask) -> None:
    name = data.get("name") or "city"
    task.update(2, "Loading DEM...")
    grid, bbox = dem_store.get(data["dem_id"])
    dem = np.asarray(grid, dtype=np.float64)

    styles = resolve_layers(data.get("layers"))
    enabled = [n for n, s in styles.items() if s.enabled]
    task.update(5, "Loading OSM layers...")
    osm = get_city_layers(bbox["north"], bbox["south"], bbox["east"], bbox["west"],
                          [n for n in enabled if n in OSM_LAYERS])
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

    task.update(30, "Building model...")
    model = build_city_model(
        dem, bbox, layers,
        mm_per_px=float(data.get("mm_per_px", 1.0)),
        z_mode=data.get("z_mode", "auto"),
        exaggeration=float(data.get("exaggeration", 1.0)),
        fit_height_mm=float(data.get("fit_height_mm", 30.0)),
        base_mm=float(data.get("base_mm", 5.0)),
        median_size=int(data.get("median_size", 3)),
        layer_overrides=data.get("layers"),
    )
    report = dict(model.report)

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
            report["puzzle"] = cut_to_zip(model.merged, data["puzzle"], name, zf,
                                          progress=task.update)
        zf.writestr("report.json", json.dumps(report, indent=2))

    task.complete(zip_path, f"{name}_city.zip",
                  {"X-City-Report": json.dumps(report["merged"]),
                   "Access-Control-Expose-Headers": "X-City-Report"})
