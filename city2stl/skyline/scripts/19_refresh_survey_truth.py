#!/usr/bin/env python3
"""Survey truth upkeep: the stat-2 refresh, the stored roof statistics, survey-only truth for new
cities, and the truth age flag. Never a Google 3D Tiles request.

    python -m city2stl.skyline.scripts.19_refresh_survey_truth --regions prague_pankrac benidorm
    python -m city2stl.skyline.scripts.19_refresh_survey_truth --roof-stats --regions miami honolulu
    python -m city2stl.skyline.scripts.19_refresh_survey_truth --new-truth --regions san_juan \\
        --areas old_san_juan --max-tiles 20 [--plan]
    python -m city2stl.skyline.scripts.19_refresh_survey_truth --temporal --regions san_juan

**Stat refresh** (default). Truth records measured before ``benchmark.STAT_VERSION`` 2 carry a survey
p95 read on tiles grouped by the run (ČÚZK rasters also misplaced, EPT project chosen per tile). Per
region this calls ``benchmark.refresh_survey_truth``: ``survey_m`` / ``survey_cells`` re-read,
``tiles_m`` kept as cached, status reclassified. The cache is backed up to
``runs/benchmark/truth/<region>.stat1.json`` first, and a summary of the shift is written to
``<region>.refresh.json`` beside it and printed.

**--roof-stats** (review 2026-10-09 item 1). Re-reads every survey-cache record
(``runs/survey/<region>.json``) that lacks the stored roof statistics (``roof_m`` p50 / p70 / p90 /
p95 / max, ``ground_p5_m``), on the same ring and key. The tile's nDSM normally comes from the
provider's raster cache, so this costs no network. Writes ``<region>.roofstats.json`` beside the
truth cache: how many re-read p95s equal the cached ``survey_m`` (the review's test: >= 99 %).

**--new-truth**. Survey-only truth (single source: no 3D Tiles, monthly cap) for the OSM footprints
of a city's truth areas (``TRUTH_AREAS``), ported from the 2026-10-09 scratch script
``truth_survey2.py``. Footprints are the pipeline's (``footprints_from_records``) and are measured
and keyed on the ring **as a region report writes it** (``benchmark.report_key``, 6 decimals): the
scratch script keyed the full-precision ring, which no report row matches. Tiles go in the order
of ``--areas``, tallest OSM tag (or published height) first within an area; tiles whose records or
nDSM are already on disk are free and always included, ``--max-tiles`` caps the others. One batch
of ``--fetch-workers`` tiles at a time, checkpointed in the survey cache; new ``survey_only``
records are merged into the truth cache after every batch (existing keys are never overwritten).
A record superseded by its report key moves to ``<region>.k7.json``.

**--temporal**. OSM ``start_date`` (one Overpass query per city, cached in
``<region>.start_dates.json``) -> ``built_year`` and ``temporal`` on every truth record:
``may_postdate`` (built in or after the survey's last year), ``predates`` or ``unknown``.

The truth cache holds keys only, so rings are looked up in every ``heights.json`` under ``runs/``,
in the region's cached OSM buildings (keys at 7 and 6 decimals, as ``photo_heights.untagged_table``
makes them) and in the pipeline's own footprints (full precision and report precision).
"""

from __future__ import annotations

from city2stl.resources import scratch_guard

# one BLAS thread (the tile reads are I/O threads); start only with 6 GB free: a region holds
# up to --fetch-workers tile rasters (~0.5-1 GB each over Miami)
scratch_guard(gpu_gb=None, ram_gb=6.0, max_workers=2)

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from city2stl.skyline import benchmark as bm  # noqa: E402

RUNS = bm.BENCHMARK_ROOT.parent
SITES = Path(bm.__file__).parent / "sites"

#: Where survey-only truth is read, per city: ``area -> (south, north, west, east)``, in the
#: review's priority order (2026-10-09 §3 item 1): Old San Juan, Condado / Isla Verde, Fort
#: Lauderdale beach, the rest of Waikiki, then the remaining strips.
TRUTH_AREAS = {
    "san_juan": {
        "old_san_juan": (18.4560, 18.4720, -66.1250, -66.0850),   # the islet
        "condado": (18.4440, 18.4640, -66.0850, -66.0550),        # Miramar, Condado, the lagoon
        "isla_verde": (18.4320, 18.4520, -66.0300, -65.9960),
        "ocean_park": (18.4400, 18.4560, -66.0550, -66.0300),     # Ocean Park, Punta Las Marias
    },
    "fort_lauderdale": {
        "ftl_beach": (26.0950, 26.1600, -80.1150, -80.0980),      # Las Olas beach -> Galt
        "hollywood_hallandale": (25.9720, 26.0500, -80.1280, -80.1080),
    },
    "honolulu": {
        "waikiki": (21.2640, 21.2950, -157.8480, -157.8150),      # + Ala Moana's east edge
    },
}
#: The note every survey-only truth record carries.
SINGLE_SOURCE_NOTE = "single-source survey truth (no Google 3D Tiles: monthly cap)"


# --------------------------------------------------------------------------- rings


def report_rings(region: str) -> dict[str, list]:
    """key -> ring from every ``heights.json`` under ``runs/`` whose region is ``region``."""
    out: dict[str, list] = {}
    for f in RUNS.rglob("heights.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if bm.region_key(d.get("region", f.parent.name)) != region:
            continue
        for b in d.get("buildings", []):
            ring = b.get("footprint_lonlat")
            if ring and len(ring) >= 3:
                out[bm.footprint_key(ring)] = ring
    return out


def _load_osm(region: str) -> dict:
    from city2stl.skyline.region_data import _load_osm_for_region, _load_region_bbox

    osm, _ = _load_osm_for_region(_load_region_bbox(region))
    return osm


def osm_rings(region: str, wanted: set[str], osm: dict | None = None) -> dict[str, list]:
    """key -> ring for ``wanted`` keys among the region's cached OSM buildings."""
    from shapely.geometry import shape

    osm = osm or _load_osm(region)
    out: dict[str, list] = {}
    for f in osm["buildings"]["features"]:
        try:
            g = shape(f["geometry"])
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        for q in [g] if g.geom_type == "Polygon" else list(g.geoms):
            ring = [[float(x), float(y)] for x, y in np.asarray(q.exterior.coords)[:, :2]]
            for k in (bm.footprint_key(ring),
                      bm.footprint_key([[round(x, 6), round(y, 6)] for x, y in ring])):
                if k in wanted:
                    out[k] = ring
    return out


def pipeline_footprints(region: str, osm: dict | None = None) -> list[dict]:
    """The pipeline's footprints (``footprints_from_records``), each ``{ring, ring6, key7, key6,
    name, tag_m, lat, lon, osm_id}``: ``ring6`` / ``key6`` as a region report writes them, ``key7``
    the full-precision key (2026-10-09 scratch truth), ``osm_id`` matched on the centroid."""
    from shapely.geometry import shape

    from city2stl.skyline._pano import elevated as el
    from city2stl.skyline.region_data import _osm_to_building_records

    osm = osm or _load_osm(region)
    ids: dict[tuple, str] = {}
    for f in osm["buildings"]["features"]:   # the centroid as _osm_to_building_records takes it
        try:
            poly = shape(f.get("geometry") or {})
            if poly.is_empty:
                continue
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception:  # noqa: BLE001
            continue
        c = poly.centroid
        oid = (f.get("properties") or {}).get("osm_id")
        if oid:
            ids[(round(c.y, 7), round(c.x, 7))] = str(oid)
    recs = _osm_to_building_records(osm)
    by_id = {r.feature_id: r for r in recs}
    fps, fids = el.footprints_from_records(recs)
    out = []
    for f, fid in zip(fps, fids, strict=True):
        ring = np.asarray(f.ring, float)[:, :2].tolist()
        ring6 = [[round(x, 6), round(y, 6)] for x, y in ring]
        r = by_id[fid]
        out.append({"ring": ring, "ring6": ring6, "key7": bm.footprint_key(ring),
                    "key6": bm.footprint_key(ring6), "name": f.name or "", "tag_m": f.osm_height_m,
                    "lat": float(r.centroid_lat), "lon": float(r.centroid_lon),
                    "osm_id": ids.get((round(r.centroid_lat, 7), round(r.centroid_lon, 7)))})
    return out


def all_rings(region: str, wanted: set[str]) -> dict[str, list]:
    """key -> ring for ``wanted`` keys: report rings, then OSM rings, then pipeline rings."""
    rings = {k: r for k, r in report_rings(region).items() if k in wanted}
    missing = wanted - set(rings)
    if missing:
        try:
            osm = _load_osm(region)
            rings.update(osm_rings(region, missing, osm))
            missing = wanted - set(rings)
            if missing:
                for f in pipeline_footprints(region, osm):
                    for k, ring in ((f["key7"], f["ring"]), (f["key6"], f["ring6"])):
                        if k in missing:
                            rings[k] = ring
        except Exception as exc:  # noqa: BLE001 - reports alone still cover most keys
            logging.warning("[refresh] %s: OSM rings unavailable: %s", region, exc)
    return rings


# --------------------------------------------------------------------------- stat refresh


def _agreement(cache: dict) -> dict:
    d = [abs(r["survey_m"] - r["tiles_m"]) for r in cache.values()
         if r.get("survey_m") is not None and r.get("tiles_m") is not None]
    return {"n": len(d), "within_3m": int(sum(x <= 3.0 for x in d)),
            "median_diff_m": round(float(np.median(d)), 2) if d else None}


def shift(old: dict, new: dict, keys: list[str]) -> dict:
    """What the refresh changed: status counts, transitions, |d survey_m|, agreement with 3D Tiles."""
    moves = Counter(f"{old[k]['status']}->{new[k]['status']}" for k in keys
                    if old[k]["status"] != new[k]["status"])
    d = [abs(new[k]["survey_m"] - old[k]["survey_m"]) for k in keys
         if new[k].get("survey_m") is not None and old[k].get("survey_m") is not None]
    return {"records": len(old), "refreshed": len(keys),
            "status_before": dict(Counter(r["status"] for r in old.values())),
            "status_after": dict(Counter(r["status"] for r in new.values())),
            "changes": dict(moves),
            "survey_gained": sum(1 for k in keys if old[k].get("survey_m") is None
                                 and new[k].get("survey_m") is not None),
            "survey_lost": sum(1 for k in keys if old[k].get("survey_m") is not None
                               and new[k].get("survey_m") is None),
            "median_abs_d_survey_m": round(float(np.median(d)), 2) if d else None,
            "p95_abs_d_survey_m": round(float(np.percentile(d, 95)), 2) if d else None,
            "tiles_agreement_before": _agreement(old), "tiles_agreement_after": _agreement(new)}


def stat_refresh(region: str, refresh: bool, workers: int) -> dict:
    cached = bm.load_truth_cache(region)
    rings = all_rings(region, set(cached))
    logging.info("[refresh] %s: %d cached records, %d with a ring", region, len(cached),
                 sum(k in rings for k in cached))
    out = bm.refresh_survey_truth(region, rings, refresh=refresh, workers=workers)
    # against the stat-1 backup, so a resumed run still reports the whole shift
    old = (json.loads(out["backup"].read_text(encoding="utf-8")) if out["backup"].exists()
           else out["old"])
    done = [k for k, r in out["new"].items()
            if k in old and r.get("stat") == bm.STAT_VERSION and old[k].get("stat") != bm.STAT_VERSION]
    summary = {"region": region, "stat": bm.STAT_VERSION, "no_ring": len(out["no_ring"]),
               "failed": len(out["failed"]), **shift(old, out["new"], done)}
    (bm.BENCHMARK_ROOT / "truth" / f"{region}.refresh.json").write_text(
        json.dumps(summary, indent=1), encoding="utf-8")
    return summary


# --------------------------------------------------------------------------- roof statistics


def p95_agreement(before: dict, after: dict, tol_m: float = 0.01) -> dict:
    """How many re-read p95s (``after[k]["roof_m"]["p95"]``) equal the cached ``survey_m``
    (``before``), over the records that had one: the review's test, >= 99 % within ``tol_m``."""
    pairs = [(before[k]["survey_m"], (after[k].get("roof_m") or {}).get("p95"))
             for k in before if k in after and before[k].get("survey_m") is not None
             and before[k].get("stat") == bm.STAT_VERSION]
    same = sum(1 for a, b in pairs if b is not None and abs(a - b) <= tol_m)
    diffs = sorted((abs(a - b) for a, b in pairs if b is not None and abs(a - b) > tol_m),
                   reverse=True)
    lost = sum(1 for _, b in pairs if b is None)
    return {"compared": len(pairs), "equal": same,
            "equal_share": round(same / len(pairs), 4) if pairs else None,
            "pass_99pct": bool(pairs) and same >= 0.99 * len(pairs),
            "lost": lost, "changed": len(diffs),
            "largest_changes_m": [round(d, 2) for d in diffs[:10]]}


def roof_stats(region: str, workers: int) -> dict:
    from city2stl.skyline import survey_heights as sh

    provider = bm.REGIONS.get(bm.region_key(region))
    before = sh.load_cache(region)
    todo_keys = {k for k, r in before.items()
                 if r.get("stat") == bm.STAT_VERSION and r.get("roof_stats") != bm.ROOF_STATS_VERSION}
    rings = all_rings(region, todo_keys)
    t0 = time.time()
    by_provider: dict[str, dict] = {}
    for k in todo_keys:
        if k in rings:
            by_provider.setdefault(before[k].get("provider") or provider, {})[k] = rings[k]
    for prov, fps in by_provider.items():
        sh.survey_footprint_heights(region, fps, prov, workers=workers, roof_stats=True)
    after = sh.load_cache(region)
    summary = {"region": region, "records": len(before), "to_read": len(todo_keys),
               "with_ring": sum(k in rings for k in todo_keys),
               "seconds": round(time.time() - t0),
               "p95_vs_cached": p95_agreement({k: before[k] for k in todo_keys}, after),
               "with_roof_stats": sum(1 for r in after.values()
                                      if r.get("roof_stats") == bm.ROOF_STATS_VERSION)}
    (bm.BENCHMARK_ROOT / "truth" / f"{region}.roofstats.json").write_text(
        json.dumps(summary, indent=1), encoding="utf-8")
    return summary


# --------------------------------------------------------------------------- new survey-only truth


def _in_box(lat: float, lon: float, box) -> bool:
    s, n, w, e = box
    return s <= lat <= n and w <= lon <= e


def _published(region: str) -> list[dict]:
    site = SITES / f"{region}.json"
    if not site.exists():
        return []
    kh = json.loads(site.read_text(encoding="utf-8-sig")).get("known_heights_m") or {}
    return [v for n, v in kh.items() if not n.startswith("_") and isinstance(v, dict)]


def _nsew_on_disk(provider: str, tile) -> bool:
    """Whether the provider's raster cache already holds this tile's nDSM (a free read)."""
    if provider != "usgs_3dep_ept" or not tile.part:
        return False
    from city2stl.height.providers._survey import as_nsew
    from geo2stl import cache as gc

    n, s, e, w = as_nsew(tile.bbox)
    ns = f"survey_usgs_3dep_ept_{tile.part}"
    key = gc.make_cache_key(ns, n, s, e, w, {"resolution_m": round(float(bm.RESOLUTION_M), 3)})
    return (gc.CACHE_ROOT / ns / f"{key}.npz").exists()


def truth_record(s: dict, project: str | None, osm_id: str | None, start_dates: dict | None) -> dict:
    """A survey-only truth record from a survey-cache record ``s``."""
    status, tm = bm.classify(s["survey_m"], None)
    rec = {"survey_m": s["survey_m"], "survey_cells": s["survey_cells"], "tiles_m": None,
           "tiles_cells": 0, "status": status, "truth_m": None if tm is None else round(tm, 2),
           "stat": bm.STAT_VERSION, "tiles_read": False, "survey_project": project,
           "survey_years": s.get("years"), "roof_m": s.get("roof_m"),
           "ground_p5_m": s.get("ground_p5_m"), "roof_stats": s.get("roof_stats"),
           "key_ring": "report", "osm_id": osm_id,
           "note": f"{SINGLE_SOURCE_NOTE}, {time.strftime('%Y-%m-%d')}"}
    if start_dates is not None:
        sd = start_dates.get(osm_id or "")
        rec.update(osm_start_date=sd, built_year=bm.built_year(sd),
                   temporal=bm.temporal_flag(sd, s.get("years")))
    return rec


def new_truth(region: str, areas: list[str] | None, max_tiles: int, workers: int,
              plan: bool) -> dict:
    from city2stl.height.providers import lidar_3dep_ept_laspy as ept
    from city2stl.skyline import survey_heights as sh

    ept._WORKERS = min(ept._WORKERS, 2)    # 2 node reads per tile (the CPU cap)
    provider = bm.REGIONS[region]
    boxes = TRUTH_AREAS[region]
    order = list(areas or boxes)
    fps = pipeline_footprints(region)
    meta: dict[str, dict] = {}
    for f in fps:
        area = next((a for a in order if _in_box(f["lat"], f["lon"], boxes[a])), None)
        if area is not None and f["key6"] not in meta:
            meta[f["key6"]] = {**f, "area": area}
    polys = {k: bm._erode(bm._polygon(m["ring6"]), bm.ERODE_M) for k, m in meta.items()}
    parts = {k: (bm.survey_part(provider, p) if not p.is_empty else None) for k, p in polys.items()}
    tiles = bm.tiles_for({k: p for k, p in polys.items() if parts[k]},
                         part=lambda p: bm.survey_part(provider, p))
    pub = _published(region)

    def prio(t):
        best = 0.0
        for k in t.keys:
            m = meta[k]
            best = max(best, float(m["tag_m"] or 0))
            for v in pub:
                if abs(v["lat"] - m["lat"]) < 0.0004 and abs(v["lon"] - m["lon"]) < 0.0004:
                    best = max(best, float(v["height_m"]))
        area_rank = min(order.index(meta[k]["area"]) for k in t.keys)
        return area_rank, -best, -len(t.keys)

    cached = sh.load_cache(region)
    done = {k for k, r in cached.items() if r.get("provider") == provider
            and r.get("stat") == bm.STAT_VERSION and r.get("roof_stats") == bm.ROOF_STATS_VERSION}
    tiles = sorted(tiles, key=prio)
    free, paid = [], []
    for t in tiles:
        (free if set(t.keys) <= done or _nsew_on_disk(provider, t) else paid).append(t)
    sel = free + paid[:max_tiles]
    plan_out = {"region": region, "areas": order, "footprints": len(meta),
                "with_project": sum(1 for v in parts.values() if v),
                "projects": dict(Counter(v for v in parts.values())),
                "tiles": len(tiles), "tiles_on_disk": len(free), "tiles_to_read": len(paid),
                "selected_new": min(max_tiles, len(paid)),
                "per_area_to_read": dict(Counter(meta[t.keys[0]]["area"] for t in paid))}
    print(json.dumps(plan_out), flush=True)
    if plan:
        return plan_out
    try:
        start_dates = load_start_dates(region)
    except Exception as exc:  # noqa: BLE001 - the age flag is metadata; truth still counts
        logging.warning("[truth] %s: no OSM start dates (%s); temporal left unset", region, exc)
        start_dates = None
    truth = bm.load_truth_cache(region)
    added, st = 0, Counter()
    t0 = time.time()
    for i in range(0, len(sel), max(1, workers)):
        batch = sel[i:i + max(1, workers)]
        rings = {k: meta[k]["ring6"] for t in batch for k in t.keys}
        got = sh.survey_footprint_heights(region, rings, provider, workers=workers,
                                          roof_stats=True)
        for k in rings:
            s = got.get(k)
            if s is None or s.get("error"):
                st["no_record" if s is None else "error"] += 1
                continue
            rec = truth_record(s, parts.get(k), meta[k]["osm_id"], start_dates)
            st[rec["status"]] += 1
            if rec["status"] != "survey_only" or k in truth:
                continue
            truth[k] = rec
            added += 1
        moved = _retire_full_precision_keys(region, truth, [meta[k] for k in rings])
        bm.save_truth_cache(region, truth)
        logging.info("[truth] %s: %d/%d tiles (%s), %d new records, %d k7 retired, %.0f s",
                     region, min(i + len(batch), len(sel)), len(sel),
                     ",".join(sorted({meta[t.keys[0]]["area"] for t in batch})), added, moved,
                     time.time() - t0)
    return {**plan_out, "statuses": dict(st), "added": added, "truth_records": len(truth),
            "seconds": round(time.time() - t0)}


def _retire_full_precision_keys(region: str, truth: dict, metas: list[dict]) -> int:
    """Move truth records keyed by a full-precision ring (``key7``) to ``<region>.k7.json`` once
    the same footprint has its report-key record: a report never has the full-precision key."""
    path = bm.BENCHMARK_ROOT / "truth" / f"{region}.k7.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    n = 0
    for m in metas:
        k7, k6 = m["key7"], m["key6"]
        if k7 != k6 and k7 in truth and k6 in truth:
            old[k7] = {**truth.pop(k7), "superseded_by": k6}
            n += 1
    if n:
        path.write_text(json.dumps(old, indent=1, sort_keys=True), encoding="utf-8")
    return n


# --------------------------------------------------------------------------- truth age


def load_start_dates(region: str, refresh: bool = False) -> dict[str, str]:
    """``{osm_id: start_date}`` for the region's bbox, cached beside the truth cache."""
    from city2stl.skyline.region_data import _load_region_bbox

    path = bm.BENCHMARK_ROOT / "truth" / f"{region}.start_dates.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))["start_dates"]
    b = _load_region_bbox(region)
    got = bm.osm_start_dates((b.north, b.south, b.east, b.west))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched": time.strftime("%Y-%m-%d %H:%M"),
                                "bbox_nsew": [b.north, b.south, b.east, b.west],
                                "start_dates": got}, indent=1), encoding="utf-8")
    return got


def temporal(region: str) -> dict:
    """``osm_start_date`` / ``built_year`` / ``temporal`` on every truth record of ``region``."""
    dates = load_start_dates(region)
    truth = bm.load_truth_cache(region)
    ids: dict[str, str | None] = {}
    for f in pipeline_footprints(region):
        for k in (f["key6"], f["key7"]):
            ids.setdefault(k, f["osm_id"])
    flags = Counter()
    for k, r in truth.items():
        oid = r.get("osm_id") or ids.get(k)
        sd = dates.get(oid or "")
        years = r.get("survey_years")
        r.update(osm_id=oid, osm_start_date=sd, built_year=bm.built_year(sd),
                 temporal=bm.temporal_flag(sd, years))
        flags[r["temporal"]] += 1
    bm.save_truth_cache(region, truth)
    return {"region": region, "start_dates": len(dates), "records": len(truth),
            "matched_osm_id": sum(1 for r in truth.values() if r.get("osm_id")),
            "temporal": dict(flags),
            "may_postdate": sorted(((r.get("built_year"), r.get("truth_m"), k)
                                    for k, r in truth.items()
                                    if r["temporal"] == bm.TEMPORAL_MAY_POSTDATE),
                                   key=lambda x: -(x[1] or 0))[:20]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--regions", nargs="+", required=True)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--roof-stats", action="store_true",
                      help="re-read survey-cache records without the stored roof statistics")
    mode.add_argument("--new-truth", action="store_true",
                      help="survey-only truth for the region's TRUTH_AREAS footprints")
    mode.add_argument("--temporal", action="store_true",
                      help="OSM start_date -> temporal flag on every truth record")
    ap.add_argument("--areas", nargs="*", default=None,
                    help="--new-truth: TRUTH_AREAS names, in priority order (default: all)")
    ap.add_argument("--max-tiles", type=int, default=10_000,
                    help="--new-truth: at most this many tiles not yet on disk")
    ap.add_argument("--plan", action="store_true", help="--new-truth: count tiles, read nothing")
    ap.add_argument("--refresh", action="store_true",
                    help="stat refresh: re-read records already at the current stat version too")
    ap.add_argument("--fetch-workers", type=int, default=2,
                    help="survey tiles read at once in this process (default 2)")
    args = ap.parse_args()
    for region in args.regions:
        if args.roof_stats:
            summary = roof_stats(region, args.fetch_workers)
        elif args.new_truth:
            summary = new_truth(region, args.areas, args.max_tiles, args.fetch_workers, args.plan)
        elif args.temporal:
            summary = temporal(region)
        else:
            summary = stat_refresh(region, args.refresh, args.fetch_workers)
        print(json.dumps(summary, indent=1, default=str), flush=True)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for _n in ("httpx", "urllib3", "rasterio", "botocore", "laspy"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    raise SystemExit(main())
