"""Place a photo with no recorded location (F-WEB2 step 5).

Identifications (image column -> OSM building) feed ``camera_solver.solve_pose``. Sources:
- labels: a photo whose buildings are named, e.g. ``sites/annotations/*.json`` transcribed by
  hand from labels drawn on the image. Also the known-answer test for the other sources.
- (planned) photo-to-photo feature matches against located photos, and skyline matching
  against OSM-predicted skylines.

    ann = load_annotations(path)
    idx = building_index(osm_features)           # lower-case name -> [(lat, lon), ...]
    pose, chosen = solve_from_labels(ann, idx)

A label may name several OSM buildings (a two-tower complex such as "Vizcayne"); every
combination is tried (capped at ``MAX_COMBOS``) and the lowest-RMS pose is kept, which also
says which tower the label meant.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from .camera_solver import Obs, Pose, solve_pose

MAX_COMBOS = 64


def load_annotations(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def building_index(features: list[dict]) -> dict[str, list[tuple[float, float]]]:
    """Lower-case OSM ``name`` -> footprint centroids (lat, lon) of buildings carrying it."""
    from shapely.geometry import shape

    out: dict[str, list[tuple[float, float]]] = {}
    for f in features:
        name = ((f.get("properties") or {}).get("name") or "").strip().lower()
        if not name or not f.get("geometry"):
            continue
        try:
            c = shape(f["geometry"]).centroid
        except Exception:  # noqa: BLE001 - a broken geometry is just not indexed
            continue
        out.setdefault(name, []).append((c.y, c.x))
    return out


def solve_from_labels(ann: dict, index: dict[str, list[tuple[float, float]]],
                      **solver_kw) -> tuple[Pose, dict[str, str]]:
    """Pose from an annotation file; returns it and label -> the OSM name it was matched to."""
    options = []  # per usable label: [(osm_name, lat, lon), ...]
    for lab in ann["labels"]:
        cands = [(n, *ll) for n in lab.get("osm_names", []) for ll in index.get(n.lower(), [])]
        if cands:
            options.append((lab, cands))
    width = int(ann["image_px"][0])
    projection = ann.get("projection", "pinhole")
    best = None
    for combo in itertools.islice(itertools.product(*[c for _, c in options]), MAX_COMBOS):
        obs = [Obs(float(lab["x"]), lat, lon, lab["label"])
               for (lab, _), (_n, lat, lon) in zip(options, combo, strict=True)]
        pose = solve_pose(obs, width, projection, **solver_kw)
        if best is None or pose.rms_px < best[0].rms_px:
            best = (pose, {lab["label"]: c[0] for (lab, _), c in zip(options, combo, strict=True)})
    if best is None:
        raise ValueError("no label matches an OSM building")
    return best
