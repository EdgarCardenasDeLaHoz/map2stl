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

Three signals from the plan:
  1. **Roof colour consistency** (this module) — direct RGB comparison of
     the satellite roof crop vs the Street View segment's roof strip.
  2. **Geometric width consistency** — partially covered by the matcher's
     existing `_width_ratio_score`; F-SKY10's plan upgrades it to be
     polygon-shape-aware (defer to Phase 3).
  3. **Vertical edge consistency** — Hough lines vs projected polygon
     edges (defer to Phase 3 / future work).

Phase 2 ships Signal 1 only. The plan's combined formula gives it
0.5 weight inside the cross_view term, so a 0.15 × 0.5 = 7.5 % nudge on
the final matcher score — conservative, but enough to break ties that
the IoU-only path gets wrong.

See ``docs/plans/F-SKY10-non-ml-cross-view-registration.md``.
"""

from __future__ import annotations

from typing import Callable

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


def make_cross_view_scorer(
    sat_image: np.ndarray,
    sat_project: Callable[[float, float], tuple[float, float]],
    sv_image: np.ndarray,
) -> Callable[[dict, list[tuple[float, float]]], dict]:
    """Build a per-view closure the matcher can call as
    ``scorer(seg, building_lonlat_ring) -> {"color": float,
    "combined": float}``.

    The returned dict's ``combined`` is what Phase 3 folds into the
    matcher's overall score. Today combined == color since Signal 1 is
    the only one shipped; once Signals 2/3 land they're added here with
    plan weights (0.5/0.3/0.2).

    Both ``sat_image`` and ``sat_project`` are captured by reference —
    fetched once per region in region_pdf and reused across all views,
    so the per-view cost is just the matplotlib ndarray slice for the
    polygon crop.
    """
    def _score(
        seg: dict, building_polygon_lonlat: list[tuple[float, float]],
    ) -> dict:
        color = score_roof_color_consistency(
            sv_image, seg, building_polygon_lonlat, sat_image, sat_project)
        return {"color": color, "combined": color}

    return _score
