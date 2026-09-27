"""core/landmarks.py — server glue for landmark overrides (F-LANDMARK §3, §5).

* storage: one row per (region, OSM id) in ``region_landmarks`` (``core/db.py``);
* ``resolve``: request specs -> ``city2stl.landmarks.LandmarkOverride`` (uploaded
  meshes found through ``core/mesh_import``, nDSM through the survey providers);
* ``preview``: one landmark alone, built by the same ``build_on_terrain`` path the
  City Model export uses, on a flat plinth, as vertex / face lists for three.js.

The geometry and the listing live in ``city2stl.landmarks``; routes in
``routers/cities.py`` (list, preview, survey sources) and ``routers/regions.py``
(stored overrides).
"""

from __future__ import annotations

import json
import logging
import math
import time

import numpy as np

from app.server.core import mesh_import
from app.server.core.db import get_db, init_db
from city2stl import landmarks as lm

logger = logging.getLogger(__name__)

#: Longest side of the preview model, mm.
PREVIEW_SIZE_MM = 80.0
#: Preview ground cells per side, bounds (the DEM of the flat plinth).
PREVIEW_PX = (64, 320)


def _mesh_path(upload_id: str):
    try:
        return mesh_import.upload_mesh_path(upload_id)
    except mesh_import.MeshImportError as exc:
        raise lm.LandmarkError(str(exc)) from exc


def validate_spec(spec: dict) -> dict:
    """A stored / requested override spec, normalised; LandmarkError if malformed."""
    if not isinstance(spec, dict):
        raise lm.LandmarkError("override must be an object")
    kind = str(spec.get("kind") or "osm")
    if kind not in lm.OVERRIDE_KINDS:
        raise lm.LandmarkError(f"unknown override kind {kind!r} (one of {', '.join(lm.OVERRIDE_KINDS)})")
    if kind == "mesh" and not spec.get("upload_id"):
        raise lm.LandmarkError("a mesh override needs the upload_id of an uploaded mesh")
    if kind == "ndsm":
        from city2stl.height.providers.survey import PROVIDERS
        provider = str(spec.get("provider") or "auto")
        if provider != "auto" and provider not in PROVIDERS:
            raise lm.LandmarkError(f"unknown nDSM provider {provider!r}")
    return {**spec, "kind": kind}


def resolve(specs: dict | None, buildings: dict | None) -> dict:
    """``{osm_id: spec}`` -> ``{osm_id: LandmarkOverride}`` (kind "osm" dropped)."""
    specs = {oid: validate_spec(s) for oid, s in (specs or {}).items()}
    if not any(s["kind"] != "osm" for s in specs.values()):
        return {}
    feats = (buildings or {}).get("features") or []
    return lm.resolve_overrides(specs, feats, mesh_path=_mesh_path)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def load_region_overrides(region: str) -> dict:
    init_db()
    with get_db() as conn:
        rows = conn.execute("SELECT osm_id, spec_json FROM region_landmarks WHERE region_name=?",
                            (region,)).fetchall()
    return {r["osm_id"]: json.loads(r["spec_json"] or "{}") for r in rows}


def save_region_override(region: str, osm_id: str, spec: dict) -> dict:
    spec = validate_spec(spec)
    init_db()
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM regions WHERE name=?", (region,)).fetchone():
            raise KeyError(region)
        conn.execute("INSERT OR REPLACE INTO region_landmarks (region_name, osm_id, spec_json, "
                     "updated_at) VALUES (?,?,?,?)", (region, osm_id, json.dumps(spec), time.time()))
        conn.commit()
    return spec


def delete_region_override(region: str, osm_id: str) -> bool:
    init_db()
    with get_db() as conn:
        cur = conn.execute("DELETE FROM region_landmarks WHERE region_name=? AND osm_id=?",
                           (region, osm_id))
        conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def preview(buildings: dict, osm_id: str, spec: dict | None) -> dict:
    """One landmark (its outline and parts) with ``spec`` applied, on a flat plinth.

    True scale, no slenderness cap (so spires show as tagged); the plinth is 2 mm.
    Returns ``{vertices, faces, size_mm, mm_per_m, report}``.
    """
    from city2stl.city_model import build_on_terrain, choose_scale

    feats = (buildings or {}).get("features") or []
    own = lm.landmark_features(feats, osm_id)
    if not own:
        raise lm.LandmarkError(f"no building with OSM id {osm_id!r} in the city data")
    overrides = resolve({osm_id: spec}, buildings) if spec else {}
    n, s, e, w = lm._footprint_bbox(own, 0.0)
    ext_m = max((n - s) * 111_320.0, (e - w) * 111_320.0 * math.cos(math.radians((n + s) / 2)), 1.0)
    margin = max(0.15 * ext_m, 5.0)
    n, s, e, w = lm._footprint_bbox(own, margin)
    bbox = {"north": n, "south": s, "east": e, "west": w}
    lat_m = (n - s) * 111_320.0
    lon_m = (e - w) * 111_320.0 * math.cos(math.radians((n + s) / 2))
    px_m = min(max(max(lat_m, lon_m) / PREVIEW_PX[1], 0.25), max(lat_m, lon_m) / PREVIEW_PX[0])
    shape = (max(int(round(lat_m / px_m)), 8), max(int(round(lon_m / px_m)), 8))
    scale = choose_scale(bbox, shape, 0.0, 0.0, mm_per_px=PREVIEW_SIZE_MM / max(shape),
                         z_mode="true", base_mm=2.0)
    z = scale.z_mm(np.zeros(shape))
    model = build_on_terrain(
        z, bbox, scale, {"buildings": {"type": "FeatureCollection", "features": own}},
        layer_overrides={"buildings": {"max_slenderness": 0}}, landmark_overrides=overrides)
    mesh = model.merged
    return {
        "vertices": np.round(np.asarray(mesh.vertices, dtype=np.float64), 3).ravel().tolist(),
        "faces": np.asarray(mesh.faces, dtype=np.int64).ravel().tolist(),
        "size_mm": [round(float(x), 2) for x in mesh.extents],
        "mm_per_m": round(scale.mm_per_px / scale.m_per_px, 4),
        "report": {"landmarks": model.report.get("landmarks", {}),
                   "buildings": model.report.get("layers", {}).get("buildings", {}),
                   "watertight": model.report["merged"]["watertight"]},
    }
