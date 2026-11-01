"""Part of ``city2stl.skyline._pano.detect`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging

import numpy as np

from .._region_render._draw import overlay_spec
from ..region_types import SeedViewRegistration, StitchedPanoResult

logger = logging.getLogger('city2stl.skyline._pano.detect')


def _dedup_matches(segments: list[dict], restore_swapped: bool = False) -> int:
    """One segment per OSM feature_id (the F-SKY6 one-to-one rule) after a smoothing swap.

    Among segments matched to the same feature, the one whose ``match_diagnostics`` scores
    that feature highest (``combined``; else its ``matched_combined``) keeps it. A loser is
    cleared, or with ``restore_swapped`` put back on its pre-smoothing match when it was
    swapped and nothing else holds that feature. Returns the number of losers.
    """
    claimants: dict[str, list[dict]] = {}
    for seg in segments:
        m = seg.get("matched_projection")
        fid = str(m.get("feature_id", "")) if m else ""
        if fid:
            claimants.setdefault(fid, []).append(seg)
    dropped = 0
    for fid, segs in list(claimants.items()):
        if len(segs) <= 1:
            continue

        def _score(s, fid=fid):
            for d in s.get("match_diagnostics", []) or []:
                if str(d.get("feature_id", "")) == fid:
                    return float(d.get("combined", 0.0))
            return float(s.get("matched_combined", 0.0))

        segs.sort(key=_score, reverse=True)
        for loser in segs[1:]:
            loser["matched_projection_pre_dedup"] = loser.get("matched_projection")
            prev = loser.get("matched_projection_pre_smoothing") if restore_swapped else None
            prev_fid = str(prev.get("feature_id", "")) if prev else ""
            if prev_fid and prev_fid not in claimants:
                loser["matched_projection"] = prev
                loser["match_smoothed"] = False
                claimants[prev_fid] = [loser]
            else:
                loser["matched_projection"] = None
                loser.pop("seed_index", None)
            dropped += 1
    return dropped


def _count_skyline_buildings(mask: np.ndarray, min_step_frac: float = 0.02,
                             min_width_frac: float = 0.01) -> int:
    """Buildings in a building mask, for the F-DET1 early-out (T26).

    Counting connected components called a continuous city one building, so good
    far-skyline seeds were dropped (Cartagena ``auto_180_2000m``: 4 blobs). This walks the
    mask's top edge instead: after a median filter (``min_width_frac`` of the width, so
    1-2 px mask noise does not count), the edge is cut wherever it jumps by at least
    ``min_step_frac`` of the image height or drops to no building, and every piece at least
    ``min_width_frac`` wide is a building. Towers side by side give one piece each, a sloped
    roof one. Never less than the old component count.
    """
    import cv2  # noqa: PLC0415
    from scipy.ndimage import median_filter  # noqa: PLC0415

    m = np.asarray(mask) > 0
    if m.ndim != 2 or not m.any():
        return 0
    h_img, w_img = m.shape
    has = m.any(axis=0)
    prof = np.where(has, h_img - m.argmax(axis=0), 0).astype(float)
    k = max(3, int(min_width_frac * w_img)) | 1
    prof = median_filter(prof, size=k, mode="nearest")
    cut = np.abs(np.diff(prof)) >= min_step_frac * h_img
    cut |= (prof[1:] == 0) != (prof[:-1] == 0)
    bounds = np.concatenate([[0], np.flatnonzero(cut) + 1, [w_img]])
    min_w = max(1, int(min_width_frac * w_img))
    pieces = sum(1 for a, b in zip(bounds[:-1], bounds[1:], strict=True)
                 if b - a >= min_w and prof[a:b].max() > 0)
    n_components = cv2.connectedComponents(m.astype(np.uint8))[0] - 1
    return max(pieces, n_components)


def _smooth_matches_across_views(
    seed_view_rows: list[SeedViewRegistration],
    min_popularity_swap: int = 2,
) -> None:
    """Promote popular OSM matches across a seed's views.

    Walks every matched segment in every view of a single seed. For each
    segment whose matched feature_id was chosen *only* by that one view
    (popularity 1), inspect its ``match_diagnostics`` for runner-up
    candidates. If a runner-up has popularity ≥ ``min_popularity_swap``
    elsewhere in the seed, swap the segment's match to that candidate —
    the per-view matcher dissented from a consensus that was reached
    from several other angles, so the cross-view majority almost
    certainly named the correct OSM polygon.

    Operates in-place on the ``matched_segments`` list of each view.
    Does not modify segments without a current match. Logs each swap
    so the diagnostic page can attribute changed badges to this pass.
    """
    fid_popularity: dict[str, int] = {}
    for sv in seed_view_rows:
        seen_in_view: set[str] = set()
        for seg in sv.matched_segments or []:
            m = seg.get("matched_projection")
            if not m:
                continue
            fid = str(m.get("feature_id", ""))
            if not fid or fid in seen_in_view:
                continue
            seen_in_view.add(fid)
            fid_popularity[fid] = fid_popularity.get(fid, 0) + 1

    swap_count = 0
    for sv in seed_view_rows:
        for seg in sv.matched_segments or []:
            m = seg.get("matched_projection")
            if not m:
                continue
            cur_fid = str(m.get("feature_id", ""))
            if fid_popularity.get(cur_fid, 0) >= min_popularity_swap:
                continue
            best_alt_fid = None
            best_alt_pop = 0
            for d in seg.get("match_diagnostics", []) or []:
                alt_fid = str(d.get("feature_id", ""))
                if not alt_fid or alt_fid == cur_fid:
                    continue
                pop = fid_popularity.get(alt_fid, 0)
                if pop >= min_popularity_swap and pop > best_alt_pop:
                    best_alt_pop = pop
                    best_alt_fid = alt_fid
            if best_alt_fid is None:
                continue
            for d in seg.get("match_diagnostics", []) or []:
                if str(d.get("feature_id", "")) == best_alt_fid:
                    seg["matched_projection_pre_smoothing"] = m
                    seg["matched_projection"] = {
                        "feature_id": best_alt_fid,
                        "x_px": d.get("x_px", m.get("x_px", 0)),
                        "x_left_px": d.get("x_left_px", m.get("x_left_px", 0)),
                        "x_right_px": d.get("x_right_px", m.get("x_right_px", 0)),
                        "forward_m": d.get("forward_m", m.get("forward_m", 0.0)),
                    }
                    seg["match_smoothed"] = True
                    swap_count += 1
                    break

    if swap_count:
        logger.info(f"[smooth_matches] swapped {swap_count} dissenting per-view "
                    f"match(es) to seed-level popular candidates")
        # Post-swap dedup (per view): the swap can leave two segments in
        # the same view pointing at the same OSM feature_id (both were
        # dissenters with the same popular alternative). Keep the
        # segment whose original match best agreed with the swapped
        # candidate — measured by the candidate's combined score in
        # ``match_diagnostics`` — and clear the losers. Mirrors the
        # F-SKY6 one-to-one constraint enforced by the per-view matcher.
        dedup_dropped = sum(_dedup_matches(sv.matched_segments or [])
                            for sv in seed_view_rows)
        if dedup_dropped:
            logger.info(f"[smooth_matches] dedup cleared {dedup_dropped} duplicate "
                        f"match(es) created by the swap")

        # Rebuild seed_index across the seed so renamed buildings get
        # consistent badge numbers + colours. Walk views in capture
        # order; within each view walk segments left-to-right (already
        # the splitter's order). First-seen fid gets the lowest index.
        rebuilt: dict[str, int] = {}
        next_idx = 1
        for sv in seed_view_rows:
            for seg in sv.matched_segments or []:
                m = seg.get("matched_projection")
                if not m:
                    continue
                fid = str(m.get("feature_id", ""))
                if not fid:
                    continue
                if fid not in rebuilt:
                    rebuilt[fid] = next_idx
                    next_idx += 1
                seg["seed_index"] = rebuilt[fid]
        # Re-render the per-view registration overlay so the badge
        # numbers + colours in the image reflect the smoothed matches. The
        # overlay is redrawn from ``raw_image`` on access (T21), so only its
        # spec is replaced. The contour & projections were not re-validated
        # here, so they stay out of the redraw (no stale per-view info).
        for sv in seed_view_rows:
            if getattr(sv, "raw_image", None) is None:
                continue
            sv.set_overlay_spec(overlay_spec({}, sv.matched_segments))


def _smooth_pano_matches_against_views(
    pano_result: StitchedPanoResult,
    seed_view_rows: list[SeedViewRegistration],
    min_popularity: int = 2,
) -> None:
    """Promote per-view popular OSM matches into the pano's match list.

    The stitched-pano matcher runs on a 360° composite and produces its
    own matched_segments. When the per-view consensus disagrees with
    one of the pano's matches (the per-view rows match feature_id A in
    several views, but the pano matched B at that bearing), the pano
    is treated as the dissenter and swapped to A if A is also a top-3
    candidate in the pano segment's ``match_diagnostics``. Mirrors the
    per-view smoothing pass for cross-view consistency.

    A swap can hand a segment a feature another pano segment already holds (T25): the
    better match keeps it (``_dedup_matches``) and a swapped loser goes back to its own
    pre-smoothing match.
    """
    if pano_result is None or not pano_result.matched_segments:
        return
    fid_popularity: dict[str, int] = {}
    for sv in seed_view_rows:
        seen_in_view: set[str] = set()
        for seg in sv.matched_segments or []:
            m = seg.get("matched_projection")
            if not m:
                continue
            fid = str(m.get("feature_id", ""))
            if not fid or fid in seen_in_view:
                continue
            seen_in_view.add(fid)
            fid_popularity[fid] = fid_popularity.get(fid, 0) + 1

    swap_count = 0
    for seg in pano_result.matched_segments:
        m = seg.get("matched_projection")
        if not m:
            continue
        cur_fid = str(m.get("feature_id", ""))
        if fid_popularity.get(cur_fid, 0) >= min_popularity:
            continue
        best_alt_fid = None
        best_alt_pop = 0
        for d in seg.get("match_diagnostics", []) or []:
            alt_fid = str(d.get("feature_id", ""))
            if not alt_fid or alt_fid == cur_fid:
                continue
            pop = fid_popularity.get(alt_fid, 0)
            if pop >= min_popularity and pop > best_alt_pop:
                best_alt_pop = pop
                best_alt_fid = alt_fid
        if best_alt_fid is None:
            continue
        for d in seg.get("match_diagnostics", []) or []:
            if str(d.get("feature_id", "")) == best_alt_fid:
                seg["matched_projection_pre_smoothing"] = m
                seg["matched_projection"] = {
                    "feature_id": best_alt_fid,
                    "x_px": d.get("x_px", m.get("x_px", 0)),
                    "x_left_px": d.get("x_left_px", m.get("x_left_px", 0)),
                    "x_right_px": d.get("x_right_px", m.get("x_right_px", 0)),
                    "forward_m": d.get("forward_m", m.get("forward_m", 0.0)),
                }
                seg["match_smoothed"] = True
                swap_count += 1
                break

    if swap_count:
        logger.info(f"[smooth_matches] swapped {swap_count} pano dissenting match(es) "
                    f"to per-view popular candidates")
        dropped = _dedup_matches(pano_result.matched_segments, restore_swapped=True)
        if dropped:
            logger.info(f"[smooth_matches] pano dedup resolved {dropped} duplicate "
                        f"match(es) created by the swap")


def matched_footprint_overlap(segments: list, width: int) -> float | None:
    """Median column IoU of each matched segment with its footprint's projection (F-DET A7).

    A low value with a high match count says the matcher pairs segments with footprints that
    only graze them (segmentation or bearing trouble, not an OSM gap). Spans that wrap past the
    pano seam (right < left, or wider than half the pano) are skipped.
    """
    ious = []
    for s in segments or ():
        m = s.get("matched_projection") if isinstance(s, dict) else None
        if not m or m.get("x_left_px") is None or m.get("x_right_px") is None:
            continue
        a0, a1 = float(s["x_left"]), float(s["x_right"])
        b0, b1 = float(m["x_left_px"]), float(m["x_right_px"])
        if a1 < a0 or b1 < b0 or (width and max(a1 - a0, b1 - b0) > width / 2):
            continue
        inter = max(0.0, min(a1, b1) - max(a0, b0))
        union = max(a1, b1) - min(a0, b0)
        if union > 0:
            ious.append(inter / union)
    return float(np.median(ious)) if ious else None
