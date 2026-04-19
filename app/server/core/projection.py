"""
projection.py — Shared map projection helpers for all raster endpoints.

Wraps geo2stl.projections.project_coordinates with convenience functions
for single-array and dual-array (water+ESA alignment) projection.
"""

import numpy as np


def project_grid(arr, north, south, east, west, projection, clip_nans,
                 categorical=False):
    """Apply geo2stl projection to a 2-D array.

    For categorical arrays (ESA class IDs) uses nearest-neighbour interpolation
    with fill_value=0 (preserves integer class IDs, clip_nans disabled).
    For continuous arrays (DEM, water mask, hydrology) uses bilinear with
    fill_value=NaN so that clip_nans can detect and strip projected edges.
    """
    from geo2stl.projections import project_coordinates

    projected, _meta = project_coordinates(
        arr, (north, south, east, west),
        projection=projection,
        maintain_dimensions=True,
        fill_value=0 if categorical else np.nan,
        clip_nans=clip_nans if not categorical else False,
        # Nearest-neighbour for categorical data (ESA class IDs) preserves
        # integer values.  Bilinear would blend adjacent class IDs (e.g.
        # class 10 + class 20 → 15), producing meaningless intermediate values.
        order=0 if categorical else 1,
    )
    return projected


def project_water_arrays(water_mask, esa_img, north, south, east, west,
                         projection, clip_nans):
    """Project both water mask and ESA arrays to keep them aligned.

    Both arrays are projected with clip_nans=False first, then the NaN mask
    from the water-mask projection is used to clip both arrays identically.
    This guarantees they always have the same output dimensions.
    """
    from geo2stl.projections import project_coordinates

    # Project water mask (continuous → NaN fill, no clip yet)
    wm_proj, _wm_meta = project_coordinates(
        water_mask, (north, south, east, west),
        projection=projection, maintain_dimensions=True,
        fill_value=np.nan, clip_nans=False,
    )
    # ESA class labels are categorical — nearest-neighbour (order=0), 0 fill
    esa_proj, _esa_meta = project_coordinates(
        esa_img.astype(np.float32), (north, south, east, west),
        projection=projection, maintain_dimensions=True,
        fill_value=0, clip_nans=False,
        order=0,
    )

    # Clip both arrays identically using the NaN pattern from the water mask
    if clip_nans and wm_proj.ndim == 2:
        col_has_data = ~np.all(np.isnan(wm_proj), axis=0)
        if col_has_data.any():
            wm_proj = wm_proj[:, col_has_data]
            esa_proj = esa_proj[:, col_has_data]
        row_has_data = ~np.all(np.isnan(wm_proj), axis=1)
        if row_has_data.any():
            wm_proj = wm_proj[row_has_data, :]
            esa_proj = esa_proj[row_has_data, :]

    # Re-threshold water mask after interpolation (bilinear can produce fractional);
    # also converts remaining NaN fill to 0.0 (NaN > 0.5 → False → 0.0).
    wm_proj = (wm_proj > 0.5).astype(np.float32)
    return wm_proj, esa_proj


def project_rgb_image(img_arr, north, south, east, west, projection, clip_nans):
    """Project an RGB image (H×W×3 uint8) channel-by-channel.

    Each channel is projected as continuous data with bilinear interpolation.
    NaN-filled border pixels are set to 0 (black) in the output.
    Returns the projected image as uint8.
    """
    from geo2stl.projections import project_coordinates

    h, w = img_arr.shape[:2]
    channels = []
    nan_mask = None
    for c in range(img_arr.shape[2]):
        ch = img_arr[:, :, c].astype(np.float32)
        projected, _meta = project_coordinates(
            ch, (north, south, east, west),
            projection=projection,
            maintain_dimensions=True,
            fill_value=np.nan,
            clip_nans=False,
        )
        if nan_mask is None:
            nan_mask = np.isnan(projected)
        projected = np.nan_to_num(projected, nan=0.0)
        channels.append(projected)

    result = np.stack(channels, axis=-1)

    # Clip NaN borders if requested
    if clip_nans and nan_mask is not None and nan_mask.ndim == 2:
        col_has_data = ~np.all(nan_mask, axis=0)
        if col_has_data.any():
            result = result[:, col_has_data, :]
        row_has_data = ~np.all(nan_mask[:, col_has_data] if col_has_data.any() else nan_mask, axis=1)
        if row_has_data.any():
            result = result[row_has_data, :, :]

    return np.clip(result, 0, 255).astype(np.uint8)
