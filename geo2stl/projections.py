"""
Map Projections for 3D Terrain Models

This module provides various map projections to transform geographic coordinates
(latitude/longitude) into 2D planar coordinates suitable for 3D printing or display.

Projections available:
- none: No projection, keep raw lat/lon grid (Plate Carrée / Equirectangular)
- cosine: Simple cosine latitude correction (original proj_map_geo_to_2D behavior)
- mercator: Web Mercator projection (conformal, preserves angles)
- equidistant: Equidistant Cylindrical (preserves distances along meridians)
- lambert: Lambert Cylindrical Equal-Area (preserves area)
- sinusoidal: Sinusoidal projection (equal-area, good for continents)
- laea: Lambert Azimuthal Equal-Area (spherical), centred on the bbox centre
- lcc: Lambert Conformal Conic (spherical), standard parallels at 1/6 and 5/6 of the
  latitude span; falls back to ``equidistant`` when the bbox straddles the equator

Size rule (user decision 2026-10-13): a requested ``dim`` is the longest side of
the grid *after* projection. :func:`source_dim` is the one place that turns ``dim``
into the plate-carree sampling size (large enough that projecting it never
upscales), :func:`projected_shape` is the exact output shape, and every
projection entry point takes ``target_dim=dim`` to land on that shape, so
independently projected layers come out identical in shape whatever their
source resolution.
"""

from typing import Literal

import cv2
import numpy as np
from scipy import ndimage

from geo2stl.geo import M_PER_DEG_LAT, M_PER_DEG_LON_EQ, m_per_deg_lon

ProjectionType = Literal['none', 'cosine', 'mercator',
                         'equidistant', 'lambert', 'sinusoidal',
                         'miller', 'gall', 'laea', 'lcc']

# Earth's radius in meters
EARTH_RADIUS = 6_371_000


def _clip_nan_borders(arr: np.ndarray, nan_mask: np.ndarray) -> np.ndarray:
    """Clip border rows/cols where all values are NaN according to *nan_mask*."""
    if arr.ndim == 2:
        col_has_data = ~np.all(nan_mask, axis=0)
        if col_has_data.any():
            arr = arr[:, col_has_data]
            nan_mask = nan_mask[:, col_has_data]
        row_has_data = ~np.all(nan_mask, axis=1)
        if row_has_data.any():
            arr = arr[row_has_data, :]
        return arr

    if arr.ndim == 3:
        col_has_data = ~np.all(nan_mask, axis=0)
        if col_has_data.any():
            arr = arr[:, col_has_data, :]
            nan_mask = nan_mask[:, col_has_data]
        row_has_data = ~np.all(nan_mask, axis=1)
        if row_has_data.any():
            arr = arr[row_has_data, :, :]
        return arr

    return arr


def wing_mask(grid: np.ndarray) -> np.ndarray:
    """Non-finite cells in connected regions that touch the grid border (the "wings").

    sinusoidal / laea / lcc return a full rectangle whose cells outside the bbox are
    NaN, curved and joined to the border. A NaN void wholly inside the grid (a real
    DEM hole) is not a wing. Connectivity is 4-neighbour.
    """
    bad = ~np.isfinite(np.asarray(grid))
    if not bad.any():
        return bad
    labels, n = ndimage.label(bad)
    edge = np.unique(np.concatenate(
        [labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    edge = edge[edge > 0]
    if edge.size == 0:
        return np.zeros_like(bad)
    return np.isin(labels, edge)


def _max_rect_hist(free: np.ndarray) -> tuple[int, int, int, int]:
    """Largest all-True rectangle ``(r0, r1, c0, c1)`` (half-open), histogram + stack, O(H*W)."""
    h, w = free.shape
    heights = [0] * w
    best, area = (0, 0, 0, 0), 0
    for r in range(h):
        row = free[r].tolist()
        heights = [hh + 1 if ok else 0 for hh, ok in zip(heights, row, strict=True)]
        stack: list[int] = []
        for c in range(w + 1):
            cur = heights[c] if c < w else 0
            while stack and heights[stack[-1]] >= cur:
                top = stack.pop()
                left = stack[-1] + 1 if stack else 0
                a = heights[top] * (c - left)
                if a > area:
                    area, best = a, (r + 1 - heights[top], r + 1, left, c)
            stack.append(c)
    return best


def largest_valid_rect(grid: np.ndarray, max_cells: int = 512) -> tuple[int, int, int, int]:
    """Largest axis-aligned rectangle ``(r0, r1, c0, c1)`` (half-open) free of wing cells.

    Wing cells are :func:`wing_mask`; a grid without any returns the full extent, so
    projections that fill their rectangle are unchanged. The search runs on a grid
    coarsened to at most ``max_cells`` per side (a block is a wing if any cell is), then
    the rectangle grows back one row/column at a time while the new line holds no wing
    cell, so the result is wing-free at full resolution and costs O(max_cells^2).
    """
    h, w = grid.shape[:2]
    wing = wing_mask(grid[..., 0] if grid.ndim == 3 else grid)
    if not wing.any():
        return 0, h, 0, w
    k = max(1, -(-max(h, w) // max_cells))
    if k > 1:
        ph, pw = -(-h // k) * k, -(-w // k) * k
        padded = np.ones((ph, pw), dtype=bool)   # padding counts as wing: stays outside
        padded[:h, :w] = wing
        coarse = padded.reshape(ph // k, k, pw // k, k).any(axis=(1, 3))
    else:
        coarse = wing
    r0, r1, c0, c1 = _max_rect_hist(~coarse)
    r0, r1, c0, c1 = r0 * k, min(r1 * k, h), c0 * k, min(c1 * k, w)
    if r1 <= r0 or c1 <= c0:
        return 0, h, 0, w    # all wing: leave the grid to the caller's own fallback
    if k > 1:
        grew = True
        while grew:
            grew = False
            if r0 > 0 and not wing[r0 - 1, c0:c1].any():
                r0 -= 1
                grew = True
            if r1 < h and not wing[r1, c0:c1].any():
                r1 += 1
                grew = True
            if c0 > 0 and not wing[r0:r1, c0 - 1].any():
                c0 -= 1
                grew = True
            if c1 < w and not wing[r0:r1, c1].any():
                c1 += 1
                grew = True
    return r0, r1, c0, c1


def get_projection_info() -> dict:
    """Return information about available projections."""
    return {
        'none': {
            'name': 'None (Plate Carrée)',
            'description': 'No projection applied. Raw lat/lon grid. Fast but distorted at high latitudes.',
            'preserves': 'Nothing specific',
            'best_for': 'Equatorial regions, quick previews'
        },
        'cosine': {
            'name': 'Cosine Correction',
            'description': 'Simple horizontal scaling by cos(latitude). Reduces width distortion.',
            'preserves': 'Approximate local scale',
            'best_for': 'General purpose, moderate latitudes'
        },
        'mercator': {
            'name': 'Web Mercator',
            'description': 'Conformal cylindrical projection. Preserves shapes locally.',
            'preserves': 'Angles and local shapes',
            'best_for': 'Navigation, web maps, any latitude except poles'
        },
        'equidistant': {
            'name': 'Equidistant Cylindrical',
            'description': 'Simple projection preserving distances along meridians.',
            'preserves': 'Distances along meridians',
            'best_for': 'Measuring north-south distances'
        },
        'lambert': {
            'name': 'Lambert Equal-Area',
            'description': 'Cylindrical equal-area projection. Preserves relative areas.',
            'preserves': 'Area',
            'best_for': 'Comparing region sizes, thematic maps'
        },
        'sinusoidal': {
            'name': 'Sinusoidal',
            'description': 'Pseudocylindrical equal-area. Good for single continents.',
            'preserves': 'Area',
            'best_for': 'Continental maps, Africa, South America'
        },
        'miller': {
            'name': 'Miller Cylindrical',
            'description': 'Compromise cylindrical. Mercator-like shapes with less polar exaggeration.',
            'preserves': 'Compromise (no property exactly)',
            'best_for': 'World/whole-region maps wanting a balanced look'
        },
        'gall': {
            'name': 'Gall Stereographic',
            'description': 'Compromise cylindrical. Moderate shape and area distortion.',
            'preserves': 'Compromise (no property exactly)',
            'best_for': 'General world maps, balanced distortion'
        },
        'laea': {
            'name': 'Lambert Azimuthal Equal-Area',
            'description': 'Azimuthal equal-area, centred on the region. No shear: meridians '
                           'lean only gradually away from the centre.',
            'preserves': 'Area',
            'best_for': 'Continents and large regions (the EU standard, EPSG:3035)'
        },
        'lcc': {
            'name': 'Lambert Conformal Conic',
            'description': 'Conic conformal, standard parallels at 1/6 and 5/6 of the latitude '
                           'span. Preserves local shapes. Falls back to Equidistant Cylindrical '
                           'when the region straddles the equator.',
            'preserves': 'Angles and local shapes',
            'best_for': 'Mid-latitude regions wider than tall'
        }
    }


def expected_aspect_ratio(bbox: tuple[float, float, float, float],
                          projection: ProjectionType,
                          input_shape: tuple[int, int] | None = None) -> float:
    """Return the width/height aspect ratio a *true* (non-dimension-preserving)
    projection of this bbox should produce.

    This is the single source of truth for "what shape should this
    projection produce" — both the per-projection ``False``-branch sizing
    code and :func:`verify_layer_alignment` (and tests) derive from the same
    formula here, so there is no way for the two to drift apart.

    ``input_shape`` (rows, cols) is required for ``'none'`` and ``'cosine'``:
    unlike the other projections (which resample onto a canonical
    lat/lon-independent frame, so their output aspect ratio depends only on
    the bbox), 'none' is the identity transform and 'cosine' scales relative
    to whatever pixel grid it's handed — their output aspect ratio depends
    on the *input's* pixel aspect ratio, not just the bbox. When omitted,
    assumes the input has the "raw equal-angle" pixel aspect ratio that the
    live DEM-fetch pipeline always produces (degrees-per-pixel equal in lat
    and lon — see geo2stl/dem.py's make_dem_image, which sizes `dim` off
    max(h,w) from equal-angle SRTM tiles).

    Note: this is an ASPECT RATIO (unitless), not a pixel count. Two layers
    fetched at different resolutions (e.g. DEM at 600px, water mask at
    300px) are correctly aligned as long as their aspect ratios match —
    matching pixel counts is not required and not checked here.
    """
    north, south, east, west = bbox
    if projection == 'none':
        # Identity transform: output aspect ratio == input pixel aspect ratio.
        if input_shape is not None:
            m, n = input_shape
            return n / m if m else 1.0
        return (east - west) / (north - south) if (north - south) else 1.0
    if projection == 'cosine':
        # Narrows input pixel width by cos(mid_lat) relative to the input's
        # OWN aspect ratio (see _project_cosine) — not a fixed bbox-derived
        # target the way mercator/lambert/equidistant are.
        cos_mid = np.cos(np.radians((north + south) / 2))
        if input_shape is not None:
            m, n = input_shape
            in_ratio = n / m if m else 1.0
        else:
            in_ratio = (east - west) / (north - south) if (north - south) else 1.0
        return in_ratio * cos_mid
    if projection == 'equidistant':
        cos_std = np.cos(np.radians((north + south) / 2))
        lon_range = (east - west) * cos_std
        lat_range = north - south
        return lon_range / lat_range if lat_range else 1.0
    if projection == 'mercator':
        max_lat = 85.0
        n_c, s_c = min(north, max_lat), max(south, -max_lat)
        def y(lat):
            return np.log(np.tan(np.pi / 4 + np.radians(np.clip(lat, -max_lat, max_lat)) / 2))
        merc_height = abs(y(n_c) - y(s_c))
        merc_width = np.radians(east - west)
        return merc_width / merc_height if merc_height else 1.0
    if projection == 'lambert':
        def y(lat):
            return np.sin(np.radians(lat))
        lam_height = abs(y(north) - y(south))
        lam_width = np.radians(east - west)
        return lam_width / lam_height if lam_height else 1.0
    if projection == 'sinusoidal':
        widest_cos = max(abs(np.cos(np.radians(north))), abs(np.cos(np.radians(south))))
        lat_height = np.radians(north - south) or 1.0
        proj_width = np.radians(east - west) * widest_cos
        return proj_width / lat_height
    if projection in ('miller', 'gall'):
        y_of_lat = _miller_y if projection == 'miller' else _gall_y
        y_n = float(y_of_lat(np.array([north]))[0])
        y_s = float(y_of_lat(np.array([south]))[0])
        proj_height = abs(y_n - y_s)
        proj_width = np.radians(east - west) * _X_SCALE[projection]
        return proj_width / proj_height if proj_height else 1.0
    if projection in _CONIC_AZIMUTHAL:
        if projection == 'lcc' and lcc_parameters(bbox) is None:
            return expected_aspect_ratio(bbox, 'equidistant', input_shape)
        x0, x1, y0, y1 = _conic_azimuthal_model(tuple(bbox), projection).extents
        return (x1 - x0) / (y1 - y0) if y1 > y0 else 1.0
    raise ValueError(f"Unknown projection: {projection}")


def projected_shape(bbox: tuple[float, float, float, float],
                    projection: ProjectionType,
                    dim: int,
                    maintain_dimensions: bool = False) -> tuple[int, int]:
    """Return the ``(rows, cols)`` of a projected grid whose longest side is *dim*.

    The aspect ratio is the projection's true one for this bbox
    (:func:`expected_aspect_ratio` for an equal-angle input), or the bbox's
    plate-carree ratio for ``'none'`` and for ``maintain_dimensions=True``
    (which keeps the input's shape). ``project_coordinates(..., target_dim=dim)``
    produces exactly this shape, so every layer projected with the same
    ``(bbox, projection, dim, maintain_dimensions)`` matches pixel for pixel.
    """
    dim = max(1, int(dim))
    aspect = float(expected_aspect_ratio(bbox, 'none' if maintain_dimensions else projection))
    if not np.isfinite(aspect) or aspect <= 0:
        aspect = 1.0
    if aspect >= 1.0:
        return max(1, int(round(dim / aspect))), dim
    return dim, max(1, int(round(dim * aspect)))


def source_dim(dim: int,
               bbox: tuple[float, float, float, float],
               projection: ProjectionType,
               maintain_dimensions: bool = False) -> int:
    """Longest side of the plate-carree (equal-angle) grid to sample for a
    requested projected size *dim*.

    The single source of truth for "how large to fetch or rasterise before
    projecting" (user decision 2026-10-13: ``dim`` is the longest side *after*
    projection). The source grid is at least as large as
    :func:`projected_shape` on both axes, so the projection only ever
    downsamples: source rows ``s >= out_rows`` and cols ``s * R >= out_cols``
    (``R`` = lon span / lat span). ``'none'`` and ``maintain_dimensions=True``
    keep the shape, so the answer is *dim*. San Francisco (37.66-37.82 N,
    cosine, dim 600): 758, which projects to 600 x 541.
    """
    dim = max(1, int(dim))
    if maintain_dimensions or projection == 'none':
        return dim
    raw = float(expected_aspect_ratio(bbox, 'none'))
    if not np.isfinite(raw) or raw <= 0:
        return dim
    rows, cols = projected_shape(bbox, projection, dim)
    s = max(rows, cols / raw)                      # source rows
    return max(1, int(np.ceil(max(s, s * raw) - 1e-9)))


def source_shape(dim: int,
                 bbox: tuple[float, float, float, float],
                 projection: ProjectionType,
                 maintain_dimensions: bool = False) -> tuple[int, int]:
    """``(rows, cols)`` of the equal-angle grid whose longest side is
    :func:`source_dim`, for rasterisers that take an explicit shape."""
    return projected_shape(bbox, 'none',
                           source_dim(dim, bbox, projection, maintain_dimensions))


#: The shared source grid has at most (this x *dim*)² cells and a longest side of
#: at most :data:`SHARED_SOURCE_MAX_SIDE` x *dim*: a near-polar Mercator box would
#: otherwise ask for a huge grid. A projection that needs more samples than that is
#: served from its own grid (:func:`shared_covers`). A cell budget rather than a
#: side cap: Norway (dim 600, 57-72 N) needs 596 x 1438 for Mercator, 0.86M cells,
#: which a 1200 px side cap refused, so cosine and Mercator left the shared grid.
SHARED_SOURCE_MAX_FACTOR = 2.0
SHARED_SOURCE_MAX_SIDE = 3.0


def shared_source_dim(dim: int,
                      bbox: tuple[float, float, float, float],
                      maintain_dimensions: bool = False) -> int:
    """Longest side of one equal-angle grid fine enough for every projection.

    The largest :func:`source_dim` over all projections, capped to
    (:data:`SHARED_SOURCE_MAX_FACTOR` x *dim*)² cells and a longest side of
    :data:`SHARED_SOURCE_MAX_SIDE` x *dim*. Work done on this grid (the base
    DEM read, the river carve) does not depend on the projection, so switching
    projection only resamples it down to the projection's :func:`source_shape`.
    Europa (dim 600): 936, Mercator's grid.
    """
    dim = max(1, int(dim))
    if maintain_dimensions:
        return dim
    best = min(max(source_dim(dim, bbox, p) for p in get_projection_info()),
               int(np.ceil(SHARED_SOURCE_MAX_SIDE * dim)))
    rows, cols = projected_shape(bbox, 'none', best)
    budget = (SHARED_SOURCE_MAX_FACTOR * dim) ** 2
    if rows * cols > budget:
        best = int(best * np.sqrt(budget / (rows * cols)))
    return max(dim, best)


def shared_source_shape(dim: int,
                        bbox: tuple[float, float, float, float],
                        maintain_dimensions: bool = False) -> tuple[int, int]:
    """``(rows, cols)`` of the :func:`shared_source_dim` grid."""
    return projected_shape(bbox, 'none', shared_source_dim(dim, bbox, maintain_dimensions))


def shared_covers(shared: tuple[int, int], shape: tuple[int, int]) -> bool:
    """Whether a grid of *shared* shape can be pooled down to *shape* (both axes)."""
    return int(shared[0]) >= int(shape[0]) and int(shared[1]) >= int(shape[1])


def fit_grid(arr: np.ndarray, shape: tuple[int, int], categorical: bool = False) -> np.ndarray:
    """Resample a 2-D (or H x W x C) grid to ``shape`` = (rows, cols): area
    averaging when shrinking, bilinear when growing, nearest for categorical."""
    rows, cols = int(shape[0]), int(shape[1])
    if arr.shape[:2] == (rows, cols):
        return arr
    if categorical:
        interp = cv2.INTER_NEAREST
    elif rows <= arr.shape[0] and cols <= arr.shape[1]:
        interp = cv2.INTER_AREA
    else:
        interp = cv2.INTER_LINEAR
    src = arr if arr.dtype in (np.float32, np.uint8) else arr.astype(np.float32)
    out = cv2.resize(src, (cols, rows), interpolation=interp)
    return out.astype(arr.dtype, copy=False) if arr.dtype != out.dtype else out


def verify_layer_alignment(shapes: dict, bbox: tuple[float, float, float, float],
                           projection: ProjectionType,
                           maintain_dimensions: bool,
                           rtol: float = 0.02) -> None:
    """Raise AssertionError if any layer's aspect ratio doesn't match the
    others.

    ``shapes`` maps a layer name to its ``(rows, cols)`` output shape.
    Resolution (pixel count) may legitimately differ between layers — only
    the aspect ratio (cols/rows) is required to match, within ``rtol``
    relative tolerance (default 2%, to absorb integer-rounding of pixel
    counts at different resolutions).

    This always does a cross-layer comparison (every layer's ratio must
    match every other layer's ratio) — the correctness guarantee that
    actually matters for the 3D output. It additionally checks against
    :func:`expected_aspect_ratio` for projections where that target is an
    exact match regardless of input shape or clip behaviour (mercator/
    lambert/equidistant/miller/gall). Skipped for the absolute check:
    'none'/'cosine' (their true aspect ratio depends on each layer's own
    input pixel shape — see expected_aspect_ratio's docstring) and
    'sinusoidal', 'laea' and 'lcc' (clip_nans trims their non-rectangular
    outlines to a bounding box that expected_aspect_ratio does not model
    exactly). The cross-layer comparison alone is sufficient and correct for
    all of them.

    Intended as a cheap runtime/test guard, not a hot-path check — call it
    where multiple layers for the same bbox/projection are assembled (e.g.
    the composite pipeline, or a debug/test harness), not per-request on
    every single layer fetch.
    """
    if not shapes:
        return

    ratios = {name: (cols / rows if rows else float('nan'))
              for name, (rows, cols) in shapes.items()}

    names = list(ratios)
    ref_ratio = ratios[names[0]]
    for name in names[1:]:
        if not np.isclose(ratios[name], ref_ratio, rtol=rtol):
            raise AssertionError(
                f"Layer aspect ratio mismatch: '{names[0]}'={ref_ratio:.4f} "
                f"vs '{name}'={ratios[name]:.4f} for projection={projection!r} "
                f"bbox={bbox} (shapes={shapes})"
            )

    if not maintain_dimensions and projection not in ('none', 'cosine', 'sinusoidal', 'laea', 'lcc'):
        expected = expected_aspect_ratio(bbox, projection)
        for name, ratio in ratios.items():
            if not np.isclose(ratio, expected, rtol=rtol):
                raise AssertionError(
                    f"Layer '{name}' aspect ratio {ratio:.4f} does not match "
                    f"expected {expected:.4f} for projection={projection!r} "
                    f"bbox={bbox} (shape={shapes[name]})"
                )


def project_coordinates(
    mat: np.ndarray,
    bbox: tuple[float, float, float, float],
    projection: ProjectionType = 'cosine',
    maintain_dimensions: bool = True,
    fill_value: float = np.nan,
    clip_nans: bool = False,
    order: int = 1,
    target_dim: int | None = None,
) -> tuple[np.ndarray, dict]:
    """
    Project a geographic raster to a 2D planar coordinate system.

    Parameters
    ----------
    mat : np.ndarray
        Input 2D array (elevation/data values)
    bbox : tuple
        Bounding box as (north, south, east, west) in degrees
    projection : str
        Projection type: 'none', 'cosine', 'mercator', 'equidistant', 'lambert',
        'sinusoidal', 'miller', 'gall', 'laea', 'lcc'
    maintain_dimensions : bool
        If True, output has same dimensions as input (uses interpolation).
        If False, dimensions may change based on projection.
    fill_value : float
        Value to use for areas outside the valid projection domain
    clip_nans : bool
        If True, strip leading/trailing columns and rows that are entirely NaN
        after projection (removes the padding added by cosine/mercator projections).
    order : int
        Interpolation order: 0 = nearest-neighbour (best for categorical /
        class-label data — preserves integer IDs), 1 = bilinear (default,
        best for continuous data like elevation).
    target_dim : int, optional
        Longest side of the output (user decision 2026-10-13: ``dim`` is the
        size after projection). The output is exactly
        :func:`projected_shape` ``(bbox, projection, target_dim,
        maintain_dimensions)`` whatever the input resolution, so layers
        sampled at different sizes land on one grid. Shape-preserving modes
        ('none', ``maintain_dimensions=True``) resample the input to that shape
        first. None keeps the legacy rule (output rows = input rows).

    Returns
    -------
    tuple
        (projected_array, metadata_dict)
        metadata contains projection info and scale factors
    """
    north, south, east, west = bbox
    out_shape = None
    if target_dim:
        out_shape = projected_shape(bbox, projection, target_dim, maintain_dimensions)
        if maintain_dimensions or projection == 'none':
            # The shape-preserving paths keep the input's shape: give them that shape.
            mat = fit_grid(mat, out_shape, categorical=(order == 0))
            out_shape = None
    m, n = mat.shape  # rows (lat), cols (lon)

    # Center latitude for reference
    center_lat = (north + south) / 2
    center_lon = (east + west) / 2

    metadata = {
        'projection': projection,
        'input_shape': (m, n),
        'bbox': bbox,
        'center_lat': center_lat,
        'center_lon': center_lon
    }

    if projection == 'none':
        # No transformation - just return a copy
        metadata['output_shape'] = (m, n)
        metadata['scale_x_m_per_px'] = (
            east - west) * m_per_deg_lon(center_lat) / n
        metadata['scale_y_m_per_px'] = (north - south) * M_PER_DEG_LAT / m
        return mat.copy(), metadata

    elif projection == 'cosine':
        result, metadata = _project_cosine(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order,
            out_shape=out_shape)

    elif projection == 'mercator':
        result, metadata = _project_mercator(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order,
            out_shape=out_shape)

    elif projection == 'equidistant':
        result, metadata = _project_equidistant(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order,
            out_shape=out_shape)

    elif projection == 'lambert':
        result, metadata = _project_lambert(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order,
            out_shape=out_shape)

    elif projection == 'sinusoidal':
        result, metadata = _project_sinusoidal(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order,
            out_shape=out_shape)

    elif projection in _CONIC_AZIMUTHAL:
        result, metadata = _project_conic_azimuthal(
            mat, bbox, projection, maintain_dimensions, fill_value, metadata,
            order=order, out_shape=out_shape)

    elif projection == 'miller':
        result, metadata = _project_cylindrical_y(
            mat, bbox, maintain_dimensions, fill_value, metadata,
            _miller_y, order=order, out_shape=out_shape)

    elif projection == 'gall':
        result, metadata = _project_cylindrical_y(
            mat, bbox, maintain_dimensions, fill_value, metadata,
            _gall_y, order=order, x_scale=_X_SCALE['gall'], out_shape=out_shape)

    else:
        raise ValueError(f"Unknown projection: {projection}")

    # clip_nans trims all-NaN border rows/cols — but that inherently changes
    # the shape, which would silently violate maintain_dimensions=True's
    # documented contract ("output has same dimensions as input", full stop).
    # A lat-warping projection (mercator/lambert/equidistant/sinusoidal/
    # miller/gall) can introduce genuine edge NaNs even when out_m==m, so
    # without this guard maintain_dimensions=True + clip_nans=True could
    # silently return a shape 1+ px smaller than the input (found via
    # F-PROJ-DIMS cross-projection Playwright verification: mercator
    # returned (128,200) instead of the input's (129,200) with both flags
    # on). maintain_dimensions=True wins — clip_nans is skipped in that mode.
    if clip_nans and not maintain_dimensions and result.ndim == 2 and out_shape is None:
        # Strip columns where every value is NaN
        col_has_data = ~np.all(np.isnan(result), axis=0)
        if col_has_data.any():
            result = result[:, col_has_data]
        # Strip rows where every value is NaN
        row_has_data = ~np.all(np.isnan(result), axis=1)
        if row_has_data.any():
            result = result[row_has_data, :]
        metadata['output_shape'] = result.shape

    return result, metadata


def _project_cosine(
    mat: np.ndarray,
    bbox: tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
    out_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, dict]:
    """
    Original cosine latitude correction.
    Squishes horizontal pixels by cos(lat) to approximate local scale.
    """
    north, south, east, west = bbox
    m, n = mat.shape

    # Create coordinate grids
    lat_values = np.linspace(north, south, m)
    cos_lat = np.cos(np.radians(lat_values))

    # Average cosine factor
    avg_cos = np.mean(cos_lat)
    metadata['cos_factor'] = float(avg_cos)

    if maintain_dimensions:
        # Resample to maintain dimensions
        # Each row needs different horizontal scaling
        result = np.full((m, n), fill_value, dtype=np.float64)

        for i, c in enumerate(cos_lat):
            row = mat[i, :]
            # Scale factor relative to center
            scale = c / avg_cos

            if scale < 0.01:  # Near poles
                continue

            # Number of valid pixels in this row
            new_width = int(n * scale)
            if new_width < 1:
                continue

            # Resample the row
            x_old = np.linspace(0, 1, n)
            x_new = np.linspace(0, 1, new_width)
            if order == 0:
                # Nearest-neighbour: pick closest source pixel
                indices = np.clip(
                    np.round(x_new * (n - 1)).astype(int), 0, n - 1)
                row_resampled = row[indices]
            else:
                row_resampled = np.interp(x_new, x_old, row)

            # Center the resampled row
            start = (n - new_width) // 2
            end = start + new_width
            if start >= 0 and end <= n:
                result[i, start:end] = row_resampled
            else:
                # Handle edge cases
                src_start = max(0, -start)
                src_end = min(new_width, n - start)
                dst_start = max(0, start)
                dst_end = min(n, end)
                if src_end > src_start and dst_end > dst_start:
                    result[i, dst_start:dst_end] = row_resampled[src_start:src_end]

        metadata['output_shape'] = (m, n)
        # Scale in meters per pixel (approximate at center)
        metadata['scale_x_m_per_px'] = (east - west) * M_PER_DEG_LON_EQ * avg_cos / n
        metadata['scale_y_m_per_px'] = (north - south) * M_PER_DEG_LAT / m

        return result, metadata

    else:
        # True-aspect-ratio output: cosine correction narrows the row width
        # by avg_cos relative to the input, so the output is proportionally
        # narrower — deterministic from (m, n, bbox) alone, matching the
        # pattern used by _project_mercator/_project_lambert/_project_equidistant
        # so independently-projected layers from the same bbox/dim/projection
        # land on identical output shapes (the cross-layer alignment guarantee).
        out_m = m
        out_n = max(1, int(round(n * avg_cos)))
        if out_shape:
            out_m, out_n = out_shape
        interp = cv2.INTER_NEAREST if order == 0 else cv2.INTER_LINEAR
        result = cv2.resize(mat.astype(np.float32), (out_n, out_m),
                            interpolation=interp)

        metadata['output_shape'] = (out_m, out_n)
        metadata['scale_x_m_per_px'] = (east - west) * M_PER_DEG_LON_EQ * avg_cos / out_n
        metadata['scale_y_m_per_px'] = (north - south) * M_PER_DEG_LAT / out_m

        return result.astype(np.float64), metadata


# ---------------------------------------------------------------------------
# Inverse sampling (shared by mercator, lambert, sinusoidal, miller, gall)
# ---------------------------------------------------------------------------

def _sample_axis(x: np.ndarray, n: int, order: int):
    """Neighbour indices, weights and in-range flags along one axis of length *n*
    (see :func:`_sample_grid`). Returns ``(i0, i1, t, valid)``; ``i1`` and ``t``
    are None for order 0."""
    x = np.asarray(x, dtype=np.float64)
    if x.size and x.min() >= 0 and x.max() <= n - 1:   # the usual case (NaN fails both)
        valid, xc = None, x
    else:
        valid = (x >= 0) & (x <= n - 1)
        xc = np.where(valid, x, 0.0)                     # out-of-range: filled later
    if order == 0:
        return np.floor(xc + 0.5).astype(np.intp), None, None, valid
    # Cell (floor(x), floor(x) + 1); at x == n - 1 it is (n - 2, n - 1) with t = 1,
    # like scipy's mirrored edge (so a NaN in row n - 2 reaches row n - 1, as there).
    f = np.floor(xc)
    np.minimum(f, max(n - 2, 0), out=f)
    i0 = f.astype(np.intp)
    i1 = i0 + 1 if n > 1 else i0
    f -= xc
    np.negative(f, out=f)                                # t = x - i0, in [0, 1]
    return i0, i1, f.astype(np.float32), valid


def _take_lerp(a: np.ndarray, i0: np.ndarray, i1, t, axis: int) -> np.ndarray:
    """Gather ``a`` at ``i0`` along *axis* (order 0), or lerp between ``i0`` and
    ``i1`` with weight ``t`` (order 1). A 2-D index on axis 1 is per row."""
    def take(idx):
        if idx.ndim == 2:
            return np.take_along_axis(a, idx, axis=axis)
        return np.take(a, idx, axis=axis)
    lo = take(i0)
    if i1 is None:
        return lo
    hi = take(i1)
    hi -= lo          # NaN in either neighbour gives NaN, even at zero weight
    hi *= t
    hi += lo
    return hi


def _sample_grid(
    mat: np.ndarray,
    rows: np.ndarray,
    cols: np.ndarray,
    order: int,
    fill_value: float,
) -> np.ndarray:
    """Sample ``mat`` at row ``rows[i]`` and column ``cols[j]`` (1-D) or
    ``cols[i, j]`` (2-D); returns a float64 ``(len(rows), n_cols)`` grid.

    Drop-in for ``ndimage.map_coordinates(mat, [row_grid, col_grid], order=order,
    mode='constant', cval=fill_value)`` wherever the row coordinate depends only on
    the output row, which holds for every projection here. That lets each axis be
    interpolated in turn (two gathers and a lerp per axis, float32), with no float64
    copy of the input and no meshgrid: ~4x faster at dim 2000 (F-SPEED #8).

    Matches map_coordinates (tests/test_projections_remap.py):

    - order 0 takes index ``floor(x + 0.5)`` (ties round up): values are exact.
    - order 1 is bilinear over the cell ``(floor(x), floor(x) + 1)``. A NaN among the
      four neighbours gives NaN even at zero weight, as in scipy, so NaN masks are
      identical. Values differ by float32 rounding only (about 1 ulp of the value).
    - a coordinate outside ``[0, len - 1]`` (or NaN) gives ``fill_value``.

    Not cv2.remap: it quantises sub-pixel positions to 1/32 px for INTER_LINEAR,
    which shifted a steep synthetic DEM by up to 0.5 m against map_coordinates.
    """
    m, n = mat.shape
    i0, i1, tr, vr = _sample_axis(rows, m, order)
    j0, j1, tc, vc = _sample_axis(cols, n, order)
    src = mat if order == 0 else np.asarray(mat, dtype=np.float32)
    tmp = _take_lerp(src, i0, i1, None if tr is None else tr[:, None], axis=0)
    out = _take_lerp(tmp, j0, j1, tc, axis=1).astype(np.float64, copy=False)
    if vr is not None:
        out[~vr] = fill_value
    if vc is not None:
        out[np.broadcast_to(~vc, out.shape)] = fill_value
    return out


# ---------------------------------------------------------------------------
# Generic cylindrical projection
# ---------------------------------------------------------------------------
# A cylindrical projection maps longitude linearly to x and latitude to y via a
# monotonic function y = f(lat) (degrees in, unitless out). Any such projection
# (Mercator, Lambert, Miller, Gall, ...) is a one-liner given f. We inverse-sample
# on a regular y grid so maintain_dimensions holds and the result stays aligned
# with all other layers. Sampling goes through _sample_grid.

def _miller_y(lat_deg):
    """Miller Cylindrical: y = 1.25 * ln(tan(pi/4 + 0.4*phi)). Compromise."""
    phi = np.radians(np.clip(lat_deg, -89.9, 89.9))
    return 1.25 * np.log(np.tan(np.pi / 4 + 0.4 * phi))


# x = scale * lambda for the cylindrical projections sharing _project_cylindrical_y.
# Gall stereographic is x = lambda / sqrt(2) (projecting onto a cylinder secant at 45°);
# until 2026-10-02 it used Miller's x = lambda and came out sqrt(2) too wide.
_X_SCALE = {'miller': 1.0, 'gall': 1.0 / np.sqrt(2)}


def _gall_y(lat_deg):
    """Gall Stereographic: y = (1 + sqrt(2)/2) * tan(phi/2). Compromise."""
    phi = np.radians(np.clip(lat_deg, -89.9, 89.9))
    return (1 + np.sqrt(2) / 2) * np.tan(phi / 2)


def _project_cylindrical_y(
    mat: np.ndarray,
    bbox: tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    y_of_lat,
    order: int = 1,
    x_scale: float = 1.0,
    out_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, dict]:
    """Project a raster with a cylindrical y = y_of_lat(lat), x = x_scale * lon transform.

    Longitude is linear in x; latitude is warped in y by ``y_of_lat`` (a
    numpy-vectorized function taking degrees). Output keeps the input shape
    when maintain_dimensions is True; otherwise output width is derived from
    the true aspect ratio in projected space (deterministic from (m, n,
    bbox), matching _project_mercator/_project_lambert so independently
    projected layers from the same bbox/dim/projection stay aligned).
    """
    north, south, east, west = bbox
    m, n = mat.shape

    # y range in projected space, sampled uniformly for the output rows.
    y_north = float(y_of_lat(np.array([north]))[0])
    y_south = float(y_of_lat(np.array([south]))[0])

    if maintain_dimensions:
        out_m, out_n = m, n
    else:
        proj_height = abs(y_north - y_south)
        proj_width = np.radians(east - west) * x_scale
        aspect = proj_width / proj_height if proj_height else 1.0
        out_m = m
        out_n = max(1, int(round(m * aspect)))
        if out_shape:
            out_m, out_n = out_shape

    y_out = np.linspace(y_north, y_south, out_m)

    # Invert numerically: for each output y, find the latitude that maps to it.
    # A dense monotonic lookup over the bbox latitude span is exact enough and
    # avoids per-projection closed-form inverses.
    lat_dense = np.linspace(north, south, 4096)
    y_dense = y_of_lat(lat_dense)
    # y_dense is monotonic decreasing (north->south); np.interp needs ascending x.
    lat_sample = np.interp(y_out, y_dense[::-1], lat_dense[::-1])

    lon_sample = np.linspace(west, east, out_n)

    lat_px = np.clip((north - lat_sample) / (north - south) * (m - 1), 0, m - 1)
    lon_px = np.clip((lon_sample - west) / (east - west) * (n - 1), 0, n - 1)
    result = _sample_grid(mat, lat_px, lon_px, order, fill_value)

    metadata['output_shape'] = (out_m, out_n)
    metadata['y_range'] = (y_north, y_south)
    return result, metadata


def _project_mercator(
    mat: np.ndarray,
    bbox: tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
    out_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, dict]:
    """
    Web Mercator projection.
    Conformal (preserves angles), but distorts area at high latitudes.
    """
    north, south, east, west = bbox
    m, n = mat.shape

    # Clamp latitudes to avoid infinity at poles
    max_lat = 85.0
    north_clamped = min(north, max_lat)
    south_clamped = max(south, -max_lat)

    def lat_to_mercator_y(lat):
        """Convert latitude to Mercator Y coordinate."""
        lat_rad = np.radians(np.clip(lat, -max_lat, max_lat))
        return np.log(np.tan(np.pi/4 + lat_rad/2))

    def mercator_y_to_lat(y):
        """Convert Mercator Y back to latitude."""
        return np.degrees(2 * np.arctan(np.exp(y)) - np.pi/2)

    # Mercator Y range
    y_north = lat_to_mercator_y(north_clamped)
    y_south = lat_to_mercator_y(south_clamped)

    # Create output grid
    if maintain_dimensions:
        out_m, out_n = m, n
    else:
        # Aspect ratio in Mercator space
        merc_height = abs(y_north - y_south)
        merc_width = np.radians(east - west)
        aspect = merc_width / merc_height
        out_m = m
        out_n = int(m * aspect)
        if out_shape:
            out_m, out_n = out_shape

    # Output coordinates in Mercator space
    y_out = np.linspace(y_north, y_south, out_m)
    x_out = np.linspace(np.radians(west), np.radians(east), out_n)

    # Convert Mercator coordinates back to lat/lon for sampling
    lat_sample = mercator_y_to_lat(y_out)
    lon_sample = np.degrees(x_out)

    # Create sampling grid
    # Map to input pixel coordinates
    lat_px = np.clip((north - lat_sample) / (north - south) * (m - 1), 0, m - 1)
    lon_px = np.clip((lon_sample - west) / (east - west) * (n - 1), 0, n - 1)
    result = _sample_grid(mat, lat_px, lon_px, order, fill_value)

    metadata['output_shape'] = (out_m, out_n)
    metadata['mercator_y_range'] = (y_north, y_south)

    # Scale at the equator
    metadata['scale_m_per_px'] = EARTH_RADIUS * np.radians(east - west) / out_n

    return result, metadata


def _project_equidistant(
    mat: np.ndarray,
    bbox: tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
    out_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, dict]:
    """
    Equidistant Cylindrical projection.
    Preserves distances along meridians.
    """
    north, south, east, west = bbox
    m, n = mat.shape

    # Standard parallel (where scale is true)
    center_lat = (north + south) / 2
    cos_std = np.cos(np.radians(center_lat))

    if maintain_dimensions:
        out_m, out_n = m, n
    else:
        # True aspect ratio
        lat_range = north - south
        lon_range = (east - west) * cos_std
        aspect = lon_range / lat_range
        out_m = m
        out_n = max(1, int(m * aspect))
        if out_shape:
            out_m, out_n = out_shape

    # For equidistant, we just need to adjust horizontal scale
    # This is essentially the same as cosine correction with maintain_dimensions
    if maintain_dimensions:
        return _project_cosine(mat, bbox, True, fill_value, metadata, order=order)

    # Resample to correct aspect ratio
    interp = cv2.INTER_NEAREST if order == 0 else cv2.INTER_LINEAR
    result = cv2.resize(mat.astype(np.float32), (out_n, out_m),
                        interpolation=interp)

    metadata['output_shape'] = (out_m, out_n)
    metadata['standard_parallel'] = center_lat
    metadata['scale_x_m_per_px'] = (east - west) * M_PER_DEG_LON_EQ * cos_std / out_n
    metadata['scale_y_m_per_px'] = (north - south) * M_PER_DEG_LAT / out_m

    return result, metadata


def _project_lambert(
    mat: np.ndarray,
    bbox: tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
    out_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, dict]:
    """
    Lambert Cylindrical Equal-Area projection.
    Preserves area, but distorts shapes.
    """
    north, south, east, west = bbox
    m, n = mat.shape

    def lat_to_lambert_y(lat):
        """Convert latitude to Lambert Y coordinate."""
        return np.sin(np.radians(lat))

    def lambert_y_to_lat(y):
        """Convert Lambert Y back to latitude."""
        return np.degrees(np.arcsin(np.clip(y, -1, 1)))

    # Lambert Y range
    y_north = lat_to_lambert_y(north)
    y_south = lat_to_lambert_y(south)

    if maintain_dimensions:
        out_m, out_n = m, n
    else:
        # Equal area aspect ratio
        lambert_height = abs(y_north - y_south)
        lambert_width = np.radians(east - west)
        aspect = lambert_width / lambert_height
        out_m = m
        out_n = max(1, int(m * aspect))
        if out_shape:
            out_m, out_n = out_shape

    # Output coordinates
    y_out = np.linspace(y_north, y_south, out_m)

    # Convert back to latitude for sampling
    lat_sample = lambert_y_to_lat(y_out)

    # Longitude is linear
    lon_sample = np.linspace(west, east, out_n)

    # Map to input pixel coordinates
    lat_px = np.clip((north - lat_sample) / (north - south) * (m - 1), 0, m - 1)
    lon_px = np.clip((lon_sample - west) / (east - west) * (n - 1), 0, n - 1)
    result = _sample_grid(mat, lat_px, lon_px, order, fill_value)

    metadata['output_shape'] = (out_m, out_n)
    metadata['lambert_y_range'] = (y_north, y_south)

    return result, metadata


def _project_sinusoidal(
    mat: np.ndarray,
    bbox: tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
    out_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, dict]:
    """
    Sinusoidal (Sanson-Flamsteed) projection.
    Equal-area pseudocylindrical projection.
    """
    north, south, east, west = bbox
    m, n = mat.shape

    center_lon = (east + west) / 2

    if maintain_dimensions:
        out_m, out_n = m, n
    else:
        # True aspect ratio: sinusoidal's projected width at a given latitude
        # is (east-west)*cos(lat) — widest at whichever bbox edge is closest
        # to the equator. Sizing out_n from that widest row (rather than a
        # fixed out_n = n) means no valid data is clipped by undersizing, and
        # the empty "wing" corners are trimmed by the existing clip_nans path
        # exactly as they are today. Deterministic from (m, n, bbox), so
        # independently projected layers from the same bbox/dim stay aligned.
        widest_cos = max(abs(np.cos(np.radians(north))), abs(np.cos(np.radians(south))))
        lat_height = np.radians(north - south) or 1.0
        proj_width = np.radians(east - west) * widest_cos
        aspect = proj_width / lat_height
        out_m = m
        out_n = max(1, int(round(m * aspect)))
        if out_shape:
            out_m, out_n = out_shape

    # Vectorized inverse sample. For each output row i (latitude lat) and each
    # output column (normalized x in [-1, 1]):
    #     lon = x * half_width / cos(lat) + center_lon
    # We build the full (out_m, out_n) lon-sample grid at once and sample it in
    # one _sample_grid call (the row coordinate is per row, the column per cell).
    lat_values = np.linspace(north, south, out_m)
    x_out = np.linspace(-1.0, 1.0, out_n)          # (out_n,)
    half_width = (east - west) / 2.0

    # x in [-1, 1] spans the widest row, (east-west)/2 * widest_cos either side of the
    # centre; a row at latitude lat covers lon = centre + x * half_width * widest_cos / cos(lat).
    # Until 2026-10-02 the widest_cos factor was missing, so only the middle cos(lat) of the
    # width held data and the rest was trimmed: a 37° N box came out 20 % too narrow.
    widest_cos = max(abs(np.cos(np.radians(north))), abs(np.cos(np.radians(south))))
    cos_lat = np.cos(np.radians(lat_values))       # (out_m,)
    # Guard the poles: rows with ~0 cosine can't be sampled (division blows up).
    safe = cos_lat >= 0.01
    cos_lat_safe = np.where(safe, cos_lat, 1.0)

    # lon_sample[i, j] = x_out[j] * half_width * widest_cos / cos_lat[i] + center_lon
    lon_sample = (x_out[None, :] * half_width * widest_cos) / cos_lat_safe[:, None] + center_lon

    eps = 1e-9 * abs(east - west)
    valid = (lon_sample >= west - eps) & (lon_sample <= east + eps) & safe[:, None]

    # Input pixel coordinates for every output cell.
    lon_px = np.clip((lon_sample - west) / (east - west) * (n - 1), 0, n - 1)
    lat_px = (north - lat_values) / (north - south) * (m - 1)
    result = _sample_grid(mat, lat_px, lon_px, order, fill_value)   # fresh float64
    result[~valid] = fill_value

    metadata['output_shape'] = (out_m, out_n)
    metadata['center_lon'] = center_lon

    return result, metadata


# ---------------------------------------------------------------------------
# Azimuthal and conic projections (laea, lcc)
# ---------------------------------------------------------------------------
# Neither has a rectangular outline, so (like sinusoidal) the output raster is the
# bounding rectangle of the projected bbox and pixels outside the bbox are filled.
# Pixel (row i, col j) of an (m, n) output sits at
#     x = x0 + j / (n - 1) * (x1 - x0),   y = y1 - i / (m - 1) * (y1 - y0)
# on the unit sphere, north up. graticule.js (geoAtFrac, projectedFrac) mirrors this.

_CONIC_AZIMUTHAL = ('laea', 'lcc')
#: Boundary samples per bbox edge when measuring the projected extent.
_EXTENT_SAMPLES = 1001
_MAX_LAT = 89.9


def lcc_parameters(bbox: tuple[float, float, float, float]) -> tuple[float, float] | None:
    """Standard parallels ``(lat1, lat2)`` in degrees for the Lambert Conformal Conic:
    south + span/6 and north - span/6. None when the cone is undefined, in which case
    ``lcc`` behaves like ``equidistant`` (same shape, same sampling): the bbox straddles
    the equator (south < 0 < north, the cone would flip hemisphere) or the two parallels
    coincide within 1e-6 degrees.
    """
    north, south = float(bbox[0]), float(bbox[1])
    if south < 0.0 < north:
        return None
    span = north - south
    lat1, lat2 = south + span / 6.0, north - span / 6.0
    if abs(lat2 - lat1) < 1e-6:
        return None
    return (max(-_MAX_LAT, min(_MAX_LAT, lat1)),
            max(-_MAX_LAT, min(_MAX_LAT, lat2)))


def _tan_half(phi):
    return np.tan(np.pi / 4 + phi / 2)


class _ConicAzimuthalModel:
    """Forward/inverse of a spherical LAEA or LCC (unit sphere) plus the projected
    extent ``(x0, x1, y0, y1)`` of the bbox. Angles in degrees at the interface."""

    def __init__(self, bbox: tuple[float, float, float, float], projection: str):
        north, south, east, west = (float(v) for v in bbox)
        self.projection = projection
        self.lat0 = np.radians((north + south) / 2.0)
        self.lon0 = np.radians((east + west) / 2.0)
        if projection == 'laea':
            self.forward, self.inverse = self._laea_forward, self._laea_inverse
        else:
            lat1, lat2 = lcc_parameters(bbox)
            p1, p2 = np.radians(lat1), np.radians(lat2)
            self.n = np.log(np.cos(p1) / np.cos(p2)) / np.log(_tan_half(p2) / _tan_half(p1))
            self.F = np.cos(p1) * _tan_half(p1) ** self.n / self.n
            self.rho0 = self.F / _tan_half(self.lat0) ** self.n
            self.forward, self.inverse = self._lcc_forward, self._lcc_inverse
        # Extent from the bbox boundary (the extremes of both projections lie on it).
        ts = np.linspace(0.0, 1.0, _EXTENT_SAMPLES)
        lats = np.concatenate([south + ts * (north - south), south + ts * (north - south),
                               np.full_like(ts, north), np.full_like(ts, south)])
        lons = np.concatenate([np.full_like(ts, west), np.full_like(ts, east),
                               west + ts * (east - west), west + ts * (east - west)])
        x, y = self.forward(lats, lons)
        self.extents = (float(x.min()), float(x.max()), float(y.min()), float(y.max()))

    # -- LAEA (Snyder 24-2 .. 24-5, sphere) ------------------------------------
    def _laea_forward(self, lat, lon):
        phi, dl = np.radians(lat), np.radians(lon) - self.lon0
        s0, c0 = np.sin(self.lat0), np.cos(self.lat0)
        k = np.sqrt(2.0 / (1.0 + s0 * np.sin(phi) + c0 * np.cos(phi) * np.cos(dl)))
        return (k * np.cos(phi) * np.sin(dl),
                k * (c0 * np.sin(phi) - s0 * np.cos(phi) * np.cos(dl)))

    def _laea_inverse(self, x, y):
        """(lat, lon) in degrees; NaN outside the sphere (rho > 2)."""
        rho = np.hypot(x, y)
        ok = rho <= 2.0
        c = 2.0 * np.arcsin(np.where(ok, rho / 2.0, 0.0))
        s0, c0 = np.sin(self.lat0), np.cos(self.lat0)
        safe = np.where(rho > 1e-15, rho, 1.0)
        sin_phi = np.cos(c) * s0 + np.where(rho > 1e-15, y * np.sin(c) * c0 / safe, 0.0)
        lat = np.arcsin(np.clip(sin_phi, -1.0, 1.0))
        lon = self.lon0 + np.arctan2(x * np.sin(c),
                                     rho * c0 * np.cos(c) - y * s0 * np.sin(c))
        lat, lon = np.degrees(lat), np.degrees(lon)
        return np.where(ok, lat, np.nan), np.where(ok, lon, np.nan)

    # -- LCC (Snyder 15-1 .. 15-11, sphere) --------------------------------------
    def _lcc_forward(self, lat, lon):
        phi = np.radians(np.clip(lat, -_MAX_LAT, _MAX_LAT))
        rho = self.F / _tan_half(phi) ** self.n
        theta = self.n * (np.radians(lon) - self.lon0)
        return rho * np.sin(theta), self.rho0 - rho * np.cos(theta)

    def _lcc_inverse(self, x, y):
        sgn = np.sign(self.n)
        dy = self.rho0 - y
        rho = sgn * np.hypot(x, dy)
        theta = np.arctan2(sgn * x, sgn * dy)
        with np.errstate(divide='ignore', invalid='ignore'):
            lat = 2.0 * np.arctan((self.F / rho) ** (1.0 / self.n)) - np.pi / 2
        lon = self.lon0 + theta / self.n
        return np.degrees(lat), np.degrees(lon)


_MODEL_CACHE: dict = {}


def _conic_azimuthal_model(bbox: tuple[float, float, float, float],
                           projection: str) -> _ConicAzimuthalModel:
    key = (tuple(float(v) for v in bbox), projection)
    model = _MODEL_CACHE.get(key)
    if model is None:
        if len(_MODEL_CACHE) > 64:
            _MODEL_CACHE.clear()
        model = _MODEL_CACHE[key] = _ConicAzimuthalModel(key[0], projection)
    return model


def projected_fraction(bbox: tuple[float, float, float, float], projection: str,
                       lat, lon):
    """Where ``(lat, lon)`` lands in the ``laea`` / ``lcc`` raster, as fractions
    ``(fx, fy)`` of its width and height (x 0 = west edge, y 0 = north edge)."""
    model = _conic_azimuthal_model(tuple(bbox), projection)
    x0, x1, y0, y1 = model.extents
    x, y = model.forward(np.asarray(lat, dtype=np.float64), np.asarray(lon, dtype=np.float64))
    return (x - x0) / (x1 - x0), (y1 - y) / (y1 - y0)


def _project_conic_azimuthal(
    mat: np.ndarray,
    bbox: tuple[float, float, float, float],
    projection: str,
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
    out_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, dict]:
    """Lambert Azimuthal Equal-Area (``laea``) or Lambert Conformal Conic (``lcc``).

    Spherical formulas; the centre (lat0, lon0) is the bbox centre, so it works anywhere.
    Each output pixel is inverse-projected to lat/lon and the input sampled there
    (bilinear, or nearest for ``order=0``); pixels outside the bbox get ``fill_value``.
    ``lcc`` falls back to ``equidistant`` when :func:`lcc_parameters` is None.
    """
    if projection == 'lcc' and lcc_parameters(bbox) is None:
        metadata['lcc_fallback'] = 'equidistant'
        return _project_equidistant(mat, bbox, maintain_dimensions, fill_value,
                                    metadata, order=order, out_shape=out_shape)
    north, south, east, west = bbox
    m, n = mat.shape
    model = _conic_azimuthal_model(tuple(bbox), projection)
    x0, x1, y0, y1 = model.extents

    if maintain_dimensions:
        out_m, out_n = m, n
    else:
        aspect = (x1 - x0) / (y1 - y0) if y1 > y0 else 1.0
        out_m = m
        out_n = max(1, int(round(m * aspect)))
        if out_shape:
            out_m, out_n = out_shape

    xs = np.linspace(x0, x1, out_n) if out_n > 1 else np.array([(x0 + x1) / 2])
    ys = np.linspace(y1, y0, out_m) if out_m > 1 else np.array([(y0 + y1) / 2])
    lat, lon = model.inverse(xs[None, :], ys[:, None])      # (out_m, out_n)

    eps_lat, eps_lon = 1e-6 * (north - south), 1e-6 * (east - west)
    with np.errstate(invalid='ignore'):
        valid = ((lat >= south - eps_lat) & (lat <= north + eps_lat)
                 & (lon >= west - eps_lon) & (lon <= east + eps_lon))   # NaN is False
    row_px = np.clip((north - np.where(valid, lat, north)) / (north - south) * (m - 1), 0, m - 1)
    col_px = np.clip((np.where(valid, lon, west) - west) / (east - west) * (n - 1), 0, n - 1)

    result = ndimage.map_coordinates(np.asarray(mat, dtype=np.float64), [row_px, col_px],
                                     order=order, mode='nearest')
    result[~valid] = fill_value

    metadata['output_shape'] = (out_m, out_n)
    metadata['projected_extent'] = model.extents
    metadata['center_lat'] = float(np.degrees(model.lat0))
    metadata['center_lon'] = float(np.degrees(model.lon0))
    return result, metadata


# Convenience function matching original API
def proj_map_geo_to_2D(
    mat: np.ndarray,
    NSEW: np.ndarray,
    clip_out: bool = True,
    projection: ProjectionType = 'cosine',
    maintain_dimensions: bool = False
) -> np.ndarray:
    """
    Backward-compatible wrapper for the original proj_map_geo_to_2D function.

    Parameters
    ----------
    mat : np.ndarray
        Input elevation matrix
    NSEW : np.ndarray
        Bounding box as [north, south, east, west]
    clip_out : bool
        If True and maintain_dimensions=False, clip NaN columns (original behavior)
    projection : str
        Projection type (default: 'cosine' for backward compatibility)
    maintain_dimensions : bool
        If True, output has same dimensions as input

    Returns
    -------
    np.ndarray
        Projected matrix
    """
    bbox = tuple(NSEW)
    result, metadata = project_coordinates(
        mat, bbox,
        projection=projection,
        maintain_dimensions=maintain_dimensions,
        fill_value=np.nan
    )
    return result


def project_grid(arr: np.ndarray,
                 north: float, south: float, east: float, west: float,
                 projection: ProjectionType,
                 clip_nans: bool,
                 categorical: bool = False,
                 maintain_dimensions: bool = False,
                 target_dim: int | None = None) -> np.ndarray:
    """Apply a geo2stl projection to a 2-D array.

    Convenience wrapper used by DEM/hydrology pipelines that need a single
    projected output array without the metadata dict.

    Parameters
    ----------
    arr :         2-D input raster (elevation, water mask, etc.)
    north/south/east/west : geographic bounds in degrees
    projection :  projection type string ('cosine', 'mercator', etc.)
    clip_nans :   if True, strip all-NaN edge rows/columns after projecting
    categorical : if True, use nearest-neighbour interpolation (order=0) to
                  preserve integer class IDs; NaN fill is replaced with 0.
    maintain_dimensions : if True, output keeps the input's pixel shape
                  (legacy/opt-in). Default False — output reflects the
                  projection's true geographic aspect ratio (F-PROJ-DIMS).
    target_dim :  longest side of the output; the output is then exactly
                  :func:`projected_shape` whatever ``arr``'s size (see
                  :func:`project_coordinates`). Pass the request's ``dim``.
    """
    projected, _meta = project_coordinates(
        arr, (north, south, east, west),
        projection=projection,
        maintain_dimensions=maintain_dimensions,
        fill_value=np.nan,
        clip_nans=clip_nans,
        order=0 if categorical else 1,
        target_dim=target_dim,
    )
    if categorical:
        projected = np.nan_to_num(projected, nan=0.0)
    return projected


def project_water_arrays(water_mask: np.ndarray,
                         esa_img: np.ndarray,
                         north: float, south: float, east: float, west: float,
                         projection: ProjectionType,
                         clip_nans: bool,
                         align_streams: bool = True,
                         maintain_dimensions: bool = False,
                         target_dim: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Project water and ESA arrays via common projection helpers.

    Each stream is projected independently through ``project_coordinates``.
    If ``align_streams=True`` (default), a shared valid-data mask is used when
    clipping NaN borders so both arrays keep identical output shapes. This
    requires water_mask and esa_img to already share the same input shape
    (true at every call site — both come from one fetch_water_mask() call).
    With ``target_dim`` both land on :func:`projected_shape` and no border is
    clipped (see :func:`project_coordinates`).

    The mask stays bilinear (order 1) then ``> 0.5``: nearest-neighbour (order 0)
    is not the same threshold. On a synthetic lakes-and-rivers mask at dim 2000 it
    flipped ~1 % of all pixels (~5 % of water pixels, mostly 1-3 px river edges) to
    save 15-30 ms (F-SPEED #14, measured 2026-10-14).
    """
    wm_proj, _wm_meta = project_coordinates(
        water_mask, (north, south, east, west),
        projection=projection,
        maintain_dimensions=maintain_dimensions,
        fill_value=np.nan,
        clip_nans=False,
        target_dim=target_dim,
    )
    esa_proj, _esa_meta = project_coordinates(
        esa_img.astype(np.float32), (north, south, east, west),
        projection=projection,
        maintain_dimensions=maintain_dimensions,
        fill_value=0,
        clip_nans=False,
        order=0,
        target_dim=target_dim,
    )

    if clip_nans and wm_proj.ndim == 2 and not target_dim:
        wm_nan = np.isnan(wm_proj)
        esa_nan = np.isnan(esa_proj)
        if align_streams:
            shared_nan = wm_nan | esa_nan
            wm_proj = _clip_nan_borders(wm_proj, shared_nan)
            esa_proj = _clip_nan_borders(esa_proj, shared_nan)
        else:
            wm_proj = _clip_nan_borders(wm_proj, wm_nan)
            esa_proj = _clip_nan_borders(esa_proj, esa_nan)

    wm_proj = (wm_proj > 0.5).astype(np.float32)
    esa_proj = np.nan_to_num(esa_proj, nan=0.0)
    return wm_proj, esa_proj


def project_rgb_image(img_arr: np.ndarray,
                      north: float, south: float, east: float, west: float,
                      projection: ProjectionType,
                      clip_nans: bool,
                      maintain_dimensions: bool = False,
                      target_dim: int | None = None) -> np.ndarray:
    """Project an RGB image channel-by-channel and return uint8 output
    (``target_dim``: see :func:`project_coordinates`)."""
    channels = []
    nan_mask = None
    for c in range(img_arr.shape[2]):
        ch = img_arr[:, :, c].astype(np.float32)
        projected, _meta = project_coordinates(
            ch, (north, south, east, west),
            projection=projection,
            maintain_dimensions=maintain_dimensions,
            fill_value=np.nan,
            clip_nans=False,
            target_dim=target_dim,
        )
        if nan_mask is None:
            nan_mask = np.isnan(projected)
        projected = np.nan_to_num(projected, nan=0.0)
        channels.append(projected)

    result = np.stack(channels, axis=-1)

    if clip_nans and not target_dim and nan_mask is not None and nan_mask.ndim == 2:
        result = _clip_nan_borders(result, nan_mask)

    return np.clip(result, 0, 255).astype(np.uint8)
