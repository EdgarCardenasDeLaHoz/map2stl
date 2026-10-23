"""Panorama-measured heights for the OSM buildings of a city export.

The skyline pipeline publishes one verification tier per building in a region report's
``heights.json`` (``city2stl/skyline/_core/tiers.py``). Until now only the Reports viewer
read it; this module merges the trustworthy rows into the building layer an export builds:

    load_rows(path_or_name)                          -> the report's building rows
    apply_panorama_heights(buildings_fc, rows, ...)  -> (new FeatureCollection, summary)

Which rows: ``corroborated`` (two independent readings agree) and ``single`` (one kind of
image reading that the pipeline's own withhold rules let through). ``tag`` rows already
carry the OSM tag and ``prior`` rows are the height model's guess, so neither overrides
anything; ``survey`` rows are lidar and are not touched by the exporter either way.

Which features: a row replaces a building's height unless that height is already a
measurement or a human's statement (OSM tag or levels, landmark, survey / lidar, or a height
the user typed in the Buildings panel: ``user_override``). It
replaces the 10 m fallback and the raster estimates (GBA, Open Buildings 2.5D, ...), which
measured the block or the 3 m grid, not the roof.

Rows find their feature by footprint overlap (intersection over union >= ``MIN_IOU``): a
report may have been built from an earlier fetch of the same area, so ids and vertices need
not match. Each feature takes the row that overlaps it best. The replaced value and source
stay on the feature as ``height_prev_m`` / ``height_prev_source``; the new source is
``panorama_<tier>``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import Polygon
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)

PANORAMA_TIERS = ("corroborated", "single")
#: Smallest footprint overlap (intersection over union) that pairs a row with a feature.
MIN_IOU = 0.5
#: Height sources that are a measurement or a stated value: a panorama row never replaces them.
#: ``user_override`` is the Buildings panel's edit (``building-heights.js``); the export merges it
#: in before the panorama rows are applied (``city_model_task.py``), so it must be kept here.
KEEP_SOURCES = ("osm_tag", "osm_levels", "landmark", "survey", "lidar", "user_override")
#: ``<root>/<report>/heights.json``; the env var moves the root (the reports are not in git).
_ROOT_ENV = "MAP2STL_SKYLINE_REPORTS"
_REPORT_SUFFIX = "_skyline_report"


def reports_root() -> Path:
    env = os.environ.get(_ROOT_ENV)
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent / "skyline" / "runs" / "region_reports"


def resolve_report(spec: str | os.PathLike) -> Path:
    """``heights.json`` for ``spec``: that file, a report folder, or a report name
    (``Cartagena_v19_extras`` or ``..._skyline_report``) under :func:`reports_root`."""
    p = Path(spec)
    if p.is_file():
        return p
    if p.is_dir() and (p / "heights.json").is_file():
        return p / "heights.json"
    name = str(spec)
    if any(sep in name for sep in ("/", "\\")) or name in ("", ".", ".."):
        raise FileNotFoundError(f"No panorama heights.json at {spec}")
    root = reports_root()
    for cand in (root / name, root / (name + _REPORT_SUFFIX)):
        if (cand / "heights.json").is_file():
            return cand / "heights.json"
    raise FileNotFoundError(f"No panorama report named {name!r} under {root}")


def load_rows(spec: str | os.PathLike) -> list[dict]:
    """The building rows of a region report's ``heights.json`` (see :func:`resolve_report`)."""
    doc = json.loads(resolve_report(spec).read_text(encoding="utf-8"))
    rows = doc.get("buildings") if isinstance(doc, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def _keeps(source: Any) -> bool:
    s = str(source or "")
    return any(s == k or s.startswith(k) for k in KEEP_SOURCES)


def _row_polygon(row: dict) -> Polygon | None:
    ring = row.get("footprint_lonlat")
    if not ring or len(ring) < 4:
        return None
    try:
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = poly.buffer(0)
        return None if poly.is_empty else poly
    except Exception:  # noqa: BLE001 - a malformed ring is a row we cannot place
        return None


def _usable(row: dict, tiers: tuple[str, ...]) -> float | None:
    if row.get("tier") not in tiers:
        return None
    h = row.get("effective_height_m")
    try:
        h = float(h)
    except (TypeError, ValueError):
        return None
    return h if h > 0 else None


def apply_panorama_heights(buildings: dict, rows: list[dict],
                           tiers: tuple[str, ...] = PANORAMA_TIERS,
                           min_iou: float = MIN_IOU) -> tuple[dict, dict]:
    """``buildings`` with the panorama rows' heights applied, and what happened.

    ``buildings`` is not modified: a changed feature is a copy with new properties.
    The summary counts rows by tier (``rows``), features changed by tier (``applied``),
    and the rows not applied by reason (``skipped``: ``no_footprint``,
    ``no_match`` - no feature overlaps enough - ``kept_measured`` - the feature's height is
    a tag, landmark or survey - and ``lost_to_better_overlap``).
    """
    feats = list((buildings or {}).get("features") or [])
    summary: dict[str, Any] = {"rows": {}, "applied": {}, "skipped": {}, "min_iou": min_iou,
                               "tiers": list(tiers)}

    def skip(why: str) -> None:
        summary["skipped"][why] = summary["skipped"].get(why, 0) + 1

    geoms = shapely.from_geojson([json.dumps(f.get("geometry")) for f in feats], on_invalid="ignore") \
        if feats else []
    valid = [i for i, g in enumerate(geoms) if g is not None and not shapely.is_empty(g)]
    tree = STRtree([geoms[i] for i in valid]) if valid else None

    best: dict[int, tuple[float, dict, float]] = {}   # feature index -> (iou, row, height)
    for row in rows:
        h = _usable(row, tiers)
        if h is None:
            continue
        tier = row["tier"]
        summary["rows"][tier] = summary["rows"].get(tier, 0) + 1
        poly = _row_polygon(row)
        if poly is None or tree is None:
            skip("no_footprint" if poly is None else "no_match")
            continue
        top_iou, top_i = 0.0, None
        for k in tree.query(poly):
            g = geoms[valid[int(k)]]
            if not g.is_valid:
                g = g.buffer(0)
            union = g.union(poly).area
            iou = g.intersection(poly).area / union if union > 0 else 0.0
            if iou > top_iou:
                top_iou, top_i = iou, valid[int(k)]
        if top_i is None or top_iou < min_iou:
            skip("no_match")
        elif _keeps((feats[top_i].get("properties") or {}).get("height_source")):
            skip("kept_measured")
        elif top_i in best and best[top_i][0] >= top_iou:
            skip("lost_to_better_overlap")
        else:
            if top_i in best:
                skip("lost_to_better_overlap")
            best[top_i] = (top_iou, row, h)

    for i, (_, row, h) in best.items():
        props = dict(feats[i].get("properties") or {})
        props["height_prev_m"] = props.get("height_m")
        props["height_prev_source"] = props.get("height_source")
        props["height_m"] = round(h, 1)
        props["height_source"] = f"panorama_{row['tier']}"
        feats[i] = {**feats[i], "properties": props}
        summary["applied"][row["tier"]] = summary["applied"].get(row["tier"], 0) + 1
    return {**buildings, "features": feats}, summary
