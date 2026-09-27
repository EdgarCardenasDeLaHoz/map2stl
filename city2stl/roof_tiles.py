"""Per-building satellite crops at full zoom, with a tile cache.

`fetch_region_satellite` fetches a whole bbox and caps the job at 400 tiles, so
a city-sized box silently walks the zoom down until it fits. That is why the
Cartagena run classified at about 1.2 m per pixel: a ten-metre house was eight
pixels across, and no roof shape survives at eight pixels. The tier that
answered was the one that needed the least evidence.

Labels are sparse -- tens to hundreds of tagged roofs per city -- so nothing
here needs a city-sized image. Fetch only the tiles each footprint touches, at
zoom 18 (about 0.4 to 0.6 m per pixel depending on latitude), and cache them on
disk so neighbouring buildings that share a tile cost one request between them.

Tile math and fetching are ``geo2stl.imagery``; this module adds the on-disk
tile cache and the per-footprint crop.
"""
import math
import os

import numpy as np

from geo2stl import imagery

TILE = imagery.TILE_SIZE
# Shared with the rest of the project's caches rather than kept beside the
# module, so a checkout can be wiped without losing the tiles.
CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cache", "roof_tiles")
_session = None


def _sess():
    global _session
    if _session is None:
        _session = imagery.new_session()
    return _session


def lon_to_gpx(lon, z):
    return imagery.lon_to_global_px(lon, z)


def lat_to_gpy(lat, z):
    return imagery.lat_to_global_px(lat, z)


def m_per_px(lat, z):
    return imagery.m_per_px(lat, z)


def tile(z, x, y):
    """One 256×256 tile as RGB, cached on disk. None if it cannot be had."""
    return imagery.fetch_tile(z, x, y, session=_sess(), cache_dir=CACHE, timeout=20)


def prefetch_bbox(north, south, east, west, zoom=18, workers=16, log=None):
    """Fill the cache with every tile under a bounding box, concurrently.

    `crop_for_ring` fetches serially, one building at a time, which is fine for
    the few hundred tagged roofs a training harvest reads.  Classifying a whole
    city is a different job: Granada has 19 521 buildings and the serial path
    was returning about four tiles a minute, so an export that should take
    minutes was heading for hours.  The tiles themselves are few -- a 3 km box
    at zoom 18 is roughly four hundred of them -- so the cost is entirely
    round-trip latency, and fetching them in parallel up front removes it.

    Returns (fetched, already_cached, failed).  Failures are not raised: a tile
    that will not come is handled downstream by `crop_for_ring`, which skips
    missing tiles and only gives up when a footprint has none at all.
    """
    from concurrent.futures import ThreadPoolExecutor

    x0 = lon_to_gpx(min(west, east), zoom)
    x1 = lon_to_gpx(max(west, east), zoom)
    y0 = lat_to_gpy(max(north, south), zoom)
    y1 = lat_to_gpy(min(north, south), zoom)
    tx0, tx1 = int(x0 // TILE), int(x1 // TILE)
    ty0, ty1 = int(y0 // TILE), int(y1 // TILE)

    todo, cached = [], 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            if imagery.tile_cache_path(CACHE, zoom, tx, ty).exists():
                cached += 1
            else:
                todo.append((tx, ty))

    if log:
        log(f"prefetching {len(todo):d} tiles ({cached:d} already cached) "
            f"at zoom {zoom:d}")
    if not todo:
        return 0, cached, 0

    # A Session per worker: the module-level one is shared state and requests
    # does not promise it is safe to drive from several threads at once.
    local = {}

    def one(t):
        tx, ty = t
        import threading
        key = threading.get_ident()
        s = local.get(key)
        if s is None:
            s = local[key] = imagery.new_session()
        return imagery.fetch_tile(zoom, tx, ty, session=s, cache_dir=CACHE,
                                  timeout=20) is not None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ok = sum(1 for r in pool.map(one, todo) if r)
    if log:
        log(f"prefetch done: {ok:d} fetched, {len(todo) - ok:d} failed")
    return ok, cached, len(todo) - ok


def crop_for_ring(ring, zoom=18, pad=1.0, max_px=768):
    """The image around one footprint, plus the geo bounds it covers.

    `pad` is a multiple of the footprint's own size added on every side, so a
    small house still gets context and a large block does not drown in it.
    Returns (rgb, north, south, east, west, m_per_px) or None when no tile
    under the footprint could be fetched.
    """
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    clat = 0.5 * (min(lats) + max(lats))

    x0 = lon_to_gpx(min(lons), zoom)
    x1 = lon_to_gpx(max(lons), zoom)
    y0 = lat_to_gpy(max(lats), zoom)      # north edge is the smaller y
    y1 = lat_to_gpy(min(lats), zoom)
    px = max((x1 - x0) * pad, 8.0)
    py = max((y1 - y0) * pad, 8.0)
    x0, x1 = x0 - px, x1 + px
    y0, y1 = y0 - py, y1 + py
    if (x1 - x0) > max_px or (y1 - y0) > max_px:
        return None                       # a stadium, not a roof we can model

    tx0, tx1 = int(math.floor(x0 / TILE)), int(math.floor((x1 - 1e-6) / TILE))
    ty0, ty1 = int(math.floor(y0 / TILE)), int(math.floor((y1 - 1e-6) / TILE))
    big, got = imagery.stitch_tiles(tx0, tx1, ty0, ty1, zoom, session=_sess(),
                                    cache_dir=CACHE, timeout=20)
    if got == 0:
        return None

    ox, oy = tx0 * TILE, ty0 * TILE
    box = (int(x0 - ox), int(y0 - oy), int(math.ceil(x1 - ox)),
           int(math.ceil(y1 - oy)))
    rgb = np.asarray(big.crop(box), dtype=np.uint8)
    if rgb.shape[0] < 8 or rgb.shape[1] < 8:
        return None

    # Geo bounds of the crop, so the caller can rasterise the ring into it.
    west, north = imagery.global_px_to_lonlat(ox + box[0], oy + box[1], zoom)
    east, south = imagery.global_px_to_lonlat(ox + box[2], oy + box[3], zoom)
    return rgb, north, south, east, west, m_per_px(clat, zoom)
