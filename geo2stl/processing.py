"""
geo2stl/processing.py — Pure raster processing operations for DEM/layer pipelines.

No HTTP, cache, or server dependencies.  Used by app.server.core.dem which
re-imports and re-exports these three functions directly (no separate shim file).

-- Relationship to dem.py --
The three functions here were extracted from app.server.core.dem.  After the
migration, dem.py replaces its function bodies with:
    from geo2stl.processing import apply_layer_processing, blend_layers, upsample_dem
Those names are still importable at app.server.core.dem.* so routers keep working.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    # ProcessingSpec is a Pydantic model defined in app.server.schemas.
    # We reference it only in type annotations (never at runtime) to avoid
    # creating a dependency from this library package onto the server layer.
    from app.server.schemas import ProcessingSpec  # noqa: F401

logger = logging.getLogger(__name__)


def apply_layer_processing(arr: np.ndarray, spec: "ProcessingSpec") -> np.ndarray:
    """
    Apply the processing pipeline defined in *spec* to a 2-D float64 array.

    Operations (in order): clip -> smooth -> sharpen -> extract_rivers -> normalize -> invert

    ``spec`` is duck-typed: any object with the attributes
    ``clip_min``, ``clip_max``, ``smooth_sigma``, ``sharpen``,
    ``extract_rivers``, ``river_max_width_px``, ``normalize``, ``invert``
    will work, including the Pydantic model from app.server.schemas.
    """
    import cv2 as _cv2  # noqa: F401 (imported for potential future use)
    from scipy import ndimage as ndi

    out = arr.astype(np.float64)

    if spec.clip_min is not None or spec.clip_max is not None:
        lo = spec.clip_min if spec.clip_min is not None else out.min()
        hi = spec.clip_max if spec.clip_max is not None else out.max()
        out = np.clip(out, lo, hi)

    if spec.smooth_sigma > 0:
        out = ndi.gaussian_filter(out, sigma=spec.smooth_sigma)

    if spec.sharpen:
        blurred = ndi.gaussian_filter(out, sigma=1.5)
        out = out + 0.5 * (out - blurred)

    if spec.extract_rivers:
        binary = out > 0.5
        r = spec.river_max_width_px
        half = max(1, r // 2)
        struct = np.ones((half * 2 + 1, half * 2 + 1), dtype=bool)
        large_bodies = ndi.binary_opening(binary, structure=struct)
        out = (binary & ~large_bodies).astype(np.float64)

    if spec.normalize:
        lo, hi = out.min(), out.max()
        if hi > lo:
            out = (out - lo) / (hi - lo)

    if spec.invert:
        lo, hi = out.min(), out.max()
        out = hi - out + lo

    return out


def blend_layers(
    base: np.ndarray,
    layer: np.ndarray,
    blend_mode: str,
    weight: float,
    output_shape: tuple,
) -> np.ndarray:
    """Blend *layer* onto *base* using the specified mode.

    Valid modes: ``base``, ``replace``, ``blend``, ``rivers``, ``max``, ``min``.
    """
    import cv2 as _cv2

    if layer.shape != base.shape:
        h, w = base.shape
        layer = _cv2.resize(layer.astype(np.float32), (w, h),
                            interpolation=_cv2.INTER_LINEAR).astype(np.float64)

    if blend_mode == "base":
        return base.copy()
    elif blend_mode == "replace":
        mask = layer != 0
        out = base.copy()
        out[mask] = layer[mask]
        return out
    elif blend_mode == "blend":
        return base * (1.0 - weight) + layer * weight
    elif blend_mode == "rivers":
        return base - layer * weight
    elif blend_mode == "max":
        return np.maximum(base, layer)
    elif blend_mode == "min":
        return np.minimum(base, layer)
    else:
        raise ValueError(
            f"Unknown blend_mode {blend_mode!r}. "
            "Valid modes: base, replace, blend, rivers, max, min"
        )


def upsample_dem(im: np.ndarray, dim: int) -> np.ndarray:
    """Upsample DEM if its native resolution is smaller than *dim*."""
    import cv2 as _cv2

    if dim and im is not None:
        h_nat, w_nat = im.shape[:2]
        if max(h_nat, w_nat) < dim:
            scale = float(dim) / float(max(h_nat, w_nat))
            new_w = max(1, int(round(w_nat * scale)))
            new_h = max(1, int(round(h_nat * scale)))
            logger.info(f"Upsampling DEM {w_nat}x{h_nat} -> {new_w}x{new_h}")
            im = _cv2.resize(im, (new_w, new_h), interpolation=_cv2.INTER_LINEAR)
    return im
