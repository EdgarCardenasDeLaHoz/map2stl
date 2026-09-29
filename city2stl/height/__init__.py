"""
city2stl/height — Building height data types and computational pipelines.

Defined here:
  - HeightResult      dataclass for raster height data
  - HeightProvider    protocol that all sources implement
  - provider_stats()  coverage / value summary of one HeightResult
  - resolution_priority(), BUILDING_RESOLUTION_LIMIT_M
                      cell-size weighting, and the coarsest source per-building
                      enhancement accepts
  - merge_height_rasters()  priority-based merge of multiple HeightResults,
                            ranking by confidence scaled for cell size

Submodules (import them directly; nothing is re-exported here):
  - infill            infill_idw() / infill_nearest() heightmap gap filling
  - stl_import        stl_to_heightmap(): georeferenced STL to a 2-D heightmap
  - predict, train    CNN height prediction and U-Net training (historical,
                      not used at runtime; see docs/history/ml-height/README.md)
  - providers/        one module per external height source
  - service           provider registry, selection and enhance_city_data()

This package has no dependency on app.server.  It can be used directly from
notebooks, scripts, or the session SDK without starting the FastAPI server.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

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

    @classmethod
    def empty(cls, dim: tuple[int, int], source_name: str,
              resolution_m: float) -> HeightResult:
        """All-NaN raster with zero confidence, shaped *dim* = (H, W)."""
        h, w = dim
        return cls(
            raster=np.full((h, w), np.nan, dtype=np.float32),
            confidence=np.zeros((h, w), dtype=np.float32),
            source_name=source_name,
            resolution_m=resolution_m,
        )


# -- bbox type alias ---------------------------------------------------------
# (north, south, east, west) matching the rest of the codebase
BBox = tuple[float, float, float, float]


@runtime_checkable
class HeightProvider(Protocol):
    """Interface every height-data source must satisfy."""

    name: str

    def covers(self, bbox: BBox) -> bool:
        """Return True if this provider can serve data for *bbox*."""
        ...

    def fetch_heights(self, bbox: BBox, dim: tuple[int, int]) -> HeightResult:
        """Fetch height raster for *bbox* at target resolution *dim* = (H, W).

        Must return a HeightResult with arrays shaped (H, W).
        Pixels with no data must be NaN (not 0).
        """
        ...


# -- Priority-based merge ----------------------------------------------------

def _resample(arr: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
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


def _filter_outliers(
    raster: np.ndarray,
    absolute_max_m: float = 600.0,
) -> np.ndarray:
    """Discard below-ground and physically impossible height pixels.

    Pixels below 0 m or above absolute_max_m are set to NaN. The cap exists
    for terrain-shadow and nodata artefacts, which land in the thousands of
    metres, not for tall buildings.

    This used to also apply an IQR fence at Q3 + 3 * IQR, which was wrong for
    a reason worth recording: the quartiles are computed over every pixel of
    the raster, and in any real city the overwhelming majority of pixels are
    ground at or near 0 m. The fence therefore tracked the ground, not the
    building distribution, and every tall building in the scene sat above it.
    Measured against OSM height tags, per footprint at the sampling percentile
    the pipeline actually uses:

        Miami      filtered MAE 45.61  corr +0.306  tallest est 147 m
                 unfiltered MAE 35.48  corr +0.662  tallest est 233 m  (tag max 228)
        Cartagena  filtered MAE 34.68  corr +0.345  tallest est  27 m
                 unfiltered MAE 20.09  corr +0.773  tallest est 188 m  (tag max 177)

    The fence cost accuracy on every metric in both cities and, in Cartagena,
    flattened a skyline of 150-200 m towers to 27 m. On that raster the fence
    landed at 22.9 m for GlobalBuildingAtlas and 0.1 m for the nDSM.

    Returns a copy; does not modify the input.
    """
    out = raster.copy()
    bad = (~np.isnan(out)) & ((out < 0.0) | (out > absolute_max_m))
    out[bad] = np.nan
    return out


def provider_stats(hr: HeightResult) -> dict:
    """Return a summary statistics dict for a single HeightResult.

    Useful for diagnostics endpoints and logging.
    """
    valid = hr.raster[~np.isnan(hr.raster)]
    n_valid = int(valid.size)
    n_total = int(hr.raster.size)
    return {
        "source": hr.source_name,
        "resolution_m": hr.resolution_m,
        "coverage_pct": round(n_valid / n_total * 100, 1) if n_total > 0 else 0.0,
        "valid_pixels": n_valid,
        "total_pixels": n_total,
        "min_m": float(np.nanmin(hr.raster)) if n_valid > 0 else None,
        "max_m": float(np.nanmax(hr.raster)) if n_valid > 0 else None,
        "mean_m": float(np.nanmean(hr.raster)) if n_valid > 0 else None,
        "p95_m": float(np.percentile(valid, 95)) if n_valid >= 10 else None,
    }


#: Resolution at or below which a source is treated as fully per-building.
#: Five metres is roughly the width of the narrowest building we care about,
#: so a cell that size is dominated by one roof rather than by the street.
BUILDING_SCALE_M = 5.0

#: Coarsest source that may be used to answer a *per-building* height question.
#: Ten metres keeps the dedicated building-height products (Overture at 5 m,
#: Copernicus EU at 10 m) and excludes the general-purpose elevation differences
#: (nDSM and 3DEP at 30 m, WSF3D at 90 m, GHSL at 100 m). Merging is not gated
#: by this -- a coarse source is still the best available answer for a whole-city
#: raster -- only the per-building enhancement in `enhance_city_data` is.
BUILDING_RESOLUTION_LIMIT_M = 10.0


def resolution_priority(resolution_m: float) -> float:
    """Multiplier applied to a provider's confidence for its cell size.

    A height raster answers "how tall is this building", and a cell coarser
    than a building cannot: it averages the roof with the streets, courtyards
    and gardens around it, which drags the value toward the ground. That is a
    property of the sampling, not of the source's reputation, so it belongs in
    the merge priority rather than in each provider's confidence constant.

    Measured: a 30 m nDSM over European cities reads a median of 2.0 to 2.4 m
    with 58 to 72 per cent of pixels below 3 m, while 5 m Overture footprints
    over the same cities read 9.4 to 24.0 m. The nDSM number is not a
    measurement of short buildings; it is a measurement of mostly-not-building.

    The penalty is the square root of the cell-size ratio rather than the ratio
    itself, which demotes a coarse source without erasing it -- a 100 m source
    is still better than no data where nothing else covers the pixel.
    """
    return float(min(1.0, (BUILDING_SCALE_M / max(resolution_m, 1e-6)) ** 0.5))


def merge_height_rasters(
    results: Sequence[HeightResult],
    target_shape: tuple[int, int] | None = None,
    filter_outliers: bool = True,
) -> HeightResult:
    """Merge multiple HeightResults by resolution-weighted confidence priority.

    A pixel goes to whichever source has the highest ``confidence`` scaled by
    :func:`resolution_priority`, so a nominally more trustworthy source does
    not win a per-building question it cannot resolve.  Sources at or finer
    than ``BUILDING_SCALE_M`` are unpenalised and rank by confidence alone.  If
    two sources tie, the one appearing *first* in *results* wins.

    The emitted ``confidence`` raster is the winning source's own confidence,
    unscaled.  Downstream gates such as ``enhance_buildings_with_raster``'s
    ``min_confidence`` therefore keep the meaning they were tuned against;
    resolution decides who wins a pixel, not whether the pixel is usable.

    Each result is resampled to *target_shape* (H, W) before merging.
    If *target_shape* is None the shape of the first result is used.

    When *filter_outliers* is True (default), each provider's raster is
    IQR-clamped before merging to remove terrain-shadow and nodata artefacts.

    Returns a single HeightResult with source_name="merged".
    """
    if not results:
        raise ValueError("merge_height_rasters() requires at least one result")

    if target_shape is None:
        target_shape = results[0].raster.shape

    th, tw = target_shape
    merged_raster = np.full((th, tw), np.nan, dtype=np.float32)
    merged_conf = np.zeros((th, tw), dtype=np.float32)
    merged_prio = np.zeros((th, tw), dtype=np.float32)

    for hr in results:
        raster = _resample(hr.raster, target_shape)
        if filter_outliers:
            raster = _filter_outliers(raster)
        conf = _resample(hr.confidence, target_shape)
        # Zero out confidence where raster was NaN'd by outlier filter
        conf = np.where(np.isnan(raster), 0.0, conf).astype(np.float32)
        prio = (conf * resolution_priority(hr.resolution_m)).astype(np.float32)

        # Pixels where this source has data AND higher priority
        has_data = ~np.isnan(raster)
        better = prio > merged_prio

        fill_mask = has_data & (better | np.isnan(merged_raster))
        merged_raster[fill_mask] = raster[fill_mask]
        merged_conf[fill_mask] = conf[fill_mask]
        merged_prio[fill_mask] = prio[fill_mask]

    best_res = min(r.resolution_m for r in results)
    return HeightResult(
        raster=merged_raster,
        confidence=merged_conf,
        source_name="merged",
        resolution_m=best_res,
    )
