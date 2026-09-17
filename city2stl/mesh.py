"""
city2stl/mesh.py — 3D city mesh generation: extruded buildings + terrain.

Provides pure-computation geometry and mesh assembly for the city 3MF pipeline.
No HTTP, cache, or server dependencies — importable from notebooks and tests.

Server entry point: app.server.core.cities_3d re-exports all public symbols
from this module as a thin shim.

-- Legacy note --
city2stl/buildings.py (triangulate_prism, get_polygons) and
city2stl/create.py (get_building_model, get_landspace_model) served the same
purpose with the old osmnx API. The functions in this module are the modern
replacement: _extrude_ring supersedes triangulate_prism, _build_building_meshes
supersedes get_building_model, and _terrain_mesh supersedes get_landspace_model.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile

import numpy as np

logger = logging.getLogger(__name__)

try:
    from numpy2stl import write3MF as _write3mf
    _WRITE3MF_AVAILABLE = True
except ImportError:
    _WRITE3MF_AVAILABLE = False

try:
    from numpy2stl import array_to_mesh as _array_to_mesh
    _ARRAY_TO_MESH_AVAILABLE = True
except ImportError:
    _ARRAY_TO_MESH_AVAILABLE = False

try:
    from shapely.geometry import Polygon as _ShapelyPolygon
    _SHAPELY_AVAILABLE = True
except ImportError:
    _SHAPELY_AVAILABLE = False

try:
    from numpy2stl.core.generate import polygon_to_prism as _polygon_to_prism
    from numpy2stl.core.solid import vertices_to_index as _vertices_to_index
    _POLYGON_TO_PRISM_AVAILABLE = True
except ImportError:
    _POLYGON_TO_PRISM_AVAILABLE = False


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
# Holes
# ---------------------------------------------------------------------------

def _segments_cross(p1, p2, q1, q2) -> bool:
    """True when segment p1-p2 properly crosses q1-q2, endpoints touching excluded."""
    d1 = _cross2(q1, q2, p1)
    d2 = _cross2(q1, q2, p2)
    d3 = _cross2(p1, p2, q1)
    d4 = _cross2(p1, p2, q2)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _ring_array(ring) -> np.ndarray:
    """A GeoJSON ring as an Nx2 array with any closing duplicate removed."""
    pts = np.asarray(ring, dtype=float)
    if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    return pts


def _point_in_ring(p, ring: np.ndarray) -> bool:
    """Even-odd crossing test for a point against a closed Nx2 ring."""
    x, y = float(p[0]), float(p[1])
    inside = False
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            t = (y - y0) / ((y1 - y0) or 1e-30)
            if x < x0 + t * (x1 - x0):
                inside = not inside
    return inside


def _oriented(pts: np.ndarray, ccw: bool) -> np.ndarray:
    """The ring, reversed if its winding is not the one asked for."""
    if len(pts) < 3:
        return pts
    x, y = pts[:, 0], pts[:, 1]
    signed = float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    return pts if (signed > 0) == ccw else pts[::-1].copy()


def _ring_area(pts: np.ndarray) -> float:
    """Unsigned shoelace area of an Nx2 ring."""
    x, y = pts[:, 0], pts[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2.0


def _bridge_holes(exterior, interiors) -> list[list[float]]:
    """One simple ring walking the exterior and every interior, joined by cuts.

    Ear clipping cannot see a hole; it needs a single closed loop. Each interior ring is
    therefore spliced into the exterior along a cut between the closest mutually visible pair of
    vertices, and is traversed in the opposite winding so that the interior stays on the outside
    of the resulting loop -- which is what makes the courtyard remain empty after triangulation.

    A cut that crosses any existing edge would produce a self-intersecting ring, so candidate
    pairs are tried nearest first and the first clear one wins. Footprints are small enough that
    the quadratic search costs nothing. An interior no cut can reach is dropped rather than
    allowed to corrupt the ring: a filled courtyard is wrong, a self-intersecting prism is worse.
    """
    outer = _oriented(_ring_array(exterior), ccw=True)
    if len(outer) < 3:
        return [list(map(float, p)) for p in outer]

    # The exterior runs counter-clockwise and every interior clockwise, whatever the source
    # claimed. GeoJSON specifies this and producers routinely ignore it; a hole spliced in with
    # the same winding as its exterior adds area instead of removing it.
    holes = [_oriented(_ring_array(h), ccw=False) for h in interiors]
    holes = [h for h in holes if len(h) >= 3]
    # Largest first: a big courtyard has more room to find a clear cut before the ring has been
    # complicated by earlier splices.
    holes.sort(key=lambda h: -_ring_area(h))

    for hole in holes:
        edges = [(outer[i], outer[(i + 1) % len(outer)]) for i in range(len(outer))]
        edges += [(hole[i], hole[(i + 1) % len(hole)]) for i in range(len(hole))]

        # Every (outer vertex, hole vertex) pair, closest first.
        d = np.linalg.norm(outer[:, None, :] - hole[None, :, :], axis=2)
        order = np.dstack(np.unravel_index(np.argsort(d, axis=None), d.shape))[0]

        spliced = False
        for oi, hi in order:
            a, b = outer[oi], hole[hi]
            # An edge meeting the cut at one of its own endpoints is not a crossing; only edges
            # clear of both ends can disqualify it.
            blocked = any(
                _segments_cross(a, b, e0, e1)
                for e0, e1 in edges
                if not (np.allclose(e0, a) or np.allclose(e0, b)
                        or np.allclose(e1, a) or np.allclose(e1, b))
            )
            if blocked:
                continue
            # A cut clear of every edge may still run outside the footprint, through a concave
            # notch in the exterior or straight across another hole. Its midpoint settles that.
            mid = (a + b) / 2.0
            if not _point_in_ring(mid, outer):
                continue
            if any(_point_in_ring(mid, other) for other in holes if other is not hole):
                continue
            # In along the cut, once round the hole, back out the same way.
            loop = list(hole[hi:]) + list(hole[:hi]) + [b]
            outer = np.array(list(outer[:oi + 1]) + loop + list(outer[oi:]), dtype=float)
            # The return leg of the cut coincides with the outward leg, which gives the ring two
            # pairs of identical vertices. Ear clipping cannot make progress on those -- every
            # candidate triangle contains its own duplicate -- so the return leg is moved aside
            # by a fraction of the footprint's own size. The resulting sliver is far below any
            # printable resolution but the triangulator can see round it.
            eps = 1e-6 * float(np.linalg.norm(outer.max(axis=0) - outer.min(axis=0)))
            cut = b - a
            norm = float(np.linalg.norm(cut))
            if norm > 0:
                perp = np.array([-cut[1], cut[0]]) / norm * eps
                outer[oi + len(hole) + 1] = outer[oi + len(hole) + 1] + perp
                outer[oi + len(hole) + 2] = outer[oi + len(hole) + 2] + perp
            spliced = True
            break
        if not spliced:
            logger.debug("no clear bridge for an interior ring; leaving it filled")

    return [[float(x), float(y)] for x, y in outer]


# ---------------------------------------------------------------------------
# Building prism (extruded polygon)
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

    if _SHAPELY_AVAILABLE:
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

    if _POLYGON_TO_PRISM_AVAILABLE:
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
) -> tuple[np.ndarray, np.ndarray]:
    """
    Convert a GeoJSON FeatureCollection of buildings into a single combined mesh.

    Each building's terrain_z and height_m properties are used for z0/z1.
    """
    north = bbox["north"]
    south = bbox["south"]
    east  = bbox["east"]
    west  = bbox["west"]
    lat_range = north - south or 1.0
    lon_range = east  - west  or 1.0
    z_range   = (z_max - z_min) or 1.0

    def lon_to_x(lo): return (lo - west)  / lon_range * W_mm
    def lat_to_y(la): return (la - south) / lat_range * H_mm
    def elev_to_z(e): return base_mm + (e - z_min) / z_range * model_height_mm

    all_verts: list[np.ndarray] = []
    all_faces: list[np.ndarray] = []
    v_offset = 0

    features = buildings_geojson.get("features") or []
    for feat in features:
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            continue

        height_m  = float(props.get("height_m") or 10)
        terrain_raw = props.get("terrain_z")
        terrain_z = z_min if terrain_raw is None else float(terrain_raw)
        z0 = elev_to_z(terrain_z)
        z1 = z0 + height_m * building_z_scale

        # Roof shape from OSM roof:shape / roof:height tags
        roof_shape = (props.get("roof:shape") or "flat").lower().strip()
        roof_h_raw = props.get("roof:height")
        if roof_h_raw is not None:
            try:
                roof_height_mm = float(roof_h_raw) * building_z_scale
            except (ValueError, TypeError):
                roof_height_mm = 0.0
        else:
            # Default: 30 % of building height for non-flat roofs
            roof_height_mm = height_m * 0.30 * building_z_scale if roof_shape != "flat" else 0.0

        # Collect rings
        # Each part contributes one ring: its exterior, with any interiors bridged in so a
        # courtyard survives extrusion instead of being filled solid.
        if geom["type"] == "Polygon":
            parts = [geom["coordinates"]]
        elif geom["type"] == "MultiPolygon":
            parts = list(geom["coordinates"])
        else:
            continue
        rings = []
        for part in parts:
            if not part:
                continue
            rings.append(_bridge_holes(part[0], part[1:]) if len(part) > 1 else part[0])

        for ring in rings:
            if roof_shape != "flat" and roof_height_mm > 0:
                v, f = _extrude_ring_with_roof(
                    ring, z0, z1, roof_shape, roof_height_mm, lon_to_x, lat_to_y
                )
            else:
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
) -> tuple[np.ndarray, np.ndarray]:
    """
    Convert a 2-D DEM array [rows, cols] into a closed, printable terrain mesh.

    Delegates to numpy2stl.array_to_mesh(solid=True) when available.
    Pre-scales DEM values into [base_mm, base_mm+model_height_mm], then
    rescales the returned (col_idx, row_idx) coordinates to physical mm space.

    Returns (vertices Nx3 float32, faces Mx3 int32).
    """
    rows, cols = dem_arr.shape
    z_min = float(dem_arr.min())
    z_range = (float(dem_arr.max()) - z_min) or 1.0

    # Scale DEM to desired physical z range
    scaled = base_mm + (dem_arr - z_min) / z_range * model_height_mm

    if _ARRAY_TO_MESH_AVAILABLE:
        import io
        import sys
        _devnull = io.StringIO()
        _old_stdout, sys.stdout = sys.stdout, _devnull
        try:
            verts, faces = _array_to_mesh(scaled, floor_val=0.0, solid=True)
        finally:
            sys.stdout = _old_stdout

        if verts is None or len(verts) == 0:
            return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.int32)

        verts = verts.astype(np.float32).copy()
        # x: col_idx [0, cols-1] -> [0, W_mm]
        # y: row_idx [0, rows-1] -> [H_mm, 0]  (row 0 = north, inverted)
        col_scale = W_mm / max(cols - 1, 1)
        row_scale = H_mm / max(rows - 1, 1)
        verts[:, 0] = verts[:, 0] * col_scale
        verts[:, 1] = H_mm - verts[:, 1] * row_scale

        return verts, faces.astype(np.int32)

    # -- Fallback: manual implementation ---------------------------------
    xs = np.linspace(0.0, W_mm, cols)
    ys = np.linspace(H_mm, 0.0, rows)   # row 0 -> north (H_mm), last row -> south (0)
    xx, yy = np.meshgrid(xs, ys)
    zz = scaled

    # -- Top surface vertices ---------------------------------------------
    top_v = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])

    def ti(r, c): return r * cols + c

    faces: list[list[int]] = []
    for r in range(rows - 1):
        for c in range(cols - 1):
            tl, tr_, bl, br = ti(r, c), ti(r, c + 1), ti(r + 1, c), ti(r + 1, c + 1)
            faces.extend([[tl, bl, tr_], [tr_, bl, br]])

    # -- Skirt vertices at z=0 --------------------------------------------
    left_skirt  = np.column_stack([np.zeros(rows),          np.linspace(H_mm, 0, rows), np.zeros(rows)])
    right_skirt = np.column_stack([np.full(rows, W_mm),     np.linspace(H_mm, 0, rows), np.zeros(rows)])
    back_skirt  = np.column_stack([np.linspace(0, W_mm, cols), np.full(cols, H_mm),     np.zeros(cols)])
    front_skirt = np.column_stack([np.linspace(0, W_mm, cols), np.zeros(cols),           np.zeros(cols)])

    n_top = len(top_v)
    li = n_top
    n_top += rows
    ri = n_top
    n_top += rows
    bi = n_top
    n_top += cols
    fi = n_top
    n_top += cols

    all_v = np.vstack([top_v, left_skirt, right_skirt, back_skirt, front_skirt])

    for r in range(rows - 1):
        t0 = ti(r, 0)
        t1 = ti(r + 1, 0)
        s0 = li + r
        s1 = li + r + 1
        faces.extend([[t0, s0, t1], [s0, s1, t1]])

    for r in range(rows - 1):
        t0 = ti(r, cols - 1)
        t1 = ti(r + 1, cols - 1)
        s0 = ri + r
        s1 = ri + r + 1
        faces.extend([[t0, t1, s0], [s0, t1, s1]])

    for c in range(cols - 1):
        t0 = ti(0, c)
        t1 = ti(0, c + 1)
        s0 = bi + c
        s1 = bi + c + 1
        faces.extend([[t0, t1, s0], [s0, t1, s1]])

    for c in range(cols - 1):
        t0 = ti(rows - 1, c)
        t1 = ti(rows - 1, c + 1)
        s0 = fi + c
        s1 = fi + c + 1
        faces.extend([[t0, s0, t1], [s0, s1, t1]])

    bl_c = li + rows - 1
    br_c = ri + rows - 1
    tr_c = ri + 0
    tl_c = li + 0
    faces.extend([[bl_c, tr_c, br_c], [bl_c, tl_c, tr_c]])

    return all_v.astype(np.float32), np.array(faces, dtype=np.int32)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_city_3mf(
    buildings_geojson: dict,
    dem_values: list[float],
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
    north = bbox["north"]
    south = bbox["south"]
    east  = bbox["east"]
    west  = bbox["west"]
    lat_mid  = (north + south) / 2
    lon_range = (east - west) * math.cos(math.radians(lat_mid))
    lat_range = north - south
    aspect = lon_range / (lat_range or 1.0)
    W_mm  = 150.0
    H_mm  = W_mm / aspect if aspect > 0 else W_mm

    # -- Terrain mesh -----------------------------------------------------
    t_verts, t_faces = _terrain_mesh(dem_arr, W_mm, H_mm, base_mm, model_height_mm)

    # -- Cities 14: optional mesh simplification on terrain ---------------
    if simplify_terrain:
        try:
            from numpy2stl.processing.simplify import simplify_mesh_surfaces
            t_faces = simplify_mesh_surfaces(t_verts, t_faces)
            logger.info(f"Terrain mesh simplified to {len(t_faces)} faces")
        except Exception as e:
            logger.warning(f"Mesh simplification skipped: {e}")

    # -- Building meshes --------------------------------------------------
    b_verts, b_faces = _build_building_meshes(
        buildings_geojson, bbox, W_mm, H_mm, base_mm, model_height_mm,
        z_min, z_max, building_z_scale,
    )

    # -- Write 3MF to in-memory bytes -------------------------------------
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
