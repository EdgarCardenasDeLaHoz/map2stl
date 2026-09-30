"""Vector city model: terrain + OSM layers merged into one printable solid.

The DEM is the terrain; every other layer stays vector (polygons -> prisms or
terrain-following slabs) and is merged in 3D with manifold3d, so no pixel
checkerboard is introduced into building or road outlines. One scale serves all
layers:

* horizontal: ``mm_per_px`` (default 1 DEM pixel = 1 mm);
* vertical: ``ModelScale`` — true scale x exaggeration for regions whose diagonal
  is under ``AUTO_TRUE_SCALE_MAX_KM``, fit-to-height above (a large region at
  true scale prints nearly flat). Always overridable.

Simplification is bounded by what the print and the data can resolve, not by a
face budget: the terrain is an adaptive triangulation within max(0.05 mm, half the
source DEM's 1 m vertical step at model scale) of the (median-filtered) DEM at
every pixel — detail finer than that is quantisation noise from upsampling, not
terrain; vector outlines are simplified to
``SIMPLIFY_TOL_MM`` and snapped to a ``SNAP_MM`` grid (so walls two buildings
share stay exactly coincident and merge cleanly). Both tolerances are far below a
0.4 mm nozzle / 0.1 mm layer.

Extruded layers (buildings, walls, towers, ...) sit on the highest terrain point
under their footprint and carry a skirt down to the lowest, so nothing floats on
a slope and nothing is buried. Surface layers (roads, green, rail, trails) are
slabs that follow the terrain, raised or engraved. Water is cut flat.

Outputs: ``merged`` (one watertight solid) and ``parts`` (terrain plus one
non-overlapping solid per layer, for multi-material 3MF).
See docs/plans/active/F-CITYMODEL-vector-city-model.md.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import manifold3d as mf
import numpy as np
import shapely
import trimesh
from numpy2stl.core.extrude import close_surface, orient_ccw, prism, prisms
from numpy2stl.core.heightfield import tin_solid
from numpy2stl.processing.boolean import from_manifold, to_manifold, union
from numpy2stl.processing.decimate import heightfield_tin
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy.ndimage import map_coordinates, median_filter
from shapely.geometry import Polygon, box

from city2stl.heights import min_height_from_tags
from city2stl.landmarks import LandmarkPlan
from city2stl.roofs import building_solids
from geo2stl.geo import GeoGrid, bbox_diagonal_km

logger = logging.getLogger(__name__)

AUTO_TRUE_SCALE_MAX_KM = 20.0
TERRAIN_MAX_ERROR_MM = 0.05   # floor for the terrain mesh's vertical deviation from the DEM
SOURCE_VERTICAL_STEP_M = 1.0  # SRTM / Copernicus heights are stored in whole metres
SIMPLIFY_TOL_MM = 0.05        # outline simplification tolerance at model scale
SNAP_MM = 0.01                # coordinate grid for outlines
MIN_FOOTPRINT_MM2 = 0.3       # smaller footprints cannot print as a distinct feature
PRINT_MIN_WIDTH_MM = 0.8      # two 0.4 mm extrusion lines: the narrowest reliable feature
MAX_SLENDERNESS = 8.0         # extruded height <= this x footprint width (snaps off otherwise)

Mode = Literal["extrude", "raised", "engraved", "water"]
Mesh = tuple[np.ndarray, np.ndarray]


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelScale:
    """Maps DEM pixels and metres to model millimetres."""

    mm_per_px: float
    m_per_px: float          # ground metres per DEM pixel (mean of x and y)
    z_mode: Literal["true", "fit"]
    z_mm_per_m: float        # vertical mm per metre of elevation (and of building height)
    exaggeration: float
    elev_min_m: float
    base_mm: float

    def z_mm(self, elev_m):
        return self.base_mm + (np.asarray(elev_m) - self.elev_min_m) * self.z_mm_per_m

    def describe(self) -> dict:
        return {
            "mm_per_px": self.mm_per_px, "m_per_px": round(self.m_per_px, 3),
            "scale_1_to": round(self.m_per_px * 1000 / self.mm_per_px),
            "z_mode": self.z_mode, "z_mm_per_m": round(self.z_mm_per_m, 5),
            "vertical_exaggeration": round(
                self.z_mm_per_m / (self.mm_per_px / self.m_per_px), 3),
            "base_mm": self.base_mm,
        }


def choose_scale(
    bbox: dict,
    dem_shape: tuple[int, int],
    elev_min_m: float,
    elev_max_m: float,
    *,
    mm_per_px: float = 1.0,
    z_mode: Literal["auto", "true", "fit"] = "auto",
    exaggeration: float = 1.0,
    fit_height_mm: float = 30.0,
    base_mm: float = 5.0,
) -> ModelScale:
    """Pick the vertical scale: true x exaggeration below the auto threshold, else fit."""
    m_per_px = GeoGrid(bbox, dem_shape).m_per_px
    if z_mode == "auto":
        z_mode = "true" if bbox_diagonal_km(bbox) < AUTO_TRUE_SCALE_MAX_KM else "fit"
    if z_mode == "true":
        z_mm_per_m = mm_per_px / m_per_px * exaggeration
    else:
        relief = max(elev_max_m - elev_min_m, 1e-6)
        z_mm_per_m = fit_height_mm / relief * exaggeration
    return ModelScale(mm_per_px, m_per_px, z_mode, z_mm_per_m, exaggeration,
                      float(elev_min_m), base_mm)


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LayerStyle:
    """How one OSM layer becomes geometry."""

    mode: Mode
    enabled: bool = True
    height_scale: float = 1.0     # extrude: multiplier on height_m x z_mm_per_m
    offset_mm: float = 0.4        # raised: height above terrain; engraved/water: depth
    line_width_m: float = 4.0     # buffer width for line features without their own width
    min_height_mm: float = 0.4    # extrude: never thinner than this
    min_width_mm: float = PRINT_MIN_WIDTH_MM   # narrower features are widened to this
    max_slenderness: float = MAX_SLENDERNESS   # extrude: height cap as a multiple of width (0 = off)
    # extrude: print-scale reduction of flat roofs (merge_flat_roofs). Buildings whose
    # tops round to the same print layer are merged; gaps under min_gap_mm close.
    merge_flat: bool = True
    layer_height_mm: float = 0.1  # print layer: tops closer than this print as one
    min_gap_mm: float = 0.4       # one nozzle: a narrower gap fills in on the print anyway
    outline_tol_mm: float = 0.1   # simplification of the merged outlines


DEFAULT_LAYERS: dict[str, LayerStyle] = {
    "buildings":      LayerStyle("extrude"),
    "fortifications": LayerStyle("extrude", line_width_m=4.0),
    "walls":          LayerStyle("extrude", line_width_m=3.0),
    "towers":         LayerStyle("extrude", line_width_m=3.0),
    "churches":       LayerStyle("extrude"),
    "roads":          LayerStyle("raised", offset_mm=0.4, line_width_m=7.0),
    "railways":       LayerStyle("raised", offset_mm=0.3, line_width_m=4.0),
    # Optional (off unless a request enables it): hiking paths and tracks outside
    # town (geo2stl.trails, filter_trails); the Mountain preset turns it on.
    "trails":         LayerStyle("raised", enabled=False, offset_mm=0.3, line_width_m=3.0),
    "green":          LayerStyle("raised", offset_mm=0.2),
    "waterways":      LayerStyle("water", offset_mm=1.0, line_width_m=6.0),
}
# Earlier layers win where layers overlap in the multi-part output.
LAYER_PRIORITY = ["buildings", "fortifications", "walls", "towers", "churches",
                  "waterways", "roads", "railways", "trails", "green"]

# OSM waterway class -> typical channel width (m) when the feature has no width tag.
_WATERWAY_WIDTH_M = {"river": 20.0, "canal": 12.0, "stream": 4.0, "drain": 2.0, "ditch": 1.5}


def resolve_layers(overrides: dict | None) -> dict[str, LayerStyle]:
    """DEFAULT_LAYERS updated with {layer: {field: value}} overrides from a request."""
    layers = dict(DEFAULT_LAYERS)
    for name, fields in (overrides or {}).items():
        base = layers.get(name, LayerStyle("raised"))
        allowed = {k: v for k, v in fields.items() if k in LayerStyle.__dataclass_fields__}
        layers[name] = replace(base, **allowed)
    return layers


# ---------------------------------------------------------------------------
# Terrain and georeferencing
# ---------------------------------------------------------------------------

@dataclass
class Terrain:
    """The terrain heightfield in model millimetres, plus the lon/lat -> mm mapping.

    DEM pixel (row i, col j) is the point x = j*s, y = (H-1-i)*s (north up).
    """

    z: np.ndarray            # (H, W) top surface in mm, row 0 = north
    bbox: dict
    scale: ModelScale
    rect: Polygon = field(init=False)
    tin_xy: np.ndarray | None = None   # adaptive terrain vertices (mm), set by terrain_solid

    def __post_init__(self) -> None:
        h, w = self.z.shape
        s = self.scale.mm_per_px
        self.rect = box(0.0, 0.0, (w - 1) * s, (h - 1) * s)

    def _lonlat_affine(self) -> tuple[np.ndarray, np.ndarray]:
        """(scale, offset): model mm = lon/lat * scale + offset."""
        h, w = self.z.shape
        s = self.scale.mm_per_px
        n, so, e, wst = (self.bbox[k] for k in ("north", "south", "east", "west"))
        fx, fy = w / (e - wst) * s, h / (n - so) * s
        return np.array([fx, fy]), np.array([-wst * fx - 0.5 * s, (h - 0.5) * s - n * fy])

    def project(self, geoms: np.ndarray) -> np.ndarray:
        """lon/lat geometries -> model mm (vectorised)."""
        k, off = self._lonlat_affine()
        return shapely.transform(geoms, lambda c: c * k + off)

    def unproject_xy(self, xy_mm: np.ndarray) -> np.ndarray:
        """Model mm points (N, 2) -> lon/lat (N, 2); the inverse of :meth:`project`."""
        k, off = self._lonlat_affine()
        return (np.asarray(xy_mm, dtype=np.float64) - off) / k

    @property
    def mm_per_m(self) -> float:
        """Horizontal model mm per ground metre."""
        return self.scale.mm_per_px / self.scale.m_per_px

    def m_to_mm(self, metres):
        return np.asarray(metres) / self.scale.m_per_px * self.scale.mm_per_px

    def sample(self, x_mm, y_mm) -> np.ndarray:
        """Bilinear terrain height (mm) at model coordinates."""
        h, _ = self.z.shape
        s = self.scale.mm_per_px
        return map_coordinates(self.z, [(h - 1) - np.asarray(y_mm) / s, np.asarray(x_mm) / s],
                               order=1, mode="nearest")

    def ranges_under(self, polys: Sequence[Polygon]) -> tuple[np.ndarray, np.ndarray]:
        """Per footprint (min, max) terrain height: its vertices plus the DEM cells inside."""
        n = len(polys)
        lo, hi = np.full(n, np.inf), np.full(n, -np.inf)
        if not n:
            return lo, hi
        arr = np.asarray(polys, dtype=object)
        coords, idx = shapely.get_coordinates(arr, return_index=True)
        zv = self.sample(coords[:, 0], coords[:, 1])
        np.minimum.at(lo, idx, zv)
        np.maximum.at(hi, idx, zv)
        k, cells = self.cells_inside(arr)
        zc = self.z.ravel()[cells]
        np.minimum.at(lo, k, zc)
        np.maximum.at(hi, k, zc)
        return lo, hi

    def cells_inside(self, polys: np.ndarray, max_cells: int = 20_000_000
                     ) -> tuple[np.ndarray, np.ndarray]:
        """(polygon index, flat DEM index) of every DEM cell whose centre lies inside
        each polygon (what ``rasterize`` burns, but overlapping polygons each keep
        their cells). One vectorised point-in-polygon test over the candidate cells
        of every bounding box: rasterio's rasterize converted each of 25k footprints
        through ``__geo_interface__`` (8 s for Granada). Bounding boxes adding up to
        more than ``max_cells`` cells fall back to rasterize (largest wins overlaps).
        """
        h, w = self.z.shape
        s = self.scale.mm_per_px
        b = shapely.bounds(polys)
        ok = np.isfinite(b).all(axis=1)
        j0 = np.clip(np.ceil(b[:, 0] / s), 0, w)
        j1 = np.clip(np.floor(b[:, 2] / s), -1, w - 1)
        i0 = np.clip(np.ceil((h - 1) - b[:, 3] / s), 0, h)      # rows grow southwards
        i1 = np.clip(np.floor((h - 1) - b[:, 1] / s), -1, h - 1)
        nj = np.where(ok, np.maximum(j1 - j0 + 1, 0), 0).astype(np.int64)
        ni = np.where(ok, np.maximum(i1 - i0 + 1, 0), 0).astype(np.int64)
        cnt = nj * ni
        total = int(cnt.sum())
        if total > max_cells:
            labels = rasterize(((p, k + 1) for k, p in enumerate(polys)), out_shape=(h, w),
                               transform=Affine(s, 0, -0.5 * s, 0, -s, (h - 0.5) * s),
                               fill=0, dtype="int32").ravel()
            cells = np.flatnonzero(labels)
            return labels[cells] - 1, cells
        if not total:
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        k = np.repeat(np.arange(len(polys)), cnt)
        r = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        row = i0[k].astype(np.int64) + r // nj[k]
        col = j0[k].astype(np.int64) + r % nj[k]
        # Prepared, a polygon answers each point from its own index instead of walking
        # every edge: Cartagena's coastline / wetland polygons took 10 s, now < 0.1 s.
        shapely.prepare(polys)
        inside = shapely.contains_xy(polys[k], col * s, ((h - 1) - row) * s)
        return k[inside], (row * w + col)[inside]


def prepare_dem(dem_m: np.ndarray, median_size: int = 3) -> np.ndarray:
    """Fill NaNs with the lowest real elevation and median-filter away upsampling blocks."""
    dem = np.asarray(dem_m, dtype=np.float64).copy()
    bad = ~np.isfinite(dem)
    if bad.all():
        raise ValueError("DEM contains no finite elevation values")
    if bad.any():
        dem[bad] = np.nanmin(np.where(bad, np.nan, dem))
    if median_size and median_size > 1:
        dem = median_filter(dem, size=median_size, mode="nearest")
    return dem


def terrain_tin(z: np.ndarray, max_error: float = TERRAIN_MAX_ERROR_MM,
                seed_step: int = 8, max_iter: int = 80) -> tuple[np.ndarray, np.ndarray]:
    """Adaptive terrain triangulation within ``max_error`` mm at every pixel.

    See ``numpy2stl.processing.decimate.heightfield_tin``. Returns (pixel indices
    into z.ravel(), triangles over those indices).
    """
    return heightfield_tin(z, max_error, seed_step=seed_step, max_iter=max_iter)


def terrain_solid(terrain: Terrain, max_error: float = TERRAIN_MAX_ERROR_MM,
                  cache: bool = False) -> Mesh:
    """Watertight terrain block: adaptive top surface, side walls, flat bottom at z = 0.

    ``cache``: read / write the solid in the ``city_terrain`` disk cache, keyed by
    the heightfield's bytes, ``max_error`` and ``mm_per_px`` (city2stl.model_cache).
    """
    from city2stl import model_cache

    key = model_cache.terrain_key(terrain.z, max_error, terrain.scale.mm_per_px)
    hit = model_cache.read_mesh(model_cache.NS_TERRAIN, key) if cache else None
    if hit is not None:
        v, f, _ = hit
    else:
        v, f = tin_solid(terrain.z, max_error, mm_per_px=terrain.scale.mm_per_px)
        if cache:
            model_cache.write_mesh(model_cache.NS_TERRAIN, key, v, f)
    terrain.tin_xy = v[: len(v) // 2, :2]
    return v, f


# ---------------------------------------------------------------------------
# Feature geometry
# ---------------------------------------------------------------------------

def _line_width_m(layer: str, props: dict, style: LayerStyle) -> float:
    for key in ("road_width_m", "width"):
        try:
            val = float(str(props.get(key)).split()[0])
            if val > 0:
                return val
        except (TypeError, ValueError, IndexError):
            pass
    if layer == "waterways":
        return _WATERWAY_WIDTH_M.get(str(props.get("waterway") or ""), style.line_width_m)
    return style.line_width_m


def _widths(polys: np.ndarray) -> np.ndarray:
    """Footprint width: short side of the minimum rotated rectangle."""
    rect = shapely.minimum_rotated_rectangle(polys)
    c = shapely.get_coordinates(rect).reshape(len(polys), -1, 2)[:, :3]
    return np.minimum(np.linalg.norm(c[:, 1] - c[:, 0], axis=1),
                      np.linalg.norm(c[:, 2] - c[:, 1], axis=1))


def feature_polygons(name: str, features: list[dict], style: LayerStyle,
                     terrain: Terrain) -> tuple[list[Polygon], list[dict], dict]:
    """Features as clipped, simplified, snapped model-mm polygons (vectorised).

    Features narrower than ``style.min_width_mm`` at model scale are widened to it
    (a 2 m trail at 1:3500 is 0.6 mm: below what a 0.4 mm nozzle lays down).
    Returns (polygons, their properties, counts: ``dropped`` = features that
    produced nothing, ``widened``).
    """
    feats = [f for f in features if f.get("geometry")
             and not (name == "waterways" and (f.get("properties") or {}).get("natural") == "coastline")]
    counts = {"dropped": len(features) - len(feats), "widened": 0}
    if not feats:
        return [], [], counts
    props = [f.get("properties") or {} for f in feats]
    geoms = shapely.from_geojson([json.dumps(f["geometry"]) for f in feats], on_invalid="ignore")
    geoms = terrain.project(geoms)
    type_id = shapely.get_type_id(geoms)
    lines = (type_id == 1) | (type_id == 5)
    if lines.any():
        width = terrain.m_to_mm([_line_width_m(name, props[i], style)
                                 for i in np.flatnonzero(lines)])
        counts["widened"] += int((width < style.min_width_mm).sum())
        half = np.maximum(width, style.min_width_mm) / 2
        geoms[lines] = shapely.buffer(geoms[lines], half, cap_style="flat", join_style="mitre")
    geoms = shapely.make_valid(geoms)
    areas = (~lines) & (shapely.get_type_id(geoms) >= 3) & ~shapely.is_empty(geoms)
    if areas.any():
        w = _widths(geoms[areas])
        thin = w < style.min_width_mm
        if thin.any():
            idx = np.flatnonzero(areas)[thin]
            geoms[idx] = shapely.buffer(geoms[idx], (style.min_width_mm - w[thin]) / 2,
                                        join_style="mitre")
            counts["widened"] += int(thin.sum())
    geoms = shapely.simplify(geoms, SIMPLIFY_TOL_MM, preserve_topology=True)
    geoms = shapely.intersection(geoms, terrain.rect)
    geoms = shapely.set_precision(geoms, SNAP_MM)
    parts, owner = shapely.get_parts(geoms, return_index=True)
    # A hole touching its outline at one point is a valid polygon, but its prism
    # shares that vertex between two walls (not manifold). Opening the pinch by
    # one snap step changes the outline far below print resolution.
    holed = shapely.get_num_interior_rings(parts) > 0
    if holed.any():
        parts[holed] = shapely.buffer(shapely.buffer(parts[holed], -SNAP_MM, join_style="mitre"),
                                      SNAP_MM, join_style="mitre")
        parts, sub = shapely.get_parts(parts, return_index=True)
        owner = owner[sub]
    polys_mask = (shapely.get_type_id(parts) == 3) & (shapely.area(parts) >= MIN_FOOTPRINT_MM2)
    out_polys = list(parts[polys_mask])
    out_props = [props[k] for k in owner[polys_mask]]
    produced = np.zeros(len(feats), bool)
    produced[owner[polys_mask]] = True
    counts["dropped"] += int((~produced).sum())
    return out_polys, out_props, counts


def _roof(props: dict, height_m: float, z_per_m: float) -> tuple[str, float]:
    """(roof shape, roof height in mm) as built: a roof lower than 0.2 mm is flat."""
    shape_ = str(props.get("roof:shape") or "flat").lower().strip()
    if shape_ == "flat":
        return shape_, 0.0
    try:
        roof_m = float(str(props.get("roof:height")).split()[0])
    except (TypeError, ValueError, IndexError):
        roof_m = 0.3 * height_m
    roof_mm = min(max(roof_m, 0.0), 0.5 * height_m) * z_per_m
    if roof_mm <= 0.2:            # below print resolution: flat at full height
        return "flat", 0.0
    return shape_, roof_mm


def _roofed(poly: Polygon, props: dict, z_floor: float, ground_hi: float,
            z_per_m: float, style: LayerStyle, cap_mm: float = math.inf) -> list[Mesh]:
    """Solids for one building from the skirt floor to its roof (``city2stl.roofs``).

    OSM ``height`` includes the roof: the eave sits roof-height below the top.
    ``cap_mm`` limits the height above the ground (slenderness rule).
    """
    height_m = float(props.get("height_m") or 10.0)
    total = max(min(height_m * z_per_m, cap_mm), style.min_height_mm)
    shape_, roof_mm = _roof(props, height_m, z_per_m)
    top = ground_hi + total
    if shape_ == "flat":   # a prism (GEOS triangulation) is closed by construction
        m = prism(poly, z_floor, top)
        return [m] if m is not None else []
    try:
        solids = building_solids(poly, z_floor, top - roof_mm, roof_mm, shape_, props)
    except (RuntimeError, ValueError) as exc:   # Triangle on a sliver the snap left behind
        logger.debug("roof %s failed (%s); flat at mid-roof", shape_, exc)
        solids = []
    good = [m for m in solids if _manifold(m).status() == mf.Error.NoError]
    if len(good) == len(solids) and good:
        return good
    # Keep the building if a roof cannot be closed: flat at mid-roof height.
    m = prism(poly, z_floor, top - roof_mm / 2)
    return [m] if m is not None else []


def merge_flat_roofs(polys: list[Polygon], props: list[dict], lo: np.ndarray, hi: np.ndarray,
                     base: np.ndarray, cap: np.ndarray, z_per_m: float, style: LayerStyle,
                     rect: Polygon) -> tuple[list[Mesh], np.ndarray, dict]:
    """Print-scale reduction: flat roofs that print at the same layer become one solid.

    Candidates are flat-roofed buildings standing on the ground (no
    ``building:part``, no ``min_height``; landmark overrides were already taken
    out). Their *absolute* top - highest ground under the footprint plus the
    height, after the min-height and slenderness rules - is rounded to
    ``style.layer_height_mm``: two tops in the same layer print as one surface.
    Candidates in the same layer whose outlines are closer than
    ``style.min_gap_mm`` (one nozzle: a narrower gap fills in on the print
    anyway) form a group; each group is unioned, closed by half that gap
    (``buffer(+g/2).buffer(-g/2)``, mitre), simplified at ``style.outline_tol_mm``,
    snapped and exploded, and every resulting polygon is one prism from the
    group's lowest skirt floor to its highest top (all within one layer).

    The slicer would draw the same outline, so this is lossless at print scale;
    what it saves is one solid (and its walls against its neighbours) per
    building. Hillside cities merge little (the ground under each building
    differs); flat ones merge whole blocks. Buildings in no group are left to
    the caller. Returns (solids, mask of the buildings merged, counts).
    """
    n = len(polys)
    counts = {"merged_from": 0, "merged_into": 0}
    merged = np.zeros(n, bool)
    if not style.merge_flat or n < 2:
        return [], merged, counts
    height_m = np.array([float(p.get("height_m") or 10.0) for p in props])
    flat = np.array([base[k] <= 0 and not is_part(props[k])
                     and _roof(props[k], height_m[k], z_per_m)[0] == "flat" for k in range(n)])
    idx = np.flatnonzero(flat)
    if len(idx) < 2:
        return [], merged, counts
    total = np.maximum(np.minimum(height_m[idx] * z_per_m, cap[idx]), style.min_height_mm)
    top = hi[idx] + total
    floor = np.maximum(lo[idx] - 0.2, 0.0)
    layer = np.round(top / max(style.layer_height_mm, 1e-6)).astype(np.int64)
    arr = np.asarray(polys, dtype=object)[idx]
    a, b = shapely.STRtree(arr).query(arr, predicate="dwithin", distance=style.min_gap_mm)
    same = (a < b) & (layer[a] == layer[b])
    if not same.any():
        return [], merged, counts
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    m = len(idx)
    _, comp = connected_components(coo_matrix((np.ones(same.sum()), (a[same], b[same])),
                                              shape=(m, m)), directed=False)
    size = np.bincount(comp)
    group_ids = np.flatnonzero(size > 1)
    r = style.min_gap_mm / 2
    unions = np.array([shapely.union_all(arr[comp == g]) for g in group_ids], dtype=object)
    closed = shapely.buffer(shapely.buffer(unions, r, join_style="mitre"), -r, join_style="mitre")
    closed = shapely.simplify(closed, style.outline_tol_mm, preserve_topology=True)
    # Mitre closing + simplify can leave a self-touching ring (Granada: GEOS "side
    # location conflict" in the intersection); make_valid may add stray lines, which
    # the polygon filter below drops.
    closed = shapely.make_valid(closed)
    closed = shapely.set_precision(shapely.intersection(closed, rect), SNAP_MM)
    parts, owner = shapely.get_parts(closed, return_index=True)
    holed = shapely.get_num_interior_rings(parts) > 0
    if holed.any():   # open pinched holes, as feature_polygons does
        parts[holed] = shapely.buffer(shapely.buffer(parts[holed], -SNAP_MM, join_style="mitre"),
                                      SNAP_MM, join_style="mitre")
        parts, sub = shapely.get_parts(parts, return_index=True)
        owner = owner[sub]
    ok = (shapely.get_type_id(parts) == 3) & (shapely.area(parts) >= MIN_FOOTPRINT_MM2)
    parts, g = parts[ok], group_ids[owner[ok]]
    gfloor = np.full(comp.max() + 1, np.inf)
    gtop = np.full(comp.max() + 1, -np.inf)
    np.minimum.at(gfloor, comp, floor)
    np.maximum.at(gtop, comp, top)
    solids = [m for m in prisms(parts, gfloor[g], gtop[g]) if m is not None]
    in_group = np.isin(comp, group_ids)
    merged[idx[in_group]] = True
    counts["merged_from"] = int(in_group.sum())
    counts["merged_into"] = len(solids)
    return solids, merged, counts


def _slab(poly: Polygon, terrain: Terrain, top_off: float, bottom_off: float) -> Mesh | None:
    """Solid between terrain+bottom_off and terrain+top_off, following the terrain.

    The top is triangulated from the outline plus the adaptive terrain's own
    vertices inside it, so the slab carries exactly the detail the terrain needed
    (flat ground stays a few large triangles) instead of a fixed-density mesh.
    Outline edges are split at the terrain's seed spacing so they do not bridge
    over a crest between two distant outline vertices.
    """
    s = terrain.scale.mm_per_px
    poly = shapely.segmentize(poly, 4.0 * s)
    tri = triangulate_polygon(poly, terrain.tin_xy, 0.25 * s)
    if tri is None:
        return None
    v2, f = tri
    ground = terrain.sample(v2[:, 0], v2[:, 1])
    top = np.column_stack([v2, ground + top_off])
    f = orient_ccw(top, f)
    return close_surface(top, f, lambda xy: terrain.sample(xy[:, 0], xy[:, 1]) + bottom_off)


def triangulate_polygon(poly: Polygon, extra: np.ndarray | None = None,
                        inset: float = 0.0) -> tuple[np.ndarray, np.ndarray] | None:
    """Triangulation of ``poly`` whose edges include its outline and holes, plus the
    ``extra`` points (N, 2) lying more than ``inset`` inside it. Returns (xy, faces) or None.

    A constrained Delaunay triangulation built from robust pieces: qhull's
    Delaunay of all the points; then every outline segment that is not an edge
    of it is recovered by re-triangulating its cavity (the triangles it crosses,
    :func:`_recover_segments`) with GEOS's polygon triangulation. No point is
    added, so by Euler's formula the face count is that of any triangulation of
    these vertices (the one Triangle used to return included). Triangles whose
    centroid lies inside the polygon are kept.

    This replaced Shewchuk's Triangle ("p" switch), which on the near-degenerate
    segments snapped outlines produce can fail with "Topological inconsistency
    after splitting a segment", or not return at all.
    """
    from scipy.spatial import Delaunay, QhullError

    # The outline is noded first: a ring touching another at a vertex that lies
    # inside the other's segment (valid), or crossing it (invalid input), would
    # leave segments no triangulation can contain.
    lines = shapely.get_parts(shapely.node(shapely.boundary(poly)))
    coords, owner = shapely.get_coordinates(lines, return_index=True)
    k = np.flatnonzero(owner[1:] == owner[:-1])
    pts, segs = [coords], [np.column_stack([k, k + 1])]
    if extra is not None and len(extra):
        inner = shapely.buffer(poly, -inset) if inset > 0 else poly
        x0, y0, x1, y1 = poly.bounds
        t = np.asarray(extra, dtype=np.float64)
        cand = t[(t[:, 0] > x0) & (t[:, 0] < x1) & (t[:, 1] > y0) & (t[:, 1] < y1)]
        if len(cand) and not inner.is_empty:
            pts.append(cand[shapely.contains_xy(inner, cand[:, 0], cand[:, 1])])
    # Duplicate vertices (a snapped hole touching its outline) become one.
    verts, inv = np.unique(np.concatenate(pts), axis=0, return_inverse=True)
    seg = inv.ravel()[np.concatenate(segs)]
    seg = np.unique(np.sort(seg[seg[:, 0] != seg[:, 1]], axis=1), axis=0)
    if len(verts) < 3 or len(seg) < 3:
        return None
    try:
        simp = Delaunay(verts).simplices.astype(np.int64)
    except (QhullError, ValueError) as exc:
        logger.debug("triangulate failed: %s", exc)
        return None
    missing = ~_has_edges(simp, seg, len(verts))
    if missing.any():
        got = _recover_segments(verts, simp, seg, missing)
        if got is None:
            return None
        verts, simp = got
    c = verts[simp].mean(axis=1)
    f = _split_flat(verts, simp[shapely.contains_xy(poly, c[:, 0], c[:, 1])])
    if not len(f):
        return None
    used, f = np.unique(f, return_inverse=True)
    return verts[used], f.reshape(-1, 3)


def _has_edges(simp: np.ndarray, seg: np.ndarray, n: int) -> np.ndarray:
    """Per segment (sorted index pairs): is it an edge of the triangles ``simp``?"""
    simp, seg = np.asarray(simp, np.int64), np.asarray(seg, np.int64)   # qhull's are int32
    e = np.sort(np.concatenate([simp[:, [0, 1]], simp[:, [1, 2]], simp[:, [2, 0]]]), axis=1)
    return np.isin(seg[:, 0] * n + seg[:, 1], e[:, 0] * n + e[:, 1])


def _recover_segments(verts: np.ndarray, simp: np.ndarray, seg: np.ndarray,
                      missing: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Make every segment an edge: the triangles a missing segment crosses are
    removed, their union cut along all segments inside it (GEOS polygonize) and
    each cell triangulated with GEOS's constrained Delaunay. Cells only have
    existing vertices as corners (segments cross neither each other, after
    noding, nor any triangle edge outside the cavity), so no point is added.
    Returns (vertices, triangles), or None if a segment is still missing."""
    tri_polys = shapely.polygons(np.concatenate([verts[simp], verts[simp][:, :1]], axis=1))
    _, hit = shapely.STRtree(tri_polys).query(shapely.linestrings(verts[seg[missing]]),
                                              predicate="crosses")
    hit = np.unique(hit)
    # The cavity's outline from indices (edges used once by its triangles): a GEOS
    # union of the triangles would drop collinear outline vertices.
    n = len(verts)
    simp = np.asarray(simp, np.int64)
    e = np.sort(np.concatenate([simp[hit][:, [0, 1]], simp[hit][:, [1, 2]], simp[hit][:, [2, 0]]]), axis=1)
    ek, counts = np.unique(e[:, 0] * n + e[:, 1], return_counts=True)
    rim = np.column_stack(np.divmod(ek[counts == 1], n))
    # ... plus every segment inside it: the missing ones and those that are edges
    # of two cavity triangles.
    inner_seg = seg[missing | np.isin(seg[:, 0] * n + seg[:, 1], ek[counts == 2])]
    linework = shapely.union_all(shapely.linestrings(verts[np.vstack([rim, inner_seg])]))
    cells = shapely.get_parts(shapely.polygonize(shapely.get_parts(linework)))
    if len(cells):
        ci, _ = shapely.STRtree(tri_polys[hit]).query(shapely.point_on_surface(cells),
                                                      predicate="intersects")
        cells = cells[np.unique(ci)]
    tris = shapely.get_parts(shapely.constrained_delaunay_triangles(cells)) if len(cells) else []
    xy = shapely.get_coordinates(tris).reshape(-1, 4, 2)[:, :3].reshape(-1, 2)
    # Corners are existing vertices, bit for bit; anything else (GEOS noded a
    # near-touch) becomes a new vertex.
    allv = np.vstack([verts, xy])
    uniq, first, inv = np.unique(allv, axis=0, return_index=True, return_inverse=True)
    inv = inv.ravel()
    remap = np.arange(len(allv))
    remap[len(verts):] = first[inv[len(verts):]]
    extra = remap[len(verts):] >= len(verts)
    if extra.any():
        logger.debug("triangulate: %d cavity corners are new vertices", int(extra.sum()))
    new = remap[len(verts):].reshape(-1, 3)
    keep = np.ones(len(simp), bool)
    keep[hit] = False
    out = np.vstack([simp[keep], new])
    if not _has_edges(out, seg, len(allv)).all():
        logger.debug("triangulate: %d outline segments not recovered", int((~_has_edges(out, seg, len(allv))).sum()))
        return None
    return allv, out


def _split_flat(xy: np.ndarray, f: np.ndarray, passes: int = 3) -> np.ndarray:
    """Remove zero-area triangles: qhull joins three collinear points of a split
    outline edge into one. For flat (a, b, c) with b between a and c, the
    triangle (a, c, d) across the long edge becomes (a, b, d) + (b, c, d) (a flip
    through b); with nobody across, the flat triangle is on the outline and is
    dropped (the outline runs a-c, straight through b's position)."""
    f = np.array(f, copy=True)
    for _ in range(passes):
        p = xy[f]
        d1, d2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
        cross = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
        scale = np.maximum(np.einsum("ij,ij->i", d1, d1), np.einsum("ij,ij->i", d2, d2))
        flat = np.flatnonzero(cross <= 1e-12 * np.maximum(scale, 1e-300))
        if not len(flat):
            return f
        drop = []
        for t in flat:
            tri = f[t]
            q = xy[tri]
            lens = [np.sum((q[(k + 1) % 3] - q[(k + 2) % 3]) ** 2) for k in range(3)]
            k = int(np.argmax(lens))                    # vertex opposite the long edge
            b, a, c = tri[k], tri[(k + 1) % 3], tri[(k + 2) % 3]
            other = np.flatnonzero((f == a).any(axis=1) & (f == c).any(axis=1))
            other = other[other != t]
            if len(other):
                s = other[0]
                dd = [v for v in f[s] if v != a and v != c][0]
                f[t] = [a, b, dd]
                f[s] = [b, c, dd]
            else:
                drop.append(t)
        f = np.delete(f, drop, axis=0)
    return f


def _is_flowing_water(props: dict) -> bool:
    """A river, stream or canal (line or riverbank area), not a lake or the sea."""
    if props.get("waterway"):
        return True
    return str(props.get("water") or "") in ("river", "stream", "canal", "stream_pool")


def is_part(props: dict) -> bool:
    """An OSM ``building:part`` (Simple 3D Buildings)."""
    v = props.get("building:part")
    return bool(v) and str(v).lower() not in ("no", "none", "nan")


def assemble_parts(polys: list[Polygon], props: list[dict]) -> tuple[list, list, int, int]:
    """Simple 3D Buildings: where ``building:part`` features exist, they are the
    building; the outline (which carries the overall, usually tallest, height)
    only fills the area no part covers. Without this a cathedral printed as one
    block at the height of its bell tower.

    Returns (polygons, properties, number of parts, number of outlines trimmed).
    """
    part_idx = [k for k, pr in enumerate(props) if is_part(pr)]
    if not part_idx:
        return polys, props, 0, 0
    # Outlines whose interior overlaps a part (one vectorised query; merely
    # touching a part's wall is not overlapping), each trimmed by its own parts.
    arr = np.asarray(polys, dtype=object)
    part_arr = arr[part_idx]
    oi, pj = shapely.STRtree(part_arr).query(arr, predicate="intersects")
    is_part_k = np.zeros(len(polys), bool)
    is_part_k[part_idx] = True
    keep = ~is_part_k[oi]
    oi, pj = oi[keep], pj[keep]
    real = ~shapely.touches(arr[oi], part_arr[pj])
    oi, pj = oi[real], pj[real]
    # Each outline minus the union of its parts, all at once: the parts padded
    # into one row per outline, unioned along the row, then one difference.
    outl, first, cnt = np.unique(oi, return_index=True, return_counts=True)
    order = np.argsort(oi, kind="stable")
    grid = np.full((len(outl), int(cnt.max()) if len(cnt) else 0), None, dtype=object)
    slot = np.arange(len(oi)) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    grid[np.repeat(np.arange(len(outl)), cnt), slot] = part_arr[pj[order]]
    rest = shapely.set_precision(shapely.difference(arr[outl], shapely.union_all(grid, axis=1)),
                                 SNAP_MM)
    pieces, owner = shapely.get_parts(rest, return_index=True)
    good = (shapely.get_type_id(pieces) == 3) & (shapely.area(pieces) >= MIN_FOOTPRINT_MM2)
    by_outline: dict[int, list] = {}
    for g, o in zip(pieces[good], outl[owner[good]].tolist(), strict=True):
        by_outline.setdefault(o, []).append(g)
    trimmed_set = set(outl.tolist())
    out_p, out_pr = [], []
    for k, (poly, pr) in enumerate(zip(polys, props, strict=True)):
        if k not in trimmed_set:
            out_p.append(poly)
            out_pr.append(pr)
            continue
        for g in by_outline.get(k, []):
            out_p.append(g)
            out_pr.append(pr)
    trimmed = len(trimmed_set)
    return out_p, out_pr, len(part_idx), trimmed


TRAIL_BUILT_UP_M = 60.0   # built-up area: within this of a building
TRAIL_ROAD_M = 6.0        # "along a road": within this of a road centreline
TRAIL_MAX_SHARE = 0.5     # a trail with more of its length in town / on roads is dropped
TRAIL_SAMPLE_M = 5.0      # spacing of the points the shares are measured at


def filter_trails(features: list[dict], terrain: Terrain,
                  buildings: list[dict] | None = None,
                  roads: list[dict] | None = None) -> tuple[list[dict], dict]:
    """Trail features that are hiking trails, not town footpaths.

    ``geo2stl.trails`` already fetches only paths, tracks and signposted
    footways; a path through town is still a footpath beside the streets. A
    feature is dropped when more than ``TRAIL_MAX_SHARE`` of its length lies in
    the built-up area (within ``TRAIL_BUILT_UP_M`` of a building: a 120 m gap
    between houses is still town) or along the road network (within
    ``TRAIL_ROAD_M`` of a road centreline). Shares are measured at points every
    ``TRAIL_SAMPLE_M`` along each line; the built-up area is a distance
    transform of the buildings rasterised on the DEM grid. Areal features pass.

    Returns (kept features, ``{"trails_kept", "trails_dropped",
    "trails_in_town", "trails_along_roads"}``).
    """
    stats = {"trails_kept": len(features), "trails_dropped": 0,
             "trails_in_town": 0, "trails_along_roads": 0}
    feats = [f for f in features if f.get("geometry")]
    if not feats or not (buildings or roads):
        return features, stats
    geoms = terrain.project(shapely.from_geojson([json.dumps(f["geometry"]) for f in feats],
                                                 on_invalid="ignore"))
    tid = shapely.get_type_id(geoms)
    lines = np.flatnonzero((tid == 1) | (tid == 5))
    if not len(lines):
        return features, stats
    step = float(terrain.m_to_mm(TRAIL_SAMPLE_M))
    length = shapely.length(geoms[lines])
    n = np.maximum(2, np.ceil(length / step).astype(int) + 1)
    owner = np.repeat(np.arange(len(lines)), n)
    start = np.repeat(np.cumsum(n) - n, n)
    frac = (np.arange(len(owner)) - start) / (n[owner] - 1)
    xy = shapely.get_coordinates(shapely.line_interpolate_point(geoms[lines][owner], frac,
                                                                normalized=True))
    in_town = np.zeros(len(lines))
    if buildings:
        from scipy.ndimage import distance_transform_edt

        bg = terrain.project(shapely.from_geojson(
            [json.dumps(f["geometry"]) for f in buildings if f.get("geometry")], on_invalid="ignore"))
        bg = bg[~shapely.is_missing(bg) & ~shapely.is_empty(bg)]
        h, w = terrain.z.shape
        sp = terrain.scale.mm_per_px
        if len(bg):
            # Cells whose centre is inside a building, plus the cell of each vertex
            # (footprints smaller than a cell).
            mask = np.zeros(h * w, bool)
            mask[terrain.cells_inside(bg)[1]] = True
            vx = shapely.get_coordinates(bg)
            mask[np.clip(np.round((h - 1) - vx[:, 1] / sp).astype(int), 0, h - 1) * w
                 + np.clip(np.round(vx[:, 0] / sp).astype(int), 0, w - 1)] = True
            mask = mask.reshape(h, w)
            dist_px = distance_transform_edt(~mask)
            built = dist_px * terrain.scale.m_per_px <= TRAIL_BUILT_UP_M
            col = np.clip(np.round(xy[:, 0] / sp).astype(int), 0, w - 1)
            row = np.clip(np.round((h - 1) - xy[:, 1] / sp).astype(int), 0, h - 1)
            in_town = np.bincount(owner, built[row, col], len(lines)) / n
    on_road = np.zeros(len(lines))
    if roads:
        rg = terrain.project(shapely.from_geojson(
            [json.dumps(f["geometry"]) for f in roads if f.get("geometry")], on_invalid="ignore"))
        rg = rg[~shapely.is_missing(rg) & ~shapely.is_empty(rg)]
        if len(rg):
            pi, _ = shapely.STRtree(rg).query(shapely.points(xy), predicate="dwithin",
                                              distance=float(terrain.m_to_mm(TRAIL_ROAD_M)))
            near = np.zeros(len(xy))
            near[np.unique(pi)] = 1.0
            on_road = np.bincount(owner, near, len(lines)) / n
    town, road = in_town > TRAIL_MAX_SHARE, on_road > TRAIL_MAX_SHARE
    drop = np.zeros(len(feats), bool)
    drop[lines[town | road]] = True
    stats.update(trails_kept=len(feats) - int(drop.sum()), trails_dropped=int(drop.sum()),
                 trails_in_town=int(town.sum()), trails_along_roads=int((road & ~town).sum()))
    return [f for f, d in zip(feats, drop, strict=True) if not d], stats


def terrain_tolerance(scale: ModelScale) -> float:
    """The terrain mesh tolerance :func:`build_on_terrain` uses by default (mm)."""
    return max(TERRAIN_MAX_ERROR_MM, 0.5 * SOURCE_VERTICAL_STEP_M * scale.z_mm_per_m)


def _polygon_constants() -> dict:
    return {"simplify": SIMPLIFY_TOL_MM, "snap": SNAP_MM, "min_footprint": MIN_FOOTPRINT_MM2}


def _polygons_key(name: str, features: list[dict], style: LayerStyle, terrain: Terrain,
                  features_digest: str | None = None) -> str:
    from city2stl import model_cache as mc

    return mc.polygons_key(name, features_digest or mc.digest(features), terrain.bbox,
                           terrain.scale, terrain.z.shape, style, _polygon_constants())


def layer_polygons(name: str, features: list[dict], style: LayerStyle, terrain: Terrain,
                   cache: bool = True, features_digest: str | None = None
                   ) -> tuple[list[Polygon], list[dict], dict, str]:
    """:func:`feature_polygons` through the ``city_polygons`` disk cache.

    Returns (polygons, properties, counts, cache key). The key covers the
    features, bbox, scale, DEM shape and the style fields / constants the
    polygons depend on (``city2stl.model_cache.polygons_key``); ``cache=False``
    (or ``MAP2STL_CITY_CACHE=0``) computes without reading or writing.
    """
    from city2stl import model_cache as mc

    key = _polygons_key(name, features, style, terrain, features_digest)
    use = cache and mc.enabled()
    got = mc.read_polygons(key) if use else None
    if got is not None:
        return (*got, key)
    polys, props, counts = feature_polygons(name, features, style, terrain)
    if use:
        mc.write_polygons(key, polys, props, counts)
    return polys, props, counts, key


def layer_preflight(name: str, features: list[dict], style: LayerStyle, terrain: Terrain,
                    tin_density: float = 0.0, cache: bool = True) -> dict:
    """Cheap per-layer figures for the pre-flight report, without building a solid.

    The same ``dropped`` / ``widened`` / ``clamped`` counts :func:`build_layer`
    reports (same polygons, same rules), plus ``thinnest_mm`` (narrowest printed
    footprint), ``tallest_mm`` (extruded top above the lowest ground under it),
    ``area_mm2``, ``volume_mm3`` (added, or removed for engraved / water),
    ``surface_mm2`` (extruded: roofs + walls) and
    ``faces_est``: 4 per outline vertex, plus the terrain vertices a draped slab
    carries (``tin_density`` = terrain vertices per mm²). The polygons come from
    the same disk cache the build uses (:func:`layer_polygons`).
    """
    polys, props, counts, _ = layer_polygons(name, features, style, terrain, cache)
    out: dict = {"mode": style.mode, "features": len(features), "polygons": len(polys), **counts}
    if not polys:
        return out
    arr = np.asarray(polys, dtype=object)
    area = shapely.area(arr)
    nverts = shapely.get_num_coordinates(arr)
    out["thinnest_mm"] = round(float(_widths(arr).min()), 3)
    out["area_mm2"] = round(float(area.sum()), 1)
    if style.mode == "extrude":
        polys, props, _, _ = assemble_parts(polys, props)
        arr = np.asarray(polys, dtype=object)
        z_per_m = terrain.scale.z_mm_per_m * style.height_scale
        lo, hi = terrain.ranges_under(polys)
        base = np.array([min_height_from_tags(p) for p in props]) * z_per_m
        cap = (style.max_slenderness * _widths(arr)
               if style.max_slenderness > 0 else np.full(len(polys), np.inf))
        height = np.array([float(p.get("height_m") or 10.0) for p in props]) * z_per_m
        out["clamped"] = int((height - base > cap).sum())
        # As in _roofed: never above base + cap, never thinner than min_height_mm.
        total = np.maximum(np.minimum(height, base + cap), style.min_height_mm)
        out["tallest_mm"] = round(float((hi - lo + total).max()), 2)
        # Above the ground: a part on a tower from base up, a building from its skirt.
        above = np.where(base > 0, total - base, total + (hi - lo) / 2)
        out["volume_mm3"] = round(float((shapely.area(arr) * above).sum()), 1)
        out["surface_mm2"] = round(float((shapely.area(arr) + shapely.length(arr) * above).sum()), 1)
        out["faces_est"] = int(4 * shapely.get_num_coordinates(arr).sum())
    else:
        sign = 1.0 if style.mode == "raised" else -1.0
        out["volume_mm3"] = round(sign * float(area.sum()) * style.offset_mm, 1)
        out["faces_est"] = int(4 * (nverts.sum() + tin_density * area.sum()))
    return out


def build_layer(name: str, features: list[dict], style: LayerStyle,
                terrain: Terrain, landmarks: LandmarkPlan | None = None,
                polygons: tuple[list, list, dict] | None = None) -> tuple[list[Mesh], dict]:
    """Solids for one layer, plus counts for the report.

    ``landmarks`` (extrude layers): overridden buildings are swapped for their
    override solids before parts are assembled (``city2stl.landmarks``).
    ``polygons``: the layer's ``feature_polygons`` result, when the caller has it
    (e.g. from :func:`layer_polygons`' cache).
    Extrude layers merge flat roofs printing at the same layer
    (:func:`merge_flat_roofs`; ``merged_from`` / ``merged_into`` in the counts).
    """
    polys, props, counts = polygons if polygons is not None else         feature_polygons(name, features, style, terrain)
    stats = {"features": len(features), "polygons": len(polys), **counts}
    solids: list[Mesh] = []
    if style.mode == "extrude":
        if landmarks:
            n_before = len(polys)
            polys, props, solids = landmarks.take(name, polys, props, terrain, style)
            if n_before != len(polys):
                stats["landmark_replaced"] = n_before - len(polys)
        polys, props, stats["parts"], stats["outlines_replaced"] = assemble_parts(polys, props)
        z_per_m = terrain.scale.z_mm_per_m * style.height_scale
        lo, hi = terrain.ranges_under(polys)
        base = np.array([min_height_from_tags(p) for p in props]) * z_per_m
        cap = (style.max_slenderness * _widths(np.asarray(polys, dtype=object))
               if style.max_slenderness > 0 and polys else np.full(len(polys), np.inf))
        # The slenderness rule applies to each solid's own extent: a spire part
        # stands on its tower, not on the ground.
        want = np.array([float(p.get("height_m") or 10.0) for p in props]) * z_per_m - base
        stats["clamped"] = int((want > cap).sum())
        merged_solids, merged, mstats = merge_flat_roofs(
            polys, props, lo, hi, base, cap, z_per_m, style, terrain.rect)
        stats.update(mstats)
        solids.extend(merged_solids)
        # As _roofed: floor on the tower for a part, the skirt otherwise; flat
        # roofs as prisms in one vectorised pass, other roofs one by one.
        z_floor = np.where(base > 0, hi + base, np.maximum(lo - 0.2, 0.0))
        height = np.array([float(p.get("height_m") or 10.0) for p in props])
        top = hi + np.maximum(np.minimum(height * z_per_m, base + cap), style.min_height_mm)
        rest = np.flatnonzero(~merged)
        flat = np.array([_roof(props[k], height[k], z_per_m)[0] == "flat" for k in rest], bool)
        fk = rest[flat] if len(rest) else rest
        solids.extend(m for m in prisms(np.asarray(polys, dtype=object)[fk], z_floor[fk], top[fk])
                      if m is not None)
        for k in (rest[~flat] if len(rest) else rest):
            solids.extend(_roofed(polys[k], props[k], z_floor[k], hi[k], z_per_m, style,
                                  base[k] + cap[k]))
    elif style.mode == "water":
        # Standing water (lakes, reservoirs, the sea) is cut flat below its lowest
        # shore; flowing water follows the valley floor. Cut flat, a river on a
        # slope became a trench down to its lowest point along the whole course.
        lo, hi = terrain.ranges_under(polys)
        for poly, pr, l_, h_ in zip(polys, props, lo, hi, strict=True):
            if _is_flowing_water(pr):
                m = _slab(poly, terrain, 1.0, -style.offset_mm)
            else:
                m = prism(poly, max(l_ - style.offset_mm, 0.1), h_ + 1.0)
            if m is not None:
                solids.append(m)
    else:
        top, bottom = ((style.offset_mm, -1.0) if style.mode == "raised"
                       else (1.0, -style.offset_mm))
        # One slab per connected area: overlapping or touching features (a road
        # network) would otherwise each carry walls and a bottom inside the others.
        if len(polys) > 1:
            merged = shapely.set_precision(shapely.union_all(polys), SNAP_MM)
            polys = [p for p in shapely.get_parts(merged)
                     if p.geom_type == "Polygon" and p.area >= MIN_FOOTPRINT_MM2]
        stats["slabs"] = len(polys)
        for poly in polys:
            m = _slab(poly, terrain, top, bottom)
            if m is not None:
                solids.append(m)
    stats["solids"] = len(solids)
    return solids, stats


def _layer_solid(name: str, feats: list[dict], style: LayerStyle, terrain: Terrain,
                 plan: LandmarkPlan | None, terrain_digest: str, cache: bool
                 ) -> tuple[mf.Manifold | None, dict, str]:
    """One layer's union solid, report block and cache key (``city_solids`` cache).

    The key adds to the polygon key the whole style, the terrain heightfield and
    the landmark overrides the layer depends on: the overrides themselves for
    buildings, the footprints they replaced for later extrude layers (which drop
    features inside them), nothing otherwise. A buildings hit restores the
    plan's footprints and report, which later layers and the report read.
    """
    from city2stl import model_cache as mc

    t0 = time.perf_counter()
    fd = mc.digest(feats)
    pkey = _polygons_key(name, feats, style, terrain, fd)
    lm = None
    if plan and style.mode == "extrude":
        lm = (mc.overrides_digest(plan.overrides) if name == "buildings"
              else mc.footprints_digest(plan.footprints))
    key = mc.solid_key(pkey, style, terrain_digest, lm) if cache else ""
    hit = mc.read_mesh(mc.NS_SOLIDS, key) if cache else None
    if hit is not None:
        v, f, meta = hit
        if plan is not None and name == "buildings" and meta.get("landmarks"):
            plan.footprints.update({oid: shapely.from_wkb(w)
                                    for oid, w in meta["landmarks"]["footprints"].items()})
            plan.report.update(meta["landmarks"]["report"])
        u = to_manifold(v, f, strict=False) if len(f) else None
        rep = {**meta["report"], "cached": True,
               "seconds": {"geometry": 0.0, "union": round(time.perf_counter() - t0, 2)}}
        return u, rep, key
    polys = layer_polygons(name, feats, style, terrain, cache, fd)
    t1 = time.perf_counter()
    solids, stats = build_layer(name, feats, style, terrain, plan, polygons=polys[:3])
    t2 = time.perf_counter()
    u, rejected = union(solids)
    rep = {**stats, "rejected": rejected}
    if cache:
        meta = {"report": rep}
        if plan is not None and name == "buildings":
            meta["landmarks"] = {"footprints": {oid: shapely.to_wkb(fp, hex=True)
                                                for oid, fp in plan.footprints.items()},
                                 "report": plan.report}
        v, f = from_manifold(u) if u is not None else (np.zeros((0, 3)), np.zeros((0, 3), np.int64))
        mc.write_mesh(mc.NS_SOLIDS, key, v, f, meta)
    rep = {**rep, "seconds": {"polygons": round(t1 - t0, 2), "geometry": round(t2 - t1, 2),
                              "union": round(time.perf_counter() - t2, 2)}}
    return u, rep, key


# ---------------------------------------------------------------------------
# Boolean assembly
# ---------------------------------------------------------------------------

def _manifold(mesh: Mesh) -> mf.Manifold:
    return to_manifold(*mesh, strict=False)


CONTACT_SPLIT_MM = 1e-3   # separation given to solids that touch at a point or edge


def _to_trimesh(m: mf.Manifold) -> trimesh.Trimesh:
    v, f = from_manifold(m)
    return trimesh.Trimesh(_separate_contacts(v, f), f, process=False)


def _separate_contacts(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Pull apart vertices that share a position (solids touching at a corner/edge).

    manifold3d keeps such contacts as distinct vertices, so its mesh is closed, but
    STL has no indices: any reader welds them back into edges used four times.
    Moving each copy ``CONTACT_SPLIT_MM`` towards its own faces (far below print
    resolution) keeps the file watertight after welding.
    """
    # Compared as STL stores them (float32): positions that differ only below
    # that precision are welded by every reader too. Grouped by a lexsort of the
    # float32 bit patterns (+0.0 folds -0.0 into 0.0): np.unique(axis=0) sorted
    # the rows as void records, 5x slower on a 1 M-vertex model.
    if not len(v):
        return v
    b = (v.astype(np.float32) + np.float32(0.0)).view(np.uint32)
    order = np.lexsort((b[:, 2], b[:, 1], b[:, 0]))
    sb = b[order]
    new = np.ones(len(sb), bool)
    new[1:] = (sb[1:] != sb[:-1]).any(axis=1)
    inv = np.empty(len(v), np.int64)
    inv[order] = np.cumsum(new) - 1
    dup = np.bincount(inv)[inv] > 1
    if not dup.any():
        return v
    # Mean centre of each duplicated vertex's triangle fan (only those faces).
    touch = f[dup[f].any(axis=1)]
    tri_c = v[touch].mean(axis=1)
    ids = touch.ravel()
    n = np.bincount(ids, minlength=len(v))
    centre = np.column_stack([np.bincount(ids, weights=np.repeat(tri_c[:, k], 3), minlength=len(v))
                              for k in range(3)])
    d = centre[dup] / np.maximum(n[dup], 1)[:, None] - v[dup]
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
    # Copies whose fans point the same way would land together again: the k-th
    # copy of a position moves (k + 1) steps.
    group = inv[dup]
    order = np.argsort(group, kind="stable")
    first = np.searchsorted(group[order], group[order])
    rank = np.empty(len(group), np.int64)
    rank[order] = np.arange(len(group)) - first
    out = v.copy()
    out[dup] += (CONTACT_SPLIT_MM * (rank + 1))[:, None] * d
    return out


def welded_watertight(mesh: trimesh.Trimesh) -> bool:
    """Watertight as an STL reader sees it: coincident vertices merged."""
    w = trimesh.Trimesh(mesh.vertices, mesh.faces, process=True)
    return bool(w.is_watertight)


def _signed_volume(v: np.ndarray, f: np.ndarray) -> float:
    t = v[f]
    return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)


def lossless_simplify(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Merge coplanar regions without moving any surface (numpy2stl, verified here).

    Flat roofs, walls, the base and flat ground collapse to few triangles; the
    result keeps only original vertices. Returns the input if the check fails.
    """
    from numpy2stl.processing.simplify import simplify_mesh_surfaces

    v = np.asarray(mesh.vertices)
    f0 = np.asarray(mesh.faces)
    faces = simplify_mesh_surfaces(v, f0)
    # Closed = every edge used an even number of times. Solids that touch along an
    # edge (a building flush with a wall) share it four times once coincident
    # vertices are welded - exactly as they do in any STL - which is not a hole.
    # Checked on the index arrays (1-D edge keys, signed-volume sum): trimesh's
    # edges/volume properties cost more than the simplification itself.
    e = np.sort(faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1).astype(np.int64)
    _, uses = np.unique(e[:, 0] * len(v) + e[:, 1], return_counts=True)
    if not (uses % 2).any() and math.isclose(_signed_volume(v, faces), _signed_volume(v, f0),
                                             rel_tol=1e-6):
        out = trimesh.Trimesh(v, faces, process=False)
        out.remove_unreferenced_vertices()
        return out
    logger.warning("lossless simplification failed its check; keeping the full mesh")
    return mesh


@dataclass
class CityModel:
    merged: trimesh.Trimesh
    parts: dict[str, trimesh.Trimesh]
    scale: ModelScale
    report: dict


def build_city_model(
    dem_m: np.ndarray,
    bbox: dict,
    layers_geojson: dict[str, dict],
    *,
    mm_per_px: float = 1.0,
    z_mode: Literal["auto", "true", "fit"] = "auto",
    exaggeration: float = 1.0,
    fit_height_mm: float = 30.0,
    base_mm: float = 5.0,
    median_size: int = 3,
    terrain_max_error_mm: float | None = None,
    simplify: bool = True,
    layer_overrides: dict | None = None,
    landmark_overrides: dict | None = None,
    cache: bool = True,
    trail_context: dict | None = None,
) -> CityModel:
    """Terrain from ``dem_m`` (metres, row 0 north, already projected like the
    app's DEM) plus the OSM layers in ``layers_geojson`` ({layer: FeatureCollection}).

    Convenience for callers holding a plain DEM: the terrain stage here is only the
    median filter and the scale. The app runs its own terrain stage (composite,
    edits, sea-level cap, label, contours) and calls :func:`build_on_terrain`.
    """
    dem = prepare_dem(dem_m, median_size)
    scale = choose_scale(bbox, dem.shape, float(dem.min()), float(dem.max()),
                         mm_per_px=mm_per_px, z_mode=z_mode, exaggeration=exaggeration,
                         fit_height_mm=fit_height_mm, base_mm=base_mm)
    return build_on_terrain(scale.z_mm(dem), bbox, scale, layers_geojson,
                            terrain_max_error_mm=terrain_max_error_mm, simplify=simplify,
                            layer_overrides=layer_overrides,
                            landmark_overrides=landmark_overrides, cache=cache,
                            trail_context=trail_context)


def build_on_terrain(
    z_mm: np.ndarray,
    bbox: dict | None,
    scale: ModelScale,
    layers_geojson: dict[str, dict],
    *,
    terrain_max_error_mm: float | None = None,
    simplify: bool = True,
    layer_overrides: dict | None = None,
    landmark_overrides: dict | None = None,
    cache: bool = True,
    trail_context: dict | None = None,
) -> CityModel:
    """The feature stage: OSM layers on a finished terrain heightfield.

    ``z_mm`` is the terrain top surface in model mm (row 0 north, base included),
    as produced by the terrain stage with ``scale``. With no layers this is the
    terrain-only model every mesh export uses. ``bbox`` is needed only to place
    features.

    ``landmark_overrides``: ``{osm_id: LandmarkOverride}`` from
    ``city2stl.landmarks.resolve_overrides`` -- those buildings are replaced by an
    uploaded mesh or a surveyed nDSM solid; the report's ``landmarks`` block says
    which were applied and why any was not.

    ``cache``: the terrain solid, each layer's polygons and each layer's union
    solid are read from / written to the disk caches of ``city2stl.model_cache``
    (keyed by digests of everything they depend on), so a rebuild with unchanged
    inputs skips all layer geometry and changing one input rebuilds only what it
    touches. Each layer's report says ``"cached": true`` when it was a hit.
    Disabled by ``cache=False`` or ``MAP2STL_CITY_CACHE=0``.

    Trails (when enabled) are first cut to hiking trails outside town
    (:func:`filter_trails`) using the buildings and roads in ``layers_geojson``,
    else in ``trail_context`` ({layer: FeatureCollection}: layers that are not
    built but still say where the town is); the trails report carries
    ``trails_kept`` / ``trails_dropped``.
    """
    from city2stl import model_cache as mc

    cache = cache and mc.enabled()
    dem_shape = z_mm.shape
    terrain = Terrain(np.asarray(z_mm, np.float64), bbox or {}, scale)
    if terrain_max_error_mm is None:
        terrain_max_error_mm = max(TERRAIN_MAX_ERROR_MM,
                                   0.5 * SOURCE_VERTICAL_STEP_M * scale.z_mm_per_m)
    t0 = time.perf_counter()
    ground = terrain_solid(terrain, terrain_max_error_mm, cache=cache)
    timings = {"terrain": round(time.perf_counter() - t0, 2)}
    ground_m = _manifold(ground)

    styles = resolve_layers(layer_overrides)
    report: dict = {"scale": scale.describe(), "dem_shape": list(dem_shape),
                    "diagonal_km": round(bbox_diagonal_km(bbox), 2) if bbox else None,
                    "tolerances_mm": {"terrain": terrain_max_error_mm, "outline": SIMPLIFY_TOL_MM,
                                      "snap": SNAP_MM, "min_footprint_mm2": MIN_FOOTPRINT_MM2},
                    # full-resolution grid solid (top + bottom) vs the adaptive one
                    "terrain_faces": {"grid": 4 * (dem_shape[0] - 1) * (dem_shape[1] - 1),
                                      "adaptive": int(len(ground[1]))},
                    "layers": {}}

    terrain_digest = mc.digest(terrain.z) if cache else ""
    trail_stats = None
    tstyle = styles.get("trails")
    tfeats = (layers_geojson.get("trails") or {}).get("features") or []
    if tstyle is not None and tstyle.enabled and tfeats and bbox:
        ctx = {**(trail_context or {}), **layers_geojson}
        kept, trail_stats = filter_trails(
            tfeats, terrain, (ctx.get("buildings") or {}).get("features"),
            (ctx.get("roads") or {}).get("features"))
        layers_geojson = {**layers_geojson, "trails": {"type": "FeatureCollection", "features": kept}}
    adds: dict[str, mf.Manifold] = {}
    cuts: dict[str, mf.Manifold] = {}
    layer_keys: dict[str, str] = {}
    plan = LandmarkPlan(landmark_overrides) if landmark_overrides else None
    for name in LAYER_PRIORITY + [n for n in styles if n not in LAYER_PRIORITY]:
        style = styles.get(name)
        feats = (layers_geojson.get(name) or {}).get("features") or []
        if style is None or not style.enabled or not feats:
            continue
        if not bbox:
            raise ValueError(f"layer {name!r} needs the model's bbox to place its features")
        t0 = time.perf_counter()
        u, rep, layer_keys[name] = _layer_solid(name, feats, style, terrain, plan,
                                                terrain_digest, cache)
        rep["seconds"]["total"] = round(time.perf_counter() - t0, 2)
        report["layers"][name] = {"mode": style.mode, **rep}
        if name == "trails" and trail_stats:
            report["layers"][name].update(trail_stats)
        if u is not None:
            (cuts if style.mode in ("engraved", "water") else adds)[name] = u
    if plan is not None:
        report["landmarks"] = plan.report

    # The whole model: with every input unchanged, assembly and the lossless
    # simplification (most of a rebuild) are skipped too.
    model_key = (mc.digest(mc.MODEL_CACHE_VERSION, "model", terrain_digest, terrain_max_error_mm,
                           scale.mm_per_px, sorted(layer_keys.items()), bool(simplify))
                 if cache else "")
    got = _read_model(model_key) if cache else None
    if got is not None:
        merged, parts, cached_rep = got
        report.update(cached_rep)
        report["model_cached"] = True
        timings["assemble"] = 0.0
        report["seconds"] = timings
        return CityModel(merged, parts, scale, report)

    t0 = time.perf_counter()
    merged, parts = assemble_model(ground_m, adds, cuts)
    timings["assemble"] = round(time.perf_counter() - t0, 2)
    t0 = time.perf_counter()
    if simplify:
        before = len(merged.faces)
        # The merged model and every part are simplified separately (shared surfaces
        # simplify differently on each side, so merged cannot be rebuilt from the
        # parts). They are independent and mostly GEOS / numpy work that releases the
        # GIL: 4 threads, largest first, took Granada from 22 s to 10 s.
        from concurrent.futures import ThreadPoolExecutor

        jobs = {"__merged__": merged, **parts}
        order = sorted(jobs, key=lambda k: -len(jobs[k].faces))
        with ThreadPoolExecutor(max_workers=4) as ex:
            done = dict(zip(order, ex.map(lambda k: lossless_simplify(jobs[k]), order), strict=True))
        merged = done.pop("__merged__")
        parts = {k: done[k] for k in parts}
        report["lossless_simplify"] = {"faces_before": before, "faces_after": len(merged.faces)}
        timings["simplify"] = round(time.perf_counter() - t0, 2)
    report["seconds"] = timings

    report["merged"] = {"faces": len(merged.faces), "watertight": welded_watertight(merged),
                        "volume_mm3": round(float(merged.volume), 1),
                        "size_mm": [round(float(x), 2) for x in merged.extents]}
    report["parts"] = {k: len(v.faces) for k, v in parts.items()}
    if cache:
        _write_model(model_key, merged, parts,
                     {k: report[k] for k in ("merged", "parts", "lossless_simplify") if k in report})
    return CityModel(merged, parts, scale, report)


def assemble_model(ground_m: mf.Manifold, adds: dict[str, mf.Manifold],
                   cuts: dict[str, mf.Manifold]
                   ) -> tuple[trimesh.Trimesh, dict[str, trimesh.Trimesh]]:
    """The merged solid and the disjoint 3MF parts from the terrain and layer solids.

    ``adds`` (raised / extruded layers) in priority order: each part is its layer
    minus the terrain and every earlier layer, and minus every cut (engraved /
    water layers). The merged solid is the union of everything minus the cuts.
    """
    cut_all = mf.Manifold.batch_boolean(list(cuts.values()), mf.OpType.Add) if cuts else None
    ground_cut = ground_m - cut_all if cut_all is not None else ground_m

    parts: dict[str, trimesh.Trimesh] = {"terrain": _to_trimesh(ground_cut)}
    # Sequential on purpose (2026-09-29, Granada): one batch union of all layers
    # (5 s), per-part subtraction chains without the growing union (34 s), threads
    # (no gain: manifold3d holds the GIL) and batching small layers were all slower.
    claimed = ground_m
    for name, m in adds.items():
        own = m - claimed
        if cut_all is not None:
            own = own - cut_all
        if not own.is_empty():
            parts[name] = _to_trimesh(own)
        claimed = claimed + m
    merged = _to_trimesh(claimed - cut_all if cut_all is not None else claimed)
    return merged, parts


def _read_model(key: str) -> tuple[trimesh.Trimesh, dict, dict] | None:
    """(merged, parts, report fields) from the ``city_models`` cache, or None."""
    from geo2stl.cache import read_array_cache

    got = read_array_cache("city_models", key)
    if got is None:
        return None
    arrays, meta = got
    try:
        mesh = {n: trimesh.Trimesh(arrays[f"{i}_v"].astype(np.float64),
                                   arrays[f"{i}_f"].astype(np.int64), process=False)
                for i, n in enumerate(meta["names"])}
    except KeyError:
        return None
    merged = mesh.pop("__merged__")
    return merged, mesh, meta["report"]


def _write_model(key: str, merged: trimesh.Trimesh, parts: dict, rep: dict) -> None:
    from geo2stl.cache import write_array_cache

    names = ["__merged__", *parts]
    meshes = [merged, *parts.values()]
    arrays = {}
    for i, m in enumerate(meshes):
        arrays[f"{i}_v"] = np.asarray(m.vertices, np.float64)
        f = np.asarray(m.faces)
        arrays[f"{i}_f"] = f.astype(np.int32 if len(m.vertices) < 2**31 else np.int64)
    write_array_cache("city_models", key, arrays, {"names": names, "report": rep}, keep_dtype=True)
