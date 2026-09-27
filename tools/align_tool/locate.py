"""Decide which patch of ground a printed city plate actually covers.

The registration pipeline used to centre its OSM window on the administrative
centroid returned by the geocoder, then hope the plate landed inside it.  For
every city checked by hand that guess was wrong by hundreds of metres to nearly
four kilometres -- a plate is framed around whatever the vendor thought was
interesting, not around the polygon centroid of the municipality.  The hand
alignments in `ground_truth/` are pure translations of the initial guess,
so the missing piece is a centre and a span, not a rotation.

This module resolves that centre for a given plate, in priority order:

1.  A manual override, for the rare plate nothing automatic can place.
2.  A hand alignment saved by the drag-align tool, converted back to a lat/lon
    plate centre.  This is exact by construction.
3.  A cached solve from a previous run.
4.  An automatic solve: rasterize the plate's water cut-out, fetch OSM water
    over a search window several times the plate's own footprint, and find the
    translation and span that line them up by FFT cross-correlation.  Rotation
    is fixed at zero, verified across every hand alignment.
5.  Nothing, in which case the caller falls back to the geocoded centroid.

Run `python locate.py --validate` to score the solver in metres against the
hand alignments already on disk, and `--dump` to render what it saw.

Three defects found by measurement, and what was done about each:

*   The search window was too small.  At the old 3x margin a 2 km plate could
    only move one kilometre before hanging over the edge, but Lisbon and Miami
    sit 3.8 km from their geocoded centroids.  Their true positions were not
    mis-ranked, they were outside the window and unreachable at any threshold.
    The margin is now 7x.
*   The sea was missing from the OSM mask.  Open water is `natural=coastline`,
    a *line* with no polygon behind it, and a partly failed Overpass fetch was
    being cached as though it were complete -- so Barcelona's mask was inland
    ravines and nothing else.  Coastline is now fetched as its own layer, the
    sea is flood filled from it, and nothing is cached unless it arrived.
*   The plate raster included its own padding.  `mesh_to_heightmap(isotropic=
    True)` centre-pads a non-square plate with NaN, and NaN is how water is
    detected, so Lisbon grew a full-width straight water bar along its north
    edge that correlated happily with any straight line in OSM.  The padding is
    now removed by differencing against the solid plate, which has the same
    bounds and no holes.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import socket
import sys
from pathlib import Path

import numpy as np
import paths  # noqa: E402

from geo2stl import osm as _osm  # noqa: E402
from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon  # noqa: E402

HERE = paths.HERE

GROUND_TRUTH = paths.GROUND_TRUTH
CACHE_PATH = HERE / "data" / "plate_centers.json"
OSM_CACHE = HERE / "data" / "water_cache"
PROBE_DIR = HERE / "data" / "probe"

# Search window side, as a multiple of the plate's own footprint.  The plate has
# to fit inside the window, so its centre can only move by (margin - 1) / 2
# plate widths before it hangs over the edge and stops being findable.  Seven
# gives three plate widths of reach, which covers the worst measured miss
# (Miami, 3.8 km on a 2 km plate) with room to spare.
SEARCH_MARGIN = 7.0

# Ground resolution of the search, in metres per pixel.  The grid is sized from
# this rather than fixed, so widening the window costs pixels, not precision.
TARGET_CELL_M = 8.0
MAX_SEARCH_GRID = 2048

# Spans to try, as multiples of the nominal plate coverage.  The hand alignments
# put the real span between 1651 m (Salzburg) and 2269 m (Barcelona) against a
# nominal 2000 m, so the plate's own XY extent is not a reliable scale after all
# and the span is solved for alongside the position.
SPAN_FACTORS = tuple(round(0.78 + 0.02 * i, 2) for i in range(0, 22))  # 0.78 .. 1.20

# A candidate has to beat the runner-up by this much, and stand this far above
# the surface, before the solve is trusted.  Measured across the five plates
# with a real hand alignment, the correct answer scores z between 15.2 (Lisbon)
# and 24.0 (Salzburg) with a margin between 4.1 and 13.1, so these leave a
# factor of roughly two in hand.  Re-derive them with `--validate` after any
# change to the cues.
MIN_PEAK_Z = 8.0
MIN_MARGIN_Z = 2.5

# Peaks closer together than this fraction of the plate width are the same peak.
PEAK_SEPARATION = 0.15

# Below these coverage fractions a mask carries no shape information: an empty
# OSM water layer (a failed fetch) or a plate with no water on it.  The plate
# floor is low because Valencia's only water feature is the drained Turia bed,
# a one-pixel line covering 0.11% of the plate -- thin, but a real and quite
# distinctive curve, and the outline cue reads it as well as it reads a harbour.
MIN_OSM_WATER_FRAC = 0.001
MIN_PLATE_WATER_FRAC = 0.0008

# Water in OSM, split by how it has to be handled.  Coastline is a line with the
# open sea on one side and nothing at all on the other, so it is fetched apart
# from the rest and flood filled.  `stream` is deliberately absent: over
# Barcelona it returned a fan of forty hillside ravines that swamped the real
# shoreline, and no plate in the collection depicts a stream.
SELECTORS = {
    "coast": '[natural="coastline"]',
    "water": '[natural~"^(water|wetland|bay|strait)$"]',
    "way": '[waterway~"^(river|riverbank|canal|dock)$"]',
}

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
# overpass.osm.jp is deliberately absent: it serves a certificate that does not name the
# host, so every request to it fails verification and only costs the backoff.
OVERPASS_TIMEOUT_S = 180
OVERPASS_ATTEMPTS = 6
OVERPASS_MIN_GAP_S = 6.0
OVERPASS_BACKOFF_S = 10.0
OVERPASS_USER_AGENT = "3dmaps-align/1.0 (+plate registration)"

# How the sea is recovered from the coastline.  Each segment probes this many
# pixels to either side; a region needs at least MIN_SEA_VOTES water-side probes
# and SEA_VOTE_RATIO times more of them than land-side probes to count as sea,
# which keeps the handful of stray probes thrown off by a convex bend from
# flooding the continent.  A result covering more than MAX_SEA_FRAC of the
# window means the coastline had a gap and the fill escaped, so it is discarded.
SEED_OFFSET_PX = 3
MIN_SEA_VOTES = 3
SEA_VOTE_RATIO = 2.0
MAX_SEA_FRAC = 0.85

# Width given to a water feature OSM models as a line rather than an area.
WATER_LINE_BUFFER_M = 20.0

# Tags a way may carry to state its own width, in metres, most trustworthy first. Rare in
# practice -- one way in the four hundred and eighty walls around Granada and Prague has any of
# them -- so they refine a caller's default rather than replace it.
WIDTH_TAGS = ("width", "est_width", "wall:width")

# Not one way across San Juan, Cartagena, Granada and Prague states a width, so a wall's
# thickness has to come from what kind of wall it is. The barrier value splits them cleanly:
# barrier=city_wall is a defensive circuit, averaging three hundred metres a way in San Juan and
# eight hundred in Cartagena, and is metres thick in masonry; barrier=wall is everything else,
# averaging forty-five metres a way in both Granada and Prague, and is a garden or property
# boundary. Treating the two alike either loses the fortification or invents a wall around every
# back yard.
BARRIER_WIDTH_M = {"city_wall": 6.0, "wall": 1.5}

# Pixels across the STL heightmap raster.  export_align_data.py rasterises every
# plate at this size and the drag tool never changes it, so a saved transform's
# input space is always this grid even when the OSM window is larger.
STL_RASTER = 512

# Resolution the plate is rasterised at once, then resampled from for each
# candidate span.  Rasterising a mesh costs seconds; resampling costs nothing.
PLATE_RASTER = 512

# Local SRTM store, used for the terrain cross-check that separates candidates
# the water cue cannot.  Absent or unreadable, the check is skipped.
H5_ROOT = paths.H5_ROOT

# Plates whose centre cannot be solved and must be stated outright.
CENTER_OVERRIDE: dict[str, tuple[float, float]] = {}


def slug(region: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", region.lower()).strip("_")


def search_grid_for(side_m: float) -> int:
    """Pixels across a window of `side_m` metres, rounded to a multiple of 64."""
    grid = int(round(side_m / TARGET_CELL_M / 64.0)) * 64
    return max(512, min(MAX_SEARCH_GRID, grid))


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------

def _same_transform(a, b, tol_px: float = 0.5) -> bool:
    """Whether two affine matrices agree to within half a pixel of translation."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.shape != b.shape:
        return False
    return bool(np.abs(a[:, :2] - b[:, :2]).max() < 1e-9
                and np.abs(a[:, 2] - b[:, 2]).max() < tol_px)


def center_from_ground_truth(city_slug: str) -> dict | None:
    """Convert a saved drag-align transform into the plate's centre in lat/lon.

    The saved `transform` is the pipeline-convention affine mapping STL raster
    pixels onto OSM window pixels.  The plate's centre is the centre of the STL
    raster, so pushing that point through the transform and then through the
    window's bbox gives the plate centre directly.  Raster row zero is south in
    both rasters, so latitude increases with row.
    """
    path = GROUND_TRUTH / f"{city_slug}.json"
    if not path.exists():
        return None
    gt = json.loads(path.read_text(encoding="utf-8"))
    matrix = gt.get("transform")
    bbox = gt.get("osm_bbox_nsew")
    if matrix is None or bbox is None:
        return None

    # The drag tool writes a record the moment a city is opened, seeding it with
    # the pipeline's own guess, and rewrites it on save whether or not anything
    # was moved.  A record still identical to that guess is therefore not a hand
    # alignment at all -- it is the geocoded centroid wearing a hand alignment's
    # filename.  Barcelona's was exactly this, and because it was trusted ahead
    # of everything else it pinned the plate two and a half kilometres inland,
    # put its harbour in the middle of the Eixample, and made the solver look
    # wrong when the solver was right.
    guess = (gt.get("pipeline_guess") or {}).get("matrix")
    if guess is not None and _same_transform(matrix, guess):
        return None

    # The two rasters do not share a pixel grid.  The STL raster is always
    # exported at STL_RASTER pixels, while the OSM window is re-fetched at
    # whatever resolution the drag tool asked for: 512 at the default 1.5x
    # margin, 1024 at 3x, and so on.
    res = int(gt.get("resolution", STL_RASTER))
    m = np.asarray(matrix, dtype=float)
    c = STL_RASTER / 2.0
    x = m[0, 0] * c + m[0, 1] * c + m[0, 2]
    y = m[1, 0] * c + m[1, 1] * c + m[1, 2]

    N, S, E, W = (float(v) for v in bbox)
    lat = S + (y / res) * (N - S)
    lon = W + (x / res) * (E - W)

    scale = float(gt.get("transform_params", {}).get("scale", 0.0))
    cell = float(gt.get("cell_size_m", 0.0))
    span = scale * STL_RASTER * cell if scale and cell else None

    return {"lat": lat, "lon": lon, "source": "ground_truth", "span_m": span}


# --------------------------------------------------------------------------
# Plate rasters
# --------------------------------------------------------------------------

_mesh_cache: dict[tuple[str, int], np.ndarray] = {}


def _heightmap(stl_path, resolution: int) -> np.ndarray:
    key = (str(stl_path), resolution)
    if key not in _mesh_cache:
        from numpy2stl.stl2numpy.heightmap import mesh_to_heightmap
        hm = mesh_to_heightmap(str(stl_path), resolution=resolution, isotropic=True)
        _mesh_cache[key] = np.asarray(hm["heightmap"], dtype=np.float64)
    return _mesh_cache[key]


def plate_span_m(stl_path, scale_m_per_unit: float) -> float:
    """The plate's nominal ground footprint in metres, from its own XY extent."""
    import trimesh
    mesh = trimesh.load(str(stl_path), force="mesh")
    lo, hi = mesh.bounds
    extent = max(float(hi[0] - lo[0]), float(hi[1] - lo[1]))
    if extent <= 0:
        raise RuntimeError(f"degenerate STL XY extent: {stl_path}")
    return extent * float(scale_m_per_unit)


def plate_water_mask(water_stl, solid_stl, resolution: int = PLATE_RASTER) -> np.ndarray:
    """The plate's water, as a mask in the plate's own frame.

    The water layer is the plate with the water cut out, so cells the mesh does
    not cover -- the NaNs -- are the water.  But `mesh_to_heightmap(isotropic=
    True)` also centre-pads a non-square plate with NaN, and that padding is
    indistinguishable from water on its own.  Lisbon's plate is 112.4 by 107.4
    units, which produced a full-width straight water bar along one edge; a
    straight line is the one shape that correlates with something in every OSM
    window, so it did real damage.

    The solid plate has identical XY bounds and no holes, so its NaNs are
    exactly the padding.  Differencing the two leaves the water alone.
    """
    water = np.isnan(_heightmap(water_stl, resolution))
    if solid_stl is not None:
        pad = np.isnan(_heightmap(solid_stl, resolution))
        water = water & ~pad
    return water.astype(np.float32)


def plate_terrain(solid_stl, resolution: int = PLATE_RASTER,
                  building_px: int = 8) -> np.ndarray | None:
    """The plate's ground surface with its buildings knocked down.

    Buildings are narrow positive bumps sitting on a broad terrain surface, so a
    grey-scale erosion with a kernel wider than a city block leaves the terrain
    behind.  What survives is comparable with an SRTM tile, which is the cue
    that separates two candidate positions the water cue scores equally.
    """
    import cv2
    hm = _heightmap(solid_stl, resolution)
    if not np.isfinite(hm).any():
        return None
    filled = np.where(np.isfinite(hm), hm, np.nanmin(hm)).astype(np.float32)
    k = 2 * building_px + 1
    ground = cv2.erode(filled, np.ones((k, k), np.uint8))
    return cv2.GaussianBlur(ground, (0, 0), building_px)


def resample(mask: np.ndarray, size: int) -> np.ndarray:
    """Resample a mask to `size` x `size`, keeping partial coverage as fractions."""
    import cv2
    interp = cv2.INTER_AREA if size < mask.shape[0] else cv2.INTER_LINEAR
    return cv2.resize(mask.astype(np.float32), (size, size), interpolation=interp)


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------

def _zero_mean_unit(a: np.ndarray) -> np.ndarray:
    a = a - a.mean()
    n = float(np.sqrt((a * a).sum()))
    return a / n if n > 0 else a


def correlation_surface(fixed: np.ndarray, moving: np.ndarray) -> np.ndarray:
    """Cross-correlation of the two, rolled so that zero shift is at the centre.

    Both arrays must be the same shape, with `moving` padded so its own centre
    sits at the centre of the frame.  Row index above centre means the plate
    moves north, column index above centre means east.
    """
    return correlate_with(np.fft.rfft2(_zero_mean_unit(fixed)), fixed.shape, moving)


def correlate_with(fixed_fft: np.ndarray, shape, moving: np.ndarray) -> np.ndarray:
    """As `correlation_surface`, reusing an already transformed fixed image.

    The span sweep slides the same OSM window against two dozen resamplings of
    the plate, so transforming the window once instead of once per span removes
    a third of the work for nothing in return.
    """
    m = np.fft.rfft2(_zero_mean_unit(moving))
    corr = np.fft.irfft2(fixed_fft * np.conj(m), s=shape)
    h, w = corr.shape
    return np.roll(corr, (h // 2, w // 2), axis=(0, 1))


def find_peaks(surface: np.ndarray, separation: int, count: int = 6) -> list[dict]:
    """The strongest peaks, each dominating a neighbourhood of `separation` px.

    A plain argmax returns one arbitrary point of a ridge, which is what a
    straight shoreline produces.  Suppressing a radius around each accepted peak
    turns that ridge into a spaced-out shortlist for a second cue to arbitrate,
    and makes the gap between first and second place mean something.
    """
    work = surface.copy()
    mean, std = float(surface.mean()), float(surface.std()) or 1.0
    out = []
    for _ in range(count):
        idx = int(np.argmax(work))
        y, x = (int(v) for v in np.unravel_index(idx, work.shape))
        out.append({"y": y, "x": x, "z": (float(surface[y, x]) - mean) / std})
        y0, y1 = max(0, y - separation), min(work.shape[0], y + separation + 1)
        x0, x1 = max(0, x - separation), min(work.shape[1], x + separation + 1)
        work[y0:y1, x0:x1] = -np.inf
    return out


def _pad_centered(mask: np.ndarray, grid: int) -> tuple[np.ndarray, float, float]:
    """Place `mask` at the centre of a `grid` x `grid` frame of zeros."""
    if mask.shape[0] > grid or mask.shape[1] > grid:
        raise ValueError(f"plate raster {mask.shape} exceeds search grid {grid}")
    oy = (grid - mask.shape[0]) // 2
    ox = (grid - mask.shape[1]) // 2
    out = np.zeros((grid, grid), dtype=np.float32)
    out[oy:oy + mask.shape[0], ox:ox + mask.shape[1]] = mask
    return out, oy + mask.shape[0] / 2.0, ox + mask.shape[1] / 2.0


def edges(mask: np.ndarray) -> np.ndarray:
    """The outline of a mask: where it changes value between adjacent cells."""
    out = np.zeros_like(mask, dtype=np.float32)
    out[:-1, :] += np.abs(np.diff(mask, axis=0))
    out[1:, :] += np.abs(np.diff(mask, axis=0))
    out[:, :-1] += np.abs(np.diff(mask, axis=1))
    out[:, 1:] += np.abs(np.diff(mask, axis=1))
    return np.minimum(out, 1.0)


# --------------------------------------------------------------------------
# OSM water
# --------------------------------------------------------------------------

def osm_water(bbox_nsew, grid: int, verbose: bool = True,
              attempts: int = OVERPASS_ATTEMPTS) -> dict:
    """Water inside a bbox as a filled mask plus an outline mask.

    This deliberately does not go through `get_osm_semantic_masks`, which comes
    back empty for most of these cities for two reasons that have nothing to do
    with the network.  Its tag set never asks for `natural=coastline`, and the
    open sea is not an area in OSM at all -- a coastal city's shoreline is a
    line with no polygon behind it.  And `_rasterize_semantic` keeps only
    Polygon and MultiPolygon rows, so even the features it does fetch are
    dropped whenever OSM models them as a line, which is the usual case for a
    coastline and for a narrow river.

    It also talks to Overpass directly rather than through osmnx, which keeps the
    pacing, the mirror rotation and the per-selector caching in one place.  The
    original reason was that osmnx could not reach Overpass from this machine at
    all; that turned out to be two defects inside osmnx rather than the network,
    and `tune_osmnx` disarms both.  The direct path stays because the caching
    above depends on one request per selector, which osmnx will not do.
    """
    N, S, E, W = (float(v) for v in bbox_nsew)
    filled = np.zeros((grid, grid), dtype=np.float32)
    outline = np.zeros((grid, grid), dtype=np.float32)
    missing = []

    for key, selector in SELECTORS.items():
        layer = _layer(key, selector, (N, S, E, W), grid, verbose, attempts)
        if layer is None:
            missing.append(key)
            continue
        filled = np.maximum(filled, layer["filled"])
        outline = np.maximum(outline, layer["outline"])

    return {"filled": filled, "outline": np.maximum(outline, edges(filled)),
            "missing": missing}


def _layer(key: str, selector: str, bbox, grid: int, verbose: bool,
           attempts: int = OVERPASS_ATTEMPTS,
           line_width_m: float | None = None) -> dict | None:
    """One water layer, from cache when possible, otherwise from Overpass.

    Each selector is cached on its own.  A combined cache was what hid the worst
    bug in this module: Barcelona's coastline request answered 429 while the
    other two succeeded, the union was written to disk as though complete, and
    every later run read back a Barcelona with no Mediterranean in it.  A layer
    that did not arrive is simply not cached, so the next run asks again.
    """
    cached = _cache_load(key, bbox, grid)
    if cached is not None:
        return cached

    try:
        elements = _overpass(selector, bbox, attempts)
    except Exception as exc:
        if verbose:
            print(f"  osm {key} failed: {exc}", flush=True)
        return None

    if key == "coast":
        layer = _coast_layer(elements, bbox, grid)
    else:
        geoms = _geometries(elements, line_width_m)
        mask = _rasterize(geoms, bbox, grid)
        layer = {"filled": mask, "outline": edges(mask)}
    _cache_save(key, bbox, grid, layer)
    return layer


def _coast_layer(elements: list[dict], bbox, grid: int) -> dict:
    """Turn coastline ways into both a shoreline and a filled sea.

    OSM draws the coastline as an open line, so on its own it gives an outline
    to match but no area.  The plate, by contrast, has the whole sea cut out of
    it as a solid region, and matching a solid against a line throws away most
    of the evidence -- for Barcelona it threw away all of it, because with no
    sea in the OSM mask the plate's true position scored a correlation z of
    -0.1, indistinguishable from empty ground.

    The convention that coastline ways run with land on the left and water on
    the right makes the sea recoverable.  Burning the ways into a barrier cuts
    the window into connected regions; each segment then votes for the region
    just to its right and against the one just to its left, and the regions that
    win their vote are the sea.  Voting rather than plain seeding matters: a
    convex bend puts a few right-hand seeds on the landward side, and a single
    stray seed is enough to flood the entire continent.
    """
    from scipy import ndimage

    N, S, E, W = bbox
    lines = [el["geometry"] for el in elements if el.get("geometry")]
    for el in elements:
        for member in el.get("members", ()):
            if member.get("geometry"):
                lines.append(member["geometry"])
    empty = np.zeros((grid, grid), np.float32)
    if not lines:
        return {"filled": empty, "outline": empty.copy()}

    # Everything here is done north-up and flipped at the end, so that "right of
    # travel" keeps its ordinary geographic meaning.
    def to_px(lon, lat):
        return ((lon - W) / (E - W) * grid, (N - lat) / (N - S) * grid)

    barrier = np.zeros((grid, grid), np.uint8)
    probes = []
    for pts in lines:
        px = [to_px(p["lon"], p["lat"]) for p in pts]
        for (x0, y0), (x1, y1) in zip(px, px[1:]):
            _draw_line(barrier, x0, y0, x1, y1)
            dx, dy = x1 - x0, y1 - y0
            n = math.hypot(dx, dy)
            if n < 1e-9:
                continue
            # Right of travel in image coordinates, where y grows southward:
            # heading north is (0, -1) and its right is east, (1, 0).
            rx, ry = -dy / n, dx / n
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            probes.append((mx + SEED_OFFSET_PX * rx, my + SEED_OFFSET_PX * ry, +1))
            probes.append((mx - SEED_OFFSET_PX * rx, my - SEED_OFFSET_PX * ry, -1))

    shore = barrier.astype(np.float32)
    labels, count = ndimage.label(barrier == 0)
    water_votes = np.zeros(count + 1, dtype=np.int32)
    land_votes = np.zeros(count + 1, dtype=np.int32)
    for x, y, side in probes:
        xi, yi = int(round(x)), int(round(y))
        if not (0 <= xi < grid and 0 <= yi < grid):
            continue
        lab = int(labels[yi, xi])
        if lab == 0:  # landed back on the coastline itself
            continue
        if side > 0:
            water_votes[lab] += 1
        else:
            land_votes[lab] += 1

    sea_labels = [i for i in range(1, count + 1)
                  if water_votes[i] >= MIN_SEA_VOTES
                  and water_votes[i] > SEA_VOTE_RATIO * land_votes[i]]
    sea = (np.isin(labels, sea_labels).astype(np.float32) if sea_labels
           else empty.copy())
    if sea.mean() > MAX_SEA_FRAC:  # the fill escaped through a gap in the coastline
        sea[:] = 0.0

    return {"filled": np.flipud(sea),
            "outline": np.flipud(np.maximum(shore, edges(sea)))}


def _draw_line(img: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> None:
    """Burn a one-pixel line into `img`, clipped to its bounds by cv2."""
    import cv2
    cv2.line(img, (int(round(x0)), int(round(y0))), (int(round(x1)), int(round(y1))),
             1, 1)


def _tag_width_m(tags: dict) -> float | None:
    """The width a way claims for itself, in metres, or None if it claims none.

    Values are written loosely -- "2", "2 m", "1.5m" -- so the leading number is taken and the
    unit ignored, which is safe because metres is the OSM default and the alternatives are
    written with a unit this parser would reject anyway.
    """
    for key in WIDTH_TAGS:
        raw = tags.get(key)
        if raw is None:
            continue
        try:
            value = float(str(raw).strip().split()[0].rstrip("m").strip())
        except (ValueError, IndexError):
            continue
        if 0.0 < value < 100.0:
            return value
    return None


def _geometries(elements: list[dict], line_width_m: float | None = None) -> list:
    """Turn Overpass elements into shapely geometries in lon/lat degrees.

    With no `line_width_m` the features are areal, and a way whose first and last points
    coincide becomes a Polygon while anything else becomes a LineString buffered to
    `WATER_LINE_BUFFER_M`.  That is how every water selector is read: a closed way round a lake
    is the lake.

    With `line_width_m` the features are linear, and every way is stroked at that width whether
    or not it closes.  A wall is the case this exists for.  Closure means a wall circuit
    returns to where it started and says nothing about the ground inside it, so reading it as
    an area paints the enclosure instead of the structure -- for the Alhambra, the whole
    hilltop instead of a five-metre curtain wall.  A way that states its own width in `width`
    or `est_width` is drawn at that instead, and failing that a wall is drawn at the width its
    barrier kind implies.

    A relation's members are stitched back into rings before any of this, because a large river
    is a multipolygon whose outer boundary OpenStreetMap splits across several ways and most of
    those ways do not close on their own.  Treating each member separately therefore drew the
    Seine as a pair of twenty-metre ribbons along its banks with the channel between them left
    empty, and the `waterway=river` centreline drawn down that empty channel then cut the
    remainder into enclosed pockets.  Stitching first recovered a further 2.4% of the Paris
    window.  Where the members refuse to close into anything they are still buffered as lines,
    which is the right answer for a stream mapped as a bare centreline.

    The inner/outer ring distinction is still not kept, which is deliberate: the mask records
    only where a feature is, not how a multipolygon is nested.
    """
    from shapely.geometry import LineString, Polygon
    from shapely.ops import linemerge, polygonize

    buffer_deg = WATER_LINE_BUFFER_M / M_PER_DEG_LAT
    out = []

    def add(points: list[dict], tags: dict) -> None:
        coords = [(p["lon"], p["lat"]) for p in points]
        if len(coords) < 2:
            return
        try:
            if line_width_m is not None:
                width = (_tag_width_m(tags)
                         or BARRIER_WIDTH_M.get(tags.get("barrier", ""))
                         or line_width_m)
                geom = LineString(coords).buffer(width / 2.0 / M_PER_DEG_LAT)
            elif len(coords) >= 4 and coords[0] == coords[-1]:
                geom = Polygon(coords)
                if not geom.is_valid:
                    geom = geom.buffer(0)
            else:
                geom = LineString(coords).buffer(buffer_deg)
        except Exception:
            return
        if not geom.is_empty:
            out.append(geom)

    def add_rings(members, tags) -> bool:
        """Join a relation's member ways into closed rings and keep whatever closes."""
        lines = []
        for member in members:
            coords = [(p["lon"], p["lat"]) for p in member.get("geometry") or []]
            if len(coords) >= 2:
                lines.append(LineString(coords))
        if not lines:
            return False
        try:
            rings = [g for g in polygonize(linemerge(lines)) if not g.is_empty]
        except Exception:
            return False
        if not rings:
            return False
        out.extend(rings)
        return True

    for el in elements:
        tags = el.get("tags") or {}
        if el.get("geometry"):
            add(el["geometry"], tags)
        members = [m for m in el.get("members", ()) if m.get("geometry")]
        # Stroked features are lines by intent, so a wall circuit must never be closed into an
        # area; only areal features are stitched.
        if line_width_m is None and add_rings(members, tags):
            continue
        for member in members:
            add(member["geometry"], member.get("tags") or tags)
    return out


def _rasterize(geoms: list, bbox, grid: int) -> np.ndarray:
    """Burn geometries into a square grid with row 0 = south.

    North-up is what `numpy2stl.raster.burn_polygons` produces and row 0 = south
    is what `mesh_to_heightmap` produces, so the result is flipped to match the
    plate raster -- the same correction `cities._rasterize_rasterio` makes.
    """
    from numpy2stl.raster import burn_polygons

    N, S, E, W = bbox
    arr = burn_polygons(geoms, (grid, grid), bounds=(W, S, E, N), values=1.0,
                        all_touched=True, dtype=np.float32)
    return np.flipud(arr).copy()


def _overpass(selector: str, bbox, attempts: int = OVERPASS_ATTEMPTS) -> list[dict]:
    """POST one Overpass query and return its elements, trying each mirror.

    `out geom` inlines each way's coordinates and each relation member's, which
    saves a second round trip to resolve node references.  Only ways and
    relations are asked for: a water feature drawn as a lone node has no shape
    and so contributes nothing to a correlation.

    One selector per request.  A single combined query returned 504 Gateway
    Timeout on a Barcelona-sized window, and asking for one tag family at a time
    keeps each request small enough for the public endpoints to serve.

    `attempts` is worth lowering for callers that only want the layer for a
    human to look at.  A solve should keep the full budget, but the export's
    drag-tool overlay does not justify six attempts at a three-minute timeout --
    that is eighteen minutes of waiting for one decorative selector.
    """
    N, S, E, W = bbox
    query = (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];"
        f"(way{selector}({S},{W},{N},{E});"
        f"relation{selector}({S},{W},{N},{E}););out geom;"
    )
    return _osm.overpass_query(
        query, urls=OVERPASS_URLS, attempts=attempts, timeout_s=OVERPASS_TIMEOUT_S,
        min_gap_s=OVERPASS_MIN_GAP_S, backoff_s=OVERPASS_BACKOFF_S,
        user_agent=OVERPASS_USER_AGENT)


def _overpass_wait() -> None:
    """Hold back until `OVERPASS_MIN_GAP_S` has passed since the last request.

    The public endpoint hands out a small number of concurrent slots per client
    and answers 429 once they are gone.  A full validate run fires two dozen
    queries, which is more than enough to trip that, so calls are spaced out.
    The clock is `geo2stl.osm`'s, shared with every raw query in the process.
    """
    _osm.overpass_wait(OVERPASS_MIN_GAP_S)


def _cache_path(key: str, bbox, grid: int) -> Path:
    N, S, E, W = bbox
    return OSM_CACHE / f"{N:.5f}_{S:.5f}_{E:.5f}_{W:.5f}_{grid}_{key}.npz"


def _cache_load(key: str, bbox, grid: int) -> dict | None:
    path = _cache_path(key, bbox, grid)
    if not path.exists():
        return None
    try:
        with np.load(path) as z:
            return {"filled": z["filled"].astype(np.float32),
                    "outline": z["outline"].astype(np.float32)}
    except Exception:
        return None


def _cache_save(key: str, bbox, grid: int, layer: dict) -> None:
    path = _cache_path(key, bbox, grid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, filled=layer["filled"].astype(np.uint8),
                            outline=layer["outline"].astype(np.uint8))
    except Exception:
        pass


# --------------------------------------------------------------------------
# Terrain cross-check
# --------------------------------------------------------------------------

def dem_window(bbox, grid: int) -> np.ndarray | None:
    """SRTM elevation for the search window, on the search grid, row 0 = south.

    The local store is ~90 m per pixel, far coarser than the search, which is
    exactly what makes it useful: it says nothing about where a street is and a
    great deal about which hill the plate is sitting on.
    """
    try:
        import cv2

        from geo2stl.dem import fetch_h5_dem
        h5 = H5_ROOT / "strm_data.h5"
        if not h5.exists():
            return None
        N, S, E, W = bbox
        dem = np.asarray(fetch_h5_dem(N, S, E, W, h5_file=h5), dtype=np.float32)
        if dem.size == 0 or not np.isfinite(dem).any():
            return None
        dem = cv2.resize(dem, (grid, grid), interpolation=cv2.INTER_LINEAR)
        return np.flipud(dem)  # fetch_h5_dem is row 0 = north
    except Exception:
        return None


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    """Normalized cross-correlation of two equally shaped patches."""
    a = a.astype(np.float64) - a.mean()
    b = b.astype(np.float64) - b.mean()
    na, nb = float(np.sqrt((a * a).sum())), float(np.sqrt((b * b).sum()))
    if na <= 0 or nb <= 0:
        return 0.0
    return float((a * b).sum() / (na * nb))


def terrain_score(dem: np.ndarray, terrain: np.ndarray, cy: int, cx: int) -> float:
    """How well the plate's own ground surface matches SRTM at one position."""
    h, w = terrain.shape
    y0, x0 = int(round(cy - h / 2)), int(round(cx - w / 2))
    if y0 < 0 or x0 < 0 or y0 + h > dem.shape[0] or x0 + w > dem.shape[1]:
        return 0.0
    patch = dem[y0:y0 + h, x0:x0 + w]
    if not np.isfinite(patch).all() or patch.std() < 1.0:
        return 0.0  # flat ground carries no information
    return ncc(patch, terrain)


# --------------------------------------------------------------------------
# Solver
# --------------------------------------------------------------------------

def solve_center(
    region: str,
    water_stl,
    solid_stl,
    base_span_m: float,
    seed: tuple[float, float] | None = None,
    search_margin: float = SEARCH_MARGIN,
    verbose: bool = True,
) -> dict:
    """Find the plate's centre and span by sliding its water mask over OSM.

    Rotation is assumed zero, verified across every hand alignment, where each
    correction was a pure translation.  Span is not assumed: the plate's own XY
    extent implies 2000 m for all eight plates, but the hand alignments put the
    real figure anywhere from 1651 m to 2269 m, so it is searched over together
    with the position.
    """
    from numpy2stl.applications.cities import tight_bbox_from_extent

    if seed is None:
        seed = seed_center(region)
    side_m = base_span_m * search_margin
    grid = search_grid_for(side_m)
    cell_m = side_m / grid
    bbox = tight_bbox_from_extent(seed[0], seed[1], side_m, margin=1.0)
    N, S, E, W = bbox

    plate_hi = plate_water_mask(water_stl, solid_stl)
    result = {
        "source": "solved",
        "seed": [float(seed[0]), float(seed[1])],
        "base_span_m": float(base_span_m),
        "cell_size_m": float(cell_m),
        "grid": grid,
        "search_bbox_nsew": [float(v) for v in bbox],
        "plate_water_frac": float(plate_hi.mean()),
        "ok": False,
    }
    if result["plate_water_frac"] < MIN_PLATE_WATER_FRAC:
        result["reason"] = f"plate water mask too sparse ({result['plate_water_frac']:.4f})"
        return result

    osm = osm_water(bbox, grid, verbose=verbose)
    result["osm_missing"] = osm["missing"]
    result["osm_water_frac"] = float(osm["filled"].mean())
    if osm["missing"]:
        result["reason"] = f"osm layers missing: {','.join(osm['missing'])}"
        return result
    if float(osm["outline"].mean()) < MIN_OSM_WATER_FRAC:
        result["reason"] = f"osm water mask empty ({osm['outline'].mean():.4f})"
        return result

    dem = dem_window(bbox, grid)
    terrain_hi = plate_terrain(solid_stl) if dem is not None else None

    fixed_ffts = {
        "filled": np.fft.rfft2(_zero_mean_unit(osm["filled"])),
        "outline": np.fft.rfft2(_zero_mean_unit(osm["outline"])),
    }
    shape = osm["filled"].shape

    best = None
    for factor in SPAN_FACTORS:
        span = base_span_m * factor
        px = int(round(span / cell_m))
        if px < 16 or px > grid:
            continue
        plate = resample(plate_hi, px)
        padded, cy, cx = _pad_centered(plate, grid)
        separation = max(4, int(PEAK_SEPARATION * px))
        for cue, moving in (("filled", padded), ("outline", edges(padded))):
            surface = correlate_with(fixed_ffts[cue], shape, moving)
            peaks = find_peaks(surface, separation)
            cand = {"span_m": span, "factor": factor, "cue": cue,
                    "peaks": peaks, "cy": cy, "cx": cx, "px": px}
            if best is None or peaks[0]["z"] > best["peaks"][0]["z"]:
                best = cand

    if best is None:
        result["reason"] = "no usable span"
        return result

    # Score the shortlist with terrain, which knows nothing about water and so
    # can break a tie the water cue cannot -- most importantly along a straight
    # shoreline, where the correlation is a ridge and every point on it scores
    # the same.
    terrain = resample(terrain_hi, best["px"]) if terrain_hi is not None else None
    for peak in best["peaks"]:
        dy = peak["y"] - grid // 2
        dx = peak["x"] - grid // 2
        peak["lat"] = S + ((best["cy"] + dy) / grid) * (N - S)
        peak["lon"] = W + ((best["cx"] + dx) / grid) * (E - W)
        peak["terrain"] = (terrain_score(dem, terrain, best["cy"] + dy, best["cx"] + dx)
                           if terrain is not None else 0.0)

    top, runner = best["peaks"][0], best["peaks"][1]
    margin = top["z"] - runner["z"]
    chosen = top
    # Only let terrain overrule the water cue when water is genuinely undecided.
    if margin < MIN_MARGIN_Z and terrain is not None:
        ranked = sorted(best["peaks"][:3], key=lambda p: p["terrain"], reverse=True)
        if ranked[0]["terrain"] > 0.35 and ranked[0]["terrain"] > ranked[1]["terrain"] + 0.1:
            chosen = ranked[0]
            result["arbitrated_by"] = "terrain"

    result.update(
        span_m=best["span_m"], span_factor=best["factor"], cue=best["cue"],
        z=chosen["z"], margin=margin, terrain=chosen["terrain"],
        lat=chosen["lat"], lon=chosen["lon"],
        candidates=[{k: p[k] for k in ("z", "terrain", "lat", "lon")}
                    for p in best["peaks"][:4]],
    )

    if chosen["z"] < MIN_PEAK_Z:
        result["reason"] = f"correlation peak too weak (z={chosen['z']:.1f})"
        return result
    if margin < MIN_MARGIN_Z and "arbitrated_by" not in result:
        result["reason"] = (f"two positions score alike "
                            f"(z={top['z']:.1f} vs {runner['z']:.1f})")
        return result

    result["ok"] = True
    return result


_OSMNX_TUNED = [False]


def tune_osmnx(probe_timeout_s: float = 30.0, verbose: bool = True,
               force: bool = False) -> str | None:
    """Make osmnx able to reach Overpass from this machine.

    The building heightmap comes from `numpy2stl.registration`, which fetches
    through osmnx, and osmnx here fails with a connect or read timeout while a
    plain POST of the same query returns in under two seconds.  That was put
    down to the network for weeks.  It is not the network.

    osmnx ships with `settings.overpass_rate_limit = True`, which makes it ask
    the endpoint's `/api/status` for a free slot before every query and sleep
    until one appears.  It is that status request that hangs from here, not the
    query, so the failure looks like an unreachable host while the interpreter
    endpoint beside it answers normally.  Measured on one 0.01-degree building
    query: 90 s read timeout with the rate limiter on, 2.3 s with it off, same
    endpoint and same bbox, 253 features returned.

    Turning the limiter off hands the pacing back to us.  That is fine for the
    caller this exists for -- a full export fires one building query per city
    with a minute of meshing between them -- but anything that loops harder
    should space its own calls, the way `_overpass_wait` does for this module's
    own requests.

    The endpoint is then chosen by probing each mirror with a query small
    enough to answer instantly and taking the first that responds; osmnx wants
    the base URL without the trailing `/interpreter` that `OVERPASS_URLS`
    carries, so the suffix is stripped.  Returns the chosen base URL, or None
    if osmnx is not installed or no mirror answered, in which case osmnx keeps
    its own default and the caller is no worse off than before.

    It runs once per process unless `force` is set, which is what a retry after
    a failed fetch wants: the mirror chosen at startup may be the one that has
    just gone away.
    """
    if _OSMNX_TUNED[0] and not force:
        return None
    _OSMNX_TUNED[0] = True

    import requests
    try:
        import osmnx as ox
    except ImportError:
        return None

    ox.settings.overpass_rate_limit = False
    ox.settings.requests_timeout = 120
    ox.settings.doh_url_template = None

    # osmnx's _config_dns resolves the endpoint's hostname once and then
    # replaces socket.getaddrinfo for the whole process so that name always
    # returns that one address.  It does this on purpose -- overpass-api.de
    # round-robins between gall and lambert, and osmnx wants its slot check and
    # its query to reach the same machine -- but the cost here outweighs it.
    # If the pinned machine is the unhealthy one, every later request in the
    # process connect-times-out, including this module's own direct POSTs,
    # which never go through osmnx at all.  That is what made one failed
    # building fetch take a city's whole water overlay down with it, and why a
    # fresh process could reach the same host seconds later.
    #
    # Neutralising it costs only the slot-check affinity, and the slot check is
    # already off above.
    try:
        from osmnx import _http as ox_http
        ox_http._config_dns = lambda url: None
        socket.getaddrinfo = ox_http._original_getaddrinfo
    except Exception:  # a future osmnx may not have either name
        pass

    # The probe is paced like any other request from this module, and a 429 counts
    # as an answer.  It has to: the probe shares its quota with the solve that is
    # about to run, so the likeliest reason a healthy mirror refuses this query is
    # that our own last query is still holding a slot.  Rejecting a mirror for
    # being busy would leave osmnx pointed at whatever its default is, which is
    # the outcome the probe exists to avoid.  Only a server error or no answer at
    # all rules a mirror out.
    probe = "[out:json][timeout:10];node(50.0,14.4,50.001,14.401);out count;"
    for url in OVERPASS_URLS:
        try:
            _overpass_wait()
            response = requests.post(
                url, data={"data": probe}, timeout=(15, probe_timeout_s),
                headers={"User-Agent": OVERPASS_USER_AGENT},
            )
            if response.status_code >= 500:
                continue
        except Exception:
            continue
        base = url[: -len("/interpreter")] if url.endswith("/interpreter") else url
        ox.settings.overpass_url = base
        if verbose:
            print(f"osmnx: rate limiter off, endpoint {base}", flush=True)
        return base

    if verbose:
        print("osmnx: rate limiter off, endpoint left at its default "
              "(no mirror answered the probe)", flush=True)
    return None


def seed_center(region: str) -> tuple[float, float]:
    """Where to centre the search window: the city's downtown, not its bounding box.

    Every plate this pipeline handles depicts a downtown, so that is what the window
    should be centred on. The midpoint of an administrative bounding box is not it, and
    is not even a centroid -- being the midpoint of the extremes, a single outlying limb
    drags it. Denver's limits reach the airport and put the midpoint 11.6 km from LoDo;
    Boston's take in the harbour islands and put it 7 km out over the water. Against a
    wide phase that reaches about 5.5 km, that is exactly the defect that made Lisbon and
    Miami unreachable, one level further up.

    `get_city_center_point` geocodes "Downtown {region}" and falls back to the bounding
    box midpoint itself, so this is strictly better informed than what it replaces.
    """
    from numpy2stl.applications.cities import get_city_bbox, get_city_center_point
    point = get_city_center_point(region)
    if point is not None:
        return (float(point[0]), float(point[1]))
    N, S, E, W = get_city_bbox(region)
    return ((N + S) / 2.0, (E + W) / 2.0)


# --------------------------------------------------------------------------
# Cache and top-level resolution
# --------------------------------------------------------------------------

def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")


def resolve_center(
    region: str,
    water_stl=None,
    solid_stl=None,
    span_m: float | None = None,
    use_cache: bool = True,
    solve: bool = True,
    plate_key: str | None = None,
    span_known: bool = True,
    verbose: bool = True,
) -> tuple[float, float] | None:
    """Best available plate centre for a city, or None to use the geocoder.

    A newly added plate falls straight through to the solver, so dropping an STL
    into the collection is enough to get it placed; once a human has dragged it
    into position the saved alignment takes over and the solve is never
    consulted again.

    `plate_key` names the plate where the region alone would not: Paris and Philadelphia have
    both a micropolitan plate and a miniature, and the two must not share a cache entry or a
    hand alignment.  `span_known` is False for a pack that states no ground coverage, which
    sends the building solver to measure the span before placing the plate rather than
    sweeping a narrow range around an assumption that may be a factor of three out.
    """
    key = plate_key or slug(region)

    override = CENTER_OVERRIDE.get(key)
    if override is not None:
        if verbose:
            print(f"{key}: centre from override", flush=True)
        return override

    gt = center_from_ground_truth(key)
    if gt is not None:
        if verbose:
            print(f"{key}: centre from hand alignment", flush=True)
        return (gt["lat"], gt["lon"])

    cache = _load_cache()
    if use_cache and key in cache and cache[key].get("ok"):
        entry = cache[key]
        if verbose:
            print(f"{key}: centre from cached solve (z={entry.get('z', 0):.1f})", flush=True)
        return (entry["lat"], entry["lon"])

    if not solve or span_m is None:
        return None

    res = None
    if water_stl is not None:
        res = solve_center(region, water_stl, solid_stl, span_m, verbose=verbose)
        if not res.get("ok") and verbose:
            print(f"{key}: water solve failed ({res.get('reason')})", flush=True)

    # Buildings are the fallback, not the first choice: water is a far sharper cue where a
    # plate has any, and the two agree to within a few tens of metres where both run. But a
    # pack with no water cut-out -- Philadelphia, and the Boston, Denver and Paris
    # miniatures -- has nothing else, and the geocoder it used to fall through to put
    # Philadelphia's centre 5.5 km from the skyline.
    if res is None or not res.get("ok"):
        if solid_stl is not None:
            import locate_buildings as LB
            res = LB.solve_plate(region, solid_stl, span_m, span_known=span_known,
                                 verbose=verbose)
        elif res is None:
            return None

    cache[key] = res
    _save_cache(cache)
    if not res.get("ok"):
        if verbose:
            print(f"{key}: solve failed ({res.get('reason')}); falling back to geocoder",
                  flush=True)
        return None
    if verbose:
        if res.get("source") == "solved-buildings":
            print(f"{key}: centre solved from buildings "
                  f"({res['agree']}/{res['crops']} crops, r={res['mean_r']:.3f}, "
                  f"span={res['span_m']:.0f} m, rot={res['rot_deg']:+.1f} deg)", flush=True)
        else:
            print(f"{key}: centre solved (z={res['z']:.1f}, margin={res['margin']:.1f}, "
                  f"span={res['span_m']:.0f} m)", flush=True)
    return (res["lat"], res["lon"])


def measured_span_m(region: str, plate_key: str | None = None) -> float | None:
    """The plate's true ground span, when something has actually measured it.

    A hand alignment counts first.  The water solver does search over span and
    gets close -- within half a percent for Salzburg, whose plate really does
    cover 1651 m rather than the nominal 2000 -- but it read Lisbon ten percent
    high, and a ten percent scale error applied to the OSM fetch is worse than
    no correction at all.  Position is what that solver is trusted for; scale it
    only informs.

    The building solver's span is a different quantity and is trusted.  It does
    not read the shift of a single peak: crops sit at known offsets inside the
    plate, so a wrong span makes them disagree radially, and the span that wins
    is the one the most crops agree at.  Philadelphia is the case that needs it
    -- its plate covers about 1560 m against the assumed 2000, which costs 0.21
    of IoU on its own -- and it is exactly the kind of plate that has no hand
    alignment to read the figure off.
    """
    key = plate_key or slug(region)
    gt = center_from_ground_truth(key)
    if gt is not None and gt.get("span_m"):
        return float(gt["span_m"])
    entry = _load_cache().get(key)
    if (entry is not None and entry.get("ok")
            and entry.get("source") == "solved-buildings" and entry.get("span_m")):
        return float(entry["span_m"])
    return None


def measured_rotation_deg(region: str, plate_key: str | None = None) -> float | None:
    """How far the plate must be turned to sit square with its city, in degrees.

    Only the building solver measures this, and only some plates need it.  A plate
    drawn in its city's own frame needs nothing, which covers the eight water packs
    and micropolitan Philadelphia; a plate drawn square to north does need it in any
    city whose grid is not, and Philadelphia's grid is 9.25 degrees off north, so its
    miniature needs +9.

    The sign is converted here so that no caller has to know the solver's convention.
    `solve_plate` reports `rot_deg` as the *negation* of the angle `turn` takes --
    its sweep turns the plate by `-deg` and records `deg`, and `fit_rotation` returns
    a negated residual to match -- and what a caller wants is the angle to turn by.
    """
    key = plate_key or slug(region)

    gt = center_from_ground_truth(key)
    if gt is not None and gt.get("rot_deg") is not None:
        return float(gt["rot_deg"])          # a hand alignment states the turn directly

    # A hand alignment fixes the centre and stops the solver ever running, but it records
    # no angle today, so the cached solve is still the only place one exists.  Reading it
    # even when ground truth is present is what keeps a hand-placed plate from silently
    # reverting to square.
    entry = _load_cache().get(key)
    if (entry is not None and entry.get("ok")
            and entry.get("source") == "solved-buildings"
            and entry.get("rot_deg") is not None):
        return -float(entry["rot_deg"])
    return None


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def meters_between(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """North and east offset from `a` to `b`, in metres."""
    dn = (b[0] - a[0]) * M_PER_DEG_LAT
    de = (b[1] - a[1]) * m_per_deg_lon(a[0])
    return dn, de


def _plates(only: list[str] | None):
    """Every plate the exporter knows about, with its STL pair and nominal span."""
    import export_align_data as ex
    for name, (region, folder, _tall) in ex.CITIES.items():
        key = getattr(ex, "SLUGS", {}).get(name) or slug(region)
        if only and not any(o.lower() in key for o in only):
            continue
        stl = ex.find_stl(folder)
        if stl is None:
            print(f"{key:22s} missing STL", flush=True)
            continue
        # A missing water plate is normal, not an error: four of the packs ship none, and
        # those are exactly the ones `locate_buildings` exists for.  Only the water solver
        # needs to check for it.
        water = ex.find_water_stl(folder)
        span = plate_span_m(stl, ex.plate_scale_m_per_unit(name, stl))
        # Nominal, not measured.  For a pack with no stated coverage this is only the centre
        # of the wide sweep that measures the real one.
        wide = name in getattr(ex, "COVERAGE_UNKNOWN", ())
        yield key, region, stl, water, span, wide


def validate(only: list[str] | None = None) -> int:
    """Score the solver in metres against every hand alignment on disk."""
    print(f"{'city':22s} {'dN':>7s} {'dE':>7s} {'dist':>7s} {'z':>5s} {'marg':>5s} "
          f"{'terr':>5s} {'span':>5s}  note", flush=True)
    for key, region, stl, water, span, _wide in _plates(only):
        truth = center_from_ground_truth(key)
        res = solve_center(region, water, stl, span)
        note = res.get("reason", "") if not res.get("ok") else ""
        if res.get("arbitrated_by"):
            note = f"terrain-arbitrated {note}".strip()
        if res.get("lat") is None:
            print(f"{key:22s} {'-':>7s} {'-':>7s} {'-':>7s} {'-':>5s} {'-':>5s} "
                  f"{'-':>5s} {'-':>5s}  {note}", flush=True)
            continue
        if truth is None:
            print(f"{key:22s} {'-':>7s} {'-':>7s} {'-':>7s} {res['z']:5.1f} "
                  f"{res['margin']:5.1f} {res['terrain']:5.2f} {res['span_m']:5.0f}  "
                  f"no hand alignment; solved {res['lat']:.5f},{res['lon']:.5f} {note}",
                  flush=True)
            continue
        dn, de = meters_between((truth["lat"], truth["lon"]), (res["lat"], res["lon"]))
        flag = "" if res.get("ok") else "REJECTED "
        print(f"{key:22s} {dn:+7.0f} {de:+7.0f} {math.hypot(dn, de):7.0f} "
              f"{res['z']:5.1f} {res['margin']:5.1f} {res['terrain']:5.2f} "
              f"{res['span_m']:5.0f}  {flag}{res.get('cue')} cue, "
              f"truth span {truth['span_m'] or 0:.0f} {note}", flush=True)
    return 0


def validate_buildings(only: list[str] | None = None) -> int:
    """Score the building solver the same way, including the plates with no water.

    The eight located cities are the control: their hand alignments and water solves say
    where the answer is, so a building solve that lands on it is doing real work rather than
    finding the densest district.  Philadelphia and the miniatures have no such check, so
    what is reported for them is the solve's own confidence and the span and rotation it
    read -- the numbers that have to stand on their own before the plate is exported.
    """
    import locate_buildings as LB

    print(f"{'city':22s} {'dN':>7s} {'dE':>7s} {'dist':>7s} {'agree':>8s} {'r':>6s} "
          f"{'span':>6s} {'rot':>7s}  note", flush=True)
    for key, region, stl, _water, span, wide in _plates(only):
        res = LB.solve_plate(region, stl, span, span_known=not wide, verbose=False)
        note = "" if res.get("ok") else f"REJECTED {res.get('reason', '')}"
        if res.get("lat") is None:
            print(f"{key:22s} {'-':>7s} {'-':>7s} {'-':>7s} {'-':>8s} {'-':>6s} "
                  f"{'-':>6s} {'-':>7s}  {note}", flush=True)
            continue
        agreement = f"{res['agree']}/{res['crops']}"
        truth = center_from_ground_truth(key)
        if truth is None:
            entry = _load_cache().get(key) or {}
            truth = entry if entry.get("ok") and entry.get("lat") else None
        if truth is None:
            cols = f"{'-':>7s} {'-':>7s} {'-':>7s}"
            where = f"no reference; solved {res['lat']:.5f},{res['lon']:.5f}"
        else:
            dn, de = meters_between((truth["lat"], truth["lon"]), (res["lat"], res["lon"]))
            cols = f"{dn:+7.0f} {de:+7.0f} {math.hypot(dn, de):7.0f}"
            where = ""
        print(f"{key:22s} {cols} {agreement:>8s} {res['mean_r']:6.3f} "
              f"{res['span_m']:6.0f} {res['rot_deg']:+7.2f}  {where}{note}", flush=True)
    return 0


def dump(only: list[str] | None = None) -> int:
    """Render what the solver saw, one PNG per city, for eyeballing a failure.

    Red is OSM water in the search window, blue is the plate's water drawn where
    the solver put it, green is the same drawn at the hand-aligned position.
    """
    import cv2

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    for key, region, stl, water_stl, span, _wide in _plates(only):
        res = solve_center(region, water_stl, stl, span)
        bbox = res["search_bbox_nsew"]
        grid = res["grid"]
        N, S, E, W = bbox
        osm = osm_water(bbox, grid, verbose=False)
        canvas = np.zeros((grid, grid, 3), np.uint8)
        canvas[..., 2] = (np.clip(osm["outline"], 0, 1) * 90).astype(np.uint8)
        canvas[..., 2] = np.maximum(canvas[..., 2],
                                    (np.clip(osm["filled"], 0, 1) * 200).astype(np.uint8))
        plate = resample(plate_water_mask(water_stl, stl),
                         int(round(res.get("span_m", span) / res["cell_size_m"])))
        for pos, channel in ((("lat", "lon"), 0), (None, 1)):
            if pos is None:
                truth = center_from_ground_truth(key)
                if truth is None:
                    continue
                lat, lon = truth["lat"], truth["lon"]
            else:
                if res.get("lat") is None:
                    continue
                lat, lon = res["lat"], res["lon"]
            cy = (lat - S) / (N - S) * grid
            cx = (lon - W) / (E - W) * grid
            _stamp(canvas, plate, cy, cx, channel)
        cv2.imwrite(str(PROBE_DIR / f"{key}.png"), np.flipud(canvas))
        print(f"{key}: {'ok' if res.get('ok') else res.get('reason')}", flush=True)
    return 0


def _stamp(canvas: np.ndarray, mask: np.ndarray, cy: float, cx: float,
           channel: int) -> None:
    """Draw `mask` into one colour channel with its centre at (cy, cx)."""
    h, w = mask.shape
    y0, x0 = int(round(cy - h / 2)), int(round(cx - w / 2))
    ys = slice(max(0, y0), min(canvas.shape[0], y0 + h))
    xs = slice(max(0, x0), min(canvas.shape[1], x0 + w))
    if ys.start >= ys.stop or xs.start >= xs.stop:
        return
    sub = mask[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
    canvas[ys, xs, channel] = np.maximum(canvas[ys, xs, channel],
                                         (np.clip(sub, 0, 1) * 255).astype(np.uint8))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="plate centre resolution")
    ap.add_argument("--validate", action="store_true",
                    help="score the solver against the hand alignments")
    ap.add_argument("--validate-buildings", action="store_true",
                    help="score the no-water building solver, including plates with no water")
    ap.add_argument("--dump", action="store_true",
                    help="render the search window and the solved position")
    ap.add_argument("cities", nargs="*", help="restrict to these city slugs")
    args = ap.parse_args(argv)

    only = args.cities or None
    if args.validate:
        return validate(only)
    if args.validate_buildings:
        return validate_buildings(only)
    if args.dump:
        return dump(only)

    for key, region, stl, water, span, _wide in _plates(only):
        print(f"{key:22s} {resolve_center(region, water, stl, span)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
