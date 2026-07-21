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
"""

import numpy as np
from scipy import ndimage
import cv2
from typing import Tuple, Optional, Literal

ProjectionType = Literal['none', 'cosine', 'mercator',
                         'equidistant', 'lambert', 'sinusoidal']

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
        }
    }


def expected_aspect_ratio(bbox: Tuple[float, float, float, float],
                          projection: ProjectionType,
                          input_shape: Optional[Tuple[int, int]] = None) -> float:
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
        y = lambda lat: np.log(np.tan(np.pi / 4 + np.radians(np.clip(lat, -max_lat, max_lat)) / 2))
        merc_height = abs(y(n_c) - y(s_c))
        merc_width = np.radians(east - west)
        return merc_width / merc_height if merc_height else 1.0
    if projection == 'lambert':
        y = lambda lat: np.sin(np.radians(lat))
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
        proj_width = np.radians(east - west)
        return proj_width / proj_height if proj_height else 1.0
    raise ValueError(f"Unknown projection: {projection}")


def verify_layer_alignment(shapes: dict, bbox: Tuple[float, float, float, float],
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
    'sinusoidal' (clip_nans trims its non-rectangular "wings" to a
    data-dependent-looking but actually bbox-deterministic bounding box that
    expected_aspect_ratio does not model exactly). The cross-layer
    comparison alone is sufficient and correct for all three.

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

    if not maintain_dimensions and projection not in ('none', 'cosine', 'sinusoidal'):
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
    bbox: Tuple[float, float, float, float],
    projection: ProjectionType = 'cosine',
    maintain_dimensions: bool = True,
    fill_value: float = np.nan,
    clip_nans: bool = False,
    order: int = 1,
) -> Tuple[np.ndarray, dict]:
    """
    Project a geographic raster to a 2D planar coordinate system.

    Parameters
    ----------
    mat : np.ndarray
        Input 2D array (elevation/data values)
    bbox : tuple
        Bounding box as (north, south, east, west) in degrees
    projection : str
        Projection type: 'none', 'cosine', 'mercator', 'equidistant', 'lambert', 'sinusoidal'
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

    Returns
    -------
    tuple
        (projected_array, metadata_dict)
        metadata contains projection info and scale factors
    """
    north, south, east, west = bbox
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
            east - west) * 111320 * np.cos(np.radians(center_lat)) / n
        metadata['scale_y_m_per_px'] = (north - south) * 110540 / m
        return mat.copy(), metadata

    elif projection == 'cosine':
        result, metadata = _project_cosine(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order)

    elif projection == 'mercator':
        result, metadata = _project_mercator(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order)

    elif projection == 'equidistant':
        result, metadata = _project_equidistant(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order)

    elif projection == 'lambert':
        result, metadata = _project_lambert(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order)

    elif projection == 'sinusoidal':
        result, metadata = _project_sinusoidal(
            mat, bbox, maintain_dimensions, fill_value, metadata, order=order)

    elif projection == 'miller':
        result, metadata = _project_cylindrical_y(
            mat, bbox, maintain_dimensions, fill_value, metadata,
            _miller_y, order=order)

    elif projection == 'gall':
        result, metadata = _project_cylindrical_y(
            mat, bbox, maintain_dimensions, fill_value, metadata,
            _gall_y, order=order)

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
    if clip_nans and not maintain_dimensions and result.ndim == 2:
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
    bbox: Tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
) -> Tuple[np.ndarray, dict]:
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

        for i, (lat, c) in enumerate(zip(lat_values, cos_lat)):
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
        metadata['scale_x_m_per_px'] = (east - west) * 111320 * avg_cos / n
        metadata['scale_y_m_per_px'] = (north - south) * 110540 / m

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
        interp = cv2.INTER_NEAREST if order == 0 else cv2.INTER_LINEAR
        result = cv2.resize(mat.astype(np.float32), (out_n, out_m),
                            interpolation=interp)

        metadata['output_shape'] = (out_m, out_n)
        metadata['scale_x_m_per_px'] = (east - west) * 111320 * avg_cos / out_n
        metadata['scale_y_m_per_px'] = (north - south) * 110540 / out_m

        return result.astype(np.float64), metadata


# ---------------------------------------------------------------------------
# Generic cylindrical projection
# ---------------------------------------------------------------------------
# A cylindrical projection maps longitude linearly to x and latitude to y via a
# monotonic function y = f(lat) (degrees in, unitless out). Any such projection
# (Mercator, Lambert, Miller, Gall, ...) is a one-liner given f. We inverse-sample
# on a regular y grid so maintain_dimensions holds and the result stays aligned
# with all other layers. This is the vectorized single-map_coordinates path.

def _miller_y(lat_deg):
    """Miller Cylindrical: y = 1.25 * ln(tan(pi/4 + 0.4*phi)). Compromise."""
    phi = np.radians(np.clip(lat_deg, -89.9, 89.9))
    return 1.25 * np.log(np.tan(np.pi / 4 + 0.4 * phi))


def _gall_y(lat_deg):
    """Gall Stereographic: y = (1 + sqrt(2)/2) * tan(phi/2). Compromise."""
    phi = np.radians(np.clip(lat_deg, -89.9, 89.9))
    return (1 + np.sqrt(2) / 2) * np.tan(phi / 2)


def _project_cylindrical_y(
    mat: np.ndarray,
    bbox: Tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    y_of_lat,
    order: int = 1,
) -> Tuple[np.ndarray, dict]:
    """Project a raster with a cylindrical y = y_of_lat(lat) transform.

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
        proj_width = np.radians(east - west)
        aspect = proj_width / proj_height if proj_height else 1.0
        out_m = m
        out_n = max(1, int(round(m * aspect)))

    y_out = np.linspace(y_north, y_south, out_m)

    # Invert numerically: for each output y, find the latitude that maps to it.
    # A dense monotonic lookup over the bbox latitude span is exact enough and
    # avoids per-projection closed-form inverses.
    lat_dense = np.linspace(north, south, 4096)
    y_dense = y_of_lat(lat_dense)
    # y_dense is monotonic decreasing (north->south); np.interp needs ascending x.
    lat_sample = np.interp(y_out, y_dense[::-1], lat_dense[::-1])

    lon_sample = np.linspace(west, east, out_n)

    lat_px = (north - lat_sample) / (north - south) * (m - 1)
    lon_px = (lon_sample - west) / (east - west) * (n - 1)
    lon_grid, lat_grid = np.meshgrid(lon_px, lat_px)

    result = ndimage.map_coordinates(
        mat.astype(np.float64),
        [lat_grid.ravel(), lon_grid.ravel()],
        order=order, mode='constant', cval=fill_value,
    ).reshape(out_m, out_n)

    metadata['output_shape'] = (out_m, out_n)
    metadata['y_range'] = (y_north, y_south)
    return result, metadata


def _project_mercator(
    mat: np.ndarray,
    bbox: Tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
) -> Tuple[np.ndarray, dict]:
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

    # Output coordinates in Mercator space
    y_out = np.linspace(y_north, y_south, out_m)
    x_out = np.linspace(np.radians(west), np.radians(east), out_n)

    # Convert Mercator coordinates back to lat/lon for sampling
    lat_sample = mercator_y_to_lat(y_out)
    lon_sample = np.degrees(x_out)

    # Create sampling grid
    # Map to input pixel coordinates
    lat_px = (north - lat_sample) / (north - south) * (m - 1)
    lon_px = (lon_sample - west) / (east - west) * (n - 1)

    lon_grid, lat_grid = np.meshgrid(lon_px, lat_px)

    # Use map_coordinates for smooth interpolation
    result = ndimage.map_coordinates(
        mat.astype(np.float64),
        [lat_grid.ravel(), lon_grid.ravel()],
        order=order,
        mode='constant',
        cval=fill_value
    ).reshape(out_m, out_n)

    metadata['output_shape'] = (out_m, out_n)
    metadata['mercator_y_range'] = (y_north, y_south)

    # Scale at center latitude
    center_lat = (north + south) / 2
    metadata['scale_m_per_px'] = EARTH_RADIUS * np.radians(east - west) / out_n

    return result, metadata


def _project_equidistant(
    mat: np.ndarray,
    bbox: Tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
) -> Tuple[np.ndarray, dict]:
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
    metadata['scale_x_m_per_px'] = (east - west) * 111320 * cos_std / out_n
    metadata['scale_y_m_per_px'] = (north - south) * 110540 / out_m

    return result, metadata


def _project_lambert(
    mat: np.ndarray,
    bbox: Tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
) -> Tuple[np.ndarray, dict]:
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

    # Output coordinates
    y_out = np.linspace(y_north, y_south, out_m)

    # Convert back to latitude for sampling
    lat_sample = lambert_y_to_lat(y_out)

    # Longitude is linear
    lon_sample = np.linspace(west, east, out_n)

    # Map to input pixel coordinates
    lat_px = (north - lat_sample) / (north - south) * (m - 1)
    lon_px = (lon_sample - west) / (east - west) * (n - 1)

    lon_grid, lat_grid = np.meshgrid(lon_px, lat_px)

    result = ndimage.map_coordinates(
        mat.astype(np.float64),
        [lat_grid.ravel(), lon_grid.ravel()],
        order=order,
        mode='constant',
        cval=fill_value
    ).reshape(out_m, out_n)

    metadata['output_shape'] = (out_m, out_n)
    metadata['lambert_y_range'] = (y_north, y_south)

    return result, metadata


def _project_sinusoidal(
    mat: np.ndarray,
    bbox: Tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict,
    order: int = 1,
) -> Tuple[np.ndarray, dict]:
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

    # Vectorized inverse sample. For each output row i (latitude lat) and each
    # output column (normalized x in [-1, 1]):
    #     lon = x * half_width / cos(lat) + center_lon
    # We build the full (out_m, out_n) lon-sample grid at once and sample with a
    # single map_coordinates call. The previous implementation looped per row
    # and called map_coordinates once per row (out_m scipy calls), which made
    # sinusoidal ~20-40x slower than the other projections.
    lat_values = np.linspace(north, south, out_m)
    x_out = np.linspace(-1.0, 1.0, out_n)          # (out_n,)
    half_width = (east - west) / 2.0

    cos_lat = np.cos(np.radians(lat_values))       # (out_m,)
    # Guard the poles: rows with ~0 cosine can't be sampled (division blows up).
    safe = cos_lat >= 0.01
    cos_lat_safe = np.where(safe, cos_lat, 1.0)

    # lon_sample[i, j] = x_out[j] * half_width / cos_lat[i] + center_lon
    lon_sample = (x_out[None, :] * half_width) / cos_lat_safe[:, None] + center_lon

    valid = (lon_sample >= west) & (lon_sample <= east) & safe[:, None]

    # Input pixel coordinates for every output cell.
    lon_px = (lon_sample - west) / (east - west) * (n - 1)
    lat_px = ((north - lat_values) / (north - south) * (m - 1))[:, None]
    lat_px = np.broadcast_to(lat_px, lon_px.shape)

    sampled = ndimage.map_coordinates(
        mat.astype(np.float64),
        [lat_px.ravel(), lon_px.ravel()],
        order=order,
        mode='constant',
        cval=fill_value,
    ).reshape(out_m, out_n)

    result = np.where(valid, sampled, fill_value).astype(np.float64)

    metadata['output_shape'] = (out_m, out_n)
    metadata['center_lon'] = center_lon

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
                 maintain_dimensions: bool = False) -> np.ndarray:
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
    """
    projected, _meta = project_coordinates(
        arr, (north, south, east, west),
        projection=projection,
        maintain_dimensions=maintain_dimensions,
        fill_value=np.nan,
        clip_nans=clip_nans,
        order=0 if categorical else 1,
    )
    if categorical:
        projected = np.nan_to_num(projected, nan=0.0)
    return projected


def project_binary_mask(arr: np.ndarray,
                        north: float, south: float, east: float, west: float,
                        projection: ProjectionType,
                        clip_nans: bool,
                        maintain_dimensions: bool = False) -> np.ndarray:
    """Project a binary mask independently and return a binarized float32 array."""
    projected, _meta = project_coordinates(
        arr.astype(np.float32), (north, south, east, west),
        projection=projection,
        maintain_dimensions=maintain_dimensions,
        fill_value=np.nan,
        clip_nans=clip_nans,
        order=1,
    )
    projected = np.nan_to_num(projected, nan=0.0)
    return (projected > 0.5).astype(np.float32)


def project_categorical_layer(arr: np.ndarray,
                              north: float, south: float, east: float, west: float,
                              projection: ProjectionType,
                              clip_nans: bool,
                              maintain_dimensions: bool = False) -> np.ndarray:
    """Project a categorical raster independently using nearest-neighbour sampling."""
    return project_grid(arr.astype(np.float32), north, south, east, west,
                        projection, clip_nans, categorical=True,
                        maintain_dimensions=maintain_dimensions)


def project_city_raster(arr: np.ndarray,
                        north: float, south: float, east: float, west: float,
                        projection: ProjectionType,
                        clip_nans: bool,
                        maintain_dimensions: bool = False) -> np.ndarray:
    """Project city raster (continuous height field) through the shared grid path."""
    return project_grid(arr.astype(np.float32), north, south, east, west,
                        projection, clip_nans, categorical=False,
                        maintain_dimensions=maintain_dimensions)


def project_water_arrays(water_mask: np.ndarray,
                         esa_img: np.ndarray,
                         north: float, south: float, east: float, west: float,
                         projection: ProjectionType,
                         clip_nans: bool,
                         align_streams: bool = True,
                         maintain_dimensions: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Project water and ESA arrays via common projection helpers.

    Each stream is projected independently through ``project_coordinates``.
    If ``align_streams=True`` (default), a shared valid-data mask is used when
    clipping NaN borders so both arrays keep identical output shapes. This
    requires water_mask and esa_img to already share the same input shape
    (true at every call site — both come from one fetch_water_mask() call).
    """
    wm_proj, _wm_meta = project_coordinates(
        water_mask, (north, south, east, west),
        projection=projection,
        maintain_dimensions=maintain_dimensions,
        fill_value=np.nan,
        clip_nans=False,
    )
    esa_proj, _esa_meta = project_coordinates(
        esa_img.astype(np.float32), (north, south, east, west),
        projection=projection,
        maintain_dimensions=maintain_dimensions,
        fill_value=0,
        clip_nans=False,
        order=0,
    )

    if clip_nans and wm_proj.ndim == 2:
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
                      maintain_dimensions: bool = False) -> np.ndarray:
    """Project an RGB image channel-by-channel and return uint8 output."""
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
        )
        if nan_mask is None:
            nan_mask = np.isnan(projected)
        projected = np.nan_to_num(projected, nan=0.0)
        channels.append(projected)

    result = np.stack(channels, axis=-1)

    if clip_nans and nan_mask is not None and nan_mask.ndim == 2:
        result = _clip_nan_borders(result, nan_mask)

    return np.clip(result, 0, 255).astype(np.uint8)
