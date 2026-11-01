"""Part of ``city2stl.skyline._report_plots._pano_plots`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..region_types import SeedViewRegistration
import logging
from pathlib import Path

from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

from ._plot_utils import (
    POLAR_MAX_M,
    _osm_height_m,
)

logger = logging.getLogger('city2stl.skyline._report_plots._pano_plots')


def _draw_pano_bboxes_inplace(
    png_path: Path,
    matched_segments: list,
) -> None:
    """Open a pano PNG and draw the matched-segment bboxes + numbered
    badges on top (in pano coordinates). Mutates the file in place.

    Uses the segment ``seed_index`` for both the badge number and the
    colour, so a tower has the same identity across RGB / SegFormer
    mask / Depth layers.
    """
    if not matched_segments:
        return
    try:
        import cv2  # noqa: PLC0415

        from ..region_config import _SEGMENT_PALETTE  # noqa: PLC0415
    except Exception:
        return
    if not png_path.exists():
        return
    try:
        img = cv2.imread(str(png_path))
        if img is None:
            return
        H, W = img.shape[:2]
        for seg in matched_segments:
            if seg.get("matched_projection") is None:
                continue
            badge_n = int(seg.get("seed_index", 0)) or 1
            color_bgr = _SEGMENT_PALETTE[
                (badge_n - 1) % len(_SEGMENT_PALETTE)]
            # _SEGMENT_PALETTE is RGB; cv2 wants BGR.
            color = (int(color_bgr[2]), int(color_bgr[1]), int(color_bgr[0]))
            xL = max(0, min(W - 1, int(seg.get("x_left", 0))))
            xR = max(0, min(W - 1, int(seg.get("x_right", 0))))
            yT = max(0, min(H - 1, int(seg.get("top_y", 0))))
            yB = max(0, min(H - 1, int(seg.get("base_y", H - 1))))
            if xR <= xL or yB <= yT:
                continue
            cv2.rectangle(img, (xL, yT), (xR, yB), color, 2)
            badge_x = xL + 2
            badge_y = max(18, yT + 2)
            cv2.circle(img, (badge_x + 9, badge_y + 7), 11, color, -1)
            cv2.circle(img, (badge_x + 9, badge_y + 7), 11,
                       (0, 0, 0), 1, cv2.LINE_AA)
            cv2.putText(
                img, str(badge_n),
                (badge_x + (3 if badge_n < 10 else 0), badge_y + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
            )
            # Detected height label just below the badge (OSM-tagged
            # height of the matched building). Skipped when untagged.
            h_m = seg.get("height_m")
            if h_m is not None:
                htxt = f"{float(h_m):.0f}m"
                ty = badge_y + 26
                cv2.putText(img, htxt, (xL + 2, ty),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0),
                            3, cv2.LINE_AA)
                cv2.putText(img, htxt, (xL + 2, ty),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, color,
                            1, cv2.LINE_AA)
        cv2.imwrite(str(png_path), img)
    except Exception as exc:
        logger.warning("pano bbox overlay skipped for %s: %s",
                       png_path.name, exc)


def _draw_pano_north_line_inplace(
    png_path: Path,
    headings_per_col,
) -> None:
    """Overlay vertical guide lines marking the columns where the pano
    believes the bearing is N (0°), E (90°), S (180°), W (270°). Lets the
    reviewer confirm the column-to-bearing mapping against the actual
    skyline at a glance. N is yellow (most prominent); E/S/W are thinner
    cyan/green/magenta.
    """
    if headings_per_col is None or not png_path.exists():
        return
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except Exception:
        return
    try:
        headings = np.asarray(headings_per_col)
        if headings.size == 0:
            return
        img = cv2.imread(str(png_path))
        if img is None:
            return
        H, W = img.shape[:2]
        # (bearing, label, BGR colour, line thickness).
        cardinals = [
            (0.0, "N", (0, 255, 255), 2),    # yellow, thick
            (90.0, "E", (0, 230, 0), 1),     # green
            (180.0, "S", (255, 160, 0), 1),  # blue-ish
            (270.0, "W", (255, 0, 255), 1),  # magenta
        ]
        for bearing, label, color, thick in cardinals:
            diff = np.abs(((headings - bearing + 180.0) % 360.0) - 180.0)
            cx = int(np.argmin(diff))
            cx = max(0, min(W - 1, cx))
            # Black underlay then coloured line for contrast on sky/water.
            cv2.line(img, (cx, 0), (cx, H - 1), (0, 0, 0),
                     thick + 2, cv2.LINE_AA)
            cv2.line(img, (cx, 0), (cx, H - 1), color,
                     thick, cv2.LINE_AA)
            (tw, th), _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
            tx = max(2, min(W - tw - 2, cx - tw // 2))
            cv2.rectangle(img, (tx - 4, 2), (tx + tw + 4, th + 10),
                          (0, 0, 0), -1)
            cv2.putText(img, label, (tx, th + 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        color, 2, cv2.LINE_AA)
        cv2.imwrite(str(png_path), img)
    except Exception as exc:
        logger.warning("pano cardinal lines overlay skipped for %s: %s",
                       png_path.name, exc)


def _render_pano_minimap_polar_png(
    out_path: Path,
    sv: SeedViewRegistration,
    osm_data: dict,
    buildings_by_id: dict,
    pano_result,
    *,
    satellite_bg: bool = False,
    radius_m: float = POLAR_MAX_M,
) -> bool:
    """Render the seed's footprint or satellite minimap as a polar plot
    (camera at centre, 360° around). Bearing 0° at top, clockwise.

    Replaces the rectangular auto-zoomed minimap with a polar view that
    matches the Reconstruction tab's coordinate frame so a reviewer can
    align bearings directly between the two. ``satellite_bg=True``
    fetches Web-Mercator satellite tiles for the bbox covering the
    radius and renders them as a polar background via pcolormesh.
    """
    try:
        import math  # noqa: PLC0415

        import matplotlib  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
        from matplotlib.patches import Polygon as MPLPolygon  # noqa: PLC0415

        from ..region_config import _SEGMENT_PALETTE  # noqa: PLC0415
        from ..region_data import _bearing_deg, _distance_m  # noqa: PLC0415
    except Exception as exc:
        logger.warning("polar minimap skipped: %s", exc)
        return False
    seed_lat = sv.seed_lat
    seed_lon = sv.seed_lon

    fig = plt.figure(figsize=(7.0, 7.0))
    try:
        ax = fig.add_subplot(111, projection="polar")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_ylim(0, radius_m)
        ax.grid(True, alpha=0.3)

        # Satellite imagery background: fetch a bbox covering ±radius_m
        # around the seed, then resample into polar (theta, r) cells so it
        # composites cleanly under the OSM/matched overlays.
        if satellite_bg:
            try:
                from ..satellite_image import fetch_region_satellite  # noqa: PLC0415
                deg_per_m_lat = 1.0 / M_PER_DEG_LAT
                deg_per_m_lon = 1.0 / m_per_deg_lon(seed_lat)
                margin_m = radius_m * 1.1
                bbox = (
                    seed_lat - margin_m * deg_per_m_lat,
                    seed_lon - margin_m * deg_per_m_lon,
                    seed_lat + margin_m * deg_per_m_lat,
                    seed_lon + margin_m * deg_per_m_lon,
                )
                sat_img, project, _meta = fetch_region_satellite(
                    bbox, target_m_per_px=1.5)  # higher-res satellite tiles
                H_sat, W_sat = sat_img.shape[:2]
                seed_px_x, seed_px_y = project(seed_lon, seed_lat)
                # Estimate meters-per-pixel by stepping 100m east.
                east_px_x, _ = project(
                    seed_lon + 100.0 * deg_per_m_lon, seed_lat)
                m_per_px = 100.0 / max(1e-6, abs(east_px_x - seed_px_x))
                n_r, n_theta = 110, 240
                r_edges = np.linspace(0.0, radius_m, n_r + 1)
                theta_edges = np.linspace(0.0, 2.0 * math.pi, n_theta + 1)
                r_centers = 0.5 * (r_edges[:-1] + r_edges[1:])
                theta_centers = 0.5 * (theta_edges[:-1] + theta_edges[1:])
                TH, RR = np.meshgrid(theta_centers, r_centers)
                # theta=0 -> North (image -y); clockwise positive.
                dx_px = (RR * np.sin(TH)) / m_per_px
                dy_px = -(RR * np.cos(TH)) / m_per_px
                sx = np.clip(np.rint(seed_px_x + dx_px).astype(np.int32),
                             0, W_sat - 1)
                sy = np.clip(np.rint(seed_px_y + dy_px).astype(np.int32),
                             0, H_sat - 1)
                sampled = sat_img[sy, sx]  # (n_r, n_theta, 3)
                # pcolormesh on polar axes wants C of shape (n_r, n_theta)
                # plus a `colors` array; we instead pass facecolors via
                # set_facecolors after a dummy mesh.
                THETA_E, R_E = np.meshgrid(theta_edges, r_edges)
                mesh = ax.pcolormesh(
                    THETA_E, R_E, np.zeros((n_r, n_theta)),
                    shading="flat", zorder=0,
                )
                rgba = np.ones((n_r, n_theta, 4), dtype=np.float32)
                rgba[..., :3] = sampled.astype(np.float32) / 255.0
                mesh.set_array(None)
                mesh.set_facecolors(rgba.reshape(-1, 4))
                mesh.set_edgecolors("none")
            except Exception as exc:
                logger.warning("satellite background fetch failed: %s", exc)

        # Context OSM layers UNDER the buildings: parks (green) + water
        # (coastline/rivers, blue). Gives the footprint geographic context so
        # the orientation is readable. Drawn faint so buildings stay dominant.
        def _draw_osm_layer(features, rgba, zorder):
            for feat in (features or [])[:6000]:
                try:
                    geom = feat.get("geometry") or {}
                    gt = geom.get("type")
                    if gt == "Polygon":
                        rings = [geom.get("coordinates", [[]])[0]]
                    elif gt == "MultiPolygon":
                        rings = [poly[0] for poly in geom.get("coordinates", [])]
                    elif gt in ("LineString", "MultiLineString"):
                        rings = ([geom.get("coordinates", [])] if gt == "LineString"
                                 else geom.get("coordinates", []))
                    else:
                        continue
                    for ring in rings:
                        if not ring or len(ring) < 2:
                            continue
                        pts, any_in = [], False
                        for (lon, lat) in ring:
                            bb = _bearing_deg(seed_lat, seed_lon, lat, lon)
                            dd = _distance_m(seed_lat, seed_lon, lat, lon)
                            if dd <= radius_m:
                                any_in = True
                            pts.append((math.radians(bb), min(dd, radius_m)))
                        if not any_in or len(pts) < 2:
                            continue
                        if gt in ("LineString", "MultiLineString"):
                            th = [p[0] for p in pts]
                            rr = [p[1] for p in pts]
                            ax.plot(th, rr, color=rgba, linewidth=1.2,
                                    zorder=zorder)
                        elif len(pts) >= 3:
                            ax.add_patch(MPLPolygon(
                                pts, closed=True, color=rgba, zorder=zorder,
                                linewidth=0.0))
                except Exception:
                    continue

        _draw_osm_layer((osm_data.get("waterways", {}) or {}).get("features"),
                        (0.32, 0.55, 0.85, 0.30), zorder=0.4)
        _draw_osm_layer((osm_data.get("green", {}) or {}).get("features"),
                        (0.42, 0.68, 0.36, 0.32), zorder=0.5)

        # Grey OSM buildings (context). Drop anything past the visible
        # radius so we don't waste rendering on off-plot polygons.
        fill_alpha = 0.35 if satellite_bg else 0.5
        grey_rgba = (0.4, 0.4, 0.4, fill_alpha) if satellite_bg else (0.6, 0.6, 0.6, fill_alpha)
        for feat in (osm_data.get("buildings", {}).get("features") or [])[:6000]:
            try:
                geom = feat.get("geometry") or {}
                if geom.get("type") not in ("Polygon", "MultiPolygon"):
                    continue
                coords_iter: list = []
                if geom.get("type") == "Polygon":
                    coords_iter = [geom.get("coordinates", [[]])[0]]
                else:
                    coords_iter = [poly[0] for poly in geom.get("coordinates", [])]
                for ring in coords_iter:
                    if not ring:
                        continue
                    pts_polar: list[tuple[float, float]] = []
                    any_in = False
                    for (lon, lat) in ring:
                        bearing = _bearing_deg(seed_lat, seed_lon, lat, lon)
                        dist = _distance_m(seed_lat, seed_lon, lat, lon)
                        if dist <= radius_m:
                            any_in = True
                        pts_polar.append(
                            (math.radians(bearing), min(dist, radius_m)))
                    if any_in and len(pts_polar) >= 3:
                        poly = MPLPolygon(
                            pts_polar, closed=True, color=grey_rgba,
                            zorder=1, linewidth=0.0,
                        )
                        ax.add_patch(poly)
            except Exception:
                continue

        # Matched footprints in seed_index colours. Skip towers past
        # ``radius_m`` — those are out of frame.
        if pano_result is not None and pano_result.matched_segments:
            for seg in pano_result.matched_segments:
                m = seg.get("matched_projection")
                if not m:
                    continue
                fid = str(m.get("feature_id", ""))
                badge_n = int(seg.get("seed_index", 0)) or 1
                r, g, b = _SEGMENT_PALETTE[
                    (badge_n - 1) % len(_SEGMENT_PALETTE)]
                color = (r / 255.0, g / 255.0, b / 255.0)
                b_rec = buildings_by_id.get(fid)
                if b_rec is None:
                    continue
                bearing = _bearing_deg(
                    seed_lat, seed_lon,
                    b_rec.centroid_lat, b_rec.centroid_lon)
                dist = _distance_m(
                    seed_lat, seed_lon,
                    b_rec.centroid_lat, b_rec.centroid_lon)
                if dist > radius_m:
                    continue
                # Hollow marker + plain text so the satellite/footprint
                # background stays visible (filled badges obstructed it).
                ax.scatter([math.radians(bearing)], [dist],
                           facecolors="none", edgecolors=color,
                           s=90, marker="o", linewidth=1.4, zorder=3)
                ax.text(math.radians(bearing), dist * 1.06,
                        str(badge_n), fontsize=8, fontweight="bold",
                        ha="center", va="center", color=color, zorder=4)

        # Seed marker at centre.
        ax.scatter([0], [0], c="red", s=160, marker="*",
                   edgecolors="black", linewidth=0.6, zorder=5)
        title = ("Satellite footprints (polar)" if satellite_bg
                 else "Footprints (polar)")
        ax.set_title(title, fontsize=10)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=110, bbox_inches="tight")
    finally:
        plt.close(fig)
    return True


def _render_pano_heights_polar_png(
    out_path: Path,
    sv: SeedViewRegistration,
    osm_data: dict,
    pano_result,
    *,
    radius_m: float = POLAR_MAX_M,
) -> bool:
    """Render OSM building footprints in polar coordinates coloured by
    their OSM-tagged height. Same axis frame as Footprints / Satellite
    / Reconstruction; lets the reviewer cross-reference per-tower
    height with the splitter's matched segments at a glance.

    Buildings without an OSM height tag (and no ``building:levels``)
    are drawn faint grey so the reader sees they exist but knows the
    height is unknown.
    """
    try:
        import math  # noqa: PLC0415

        import matplotlib  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
        from matplotlib.cm import ScalarMappable  # noqa: PLC0415
        from matplotlib.colors import Normalize  # noqa: PLC0415
        from matplotlib.patches import Polygon as MPLPolygon  # noqa: PLC0415

        from ..region_data import _bearing_deg, _distance_m  # noqa: PLC0415
    except Exception as exc:
        logger.warning("heights polar plot skipped: %s", exc)
        return False
    seed_lat = sv.seed_lat
    seed_lon = sv.seed_lon
    fig = plt.figure(figsize=(7.5, 7.0))
    try:
        ax = fig.add_subplot(111, projection="polar")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_ylim(0, radius_m)
        ax.grid(True, alpha=0.3)

        # Pass 1: collect features that fall inside the radius and parse
        # their heights so we can compute a consistent colour scale.
        keep: list[tuple[list[tuple[float, float]], float | None]] = []
        feats_iter = (osm_data.get("buildings", {}).get("features") or [])[:6000]
        for feat in feats_iter:
            geom = feat.get("geometry") or {}
            if geom.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            rings = (
                [geom.get("coordinates", [[]])[0]]
                if geom.get("type") == "Polygon"
                else [poly[0] for poly in geom.get("coordinates", [])]
            )
            for ring in rings:
                if not ring:
                    continue
                pts: list[tuple[float, float]] = []
                any_in = False
                for (lon, lat) in ring:
                    bb = _bearing_deg(seed_lat, seed_lon, lat, lon)
                    dd = _distance_m(seed_lat, seed_lon, lat, lon)
                    if dd <= radius_m:
                        any_in = True
                    pts.append((math.radians(bb), min(dd, radius_m)))
                if any_in and len(pts) >= 3:
                    keep.append((pts, _osm_height_m(feat)))

        tagged_heights = [h for (_pts, h) in keep if h is not None and h > 0.0]
        if tagged_heights:
            # Clip the colour scale at the 95th percentile so a single 400 m
            # outlier (Willis Tower etc.) doesn't squash the rest into one
            # colour bin.
            cmap = plt.get_cmap("plasma")
            hi = float(np.percentile(tagged_heights, 95))
            lo = float(np.percentile(tagged_heights, 5))
            if hi <= lo:
                hi = lo + 1.0
            norm = Normalize(vmin=lo, vmax=hi)
            n_tagged = len(tagged_heights)
            n_untagged = len(keep) - n_tagged
        else:
            cmap = None
            norm = None
            n_tagged = 0
            n_untagged = len(keep)

        for pts, h in keep:
            if h is not None and h > 0.0 and cmap is not None:
                color = cmap(norm(min(h, norm.vmax)))
                edge = "black"
                ew = 0.3
            else:
                color = (0.65, 0.65, 0.65, 0.45)
                edge = "none"
                ew = 0.0
            ax.add_patch(MPLPolygon(
                pts, closed=True, color=color, zorder=2,
                linewidth=ew, edgecolor=edge,
            ))

        ax.scatter([0], [0], c="red", s=160, marker="*",
                   edgecolors="black", linewidth=0.6, zorder=5)
        title = (
            f"OSM-tagged heights (polar) — "
            f"{n_tagged} tagged · {n_untagged} untagged"
        )
        ax.set_title(title, fontsize=10)
        if cmap is not None and norm is not None:
            sm = ScalarMappable(norm=norm, cmap=cmap)
            sm.set_array([])
            cbar = fig.colorbar(
                sm, ax=ax, pad=0.08, shrink=0.75,
                label="OSM height (m)")
            cbar.ax.tick_params(labelsize=8)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=110, bbox_inches="tight")
    finally:
        plt.close(fig)
    return True
