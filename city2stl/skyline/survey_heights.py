"""Per-footprint survey LiDAR heights for a region run (F-SKY26 step 2b).

The same measurement as the benchmark's survey truth, so a published survey height and the
truth it is checked against are one number: the p95 of the survey nDSM inside the footprint
shrunk by ``benchmark.ERODE_M``, footprints grouped into ``benchmark.tiles_for`` tiles, read
with ``benchmark.footprint_stat``. Only the survey is read: never Google 3D Tiles (a run-time
source would spend the monthly free cap, decision 2026-10-07).

    pick_provider(bbox, region=None) -> provider name | None
    survey_footprint_heights(region, footprints, provider=None) -> {key: record}

A record is ``{survey_m, survey_cells, provider, years}`` (``years``: ``(first, last)`` survey
year from ``providers/survey.py::years_for_bbox``, or None). Cached per region in
``runs/survey/<region>.json``; a tile whose read failed (``SurveyError`` or any other error) is
returned with ``error`` and not cached, so a down endpoint never pins "no survey here". A tile
the survey does not cover is a complete answer (``survey_m`` None) and is cached.

Publishing these heights is step 2d (``use_survey_heights``), not done here.
"""

from __future__ import annotations

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
    p = cache_path(region)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")


def _years(provider: str, bbox) -> list[int] | None:
    from city2stl.height.providers import survey

    try:
        y = survey.years_for_bbox(provider, bbox)
    except Exception as exc:  # noqa: BLE001 - the year is metadata; the height still counts
        logger.warning("[survey_heights] %s survey year unknown on %s: %s", provider, bbox, exc)
        return None
    return None if y is None else [int(y[0]), int(y[1])]


def survey_footprint_heights(region: str, footprints: dict[str, list],
                             provider: str | None = None, *,
                             resolution_m: float = bm.RESOLUTION_M,
                             refresh: bool = False) -> dict[str, dict]:
    """Survey height record per footprint key (``benchmark.footprint_key``).

    ``footprints``: key -> lon/lat ring. ``provider``: a ``providers/survey.py`` name; by
    default ``pick_provider`` over the footprints' bbox. Returns {} when no survey covers them.
    Cached records of the same provider are reused unless ``refresh``.
    """
    if not footprints:
        return {}
    provider = provider or pick_provider(_bbox_of(footprints), region)
    if provider is None:
        logger.info("[survey_heights] %s: no survey provider covers it", region)
        return {}
    cache = load_cache(region)
    todo = {k: bm._erode(bm._polygon(ring), bm.ERODE_M) for k, ring in footprints.items()
            if refresh or (cache.get(k) or {}).get("provider") != provider}
    fresh: dict[str, dict] = {}
    tiles = bm.tiles_for(todo)
    for i, tile in enumerate(tiles, 1):
        logger.info("[survey_heights] %s tile %d/%d: %d footprints", region, i, len(tiles),
                    len(tile.keys))
        try:
            grid = bm.survey_ndsm(provider, tile.bbox, resolution_m)
        except Exception as exc:  # SurveyError above all; one bad tile must not lose the rest
            logger.warning("[survey_heights] %s failed on %s: %s", provider, tile.bbox, exc)
            for k in tile.keys:
                fresh[k] = {"survey_m": None, "survey_cells": 0, "provider": provider,
                            "years": None, "error": str(exc)}
            continue
        years = _years(provider, tile.bbox) if grid is not None else None
        for k in tile.keys:
            hgt, cells = (None, 0) if grid is None else bm.footprint_stat(grid[0], grid[1],
                                                                          todo[k])
            rec = {"survey_m": None if hgt is None else round(hgt, 2), "survey_cells": cells,
                   "provider": provider, "years": years}
            fresh[k] = cache[k] = rec
        save_cache(region, cache)
    return {k: fresh.get(k, cache.get(k)) for k in footprints if k in fresh or k in cache}
