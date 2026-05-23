"""city2stl.skyline_cv.depth_estimation — Depth Anything V2 on Street View panos.

F-SKY12 Phase A — verifier only.

Reuses the Depth Anything V2 loader from ``city2stl/height/predict.py`` (already
a project dependency, already cached on disk). Calibrates relative depth to
metres using known OSM building anchor distances, then derives a per-match
height estimate that can be cross-checked against the geometric (pinhole-y)
estimate produced by the existing skyline_cv pipeline.

Three pure functions, all unit-testable:

  - ``predict_pano_depth(view_rgb)`` -> relative depth in [0, 1]
  - ``calibrate_pano_depth(depth_rel, anchors)`` -> metric depth (m)
  - ``depth_height_from_segment(depth_m, segment_top_xy, intrinsics, ...)``
    -> per-segment height (m)

A fourth helper, ``compare_heights``, returns the boolean
``depth_disagreement`` flag (relative-difference threshold).

Phase A does **not** modify aggregated heights. It only surfaces a second
signal. Phase B (future) will use it for confidence weighting and rescue.

See ``docs/plans/F-SKY12-depth-from-panos.md`` for the broader design.
"""

from __future__ import annotations

import logging
import math
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DA2 inference (delegates to the existing loader in city2stl/height/predict)
# ---------------------------------------------------------------------------

def predict_pano_depth(view_rgb: np.ndarray, device: str = "cpu") -> np.ndarray:
    """Run Depth Anything V2 on a Street View image and return a relative-depth map.

    Parameters
    ----------
    view_rgb : (H, W, 3) uint8 RGB array — a single spin-view crop.
    device   : "cpu" or a torch device string. Defaults to "cpu".

    Returns
    -------
    depth_rel : (H, W) float32 in [0, 1]. Closer objects have higher values
                (matches ``_depth_anything_inference`` in height/predict.py).
                Not yet calibrated to metres — use ``calibrate_pano_depth``.
    """
    # Delegate to the existing helper so we share the cache and the loader.
    from city2stl.height.predict import _depth_anything_inference  # noqa: PLC0415

    return _depth_anything_inference(view_rgb, device=device)


# ---------------------------------------------------------------------------
# Calibration: relative depth -> metres via known anchor(s)
# ---------------------------------------------------------------------------

def calibrate_pano_depth(
    depth_rel: np.ndarray,
    anchors: Sequence[tuple[int, int, float]],
) -> np.ndarray:
    """Convert a relative depth map to metric depth using known-distance anchors.

    Solves ``depth_m = a + b * (1 - depth_rel)`` because DA2 inverse-depth has
    "closer = higher", so ``(1 - depth_rel)`` is monotonically increasing with
    physical distance. With ≥ 2 anchors we fit a + b via least squares; with
    exactly 1 anchor we fix the scale only (a = 0, b chosen so the anchor
    point lands on its known distance).

    Parameters
    ----------
    depth_rel : (H, W) float32 in [0, 1] from ``predict_pano_depth``.
    anchors   : sequence of ``(row, col, distance_m)`` triples — pixel
                locations of OSM building footprints whose distance from the
                camera is known.

    Returns
    -------
    depth_m : (H, W) float32 — per-pixel metric depth in metres. Pixels far
              from any anchor extrapolate linearly and may be unreliable;
              callers should sample at footprint pixels and use the median.

    Raises
    ------
    ValueError if no anchors are provided.
    """
    if not anchors:
        raise ValueError("calibrate_pano_depth needs at least one anchor")

    h, w = depth_rel.shape
    # Sampled relative-depth values at anchor pixels (use 1 - depth_rel so it
    # increases with physical distance).
    samples = []
    for row, col, dist_m in anchors:
        if not (0 <= row < h and 0 <= col < w):
            continue
        d_rel = float(depth_rel[row, col])
        samples.append((1.0 - d_rel, dist_m))

    if not samples:
        raise ValueError("all anchors fell outside the depth map")

    inv = np.asarray([s[0] for s in samples], dtype=np.float32)
    dist = np.asarray([s[1] for s in samples], dtype=np.float32)

    if len(samples) == 1:
        # Scale-only fit: depth_m = b * (1 - depth_rel), b = dist / (1 - d_rel).
        b = float(dist[0] / max(inv[0], 1e-6))
        a = 0.0
    else:
        # Least-squares linear fit on the two-or-more anchors.
        coeffs = np.polyfit(inv, dist, 1)
        b, a = float(coeffs[0]), float(coeffs[1])

    depth_m = a + b * (1.0 - depth_rel.astype(np.float32))
    # Clip negatives (depth must be non-negative; DA2 occasionally extrapolates
    # past the anchor range).
    np.clip(depth_m, 0.0, None, out=depth_m)
    return depth_m


# ---------------------------------------------------------------------------
# Height extraction from depth + pinhole geometry
# ---------------------------------------------------------------------------

def depth_height_from_segment(
    depth_m: np.ndarray,
    segment_top_xy: tuple[int, int],
    fy: float,
    cy: float,
    camera_height_m: float = 2.5,
) -> float:
    """Derive a building's absolute height from a metric depth map.

    Given the silhouette **top pixel** ``(x, y)`` of a building and the
    metric depth at that pixel, recover the physical height above the
    camera using the pinhole pitch:

        pitch  = atan((cy - y) / fy)
        height = camera_height_m + depth_m_at_pixel * tan(pitch)

    The camera_height_m default (2.5 m) matches Street View's typical
    capture rig elevation. Distance to the building is the metric depth at
    the silhouette-top pixel (we approximate that the top is at the same
    horizontal distance as the facade, which holds for tall narrow towers
    and is a small error for short squat blocks).

    Parameters
    ----------
    depth_m         : (H, W) float32 metric depth map from
                      ``calibrate_pano_depth``.
    segment_top_xy  : (col, row) pixel coordinates of the silhouette top.
    fy              : vertical focal length in pixels.
    cy              : vertical principal point in pixels (usually H/2).
    camera_height_m : Street View camera elevation above ground (m).

    Returns
    -------
    height_m : float — estimated total building height above ground (m).
               Returns 0.0 if the top is at or below the horizon (cy);
               negative depths or NaN inputs are guarded.
    """
    x, y = segment_top_xy
    h, w = depth_m.shape
    if not (0 <= x < w and 0 <= y < h):
        return 0.0

    dy = float(cy - y)
    if dy <= 0.0:
        # Top pixel is at or below the principal point — no positive pitch.
        return 0.0

    d_m = float(depth_m[y, x])
    if not math.isfinite(d_m) or d_m <= 0.0:
        return 0.0

    pitch = math.atan(dy / float(fy))
    return float(camera_height_m + d_m * math.tan(pitch))


# ---------------------------------------------------------------------------
# Disagreement flag
# ---------------------------------------------------------------------------

# Phase A default: flag when relative difference > 40%. Threshold lives here so
# the per-region PDF report can quote it.
DEPTH_DISAGREEMENT_THRESHOLD: float = 0.40


def compare_heights(
    h_geometric: float,
    h_depth: float,
    threshold: float = DEPTH_DISAGREEMENT_THRESHOLD,
) -> bool:
    """Return True when the two height estimates disagree by > ``threshold``.

    The relative-difference metric is symmetric:

        rel_diff = |h_geom - h_depth| / max(h_geom, h_depth)

    Returns False when either estimate is non-positive (no valid signal to
    compare), so this flag fires only when both are real positive heights
    that disagree.
    """
    if h_geometric <= 0.0 or h_depth <= 0.0:
        return False
    denom = max(h_geometric, h_depth)
    if denom <= 0.0:
        return False
    return abs(h_geometric - h_depth) / denom > threshold
