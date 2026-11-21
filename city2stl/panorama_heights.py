"""Panorama and photo heights for the OSM buildings of a city payload (F-UNIFY Phase 5).

The skyline pipeline publishes one verification tier per building in a region report's
``heights.json`` (``city2stl/skyline/_core/tiers.py``); the Photo solve page keeps the heights
read from one photo in ``<annotations>/<region>_photo_heights.json``
(``app/server/core/photo_solve.py::save_heights``). This module merges the trustworthy rows into
a building layer:

    find_reports(bbox)                               -> the reports that cover the bbox
    photo_rows(bbox)                                 -> the photo-solve readings inside it
    apply_heights(buildings_fc, bbox, mode)          -> (new FeatureCollection, summary)
    apply_panorama_heights(buildings_fc, rows, ...)  -> (new FeatureCollection, summary)
    strip_applied(buildings_fc)                      -> (the collection without them, count)
    load_rows(path_or_name)                          -> one report's building rows

``app/server/core/city_data.py::with_panorama_heights`` calls :func:`apply_heights` on every
read of a cached city payload (preview, city overlay, export), so the preview and the export
show the same heights.

Which rows: ``corroborated`` (two independent readings agree) and ``single`` (one kind of
image reading that the pipeline's own withhold rules let through), and the photo-solve readings
(tier ``photo``: a human paired the photo with the map and chose "Use these heights").
``tag`` rows already carry the OSM tag and ``prior`` rows are the height model's guess, so
neither overrides anything; ``survey`` rows are lidar and are not touched by the exporter.

Which features: a row replaces a building's height unless that height is already a
measurement or a human's statement (OSM tag or levels, landmark, survey / lidar, or a height
the user typed in the Buildings panel: ``user_override``). It replaces the 10 m fallback and
the raster estimates (GBA, Open Buildings 2.5D, ...), which measured the block or the 3 m grid,
not the roof.

Report rows find their feature by footprint overlap (intersection over union >= ``MIN_IOU``):
a report may have been built from an earlier fetch of the same area, so ids and vertices need
not match. Photo readings are points (the paired map point on the building) and take the
smallest footprint that contains them. Each feature takes the best row: a photo reading first,
then the newest report, then the best overlap. The replaced value and source stay on the
feature as ``height_prev_m`` / ``height_prev_source``; the new source is ``panorama_<tier>`` or
``photo_solve``, with ``height_tier`` and ``height_report`` (which report or photo store).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import statistics
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry import Point, Polygon, box
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)

PANORAMA_TIERS = ("corroborated", "single")
#: Tier of a photo-solve reading (``photo_solve.save_heights``); its source is ``photo_solve``.
PHOTO_TIER = "photo"
PHOTO_SOURCE = "photo_solve"
#: Every tier :func:`apply_panorama_heights` takes by default.
APPLIED_TIERS = PANORAMA_TIERS + (PHOTO_TIER,)
#: Sources this module writes; :func:`strip_applied` undoes exactly these.
APPLIED_PREFIXES = ("panorama_", "photo_")
#: Smallest footprint overlap (intersection over union) that pairs a row with a feature.
MIN_IOU = 0.5
#: Height sources that are a measurement or a stated value: a panorama row never replaces them.
#: ``user_override`` is the Buildings panel's edit (``building-heights.js``); the export merges it
#: in before the panorama rows are applied (``city_model_task.py``), so it must be kept here.
KEEP_SOURCES = ("osm_tag", "osm_levels", "landmark", "survey", "lidar", "user_override")
#: ``<root>/<report>/heights.json``; the env vars move the root (the reports are not in git).
#: ``MAP2STL_REGION_REPORTS_DIR`` is where the app's Heights page writes and lists reports
#: (``app/server/routers/reports.py``), so a report made there is found here too.
_ROOT_ENVS = ("MAP2STL_SKYLINE_REPORTS", "MAP2STL_REGION_REPORTS_DIR")
_PHOTO_ENV = "MAP2STL_PHOTO_ANNOTATIONS_DIR"
_REPORT_SUFFIX = "_skyline_report"
#: Mode words; anything else is a report name (or a list of them).
_OFF_WORDS = {"off", "false", "0", "no", "none"}
_AUTO_WORDS = {"", "auto", "on", "true", "1", "yes"}


def reports_root() -> Path:
    for var in _ROOT_ENVS:
        env = os.environ.get(var)
        if env:
            return Path(env).expanduser()
    return Path(__file__).resolve().parent / "skyline" / "runs" / "region_reports"


def photo_root() -> Path:
    """Where the Photo solve page keeps ``<region>_photo_heights.json``
    (``city2stl.photo_label.regions.ANNOTATIONS``, moved by the same env var)."""
    env = os.environ.get(_PHOTO_ENV)
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent / "skyline" / "sites" / "annotations"


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


# ------------------------------------------------------------------ reading the reports

_ROWS_MEMO: dict[str, tuple[tuple, list[dict]]] = {}
_ROWS_MEMO_SIZE = 4
_HEAD_MEMO: dict[str, tuple[tuple, dict]] = {}


def _stamp(path: Path) -> tuple:
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


def load_rows(spec: str | os.PathLike) -> list[dict]:
    """The building rows of a region report's ``heights.json`` (see :func:`resolve_report`).
    Parsed once per file version (mtime, size); the list returned is shared, do not modify it."""
    path = resolve_report(spec)
    key, stamp = str(path), _stamp(path)
    hit = _ROWS_MEMO.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("buildings") if isinstance(doc, dict) else None
    rows = [r for r in rows or [] if isinstance(r, dict)]
    _ROWS_MEMO[key] = (stamp, rows)
    while len(_ROWS_MEMO) > _ROWS_MEMO_SIZE:
        _ROWS_MEMO.pop(next(iter(_ROWS_MEMO)))
    return rows


_HEAD_BYTES = 8192
_BBOX_RE = re.compile(r'"bbox_nsew"\s*:\s*\[([^\]]*)\]')
_REGION_RE = re.compile(r'"region"\s*:\s*"((?:[^"\\]|\\.)*)"')
_COUNTS_RE = re.compile(r'"tier_counts"\s*:\s*(\{[^{}]*\})')


def _head(heights: Path) -> dict:
    """``region``, ``bbox_nsew`` and ``tier_counts`` of a ``heights.json``, from its first 8 kB
    when they are there (the writer puts them before ``buildings``), else from the whole file.
    A 30 MB survey report is not parsed just to learn that it is in another city."""
    key, stamp = str(heights), _stamp(heights)
    hit = _HEAD_MEMO.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    with heights.open("r", encoding="utf-8", errors="replace") as fh:
        text = fh.read(_HEAD_BYTES)
    out: dict[str, Any] = {}
    m = _BBOX_RE.search(text)
    if m:
        try:
            out["bbox_nsew"] = [float(v) for v in m.group(1).split(",")]
        except ValueError:
            pass
    m = _REGION_RE.search(text)
    if m:
        out["region"] = json.loads(f'"{m.group(1)}"')
    m = _COUNTS_RE.search(text)
    if m:
        try:
            out["tier_counts"] = json.loads(m.group(1))
        except ValueError:
            pass
    if "bbox_nsew" not in out:
        doc = json.loads(heights.read_text(encoding="utf-8"))
        doc = doc if isinstance(doc, dict) else {}
        out = {k: doc.get(k) for k in ("region", "bbox_nsew", "tier_counts") if doc.get(k)}
    _HEAD_MEMO[key] = (stamp, out)
    return out


def _sidecar(report_dir: Path) -> dict | None:
    """The report's ``report_summary.json`` (``city2stl.skyline.report_index.read_sidecar``):
    None when absent, of another schema, or older than ``heights.json``."""
    try:
        from city2stl.skyline import report_index  # noqa: PLC0415 - light, but keep it lazy
    except Exception:  # noqa: BLE001
        return None
    return report_index.read_sidecar(report_dir)


def _bbox4(bbox) -> tuple[float, float, float, float]:
    """``(north, south, east, west)`` from a tuple / list or a dict with those keys."""
    if isinstance(bbox, dict):
        return (float(bbox["north"]), float(bbox["south"]), float(bbox["east"]),
                float(bbox["west"]))
    n, s, e, w = (float(v) for v in bbox)
    return n, s, e, w


def _bbox_poly(nsew) -> Polygon:
    n, s, e, w = (float(v) for v in nsew)
    return box(min(w, e), min(s, n), max(w, e), max(s, n))


def _region_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


def _report_name(report_dir: Path) -> str:
    name = report_dir.name
    return name[: -len(_REPORT_SUFFIX)] if name.endswith(_REPORT_SUFFIX) else name


def _usable_count(counts: dict | None) -> int | None:
    if not isinstance(counts, dict):
        return None
    return sum(int(counts.get(t) or 0) for t in PANORAMA_TIERS)


def _describe(heights: Path, bbox_poly: Polygon | None) -> dict | None:
    """One report as :func:`find_reports` lists it, or None when it cannot cover ``bbox_poly``
    or has no corroborated / single row there."""
    report_dir = heights.parent
    side = _sidecar(report_dir) or {}
    head = {} if side.get("bbox_nsew") else _head(heights)
    nsew = side.get("bbox_nsew") or head.get("bbox_nsew")
    if bbox_poly is not None and nsew and len(nsew) == 4 \
            and not _bbox_poly(nsew).intersects(bbox_poly):
        return None
    counts = side.get("tier_counts") or head.get("tier_counts")
    if _usable_count(counts) == 0:
        return None
    tiers: dict[str, int] = {}
    inside = 0
    for r in load_rows(heights):
        if _usable(r, PANORAMA_TIERS) is None:
            continue
        tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
        poly = _row_polygon(r)
        if poly is not None and (bbox_poly is None or poly.intersects(bbox_poly)):
            inside += 1
    if not inside:
        return None
    return {"name": _report_name(report_dir), "dir": report_dir.name,
            "region": side.get("region") or head.get("region") or _report_name(report_dir),
            "bbox_nsew": nsew, "generated_at": side.get("generated_at"),
            "mtime": heights.stat().st_mtime, "rows": tiers, "rows_in_bbox": inside,
            "sidecar": bool(side), "heights": str(heights)}


def find_reports(bbox=None, root: Path | None = None, *, include_superseded: bool = False
                 ) -> list[dict]:
    """The skyline reports under ``root`` (:func:`reports_root`) that can give ``bbox`` a height.

    A report qualifies when its corroborated / single rows' footprints intersect the bbox. The
    bbox test reads the ``report_summary.json`` sidecar's ``bbox_nsew`` and ``tier_counts`` when
    the sidecar is current, else the head of ``heights.json``; only a report whose box
    intersects is parsed in full. ``bbox`` None lists every report with usable rows.

    Several runs of one region (``region`` in the sidecar / ``heights.json``) are versions of
    the same answer, so only the newest (``heights.json`` mtime) is used; the older ones come
    back with ``superseded_by`` only when ``include_superseded``. Reports of different regions
    are all used, newest first. Each entry: ``name`` (accepted by :func:`resolve_report`),
    ``dir``, ``region``, ``bbox_nsew``, ``generated_at``, ``mtime``, ``rows`` (usable rows per
    tier), ``rows_in_bbox``, ``sidecar``, ``heights`` (path) and ``superseded_by`` (None).
    """
    root = Path(root) if root is not None else reports_root()
    bbox_poly = _bbox_poly(_bbox4(bbox)) if bbox is not None else None
    found = []
    try:
        candidates = sorted(root.glob("*/heights.json"))
    except OSError:
        return []
    for heights in candidates:
        try:
            entry = _describe(heights, bbox_poly)
        except (OSError, ValueError) as exc:
            logger.warning("Skyline report %s unreadable: %s", heights.parent.name, exc)
            continue
        if entry is not None:
            found.append(entry)
    found.sort(key=lambda r: r["mtime"], reverse=True)
    newest: dict[str, str] = {}
    out = []
    for entry in found:
        key = _region_key(entry["region"])
        entry["superseded_by"] = newest.get(key)
        if entry["superseded_by"] is None:
            newest[key] = entry["name"]
            out.append(entry)
        elif include_superseded:
            out.append(entry)
    return out


def photo_rows(bbox=None, root: Path | None = None) -> list[dict]:
    """The photo-solve readings (``<root>/*_photo_heights.json``) whose point is in ``bbox``.

    One row per reading: ``tier`` ``photo``, ``effective_height_m``, ``point_lonlat``,
    ``building`` (the name the page showed), ``photo`` (which photo) and ``store`` (the file's
    region). :func:`apply_panorama_heights` combines the readings that land on one footprint by
    their median."""
    root = Path(root) if root is not None else photo_root()
    bbox_poly = _bbox_poly(_bbox4(bbox)) if bbox is not None else None
    out = []
    try:
        files = sorted(root.glob("*_photo_heights.json"))
    except OSError:
        return []
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Photo heights %s unreadable: %s", path.name, exc)
            continue
        store = path.name[: -len("_photo_heights.json")]
        for rel, entry in ((doc or {}).get("photos") or {}).items():
            for h in (entry or {}).get("heights") or []:
                try:
                    lat, lon, m = float(h["lat"]), float(h["lon"]), float(h["height_m"])
                except (KeyError, TypeError, ValueError):
                    continue
                if bbox_poly is not None and not bbox_poly.covers(Point(lon, lat)):
                    continue
                out.append({"tier": PHOTO_TIER, "effective_height_m": m,
                            "point_lonlat": [lon, lat], "building": h.get("building"),
                            "photo": rel, "store": store})
    return out


# ------------------------------------------------------------------ applying them

def heights_mode(value) -> str | list[str]:
    """The request's ``panorama_heights`` setting as ``"auto"``, ``"off"`` or a list of report
    names. Unset / True / "auto" / "on" is auto (the newest report of each region that covers
    the bbox, plus the photo readings); False / "off" is off; any other string, or a list, names
    the reports to use (no photo readings, no discovery)."""
    if value is None or value is True:
        return "auto"
    if value is False:
        return "off"
    if isinstance(value, (list, tuple)):
        names = [str(v) for v in value if str(v).strip()]
        return names or "off"
    word = str(value).strip()
    if word.lower() in _OFF_WORDS:
        return "off"
    if word.lower() in _AUTO_WORDS:
        return "auto"
    return [word]


def signature(bbox, mode="auto", *, reports_dir: Path | None = None,
              photos_dir: Path | None = None) -> str:
    """A short cache-key part for what :func:`apply_heights` would apply to ``bbox``: "" when
    nothing would (off, or no report / reading covers it), so keys of caches built before
    Phase 5 stay valid; else ``_ph`` + a hash of each source's name and mtime, so a new report
    run or photo reading invalidates the cached result."""
    mode = heights_mode(mode)
    if mode == "off":
        return ""
    parts = []
    try:
        if mode == "auto":
            parts += [f"{r['name']}@{r['mtime']}" for r in find_reports(bbox, reports_dir)]
            root = Path(photos_dir) if photos_dir is not None else photo_root()
            if photo_rows(bbox, root):
                parts += [f"{p.name}@{p.stat().st_mtime_ns}"
                          for p in sorted(root.glob("*_photo_heights.json"))]
        else:
            for name in mode:
                try:
                    path = resolve_report(Path(reports_dir) / name if reports_dir else name)
                except FileNotFoundError:
                    parts.append(f"{name}@missing")
                    continue
                parts.append(f"{name}@{path.stat().st_mtime_ns}")
    except OSError:
        return ""
    if not parts:
        return ""
    return "_ph" + hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


def strip_applied(buildings: dict) -> tuple[dict, int]:
    """``buildings`` with every height this module wrote put back (``height_prev_*``), and how
    many. A client copy of a preview payload carries them; the export strips and reapplies so a
    report that changed, or a switch turned off, is not baked in from the old copy."""
    feats = list((buildings or {}).get("features") or [])
    n = 0
    for i, f in enumerate(feats):
        props = (f or {}).get("properties") or {}
        src = str(props.get("height_source") or "")
        if not src.startswith(APPLIED_PREFIXES) or "height_prev_source" not in props:
            continue
        props = dict(props)
        prev_m, prev_src = props.pop("height_prev_m", None), props.pop("height_prev_source", None)
        for k in ("height_tier", "height_report", "height_m", "height_source"):
            props.pop(k, None)
        if prev_m is not None:
            props["height_m"] = prev_m
        if prev_src is not None:
            props["height_source"] = prev_src
        feats[i] = {**f, "properties": props}
        n += 1
    if not n:
        return buildings, 0
    return {**buildings, "features": feats}, n


def apply_heights(buildings: dict, bbox, mode="auto", *, reports_dir: Path | None = None,
                  photos_dir: Path | None = None) -> tuple[dict, dict]:
    """``buildings`` with the panorama / photo heights for ``bbox`` applied, and the summary.

    ``mode`` is :func:`heights_mode`'s. The heights this module wrote before are stripped first
    (:func:`strip_applied`), so the call is idempotent and "off" undoes them. The summary is
    :func:`apply_panorama_heights`'s plus ``mode`` (auto / off / named), ``buildings`` (features
    changed), ``reports`` (name, region, generated_at, rows, rows_in_bbox, applied, per report
    used; a report that changed nothing is listed with ``applied`` 0), ``photo`` (readings,
    applied) and ``error`` (a named report that does not exist, or discovery failing; the
    other rows still apply). Never raises on a bad report.
    """
    mode = heights_mode(mode)
    original = buildings
    buildings, stripped = strip_applied(buildings)
    summary: dict[str, Any] = {
        "mode": mode if isinstance(mode, str) else "named",
        "buildings": 0, "reports": [], "photo": {"readings": 0, "applied": 0}}
    if mode == "off":
        return (buildings if stripped else original), summary
    rows: list[dict] = []
    reports: list[dict] = []
    errors = []
    if mode == "auto":
        try:
            reports = find_reports(bbox, reports_dir)
        except Exception as exc:  # noqa: BLE001 - discovery must not fail a city read
            errors.append(f"report discovery: {exc}")
        try:
            prow = photo_rows(bbox, photos_dir)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"photo heights: {exc}")
            prow = []
        rows += [{**r, "_rank": 0, "_report": f"photo:{r['store']}"} for r in prow]
        summary["photo"]["readings"] = len(prow)
    else:
        for name in mode:
            try:
                path = resolve_report(Path(reports_dir) / name if reports_dir else name)
            except FileNotFoundError as exc:
                errors.append(str(exc))
                continue
            reports.append({"name": _report_name(path.parent), "region": _head(path).get("region"),
                            "generated_at": None, "heights": str(path)})
    for rank, rep in enumerate(reports, start=1):
        try:
            rep_rows = load_rows(rep["heights"])
        except (OSError, ValueError) as exc:
            errors.append(f"{rep['name']}: {exc}")
            continue
        rows += [{**r, "_rank": rank, "_report": rep["name"]} for r in rep_rows]
    out, applied = apply_panorama_heights(buildings, rows)
    summary.update(applied)
    by_report = applied["by_report"]
    summary["reports"] = [
        {**{k: rep.get(k) for k in ("name", "region", "generated_at", "rows", "rows_in_bbox")},
         "applied": by_report.get(rep["name"], 0)} for rep in reports]
    summary["photo"]["applied"] = sum(v for k, v in by_report.items() if k.startswith("photo:"))
    summary["buildings"] = sum(by_report.values())
    if errors:
        summary["error"] = "; ".join(errors)
    if not summary["buildings"] and not stripped:
        out = original   # nothing applied or undone: keep the caller's object (no copy)
    return out, summary


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
                           tiers: tuple[str, ...] = APPLIED_TIERS,
                           min_iou: float = MIN_IOU) -> tuple[dict, dict]:
    """``buildings`` with the rows' heights applied, and what happened.

    ``buildings`` is not modified: a changed feature is a copy with new properties. A row is a
    report row (``footprint_lonlat``, matched by IoU >= ``min_iou``) or a photo reading
    (``point_lonlat``, matched by the smallest footprint that contains it; several readings on
    one footprint give their median). Optional ``_rank`` (lower wins, default 0) and ``_report``
    (counted in ``by_report``, written as ``height_report``) come from :func:`apply_heights`.

    The summary counts rows by tier (``rows``), features changed by tier (``applied``) and by
    report (``by_report``), and the rows not applied by reason (``skipped``: ``no_footprint``,
    ``no_match`` - no feature overlaps enough - ``kept_measured`` - the feature's height is
    a tag, landmark or survey - ``lost_to_better_overlap`` and ``lost_to_newer_report``).
    """
    feats = list((buildings or {}).get("features") or [])
    summary: dict[str, Any] = {"rows": {}, "applied": {}, "skipped": {}, "by_report": {},
                               "min_iou": min_iou, "tiers": list(tiers)}

    def skip(why: str) -> None:
        summary["skipped"][why] = summary["skipped"].get(why, 0) + 1

    geoms = shapely.from_geojson([json.dumps(f.get("geometry")) for f in feats], on_invalid="ignore") \
        if feats else []
    valid = [i for i, g in enumerate(geoms) if g is not None and not shapely.is_empty(g)]
    tree = STRtree([geoms[i] for i in valid]) if valid else None

    def fixed(i: int):
        g = geoms[i]
        return g if g.is_valid else g.buffer(0)

    best: dict[int, tuple[tuple, dict, float]] = {}   # feature -> ((rank, -iou), row, height)
    photo: dict[tuple[int, int], list[tuple[float, dict]]] = {}   # (feature, rank) -> readings

    def offer(i: int, key: tuple, row: dict, h: float) -> None:
        if _keeps((feats[i].get("properties") or {}).get("height_source")):
            skip("kept_measured")
            return
        old = best.get(i)
        if old is not None:
            skip("lost_to_newer_report" if old[0][0] != key[0] else "lost_to_better_overlap")
            if old[0] <= key:
                return
        best[i] = (key, row, h)

    for row in rows:
        h = _usable(row, tiers)
        if h is None:
            continue
        tier = row["tier"]
        rank = int(row.get("_rank", 0) or 0)
        summary["rows"][tier] = summary["rows"].get(tier, 0) + 1
        pt = row.get("point_lonlat")
        if pt is not None:
            try:
                point = Point(float(pt[0]), float(pt[1]))
            except (TypeError, ValueError, IndexError):
                skip("no_footprint")
                continue
            hits = [valid[int(k)] for k in tree.query(point)] if tree is not None else []
            hits = [i for i in hits if fixed(i).covers(point)]
            if not hits:
                skip("no_match")
                continue
            # the smallest footprint holding the point (a tower inside a podium outline)
            i = min(hits, key=lambda j: fixed(j).area)
            photo.setdefault((i, rank), []).append((h, row))
            continue
        poly = _row_polygon(row)
        if poly is None or tree is None:
            skip("no_footprint" if poly is None else "no_match")
            continue
        top_iou, top_i = 0.0, None
        for k in tree.query(poly):
            g = fixed(valid[int(k)])
            union = g.union(poly).area
            iou = g.intersection(poly).area / union if union > 0 else 0.0
            if iou > top_iou:
                top_iou, top_i = iou, valid[int(k)]
        if top_i is None or top_iou < min_iou:
            skip("no_match")
        else:
            offer(top_i, (rank, -top_iou), row, h)
    for (i, rank), readings in photo.items():
        h = float(statistics.median(v for v, _ in readings))
        offer(i, (rank, -1.0), readings[0][1], h)

    for i, (_, row, h) in best.items():
        props = dict(feats[i].get("properties") or {})
        props["height_prev_m"] = props.get("height_m")
        props["height_prev_source"] = props.get("height_source")
        props["height_m"] = round(h, 1)
        tier = row["tier"]
        props["height_source"] = PHOTO_SOURCE if tier == PHOTO_TIER else f"panorama_{tier}"
        props["height_tier"] = tier
        if row.get("_report"):
            props["height_report"] = row["_report"]
        feats[i] = {**feats[i], "properties": props}
        summary["applied"][tier] = summary["applied"].get(tier, 0) + 1
        rep = row.get("_report") or ""
        summary["by_report"][rep] = summary["by_report"].get(rep, 0) + 1
    return {**buildings, "features": feats}, summary
