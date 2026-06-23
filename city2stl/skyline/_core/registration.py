"""skyline._core.registration — extracted from pipeline.py (A1 split)."""
from __future__ import annotations
from collections import OrderedDict as _OrderedDict

import logging
import math
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks
from shapely.geometry import shape

# F-CLEAN14: the F-SKY12 depth except-branches reference ``logger`` but the
# module never defined one (latent NameError, only reachable on a depth-module
# failure). Defined here so those branches log instead of crashing.
logger = logging.getLogger(__name__)

from .types import BuildingRecord, Viewpoint, CapturedView
from .projection import _project_all_buildings_vectorized, _projected_building_x_ranges
from .segmentation import (_neural_sky_and_building_masks, _neural_water_mask,
                           _mobilesam_available, _get_mobilesam_predictor)
from .skyline import detect_skyline_contour

def _proj_x_range(p: dict) -> tuple[float, float]:
    """Return (left, right) pixel x-bounds of a projection dict.

    Falls back to ±10 px around ``x_px`` when explicit ``x_left_px`` /
    ``x_right_px`` aren't set. Swaps if the caller emitted them out of
    order. Shared by ``osm_anchor_silhouettes`` and
    ``match_segments_to_buildings`` — they used to have private duplicates
    that drifted independently.
    """
    pL = float(p.get("x_left_px", float(p["x_px"]) - 10.0))
    pR = float(p.get("x_right_px", float(p["x_px"]) + 10.0))
    return (pL, pR) if pL <= pR else (pR, pL)

def _proj_containment_in_seg(seg_L: float, seg_R: float, p: dict) -> float:
    """Fraction of projection p's x-range that lies inside [seg_L, seg_R].

    Companion to ``_proj_x_range``. Used as the "is this projection a
    candidate for this segment" metric by F-SKY2, F-SKY3, and the
    matcher's containment fallback — they previously each inlined the
    same arithmetic.
    """
    pL, pR = _proj_x_range(p)
    inter = max(0.0, min(seg_R, pR) - max(seg_L, pL))
    proj_w = max(1.0, pR - pL)
    return inter / proj_w

def osm_anchor_silhouettes(
    segments: list[dict],
    projections: list[dict],
    *,
    building_mask: "np.ndarray | None" = None,
    min_proj_containment: float = 0.5,
    min_gap_px: int = 2,
    min_child_width_px: int = 4,
    min_center_separation_px: int = 14,
) -> list[dict]:
    """Split mask-merged silhouettes using OSM footprint projections as
    structural anchors (F-SKY2).

    SegFormer routinely merges adjacent buildings in dense skylines —
    three glass towers on Cartagena's Bocagrande share one contiguous
    building-mask blob and the OSM-blind silhouette detector emits a
    single wide segment. The matcher then has 1 segment to assign to
    3 buildings; two get silently dropped.

    For each input segment we find the OSM projections whose x-range
    is at least ``min_proj_containment`` fraction *inside* the segment.
    (Interval-IoU is the wrong metric here — N narrow projections
    inside one wide segment all have small IoU because the denominator
    is dominated by the segment width; containment captures "this
    projection mostly lives inside this segment" regardless of
    relative widths.) When 2+ projections qualify AND there is a
    ``min_gap_px``-wide gap between adjacent OSM x-ranges inside the
    segment, the segment is split at that gap. The split column is
    refined to the building-mask column with the lowest coverage
    inside the gap when ``building_mask`` is provided — this snaps the
    cut to where the mask itself is thinnest (the actual inter-building
    separator), not just the OSM midpoint.

    A segment that has 0 or 1 overlapping projections, or whose
    overlapping projections have no qualifying gap, passes through
    unchanged. Children inherit y bounds from the parent (refining
    per-child top/base is a follow-up).

    Calling with empty ``segments`` or empty ``projections`` returns
    the input unchanged — the function is a no-op when registration
    failed and there are no anchors to apply.

    See ``docs/plans/F-SKY2-osm-anchored-segments.md``.
    """
    if not segments or not projections:
        return list(segments)

    out: list[dict] = []
    for seg in segments:
        sL = float(seg["x_left"])
        sR = float(seg["x_right"])
        if sR <= sL:
            out.append(seg)
            continue

        overlapping: list[tuple[float, float, float, dict]] = []
        for p in projections:
            if _proj_containment_in_seg(sL, sR, p) >= min_proj_containment:
                pL, pR = _proj_x_range(p)
                overlapping.append((float(p["x_px"]), pL, pR, p))

        if len(overlapping) < 2:
            out.append(seg)
            continue

        overlapping.sort(key=lambda t: t[0])

        split_cols: list[float] = []
        for i in range(len(overlapping) - 1):
            this_xpx, _, this_R, _ = overlapping[i]
            next_xpx, next_L, _, _ = overlapping[i + 1]
            # Always cut between two distinct OSM building centres as long
            # as their projection centres are far enough apart to leave
            # workable children. The previous gap-only rule failed on
            # adjacent waterfront towers whose footprints just touched —
            # zero gap, zero cut, one mega-segment swallowing both. Now
            # the cut fires on centre separation; the mask-thinnest
            # refinement still snaps to a real valley when one exists.
            if next_xpx - this_xpx < min_center_separation_px:
                continue
            gap_w = next_L - this_R
            if gap_w >= min_gap_px:
                # Real valley between footprints — cut at gap midpoint.
                mid = 0.5 * (this_R + next_L)
            else:
                # Footprints abut or overlap; cut between the projection
                # centres themselves and let mask refinement find the
                # nearest valley.
                mid = 0.5 * (this_xpx + next_xpx)
            if mid <= sL + 2 or mid >= sR - 2:
                continue
            # Refine: snap split column to the building-mask column with
            # lowest coverage within a window around the candidate cut.
            # Generous window so we can find a thin valley even when the
            # initial midpoint sits inside a tall column.
            if building_mask is not None:
                search_half = max(8, int(0.5 * (next_xpx - this_xpx)))
                wL = max(int(mid - search_half), int(sL))
                wR = min(int(mid + search_half), int(sR))
                if wR > wL:
                    col_cov = building_mask[:, wL:wR + 1].sum(axis=0)
                    mid = float(wL + int(np.argmin(col_cov)))
            split_cols.append(mid)

        if not split_cols:
            out.append(seg)
            continue

        cuts = [sL] + sorted(split_cols) + [sR]
        for i in range(len(cuts) - 1):
            cL = cuts[i]
            cR = cuts[i + 1]
            if cR - cL < min_child_width_px:
                continue
            # peak_x: prefer the OSM x_px that falls inside this child.
            mid_x = int(round(0.5 * (cL + cR)))
            child_peak = mid_x
            for px, _, _, _ in overlapping:
                if cL <= px <= cR:
                    child_peak = int(round(px))
                    break
            child = dict(seg)
            child["x_left"] = float(cL)
            child["x_right"] = float(cR)
            child["mid_x"] = mid_x
            child["peak_x"] = child_peak
            child["osm_anchored"] = True
            out.append(child)

    return sorted(out, key=lambda s: s["mid_x"])

def osm_sam_instance_silhouettes(
    image_rgb: np.ndarray,
    segments: list[dict],
    projections: list[dict],
    *,
    building_mask: "np.ndarray | None" = None,
    min_proj_containment: float = 0.5,
    min_marker_separation_px: int = 20,
    confidence_floor: float = 0.65,
    min_child_width_px: int = 4,
) -> list[dict]:
    """Split merged segments using MobileSAM prompted by OSM centroids (F-SKY5).

    Replaces F-SKY3's heuristic Voronoi split with a learned instance-
    segmentation model. Fires only on segments that contain ≥ 2 OSM marker
    projections (the cases where F-SKY2 gap-based splitting and the matcher's
    containment fallback did not already resolve the merge). When MobileSAM is
    not installed or the checkpoint is missing, returns ``segments`` unchanged
    (transparent no-op — the pipeline continues with F-SKY2 segments).

    For each qualifying merged segment:
    - Gather the OSM-projected centroids (x_px, mid_y) as SAM point prompts.
    - Run MobileSAM; discard masks below ``confidence_floor``.
    - Intersect each surviving mask with the SegFormer ``building_mask`` (when
      provided) so SAM predictions that leak into sky / water are clipped.
    - Bound children to the parent segment's [x_left, x_right] column range.
    - Collapse sibling masks whose peaks are within ``min_marker_separation_px``
      pixels (avoids over-segmenting a single tower into body + spire).
    - If 0 masks survive the floor, leave the original segment unchanged.

    See ``docs/plans/F-SKY5-mobilesam-instance.md``.
    """
    if not _mobilesam_available():
        return list(segments)
    if not segments or not projections:
        return list(segments)

    predictor = _get_mobilesam_predictor()
    h, w = image_rgb.shape[:2]
    mid_y = h // 2

    # Set image once for this call (MobileSAM caches the embedding internally).
    import torch  # noqa: PLC0415
    with torch.no_grad():
        predictor.set_image(image_rgb)

    out: list[dict] = []
    for seg in segments:
        sL = float(seg["x_left"])
        sR = float(seg["x_right"])

        # Collect OSM markers contained in this segment.
        markers: list[tuple[float, float]] = []
        for p in projections:
            if _proj_containment_in_seg(sL, sR, p) >= min_proj_containment:
                markers.append((float(p["x_px"]), float(mid_y)))

        if len(markers) < 2:
            out.append(seg)
            continue

        # Prompt SAM once per marker to get per-instance masks.
        # Batching all points together returns a single mask covering the union;
        # one point per call is the correct instance-segmentation pattern.
        surviving: list[tuple[float, np.ndarray]] = []
        for mx, my in markers:
            try:
                with torch.no_grad():
                    masks_arr, scores_arr, _ = predictor.predict(
                        point_coords=np.array([[mx, my]], dtype=np.float32),
                        point_labels=np.array([1], dtype=np.int32),
                        multimask_output=True,
                    )
            except Exception:
                continue
            # Pick the highest-confidence mask from the 3 multimask outputs.
            best_idx = int(np.argmax(scores_arr))
            best_score = float(scores_arr[best_idx])
            if best_score < confidence_floor:
                continue
            m = masks_arr[best_idx].astype(bool)
            if building_mask is not None:
                m = m & building_mask.astype(bool)
            m[:, :int(sL)] = False
            m[:, int(sR) + 1:] = False
            if not m.any():
                continue
            surviving.append((best_score, m))

        if not surviving:
            out.append(seg)
            continue

        # Sort by score descending; build non-overlapping child segments.
        surviving.sort(key=lambda t: t[0], reverse=True)

        children: list[dict] = []
        used_cols: set[int] = set()
        for _score, m in surviving:
            cols = np.where(m.any(axis=0))[0]
            if len(cols) == 0:
                continue
            cL, cR = float(cols[0]), float(cols[-1])
            if cR - cL < min_child_width_px:
                continue
            peak_x = int(round(0.5 * (cL + cR)))
            # Collapse: skip if a prior child's peak is within separation_px.
            if any(abs(peak_x - c["peak_x"]) < min_marker_separation_px
                   for c in children):
                continue
            if peak_x in used_cols:
                continue
            used_cols.update(range(int(cL), int(cR) + 1))
            child = dict(seg)
            child["x_left"] = cL
            child["x_right"] = cR
            child["mid_x"] = peak_x
            child["peak_x"] = peak_x
            child["sam_instance"] = True
            children.append(child)

        if not children:
            out.append(seg)
        else:
            out.extend(children)

    return sorted(out, key=lambda s: s["mid_x"])

def match_segments_to_buildings(
    segments: list[dict],
    projections: list[dict],
    buildings_by_id: dict[str, BuildingRecord],
    min_interval_iou: float = 0.10,
    cross_view_scorer: "callable | None" = None,
) -> list[dict]:
    """For each skyline segment, pick the best-matching projected building.

    Scoring (Phase B):
      base       = interval-IoU (geometric overlap, width-aware)
      width      = log-symmetric width-ratio score (0..1)
      occlusion  = penalty when a much-farther candidate's x-range is
                   contained in a much-nearer candidate's x-range — the
                   farther one is occluded and shouldn't win
      combined   = 0.55 * base + 0.30 * width + 0.15 * (1 - occlusion)

    Width penalty is now disqualifying when ratio > 4× (combined ×0.3)
    to prevent a long warehouse winning over a tower on weak IoU alone.

    Each output segment gets a `match_diagnostics` list with the top-3
    scored candidates so the user can audit flagged failures by reading
    one match against the alternatives the matcher considered.

    Tie-breaking (within the close-combined bucket):
      - Nearest building wins (smallest forward_m).
      - Kiosk-in-front-of-tower exception preserved: nearest < 5 m proxy
        + a tower within 1.5× the nearest distance → prefer the tower.

    F-SKY10 cross-view rerank
    -------------------------
    When ``cross_view_scorer`` is supplied (built by
    ``cross_view.make_cross_view_scorer``), each candidate's combined
    score is blended with a roof-colour-consistency score sampled from
    the per-region satellite image:

        final = 0.85 * combined_intra + 0.15 * cross_view_combined

    A wrong-building match (inland polygon credited for a waterfront
    silhouette) typically has poor cross-view colour agreement, so the
    rerank pushes the second-place candidate (the actually-visible
    building) into first place. The 0.15 weight is intentionally
    conservative — the plan calls for the intra-view IoU/width signal
    to remain dominant; cross-view is a tiebreaker, not an override.
    The per-candidate score lands in ``match_diagnostics[i]["cv"]`` so
    the audit page can show how it influenced the choice.
    """
    def _seg_width(seg: dict) -> float:
        return max(1.0, float(seg["x_right"]) - float(seg["x_left"]))

    def _proj_width(proj: dict) -> float:
        pL = float(proj.get("x_left_px", proj["x_px"] - 10.0))
        pR = float(proj.get("x_right_px", proj["x_px"] + 10.0))
        return max(1.0, abs(pR - pL))

    _proj_range = _proj_x_range  # module-level helper; alias for brevity

    def _interval_iou(seg: dict, proj: dict) -> float:
        sL = float(seg["x_left"])
        sR = float(seg["x_right"])
        pL, pR = _proj_range(proj)
        inter = max(0.0, min(sR, pR) - max(sL, pL))
        union = max(sR, pR) - min(sL, pL)
        return (inter / union) if union > 0 else 0.0

    def _proj_containment(seg: dict, proj: dict) -> float:
        """Thin adapter over ``_proj_containment_in_seg``. Second leg of
        the matcher's acceptance test (narrow projections in wide
        multi-building segments have low IoU but high containment).
        """
        return _proj_containment_in_seg(
            float(seg["x_left"]), float(seg["x_right"]), proj)

    def _width_ratio_score(seg: dict, proj: dict) -> float:
        ratio = _proj_width(proj) / _seg_width(seg)
        if ratio <= 0:
            return 0.0
        log_r = abs(math.log2(ratio))
        return max(0.0, 1.0 - 0.5 * log_r)

    def _occlusion_penalty(cand: dict, all_projs: list[dict]) -> float:
        """Return 0..1 penalty: how much of cand's x-range is covered by
        a MUCH-NEARER candidate. 0 = no occlusion (foreground or no closer
        building). 1 = fully occluded by something dramatically closer.
        """
        cL, cR = _proj_range(cand)
        cw = max(1.0, cR - cL)
        cand_fwd = float(cand.get("forward_m", 1e9))
        # "Much nearer" = at least 1.5× closer than this candidate. Avoids
        # penalizing similar-distance siblings (would over-cull dense rows
        # where all towers have similar forward_m).
        threshold = cand_fwd / 1.5
        max_overlap = 0.0
        for o in all_projs:
            if o is cand:
                continue
            o_fwd = float(o.get("forward_m", 1e9))
            if o_fwd >= threshold:
                continue
            oL, oR = _proj_range(o)
            inter = max(0.0, min(cR, oR) - max(cL, oL))
            cov = inter / cw
            if cov > max_overlap:
                max_overlap = cov
        return min(1.0, max_overlap)

    def _building_lonlat_ring(fid: str) -> list[tuple[float, float]]:
        """Pull a building's exterior ring as (lon, lat) tuples for the
        cross-view scorer. Returns [] when the polygon is missing or has
        no usable exterior (cross-view returns a neutral 0.5 in that
        case so the matcher is undisturbed)."""
        b = buildings_by_id.get(fid)
        if b is None:
            return []
        geom = getattr(b, "geometry", None)
        if geom is None:
            return []
        try:
            xs, ys = geom.exterior.xy
        except Exception:
            return []
        return [(float(x), float(y)) for x, y in zip(xs, ys)]

    out: list[dict] = []
    for seg in segments:
        scored: list[tuple] = []  # (combined, iou, w_score, occ, proj, cv)
        for p in projections:
            iou = _interval_iou(seg, p)
            # F-SKY2.1: accept by IoU OR by ≥ 50 % projection-containment.
            # The containment leg saves narrow projections inside wide
            # multi-building silhouettes that IoU alone discards because
            # the segment width dominates the denominator.
            if iou < min_interval_iou and _proj_containment(seg, p) < 0.5:
                continue
            w_score = _width_ratio_score(seg, p)
            occ = _occlusion_penalty(p, projections)
            combined_intra = 0.55 * iou + 0.30 * w_score + 0.15 * (1.0 - occ)
            # Hard width disqualifier: a building projected 4× wider or
            # narrower than the segment is implausible as a match.
            # EXCEPT when the segment was already cut by ``osm_anchor_silhouettes``
            # — that splitter trims the segment to one tower's width
            # exactly because the candidate footprint is wider than a
            # single tower's silhouette; the ratio > 4 there is a
            # signal of correctness, not implausibility.
            if not seg.get("osm_anchored"):
                ratio = _proj_width(p) / _seg_width(seg)
                if ratio > 4.0 or ratio < 0.25:
                    combined_intra *= 0.3
            # Distance bias: closer buildings score higher. f(d) softly
            # decays from ~1.0 at the camera to ~0.45 at 1500 m. The 0.7
            # baseline + 0.3 weighting on the bonus caps the effect at
            # ~22 % score swing between a 100 m and a 1500 m candidate,
            # enough to flip the bucket when IoU is within ~0.10 of each
            # other — the canonical "inland complex beats waterfront
            # tower" failure mode — without tipping cases where a closer
            # weak-IoU bush wins over the actual tower.
            fwd_m = float(p.get("forward_m", 1e9))
            depth_bonus = 1.0 / (1.0 + fwd_m / 500.0)
            combined_intra *= (0.70 + 0.30 * depth_bonus)
            # F-SKY10: optional cross-view rerank. Score lookup is the
            # one expensive call (satellite ndarray slice + median RGB)
            # so it's gated on the candidate having passed the
            # IoU/containment filter above — we never score buildings
            # the matcher would reject anyway.
            cv = None
            if cross_view_scorer is not None:
                ring = _building_lonlat_ring(p["feature_id"])
                if ring:
                    try:
                        cv = float(cross_view_scorer(seg, ring).get(
                            "combined", 0.5))
                    except Exception:
                        cv = None
            if cv is not None:
                combined = 0.85 * combined_intra + 0.15 * cv
            else:
                combined = combined_intra
            scored.append((combined, iou, w_score, occ, p, cv))

        match: dict | None = None
        diagnostics: list[dict] = []
        if scored:
            scored.sort(key=lambda t: t[0], reverse=True)
            # Capture top-3 diagnostics for the audit field.
            for c, iou, ws, occ, p, cv in scored[:3]:
                d = {
                    "feature_id": p["feature_id"],
                    "combined": round(c, 3),
                    "iou": round(iou, 3),
                    "width_score": round(ws, 3),
                    "occlusion": round(occ, 3),
                    "forward_m": round(float(p.get("forward_m", 0.0)), 1),
                }
                if cv is not None:
                    d["cv"] = round(cv, 3)
                diagnostics.append(d)
            best_combined = scored[0][0]
            bucket = [
                p for c, _iou, _w, _occ, p, _cv in scored
                if c >= best_combined - 0.10
            ]
            # Foreground-takes-precedence: rescue any credible candidate
            # whose x-range CONTAINS the segment's peak_x AND is strictly
            # closer than the bucket's nearest. The hard width
            # disqualifier (×0.3 when ratio > 4) routinely knocks
            # waterfront towers out of the bucket because a single tower
            # silhouette segment is narrower than the building's full
            # projected footprint width — but that nearer tower is the
            # actually-visible-foreground answer. We only rescue when
            # height proxy ≥ 8 m so 1-storey kiosks don't take over from
            # a tagged inland tower. "Strictly closer" (not 1.5×) is
            # required because cross-bay views like Cartagena's seed_5
            # see waterfront and inland rows at similar distances; the
            # difference can be as little as 10-20 %, and the rescue
            # needs to fire there.
            bucket_nearest_fwd = min(
                (float(p.get("forward_m", 1e9)) for p in bucket),
                default=1e9)
            peak_x = float(seg.get(
                "peak_x", (float(seg["x_left"]) + float(seg["x_right"])) * 0.5))
            # Image-derived plausibility: a segment whose silhouette spans
            # ≥ 100 px vertically is, in image space, a foreground building
            # — the closest-strict-rescue can accept untagged candidates
            # for it. Untagged condos / new construction in OSM have
            # ``_height_proxy = 0``; the old gate (≥ 8 m) discarded
            # the exact waterfront-tower rescue case that drives the
            # "inland complex matched, foreground tower ignored" failure.
            seg_pixel_height = (
                float(seg.get("base_y", 0)) - float(seg.get("top_y", 0)))
            seg_is_tall_in_image = seg_pixel_height >= 100.0
            for c, _iou, _w, _occ, p, _cv in scored:
                if p in bucket:
                    continue
                pL, pR = _proj_range(p)
                if not (pL <= peak_x <= pR):
                    continue
                p_fwd = float(p.get("forward_m", 1e9))
                if p_fwd >= bucket_nearest_fwd:
                    continue
                b = buildings_by_id.get(p["feature_id"])
                h_proxy = _height_proxy(b) if b is not None else 0.0
                # Accept rescue when the OSM tag indicates a real
                # building OR the image-space height says the segment
                # itself is a foreground tower. Either signal alone is
                # sufficient — a tagged 10 m+ candidate behind a short
                # mask is still a real building; a 100+ px-tall segment
                # in the image is unmistakably a tower regardless of tag.
                if h_proxy < 8.0 and not seg_is_tall_in_image:
                    continue
                bucket.append(p)
            bucket.sort(key=lambda p: float(p.get("forward_m", 1e9)))
            nearest = bucket[0]
            nearest_fwd = float(nearest.get("forward_m", 1e9))
            b_near = buildings_by_id.get(nearest["feature_id"])
            nearest_h = _height_proxy(b_near) if b_near is not None else 0.0
            match = nearest
            if nearest_h < 5.0:
                max_fwd = nearest_fwd * 1.5
                for cand in bucket[1:]:
                    if float(cand.get("forward_m", 1e9)) > max_fwd:
                        break
                    b = buildings_by_id.get(cand["feature_id"])
                    h = _height_proxy(b) if b is not None else 0.0
                    if h > nearest_h * 2.0:
                        match = cand
                        break
        # F-SKY6: record the combined score of the chosen match so the
        # post-pass deduplication can pick a winner when two segments
        # claimed the same OSM building.
        match_combined = 0.0
        if match is not None:
            for c, _iou, _w, _o, p, _cv in scored:
                if p["feature_id"] == match["feature_id"]:
                    match_combined = float(c)
                    break
        out.append({
            **seg,
            "matched_projection": match,
            "matched_combined": match_combined,
            "match_diagnostics": diagnostics,
        })

    # F-SKY6 part 1: enforce one-to-one segment ↔ building uniqueness.
    # The per-segment scoring above lets two adjacent segments pick the
    # same OSM building (observed on Cartagena seed_5 page 31 where
    # segments 1 and 2 both matched b0268). For each duplicated building,
    # keep the segment with the highest matched_combined and clear the
    # match on the losers. Losers become unmatched (no "next-preference"
    # fallback in this pass — that's a follow-up). match_diagnostics is
    # preserved on losers so the audit page still shows what they
    # considered.
    fid_claimants: dict[str, list[int]] = {}
    for i, o in enumerate(out):
        m = o.get("matched_projection")
        if m is None:
            continue
        fid_claimants.setdefault(m["feature_id"], []).append(i)
    for fid, idxs in fid_claimants.items():
        if len(idxs) <= 1:
            continue
        idxs.sort(key=lambda i: -float(out[i].get("matched_combined", 0.0)))
        for loser_i in idxs[1:]:
            out[loser_i]["matched_projection"] = None
            out[loser_i]["matched_combined"] = 0.0
    return out

def _height_proxy(building: BuildingRecord) -> float:
    """Rank buildings as skyline-defining when no tagged height exists.

    Tagged height wins; otherwise sqrt(footprint area) as a coarse proxy.
    """
    if building.height_tag_m is not None:
        return float(building.height_tag_m)
    return float(min(40.0, 0.6 * math.sqrt(max(building.area_m2, 1.0))))

def _cull_occluded_projections(
    projections: list[dict],
    buildings_by_id: dict[str, BuildingRecord],
    image_width: int,
    bin_px: int = 24,
) -> list[dict]:
    """Per ~bin_px column window, keep only buildings that are actually visible.

    Physics: the CLOSEST building in each column bin occupies the visible
    foreground. A taller building BEHIND it is visible only if its roof
    sticks above the front building's roof from the camera's viewpoint.

    Approximated with height proxies (tagged height or sqrt-area fallback):
       front_roof_angle ≈ front_height / front_forward_m
       back_roof_angle  ≈ back_height  / back_forward_m
    A back building is kept iff its roof_angle exceeds the front's by a
    margin (20 %). Otherwise it's fully occluded and dropped.

    Without this rule, the matcher kept the "tallest in bin" — which often
    placed a far thin OSM building at the image column actually occupied by
    a nearer shorter building, producing nonsensical match overlays.
    """
    if not projections:
        return []
    bins: dict[int, list[dict]] = {}
    for proj in projections:
        b = int(round(proj["x_px"] / max(1, bin_px)))
        bins.setdefault(b, []).append(proj)

    def _angle(p: dict) -> float:
        b = buildings_by_id.get(p["feature_id"])
        h = _height_proxy(b) if b is not None else 10.0
        # Camera height ~1.7m; we want the rooftop angle above the camera.
        return float(max(h - 1.7, 0.5)) / max(float(p.get("forward_m", 1e9)), 1.0)

    kept: dict[str, dict] = {}
    for items in bins.values():
        if len(items) == 1:
            kept[items[0]["feature_id"]] = items[0]
            continue
        items_sorted = sorted(
            items, key=lambda p: float(p.get("forward_m", 1e9)))
        front = items_sorted[0]
        kept[front["feature_id"]] = front
        front_angle = _angle(front)
        for back in items_sorted[1:]:
            if _angle(back) > front_angle * 1.20:
                # Back building's roof sticks visibly above the front's:
                # contributes the top of the skyline, keep it.
                kept[back["feature_id"]] = back
            # else: fully occluded by the front building, drop.
    return list(kept.values())

def _match_projections_to_peaks(
    projections: list[dict],
    peaks: np.ndarray,
    max_match_px: float = 30.0,
) -> tuple[list[tuple[int, int]], float]:
    """Hungarian assignment between projected building x positions and skyline peaks.

    Returns (matches, mean_residual_px). matches is a list of (proj_idx, peak_idx)
    pairs; pairs whose cost exceeds max_match_px are dropped.
    """
    if not projections or peaks.size == 0:
        return [], float("inf")

    proj_x = np.asarray([p["x_px"] for p in projections], dtype=np.float32)
    peak_x = peaks.astype(np.float32)

    cost = np.abs(proj_x[:, None] - peak_x[None, :])
    row_idx, col_idx = linear_sum_assignment(cost)
    matches: list[tuple[int, int]] = []
    residuals: list[float] = []
    for r, c in zip(row_idx, col_idx):
        d = float(cost[r, c])
        if d <= max_match_px:
            matches.append((int(r), int(c)))
            residuals.append(d)
    if not matches:
        return [], float("inf")
    return matches, float(np.mean(residuals))

def _score_offset_semantic_iou(
    buildings: Sequence[BuildingRecord],
    viewpoint: Viewpoint,
    offset_deg: float,
    building_mask: np.ndarray,
    water_mask: "np.ndarray | None" = None,
) -> float:
    """Width-weighted semantic-alignment score in [-1, 1] (clamped ≥ 0).

    The score is built from three terms aggregated across all in-frustum OSM
    buildings, each projected to its [x_min, x_max] image-column range:

      hit_cols   = Σ_b (predicted-cols ∩ building-dominant-mask-cols)
      water_cols = Σ_b (predicted-cols ∩ water-dominant-mask-cols)
      pred_cols  = Σ_b width_b
      placement  = (hit_cols - water_cols) / pred_cols
                 → fraction of "OSM ink" landing on building, net of water hits

      miss = unmatched mask-building-cols / total mask-building-cols
                 → fraction of observed skyline that the OSM cone DOESN'T cover

      score = placement - 0.3 * miss

    Why this formulation:
      - **Width-weighting** by per-building projected width replaces an
        equal-weight average; a 50-col Bocagrande tower properly outweighs a
        1-col distant warehouse, so the right offset doesn't get dragged down
        by misaligned tiny buildings.
      - **Net water-penalty**: putting OSM ink directly into observed water is
        the clearest single signal of a wrong heading on a maritime view.
        Subtracted in the same units as placement (column-counts).
      - **Miss penalty**: an offset that lands all predicted buildings in
        valid mask columns but rotates the cone such that 60% of the OBSERVED
        skyline is unexplained is still wrong. The miss term catches that.
        Weighted 0.3 because directional gross-miss is real but a smaller
        signal than placement; we don't want a half-occluded right offset to
        score worse than a fully-pointed-wrong offset that happens to cover
        the visible skyline by accident.
    """
    if building_mask is None or building_mask.size == 0:
        return 0.0
    bmask = np.asarray(building_mask)
    h_img, w = bmask.shape[:2]

    x_min, x_max = _projected_building_x_ranges(
        buildings, viewpoint, offset_deg, w)
    if x_min.size == 0:
        return 0.0

    build_per_col = bmask.sum(axis=0)
    if water_mask is not None and water_mask.size > 0:
        water_per_col = np.asarray(water_mask).sum(axis=0)
    else:
        water_per_col = np.zeros(w, dtype=np.int64)
    is_building_col = (build_per_col > water_per_col) & (build_per_col > 5)
    is_water_col = (water_per_col > build_per_col) & (water_per_col > 5)

    # Build a flat "predicted" mask once and accumulate hit/water/pred totals
    # by iterating the per-building ranges (preserving width-weighting).
    predicted = np.zeros(w, dtype=bool)
    hit_cols = 0
    water_cols = 0
    pred_cols = 0
    for xL, xR in zip(x_min.tolist(), x_max.tolist()):
        width = xR - xL + 1
        if width <= 0:
            continue
        pred_cols += width
        seg_b = is_building_col[xL: xR + 1]
        seg_w = is_water_col[xL: xR + 1]
        hit_cols += int(np.count_nonzero(seg_b))
        water_cols += int(np.count_nonzero(seg_w))
        predicted[xL: xR + 1] = True

    if pred_cols == 0:
        return 0.0

    placement = (hit_cols - water_cols) / float(pred_cols)

    # Miss term: fraction of observed-skyline-columns that the OSM cone fails
    # to cover. The mask itself can have spurious thin building columns; only
    # apply the miss term when there's a non-trivial amount of building in
    # view (≥ 5% of frame width).
    n_observed = int(np.count_nonzero(is_building_col))
    if n_observed >= max(20, int(0.05 * w)):
        unmatched_observed = int(
            np.count_nonzero(is_building_col & ~predicted))
        miss = unmatched_observed / float(n_observed)
    else:
        miss = 0.0

    # Miss-penalty raised 0.3 → 0.6 to better discriminate 180°-symmetric
    # local maxima. At a wrong-by-180° offset, predicted buildings often
    # still land in some real mask-building columns (because there ARE
    # buildings in both directions from many seeds), so `placement` alone
    # gives a misleadingly-positive score. The miss term captures that
    # the wrong offset leaves most of the OBSERVED skyline unexplained.
    score = placement - 0.6 * miss
    return max(0.0, min(1.0, score))

def register_view_to_osm(
    captured: CapturedView,
    buildings: Sequence[BuildingRecord],
    heading_search_deg: float,
    heading_step_deg: float,
    min_matches: int = 3,
    max_match_px: float = 30.0,
    coarse_search_deg: float | None = None,
    coarse_step_deg: float = 10.0,
    forced_center_deg: float | None = None,
    use_semantic_iou: bool = True,
) -> dict:
    """Register an image to OSM by finding the heading offset that best aligns
    projected building x positions with detected skyline peaks.

    Photo Sphere panos served by Google's Static API have an arbitrary internal
    coordinate frame — the API's ``heading`` parameter is NOT geographic for
    them — so the offset between API heading and geographic compass can be
    anything in [0, 360°). When ``coarse_search_deg`` is set we first sweep
    that wide range at ``coarse_step_deg`` to find the right ballpark, then
    refine within ±``heading_search_deg`` at ``heading_step_deg``.

    For road panos pass coarse_search_deg=None — they are already geographic-
    aligned and the cheap ±15° fine search is sufficient.
    """
    contour, sky_mask = detect_skyline_contour(captured.image)
    # Extract peaks inline (median-filtered inverted contour → local minima = rooftops)
    _c_inv = -median_filter(np.asarray(contour,
                            dtype=np.float32), size=7, mode="nearest")
    observed_peaks, _ = find_peaks(
        _c_inv, distance=18, prominence=max(3.0, float(np.std(_c_inv)) * 0.15)
    )
    observed_peaks = observed_peaks.astype(np.int32)
    buildings_by_id = {b.feature_id: b for b in buildings}

    # Pull the neural masks once — they're the substrate for the semantic
    # mIoU objective. When the model is unavailable we fall back to the legacy
    # pixel-residual score (worse on panoramas, but functional).
    _, building_mask_neural = _neural_sky_and_building_masks(captured.image)
    water_mask_neural = _neural_water_mask(captured.image)
    has_mask = use_semantic_iou and building_mask_neural is not None

    def _score_offset(offset: float):
        """Return (combined_score, mean_resid_px, iou, culled, matches).

        Lower combined_score = better fit. When the neural building mask is
        available the objective is dominated by (1 - mIoU): a wrong offset
        puts predicted buildings into sky/water columns and the IoU tanks,
        whereas peak residuals are nearly always low because some peaks land
        within ±15 px of any projection on a panoramic skyline.

        Uses ``_project_all_buildings_vectorized`` for the projection step —
        ~100× faster than the per-building Python loop (which dominated
        runtime before vectorisation).
        """
        projected = _project_all_buildings_vectorized(
            buildings, captured.viewpoint, offset)
        if len(projected) < min_matches or observed_peaks.size < min_matches:
            return float("inf"), float("inf"), 0.0, [], []
        culled = _cull_occluded_projections(
            projected, buildings_by_id, captured.viewpoint.image_width)
        matches, mean_resid = _match_projections_to_peaks(
            culled, observed_peaks, max_match_px=max_match_px)
        if len(matches) < min_matches:
            return float("inf"), float("inf"), 0.0, [], []
        if has_mask:
            iou = _score_offset_semantic_iou(
                buildings, captured.viewpoint, offset,
                building_mask_neural, water_mask_neural)
            combined = (1.0 - iou) * 100.0 + mean_resid * 0.1
            return combined, mean_resid, iou, culled, matches
        return mean_resid, mean_resid, 0.0, culled, matches

    # When a seed-level anchor is supplied, skip the coarse pass entirely and
    # search only near the anchor. This forces all 12 spin views of a Photo
    # Sphere seed to share one consistent pano-to-geographic offset (the
    # matcher otherwise finds different "best" offsets per view because the
    # panorama has many similar towers and locally any offset can score okay).
    center_deg = 0.0
    if forced_center_deg is not None:
        center_deg = float(forced_center_deg)
    elif coarse_search_deg and coarse_search_deg > heading_search_deg:
        # Phase 1: coarse calibration sweep. Finds the ballpark pano-to-
        # geographic offset within ~coarse_step_deg.
        coarse_offsets = np.arange(
            -coarse_search_deg, coarse_search_deg + 0.001, coarse_step_deg)
        coarse_best_score = float("inf")
        for offset in coarse_offsets:
            cscore, *_ = _score_offset(float(offset))
            if cscore < coarse_best_score:
                coarse_best_score = cscore
                center_deg = float(offset)

    # Phase 2: fine refinement within ±heading_search_deg around the coarse best.
    fine_offsets = np.arange(
        center_deg - heading_search_deg,
        center_deg + heading_search_deg + 0.001,
        heading_step_deg,
    )
    best_offset = 0.0
    best_combined = float("inf")
    best_resid = float("inf")
    best_iou = 0.0
    best_projections: list[dict] = []
    best_matches: list[tuple[int, int]] = []
    for offset in fine_offsets:
        combined, resid, iou, culled, matches = _score_offset(float(offset))
        if combined < best_combined:
            best_combined = combined
            best_resid = resid
            best_iou = iou
            best_offset = float(offset)
            best_projections = culled
            best_matches = matches
    # Report reg_score as the pixel residual (interpretable, comparable across
    # views) but the offset selection uses the combined objective.
    best_score = best_resid

    # Compute a per-view "display IoU" — raw placement (hit ÷ pred_cols)
    # without the joint-optimization miss penalty. The miss penalty makes
    # sense at the joint anchor where we want to cover the full observed
    # skyline, but it over-clips at the per-view level: a view that
    # successfully matches half its buildings can still get
    # best_iou=0.0 because the other half isn't covered, which doesn't
    # reflect that the per-view registration is working. The display
    # value answers "how much of the projected OSM ink lands on
    # actual mask-building columns?" — meaningful per-view.
    if has_mask and best_projections:
        try:
            bmask = np.asarray(building_mask_neural)
            wmask = (
                np.asarray(water_mask_neural)
                if water_mask_neural is not None else None
            )
            w = bmask.shape[1]
            x_min, x_max = _projected_building_x_ranges(
                buildings, captured.viewpoint, best_offset, w)
            if x_min.size > 0:
                build_per_col = bmask.sum(axis=0)
                water_per_col = (
                    wmask.sum(axis=0) if wmask is not None
                    else np.zeros(w, dtype=np.int64)
                )
                is_b_col = (build_per_col > water_per_col) & (build_per_col > 5)
                hit = 0
                pred = 0
                for xL, xR in zip(x_min.tolist(), x_max.tolist()):
                    width = xR - xL + 1
                    if width <= 0:
                        continue
                    pred += width
                    hit += int(np.count_nonzero(is_b_col[xL : xR + 1]))
                if pred > 0:
                    best_iou = float(hit) / float(pred)
        except Exception:
            pass  # leave best_iou as found by the optimizer

    matched_projections: list[dict] = []
    if best_matches:
        for r, c in best_matches:
            entry = dict(best_projections[r])
            entry["matched_peak_x"] = int(observed_peaks[c])
            entry["match_residual_px"] = abs(
                entry["x_px"] - float(observed_peaks[c]))
            matched_projections.append(entry)

    return {
        "contour": contour,
        "sky_mask": sky_mask,
        "observed_peaks": observed_peaks,
        "best_offset": best_offset,
        # mean pixel residual (legacy display)
        "best_score": best_score,
        "best_iou": best_iou,                # semantic mIoU at best offset
        "best_combined_score": best_combined,
        "projections": matched_projections,
        # all culled-visible buildings at best offset
        "all_projections": best_projections,
        "n_matches": len(matched_projections),
    }


__all__ = [
    '_proj_x_range',
    '_proj_containment_in_seg',
    'osm_anchor_silhouettes',
    'osm_sam_instance_silhouettes',
    'match_segments_to_buildings',
    '_height_proxy',
    '_cull_occluded_projections',
    '_match_projections_to_peaks',
    '_score_offset_semantic_iou',
    'register_view_to_osm',
]
