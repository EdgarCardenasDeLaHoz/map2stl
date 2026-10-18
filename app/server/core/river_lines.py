"""River lines for the Edit map: HydroRIVERS reaches as simplified polylines.

The Edit map draws them over the raster carve (``modules/layers/river-lines.js``), so a
river reads as a line at any zoom. Detail follows the zoom: the client asks for the
part of the region it shows and how many screen pixels that is, and gets back

- only the reaches crossing that view, at least ``MIN_LENGTH_PX`` long on screen;
- simplified to about one screen pixel (``shapely.simplify``);
- at most ``MAX_LINES`` lines and ``MAX_POINTS`` points: when a view holds more (the
  Amazon at size 1 is 1.23M reaches), the Strahler-order floor rises above the Rivers
  size control (``min_order``) until it fits, so a zoomed-out view keeps the big rivers
  and zooming in brings the small ones back.

Encoding is flat little-endian arrays in base64: ``coords_b64`` float32 lon, lat pairs;
``offsets_b64`` uint32 point offsets (n + 1, line i is points offsets[i]..offsets[i+1]);
``orders_b64`` uint8 Strahler orders. Coordinates are WGS-84: the client projects them
with ``modules/dem/graticule.js::projectedFrac``, the same mapping as the carve's grid.

Two in-memory caches: the region's exploded reaches per (bbox, min_order), and the
finished payload per request. ``geo2stl.hydrology.fetch_hydrorivers`` keeps its own
parquet and bbox caches underneath.
"""

from __future__ import annotations

import base64
import logging
import threading
from collections import OrderedDict

import numpy as np

from geo2stl.hydrology import fetch_hydrorivers

logger = logging.getLogger(__name__)

#: Most lines one response carries.
MAX_LINES = 20_000
#: Most points one response carries; past it the simplification doubles.
MAX_POINTS = 400_000
#: Reaches shorter than this on screen (their bounding box, px) are left out.
MIN_LENGTH_PX = 2.0
#: Simplification tolerance, screen pixels.
SIMPLIFY_PX = 1.0
#: Largest width / height a request may name, px (a 4K screen at 2x is 7680).
MAX_VIEW_PX = 8192

_REGION_MEMO_MAX = 2       # the Amazon's reaches are a few hundred MB: keep few
_PAYLOAD_MEMO_MAX = 64
_lock = threading.Lock()
_region_memo: OrderedDict = OrderedDict()
_payload_memo: OrderedDict = OrderedDict()


def clear_caches() -> None:
    """Forget the cached reaches and payloads (tests)."""
    with _lock:
        _region_memo.clear()
        _payload_memo.clear()


def _memo_get(memo: OrderedDict, key):
    with _lock:
        hit = memo.get(key)
        if hit is not None:
            memo.move_to_end(key)
        return hit


def _memo_put(memo: OrderedDict, key, value, limit: int) -> None:
    with _lock:
        memo[key] = value
        memo.move_to_end(key)
        while len(memo) > limit:
            memo.popitem(last=False)


def _region_reaches(north: float, south: float, east: float, west: float, min_order: int):
    """The region's reaches at ``min_order`` and up, one LineString each.

    Returns ``(geoms, orders, bounds)``: object array of shapely lines, int16 orders,
    float64 (n, 4) bounds (minx, miny, maxx, maxy). Multi-part reaches are split.
    """
    import shapely

    key = (round(north, 6), round(south, 6), round(east, 6), round(west, 6), int(min_order))
    hit = _memo_get(_region_memo, key)
    if hit is not None:
        return hit
    gdf = fetch_hydrorivers(north, south, east, west, min_order=int(min_order))
    if gdf is None or len(gdf) == 0:
        out = (np.empty(0, dtype=object), np.empty(0, dtype=np.int16), np.empty((0, 4)))
    else:
        geoms = np.asarray(gdf.geometry.values, dtype=object)
        orders = gdf["ORD_STRA"].to_numpy(dtype=np.int16)
        parts, parent = shapely.get_parts(geoms, return_index=True)
        keep = ~shapely.is_empty(parts)
        parts, parent = parts[keep], parent[keep]
        out = (parts, orders[parent], shapely.bounds(parts))
    _memo_put(_region_memo, key, out, _REGION_MEMO_MAX)
    return out


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def river_lines_payload(
    north: float, south: float, east: float, west: float, *,
    min_order: int = 3,
    view: tuple[float, float, float, float] | None = None,
    width_px: int = 1000,
    height_px: int = 1000,
) -> dict:
    """Simplified river lines for one view of a region.

    Args:
        north/south/east/west: the region (the box the reaches are read for).
        min_order: the Rivers size control (#hydroMinOrder); the lowest order returned.
        view: (north, south, east, west) shown on screen, clipped to the region; None = all.
        width_px / height_px: screen pixels the view covers, for the simplification
            and the shortest reach kept.

    Returns:
        dict with ``count``, ``order_floor`` (the lowest order actually returned),
        ``truncated`` (lines were dropped beyond the order floor to stay under
        ``MAX_LINES``), ``tolerance_deg``, ``view`` and the three ``*_b64`` arrays.
    """
    import shapely

    vn, vs, ve, vw = view if view is not None else (north, south, east, west)
    vn, vs = min(vn, north), max(vs, south)
    ve, vw = min(ve, east), max(vw, west)
    if vn <= vs or ve <= vw:
        vn, vs, ve, vw = north, south, east, west
    width_px = int(min(max(width_px, 16), MAX_VIEW_PX))
    height_px = int(min(max(height_px, 16), MAX_VIEW_PX))
    min_order = int(min(max(min_order, 1), 10))

    key = (round(north, 6), round(south, 6), round(east, 6), round(west, 6), min_order,
           round(vn, 5), round(vs, 5), round(ve, 5), round(vw, 5), width_px, height_px)
    hit = _memo_get(_payload_memo, key)
    if hit is not None:
        return hit

    geoms, orders, bounds = _region_reaches(north, south, east, west, min_order)
    px_lon = (ve - vw) / width_px
    px_lat = (vn - vs) / height_px

    sel = np.ones(len(geoms), dtype=bool)
    if len(geoms):
        minx, miny, maxx, maxy = bounds.T
        sel = (maxx >= vw) & (minx <= ve) & (maxy >= vs) & (miny <= vn)
        sel &= np.maximum((maxx - minx) / px_lon, (maxy - miny) / px_lat) >= MIN_LENGTH_PX
    idx = np.flatnonzero(sel)

    order_floor = min_order
    truncated = False
    if len(idx) > MAX_LINES:
        sub = orders[idx]
        # Raise the floor to the lowest order whose lines fit; past the top order,
        # keep the highest orders first.
        for o in range(min_order + 1, int(sub.max()) + 1):
            if np.count_nonzero(sub >= o) <= MAX_LINES:
                order_floor = o
                break
        else:
            order_floor = int(sub.max())
        idx = idx[sub >= order_floor]
        if len(idx) > MAX_LINES:
            idx = idx[np.argsort(-orders[idx], kind="stable")[:MAX_LINES]]
            truncated = True
        order_floor = int(orders[idx].min())   # the lowest order actually present

    tol = SIMPLIFY_PX * min(px_lon, px_lat)
    lines = geoms[idx]
    for _ in range(5):
        simple = shapely.simplify(lines, tol, preserve_topology=False) if len(lines) else lines
        coords, which = (shapely.get_coordinates(simple, return_index=True) if len(simple)
                         else (np.empty((0, 2)), np.empty(0, dtype=np.int64)))
        if len(coords) <= MAX_POINTS:
            break
        tol *= 2.0
    counts = np.bincount(which, minlength=len(idx)) if len(idx) else np.empty(0, dtype=np.int64)
    offsets = np.concatenate([[0], np.cumsum(counts)]).astype("<u4")

    payload = {
        "count": int(len(idx)),
        "order_floor": int(order_floor),
        "truncated": bool(truncated),
        "tolerance_deg": float(tol),
        "view": [vn, vs, ve, vw],
        "coords_b64": _b64(coords.astype("<f4")),
        "offsets_b64": _b64(offsets),
        "orders_b64": _b64(np.clip(orders[idx], 0, 255).astype(np.uint8)),
    }
    logger.info("River lines: %d of %d reaches, order >= %d, %d points, tol %.2g deg",
                len(idx), len(geoms), order_floor, len(coords), tol)
    _memo_put(_payload_memo, key, payload, _PAYLOAD_MEMO_MAX)
    return payload
