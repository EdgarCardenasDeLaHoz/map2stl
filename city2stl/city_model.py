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
See docs/plans/F-CITYMODEL-vector-city-model.md.
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
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy.ndimage import map_coordinates, maximum_filter, median_filter
from shapely.geometry import Polygon, box

from city2stl.mesh import _extrude_ring_with_roof
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


DEFAULT_LAYERS: dict[str, LayerStyle] = {
    "buildings":      LayerStyle("extrude"),
    "fortifications": LayerStyle("extrude", line_width_m=4.0),
    "walls":          LayerStyle("extrude", line_width_m=3.0),
    "towers":         LayerStyle("extrude", line_width_m=3.0),
    "churches":       LayerStyle("extrude"),
    "roads":          LayerStyle("raised", offset_mm=0.4, line_width_m=7.0),
    "railways":       LayerStyle("raised", offset_mm=0.3, line_width_m=4.0),
    "trails":         LayerStyle("raised", offset_mm=0.3, line_width_m=3.0),
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

    def project(self, geoms: np.ndarray) -> np.ndarray:
        """lon/lat geometries -> model mm (vectorised)."""
        h, w = self.z.shape
        s = self.scale.mm_per_px
        n, so, e, wst = (self.bbox[k] for k in ("north", "south", "east", "west"))
        fx, fy = w / (e - wst) * s, h / (n - so) * s
        off = np.array([-wst * fx - 0.5 * s, (h - 0.5) * s - n * fy])
        return shapely.transform(geoms, lambda c: c * np.array([fx, fy]) + off)

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
        coords, idx = shapely.get_coordinates(np.asarray(polys, dtype=object), return_index=True)
        zv = self.sample(coords[:, 0], coords[:, 1])
        np.minimum.at(lo, idx, zv)
        np.maximum.at(hi, idx, zv)
        h, w = self.z.shape
        s = self.scale.mm_per_px
        labels = rasterize(((p, k + 1) for k, p in enumerate(polys)), out_shape=(h, w),
                           transform=Affine(s, 0, -0.5 * s, 0, -s, (h - 0.5) * s),
                           fill=0, dtype="int32")
        inside = labels > 0
        np.minimum.at(lo, labels[inside] - 1, self.z[inside])
        np.maximum.at(hi, labels[inside] - 1, self.z[inside])
        return lo, hi


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


def _triangulate_pixels(ij: np.ndarray) -> np.ndarray:
    """Delaunay triangles over integer pixel coordinates (Shewchuk's Triangle)."""
    import triangle

    return triangle.triangulate({"vertices": ij.astype(np.float64)}, "Q")["triangles"]


def _rasterize_tin(xy: np.ndarray, zv: np.ndarray, tris: np.ndarray,
                   shape: tuple[int, int]) -> np.ndarray:
    """Linear interpolation of a TIN whose vertices lie on pixel centres, per pixel.

    Scan-converts every triangle's bounding box at once (vectorised) instead of
    locating each pixel in the triangulation, which is ~50x slower.
    """
    h, w = shape
    a, b, c = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
    lo = np.minimum(np.minimum(a, b), c).astype(np.int64)
    hi = np.maximum(np.maximum(a, b), c).astype(np.int64)
    bw, bh = hi[:, 0] - lo[:, 0] + 1, hi[:, 1] - lo[:, 1] + 1
    counts = bw * bh
    tid = np.repeat(np.arange(len(tris)), counts)
    off = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    px = lo[tid, 0] + off % bw[tid]
    py = lo[tid, 1] + off // bw[tid]
    A, B, C = a[tid], b[tid], c[tid]
    den = (B[:, 1] - C[:, 1]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (A[:, 1] - C[:, 1])
    l1 = ((B[:, 1] - C[:, 1]) * (px - C[:, 0]) + (C[:, 0] - B[:, 0]) * (py - C[:, 1])) / den
    l2 = ((C[:, 1] - A[:, 1]) * (px - C[:, 0]) + (A[:, 0] - C[:, 0]) * (py - C[:, 1])) / den
    l3 = 1.0 - l1 - l2
    inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
    zt = zv[tris][tid]
    out = np.full(shape, np.nan)
    out[py[inside], px[inside]] = (l1 * zt[:, 0] + l2 * zt[:, 1] + l3 * zt[:, 2])[inside]
    return out


def terrain_tin(z: np.ndarray, max_error: float = TERRAIN_MAX_ERROR_MM,
                seed_step: int = 8, max_iter: int = 80) -> tuple[np.ndarray, np.ndarray]:
    """Adaptive triangulation of a heightfield within ``max_error`` at every pixel.

    Start from every border pixel plus a lattice every ``seed_step`` pixels (about
    the source DEM's own spacing, where its information actually is), triangulate,
    then add every pixel that is a local peak of the remaining error and exceeds
    ``max_error``; repeat until no pixel does. Border pixels are all kept, so the
    model edge (and its side walls) is exact.
    Returns (pixel indices into z.ravel(), triangles over those indices).
    """
    h, w = z.shape
    ii, jj = np.divmod(np.arange(h * w), w)
    zf = z.ravel()
    chosen = (ii == 0) | (ii == h - 1) | (jj == 0) | (jj == w - 1)
    chosen |= (ii % seed_step == 0) & (jj % seed_step == 0)
    for _ in range(max_iter):
        idx = np.flatnonzero(chosen)
        xy = np.column_stack([jj[idx], ii[idx]])
        tris = _triangulate_pixels(xy)
        err = np.abs(_rasterize_tin(xy, zf[idx], tris, (h, w)) - z)
        err[~np.isfinite(err)] = 0.0
        if err.max() <= max_error:
            return idx, idx[tris]
        chosen |= ((err > max_error) & (err >= maximum_filter(err, size=3))).ravel()
    logger.warning("terrain_tin stopped at %d iterations above %.3f mm", max_iter, max_error)
    idx = np.flatnonzero(chosen)
    return idx, idx[_triangulate_pixels(np.column_stack([jj[idx], ii[idx]]))]


def terrain_solid(terrain: Terrain, max_error: float = TERRAIN_MAX_ERROR_MM) -> Mesh:
    """Watertight terrain block: adaptive top surface, side walls, flat bottom at z = 0."""
    h, w = terrain.z.shape
    s = terrain.scale.mm_per_px
    idx, tris = terrain_tin(terrain.z, max_error)
    remap = np.full(h * w, -1)
    remap[idx] = np.arange(len(idx))
    ii, jj = np.divmod(idx, w)
    top = np.column_stack([jj * s, (h - 1 - ii) * s, terrain.z.ravel()[idx]])
    terrain.tin_xy = top[:, :2]
    f = remap[tris]
    # Counter-clockwise from above => normals up.
    p = top[f]
    ccw = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
           - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0])) > 0
    f[~ccw] = f[~ccw][:, ::-1]
    return _close_surface(top, f, lambda xy: np.zeros(len(xy)))


def _close_surface(top: np.ndarray, f: np.ndarray, bottom_z) -> Mesh:
    """Solid from an upward-facing open surface: walls down to bottom_z(xy), bottom reversed."""
    n = len(top)
    bot = top.copy()
    bot[:, 2] = bottom_z(top[:, :2])
    edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    key = np.sort(edges, axis=1)
    _, inv, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    border = edges[counts[inv.ravel()] == 1]          # directed as in the CCW top faces
    a, b = border[:, 0], border[:, 1]
    walls = np.vstack([np.column_stack([a, b + n, b]), np.column_stack([a, a + n, b + n])])
    faces = np.vstack([f, f[:, ::-1] + n, walls])
    return np.vstack([top, bot]), faces


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


def _prism(poly: Polygon, z0: float, z1: float) -> Mesh | None:
    """Closed prism over a polygon (holes kept) using only the outline's own vertices."""
    if z1 - z0 <= 1e-6:
        return None
    poly = shapely.geometry.polygon.orient(poly, 1.0)   # exterior CCW, holes CW
    rings = [np.asarray(poly.exterior.coords)[:-1]] + [np.asarray(r.coords)[:-1] for r in poly.interiors]
    tri = shapely.get_coordinates(shapely.constrained_delaunay_triangles(poly)).reshape(-1, 4, 2)[:, :3]
    if not len(tri):
        return None
    ring_pts = np.concatenate(rings)
    uniq, inv = np.unique(np.concatenate([ring_pts, tri.reshape(-1, 2)]), axis=0, return_inverse=True)
    inv = inv.ravel()
    ring_idx, f = inv[:len(ring_pts)], inv[len(ring_pts):].reshape(-1, 3)
    p = uniq[f]
    ccw = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
           - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0])) > 0
    f[~ccw] = f[~ccw][:, ::-1]
    n = len(uniq)
    walls = []
    start = 0
    for r in rings:
        k = ring_idx[start:start + len(r)]
        a, b = k, np.roll(k, -1)
        walls += [np.column_stack([a, b + n, b]), np.column_stack([a, a + n, b + n])]
        start += len(r)
    v = np.vstack([np.column_stack([uniq, np.full(n, z1)]), np.column_stack([uniq, np.full(n, z0)])])
    # Rings run with the solid on their left (exterior CCW, holes CW), so each wall
    # quad (top a, bottom b, top b)+(top a, bottom a, bottom b) faces outward.
    return v, np.vstack([f, f[:, ::-1] + n] + walls)


def _roofed(poly: Polygon, props: dict, z_floor: float, ground_hi: float,
            z_per_m: float, style: LayerStyle, cap_mm: float = math.inf) -> Mesh | None:
    """A building-like prism from the skirt floor to its roof (OSM roof shapes kept).

    OSM ``height`` includes the roof: the eave sits roof-height below the top.
    ``cap_mm`` limits the height above the ground (slenderness rule).
    """
    height_m = float(props.get("height_m") or 10.0)
    total = max(min(height_m * z_per_m, cap_mm), style.min_height_mm)
    shape_ = str(props.get("roof:shape") or "flat").lower().strip()
    if shape_ != "flat" and not poly.interiors:
        try:
            roof_m = float(str(props.get("roof:height")).split()[0])
        except (TypeError, ValueError, IndexError):
            roof_m = 0.3 * height_m
        roof_mm = min(max(roof_m, 0.0), 0.5 * height_m) * z_per_m
        if roof_mm > 0.2:
            ring = [list(c) for c in poly.exterior.coords]
            v, f = _extrude_ring_with_roof(ring, z_floor, ground_hi + total - roof_mm,
                                           shape_, roof_mm, float, float)
            if v is not None:
                roofed = np.asarray(v, np.float64), np.asarray(f)
                if _manifold(roofed).status() == mf.Error.NoError:
                    return roofed
            # The roof generator cannot close pitched roofs on concave footprints:
            # keep the building, flat at mid-roof height.
            return _prism(poly, z_floor, ground_hi + total - roof_mm / 2)
    return _prism(poly, z_floor, ground_hi + total)


def _slab(poly: Polygon, terrain: Terrain, top_off: float, bottom_off: float) -> Mesh | None:
    """Solid between terrain+bottom_off and terrain+top_off, following the terrain.

    The top is triangulated from the outline plus the adaptive terrain's own
    vertices inside it, so the slab carries exactly the detail the terrain needed
    (flat ground stays a few large triangles) instead of a fixed-density mesh.
    Outline edges are split at the terrain's seed spacing so they do not bridge
    over a crest between two distant outline vertices.
    """
    import triangle

    s = terrain.scale.mm_per_px
    poly = shapely.segmentize(poly, 4.0 * s)
    rings = [np.asarray(poly.exterior.coords)[:-1]] + [np.asarray(r.coords)[:-1] for r in poly.interiors]
    pts, segs, start = [], [], 0
    for r in rings:
        n = len(r)
        pts.append(r)
        k = np.arange(start, start + n)
        segs.append(np.column_stack([k, np.roll(k, -1)]))
        start += n
    if terrain.tin_xy is not None:
        inner = shapely.buffer(poly, -0.25 * s)
        x0, y0, x1, y1 = poly.bounds
        t = terrain.tin_xy
        cand = t[(t[:, 0] > x0) & (t[:, 0] < x1) & (t[:, 1] > y0) & (t[:, 1] < y1)]
        if len(cand) and not inner.is_empty:
            pts.append(cand[shapely.contains_xy(inner, cand[:, 0], cand[:, 1])])
    # Triangle (C) reads int32 indices and crashes on duplicate vertices or
    # zero-length segments, e.g. where a snapped hole touches its outline.
    verts, inv = np.unique(np.concatenate(pts), axis=0, return_inverse=True)
    seg = inv.ravel()[np.concatenate(segs)]
    seg = np.unique(np.sort(seg[seg[:, 0] != seg[:, 1]], axis=1), axis=0)
    tri_in = {"vertices": np.ascontiguousarray(verts, np.float64),
              "segments": np.ascontiguousarray(seg, np.int32)}
    if poly.interiors:
        tri_in["holes"] = np.ascontiguousarray(
            [shapely.Polygon(r).point_on_surface().coords[0] for r in poly.interiors], np.float64)
    try:
        out = triangle.triangulate(tri_in, "pQ")
    except Exception as exc:
        logger.debug("triangulate failed: %s", exc)
        return None
    v2, f = out.get("vertices"), out.get("triangles")
    if f is None or not len(f):
        return None
    ground = terrain.sample(v2[:, 0], v2[:, 1])
    top = np.column_stack([v2, ground + top_off])
    p = top[f]
    ccw = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
           - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0])) > 0
    f = np.where(ccw[:, None], f, f[:, ::-1])
    return _close_surface(top, f, lambda xy: terrain.sample(xy[:, 0], xy[:, 1]) + bottom_off)


def build_layer(name: str, features: list[dict], style: LayerStyle,
                terrain: Terrain) -> tuple[list[Mesh], dict]:
    """Solids for one layer, plus counts for the report."""
    polys, props, counts = feature_polygons(name, features, style, terrain)
    stats = {"features": len(features), "polygons": len(polys), **counts}
    solids: list[Mesh] = []
    if style.mode == "extrude":
        z_per_m = terrain.scale.z_mm_per_m * style.height_scale
        lo, hi = terrain.ranges_under(polys)
        cap = (style.max_slenderness * _widths(np.asarray(polys, dtype=object))
               if style.max_slenderness > 0 and polys else np.full(len(polys), np.inf))
        want = np.array([float(p.get("height_m") or 10.0) for p in props]) * z_per_m
        stats["clamped"] = int((want > cap).sum())
        for poly, pr, l_, h_, c_ in zip(polys, props, lo, hi, cap, strict=True):
            m = _roofed(poly, pr, max(l_ - 0.2, 0.0), h_, z_per_m, style, c_)
            if m is not None:
                solids.append(m)
    elif style.mode == "water":
        lo, hi = terrain.ranges_under(polys)
        for poly, l_, h_ in zip(polys, lo, hi, strict=True):
            m = _prism(poly, max(l_ - style.offset_mm, 0.1), h_ + 1.0)
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


# ---------------------------------------------------------------------------
# Boolean assembly
# ---------------------------------------------------------------------------

def _manifold(mesh: Mesh) -> mf.Manifold:
    v, f = mesh
    return mf.Manifold(mf.Mesh(vert_properties=np.ascontiguousarray(v, np.float32),
                               tri_verts=np.ascontiguousarray(f, np.uint32)))


CONTACT_SPLIT_MM = 1e-3   # separation given to solids that touch at a point or edge


def _to_trimesh(m: mf.Manifold) -> trimesh.Trimesh:
    out = m.to_mesh()
    v = np.asarray(out.vert_properties)[:, :3].astype(np.float64)
    f = np.asarray(out.tri_verts)
    return trimesh.Trimesh(_separate_contacts(v, f), f, process=False)


def _separate_contacts(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Pull apart vertices that share a position (solids touching at a corner/edge).

    manifold3d keeps such contacts as distinct vertices, so its mesh is closed, but
    STL has no indices: any reader welds them back into edges used four times.
    Moving each copy ``CONTACT_SPLIT_MM`` towards its own faces (far below print
    resolution) keeps the file watertight after welding.
    """
    _, inv, counts = np.unique(v, axis=0, return_inverse=True, return_counts=True)
    dup = counts[inv.ravel()] > 1
    if not dup.any():
        return v
    centre = np.zeros_like(v)
    n = np.zeros(len(v))
    tri_c = v[f].mean(axis=1)
    for k in range(3):
        np.add.at(centre, f[:, k], tri_c)
        np.add.at(n, f[:, k], 1)
    d = centre[dup] / np.maximum(n[dup], 1)[:, None] - v[dup]
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
    # Copies whose fans point the same way would land together again: the k-th
    # copy of a position moves (k + 1) steps.
    group = inv.ravel()[dup]
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


def _union(solids: list[Mesh]) -> tuple[mf.Manifold | None, int]:
    """Union of the valid solids, and how many were rejected as non-manifold."""
    ms = [_manifold(s) for s in solids]
    good = [m for m in ms if m.status() == mf.Error.NoError and not m.is_empty()]
    if not good:
        return None, len(ms)
    u = mf.Manifold.batch_boolean(good, mf.OpType.Add) if len(good) > 1 else good[0]
    return u, len(ms) - len(good)


def lossless_simplify(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Merge coplanar regions without moving any surface (numpy2stl, verified here).

    Flat roofs, walls, the base and flat ground collapse to few triangles; the
    result keeps only original vertices. Returns the input if the check fails.
    """
    from numpy2stl.processing.simplify import simplify_mesh_surfaces

    faces = simplify_mesh_surfaces(np.asarray(mesh.vertices), np.asarray(mesh.faces))
    out = trimesh.Trimesh(mesh.vertices, faces, process=False)
    out.remove_unreferenced_vertices()
    # Closed = every edge used an even number of times. Solids that touch along an
    # edge (a building flush with a wall) share it four times once coincident
    # vertices are welded - exactly as they do in any STL - which is not a hole.
    _, uses = np.unique(np.sort(out.edges, axis=1), axis=0, return_counts=True)
    if not (uses % 2).any() and math.isclose(out.volume, mesh.volume, rel_tol=1e-6):
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
                            layer_overrides=layer_overrides)


def build_on_terrain(
    z_mm: np.ndarray,
    bbox: dict | None,
    scale: ModelScale,
    layers_geojson: dict[str, dict],
    *,
    terrain_max_error_mm: float | None = None,
    simplify: bool = True,
    layer_overrides: dict | None = None,
) -> CityModel:
    """The feature stage: OSM layers on a finished terrain heightfield.

    ``z_mm`` is the terrain top surface in model mm (row 0 north, base included),
    as produced by the terrain stage with ``scale``. With no layers this is the
    terrain-only model every mesh export uses. ``bbox`` is needed only to place
    features.
    """
    dem_shape = z_mm.shape
    terrain = Terrain(np.asarray(z_mm, np.float64), bbox or {}, scale)
    if terrain_max_error_mm is None:
        terrain_max_error_mm = max(TERRAIN_MAX_ERROR_MM,
                                   0.5 * SOURCE_VERTICAL_STEP_M * scale.z_mm_per_m)
    t0 = time.perf_counter()
    ground = terrain_solid(terrain, terrain_max_error_mm)
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

    adds: dict[str, mf.Manifold] = {}
    cuts: dict[str, mf.Manifold] = {}
    for name in LAYER_PRIORITY + [n for n in styles if n not in LAYER_PRIORITY]:
        style = styles.get(name)
        feats = (layers_geojson.get(name) or {}).get("features") or []
        if style is None or not style.enabled or not feats:
            continue
        if not bbox:
            raise ValueError(f"layer {name!r} needs the model's bbox to place its features")
        t0 = time.perf_counter()
        solids, stats = build_layer(name, feats, style, terrain)
        t1 = time.perf_counter()
        u, rejected = _union(solids)
        report["layers"][name] = {"mode": style.mode, **stats, "rejected": rejected,
                                  "seconds": {"geometry": round(t1 - t0, 2),
                                              "union": round(time.perf_counter() - t1, 2)}}
        if u is not None:
            (cuts if style.mode in ("engraved", "water") else adds)[name] = u

    t0 = time.perf_counter()
    cut_all = mf.Manifold.batch_boolean(list(cuts.values()), mf.OpType.Add) if cuts else None
    ground_cut = ground_m - cut_all if cut_all is not None else ground_m

    parts: dict[str, trimesh.Trimesh] = {"terrain": _to_trimesh(ground_cut)}
    claimed = ground_m
    for name, m in adds.items():
        own = m - claimed
        if cut_all is not None:
            own = own - cut_all
        if not own.is_empty():
            parts[name] = _to_trimesh(own)
        claimed = claimed + m
    merged = _to_trimesh(claimed - cut_all if cut_all is not None else claimed)
    timings["assemble"] = round(time.perf_counter() - t0, 2)
    t0 = time.perf_counter()
    if simplify:
        before = len(merged.faces)
        merged = lossless_simplify(merged)
        parts = {k: lossless_simplify(v) for k, v in parts.items()}
        report["lossless_simplify"] = {"faces_before": before, "faces_after": len(merged.faces)}
        timings["simplify"] = round(time.perf_counter() - t0, 2)
    report["seconds"] = timings

    report["merged"] = {"faces": len(merged.faces), "watertight": welded_watertight(merged),
                        "volume_mm3": round(float(merged.volume), 1),
                        "size_mm": [round(float(x), 2) for x in merged.extents]}
    report["parts"] = {k: len(v.faces) for k, v in parts.items()}
    return CityModel(merged, parts, scale, report)
