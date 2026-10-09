"""reports.py — browse the skyline pipeline's rendered artifacts.

Serves three things under ``/reports``:

* ``GET /reports`` — a single navigation page (Jinja2 template ``reports.html``).
* ``GET /api/reports/index`` — an inventory of every region report, height report and height
  trace currently on disk, built by scanning the directories rather than by reading a
  pre-generated file. ``scripts/build_landing_page.py`` writes a static ``index.html`` that has to
  be re-run after every batch, and it was three regions stale when this router was written; the
  page served here cannot go stale for the same reason.
* ``GET /api/reports/registration`` — plate-registration reports (numpy2stl HTML reports under
  ``Code/_reports/``, ``Code/_reports_regen/``, ``Cities/micropolitan/reports/`` and the web app's
  per-import mesh auto-register folders) plus a summary of every align-tool pack
  (``tools/align_tool/data/<slug>/meta.json``). Roots are configurable (``_REG_ROOTS``).
* ``GET /reports/files/{root}/{path}`` — the artifact files themselves (HTML pages, PNGs, PDFs,
  JSON sidecars), rooted at a small fixed set of directories with a traversal guard.

Everything here is read-only: nothing under any root is written, moved or deleted.

The per-seed statistics are parsed back out of each region's rendered ``index.html`` by
``city2stl.skyline.report_index``, which ``build_landing_page`` uses too. Those numbers are not
written to a sidecar anywhere, so the rendered page is the only machine-readable copy, and one
parser keeps one definition of the row format instead of two that drift apart.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from city2stl.skyline.report_index import parse_pano_rows, seed_source, stat
from city2stl.skyline.tier_display import (
    TIER_COLORS,
    TIER_HINTS,
    TIER_LABELS,
    VERIFIED_TIERS,
    cadastre_attributions,
    survey_attributions,
    survey_providers,
    tier_counts,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])

#: map2stl/ — routers -> server -> app -> map2stl
_MAP2STL = Path(__file__).resolve().parents[3]
_SKYLINE_RUNS = _MAP2STL / "city2stl" / "skyline" / "runs"

#: Only these directories are reachable through ``/reports/files``. Keys appear in URLs.
_ROOTS: dict[str, Path] = {
    "region": _SKYLINE_RUNS / "region_reports",
    "height": _MAP2STL / "output" / "height_reports",
    "trace": _SKYLINE_RUNS / "height_traces",
}

_REPORT_DIR_SUFFIX = "_skyline_report"

#: Code/ — the workspace holding map2stl/ and numpy2stl/; Cities/ is beside it.
_WORKSPACE = _MAP2STL.parent


def _default_registration_roots() -> dict[str, Path]:
    """Registration report roots, keyed by the name used in ``/reports/files/<key>/``.

    ``MAP2STL_REGISTRATION_REPORT_ROOTS`` overrides the whole set as ``key=path;key=path``
    (relative paths resolve against ``Code/``).  A root that does not exist is simply
    skipped by the scan.
    """
    env = os.environ.get("MAP2STL_REGISTRATION_REPORT_ROOTS") or os.environ.get("STRM2STL_REGISTRATION_REPORT_ROOTS")
    if env:
        out = {}
        for part in env.split(";"):
            key, _, raw = part.partition("=")
            if key.strip() and raw.strip():
                path = Path(raw.strip())
                out[key.strip()] = path if path.is_absolute() else _WORKSPACE / path
        return out
    from app.server.core.cache import CACHE_ROOT
    return {
        "registration": _WORKSPACE / "_reports",               # numpy2stl REPORTS_ROOT
        "registration_regen": _WORKSPACE / "_reports_regen",
        "micropolitan": _WORKSPACE.parent / "Cities" / "micropolitan" / "reports",
        "mesh_import": CACHE_ROOT / "mesh_imports" / "reports",  # web app auto-register
        "align": _MAP2STL / "tools" / "align_tool" / "data",    # plate packs (meta.json)
    }


#: Registration roots, separate from ``_ROOTS`` so the skyline inventory is unaffected.
#: The ``align`` key is a pack directory (one folder per plate, each with meta.json);
#: every other key holds HTML report folders.
_REG_ROOTS: dict[str, Path] = _default_registration_roots()
_ALIGN_KEY = "align"

#: Extensions the browser can render. Anything else is refused rather than offered as a download,
#: because these roots also hold caches that are of no use to a reader.
_SERVABLE = {".html", ".htm", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf", ".json", ".css"}


# --- parsers ------------------------------------------------------------------

def _seed_slug(seed_name: str) -> str:
    """Asset filenames drop a leading ``seed_``; ``html_report.py:1256`` does the same."""
    return seed_name[5:] if seed_name.startswith("seed_") else seed_name


_PANO_KINDS = ("pano", "pano_seg", "pano_depth", "pano_recon", "pano_scan")


def _pano_assets(report_dir: Path, slug: str, url_base: str) -> dict[str, str]:
    """URLs for whichever panorama renders this seed actually produced."""
    out = {}
    for kind in _PANO_KINDS:
        if (report_dir / "assets" / "pano" / f"{slug}_{kind}.png").is_file():
            out[kind] = f"{url_base}/assets/pano/{slug}_{kind}.png"
    return out


#: Overhead context renders. ``fp`` / ``sat`` / ``heights`` are the polar views drawn around the
#: seed point; all four are roughly square, unlike the 5:1 panorama strips.
_MINIMAP_KINDS = (("minimap", ""), ("polar footprints", "_polar_fp"),
                  ("polar satellite", "_polar_sat"), ("polar heights", "_polar_heights"))


def _minimap_assets(report_dir: Path, slug: str, url_base: str) -> list[dict]:
    out = []
    for label, suffix in _MINIMAP_KINDS:
        name = f"{slug}{suffix}.png"
        if (report_dir / "assets" / "minimap" / name).is_file():
            out.append({"label": label, "url": f"{url_base}/assets/minimap/{name}"})
    return out


def _view_assets(report_dir: Path, slug: str, url_base: str) -> list[dict]:
    """Per-view Street View frames, in view order, with whatever renders exist for each."""
    views_dir = report_dir / "assets" / "views"
    if not views_dir.is_dir():
        return []
    pat = re.compile(rf"^{re.escape(slug)}_view_(\d+)\.png$")
    out = []
    for path in sorted(views_dir.glob(f"{slug}_view_*.png")):
        m = pat.match(path.name)
        if not m:
            continue
        idx = int(m.group(1))
        entry = {"index": idx, "image": f"{url_base}/assets/views/{path.name}"}
        for kind in ("mask", "depth", "recon"):
            if (views_dir / f"{slug}_view_{idx}_{kind}.png").is_file():
                entry[kind] = f"{url_base}/assets/views/{slug}_view_{idx}_{kind}.png"
        out.append(entry)
    return sorted(out, key=lambda e: e["index"])


def _region_rows(report_dir: Path, region: str, text: str) -> list[dict]:
    """Per-seed rows for one region, each carrying its own artifact URLs."""
    url_base = f"/reports/files/region/{report_dir.name}"
    rows = []
    for r in parse_pano_rows(text):
        slug = _seed_slug(r["seed"])
        rows.append({
            "region": region,
            "seed": r["seed"],
            "slug": slug,
            "url": f"{url_base}/{r['url']}",
            "source": seed_source(r["seed"], region),
            "detected": r["detected"],
            "matched": r["matched"],
            "match_rate": r["match_rate"],
            "coverage": r["coverage"],
            "quality": r["quality"],
            "quality_label": r["quality_label"],
            "minimaps": _minimap_assets(report_dir, slug, url_base),
            "pano": _pano_assets(report_dir, slug, url_base),
            "views": _view_assets(report_dir, slug, url_base),
        })
    return rows


# --- inventory ----------------------------------------------------------------

def _mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime,
                                      tz=UTC).isoformat(timespec="seconds")
    except OSError:
        return None


def _count(dir_path: Path, pattern: str) -> int:
    return len(list(dir_path.glob(pattern))) if dir_path.is_dir() else 0


def _region_entry(report_dir: Path) -> dict:
    region = report_dir.name[:-len(_REPORT_DIR_SUFFIX)]
    url_base = f"/reports/files/region/{report_dir.name}"
    index_path = report_dir / "index.html"

    entry = {
        "name": region,
        "dir": report_dir.name,
        "index_url": f"{url_base}/index.html" if index_path.is_file() else None,
        "modified": _mtime(index_path if index_path.is_file() else report_dir),
        "seed_pages": _count(report_dir, "seed_*.html"),
        "pano_images": _count(report_dir / "assets" / "pano", "*.png"),
        "view_images": _count(report_dir / "assets" / "views", "*.png"),
        "minimap_images": _count(report_dir / "assets" / "minimap", "*.png"),
        "screening_map": None,
        "web_images": [],
        "heights_json": None,
        "buildings": 0,
        "seeds": 0,
        "rows": [],
        "quality": {"good": 0, "medium": 0, "weak": 0},
    }

    if (report_dir / "assets" / "screening_map.png").is_file():
        entry["screening_map"] = f"{url_base}/assets/screening_map.png"
    if (report_dir / "heights.json").is_file():
        entry["heights_json"] = f"{url_base}/heights.json"

    web_dir = report_dir / "web_images"
    if web_dir.is_dir():
        entry["web_images"] = [f"{url_base}/web_images/{p.name}"
                               for p in sorted(web_dir.iterdir())
                               if p.suffix.lower() in (".jpg", ".jpeg", ".png")]

    if index_path.is_file():
        try:
            text = index_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("Cannot read %s: %s", index_path, e)
            return entry
        entry["seeds"] = stat(text, "seeds")
        entry["buildings"] = stat(text, "aggregated buildings")
        entry["rows"] = _region_rows(report_dir, region, text)
        for row in entry["rows"]:
            entry["quality"][row["quality"]] = entry["quality"].get(row["quality"], 0) + 1

    return entry


def _build_inventory() -> dict:
    regions_root = _ROOTS["region"]
    regions, pdfs = [], []
    if regions_root.is_dir():
        for path in sorted(regions_root.iterdir(), key=lambda p: p.name.lower()):
            if path.is_dir() and path.name.endswith(_REPORT_DIR_SUFFIX):
                regions.append(_region_entry(path))
            elif path.suffix.lower() == ".pdf":
                pdfs.append({
                    "name": path.stem,
                    "url": f"/reports/files/region/{path.name}",
                    "modified": _mtime(path),
                    "size": path.stat().st_size,
                })

    heights_root = _ROOTS["height"]
    height_reports = []
    if heights_root.is_dir():
        for path in sorted(heights_root.glob("*.html")):
            height_reports.append({
                "name": path.stem.replace("_height_report", ""),
                "url": f"/reports/files/height/{path.name}",
                "modified": _mtime(path),
                "size": path.stat().st_size,
            })

    traces_root = _ROOTS["trace"]
    traces = []
    if traces_root.is_dir():
        for path in sorted(traces_root.glob("*.json")):
            region, _, feature = path.stem.partition("__")
            plot = path.with_suffix(".png")
            traces.append({
                "name": path.stem,
                "region": region,
                "feature": feature or "all",
                "url": f"/reports/files/trace/{path.name}",
                "plot": f"/reports/files/trace/{plot.name}" if plot.is_file() else None,
                "modified": _mtime(path),
            })

    landing = regions_root / "index.html"
    totals = {
        "regions": len(regions),
        "seeds": sum(len(r["rows"]) for r in regions),
        "pano_images": sum(r["pano_images"] for r in regions),
        "view_images": sum(r["view_images"] for r in regions),
        "buildings": sum(r["buildings"] for r in regions),
        "good": sum(r["quality"]["good"] for r in regions),
        "medium": sum(r["quality"]["medium"] for r in regions),
        "weak": sum(r["quality"]["weak"] for r in regions),
    }

    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "totals": totals,
        "regions": regions,
        "height_reports": height_reports,
        "traces": traces,
        "pdfs": pdfs,
        "legacy_landing_url": ("/reports/files/region/index.html"
                               if landing.is_file() else None),
    }


@router.get("/api/reports/index")
async def reports_index():
    """Everything the browser page needs, in one request."""
    try:
        return JSONResponse(_build_inventory())
    except Exception as e:
        logger.error("Failed to build report inventory: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Report inventory failed: {e}") from e


@router.get("/api/reports/heights/{region_dir}")
async def reports_heights(region_dir: str):
    """Summary of one region's ``heights.json`` — the per-building metrics are large.

    Returns the header fields plus per-building height sources counted, not the building list
    itself; the raw file stays available through ``/reports/files``.

    Verification tiers (F-SKY26 2f): ``tier_counts`` (the file's own, else counted; schema 1
    files count as ``unlabelled``), ``n_verified``, ``n_withheld`` (single readings replaced by
    the prior), ``tiers`` (label, colour and hint per tier, so the page draws the same legend
    as the reports) and ``survey`` ({provider: rows} plus the licence ``attributions`` the
    page must show when survey heights are published).
    """
    path = _safe_path("region", f"{region_dir}/heights.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Cannot read heights.json: {e}") from e

    buildings = data.get("buildings") or []
    sources: dict[str, int] = {}
    heights = []
    for b in buildings:
        src = b.get("effective_height_source") or "unknown"
        sources[src] = sources.get(src, 0) + 1
        h = b.get("effective_height_m")
        if isinstance(h, (int, float)):
            heights.append(float(h))
    heights.sort()
    counts = tier_counts(buildings, data.get("tier_counts"))
    providers = survey_providers(buildings)

    def _pct(frac: float) -> float | None:
        if not heights:
            return None
        return round(heights[min(len(heights) - 1, int(frac * len(heights)))], 1)

    return JSONResponse({
        "region": data.get("region"),
        "bbox_nsew": data.get("bbox_nsew"),
        "n_building_records": data.get("n_building_records"),
        "known_heights": data.get("known_heights"),
        "n_known_heights": len(data.get("known_heights") or []),
        "schema_version": data.get("schema_version", 1),
        "tier_counts": counts,
        "n_verified": sum(counts.get(t, 0) for t in VERIFIED_TIERS),
        "n_withheld": sum(1 for b in buildings
                          if b.get("withheld_reason") or b.get("single_reading_m") is not None),
        "tiers": {t: {"label": TIER_LABELS[t], "color": TIER_COLORS[t], "hint": TIER_HINTS[t]}
                  for t in counts},
        "survey": {"providers": providers,
                   "attributions": survey_attributions(providers)
                   + cadastre_attributions(buildings)},
        "n_buildings": len(buildings),
        "height_sources": sources,
        "height_p10": _pct(0.10),
        "height_median": _pct(0.50),
        "height_p90": _pct(0.90),
        "height_max": round(heights[-1], 1) if heights else None,
    })


# --- registration reports and align packs ---------------------------------------

def _report_folder_entry(key: str, root: Path, folder: Path) -> dict | None:
    """One numpy2stl registration report folder (``index.html`` + ``assets/``)."""
    index = folder / "index.html"
    if not index.is_file():
        return None
    rel = folder.relative_to(root).as_posix()
    url_base = f"/reports/files/{key}/{rel}"
    title = None
    try:
        head = index.read_text(encoding="utf-8", errors="replace")[:4000]
        m = re.search(r"<title>([^<]*)</title>", head)
        title = m.group(1).strip() if m else None
    except OSError:
        pass
    return {
        "root": key,
        "name": rel,
        "title": title,
        "index_url": f"{url_base}/index.html",
        "summary_url": (f"{url_base}/summary.html"
                        if (folder / "summary.html").is_file() else None),
        "images": _count(folder / "assets", "*.png"),
        "modified": _mtime(index),
    }


def _scan_report_root(key: str, root: Path) -> list[dict]:
    """Report folders one or two levels down, plus loose HTML pages at the top."""
    out = []
    if not root.is_dir():
        return out
    for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if path.is_dir():
            entry = _report_folder_entry(key, root, path)
            if entry is not None:
                out.append(entry)
                continue
            for sub in sorted(p for p in path.iterdir() if p.is_dir()):
                entry = _report_folder_entry(key, root, sub)
                if entry is not None:
                    out.append(entry)
        elif path.suffix.lower() in (".html", ".htm"):
            out.append({"root": key, "name": path.name, "title": None,
                        "index_url": f"/reports/files/{key}/{path.name}",
                        "summary_url": None, "images": 0, "modified": _mtime(path)})
    return out


#: Pack rasters worth showing as thumbnails, in the drag tool's order.
_PACK_IMAGES = ("stl_heightmap.png", "osm_buildings.png", "sat.png", "stl_water.png",
                "osm_water.png", "stl_mask.png", "stl_relief.png")


def _pick(d: dict | None, *keys) -> dict | None:
    if not isinstance(d, dict):
        return None
    return {k: d.get(k) for k in keys if k in d}


def _pack_entry(folder: Path) -> dict | None:
    """Summary of one align-tool pack's ``meta.json`` (the heavy arrays stay on disk)."""
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"slug": folder.name, "dir": folder.name, "error": f"meta.json unreadable: {e}"}
    url_base = f"/reports/files/{_ALIGN_KEY}/{folder.name}"
    bbox = meta.get("osm_bbox_nsew")
    return {
        "slug": meta.get("slug") or folder.name,
        "dir": folder.name,
        "city": meta.get("city"),
        "region": meta.get("region"),
        "source": meta.get("source"),
        "bbox": ({"north": bbox[0], "south": bbox[1], "east": bbox[2], "west": bbox[3]}
                 if isinstance(bbox, list) and len(bbox) == 4 else None),
        "cell_size_m": meta.get("cell_size_m"),
        "resolution": meta.get("resolution"),
        "tallest_m": meta.get("tallest_m"),
        "refined_span_m": meta.get("refined_span_m"),
        "guess": _pick(meta.get("pipeline_guess"), "source", "scale", "rot_deg"),
        "refinement": _pick(meta.get("refinement"), "accepted", "r", "peak_z", "shift_m",
                            "reasons"),
        "street_placement": _pick(meta.get("street_placement"), "confident",
                                  "position_confident", "moved_m", "size", "turn_deg",
                                  "unique", "size_margin", "agree", "channels"),
        "registration": _pick(meta.get("registration"), "status", "reasons", "lead",
                              "endorsing", "voting", "corrected_m"),
        "meta_url": f"{url_base}/meta.json",
        "placement_url": (f"{url_base}/placement.json"
                          if (folder / "placement.json").is_file() else None),
        "images": [{"name": n, "url": f"{url_base}/{n}"} for n in _PACK_IMAGES
                   if (folder / n).is_file()],
        "modified": _mtime(meta_path),
    }


def _build_registration_inventory() -> dict:
    roots_out, reports, packs = [], [], []
    for key, root in _REG_ROOTS.items():
        exists = root.is_dir()
        roots_out.append({"key": key, "path": str(root), "exists": exists,
                          "kind": "packs" if key == _ALIGN_KEY else "reports"})
        if not exists:
            continue
        if key == _ALIGN_KEY:
            for folder in sorted(p for p in root.iterdir() if p.is_dir()):
                entry = _pack_entry(folder)
                if entry is not None:
                    packs.append(entry)
        else:
            reports.extend(_scan_report_root(key, root))
    verdicts: dict[str, int] = {}
    for p in packs:
        status = (p.get("registration") or {}).get("status") or "unchecked"
        verdicts[status] = verdicts.get(status, 0) + 1
    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "roots": roots_out,
        "reports": reports,
        "packs": packs,
        "totals": {"reports": len(reports), "packs": len(packs), "verdicts": verdicts},
    }


@router.get("/api/reports/registration")
async def reports_registration():
    """Registration reports and align-tool packs, scanned on each request (read-only)."""
    try:
        return JSONResponse(_build_registration_inventory())
    except Exception as e:
        logger.error("Failed to build registration inventory: %s", e, exc_info=True)
        raise HTTPException(status_code=500,
                            detail=f"Registration inventory failed: {e}") from e


# --- file serving -------------------------------------------------------------

def _safe_path(root_key: str, rel_path: str) -> Path:
    """Resolve ``rel_path`` under one of the fixed roots, or raise.

    ``/static`` next door resolves user paths with ``os.path.join`` and no containment check.
    These roots sit beside the OSM and Street View caches, so the check is made here rather than
    inherited.
    """
    root = _ROOTS.get(root_key) or _REG_ROOTS.get(root_key)
    if root is None or not root.is_dir():
        raise HTTPException(status_code=404, detail="Unknown report root")
    try:
        target = (root / rel_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=404, detail="Not found") from None
    if not target.is_relative_to(root.resolve()):
        raise HTTPException(status_code=404, detail="Not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    if target.suffix.lower() not in _SERVABLE:
        raise HTTPException(status_code=404, detail="Not a viewable artifact")
    return target


@router.get("/reports/files/{root_key}/{rel_path:path}")
async def reports_file(root_key: str, rel_path: str):
    """Serve one artifact file out of a report directory."""
    path = _safe_path(root_key, rel_path)
    mime, _ = mimetypes.guess_type(str(path))
    # Reports are rewritten in place by a re-run, so a cached copy would show the previous batch.
    return FileResponse(path, media_type=mime or "application/octet-stream",
                        headers={"Cache-Control": "no-cache"})
