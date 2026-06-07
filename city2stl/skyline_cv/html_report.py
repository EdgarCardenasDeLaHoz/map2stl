"""city2stl.skyline_cv.html_report — F-SKY15 HTML diagnostic report.

Parallel renderer to the existing PDF in ``region_pdf.py``. Same data
sources (``SeedViewRegistration``, ``osm_data``, building-heights
aggregate), different output: a navigable folder of static HTML pages
with embedded minimap PNGs.

Why this exists: the PDF is fine for archival but it's opaque to AI
contributors (who can't read it without screenshots) and to
diff-based review. HTML pages expose every field as DOM text — easy to
grep ("what was the IoU for seed_5?"), easy to diff between runs.

This module **adds** a renderer; it does **not** replace the PDF. The
PDF path continues to be the canonical output.

Public surface:

  render_seed_page(sv, region_name, minimap_rel_path) -> str
      Render one seed's HTML page (string output).
  render_region_index(region_name, seed_views, building_heights) -> str
      Render the region's index page linking to each seed.
  write_region_report(out_dir, region_name, seed_views, osm_data,
                      buildings_by_id, building_heights) -> None
      Top-level: writes index.html + seed_N.html files + minimap PNGs.

Reuse audit (per ``feedback_reuse_city2stl_libraries``):
  - Minimap PNG generation reuses the existing ``_draw_view_minimap``
    (matplotlib figure → BytesIO PNG → file). No second minimap codepath.
  - All data comes from existing ``SeedViewRegistration`` fields
    populated by the PDF pipeline. No shadow data model.
  - Plain string templates + ``html.escape`` from stdlib. No new
    dependency (Jinja, Markdown, etc).
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .region_pdf import SeedViewRegistration

logger = logging.getLogger(__name__)

# Shared polar-plot radius for the Footprints / Satellite / Reconstruction
# tabs. Anything farther than this is dropped from all three views — both
# Depth Anything's reliable range and the OSM context buildings get noisy
# past ~1.5 km, and the unified axis lets reviewers compare the three
# panels at a glance.
POLAR_MAX_M = 1500.0


# ---------------------------------------------------------------------------
# Minimap PNG via the existing matplotlib renderer
# ---------------------------------------------------------------------------

def _save_view_image_png(out_path: Path, sv: "SeedViewRegistration") -> bool:
    """Save the per-view registration overlay image (``sv.image``) as a PNG.

    ``sv.image`` is the BGR/RGB numpy array set during pipeline run; it
    already carries the matched-segment bounding boxes and badges drawn
    by ``_registration_overlay``. We just write it to disk so the HTML
    page can link it.
    """
    image = getattr(sv, "image", None)
    if image is None:
        return False
    try:
        from PIL import Image
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image).save(out_path, optimize=True)
        return True
    except Exception as exc:
        logger.warning(
            "F-SKY15 view image save failed for seed=%s view=%s: %s",
            sv.seed_name, getattr(sv, "heading", "?"), exc,
        )
        return False


def _render_screening_map_png(out_path: Path, region_bbox, screened: list, osm_data: dict) -> bool:
    """Save the region-wide screening/selection map as a PNG.

    Reuses the PDF's ``_draw_location_map`` so the HTML matches: region bbox +
    faint OSM context, every screened candidate (seeds as stars, auto-proposals
    as dots) coloured by coverage (green/orange/red), with a heading arrow.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from .region_pdf import _draw_location_map  # noqa: PLC0415
    except Exception as exc:
        logger.warning("F-SKY15 screening map skipped (matplotlib unavailable): %s", exc)
        return False
    fig, ax = plt.subplots(figsize=(10.0, 9.0))
    try:
        _draw_location_map(ax, region_bbox, screened, osm_data)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=120, bbox_inches="tight")
        return True
    except Exception as exc:
        logger.warning("F-SKY15 screening map render failed: %s", exc)
        return False
    finally:
        plt.close(fig)


def _render_view_mask_png(out_path: Path, sv: "SeedViewRegistration") -> bool:
    """Save the SegFormer class masks over a grayscale base frame.

    All four ADE20K classes the pipeline cares about are layered on top of
    the desaturated RGB so reviewers can see what SegFormer actually
    labelled — not just buildings. Colour key:
        sky        → cyan      (#3aa6ff)
        building   → red       (#e64235)
        water      → deep blue (#1b4ccc)
        vegetation → green     (#36a04a)

    The grayscale base preserves visual context (silhouettes, horizon
    line) without competing with the overlay colours. Falls back to the
    raw building mask if no frame is available. Returns False if there's
    no building mask at all (the older view rows still hold None for the
    new sky/water/vegetation fields, so this gate keeps the report
    rendering on a mixed cache).
    """
    bmask = getattr(sv, "building_mask", None)
    if bmask is None:
        return False
    try:
        import numpy as np
        from PIL import Image

        # Prefer the raw frame; fall back to the overlay image (which has
        # bounding boxes drawn on it but is still a valid base) if raw was
        # not populated (e.g. cache from before the field was added).
        img = getattr(sv, "raw_image", None)
        if img is None:
            img = getattr(sv, "image", None)

        b_arr = np.asarray(bmask).astype(bool)

        if img is not None and img.shape[:2] == b_arr.shape[:2]:
            rgb = np.asarray(img).astype(np.float32)
            # ITU-R BT.601 luma → grayscale, then tile to 3 channels.
            gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1]
                    + 0.114 * rgb[..., 2])
            # Slight desaturation: gray base at 0.85 brightness so the
            # coloured overlays stay legible.
            base = np.stack([gray, gray, gray], axis=-1) * 0.85

            def _blend(base_arr, mask_arr, color_rgb, alpha):
                if mask_arr is None:
                    return base_arr
                m = np.asarray(mask_arr).astype(bool)
                if m.shape != base_arr.shape[:2]:
                    return base_arr
                col = np.array(color_rgb, dtype=np.float32)
                base_arr[m] = (
                    base_arr[m] * (1.0 - alpha) + col * alpha
                )
                return base_arr

            # Order matters when classes overlap: paint water+vegetation
            # first (background-y), then sky, then building on top — the
            # silhouettes you most care about win the colour fight.
            base = _blend(base, getattr(sv, "water_mask", None),
                          (27, 76, 204), 0.55)
            base = _blend(base, getattr(sv, "vegetation_mask", None),
                          (54, 160, 74), 0.50)
            base = _blend(base, getattr(sv, "sky_mask", None),
                          (58, 166, 255), 0.45)
            base = _blend(base, b_arr, (230, 66, 53), 0.55)
            out = np.clip(base, 0, 255).astype(np.uint8)
        else:
            # No base frame — just the building mask as a binary image.
            out = (b_arr.astype(np.uint8) * 255)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(out).save(out_path, optimize=True)
        return True
    except Exception as exc:
        logger.warning("F-SKY15 mask render failed for seed=%s: %s", sv.seed_name, exc)
        return False


def _render_view_depth_png(out_path: Path, sv: "SeedViewRegistration") -> bool:
    """Save the Depth Anything V2 inverse-depth map of the raw frame as a
    colormapped PNG (F-SKY12 diagnostic tab).

    Computed on-demand here — the rest of the pipeline doesn't store the
    depth map, and recomputing in the renderer keeps the per-view
    SeedViewRegistration dataclass lean. Slow (1-2 s per view on CPU);
    gated by env var ``SKYLINE_CV_HTML_DEPTH``.

    The colormap is ``turbo`` (perceptually monotonic, bright for near
    things) so a reviewer can read off relative distance at a glance:
    yellow/red = close, dark blue = far. Returns False if depth
    inference fails or the raw frame is missing.
    """
    img = getattr(sv, "raw_image", None)
    if img is None:
        return False
    try:
        from .depth_estimation import predict_pano_depth  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        try:
            import matplotlib.pyplot as plt  # noqa: PLC0415
            cmap = plt.get_cmap("turbo")
        except Exception:
            cmap = None
        depth_rel = predict_pano_depth(np.asarray(img))
        # Normalise to [0, 1] for the colormap.
        d_min = float(depth_rel.min())
        d_max = float(depth_rel.max())
        if d_max - d_min < 1e-6:
            return False
        norm = (depth_rel - d_min) / (d_max - d_min)
        if cmap is not None:
            rgba = (cmap(norm) * 255).astype(np.uint8)
            rgb = rgba[..., :3]
        else:
            rgb = np.stack(
                [(norm * 255).astype(np.uint8)] * 3, axis=-1)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgb).save(out_path, optimize=True)
        return True
    except Exception as exc:
        logger.warning(
            "F-SKY12 depth render failed for seed=%s: %s", sv.seed_name, exc)
        return False


def _render_view_reconstruction_png(
    out_path: Path,
    sv: "SeedViewRegistration",
    buildings_by_id: dict,
) -> bool:
    """Plot a per-view depth-to-footprint reconstruction next to the OSM
    projection so a reviewer can verify the registration is consistent
    with the visible scene depth (F-SKY12 + F-SKY15 reconstruction
    subtab).

    For each matched segment in the view:
      * Sample the predicted depth map at ``(base_y, peak_x)`` — the
        building's base-of-facade pixel — to estimate the depth-derived
        distance from camera.
      * Compute the bearing from ``peak_x`` via the per-view pinhole.
      * Place a colored dot in a top-down "FOV-up" frame: bearing on x,
        depth-distance on y. The OSM-projected centroid for the same
        ``matched_projection.feature_id`` is drawn at its OSM-computed
        ``(bearing, forward_m)`` for direct comparison.

    Returns False on failure (no matched segments, no depth, no
    matplotlib, etc.) so the page omits the panel cleanly.
    """
    matched = getattr(sv, "matched_segments", None)
    raw = getattr(sv, "raw_image", None)
    if not matched or raw is None:
        return False
    try:
        import math  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
        from .depth_estimation import predict_pano_depth  # noqa: PLC0415
        from .region_pdf import _bearing_deg, _distance_m  # noqa: PLC0415
    except Exception as exc:
        logger.warning(
            "F-SKY12 reconstruction skipped for seed=%s: %s",
            sv.seed_name, exc)
        return False
    try:
        depth = predict_pano_depth(np.asarray(raw))
        h, w = depth.shape[:2]
        half_fov = float(sv.fov) * 0.5
        heading_eff = (sv.heading + sv.best_offset) % 360.0
        f_px = 0.5 * w / math.tan(math.radians(sv.fov) * 0.5)

        # Collect (bearing_deg, depth_distance_m_proxy, fid, color) for
        # each matched segment + the OSM-projected counterpart distance.
        recon_dots: list[tuple[float, float, str, tuple[float, float, float]]] = []
        osm_dots: list[tuple[float, float, str, tuple[float, float, float]]] = []
        for seg in matched:
            m = seg.get("matched_projection")
            if not m:
                continue
            fid = str(m.get("feature_id", ""))
            badge_n = int(seg.get("seed_index", 0))
            # Reuse the report's palette so colours match the bboxes.
            from .region_pdf import _SEGMENT_PALETTE  # noqa: PLC0415
            r, g, b = _SEGMENT_PALETTE[
                (badge_n - 1) % len(_SEGMENT_PALETTE)]
            color = (r / 255.0, g / 255.0, b / 255.0)
            peak_x = int(round(float(
                seg.get("peak_x", (seg["x_left"] + seg["x_right"]) / 2))))
            base_y = int(round(float(seg.get("base_y", h - 1))))
            peak_x = max(0, min(w - 1, peak_x))
            base_y = max(0, min(h - 1, base_y))
            # Depth-derived distance proxy: inverse of relative depth, scaled
            # so a reasonable building distance lands in [50, 1500] m.
            # Depth Anything V2 returns higher = closer; invert.
            d_val = float(depth[base_y, peak_x])
            d_inv = 1.0 - max(0.0, min(1.0, d_val))
            depth_dist_m = 50.0 + d_inv * 1450.0
            # Bearing from segment x via pinhole.
            norm_x = (peak_x - w / 2.0) / (w / 2.0)
            angle_off = math.degrees(math.atan(norm_x * math.tan(
                math.radians(half_fov))))
            seg_bearing = (heading_eff + angle_off) % 360.0
            recon_dots.append((seg_bearing, depth_dist_m, fid, color))
            # OSM-projected counterpart.
            b_rec = buildings_by_id.get(fid)
            if b_rec is not None:
                osm_bearing = _bearing_deg(
                    sv.seed_lat, sv.seed_lon,
                    b_rec.centroid_lat, b_rec.centroid_lon)
                osm_dist = _distance_m(
                    sv.seed_lat, sv.seed_lon,
                    b_rec.centroid_lat, b_rec.centroid_lon)
                osm_dots.append((osm_bearing, osm_dist, fid, color))

        if not recon_dots and not osm_dots:
            return False

        fig, ax = plt.subplots(figsize=(7.0, 5.0))
        # FOV-up frame: x is bearing offset from camera heading, y is
        # distance from camera. Camera at (0, 0).
        def _to_xy(bearing, dist):
            delta = ((bearing - heading_eff + 540.0) % 360.0) - 180.0
            return float(delta), float(dist)
        # Connect each matched pair with a thin line so the reviewer
        # can see depth-vs-OSM disagreement at a glance.
        osm_by_fid = {d[2]: d for d in osm_dots}
        for (bearing, dist, fid, color) in recon_dots:
            x, y = _to_xy(bearing, dist)
            ax.scatter([x], [y], c=[color], s=80, marker="o",
                       edgecolors="black", linewidth=0.6, zorder=3)
            if fid in osm_by_fid:
                ob, od, _, _ = osm_by_fid[fid]
                ox, oy = _to_xy(ob, od)
                ax.plot([x, ox], [y, oy], color=color, linewidth=0.8,
                        alpha=0.6, zorder=1)
        for (bearing, dist, fid, color) in osm_dots:
            x, y = _to_xy(bearing, dist)
            ax.scatter([x], [y], c=[color], s=120, marker="^",
                       edgecolors="black", linewidth=0.6, zorder=2,
                       alpha=0.8)
        # Camera at origin.
        ax.scatter([0], [0], c="red", s=160, marker="*",
                   edgecolors="black", linewidth=0.6, zorder=4)
        # FOV cone.
        ax.axvline(-half_fov, color="grey", linestyle="--",
                   linewidth=0.5, alpha=0.5)
        ax.axvline(+half_fov, color="grey", linestyle="--",
                   linewidth=0.5, alpha=0.5)
        ax.set_xlabel("bearing offset from camera heading (deg, image-x)")
        ax.set_ylabel("estimated distance from camera (m)")
        ax.set_title(
            f"Depth → footprint reconstruction "
            f"(circle: depth-derived  ▲: OSM-projected)",
            fontsize=10,
        )
        ax.set_xlim(-half_fov * 1.2, +half_fov * 1.2)
        ax.set_ylim(0, max(
            [d for _, d, _, _ in (recon_dots + osm_dots)] + [200.0]) * 1.1)
        ax.grid(True, alpha=0.3)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        return True
    except Exception as exc:
        logger.warning(
            "F-SKY12 reconstruction render failed for seed=%s: %s",
            sv.seed_name, exc)
        return False


def _render_seed_minimap_png(
    out_path: Path,
    sv: "SeedViewRegistration",
    osm_data: dict,
    buildings_by_id: dict,
    *,
    satellite_bg: bool = False,
    rotate_to_fov_up: bool = True,
) -> bool:
    """Save the seed's minimap as a standalone PNG.

    Wraps the existing ``_draw_view_minimap`` so the HTML report and the
    PDF stay visually consistent. ``satellite_bg=True`` forces an ESRI
    satellite tile underlay so the user can compare matched footprints
    against real imagery — the "Satellite" sub-tab in the per-view block.

    When ``rotate_to_fov_up`` is True (default) the rendered PNG is rotated
    so the camera FOV cone points straight up. This makes a side-by-side
    inspection of the street view and the minimap intuitive: image-left
    corresponds to map-left and image-right to map-right regardless of the
    seed's heading. The rotation discards the geographic-north convention
    on the rendered PNG (the axis ticks and latitude labels become
    visually rotated too) but the underlying spatial relationships are
    preserved — geometry stays correct, only the display frame changes.

    Returns True on success, False (with a warning) if matplotlib fails —
    the HTML page will simply omit the image rather than crash the whole
    report.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Late import to avoid the heavy region_pdf module at module load
        from .region_pdf import _draw_view_minimap  # noqa: PLC0415
    except Exception as exc:
        logger.warning("F-SKY15 minimap render skipped (matplotlib unavailable): %s", exc)
        return False

    fig, ax = plt.subplots(figsize=(8.0, 7.0))
    try:
        _draw_view_minimap(
            ax,
            sv.seed_lat,
            sv.seed_lon,
            sv.heading + sv.best_offset,
            sv.fov,
            osm_data,
            sv.matched_segments,
            buildings_by_id=buildings_by_id,
            image_width=sv.image.shape[1] if getattr(sv, "image", None) is not None else 960,
            pano_osm_iou=sv.pano_osm_iou,
            pano_osm_n_keypoints=sv.pano_osm_n_keypoints,
            pano_projected_coastline=sv.pano_projected_coastline,
            pano_projected_vegetation=getattr(sv, "pano_projected_vegetation", None),
            satellite_bg=satellite_bg,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=120, bbox_inches="tight")
        if rotate_to_fov_up:
            # Rotate the saved PNG so the camera FOV cone points up.
            # ``_draw_view_minimap`` saves with north up; in saved-PNG
            # pixel coords the FOV vector points in direction (sin h,
            # -cos h) where +y is down. Rotating by ``-heading_eff``
            # (CW in image coords; PIL rotate is CCW-positive so we
            # pass negative degrees) maps that vector to (0, -1), i.e.
            # straight up in the rendered image.
            try:
                from PIL import Image  # noqa: PLC0415
                heading_eff = (sv.heading + sv.best_offset) % 360.0
                img = Image.open(out_path).convert("RGB")
                rotated = img.rotate(
                    -heading_eff, expand=True, fillcolor=(255, 255, 255),
                    resample=Image.BICUBIC,
                )
                rotated.save(out_path, optimize=True)
            except Exception as exc:
                logger.warning(
                    "F-SKY15 minimap rotate skipped for seed %s: %s",
                    sv.seed_name, exc,
                )
        return True
    except Exception as exc:
        logger.warning("F-SKY15 minimap render failed for seed %s: %s", sv.seed_name, exc)
        return False
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# Per-seed HTML page
# ---------------------------------------------------------------------------

def _fmt_optional_float(value: float | None, suffix: str = "", precision: int = 2) -> str:
    """Format an Optional[float] for display in a table cell.

    Returns ``"—"`` for None so missing diagnostics are visually obvious
    (rather than showing "0.00" which could be misread as a real value).
    """
    if value is None:
        return "—"
    try:
        return f"{value:.{precision}f}{suffix}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _fmt_optional_bool(value: bool | None) -> str:
    if value is None:
        return "—"
    return "yes" if value else "no"


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
        from .region_pdf import _SEGMENT_PALETTE  # noqa: PLC0415
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
        from .region_pdf import (  # noqa: PLC0415
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
            from .satellite_image import fetch_region_satellite  # noqa: PLC0415
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
                bbox, target_m_per_px=4.0)
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
            ax.scatter([math.radians(bearing)], [dist],
                       c=[color], s=120, marker="o",
                       edgecolors="black", linewidth=0.6, zorder=3)
            ax.text(math.radians(bearing), dist * 1.04,
                    str(badge_n), fontsize=8, fontweight="bold",
                    ha="center", va="center",
                    bbox=dict(boxstyle="circle,pad=0.15", facecolor=color,
                              edgecolor="black", linewidth=0.5, alpha=0.95),
                    zorder=4)

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


def _osm_height_m(feat: dict) -> float | None:
    """Extract a metres-units height from an OSM building feature.

    Priority: explicit ``height`` / ``building:height`` (stripped of
    units), then ``building:levels`` × 3.0 m as a coarse fallback.
    Returns ``None`` when no usable tag is present.
    """
    props = feat.get("properties") or {}
    for key in ("height", "building:height"):
        v = props.get(key)
        if v is None:
            continue
        try:
            return float(str(v).split()[0].replace("m", "").strip())
        except (ValueError, TypeError):
            continue
    lv = props.get("building:levels")
    if lv is not None:
        try:
            return float(str(lv).split()[0]) * 3.0
        except (ValueError, TypeError):
            pass
    return None


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
        from .region_pdf import (  # noqa: PLC0415
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
        from .pipeline import (  # noqa: PLC0415
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


def _osm_polygon_area_m2(ring: list, lat0: float) -> float:
    """Rough planar area in m² for a small lon/lat ring using a local
    equirectangular projection centred at ``lat0``. Accurate to <1% for
    polygons up to a few km wide — good enough to filter sheds / kiosks
    from real buildings.
    """
    import math  # noqa: PLC0415
    mlat = 110540.0
    mlon = 111320.0 * math.cos(math.radians(lat0))
    if len(ring) < 3:
        return 0.0
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1 = ring[i][0] * mlon
        y1 = ring[i][1] * mlat
        x2 = ring[(i + 1) % n][0] * mlon
        y2 = ring[(i + 1) % n][1] * mlat
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def _build_osm_nearest_per_degree(
    osm_data: dict | None,
    seed_lat: float,
    seed_lon: float,
    *,
    min_area_m2: float = 150.0,
    require_height_tag: bool = False,
    median_kernel: int = 5,
) -> "np.ndarray":
    """Compute the per-degree nearest-OSM-building distance.

    Filters:
      - ``min_area_m2`` drops small structures (sheds, kiosks) that
        otherwise dominate the nearest signal at close range and read
        as noise.
      - ``require_height_tag`` restricts to polygons with an OSM
        ``height`` / ``building:levels`` tag (usually landmark
        buildings).

    Then applies a 1D median filter (wrap-aware) to the 360-bin signal
    to suppress single-degree spikes from polygon vertex sampling.
    Returns a length-360 array with NaN for bearings with no nearby
    qualifying building.
    """
    import math  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    from .region_pdf import _bearing_deg, _distance_m  # noqa: PLC0415
    out = np.full(360, np.nan, dtype=np.float32)
    if osm_data is None:
        return out
    bucket = [float("inf")] * 360
    for feat in (osm_data.get("buildings", {}).get("features") or [])[:8000]:
        geom = feat.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        if require_height_tag:
            if _osm_height_m(feat) is None:
                continue
        if geom.get("type") == "Polygon":
            rings = [geom.get("coordinates", [[]])[0]]
        else:
            rings = [poly[0] for poly in geom.get("coordinates", [])]
        for ring in rings:
            if not ring or len(ring) < 3:
                continue
            if _osm_polygon_area_m2(ring, seed_lat) < min_area_m2:
                continue
            for (lon, lat) in ring:
                bb = _bearing_deg(seed_lat, seed_lon, lat, lon)
                dd = _distance_m(seed_lat, seed_lon, lat, lon)
                if dd <= POLAR_MAX_M:
                    bi = int(round(bb)) % 360
                    if dd < bucket[bi]:
                        bucket[bi] = dd
    for d in range(360):
        if bucket[d] < float("inf"):
            out[d] = float(bucket[d])
    # Wrap-aware median filter: pad with the same array's tail/head so
    # the filter handles the 0°/360° boundary correctly.
    if median_kernel >= 3 and median_kernel % 2 == 1:
        try:
            from scipy.signal import medfilt  # noqa: PLC0415
            pad = median_kernel // 2
            padded = np.concatenate(
                [out[-pad:], out, out[:pad]])
            # medfilt ignores NaN incorrectly; mask + nanmedian per
            # window so a single-degree spike collapses to its
            # neighbourhood median while NaN gaps stay NaN.
            filt = np.full(out.shape, np.nan, dtype=np.float32)
            for i in range(360):
                win = padded[i: i + median_kernel]
                if np.any(~np.isnan(win)):
                    filt[i] = float(np.nanmedian(win))
            out = filt
        except Exception:
            pass
    return out


# Sentinel distance for bearings with no building (sky/water) or no
# nearby OSM. Shared with region_pdf's bearing recovery so the scan
# plot and the applied correction agree. A large value turns "pano
# sees a building but OSM says open space" into a strong mismatch.
NO_BUILDING_M = 3000.0


def _bearing_xcorr_offset(
    silh: "np.ndarray", osm_nearest: "np.ndarray",
    *, improve_min: float = 0.45,
) -> "tuple[int, float, np.ndarray, bool]":
    """Cross-correlate two dense 360-bin signals over all rotations and
    return ``(best_offset_deg, best_mae, mae_curve, applied)``.

    Both inputs are expected to already be NO_BUILDING-filled (no NaN).
    ``applied`` mirrors the pipeline's gate: the offset is trusted only
    when rotating to it improves on the CURRENT anchor (offset 0) by
    ≥``improve_min`` MAE. Empirically this cleanly separates already-
    aligned seeds (improve <30%) from misaligned ones (improve >50%).

    ``mae_curve`` is the mean-absolute-error at each candidate offset
    (lower = better) for diagnostic plotting.
    """
    import numpy as np  # noqa: PLC0415
    scores = np.full(360, 1e9, dtype=np.float32)
    if silh.shape != (360,) or osm_nearest.shape != (360,):
        return 0, 1e9, scores, False
    # Fill any residual NaN with the sentinel so the vectors are dense.
    s = np.where(np.isnan(silh), NO_BUILDING_M, silh)
    o = np.where(np.isnan(osm_nearest), NO_BUILDING_M, osm_nearest)
    for off in range(360):
        rs = np.roll(s, off)
        scores[off] = float(np.mean(np.abs(rs - o)))
    pre_mae = float(scores[0])
    best = int(np.argmin(scores))
    best_mae = float(scores[best])
    shift = best if best <= 180 else best - 360
    improve = (pre_mae - best_mae) / max(pre_mae, 1e-6)
    applied = (abs(shift) >= 15 and improve >= improve_min)
    return shift, best_mae, scores, applied


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
        from .depth_estimation import predict_pano_depth  # noqa: PLC0415
        from .region_pdf import _bearing_deg, _distance_m  # noqa: PLC0415
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

        # Re-derive the depth-to-distance scale from matched segments
        # (so the silhouette uses the same metric the polar uses).
        scale = 1450.0
        ratios = []
        for seg in (pano_result.matched_segments or []):
            m = seg.get("matched_projection")
            if not m:
                continue
            fid = str(m.get("feature_id", ""))
            if not fid:
                continue
            # We'd need buildings_by_id here, but matched segments
            # already carry OSM distance hints via the matcher's anchor.
            # Settle for the legacy 1450 scale when an anchor isn't
            # locally derivable; the visual still tells the story.
            pass

        # Per-degree depth silhouette — median over the lower half of
        # the building-mask pixels per column (column_building_distance
        # "lower_median"); same metric the pipeline's bearing recovery
        # uses, so the scan and the applied correction agree.
        from .depth_estimation import (  # noqa: PLC0415
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
        shift, best_mae, _scores, applied = _bearing_xcorr_offset(
            silh_xc, osm_xc)

        # ---- Column-indexed plot so the scan aligns 1:1 under the pano
        # image (same x = same pano column). For each pano column we
        # show the RAW depth distance (blue, unclamped — what depth
        # actually says) and the OSM-nearest distance at that column's
        # bearing (orange). Vertical guides mark N/E/S/W; ticks along the
        # bottom mark columns that contain building mask.
        col_dist = np.full(Wd, np.nan, dtype=np.float32)
        col_osm = np.full(Wd, np.nan, dtype=np.float32)
        col_has_bldg = bmask_arr.any(axis=0)
        for c in range(Wd):
            dc = column_building_distance(
                depth_arr, bmask_arr, c, scale=scale, mode="base")
            if dc is not None:
                col_dist[c] = float(min(POLAR_MAX_M, dc))
            bdeg = int(round(float(hpc_arr[c]))) % 360
            v = osm_nearest_arr[bdeg]
            if not np.isnan(v):
                col_osm[c] = v

        fig, ax = plt.subplots(figsize=(14.0, 3.6))
        xc = np.arange(Wd)
        ax.plot(xc, col_dist, color="#1f77b4", linewidth=1.2,
                alpha=0.9, label="Depth distance (raw)")
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

        ax.set_xlabel("pano column (aligned with the pano image above; "
                      "dashed = N/E/S/W)")
        ax.set_ylabel("nearest-building distance (m)")
        verdict = "APPLIED" if applied else "SKIPPED (low confidence)"
        ax.set_title(
            "Distance vs pano column — depth (blue) vs OSM (orange). "
            f"Bearing correction {shift:+d}° [{verdict}], MAE "
            f"{best_mae:.0f} m.",
            fontsize=10,
        )
        ax.grid(True, alpha=0.2)
        ax.legend(loc="upper right", fontsize=8)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=100, bbox_inches="tight")
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
        from .depth_estimation import predict_pano_depth  # noqa: PLC0415
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
        from .depth_estimation import predict_pano_depth  # noqa: PLC0415
        from .region_pdf import _bearing_deg, _distance_m  # noqa: PLC0415
        from .region_pdf import _SEGMENT_PALETTE  # noqa: PLC0415
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

        # Anchor the depth-to-distance scale against OSM ground truth
        # using a sqrt mapping. Linear (dist = d_inv * scale) under-
        # estimated far towers because inverse-depth saturates at large
        # distances. sqrt amplifies mid-range d_inv values, pushing
        # moderate-far towers outward and giving better agreement with
        # OSM for the saturating tail. Scale fitted as median(od /
        # sqrt(d_inv)) across matched fids; falls back to a hardcoded
        # mapping when no OSM anchor exists.
        osm_dist_by_fid: dict[str, float] = {fid: od for (_, od, fid, _, _) in osm_xy}
        ratios: list[float] = []
        for (_bearing, d_inv, fid, _color, _badge) in recon:
            if fid in osm_dist_by_fid and d_inv > 0.05:
                ratios.append(osm_dist_by_fid[fid] / math.sqrt(d_inv))
        if ratios:
            ratios.sort()
            scale = ratios[len(ratios) // 2]
        else:
            scale = 1450.0  # legacy fallback (no OSM anchor available)
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
                from .depth_estimation import (  # noqa: PLC0415
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

        osm_by_fid = {d[2]: d for d in osm_xy}
        for (bearing, dist, fid, color, badge) in recon_scaled:
            theta = math.radians(bearing)
            ax.scatter([theta], [dist], c=[color], s=80, marker="o",
                       edgecolors="black", linewidth=0.6, zorder=3)
            if badge > 0:
                ax.text(theta, dist, str(badge), fontsize=7,
                        ha="center", va="center", zorder=4,
                        color="black", fontweight="bold")
            if fid in osm_by_fid:
                ob, od, _, _, _ = osm_by_fid[fid]
                ax.plot(
                    [theta, math.radians(ob)], [dist, od],
                    color=color, linewidth=0.8, alpha=0.6, zorder=1,
                )
        for (bearing, dist, fid, color, badge) in osm_xy:
            theta = math.radians(bearing)
            ax.scatter([theta], [dist], c=[color],
                       s=120, marker="^", edgecolors="black",
                       linewidth=0.6, zorder=2, alpha=0.8)
            if badge > 0:
                ax.text(theta, dist, str(badge), fontsize=7,
                        ha="center", va="center", zorder=4,
                        color="black", fontweight="bold")
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


def render_seed_pano_page(
    primary_sv: "SeedViewRegistration",
    sv_list: list,
    pano_result,
    region_name: str,
    minimap_rel_path: str | None,
    *,
    pano_rel_paths: dict | None = None,
) -> str:
    """Render the per-seed HTML page with ONE big 6-layer pano tab block.

    ``pano_rel_paths`` is a dict containing ``pano_rgb``, ``pano_segformer``,
    ``pano_depth``, ``minimap``, ``minimap_sat``, ``pano_reconstruction``
    keys → relative URLs.
    """
    title = f"Seed {primary_sv.seed_name} — {region_name}"

    summary_rows = [
        ("Lat / Lon", f"{primary_sv.seed_lat:.5f}, {primary_sv.seed_lon:.5f}"),
        ("FOV", f"{primary_sv.fov:.1f}°"),
        ("Views captured", str(len(sv_list))),
        ("Negative seed", _fmt_optional_bool(primary_sv.is_negative)),
    ]
    if pano_result is not None:
        summary_rows.append((
            "Pano matched segments",
            f"{pano_result.n_matched} of {pano_result.n_segments}",
        ))
        summary_rows.append((
            "Anchor offset (joint IoU)",
            f"{pano_result.anchor_offset_deg:.1f}°",
        ))
    summary_html = "\n".join(
        f"    <tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>"
        for k, v in summary_rows
    )

    paths = pano_rel_paths or {}

    def _panel(url: str | None, alt: str, missing: str) -> str:
        if not url:
            return f'<p class="missing"><em>{missing}</em></p>'
        return (
            f'<img src="{html.escape(url)}" alt="{html.escape(alt)}" '
            'style="width:100%;height:auto;border:1px solid #ccc;">'
        )

    pano_rgb_panel = _panel(
        paths.get("pano_rgb"),
        f"Stitched pano for {primary_sv.seed_name}",
        "Pano RGB unavailable",
    )
    pano_seg_panel = _panel(
        paths.get("pano_segformer"),
        f"SegFormer 4-class pano mask for {primary_sv.seed_name}",
        "Pano SegFormer overlay unavailable",
    )
    pano_depth_panel = _panel(
        paths.get("pano_depth"),
        f"Depth Anything V2 pano for {primary_sv.seed_name}",
        "Pano depth unavailable",
    )
    pano_scan_panel = _panel(
        paths.get("pano_scan"),
        f"Distance-vs-bearing scan for {primary_sv.seed_name}",
        "Distance scan unavailable",
    )
    footprints_panel = _panel(
        paths.get("minimap"),
        f"Footprint minimap for {primary_sv.seed_name}",
        "Footprint minimap unavailable",
    )
    satellite_panel = _panel(
        paths.get("minimap_sat"),
        f"Satellite minimap for {primary_sv.seed_name}",
        "Satellite minimap unavailable",
    )
    recon_panel = _panel(
        paths.get("pano_reconstruction"),
        f"Pano depth-to-footprint reconstruction for {primary_sv.seed_name}",
        "Reconstruction unavailable",
    )
    heights_panel = _panel(
        paths.get("minimap_heights"),
        f"OSM-tagged building heights for {primary_sv.seed_name}",
        "Heights overlay unavailable",
    )

    tab_id = html.escape(primary_sv.seed_name) + "_pano"
    # Two parallel tab groups: pano-space (wide RGB / SegFormer / Depth)
    # on top, top-down space (square Footprints / Satellite /
    # Reconstruction) below. They're independently tabbable so the
    # reader can hold a pano view and a top-down view side-by-side and
    # cross-reference bearings.
    pano_space_tabs = (
        f'    <figure class="tabs panospace">\n'
        f'      <input type="radio" name="pano_{tab_id}" id="pano_{tab_id}_rgb" checked>\n'
        f'      <input type="radio" name="pano_{tab_id}" id="pano_{tab_id}_seg">\n'
        f'      <input type="radio" name="pano_{tab_id}" id="pano_{tab_id}_depth">\n'
        f'      <input type="radio" name="pano_{tab_id}" id="pano_{tab_id}_scan">\n'
        f'      <div class="tab-labels">\n'
        f'        <label for="pano_{tab_id}_rgb">Street view</label>\n'
        f'        <label for="pano_{tab_id}_seg">SegFormer mask</label>\n'
        f'        <label for="pano_{tab_id}_depth">Depth</label>\n'
        f'        <label for="pano_{tab_id}_scan">Distance scan</label>\n'
        f'      </div>\n'
        f'      <div class="tab-panel pano-rgb">{pano_rgb_panel}</div>\n'
        f'      <div class="tab-panel pano-seg">{pano_seg_panel}</div>\n'
        f'      <div class="tab-panel pano-depth">{pano_depth_panel}</div>\n'
        f'      <div class="tab-panel pano-scan">{pano_scan_panel}</div>\n'
        f'    </figure>'
    )
    topdown_tabs = (
        f'    <figure class="tabs topdown">\n'
        f'      <input type="radio" name="td_{tab_id}" id="td_{tab_id}_fp" checked>\n'
        f'      <input type="radio" name="td_{tab_id}" id="td_{tab_id}_sat">\n'
        f'      <input type="radio" name="td_{tab_id}" id="td_{tab_id}_recon">\n'
        f'      <input type="radio" name="td_{tab_id}" id="td_{tab_id}_heights">\n'
        f'      <div class="tab-labels">\n'
        f'        <label for="td_{tab_id}_fp">Footprints</label>\n'
        f'        <label for="td_{tab_id}_sat">Satellite</label>\n'
        f'        <label for="td_{tab_id}_recon">Reconstruction</label>\n'
        f'        <label for="td_{tab_id}_heights">Heights</label>\n'
        f'      </div>\n'
        f'      <div class="tab-panel pano-fp">{footprints_panel}</div>\n'
        f'      <div class="tab-panel pano-sat">{satellite_panel}</div>\n'
        f'      <div class="tab-panel pano-recon">{recon_panel}</div>\n'
        f'      <div class="tab-panel pano-heights">{heights_panel}</div>\n'
        f'    </figure>'
    )
    pano_tabs = pano_space_tabs + "\n" + topdown_tabs

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 1500px; margin: 1.5em auto; padding: 0 1em; color: #222; }}
  h1 {{ border-bottom: 2px solid #0a3070; padding-bottom: 0.2em; }}
  h2 {{ color: #0a3070; margin-top: 1.5em; }}
  table {{ border-collapse: collapse; margin: 0.5em 0; }}
  th, td {{ padding: 0.25em 0.7em; border: 1px solid #ddd; text-align: left; }}
  th {{ background: #f4f6fa; font-weight: 600; }}
  .summary th {{ text-align: right; width: 14em; }}
  .small {{ font-size: 0.85em; color: #666; }}
  .missing {{ color: #888; }}
  figure.tabs {{ margin: 0.5em 0; }}
  figure.tabs > input[type=radio] {{ display: none; }}
  figure.tabs .tab-labels {{ display: flex; gap: 0.3em; margin-bottom: 0.3em;
                              flex-wrap: wrap; }}
  figure.tabs .tab-labels label {{
      padding: 0.3em 0.9em; cursor: pointer;
      border: 1px solid #ccc; border-bottom: none;
      background: #f4f6fa; font-size: 0.85em; font-weight: 600;
      border-radius: 4px 4px 0 0;
  }}
  figure.tabs .tab-panel {{ display: none; }}
  /* Pano-space tab group (4 tabs: rgb/seg/depth/scan). */
  figure.tabs.panospace > input:nth-of-type(1):checked ~ .tab-labels label:nth-of-type(1),
  figure.tabs.panospace > input:nth-of-type(2):checked ~ .tab-labels label:nth-of-type(2),
  figure.tabs.panospace > input:nth-of-type(3):checked ~ .tab-labels label:nth-of-type(3),
  figure.tabs.panospace > input:nth-of-type(4):checked ~ .tab-labels label:nth-of-type(4) {{
      background: #fff; border-color: #0a3070; color: #0a3070;
  }}
  figure.tabs.panospace > input:nth-of-type(1):checked ~ .pano-rgb {{ display: block; }}
  figure.tabs.panospace > input:nth-of-type(2):checked ~ .pano-seg {{ display: block; }}
  figure.tabs.panospace > input:nth-of-type(3):checked ~ .pano-depth {{ display: block; }}
  figure.tabs.panospace > input:nth-of-type(4):checked ~ .pano-scan {{ display: block; }}
  /* Top-down tab group (4 tabs: footprints/satellite/reconstruction/heights). */
  figure.tabs.topdown > input:nth-of-type(1):checked ~ .tab-labels label:nth-of-type(1),
  figure.tabs.topdown > input:nth-of-type(2):checked ~ .tab-labels label:nth-of-type(2),
  figure.tabs.topdown > input:nth-of-type(3):checked ~ .tab-labels label:nth-of-type(3),
  figure.tabs.topdown > input:nth-of-type(4):checked ~ .tab-labels label:nth-of-type(4) {{
      background: #fff; border-color: #0a3070; color: #0a3070;
  }}
  figure.tabs.topdown > input:nth-of-type(1):checked ~ .pano-fp {{ display: block; }}
  figure.tabs.topdown > input:nth-of-type(2):checked ~ .pano-sat {{ display: block; }}
  figure.tabs.topdown > input:nth-of-type(3):checked ~ .pano-recon {{ display: block; }}
  figure.tabs.topdown > input:nth-of-type(4):checked ~ .pano-heights {{ display: block; }}
  /* Center top-down square plots so they don't stretch to full width. */
  figure.tabs.topdown .tab-panel img {{ max-width: 700px; display: block;
                                         margin: 0 auto; }}
  nav.breadcrumb {{ font-size: 0.9em; margin-bottom: 1em; }}
  nav.breadcrumb a {{ color: #0a3070; text-decoration: none; }}
  img {{ cursor: zoom-in; }}
  .zoom-modal {{ display: none; position: fixed; inset: 0;
                background: rgba(0,0,0,0.88); z-index: 9999;
                overflow: hidden; cursor: grab; }}
  .zoom-modal.open {{ display: block; }}
  .zoom-modal img {{ position: absolute; transform-origin: 0 0;
                    user-select: none; -webkit-user-drag: none;
                    pointer-events: none; max-width: none; }}
  .zoom-close {{ position: fixed; top: 0.6em; right: 0.9em;
                color: #fff; font-size: 1.6em; cursor: pointer;
                z-index: 10000; user-select: none;
                background: rgba(0,0,0,0.5); padding: 0.1em 0.5em;
                border-radius: 4px; }}
  .zoom-hint {{ position: fixed; bottom: 0.8em; left: 50%;
               transform: translateX(-50%); color: rgba(255,255,255,0.75);
               font-size: 0.85em; user-select: none;
               background: rgba(0,0,0,0.5); padding: 0.3em 0.8em;
               border-radius: 4px; }}
</style>
</head>
<body>
<nav class="breadcrumb"><a href="index.html">← {html.escape(region_name)} index</a></nav>
<h1>{html.escape(title)}</h1>

<section class="summary">
  <h2>Summary</h2>
  <table>
{summary_html}
  </table>
</section>

<section class="pano">
  <h2>360° pano — 6 layers</h2>
{pano_tabs}
  <p class="legend small">
    Street view = stitched pano. SegFormer mask = building / sky / water /
    vegetation overlay from the cached per-view masks. Depth = Depth
    Anything V2 inverse-depth (turbo colormap; bright = closer).
    Footprints / Satellite = seed-level top-down minimap (rotated so
    image-left and map-left agree). Reconstruction = polar plot
    comparing depth-derived (circles) and OSM-projected (▲) building
    positions for each matched segment.
  </p>
</section>

<footer class="small" style="margin-top:2em;color:#888;">
  Generated by city2stl.skyline_cv.html_report (F-SKY15 pano-layered)
</footer>
<div class="zoom-modal" id="zoomModal">
  <span class="zoom-close" onclick="zoomClose()">×</span>
  <img id="zoomImg" alt="">
  <span class="zoom-hint">scroll = zoom • drag = pan • esc / × = close</span>
</div>
<script>
(function(){{
  var modal = document.getElementById('zoomModal');
  var img = document.getElementById('zoomImg');
  var s = {{scale: 1, tx: 0, ty: 0, drag: false, lx: 0, ly: 0}};
  function apply(){{
    img.style.transform = 'translate('+s.tx+'px,'+s.ty+'px) scale('+s.scale+')';
  }}
  window.zoomOpen = function(src){{
    img.src = src;
    img.onload = function(){{
      var vw = window.innerWidth, vh = window.innerHeight;
      var fit = Math.min(vw / img.naturalWidth, vh / img.naturalHeight) * 0.95;
      s.scale = fit;
      s.tx = (vw - img.naturalWidth * fit) / 2;
      s.ty = (vh - img.naturalHeight * fit) / 2;
      apply();
    }};
    modal.classList.add('open');
  }};
  window.zoomClose = function(){{ modal.classList.remove('open'); }};
  modal.addEventListener('wheel', function(e){{
    e.preventDefault();
    var f = e.deltaY < 0 ? 1.18 : 1/1.18;
    var ns = Math.max(0.05, Math.min(40, s.scale * f));
    var r = ns / s.scale;
    s.tx = e.clientX - (e.clientX - s.tx) * r;
    s.ty = e.clientY - (e.clientY - s.ty) * r;
    s.scale = ns;
    apply();
  }}, {{passive: false}});
  modal.addEventListener('mousedown', function(e){{
    if (e.target.classList.contains('zoom-close')) return;
    s.drag = true; s.lx = e.clientX; s.ly = e.clientY;
    modal.style.cursor = 'grabbing';
  }});
  modal.addEventListener('mousemove', function(e){{
    if (!s.drag) return;
    s.tx += e.clientX - s.lx; s.ty += e.clientY - s.ly;
    s.lx = e.clientX; s.ly = e.clientY;
    apply();
  }});
  function endDrag(){{ s.drag = false; modal.style.cursor = 'grab'; }}
  modal.addEventListener('mouseup', endDrag);
  modal.addEventListener('mouseleave', endDrag);
  document.addEventListener('keydown', function(e){{
    if (e.key === 'Escape' && modal.classList.contains('open')) zoomClose();
  }});
  document.addEventListener('DOMContentLoaded', function(){{
    document.querySelectorAll('img').forEach(function(im){{
      if (im.id === 'zoomImg') return;
      im.addEventListener('click', function(){{ zoomOpen(im.src); }});
    }});
  }});
}})();
</script>
</body>
</html>
"""


def render_seed_page(
    sv: "SeedViewRegistration",
    region_name: str,
    minimap_rel_path: str | None,
    *,
    estimates: list | None = None,
    views: list | None = None,
    view_image_rel_paths: list[str | None] | None = None,
    view_minimap_rel_paths: list[str | None] | None = None,
    view_mask_rel_paths: list[str | None] | None = None,
    view_satellite_rel_paths: list[str | None] | None = None,
    view_depth_rel_paths: list[str | None] | None = None,
    view_reconstruction_rel_paths: list[str | None] | None = None,
) -> str:
    """Render the per-seed HTML page as a string.

    ``sv`` is the primary view (used for the summary panel — typically
    the first view of the seed in capture order).
    ``minimap_rel_path`` is the URL path (relative to the seed page's
    location) to the minimap PNG; ``None`` if the PNG render failed and
    the page should show a placeholder.
    ``estimates`` is the list of ``RegisteredBuildingEstimate`` records
    for this seed. When provided, the page includes the per-segment
    table with both geometric and depth heights (F-SKY12 diagnostics).
    ``views`` is the full list of per-view ``SeedViewRegistration``
    rows for this seed (one per heading). When provided alongside
    ``view_image_rel_paths`` (same length, one rel-URL per view or None
    if the PNG save failed), the page emits a collapsible ``<details>``
    section per view with the registration-overlay image inline.
    ``view_minimap_rel_paths`` (same length) carries a per-view footprint
    minimap PNG; when present the image and footprint are laid out side by
    side so the user can compare what the camera sees against the OSM/MS
    footprints the matcher used (F-SKY8 conflict diagnosis).
    """
    # Title is escaped at each insertion point below; build it raw here
    # to avoid double-escaping.
    title = f"Seed {sv.seed_name} — {region_name}"

    summary_rows = [
        ("Lat / Lon", f"{sv.seed_lat:.5f}, {sv.seed_lon:.5f}"),
        ("Heading + offset", f"{(sv.heading + sv.best_offset) % 360.0:.1f}° (raw={sv.heading:.1f}°, offset={sv.best_offset:+.1f}°)"),
        ("FOV", f"{sv.fov:.1f}°"),
        ("Registration score", f"{sv.registration_score:.3f}"),
        ("Best IoU (semantic)", f"{sv.iou:.3f}"),
        ("Estimates produced", str(sv.estimates_count)),
        ("Matched segments", str(len(sv.matched_segments or []))),
        ("Negative seed", _fmt_optional_bool(sv.is_negative)),
        ("Aerial seed", _fmt_optional_bool(sv.is_aerial)),
    ]
    if sv.pano_osm_iou is not None:
        summary_rows.append((
            "pano↔OSM IoU (F-SKY13B)",
            f"{sv.pano_osm_iou:.3f} ({sv.pano_osm_n_keypoints or 0} keypoints)",
        ))
    if sv.pano_projected_coastline is not None:
        summary_rows.append((
            "Pano-projected coastline pts",
            str(len(sv.pano_projected_coastline)),
        ))
    # F-SKY11.1 pano-coastline recovery diagnostics. peak is the score at
    # the recovered heading (higher = sharper match); sigma is the spread
    # of the score curve (lower = the recovery is more confidently picking
    # a single offset). water_frac is the fraction of pano pixels classified
    # as water — low values explain low IoU scores ("no water visible from
    # this seed, nothing for OSM coastline to align against").
    if getattr(sv, "pano_recovered_offset_deg", None) is not None:
        summary_rows.append((
            "Pano recovery offset",
            f"{sv.pano_recovered_offset_deg:.1f}°",
        ))
    if getattr(sv, "pano_recovered_peak", None) is not None:
        summary_rows.append((
            "Pano recovery peak",
            f"{sv.pano_recovered_peak:.3f} (sharper = better match)",
        ))
    if getattr(sv, "pano_recovered_sigma", None) is not None:
        summary_rows.append((
            "Pano recovery sigma",
            f"{sv.pano_recovered_sigma:.3f} (lower = more confident)",
        ))
    if getattr(sv, "pano_water_frac", None) is not None:
        summary_rows.append((
            "Pano water fraction",
            f"{sv.pano_water_frac:.1%}",
        ))

    summary_html = "\n".join(
        f"    <tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>"
        for k, v in summary_rows
    )

    minimap_html = (
        f'<img src="{html.escape(minimap_rel_path)}" alt="minimap for {html.escape(sv.seed_name)}" '
        'style="max-width:900px;width:100%;height:auto;border:1px solid #ccc;">'
        if minimap_rel_path else
        '<p class="missing"><em>Minimap unavailable (render failed)</em></p>'
    )

    # F-SKY15 Phase B: per-view image gallery. One <details> per view,
    # collapsed by default to keep the page readable. The summary line
    # quotes heading / FOV / segment count so users can scan without
    # expanding. Inline images use width:100% so they scale to the
    # browser; max-width caps the size on wide screens.
    views_html = ""
    if views:
        rel_paths = view_image_rel_paths or [None] * len(views)
        mm_paths = view_minimap_rel_paths or [None] * len(views)
        mask_paths = view_mask_rel_paths or [None] * len(views)
        sat_paths = view_satellite_rel_paths or [None] * len(views)
        depth_paths = view_depth_rel_paths or [None] * len(views)
        recon_paths = view_reconstruction_rel_paths or [None] * len(views)
        view_blocks = []
        for i, (vsv, rel, mm_rel, mask_rel, sat_rel, depth_rel, recon_rel) in enumerate(
                zip(views, rel_paths, mm_paths, mask_paths, sat_paths,
                    depth_paths, recon_paths)):
            img_block = (
                f'<img src="{html.escape(rel)}" alt="view {i} for {html.escape(sv.seed_name)}" '
                'style="width:100%;height:auto;border:1px solid #ccc;">'
                if rel else
                '<p class="missing"><em>View image unavailable</em></p>'
            )
            mm_block = (
                f'<img src="{html.escape(mm_rel)}" alt="footprint minimap for view {i}" '
                'style="width:100%;height:auto;border:1px solid #ccc;">'
                if mm_rel else
                '<p class="missing"><em>Footprint minimap unavailable</em></p>'
            )
            sat_block = (
                f'<img src="{html.escape(sat_rel)}" alt="satellite view for view {i}" '
                'style="width:100%;height:auto;border:1px solid #ccc;">'
                if sat_rel else
                '<p class="missing"><em>Satellite view unavailable</em></p>'
            )
            mask_block = (
                f'<img src="{html.escape(mask_rel)}" alt="SegFormer mask for view {i}" '
                'style="width:100%;height:auto;border:1px solid #ccc;">'
                if mask_rel else
                '<p class="missing"><em>No segmentation mask stored</em></p>'
            )
            depth_block = (
                f'<img src="{html.escape(depth_rel)}" alt="Depth Anything V2 depth map for view {i}" '
                'style="width:100%;height:auto;border:1px solid #ccc;">'
                if depth_rel else
                '<p class="missing"><em>Depth map unavailable</em></p>'
            )
            recon_block = (
                f'<img src="{html.escape(recon_rel)}" alt="Depth-to-footprint reconstruction for view {i}" '
                'style="width:100%;height:auto;border:1px solid #ccc;">'
                if recon_rel else
                '<p class="missing"><em>Reconstruction plot unavailable</em></p>'
            )
            # Side-by-side: pano tabs (street view ↔ SegFormer mask) on the
            # left, footprint minimap on the right. Street view and mask share
            # the same underlying pano frame, so they belong in one tabbed
            # panel — the user toggles between "what the camera sees" and
            # "what the segmentation model labelled as building". Footprint
            # minimap stays separate because it's a different projection.
            # CSS-only tabs via radio inputs; ids include seed + view index
            # so multiple per-view blocks on one page don't collide.
            tab_id = f"{html.escape(sv.seed_name)}_v{i}"
            pano_tabs_block = (
                f'        <figure class="tabs">\n'
                f'          <input type="radio" name="tabs_{tab_id}" id="tab_{tab_id}_img" checked>\n'
                f'          <input type="radio" name="tabs_{tab_id}" id="tab_{tab_id}_mask">\n'
                f'          <input type="radio" name="tabs_{tab_id}" id="tab_{tab_id}_depth">\n'
                f'          <div class="tab-labels">\n'
                f'            <label for="tab_{tab_id}_img">Street view</label>\n'
                f'            <label for="tab_{tab_id}_mask">SegFormer mask</label>\n'
                f'            <label for="tab_{tab_id}_depth">Depth</label>\n'
                f'          </div>\n'
                f'          <div class="tab-panel tab-img">{img_block}</div>\n'
                f'          <div class="tab-panel tab-mask">{mask_block}</div>\n'
                f'          <div class="tab-panel tab-depth">{depth_block}</div>\n'
                f'        </figure>'
            )
            footprint_tabs_block = (
                f'        <figure class="tabs">\n'
                f'          <input type="radio" name="ftabs_{tab_id}" id="ftab_{tab_id}_fp" checked>\n'
                f'          <input type="radio" name="ftabs_{tab_id}" id="ftab_{tab_id}_sat">\n'
                f'          <input type="radio" name="ftabs_{tab_id}" id="ftab_{tab_id}_recon">\n'
                f'          <div class="tab-labels">\n'
                f'            <label for="ftab_{tab_id}_fp">Footprints</label>\n'
                f'            <label for="ftab_{tab_id}_sat">Satellite</label>\n'
                f'            <label for="ftab_{tab_id}_recon">Reconstruction</label>\n'
                f'          </div>\n'
                f'          <div class="tab-panel tab-img">{mm_block}</div>\n'
                f'          <div class="tab-panel tab-mask">{sat_block}</div>\n'
                f'          <div class="tab-panel tab-depth">{recon_block}</div>\n'
                f'        </figure>'
            )
            compare_block = (
                '      <div class="compare">\n'
                f'{pano_tabs_block}\n'
                f'{footprint_tabs_block}\n'
                '      </div>'
            )
            n_segs = len(getattr(vsv, "matched_segments", []) or [])
            heading_eff = (getattr(vsv, "heading", 0.0) + getattr(vsv, "best_offset", 0.0)) % 360.0
            view_blocks.append(
                f"    <details>\n"
                f"      <summary>View {i + 1} · heading {heading_eff:.1f}° · "
                f"{n_segs} matched segment{'s' if n_segs != 1 else ''}"
                f" · score {getattr(vsv, 'registration_score', 0.0):.2f}</summary>\n"
                f"{compare_block}\n"
                f"    </details>"
            )
        views_html = f"""
  <section class="views">
    <h2>Per-view: street view vs footprints ({len(views)} views)</h2>
    <p class="small">Click a view to expand. Left = street-view capture with
       matched-building boxes + numbered badges. Right = footprint minimap for
       the same heading (OSM + Microsoft ML footprints, F-SKY8). Compare the
       two to spot where the two footprint sources disagree with what the
       camera actually sees.</p>
{chr(10).join(view_blocks)}
  </section>
"""

    estimates_html = ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 1100px; margin: 1.5em auto; padding: 0 1em; color: #222; }}
  h1 {{ border-bottom: 2px solid #0a3070; padding-bottom: 0.2em; }}
  h2 {{ color: #0a3070; margin-top: 1.5em; }}
  table {{ border-collapse: collapse; margin: 0.5em 0; }}
  th, td {{ padding: 0.25em 0.7em; border: 1px solid #ddd; text-align: left; }}
  th {{ background: #f4f6fa; font-weight: 600; }}
  .summary th {{ text-align: right; width: 14em; }}
  .estimates tr.disagree {{ background: #fdecec; }}
  .small {{ font-size: 0.85em; color: #666; }}
  .legend {{ margin: 0.4em 0; }}
  .missing {{ color: #888; }}
  .compare {{ display: flex; gap: 1em; flex-wrap: wrap; align-items: flex-start; margin: 0.5em 0; }}
  .compare figure {{ flex: 1 1 480px; min-width: 320px; margin: 0; }}
  .compare figcaption {{ margin-bottom: 0.3em; font-weight: 600; }}
  /* CSS-only tabs for the pano panel (street view ↔ SegFormer mask). */
  figure.tabs > input[type=radio] {{ display: none; }}
  figure.tabs .tab-labels {{ display: flex; gap: 0.4em; margin-bottom: 0.3em; }}
  figure.tabs .tab-labels label {{
      padding: 0.25em 0.7em; cursor: pointer;
      border: 1px solid #ccc; border-bottom: none;
      background: #f4f6fa; font-size: 0.85em; font-weight: 600;
      border-radius: 4px 4px 0 0;
  }}
  figure.tabs .tab-panel {{ display: none; }}
  figure.tabs > input.tab-img:checked ~ .tab-labels label[for$="_img"],
  figure.tabs > input:nth-of-type(1):checked ~ .tab-labels label[for$="_img"] {{
      background: #fff; border-color: #0a3070; color: #0a3070;
  }}
  figure.tabs > input:nth-of-type(2):checked ~ .tab-labels label[for$="_mask"] {{
      background: #fff; border-color: #0a3070; color: #0a3070;
  }}
  figure.tabs > input:nth-of-type(3):checked ~ .tab-labels label[for$="_depth"] {{
      background: #fff; border-color: #0a3070; color: #0a3070;
  }}
  figure.tabs > input:nth-of-type(1):checked ~ .tab-img {{ display: block; }}
  figure.tabs > input:nth-of-type(2):checked ~ .tab-mask {{ display: block; }}
  figure.tabs > input:nth-of-type(3):checked ~ .tab-depth {{ display: block; }}
  nav.breadcrumb {{ font-size: 0.9em; margin-bottom: 1em; }}
  nav.breadcrumb a {{ color: #0a3070; text-decoration: none; }}
</style>
</head>
<body>
<nav class="breadcrumb"><a href="index.html">← {html.escape(region_name)} index</a></nav>
<h1>{html.escape(title)}</h1>

<section class="summary">
  <h2>Summary</h2>
  <table>
{summary_html}
  </table>
</section>

<section class="minimap">
  <h2>Footprints view</h2>
  {minimap_html}
  <p class="legend small">
    Blue solid = OSM coastline · faint blue fill = OSM water polygon ·
    faint green fill = OSM green areas (parks / grass / forest, F-SKY18) ·
    dashed grey = 1 km consideration window ·
    blue dots = pano-projected coastline (snapped to OSM along bearing) ·
    green dots = pano-projected vegetation base (snapped to OSM green).
    Coloured polygons = matched OSM building footprints (by segment colour).
  </p>
</section>
{views_html}
{estimates_html}
<footer class="small" style="margin-top:2em;color:#888;">
  Generated by city2stl.skyline_cv.html_report (F-SKY15)
</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Region index
# ---------------------------------------------------------------------------

def render_region_index(
    region_name: str,
    seed_views: list,
    building_heights: list[dict] | None = None,
    *,
    step_timings: list[tuple[str, float]] | None = None,
    screening_map_rel: str | None = None,
) -> str:
    """Render the per-region index.html.

    Lists every seed with its summary stats and links to the per-seed
    page. The building_heights aggregate (when provided) feeds the
    bottom-of-page totals. ``step_timings`` (label, seconds) renders a
    pipeline-timing table so contributors can see where a run spent its
    time.
    """
    title = f"{region_name} — Skyline diagnostic report"

    screening_map_html = ""
    if screening_map_rel:
        screening_map_html = f"""
<h2>Region screening / selection map</h2>
<img src="{html.escape(screening_map_rel)}" alt="region screening map"
     style="max-width:1000px;width:100%;height:auto;border:1px solid #ccc;">
<p class="small">Every screened camera location across the region. Stars = seeds,
   dots = auto-proposed standoff points; colour = Street-View coverage quality
   (green good · orange medium · red weak), arrow = screening heading. Faint
   grey = OSM building context.</p>
"""

    timings_html = ""
    if step_timings:
        # Each row is (label, seconds) or (label, seconds, level). The total
        # is the sum of level-0 rows only (sub-steps would double-count their
        # parent). Every row gets a "% of total" cell so sub-step shares are
        # visible — a level-2 row showing 12% means that one operation took
        # 12% of the entire wall-clock pipeline time, even though its parent
        # level-1 row reports the same time under its own % column.
        def _unpack(row):
            label, dt = row[0], row[1]
            level = row[2] if len(row) > 2 else 0
            return label, dt, level
        total = sum(_unpack(r)[1] for r in step_timings if _unpack(r)[2] == 0)
        rows = []
        for r in step_timings:
            label, dt, level = _unpack(r)
            pct = (dt / total * 100.0) if total > 0 else 0.0
            pct_cell = f"{pct:.1f}%"
            indent = ("&nbsp;&nbsp;&nbsp;&nbsp;" * level + "↳ ") if level else ""
            row_cls = f' class="lvl{level}"' if level else ""
            rows.append(
                f"      <tr{row_cls}><td>{indent}{html.escape(label)}</td>"
                f"<td>{dt:.2f} s</td><td>{pct_cell}</td></tr>"
            )
        timings_html = f"""
<h2>Pipeline timing</h2>
<table class="timings">
  <thead><tr><th>step</th><th>duration</th><th>% of total</th></tr></thead>
  <tbody>
{chr(10).join(rows)}
      <tr><th>total (level-0 steps)</th><th>{total:.2f} s</th><th>100.0%</th></tr>
  </tbody>
</table>
<p class="small">Wall-clock time per pipeline step. Indented rows
   (<code>↳</code>) are sub-steps of the preceding top-level row and are
   summed across all seeds. Their "% of total" shares the same denominator
   as their parent — useful for spotting which inner operation dominates a
   phase. The HTML render itself is not included (it runs after this table
   is built); see the console <code>[timing]</code> lines for that.</p>
"""

    # Aggregated building heights (headline output, ported from the PDF's
    # "Seed-Derived Building Heights" page). Each row is one OSM building with
    # its cross-seed median/weighted height and a disagreement metric — the
    # key "are matches pointing at the same building?" signal.
    heights_html = ""
    if building_heights:
        cross = [r for r in building_heights if r.get("n_seeds", 1) >= 2]
        single = [r for r in building_heights if r.get("n_seeds", 1) < 2]
        disagreements = sorted(
            float(r.get("seed_disagreement_m", 0.0)) for r in cross)

        def _pctile(vals, p):
            if not vals:
                return None
            k = (len(vals) - 1) * (p / 100.0)
            lo = int(k)
            hi = min(lo + 1, len(vals) - 1)
            return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)

        if disagreements:
            med = _pctile(disagreements, 50)
            p90 = _pctile(disagreements, 90)
            agree_line = (
                f"Cross-seed disagreement (lower = better): median "
                f"{med:.1f} m · p90 {p90:.1f} m · max {disagreements[-1]:.1f} m "
                f"over {len(cross)} buildings seen from ≥2 seeds."
            )
        else:
            agree_line = "No buildings seen from ≥2 seeds yet."

        heights_html = f"""
<h2>Building heights (aggregated)</h2>
<p>{len(building_heights)} buildings with estimates — {len(cross)} cross-seed (≥2 seeds), {len(single)} single-seed.</p>
<p class="small">{html.escape(agree_line)}</p>
"""

    # One row per UNIQUE seed name. seed_views contains one row per (seed, view),
    # so we dedupe by seed_name and pick the first row for the summary stats.
    seen = set()
    seed_rows = []
    for sv in seed_views:
        if sv.seed_name in seen:
            continue
        seen.add(sv.seed_name)
        slug = sv.seed_name[5:] if sv.seed_name.startswith("seed_") else sv.seed_name
        # Format pano-recovery peak/sigma compactly — these are the key
        # "is the registration confident" signals.
        recov_peak = getattr(sv, "pano_recovered_peak", None)
        recov_sigma = getattr(sv, "pano_recovered_sigma", None)
        recov_offset = getattr(sv, "pano_recovered_offset_deg", None)
        water = getattr(sv, "pano_water_frac", None)
        recov_cell = (
            f"{recov_offset:.0f}° (peak {recov_peak:.2f} / σ {recov_sigma:.2f})"
            if recov_offset is not None and recov_peak is not None and recov_sigma is not None
            else "—"
        )
        seed_rows.append(
            f"      <tr>"
            f"<td><a href=\"seed_{html.escape(slug)}.html\">{html.escape(sv.seed_name)}</a></td>"
            f"<td>{sv.seed_lat:.5f}, {sv.seed_lon:.5f}</td>"
            f"<td>{_fmt_optional_float(sv.pano_osm_iou, precision=2)}</td>"
            f"<td>{sv.pano_osm_n_keypoints or '—'}</td>"
            f"<td>{_fmt_optional_float(water, suffix='', precision=2) if water is not None else '—'}</td>"
            f"<td>{html.escape(recov_cell)}</td>"
            f"<td>{_fmt_optional_bool(sv.is_negative)}</td>"
            f"</tr>"
        )

    n_buildings = len(building_heights) if building_heights else 0
    n_seeds = len(seen)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 1100px; margin: 1.5em auto; padding: 0 1em; color: #222; }}
  h1 {{ border-bottom: 2px solid #0a3070; padding-bottom: 0.2em; }}
  table {{ border-collapse: collapse; margin: 0.5em 0; }}
  th, td {{ padding: 0.3em 0.7em; border: 1px solid #ddd; text-align: left; }}
  th {{ background: #f4f6fa; font-weight: 600; }}
  a {{ color: #0a3070; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  table.timings tr.lvl1 td {{ background: #fafbfd; color: #444; }}
  table.timings tr.lvl2 td {{ background: #fdfdfe; color: #666; font-size: 0.9em; }}
  .stats {{ display: flex; gap: 2em; margin: 1em 0; }}
  .stat {{ background: #f4f6fa; padding: 0.6em 1em; border-radius: 4px; }}
  .stat .num {{ font-size: 1.4em; font-weight: 600; color: #0a3070; }}
  .small {{ font-size: 0.85em; color: #666; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>

<div class="stats">
  <div class="stat"><div class="num">{n_seeds}</div>seeds</div>
  <div class="stat"><div class="num">{n_buildings}</div>aggregated buildings</div>
</div>
{screening_map_html}
{timings_html}
<h2>Seeds</h2>
<table>
  <thead>
    <tr>
      <th>seed</th><th>lat, lon</th>
      <th>pano↔OSM IoU</th><th>OSM kp</th>
      <th title="Fraction of pano pixels classified as water by SegFormer">water frac</th>
      <th title="Recovered heading offset, peak score, and score-curve sigma (lower = sharper recovery)">pano recovery</th>
      <th>negative</th>
    </tr>
  </thead>
  <tbody>
{chr(10).join(seed_rows)}
  </tbody>
</table>

<p class="small">
  pano↔OSM IoU (F-SKY13 Phase B) is populated only when pano-coastline
  recovery is enabled for the region. ``—`` means the diagnostic was
  not computed for that seed.
</p>
{heights_html}
<footer class="small" style="margin-top:2em;color:#888;">
  Generated by city2stl.skyline_cv.html_report (F-SKY15)
</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Top-level write
# ---------------------------------------------------------------------------

def write_region_report(
    out_dir: Path,
    region_name: str,
    seed_views: list,
    osm_data: dict,
    buildings_by_id: dict | None = None,
    building_heights: list[dict] | None = None,
    estimates_by_seed: dict | None = None,
    step_timings: list[tuple[str, float]] | None = None,
    screened: list | None = None,
    region_bbox=None,
    pano_results: list | None = None,
) -> None:
    """Write the full HTML report tree to ``out_dir``.

    Layout:
      out_dir/
        index.html
        seed_<name>.html       ← one per unique seed_name in seed_views
        assets/minimap/<name>.png

    ``estimates_by_seed`` is an optional dict mapping seed_name → list
    of ``RegisteredBuildingEstimate``. When provided, each per-seed
    page includes the estimates table with depth diagnostics.

    No-op (with a logged warning) if ``seed_views`` is empty. Never
    raises — HTML report failures must not break the PDF path.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    minimap_dir = out_dir / "assets" / "minimap"
    views_dir = out_dir / "assets" / "views"
    minimap_dir.mkdir(parents=True, exist_ok=True)
    views_dir.mkdir(parents=True, exist_ok=True)

    buildings_by_id = buildings_by_id or {}
    estimates_by_seed = dict(estimates_by_seed) if estimates_by_seed else {}

    # Group seed_views by seed_name. Each value is the ordered list of
    # per-view rows for that seed. The first entry is used for the
    # seed-level summary panel; all entries are walked for the per-view
    # image gallery.
    views_by_seed: dict[str, list["SeedViewRegistration"]] = {}
    for sv in seed_views:
        views_by_seed.setdefault(sv.seed_name, []).append(sv)

    # If estimates weren't supplied explicitly, auto-collect them from
    # the ``view_estimates`` field that the pipeline sets on each
    # SeedViewRegistration. This is the F-SKY15+F-SKY12 path: depth
    # diagnostics surface in the HTML estimates table without anyone
    # having to thread per-seed estimate dicts through manually.
    for seed_name, sv_list in views_by_seed.items():
        if estimates_by_seed.get(seed_name):
            continue
        collected: list = []
        for vsv in sv_list:
            ve = getattr(vsv, "view_estimates", None)
            if ve:
                collected.extend(ve)
        if collected:
            estimates_by_seed[seed_name] = collected

    # Index pano_results by seed_name for quick lookup.
    pano_by_seed: dict = {}
    for pr in (pano_results or []):
        pano_by_seed[getattr(pr, "seed_name", "")] = pr

    import os as _os  # noqa: PLC0415
    _depth_enabled = _os.environ.get(
        "SKYLINE_CV_HTML_DEPTH", "1").strip().lower() in (
            "1", "true", "yes", "on")
    _pano_layered = _os.environ.get(
        "SKYLINE_CV_PANO_LAYERED", "1").strip().lower() in (
            "1", "true", "yes", "on")

    for seed_name, sv_list in views_by_seed.items():
        primary = sv_list[0]
        # Strip a "seed_" prefix if the name already includes it so the
        # output filename doesn't double up (seed_seed_5.html → seed_5.html).
        slug = seed_name[5:] if seed_name.startswith("seed_") else seed_name

        # Minimap PNG (uses primary view for heading + minimap diagnostics)
        png_path = minimap_dir / f"{slug}.png"
        ok = _render_seed_minimap_png(png_path, primary, osm_data, buildings_by_id)
        minimap_rel = f"assets/minimap/{slug}.png" if ok else None

        # Pano-layered path: one per-seed page showing 6 layers of the
        # stitched 360° pano (Street view, SegFormer mask, Depth,
        # Footprints, Satellite, Reconstruction). Default ON; the legacy
        # per-view path stays available via SKYLINE_CV_PANO_LAYERED=0.
        if _pano_layered and not primary.is_negative:
            pano_result = pano_by_seed.get(seed_name)
            pano_rels: dict = {"minimap": minimap_rel}
            views_pano_dir = out_dir / "assets" / "pano"
            pano_segments_for_overlay = (
                pano_result.matched_segments
                if pano_result is not None else None
            )
            if pano_result is not None and pano_result.pano_image is not None:
                pano_rgb_path = views_pano_dir / f"{slug}_pano.png"
                pano_rgb_path.parent.mkdir(parents=True, exist_ok=True)
                from PIL import Image as _PILImg  # noqa: PLC0415
                _hpc = getattr(pano_result, "headings_per_col", None)
                try:
                    _PILImg.fromarray(
                        pano_result.pano_image).save(
                            pano_rgb_path, optimize=True)
                    _draw_pano_bboxes_inplace(
                        pano_rgb_path, pano_segments_for_overlay)
                    _draw_pano_north_line_inplace(pano_rgb_path, _hpc)
                    pano_rels["pano_rgb"] = f"assets/pano/{slug}_pano.png"
                except Exception:
                    pass
                # SegFormer 4-class pano overlay (free; reuses cached masks).
                # The anchor_offset must match what ``_stitch_pano_composite``
                # used or the mask is shifted relative to the RGB.
                seg_png = views_pano_dir / f"{slug}_pano_seg.png"
                if _render_pano_segformer_overlay_png(
                        seg_png, pano_result.pano_image, sv_list, primary.fov,
                        anchor_offset_deg=float(
                            pano_result.anchor_offset_deg),
                        pano_result=pano_result):
                    _draw_pano_bboxes_inplace(
                        seg_png, pano_segments_for_overlay)
                    _draw_pano_north_line_inplace(seg_png, _hpc)
                    pano_rels["pano_segformer"] = (
                        f"assets/pano/{slug}_pano_seg.png")
                if _depth_enabled:
                    depth_png = views_pano_dir / f"{slug}_pano_depth.png"
                    if _render_pano_depth_png(
                            depth_png, pano_result.pano_image,
                            pano_result=pano_result):
                        _draw_pano_bboxes_inplace(
                            depth_png, pano_segments_for_overlay)
                        _draw_pano_north_line_inplace(depth_png, _hpc)
                        pano_rels["pano_depth"] = (
                            f"assets/pano/{slug}_pano_depth.png")
                    recon_png = views_pano_dir / f"{slug}_pano_recon.png"
                    if _render_pano_reconstruction_png(
                            recon_png, pano_result, buildings_by_id,
                            osm_data=osm_data):
                        pano_rels["pano_reconstruction"] = (
                            f"assets/pano/{slug}_pano_recon.png")
                    # 2D distance-vs-bearing strip (depth silhouette vs
                    # OSM nearest). Lives in the pano-space tab group
                    # because x = pano column → degree.
                    scan_png = views_pano_dir / f"{slug}_pano_scan.png"
                    if _render_pano_bearing_scan_png(
                            scan_png, pano_result, osm_data):
                        pano_rels["pano_scan"] = (
                            f"assets/pano/{slug}_pano_scan.png")
            # Polar footprint + satellite minimaps (replaces rectangular).
            polar_fp_png = minimap_dir / f"{slug}_polar_fp.png"
            if _render_pano_minimap_polar_png(
                    polar_fp_png, primary, osm_data, buildings_by_id,
                    pano_result, satellite_bg=False):
                pano_rels["minimap"] = f"assets/minimap/{slug}_polar_fp.png"
            polar_sat_png = minimap_dir / f"{slug}_polar_sat.png"
            if _render_pano_minimap_polar_png(
                    polar_sat_png, primary, osm_data, buildings_by_id,
                    pano_result, satellite_bg=True):
                pano_rels["minimap_sat"] = f"assets/minimap/{slug}_polar_sat.png"
            # OSM-tagged building heights (polar). Shares the unified
            # axis with the other top-down panels so bearings and
            # distances are directly comparable.
            polar_heights_png = minimap_dir / f"{slug}_polar_heights.png"
            if _render_pano_heights_polar_png(
                    polar_heights_png, primary, osm_data, pano_result):
                pano_rels["minimap_heights"] = (
                    f"assets/minimap/{slug}_polar_heights.png")

            page_html = render_seed_pano_page(
                primary, sv_list, pano_result, region_name, minimap_rel,
                pano_rel_paths=pano_rels,
            )
            (out_dir / f"seed_{slug}.html").write_text(page_html, encoding="utf-8")
            continue

        # Per-view image PNGs + per-view footprint minimaps (one each per
        # view). The per-view minimap uses that view's own heading + matched
        # segments, so it sits side-by-side with the matching street-view
        # image for direct comparison.
        view_image_rels: list[str | None] = []
        view_minimap_rels: list[str | None] = []
        view_mask_rels: list[str | None] = []
        view_satellite_rels: list[str | None] = []
        view_depth_rels: list[str | None] = []
        view_recon_rels: list[str | None] = []
        # Depth rendering is slow (1-2 s/view on CPU via Depth Anything V2).
        # Gate it behind an env var so the default report ships fast and
        # users can opt in for a deeper inspection. The reconstruction
        # subtab also requires depth, so both share the same gate.
        import os as _os  # noqa: PLC0415
        _depth_enabled = _os.environ.get(
            "SKYLINE_CV_HTML_DEPTH", "1").strip().lower() in (
                "1", "true", "yes", "on")
        for i, vsv in enumerate(sv_list):
            view_png = views_dir / f"{slug}_view_{i}.png"
            if _save_view_image_png(view_png, vsv):
                view_image_rels.append(f"assets/views/{slug}_view_{i}.png")
            else:
                view_image_rels.append(None)

            mm_png = minimap_dir / f"{slug}_view_{i}.png"
            if _render_seed_minimap_png(mm_png, vsv, osm_data, buildings_by_id):
                view_minimap_rels.append(f"assets/minimap/{slug}_view_{i}.png")
            else:
                view_minimap_rels.append(None)

            sat_png = minimap_dir / f"{slug}_view_{i}_sat.png"
            if _render_seed_minimap_png(
                    sat_png, vsv, osm_data, buildings_by_id,
                    satellite_bg=True):
                view_satellite_rels.append(
                    f"assets/minimap/{slug}_view_{i}_sat.png")
            else:
                view_satellite_rels.append(None)

            mask_png = views_dir / f"{slug}_view_{i}_mask.png"
            if _render_view_mask_png(mask_png, vsv):
                view_mask_rels.append(f"assets/views/{slug}_view_{i}_mask.png")
            else:
                view_mask_rels.append(None)

            if _depth_enabled:
                depth_png = views_dir / f"{slug}_view_{i}_depth.png"
                if _render_view_depth_png(depth_png, vsv):
                    view_depth_rels.append(
                        f"assets/views/{slug}_view_{i}_depth.png")
                else:
                    view_depth_rels.append(None)
                recon_png = views_dir / f"{slug}_view_{i}_recon.png"
                if _render_view_reconstruction_png(
                        recon_png, vsv, buildings_by_id):
                    view_recon_rels.append(
                        f"assets/views/{slug}_view_{i}_recon.png")
                else:
                    view_recon_rels.append(None)
            else:
                view_depth_rels.append(None)
                view_recon_rels.append(None)

        page_html = render_seed_page(
            primary, region_name, minimap_rel,
            estimates=estimates_by_seed.get(seed_name),
            views=sv_list,
            view_image_rel_paths=view_image_rels,
            view_minimap_rel_paths=view_minimap_rels,
            view_mask_rel_paths=view_mask_rels,
            view_satellite_rel_paths=view_satellite_rels,
            view_depth_rel_paths=view_depth_rels,
            view_reconstruction_rel_paths=view_recon_rels,
        )
        (out_dir / f"seed_{slug}.html").write_text(page_html, encoding="utf-8")

    # Region-wide screening / selection map (PNG, reuses the PDF renderer).
    screening_map_rel = None
    if screened and region_bbox is not None:
        sm_path = out_dir / "assets" / "screening_map.png"
        if _render_screening_map_png(sm_path, region_bbox, screened, osm_data):
            screening_map_rel = "assets/screening_map.png"

    index_html = render_region_index(
        region_name, seed_views, building_heights, step_timings=step_timings,
        screening_map_rel=screening_map_rel)
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")

    logger.info(
        "F-SKY15 HTML report written: %s (%d seeds, %d total views)",
        out_dir, len(views_by_seed), sum(len(v) for v in views_by_seed.values()),
    )
