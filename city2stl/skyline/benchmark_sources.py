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
    ERODE_M,
    RESOLUTION_M,
    _erode,
    _grid_dim,
    _polygon,
    footprint_stat,
    tiles_for,
)
from .benchmark_scoring import score_buildings
from .benchmark_truth import write_json_atomic

logger = logging.getLogger('city2stl.skyline.benchmark')


# --------------------------------------------------------------------------- shipped sources (T29)

#: The per-footprint height sources the app ships, scored against the same truth (T29):
#: ``overture`` (``open_buildings`` provider), ``gba`` (GlobalBuildingAtlas) and ``merged``
#: (``merge_height_rasters`` over the registered providers fine enough for one building,
#: as ``city2stl/height/service.py`` merges them). Read with the truth's footprint statistic
#: (p95 of the eroded footprint) on the same 500 m tiles.
SHIPPED_SOURCES = ("overture", "gba", "merged")


_SOURCE_PROVIDER = {"overture": "open_buildings", "gba": "gba"}


def _sources_cache_path(region: str) -> Path:
    return _geo.BENCHMARK_ROOT / "sources" / f"{region.lower()}.json"


def load_sources_cache(region: str) -> dict:
    p = _sources_cache_path(region)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def source_ndsm(source: str, bbox, resolution_m: float = RESOLUTION_M):
    """``(height raster, transform)`` of a shipped source on the bbox's lon/lat grid, or
    None when it does not cover the bbox. A failed read raises."""
    from rasterio.transform import from_bounds as t_from_bounds

    from city2stl.height import BUILDING_RESOLUTION_LIMIT_M, merge_height_rasters
    from city2stl.height.service import _REGISTRY

    h, w = _grid_dim(bbox, resolution_m)
    if source == "merged":
        fine = [r for r in _REGISTRY if r.resolution_m <= BUILDING_RESOLUTION_LIMIT_M
                and r.instance.covers(bbox)]
        if not fine:
            return None
        res = merge_height_rasters([r.instance.fetch_heights(bbox, (h, w)) for r in fine],
                                   target_shape=(h, w))
    else:
        from city2stl.height.service import _PROVIDER_MAP

        prov = _PROVIDER_MAP[_SOURCE_PROVIDER[source]].instance
        if not prov.covers(bbox):
            return None
        res = prov.fetch_heights(bbox, (h, w))
    arr = np.asarray(res.raster, dtype=np.float32)
    if not np.isfinite(arr).any():
        return None
    n, s, e, wst = bbox
    return arr, t_from_bounds(wst, s, e, n, w, h)


def source_heights(region: str, footprints: dict[str, list],
                   sources: tuple[str, ...] = SHIPPED_SOURCES, *,
                   resolution_m: float = RESOLUTION_M, reader=None,
                   refresh: bool = False) -> dict[str, dict[str, float | None]]:
    """``{source: {key: height or None}}`` for ``footprints`` (key -> lon/lat ring).

    Cached per region in ``runs/benchmark/sources/<region>.json`` like the truth: only a
    tile every source answered for ("not covered" included) is saved, so a failed read is
    tried again next time. ``reader(source, bbox, resolution_m)`` replaces ``source_ndsm``
    (tests). These are network reads (Overture and GBA parquet, plus the merge's other
    fine providers): run them where those hosts are reachable."""
    reader = reader or source_ndsm
    cache = load_sources_cache(region)
    todo = {k: _erode(_polygon(ring), ERODE_M) for k, ring in footprints.items()
            if refresh or any(k not in cache.get(s, {}) for s in sources)}
    fresh: dict[str, dict] = {s: {} for s in sources}
    for tile in tiles_for(todo):
        grids, complete = {}, True
        for src in sources:
            try:
                grids[src] = reader(src, tile.bbox, resolution_m)
            except Exception as exc:  # one bad source must not lose the others
                complete = False
                logger.warning("[bench] source %s failed on %s: %s", src, tile.bbox, exc)
        for src in sources:
            g = grids.get(src)
            for k in tile.keys:
                hgt = None if g is None else footprint_stat(g[0], g[1], todo[k])[0]
                # 0 m inside a footprint: the source has no building there, not a height
                fresh[src][k] = None if hgt is None or hgt <= 0 else round(hgt, 2)
                if complete:
                    cache.setdefault(src, {})[k] = fresh[src][k]
        if complete:
            write_json_atomic(_sources_cache_path(region), cache)
    return {s: {k: fresh[s].get(k, cache.get(s, {}).get(k)) for k in footprints}
            for s in sources}


def score_sources(buildings: list[dict], truth: dict[str, dict],
                  heights: dict[str, dict[str, float | None]],
                  pred_field: str = "no_survey_height_m") -> dict:
    """Each shipped source scored on confirmed truth (``score_buildings``: bands and
    tagged/untagged), next to the run's published height and its Street View reading on the
    *same* buildings, plus coverage (share of the confirmed buildings the source answers)."""
    confirmed = [b for b in buildings
                 if (truth.get(b["key"]) or {}).get("status") == "confirmed"]
    out = {}
    for src, by_key in heights.items():
        rows = [{**b, "source_m": by_key[b["key"]]} for b in confirmed
                if by_key.get(b["key"]) is not None]
        sv = [{**b, "sv_m": b.get("street_view_m", b.get(pred_field, b.get("effective_height_m")))}
              for b in rows]
        out[src] = {
            "coverage": round(len(rows) / len(confirmed), 3) if confirmed else None,
            "source": score_buildings(rows, truth, pred_field="source_m"),
            "published_same": score_buildings(rows, truth, pred_field=pred_field),
            "street_view_same": score_buildings(sv, truth, pred_field="sv_m"),
        }
    return out
