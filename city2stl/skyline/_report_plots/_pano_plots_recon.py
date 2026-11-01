"""Part of ``city2stl.skyline._report_plots._pano_plots`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
import logging
from pathlib import Path

from ._plot_utils import (
    POLAR_MAX_M,
    _build_osm_nearest_per_degree,
)

logger = logging.getLogger('city2stl.skyline._report_plots._pano_plots')


def _render_pano_reconstruction_png(
    out_path: Path,
    pano_result,
    buildings_by_id: dict,
    osm_data: dict | None = None,
) -> bool:
    """Plot pano-scope depth-derived vs OSM-projected positions in the
    seed's bearing/distance frame. One scatter per matched OSM polygon
    in pano_result.matched_segments. ``osm_data`` (optional) adds the
    same grey OSM context polygons that the Footprints/Satellite tabs
    draw, so the three panels share both axis limits and background
    geometry.
    """
    if pano_result is None or not pano_result.matched_segments:
        return False
    try:
        import math  # noqa: PLC0415

        import matplotlib  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415

        from ..depth_estimation import predict_pano_depth  # noqa: PLC0415
        from ..region_config import _SEGMENT_PALETTE  # noqa: PLC0415
        from ..region_data import _bearing_deg, _distance_m  # noqa: PLC0415
    except Exception as exc:
        logger.warning("pano reconstruction skipped: %s", exc)
        return False
    pano_img = pano_result.pano_image
    if pano_img is None or pano_img.size == 0:
        return False
    fig = None
    try:
        depth = getattr(pano_result, "pano_depth", None)
        if depth is None:
            depth = predict_pano_depth(np.asarray(pano_img))
        H, W = depth.shape[:2]
        # Capture raw d_inv per matched fid so we can anchor scale below.
        # Far-tower exclusion (>POLAR_MAX_M) is applied at the OSM step:
        # any segment whose matched building sits outside the unified
        # polar radius is dropped from BOTH lists, so it doesn't appear
        # in this view nor get used to calibrate the scale anchor.
        recon: list = []  # tuples: (bearing, d_inv, fid, color, badge)
        osm_xy: list = []  # tuples: (bearing, dist_m, fid, color, badge)
        for seg in pano_result.matched_segments:
            m = seg.get("matched_projection")
            if not m:
                continue
            fid = str(m.get("feature_id", ""))
            b_rec = buildings_by_id.get(fid)
            if b_rec is None:
                continue
            ob = _bearing_deg(
                pano_result.seed_lat, pano_result.seed_lon,
                b_rec.centroid_lat, b_rec.centroid_lon)
            od = _distance_m(
                pano_result.seed_lat, pano_result.seed_lon,
                b_rec.centroid_lat, b_rec.centroid_lon)
            if od > POLAR_MAX_M:
                continue
            badge = int(seg.get("seed_index", 0))
            r, g, b = _SEGMENT_PALETTE[
                (badge - 1) % len(_SEGMENT_PALETTE)]
            color = (r / 255.0, g / 255.0, b / 255.0)
            peak_x = int(round(float(
                seg.get("peak_x", (seg["x_left"] + seg["x_right"]) / 2))))
            base_y = int(round(float(seg.get("base_y", H - 1))))
            peak_x = max(0, min(W - 1, peak_x))
            base_y = max(0, min(H - 1, base_y))
            d_val = float(depth[base_y, peak_x])
            d_inv = 1.0 - max(0.0, min(1.0, d_val))
            seg_bearing = float(seg.get(
                "true_bearing_deg",
                pano_result.headings_per_col[peak_x]
                if pano_result.headings_per_col is not None else 0.0,
            )) % 360.0
            recon.append((seg_bearing, d_inv, fid, color, badge))
            osm_xy.append((ob, od, fid, color, badge))

        if not recon and not osm_xy:
            return False

        # Depth->distance scale: the OSM-anchored value computed once in
        # the pipeline (median od / sqrt(d_inv) over matched towers) and
        # shared with the Distance scan so the two plots agree.
        osm_dist_by_fid: dict[str, float] = {fid: od for (_, od, fid, _, _) in osm_xy}
        scale = float(getattr(pano_result, "depth_scale", 1450.0))
        # Far-tower OSM clamp: Depth Anything V2 inverse-depth saturates
        # past ~1 km. For matched fids with OSM-true distance over the
        # FAR_CLAMP_M threshold, override the depth-derived distance with
        # the OSM ground truth — depth would otherwise undershoot by 30-
        # 60% on that tail. Closer towers keep their depth-derived dist
        # so the circle-vs-triangle visual still surfaces registration
        # disagreements where depth IS reliable.
        FAR_CLAMP_M = 1200.0
        recon_scaled: list = []
        n_clamped = 0
        for (bearing, d_inv, fid, color, badge) in recon:
            sqrt_dist = math.sqrt(d_inv) * scale
            osm_dist = osm_dist_by_fid.get(fid)
            if osm_dist is not None and osm_dist > FAR_CLAMP_M:
                final_dist = osm_dist
                n_clamped += 1
            else:
                final_dist = sqrt_dist
            recon_scaled.append((bearing, final_dist, fid, color, badge))
        if n_clamped:
            logger.info(
                "pano recon: clamped %d/%d far towers (>%.0fm) to OSM",
                n_clamped, len(recon), FAR_CLAMP_M,
            )

        fig, ax = plt.subplots(subplot_kw={"projection": "polar"},
                               figsize=(7.0, 7.0))
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)

        # Grey OSM context polygons — same source as the Footprints tab,
        # so all three polar plots share both the axis and the
        # background geometry.
        if osm_data is not None:
            try:
                from matplotlib.patches import Polygon as MPLPolygon  # noqa: PLC0415
                grey_rgba = (0.6, 0.6, 0.6, 0.5)
                for feat in (osm_data.get("buildings", {}).get(
                        "features") or [])[:6000]:
                    geom = feat.get("geometry") or {}
                    if geom.get("type") not in ("Polygon", "MultiPolygon"):
                        continue
                    if geom.get("type") == "Polygon":
                        rings = [geom.get("coordinates", [[]])[0]]
                    else:
                        rings = [poly[0]
                                 for poly in geom.get("coordinates", [])]
                    for ring in rings:
                        if not ring:
                            continue
                        pts_polar: list[tuple[float, float]] = []
                        any_in = False
                        for (lon, lat) in ring:
                            bb = _bearing_deg(
                                pano_result.seed_lat, pano_result.seed_lon,
                                lat, lon)
                            dd = _distance_m(
                                pano_result.seed_lat, pano_result.seed_lon,
                                lat, lon)
                            if dd <= POLAR_MAX_M:
                                any_in = True
                            pts_polar.append(
                                (math.radians(bb), min(dd, POLAR_MAX_M)))
                        if any_in and len(pts_polar) >= 3:
                            ax.add_patch(MPLPolygon(
                                pts_polar, closed=True, color=grey_rgba,
                                zorder=1, linewidth=0.0))
            except Exception as exc:
                logger.warning("recon OSM context skipped: %s", exc)

        # Per-degree depth × mask intersection silhouette. For each
        # integer bearing 0-359°, find the pano column closest to that
        # heading, walk down to the topmost building-mask pixel in that
        # column, and read depth at that pixel. The result is a 360°
        # "nearest-building" curve — a powerful diagnostic for bearing
        # recovery (rotate the OSM polygons until grey shapes and the
        # blue silhouette overlap and you've found the correction).
        hpc = getattr(pano_result, "headings_per_col", None)
        bmask = getattr(pano_result, "pano_building_mask", None)
        if hpc is not None and bmask is not None:
            try:
                hpc_arr = np.asarray(hpc).astype(np.float32)
                bmask_arr = np.asarray(bmask).astype(bool)
                Hm, Wm = bmask_arr.shape[:2]
                # Build a 360-entry lookup: for each degree, the column
                # index whose heading is closest. Vectorised — avoids a
                # 360x W broadcast.
                col_for_deg = np.empty(360, dtype=np.int32)
                for deg in range(360):
                    d = np.abs(((hpc_arr - deg + 180.0) % 360.0) - 180.0)
                    col_for_deg[deg] = int(np.argmin(d))
                # Per-degree silhouette via column_building_distance
                # ("lower_median": median depth over the lower half of
                # the building-mask pixels in the column). Less noisy
                # than a single base pixel, still near the ground-contact
                # distance. Pre-compute OSM-nearest so we can clamp the
                # silhouette where depth saturates (>1200 m).
                from ..depth_estimation import (  # noqa: PLC0415
                    column_building_distance,
                )
                osm_curve_for_clamp = _build_osm_nearest_per_degree(
                    osm_data,
                    pano_result.seed_lat, pano_result.seed_lon,
                    min_area_m2=150.0,
                    require_height_tag=False,
                    median_kernel=5,
                )
                DEPTH_SATURATION_M = 1200.0
                silh_theta: list[float] = []
                silh_dist: list[float] = []
                for deg in range(360):
                    col = int(col_for_deg[deg])
                    dist_m = column_building_distance(
                        depth, bmask_arr, col,
                        scale=scale, mode="base")
                    if dist_m is None:
                        continue
                    # OSM clamp on saturated values: depth past 1200 m
                    # carries no signal, so prefer OSM ground truth
                    # when available.
                    if (dist_m > DEPTH_SATURATION_M
                            and not np.isnan(osm_curve_for_clamp[deg])
                            and osm_curve_for_clamp[deg] < dist_m):
                        dist_m = float(osm_curve_for_clamp[deg])
                    if dist_m <= POLAR_MAX_M:
                        silh_theta.append(math.radians(float(deg)))
                        silh_dist.append(dist_m)

                # OSM-derived "ideal" silhouette: reuse the curve we
                # already computed for the saturation clamp above.
                try:
                    valid = ~np.isnan(osm_curve_for_clamp)
                    if valid.any():
                        deg_idx = np.arange(360)[valid]
                        on_t = np.radians(deg_idx.astype(np.float32))
                        on_d = osm_curve_for_clamp[valid]
                        ax.scatter(on_t, on_d, c="#ff7f0e", s=6,
                                   marker="o", alpha=0.65, zorder=2.4,
                                   edgecolors="none",
                                   label="OSM nearest")
                except Exception as exc:
                    logger.warning("OSM-nearest signal skipped: %s", exc)
                if silh_theta:
                    th_arr = np.array(silh_theta)
                    d_arr = np.array(silh_dist)
                    # Already in 0->359 degree order (we iterate range(360)).
                    # Break the line at gaps > 3° (water / sky stretches
                    # with no mask) so matplotlib doesn't draw a diagonal
                    # across the centre of the plot.
                    gap_thresh = math.radians(3.0)
                    diffs = np.diff(th_arr)
                    breaks = np.flatnonzero(diffs > gap_thresh)
                    starts = np.concatenate(([0], breaks + 1))
                    ends = np.concatenate((breaks + 1, [th_arr.size]))
                    for s, e in zip(starts, ends, strict=False):
                        if e - s >= 2:
                            ax.plot(th_arr[s:e], d_arr[s:e],
                                    color="#1f77b4", linewidth=1.2,
                                    alpha=0.55, zorder=2)
                    ax.scatter(th_arr, d_arr, c="#1f77b4", s=8,
                               marker="o", alpha=0.7, zorder=2.5,
                               edgecolors="none")
            except Exception as exc:
                logger.warning("per-degree silhouette skipped: %s", exc)

        # Hollow markers + plain coloured text (no filled badges) so the
        # grey OSM context polygons + satellite stay visible underneath.
        # Depth-derived = hollow circle, OSM-projected = hollow triangle.
        osm_by_fid = {d[2]: d for d in osm_xy}
        for (bearing, dist, fid, color, badge) in recon_scaled:
            theta = math.radians(bearing)
            ax.scatter([theta], [dist], facecolors="none", edgecolors=color,
                       s=70, marker="o", linewidth=1.3, zorder=3)
            if badge > 0:
                ax.text(theta, dist, str(badge), fontsize=7,
                        ha="center", va="center", zorder=4,
                        color=color, fontweight="bold")
            if fid in osm_by_fid:
                ob, od, _, _, _ = osm_by_fid[fid]
                ax.plot(
                    [theta, math.radians(ob)], [dist, od],
                    color=color, linewidth=0.8, alpha=0.5, zorder=1,
                )
        for (bearing, dist, _fid, color, _badge) in osm_xy:
            theta = math.radians(bearing)
            ax.scatter([theta], [dist], facecolors="none", edgecolors=color,
                       s=100, marker="^", linewidth=1.3, zorder=2)
        # Unified axis with Footprints / Satellite tabs (POLAR_MAX_M).
        ax.set_ylim(0, POLAR_MAX_M)
        ax.set_title(
            "360° depth → footprint reconstruction "
            "(circle: depth-derived  ▲: OSM-projected)",
            fontsize=10,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=110, bbox_inches="tight")
        return True
    except Exception as exc:
        logger.warning("pano reconstruction render failed: %s", exc)
        return False
    finally:
        if fig is not None:
            plt.close(fig)
