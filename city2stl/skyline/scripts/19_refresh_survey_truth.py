#!/usr/bin/env python3
"""Re-measure the survey side of the cached benchmark truth with the current statistic.

    python -m city2stl.skyline.scripts.19_refresh_survey_truth --regions prague_pankrac benidorm

Truth records measured before ``benchmark.STAT_VERSION`` 2 carry a survey p95 read on tiles
grouped by the run (ČÚZK rasters also misplaced, EPT project chosen per tile). Per region this
calls ``benchmark.refresh_survey_truth``: ``survey_m`` / ``survey_cells`` re-read, ``tiles_m`` kept
as cached, status reclassified; never a 3D Tiles request. The cache is backed up to
``runs/benchmark/truth/<region>.stat1.json`` first, and a summary of the shift is written to
``<region>.refresh.json`` beside it and printed.

The truth cache holds keys only, so the rings are looked up in every ``heights.json`` under
``runs/`` and, for keys still missing (photo-pipeline footprints), in the region's cached OSM
buildings (keys at 7 and 6 decimals, as ``photo_heights.untagged_table`` makes them).
"""

from __future__ import annotations

from city2stl.resources import scratch_guard

# one BLAS thread (the tile reads are I/O threads); start only with 6 GB free: a region holds
# up to --fetch-workers tile rasters (~0.5-1 GB each over Miami)
scratch_guard(gpu_gb=None, ram_gb=6.0, max_workers=1)

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from collections import Counter  # noqa: E402

import numpy as np  # noqa: E402

from city2stl.skyline import benchmark as bm  # noqa: E402

RUNS = bm.BENCHMARK_ROOT.parent


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


def osm_rings(region: str, wanted: set[str]) -> dict[str, list]:
    """key -> ring for ``wanted`` keys among the region's cached OSM buildings."""
    from shapely.geometry import shape

    from city2stl.skyline.region_data import _load_osm_for_region, _load_region_bbox

    osm, _ = _load_osm_for_region(_load_region_bbox(region))
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--regions", nargs="+", required=True)
    ap.add_argument("--refresh", action="store_true",
                    help="re-read records already at the current stat version too")
    ap.add_argument("--fetch-workers", type=int, default=4,
                    help="survey tiles read at once in this process (default 4); run one "
                         "process per region to refresh regions in parallel")
    args = ap.parse_args()
    for region in args.regions:
        cached = bm.load_truth_cache(region)
        rings = report_rings(region)
        missing = {k for k in cached if k not in rings}
        if missing:
            try:
                rings.update(osm_rings(region, missing))
            except Exception as exc:  # noqa: BLE001 - reports alone still refresh most keys
                logging.warning("[refresh] %s: OSM rings unavailable: %s", region, exc)
        logging.info("[refresh] %s: %d cached records, %d with a ring", region, len(cached),
                     sum(k in rings for k in cached))
        out = bm.refresh_survey_truth(region, rings, refresh=args.refresh,
                                      workers=args.fetch_workers)
        # against the stat-1 backup, so a resumed run still reports the whole shift
        old = (json.loads(out["backup"].read_text(encoding="utf-8")) if out["backup"].exists()
               else out["old"])
        done = [k for k, r in out["new"].items()
                if k in old and r.get("stat") == bm.STAT_VERSION and old[k].get("stat") != bm.STAT_VERSION]
        summary = {"region": region, "stat": bm.STAT_VERSION, "no_ring": len(out["no_ring"]),
                   "failed": len(out["failed"]), **shift(old, out["new"], done)}
        (bm.BENCHMARK_ROOT / "truth" / f"{region}.refresh.json").write_text(
            json.dumps(summary, indent=1), encoding="utf-8")
        print(json.dumps(summary, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
