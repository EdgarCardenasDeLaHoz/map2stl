"""
core/cities_3d.py — 3D city mesh generation: extruded buildings + terrain.

Cities 10+12: generates a 3MF file containing a terrain layer and building
prisms from GeoJSON footprints + DEM elevation data.

No dependencies beyond numpy and the standard library.
write3MF is imported from numpy2stl which is already used by core/export.py.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Add strm2stl root to sys.path so `numpy2stl.numpy2stl.*` is importable.
# Must point to Code/strm2stl/ (outer dir that contains the numpy2stl/ package),
# NOT Code/strm2stl/numpy2stl/ — that level would cache the inner package as
# `numpy2stl` and break the `numpy2stl.numpy2stl.oceans` import used by dem.py.
_STRM2STL_DIR = Path(__file__).parent.parent.parent
if str(_STRM2STL_DIR) not in sys.path:
    sys.path.insert(0, str(_STRM2STL_DIR))

try:
    from numpy2stl.numpy2stl.save import write3MF as _write3mf
    _WRITE3MF_AVAILABLE = True
except ImportError:
    _WRITE3MF_AVAILABLE = False


# ---------------------------------------------------------------------------
# 2-D polygon triangulation (ear-clipping, pure numpy)
# ---------------------------------------------------------------------------

def _cross2(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _point_in_triangle(p, a, b, c):
    d1 = _cross2(a, b, p)
    d2 = _cross2(b, c, p)
    d3 = _cross2(c, a, p)
    return (d1 >= 0 and d2 >= 0 and d3 >= 0) or (d1 <= 0 and d2 <= 0 and d3 <= 0)


def _ear_clip(pts: np.ndarray) -> List[Tuple[int, int, int]]:
    """
    Ear-clipping triangulation for a simple 2-D polygon given as Nx2 array.
    Returns a list of (i, j, k) index triples. O(n²) — fine for building footprints.
    """
    n = len(pts)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]

    # Ensure CCW orientation via signed area
    area = sum(_cross2(pts[0], pts[i], pts[i + 1]) for i in range(1, n - 1))
    flipped = area < 0
    if flipped:
        pts = pts[::-1].copy()

    idx = list(range(n))
    tris: List[Tuple[int, int, int]] = []
    max_iter = n * n * 2

    for _ in range(max_iter):
        if len(idx) < 3:
            break
        clipped = False
        m = len(idx)
        for i in range(m):
            a = idx[(i - 1) % m]
            b = idx[i]
            c = idx[(i + 1) % m]
            cross = _cross2(pts[a], pts[b], pts[c])
            if cross <= 0:
                continue  # reflex vertex
            # Check no other vertex lies inside triangle (a, b, c)
            inside = any(
                j not in (a, b, c) and _point_in_triangle(pts[j], pts[a], pts[b], pts[c])
                for j in idx
            )
            if not inside:
                tris.append((a, b, c))
                idx.pop(i)
                clipped = True
                break
        if not clipped:
            break  # degenerate polygon

    if len(idx) == 3:
        tris.append(tuple(idx))  # type: ignore[arg-type]

    if flipped:
        # Remap reversed-array indices back to original polygon indices and flip
        # winding so the resulting triangles are CCW in the original (CW) array,
        # giving an outward normal pointing UP (+Z) for roof faces.
        return [(n - 1 - a, n - 1 - c, n - 1 - b) for a, b, c in tris]
    return tris


# ---------------------------------------------------------------------------
# Building prism (extruded polygon)
# ---------------------------------------------------------------------------

def _extrude_ring(
    ring: List[List[float]],
    z0: float,
    z1: float,
    lon_to_x,
    lat_to_y,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Extrude one GeoJSON exterior ring into a closed 3-D prism.

    Args:
        ring:     list of [lon, lat] pairs (closing duplicate may be present)
        z0:       base elevation in mm (bottom of building)
        z1:       roof elevation in mm (top of building)
        lon_to_x: callable(lon) → x_mm
        lat_to_y: callable(lat) → y_mm

    Returns:
        (vertices Nx3 float32, faces Mx3 int32), or (None, None) on error.
    """
    # Drop closing duplicate
    raw = ring[:-1] if ring and len(ring) > 1 and ring[0] == ring[-1] else ring
    pts_mm = np.array([[lon_to_x(lo), lat_to_y(la)] for lo, la in raw], dtype=float)
    n = len(pts_mm)
    if n < 3:
        return None, None

    # Roof and floor vertex arrays
    roof   = np.column_stack([pts_mm, np.full(n, z1)])
    floor_ = np.column_stack([pts_mm, np.full(n, z0)])
    verts  = np.vstack([roof, floor_]).astype(np.float32)  # [0..n-1]=roof, [n..2n-1]=floor

    faces: List[List[int]] = []

    # Roof faces (CCW from above → correct outward normal)
    for a, b, c in _ear_clip(pts_mm):
        faces.append([a, b, c])

    # Floor faces (CW = inward normal from above)
    for a, b, c in _ear_clip(pts_mm):
        faces.append([n + c, n + b, n + a])

    # Wall quads (2 triangles per edge)
    for i in range(n):
        j = (i + 1) % n
        ri, rj = i, j
        fi, fj = n + i, n + j
        faces.append([ri, rj, fj])
        faces.append([ri, fj, fi])

    if not faces:
        return None, None
    return verts, np.array(faces, dtype=np.int32)


def _build_building_meshes(
    buildings_geojson: dict,
    bbox: dict,
    W_mm: float,
    H_mm: float,
    base_mm: float,
    model_height_mm: float,
    z_min: float,
    z_max: float,
    building_z_scale: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Convert a GeoJSON FeatureCollection of buildings into a single combined mesh.

    Each building's terrain_z and height_m properties are used for z0/z1.
    """
    north = bbox["north"]; south = bbox["south"]
    east  = bbox["east"];  west  = bbox["west"]
    lat_range = north - south or 1.0
    lon_range = east  - west  or 1.0
    z_range   = (z_max - z_min) or 1.0

    def lon_to_x(lo): return (lo - west)  / lon_range * W_mm
    def lat_to_y(la): return (la - south) / lat_range * H_mm
    def elev_to_z(e): return base_mm + (e - z_min) / z_range * model_height_mm

    all_verts: List[np.ndarray] = []
    all_faces: List[np.ndarray] = []
    v_offset = 0

    features = buildings_geojson.get("features") or []
    for feat in features:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue

        height_m  = float(props.get("height_m") or 10)
        terrain_z = float(props.get("terrain_z") or z_min)
        z0 = elev_to_z(terrain_z)
        z1 = z0 + height_m * building_z_scale

        # Collect rings
        if geom["type"] == "Polygon":
            rings = [geom["coordinates"][0]]          # exterior only
        elif geom["type"] == "MultiPolygon":
            rings = [poly[0] for poly in geom["coordinates"]]
        else:
            continue

        for ring in rings:
            v, f = _extrude_ring(ring, z0, z1, lon_to_x, lat_to_y)
            if v is None:
                continue
            all_verts.append(v)
            all_faces.append(f + v_offset)
            v_offset += len(v)

    if not all_verts:
        # Return a tiny placeholder so write3MF doesn't choke
        v = np.zeros((3, 3), dtype=np.float32)
        f = np.array([[0, 1, 2]], dtype=np.int32)
        return v, f

    return np.vstack(all_verts), np.vstack(all_faces)


# ---------------------------------------------------------------------------
# Terrain mesh
# ---------------------------------------------------------------------------

def _terrain_mesh(
    dem_arr: np.ndarray,
    W_mm: float,
    H_mm: float,
    base_mm: float,
    model_height_mm: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Convert a 2-D DEM array [rows, cols] into a closed, printable terrain mesh.

    Surface: grid of triangles (DEM elevation mapped to [base_mm, base_mm+model_height_mm]).
    Bottom: flat rectangle at z=0.
    Walls: triangle strips connecting terrain edges to z=0.

    Returns (vertices Nx3 float32, faces Mx3 int32).
    """
    rows, cols = dem_arr.shape
    z_min = float(dem_arr.min())
    z_max = float(dem_arr.max())
    z_range = (z_max - z_min) or 1.0

    xs = np.linspace(0.0, W_mm, cols)
    ys = np.linspace(H_mm, 0.0, rows)   # row 0 → north (H_mm), last row → south (0)
    xx, yy = np.meshgrid(xs, ys)
    zz = base_mm + (dem_arr - z_min) / z_range * model_height_mm

    # ── Top surface vertices ─────────────────────────────────────────────────
    top_v = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])

    def ti(r, c): return r * cols + c

    faces: List[List[int]] = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            tl, tr_, bl, br = ti(r, c), ti(r, c + 1), ti(r + 1, c), ti(r + 1, c + 1)
            # CCW winding viewed from above → outward normal points UP (+Z)
            faces.extend([[tl, bl, tr_], [tr_, bl, br]])

    # ── Skirt vertices at z=0 ────────────────────────────────────────────────
    left_skirt  = np.column_stack([np.zeros(rows),          np.linspace(H_mm, 0, rows), np.zeros(rows)])
    right_skirt = np.column_stack([np.full(rows, W_mm),     np.linspace(H_mm, 0, rows), np.zeros(rows)])
    back_skirt  = np.column_stack([np.linspace(0, W_mm, cols), np.full(cols, H_mm),     np.zeros(cols)])
    front_skirt = np.column_stack([np.linspace(0, W_mm, cols), np.zeros(cols),           np.zeros(cols)])

    n_top = len(top_v)
    li = n_top;            n_top += rows    # left  skirt index start
    ri = n_top;            n_top += rows    # right skirt index start
    bi = n_top;            n_top += cols    # back  skirt index start
    fi = n_top;            n_top += cols    # front skirt index start

    all_v = np.vstack([top_v, left_skirt, right_skirt, back_skirt, front_skirt])

    # ── Side walls ───────────────────────────────────────────────────────────
    for r in range(rows - 1):
        t0 = ti(r, 0);        t1 = ti(r + 1, 0)
        s0 = li + r;          s1 = li + r + 1
        faces.extend([[t0, s0, t1], [s0, s1, t1]])          # left

    for r in range(rows - 1):
        t0 = ti(r, cols - 1); t1 = ti(r + 1, cols - 1)
        s0 = ri + r;          s1 = ri + r + 1
        faces.extend([[t0, t1, s0], [s0, t1, s1]])           # right

    for c in range(cols - 1):
        t0 = ti(0, c);        t1 = ti(0, c + 1)
        s0 = bi + c;          s1 = bi + c + 1
        faces.extend([[t0, t1, s0], [s0, t1, s1]])           # back (north)

    for c in range(cols - 1):
        t0 = ti(rows - 1, c); t1 = ti(rows - 1, c + 1)
        s0 = fi + c;          s1 = fi + c + 1
        faces.extend([[t0, s0, t1], [s0, s1, t1]])           # front (south)

    # ── Bottom plate ─────────────────────────────────────────────────────────
    # Corners reuse skirt vertices: li+rows-1=(0,0,0), ri+rows-1=(W,0,0),
    #                                ri+0=(W,H,0),       li+0=(0,H,0)
    bl_c = li + rows - 1
    br_c = ri + rows - 1
    tr_c = ri + 0
    tl_c = li + 0
    # CCW winding viewed from below → outward normal points DOWN (-Z)
    faces.extend([[bl_c, tr_c, br_c], [bl_c, tl_c, tr_c]])

    return all_v.astype(np.float32), np.array(faces, dtype=np.int32)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_city_3mf(
    buildings_geojson: dict,
    dem_values: List[float],
    dem_width: int,
    dem_height: int,
    bbox: dict,           # {north, south, east, west}
    model_height_mm: float = 20.0,
    base_mm: float = 5.0,
    building_z_scale: float = 0.5,
    simplify_terrain: bool = True,    # Cities 14: reduce terrain triangle count
    terrain_max_dim: int = 150,       # downsample DEM to at most this in each axis
    name: str = "city",
) -> bytes:
    """
    Generate a 3MF file (returned as bytes) containing:
      - "terrain"  : terrain mesh from DEM
      - "buildings": extruded building prisms

    Args:
        buildings_geojson : GeoJSON FeatureCollection; features need height_m + terrain_z props
        dem_values        : flat row-major DEM elevation array (metres)
        dem_width / dem_height : DEM grid dimensions
        bbox              : {north, south, east, west} in degrees
        model_height_mm   : target Z height for the terrain elevation range
        base_mm           : base plate thickness in mm
        building_z_scale  : mm per real metre for building heights
        terrain_max_dim   : max grid dimension; downsamples large DEMs
        name              : base name for the 3MF objects
    """
    if not _WRITE3MF_AVAILABLE:
        raise RuntimeError("numpy2stl.save.write3MF not available; check numpy2stl path")

    # Reshape DEM
    dem_arr = np.array(dem_values, dtype=np.float32).reshape(dem_height, dem_width)

    # Downsample if necessary (avoid huge meshes)
    if max(dem_height, dem_width) > terrain_max_dim:
        factor = terrain_max_dim / max(dem_height, dem_width)
        new_h = max(4, int(dem_height * factor))
        new_w = max(4, int(dem_width  * factor))
        try:
            from skimage.transform import resize as sk_resize
            dem_arr = sk_resize(dem_arr, (new_h, new_w), anti_aliasing=True).astype(np.float32)
        except ImportError:
            # Manual strided downsample
            row_step = max(1, dem_height // new_h)
            col_step = max(1, dem_width  // new_w)
            dem_arr  = dem_arr[::row_step, ::col_step]

    rows, cols = dem_arr.shape
    z_min = float(dem_arr.min())
    z_max = float(dem_arr.max())

    # Physical XY dimensions: preserve geographic aspect ratio, target 150 mm wide
    north = bbox["north"]; south = bbox["south"]
    east  = bbox["east"];  west  = bbox["west"]
    lat_mid  = (north + south) / 2
    lon_range = (east - west) * math.cos(math.radians(lat_mid))
    lat_range = north - south
    aspect = lon_range / (lat_range or 1.0)
    W_mm  = 150.0
    H_mm  = W_mm / aspect if aspect > 0 else W_mm

    # ── Terrain mesh ─────────────────────────────────────────────────────────
    t_verts, t_faces = _terrain_mesh(dem_arr, W_mm, H_mm, base_mm, model_height_mm)

    # ── Cities 14: optional mesh simplification on terrain ───────────────────
    if simplify_terrain:
        try:
            from numpy2stl.numpy2stl.simplify import simplify_mesh_surfaces
            t_faces = simplify_mesh_surfaces(t_verts, t_faces)
            logger.info(f"Terrain mesh simplified to {len(t_faces)} faces")
        except Exception as e:
            logger.warning(f"Mesh simplification skipped: {e}")

    # ── Building meshes ──────────────────────────────────────────────────────
    b_verts, b_faces = _build_building_meshes(
        buildings_geojson, bbox, W_mm, H_mm, base_mm, model_height_mm,
        z_min, z_max, building_z_scale,
    )

    # ── Write 3MF to in-memory bytes ─────────────────────────────────────────
    with tempfile.NamedTemporaryFile(suffix=".3mf", delete=False) as tf:
        tmp_path = tf.name

    try:
        _write3mf(tmp_path, {
            f"{name}_terrain":   (t_verts, t_faces),
            f"{name}_buildings": (b_verts, b_faces),
        })
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(tmp_path)
