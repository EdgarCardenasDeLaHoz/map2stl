"""Part of ``city2stl.skyline.benchmark`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from geo2stl.cache import (  # noqa: F401 - file_lock re-exported (bm.file_lock)
    atomic_write_json,
    file_lock,
)

from . import benchmark_geometry as _geo
from .benchmark_geometry import (
    AGREE_ABS_M,
    AGREE_REL,
    ERODE_M,
    RESOLUTION_M,
    STAT_VERSION,
    _erode,
    _grid_dim,
    _polygon,
    footprint_key,
    footprint_stat,
    footprint_stats,
    region_key,
    tiles_for,
)

logger = logging.getLogger('city2stl.skyline.benchmark')


# --------------------------------------------------------------------------- sources


def survey_part(provider: str, poly) -> str | None:
    """The part of ``provider`` a footprint is read from, chosen on the footprint alone.

    ``usgs_3dep_ept``: the newest EPT project meeting the footprint. Chosen on a tile's bbox
    it changed with the tile: a 2019 topobathy project clipping the corner of a Miami tile
    won over the city's project and left the tile without points. Other providers: None.
    """
    if provider != "usgs_3dep_ept":
        return None
    from city2stl.height.providers import lidar_3dep_ept_laspy as ept

    minx, miny, maxx, maxy = poly.bounds
    found = ept.projects_for_bbox((maxy, miny, maxx, minx))
    return found[0]["name"] if found else None


def survey_ndsm(provider: str, bbox, resolution_m: float = RESOLUTION_M, part: str | None = None):
    """``(ndsm, transform)`` from a survey provider, or None (not covered). ``part``: from
    ``survey_part`` (an EPT project), else the provider's own choice for the bbox."""
    from city2stl.height.providers import survey

    if part is not None and provider == "usgs_3dep_ept":
        from city2stl.height.providers import lidar_3dep_ept_laspy as ept

        return ept.ndsm_for_bbox(survey.as_nsew(bbox), resolution_m, project=part)
    return survey.ndsm_for_bbox(provider, bbox, resolution_m)


class TilesUnavailable(RuntimeError):
    """3D Tiles could not be asked at all (no API key): a failed source, not "not covered"."""


def tiles_ndsm(bbox, resolution_m: float = RESOLUTION_M, provider=None):
    """``(ndsm, transform)`` from Google 3D Tiles on the bbox's lon/lat grid, or None when
    the tiles have no data there.

    Raises ``TilesUnavailable`` when the provider has no API key. ``covers()`` is False
    then too, and returning None made ``footprint_truth`` cache a survey-only record for
    good (T36: a worktree without ``.env`` pinned 63 of them).
    """
    from rasterio.transform import from_bounds as t_from_bounds

    from city2stl.height.providers.google_3d import Google3DProvider
    from city2stl.skyline.streetview_io import _load_env_file_if_present

    _load_env_file_if_present()  # the key lives in map2stl/.env (GOOGLE_MAPS_API_KEY)
    # plane ground: per-window p5 read Seattle hillside buildings +4.3 m (median); 2026-10-23
    provider = provider or Google3DProvider(ground="plane")
    if not getattr(provider, "_api_key", None):
        raise TilesUnavailable("no Google Maps API key (GOOGLE_MAPS_API_KEY): 3D Tiles not read")
    if not provider.covers(bbox):
        return None
    h, w = _grid_dim(bbox, resolution_m)
    # require_built=False: a box over low-rise blocks has no 8 m relief and would read
    # as "outside coverage"; the survey cross-check catches real coverage gaps instead.
    res = provider.fetch_heights(bbox, (h, w), require_built=False)
    arr = np.asarray(res.raster, dtype=np.float32)
    if not np.isfinite(arr).any():
        return None
    n, s, e, wst = bbox
    return arr, t_from_bounds(wst, s, e, n, w, h)


# --------------------------------------------------------------------------- truth


def classify(survey_m: float | None, tiles_m: float | None) -> tuple[str, float | None]:
    """``(status, truth_m)`` from the two measurements.

    ``confirmed`` (both, agreeing; truth = their mean), ``disputed`` (both, disagreeing;
    no truth), ``survey_only`` / ``tiles_only`` (truth = that value, outside the
    headline), ``unmeasured``.
    """
    if survey_m is not None and tiles_m is not None:
        tol = max(AGREE_ABS_M, AGREE_REL * max(survey_m, tiles_m))
        if abs(survey_m - tiles_m) <= tol:
            return "confirmed", (survey_m + tiles_m) / 2
        return "disputed", None
    if survey_m is not None:
        return "survey_only", survey_m
    if tiles_m is not None:
        return "tiles_only", tiles_m
    return "unmeasured", None


def _truth_cache_path(region: str) -> Path:
    return _geo.BENCHMARK_ROOT / "truth" / f"{region.lower()}.json"


def load_truth_cache(region: str) -> dict:
    p = _truth_cache_path(region)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_json_atomic(path: Path, obj) -> None:
    """Write ``obj`` as JSON to ``path`` through a temporary file and a rename, so a reader in
    another process never sees half a file (retried while Windows holds the target open)."""
    atomic_write_json(path, obj, indent=1, sort_keys=True)


def save_truth_cache(region: str, truth: dict) -> None:
    write_json_atomic(_truth_cache_path(region), truth)


#: A 3D Tiles area without a building mesh (T40). Cartagena's tiles are terrain only: all
#: 900 truth records read 0-19 m while its towers are 125-202 m (published). Such an area is
#: recognised by its OSM-tagged towers: when at least ``FLAT_MIN_TOWERS`` footprints tagged
#: ``FLAT_TAG_MIN_M`` or taller have a tiles reading and ``FLAT_SHARE`` of them read under
#: ``FLAT_READ_SHARE`` of their tag, the tiles there are "not covered", not truth.
FLAT_TAG_MIN_M = 30.0


FLAT_READ_SHARE = 0.3


FLAT_MIN_TOWERS = 3


FLAT_SHARE = 0.8


def flat_mesh(tiles_m: dict[str, float | None], tags_m: dict[str, float]) -> bool:
    """Whether 3D Tiles readings ``tiles_m`` (key -> m) come from a mesh without buildings,
    judged on the OSM height tags ``tags_m`` (key -> m) of the same footprints."""
    pairs = [(tiles_m[k], t) for k, t in tags_m.items()
             if t is not None and t >= FLAT_TAG_MIN_M and tiles_m.get(k) is not None]
    if len(pairs) < FLAT_MIN_TOWERS:
        return False
    low = sum(1 for v, t in pairs if v < FLAT_READ_SHARE * t)
    return low >= FLAT_SHARE * len(pairs)


def _drop_tiles(rec: dict) -> dict:
    """``rec`` with its 3D Tiles reading removed (a flat mesh: not covered) and reclassified."""
    rec = {**rec, "tiles_m": None, "tiles_cells": 0, "tiles_flat": True}
    rec["status"], truth_m = classify(rec.get("survey_m"), None)
    rec["truth_m"] = None if truth_m is None else round(truth_m, 2)
    return rec


#: Threads measuring 500 m truth tiles at once (T27); the reads are network-bound.
TRUTH_WORKERS = 4


def truth_workers(workers: int | None = None) -> int:
    """``workers`` if given, else env ``MAP2STL_TRUTH_WORKERS``, else ``TRUTH_WORKERS``; >= 1."""
    from .config import current  # noqa: PLC0415

    if workers is None:
        workers = current().truth_workers
        if workers is None:
            workers = TRUTH_WORKERS
    return max(1, int(workers))


def footprint_truth(region: str, footprints: dict[str, list], survey_provider: str | None,
                    *, use_tiles: bool = True, resolution_m: float = RESOLUTION_M,
                    tiles_provider=None, refresh: bool = False,
                    tags_m: dict[str, float] | None = None,
                    workers: int | None = None) -> dict[str, dict]:
    """Truth record per footprint key, from the region cache plus any missing tiles.

    ``footprints``: key (``footprint_key``) → lon/lat ring. Returns key → ``{survey_m,
    survey_cells, tiles_m, tiles_cells, status, truth_m}``. Measured records are written
    back to the cache after every tile, so an interrupted run keeps what it fetched.

    Only complete reads are cached: every source this region has (the survey, and 3D Tiles
    unless ``use_tiles`` is False) answered, "not covered" included. A source that raised,
    or a ``--no-tiles`` run, is scored this time but not saved, so it cannot pin a
    survey-only or tiles-only record for good. ``refresh`` re-measures cached footprints.

    ``tags_m`` (key -> OSM height tag) enables the flat-mesh test (``flat_mesh``, T40): per
    tile, and over all of the region's records (cached ones included, so old records from a
    flat mesh are dropped without new reads), 3D Tiles from a mesh without buildings are
    treated as not covered. Such records carry ``tiles_flat``.

    Tiles are measured in a thread pool of ``workers`` threads (T27; default ``truth_workers``:
    env ``MAP2STL_TRUTH_WORKERS`` or ``TRUTH_WORKERS``; 1 = serial). Workers only read the
    sources; the cache is merged and written (atomically) by the calling thread in tile order,
    so records, cache contents and log order equal the serial run's. A source that raises marks
    only its own tile incomplete (as serially); any other exception propagates after the
    running tiles finish.
    """
    cache = load_truth_cache(region)
    todo = {k: _erode(_polygon(ring), ERODE_M) for k, ring in footprints.items()
            if refresh or k not in cache}
    fresh: dict[str, dict] = {}
    part = (lambda p: survey_part(survey_provider, p)) if survey_provider else None
    tiles = tiles_for(todo, resolution_m=resolution_m, part=part)

    def measure(i: int, tile) -> tuple[dict[str, dict], bool]:
        """One tile's records and whether every source answered. Touches no shared state."""
        logger.info("[bench] %s tile %d/%d: %d footprints", region, i, len(tiles),
                    len(tile.keys))
        grids, complete = {}, use_tiles
        if survey_provider:
            try:
                grids["survey"] = (survey_ndsm(survey_provider, tile.bbox, resolution_m, tile.part)
                                   if tile.part else
                                   survey_ndsm(survey_provider, tile.bbox, resolution_m))
            except Exception as exc:  # one bad tile must not lose the rest
                complete = False
                logger.warning("[bench] survey %s failed on %s: %s", survey_provider,
                               tile.bbox, exc)
        if use_tiles:
            try:
                grids["tiles"] = tiles_ndsm(tile.bbox, resolution_m, tiles_provider)
            except Exception as exc:
                complete = False
                logger.warning("[bench] 3D Tiles failed on %s: %s", tile.bbox, exc)
        recs: dict[str, dict] = {}
        for k in tile.keys:
            rec: dict = {}
            for src in ("survey", "tiles"):
                g = grids.get(src)
                hgt, cells = (None, 0) if g is None else footprint_stat(g[0], g[1], todo[k])
                rec[f"{src}_m"] = None if hgt is None else round(hgt, 2)
                rec[f"{src}_cells"] = cells
            g = grids.get("survey")
            if g is not None:  # the stored roof statistics of the survey side (item 1)
                st = footprint_stats(g[0], g[1], todo[k], outline=_polygon(footprints[k]))
                rec.update(survey_roof_m=st["roof_m"], survey_ground_p5_m=st["ground_p5_m"],
                           roof_stats=st["roof_stats"])
            rec["status"], truth_m = classify(rec["survey_m"], rec["tiles_m"])
            rec["truth_m"] = None if truth_m is None else round(truth_m, 2)
            rec["stat"] = STAT_VERSION  # absent: version 1 (tiles grouped by the run)
            recs[k] = rec
        if tags_m and flat_mesh({k: r["tiles_m"] for k, r in recs.items()}, tags_m):
            logger.warning("[bench] %s tile %d/%d: 3D Tiles have no building mesh here "
                           "(tagged towers read flat); treated as not covered", region, i,
                           len(tiles))
            recs = {k: _drop_tiles(r) for k, r in recs.items()}
        return recs, complete

    n_workers = max(1, min(truth_workers(workers), len(tiles)))
    if n_workers == 1:
        results = (measure(i, tile) for i, tile in enumerate(tiles, 1))
        pool = None
    else:  # network-bound reads overlap; map() yields in tile order, so results match serial
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="truth")
        from geo2stl import google_usage

        results = pool.map(google_usage.bind(measure), range(1, len(tiles) + 1), tiles)
    try:
        # the cache is read and written here only, never from a worker thread
        for i, (recs, complete) in enumerate(results, 1):
            for k, rec in recs.items():
                fresh[k] = rec
                if complete:
                    cache[k] = rec
            if complete:
                save_truth_cache(region, cache)
            else:
                logger.info("[bench] %s tile %d/%d not cached (a source failed or was skipped)",
                            region, i, len(tiles))
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
    out = {k: fresh.get(k, cache.get(k)) for k in footprints if k in fresh or k in cache}
    if tags_m and flat_mesh({k: r.get("tiles_m") for k, r in out.items()}, tags_m):
        # the whole region's tiles are a flat mesh: drop them from every record, cached ones
        # too (no new reads), so no tiles-only "truth" survives from it
        logger.warning("[bench] %s: 3D Tiles have no building mesh in this region; "
                       "tiles readings dropped from %d records", region, len(cache))
        cache = {k: _drop_tiles(r) if r.get("tiles_m") is not None else r
                 for k, r in cache.items()}
        save_truth_cache(region, cache)
        out = {k: _drop_tiles(r) if r.get("tiles_m") is not None else r
               for k, r in out.items()}
    return out


def report_key(ring) -> str:
    """``footprint_key`` of a ring as a region report writes it (``heights.json`` rounds
    ``footprint_lonlat`` to 6 decimals), so truth measured on an OSM ring (7 decimals) is filed
    under the key the report's row will have."""
    return footprint_key([[round(float(x), 6), round(float(y), 6)] for x, y, *_ in ring])


# --------------------------------------------------------------------------- truth age

#: ``temporal`` of a truth record (review 2026-10-09 item 1, as 3DBAG's
#: ``_LATEST_BUT_OUTDATED``): the survey may have flown before the building stood.
TEMPORAL_MAY_POSTDATE = "may_postdate"   # OSM start_date in or after the survey's last year


TEMPORAL_PREDATES = "predates"           # start_date before the survey's first year


TEMPORAL_UNKNOWN = "unknown"             # no start_date (most buildings), or no survey year


def built_year(start_date) -> int | None:
    """The latest year an OSM ``start_date`` names ("2017-05-01" -> 2017, "1920s" -> 1920,
    "2016..2018" -> 2018, "~1950" -> 1950), or None. The latest, so a range that may reach past
    the survey is flagged."""
    import re

    years = [int(y) for y in re.findall(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)", str(start_date or ""))]
    return max(years) if years else None


def temporal_flag(start_date, survey_years) -> str:
    """``TEMPORAL_*`` for a building with OSM ``start_date`` read by a survey flown in
    ``survey_years`` (``(first, last)``). The survey's last year counts as "may postdate":
    a building finished in the flight year may or may not be in the point cloud."""
    y = built_year(start_date)
    if y is None or not survey_years:
        return TEMPORAL_UNKNOWN
    return TEMPORAL_MAY_POSTDATE if y >= int(survey_years[-1]) else TEMPORAL_PREDATES


def osm_start_dates(bbox_nsew, timeout_s: int = 120) -> dict[str, str]:
    """``{"way/123": start_date}`` for OSM buildings and building parts with a ``start_date``
    in ``bbox_nsew`` (one Overpass query; the region's OSM cache drops the tag)."""
    from geo2stl.osm import overpass_query

    n, s, e, w = bbox_nsew
    b = f"({s},{w},{n},{e})"
    q = (f"[out:json][timeout:{timeout_s}];("
         f'way["building"]["start_date"]{b};relation["building"]["start_date"]{b};'
         f'way["building:part"]["start_date"]{b};relation["building:part"]["start_date"]{b};'
         f");out tags;")
    return {f"{el['type']}/{el['id']}": str(el["tags"]["start_date"])
            for el in overpass_query(q, timeout_s=timeout_s, backoff_s=5.0)
            if (el.get("tags") or {}).get("start_date")}


def refresh_survey_truth(region: str, footprints: dict[str, list],
                         survey_provider: str | None = None, *,
                         resolution_m: float = RESOLUTION_M, refresh: bool = False,
                         workers: int = 1) -> dict:
    """Re-read the survey side of the region's cached truth with the current statistic
    (``STAT_VERSION``); the 3D Tiles side is kept as cached.

    Truth records of stat version 1 carry a survey p95 that followed the run's footprint
    grouping, ČÚZK rasters read off their returned transform, and an EPT project chosen per
    tile (see ``STAT_VERSION``). This re-reads ``survey_m`` / ``survey_cells`` per footprint
    with ``survey_heights.survey_footprint_heights`` (the same tiles and p95 as
    ``footprint_truth``, cached in ``runs/survey/``), keeps ``tiles_m`` / ``tiles_cells`` /
    ``tiles_flat`` untouched, sets ``stat`` and reclassifies (``classify``). Never reads 3D
    Tiles, so it costs nothing against the Google cap.

    ``footprints``: key -> lon/lat ring for the cached keys (keys not in the cache are
    ignored; cached keys without a ring are left as they are). Records already at
    ``STAT_VERSION`` are skipped unless ``refresh``. A footprint whose survey read failed keeps
    its old record. Before the first write the cache is copied to ``<region>.stat1.json``
    (once; a later run keeps the original backup). Returns ``{"old": cache before,
    "new": cache after, "refreshed": keys, "failed": keys, "no_ring": keys, "backup": path}``.
    ``workers``: survey tiles read at once (``survey_footprint_heights``).
    """
    from . import survey_heights as sh

    provider = survey_provider or _geo.REGIONS.get(region_key(region))
    if provider is None:
        raise ValueError(f"{region}: no survey provider (not a benchmark region)")
    old = load_truth_cache(region)
    todo = {k: footprints[k] for k, r in old.items()
            if k in footprints and (refresh or r.get("stat") != STAT_VERSION)}
    no_ring = sorted(k for k in old if k not in footprints)
    backup = _truth_cache_path(region).with_suffix(".stat1.json")
    if todo and not backup.exists():
        backup.write_text(json.dumps(old, indent=1, sort_keys=True), encoding="utf-8")
    got = sh.survey_footprint_heights(region, todo, provider, resolution_m=resolution_m,
                                      refresh=refresh, workers=workers) if todo else {}
    new = {k: dict(r) for k, r in old.items()}
    refreshed, failed = [], []
    for k in todo:
        s = got.get(k)
        if s is None or s.get("error") or s.get("stat") != STAT_VERSION:
            failed.append(k)
            continue
        rec = new[k]
        rec["survey_m"], rec["survey_cells"] = s["survey_m"], s["survey_cells"]
        rec["status"], truth_m = classify(rec["survey_m"], rec.get("tiles_m"))
        rec["truth_m"] = None if truth_m is None else round(truth_m, 2)
        rec["stat"] = STAT_VERSION
        refreshed.append(k)
    if refreshed:
        save_truth_cache(region, new)
    return {"old": old, "new": new, "refreshed": refreshed, "failed": failed,
            "no_ring": no_ring, "backup": backup}


# --------------------------------------------------------------------------- scoring


def load_report(heights_json: Path, include_unmeasured: bool = False) -> tuple[str, list[dict]]:
    """``(region, buildings)`` from a skyline ``heights.json``; each building gains
    ``key`` (``footprint_key``). Buildings without a footprint are dropped, and so are rows
    the run did not measure (``measured: False``: tagged buildings listed with their tag)
    unless ``include_unmeasured`` (the band tables' "published" scope)."""
    from ._core.tiers import upgrade_tiers

    data = upgrade_tiers(json.loads(Path(heights_json).read_text(encoding="utf-8")))
    out = []
    for b in data.get("buildings", []):
        if b.get("measured") is False and not include_unmeasured:
            continue
        ring = b.get("footprint_lonlat")
        if ring and len(ring) >= 3:
            out.append({**b, "key": footprint_key(ring)})
    return data.get("region", Path(heights_json).parent.name), out
