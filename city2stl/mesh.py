"""
city2stl/mesh.py — building prism geometry: ear-clipping, hole bridging, and
extrusion of footprint rings with OSM roof shapes (flat, gabled, hipped,
skillion, pyramidal).

Used by city2stl/city_model.py, which places the prisms on the terrain and
merges everything into one solid. No HTTP, cache, or server dependencies.
"""

from __future__ import annotations

import logging

import numpy as np
from numpy2stl.core.generate import polygon_to_prism as _polygon_to_prism
from numpy2stl.core.solid import vertices_to_index as _vertices_to_index
from shapely.geometry import Polygon as _ShapelyPolygon

logger = logging.getLogger(__name__)


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


def _ear_clip(pts: np.ndarray) -> list[tuple[int, int, int]]:
    """
    Ear-clipping triangulation for a simple 2-D polygon given as Nx2 array.
    Returns a list of (i, j, k) index triples. O(n^2) -- fine for building footprints.
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
    tris: list[tuple[int, int, int]] = []
    max_iter = n * n * 2

    # A vertex sitting on a corner of the candidate triangle is not evidence against the ear;
    # it is the same point named twice. Rings that have had a hole bridged into them contain
    # such pairs by construction, and without this the containment test rejects every ear near
    # the bridge and the triangulation returns nothing at all.
    diag = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0))) or 1.0
    tol2 = (1e-5 * diag) ** 2

    def coincident(p, q) -> bool:
        d = p - q
        return float(d[0] * d[0] + d[1] * d[1]) <= tol2

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
                j not in (a, b, c)
                and not (coincident(pts[j], pts[a]) or coincident(pts[j], pts[b])
                         or coincident(pts[j], pts[c]))
                and _point_in_triangle(pts[j], pts[a], pts[b], pts[c])
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

# Two points closer together than this in millimetres are the same point as far
# as the triangulator is concerned. At the city scale one micron is far below
# any real geometry and far above float noise in the lon/lat -> mm transform.
_RING_EPS_MM = 1e-3


def _sanitize_ring_xy(pts_mm: np.ndarray) -> np.ndarray | None:
    """Make a 2-D ring safe to hand to the triangulator, or reject it.

    Triangle is a C library reached through a Python wrapper, and on a
    degenerate input it does not raise -- it faults. An access violation
    inside triangulate cannot be caught by the try/except around
    polygon_to_prism, so it takes the whole process down, which in production
    means the server dies mid-export with no traceback on either side. Seville
    did exactly that three times. So the ring has to be checked before the call
    rather than after it.

    Rejects non-finite coordinates, collapses repeated vertices, and requires
    at least three distinct points enclosing a non-zero area. Rings that are
    merely self-intersecting are repaired with a zero-width buffer rather than
    dropped, since the hole-bridged rings this pipeline builds for courtyard
    buildings self-intersect by construction and are otherwise fine.

    Returns the cleaned Nx2 array, or None if nothing usable remains.
    """
    if pts_mm.ndim != 2 or pts_mm.shape[1] != 2 or len(pts_mm) < 3:
        return None
    pts = pts_mm[np.isfinite(pts_mm).all(axis=1)]
    if len(pts) < 3:
        return None

    # Collapse consecutive duplicates, treating the ring as closed so that a
    # first point equal to the last is caught too.
    keep = [0]
    for i in range(1, len(pts)):
        if np.hypot(*(pts[i] - pts[keep[-1]])) > _RING_EPS_MM:
            keep.append(i)
    if len(keep) >= 2 and np.hypot(*(pts[keep[-1]] - pts[keep[0]])) <= _RING_EPS_MM:
        keep.pop()
    pts = pts[keep]
    if len(pts) < 3:
        return None

    # Shoelace area. A zero-area ring is a line, and Triangle has no answer
    # for a polygon with no interior.
    x, y = pts[:, 0], pts[:, 1]
    area = 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    if abs(area) < _RING_EPS_MM:
        return None

    try:
        poly = _ShapelyPolygon(pts)
        if not poly.is_valid:
            fixed = poly.buffer(0)
            if fixed.is_empty:
                return None
            if fixed.geom_type == "MultiPolygon":
                fixed = max(fixed.geoms, key=lambda g: g.area)
            if fixed.geom_type != "Polygon":
                return None
            repaired = np.asarray(fixed.exterior.coords, dtype=float)[:-1]
            if len(repaired) < 3:
                return None
            return repaired
    except Exception as _e:                                   # noqa: BLE001
        logger.debug("ring repair failed (%s); rejecting ring", _e)
        return None

    return pts


def _extrude_ring(
    ring: list[list[float]],
    z0: float,
    z1: float,
    lon_to_x,
    lat_to_y,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """
    Extrude one GeoJSON exterior ring into a closed 3-D prism.

    Delegates to numpy2stl.generate.polygon_to_prism when available,
    falling back to the manual ear-clip implementation.

    Args:
        ring:     list of [lon, lat] pairs (closing duplicate may be present)
        z0:       base elevation in mm (bottom of building)
        z1:       roof elevation in mm (top of building)
        lon_to_x: callable(lon) -> x_mm
        lat_to_y: callable(lat) -> y_mm

    Returns:
        (vertices Nx3 float32, faces Mx3 int32), or (None, None) on error.
    """
    # Drop closing duplicate
    raw = ring[:-1] if ring and len(ring) > 1 and ring[0] == ring[-1] else ring
    pts_mm = np.array([[lon_to_x(lo), lat_to_y(la)] for lo, la in raw], dtype=float)
    cleaned = _sanitize_ring_xy(pts_mm)
    if cleaned is None:
        return None, None
    pts_mm = cleaned
    n = len(pts_mm)

    try:
        verts_3d = np.column_stack([pts_mm, np.full(n, z1)])
        raw_tris = _polygon_to_prism(verts_3d, perimeters=[np.arange(n)], base_val=z0)
        verts, faces = _vertices_to_index(raw_tris)
        return verts.astype(np.float32), faces.astype(np.int32)
    except Exception as _e:
        logger.debug("polygon_to_prism failed (%s), using fallback ear-clip", _e)

    # -- Fallback: manual ear-clip implementation -------------------------
    roof   = np.column_stack([pts_mm, np.full(n, z1)])
    floor_ = np.column_stack([pts_mm, np.full(n, z0)])
    verts  = np.vstack([roof, floor_]).astype(np.float32)  # [0..n-1]=roof, [n..2n-1]=floor

    faces: list[list[int]] = []

    # Roof faces (CCW from above -> correct outward normal)
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


# ---------------------------------------------------------------------------
# Building prism with shaped roof
# ---------------------------------------------------------------------------

def _extrude_ring_with_roof(
    ring: list[list[float]],
    z0: float,
    z1: float,
    roof_shape: str,
    roof_height_mm: float,
    lon_to_x,
    lat_to_y,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Extrude one GeoJSON exterior ring into a closed 3-D prism with a shaped roof.

    Supported ``roof_shape`` values (from the OSM ``roof:shape`` tag):
    - ``"flat"``      — identical to ``_extrude_ring``; ignores roof_height_mm
    - ``"pyramidal"`` — all walls rise to z1; roof is a fan of triangles to a
                        central apex at z1 + roof_height_mm
    - ``"gabled"``    — walls rise to z1; roof has a ridge along the principal
                        axis at z1 + roof_height_mm, sloping down to z1 on both
                        sides via per-vertex height interpolation
    - ``"hipped"``    — same as gabled (the hip ends are handled implicitly by
                        the per-vertex interpolation)
    - ``"skillion"``  — single-slope roof; one side at z1, opposite side at
                        z1 + roof_height_mm; slope follows the principal axis
    - ``"dome"``      — approximated as pyramidal for mesh simplicity
    - any other value — falls back to ``"flat"``

    Args:
        ring:           list of [lon, lat] pairs (GeoJSON exterior ring)
        z0:             base elevation in mm
        z1:             eave / wall-top elevation in mm
        roof_shape:     OSM roof:shape string
        roof_height_mm: additional height above z1 for the roof peak in mm
        lon_to_x:       callable(lon) -> x_mm
        lat_to_y:       callable(lat) -> y_mm

    Returns:
        (vertices Nx3 float32, faces Mx3 int32), or (None, None) on error.
    """
    shape = (roof_shape or "flat").lower().strip()

    # Shapes that map to pyramidal
    if shape in ("dome", "onion", "cone"):
        shape = "pyramidal"

    # Unsupported / unknown shapes fall back to flat
    if shape not in ("flat", "pyramidal", "gabled", "hipped", "skillion"):
        shape = "flat"

    # Flat or zero-height roof: delegate to the standard extruder
    if shape == "flat" or roof_height_mm <= 0:
        return _extrude_ring(ring, z0, z1, lon_to_x, lat_to_y)

    # Convert ring to 2-D mm coordinates
    raw = ring[:-1] if ring and len(ring) > 1 and ring[0] == ring[-1] else ring
    pts_mm = np.array([[lon_to_x(lo), lat_to_y(la)] for lo, la in raw], dtype=np.float64)
    cleaned = _sanitize_ring_xy(pts_mm)
    if cleaned is None:
        return None, None
    pts_mm = cleaned
    n = len(pts_mm)

    # Compute per-vertex roof heights above z1 ----------------------------
    apex: np.ndarray | None = None
    has_apex = False

    if shape == "pyramidal":
        roof_z = np.full(n, z1)
        cx, cy = pts_mm.mean(axis=0)
        apex = np.array([cx, cy, z1 + roof_height_mm], dtype=np.float64)
        has_apex = True
    else:
        # PCA: principal axis via covariance of vertex positions
        ctr = pts_mm.mean(axis=0)
        rel = pts_mm - ctr
        cov = (rel.T @ rel) / max(n, 1)
        _, eigvecs = np.linalg.eigh(cov)  # eigenvalues ascending
        perp_axis = eigvecs[:, 0]         # shortest axis (perpendicular to ridge)
        along_axis = eigvecs[:, 1]        # longest axis (along ridge)

        if shape in ("gabled", "hipped"):
            # Height drops linearly with distance from the ridge axis
            d_perp = rel @ perp_axis
            max_d = max(float(np.abs(d_perp).max()), 1e-6)
            roof_z = z1 + roof_height_mm * np.maximum(0.0, 1.0 - np.abs(d_perp) / max_d)
        else:  # skillion
            # Height increases linearly from one side to the other
            d_along = rel @ along_axis
            d_min, d_max = float(d_along.min()), float(d_along.max())
            span = max(d_max - d_min, 1e-6)
            t = (d_along - d_min) / span  # [0, 1]
            roof_z = z1 + roof_height_mm * t

    # Build vertex arrays -------------------------------------------------
    # [0 .. n-1]    : roof ring vertices with per-vertex z
    # [n .. 2n-1]   : floor ring vertices all at z0
    # [2n]          : apex (pyramidal only)
    roof_ring = np.column_stack([pts_mm, roof_z]).astype(np.float32)
    floor_ring = np.column_stack([pts_mm, np.full(n, z0)]).astype(np.float32)

    if has_apex and apex is not None:
        verts = np.vstack([roof_ring, floor_ring, apex.reshape(1, 3).astype(np.float32)])
        apex_idx = 2 * n
    else:
        verts = np.vstack([roof_ring, floor_ring])
        apex_idx = -1  # unused

    faces: list[list[int]] = []

    # Floor (flat bottom, winding reversed so normal points down) ---------
    for a, b, c in _ear_clip(pts_mm):
        faces.append([n + c, n + b, n + a])

    # Walls: one quad per edge of the ring --------------------------------
    for i in range(n):
        j = (i + 1) % n
        ri, rj = i, j          # roof-level ring indices
        fi, fj = n + i, n + j  # floor-level ring indices
        faces.append([ri, rj, fj])
        faces.append([ri, fj, fi])

    # Roof -----------------------------------------------------------------
    if has_apex:
        # Pyramidal: fan from apex to each ring edge
        for i in range(n):
            j = (i + 1) % n
            # CCW order when viewed from outside: ring edge goes i→j,
            # apex is above → order [j, i, apex] gives outward normal
            faces.append([j, i, apex_idx])
    else:
        # Non-flat shapes: ear-clip the 2-D projection, apply to roof ring
        for a, b, c in _ear_clip(pts_mm):
            faces.append([a, b, c])

    if not faces:
        return None, None
    return verts, np.array(faces, dtype=np.int32)
