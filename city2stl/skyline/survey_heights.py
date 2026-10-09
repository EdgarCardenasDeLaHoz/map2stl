"""Per-footprint survey LiDAR heights for a region run (F-SKY26 step 2b).

The same measurement as the benchmark's survey truth, so a published survey height and the
truth it is checked against are one number: the p95 of the survey nDSM inside the footprint
shrunk by ``benchmark.ERODE_M``, footprints grouped into ``benchmark.tiles_for`` tiles (a fixed
world grid, so the value does not depend on the other footprints: ``stat`` 2), read with
``benchmark.footprint_stat``. Only the survey is read: never Google 3D Tiles (a run-time
source would spend the monthly free cap, decision 2026-10-07).

    pick_provider(bbox, region=None) -> provider name | None
    survey_footprint_heights(region, footprints, provider=None) -> {key: record}

A record is ``{survey_m, survey_cells, provider, years, stat}`` (``years``: ``(first, last)`` survey
year from ``providers/survey.py::years_for_bbox``, or None), plus the stored roof statistics
(``benchmark.footprint_stats``, review 2026-10-09 item 1): ``roof_m`` ``{p50, p70, p90, p95, max}``
(``roof_m["p95"] == survey_m``, the headline), ``ground_p5_m`` / ``ground_cells`` (p5 of the nDSM in a
4 m ring outside the footprint) and ``roof_stats`` (``benchmark.ROOF_STATS_VERSION``). Records
cached before the roof statistics existed have only ``survey_m``; ``roof_stats=True`` re-reads them
(the tile's nDSM usually comes from the provider's raster cache, so no network). Cached per region in
``runs/survey/<region>.json``; a tile whose read failed (``SurveyError`` or any other error) is
returned with ``error`` and not cached, so a down endpoint never pins "no survey here". A tile
the survey does not cover is a complete answer (``survey_m`` None) and is cached.

Publishing these heights is step 2d (``use_survey_heights``), not done here.
"""

from __future__ import annotations

import gc
import json
import logging
from pathlib import Path

from . import benchmark as bm

logger = logging.getLogger(__name__)

SURVEY_ROOT = Path(__file__).resolve().parent / "runs" / "survey"
#: Tried first when several providers cover a bbox: USGS's newest EPT project, because
#: Planetary Computer's COPC copy (``usgs_3dep``) has no tiles over central Miami or Seattle
#: and serves older projects (the benchmark's ``REGIONS`` choice).
PREFERRED = ("usgs_3dep_ept",)


def _bbox_of(footprints: dict[str, list]) -> tuple[float, float, float, float]:
    """``(north, south, east, west)`` around every ring of ``footprints``."""
    xs = [float(x) for ring in footprints.values() for x, _ in ring]
    ys = [float(y) for ring in footprints.values() for _, y in ring]
    return max(ys), min(ys), max(xs), min(xs)


def pick_provider(bbox, region: str | None = None) -> str | None:
    """The survey provider for ``bbox``: a benchmark region's own (``benchmark.REGIONS``), else
    the first available provider covering it (``PREFERRED`` first), else None."""
    if region is not None and bm.region_key(region) in bm.REGIONS:
        return bm.REGIONS[bm.region_key(region)]
    from city2stl.height.providers import survey

    avail = [a["name"] for a in survey.available_for_bbox(bbox) if a["available"]]
    return next((n for n in PREFERRED if n in avail), avail[0] if avail else None)


def cache_path(region: str) -> Path:
    return SURVEY_ROOT / f"{bm.region_key(region)}.json"


def load_cache(region: str) -> dict:
    p = cache_path(region)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_cache(region: str, cache: dict) -> None:
    bm.write_json_atomic(cache_path(region), cache)


def _years(provider: str, bbox, part: str | None = None) -> list[int] | None:
    from city2stl.height.providers import survey

    if part is not None and provider == "usgs_3dep_ept":  # the EPT project read (bm.survey_part)
        from city2stl.height.providers import lidar_3dep_ept_laspy as ept

        y = ept._project_year(part)
        return [y, y] if y else None

    try:
        y = survey.years_for_bbox(provider, bbox)
    except Exception as exc:  # noqa: BLE001 - the year is metadata; the height still counts
        logger.warning("[survey_heights] %s survey year unknown on %s: %s", provider, bbox, exc)
        return None
    return None if y is None else [int(y[0]), int(y[1])]


def _bounded(fn, items, workers: int):
    """``fn(item)`` for each item, at most ``workers`` at once (threads), yielded as they finish.

    Only the calls in flight hold their results: a finished one is handed over and forgotten,
    so a region's peak memory is ``workers`` tile rasters, not every tile read so far (all
    futures submitted up front kept each raster alive until the end: 4-5 GB per region).
    """
    if workers <= 1 or len(items) <= 1:
        yield from map(fn, items)
        return
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    todo = iter(items)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="survey") as pool:
        running = {pool.submit(fn, it) for _, it in zip(range(workers), todo, strict=False)}
        while running:
            done, running = wait(running, return_when=FIRST_COMPLETED)
            for f in done:
                nxt = next(todo, None)
                if nxt is not None:
                    running.add(pool.submit(fn, nxt))
                res = f.result()
                del f
                yield res
                del res


def _stale(rec: dict | None, provider: str, roof_stats: bool) -> bool:
    """Whether a cached record must be read again: another provider or stat version, or (with
    ``roof_stats``) no current roof statistics."""
    rec = rec or {}
    return (rec.get("provider") != provider or rec.get("stat") != bm.STAT_VERSION
            or (roof_stats and rec.get("roof_stats") != bm.ROOF_STATS_VERSION))


def measure(grid, poly, ring) -> dict:
    """One footprint's record fields from a tile's ``(ndsm, transform)`` (None: not covered):
    ``survey_m`` (the p95, ``benchmark.footprint_stat``), ``survey_cells`` and the roof
    statistics (``benchmark.footprint_stats``; ``ring``: the footprint before erosion)."""
    if grid is None:
        return {"survey_m": None, "survey_cells": 0, "roof_m": None, "ground_p5_m": None,
                "ground_cells": 0, "roof_stats": bm.ROOF_STATS_VERSION}
    hgt, cells = bm.footprint_stat(grid[0], grid[1], poly)
    st = bm.footprint_stats(grid[0], grid[1], poly, outline=bm._polygon(ring))
    return {"survey_m": None if hgt is None else round(hgt, 2), "survey_cells": cells,
            "roof_m": st["roof_m"], "ground_p5_m": st["ground_p5_m"],
            "ground_cells": st["ground_cells"], "roof_stats": st["roof_stats"]}


def survey_footprint_heights(region: str, footprints: dict[str, list],
                             provider: str | None = None, *,
                             resolution_m: float = bm.RESOLUTION_M,
                             refresh: bool = False, workers: int = 1,
                             roof_stats: bool = False) -> dict[str, dict]:
    """Survey height record per footprint key (``benchmark.footprint_key``).

    ``footprints``: key -> lon/lat ring. ``provider``: a ``providers/survey.py`` name; by
    default ``pick_provider`` over the footprints' bbox. Returns {} when no survey covers them.
    Cached records of the same provider and stat version (``benchmark.STAT_VERSION``) are
    reused unless ``refresh``; older records are re-measured. ``workers`` > 1 reads that many
    tiles at once (threads; the reads wait on the network and on numpy); results are taken
    and cached one at a time, so a stopped run keeps every finished tile.

    ``roof_stats``: also re-measure cached records without the current roof statistics
    (``roof_stats`` != ``benchmark.ROOF_STATS_VERSION``). Every new read stores them anyway.
    """
    if not footprints:
        return {}
    provider = provider or pick_provider(_bbox_of(footprints), region)
    if provider is None:
        logger.info("[survey_heights] %s: no survey provider covers it", region)
        return {}
    cache = load_cache(region)
    todo = {k: bm._erode(bm._polygon(ring), bm.ERODE_M) for k, ring in footprints.items()
            if refresh or _stale(cache.get(k), provider, roof_stats)}
    fresh: dict[str, dict] = {}
    tiles = bm.tiles_for(todo, resolution_m=resolution_m,
                         part=lambda p: bm.survey_part(provider, p))

    def read(tile):
        try:
            grid = (bm.survey_ndsm(provider, tile.bbox, resolution_m, tile.part) if tile.part
                    else bm.survey_ndsm(provider, tile.bbox, resolution_m))
        except Exception as exc:  # SurveyError above all; one bad tile must not lose the rest
            return tile, None, None, exc
        return tile, grid, (_years(provider, tile.bbox, tile.part) if grid is not None
                            else None), None

    for i, (tile, grid, years, exc) in enumerate(_bounded(read, tiles, workers), 1):
        logger.info("[survey_heights] %s tile %d/%d: %d footprints", region, i, len(tiles),
                    len(tile.keys))
        if exc is not None:
            logger.warning("[survey_heights] %s failed on %s: %s", provider, tile.bbox, exc)
            for k in tile.keys:
                fresh[k] = {"survey_m": None, "survey_cells": 0, "provider": provider,
                            "years": None, "error": str(exc)}
            continue
        for k in tile.keys:
            rec = measure(grid, todo[k], footprints[k])
            rec.update(provider=provider, years=years, stat=bm.STAT_VERSION)
            fresh[k] = rec
        # checkpoint into the cache as it is on disk now (another process reading the same
        # region keeps its records), then drop the tile's raster before the next
        cache = {**load_cache(region), **{k: fresh[k] for k in tile.keys}}
        save_cache(region, cache)
        del grid
        gc.collect()
    return {k: fresh.get(k, cache.get(k)) for k in footprints if k in fresh or k in cache}
