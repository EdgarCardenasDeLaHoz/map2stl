"""Building solids with pitched roofs, in model millimetres.

A pitched roof over a convex footprint is the lower envelope of one plane per eave
edge, each rising from its edge at a common slope: with every edge that is a
hipped roof (a pyramid on a square), with only the edges along the ridge it is a
gabled roof whose gable ends are vertical walls. The top surface is triangulated
with the plane intersections (ridges, hips) as constrained edges, so every facet
is exactly planar and every vertex is on the roof.

Concave footprints are split into convex pieces (Hertel-Mehlhorn), each roofed at
the building's slope; their union gives cross-gables and valleys. Domes, onions and
cones are surfaces of revolution over the footprint's largest inscribed circle.

OSM tags used: ``roof:shape``, ``roof:height``, ``roof:orientation`` (along|across
the long axis), ``roof:direction`` (compass bearing a skillion faces, downslope).
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from numpy2stl.core.extrude import close_surface, orient_ccw, prism
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import polylabel

Mesh = tuple[np.ndarray, np.ndarray]

HIPPED = {"hipped", "half-hipped", "mansard", "pyramidal", "side_hipped"}
GABLED = {"gabled", "gambrel", "saltbox", "sawtooth"}
REVOLVED = {"dome", "onion", "cone", "round"}
REVOLVE_SEGMENTS = 32


def _edges(poly: Polygon) -> tuple[np.ndarray, np.ndarray]:
    ring = np.asarray(orient(poly, 1.0).exterior.coords)
    return ring[:-1], ring[1:]


def _long_axis(poly: Polygon) -> np.ndarray:
    """Unit vector along the minimum rotated rectangle's long side."""
    c = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:3]
    a, b = c[1] - c[0], c[2] - c[1]
    v = a if np.linalg.norm(a) >= np.linalg.norm(b) else b
    return v / max(np.linalg.norm(v), 1e-12)


def _planes(p: Polygon, slope: float, ridge_axis: np.ndarray | None) -> np.ndarray:
    """(k, 3) planes z = a x + b y + c rising inward from the eave edges.

    ``ridge_axis`` None: every edge (hipped); otherwise only edges within 30 degrees
    of the axis (gabled; the others become vertical gable walls).
    """
    p0, p1 = _edges(p)
    d = p1 - p0
    length = np.linalg.norm(d, axis=1)
    keep = length > 1e-9
    if ridge_axis is not None:
        cos = np.abs(d @ ridge_axis) / np.maximum(length, 1e-12)
        keep &= cos > math.cos(math.radians(30))
    p0, d, length = p0[keep], d[keep], length[keep]
    n = np.column_stack([-d[:, 1], d[:, 0]]) / length[:, None]     # inward for a CCW ring
    return np.column_stack([slope * n, -slope * (n * p0).sum(1)])


def _envelope_top(p: Polygon, planes: np.ndarray, rise_mm: float) -> Mesh | None:
    """Triangulated top surface z = min(planes, rise) over convex ``p``, above z = 0."""
    import triangle

    x0, y0, x1, y1 = p.bounds
    big = box(x0 - 1, y0 - 1, x1 + 1, y1 + 1)
    pieces = []
    for i, (a, b, c) in enumerate(planes):
        region = p
        for j, (a2, b2, c2) in enumerate(planes):
            if i == j:
                continue
            # keep where plane i <= plane j: (a-a2) x + (b-b2) y + (c-c2) <= 0
            region = region.intersection(_halfplane(big, a - a2, b - b2, c - c2))
            if region.is_empty:
                break
        if not region.is_empty:
            pieces.append(region)
    if rise_mm < math.inf:
        # A flat top where every plane is above the ridge cap (wide hipped roofs).
        pieces.append(p)
    rings = [np.asarray(g.exterior.coords)[:-1] for r in pieces
             for g in shapely.get_parts(r) if g.geom_type == "Polygon" and g.area > 1e-9]
    if not rings:
        return None
    pts, segs, start = [], [], 0
    for r in rings:
        k = np.arange(start, start + len(r))
        pts.append(r)
        segs.append(np.column_stack([k, np.roll(k, -1)]))
        start += len(r)
    verts, inv = np.unique(np.round(np.concatenate(pts), 9), axis=0, return_inverse=True)
    seg = inv.ravel()[np.concatenate(segs)]
    seg = np.unique(np.sort(seg[seg[:, 0] != seg[:, 1]], axis=1), axis=0)
    out = triangle.triangulate({"vertices": np.ascontiguousarray(verts, np.float64),
                                "segments": np.ascontiguousarray(seg, np.int32)}, "pQ")
    v2, f = out.get("vertices"), out.get("triangles")
    if f is None or not len(f):
        return None
    inside = shapely.contains_xy(p.buffer(1e-7), *v2[f].mean(axis=1).T)
    f = f[inside]
    z = np.minimum((v2 @ planes[:, :2].T + planes[:, 2]).min(axis=1), rise_mm)
    top = np.column_stack([v2, np.maximum(z, 0.0)])
    return top, orient_ccw(top, f)


def _halfplane(big: Polygon, a: float, b: float, c: float) -> Polygon:
    """{a x + b y + c <= 0} clipped to ``big``."""
    if abs(a) < 1e-15 and abs(b) < 1e-15:
        return big if c <= 0 else Polygon()
    x0, y0, x1, y1 = big.bounds
    corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    s = corners @ np.array([a, b]) + c
    pts = []
    for k in range(4):
        p, q, sp, sq = corners[k], corners[(k + 1) % 4], s[k], s[(k + 1) % 4]
        if sp <= 0:
            pts.append(p)
        if (sp < 0) != (sq < 0) and sp != sq:
            pts.append(p + (q - p) * (sp / (sp - sq)))
    return Polygon(pts) if len(pts) >= 3 else Polygon()


def convex_pieces(poly: Polygon) -> list[Polygon]:
    """Hertel-Mehlhorn: triangulate, then drop diagonals while pieces stay convex."""
    poly = orient(poly, 1.0)
    if poly.area >= poly.convex_hull.area * (1 - 1e-9):
        return [poly]
    tris = list(shapely.get_parts(shapely.constrained_delaunay_triangles(poly)))
    pieces = [orient(t, 1.0) for t in tris if t.area > 1e-12]
    merged = True
    while merged and len(pieces) > 1:
        merged = False
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                if pieces[i].intersection(pieces[j]).length <= 1e-9:
                    continue
                u = shapely.union(pieces[i], pieces[j])
                if u.geom_type == "Polygon" and u.area >= u.convex_hull.area * (1 - 1e-9):
                    pieces[i] = orient(u.simplify(0), 1.0)
                    del pieces[j]
                    merged = True
                    break
            if merged:
                break
    return pieces


def _revolved(center: np.ndarray, radius: float, z0: float, rise: float, shape: str) -> Mesh:
    """Closed dome / onion / cone over a circle, base at z0."""
    n = REVOLVE_SEGMENTS
    t = np.linspace(0.0, 1.0, 9)[:-1]                          # profile samples, apex added
    if shape == "cone":
        rho, z = 1.0 - t, t
    elif shape == "onion":
        rho = np.where(t < 0.35, 1.0 + 0.25 * np.sin(t / 0.35 * np.pi), (1 - t) / 0.65 * 0.9)
        z = t
    else:                                                       # dome / round
        ang = t * np.pi / 2
        rho, z = np.cos(ang), np.sin(ang)
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rings = [np.column_stack([center[0] + radius * r * np.cos(th), center[1] + radius * r * np.sin(th),
                              np.full(n, z0 + rise * zz)]) for r, zz in zip(rho, z, strict=True)]
    v = np.vstack(rings + [[center[0], center[1], z0 + rise], [center[0], center[1], z0]])
    apex, base = len(v) - 2, len(v) - 1
    faces = []
    for k in range(len(rings) - 1):
        a, b = k * n, (k + 1) * n
        for i in range(n):
            j = (i + 1) % n
            faces += [[a + i, a + j, b + j], [a + i, b + j, b + i]]
    last = (len(rings) - 1) * n
    faces += [[last + i, last + (i + 1) % n, apex] for i in range(n)]
    faces += [[(i + 1) % n, i, base] for i in range(n)]
    return v, np.asarray(faces)


def building_solids(poly: Polygon, z_floor: float, z_eave: float, rise_mm: float,
                    shape: str, props: dict | None = None) -> list[Mesh]:
    """Solids for one building: walls from ``z_floor`` to ``z_eave`` and a roof rising
    ``rise_mm`` above the eave. Several solids may overlap (the caller unions them).

    Unknown or flat shapes, tiny rises, or footprints with courtyards get a flat top at
    ``z_eave + rise_mm / 2`` (the mean height of a pitched roof).
    """
    props = props or {}
    shape = (shape or "flat").lower().strip()
    flat_top = z_eave + (rise_mm / 2 if shape != "flat" else rise_mm)
    if shape == "flat" or rise_mm <= 1e-6 or poly.interiors:
        m = prism(poly, z_floor, flat_top)
        return [m] if m is not None else []

    if shape in REVOLVED:
        label = polylabel(poly, tolerance=0.01)
        radius = poly.exterior.distance(label)
        body = prism(poly, z_floor, z_eave + 1e-3)
        out = [body] if body is not None else []
        if radius > 0.05:
            out.append(_revolved(np.array(label.coords[0]), radius, z_eave, rise_mm, shape))
        return out

    axis = _long_axis(poly)
    if str(props.get("roof:orientation") or "").lower() == "across":
        axis = np.array([-axis[1], axis[0]])
    # The roof reaches its full rise over the widest part of the footprint: the
    # radius of the largest inscribed circle is half the width of a rectangle and
    # of each arm of an L, where the bounding rectangle would overstate an L.
    half_width = poly.exterior.distance(polylabel(poly, tolerance=0.01))
    slope = rise_mm / max(half_width, 1e-9)

    out: list[Mesh] = []
    for piece in convex_pieces(poly):
        if shape == "skillion":
            planes = _skillion_plane(piece, rise_mm, props.get("roof:direction"), axis)
            cap = rise_mm
        else:
            planes = _planes(piece, slope, None if shape in HIPPED else axis)
            cap = rise_mm
        if not len(planes):
            m = prism(piece, z_floor, flat_top)
        else:
            top = _envelope_top(piece, planes, cap)
            m = None
            if top is not None:
                v, f = top
                v = v.copy()
                v[:, 2] += z_eave
                m = close_surface(v, f, lambda xy: np.full(len(xy), z_floor))
        if m is None:
            m = prism(piece, z_floor, flat_top)
        if m is not None:
            out.append(m)
    return out


def _skillion_plane(p: Polygon, rise_mm: float, direction, axis: np.ndarray) -> np.ndarray:
    """One plane falling toward ``roof:direction`` (compass degrees), from +rise to 0."""
    try:
        deg = float(str(direction).split()[0])
        down = np.array([math.sin(math.radians(deg)), math.cos(math.radians(deg))])
    except (TypeError, ValueError, IndexError):
        down = np.array([-axis[1], axis[0]])
    xy = np.asarray(p.exterior.coords)
    proj = xy @ down
    span = max(proj.max() - proj.min(), 1e-9)
    s = rise_mm / span
    # z = s * (proj.max() - x.down)
    return np.array([[-s * down[0], -s * down[1], s * proj.max()]])
