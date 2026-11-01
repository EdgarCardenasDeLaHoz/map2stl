"""Part of ``city2stl.skyline.benchmark`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from geo2stl.cache import (  # noqa: F401 - file_lock re-exported (bm.file_lock)
    atomic_write_json,
    file_lock,
)
from geo2stl.geo import M_PER_DEG_SPHERE as M_PER_DEG_LAT

from .local_dirs import local_dir

#: Inward buffer before sampling a footprint, metres.
ERODE_M = 1.0


#: Roof statistic: percentile of the nDSM cells inside the footprint. The headline truth
#: (``survey_m``, ``truth_m``); the others in ``ROOF_STATS`` are stored beside it.
ROOF_PERCENTILE = 95.0


#: Roof statistics stored per footprint (review 2026-10-09 item 1): p95 stays the headline;
#: the roof top matches silhouette readings (drone, Street View, lean), p70 floor counts (3DBAG
#: publishes median / p70 / max, HalifaxDT p95). ``(name, percentile)``. The raw max is
#: stored as asked, but single noise cells make it useless as truth on some surveys (Miami's
#: 2019 topobathy: max over p95 + 20 m on 24 % of 914 records, up to 294 m; Honolulu 1.7 %),
#: so the silhouettes' statistic is p99 (2.8 % and 1.0 %).
ROOF_STATS = (("p50", 50.0), ("p70", 70.0), ("p90", 90.0), ("p95", 95.0), ("p99", 99.0),
              ("max", 100.0))


#: Version of the stored roof statistics (records carry it as ``roof_stats``; absent: only
#: the p95). Bumped when ``ROOF_STATS`` / the ground ring change, never for the p95 itself.
#: 1: p50 / p70 / p90 / p95 / max; 2: + p99.
ROOF_STATS_VERSION = 2


#: Ground statistic: p5 of the nDSM in the ring from the footprint outline to this many
#: metres outside it (3DBAG: ground = p5 of the ground points within 4 m). The nDSM is height
#: above the survey's own ground model, so this reads ~0 where that model fits the street and
#: the plinth height where the building stands on a raised deck.
GROUND_RING_M = 4.0


GROUND_PERCENTILE = 5.0


#: Which stored statistic each reading kind is measured against (method-matched truth,
#: review §3 item 1): silhouettes reach the top of the roof (p99, the spike-robust top; see
#: ``ROOF_STATS``), floor counts its main level. Kinds not listed: the headline p95.
METHOD_STAT = {"drone": "p99", "street": "p99", "photo": "p99", "lean": "p99",
               "multiview": "p99", "stereo": "p99", "shadow": "p99", "floors": "p70"}


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


#: Truth-height bands for the per-band table (metres, [lo, hi)). The headline's bands, kept
#: as they are so old summaries stay comparable; the review's and EUBUCCO's are below.
BANDS = ((0.0, 30.0), (30.0, 60.0), (60.0, 100.0), (100.0, math.inf))


#: The review's bands (2026-10-09 §4.4): <15 / 15-40 / 40-100 / >100 m.
REVIEW_BANDS = ((0.0, 15.0), (15.0, 40.0), (40.0, 100.0), (100.0, math.inf))


#: EUBUCCO's evaluation bands (0-5 / 5-10 / 10-20 / 20+ m), for comparison with its held-out
#: MAEs (1.24 / 1.21 / 3.02 / 11.68 m).
EUBUCCO_BANDS = ((0.0, 5.0), (5.0, 10.0), (10.0, 20.0), (20.0, math.inf))


#: Camera-to-building distance bands for drone (and Street View) readings, metres.
DISTANCE_BANDS = ((0.0, 500.0), (500.0, 1000.0), (1000.0, math.inf))


#: "tol": a height is right when within max(TOL_REL x truth, TOL_ABS_M) of the truth
#: (review §3 shared rules: 25 %, with a 2 m floor so a 4 m house is not judged to 1 m).
TOL_REL = 0.25


TOL_ABS_M = 2.0


BENCHMARK_ROOT = Path(__file__).resolve().parent / "runs" / "benchmark"  # truth/ lives here


#: Dated run folders (single-use, large): local when SKYLINE_LOCAL_DIR is set.


def benchmark_runs() -> Path:
    """Dated run folders, resolved at call time so a ``SkylineConfig.local_dir`` reaches it."""
    return local_dir("benchmark")


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
    # Andalucia: CNIG MDSn building class first (2026-10-21; REDIAM MDHN suppresses building cells
    # and read ~5 m low), REDIAM 1 m LiDAR height (2020-21) only where CNIG has no building
    # (``FALLBACKS``), as ``height/survey_fill.py`` does.
    "granada": "cnig_mdsn",
}


#: Region -> second survey provider, read only for footprints the primary shows no building on
#: (``survey_heights.survey_footprint_heights(fallback=...)``). Other regions have none.
FALLBACKS = {"granada": "rediam_mdhn"}


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
    vals, n_inside = _cells(ndsm, transform, poly)
    if vals is None:
        return None, 0
    if vals.size < min_cells or vals.size < min_finite_fraction * n_inside:
        return None, int(vals.size)
    return float(np.percentile(vals, percentile)), int(vals.size)


def _cells(ndsm: np.ndarray, transform, poly) -> tuple[np.ndarray | None, int]:
    """``(finite nDSM values inside poly, cells inside poly)``; ``(None, 0)`` when the polygon
    misses the raster. A polygon smaller than a cell takes the cell under it."""
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
    return vals[np.isfinite(vals)], int(vals.size)


def _buffer_m(poly, metres: float):
    """``poly`` grown by ``metres`` (local equirectangular frame), in lon/lat."""
    from shapely import affinity

    lat = poly.centroid.y
    kx = M_PER_DEG_LAT * math.cos(math.radians(lat))
    local = affinity.scale(poly, xfact=kx, yfact=M_PER_DEG_LAT, origin=(0, 0))
    return affinity.scale(local.buffer(metres), xfact=1 / kx, yfact=1 / M_PER_DEG_LAT,
                          origin=(0, 0))


def ground_ring(outline, ring_m: float = GROUND_RING_M):
    """The ring from a footprint's outline (not eroded) to ``ring_m`` outside it."""
    return _buffer_m(outline, ring_m).difference(outline)


def footprint_stats(ndsm: np.ndarray, transform, poly, outline=None,
                    min_cells: int = MIN_CELLS,
                    min_finite_fraction: float = MIN_FINITE_FRACTION) -> dict:
    """Every stored statistic of one footprint: ``{roof_m: {p50, p70, p90, p95, max} | None,
    cells, ground_p5_m, ground_cells, roof_stats}``.

    ``poly``: the eroded footprint (as ``footprint_stat`` reads it; ``roof_m["p95"]`` is the
    same number as ``footprint_stat``). ``outline``: the footprint before erosion, for the
    ground ring (``ground_ring``); without it the ground is not measured. ``roof_m`` is None
    when ``footprint_stat`` would return None; the ground needs ``min_cells`` valid cells.
    """
    out: dict = {"roof_m": None, "cells": 0, "ground_p5_m": None, "ground_cells": 0,
                 "roof_stats": ROOF_STATS_VERSION}
    vals, n_inside = _cells(ndsm, transform, poly)
    if vals is not None:
        out["cells"] = int(vals.size)
        if vals.size >= min_cells and vals.size >= min_finite_fraction * n_inside:
            roof = {name: float(np.percentile(vals, q)) for name, q in ROOF_STATS if q < 100}
            roof["max"] = float(vals.max())
            # the headline p95 exactly as footprint_stat computes it
            roof["p95"] = float(np.percentile(vals, ROOF_PERCENTILE))
            out["roof_m"] = {name: round(roof[name], 2) for name, _ in ROOF_STATS}
    if outline is not None:
        ring = ground_ring(outline)
        g, _ = (None, 0) if ring.is_empty else _cells(ndsm, transform, ring)
        if g is not None:
            out["ground_cells"] = int(g.size)
            if g.size >= min_cells:
                out["ground_p5_m"] = round(float(np.percentile(g, GROUND_PERCENTILE)), 2)
    return out


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
