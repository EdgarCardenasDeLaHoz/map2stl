"""
core/benchmark_scores.py — the Benchmark page's data (F-UNIFY Phase 4).

Reads what the benchmark already wrote and scores again on request; the router
(``routers/benchmark.py``) only calls these functions.

* **History** — every ``summary.json`` under ``runs/benchmark/<stamp>/`` (and the local
  ``SKYLINE_LOCAL_DIR/benchmark``) is one scoring of its regions, plus the scores the page
  itself ran (``scores/<region>.json``). The newest entry of a region is its current score,
  the one before it gives the delta. No network: everything is read from files.
* **Final heights** — what the app publishes. A post-T28 report's ``overall`` already is the
  final answer; a pre-T28 report's final answer is ``withheld_simulated``; a survey-only
  region (no 3D Tiles cross-check) is ``single_source``.
* **Score again** — ``score_region`` runs ``scripts/10_benchmark.score_report`` on a report
  of the region. With paid fetches off it scores the cached truth only
  (``cached_truth=True``); footprints without a cached record are left out and counted.
  With them on it may read 3D Tiles / surveys (the caller asked for that).
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from city2stl.skyline import benchmark as bm
from city2stl.skyline import tier_display as td

logger = logging.getLogger(__name__)

#: Google 3D Tiles free monthly cap on root requests.
TILES_ROOT_CAP = 1000
TIER_ORDER = ("survey", "corroborated", "tag", "single", "prior", "unlabelled")
#: Regions with a truth cache that are not in ``bm.REGIONS`` (no survey; weak truth).
EXTRA_REGIONS = {"cartagena": "3D Tiles (flat) · weak truth"}
_RUNS = bm.BENCHMARK_ROOT.parent  # city2stl/skyline/runs

_lock = threading.Lock()


# --------------------------------------------------------------------------- paths


def scores_dir() -> Path:
    """Where the page's own scorings are kept (``MAP2STL_BENCHMARK_SCORES_DIR`` overrides)."""
    env = os.environ.get("MAP2STL_BENCHMARK_SCORES_DIR")
    return Path(env) if env else bm.BENCHMARK_ROOT / "scores"


def summary_roots() -> list[Path]:
    return [bm.BENCHMARK_ROOT, bm.BENCHMARK_RUNS]


def report_url(heights: str | Path) -> str | None:
    """``/reports/files/...`` URL of the ``benchmark.html`` next to a ``heights.json``."""
    page = Path(heights).parent / "benchmark.html"
    if not page.exists():
        return None
    try:
        rel = page.resolve().relative_to(_RUNS.resolve())
    except ValueError:
        return None
    parts = rel.parts
    root = {"region_reports": "region", "benchmark": "benchmark"}.get(parts[0])
    if root is None:
        return None
    return "/reports/files/" + root + "/" + "/".join(parts[1:])


def _local_report(path: str | None) -> str | None:
    """A summary's ``report`` path from the other PC, mapped under this PC's runs folder."""
    if not path:
        return None
    m = re.search(r"skyline/runs/(.+)$", path.replace("\\", "/"))
    if m:
        local = _RUNS / m.group(1)
        return str(local) if local.exists() else None
    p = Path(path)
    return str(p) if p.exists() else None


# --------------------------------------------------------------------------- reading


def _display(region: str) -> str:
    return region.replace("_", " ").title()


def _final(result: dict) -> dict | None:
    """The overall errors of the heights the app publishes, or None when not scored."""
    if "error" in result:
        return None
    sim = (result.get("withheld_simulated") or {}).get("overall") or {}
    if "street_view_unwithheld" not in result and sim.get("n"):
        return sim
    overall = result.get("overall") or {}
    if overall.get("n"):
        return overall
    single = (result.get("single_source") or {}).get("overall") or {}
    return single if single.get("n") else None


def _share(stats: dict) -> tuple[str, float | None]:
    if stats.get("within_3m") is not None:
        return "3m", stats["within_3m"]
    return "25pct", stats.get("within_25pct")


def _rows(result: dict) -> list[dict]:
    tiers = result.get("tiers") or {}
    rows = []
    for key in TIER_ORDER:
        t = tiers.get(key) or {}
        if not t.get("n"):
            continue
        kind, within = _share(t)
        rows.append(
            {
                "key": key,
                "label": td.TIER_LABELS.get(key, key),
                "hint": td.TIER_HINTS.get(key, ""),
                "color": td.TIER_COLORS.get(key, "#999"),
                "buildings": t["n"],
                "mae_m": t.get("mae_m"),
                "bias_m": t.get("bias_m"),
                "within": within,
                "within_kind": kind,
            }
        )
    for key, s in (result.get("sources") or {}).items():  # T29 sources, when scored
        o = s.get("overall") or s if isinstance(s, dict) else {}
        if o.get("n"):
            kind, within = _share(o)
            rows.append(
                {
                    "key": f"source:{key}",
                    "label": str(key),
                    "hint": "",
                    "color": "#bf5af2",
                    "buildings": o["n"],
                    "mae_m": o.get("mae_m"),
                    "bias_m": o.get("bias_m"),
                    "within": within,
                    "within_kind": kind,
                }
            )
    return rows


_PROVIDERS = {
    "usgs_3dep_ept": "USGS 3DEP lidar",
    "cnig_mdsn": "CNIG lidar",
    "ign_lidarhd": "IGN LiDAR HD",
    "cuzk_dmp": "CUZK lidar",
}


def _truth_label(result: dict) -> str:
    survey = result.get("survey")
    mode = ((result.get("bench") or {}).get("measured") or {}).get("truth_mode")
    label = _PROVIDERS.get(survey, (survey or "").replace("_", " ") or "3D Tiles")
    if survey and mode == bm.SINGLE_SOURCE:
        return label + " (survey only)"
    if survey and mode:
        return label + " + 3D Tiles"
    return label


def entry_from_result(result: dict, stamp: str, source: str) -> dict | None:
    """One history entry from a ``score_report`` result; None when it holds no score."""
    final = _final(result)
    if final is None:
        return None
    report = _local_report(result.get("report"))
    kind, within = _share(final)
    return {
        "stamp": stamp,
        "source": source,
        "truth": _truth_label(result),
        "buildings": final["n"],
        "n_buildings": result.get("n_buildings"),
        "mae_m": final.get("mae_m"),
        "median_ae_m": final.get("median_ae_m"),
        "bias_m": final.get("bias_m"),
        "within": within,
        "within_kind": kind,
        "rows": _rows(result),
        "report": report,
        "report_url": report_url(report) if report else None,
        "note": result.get("note"),
    }


_summary_cache: dict[tuple[str, float], dict] = {}


def _load_summary(path: Path) -> dict | None:
    """A summary.json, parsed once per file version (the list asks for every region)."""
    try:
        key = (str(path), path.stat().st_mtime)
        if key not in _summary_cache:
            _summary_cache.clear() if len(_summary_cache) > 40 else None
            _summary_cache[key] = json.loads(path.read_text(encoding="utf-8"))
        return _summary_cache[key]
    except (OSError, ValueError):
        return None


def history(region: str) -> list[dict]:
    """Every scoring of ``region``, oldest first."""
    seen: dict[str, dict] = {}
    for root in summary_roots():
        for path in sorted(root.glob("*/summary.json")):
            data = _load_summary(path)
            if data is None:
                continue
            for res in data.get("results", []):
                if res.get("region") != region:
                    continue
                e = entry_from_result(res, data.get("stamp") or path.parent.name, "summary")
                if e:
                    seen.setdefault(e["stamp"] + "/summary", e)
    own = scores_dir() / f"{region}.json"
    if own.exists():
        try:
            for e in json.loads(own.read_text(encoding="utf-8")).get("history", []):
                seen[e["stamp"] + "/" + e.get("source", "job")] = e
        except (OSError, ValueError):
            pass
    return sorted(seen.values(), key=lambda e: e["stamp"])


def _all_region_ids() -> list[str]:
    return [*bm.REGIONS, *(r for r in EXTRA_REGIONS if r not in bm.REGIONS)]


def _sub(region: str, entry: dict | None) -> str:
    if region in EXTRA_REGIONS:
        return EXTRA_REGIONS[region]
    if entry:
        return f"{entry['truth']} · {entry['buildings']} buildings"
    return f"{_PROVIDERS.get(bm.REGIONS.get(region), 'survey')} · not scored yet"


def list_regions() -> list[dict]:
    out = []
    for rid in _all_region_ids():
        h = history(rid)
        cur = h[-1] if h else None
        out.append(
            {
                "id": rid,
                "name": _display(rid),
                "sub": _sub(rid, cur),
                "truth": cur["truth"] if cur else None,
                "buildings": cur["buildings"] if cur else None,
                "mae_m": cur["mae_m"] if cur else None,
                "scored": cur["stamp"] if cur else None,
            }
        )
    return out


def region_detail(region: str) -> dict | None:
    if region not in _all_region_ids():
        return None
    h = history(region)
    cur = h[-1] if h else None
    prev = h[-2] if len(h) > 1 else None
    delta = None
    if cur and prev and None not in (cur["mae_m"], prev["mae_m"]):
        delta = round(cur["mae_m"] - prev["mae_m"], 2)
    return {
        "id": region,
        "name": _display(region),
        "sub": _sub(region, cur),
        "current": cur,
        "previous": {"stamp": prev["stamp"], "mae_m": prev["mae_m"]} if prev else None,
        "delta_mae_m": delta,
        "history": [{"stamp": e["stamp"], "mae_m": e["mae_m"]} for e in h],
        "heights_from": [{"id": "latest", "label": "Newest report"}, *report_choices(region)],
    }


# --------------------------------------------------------------------------- reports to score


def _report_files(region: str) -> list[Path]:
    found = list(bm.BENCHMARK_RUNS.glob(f"*/{region}_skyline_report/heights.json"))
    found += list(_RUNS.glob(f"region_reports/{region}*_skyline_report/heights.json"))
    return sorted(set(found), key=lambda p: p.stat().st_mtime, reverse=True)


def report_choices(region: str) -> list[dict]:
    out = []
    for p in _report_files(region)[:6]:
        try:
            rel = p.resolve().relative_to(_RUNS.resolve()).as_posix()
        except ValueError:
            continue
        out.append(
            {
                "id": rel,
                "label": time.strftime("%b %d", time.localtime(p.stat().st_mtime))
                + " · "
                + p.parent.name.replace("_skyline_report", ""),
            }
        )
    return out


def resolve_heights(region: str, heights_from: str) -> Path | None:
    if heights_from in ("", "latest"):
        found = _report_files(region)
        return found[0] if found else None
    cand = _RUNS / heights_from
    try:
        cand.resolve().relative_to(_RUNS.resolve())
    except ValueError:
        return None
    return cand if cand.name == "heights.json" and cand.exists() else None


def truth_gap(region: str, heights: Path) -> dict:
    """How many footprints of the report have no cached truth, and how many 3D Tiles reads
    measuring them would take. Reads files only."""
    _, buildings = bm.load_report(heights)
    cache = bm.load_truth_cache(region)
    missing = [b for b in buildings if b["key"] not in cache]
    todo = {b["key"]: bm._erode(bm._polygon(b["footprint_lonlat"]), bm.ERODE_M) for b in missing}
    tiles = len(bm.tiles_for(todo)) if todo else 0
    return {"buildings": len(buildings), "missing": len(missing), "tile_fetches": tiles}


# --------------------------------------------------------------------------- scoring


def _score_report(*args, **kwargs) -> dict:
    mod = importlib.import_module("city2stl.skyline.scripts.10_benchmark")
    return mod.score_report(*args, **kwargs)


def _save(region: str, entry: dict) -> None:
    path = scores_dir() / f"{region}.json"
    with _lock:
        hist: list = []
        if path.exists():
            try:
                hist = json.loads(path.read_text(encoding="utf-8")).get("history", [])
            except (OSError, ValueError):
                hist = []
        hist.append(entry)
        bm.write_json_atomic(path, {"region": region, "history": hist[-20:]})


class NeedsPaidFetches(Exception):
    """Cached truth cannot score this report and fetching was not allowed."""


def score_region(region: str, heights_from: str, allow_paid_fetches: bool, job=None) -> dict:
    """Score ``region`` again; returns the new history entry (also stored)."""

    def step(p: int, msg: str) -> None:
        if job is not None:
            job.update(p, msg)

    if region not in _all_region_ids():
        raise ValueError(f"Unknown region: {region}")
    step(5, "Finding the report")
    heights = resolve_heights(region, heights_from)
    if heights is None:
        raise ValueError(f"No height report found for {_display(region)}; run the region first.")
    step(15, "Checking cached truth")
    gap = truth_gap(region, heights)
    if not allow_paid_fetches and gap["buildings"] and gap["missing"] == gap["buildings"]:
        raise NeedsPaidFetches(
            f"No cached truth for {_display(region)}: scoring needs {gap['tile_fetches']} 3D Tiles "
            f"fetches for {gap['missing']} buildings. Turn on 'Allow paid fetches' to measure them."
        )
    step(30, "Scoring" + (" (may fetch truth)" if allow_paid_fetches else " from cached truth"))
    result = _score_report(heights, region, cached_truth=not allow_paid_fetches)
    result["report"] = str(heights)
    entry = entry_from_result(result, time.strftime("%Y-%m-%d_%H%M"), "job")
    if entry is None:
        raise ValueError("Nothing could be scored: no building has cached truth.")
    entry["missing_truth"] = gap["missing"]
    entry["tile_fetches_needed"] = gap["tile_fetches"]
    if gap["missing"] and not allow_paid_fetches:
        entry["note"] = (
            f"{gap['missing']} of {gap['buildings']} buildings have no cached truth "
            f"and were left out; measuring them needs about {gap['tile_fetches']} "
            "3D Tiles fetches."
        )
    step(95, "Saving")
    _save(region, entry)
    return entry


def gap_for(region: str, heights_from: str) -> dict | None:
    """Missing-truth numbers for the page's 'Allow paid fetches' hint; None without a report."""
    heights = resolve_heights(region, heights_from)
    return truth_gap(region, heights) if heights else None


# --------------------------------------------------------------------------- usage


def tiles_usage() -> dict:
    """3D Tiles root requests this month. Nothing counts them yet, so value is None and the
    page hides the bar (a counter would plug in here)."""
    return {"value": None, "limit": TILES_ROOT_CAP, "month": time.strftime("%Y-%m")}
