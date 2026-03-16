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
        }
    }


def project_coordinates(
    mat: np.ndarray,
    bbox: Tuple[float, float, float, float],
    projection: ProjectionType = 'cosine',
    maintain_dimensions: bool = True,
    fill_value: float = np.nan
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
        return _project_cosine(mat, bbox, maintain_dimensions, fill_value, metadata)

    elif projection == 'mercator':
        return _project_mercator(mat, bbox, maintain_dimensions, fill_value, metadata)

    elif projection == 'equidistant':
        return _project_equidistant(mat, bbox, maintain_dimensions, fill_value, metadata)

    elif projection == 'lambert':
        return _project_lambert(mat, bbox, maintain_dimensions, fill_value, metadata)

    elif projection == 'sinusoidal':
        return _project_sinusoidal(mat, bbox, maintain_dimensions, fill_value, metadata)

    else:
        raise ValueError(f"Unknown projection: {projection}")


def _project_cosine(
    mat: np.ndarray,
    bbox: Tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict
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
        # Original behavior - variable output width
        # This can cause layer misalignment!
        xv, yv = np.meshgrid(range(n), range(m))
        xc = (n - 1) / 2
        yc = (m - 1) / 2
        xv_c = (xv - xc).astype(int)
        yv_c = (yv - yc).astype(int)

        lat_v = np.deg2rad(lat_values[:, None])
        xv_adj = xv_c * np.cos(lat_v)

        xv2 = (xv_adj + xc).astype(int)
        yv2 = (yv_c + yc).astype(int)

        mat_adj = np.full_like(mat, np.nan, dtype=np.float64)

        # Clip indices to valid range
        valid = (xv2 >= 0) & (xv2 < n) & (yv2 >= 0) & (yv2 < m)
        mat_adj[yv2[valid], xv2[valid]] = mat[yv[valid], xv[valid]]

        # Clip NaN columns
        mat_adj = mat_adj[:, ~np.all(np.isnan(mat_adj), axis=0)]

        metadata['output_shape'] = mat_adj.shape
        return mat_adj, metadata


def _project_mercator(
    mat: np.ndarray,
    bbox: Tuple[float, float, float, float],
    maintain_dimensions: bool,
    fill_value: float,
    metadata: dict
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
        order=1,
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
    metadata: dict
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
        return _project_cosine(mat, bbox, True, fill_value, metadata)

    # Resample to correct aspect ratio
    result = cv2.resize(mat.astype(np.float32), (out_n, out_m),
                        interpolation=cv2.INTER_LINEAR)

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
    metadata: dict
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
        order=1,
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
    metadata: dict
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
        out_m, out_n = m, n  # Keep same for sinusoidal

    result = np.full((out_m, out_n), fill_value, dtype=np.float64)

    # For each output row
    lat_values = np.linspace(north, south, out_m)

    for i, lat in enumerate(lat_values):
        cos_lat = np.cos(np.radians(lat))

        if cos_lat < 0.01:  # Near poles
            continue

        # In sinusoidal projection, x = (lon - center_lon) * cos(lat)
        # We need to reverse this: sample from input at appropriate longitudes

        # Output x range (in projected space)
        x_out = np.linspace(-1, 1, out_n)  # Normalized [-1, 1]

        # Convert to longitude
        # x_proj = (lon - center_lon) * cos(lat) * scale
        # lon = x_proj / (cos(lat) * scale) + center_lon
        half_width = (east - west) / 2
        lon_sample = x_out * half_width / cos_lat + center_lon

        # Check bounds
        valid = (lon_sample >= west) & (lon_sample <= east)

        if not np.any(valid):
            continue

        # Map to input pixel coordinates
        lon_px = (lon_sample[valid] - west) / (east - west) * (n - 1)
        lat_px = (north - lat) / (north - south) * (m - 1)

        # Sample from input
        lat_px_arr = np.full_like(lon_px, lat_px)

        sampled = ndimage.map_coordinates(
            mat.astype(np.float64),
            [lat_px_arr, lon_px],
            order=1,
            mode='constant',
            cval=fill_value
        )

        result[i, valid] = sampled

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
