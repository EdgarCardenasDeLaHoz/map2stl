"""skyline report_plots — split (A3) (_pano_plots)."""
from __future__ import annotations
import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ._plot_utils import (POLAR_MAX_M, NO_BUILDING_M, _osm_height_m,
                          _bearing_xcorr_offset, _build_osm_nearest_per_degree)

logger = logging.getLogger(__name__)


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
        import numpy as np  # noqa: PLC0415
        from ..region_pdf import _SEGMENT_PALETTE  # noqa: PLC0415
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
    sv: "SeedViewRegistration",
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
        import numpy as np  # noqa: PLC0415
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
        from matplotlib.patches import Polygon as MPLPolygon  # noqa: PLC0415
        from ..region_pdf import (  # noqa: PLC0415
            _bearing_deg, _distance_m, _SEGMENT_PALETTE,
        )
    except Exception as exc:
        logger.warning("polar minimap skipped: %s", exc)
        return False
    seed_lat = sv.seed_lat
    seed_lon = sv.seed_lon

    fig = plt.figure(figsize=(7.0, 7.0))
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
            deg_per_m_lat = 1.0 / 110540.0
            deg_per_m_lon = 1.0 / (
                111320.0 * math.cos(math.radians(seed_lat)))
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
    plt.close(fig)
    return True

def _render_pano_heights_polar_png(
    out_path: Path,
    sv: "SeedViewRegistration",
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
        import numpy as np  # noqa: PLC0415
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
        from matplotlib.patches import Polygon as MPLPolygon  # noqa: PLC0415
        from matplotlib.cm import ScalarMappable  # noqa: PLC0415
        from matplotlib.colors import Normalize  # noqa: PLC0415
        from ..region_pdf import (  # noqa: PLC0415
            _bearing_deg, _distance_m,
        )
    except Exception as exc:
        logger.warning("heights polar plot skipped: %s", exc)
        return False
    seed_lat = sv.seed_lat
    seed_lon = sv.seed_lon
    fig = plt.figure(figsize=(7.5, 7.0))
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
    plt.close(fig)
    return True

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
        from ..pipeline import (  # noqa: PLC0415
            stitch_pano_mask_channel,
        )
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
    sky = getattr(pano_result, "pano_sky_mask", None)
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
            sky = stitch_pano_mask_channel(
                spin_for_stitch, fov_deg, spin_step_deg, "sky_mask")
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
    gray = (0.299 * pi[..., 0] + 0.587 * pi[..., 1] + 0.114 * pi[..., 2])
    base = np.stack([gray, gray, gray], axis=-1).astype(np.float32) * 0.85

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
        import math  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
        from ..depth_estimation import predict_pano_depth  # noqa: PLC0415
        from ..region_pdf import _bearing_deg, _distance_m  # noqa: PLC0415
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
        plt.close(fig)
        return True
    except Exception as exc:
        logger.warning("bearing scan render failed: %s", exc)
        return False

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
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        import cv2  # noqa: PLC0415
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
            base = (cmap(norm) * 255).astype(np.uint8)[..., :3]
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
        import numpy as np  # noqa: PLC0415
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
        from ..depth_estimation import predict_pano_depth  # noqa: PLC0415
        from ..region_pdf import _bearing_deg, _distance_m  # noqa: PLC0415
        from ..region_pdf import _SEGMENT_PALETTE  # noqa: PLC0415
    except Exception as exc:
        logger.warning("pano reconstruction skipped: %s", exc)
        return False
    pano_img = pano_result.pano_image
    if pano_img is None or pano_img.size == 0:
        return False
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
                    for s, e in zip(starts, ends):
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
        for (bearing, dist, fid, color, badge) in osm_xy:
            theta = math.radians(bearing)
            ax.scatter([theta], [dist], facecolors="none", edgecolors=color,
                       s=100, marker="^", linewidth=1.3, zorder=2)
        # Unified axis with Footprints / Satellite tabs (POLAR_MAX_M).
        ax.set_ylim(0, POLAR_MAX_M)
        ax.set_title(
            f"360° depth → footprint reconstruction "
            f"(circle: depth-derived  ▲: OSM-projected)",
            fontsize=10,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        return True
    except Exception as exc:
        logger.warning("pano reconstruction render failed: %s", exc)
        return False


__all__ = [
    '_draw_pano_bboxes_inplace',
    '_draw_pano_north_line_inplace',
    '_render_pano_minimap_polar_png',
    '_render_pano_heights_polar_png',
    '_render_pano_segformer_overlay_png',
    '_render_pano_bearing_scan_png',
    '_render_pano_depth_png',
    '_render_pano_reconstruction_png',
]
