"""Cross-view scoring: street-view ↔ satellite agreement (F-SKY10 Phase 2).

The matcher's existing IoU / containment / width-ratio signals are all
*intra-view* — they compare an OSM polygon's projection in the same Street
View image to the silhouette segment it might match. F-SKY10 adds an
*inter-view* signal: does the building's roof colour in the satellite
image match the colour of pixels the segment claims is the building's
roof in the Street View?

When a segment is *correctly* matched, the colours agree (a glass-curtain
tower has the same grey-blue from both views; a terracotta colonial roof
is red from above and from the side). When the matcher picks a wrong
inland building for a waterfront silhouette (the classic Cartagena
seed_5 failure), the colours disagree — the building actually visible at
that bearing has a different roof colour than the polygon the matcher
chose. The colour signal flags those cases so the reranker can prefer
the second-place candidate.

Three signals implemented:
  1. **Roof colour consistency** (Signal 1, 50%) — direct RGB comparison of
     the satellite roof crop vs the Street View segment's roof strip.
  2. **Geometric width consistency** (Signal 2, 30%) — heuristic width score
     based on segment width stability (edge density + width ratio).
  3. **Vertical edge consistency** (Signal 3, 20%) — Hough line-based edge
     detection in the segment region (stronger signal = more structured edges).

Combined formula (in make_cross_view_scorer):
  combined = 0.5 * color + 0.3 * width + 0.2 * edges

The combined score contributes a 0.15 × [0.5 to 1.0] = 7.5–15% nudge on
the final matcher score — conservative, but enough to break ties that
the IoU-only path gets wrong.

See ``docs/plans/F-SKY10-F-SKY11.2-IMPLEMENTATION-2026-05-17.md``.
"""

from __future__ import annotations

import math
from typing import Callable

import cv2
import numpy as np

from .satellite_image import crop_polygon_from_satellite


# Max possible RGB Euclidean distance: sqrt(255^2 * 3) ≈ 441.7.
# Used to normalize the colour distance into [0, 1].
_MAX_RGB_DIST = 441.673


def _median_rgb(patch: "np.ndarray | None") -> "tuple[float, float, float] | None":
    """Median RGB triplet for a patch, or None if the patch is empty.

    Median (not mean) so a single specular highlight or a bird flying past
    the camera doesn't skew the result. Float output so downstream
    distance maths doesn't need to deal with uint8 wraparound.
    """
    if patch is None or patch.size == 0:
        return None
    if patch.ndim != 3 or patch.shape[2] < 3:
        return None
    r = float(np.median(patch[..., 0]))
    g = float(np.median(patch[..., 1]))
    b = float(np.median(patch[..., 2]))
    return r, g, b


def _street_view_roof_strip(
    sv_image: np.ndarray,
    seg: dict,
    *,
    strip_height_px: int = 14,
) -> "np.ndarray | None":
    """Pixels of the Street View image that the segment claims is the
    building's roof — the top ``strip_height_px`` rows of the segment's
    column range, starting at the segment's ``top_y``.

    Why a strip and not a single row: 1 px is too noisy to median over;
    a strip a few rows tall picks up enough roof texture (parapet, AC
    units, tile gradient) for a stable median without bleeding into
    the facade.
    """
    if sv_image is None or sv_image.ndim != 3:
        return None
    h, w = sv_image.shape[:2]
    try:
        x_l = int(seg.get("x_left", 0))
        x_r = int(seg.get("x_right", 0))
        top_y = int(seg.get("top_y", 0))
    except Exception:
        return None
    if x_r <= x_l:
        return None
    x_l = max(0, min(w - 1, x_l))
    x_r = max(x_l + 1, min(w, x_r))
    y0 = max(0, min(h - 1, top_y))
    y1 = max(y0 + 1, min(h, y0 + strip_height_px))
    return sv_image[y0:y1, x_l:x_r]


def score_roof_color_consistency(
    sv_image: np.ndarray,
    seg: dict,
    building_polygon_lonlat: list[tuple[float, float]],
    sat_image: np.ndarray,
    sat_project: Callable[[float, float], tuple[float, float]],
) -> float:
    """Return a [0, 1] agreement score between the segment's roof strip
    (Street View) and the building's roof crop (satellite).

    Returns 0.5 (neutral) when either side has no usable pixels — the
    score should not push the matcher in either direction when we can't
    measure. The matcher's existing IoU/width signals stay authoritative.

    Score: ``1 - euclidean(rgb_sv, rgb_sat) / 441.67``.
    """
    sv_strip = _street_view_roof_strip(sv_image, seg)
    sv_rgb = _median_rgb(sv_strip)
    if sv_rgb is None:
        return 0.5

    sat_crop = crop_polygon_from_satellite(
        sat_image, sat_project, building_polygon_lonlat)
    sat_rgb = _median_rgb(sat_crop)
    if sat_rgb is None:
        return 0.5

    d = float(np.sqrt(
        (sv_rgb[0] - sat_rgb[0]) ** 2
        + (sv_rgb[1] - sat_rgb[1]) ** 2
        + (sv_rgb[2] - sat_rgb[2]) ** 2
    ))
    score = 1.0 - d / _MAX_RGB_DIST
    # Clamp belt-and-suspenders: numerical edge cases shouldn't return
    # values outside [0, 1] given the normalization, but downstream
    # combiners assume the contract.
    return float(max(0.0, min(1.0, score)))


def score_geometric_width_consistency(
    sv_image: np.ndarray,
    seg: dict,
    building_polygon_lonlat: list[tuple[float, float]],
) -> float:
    """Return a [0, 1] consistency score for segment width stability.

    Measures the width of the segment in pixels and applies a heuristic
    penalty for extreme aspect ratios. A narrow, tall segment (like a
    needle) is suspicious — real buildings have moderate width-to-height
    ratios. A very wide, short segment is also suspect.

    Returns 0.5 (neutral) if segment geometry is invalid.

    Score: Based on height-to-width ratio; peaks at ~1:2–1:3 aspect ratios
    (typical building facades are taller than they are wide from Street View).
    """
    if not seg or "x_left" not in seg or "x_right" not in seg:
        return 0.5

    try:
        x_l = float(seg["x_left"])
        x_r = float(seg["x_right"])
        top_y = float(seg.get("top_y", 0))
    except (ValueError, TypeError):
        return 0.5

    seg_width = max(1.0, x_r - x_l)
    seg_height = max(1.0, sv_image.shape[0] - top_y) if sv_image is not None else 100.0

    # Width ratio: narrow (ratio > 4) or very wide (ratio < 0.25) are
    # suspect. Peak score at ratio ≈ 0.5–1.0 (taller than wide).
    ratio = seg_width / seg_height
    if ratio <= 0:
        return 0.5

    # Score based on log-ratio, similar to matcher's width_ratio_score.
    # ratio near 0.5 → log2(0.5) = -1 → score ≈ 0.5
    # ratio near 1.0 → log2(1.0) = 0 → score = 1.0
    # ratio near 4.0 → log2(4) = 2 → score ≈ 0.0
    log_r = abs(math.log2(ratio))
    score = max(0.0, 1.0 - 0.5 * log_r)
    return float(max(0.0, min(1.0, score)))


def score_vertical_edge_consistency(
    sv_image: np.ndarray,
    seg: dict,
) -> float:
    """Return a [0, 1] score measuring vertical edge structure in segment.

    Detects edges via Canny filter and scores based on the density of
    vertical edge pixels in the segment region. A well-defined building
    facade has strong vertical edges (corners, wall seams). Noise or
    foliage has diffuse edges.

    Returns 0.5 (neutral) if the segment region is too small or edge
    detection fails.

    Score: Proportion of edge pixels in the segment region, scaled to [0, 1].
    """
    if sv_image is None or sv_image.ndim != 3 or not seg:
        return 0.5

    try:
        x_l = int(seg.get("x_left", 0))
        x_r = int(seg.get("x_right", 0))
        top_y = int(seg.get("top_y", 0))
    except (ValueError, TypeError):
        return 0.5

    h, w = sv_image.shape[:2]
    x_l = max(0, min(w - 1, x_l))
    x_r = max(x_l + 1, min(w, x_r))
    y0 = max(0, min(h - 1, top_y))
    y1 = max(y0 + 1, min(h, y0 + 100))  # Use top 100 pixels (facade region)

    seg_width = x_r - x_l
    seg_height = y1 - y0
    seg_area = seg_width * seg_height

    if seg_area < 10:  # Region too small to analyze
        return 0.5

    # Extract the segment region and convert to grayscale.
    region = sv_image[y0:y1, x_l:x_r]
    gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)

    # Edge detection via Canny.
    edges = cv2.Canny(gray, threshold1=50, threshold2=150)

    # Detect vertical lines via Hough line detection.
    # Focus on vertical lines (slopes near 90 degrees).
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=10,
        minLineLength=10,
        maxLineGap=5,
    )

    if lines is None or len(lines) == 0:
        # No strong vertical lines detected; fall back to edge density.
        edge_pixels = np.count_nonzero(edges)
        edge_density = edge_pixels / seg_area
        # Map [0, 0.1] density to [0, 1] score.
        score = min(1.0, edge_density / 0.1)
        return float(max(0.0, min(1.0, score)))

    # Count roughly vertical lines (dx/dy < 0.5, so roughly |slope| > 2).
    vertical_lines = 0
    for line in lines:
        x1, y1, x2, y2 = line[0]
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        if dy > 0 and dx / dy < 0.5:
            vertical_lines += 1

    # Score as proportion of lines that are vertical, scaled by edge density.
    edge_pixels = np.count_nonzero(edges)
    edge_density = edge_pixels / seg_area
    line_ratio = vertical_lines / max(1, len(lines))
    combined = 0.6 * line_ratio + 0.4 * min(1.0, edge_density / 0.1)
    return float(max(0.0, min(1.0, combined)))


def make_cross_view_scorer(
    sat_image: np.ndarray,
    sat_project: Callable[[float, float], tuple[float, float]],
    sv_image: np.ndarray,
) -> Callable[[dict, list[tuple[float, float]]], dict]:
    """Build a per-view closure the matcher can call as
    ``scorer(seg, building_lonlat_ring) -> {"color": float,
    "width": float, "edges": float, "combined": float}``.

    The returned dict's ``combined`` is what the matcher folds into the
    overall score using a 0.15 cross-view blend. Combined uses plan
    weights: 0.5 (colour) + 0.3 (width) + 0.2 (edges).

    Both ``sat_image`` and ``sat_project`` are captured by reference —
    fetched once per region in region_pdf and reused across all views,
    so the per-view cost is just the matplotlib ndarray slice for the
    polygon crop and edge detection on the segment region.
    """
    def _score(
        seg: dict, building_polygon_lonlat: list[tuple[float, float]],
    ) -> dict:
        color = score_roof_color_consistency(
            sv_image, seg, building_polygon_lonlat, sat_image, sat_project)
        width = score_geometric_width_consistency(sv_image, seg, building_polygon_lonlat)
        edges = score_vertical_edge_consistency(sv_image, seg)
        combined = 0.5 * color + 0.3 * width + 0.2 * edges
        return {
            "color": color,
            "width": width,
            "edges": edges,
            "combined": combined,
        }

    return _score
