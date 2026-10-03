"""geo2stl/water_layers.py — rivers and lakes as terrain-relative composite layers.

The composite DEM's river and lake sources (``hydrorivers``,
``natural_earth_rivers``, ``lakes``) return a *relative depth* grid: negative
metres below the ground, 0 elsewhere, on the DEM's own plate-carrée grid. They
are blended with ``add`` (``dem + depth``), so a river cuts 3 m into its own
valley floor wherever that floor is, never to an absolute level.

Such sources are *terrain-relative* (``provider.terrain_relative = True``):
``app/server/routers/composite.compute_composite_dem`` passes them the base DEM
layer's raw grid (``base=``), so they rasterise at exactly that grid and
projection, keeps them apart from the rest of the stack, and the mesh export
adds them *after* the 3x3 median filter (``export._prepare_dem_array``). A
one-pixel channel is 3 of the 9 cells in a 3x3 window, so a median applied
after carving would erase it.

River size (do not re-litigate: docs/plans/done/F-REGION-large-areas-hydrology.md):

- Width and depth come from mean discharge ``Q`` (HydroRIVERS ``DIS_AV_CMS``,
  m^3/s) through the global hydraulic-geometry fit of Andreadis et al. (2013,
  after Moody & Troutman 2002): ``W = 7.2 Q^0.50``, ``D = 0.27 Q^0.39`` (metres).
- Without a discharge (Natural Earth, or a HydroRIVERS reach with Q = 0) Q is
  estimated from Strahler order, ``Q ~ 0.2 * 4^(order - 1)`` (a Horton discharge
  ratio of ~4 per order: order 1 ~ 0.2 m^3/s, order 5 ~ 50, order 9 ~ 13 000).
- Width is clamped to [2, 3000] m and depth to [0.5, 30] m, and the channel is
  never narrower than one DEM pixel, so every river that is drawn also prints.

Valley snapping (F-REGION step 4 review): HydroRIVERS is traced on a 15"
(~450 m) grid and stored as grid-aligned staircases, so its lines sit up to
~2 km from the SRTM valley floor. In the Grand Canyon the median carved cell was
~100 m above the lowest ground within 4 px: the channel was cut into the canyon
walls and across buttes. With the DEM at hand (``base``) each reach is re-routed
along the least-cost path through a corridor around it, cost = 1 + height above
the local valley floor / 5 m, between end points moved to the lowest ground
nearby (:func:`snap_reaches_to_valley`). The corridor widens with river size
(:func:`snap_radius_m`): big rivers sit in the deepest, widest valleys and have
the largest offsets, while a small stream with a wide corridor could jump into
the next valley. ``options.snap = false`` turns it off.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

logger = logging.getLogger(__name__)

# Hydraulic geometry: W = WIDTH_A * Q**WIDTH_B, D = DEPTH_C * Q**DEPTH_F (Andreadis et al. 2013).
WIDTH_A, WIDTH_B = 7.2, 0.50
DEPTH_C, DEPTH_F = 0.27, 0.39
MIN_WIDTH_M, MAX_WIDTH_M = 2.0, 3000.0
MIN_DEPTH_M, MAX_DEPTH_M = 0.5, 30.0
# Strahler order -> nominal discharge when a reach has none.
ORDER_Q0, ORDER_RATIO = 0.2, 4.0
# Half-width floor in pixels: a strip 1 px wide covers a pixel centre in every
# column (or row) it crosses, so the burnt channel stays connected.
_MIN_HALF_WIDTH_PX = 0.55

# OSM water=* values that are rivers drawn as areas. Flattening one of those to
# its lowest shore point would cut a flat trench the length of the valley, so
# the lakes layer leaves them to the river sources.
RIVER_WATER_TAGS = frozenset({"river", "stream", "canal", "ditch", "drain", "rapids",
                              "moat", "wastewater", "stream_pool", "fish_pass"})


# ---------------------------------------------------------------------------
# Grids
# ---------------------------------------------------------------------------

def bbox_grid_shape(north: float, south: float, east: float, west: float,
                    dim: int) -> tuple[int, int]:
    """(rows, cols) of a plate-carrée grid over the bbox with longer side *dim*."""
    lat_span, lon_span = abs(north - south), abs(east - west)
    if lat_span >= lon_span:
        return int(dim), max(1, round(dim * lon_span / lat_span))
    return max(1, round(dim * lat_span / lon_span)), int(dim)


def _metric_frame(north: float, south: float, east: float, west: float):
    """(affine for shapely/geopandas lon/lat -> local metres, width_m, height_m).

    x = (lon - west) * m_per_deg_lon(mid_lat), y = (lat - south) * M_PER_DEG_LAT:
    the plate-carrée grid in metres, so buffers are round in ground metres and
    ``bounds=(0, 0, width_m, height_m)`` burns straight onto the DEM grid.
    """
    mx = float(m_per_deg_lon((north + south) / 2.0))
    my = M_PER_DEG_LAT
    return ([mx, 0.0, 0.0, my, -west * mx, -south * my],
            (east - west) * mx, (north - south) * my)


def resize_relative(layer: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Resize a relative-depth grid to *shape* without losing thin channels.

    Downsampling takes the deepest value of each source block (a minimum
    filter, then nearest), so a one-pixel river survives; upsampling is nearest.
    Linear interpolation would dilute a channel's depth by the resize factor.
    """
    import cv2
    from scipy import ndimage

    layer = np.asarray(layer, dtype=np.float64)
    if layer.shape == tuple(shape):
        return layer
    h, w = shape
    fy = int(np.ceil(layer.shape[0] / h))
    fx = int(np.ceil(layer.shape[1] / w))
    if fy > 1 or fx > 1:
        layer = ndimage.minimum_filter(layer, size=(max(fy, 1), max(fx, 1)), mode="nearest")
    return cv2.resize(layer.astype(np.float32), (w, h),
                      interpolation=cv2.INTER_NEAREST).astype(np.float64)


# ---------------------------------------------------------------------------
# Rivers
# ---------------------------------------------------------------------------

def ocean_mask(dem: np.ndarray, sea_level: float = 0.0) -> np.ndarray:
    """True on open sea: cells at or below *sea_level* connected to the grid edge.

    Rivers and lakes are carved into land only. A river reach buffered past its
    mouth, or one that follows the coast, otherwise lowers the sea floor along
    the shore and leaves a ring around the coastline once the carve is
    subtracted. Basins below sea level that do not reach the edge (Dead Sea,
    Caspian, polders behind dikes) are land here and keep their rivers.
    """
    from scipy import ndimage

    z = np.asarray(dem, dtype=np.float64)
    low = np.isfinite(z) & (z <= sea_level)
    if not low.any():
        return low
    labels, n = ndimage.label(low)
    edge = np.unique(np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]]))
    edge = edge[edge > 0]
    return np.isin(labels, edge) if len(edge) else np.zeros_like(low)


def order_discharge(order) -> np.ndarray:
    """Nominal mean discharge (m^3/s) for a Strahler order (see module docstring)."""
    order = np.clip(np.asarray(order, dtype=np.float64), 1, 12)
    return ORDER_Q0 * ORDER_RATIO ** (order - 1)


def river_size_m(order=None, discharge=None) -> tuple[np.ndarray, np.ndarray]:
    """(width_m, depth_m) per reach from discharge, falling back to Strahler order.

    Either argument may be None; with both None a single order-3 reach is assumed.
    """
    if order is None and discharge is None:
        order = 3
    q_order = order_discharge(order if order is not None else 3)
    if discharge is None:
        q = np.atleast_1d(q_order)
    else:
        q = np.atleast_1d(np.asarray(discharge, dtype=np.float64))
        q_order = np.broadcast_to(np.atleast_1d(q_order), q.shape)
        q = np.where(np.isfinite(q) & (q > 0), q, q_order)
    width = np.clip(WIDTH_A * q ** WIDTH_B, MIN_WIDTH_M, MAX_WIDTH_M)
    depth = np.clip(DEPTH_C * q ** DEPTH_F, MIN_DEPTH_M, MAX_DEPTH_M)
    return width, depth


# Valley snapping: corridor half-width by Strahler order, and the cost scale.
SNAP_RADIUS_M = {3: 400.0, 4: 600.0, 5: 1000.0, 6: 1500.0}   # order >= 7: max
SNAP_RADIUS_MAX_M = 2000.0
SNAP_COST_M = 5.0      # 5 m above the valley floor costs as much as one more pixel
SNAP_END_M_PER_PX = 1.0  # an end point moves 1 px only for 1 m lower ground


def snap_radius_m(order) -> np.ndarray:
    """Corridor half-width (m) searched for the valley floor, by Strahler order."""
    order = np.nan_to_num(np.atleast_1d(np.asarray(order, dtype=np.float64)), nan=3.0)
    out = np.full(order.shape, SNAP_RADIUS_MAX_M)
    for o, r in sorted(SNAP_RADIUS_M.items(), reverse=True):
        out = np.where(order <= o, r, out)
    return out


def snap_reaches_to_valley(geoms, dem: np.ndarray, width_m: float, height_m: float,
                           radius_m) -> list:
    """Re-route each (Multi)LineString along the valley floor of *dem*.

    *geoms* are in the local metric frame of :func:`_metric_frame` (x east from
    the west edge, y north from the south edge); *dem* is the grid over
    ``(0, 0, width_m, height_m)`` with row 0 north. Per part: the end points move
    to the lowest cell within the radius, then the part follows the
    least-cost 8-connected path inside the corridor of that radius around it,
    cost ``1 + (z - local floor) / SNAP_COST_M``. End points shared by reaches
    move to the same cell, so a network stays connected.
    """
    from scipy import ndimage
    from shapely.geometry import LineString, MultiLineString
    from skimage.graph import route_through_array

    dem = np.asarray(dem, dtype=np.float64)
    h, w = dem.shape
    if not np.isfinite(dem).any():
        return list(geoms)
    # The raw grid, not a median: a median widens a one-pixel gorge floor into
    # a three-pixel tie and the channel lands beside it.
    sm = np.where(np.isfinite(dem), dem, np.nanmax(dem))
    px_x, px_y = width_m / w, height_m / h
    px = min(px_x, px_y)
    radius_m = np.broadcast_to(np.asarray(radius_m, dtype=np.float64), (len(geoms),))
    floors: dict[int, np.ndarray] = {}

    def to_px(xy):
        xy = np.asarray(xy, dtype=np.float64)[:, :2]
        return (np.clip((height_m - xy[:, 1]) / px_y - 0.5, 0, h - 1),
                np.clip(xy[:, 0] / px_x - 0.5, 0, w - 1))

    def lowest_near(r, c, rp):
        r, c = int(round(r)), int(round(c))
        r0, r1, c0, c1 = max(r - rp, 0), min(r + rp + 1, h), max(c - rp, 0), min(c + rp + 1, w)
        yy, xx = np.mgrid[r0:r1, c0:c1]
        d2 = (yy - r) ** 2 + (xx - c) ** 2
        # Lowest ground, with a small price per pixel moved so an end point on a
        # flat floor stays put instead of sliding up or down the valley.
        win = np.where(d2 <= rp * rp, sm[r0:r1, c0:c1] + SNAP_END_M_PER_PX * np.sqrt(d2),
                       np.inf)
        k = int(np.argmin(win))
        return r0 + k // win.shape[1], c0 + k % win.shape[1]

    def snap_part(line, rp):
        if line.length < 2 * px:
            return line
        dense = np.array([line.interpolate(d).coords[0]
                          for d in np.linspace(0.0, line.length,
                                               max(2, int(np.ceil(2 * line.length / px))))])
        rows, cols = to_px(dense)
        floor = floors.get(rp)
        if floor is None:
            floor = floors[rp] = ndimage.minimum_filter(sm, size=2 * rp + 1, mode="nearest")
        start = lowest_near(rows[0], cols[0], rp)
        end = lowest_near(rows[-1], cols[-1], rp)
        r0 = max(int(min(rows.min(), start[0], end[0])) - rp - 1, 0)
        r1 = min(int(max(rows.max(), start[0], end[0])) + rp + 2, h)
        c0 = max(int(min(cols.min(), start[1], end[1])) - rp - 1, 0)
        c1 = min(int(max(cols.max(), start[1], end[1])) + rp + 2, w)
        off_line = np.ones((r1 - r0, c1 - c0), dtype=bool)
        off_line[np.round(rows).astype(int) - r0, np.round(cols).astype(int) - c0] = False
        corridor = ndimage.distance_transform_edt(off_line) <= rp
        cost = 1.0 + (sm[r0:r1, c0:c1] - floor[r0:r1, c0:c1]) / SNAP_COST_M
        cost = np.where(corridor, cost, 1e6)
        path, _ = route_through_array(cost, (start[0] - r0, start[1] - c0),
                                      (end[0] - r0, end[1] - c0),
                                      fully_connected=True, geometric=True)
        path = np.asarray(path, dtype=np.float64) + [r0, c0]
        if len(path) < 2:
            return line
        return LineString(np.column_stack([(path[:, 1] + 0.5) * px_x,
                                           height_m - (path[:, 0] + 0.5) * px_y]))

    import shapely

    # A part shorter than 2 px is returned as is (snap_part); decide that for all
    # reaches in one call - at the Amazon's 3 km/px only ~8k of 315k reaches are
    # long enough, and looping over the rest in Python cost ~35 s.
    out = list(geoms)
    arr = np.empty(len(out), dtype=object)
    arr[:] = out
    long_enough = np.flatnonzero(shapely.length(arr) >= 2 * px)
    for i in long_enough:
        geom, rad = out[i], radius_m[i]
        if geom is None or geom.is_empty:
            continue
        rp = max(1, int(round(float(rad) / px)))
        parts = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        snapped = [snap_part(part, rp) for part in parts if not part.is_empty]
        out[i] = snapped[0] if len(snapped) == 1 else MultiLineString(snapped)
    return out


def river_depth_by_order(gdf, north: float, south: float, east: float, west: float,
                          shape: tuple[int, int], *, width_scale: float = 1.0,
                          dem: np.ndarray | None = None,
                          snap: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """(orders, stack): river depth (positive m) burnt separately per Strahler order.

    ``stack[k]`` holds the deepest reach of order ``orders[k]`` at each cell (0
    elsewhere; order 0 = reaches without one). The carve for a min order *m* is
    ``-max(stack[orders >= m])`` - exact, since each cell keeps its deepest reach
    of every order - so changing *m* is a lookup (:func:`river_carve`).
    Everything else as :func:`rasterize_river_depth`.

    *gdf* is a GeoDataFrame of (Multi)LineStrings in lon/lat with optional
    ``ORD_STRA`` (Strahler order) and ``DIS_AV_CMS`` (mean discharge) columns.
    Each reach is buffered by half its width in ground metres (never less than
    half a pixel) and burnt at its depth; where reaches overlap the deeper wins.
    With *dem* (the grid being carved, of *shape*) and *snap*, each reach is
    first moved onto the valley floor (:func:`snap_reaches_to_valley`).
    """
    from numpy2stl.raster import burn_polygons

    h, w = int(shape[0]), int(shape[1])
    empty = (np.zeros(0, dtype=np.int16), np.zeros((0, h, w), dtype=np.float32))
    if gdf is None or len(gdf) == 0:
        return empty
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if len(gdf) == 0:
        return empty

    affine, width_m, height_m = _metric_frame(north, south, east, west)
    px_m = min(width_m / w, height_m / h)

    order = gdf["ORD_STRA"].to_numpy(dtype=np.float64) if "ORD_STRA" in gdf.columns else None
    discharge = (gdf["DIS_AV_CMS"].to_numpy(dtype=np.float64)
                 if "DIS_AV_CMS" in gdf.columns else None)
    widths, depths = river_size_m(order, discharge)
    widths = np.broadcast_to(widths, (len(gdf),)) * float(width_scale)
    depths = np.broadcast_to(depths, (len(gdf),))   # depth_scale is applied in river_carve
    radius = np.maximum(widths / 2.0, _MIN_HALF_WIDTH_PX * px_m)

    import geopandas as gpd

    # Local metres, no CRS: buffers are then true ground distances. One vectorised
    # coordinate transform (geopandas' affine_transform goes geometry by geometry:
    # 97 s for the Amazon's 315,707 reaches).
    import shapely

    a, b, d, e, xo, yo = affine
    geoms = gpd.GeoSeries(shapely.transform(
        gdf.geometry.to_numpy(),
        lambda c: np.column_stack([c[:, 0] * a + c[:, 1] * b + xo,
                                   c[:, 0] * d + c[:, 1] * e + yo])))
    if snap and dem is not None and np.shape(dem) == (h, w):
        from shapely.geometry import box
        lines = geoms.intersection(box(0.0, 0.0, width_m, height_m)).to_numpy()
        ok = (~shapely.is_empty(lines)) & np.isin(shapely.get_type_id(lines), (1, 5))  # (Multi)LineString
        if ok.any():
            orders = order[ok] if order is not None else np.full(int(ok.sum()), 3.0)
            snapped = snap_reaches_to_valley(list(lines[ok]), dem, width_m, height_m,
                                             snap_radius_m(orders))
            for i, g in zip(np.flatnonzero(ok), snapped, strict=True):
                lines[i] = g
        geoms = gpd.GeoSeries(lines)
    geoms = geoms.simplify(px_m / 2.0)
    # 2 segments per quarter circle: the ends are at most ~8 % of a radius off, and
    # the radius is about a pixel, while 16 made each reach dozens of vertices that
    # the rasteriser then had to read (Amazon: 68 s of GeoJSON alone).
    buffered = geoms.buffer(radius, resolution=2)
    keep = (~buffered.is_empty).to_numpy()
    if not keep.any():
        return empty
    shapes = buffered.to_numpy()[keep]
    reach_order = (np.nan_to_num(order, nan=0.0).astype(np.int16)[keep] if order is not None
                   else np.zeros(int(keep.sum()), dtype=np.int16))
    vals = np.asarray(depths, dtype=np.float64)[keep]
    orders = np.unique(reach_order)
    stack = np.zeros((len(orders), h, w), dtype=np.float32)
    for k, o in enumerate(orders):
        sel = reach_order == o
        stack[k] = burn_polygons(list(shapes[sel]), (h, w), bounds=(0.0, 0.0, width_m, height_m),
                                 values=vals[sel].tolist(), mode="max")
    logger.info("river_depth_by_order: %d reaches in %d orders at %dx%d (%.0f m/px)",
                int(keep.sum()), len(orders), w, h, px_m)
    return orders, stack


def river_carve(orders: np.ndarray, stack: np.ndarray, min_order: int = 1,
                depth_scale: float = 1.0) -> np.ndarray:
    """Relative-depth grid (negative m) of the reaches of order >= *min_order*."""
    sel = np.asarray(orders) >= int(min_order)
    if not sel.any():
        return np.zeros(stack.shape[1:], dtype=np.float64)
    return -stack[sel].max(axis=0).astype(np.float64) * float(depth_scale)


def river_order_grid(orders: np.ndarray, stack: np.ndarray, min_order: int = 1) -> np.ndarray:
    """Highest Strahler order carved at each cell (0 none), for the colour-by-order view."""
    out = np.zeros(stack.shape[1:], dtype=np.uint8)
    for k, o in enumerate(orders):               # ascending, so higher orders win
        if o >= min_order and o > 0:
            out[stack[k] > 0] = int(o)
    return out


def rasterize_river_depth(gdf, north: float, south: float, east: float, west: float,
                          shape: tuple[int, int], *, width_scale: float = 1.0,
                          depth_scale: float = 1.0, dem: np.ndarray | None = None,
                          snap: bool = True) -> np.ndarray:
    """Burn river centrelines into a relative-depth grid (negative m, 0 off-river).

    *gdf* is a GeoDataFrame of (Multi)LineStrings in lon/lat with optional
    ``ORD_STRA`` (Strahler order) and ``DIS_AV_CMS`` (mean discharge) columns.
    Each reach is buffered by half its width in ground metres (never less than
    half a pixel) and burnt at its depth; where reaches overlap the deeper wins.
    With *dem* (the grid being carved, of *shape*) and *snap*, each reach is
    first moved onto the valley floor (:func:`snap_reaches_to_valley`).
    """
    orders, stack = river_depth_by_order(gdf, north, south, east, west, shape,
                                         width_scale=width_scale, dem=dem, snap=snap)
    if not len(orders):
        return np.zeros(tuple(shape), dtype=np.float64)
    return river_carve(orders, stack, -1, depth_scale)


def _natural_earth_order(gdf) -> np.ndarray | None:
    """Pseudo Strahler order for Natural Earth rivers from ``scalerank``.

    NE carries no order or discharge; its lower scalerank means a bigger river.
    ``order = clip(10 - scalerank, 4, 9)`` puts the Amazon-class rivers at 9 and
    the smallest 1:10M centrelines at 4 - every NE river is a major one.
    """
    if "scalerank" not in gdf.columns:
        return None
    rank = gdf["scalerank"].to_numpy(dtype=np.float64)
    return np.clip(10.0 - np.nan_to_num(rank, nan=6.0), 4, 9)


_NE_MEMO: dict = {}


def natural_earth_river_features(north: float, south: float, east: float, west: float,
                                 scale_m: int = 10):
    """Natural Earth river centrelines in the bbox as a GeoDataFrame (or None).

    The archive is downloaded once to ``cache/natural_earth/`` and held in
    memory afterwards (``geo2stl.hydrology.fetch_natural_earth_rivers`` refetches
    on every call).
    """
    import geopandas as gpd

    gdf = _NE_MEMO.get(scale_m)
    if gdf is None:
        from geo2stl.hydrology import _MAP2STL_ROOT
        name = f"ne_{scale_m}m_rivers_lake_centerlines.zip"
        path = Path(_MAP2STL_ROOT) / "cache" / "natural_earth" / name
        if not path.exists():
            import requests
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://naciscdn.org/naturalearth/{scale_m}m/physical/{name}"
            logger.info("Downloading Natural Earth rivers: %s", url)
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            tmp = path.with_suffix(".part")
            tmp.write_bytes(resp.content)
            tmp.replace(path)
        gdf = gpd.read_file(f"zip://{path}")
        _NE_MEMO[scale_m] = gdf
    sub = gdf.cx[west:east, south:north]
    return sub if len(sub) else None


RIVER_CARVE_VERSION = 1   # bump when river_depth_by_order's output changes
RIVER_BASE_ORDER = 3      # cache orders >= 3 so min orders 3..9 share one stack


def river_carve_layers(name, fetch, north, south, east, west, dim, options, base=None):
    """Cached (orders, stack) of :func:`river_depth_by_order` for a river source.

    Keyed by source, box, grid, width scale, snapping and the DEM's bytes (the
    reaches are snapped onto it), not by min order or depth scale - those are
    lookups (:func:`river_carve`). The stack covers orders >= min(min order,
    RIVER_BASE_ORDER); a lower min order builds (and caches) a fuller stack.
    """
    import hashlib

    from geo2stl.cache import make_cache_key, read_array_cache, write_array_cache

    shape = base.shape if base is not None else bbox_grid_shape(north, south, east, west, dim)
    min_order = int(options.get("min_order", 1))
    snap = bool(options.get("snap", True))
    dem_hash = (hashlib.sha1(np.ascontiguousarray(base, dtype=np.float64).tobytes()).hexdigest()
                if base is not None and snap else None)

    def key(b):
        return make_cache_key("river_carve", north, south, east, west, {
            "src": name, "shape": list(shape), "ws": float(options.get("width_scale", 1.0)),
            "snap": snap, "dem": dem_hash, "base": b, "v": RIVER_CARVE_VERSION,
            "scale_m": options.get("scale_m")})

    for b in range(max(min_order, 1), 0, -1):
        hit = read_array_cache("river_carve", key(b))
        if hit is not None:
            return hit[0]["orders"], hit[0]["stack"]
    b = max(1, min(min_order, RIVER_BASE_ORDER))
    gdf = fetch(north, south, east, west, {**options, "min_order": b})
    orders, stack = river_depth_by_order(gdf, north, south, east, west, shape,
                                         width_scale=float(options.get("width_scale", 1.0)),
                                         dem=base, snap=snap)
    write_array_cache("river_carve", key(b), {"orders": orders, "stack": stack})
    return orders, stack


def _river_source(name: str, fetch):
    """A terrain-relative composite provider for one river dataset."""

    def provider(north, south, east, west, dim, options, base=None):
        options = options or {}
        orders, stack = river_carve_layers(name, fetch, north, south, east, west, dim,
                                           options, base)
        return river_carve(orders, stack, int(options.get("min_order", 1)),
                           float(options.get("depth_scale", 1.0)))

    provider.__name__ = f"river_layer_source_{name}"
    provider.terrain_relative = True
    return provider


def _fetch_hydrorivers(north, south, east, west, options):
    from geo2stl import hydrology
    return hydrology.fetch_hydrorivers(north, south, east, west,
                                       min_order=int(options.get("min_order", 3)))


def _fetch_natural_earth(north, south, east, west, options):
    gdf = natural_earth_river_features(north, south, east, west,
                                       int(options.get("scale_m", 10)))
    if gdf is None:
        return None
    gdf = gdf.copy()
    gdf["ORD_STRA"] = _natural_earth_order(gdf)
    min_order = int(options.get("min_order", 1))
    if gdf["ORD_STRA"].notna().all() and min_order > 1:
        gdf = gdf[gdf["ORD_STRA"] >= min_order]
    return gdf


#: River sources by composite name -> fetch(north, south, east, west, options).
RIVER_FETCH = {"hydrorivers": _fetch_hydrorivers, "natural_earth_rivers": _fetch_natural_earth}

hydrorivers_layer = _river_source("hydrorivers", _fetch_hydrorivers)
natural_earth_rivers_layer = _river_source("natural_earth_rivers", _fetch_natural_earth)


# ---------------------------------------------------------------------------
# Lakes
# ---------------------------------------------------------------------------

def _is_lake(props: dict) -> bool:
    water = str((props or {}).get("water") or "").lower()
    if water in RIVER_WATER_TAGS:
        return False
    return str((props or {}).get("waterway") or "").lower() != "riverbank"


def lake_depth_grid(features, north: float, south: float, east: float, west: float,
                    base: np.ndarray, *, depth_m: float = 2.0,
                    min_area_m2: float = 10_000.0, smooth: int = 3) -> np.ndarray:
    """Flatten lakes to their shore minimum minus *depth_m*, as a relative grid.

    *features* are GeoJSON features (OSM ``natural=water`` / reservoirs) in
    lon/lat; *base* is the DEM on the bbox's plate-carrée grid. Each lake of at
    least *min_area_m2* (and at least one pixel) gets the flat surface
    ``min(shore) - depth_m``, where the shore is the ring of pixels just outside
    it; cells already below that surface are left alone (the grid only lowers).
    Rivers mapped as areas (``water=river`` etc.) are skipped - see
    :data:`RIVER_WATER_TAGS`.

    The levels are taken against *base* after a ``smooth`` x ``smooth`` median,
    the filter the mesh export applies before it adds the carve
    (``export._prepare_dem_array``, 3 by default), so the printed surface is
    ``median(base) + (level - median(base))`` = flat. Levelled against the raw
    grid, SRTM noise over the water came back as 0.1-0.5 mm of relief on the
    printed lake (F-REGION step 4). ``smooth`` <= 1 uses the raw grid.
    """
    from numpy2stl.raster import burn_polygons
    from scipy import ndimage
    from shapely.affinity import affine_transform
    from shapely.geometry import shape as _shape

    base = np.asarray(base, dtype=np.float64)
    h, w = base.shape
    out = np.zeros((h, w), dtype=np.float64)
    affine, width_m, height_m = _metric_frame(north, south, east, west)
    px_area = (width_m / w) * (height_m / h)

    polys = []
    for feat in features or []:
        geom = (feat or {}).get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        if not _is_lake(feat.get("properties")):
            continue
        try:
            poly = affine_transform(_shape(geom), affine)
        except Exception:  # noqa: BLE001 - one bad OSM polygon must not lose the rest
            continue
        if poly.is_empty or poly.area < max(float(min_area_m2), px_area):
            continue
        polys.append(poly)
    if not polys:
        return out

    labels = burn_polygons(polys, (h, w), bounds=(0.0, 0.0, width_m, height_m),
                           values=list(range(1, len(polys) + 1)), mode="set",
                           dtype=np.float64).astype(np.int64)
    ids = np.unique(labels[labels > 0])
    if not len(ids):
        return out

    if smooth and smooth > 1:
        filled = np.where(np.isfinite(base), base, np.nanmax(base) if np.isfinite(base).any()
                          else 0.0)
        base = np.where(np.isfinite(base), ndimage.median_filter(filled, size=int(smooth)),
                        np.nan)
    finite = np.where(np.isfinite(base), base, np.inf)
    # Shore ring: pixels outside every lake that touch lake k.
    grown = ndimage.grey_dilation(labels, size=(3, 3))
    ring = np.where((labels == 0) & (grown > 0), grown, 0)
    shore = np.asarray(ndimage.minimum(finite, labels=ring, index=ids), dtype=np.float64)
    inside = np.asarray(ndimage.minimum(finite, labels=labels, index=ids), dtype=np.float64)
    # A lake filling the whole grid has no ring; fall back to its own lowest cell.
    shore = np.where(np.isfinite(shore), shore, inside)
    surface = np.full(int(labels.max()) + 1, np.inf)
    surface[ids] = shore - float(depth_m)

    level = surface[labels]
    mask = (labels > 0) & np.isfinite(level) & np.isfinite(base)
    out[mask] = np.minimum(level[mask] - base[mask], 0.0)
    logger.info("lake_depth_grid: %d lakes, %d lake px", len(ids), int(np.count_nonzero(out)))
    return out


#: Largest box (km²) the OSM lakes layer fetches: about 100 × 100 km, a few Overpass queries.
LAKES_MAX_AREA_KM2 = 10_000.0


def _bbox_area_km2(north: float, south: float, east: float, west: float) -> float:
    lat = np.radians((north + south) / 2)
    return abs(north - south) * 110.574 * abs(east - west) * 111.32 * float(np.cos(lat))


def make_lakes_source(fetch_features):
    """A terrain-relative ``lakes`` provider over *fetch_features(n, s, e, w)*.

    *fetch_features* returns a GeoJSON FeatureCollection of water polygons
    (the app passes ``city2stl.fetch.fetch_osm_lakes``, cached). Options:
    ``depth_m`` (default 2) and ``min_area_m2`` (default 10 000 = 1 ha). The
    shore level needs the DEM, so without a base grid the layer is empty.
    """

    def provider(north, south, east, west, dim, options, base=None):
        options = options or {}
        if base is None:
            logger.warning("lakes layer needs a base DEM layer first; skipped")
            return np.zeros(bbox_grid_shape(north, south, east, west, dim))
        area_km2 = _bbox_area_km2(north, south, east, west)
        if area_km2 > LAKES_MAX_AREA_KM2:
            # OSM lake outlines for a continent mean thousands of Overpass sub-queries (osmnx:
            # Amazon was "6,910 times" its query-area limit) and a preview that never finishes.
            # At this scale the open-water (ESA) layer already carries the large lakes.
            logger.info("lakes layer skipped: %.0f km² is over %.0f km²; open water comes from "
                        "the ESA water layer", area_km2, LAKES_MAX_AREA_KM2)
            return np.zeros(base.shape)
        fc = fetch_features(north, south, east, west) or {}
        return lake_depth_grid(fc.get("features") or [], north, south, east, west, base,
                               depth_m=float(options.get("depth_m", 2.0)),
                               min_area_m2=float(options.get("min_area_m2", 10_000.0)),
                               smooth=int(options.get("smooth", 3)))

    provider.__name__ = "lakes_layer_source"
    provider.terrain_relative = True
    return provider


def register_water_layer_sources(fetch_lakes=None) -> None:
    """Register ``hydrorivers`` and ``natural_earth_rivers`` (and ``lakes`` when
    a lake fetcher is given) with :func:`geo2stl.dem.register_layer_source`."""
    from geo2stl.dem import register_layer_source

    register_layer_source("hydrorivers", hydrorivers_layer)
    register_layer_source("natural_earth_rivers", natural_earth_rivers_layer)
    if fetch_lakes is not None:
        register_layer_source("lakes", make_lakes_source(fetch_lakes))
