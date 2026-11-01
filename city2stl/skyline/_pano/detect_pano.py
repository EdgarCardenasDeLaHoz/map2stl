"""Part of ``city2stl.skyline._pano.detect`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging
import math
import time

import numpy as np

from .._core.timing import _StepTimer
from .._core.types import BuildingRecord
from ..config import current as _cfg
from ..region_data import _bearing_deg, _distance_m
from ..region_types import SkylinePoint, StitchedPanoResult
from .detect_matching import matched_footprint_overlap
from .detect_multires import (
    _multires_pano_refine,
    _multires_sam_instances,
    _pano_sliding_window_split,
)

logger = logging.getLogger('city2stl.skyline._pano.detect')


def _build_and_detect_pano(
    seed: SkylinePoint,
    cached_views: list[dict],
    seed_buildings: list[BuildingRecord],
    anchor_offset: float,
    spin_step_deg: float,
    prefetch_views: list[dict] | None = None,
    timer: _StepTimer | None = None,
    osm_green_features: list | None = None,
) -> StitchedPanoResult | None:
    """Pass 3: stitch all spin views into a 360° pano and run pano-level matching.

    Returns a ``StitchedPanoResult`` on success, ``None`` on failure (the pano
    path is supplementary — failure must not break the per-view results).

    F-SKY19 (env var ``SKYLINE_CV_MULTIRES=1``): replace the per-view-
    stitched building mask with a coarse-to-fine pano refinement. The
    coarse mask is the existing per-view stitch (free, already computed);
    the fine pass runs SegFormer at full input size on cropped building
    clusters only. Cuts mask noise from per-view stitches, applies a
    water cap so boat wakes aren't tagged as buildings, and ships
    sharper per-tower silhouettes to the pano matcher.
    """
    from contextlib import nullcontext  # noqa: PLC0415

    def _sub(label: str):
        # Level-2 sub-step under the level-1 "pano detection" phase, so
        # the pano-stage work (tiled depth, OSM projection, splitter,
        # matching, bearing recovery) shows up in the timing report.
        return timer.timed(label, level=2) if timer is not None else nullcontext()

    try:
        from .._core.pano import (  # noqa: PLC0415
            project_buildings_to_pano,
            stitch_pano_masks,
            stitch_pano_views,
        )
        from .._core.registration import match_segments_to_buildings as _match_pano
        from .._core.segmentation import (  # noqa: PLC0415
            _neural_sky_and_building_masks,
            _neural_water_mask,
        )
        from .._core.skyline import compute_building_band as _cbb
        from .._core.skyline import detect_buildings_from_mask as _det_pano
        # Use the broader prefetch (all spin headings that returned an
        # image, including those screening rejected) when available so
        # the panorama covers a full 360° view even when only a few
        # spin directions had clear skylines. The pano matcher already
        # relies on the cached/screened views' masks for per-tower
        # accuracy; the additional prefetch views just fill out the
        # visual frame and contribute their (often empty) building
        # mask to the stitch — they can't introduce spurious matches.
        stitch_source = list(prefetch_views) if prefetch_views else list(
            cached_views)
        spin_views_for_pano: list[dict] = []
        # Late import — vegetation mask is opt-in (F-SKY18).
        try:
            from .._core.segmentation import _neural_vegetation_mask  # noqa: PLC0415
        except Exception:
            _neural_vegetation_mask = None
        with _sub("pano mask assembly (SegFormer cache hits/misses)"):
          # Batch the stitch_source label_maps through SegFormer in ONE
          # forward pass before the per-view mask calls below. Without
          # this, each _neural_sky_and_building_masks() call pays its own
          # transformer forward pass (12 lazy passes/seed). It's a no-op
          # when the recovery already prefetched these (non-manual seeds),
          # but for manual-anchor seeds — where we skip recovery and thus
          # its prefetch — this restores the batched fast path instead of
          # 12 separate inferences.
          from .._core.segmentation import prefetch_label_maps as _prefetch_lm  # noqa: PLC0415
          _prefetch_lm([cv.get("image") for cv in stitch_source
                        if cv.get("image") is not None])
          for cv in stitch_source:
            img = cv.get("image")
            if img is None:
                continue
            sky_mask_arr, bmask = _neural_sky_and_building_masks(img)
            wmask = _neural_water_mask(img)
            vmask = None
            if _neural_vegetation_mask is not None:
                try:
                    vmask = _neural_vegetation_mask(img)
                except Exception:
                    vmask = None
            spin_views_for_pano.append({
                "image": img,
                "geo_heading": (cv["geo_heading"] + anchor_offset) % 360.0,
                "building_mask": bmask,
                "water_mask": wmask,
                "sky_mask": sky_mask_arr,
                "vegetation_mask": vmask,
            })
        stitch_out = stitch_pano_views(spin_views_for_pano, seed.fov, spin_step_deg)
        mask_out = stitch_pano_masks(spin_views_for_pano, seed.fov, spin_step_deg)
        if stitch_out is None or mask_out is None:
            return None
        pano_img, pano_headings = stitch_out
        pano_bmask, pano_wmask = mask_out
        # F-SKY18 Phase 4: stitch the vegetation channel early so the
        # bearing_xcorr block can use it as an asymmetric tiebreaker against
        # 180° symmetry (the bay-coastline failure mode). Cheap concat; the
        # later stitch at composite-render time reuses the cached masks.
        try:
            from .._core.pano import stitch_pano_mask_channel as _stitch_chan_xc  # noqa: PLC0415
            pano_veg_arr_xc = _stitch_chan_xc(
                spin_views_for_pano, seed.fov, spin_step_deg, "vegetation_mask")
        except Exception:
            pano_veg_arr_xc = None
        if pano_bmask.shape[1] != pano_img.shape[1]:
            raise ValueError(
                f"pano image/mask width mismatch: "
                f"img={pano_img.shape[1]} mask={pano_bmask.shape[1]}")
        # F-SKY19 multires refinement (opt-in).
        sam_instances: list[dict] = []
        if _cfg().multires:
            refined, clusters, band_y, fine_t = _multires_pano_refine(
                pano_img, pano_bmask, pano_wmask)
            logger.info(f"[multires] seed={seed.name} clusters={len(clusters)} "
                        f"fine_inference={fine_t:.2f}s "
                        f"refined_columns_with_building={int((refined.any(axis=0)).sum())}/"
                        f"{int((pano_bmask.any(axis=0)).sum())}")
            pano_bmask = refined
            # F-SKY20: MobileSAM per-instance silhouettes prompted by
            # OSM building centroids projected into the pano. Capped to
            # ``MAX_PROMPTS_PER_CLUSTER`` nearest-by-distance candidates
            # per cluster — without this, a 3000-building region produces
            # 200+ s of SAM inference (run validated this empirically).
            if _cfg().f_sky5 and clusters:
                MAX_PROMPTS_PER_CLUSTER = 15
                _pano_projs_for_sam = project_buildings_to_pano(
                    seed_buildings, seed.lat, seed.lon, pano_headings)
                band_mid_y = (band_y[0] + band_y[1]) // 2
                # Bucket candidates per cluster, then sort by forward
                # distance (nearer first) and cap.
                per_cluster: list[list[tuple[float, int, int, str]]] = [
                    [] for _ in clusters]
                for p in _pano_projs_for_sam:
                    px = int(round(float(p.get("x_px", -1))))
                    if not (0 <= px < pano_img.shape[1]):
                        continue
                    fwd = float(p.get("forward_m", 1e9))
                    for ci, (xL, xR) in enumerate(clusters):
                        if xL <= px <= xR:
                            per_cluster[ci].append(
                                (fwd, px, band_mid_y,
                                 str(p.get("feature_id", ""))))
                            break
                osm_centroids: list[tuple[int, int, str]] = []
                for bucket in per_cluster:
                    bucket.sort(key=lambda t: t[0])
                    for (_fwd, px, py, fid) in bucket[:MAX_PROMPTS_PER_CLUSTER]:
                        osm_centroids.append((px, py, fid))
                sam_instances, sam_t = _multires_sam_instances(
                    pano_img, clusters, band_y[0], band_y[1],
                    osm_centroids, refined,
                )
                logger.info(f"[multires_sam] seed={seed.name} prompts={len(osm_centroids)} "
                            f"(capped at {MAX_PROMPTS_PER_CLUSTER}/cluster) "
                            f"instances={len(sam_instances)} inference={sam_t:.2f}s")
        pano_band = _cbb(pano_bmask, slack_px=20)
        # F-SKY24 Phase 1: compute pano depth once for both the splitter's
        # depth-aware post-cut pass AND for the downstream HTML renderers
        # (depth pano + reconstruction polar plot). Cheap-ish (~5-10 s
        # CPU) but a clear win to share.
        pano_depth_arr: np.ndarray | None = None
        try:
            from ..depth_estimation import predict_pano_depth_tiled  # noqa: PLC0415
            t_dep = time.perf_counter()
            # Run DA2 ONLY on the building band, not the full frame. We
            # only USE depth at building-base pixels (the silhouette), so
            # the sky (top half) and far water (bottom) are wasted CPU.
            # Cropping to the band roughly halves the inference, AND the
            # band-only [0,1] normalisation no longer wastes range on the
            # huge sky↔water span — so the building depth is LESS
            # compressed (a bonus against the saturation/flatness). The
            # result is placed back into a full-size array (edge-extended
            # outside the band) so downstream base_y indexing is unchanged.
            _pi = np.asarray(pano_img)
            Hp, Wp = _pi.shape[:2]
            with _sub("pano depth (tiled DA2, band-cropped)"):
                if pano_band is not None:
                    y0, y1 = int(pano_band[0]), int(pano_band[1])
                    y0 = max(0, min(Hp - 1, y0))
                    y1 = max(y0 + 1, min(Hp - 1, y1))
                    d_crop = predict_pano_depth_tiled(_pi[y0:y1 + 1])
                    pano_depth_arr = np.empty((Hp, Wp), dtype=np.float32)
                    pano_depth_arr[y0:y1 + 1] = d_crop
                    if y0 > 0:
                        pano_depth_arr[:y0] = d_crop[0]
                    if y1 < Hp - 1:
                        pano_depth_arr[y1 + 1:] = d_crop[-1]
                else:
                    pano_depth_arr = predict_pano_depth_tiled(_pi)
            logger.info(f"[pano_depth] seed={seed.name} {pano_depth_arr.shape} "
                        f"band-cropped tiled-inference "
                        f"{time.perf_counter() - t_dep:.2f}s")
        except Exception as exc:
            logger.warning(f"[pano_depth] unavailable: {exc}")

        # F-SKY24 Phase 3: bearing recovery via silhouette × OSM
        # cross-correlation. The existing satellite-coastline recovery
        # gives the first-pass anchor; this pass refines it (or rescues
        # it when the satellite signal was weak — Chicago is the
        # canonical failure mode where the satellite anchor lands ~180°
        # off). We compute two 360-bin signals (per-degree depth-base
        # distance, per-degree nearest-OSM-building distance), find the
        # rotation that aligns them, and rebase pano_headings — but only
        # when the alignment is BOTH a big improvement over the current
        # anchor AND a distinct global optimum (guards against the
        # Cartagena failure mode where saturation makes many offsets
        # near-tie).
        #
        # NO_BUILDING_M sentinel: bearings with no building mask (sky /
        # water) or no nearby OSM building are set to a large distance
        # rather than left as NaN. This makes "I see a building here but
        # OSM says open space there" a STRONG mismatch signal (3000 vs
        # ~400 m), which is exactly what discriminates a correct
        # rotation from a saturated-tie wrong one.
        NO_BUILDING_M = 3000.0
        bearing_shift_deg = 0.0
        try:
          with _sub("bearing recovery (depth vs OSM rotation sweep)"):
            if pano_depth_arr is not None and pano_bmask is not None:
                Hb, Wb = pano_bmask.shape[:2]
                # Column lookup per integer degree.
                col_for_deg = np.empty(360, dtype=np.int32)
                for d in range(360):
                    dist = np.abs(
                        ((pano_headings - float(d) + 180.0) % 360.0)
                        - 180.0)
                    col_for_deg[d] = int(np.argmin(dist))
                # Depth silhouette with NO_BUILDING sentinel for empty
                # columns. Per column we take the median depth over the
                # LOWER half of the building-mask pixels (mode
                # "lower_median" in column_building_distance) rather than
                # the single base pixel — cuts single-pixel depth noise
                # while staying near the ground-contact distance, which
                # measurably sharpens the cross-correlation peak.
                from ..depth_estimation import (  # noqa: PLC0415
                    column_building_distance,
                )
                silh = np.full(360, NO_BUILDING_M, dtype=np.float32)
                for d in range(360):
                    col = int(col_for_deg[d])
                    dist_col = column_building_distance(
                        pano_depth_arr, pano_bmask, col,
                        scale=1450.0, mode="base")
                    if dist_col is not None:
                        silh[d] = dist_col
                # OSM-nearest per-degree from seed_buildings centroids,
                # also NO_BUILDING-filled where there's nothing nearby.
                osm_near = np.full(360, NO_BUILDING_M, dtype=np.float32)
                bucket = [float("inf")] * 360
                for b_rec in seed_buildings:
                    bb = _bearing_deg(
                        seed.lat, seed.lon,
                        b_rec.centroid_lat, b_rec.centroid_lon)
                    dd = _distance_m(
                        seed.lat, seed.lon,
                        b_rec.centroid_lat, b_rec.centroid_lon)
                    if dd <= 1500.0:
                        bi = int(round(bb)) % 360
                        if dd < bucket[bi]:
                            bucket[bi] = dd
                for d in range(360):
                    if bucket[d] < float("inf"):
                        osm_near[d] = float(bucket[d])
                # Calibrate the depth silhouette's MAGNITUDE to OSM before
                # scoring: scale the (sentinel-excluded) silhouette so its
                # median matches the OSM-nearest median. The recovery only
                # cares about the angular PATTERN, but matching the typical
                # magnitude makes the MAE dip sharp (high prominence) so a
                # confident fine-tune isn't masked by the silhouette
                # sitting at the wrong absolute level (the hardcoded-1450
                # vs OSM-anchored mismatch that made the pipeline curve
                # look flat while the report's scan looked sharp). Pure
                # internal-to-recovery calibration; does NOT change the
                # reported depth distances.
                _real_s = silh[silh < NO_BUILDING_M]
                _real_o = osm_near[osm_near < NO_BUILDING_M]
                if _real_s.size and _real_o.size:
                    _sm = float(np.median(_real_s))
                    _om = float(np.median(_real_o))
                    if _sm > 1e-6:
                        silh = np.where(
                            silh < NO_BUILDING_M, silh * (_om / _sm), silh
                        ).astype(np.float32)
                # Full-vector MAE at every rotation (no NaN now — both
                # arrays are dense thanks to the sentinel).
                scores = np.empty(360, dtype=np.float32)
                for off in range(360):
                    rs = np.roll(silh, off)
                    scores[off] = float(np.mean(np.abs(rs - osm_near)))
                pre_mae = float(scores[0])  # offset 0 = current anchor
                best_off = int(np.argmin(scores))
                best_mae = float(scores[best_off])
                shift = best_off if best_off <= 180 else best_off - 360
                improve = (pre_mae - best_mae) / max(pre_mae, 1e-6)
                # Gate on the QUALITY OF THE DESTINATION, not the shift
                # magnitude or the (possibly random) starting anchor. The
                # silhouette is median-calibrated to OSM above, so
                # best_mae is "average metres of depth↔OSM disagreement
                # AFTER rotating to the recovered bearing". A low best_mae
                # means the recovered bearing genuinely aligns depth with
                # OSM and is therefore trustworthy — regardless of how the
                # initial anchor was set or how far we had to rotate. A
                # high best_mae means even the best offset is a poor fit
                # (ambiguous / wrong), so we keep the existing anchor.
                # Empirically ~750 m separates good recoveries (Cartagena
                # 301-732, Chicago 377-688, Miami 356-665) from poor ones
                # (808-1391). ``improve > 0`` is automatic (best is the
                # argmin) but kept as a guard.
                MAE_CEILING_M = 750.0

                # F-SKY18 Phase 4/5: vegetation-agreement signal. Computed
                # BEFORE the apply decision so a strong ``favors_mirror`` can
                # veto an aggressive rotation (the auto_270_2000m failure mode
                # — 52% MAE improve looked confident, vegetation disagreed).
                v_best: float | None = None
                v_mirror: float | None = None
                veg_verdict = "no_data"
                try:
                    if pano_veg_arr_xc is not None and osm_green_features:
                        from ..osm_water import (  # noqa: PLC0415
                            clip_to_radius as _clip_xc_g,
                        )
                        from ..osm_water import (
                            sample_green_points as _samp_xc_g,
                        )
                        _g_pts = _samp_xc_g(
                            _clip_xc_g(osm_green_features,
                                       (seed.lon, seed.lat), 1500.0),
                            spacing_m=20.0,
                        )
                        if _g_pts:
                            pano_veg_per_deg = np.zeros(360, dtype=bool)
                            for d in range(360):
                                col = int(col_for_deg[d])
                                pano_veg_per_deg[d] = bool(
                                    pano_veg_arr_xc[:, col].any())
                            osm_g_per_deg = np.zeros(360, dtype=bool)
                            for (glon, glat) in _g_pts:
                                gb = _bearing_deg(
                                    seed.lat, seed.lon, glat, glon)
                                osm_g_per_deg[int(round(gb)) % 360] = True
                            v_best = float(
                                (np.roll(pano_veg_per_deg, best_off)
                                 == osm_g_per_deg).mean())
                            v_mirror = float(
                                (np.roll(pano_veg_per_deg,
                                         (best_off + 180) % 360)
                                 == osm_g_per_deg).mean())
                            veg_verdict = (
                                "supports_xcorr"
                                if v_best > v_mirror + 0.02 else
                                ("favors_mirror"
                                 if v_mirror > v_best + 0.02 else "tie")
                            )
                except Exception as _e_veg:
                    logger.warning(f"[F-SKY18-4] seed={seed.name} veg-agree "
                                   f"diagnostic failed: {_e_veg}")

                # Phase 5 veto: when vegetation clearly favors the 180° mirror
                # AND the proposed rotation is large (likely a flip into the
                # mirror), don't apply it. Margin / shift thresholds tuned to
                # the Cartagena measurement: auto_270_2000m had margin 0.14
                # and shift +127°; named seeds (1, 4, 5) sit at shifts ≤16°
                # so the gate never fires on them.
                VEG_MARGIN = 0.10
                LARGE_SHIFT_DEG = 30
                veg_veto = (
                    v_best is not None and v_mirror is not None
                    and (v_mirror - v_best) >= VEG_MARGIN
                    and abs(shift) >= LARGE_SHIFT_DEG
                )

                if (abs(shift) >= 1 and improve > 0.0
                        and best_mae <= MAE_CEILING_M and not veg_veto):
                    logger.info(f"[bearing_xcorr] seed={seed.name} "
                                f"shift {shift:+d}° APPLIED "
                                f"(pre MAE {pre_mae:.0f} -> {best_mae:.0f}m, "
                                f"improve {improve*100:.0f}%)")
                    pano_headings = (pano_headings + float(shift)) % 360.0
                    bearing_shift_deg = float(shift)
                elif veg_veto:
                    logger.info(f"[bearing_xcorr] seed={seed.name} "
                                f"shift {shift:+d}° VETOED by vegetation "
                                f"(veg margin {(v_mirror - v_best):+.2f} "
                                f">= {VEG_MARGIN:.2f} favours mirror; F-SKY18-5)")
                else:
                    logger.info(f"[bearing_xcorr] seed={seed.name} "
                                f"shift {shift:+d}° SKIPPED "
                                f"(best MAE {best_mae:.0f}m > {MAE_CEILING_M:.0f}m "
                                f"— poor alignment, keeping anchor)")
                if v_best is not None:
                    logger.info(f"[F-SKY18-4] seed={seed.name} "
                                f"veg_agree@best={v_best:.2f} "
                                f"@mirror={v_mirror:.2f} "
                                f"verdict={veg_verdict}")
        except Exception as exc:
            logger.warning(f"[bearing_xcorr] skipped: {exc}")

        # F-SKY22 + F-SKY24: sliding-window pano splitter with depth-
        # fused post-cut. The mask-only splitter can't separate adjacent
        # towers at different depths but similar heights; the depth-cut
        # pass adds those splits.
        with _sub("pano splitter (sliding window + OSM-anchored)"):
            pano_segs = _pano_sliding_window_split(
                pano_bmask, pano_img, _det_pano,
                pano_depth=pano_depth_arr)
        # Too-tall filter: a segment spanning (nearly) the full pano
        # height is implausible for a matched building. A real building at
        # distance has its BASE near the horizon (~mid-frame) and its top
        # in the upper frame — it never reaches from sky-top to frame-
        # bottom. A full-height segment is a too-close wall or a mask
        # artifact ("taller than the pano"), and it creates FALSE coverage
        # by overlapping many OSM projections at once. Drop them.
        _Hp = pano_bmask.shape[0]
        _pre_tall = len(pano_segs)
        pano_segs = [
            s for s in pano_segs
            if (int(s.get("base_y", _Hp - 1)) - int(s.get("top_y", 0)))
            < 0.92 * _Hp
        ]
        if len(pano_segs) != _pre_tall:
            logger.info(f"[pano_split] too-tall filter: {_pre_tall} -> "
                        f"{len(pano_segs)} (dropped {_pre_tall - len(pano_segs)} "
                        f"full-frame segments)")
        # Project OSM footprints to pano column coordinates BEFORE the
        # OSM-anchored split below so the splitter has the projection
        # info it needs. Same call we used to make later; just hoisted.
        with _sub("project OSM buildings to pano columns"):
            pano_projs = project_buildings_to_pano(
                seed_buildings, seed.lat, seed.lon, pano_headings)
        # F-SKY24 Phase 1b: OSM-anchored splitter for the pano path.
        # The per-view path already runs ``osm_anchor_silhouettes``
        # (Stage 7 in _seed_multiview_registration); the pano-wide path
        # didn't. When a wide pano segment contains 2+ OSM projections
        # whose x-ranges sit inside it, F-SKY2 splits the segment into
        # per-projection children. This catches the failure mode depth
        # couldn't: adjacent same-distance towers merged into one mask
        # blob.
        try:
            from .._core.registration import osm_anchor_silhouettes  # noqa: PLC0415
            pre_n = len(pano_segs)
            with _sub("OSM-anchored split (per-projection cut)"):
                pano_segs = osm_anchor_silhouettes(
                    pano_segs, pano_projs, building_mask=pano_bmask)
            post_n = len(pano_segs)
            if post_n != pre_n:
                logger.info(f"[pano_split] OSM-anchored split: {pre_n} -> {post_n} "
                            f"(+{post_n - pre_n})")
        except Exception as exc:
            logger.warning(f"[pano_split] OSM-anchored split skipped: {exc}")
        # F-SKY20: prepend SAM instance segments so they're first-class
        # in the splitter output; the matcher will then prefer the SAM
        # silhouettes over rule-based ones when both exist for the same
        # column range (SAM has a per-fid hint via ``sam_prompt_fid``).
        if sam_instances:
            pano_segs = list(sam_instances) + list(pano_segs)
        bbid = {b.feature_id: b for b in seed_buildings}
        with _sub("match pano segments to OSM buildings"):
            pano_matched = _match_pano(pano_segs, pano_projs, bbid)
        # Assign a stable per-tower seed_index across all pano matched
        # segments so the per-layer bbox overlays (drawn next) and the
        # Reconstruction polar plot use a consistent colour + number per
        # OSM feature_id. First-seen fid wins index 1; subsequent
        # segments matched to the same fid reuse the same index.
        pano_index_map: dict[str, int] = {}
        next_idx = 1
        # Sort left-to-right so numbering grows across the pano.
        pano_matched.sort(key=lambda s: int(s.get(
            "peak_x", s.get("mid_x", 0))))
        for seg in pano_matched:
            px = int(seg.get("peak_x", seg.get("mid_x", 0)))
            px = max(0, min(pano_headings.size - 1, px))
            seg["true_bearing_deg"] = float(pano_headings[px])
            m = seg.get("matched_projection")
            if m is None:
                continue
            fid = str(m.get("feature_id", ""))
            if not fid:
                continue
            if fid not in pano_index_map:
                pano_index_map[fid] = next_idx
                next_idx += 1
            seg["seed_index"] = pano_index_map[fid]
            # Annotate the matched building's height so the bbox overlay
            # can label it. Prefer the OSM-tagged height; fall back to a
            # geometric estimate from the pano geometry when untagged:
            #   h = cam_height * (base_y - top_y) / (base_y - horizon_row)
            # which (with K = cam_height·f_y) is independent of f_y if we
            # only have K — so we approximate cam_height from K/f_y using
            # a nominal f_y. Stored as ``height_m`` (+ source flag).
            b = bbid.get(fid)
            h_tag = (float(b.height_tag_m)
                     if b is not None and getattr(b, "height_tag_m", None)
                     is not None else None)
            seg["height_m"] = h_tag
            seg["height_src"] = "osm" if h_tag is not None else None
        n_matched = sum(1 for s in pano_matched if s.get("matched_projection"))

        # OSM-anchored depth->distance scale, computed ONCE here so the
        # Reconstruction polar plot and the Distance scan use the same
        # calibrated value (previously the scan hardcoded 1450 while the
        # recon anchored to OSM, so the same depth read ~1000 m on one and
        # ~600 m on the other). scale = median(osm_dist / sqrt(d_inv))
        # over matched towers; sqrt because DA2 inverse-depth saturates.
        depth_scale = 1450.0
        # Horizon-pitch GEOMETRIC distance fit (F-SKY25). Physics:
        #   dist = camera_height / tan(depression)
        #        = camera_height * f_y / (base_y - horizon_row)
        #        = K / (base_y - horizon_row)
        # so 1/dist is LINEAR in base_y. We fit K and horizon_row by
        # least-squares over matched towers (1/od vs base_y), giving a
        # 2-parameter GEOMETRIC model that PREDICTS distance for every
        # column from its base row. Unlike the saturated DA2 depth value,
        # the base-row position varies strongly and cleanly with distance
        # (close buildings sit low, far ones near the horizon), so this
        # tracks the real near/far variation. Fitting K+horizon_row from
        # OSM is far less circular than a per-building depth scale — it's
        # a physical model whose SHAPE is geometry, not OSM.
        geom_K = None
        geom_horizon_row = None
        geom_pairs: list[tuple[float, float]] = []  # (base_y, osm_dist)
        if pano_depth_arr is not None:
            Hd, Wd = pano_depth_arr.shape[:2]
            ratios: list[float] = []
            for seg in pano_matched:
                m = seg.get("matched_projection")
                if not m:
                    continue
                b = bbid.get(str(m.get("feature_id", "")))
                if b is None:
                    continue
                od = _distance_m(seed.lat, seed.lon,
                                 b.centroid_lat, b.centroid_lon)
                px = max(0, min(Wd - 1, int(seg.get(
                    "peak_x", seg.get("mid_x", 0)))))
                by = max(0, min(Hd - 1, int(seg.get("base_y", Hd - 1))))
                d_inv = 1.0 - max(0.0, min(1.0, float(pano_depth_arr[by, px])))
                if d_inv > 0.05:
                    ratios.append(od / math.sqrt(d_inv))
                if od > 1.0:
                    geom_pairs.append((float(by), float(od)))
            if ratios:
                ratios.sort()
                depth_scale = float(ratios[len(ratios) // 2])
            # Fit the geometric model: 1/od = a*base_y + b  (a = 1/K,
            # b = -horizon_row/K). Need >=3 towers spanning a range of
            # base rows for a stable fit.
            if len(geom_pairs) >= 3:
                bys = np.array([p[0] for p in geom_pairs], dtype=np.float64)
                ods = np.array([p[1] for p in geom_pairs], dtype=np.float64)
                if float(bys.max() - bys.min()) >= 8.0:
                    inv_d = 1.0 / np.maximum(ods, 1e-6)
                    A = np.vstack([bys, np.ones_like(bys)]).T
                    sol, *_ = np.linalg.lstsq(A, inv_d, rcond=None)
                    a, bint = float(sol[0]), float(sol[1])
                    # Slope must be positive (lower base row = closer).
                    if a > 1e-9:
                        geom_K = 1.0 / a
                        geom_horizon_row = -bint / a
                        logger.info(f"[geom_dist] seed={seed.name} "
                                    f"K={geom_K:.0f} horizon_row="
                                    f"{geom_horizon_row:.0f} "
                                    f"(from {len(geom_pairs)} towers)")

        # Stitch the vegetation channel (same source view set & sort order as the
        # building/water stitches above, so the column geometry agrees with
        # ``pano_img``). The sky channel is not stitched: nothing reads it (T21).
        try:
            from .._core.pano import stitch_pano_mask_channel  # noqa: PLC0415
            pano_veg_arr = stitch_pano_mask_channel(
                spin_views_for_pano, seed.fov, spin_step_deg, "vegetation_mask")
        except Exception:
            pano_veg_arr = None

        footprint_overlap = matched_footprint_overlap(
            pano_matched, pano_img.shape[1] if pano_img is not None else 0)

        # Center the pano on NORTH: roll every column-indexed array (image,
        # masks, depth, per-column headings) and shift each matched
        # segment's x-coords so the column whose heading is nearest 0° ends
        # up at the horizontal centre. The stored ``true_bearing_deg`` on
        # each segment is unchanged (it's the real bearing), so the polar
        # plots are unaffected; only the strip's column origin moves.
        try:
            if pano_headings is not None and pano_img is not None:
                _W = pano_img.shape[1]
                _d0 = np.minimum(pano_headings % 360.0,
                                 360.0 - (pano_headings % 360.0))
                north_col = int(np.argmin(_d0))
                roll = (_W // 2) - north_col
                if roll != 0:
                    pano_img = np.roll(pano_img, roll, axis=1)
                    pano_headings = np.roll(pano_headings, roll)
                    pano_bmask = np.roll(pano_bmask, roll, axis=1)
                    if pano_wmask is not None:
                        pano_wmask = np.roll(pano_wmask, roll, axis=1)
                    if pano_veg_arr is not None:
                        pano_veg_arr = np.roll(pano_veg_arr, roll, axis=1)
                    if pano_depth_arr is not None:
                        pano_depth_arr = np.roll(pano_depth_arr, roll, axis=1)
                    for _seg in pano_matched:
                        for _k in ("x_left", "x_right", "peak_x", "mid_x"):
                            if _seg.get(_k) is not None:
                                _seg[_k] = int(_seg[_k] + roll) % _W
        except Exception as _exc:
            logger.warning(f"[pano] north-center roll skipped: {_exc}")

        return StitchedPanoResult(
            seed_name=seed.name,
            seed_lat=seed.lat,
            seed_lon=seed.lon,
            pano_image=pano_img,
            band_y=pano_band,
            matched_segments=pano_matched,
            n_segments=len(pano_segs),
            n_matched=n_matched,
            n_buildings_in_view=len(pano_projs),
            anchor_offset_deg=float(anchor_offset),
            headings_per_col=pano_headings,
            pano_building_mask=pano_bmask,
            pano_water_mask=pano_wmask,
            pano_vegetation_mask=pano_veg_arr,
            pano_depth=pano_depth_arr,
            depth_scale=depth_scale,
            geom_K=geom_K,
            geom_horizon_row=geom_horizon_row,
            bearing_shift_deg=bearing_shift_deg,
            footprint_overlap=footprint_overlap,
        )
    except Exception as e:
        logger.warning(f"[pano] seed={seed.name} failed: {e}")
        return None
