"""OSM water for plate placement: Overpass selectors, fetch, per-layer cache, rasters.

Promoted 2026-09-27 from ``tools/align_tool/locate.py`` (its "OSM water" section),
so the street placement (``city2stl.registration.street_place``) and the web app can
import it; ``locate`` re-exports every name, so ``locate.osm_water`` / ``locate._overpass``
still work for the other tools.  Rasters are square grids with row 0 = south.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from city2stl.registration.align_paths import WATER_CACHE
from city2stl.registration.correlate import edges
from geo2stl import osm as _osm
from geo2stl.geo import M_PER_DEG_LAT

OSM_CACHE: Path = WATER_CACHE

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
        for (x0, y0), (x1, y1) in zip(px, px[1:], strict=False):
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
    plate raster -- the same correction `osm_raster._rasterize_buildings` makes.
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
