"""skyline report_plots — split (A3) (_view_plots)."""
from __future__ import annotations
import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)


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
        from ..region_pdf import _draw_location_map  # noqa: PLC0415
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
        from ..depth_estimation import predict_pano_depth  # noqa: PLC0415
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
    osm_data: "dict | None" = None,
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
        from ..depth_estimation import predict_pano_depth  # noqa: PLC0415
        from ..region_pdf import _bearing_deg, _distance_m  # noqa: PLC0415
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
            from ..region_pdf import _SEGMENT_PALETTE  # noqa: PLC0415
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

        # Grey OSM building context: same as pano reconstruction so the
        # per-view scatter is readable without opening the pano tab.
        if osm_data is not None:
            try:
                grey_rgba = (0.7, 0.7, 0.7, 0.4)
                max_dist = max(
                    [d for _, d, _, _ in (recon_dots + osm_dots)] + [500.0]) * 1.1
                for feat in (osm_data.get("buildings", {}).get(
                        "features") or [])[:6000]:
                    geom = feat.get("geometry") or {}
                    if geom.get("type") not in ("Polygon", "MultiPolygon"):
                        continue
                    rings = (
                        [geom.get("coordinates", [[]])[0]]
                        if geom.get("type") == "Polygon"
                        else [poly[0]
                              for poly in geom.get("coordinates", [])]
                    )
                    for ring in rings:
                        pts_cart = []
                        any_in = False
                        for (lon, lat) in (ring or []):
                            b = _bearing_deg(sv.seed_lat, sv.seed_lon, lat, lon)
                            d = _distance_m(sv.seed_lat, sv.seed_lon, lat, lon)
                            if d <= max_dist:
                                any_in = True
                            x, y = _to_xy(b, min(d, max_dist))
                            if abs(x) <= half_fov * 1.2:
                                pts_cart.append((x, y))
                        if any_in and len(pts_cart) >= 3:
                            from matplotlib.patches import Polygon as MPP  # noqa
                            ax.add_patch(MPP(
                                pts_cart, closed=True, color=grey_rgba,
                                zorder=0, linewidth=0.0))
            except Exception:
                pass

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
        from ..region_pdf import _draw_view_minimap  # noqa: PLC0415
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


__all__ = [
    '_save_view_image_png',
    '_render_screening_map_png',
    '_render_view_mask_png',
    '_render_view_depth_png',
    '_render_view_reconstruction_png',
    '_render_seed_minimap_png',
]
