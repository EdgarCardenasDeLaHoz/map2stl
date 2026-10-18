"""Vectorised polygon burn that reproduces GDAL's rasteriser cell for cell.

- ``burn_polygons_max`` fills many polygons into a stack of grids in a few numpy
  passes, with the same cells GDAL's ``GDALRasterizeGeometries`` (as called by
  ``rasterio.features.rasterize``, ``all_touched=False``) would fill.
- ``burn_segment_buffers_max`` does the same for round-capped buffers of
  two-point segments without building them in GEOS: it computes the vertices
  ``shapely.buffer(line, r, quad_segs=2)`` would, and leaves to the caller the
  few buffers whose fill could depend on the last bits of a vertex.

Both exist for the HydroRIVERS carve, where buffering a million tiny reaches in
GEOS and handing them to GDAL through GeoJSON cost more than the fill itself.
"""

from __future__ import annotations

import numpy as np

#: Pixel-space margin of the fragility test in ``burn_segment_buffers_max``. A
#: vertex computed here and the one GEOS computes differ by a few ulps of the
#: coordinate (about 1e-13 px at 4.7 km/px, 1e-10 px at 0.1 m/px), far inside it.
FRAGILE_TOL_PX = 1e-7


def _pixel_coords(x: np.ndarray, y: np.ndarray, west: float, north: float,
                  a: float, e: float) -> tuple[np.ndarray, np.ndarray]:
    """World to pixel coordinates, computed as GDAL does.

    GDAL inverts the north-up geotransform ``(west, a, 0, north, 0, e)``
    (``GDALInvGeoTransform``: ``-west / a, 1 / a, 0, -north / e, 0, 1 / e``) and
    applies it term by term. The same operations in the same order give the same
    doubles, so a vertex sitting exactly on a cell centre lands there in both.
    """
    inv0, inv1 = -west / a, 1.0 / a
    inv3, inv5 = -north / e, 1.0 / e
    px = inv0 + x * inv1 + y * 0.0
    py = inv3 + x * 0.0 + y * inv5
    return px, py


def burn_polygons_max(polys, values, layer, n_layers: int, shape: tuple[int, int],
                      bounds: tuple[float, float, float, float],
                      out: np.ndarray | None = None) -> np.ndarray:
    """Burn polygons into ``n_layers`` grids, keeping the largest value per cell.

    Returns float64 ``(n_layers, h, w)``: polygon ``i`` is burnt with ``values[i]``
    into ``out[layer[i]]``, the largest value wins where polygons overlap, and
    cells no polygon covers are 0. *bounds* is ``(west, south, east, north)`` of
    the grid, row 0 north. Each layer equals
    ``numpy2stl.raster.burn_polygons(polys, shape, bounds=bounds, values=values,
    mode="max")`` over that layer's polygons (rasterio, ``all_touched=False``).
    With *out* (a float64 ``(n_layers, h, w)`` stack) the polygons are max-burnt
    into it in place and it is returned as is: start it at ``-inf`` and zero the
    untouched cells after the last burn to combine several calls.

    The fill follows GDAL's scanline rule (``GDALdllImageFilledPolygon``):

    - a cell is filled when its centre is inside the polygon (even-odd over the
      exterior ring and holes), each row sampled at ``y + 0.5``;
    - an edge crosses row ``y`` when ``y1 <= y + 0.5 < y2`` (``y1 < y2``), at
      ``floor(x + 0.5)``; crossings are sorted and paired, and a pair
      ``(xa, xb)`` fills columns ``xa .. xb - 1``;
    - a horizontal edge lying exactly on a row centre is filled on its own when
      it is a bottom edge of its ring (the ring's inside lies above it).
      GDAL keys this on the edge's direction (``x1 > x2``) with the rings
      oriented one way, so the direction is taken after orienting each ring.

    MultiPolygons are burnt as their parts. Checked against rasterio on the
    Amazon's 1.2M HydroRIVERS buffers and on random polygons with vertices on
    cell centres (``tests/test_polygon_fill.py``).
    """
    import shapely

    h, w = int(shape[0]), int(shape[1])
    flat = _start(out, n_layers, h, w)
    polys = np.asarray(polys, dtype=object)
    if len(polys) and h > 0 and w > 0:
        parts, owner = shapely.get_parts(polys, return_index=True)
        keep = (shapely.get_type_id(parts) == 3) & ~shapely.is_empty(parts)
        parts, owner = parts[keep], owner[keep]
        if len(parts):
            rings, ring_part = shapely.get_rings(parts, return_index=True)
            xy, vert_ring = shapely.get_coordinates(rings, return_index=True)
            _fill(flat, xy, vert_ring, ring_part, owner, np.asarray(values, dtype=np.float64),
                  np.asarray(layer, dtype=np.int64), h, w, bounds)
    return _finish(flat, out, n_layers, h, w)


def _start(out, n_layers, h, w) -> np.ndarray:
    if out is None:
        return np.full(int(n_layers) * h * w, -np.inf)
    return np.asarray(out, dtype=np.float64).reshape(-1)


def _finish(flat, out, n_layers, h, w) -> np.ndarray:
    if out is not None:
        return out
    flat[flat == -np.inf] = 0.0
    return flat.reshape(int(n_layers), h, w)


def stadium_rings(p0: np.ndarray, p1: np.ndarray, radius: np.ndarray) -> np.ndarray:
    """Closed rings ``(n, 11, 2)`` of ``shapely.buffer(LineString([p0, p1]), radius, quad_segs=2)``.

    GEOS builds a line buffer with ``OffsetSegmentGenerator``: the left offset of
    the segment, a round cap of ``4`` steps of ``pi / 4`` around ``p1`` (its first
    point coincides with the offset end and is dropped as redundant), the right
    offset, the cap around ``p0`` from the reversed segment, closed on the first
    point. The same operations in the same order give the same coordinates, up to
    the last bit of a ``cos`` / ``sin`` (checked on the Amazon's 1.2M reaches).
    Only for segments much longer than ``1e-6 * radius``.
    """
    p0 = np.asarray(p0, dtype=np.float64)
    p1 = np.asarray(p1, dtype=np.float64)
    r = np.asarray(radius, dtype=np.float64)
    out = np.empty((len(p0), 11, 2))
    quantum = np.pi / 2.0 / 2                               # filletAngleQuantum, quad_segs=2
    for k, (a, b) in enumerate(((p0, p1), (p1, p0))):
        dx = b[:, 0] - a[:, 0]
        dy = b[:, 1] - a[:, 1]
        ln = np.sqrt(dx * dx + dy * dy)
        ux = r * dx / ln                                    # computeOffsetSegment, LEFT
        uy = r * dy / ln
        j = 5 * k
        out[:, j, 0] = b[:, 0] - uy                         # left offset at b
        out[:, j, 1] = b[:, 1] + ux
        ang = np.arctan2(dy, dx)
        start = ang + np.pi / 2
        total = np.abs(start - (ang - np.pi / 2))
        inc = total / np.floor(total / quantum + 0.5)       # 4 steps
        for i in (1, 2, 3):
            t = start + (-i) * inc
            out[:, j + i, 0] = b[:, 0] + r * np.cos(t)
            out[:, j + i, 1] = b[:, 1] + r * np.sin(t)
        out[:, j + 4, 0] = b[:, 0] - (-uy)                  # right offset at b
        out[:, j + 4, 1] = b[:, 1] + (-ux)
    out[:, 10] = out[:, 0]
    return out


def burn_segment_buffers_max(p0, p1, radius, values, layer, n_layers: int,
                             shape: tuple[int, int], bounds: tuple[float, float, float, float],
                             out: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Burn round-capped segment buffers as :func:`burn_polygons_max` burns their GEOS buffers.

    Returns ``(stack, fragile)``. Segment ``i`` (``p0[i]`` to ``p1[i]``, radius
    ``radius[i]``) is burnt as ``shapely.buffer(LineString([p0[i], p1[i]]),
    radius[i], quad_segs=2)`` (vertices from :func:`stadium_rings`) unless
    ``fragile[i]``. Those are not burnt, and the caller buffers and burns them
    itself: a segment shorter than ``1e-3 * radius``, a radius too small for the
    coordinates, or a buffer with a vertex within ``FRAGILE_TOL_PX`` of a row
    centre or a crossing within it of a column boundary, where the last bits of
    a vertex could move a cell. Elsewhere a few-ulp vertex difference cannot
    change a cell, so the stack equals burning the GEOS buffers. *out* works as in
    :func:`burn_polygons_max`, which is how the caller adds the fragile ones.
    """
    h, w = int(shape[0]), int(shape[1])
    flat = _start(out, n_layers, h, w)
    p0 = np.asarray(p0, dtype=np.float64).reshape(-1, 2)
    p1 = np.asarray(p1, dtype=np.float64).reshape(-1, 2)
    r = np.asarray(radius, dtype=np.float64)
    seglen = np.hypot(p1[:, 0] - p0[:, 0], p1[:, 1] - p0[:, 1])
    scale = np.maximum(np.abs(p0).max(axis=1), np.abs(p1).max(axis=1))
    fragile = ~((seglen >= 1e-3 * r) & (r * 1e-6 > 1e4 * np.finfo(float).eps * scale)
                & np.isfinite(seglen) & np.isfinite(r))
    good = np.flatnonzero(~fragile)
    if len(good) and h > 0 and w > 0:
        xy = stadium_rings(p0[good], p1[good], r[good]).reshape(-1, 2)
        vert_ring = np.repeat(np.arange(len(good)), 11)
        bad = _fill(flat, xy, vert_ring, np.arange(len(good)), good,
                    np.asarray(values, dtype=np.float64), np.asarray(layer, dtype=np.int64),
                    h, w, bounds, fragile_tol=FRAGILE_TOL_PX)
        fragile[good[bad]] = True
    return _finish(flat, out, n_layers, h, w), fragile


def _fill(flat, xy, vert_ring, ring_part, owner, values, layer, h, w, bounds,
          fragile_tol: float | None = None) -> np.ndarray:
    """Scanline-fill rings into *flat*; see :func:`burn_polygons_max` for the rule.

    *xy* are the closed rings' vertices, *vert_ring* the ring of each, *ring_part*
    the part of each ring, *owner* the input index of each part. With
    *fragile_tol*, parts whose fill could change if a vertex moved by a few ulps
    are not burnt; returns their mask (all False without it).
    """
    n_parts = len(owner)
    west, south, east, north = (float(v) for v in bounds)
    px, py = _pixel_coords(xy[:, 0], xy[:, 1], west, north, (east - west) / w, -(north - south) / h)
    fragile = np.zeros(n_parts, dtype=bool)
    if fragile_tol is not None:
        near = np.abs(py - (np.floor(py) + 0.5)) < fragile_tol
        fragile[ring_part[vert_ring[near]]] = True

    # Edges join consecutive vertices of a ring. Rings are closed, so the edge
    # from the last vertex back to the first is zero-length and fills nothing.
    i1 = np.flatnonzero(vert_ring[:-1] == vert_ring[1:])
    i2 = i1 + 1
    ering = vert_ring[i1]
    epart = ring_part[ering]
    x1, x2, y1, y2 = px[i1], px[i2], py[i1], py[i2]

    span_part, span_row, span_a, span_b = [], [], [], []

    # Horizontal edges on a row centre: bottom edges of their ring are filled.
    area2 = np.bincount(ering, weights=x1 * y2 - x2 * y1, minlength=int(vert_ring.max()) + 1)
    positive = area2[ering] > 0
    hz = np.flatnonzero((y1 == y2) & np.where(positive, x1 > x2, x1 < x2))
    if len(hz):
        row = np.floor(y1[hz]).astype(np.int64)
        on = (row + 0.5 == y1[hz]) & (row >= 0) & (row < h)
        hz, row = hz[on], row[on]
        span_part.append(epart[hz])
        span_row.append(row)
        span_a.append(np.floor(np.minimum(x1[hz], x2[hz]) + 0.5).astype(np.int64))
        span_b.append(np.floor(np.maximum(x1[hz], x2[hz]) + 0.5).astype(np.int64))

    # Every other edge, oriented upward (lo_y < hi_y), against every row centre
    # it may cross.
    nh = np.flatnonzero(y1 != y2)
    up = y1[nh] < y2[nh]
    lo_y = np.where(up, y1[nh], y2[nh])
    hi_y = np.where(up, y2[nh], y1[nh])
    lo_x = np.where(up, x1[nh], x2[nh])
    hi_x = np.where(up, x2[nh], x1[nh])
    ep = epart[nh]
    r0 = np.maximum(np.floor(lo_y - 0.5), 0).astype(np.int64)
    r1 = np.minimum(np.floor(hi_y - 0.5) + 1, h - 1).astype(np.int64)
    n = np.maximum(r1 - r0 + 1, 0)
    ei = np.repeat(np.arange(len(n)), n)
    row = r0[ei] + (np.arange(int(n.sum())) - (np.cumsum(n) - n)[ei])
    dy = row + 0.5
    cross = (dy < hi_y[ei]) & (dy >= lo_y[ei])
    ei, row, dy = ei[cross], row[cross], dy[cross]
    dy1, dx1 = lo_y[ei], lo_x[ei]
    slope = (hi_x[ei] - dx1) / (hi_y[ei] - dy1)
    xf = (dy - dy1) * (hi_x[ei] - dx1) / (hi_y[ei] - dy1) + dx1 + 0.5
    xi = np.floor(xf)
    if fragile_tol is not None:
        # A crossing next to a column boundary moves with its edge's ends, by up to
        # about (1 + |slope|) times their shift.
        edge_near = np.minimum(xf - xi, xi + 1 - xf) < fragile_tol * (4 + 4 * np.abs(slope))
        fragile[ep[ei[edge_near]]] = True
    # Clamping to [-1, w] keeps the order and every fill below, and lets
    # (part, row, x) pack into one sortable integer.
    xi = np.clip(xi, -1, w).astype(np.int64)
    key = np.sort((ep[ei] * h + row) * (w + 2) + (xi + 1)).astype(np.int64)
    xi = key % (w + 2) - 1
    grp = key // (w + 2)                                   # part * h + row
    # Pair crossings (0, 1), (2, 3), ... within each (part, row); an odd last one is dropped.
    start = np.r_[True, grp[1:] != grp[:-1]][:len(grp)]
    gid = np.cumsum(start) - 1
    pos = np.arange(len(key)) - np.flatnonzero(start)[gid]
    first = np.flatnonzero((pos % 2 == 0)[:-1] & (grp[1:] == grp[:-1]))
    span_part.append(grp[first] // h)
    span_row.append(grp[first] % h)
    span_a.append(xi[first])
    span_b.append(xi[first + 1])

    sp = np.concatenate(span_part)
    sr = np.concatenate(span_row)
    sa = np.concatenate(span_a)
    sb = np.concatenate(span_b)
    ok = (sa <= w - 1) & (sb > 0) & ~fragile[sp]
    sp, sr, sa, sb = sp[ok], sr[ok], np.maximum(sa[ok], 0), np.minimum(sb[ok] - 1, w - 1)
    ok = sa <= sb
    sp, sr, sa, sb = sp[ok], sr[ok], sa[ok], sb[ok]
    ln = sb - sa + 1
    si = np.repeat(np.arange(len(ln)), ln)
    col = sa[si] + (np.arange(int(ln.sum())) - (np.cumsum(ln) - ln)[si])
    who = owner[sp[si]]
    np.maximum.at(flat, layer[who] * (h * w) + sr[si] * w + col, values[who])
    return fragile
