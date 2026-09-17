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

Same ESRI World Imagery endpoint the rest of the project uses.
"""
import io
import math
import os

import numpy as np
import requests
from PIL import Image

TILE_URL = ("https://server.arcgisonline.com/ArcGIS/rest/services"
            "/World_Imagery/MapServer/tile/{z}/{y}/{x}")
TILE = 256
# Shared with the rest of the project's caches rather than kept beside the
# module, so a checkout can be wiped without losing the tiles.
CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cache", "roof_tiles")
_session = None


def _sess():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = "3dmaps-research/1.0"
    return _session


def lon_to_gpx(lon, z):
    return (lon + 180.0) / 360.0 * TILE * (2 ** z)


def lat_to_gpy(lat, z):
    s = math.sin(math.radians(max(-85.05, min(85.05, lat))))
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    return y * TILE * (2 ** z)


def m_per_px(lat, z):
    return 40075016.686 * math.cos(math.radians(lat)) / (TILE * 2 ** z)


def tile(z, x, y):
    """One 256×256 tile as RGB, cached on disk. None if it cannot be had."""
    n = 2 ** z
    if not (0 <= x < n and 0 <= y < n):
        return None
    path = os.path.join(CACHE, str(z), str(x), f"{y:d}.jpg")
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGB")
        except Exception:                                   # noqa: BLE001
            os.remove(path)
    try:
        r = _sess().get(TILE_URL.format(z=z, x=x, y=y), timeout=20)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:                                       # noqa: BLE001
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        img.save(path, "JPEG", quality=90)
    except Exception:                                       # noqa: BLE001
        pass
    return img


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
            if os.path.exists(os.path.join(CACHE, str(zoom), str(tx),
                                           f"{ty:d}.jpg")):
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
            s = local[key] = requests.Session()
            s.headers["User-Agent"] = "3dmaps-research/1.0"
        path = os.path.join(CACHE, str(zoom), str(tx), f"{ty:d}.jpg")
        try:
            r = s.get(TILE_URL.format(z=zoom, x=tx, y=ty), timeout=20)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            img.save(path, "JPEG", quality=90)
            return True
        except Exception:                                   # noqa: BLE001
            return False

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
    big = Image.new("RGB", ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE))
    got = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            t = tile(zoom, tx, ty)
            if t is None:
                continue
            big.paste(t, ((tx - tx0) * TILE, (ty - ty0) * TILE))
            got += 1
    if got == 0:
        return None

    ox, oy = tx0 * TILE, ty0 * TILE
    box = (int(x0 - ox), int(y0 - oy), int(math.ceil(x1 - ox)),
           int(math.ceil(y1 - oy)))
    rgb = np.asarray(big.crop(box), dtype=np.uint8)
    if rgb.shape[0] < 8 or rgb.shape[1] < 8:
        return None

    # Geo bounds of the crop, so the caller can rasterise the ring into it.
    def gpx_to_lon(gx):
        return gx / (TILE * 2 ** zoom) * 360.0 - 180.0

    def gpy_to_lat(gy):
        y = 0.5 - gy / (TILE * 2 ** zoom)
        return math.degrees(math.atan(math.sinh(2 * math.pi * y)))

    west = gpx_to_lon(ox + box[0])
    east = gpx_to_lon(ox + box[2])
    north = gpy_to_lat(oy + box[1])
    south = gpy_to_lat(oy + box[3])
    return rgb, north, south, east, west, m_per_px(clat, zoom)
