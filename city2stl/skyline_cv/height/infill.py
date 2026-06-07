"""
city2stl/height/infill -- Heightmap inpainting for building height gaps.

Fills NaN regions in a partial heightmap using deterministic methods.
No ML required.  These run client-side in the session and are fast enough
for typical city-scale grids (256 x 256 to 1024 x 1024 pixels).

Public API
----------
infill_idw(heightmap, mask, dem_baseline=None, power=2)
    -> np.ndarray (H, W) float32, no NaN in known+filled area

infill_nearest(heightmap)
    -> np.ndarray (H, W) float32, purely nearest-neighbour fill

Notes
-----
- "mask" here means the same mask as returned by stl_to_heightmap -- True
  where the heightmap has a valid measurement.  NaN pixels inside the mask
  are treated as unknown; pixels outside the mask are preserved as NaN.
- ``infill_idw`` is the recommended method for STL-sourced partial maps.
- ``infill_nearest`` is a fast fallback with sharp fill boundaries.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def infill_idw(
    heightmap: np.ndarray,
    mask: Optional[np.ndarray] = None,
    dem_baseline: Optional[np.ndarray] = None,
    power: float = 2.0,
) -> np.ndarray:
    """Fill NaN pixels in *heightmap* using Inverse Distance Weighting.

    For each unknown pixel, the fill value is a weighted mean of all known
    pixels, where the weight of known pixel k is ``1 / dist(k, unknown)^power``.
    When many known pixels exist, scipy.interpolate.griddata with ``method='linear'``
    (Delaunay triangulation) is used as a computationally efficient proxy,
    with a ``nearest``-neighbour fallback for pixels outside the convex hull.

    If *dem_baseline* is provided, fill values are blended toward the DEM
    surface far from known data.  This prevents the infill from diverging in
    large empty regions.

    Parameters
    ----------
    heightmap : (H, W) float32 ndarray
        Partial height raster.  NaN where unknown.
    mask : (H, W) bool ndarray, optional
        Region of interest.  Only pixels inside the mask are filled.
        Pixels outside the mask remain NaN.  If None, all pixels are eligible.
    dem_baseline : (H, W) float32 ndarray, optional
        Background DEM surface.  Where provided, far-from-data fill values
        are blended toward the DEM to keep results physically plausible.
    power : float
        IDW power parameter (default 2).  Higher = more localised.

    Returns
    -------
    (H, W) float32 ndarray
        Copy of *heightmap* with NaN pixels filled.  Pixels outside *mask*
        (if provided) remain NaN.
    """
    try:
        from scipy.interpolate import griddata
        from scipy.ndimage import distance_transform_edt
        _SCIPY = True
    except ImportError:
        _SCIPY = False
        logger.warning(
            "scipy not available - falling back to nearest-neighbour infill"
        )

    if not _SCIPY:
        return infill_nearest(heightmap)

    h, w = heightmap.shape
    result = heightmap.astype(np.float32).copy()

    # Active region: use supplied mask or default to all pixels
    if mask is not None:
        active = mask.astype(bool)
    else:
        active = np.ones((h, w), dtype=bool)

    known_mask = active & (~np.isnan(result))
    unknown_mask = active & np.isnan(result)

    if not unknown_mask.any():
        return result  # Nothing to fill

    if not known_mask.any():
        # No known data -- fill with DEM baseline or zeros
        if dem_baseline is not None:
            result[unknown_mask] = dem_baseline[unknown_mask].astype(np.float32)
        else:
            result[unknown_mask] = 0.0
        return result

    # Pixel coordinates of known and unknown points
    ky, kx = np.where(known_mask)
    known_vals = result[ky, kx]
    uy, ux = np.where(unknown_mask)

    # -- Triangulation-based interpolation (fast O(N log N)) ---------------
    known_coords = np.column_stack([ky.astype(np.float32),
                                    kx.astype(np.float32)])
    query_coords = np.column_stack([uy.astype(np.float32),
                                    ux.astype(np.float32)])

    # Linear requires >= 4 non-collinear known points for Delaunay triangulation.
    # Fall back immediately to nearest when the known set is too small.
    filled_linear: Optional[np.ndarray] = None
    if len(known_coords) >= 4:
        try:
            filled_linear = griddata(
                known_coords, known_vals.astype(np.float64),
                query_coords, method="linear"
            ).astype(np.float32)
        except Exception:
            filled_linear = None

    if filled_linear is None:
        # Either too few points or a degenerate triangulation -- use nearest.
        filled_linear = griddata(
            known_coords, known_vals.astype(np.float64),
            query_coords, method="nearest"
        ).astype(np.float32)
    else:
        # Nearest-neighbour fallback for points outside the convex hull.
        nan_linear = np.isnan(filled_linear)
        if nan_linear.any():
            filled_nearest = griddata(
                known_coords, known_vals.astype(np.float64),
                query_coords[nan_linear], method="nearest"
            ).astype(np.float32)
            filled_linear[nan_linear] = filled_nearest

    # -- DEM baseline blending ---------------------------------------------
    if dem_baseline is not None:
        # Compute distance of each unknown pixel from the nearest known pixel
        dist_map = distance_transform_edt(~known_mask)
        max_dist = float(dist_map.max())

        if max_dist > 0:
            # Blend weight: 0 near known data, 1 far away
            blend_w = np.clip(dist_map / max_dist, 0.0, 1.0).astype(np.float32)
            blend_at_unknown = blend_w[uy, ux]
            dem_at_unknown = dem_baseline[uy, ux].astype(np.float32)
            filled_linear = (
                filled_linear * (1.0 - blend_at_unknown)
                + dem_at_unknown * blend_at_unknown
            )

    result[uy, ux] = filled_linear
    return result


def infill_nearest(heightmap: np.ndarray) -> np.ndarray:
    """Fill NaN pixels using nearest-known-neighbour propagation.

    Fast O(N) algorithm using ``scipy.ndimage.distance_transform_edt``.
    Produces sharp fill boundaries.  Use ``infill_idw`` for smoother results.

    Parameters
    ----------
    heightmap : (H, W) float32 ndarray
        Partial height raster.  NaN where unknown.

    Returns
    -------
    (H, W) float32 ndarray
        Fully filled copy (no NaN where a neighbour exists).
    """
    try:
        from scipy.ndimage import distance_transform_edt
    except ImportError:
        # Last-resort: replace NaN with 0
        result = heightmap.astype(np.float32).copy()
        result[np.isnan(result)] = 0.0
        return result

    result = heightmap.astype(np.float32).copy()
    nan_mask = np.isnan(result)

    if not nan_mask.any():
        return result

    if nan_mask.all():
        result[:] = 0.0
        return result

    # Replace NaN temporarily with 0 for indexing
    filled = result.copy()
    filled[nan_mask] = 0.0

    # distance_transform_edt with return_indices gives nearest non-zero pixel
    _, indices = distance_transform_edt(nan_mask, return_indices=True)
    result[nan_mask] = filled[indices[0][nan_mask], indices[1][nan_mask]]
    return result
