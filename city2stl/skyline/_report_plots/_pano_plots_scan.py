"""Part of ``city2stl.skyline._report_plots._pano_plots`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
import logging
from pathlib import Path

from ._plot_utils import (
    NO_BUILDING_M,
    POLAR_MAX_M,
    _bearing_xcorr_offset,
    _build_osm_nearest_per_degree,
    colormap_u8,
)

logger = logging.getLogger('city2stl.skyline._report_plots._pano_plots')


def _render_pano_segformer_overlay_png(
    out_path: Path,
    pano_image,
    sv_list: list,
    fov_deg: float,
    spin_step_deg: float = 30.0,
    anchor_offset_deg: float = 0.0,
    pano_result=None,
) -> bool:
    """Stitch the per-view SegFormer building masks into a pano-coordinates
    overlay and write a 4-class colored PNG (building/sky/water/vegetation
    over a grayscale base).

    Uses the already-cached per-view masks on each SeedViewRegistration
    (building_mask, sky_mask, water_mask, vegetation_mask) so no
    additional SegFormer inference is needed.
    """
    try:
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        from .._core.pano import stitch_pano_mask_channel  # noqa: PLC0415
    except Exception as exc:
        logger.warning("pano mask stitch unavailable: %s", exc)
        return False
    if pano_image is None or pano_image.size == 0:
        return False
    # Prefer the pre-computed per-class pano masks stashed on the
    # StitchedPanoResult — they were stitched from the SAME view set
    # and sort order as the pano RGB, so the column geometry agrees by
    # construction. Fallback: re-stitch from sv_list (legacy path, may
    # misalign if cached_views ≠ stitch_source).
    bld = getattr(pano_result, "pano_building_mask", None)
    water = getattr(pano_result, "pano_water_mask", None)
    veg = getattr(pano_result, "pano_vegetation_mask", None)
    if bld is None:
        spin_for_stitch: list[dict] = []
        for sv in sv_list:
            if (sv.building_mask is None and sv.sky_mask is None
                    and sv.water_mask is None
                    and sv.vegetation_mask is None):
                continue
            _raw = getattr(sv, "raw_image", None)
            spin_for_stitch.append({
                "image": _raw if _raw is not None else sv.image,
                "geo_heading": (sv.heading + anchor_offset_deg) % 360.0,
                "building_mask": sv.building_mask,
                "sky_mask": sv.sky_mask,
                "water_mask": sv.water_mask,
                "vegetation_mask": sv.vegetation_mask,
            })
        if not spin_for_stitch:
            return False
        try:
            bld = stitch_pano_mask_channel(
                spin_for_stitch, fov_deg, spin_step_deg, "building_mask")
            water = stitch_pano_mask_channel(
                spin_for_stitch, fov_deg, spin_step_deg, "water_mask")
            veg = stitch_pano_mask_channel(
                spin_for_stitch, fov_deg, spin_step_deg, "vegetation_mask")
        except Exception as exc:
            logger.warning("pano mask channel stitch failed: %s", exc)
            return False
    if bld is None:
        return False
    H, W = bld.shape[:2]
    # Resize pano_image to the mask width/height if needed.
    pi = np.asarray(pano_image)
    if pi.shape[1] != W or pi.shape[0] != H:
        pi_img = Image.fromarray(pi).resize((W, H), Image.LANCZOS)
        pi = np.asarray(pi_img)
    # float32 throughout (T22): float64 temporaries cost ~1 GB for a Commons pano
    gray = (np.float32(0.299) * pi[..., 0] + np.float32(0.587) * pi[..., 1]
            + np.float32(0.114) * pi[..., 2])
    base = np.stack([gray, gray, gray], axis=-1) * np.float32(0.85)

    def _blend(arr, mask, color, alpha):
        if mask is None:
            return arr
        m = np.asarray(mask).astype(bool)
        if m.shape != arr.shape[:2]:
            return arr
        col = np.array(color, dtype=np.float32)
        arr[m] = arr[m] * (1.0 - alpha) + col * alpha
        return arr

    # Sky overlay omitted: the entire upper half of the pano gets tinted
    # and obscures the visible clouds + reflective glass facades, making
    # the diagnostic harder rather than easier to read. The "sky" class
    # is the visual default — its absence is more informative than its
    # presence. Vegetation/water alphas pulled down so the building red
    # stays dominant.
    base = _blend(base, water, (27, 76, 204), 0.40)
    base = _blend(base, veg, (54, 160, 74), 0.35)
    inst = getattr(pano_result, "pano_instances", None)
    if inst is not None and np.asarray(inst).shape == base.shape[:2] and np.asarray(inst).max() > 0:
        # one colour per MobileSAM building instance, white outlines; building pixels SAM
        # left unlabelled stay in the class red
        inst = np.asarray(inst)
        base = _blend(base, bld & (inst == 0) if bld is not None else None, (230, 66, 53), 0.55)
        pal = np.random.default_rng(3).integers(40, 255, (int(inst.max()) + 1, 3)).astype(np.float32)
        on = inst > 0
        base[on] = base[on] * 0.4 + pal[inst[on]] * 0.6
        edge = np.zeros_like(on)
        edge[1:, :] |= inst[1:, :] != inst[:-1, :]
        edge[:, 1:] |= inst[:, 1:] != inst[:, :-1]
        base[edge & on] = 255.0
    else:
        base = _blend(base, bld, (230, 66, 53), 0.55)
    out = np.clip(base, 0, 255).astype(np.uint8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(out_path, optimize=True)
    return True


def _render_pano_bearing_scan_png(
    out_path: Path,
    pano_result,
    osm_data: dict | None,
) -> bool:
    """Render a 2D bearing-vs-distance scan: x = bearing 0-360°,
    y = distance in metres. Two curves overlaid:

      - blue: depth-derived per-degree nearest-building distance
        (the same signal drawn radially on the Reconstruction polar
        plot)
      - orange: OSM-derived per-degree nearest-building distance
        (the ground-truth signal)

    The horizontal offset between the two curves IS the bearing
    correction — the value of ``Δθ`` that maximises their cross-
    correlation tells us how much the pano's column-to-bearing mapping
    needs to be rotated to match OSM ground truth.
    """
    try:
        import matplotlib  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415

        from ..depth_estimation import predict_pano_depth  # noqa: PLC0415
    except Exception as exc:
        logger.warning("bearing scan skipped: %s", exc)
        return False
    if pano_result is None:
        return False
    hpc = getattr(pano_result, "headings_per_col", None)
    bmask = getattr(pano_result, "pano_building_mask", None)
    depth = getattr(pano_result, "pano_depth", None)
    if hpc is None or bmask is None:
        return False
    if depth is None:
        if pano_result.pano_image is None:
            return False
        try:
            depth = predict_pano_depth(np.asarray(pano_result.pano_image))
        except Exception:
            return False

    fig = None
    try:
        hpc_arr = np.asarray(hpc).astype(np.float32)
        bmask_arr = np.asarray(bmask).astype(bool)
        depth_arr = np.asarray(depth)
        Hd, Wd = depth_arr.shape[:2]

        # Match column → degree assignment used by the polar render.
        col_for_deg = np.empty(360, dtype=np.int32)
        for deg in range(360):
            d = np.abs(((hpc_arr - deg + 180.0) % 360.0) - 180.0)
            col_for_deg[deg] = int(np.argmin(d))

        # Use the OSM-anchored depth->distance scale computed once in the
        # pipeline and shared with the Reconstruction polar plot, so the
        # two plots agree (previously the scan hardcoded 1450 while the
        # recon anchored to OSM — the ~1000 vs ~600 m mismatch).
        scale = float(getattr(pano_result, "depth_scale", 1450.0))

        # Per-degree depth silhouette — median over the lower half of
        # the building-mask pixels per column (column_building_distance
        # "lower_median"); same metric the pipeline's bearing recovery
        # uses, so the scan and the applied correction agree.
        from ..depth_estimation import (  # noqa: PLC0415
            column_building_distance,
        )
        silh = np.full(360, np.nan, dtype=np.float32)
        for deg in range(360):
            col = int(col_for_deg[deg])
            dist_col = column_building_distance(
                depth_arr, bmask_arr, col,
                scale=scale, mode="base")
            if dist_col is not None:
                silh[deg] = float(min(POLAR_MAX_M, dist_col))

        # Per-degree OSM nearest distance, with min-area filtering and
        # a median smoother to suppress polygon-vertex sampling spikes.
        osm_nearest_arr = _build_osm_nearest_per_degree(
            osm_data,
            pano_result.seed_lat, pano_result.seed_lon,
            min_area_m2=150.0,
            require_height_tag=False,
            median_kernel=5,
        )

        # The per-degree silhouette + OSM clamp feed the cross-
        # correlation only (NOT the plotted blue line). Keep an
        # unclamped raw copy for the cross-correlation sentinel fill.
        DEPTH_SATURATION_M = 1200.0
        silh_xc = silh.copy()
        for d in range(360):
            if (silh_xc[d] > DEPTH_SATURATION_M
                    and not np.isnan(osm_nearest_arr[d])
                    and osm_nearest_arr[d] < silh_xc[d]):
                silh_xc[d] = osm_nearest_arr[d]
        silh_xc = np.where(np.isnan(silh_xc), NO_BUILDING_M, silh_xc)
        osm_xc = np.where(np.isnan(osm_nearest_arr),
                          NO_BUILDING_M, osm_nearest_arr)
        shift, best_mae, mae_curve, applied, improve = _bearing_xcorr_offset(
            silh_xc, osm_xc)

        # ---- Column-indexed plot so the scan aligns 1:1 under the pano
        # image (same x = same pano column). For each pano column we
        # show the RAW depth distance (blue, unclamped — what depth
        # actually says) and the OSM-nearest distance at that column's
        # bearing (orange). Vertical guides mark N/E/S/W; ticks along the
        # bottom mark columns that contain building mask.
        col_dist = np.full(Wd, np.nan, dtype=np.float32)
        col_osm = np.full(Wd, np.nan, dtype=np.float32)
        col_geom = np.full(Wd, np.nan, dtype=np.float32)
        col_has_bldg = bmask_arr.any(axis=0)
        # F-SKY25 geometric model params (fitted in the pipeline).
        gK = getattr(pano_result, "geom_K", None)
        gH = getattr(pano_result, "geom_horizon_row", None)
        for c in range(Wd):
            dc = column_building_distance(
                depth_arr, bmask_arr, c, scale=scale, mode="base")
            if dc is not None:
                col_dist[c] = float(min(POLAR_MAX_M, dc))
            # Horizon-pitch geometric distance: dist = K / (base_row - H).
            # Uses the building's base ROW (which varies clearly with
            # distance) rather than the saturated depth value.
            if gK is not None and gH is not None:
                rows = np.flatnonzero(bmask_arr[:, c])
                if rows.size:
                    base_row = float(rows[-1])
                    denom = base_row - float(gH)
                    if denom > 1.0:
                        col_geom[c] = float(min(POLAR_MAX_M, gK / denom))
            bdeg = int(round(float(hpc_arr[c]))) % 360
            v = osm_nearest_arr[bdeg]
            if not np.isnan(v):
                col_osm[c] = v

        # Scale the (saturated, near-flat) depth distance signal to the
        # OSM signal's STANDARD DEVIATION: standardise the depth line then
        # re-spread it to match OSM's mean+std over the columns where both
        # exist. This stretches depth's tiny variation to OSM's range so
        # the comparison is on equal footing (the median-only calibration
        # left it flat). Uses only columns with both signals present.
        _both = (~np.isnan(col_dist)) & (~np.isnan(col_osm))
        if int(_both.sum()) >= 8:
            d_mu, d_sd = float(np.mean(col_dist[_both])), float(np.std(col_dist[_both]))
            o_mu, o_sd = float(np.mean(col_osm[_both])), float(np.std(col_osm[_both]))
            if d_sd > 1e-6:
                col_dist = (col_dist - d_mu) / d_sd * o_sd + o_mu
                col_dist = np.clip(col_dist, 0.0, POLAR_MAX_M)

        # Two stacked panels: bearing-recovery MAE/cross-corr curve on
        # top (full width), the column-indexed distance scan below (also
        # full width, so it stays 1:1 aligned with the pano image's
        # columns — not compressed by a side panel).
        fig, (axc, ax) = plt.subplots(
            2, 1, figsize=(14.0, 5.0),
            gridspec_kw={"height_ratios": [1.0, 2.6], "hspace": 0.42})
        xc = np.arange(Wd)
        ax.plot(xc, col_dist, color="#1f77b4", linewidth=1.0,
                alpha=0.55, label="Depth distance (saturated)")
        if np.isfinite(col_geom).any():
            ax.plot(xc, col_geom, color="#2ca02c", linewidth=1.4,
                    alpha=0.9, label="Geometric distance (base-row)")
        ax.plot(xc, col_osm, color="#ff7f0e", linewidth=1.2,
                alpha=0.9, label="OSM nearest")
        ax.set_xlim(0, Wd)
        ax.set_ylim(0, POLAR_MAX_M)

        # Cardinal-direction guides at the columns whose bearing is
        # nearest each of N/E/S/W.
        cardinals = [(0.0, "N", "#d62728"), (90.0, "E", "#2ca02c"),
                     (180.0, "S", "#1f77b4"), (270.0, "W", "#9467bd")]
        for bearing, lbl, col_c in cardinals:
            diff = np.abs(((hpc_arr - bearing + 180.0) % 360.0) - 180.0)
            cx = int(np.argmin(diff))
            ax.axvline(cx, color=col_c, linewidth=1.4, alpha=0.7,
                       linestyle="--")
            ax.text(cx, POLAR_MAX_M * 0.96, lbl, color=col_c,
                    fontsize=10, fontweight="bold", ha="center",
                    va="top",
                    bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                              edgecolor=col_c, alpha=0.85))

        # Building presence markers: a short tick at the bottom for each
        # column that contains building mask, so it's easy to line up
        # the curves with the towers in the pano above.
        bcols = np.flatnonzero(col_has_bldg)
        if bcols.size:
            ax.scatter(bcols, np.full(bcols.size, POLAR_MAX_M * 0.02),
                       marker="|", c="#444444", s=40, alpha=0.5,
                       linewidths=0.8, label="building columns")

        # Labels INSIDE the axes (no outside padding) so the data area
        # spans the full figure width and lines up 1:1 with the pano
        # image above (which is full-bleed). y-ticks point inward with
        # their numbers drawn just inside the left edge; the axis caption
        # is a small in-plot annotation.
        ax.tick_params(axis="y", direction="in", pad=-4, labelsize=7)
        for _t in ax.get_yticklabels():
            _t.set_horizontalalignment("left")
        ax.set_ylabel("")
        ax.set_xlabel("")
        ax.tick_params(axis="x", direction="in", pad=-12, labelsize=7)
        ax.text(0.004, 0.04, "distance (m) — x: pano column, "
                "dashed = N/E/S/W", transform=ax.transAxes, fontsize=7,
                color="#555", ha="left", va="bottom")
        # Verdict reflects the best_mae (destination-quality) gate:
        # APPLIED when the recovered bearing aligns depth↔OSM well
        # (best_mae <= ceiling); otherwise the best offset is a poor fit
        # and we keep the anchor.
        if applied:
            verdict = (f"APPLIED, recovered alignment {best_mae:.0f} m "
                       f"(improve {improve*100:.0f}%)")
        else:
            verdict = f"SKIPPED, best alignment {best_mae:.0f} m too poor"
        ax.set_title(
            "Distance vs pano column — depth (blue) vs OSM (orange). "
            f"Bearing correction {shift:+d}° [{verdict}], MAE "
            f"{best_mae:.0f} m.",
            fontsize=10,
        )
        ax.grid(True, alpha=0.2)
        ax.legend(loc="upper right", fontsize=8)

        # ---- Top panel: bearing-recovery MAE (used) + normalised
        # cross-correlation (compare) vs rotation offset. mae_curve[k] is
        # the mean |depth-OSM| mismatch when the depth signal is rotated
        # k°; its MIN is the recovered shift. The cross-corr is shown only
        # to illustrate why MAE is the production metric (it's magnitude/
        # co-variation driven, broken by the 3000 m sentinel). A deep,
        # narrow MAE dip = confident; flat/shallow = anchor already
        # aligned or ambiguous.
        offs = np.arange(360)
        signed = np.where(offs <= 180, offs, offs - 360).astype(np.float32)
        order = np.argsort(signed)
        l_mae, = axc.plot(signed[order], mae_curve[order], color="#1f77b4",
                          linewidth=1.2, label="MAE (used) ↓ better")
        axc.axvline(0.0, color="#999999", linewidth=1.0, linestyle=":")
        axc.scatter([shift], [best_mae], c="#1f77b4", s=30, zorder=5,
                    edgecolors="black", linewidths=0.5,
                    label=f"MAE min {shift:+d}°")
        # Normalised cross-correlation (Pearson) per rotation — compare.
        s0 = silh_xc - float(np.mean(silh_xc))
        o0 = osm_xc - float(np.mean(osm_xc))
        denom = float(np.linalg.norm(s0) * np.linalg.norm(o0)) or 1.0
        ncc = np.array([float(np.dot(np.roll(s0, k), o0))
                        for k in range(360)], dtype=np.float32) / denom
        ncc_best = int(np.argmax(ncc))
        ncc_shift = ncc_best if ncc_best <= 180 else ncc_best - 360
        axc2 = axc.twinx()
        l_ncc, = axc2.plot(signed[order], ncc[order], color="#2ca02c",
                           linewidth=1.0, alpha=0.6,
                           label="cross-corr (compare) ↑ better")
        axc2.scatter([ncc_shift], [ncc[ncc_best]], c="#2ca02c", s=26,
                     marker="^", zorder=5, edgecolors="black",
                     linewidths=0.5)
        # Top-panel labels INSIDE too (cross-corr ticks inside-right, MAE
        # ticks inside-left) so both panels span the full figure width.
        axc2.set_ylabel("")
        axc2.tick_params(axis="y", direction="in", pad=-4, labelsize=7,
                         labelcolor="#2ca02c")
        for _t in axc2.get_yticklabels():
            _t.set_horizontalalignment("right")
        axc.set_xlim(-180, 180)
        axc.set_xticks(np.arange(-180, 181, 30))
        axc.set_ylim(0, max(float(np.nanmax(mae_curve)) * 1.05, 1.0))
        axc.set_xlabel("")
        axc.set_ylabel("")
        axc.tick_params(axis="y", direction="in", pad=-4, labelsize=7,
                        labelcolor="#1f77b4")
        for _t in axc.get_yticklabels():
            _t.set_horizontalalignment("left")
        axc.tick_params(axis="x", direction="in", pad=-12, labelsize=7)
        axc.set_title("Bearing recovery: MAE (blue, used) vs cross-corr "
                      "(green, compare) per rotation° — dip at 0 = aligned",
                      fontsize=9)
        axc.grid(True, alpha=0.25)
        axc.legend([l_mae, l_ncc], [l_mae.get_label(), l_ncc.get_label()],
                   loc="upper center", fontsize=7, ncol=2)

        # Axes span the full figure width (left=0,right=1) so the bottom
        # scan's columns map 1:1 to the pano image; with all labels moved
        # inside, "tight" adds no side padding (only vertical for titles).
        fig.subplots_adjust(left=0.0, right=1.0, top=0.93, bottom=0.05,
                            hspace=0.32)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=100, bbox_inches="tight", pad_inches=0.02)
        return True
    except Exception as exc:
        logger.warning("bearing scan render failed: %s", exc)
        return False
    finally:
        if fig is not None:
            plt.close(fig)


def _render_pano_depth_png(
    out_path: Path, pano_image, pano_result=None,
) -> bool:
    """Run Depth Anything V2 on the stitched pano and save a colormapped PNG.

    Two layers composited:
      - base: turbo-colormapped raw depth (sky=blue, water=red).
      - overlay: Sobel-magnitude of depth as a bright additive layer that
        highlights tower-to-tower transitions. Without the gradient
        layer the building band squashes into one narrow colour range,
        making per-tower depth changes invisible. The overlay surfaces
        these depth discontinuities where the splitter cuts towers
        apart.
    """
    if pano_image is None or pano_image.size == 0:
        return False
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        from ..depth_estimation import predict_pano_depth  # noqa: PLC0415
        try:
            import matplotlib.pyplot as plt  # noqa: PLC0415
            cmap = plt.get_cmap("turbo")
        except Exception:
            cmap = None
        depth_rel = getattr(pano_result, "pano_depth", None)
        if depth_rel is None:
            depth_rel = predict_pano_depth(np.asarray(pano_image))
        d_min, d_max = float(depth_rel.min()), float(depth_rel.max())
        if d_max - d_min < 1e-6:
            return False
        norm = (depth_rel - d_min) / (d_max - d_min)
        if cmap is not None:
            base = colormap_u8(cmap, norm)
        else:
            base = np.stack(
                [(norm * 255).astype(np.uint8)] * 3, axis=-1)
        # Sobel magnitude of normalized depth. Highlights tower edges
        # (vertical discontinuities) and roofline transitions.
        depth_f = norm.astype(np.float32)
        gx = cv2.Sobel(depth_f, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(depth_f, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx * gx + gy * gy)
        # Normalize by 98th percentile so a few high-grad pixels don't
        # wash out the rest.
        p98 = float(np.percentile(mag, 98))
        if p98 > 1e-6:
            mag = np.clip(mag / p98, 0.0, 1.0)
        # Additive overlay: brighten the base where depth changes.
        # 0.65 weight is a balance — enough to be visible without
        # obscuring the underlying colormap.
        overlay_w = (mag * 255 * 0.65).astype(np.float32)
        composite = base.astype(np.float32)
        composite[..., 0] = np.clip(composite[..., 0] + overlay_w, 0, 255)
        composite[..., 1] = np.clip(composite[..., 1] + overlay_w, 0, 255)
        composite[..., 2] = np.clip(composite[..., 2] + overlay_w, 0, 255)
        out_arr = composite.astype(np.uint8)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(out_arr).save(out_path, optimize=True)
        return True
    except Exception as exc:
        logger.warning("pano depth render failed: %s", exc)
        return False
