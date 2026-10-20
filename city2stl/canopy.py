"""Lidar tree canopy as a city-model layer (F-DETAIL D3).

Rebuilds the 2026-10-17 Granada study's tree canopy
(``docs/decisions/mesh-pipeline.md`` > "Granada detail study") as library code, in two
stages so the server can run the first without a terrain::

    layer = canopy_layer(bbox, buildings_fc, green_fc, scale, preview=False)   # 1. data
    model = build_on_terrain(z_mm, bbox, scale, layers, canopy=layer)          # 2. solids

**Stage 1, :func:`canopy_layer`:** fetch a height-above-ground raster (a *vegetation-capable*
survey provider, :data:`TREE_PROVIDERS`: REDIAM MDHN in Andalucía, IGN LiDAR HD MNH, ČÚZK DMP,
USGS 3DEP; **not** CNIG MDSn, which is buildings only) and, if possible, the satellite image
for the greenness test; then :func:`canopy_mask` and :func:`canopy_heights` clean them at the
model's scale. The result is a :class:`CanopyLayer`, falsy when the layer is off (no survey
covers the box, no vegetation signal, nothing survives the cleanup).

**Mask** (all lengths are model millimetres, converted with the scale's mm per ground metre)::

    nDSM > 2.5 m
    AND NOT within 2 m of an OSM building
    AND (inside an OSM green polygon OR ExG > 0.115)
    -> depinch -> open with a 0.8 mm disk -> drop components < 1 mm2 -> fill holes < 0.5 mm2

ExG is ``2g - r - b`` on chromaticity-normalised colours (Woebbecke); 0.115 is the study's
Youden optimum (TPR 0.95 on tall park pixels, FPR 0.05 on roofs). The study's two signals:
lidar is necessary, greenness adds street trees and patios OSM lacks (46 % of the canopy).

**Heights:** nDSM clipped to 30 m, smoothed with a 0.4 mm gaussian (normalised by the mask, so
crowns do not droop at the edge), at least 2.5 m.

**Stage 2, :func:`canopy_solids`:** one watertight solid per connected crown area. Outline
from the mask (simplified to 0.2 mm), top from the heightfield TIN's vertices inside it
(``numpy2stl.processing.decimate::heightfield_tin``, 0.25 mm), standing on the terrain; the
bottom follows the terrain 0.6 mm below it so the part rests on the surface once
``city_model.assemble_model`` has cut it against the terrain and every earlier layer.
The preview variant uses a coarser TIN and outline and drops small crowns (~10x fewer faces).

The mask is depinched (a cell touching the crowns only at a corner is joined) because two
crowns meeting at a corner give a non-manifold edge, the ``array_to_mesh`` fault in
``docs/issues.md``.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
import shapely
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine
from scipy import ndimage as ndi
from shapely.geometry import Polygon

logger = logging.getLogger(__name__)

Mesh = tuple[np.ndarray, np.ndarray]

#: Bump when the canopy geometry changes: part of the solid's cache key.
CANOPY_VERSION = 1
#: Survey providers whose surface includes vegetation, in order of preference. CNIG MDSn is
#: left out: it models buildings only.
TREE_PROVIDERS = ("rediam_mdhn", "ign_lidarhd", "cuzk_dmp", "usgs_3dep", "usgs_3dep_ept")
#: Metres per cell asked of the satellite image for the ExG test.
RGB_TARGET_M_PER_PX = 1.0


@dataclass(frozen=True)
class CanopyParams:
    """Thresholds of the study; lengths in model mm unless the name says metres."""

    min_height_m: float = 2.5          # nDSM above this is a crown candidate
    building_buffer_m: float = 2.0     # no canopy within this of an OSM building
    exg_min: float = 0.115             # greenness that stands in for OSM green
    open_mm: float = 0.8               # morphological opening, disk diameter
    min_area_mm2: float = 1.0          # smaller crown areas are dropped
    hole_mm2: float = 0.5              # smaller holes are filled
    clip_m: float = 30.0               # crown height cap
    sigma_mm: float = 0.4              # height smoothing
    seat_mm: float = 0.6               # the solid reaches this far below the terrain
    tin_tol_mm: float = 0.25           # heightfield TIN vertical tolerance
    outline_tol_mm: float = 0.3        # outline simplification (study 0.2: +20 % faces here)
    edge_mm: float = 0.0               # >0: outline edges are split to this length (0: as simplified)
    seed_step: int = 8                 # TIN seed lattice (cells)
    min_sliver_mm3: float = 0.2        # solids smaller than this are dropped

    def preview(self) -> CanopyParams:
        """The coarse variant: a looser TIN and outline, small crowns dropped."""
        return replace(self, tin_tol_mm=4.0, outline_tol_mm=1.6, edge_mm=0.0, seed_step=64,
                       min_area_mm2=10.0, min_sliver_mm3=2.0)


@dataclass
class CanopyLayer:
    """The canopy as data: a masked height raster on the survey grid (not yet geometry).

    Falsy when the layer is off (``reason`` says why). ``height_m`` is (H, W), row 0 north,
    ``transform`` maps (col, row) to (lon, lat); NaN outside the canopy.
    """

    height_m: np.ndarray | None = None
    transform: Affine | None = None
    mm_per_m: float = 0.0              # horizontal model mm per ground metre it was cleaned at
    provider: str = ""
    preview: bool = False
    params: CanopyParams = field(default_factory=CanopyParams)
    stats: dict = field(default_factory=dict)
    reason: str = ""

    def __bool__(self) -> bool:
        return self.height_m is not None and bool(np.isfinite(self.height_m).any())

    def digest(self) -> str:
        """Cache key part: the raster, its grid and every parameter."""
        from city2stl import model_cache as mc

        return mc.digest(CANOPY_VERSION, "canopy", np.nan_to_num(self.height_m, nan=-1.0),
                         list(self.transform)[:6], round(self.mm_per_m, 9), self.params.__dict__)


@dataclass
class CanopyFetch:
    """Where :func:`canopy_layer` gets its rasters; replace either for tests.

    ``ndsm(bbox, providers) -> (provider name, array, transform) | None`` (None = no provider
    covers the box); ``rgb(bbox) -> (rgb uint8 (H, W, 3), meta) | None`` as
    ``geo2stl.imagery.fetch_rgb`` (None = no image).
    """

    ndsm: Callable[[dict, Sequence[str]], tuple[str, np.ndarray, Affine] | None] = None  # type: ignore[assignment]
    rgb: Callable[[dict], tuple[np.ndarray, dict] | None] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.ndsm = self.ndsm or _default_ndsm
        self.rgb = self.rgb or _default_rgb


def _grid_resolution_m(bbox: dict, native_m: float) -> float:
    """``native_m``, or coarser when the box at ``native_m`` is over the survey grid cap
    (Breckenridge, 8.2 x 8.9 km at 1 m, is 8164 x 8906 cells against 4096 a side)."""
    from city2stl.height.providers._survey import M_PER_DEG_LAT, MAX_GRID_SIDE

    lat = math.radians((bbox["north"] + bbox["south"]) / 2)
    side_m = max((bbox["north"] - bbox["south"]) * M_PER_DEG_LAT,
                 (bbox["east"] - bbox["west"]) * M_PER_DEG_LAT * math.cos(lat))
    return max(native_m, side_m / (MAX_GRID_SIDE - 1))


def _default_ndsm(bbox: dict, providers: Sequence[str]):
    from city2stl.height.providers import survey

    for name in providers:
        p = survey.PROVIDERS.get(name)
        if p is None or not p.covers(bbox):
            continue
        got = survey.ndsm_for_bbox(name, bbox, _grid_resolution_m(bbox, p.resolution_m))
        if got is not None:
            return name, got[0], got[1]
    return None


def _default_rgb(bbox: dict):
    from geo2stl.imagery import default_tile_cache_dir, fetch_rgb

    return fetch_rgb(bbox, target_m_per_px=RGB_TARGET_M_PER_PX, cache_dir=default_tile_cache_dir())


# ---------------------------------------------------------------------------
# Rasters
# ---------------------------------------------------------------------------

def cell_size_m(transform: Affine, bbox_lat: float) -> tuple[float, float]:
    """(metres per row, metres per column) of a lon/lat grid at latitude ``bbox_lat``."""
    from city2stl.height.providers._survey import M_PER_DEG_LAT

    return (abs(transform.e) * M_PER_DEG_LAT,
            abs(transform.a) * M_PER_DEG_LAT * float(np.cos(np.radians(bbox_lat))))


def rasterize_polygons(fc: dict | None, shape: tuple[int, int], transform: Affine) -> np.ndarray:
    """Boolean raster of the polygon features of a lon/lat FeatureCollection (cell centres)."""
    geoms = [f["geometry"] for f in (fc or {}).get("features") or []
             if (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon")]
    if not geoms:
        return np.zeros(shape, bool)
    return rasterize(((g, 1) for g in geoms), out_shape=shape, transform=transform, fill=0,
                     dtype="uint8").astype(bool)


def exg_on_grid(rgb: np.ndarray, meta: dict, shape: tuple[int, int], transform: Affine
                ) -> np.ndarray:
    """Excess green ``2g - r - b`` (chromaticity-normalised) of a Web Mercator image, sampled
    at the cell centres of a lon/lat grid. NaN where the image does not reach."""
    from geo2stl.imagery import lat_to_global_px, lon_to_global_px

    c = rgb.astype(np.float32)
    total = np.maximum(c.sum(axis=2), 1.0)
    exg = (2 * c[..., 1] - c[..., 0] - c[..., 2]) / total
    h, w = shape
    zoom = int(meta["zoom"])
    lon = transform.c + transform.a * (np.arange(w) + 0.5)
    lat = transform.f + transform.e * (np.arange(h) + 0.5)
    gx = np.array([lon_to_global_px(v, zoom) for v in lon]) - meta["crop_origin_x"]
    gy = np.array([lat_to_global_px(v, zoom) for v in lat]) - meta["crop_origin_y"]
    rr, cc = np.meshgrid(gy - 0.5, gx - 0.5, indexing="ij")
    out = ndi.map_coordinates(exg, [rr, cc], order=1, mode="constant", cval=np.nan)
    inside = (rr > -1) & (rr < exg.shape[0]) & (cc > -1) & (cc < exg.shape[1])
    return np.where(inside, out, np.nan).astype(np.float32)


def depinch(mask: np.ndarray, rounds: int = 8) -> np.ndarray:
    """``mask`` with every diagonal pinch (two set cells meeting at a corner with the other
    two empty) closed by setting one of the empty cells. Crowns or holes that touch at a
    point would otherwise give a non-manifold edge."""
    m = np.array(mask, dtype=bool, copy=True)
    for _ in range(rounds):
        a, b, c, d = m[:-1, :-1], m[:-1, 1:], m[1:, :-1], m[1:, 1:]
        p1 = a & d & ~b & ~c
        p2 = b & c & ~a & ~d
        if not (p1.any() or p2.any()):
            break
        m[:-1, 1:] |= p1
        m[:-1, :-1] |= p2
    return m


def _disk(radius_px: float) -> np.ndarray:
    r = max(radius_px, 0.5)
    n = int(np.ceil(r))
    y, x = np.mgrid[-n:n + 1, -n:n + 1]
    return (x * x + y * y) <= r * r + 1e-9


def canopy_mask(ndsm: np.ndarray, *, px_m: tuple[float, float], mm_per_m: float,
                buildings: np.ndarray | None = None, green: np.ndarray | None = None,
                exg: np.ndarray | None = None, params: CanopyParams | None = None
                ) -> tuple[np.ndarray, dict]:
    """The tree-canopy mask of a height-above-ground raster, and counts for the report.

    ``px_m``: (metres per row, per column). ``buildings`` / ``green``: boolean rasters of the
    OSM footprints and green polygons; ``exg``: excess green (NaN = unknown). Without ``green``
    and ``exg`` there is no vegetation signal and the mask is empty.
    """
    p = params or CanopyParams()
    h, w = ndsm.shape
    tall = np.nan_to_num(ndsm, nan=-1.0) > p.min_height_m
    near = np.zeros((h, w), bool)
    if buildings is not None and buildings.any():
        near = ndi.distance_transform_edt(~buildings, sampling=px_m) <= p.building_buffer_m
    veg = np.zeros((h, w), bool)
    if green is not None:
        veg |= green
    if exg is not None:
        veg |= np.nan_to_num(exg, nan=-1.0) > p.exg_min
    raw = tall & ~near & veg
    stats = {"tall_ha": _ha(tall, px_m), "raw_ha": _ha(raw, px_m)}

    px_mm = (px_m[0] * mm_per_m, px_m[1] * mm_per_m)
    px_mm_mean = float(np.mean(px_mm))
    mask = depinch(raw)
    if mask.any():
        mask = ndi.binary_opening(mask, structure=_disk(0.5 * p.open_mm / px_mm_mean))
    cell_mm2 = px_mm[0] * px_mm[1]
    mask = _drop_small(mask, p.min_area_mm2 / cell_mm2)
    # Holes: background components that do not reach the border.
    if mask.any():
        bg, n = ndi.label(~mask)
        if n:
            sizes = np.bincount(bg.ravel(), minlength=n + 1)
            border = np.unique(np.concatenate([bg[0], bg[-1], bg[:, 0], bg[:, -1]]))
            small = sizes <= p.hole_mm2 / cell_mm2
            small[0] = False
            small[border] = False
            mask |= small[bg]
    mask = depinch(mask)
    stats["mask_ha"] = _ha(mask, px_m)
    return mask, stats


def _drop_small(mask: np.ndarray, min_cells: float) -> np.ndarray:
    lab, n = ndi.label(mask)
    if not n:
        return mask
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    keep = sizes >= min_cells
    keep[0] = False
    return keep[lab]


def _ha(mask: np.ndarray, px_m: tuple[float, float]) -> float:
    return round(float(mask.sum()) * px_m[0] * px_m[1] / 1e4, 2)


def canopy_heights(ndsm: np.ndarray, mask: np.ndarray, *, mm_per_m: float,
                   px_m: tuple[float, float], params: CanopyParams | None = None) -> np.ndarray:
    """Crown height (m) inside ``mask``, NaN outside: nDSM clipped to ``clip_m``, smoothed
    with a ``sigma_mm`` gaussian normalised by the mask, at least ``min_height_m``."""
    p = params or CanopyParams()
    h = np.clip(np.nan_to_num(ndsm, nan=0.0), 0.0, p.clip_m)
    m = mask.astype(np.float64)
    sig = (p.sigma_mm / (px_m[0] * mm_per_m), p.sigma_mm / (px_m[1] * mm_per_m))
    num = ndi.gaussian_filter(h * m, sig)
    den = ndi.gaussian_filter(m, sig)
    sm = np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0.0)
    return np.where(mask, np.maximum(sm, p.min_height_m), np.nan).astype(np.float32)


# ---------------------------------------------------------------------------
# Stage 1: data
# ---------------------------------------------------------------------------

def canopy_layer(bbox: dict, buildings_fc: dict | None, green_fc: dict | None, scale, *,
                 preview: bool = False, fetch: CanopyFetch | None = None,
                 providers: Sequence[str] = TREE_PROVIDERS,
                 params: CanopyParams | None = None) -> CanopyLayer:
    """The canopy data for ``bbox`` (the server's one call; see the module docstring).

    ``buildings_fc`` / ``green_fc``: the city payload's ``buildings`` and ``green`` lon/lat
    FeatureCollections (None = absent). ``scale``: the model's :class:`ModelScale`
    (``mm_per_px / m_per_px`` is the horizontal mm per ground metre). ``preview`` picks the
    coarse variant. A survey outage or an unreachable image degrades to an off layer / no
    ExG with the reason recorded; this never raises for missing data.

    Off (falsy, ``reason`` set) when no vegetation-capable survey covers the box, when
    neither OSM green nor an image gives a vegetation signal, or when nothing survives.
    """
    from city2stl.height.providers._survey import SurveyError

    t0 = time.perf_counter()
    fetch = fetch or CanopyFetch()
    p = params or CanopyParams()
    p = p.preview() if preview else p
    mm_per_m = float(scale.mm_per_px / scale.m_per_px)
    off = CanopyLayer(preview=preview, params=p, mm_per_m=mm_per_m)
    try:
        got = fetch.ndsm(bbox, providers)
    except (SurveyError, OSError) as exc:
        logger.warning("canopy: survey fetch failed: %s", exc)
        off.reason = f"survey fetch failed: {exc}"
        return off
    if got is None:
        off.reason = "no survey provider with vegetation heights covers this box"
        return off
    provider, ndsm, transform = got
    ndsm = np.asarray(ndsm, np.float32)
    px_m = cell_size_m(transform, (bbox["north"] + bbox["south"]) / 2)
    buildings = rasterize_polygons(buildings_fc, ndsm.shape, transform)
    green = rasterize_polygons(green_fc, ndsm.shape, transform)

    exg, exg_note = None, "not fetched"
    try:
        img = fetch.rgb(bbox)
        if img is not None:
            exg = exg_on_grid(img[0], img[1], ndsm.shape, transform)
            exg_note = "satellite"
    except Exception as exc:  # noqa: BLE001 - imagery is optional; any failure means no ExG
        logger.warning("canopy: no satellite image for ExG (%s); using OSM green only", exc)
        exg_note = f"unavailable: {exc}"
    if not green.any() and exg is None:
        off.reason = "no vegetation signal: no OSM green polygons and no satellite image"
        return off

    mask, stats = canopy_mask(ndsm, px_m=px_m, mm_per_m=mm_per_m, buildings=buildings,
                              green=green, exg=exg, params=p)
    if not mask.any():
        off.reason = "no canopy survives the cleanup"
        off.stats = stats
        return off
    heights = canopy_heights(ndsm, mask, mm_per_m=mm_per_m, px_m=px_m, params=p)
    stats.update({"provider": provider, "exg": exg_note, "preview": preview,
                  "grid": list(ndsm.shape), "px_m": [round(v, 3) for v in px_m],
                  "seconds": round(time.perf_counter() - t0, 2)})
    return CanopyLayer(heights, transform, mm_per_m, provider, preview, p, stats)


# ---------------------------------------------------------------------------
# Stage 2: geometry
# ---------------------------------------------------------------------------

def _mm_affine(layer: CanopyLayer, terrain) -> Affine:
    """(col, row) of the survey grid -> model mm (pixel corners)."""
    k, off = terrain._lonlat_affine()
    t = layer.transform
    return Affine(t.a * k[0], 0.0, t.c * k[0] + off[0], 0.0, t.e * k[1], t.f * k[1] + off[1])


def canopy_polygons(layer: CanopyLayer, terrain, snap_mm: float = 0.01) -> list[Polygon]:
    """The canopy outlines in model mm: one polygon per connected area, simplified to the
    outline tolerance, clipped to the terrain, snapped; pinches opened."""
    p = layer.params
    mask = np.isfinite(layer.height_m)
    to_mm = _mm_affine(layer, terrain)
    geoms = [shapely.geometry.shape(g) for g, v in
             shapes(mask.astype(np.uint8), mask=mask, transform=to_mm, connectivity=4) if v]
    if not geoms:
        return []
    arr = np.asarray(geoms, dtype=object)
    arr = shapely.simplify(arr, p.outline_tol_mm, preserve_topology=True)
    arr = shapely.intersection(arr, terrain.rect)
    arr = shapely.set_precision(arr, snap_mm)
    parts = shapely.get_parts(arr)
    parts = parts[(shapely.get_type_id(parts) == 3)]
    holed = shapely.get_num_interior_rings(parts) > 0
    if holed.any():   # a hole touching its outline at a point is not manifold
        parts[holed] = shapely.buffer(shapely.buffer(parts[holed], -snap_mm, join_style="mitre"),
                                      snap_mm, join_style="mitre")
        parts = shapely.get_parts(parts)
        parts = parts[(shapely.get_type_id(parts) == 3)]
    return [q for q in parts if q.area >= p.min_area_mm2]


def _tin_vertices(field_mm: np.ndarray, p: CanopyParams) -> np.ndarray:
    """Pixel (col, row) of the adaptive TIN's vertices over a height field in mm."""
    from numpy2stl.processing.decimate import heightfield_tin

    idx, _ = heightfield_tin(field_mm, p.tin_tol_mm, seed_step=p.seed_step)
    rows, cols = np.divmod(idx, field_mm.shape[1])
    return np.column_stack([cols, rows])


def canopy_solids(layer: CanopyLayer, terrain, z_mm_per_m: float) -> tuple[list[Mesh], dict]:
    """Watertight solids for ``layer`` standing on ``terrain`` (a ``city_model.Terrain``).

    Top = terrain + crown height at the vertices of the outline and of the heightfield TIN
    inside it; bottom = terrain - ``seat_mm`` at the same xy. Returns (meshes, counts).
    """
    from numpy2stl.core.extrude import close_surface, orient_ccw
    from scipy.ndimage import map_coordinates

    from city2stl.city_model import triangulate_polygon

    p = layer.params
    if abs(terrain.mm_per_m - layer.mm_per_m) > 1e-6 * layer.mm_per_m:
        raise ValueError("the canopy was cleaned at a different model scale than the terrain's")
    polys = canopy_polygons(layer, terrain)
    stats = {"polygons": len(polys), "dropped_slivers": 0, "failed": 0}
    if not polys:
        return [], stats
    mask = np.isfinite(layer.height_m)
    to_mm = _mm_affine(layer, terrain)
    # Height field in mm, extended past the crown edge by the nearest crown height so the
    # TIN does not spend vertices on the cliff outside the canopy.
    _, near = ndi.distance_transform_edt(~mask, return_indices=True)
    ext = layer.height_m[near[0], near[1]].astype(np.float64) * z_mm_per_m
    verts_px = _tin_vertices(ext, p)
    vmm = np.column_stack([to_mm.a * (verts_px[:, 0] + 0.5) + to_mm.c,
                           to_mm.e * (verts_px[:, 1] + 0.5) + to_mm.f])
    inv = ~to_mm
    out: list[Mesh] = []
    tree = shapely.STRtree(shapely.points(vmm))
    for poly in polys:
        if p.edge_mm > 0:
            poly = shapely.segmentize(poly, p.edge_mm)
        hit = tree.query(poly, predicate="intersects")
        tri = triangulate_polygon(poly, vmm[hit] if len(hit) else None, 0.25 * p.outline_tol_mm)
        if tri is None:
            stats["failed"] += 1
            continue
        v2, f = tri
        c = inv.a * v2[:, 0] + inv.b * v2[:, 1] + inv.c
        r = inv.d * v2[:, 0] + inv.e * v2[:, 1] + inv.f
        hm = map_coordinates(ext, [np.asarray(r) - 0.5, np.asarray(c) - 0.5], order=1, mode="nearest")
        ground = terrain.sample(v2[:, 0], v2[:, 1])
        top = np.column_stack([v2, ground + np.maximum(hm, 0.0)])
        f = orient_ccw(top, f)
        v, ff = close_surface(top, f, lambda xy: np.maximum(
            terrain.sample(xy[:, 0], xy[:, 1]) - p.seat_mm, 0.1))
        if _volume(v, ff) < p.min_sliver_mm3:
            stats["dropped_slivers"] += 1
            continue
        out.append((v, ff))
    stats["solids"] = len(out)
    stats["faces"] = int(sum(len(f) for _, f in out))
    return out, stats


def _volume(v: np.ndarray, f: np.ndarray) -> float:
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(abs(np.einsum("ij,ij->", a, np.cross(b, c)) / 6.0))


__all__ = ["CANOPY_VERSION", "TREE_PROVIDERS", "CanopyFetch", "CanopyLayer", "CanopyParams",
           "canopy_heights", "canopy_layer", "canopy_mask", "canopy_polygons", "canopy_solids",
           "depinch", "exg_on_grid", "rasterize_polygons"]
