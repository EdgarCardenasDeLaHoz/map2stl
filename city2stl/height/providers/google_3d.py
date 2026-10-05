"""
Google 3D Tiles height provider — photogrammetric building heights.

Data: Cesium 3D Tiles (glTF/glb), sub-metre resolution.
Source: Google Map Tiles API (Photorealistic 3D Tiles).
Auth: Google Maps API key (``GOOGLE_MAPS_API_KEY`` env var or config.json).
Coverage: Major cities globally.

Extracts a Digital Surface Model by binning points from the tile meshes into
the output grid and keeping the highest one per cell. The points are the mesh
vertices plus samples spread over the faces in proportion to area, so a coarse
tile whose triangles span many cells still fills them. Ray-casting would be
more faithful, but trimesh without embree cannot run a quarter of a million
rays against a city block of geometry.

The raster returned is height above ground, not altitude. The tiles carry
WGS84 ellipsoidal altitude and every DEM the project fetches is orthometric,
so the ground is taken from the meshes themselves and the datum cancels --
see ``_ground_from_dsm``. A caller may pass a DEM instead.

Measured against Miami's registered STL plate, this is the most accurate
height source in the project: 14.4 m mean absolute error over 61 footprints,
8.9 m on the buildings above 100 m that the panoramic pipeline reads as a
third of their true height. See ``map2stl/docs/issues.md`` section 0c.
"""

from __future__ import annotations

import functools
import io
import json
import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import numpy as np
import requests

from city2stl.height import BBox, HeightResult, _resample
from geo2stl.cache import (
    make_cache_key,
    read_array_cache,
    write_array_cache,
)
from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

logger = logging.getLogger(__name__)

_CONFIDENCE = 0.9  # high — photogrammetric mesh
_RESOLUTION_M = 1.0  # sub-metre mesh, output at ~1 m
_NAMESPACE = "google3d"
_REQUEST_TIMEOUT = 30  # seconds per tile
# Tile fetches are network-bound and independent. Kept modest so a
# region run does not saturate the Map Tiles API quota.
_DOWNLOAD_WORKERS = 12
# Neighbourhood used to estimate local ground from the mesh, and the
# percentile of it taken as ground. 200 m is wide enough to contain some
# street in a dense downtown without spanning real terrain relief.
_GROUND_WINDOW_M = 200.0
_GROUND_PERCENTILE = 5.0
_GROUND_MIN_CELLS = 20
# Ceiling on points drawn from one tile. A coarse tile can span the whole
# window, and without a cap the sample count grows with its area.
_MAX_SAMPLES_PER_TILE = 400_000
# A window this many metres across is about a city block, and a built one
# spreads at least this many metres between its street and its roofs.
# Measured over 1 km windows: Miami 104 m of relief, Rio 76, Buenos Aires
# 47, Santiago 44 -- against Cartagena 1.2, Panama City 4.0, Barranquilla
# 4.3, all of which are cities of towers served as bare terrain.
_BUILT_WINDOW_M = 250.0
_BUILT_MIN_RELIEF_M = 8.0
_BUILT_MIN_FRACTION = 0.1
# Safety guard on how many tiles one call pulls. Rarely the binding
# constraint now: over a 3 km window, budgets of 500 and 4000 both filled 99%
# of the grid, because the walk stops at the target error long before the
# budget runs out.
_MAX_TILES = 1000
# Descend the tile tree until a node's geometricError is this fine, expressed
# as a multiple of the output cell. Geometry finer than the cell is discarded
# by the binning, so descending past it costs tiles and time for nothing --
# see the module note in ``_target_error_m``.
_ERROR_PER_CELL = 6.0
# Floor and ceiling on that, for grids coarse or fine enough that the
# multiple alone would leave the useful part of the tree.
_MIN_TARGET_ERROR_M = 4.0
_MAX_TARGET_ERROR_M = 64.0

# Register cache TTL
import geo2stl.cache as _cache_mod  # noqa: E402

_cache_mod.NAMESPACE_TTL.setdefault(_NAMESPACE, 30 * 86400)

_ROOT_URL = "https://tile.googleapis.com/v1/3dtiles/root.json"


# ── API key resolution ──────────────────────────────────────────

def get_api_key() -> str | None:
    """Google Maps API key from ``GOOGLE_MAPS_API_KEY`` or ``config.json``."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if key:
        return key
    # map2stl/config.json (where the app keeps its other keys) first, then the
    # workspace one above it, which is where this used to look exclusively.
    here = Path(__file__).resolve()
    for cfg_path in (here.parents[3] / "config.json", here.parents[4] / "config.json"):
        try:
            if cfg_path.exists():
                key = json.loads(cfg_path.read_text()).get("google_maps_api_key")
                if key:
                    return key
        except Exception:
            logger.debug("Could not read %s", cfg_path, exc_info=True)
    return None


# ── ECEF ↔ WGS84 transforms ────────────────────────────────────

# WGS84 ellipsoid constants
_A = 6378137.0           # semi-major axis (m)
_F = 1 / 298.257223563   # flattening
_B = _A * (1 - _F)       # semi-minor axis
_E2 = 1 - (_B / _A) ** 2  # first eccentricity squared


def ecef_to_wgs84(x: np.ndarray, y: np.ndarray,
                  z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert ECEF (m) to WGS84 (lon°, lat°, alt_m).

    Uses Bowring's iterative method (converges in 2-3 iterations).
    """
    lon = np.degrees(np.arctan2(y, x))

    p = np.sqrt(x ** 2 + y ** 2)
    lat = np.arctan2(z, p * (1 - _E2))  # initial estimate

    for _ in range(5):
        sin_lat = np.sin(lat)
        N = _A / np.sqrt(1 - _E2 * sin_lat ** 2)
        lat = np.arctan2(z + _E2 * N * sin_lat, p)

    sin_lat = np.sin(lat)
    cos_lat = np.cos(lat)
    N = _A / np.sqrt(1 - _E2 * sin_lat ** 2)

    alt = np.where(
        np.abs(cos_lat) > 1e-10,
        p / cos_lat - N,
        np.abs(z) / np.abs(sin_lat) - N * (1 - _E2),
    )

    return lon, np.degrees(lat), alt


def wgs84_to_ecef(lon: float, lat: float,
                  alt: float = 0.0) -> tuple[float, float, float]:
    """Convert a single WGS84 point to ECEF (m)."""
    lon_r, lat_r = math.radians(lon), math.radians(lat)
    sin_lat = math.sin(lat_r)
    cos_lat = math.cos(lat_r)
    N = _A / math.sqrt(1 - _E2 * sin_lat ** 2)
    x = (N + alt) * cos_lat * math.cos(lon_r)
    y = (N + alt) * cos_lat * math.sin(lon_r)
    z = (N * (1 - _E2) + alt) * sin_lat
    return x, y, z


# ── Bounding-volume helpers ─────────────────────────────────────

@functools.lru_cache(maxsize=64)
def _bbox_ecef_aabb(bbox: BBox) -> tuple[np.ndarray, np.ndarray]:
    """Axis-aligned ECEF bounds of *bbox*, as (lo, hi).

    Built from the window's corners over a range of altitudes. Tile volumes
    hug the terrain surface, so a window taken only at the ellipsoid can fail
    to meet a tile that sits a few hundred metres above or below it.
    """
    north, south, east, west = bbox
    pts = [
        wgs84_to_ecef(float(lon), float(lat), alt)
        for lat in np.linspace(south, north, 3)
        for lon in np.linspace(west, east, 3)
        for alt in (-500.0, 0.0, 1000.0)
    ]
    arr = np.asarray(pts, dtype=np.float64)
    return arr.min(axis=0), arr.max(axis=0)


def _bv_intersects_bbox(bounding_volume: dict,
                        bbox: BBox) -> bool:
    """Check whether a 3D Tiles bounding volume intersects *bbox*.

    Supports ``region`` (lon/lat, tested exactly), ``box`` (an ECEF oriented
    box) and ``sphere`` (ECEF centre and radius).

    Box and sphere are tested as axis-aligned ECEF bounds rather than by
    projecting the volume into lon/lat. Projection is unreliable for anything
    larger than a city -- the corner hull spans arbitrary longitudes -- and
    most of this tree is larger than a city.
    """
    north, south, east, west = bbox

    if "region" in bounding_volume:
        # region = [west, south, east, north, minHeight, maxHeight] in radians
        r = bounding_volume["region"]
        rw, rs, re, rn = [math.degrees(r[i]) for i in range(4)]
        return not (rn < south or rs > north or re < west or rw > east)

    if "box" in bounding_volume:
        b = bounding_volume["box"]
        centre = np.array([b[0], b[1], b[2]], dtype=np.float64)
        axes = np.array([b[3:6], b[6:9], b[9:12]], dtype=np.float64)
        # An oriented box is contained by its centre plus the summed absolute
        # extent of its half-axes along each ECEF axis.
        reach = np.abs(axes).sum(axis=0)
        lo, hi = centre - reach, centre + reach
    elif "sphere" in bounding_volume:
        sp = bounding_volume["sphere"]
        centre = np.array([sp[0], sp[1], sp[2]], dtype=np.float64)
        radius = float(sp[3])
        lo, hi = centre - radius, centre + radius
    else:
        # Unknown type -- be conservative, assume it intersects
        return True

    win_lo, win_hi = _bbox_ecef_aabb(bbox)
    return bool(np.all(hi >= win_lo) and np.all(lo <= win_hi))


# ── Tileset traversal ───────────────────────────────────────────

def _inherit_session(url: str, parent_url: str) -> str:
    """Add *parent_url*'s session token to *url* when it lacks one.

    Google's Map Tiles API issues a session token with the root tileset and
    rejects any tile request that omits it. Child URIs are root-absolute
    paths, so resolving them against the parent drops the parent's query
    string along with the token.
    """
    if "session=" in url:
        return url
    parent_session = parse_qs(urlparse(parent_url).query).get("session")
    if not parent_session:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}session={parent_session[0]}"


def _is_subtileset(uri: str) -> bool:
    """True when *uri* points at a tileset JSON rather than at glTF geometry.

    The suffix has to be read off the path: Google appends a session token as
    a query parameter, so the raw URI ends in the token, not in ".json".
    """
    return urlparse(uri).path.lower().endswith(".json")


def _fetch_subtilesets(urls: list[str]) -> list[tuple[dict, str]]:
    """Fetch sub-tileset documents concurrently, dropping the ones that fail.

    Returns (root node, url) pairs; the url is needed as the base for
    resolving that document's own child URIs.
    """
    local = threading.local()

    def _one(url: str):
        sess = getattr(local, "session", None)
        if sess is None:
            sess = local.session = requests.Session()
        try:
            resp = sess.get(url, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            root = resp.json().get("root")
        except Exception as exc:
            logger.debug("Failed to fetch sub-tileset %s: %s", url, exc)
            return None
        return (root, url) if root else None

    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as pool:
        return [r for r in pool.map(_one, urls) if r is not None]


def _collect_content_uris(
    tileset: dict,
    bbox: BBox,
    api_key: str,
    session: requests.Session,
    max_tiles: int = _MAX_TILES,
    target_error_m: float | None = None,
) -> list[str]:
    """Walk the 3D Tiles tree and collect content URIs intersecting *bbox*.

    The walk is level-order, and each level's sub-tileset documents are
    fetched together. Google publishes about one routing document per leaf
    tile, so a depth-first walk spends most of its time waiting on one
    request at a time.

    Returns absolute URLs for .glb content.
    """
    if target_error_m is None:
        target_error_m = _MAX_TARGET_ERROR_M

    uris: list[str] = []
    # Nodes whose own geometry was passed over in favour of their children.
    # Kept so a tree that yields no leaves at all still produces something.
    deferred: list[str] = []

    def _absolute(uri: str, base_url: str) -> str:
        # Google returns root-absolute paths ("/v1/3dtiles/datasets/..."),
        # not paths relative to the parent document, and urljoin handles the
        # absolute, relative and full-URL forms alike. The session token
        # lives in the parent's query string, which urljoin discards, so it
        # has to be carried across explicitly.
        full = _inherit_session(urljoin(base_url, uri), base_url)
        sep = "&" if "?" in full else "?"
        return f"{full}{sep}key={api_key}"

    root = tileset.get("root")
    frontier: list[tuple[dict, str]] = [(root, _ROOT_URL)] if root else []

    while frontier and len(uris) < max_tiles:
        pending: list[str] = []          # sub-tilesets to fetch for next level
        next_nodes: list[tuple[dict, str]] = []

        for node, base_url in frontier:
            if len(uris) >= max_tiles:
                break
            bv = node.get("boundingVolume", {})
            if bv and not _bv_intersects_bbox(bv, bbox):
                continue

            content = node.get("content", {})
            uri = content.get("uri") or content.get("url")
            children = node.get("children", [])

            if uri and _is_subtileset(uri):
                # A routing node: no geometry of its own, always followed.
                pending.append(_absolute(uri, base_url))
                continue

            # Descend past content coarser than we need. Google publishes
            # renderable geometry at every level, so taking the first content
            # found fills the budget with globe-scale meshes and never
            # reaches city detail.
            too_coarse = float(node.get("geometricError", 0.0)) > target_error_m
            if children and (too_coarse or not uri):
                if uri:
                    deferred.append(_absolute(uri, base_url))
                next_nodes.extend((child, base_url) for child in children)
                continue

            if uri:
                uris.append(_absolute(uri, base_url))

        for sub_root, sub_url in _fetch_subtilesets(pending[:max_tiles]):
            next_nodes.append((sub_root, sub_url))
        frontier = next_nodes

    if not uris and deferred:
        logger.info("Google 3D Tiles: no leaf geometry reached target error "
                    "%.2f m; falling back to %d coarser tiles",
                    target_error_m, min(len(deferred), max_tiles))
        uris = deferred[:max_tiles]

    logger.info("Google 3D Tiles: collected %d tile URIs", len(uris))
    return uris[:max_tiles]


def _target_error_m(bbox: BBox, dim: tuple[int, int]) -> float:
    """Geometric error to stop descending at, given the output grid.

    A tile whose geometric error is well below the cell size adds nothing the
    raster can represent, and the tiles get smaller as the error does, so
    every extra level multiplies the fetch. Tying the two together means a
    finer export descends further without anyone editing a constant.
    """
    north, south, east, west = bbox
    h, w = dim
    cell_lat_m = (north - south) * M_PER_DEG_LAT / max(h, 1)
    cell_lon_m = (east - west) * m_per_deg_lon((north + south) / 2) / max(w, 1)
    cell_m = max(min(cell_lat_m, cell_lon_m), 0.1)
    return float(np.clip(cell_m * _ERROR_PER_CELL,
                         _MIN_TARGET_ERROR_M, _MAX_TARGET_ERROR_M))


def _surface_points(mesh, target_spacing_m: float) -> np.ndarray:
    """Return mesh vertices plus points sampled across its faces.

    The samples are what let a coarse tile fill the grid: one per
    ``target_spacing_m`` squared of surface area, so a triangle spanning many
    output cells contributes to all of them rather than only to the three
    holding its corners. Vertices are kept as well because they carry the
    roof edges and ridges exactly, and sampling alone would round them off.
    """
    v = np.asarray(mesh.vertices, dtype=np.float64)
    if v.size == 0:
        return v

    try:
        area = float(mesh.area)
    except Exception:
        return v
    if not np.isfinite(area) or area <= 0:
        return v

    n = int(min(area / (target_spacing_m ** 2), _MAX_SAMPLES_PER_TILE))
    if n <= len(v):
        return v

    try:
        from trimesh.sample import sample_surface
        pts, _ = sample_surface(mesh, n)
    except Exception as exc:
        logger.debug("Surface sampling failed, using vertices: %s", exc)
        return v
    return np.vstack([v, np.asarray(pts, dtype=np.float64)])


def _sample_spacing_m(bbox: BBox, dim: tuple[int, int]) -> float:
    """Point spacing that fills the output grid without oversampling it."""
    north, south, _east, _west = bbox
    lat_m = (north - south) * M_PER_DEG_LAT
    return max(lat_m / max(dim[0], 1) * 0.5, 0.5)


def _new_dsm_acc(dim: tuple[int, int]) -> np.ndarray:
    """Empty accumulator for :func:`_accumulate_mesh`.

    Filled with -inf rather than NaN so ``np.maximum.at`` reduces correctly
    over repeated cells; NaN would poison every max it took part in.
    """
    return np.full(dim[0] * dim[1], -np.inf, dtype=np.float64)


def _finish_dsm(acc: np.ndarray, dim: tuple[int, int]) -> np.ndarray:
    """Turn an accumulator into a (H, W) raster, unfilled cells NaN."""
    return np.where(np.isinf(acc), np.nan, acc).reshape(dim).astype(np.float32)


def _accumulate_mesh(acc: np.ndarray, mesh, bbox: BBox,
                     dim: tuple[int, int], spacing_m: float) -> None:
    """Bin one mesh's surface points into *acc*, keeping the highest per cell.

    Taking the highest point per cell reproduces the roof surface directly.
    This replaced a downward ray-cast, which needed an optional native
    intersector and raised on the numpy fallback at this ray count.

    Meshes are folded in one at a time so that peak memory follows the
    download chunk rather than the tile budget.
    """
    north, south, east, west = bbox
    h, w = dim

    p = _surface_points(mesh, spacing_m)
    if p.size == 0:
        return
    # Undo the loader's Y-up to Z-up rotation. glTF declares Y-up, so
    # trimesh rotates every scene by +90 degrees about X on load; Cesium
    # tiles are already in ECEF, so that rotation displaces them. Without
    # this, Miami's tiles arrive correctly shaped but at 70 E, 63 N.
    x, y, z = p[:, 0], -p[:, 2], p[:, 1]
    lon, lat, alt = ecef_to_wgs84(x, y, z)

    col = ((lon - west) / (east - west) * w).astype(np.int64)
    row = ((north - lat) / (north - south) * h).astype(np.int64)
    keep = ((col >= 0) & (col < w) & (row >= 0) & (row < h)
            & np.isfinite(alt))
    if not keep.any():
        return

    np.maximum.at(acc, row[keep] * w + col[keep], alt[keep])


# ── HeightProvider implementation ────────────────────────────────

def _looks_built(raster: np.ndarray, bbox: BBox) -> bool:
    """True if *raster* has buildings in it rather than bare terrain.

    See ``_BUILT_MIN_RELIEF_M`` for why relief over a block-sized window is
    the discriminator. Only a small fraction of windows has to clear the
    threshold: a bbox is allowed to be mostly water, park or low-rise and
    still be a covered city, and the failure being guarded against is the one
    where nothing anywhere stands up.
    """
    north, south, east, west = bbox
    h, w = raster.shape
    lat_m = (north - south) * M_PER_DEG_LAT
    lon_m = (east - west) * m_per_deg_lon((north + south) / 2)
    win_r = max(2, int(round(_BUILT_WINDOW_M / max(lat_m / h, 1e-6))))
    win_c = max(2, int(round(_BUILT_WINDOW_M / max(lon_m / w, 1e-6))))

    total = built = 0
    for r0 in range(0, h - win_r + 1, win_r):
        for c0 in range(0, w - win_c + 1, win_c):
            block = raster[r0:r0 + win_r, c0:c0 + win_c]
            vals = block[np.isfinite(block)]
            if vals.size < 0.3 * win_r * win_c:
                continue
            total += 1
            if np.percentile(vals, 98) - np.percentile(vals, 2) >= _BUILT_MIN_RELIEF_M:
                built += 1
    if not total:
        return False
    return built / total >= _BUILT_MIN_FRACTION


def _ground_from_dsm(dsm: np.ndarray, bbox: BBox,
                     window_m: float = _GROUND_WINDOW_M,
                     percentile: float = _GROUND_PERCENTILE) -> np.ndarray:
    """Estimate the ground surface from the DSM itself.

    An external DEM is the obvious way to turn surface altitude into building
    height, but it is the wrong datum: this mesh carries WGS84 ellipsoidal
    altitude while elevation products are orthometric, and the geoid
    separation between them is tens of metres and varies by city. Getting
    that wrong is a constant bias on every building.

    Taking the ground from the mesh sidesteps the datum entirely, because
    both terms then come from the same source. Within a neighbourhood a few
    hundred metres across, some cells fall on streets or open ground, so a
    low percentile of the local altitudes approximates the ground. This holds
    where the terrain is gentler than the buildings are tall -- true of the
    coastal cities this is aimed at, and false in steep terrain, where an
    external DEM plus a geoid model would be the honest answer.
    """
    north, south, east, west = bbox
    h, w = dsm.shape
    lat_m = (north - south) * M_PER_DEG_LAT
    lon_m = (east - west) * m_per_deg_lon((north + south) / 2)
    win_r = max(1, int(round(window_m / max(lat_m / h, 1e-6))))
    win_c = max(1, int(round(window_m / max(lon_m / w, 1e-6))))

    ground = np.full((h, w), np.nan, dtype=np.float32)
    for r0 in range(0, h, win_r):
        for c0 in range(0, w, win_c):
            # Read a window twice the stride so neighbouring blocks overlap
            # and the ground surface does not step at the block seams.
            block = dsm[max(0, r0 - win_r // 2):r0 + win_r + win_r // 2,
                        max(0, c0 - win_c // 2):c0 + win_c + win_c // 2]
            vals = block[np.isfinite(block)]
            if vals.size < _GROUND_MIN_CELLS:
                continue
            ground[r0:r0 + win_r, c0:c0 + win_c] = np.percentile(vals, percentile)

    # Blocks with too little geometry are left unknown; fill them from the
    # scene median so a sparse fetch still yields heights near its edges.
    finite = ground[np.isfinite(ground)]
    if finite.size:
        ground = np.where(np.isfinite(ground), ground, np.median(finite))
    return ground


class Google3DProvider:
    """Google Photorealistic 3D Tiles building height provider."""

    name = "google3d"

    def __init__(self, api_key: str | None = None,
                 max_tiles: int = _MAX_TILES):
        self._api_key = api_key or get_api_key()
        self._max_tiles = max_tiles

    def covers(self, bbox: BBox) -> bool:
        """Returns True if an API key is configured (coverage is global
        for major cities, but we can't know without querying)."""
        return self._api_key is not None

    def fetch_heights(self, bbox: BBox, dim: tuple[int, int],
                      dem: np.ndarray | None = None) -> HeightResult:
        """Fetch 3D Tiles for *bbox* and return height above ground.

        Parameters
        ----------
        bbox : (north, south, east, west)
        dim  : (H, W) target output shape
        dem  : Optional terrain DEM array (H, W) in metres (WGS84 ellipsoidal
               altitude). If None, the ground is estimated from the mesh
               itself, which avoids the ellipsoidal-versus-orthometric datum
               mismatch an external DEM would introduce.
        """
        if not self._api_key:
            logger.warning("Google 3D Tiles: no API key configured")
            return _empty_result(dim)

        north, south, east, west = bbox

        # Check cache first
        # dem and max_tiles change the result, so they are part of the key;
        # without them a DEM-grounded raster was served to mesh-grounded calls.
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   extra={"dim": list(dim), "dem": dem is not None,
                                          "max_tiles": self._max_tiles})
        hit = read_array_cache(_NAMESPACE, cache_key)
        if hit is not None:
            arrays, _meta = hit
            raster = arrays.get("height")
            if raster is not None:
                confidence = np.where(np.isnan(raster), 0.0,
                                      _CONFIDENCE).astype(np.float32)
                return HeightResult(
                    raster=raster,
                    confidence=confidence,
                    source_name=self.name,
                    resolution_m=_RESOLUTION_M,
                )

        # Fetch tileset
        session = requests.Session()
        try:
            root_url = f"{_ROOT_URL}?key={self._api_key}"
            r = session.get(root_url, timeout=_REQUEST_TIMEOUT)
            r.raise_for_status()
            tileset = r.json()
        except Exception as exc:
            logger.warning("Google 3D Tiles: failed to fetch root: %s", exc)
            return _empty_result(dim)

        # Collect content URIs
        target_error_m = _target_error_m(bbox, dim)
        uris = _collect_content_uris(tileset, bbox, self._api_key, session,
                                     max_tiles=self._max_tiles,
                                     target_error_m=target_error_m)
        if not uris:
            logger.info("Google 3D Tiles: no tiles found for bbox")
            return _empty_result(dim)

        logger.info("Google 3D Tiles: downloading %d tiles", len(uris))

        # Download and parse meshes. The tiles are independent and the loop is
        # network-bound, so it runs on a thread pool; each worker keeps its
        # own session because requests.Session is not documented as safe to
        # share across threads.
        import trimesh
        local = threading.local()

        def _load_tile(uri: str):
            sess = getattr(local, "session", None)
            if sess is None:
                sess = local.session = requests.Session()
            try:
                resp = sess.get(uri, timeout=_REQUEST_TIMEOUT)
                resp.raise_for_status()
                scene_or_mesh = trimesh.load(
                    io.BytesIO(resp.content),
                    file_type="glb",
                    process=False,
                )
            except Exception as exc:
                logger.debug("Failed to load tile: %s", exc)
                return None
            if isinstance(scene_or_mesh, trimesh.Scene):
                # Concatenate through the scene graph, not over ``.geometry``
                # -- Cesium tiles carry their ECEF placement in the node
                # transforms, and reading the primitives directly leaves every
                # vertex in a local frame.
                merged = scene_or_mesh.to_geometry()
                return merged if isinstance(merged, trimesh.Trimesh) else None
            if isinstance(scene_or_mesh, trimesh.Trimesh):
                return scene_or_mesh
            return None

        acc = _new_dsm_acc(dim)
        spacing_m = _sample_spacing_m(bbox, dim)
        n_meshes = 0
        chunk = _DOWNLOAD_WORKERS * 4
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as pool:
            for i in range(0, len(uris), chunk):
                for mesh in pool.map(_load_tile, uris[i:i + chunk]):
                    if mesh is None:
                        continue
                    _accumulate_mesh(acc, mesh, bbox, dim, spacing_m)
                    n_meshes += 1

        if not n_meshes:
            logger.info("Google 3D Tiles: no valid meshes parsed")
            return _empty_result(dim)

        dsm = _finish_dsm(acc, dim)

        # Convert DSM to height above ground. A caller-supplied DEM wins, but
        # the default is to take the ground from the mesh -- see
        # ``_ground_from_dsm`` for why that is not just a fallback.
        if dem is not None:
            ground = _resample(dem.astype(np.float32), dim)
        else:
            ground = _ground_from_dsm(dsm, bbox)
        raster = (dsm - ground).astype(np.float32)
        # Below the ground estimate means ground, not a negative building.
        raster = np.where(raster < 0, 0.0, raster).astype(np.float32)
        raster[np.isnan(dsm)] = np.nan

        # Tiles outside Google's photorealistic cities are bare terrain, and
        # nothing about the fetch says so -- see ``_looks_built``.
        if not _looks_built(raster, bbox):
            logger.warning(
                "Google 3D Tiles: %d tiles over this bbox contain no "
                "buildings (surface relief below %.0f m); the area is "
                "probably outside photorealistic coverage",
                n_meshes, _BUILT_MIN_RELIEF_M)
            return _empty_result(dim)

        confidence = np.where(np.isnan(raster), 0.0,
                              _CONFIDENCE).astype(np.float32)

        # Cache result
        write_array_cache(_NAMESPACE, cache_key, {"height": raster},
                          metadata={"n_tiles": len(uris),
                                    "resolution_m": _RESOLUTION_M})

        return HeightResult(
            raster=raster,
            confidence=confidence,
            source_name=self.name,
            resolution_m=_RESOLUTION_M,
        )


def _empty_result(dim: tuple[int, int]) -> HeightResult:
    return HeightResult.empty(dim, "google3d", _RESOLUTION_M)
