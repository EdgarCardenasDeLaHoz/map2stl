"""Part of ``city2stl.skyline._pano.detect`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from ..config import current as _cfg

logger = logging.getLogger('city2stl.skyline._pano.detect')


def _multires_sam_instances(
    pano_img: np.ndarray,
    cluster_x_ranges: list[tuple[int, int]],
    band_y_top: int,
    band_y_bot: int,
    osm_centroids_in_pano: list[tuple[int, int, str]],
    refined_mask: np.ndarray,
    confidence_floor: float = 0.6,
    intersect_with_segformer: bool = True,
) -> tuple[list[dict], float]:
    """Run MobileSAM on each multires cluster crop with OSM building
    centroids as point prompts to obtain per-instance silhouettes
    (F-SKY20).

    Each ``osm_centroids_in_pano`` entry is ``(x_pano, y_pano, fid)``.
    Only centroids that fall inside a cluster's pano-x range AND the
    band are used as prompts for that cluster. Each SAM mask above
    ``confidence_floor`` is bounded to the cluster's pano region and
    intersected with the refined SegFormer mask (so SAM bleed into
    sky/water gets clipped).

    Returns ``(instance_segments, total_inference_s)``. Instance segments
    have ``{x_left, x_right, top_y, base_y, peak_x, mid_x, source: "sam",
    sam_prompt_fid: <fid>, sam_score: <float>}`` in PANO coordinates.

    Soft fallback: when MobileSAM is unavailable returns ``([], 0.0)``
    so the pipeline still ships the multires refined mask.
    """
    try:
        from mobile_sam import SamPredictor, sam_model_registry  # noqa: PLC0415
    except Exception:
        return [], 0.0
    ckpt = _cfg().mobilesam_checkpoint
    if not Path(ckpt).is_file():
        return [], 0.0

    try:
        model = sam_model_registry["vit_t"](checkpoint=ckpt)
        model.eval()
        predictor = SamPredictor(model)
    except Exception as exc:
        logger.warning(f"[multires_sam] load failed: {exc}")
        return [], 0.0

    import numpy as np  # noqa: PLC0415
    out: list[dict] = []
    total_t = 0.0
    for ci, (xL, xR) in enumerate(cluster_x_ranges):
        crop = pano_img[band_y_top: band_y_bot + 1, xL: xR + 1]
        if crop.size == 0:
            continue
        # OSM building centroids inside this cluster's pano range.
        prompts_in_crop: list[tuple[int, int, str]] = []
        for (gx, gy, fid) in osm_centroids_in_pano:
            if xL <= gx <= xR and band_y_top <= gy <= band_y_bot:
                prompts_in_crop.append((gx - xL, gy - band_y_top, fid))
        if not prompts_in_crop:
            continue
        try:
            t0 = time.perf_counter()
            predictor.set_image(crop)
            crop_h, crop_w = crop.shape[:2]
            cluster_refined = refined_mask[
                band_y_top: band_y_bot + 1, xL: xR + 1]
            for (px, py, fid) in prompts_in_crop:
                points = np.array([[px, py]], dtype=np.float32)
                labels = np.array([1], dtype=np.int32)
                masks, scores, _logits = predictor.predict(
                    point_coords=points,
                    point_labels=labels,
                    multimask_output=True,
                )
                best_i = int(scores.argmax())
                if float(scores[best_i]) < confidence_floor:
                    continue
                mask = masks[best_i].astype(bool)
                if intersect_with_segformer:
                    mask = mask & cluster_refined
                if not mask.any():
                    continue
                ys, xs = np.where(mask)
                if xs.size == 0:
                    continue
                # Translate cluster-crop coords to pano coords.
                out.append({
                    "x_left": int(xs.min() + xL),
                    "x_right": int(xs.max() + xL),
                    "top_y": int(ys.min() + band_y_top),
                    "base_y": int(ys.max() + band_y_top),
                    "peak_x": int(np.median(xs) + xL),
                    "mid_x": int(np.median(xs) + xL),
                    "source": "sam",
                    "sam_prompt_fid": fid,
                    "sam_score": float(scores[best_i]),
                    "cluster_idx": ci,
                })
            total_t += time.perf_counter() - t0
        except Exception as exc:
            logger.warning(f"[multires_sam] cluster {ci} predict failed: {exc}")
            continue
    return out, total_t


def _split_by_depth_discontinuity(
    refined_mask_crop: np.ndarray,
    depth_crop: np.ndarray,
    *,
    min_depth_jump: float = 0.08,
    min_run_height_px: int = 12,
) -> np.ndarray:
    """Use a depth map to break adjacent building columns that show a
    sharp depth jump — F-SKY21 instance-separation via depth.

    For each column with building pixels, compute the median depth.
    Walk left→right; when the median depth between adjacent columns
    jumps by more than ``min_depth_jump`` (depth is in [0, 1] inverse-
    relative scale), inject a 1-pixel-wide hole in the building mask
    at that column, which makes the rule-based splitter (Stage 5) emit
    two separate components instead of one.

    Returns the modified mask (out-of-place).
    """
    if refined_mask_crop is None or depth_crop is None:
        return refined_mask_crop
    if refined_mask_crop.shape[:2] != depth_crop.shape[:2]:
        return refined_mask_crop
    out = refined_mask_crop.copy()
    h_crop, w_crop = out.shape[:2]
    col_depth_median = np.full(w_crop, np.nan, dtype=np.float32)
    for x in range(w_crop):
        col_mask = out[:, x]
        if int(col_mask.sum()) < min_run_height_px:
            continue
        col_depth_median[x] = float(np.median(depth_crop[col_mask, x]))
    # Compute adjacent-column depth jumps and flag boundaries.
    last_valid = -1
    n_cuts = 0
    for x in range(w_crop):
        d = col_depth_median[x]
        if np.isnan(d):
            last_valid = -1
            continue
        if last_valid >= 0:
            prev_d = col_depth_median[last_valid]
            if abs(d - prev_d) > min_depth_jump:
                # Inject the cut at column x (clear building pixels).
                out[:, x] = False
                n_cuts += 1
                last_valid = -1
                continue
        last_valid = x
    if n_cuts:
        # Light morphology to make sure the cut survives the splitter.
        try:
            import cv2  # noqa: PLC0415
            cut_mask = (out.astype(np.uint8)) * 255
            cut_mask = cv2.morphologyEx(
                cut_mask, cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
            )
            out = cut_mask.astype(bool)
        except Exception:
            pass
    return out


def _multires_pano_refine(
    pano_img: np.ndarray,
    pano_bmask: np.ndarray,
    pano_wmask: np.ndarray | None,
    *,
    min_cluster_width_px: int = 30,
    min_gap_px: int = 24,
    min_column_height_px: int = 30,
    fine_input_size: int = 512,
    use_depth: bool = True,
) -> tuple[np.ndarray, list[tuple[int, int]], tuple[int, int], float]:
    """Coarse-to-fine refinement of a pano building mask (F-SKY19).

    Pipeline:
      1. Strip building pixels at or below each column's waterline
         ("buildings should not be placed on water") if a water mask
         is provided.
      2. Find connected x-clusters of "tall enough" building columns
         in the cleaned coarse mask.
      3. For each cluster, crop the pano RGB to (band_band, x_left..x_right)
         and run SegFormer at full ``fine_input_size`` on the crop —
         this dedicates the model's full receptive field to the actual
         buildings, giving sharper inter-tower gaps.
      4. Stitch the fine masks back into pano coordinates.

    Returns ``(refined_pano_mask, cluster_x_ranges, total_fine_inference_s)``.
    When SegFormer is unavailable the input mask is returned unchanged
    so the rest of the pipeline degrades gracefully.
    """
    h, w = pano_bmask.shape[:2]
    coarse = pano_bmask.copy()
    # Water cap.
    if pano_wmask is not None and pano_wmask.any():
        water_top = np.where(
            pano_wmask.any(axis=0),
            pano_wmask.argmax(axis=0),
            h,
        ).astype(np.int32)
        row_idx = np.arange(h)[:, None]
        below_water = (row_idx >= water_top[None, :])
        coarse = coarse & ~below_water
    # Column height threshold + run-length grouping → clusters.
    col_heights = coarse.sum(axis=0)
    is_bld_col = col_heights >= min_column_height_px
    runs: list[list[int]] = []
    in_run = False
    for x, v in enumerate(is_bld_col):
        if v and not in_run:
            runs.append([x, x])
            in_run = True
        elif v:
            runs[-1][1] = x
        elif in_run:
            in_run = False
    merged: list[list[int]] = []
    for run in runs:
        if merged and (run[0] - merged[-1][1]) <= min_gap_px:
            merged[-1][1] = run[1]
        else:
            merged.append(list(run))
    clusters = [(a, b) for a, b in merged if (b - a) >= min_cluster_width_px]
    if not clusters:
        return coarse, [], (0, h - 1), 0.0
    # Building band y-range (for cropping).
    rows_with = coarse.any(axis=1)
    if not rows_with.any():
        return coarse, clusters, (0, h - 1), 0.0
    ys = np.where(rows_with)[0]
    y_top = max(0, int(ys.min()) - 16)
    y_bot = min(h - 1, int(ys.max()) + 16)

    # Start from the COARSE mask, not an empty canvas. The fine SegFormer
    # pass only runs on the detected tall-tower clusters; if we started
    # from zeros, every building OUTSIDE a cluster (the shorter central
    # low-rise, isolated mid-rises) would be silently dropped even though
    # SegFormer correctly masked it in the coarse pass. Seeding from
    # coarse keeps those buildings; the per-cluster fine pass below then
    # OR-adds its sharper silhouette on top (fills glass-tower tops,
    # tightens edges) without ever removing a coarse building.
    refined = coarse.copy()
    fine_total = 0.0
    try:
        # Read the model globals off the module after _ensure_segformer() has
        # set them; a from-import would capture the pre-load None / "cpu".
        from .._core import segmentation as _seg  # noqa: PLC0415
        from .._core.segmentation import (  # noqa: PLC0415
            _ADE20K_BUILDING_CLASSES,
            _ensure_segformer,
        )
        if not _ensure_segformer():
            return coarse, clusters, 0.0
        import torch  # noqa: PLC0415
        import torch.nn.functional as F  # noqa: PLC0415
        from PIL import Image as PILImage  # noqa: PLC0415
        from transformers import SegformerImageProcessor  # noqa: PLC0415

        model = _seg._segformer_model
        processor = SegformerImageProcessor.from_pretrained(_seg._SEGFORMER_MODEL_ID)
        processor.size = {"height": int(fine_input_size), "width": int(fine_input_size)}
        processor.do_resize = True

        # Optional depth predictor for F-SKY21 instance separation.
        depth_predictor = None
        if use_depth:
            try:
                from ..depth_estimation import predict_pano_depth  # noqa: PLC0415
                depth_predictor = predict_pano_depth
            except Exception as exc:
                logger.warning(f"[multires] depth unavailable: {exc}")

        depth_total = 0.0
        depth_cuts_total = 0
        for (xL, xR) in clusters:
            crop = pano_img[y_top: y_bot + 1, xL: xR + 1]
            t0 = time.perf_counter()
            pil = PILImage.fromarray(crop)
            inputs = processor(images=pil, return_tensors="pt")
            if _seg._segformer_device != "cpu":
                inputs = {k: v.to(_seg._segformer_device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = model(**inputs)
            upsampled = F.interpolate(
                outputs.logits, size=(crop.shape[0], crop.shape[1]),
                mode="bilinear", align_corners=False)
            labels = upsampled.squeeze(0).argmax(dim=0).cpu().numpy()
            fine_total += time.perf_counter() - t0
            fine_bld = np.isin(labels, _ADE20K_BUILDING_CLASSES)
            # F-SKY21: depth-driven instance separation. Predict depth on
            # the crop; force-cut columns where the median building-pixel
            # depth jumps sharply between neighbours so the rule-based
            # splitter emits one segment per tower instead of one merged
            # blob. Cheap (~0.5-1 s per crop on CPU).
            if depth_predictor is not None:
                try:
                    td = time.perf_counter()
                    depth_crop = depth_predictor(np.asarray(crop))
                    depth_total += time.perf_counter() - td
                    pre_count = int(fine_bld.sum())
                    fine_bld = _split_by_depth_discontinuity(
                        fine_bld, depth_crop)
                    if int(fine_bld.sum()) < pre_count:
                        depth_cuts_total += 1
                except Exception as exc:
                    logger.warning(f"[multires] depth split skipped on cluster: {exc}")
            refined[y_top: y_bot + 1, xL: xR + 1] |= fine_bld
        if depth_predictor is not None:
            logger.info(f"[multires] depth refinement: {depth_total:.2f}s "
                        f"(cut applied on {depth_cuts_total}/{len(clusters)} clusters)")
    except Exception as exc:
        logger.warning(f"[multires] fine refinement failed: {exc}")
        return coarse, clusters, (y_top, y_bot), 0.0

    return refined, clusters, (y_top, y_bot), fine_total


def _pano_sliding_window_split(
    pano_mask: np.ndarray | None,
    pano_image: np.ndarray | None,
    det_fn,
    window_w: int = 360,
    stride: int = 280,
    iou_dedup: float = 0.5,
    pano_depth: np.ndarray | None = None,
    depth_jump_thresh: float = 0.03,
) -> list[dict]:
    """F-SKY22 — run the per-view splitter in a sliding window across the
    pano and merge results.

    ``detect_buildings_from_mask`` runs connected-component analysis
    globally and caps splits per component (``max_splits_per_component=
    24``). On a 1800-col pano where the entire skyline is one connected
    blob, that cap clips real towers and the absolute-px peak thresholds
    don't scale. Walking a window-wide slice gives each window its own
    component analysis + split budget. Dedupe by horizontal IoU since
    the windows overlap.
    """
    if pano_mask is None or pano_mask.size == 0:
        return []
    H, W = pano_mask.shape[:2]
    if W <= window_w:
        return list(det_fn(pano_mask, image=pano_image))

    segs: list[dict] = []
    x = 0
    n_windows = 0
    while True:
        x1 = min(W, x + window_w)
        m_win = pano_mask[:, x:x1]
        img_win = pano_image[:, x:x1] if pano_image is not None else None
        local = det_fn(m_win, image=img_win)
        for s in local:
            s2 = dict(s)
            s2["x_left"] = int(s["x_left"]) + x
            s2["x_right"] = int(s["x_right"]) + x
            s2["mid_x"] = int(s["mid_x"]) + x
            s2["peak_x"] = int(s["peak_x"]) + x
            segs.append(s2)
        n_windows += 1
        if x1 >= W:
            break
        x += stride

    # Dedupe overlapping segments: same tower observed in two adjacent
    # windows produces two near-identical entries. Keep the wider one
    # (more confident silhouette) when horizontal IoU >= threshold.
    segs.sort(key=lambda s: s["x_right"] - s["x_left"], reverse=True)
    kept: list[dict] = []

    def _iou(a: dict, b: dict) -> float:
        aL, aR = a["x_left"], a["x_right"]
        bL, bR = b["x_left"], b["x_right"]
        inter = max(0, min(aR, bR) - max(aL, bL))
        union = max(aR, bR) - min(aL, bL)
        return inter / union if union > 0 else 0.0

    for s in segs:
        if all(_iou(s, k) < iou_dedup for k in kept):
            kept.append(s)

    # Phantom-bbox filter: the sliding window picks up sub-threshold
    # blobs at window edges (water reflections, faint sky-edge noise).
    # Reject any segment whose mask density inside its own bbox is
    # below 15% or whose absolute mask pixel count is < 250 — real
    # towers from the SegFormer building mask are denser than that.
    filtered: list[dict] = []
    for s in kept:
        xL, xR = int(s["x_left"]), int(s["x_right"])
        yT = int(s.get("top_y", 0))
        yB = int(s.get("base_y", pano_mask.shape[0] - 1))
        if xR <= xL or yB <= yT:
            continue
        yT = max(0, min(pano_mask.shape[0] - 1, yT))
        yB = max(0, min(pano_mask.shape[0] - 1, yB))
        crop = pano_mask[yT: yB + 1, xL: xR + 1]
        if crop.size == 0:
            continue
        count = int(crop.sum())
        density = count / float(crop.size)
        if count < 250 or density < 0.15:
            continue
        filtered.append(s)

    filtered.sort(key=lambda s: s["mid_x"])

    # F-SKY24 Phase 1: depth-fused post-split. The mask-based splitter
    # can't separate adjacent towers at different DEPTHS but similar
    # heights (col_counts has no valley between them). After the main
    # splitter runs, walk each output segment; if its per-column median
    # depth has a strong jump inside it, split the segment at the jump.
    # Cheap (~per-segment slice over the depth pano).
    depth_split: list[dict] = []
    n_depth_cuts = 0
    max_grad_seen: list[float] = []  # diagnostic: per-segment max gradient
    if pano_depth is not None and pano_depth.shape[:2] == pano_mask.shape[:2]:
        for s in filtered:
            xL = int(s["x_left"])
            xR = int(s["x_right"])
            yT = max(0, int(s.get("top_y", 0)))
            yB = min(pano_mask.shape[0] - 1, int(s.get(
                "base_y", pano_mask.shape[0] - 1)))
            if xR - xL < 30 or yB <= yT:
                depth_split.append(s)
                continue
            # Per-column median depth within this segment's mask band.
            seg_mask = pano_mask[yT: yB + 1, xL: xR + 1]
            seg_depth = pano_depth[yT: yB + 1, xL: xR + 1]
            col_md = np.full(seg_mask.shape[1], np.nan, dtype=np.float32)
            for ci in range(seg_mask.shape[1]):
                col_vals = seg_depth[seg_mask[:, ci], ci]
                if col_vals.size >= 6:
                    col_md[ci] = float(np.median(col_vals))
            # Smooth and take signed gradient.
            if np.all(np.isnan(col_md)):
                depth_split.append(s)
                continue
            # Interpolate over NaN gaps so the gradient is meaningful.
            valid = ~np.isnan(col_md)
            if valid.sum() < 6:
                depth_split.append(s)
                continue
            idx = np.arange(col_md.size)
            col_md_filled = np.interp(idx, idx[valid], col_md[valid])
            # Smooth lightly then differentiate.
            try:
                from scipy.ndimage import gaussian_filter1d  # noqa: PLC0415
                from scipy.signal import find_peaks  # noqa: PLC0415
                smoothed = gaussian_filter1d(col_md_filled, sigma=2.0)
                grad = np.abs(np.diff(smoothed))
                # Diagnostic: track the max gradient inside the inner
                # band (excluding outer margin) so we can see if a
                # threshold lower than `depth_jump_thresh` would catch
                # genuine splits.
                margin = max(8, int(0.15 * (xR - xL)))
                inner = grad[margin: max(margin + 1, grad.size - margin)]
                if inner.size:
                    max_grad_seen.append(float(inner.max()))
                peaks, props = find_peaks(
                    grad, height=depth_jump_thresh, distance=margin)
                if peaks.size:
                    peaks = peaks[(peaks >= margin)
                                  & (peaks <= grad.size - margin)]
            except Exception:
                peaks = np.empty(0, dtype=np.int64)
            if peaks.size == 0:
                depth_split.append(s)
                continue
            # Split at each peak. Build sub-segments by carving the
            # original [xL, xR] range at peak offsets.
            cut_xs = sorted({int(xL + p + 1) for p in peaks})
            prev_x = xL
            for cx in cut_xs + [xR + 1]:
                if cx - prev_x < 20:  # too thin, skip
                    prev_x = cx
                    continue
                sub = dict(s)
                sub["x_left"] = int(prev_x)
                sub["x_right"] = int(cx - 1)
                sub["mid_x"] = (sub["x_left"] + sub["x_right"]) // 2
                # Use the column with peak col_counts for the new peak_x;
                # fall back to mid_x when counts are unavailable.
                inner = pano_mask[yT: yB + 1, prev_x: cx]
                if inner.size:
                    cc = inner.sum(axis=0)
                    sub["peak_x"] = int(prev_x + int(np.argmax(cc)))
                else:
                    sub["peak_x"] = sub["mid_x"]
                depth_split.append(sub)
                n_depth_cuts += 1
                prev_x = cx
            n_depth_cuts -= 1  # parent counted as 1 split
        # Re-sort after splits.
        depth_split.sort(key=lambda s: s["mid_x"])
    else:
        depth_split = filtered

    # Diagnostic: distribution of within-segment max depth-gradients.
    # Helps decide if the threshold is too high or there's just no
    # signal to cut on.
    grad_diag = ""
    if max_grad_seen:
        arr = sorted(max_grad_seen)
        n = len(arr)
        p50 = arr[n // 2]
        p90 = arr[min(n - 1, int(0.9 * n))]
        p_max = arr[-1]
        grad_diag = (
            f" | depth-grad p50={p50:.3f} p90={p90:.3f} max={p_max:.3f}"
            f" thr={depth_jump_thresh:.3f}"
        )
    logger.info(f"[pano_split] sliding window: W={W} win={window_w} stride={stride} "
                f"-> {n_windows} windows, {len(segs)} raw -> {len(kept)} dedup "
                f"-> {len(filtered)} density -> {len(depth_split)} after depth "
                f"split (+{n_depth_cuts}){grad_diag}")
    return depth_split
