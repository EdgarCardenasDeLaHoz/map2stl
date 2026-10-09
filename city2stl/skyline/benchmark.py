"""Skyline height benchmark (F-SKYBENCH): surveyed truth per footprint, and the scorer.

Truth for a building is its height above ground measured two ways and cross-checked:

- a survey nDSM (``city2stl.height.providers.survey``: USGS 3DEP, IGN, CNIG, ČÚZK, …), and
- Google Photorealistic 3D Tiles (``Google3DProvider``).

Each source gives the p95 of the nDSM inside the footprint shrunk by ``ERODE_M`` (so
façade edges and neighbouring roofs don't bleed in). The two agree when they differ by
at most ``max(AGREE_ABS_M, AGREE_REL × height)``; only agreeing ("confirmed") buildings
make the headline. Disagreements are kept and counted: they are new construction,
demolition, trees over low roofs, or a bad ground estimate, and say which source to doubt.

Why not OSM height tags: they are sparse (22 of 329 scored buildings on Cartagena) and are
mostly ``building:levels × 3.4 m``. Why not one source: either alone hides its own errors
(decision: ``docs/decisions/building-heights.md``, 2026-10-03).

Truth is measured on the footprints a run actually scored (``heights.json`` →
``footprint_lonlat``), keyed by a geometry hash, and cached per region under
``runs/benchmark/truth/`` so re-scoring needs no network.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

M_PER_DEG_LAT = 111_320.0
#: Inward buffer before sampling a footprint, metres.
ERODE_M = 1.0
#: Roof statistic: percentile of the nDSM cells inside the footprint.
ROOF_PERCENTILE = 95.0
#: Fewer valid cells than this and a source does not measure the building.
MIN_CELLS = 4
#: ...nor when under this fraction of the footprint's cells are valid: a survey that covers
#: only the edge of a building (Miami's lidar stops at the coast) reads a wall or a
#: neighbour, not the roof.
MIN_FINITE_FRACTION = 0.5
#: Two sources agree within max(AGREE_ABS_M, AGREE_REL × height).
AGREE_ABS_M = 3.0
AGREE_REL = 0.10
#: Footprints are grouped into square tiles of this side for fetching. 3D Tiles over a
#: 300 m box is ~350 tiles / ~25 s; the provider caps a request at 1000 tiles, so a much
#: larger tile would silently drop to a coarser level of detail.
TILE_M = 500.0
#: Margin around a tile's footprints, metres (room for the 3D Tiles ground estimate).
TILE_PAD_M = 60.0
#: Fetch resolution, metres per cell.
RESOLUTION_M = 1.0
#: Truth-height bands for the per-band table (metres, [lo, hi)).
BANDS = ((0.0, 30.0), (30.0, 60.0), (60.0, 100.0), (100.0, math.inf))

BENCHMARK_ROOT = Path(__file__).resolve().parent / "runs" / "benchmark"

#: The benchmark set (user choice 2026-10-03): site name -> survey nDSM provider.
#: US cities read USGS's newest EPT project: Planetary Computer's COPC copy has no tiles
#: over central Miami or Seattle and only Boston's 2013 survey (EPT has 2021).
REGIONS = {
    "miami": "usgs_3dep_ept",
    "chicago": "usgs_3dep_ept",
    "seattle": "usgs_3dep_ept",
    "boston": "usgs_3dep_ept",
    "benidorm": "cnig_mdsn",
    "la_defense": "ign_lidarhd",
    "madrid_cuatro_torres": "cnig_mdsn",
    "prague_pankrac": "cuzk_dmp",
    # Cartagena-like beach-tower cities (user choice 2026-10-08). Their truth is survey-only
    # (no 3D Tiles: monthly cap), so single-source. Sunny Isles is not listed: no 3DEP project
    # covers it (FL_Southeast_B1 stops at ~25.96 N, the Broward line).
    "san_juan": "usgs_3dep_ept",
    "fort_lauderdale": "usgs_3dep_ept",
    "honolulu": "usgs_3dep_ept",
}


def region_key(name: str) -> str:
    """Site-file name for a region as written in a report.

    ``"Rio De Janeiro"`` -> ``rio_de_janeiro``. A saved-region name such as
    ``"Miami, FL (2)"`` maps to the benchmark region it starts with (``miami``).
    """
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    head = key.split(",")[0].split("_(")[0]
    return next((r for r in REGIONS if head == r or head.startswith(r + "_")), head)


# --------------------------------------------------------------------------- geometry


def footprint_key(lonlat: list) -> str:
    """Stable id for a footprint: hash of its ring rounded to ~1 cm."""
    ring = [(round(float(x), 7), round(float(y), 7)) for x, y in lonlat]
    return hashlib.sha1(json.dumps(ring).encode()).hexdigest()[:16]


def _polygon(lonlat: list):
    from shapely.geometry import Polygon
    from shapely.validation import make_valid

    poly = Polygon([(float(x), float(y)) for x, y in lonlat])
    return poly if poly.is_valid else make_valid(poly)


def _erode(poly, metres: float):
    """Shrink a lon/lat polygon by ``metres`` (local equirectangular frame).

    Returns the original polygon when the shrunk one is empty (a footprint narrower
    than twice the buffer): a few edge cells beat no measurement.
    """
    from shapely import affinity

    lat = poly.centroid.y
    kx = M_PER_DEG_LAT * math.cos(math.radians(lat))
    local = affinity.scale(poly, xfact=kx, yfact=M_PER_DEG_LAT, origin=(0, 0))
    shrunk = local.buffer(-metres)
    if shrunk.is_empty or shrunk.area < 1.0:
        return poly
    return affinity.scale(shrunk, xfact=1 / kx, yfact=1 / M_PER_DEG_LAT, origin=(0, 0))


def footprint_stat(ndsm: np.ndarray, transform, poly,
                   percentile: float = ROOF_PERCENTILE,
                   min_cells: int = MIN_CELLS,
                   min_finite_fraction: float = MIN_FINITE_FRACTION
                   ) -> tuple[float | None, int]:
    """``(p<percentile> height, valid cell count)`` of ``ndsm`` inside ``poly``.

    ``ndsm``: height above ground, row 0 = north, ``transform`` an Affine from
    (col, row) to (lon, lat). Height is None when fewer than ``min_cells`` are valid,
    or when under ``min_finite_fraction`` of the cells inside the footprint are.
    """
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds

    h, w = ndsm.shape
    win = from_bounds(*poly.bounds, transform=transform)
    r0 = max(0, int(math.floor(win.row_off)) - 1)
    c0 = max(0, int(math.floor(win.col_off)) - 1)
    r1 = min(h, int(math.ceil(win.row_off + win.height)) + 1)
    c1 = min(w, int(math.ceil(win.col_off + win.width)) + 1)
    if r1 <= r0 or c1 <= c0:
        return None, 0
    sub = ndsm[r0:r1, c0:c1]
    sub_t = transform @ transform.translation(c0, r0)
    inside = geometry_mask([poly], out_shape=sub.shape, transform=sub_t, invert=True)
    if not inside.any():  # footprint smaller than a cell: take the cell under its centre
        inside = geometry_mask([poly], out_shape=sub.shape, transform=sub_t, invert=True,
                               all_touched=True)
    vals = sub[inside]
    n_inside = vals.size
    vals = vals[np.isfinite(vals)]
    if vals.size < min_cells or vals.size < min_finite_fraction * n_inside:
        return None, int(vals.size)
    return float(np.percentile(vals, percentile)), int(vals.size)


# --------------------------------------------------------------------------- tiling


@dataclass(frozen=True)
class Tile:
    bbox: tuple[float, float, float, float]  # (north, south, east, west)
    keys: tuple[str, ...]
    #: Survey part (``survey_part``) every footprint of the tile is read from, or None.
    part: str | None = None


#: Version of the per-footprint survey statistic (records carry it as ``stat``). 1: tiles
#: were the union of the run's footprints, so a footprint's p95 changed with the other
#: footprints in the run (the raster's cell size and phase followed the tile's bbox; Prague:
#: 30 of 79 equal, one off by 46 m). 2: tiles on a fixed world grid (``tile_cell``) with a
#: fixed bbox each (``tile_bbox``), so the raster under a footprint depends on it alone.
STAT_VERSION = 2
#: Shrinks a grid bbox by this many cells per side, so ``lonlat_grid``'s ``ceil`` returns
#: the exact cell count despite float error (1e-6 m: no effect on the cells' positions).
_SNAP_EPS_CELLS = 1e-6


def tile_cell(lon: float, lat: float, tile_m: float = TILE_M) -> tuple[int, int]:
    """``(col, row)`` of the fixed world tile holding (lon, lat).

    Rows are ``tile_m`` of latitude from the equator; each row's columns are ``tile_m``
    wide at the row's centre latitude (an equirectangular frame per row). Nothing depends on
    which other footprints are in the run.
    """
    row = math.floor(lat * M_PER_DEG_LAT / tile_m)
    kx = M_PER_DEG_LAT * math.cos(math.radians((row + 0.5) * tile_m / M_PER_DEG_LAT))
    return math.floor(lon * kx / tile_m), row


def _snapped_bbox(lat_lo_m: float, lat_hi_m: float, lon_lo: float, lon_hi: float,
                  resolution_m: float) -> tuple[float, float, float, float]:
    """``(n, s, e, w)`` whose edges sit on the ``resolution_m`` lattice: latitude edges at
    multiples of ``resolution_m`` metres from the equator, longitude edges at multiples of
    ``resolution_m`` at the bbox's centre latitude (where ``lonlat_grid`` measures its cell
    width). ``lat_*_m`` are metres north of the equator, ``lon_*`` degrees."""
    r = resolution_m
    s_m, n_m = math.floor(lat_lo_m / r) * r, math.ceil(lat_hi_m / r) * r
    kx = M_PER_DEG_LAT * math.cos(math.radians((s_m + n_m) / 2 / M_PER_DEG_LAT))
    w_m, e_m = math.floor(lon_lo * kx / r) * r, math.ceil(lon_hi * kx / r) * r
    eps = _SNAP_EPS_CELLS * r
    return ((n_m - eps) / M_PER_DEG_LAT, (s_m + eps) / M_PER_DEG_LAT,
            (e_m - eps) / kx, (w_m + eps) / kx)


def tile_bbox(cell: tuple[int, int], tile_m: float = TILE_M, pad_m: float = TILE_PAD_M,
              resolution_m: float = RESOLUTION_M) -> tuple[float, float, float, float]:
    """Fixed ``(n, s, e, w)`` of world tile ``cell`` plus ``pad_m``, on the cell lattice."""
    col, row = cell
    kx = M_PER_DEG_LAT * math.cos(math.radians((row + 0.5) * tile_m / M_PER_DEG_LAT))
    return _snapped_bbox(row * tile_m - pad_m, (row + 1) * tile_m + pad_m,
                         (col * tile_m - pad_m) / kx, ((col + 1) * tile_m + pad_m) / kx,
                         resolution_m)


def _own_bbox(poly, pad_m: float, resolution_m: float) -> tuple[float, float, float, float]:
    """Bbox of a footprint too big for its tile: its bounds plus ``pad_m``, on the lattice
    (depends on the footprint alone)."""
    minx, miny, maxx, maxy = poly.bounds
    kx = M_PER_DEG_LAT * math.cos(math.radians((miny + maxy) / 2))
    return _snapped_bbox(miny * M_PER_DEG_LAT - pad_m, maxy * M_PER_DEG_LAT + pad_m,
                         minx - pad_m / kx, maxx + pad_m / kx, resolution_m)


def _inside(bounds, bbox) -> bool:
    minx, miny, maxx, maxy = bounds
    n, s, e, w = bbox
    return w <= minx and maxx <= e and s <= miny and maxy <= n


def tiles_for(polys: dict[str, object], tile_m: float = TILE_M,
              pad_m: float = TILE_PAD_M, resolution_m: float = RESOLUTION_M,
              part=None) -> list[Tile]:
    """Group footprints by the fixed world tile (``tile_cell``) of their centroid; a tile's
    bbox is ``tile_bbox`` (the cell plus ``pad_m``, on the ``resolution_m`` lattice), the
    same whichever footprints are in the run. A footprint reaching past its tile's bbox
    gets a tile of its own (``_own_bbox``). Empty cells produce no tile, so a sparse set of
    scored buildings across a 10 km region costs only the tiles it touches.

    ``part`` (poly -> str | None, e.g. ``survey_part``) splits a cell's footprints by the
    survey part each is read from, so the part follows the footprint, not the tile.

    Stat version 2 (``STAT_VERSION``): a footprint's raster, hence its statistic, no longer
    depends on the other footprints in the run.
    """
    cells: dict[tuple, list[str]] = {}
    own: list[Tile] = []
    for k, p in polys.items():
        c = p.centroid
        cell = tile_cell(c.x, c.y, tile_m)
        pt = part(p) if part else None
        if _inside(p.bounds, tile_bbox(cell, tile_m, pad_m, resolution_m)):
            cells.setdefault((cell, pt or ""), []).append(k)
        else:
            own.append(Tile(_own_bbox(p, pad_m, resolution_m), (k,), pt))
    return [Tile(tile_bbox(cell, tile_m, pad_m, resolution_m), tuple(cells[cell, pt]), pt or None)
            for cell, pt in sorted(cells)] + own


def _grid_dim(bbox, resolution_m: float) -> tuple[int, int]:
    n, s, e, w = bbox
    h = max(1, math.ceil((n - s) * M_PER_DEG_LAT / resolution_m))
    wd = max(1, math.ceil((e - w) * M_PER_DEG_LAT * math.cos(math.radians((n + s) / 2))
                          / resolution_m))
    return h, wd


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
    provider = provider or Google3DProvider()
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
    return BENCHMARK_ROOT / "truth" / f"{region.lower()}.json"


def load_truth_cache(region: str) -> dict:
    p = _truth_cache_path(region)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_truth_cache(region: str, truth: dict) -> None:
    p = _truth_cache_path(region)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(truth, indent=1, sort_keys=True), encoding="utf-8")


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


def footprint_truth(region: str, footprints: dict[str, list], survey_provider: str | None,
                    *, use_tiles: bool = True, resolution_m: float = RESOLUTION_M,
                    tiles_provider=None, refresh: bool = False,
                    tags_m: dict[str, float] | None = None) -> dict[str, dict]:
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
    """
    cache = load_truth_cache(region)
    todo = {k: _erode(_polygon(ring), ERODE_M) for k, ring in footprints.items()
            if refresh or k not in cache}
    fresh: dict[str, dict] = {}
    part = (lambda p: survey_part(survey_provider, p)) if survey_provider else None
    tiles = tiles_for(todo, resolution_m=resolution_m, part=part)
    for i, tile in enumerate(tiles, 1):
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
            rec["status"], truth_m = classify(rec["survey_m"], rec["tiles_m"])
            rec["truth_m"] = None if truth_m is None else round(truth_m, 2)
            rec["stat"] = STAT_VERSION  # absent: version 1 (tiles grouped by the run)
            recs[k] = rec
        if tags_m and flat_mesh({k: r["tiles_m"] for k, r in recs.items()}, tags_m):
            logger.warning("[bench] %s tile %d/%d: 3D Tiles have no building mesh here "
                           "(tagged towers read flat); treated as not covered", region, i,
                           len(tiles))
            recs = {k: _drop_tiles(r) for k, r in recs.items()}
        for k, rec in recs.items():
            fresh[k] = rec
            if complete:
                cache[k] = rec
        if complete:
            save_truth_cache(region, cache)
        else:
            logger.info("[bench] %s tile %d/%d not cached (a source failed or was skipped)",
                        region, i, len(tiles))
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

    provider = survey_provider or REGIONS.get(region_key(region))
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


def load_report(heights_json: Path) -> tuple[str, list[dict]]:
    """``(region, buildings)`` from a skyline ``heights.json``; each building gains
    ``key`` (``footprint_key``). Buildings without a footprint are dropped, and so are rows
    the run did not measure (``measured: False``: tagged buildings listed with their tag)."""
    data = json.loads(Path(heights_json).read_text(encoding="utf-8"))
    out = []
    for b in data.get("buildings", []):
        if b.get("measured") is False:
            continue
        ring = b.get("footprint_lonlat")
        if ring and len(ring) >= 3:
            out.append({**b, "key": footprint_key(ring)})
    return data.get("region", Path(heights_json).parent.name), out


def _pairs(pred: np.ndarray, truth: np.ndarray) -> tuple[int, int]:
    """``(correctly ordered, compared)`` building pairs.

    Pairs whose true heights are within the truth tolerance (max(AGREE_ABS_M, AGREE_REL x the
    taller)) are skipped: the survey cannot say which is taller.
    """
    if pred.size < 2:
        return 0, 0
    i, j = np.triu_indices(pred.size, 1)
    dt = truth[i] - truth[j]
    keep = np.abs(dt) > np.maximum(AGREE_ABS_M, AGREE_REL * np.maximum(truth[i], truth[j]))
    good = np.sign(pred[i] - pred[j])[keep] == np.sign(dt[keep])
    return int(good.sum()), int(keep.sum())


def relative_metrics(pred: np.ndarray, truth: np.ndarray) -> dict:
    """Scale-free agreement: pair order (share of building pairs in the right order) and
    Spearman rank correlation. Unchanged by any camera-height offset or overall scale, which is
    why F-WEB2 photos (unknown camera height) are judged on them."""
    from scipy.stats import spearmanr

    ok, n = _pairs(pred, truth)
    rho = float(spearmanr(pred, truth).statistic) if pred.size >= 3 else None
    return {"n": int(pred.size), "pairs": n,
            "pair_order": round(ok / n, 3) if n else None,
            "spearman": None if rho is None or math.isnan(rho) else round(rho, 3)}


def per_view_relative(buildings: list[dict], truth: dict[str, dict], min_buildings: int = 5) -> dict:
    """Relative metrics inside each single view (one image, one camera), pooled.

    ``pair_order`` pools pairs over all views with at least ``min_buildings`` confirmed
    buildings; ``median_spearman`` is the median over those views.
    """
    by_view: dict[str, list[tuple[float, float]]] = {}
    for b in buildings:
        t = truth.get(b["key"])
        if not t or t["status"] != "confirmed":
            continue
        for v in b.get("views") or []:
            if v.get("height_m") is not None:
                by_view.setdefault(v["view_name"], []).append((float(v["height_m"]), t["truth_m"]))
    ok = n = 0
    rhos = []
    used = 0
    for rows in by_view.values():
        if len(rows) < min_buildings:
            continue
        p, t = (np.array(c) for c in zip(*rows, strict=True))
        k, m = _pairs(p, t)
        ok, n, used = ok + k, n + m, used + 1
        r = relative_metrics(p, t)["spearman"]
        if r is not None:
            rhos.append(r)
    return {"views": used, "pairs": n, "pair_order": round(ok / n, 3) if n else None,
            "median_spearman": round(float(np.median(rhos)), 3) if rhos else None}


def _errors(pred: np.ndarray, truth: np.ndarray) -> dict:
    if pred.size == 0:
        return {"n": 0}
    err = pred - truth
    rel = np.abs(err) / np.maximum(truth, 1.0)
    return {
        "n": int(pred.size),
        "mae_m": round(float(np.mean(np.abs(err))), 2),
        "median_ae_m": round(float(np.median(np.abs(err))), 2),
        "bias_m": round(float(np.mean(err)), 2),
        "within_15pct": round(float(np.mean(rel <= 0.15)), 3),
        "within_25pct": round(float(np.mean(rel <= 0.25)), 3),
    }


def street_view_buildings(buildings: list[dict]) -> list[dict] | None:
    """The run's buildings with the Street View height put back where T28 withheld it
    (``street_view_m``), so the benchmark can score both; None when nothing was withheld."""
    if not any("street_view_m" in b for b in buildings):
        return None
    return [{**b, "effective_height_m": b["street_view_m"]} if "street_view_m" in b else b
            for b in buildings]


def withheld_buildings(buildings: list[dict], fallback: str = "constant",
                       region: str | None = None) -> list[dict]:
    """A pre-T28 report's buildings as T28 would publish them: untagged ones (height
    source not ``osm_tag`` / ``osm_levels``) get the fallback, the Street View value moved
    to ``street_view_m``. Lets ``--score-only`` show the effect on old runs.

    ``fallback``: ``"constant"`` (``UNTAGGED_FALLBACK_M``) or ``"model"`` (the T41 height
    prior, trained without ``region`` so the score stays out-of-sample; neighbours are
    the report's buildings, as at run time)."""
    from ._core.height import TAGGED_SOURCES, UNTAGGED_FALLBACK_M

    heights = [UNTAGGED_FALLBACK_M] * len(buildings)
    src = "default"
    if fallback == "model":
        from .untagged_prior import predict, report_rows

        heights = [float(h) for h in predict(report_rows(buildings), exclude_city=region)]
        src = "prior_gbm"
    out = []
    for b, h in zip(buildings, heights, strict=True):
        if b.get("height_source") in TAGGED_SOURCES or "street_view_m" in b:
            out.append(b)
        else:
            out.append({**b, "street_view_m": b.get("effective_height_m"),
                        "effective_height_m": h, "effective_height_source": f"withheld:{src}"})
    return out


def score_buildings(buildings: list[dict], truth: dict[str, dict],
                    pred_field: str = "effective_height_m") -> dict:
    """Score a run's buildings against ``truth`` (confirmed buildings only).

    Returns overall errors, errors per truth-height band, per view count and per seed
    count, the status counts, and the OSM-tag yardstick (how far the tags themselves
    sit from confirmed truth, and the run scored against tags).

    ``pred_field``: the height scored. The ``10_benchmark`` headline scores
    ``no_survey_height_m``, the survey-blind answer (F-SKY26 2c): a survey height is
    measured the same way as the truth, so scoring it would be circular. A row without
    ``pred_field`` (a report from before 2c) is scored on ``effective_height_m``, which was
    survey-blind then.
    """
    rows = []
    status_counts: dict[str, int] = {}
    for b in buildings:
        t = truth.get(b["key"])
        status = t["status"] if t else "unmeasured"
        status_counts[status] = status_counts.get(status, 0) + 1
        pred = b.get(pred_field, b.get("effective_height_m"))
        if status == "confirmed" and pred is not None:
            rows.append((float(pred), float(t["truth_m"]), int(b.get("n_views") or 0),
                         int(b.get("n_seeds") or 0), b.get("height_tag_m"),
                         b.get("height_source")))
    if not rows:
        return {"n_buildings": len(buildings), "status": status_counts, "overall": {"n": 0}}
    pred = np.array([r[0] for r in rows])
    tru = np.array([r[1] for r in rows])
    nv = np.array([r[2] for r in rows])
    tg_mask = np.array([r[4] is not None and r[5] not in (None, "default") for r in rows])
    ns = np.array([r[3] for r in rows])
    out = {
        "n_buildings": len(buildings),
        "status": status_counts,
        "overall": _errors(pred, tru),
        "bands": {(f"{lo:.0f}+m" if math.isinf(hi) else f"{lo:.0f}-{hi:.0f}m"):
                  _errors(pred[(tru >= lo) & (tru < hi)], tru[(tru >= lo) & (tru < hi)])
                  for lo, hi in BANDS},
        "views": {lab: _errors(pred[m], tru[m]) for lab, m in
                  (("1", nv == 1), ("2-3", (nv >= 2) & (nv <= 3)), ("4+", nv >= 4))},
        "seeds": {lab: _errors(pred[m], tru[m]) for lab, m in
                  (("1", ns <= 1), ("2+", ns >= 2))},
        # SKYLINE_TAG_FILTER (on by default) drops per-view estimates far from a building's
        # OSM height tag, so tagged buildings are helped by their tag; untagged ones show
        # the pipeline unaided.
        "osm_tag": {lab: _errors(pred[m], tru[m]) for lab, m in
                    (("tagged", tg_mask), ("untagged", ~tg_mask))},
        # Scale-free: does the taller building come out taller? City-wide and inside each view.
        "relative": {"city": relative_metrics(pred, tru),
                     "per_view": per_view_relative(buildings, truth)},
    }
    tagged = [(r[0], r[1], float(r[4])) for r in rows
              if r[4] is not None and r[5] not in (None, "default")]
    if tagged:
        tp, tt, tg = (np.array(c) for c in zip(*tagged, strict=True))
        out["osm_tags"] = {"tag_vs_truth": _errors(tg, tt), "run_vs_tag": _errors(tp, tg),
                           "run_vs_truth_same_buildings": _errors(tp, tt)}
    return out


def label_tiers(buildings: list[dict], measured_seeds=()) -> list[dict]:
    """``buildings`` with a verification tier on rows that lack one (a report from before
    F-SKY26 2a), worked out from the row as ``withhold_untagged_street_view`` would have:
    the tag when the tag is published, the prior when a fallback is, else the drone seeds
    (``measured_seeds``) and, where Street View is still published, its seeds. No
    ``prior_disagrees``: the prior's height is not in an old report."""
    from ._core.height import TAGGED_SOURCES
    from ._core.tiers import reading, tier_fields

    out = []
    for b in buildings:
        if b.get("tier"):
            out.append(b)
            continue
        src = b.get("effective_height_source") or ""
        seeds = {k: v for k, v in (b.get("per_seed_median_m") or {}).items() if v is not None}
        drone = [reading("drone", v, k) for k, v in seeds.items() if k in measured_seeds]
        street = [reading("street", v, k) for k, v in seeds.items() if k not in measured_seeds]
        tagged = b.get("height_source") in TAGGED_SOURCES and src == b.get("height_source")
        if tagged or src == "withheld:elevated":
            readings = drone
        elif src.startswith("withheld:"):
            readings = []
        else:
            readings = drone + street
        f = tier_fields(readings, published_m=b.get("effective_height_m"),
                        tag_m=b.get("height_tag_m") if tagged else None,
                        prior_source=src.split(":", 1)[1] if src.startswith("withheld:") else None)
        out.append({**b, **f})
    return out


def score_survey_rows(buildings: list[dict], truth: dict[str, dict]) -> dict:
    """Rows that publish a survey height (tier ``survey``, F-SKY26 2d), scored against the
    3D Tiles reading only (``tiles_m``, flat meshes excluded): the survey half of the truth
    is the same measurement. Success criterion: within 25 % of ``tiles_m`` 90 % of the time."""
    rows = [(float(b["effective_height_m"]), float(t["tiles_m"])) for b in buildings
            if b.get("tier") == "survey" and b.get("effective_height_m") is not None
            and (t := truth.get(b["key"])) and t.get("tiles_m") is not None
            and not t.get("tiles_flat")]
    n_survey = sum(1 for b in buildings if b.get("tier") == "survey")
    if not rows:
        return {"n_survey_rows": n_survey, "vs_tiles": {"n": 0}}
    pred, tiles = (np.array(c) for c in zip(*rows, strict=True))
    return {"n_survey_rows": n_survey, "vs_tiles": _errors(pred, tiles)}


def score_by_tier(buildings: list[dict], truth: dict[str, dict],
                  pred_field: str = "effective_height_m", tier_field: str = "tier") -> dict:
    """Errors per verification tier: do the tiers mean what they say? Within 25 % should
    fall in tier order (``verified_2`` at least 0.85). Non-survey tiers are scored on
    confirmed truth; ``survey`` rows against ``tiles_m`` only (``score_survey_rows``).
    Rows without a tier (run ``label_tiers`` first) count as ``unlabelled``."""
    from ._core.tiers import TIERS

    by: dict[str, list[tuple[float, float]]] = {}
    for b in buildings:
        tier = b.get(tier_field) or "unlabelled"
        pred = b.get(pred_field, b.get("effective_height_m"))
        t = truth.get(b["key"])
        if pred is None or not t:
            continue
        if tier == "survey":
            if t.get("tiles_m") is None or t.get("tiles_flat"):
                continue
            ref = t["tiles_m"]
        elif t["status"] == "confirmed":
            ref = t["truth_m"]
        else:
            continue
        by.setdefault(tier, []).append((float(pred), float(ref)))
    out = {}
    for tier in (*TIERS, "unlabelled"):
        if tier in by:
            pred, ref = (np.array(c) for c in zip(*by[tier], strict=True))
            out[tier] = _errors(pred, ref)
    return out


# --------------------------------------------------------------------------- published heights

#: Words that name a kind of building, not a building: ignored when matching names.
_GENERIC_WORDS = {"hotel", "edificio", "torre", "tower", "building", "centro", "comercial",
                  "cartagena", "indias", "residencial", "apartamentos", "condominio"}


def _name_words(name: str | None) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower())
            if len(w) >= 4 and w not in _GENERIC_WORDS}


def match_known_tower(lat: float, lon: float, height_m: float, name: str, rows: list[dict],
                      radius_m: float = 60.0) -> tuple[dict | None, str]:
    """The heights.json row that a published height belongs to, and why.

    The nearest centroid is not enough: a hotel's convention-centre podium is mapped around
    its tower, and its centroid can be the nearer one (2026-10-06, Cartagena: the Estelar
    podium, read 57 m, scored against the tower's 202 m) -- and the podium can carry the
    hotel's name while the tower is unnamed. Among rows within ``radius_m``: a named building
    that is not a podium first, then an OSM tag within 35 % of the published height, then rows
    that do not contain another candidate (a podium), then a name, then distance.
    """
    from shapely.geometry import Point

    cands, rejected = [], 0
    for r in rows:
        d = math.hypot((r["centroid_lat"] - lat) * M_PER_DEG_LAT,
                       (r["centroid_lon"] - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat)))
        if d > radius_m:
            continue
        tag = r.get("height_tag_m")
        if tag and not 0.5 <= tag / height_m <= 2.0:
            rejected += 1                       # mapped with another height: not this tower
            continue                            # (Miami: Panorama Tower 249 m -> a 16 m row)
        cands.append((d, r))
    if not cands:
        return None, (f"no plausible match ({rejected} tagged far off)" if rejected
                      else "none within radius")
    polys = {id(r): _polygon(r["footprint_lonlat"]) if r.get("footprint_lonlat") else None
             for _, r in cands}
    words = _name_words(name)

    def rank(c):
        d, r = c
        named = bool(words & _name_words(r.get("name")))
        tag = r.get("height_tag_m")
        tag_ok = bool(tag) and 1 / 1.35 <= tag / height_m <= 1.35
        p = polys[id(r)]
        podium = p is not None and any(
            q is not r and p.contains(Point(q["centroid_lon"], q["centroid_lat"]))
            for _, q in cands)
        # a building named like the tower and not a podium first: tags can be wrong in a way
        # that matches a neighbour (Cartagena: Ravello tagged 160, Nautica's published height,
        # so Nautica was scored on Ravello's footprint, 2026-10-06)
        return (not (named and not podium), not tag_ok, podium, not named, d)

    best = min(cands, key=rank)
    named_tower, no_tag, podium, no_name, _d = rank(best)
    why = ("name" if not named_tower else "OSM tag" if not no_tag else "name" if not no_name else
           "nearest (not a podium)" if not podium else "nearest")
    return best[1], why


def score_known(heights_json: Path, region: str, elevated_seeds: tuple[str, ...] = ()) -> dict:
    """The report's heights against the site's published heights (``sites/<region>.json``
    ``known_heights_m``), each matched by :func:`match_known_tower`. Rows: published, the
    report's height and source, Street View, and the drone seeds' median."""
    site = Path(__file__).parent / "sites" / f"{region.lower()}.json"
    if not site.exists():
        return {}
    cfg = json.loads(site.read_text(encoding="utf-8-sig"))
    known = {k: v for k, v in (cfg.get("known_heights_m") or {}).items()
             if not k.startswith("_") and isinstance(v, dict)}
    if not known:
        return {}
    elevated = tuple(elevated_seeds) or tuple(cfg.get("elevated_seeds") or ())
    rows = [b for b in json.loads(Path(heights_json).read_text(encoding="utf-8"))["buildings"]
            if b.get("centroid_lat") is not None]
    out = []
    for name, v in known.items():
        r, why = match_known_tower(float(v["lat"]), float(v["lon"]), float(v["height_m"]), name, rows)
        row = {"name": name, "published_m": float(v["height_m"]), "match": why}
        if r is not None:
            drone = [h for s, h in (r.get("per_seed_median_m") or {}).items() if s in elevated]
            row.update(feature_id=r.get("feature_id"), osm_name=r.get("name"),
                       tag_m=r.get("height_tag_m"), report_m=r.get("effective_height_m"),
                       report_source=r.get("effective_height_source"),
                       street_view_m=r.get("street_view_m"),
                       drone_m=float(np.median(drone)) if drone else None)
        out.append(row)

    def mae(key):
        e = [abs(x[key] - x["published_m"]) for x in out if x.get(key) is not None]
        return {"n": len(e), "mae_m": float(np.mean(e)) if e else None,
                "median_ae_m": float(np.median(e)) if e else None}

    return {"towers": out, "report": mae("report_m"), "drone": mae("drone_m"),
            "street_view": mae("street_view_m"), "osm_tag": mae("tag_m")}
