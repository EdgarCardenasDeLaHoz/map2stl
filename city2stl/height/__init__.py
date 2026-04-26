"""
city2stl/height — Building height data types and computational pipelines.

Provides:
  - HeightResult      dataclass for raster height data
  - HeightProvider    protocol that all sources implement
  - merge_height_rasters()  priority-based merge of multiple HeightResults
  - predict()         CNN-based height prediction (Depth Anything V2 / U-Net)
  - train()           U-Net training loop
  - infill_idw()      heightmap inpainting via inverse-distance weighting
  - infill_nearest()  fast nearest-neighbour heightmap infill
  - stl_to_heightmap() convert georeferenced STL to a 2-D heightmap

This package has no dependency on app.server.  It can be used directly from
notebooks, scripts, or the session SDK without starting the FastAPI server.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class HeightResult:
    """Raster building-height output from a single provider.

    Attributes:
        raster:       (H, W) float32 array -- metres above ground. NaN = unknown.
        confidence:   (H, W) float32 array -- [0, 1]. Higher = more trustworthy.
        source_name:  Human-readable provider name (e.g. "ndsm", "wsf3d").
        resolution_m: Native resolution in metres per pixel.
    """
    raster: np.ndarray       # (H, W) float32, metres, NaN = unknown
    confidence: np.ndarray   # (H, W) float32, [0, 1]
    source_name: str
    resolution_m: float

    def __post_init__(self) -> None:
        if self.raster.shape != self.confidence.shape:
            raise ValueError(
                f"raster shape {self.raster.shape} != confidence shape "
                f"{self.confidence.shape}"
            )
        self.raster = self.raster.astype(np.float32)
        self.confidence = self.confidence.astype(np.float32)


# -- bbox type alias ---------------------------------------------------------
# (north, south, east, west) matching the rest of the codebase
BBox = Tuple[float, float, float, float]


class HeightProvider(Protocol):
    """Interface every height-data source must satisfy."""

    name: str

    def covers(self, bbox: BBox) -> bool:
        """Return True if this provider can serve data for *bbox*."""
        ...

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        """Fetch height raster for *bbox* at target resolution *dim* = (H, W).

        Must return a HeightResult with arrays shaped (H, W).
        Pixels with no data must be NaN (not 0).
        """
        ...


# -- Priority-based merge ----------------------------------------------------

def _resample(arr: np.ndarray, target_shape: Tuple[int, int]) -> np.ndarray:
    """Bilinear-ish resample *arr* to *target_shape* using numpy only.

    NaN pixels are excluded from the interpolation via a weighted approach:
    resize the valid-data mask alongside the values, then divide.
    """
    if arr.shape == target_shape:
        return arr

    th, tw = target_shape
    sh, sw = arr.shape

    # Row / col indices in source space
    row_idx = np.linspace(0, sh - 1, th)
    col_idx = np.linspace(0, sw - 1, tw)

    # Integer and fractional parts
    r0 = np.floor(row_idx).astype(int)
    c0 = np.floor(col_idx).astype(int)
    r1 = np.minimum(r0 + 1, sh - 1)
    c1 = np.minimum(c0 + 1, sw - 1)
    dr = (row_idx - r0).reshape(-1, 1)
    dc = (col_idx - c0).reshape(1, -1)

    # Replace NaN with 0 for weighted sum; track valid mask
    valid = (~np.isnan(arr)).astype(np.float32)
    safe = np.where(np.isnan(arr), 0.0, arr)

    w00 = (1 - dr) * (1 - dc)
    w01 = (1 - dr) * dc
    w10 = dr * (1 - dc)
    w11 = dr * dc

    val = (w00 * safe[np.ix_(r0, c0)] +
           w01 * safe[np.ix_(r0, c1)] +
           w10 * safe[np.ix_(r1, c0)] +
           w11 * safe[np.ix_(r1, c1)])

    wt = (w00 * valid[np.ix_(r0, c0)] +
          w01 * valid[np.ix_(r0, c1)] +
          w10 * valid[np.ix_(r1, c0)] +
          w11 * valid[np.ix_(r1, c1)])

    with np.errstate(invalid="ignore"):
        out = np.where(wt > 0, val / wt, np.nan)
    return out.astype(np.float32)


def merge_height_rasters(
    results: Sequence[HeightResult],
    target_shape: Tuple[int, int] | None = None,
) -> HeightResult:
    """Merge multiple HeightResults by confidence-weighted priority.

    Higher-confidence pixels always win.  If two sources have equal
    confidence, the one appearing *first* in *results* wins.

    Each result is resampled to *target_shape* (H, W) before merging.
    If *target_shape* is None the shape of the first result is used.

    Returns a single HeightResult with source_name="merged".
    """
    if not results:
        raise ValueError("merge_height_rasters() requires at least one result")

    if target_shape is None:
        target_shape = results[0].raster.shape

    th, tw = target_shape
    merged_raster = np.full((th, tw), np.nan, dtype=np.float32)
    merged_conf = np.zeros((th, tw), dtype=np.float32)

    for hr in results:
        raster = _resample(hr.raster, target_shape)
        conf = _resample(hr.confidence, target_shape)

        # Pixels where this source has data AND higher confidence
        has_data = ~np.isnan(raster)
        better = conf > merged_conf

        fill_mask = has_data & (better | np.isnan(merged_raster))
        merged_raster[fill_mask] = raster[fill_mask]
        merged_conf[fill_mask] = conf[fill_mask]

    best_res = min(r.resolution_m for r in results)
    return HeightResult(
        raster=merged_raster,
        confidence=merged_conf,
        source_name="merged",
        resolution_m=best_res,
    )
