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
mostly ``building:levels`` × 3.2 m plus one level for the roof (``city2stl/heights.py``
``METRES_PER_LEVEL``). Why not one source: either alone hides its own errors
(decision: ``docs/decisions/building-heights.md``, 2026-10-03). Where no 3D Tiles can be read
(the monthly cap: San Juan, Honolulu, Fort Lauderdale) the truth is the survey alone,
``survey_only``: scored by the band tables as single-source and reported apart.

Each survey record also stores p50 / p70 / p90 / p95 / max roof heights and a ground p5
(``footprint_stats``, review 2026-10-09 item 1), so a reading can be scored against the statistic
it measures (``METHOD_STAT``), and an age flag from OSM ``start_date`` (``temporal_flag``).

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
#: Roof statistic: percentile of the nDSM cells inside the footprint. The headline truth
#: (``survey_m``, ``truth_m``); the others in ``ROOF_STATS`` are stored beside it.
ROOF_PERCENTILE = 95.0
#: Roof statistics stored per footprint (review 2026-10-09 item 1): p95 stays the headline;
#: max matches silhouette readings (drone, Street View, lean), p70 floor counts (3DBAG
#: publishes median / p70 / max, HalifaxDT p95). ``(name, percentile)``.
ROOF_STATS = (("p50", 50.0), ("p70", 70.0), ("p90", 90.0), ("p95", 95.0), ("max", 100.0))
#: Version of the stored roof statistics (records carry it as ``roof_stats``; absent: only
#: the p95). Bumped when ``ROOF_STATS`` / the ground ring change, never for the p95 itself.
ROOF_STATS_VERSION = 1
#: Ground statistic: p5 of the nDSM in the ring from the footprint outline to this many
#: metres outside it (3DBAG: ground = p5 of the ground points within 4 m). The nDSM is height
#: above the survey's own ground model, so this reads ~0 where that model fits the street and
#: the plinth height where the building stands on a raised deck.
GROUND_RING_M = 4.0
GROUND_PERCENTILE = 5.0
#: Which stored statistic each reading kind is measured against (method-matched truth,
#: review §3 item 1): silhouettes reach the top of the roof, floor counts its main level.
#: Kinds not listed are scored against the headline p95.
METHOD_STAT = {"drone": "max", "street": "max", "street_view": "max", "lean": "max",
               "multiview": "max", "stereo": "max", "shadow": "max", "floors": "p70"}
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


class file_lock:  # noqa: N801 - used as a context manager, like open()
    """An exclusive lock on ``<path>.lock`` across processes, for a read-merge-write of a cache
    file (two survey readers of one region). Blocks, polling every 0.1 s, up to ``timeout_s``."""

    def __init__(self, path: Path, timeout_s: float = 120.0):
        self.path = Path(f"{path}.lock")
        self.timeout_s = timeout_s
        self.fd = None

    def __enter__(self):
        import os
        import time

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        t0 = time.monotonic()
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if time.monotonic() - t0 > self.timeout_s:
                    os.close(self.fd)
                    raise TimeoutError(f"{self.path} still locked after "
                                       f"{self.timeout_s:.0f} s") from exc
                time.sleep(0.1)

    def __exit__(self, *exc):
        import os

        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(self.fd)
        return False


def write_json_atomic(path: Path, obj, attempts: int = 10) -> None:
    """Write ``obj`` as JSON to ``path`` through a temporary file and a rename, so a reader in
    another process never sees half a file (retried while Windows holds the target open)."""
    import os
    import time

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True), encoding="utf-8")
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2 * (i + 1))


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


def load_report(heights_json: Path, include_unmeasured: bool = False) -> tuple[str, list[dict]]:
    """``(region, buildings)`` from a skyline ``heights.json``; each building gains
    ``key`` (``footprint_key``). Buildings without a footprint are dropped, and so are rows
    the run did not measure (``measured: False``: tagged buildings listed with their tag)
    unless ``include_unmeasured`` (the band tables' "published" scope)."""
    data = json.loads(Path(heights_json).read_text(encoding="utf-8"))
    out = []
    for b in data.get("buildings", []):
        if b.get("measured") is False and not include_unmeasured:
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


def per_view_relative(buildings: list[dict], truth: dict[str, dict], min_buildings: int = 5,
                      statuses: tuple[str, ...] = ("confirmed",)) -> dict:
    """Relative metrics inside each single view (one image, one camera), pooled.

    ``pair_order`` pools pairs over all views with at least ``min_buildings`` confirmed
    buildings (``statuses``: the truth statuses scored); ``median_spearman`` is the median over
    those views.
    """
    by_view: dict[str, list[tuple[float, float]]] = {}
    for b in buildings:
        t = truth.get(b["key"])
        if not t or t["status"] not in statuses:
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
                    pred_field: str = "effective_height_m",
                    statuses: tuple[str, ...] = ("confirmed",)) -> dict:
    """Score a run's buildings against ``truth`` (confirmed buildings only; ``statuses``
    widens that, e.g. ``("survey_only",)`` for a single-source region, ``score_report``).

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
        if status in statuses and pred is not None and t["truth_m"] is not None:
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
                     "per_view": per_view_relative(buildings, truth, statuses=statuses)},
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


# --------------------------------------------------------------------------- band tables (item 1)
# Review 2026-10-09 §3 item 1 / §4.4: a benchmark that can prove or refuse a change, by height
# band and by tier. Beside the headline (``score_buildings``, unchanged), not instead of it.

#: Truth modes of a region's scored footprints (``truth_mode``).
TWO_SOURCE = "two_source"        # survey + 3D Tiles, cross-checked: ``confirmed`` rows score
SINGLE_SOURCE = "single_source"  # survey only (no 3D Tiles read): ``survey_only`` rows score
NO_TRUTH = "none"
#: Tiers whose rows claim two independent readings agree (``verified_2``; ``corroborated`` is
#: the review's proposed name for it, item 6): a wrong one is a false corroboration.
CORROBORATED_TIERS = ("verified_2", "corroborated")
#: Tiers that publish an image reading (``single`` and the corroborated ones).
READING_TIERS = ("single", *CORROBORATED_TIERS)


def _band_label(lo: float, hi: float, kind: str = "review") -> str:
    if kind == "review":
        return (f"<{hi:.0f}" if lo == 0 else f">{lo:.0f}" if math.isinf(hi)
                else f"{lo:.0f}-{hi:.0f}")
    return f"{lo:.0f}+" if math.isinf(hi) else f"{lo:.0f}-{hi:.0f}"


REVIEW_LABELS = tuple(_band_label(lo, hi) for lo, hi in REVIEW_BANDS)
EUBUCCO_LABELS = tuple(_band_label(lo, hi, "eubucco") for lo, hi in EUBUCCO_BANDS)
DISTANCE_LABELS = tuple(_band_label(lo, hi) for lo, hi in DISTANCE_BANDS)


def band_of(value: float, bands=REVIEW_BANDS, labels=REVIEW_LABELS) -> str | None:
    """The label of the ``[lo, hi)`` band holding ``value``."""
    for (lo, hi), lab in zip(bands, labels, strict=True):
        if lo <= value < hi:
            return lab
    return None


def tol_m(truth):
    """The "tol" yardstick at ``truth`` metres: max(``TOL_REL`` x truth, ``TOL_ABS_M``)."""
    return np.maximum(TOL_REL * np.asarray(truth, float), TOL_ABS_M)


def band_errors(pred, truth) -> dict:
    """The review's metrics (§4.4) of predictions against truth: n, MAE, median AE, signed
    bias, P90 AE, ``tol`` (share within max(25 %, 2 m) of truth) and the shares off by more
    than 5 m and 10 m."""
    pred, truth = np.asarray(pred, float), np.asarray(truth, float)
    if pred.size == 0:
        return {"n": 0}
    err = pred - truth
    ae = np.abs(err)
    return {"n": int(pred.size), "mae_m": round(float(ae.mean()), 2),
            "median_ae_m": round(float(np.median(ae)), 2), "bias_m": round(float(err.mean()), 2),
            "p90_ae_m": round(float(np.percentile(ae, 90)), 2),
            "tol": round(float(np.mean(ae <= tol_m(truth))), 3),
            "over_5m": round(float(np.mean(ae > 5.0)), 3),
            "over_10m": round(float(np.mean(ae > 10.0)), 3)}


def truth_mode(buildings: list[dict], truth: dict[str, dict]) -> str:
    """``TWO_SOURCE`` when any of the report's footprints has a 3D Tiles reading (confirmed,
    disputed or tiles-only truth), else ``SINGLE_SOURCE`` when any has survey-only truth, else
    ``NO_TRUTH``. A single-source region (San Juan, Honolulu, Fort Lauderdale: no 3D Tiles,
    monthly cap) scores its survey-only records, reported apart."""
    st = {truth[b["key"]]["status"] for b in buildings if b["key"] in truth}
    if st & {"confirmed", "disputed", "tiles_only"}:
        return TWO_SOURCE
    return SINGLE_SOURCE if "survey_only" in st else NO_TRUTH


def scoring_truth(truth: dict[str, dict], mode: str) -> tuple[dict[str, dict], dict]:
    """``(truth records scored, exclusions)``. Two-source: ``confirmed`` only (the headline's
    rule). Single-source: ``survey_only``, less the records whose building may postdate the
    survey (``temporal`` ``may_postdate``, from OSM ``start_date``): the survey may show a
    construction site."""
    want = "confirmed" if mode == TWO_SOURCE else "survey_only"
    out, excluded = {}, {"may_postdate": 0}
    for k, r in truth.items():
        if r.get("status") != want or r.get("truth_m") is None:
            continue
        if mode == SINGLE_SOURCE and r.get("temporal") == TEMPORAL_MAY_POSTDATE:
            excluded["may_postdate"] += 1
            continue
        out[k] = r
    return out, excluded


def attach_roof_stats(truth: dict[str, dict], survey_cache: dict[str, dict]) -> dict[str, dict]:
    """``truth`` with each record's stored roof statistics (``roof_m``) taken from the survey
    cache (``survey_heights``) when the record has none of its own."""
    out = {}
    for k, r in truth.items():
        roof = r.get("roof_m") or r.get("survey_roof_m")
        if roof is None and (s := survey_cache.get(k)) and s.get("roof_m"):
            r = {**r, "roof_m": s["roof_m"], "ground_p5_m": s.get("ground_p5_m")}
        out[k] = r
    return out


def matched_truth(rec: dict, stat: str) -> float | None:
    """The truth for a reading measured against roof statistic ``stat`` (``METHOD_STAT``):
    the headline ``truth_m`` moved by the survey's (stat - p95). None without stored stats."""
    if stat == "p95":
        return float(rec["truth_m"])
    roof = rec.get("roof_m") or rec.get("survey_roof_m") or {}
    if roof.get(stat) is None or roof.get("p95") is None:
        return None
    return float(rec["truth_m"]) + float(roof[stat]) - float(roof["p95"])


def seed_positions(region: str) -> dict[str, tuple[float, float]]:
    """``seed name -> (lat, lon)`` for the region's seeds as a run names them: ``seed_<i>``
    from the site's ``seed_urls`` (1-based, as ``region_pdf`` numbers them) and the persisted
    auto-proposals (``runs/auto_proposals/<region>.json``). Web / Commons seeds have none."""
    from .streetview_io import _parse_streetview_url

    out: dict[str, tuple[float, float]] = {}
    site = Path(__file__).parent / "sites" / f"{region.lower()}.json"
    if site.exists():
        cfg = json.loads(site.read_text(encoding="utf-8-sig"))
        for i, url in enumerate(cfg.get("seed_urls") or []):
            got = _parse_streetview_url(str(url))
            if got is not None:
                out[f"seed_{i + 1}"] = (float(got[0]), float(got[1]))
    auto = BENCHMARK_ROOT.parent / "auto_proposals" / f"{region.lower()}.json"
    if auto.exists():
        d = json.loads(auto.read_text(encoding="utf-8"))
        for p in (d.get("points") if isinstance(d, dict) else d) or []:
            if p.get("name") and p.get("lat") is not None:
                out.setdefault(p["name"], (float(p["lat"]), float(p["lon"])))
    return out


def _dist_m(a: tuple[float, float], lat: float, lon: float) -> float:
    return math.hypot((a[0] - lat) * M_PER_DEG_LAT,
                      (a[1] - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat)))


def method_readings(b: dict, elevated=(), seeds: dict | None = None) -> list[dict]:
    """Every reading a report row carries, ``{method, value_m, dist_m}``: ``drone`` (an
    elevated seed's median) and ``street`` (another seed's; ``photo`` for web / Commons seeds)
    with the camera's distance to the building (None when the seed's position is unknown),
    ``floors`` (count x storey), the satellite readings by kind, the OSM tag (``osm_tag`` /
    ``osm_levels``) and ``prior`` on rows that publish it. ``dist_m`` is None for methods
    without a camera position."""
    from ._core.height import TAGGED_SOURCES

    seeds = seeds or {}
    lat, lon = b.get("centroid_lat"), b.get("centroid_lon")
    out = []
    for name, v in (b.get("per_seed_median_m") or {}).items():
        if v is None:
            continue
        kind = ("drone" if name in elevated else
                "photo" if name.startswith(("commons_", "web_", "flickr_")) else "street")
        pos = seeds.get(name)
        d = _dist_m(pos, lat, lon) if pos and lat is not None else None
        out.append({"method": kind, "value_m": float(v), "dist_m": d})
    if b.get("floors") and b.get("storey_m"):
        out.append({"method": "floors", "value_m": float(b["floors"]) * float(b["storey_m"]),
                    "dist_m": None})
    for kind, val in (b.get("satellite") or {}).items():
        h = val[0] if isinstance(val, (list, tuple)) else val
        if h is not None:
            out.append({"method": str(kind), "value_m": float(h), "dist_m": None})
    if b.get("height_tag_m") is not None and b.get("height_source") in TAGGED_SOURCES:
        out.append({"method": b["height_source"], "value_m": float(b["height_tag_m"]),
                    "dist_m": None})
    src = b.get("no_survey_source") or b.get("effective_height_source") or ""
    pub = b.get("no_survey_height_m", b.get("effective_height_m"))
    if pub is not None and (src.startswith("withheld:prior") or src in ("prior_gbm", "default")):
        out.append({"method": "prior", "value_m": float(pub), "dist_m": None})
    return out


def _pred_tier(b: dict) -> tuple[float | None, str]:
    """The survey-blind published height and its tier (what the headline scores)."""
    pred = b.get("no_survey_height_m", b.get("effective_height_m"))
    return (None if pred is None else float(pred),
            b.get("no_survey_tier") or b.get("tier") or "unlabelled")


def band_tier_table(buildings: list[dict], truth: dict[str, dict], bands=REVIEW_BANDS,
                    labels=REVIEW_LABELS) -> dict:
    """``{band: {tier: band_errors, "all": band_errors}}`` (plus band ``"all"``) of the
    survey-blind published height against ``truth`` (the records ``scoring_truth`` keeps),
    banded by truth."""
    cells: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for b in buildings:
        t = truth.get(b["key"])
        pred, tier = _pred_tier(b)
        if t is None or pred is None:
            continue
        band = band_of(float(t["truth_m"]), bands, labels)
        for bk in (band, "all"):
            for tk in (tier, "all"):
                cells.setdefault((bk, tk), []).append((pred, float(t["truth_m"])))
    out: dict[str, dict] = {bk: {} for bk in (*labels, "all")}
    for (bk, tk), pairs in cells.items():
        p, tr = zip(*pairs, strict=True)
        out[bk][tk] = band_errors(p, tr)
    return out


def band_method_table(buildings: list[dict], truth: dict[str, dict], elevated=(),
                      seeds: dict | None = None, bands=REVIEW_BANDS,
                      labels=REVIEW_LABELS) -> dict:
    """``{band: {method: {distance band: errors}}}`` of every reading (``method_readings``)
    against the headline truth (p95), with the method-matched truth (``METHOD_STAT``: max for
    silhouettes, p70 for floors) beside it as ``matched`` where the stats are stored. Every
    method has distance band ``"all"``; readings with a camera position (drone, street) are
    also split by its distance to the building (``DISTANCE_BANDS``)."""
    cells: dict[tuple[str, str, str], list[tuple[float, float, float | None]]] = {}
    for b in buildings:
        t = truth.get(b["key"])
        if t is None:
            continue
        band = band_of(float(t["truth_m"]), bands, labels)
        for r in method_readings(b, elevated, seeds):
            row = (r["value_m"], float(t["truth_m"]),
                   matched_truth(t, METHOD_STAT.get(r["method"], "p95")))
            dists = ("all",) if r["dist_m"] is None else (
                band_of(r["dist_m"], DISTANCE_BANDS, DISTANCE_LABELS), "all")
            for bk in (band, "all"):
                for dk in dists:
                    cells.setdefault((bk, r["method"], dk), []).append(row)
    out: dict[str, dict] = {}
    for (bk, m, dk), rows in sorted(cells.items()):
        e = band_errors([r[0] for r in rows], [r[1] for r in rows])
        mt = [(r[0], r[2]) for r in rows if r[2] is not None]
        if mt:
            me = band_errors([p for p, _ in mt], [t for _, t in mt])
            e["matched"] = {"stat": METHOD_STAT.get(m, "p95"), "n": me["n"],
                            "mae_m": me["mae_m"], "bias_m": me["bias_m"], "tol": me["tol"]}
        out.setdefault(bk, {}).setdefault(m, {})[dk] = e
    return out


def pipeline_metrics(buildings: list[dict], truth: dict[str, dict],
                     n_footprints: int | None = None) -> dict:
    """The review's pipeline metrics (§4.4).

    - ``coverage``: share of the report's rows publishing a non-prior value (survey-blind tier
      neither ``prior`` nor unlabelled), over all rows, over the rows with truth and, given
      ``n_footprints`` (``heights.json`` ``n_building_records``), over every footprint;
    - ``false_corroborated``: corroborated rows (``CORROBORATED_TIERS``) off by more than tol;
    - ``withhold``: readings the pipeline held back (a withheld single, ``single_reading_m``;
      a Street View value on a row publishing ``withheld:*``) among all image readings
      (those plus the published ones, tiers ``READING_TIERS``). Precision: share of withheld
      readings that were wrong (off by more than tol). Recall: share of wrong readings that
      were withheld. ``single`` / ``street`` restrict the withheld side to one kind.
    """
    def covered(b):
        return _pred_tier(b)[1] not in ("prior", "unlabelled")

    with_truth = [b for b in buildings if b["key"] in truth]
    n_cov, n_cov_t = sum(map(covered, buildings)), sum(map(covered, with_truth))
    out = {"coverage": {"rows": len(buildings), "non_prior": n_cov,
                        "share": round(n_cov / len(buildings), 3) if buildings else None,
                        "rows_with_truth": len(with_truth), "non_prior_with_truth": n_cov_t,
                        "share_with_truth": (round(n_cov_t / len(with_truth), 3)
                                             if with_truth else None),
                        "footprints": n_footprints,
                        "share_of_footprints": (round(n_cov / n_footprints, 3)
                                                if n_footprints else None)}}
    corr, readings = [], []
    for b in with_truth:
        tm = float(truth[b["key"]]["truth_m"])
        tol = float(tol_m(tm))
        pred, tier = _pred_tier(b)
        if tier in CORROBORATED_TIERS and pred is not None:
            corr.append(abs(pred - tm) > tol)
        if tier in READING_TIERS and pred is not None:
            readings.append(("published", tier, abs(pred - tm) > tol))
        src = b.get("no_survey_source") or b.get("effective_height_source") or ""
        if b.get("single_reading_m") is not None:
            readings.append(("withheld", "single", abs(float(b["single_reading_m"]) - tm) > tol))
        if src.startswith("withheld:") and b.get("street_view_m") is not None:
            readings.append(("withheld", "street", abs(float(b["street_view_m"]) - tm) > tol))
    out["false_corroborated"] = {"n": len(corr), "wrong": int(sum(corr)),
                                 "rate": round(sum(corr) / len(corr), 3) if corr else None}

    def pr(rows):
        held = [w for s, _, w in rows if s == "withheld"]
        wrong = [s == "withheld" for s, _, w in rows if w]
        return {"readings": len(rows), "withheld": len(held), "withheld_wrong": int(sum(held)),
                "precision": round(sum(held) / len(held), 3) if held else None,
                "wrong": len(wrong),
                "recall": round(sum(wrong) / len(wrong), 3) if wrong else None}

    out["withhold"] = {"all": pr(readings),
                       **{k: pr([r for r in readings if r[0] == "published" or r[1] == k])
                          for k in ("single", "street")}}
    return out


def bench_tables(buildings: list[dict], truth: dict[str, dict], *, elevated=(),
                 seeds: dict | None = None, survey_cache: dict | None = None,
                 n_footprints: int | None = None) -> dict:
    """Every item-1 table for one report: the truth mode, band x tier (review and EUBUCCO
    bands), band x method x distance, the pipeline metrics and the truth age counts.
    ``truth``: ``footprint_truth``'s records for the report's footprints; ``survey_cache``:
    ``survey_heights.load_cache(region)``, for the stored roof statistics."""
    mode = truth_mode(buildings, truth)
    scored, excluded = scoring_truth(truth, mode)
    if survey_cache:
        scored = attach_roof_stats(scored, survey_cache)
    keys = {b["key"] for b in buildings}
    ages: dict[str, int] = {}
    for k, r in truth.items():
        if k in keys and r.get("status") in ("confirmed", "survey_only"):
            a = r.get("temporal") or TEMPORAL_UNKNOWN
            ages[a] = ages.get(a, 0) + 1
    return {
        "truth_mode": mode,
        "truth_status": {TWO_SOURCE: "confirmed", SINGLE_SOURCE: "survey_only"}.get(mode),
        "truth_stat": f"p{ROOF_PERCENTILE:.0f}",
        "tol_rule": {"rel": TOL_REL, "abs_m": TOL_ABS_M},
        "n_scored_truth": sum(1 for b in buildings if b["key"] in scored),
        "excluded": excluded,
        "truth_age": ages,
        "with_roof_stats": sum(1 for b in buildings
                               if (scored.get(b["key"]) or {}).get("roof_m")
                               or (scored.get(b["key"]) or {}).get("survey_roof_m")),
        "band_tier": band_tier_table(buildings, scored),
        "band_tier_eubucco": band_tier_table(buildings, scored, EUBUCCO_BANDS, EUBUCCO_LABELS),
        "band_method": band_method_table(buildings, scored, elevated, seeds),
        "pipeline": pipeline_metrics(buildings, scored, n_footprints),
    }


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
