"""Part of ``city2stl.skyline._region_render._pages`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import cv2
import matplotlib.pyplot as plt
import numpy as np

from ..region_config import (
    _SEGMENT_PALETTE,
)
from ..region_types import SeedViewRegistration, StitchedPanoResult
from ._draw import _draw_view_minimap


def _render_stitched_pano_page(
    pdf,
    pr: StitchedPanoResult,
    osm_data: dict,
    buildings_by_id: dict | None = None,
    seed_views: list[SeedViewRegistration] | None = None,
) -> None:
    """Render one comparison page per seed: stitched 360° pano with segment
    overlays on top, summary stats below (per-view vs pano numbers), and a
    minimap with all pano-matched footprints colored.

    The pano image is full-width with all 12 stitched crops laid horizontally.
    Each detected segment gets a colored bounding box + numbered badge so the
    user can pair segments to footprints on the minimap (same convention as
    the per-view pages).
    """
    fig = plt.figure(figsize=(14, 8.5))
    fig.suptitle(
        f"Stitched 360° pano — {pr.seed_name}  "
        f"anchor_offset={pr.anchor_offset_deg:+.1f}°  "
        f"segments={pr.n_segments}  matched={pr.n_matched}  "
        f"buildings_in_view={pr.n_buildings_in_view}",
        fontsize=12,
    )

    # Top half: pano image with segment overlays. Crop vertically to building
    # band if available so we don't waste space on water/sky.
    pano_img = pr.pano_image
    band = pr.band_y
    if band is not None:
        y0, y1 = band
        H = pano_img.shape[0]
        y0 = max(0, min(H - 1, int(y0)))
        y1 = max(y0 + 30, min(H, int(y1) + 1))
        if y1 - y0 > 60:
            pano_img = pano_img[y0:y1, :, ...]
    # Burn segments onto a copy so we don't mutate the cached array.
    overlay = pano_img.copy()
    for i, seg in enumerate(pr.matched_segments):
        if seg.get("matched_projection") is None:
            continue
        color = _SEGMENT_PALETTE[i % len(_SEGMENT_PALETTE)]
        xL, xR = int(seg["x_left"]), int(seg["x_right"])
        top_y, base_y = int(seg["top_y"]), int(seg["base_y"])
        if band is not None:
            top_y -= band[0]
            base_y -= band[0]
        top_y = max(0, top_y)
        base_y = min(overlay.shape[0] - 1, base_y)
        if base_y <= top_y or xR <= xL:
            continue
        cv2.rectangle(overlay, (xL, top_y), (xR, base_y), color, 2)
        cv2.circle(overlay, (xL + 11, max(15, top_y + 7)), 11, color, -1)
        cv2.circle(overlay, (xL + 11, max(15, top_y + 7)), 11, (0, 0, 0), 1)
        cv2.putText(
            overlay, str(i + 1),
            (xL + (4 if i + 1 < 10 else 1), max(20, top_y + 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
        )

    ax_pano = fig.add_axes([0.03, 0.50, 0.94, 0.38])
    ax_pano.imshow(overlay)
    # Heading-tick overlay along the bottom: every 30° draw a vertical
    # marker + compass label so we can verify visually that the stitching's
    # column-to-heading map matches the depicted scene. (If e.g. column
    # marked "180°" doesn't show buildings the user expects in the south
    # direction, that pinpoints a heading-mapping bug.)
    headings_per_col = getattr(pr, "headings_per_col", None)
    if headings_per_col is not None and headings_per_col.size > 0:
        h_img = overlay.shape[0]
        labeled_cardinals = [
            (0.0, "N"), (45.0, "NE"), (90.0, "E"), (135.0, "SE"),
            (180.0, "S"), (225.0, "SW"), (270.0, "W"), (315.0, "NW"),
        ]
        for hdg, name in labeled_cardinals:
            # Find the column closest to this compass heading
            diffs = np.abs(((headings_per_col - hdg + 180.0) % 360.0) - 180.0)
            if diffs.min() > 8.0:
                continue
            col = int(np.argmin(diffs))
            ax_pano.axvline(col, color="yellow", linewidth=0.5, alpha=0.7)
            ax_pano.text(
                col, h_img * 0.97, f"{name} {hdg:.0f}°",
                color="black", fontsize=8, ha="center", va="bottom",
                bbox=dict(boxstyle="round,pad=0.15", fc="yellow",
                          ec="black", alpha=0.85, lw=0.5),
            )
    ax_pano.axis("off")
    ax_pano.set_title(
        "Stitched pano — mask-stitched per-view masks (yellow ticks = compass headings)",
        fontsize=10,
    )

    # Bottom-left: pano-matched-footprint minimap.
    ax_map = fig.add_axes([0.04, 0.06, 0.45, 0.36])
    _draw_view_minimap(
        ax_map,
        pr.seed_lat,
        pr.seed_lon,
        # Use 0° heading and 360° FOV stand-in: we just want all matched
        # footprints visible. The minimap's per-segment bearing rays will
        # still go to the right places because they're computed from the
        # actual matched-footprint centroids when available.
        heading_deg=0.0,
        fov_deg=360.0,
        osm_data=osm_data,
        matched_segments=pr.matched_segments,
        buildings_by_id=buildings_by_id,
        image_width=pr.pano_image.shape[1],
    )

    # Bottom-right: side-by-side comparison vs per-view aggregate.
    ax_cmp = fig.add_axes([0.52, 0.06, 0.45, 0.36])
    ax_cmp.axis("off")
    per_view_for_seed = (
        [sv for sv in (seed_views or []) if sv.seed_name == pr.seed_name]
    )
    n_view_segments = sum(len(sv.matched_segments) for sv in per_view_for_seed)
    n_view_matched = sum(
        1 for sv in per_view_for_seed
        for seg in sv.matched_segments
        if seg.get("matched_projection")
    )
    matched_pano_ids = {
        seg["matched_projection"]["feature_id"]
        for seg in pr.matched_segments
        if seg.get("matched_projection")
    }
    matched_view_ids = {
        seg["matched_projection"]["feature_id"]
        for sv in per_view_for_seed
        for seg in sv.matched_segments
        if seg.get("matched_projection")
    }
    overlap_ids = matched_pano_ids & matched_view_ids
    pano_only = matched_pano_ids - matched_view_ids
    view_only = matched_view_ids - matched_pano_ids
    cmp_lines = [
        "PANO vs PER-VIEW (this seed only)",
        "",
        f"{'metric':<28}{'pano':>10}{'per-view':>12}",
        f"{'-'*52}",
        f"{'segments detected':<28}{pr.n_segments:>10d}{n_view_segments:>12d}",
        f"{'segments with OSM match':<28}{pr.n_matched:>10d}{n_view_matched:>12d}",
        f"{'distinct buildings matched':<28}{len(matched_pano_ids):>10d}{len(matched_view_ids):>12d}",
        "",
        f"  overlap (both methods agree):  {len(overlap_ids):>4d}",
        f"  pano only:                     {len(pano_only):>4d}",
        f"  per-view only:                 {len(view_only):>4d}",
    ]
    ax_cmp.text(
        0.02, 0.98, "\n".join(cmp_lines),
        family="monospace", fontsize=9, va="top", transform=ax_cmp.transAxes,
    )

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _count_seg_flags(matched_segments: list[dict]) -> tuple[int, int, int]:
    """Tally (F=outside_FOV, B=not_closest, P=implausible_vs_proxy) flags
    across a view's matched segments. Used by the per-view PDF header.
    Extracted so the rendering path doesn't carry an inline 20-line
    loop just to compute three counts.
    """
    n_fov_fail = n_not_closest = n_implausible = 0
    for seg in matched_segments:
        if seg.get("matched_projection") is None:
            continue
        if not bool(seg.get("bearing_in_fov", True)):
            n_fov_fail += 1
        if not bool(seg.get("is_closest_in_bin", True)):
            n_not_closest += 1
        proxy_h = float(seg.get("height_proxy_m", 0.0))
        pred_h = seg.get("predicted_height_m", float("nan"))
        if (
            proxy_h >= 3.0
            and isinstance(pred_h, float)
            and np.isfinite(pred_h)
            and (pred_h > proxy_h * 5.0 or pred_h < proxy_h * 0.2)
        ):
            n_implausible += 1
    return n_fov_fail, n_not_closest, n_implausible


def _render_seed_view_page(
    pdf,
    sv: SeedViewRegistration,
    osm_data: dict,
    buildings_by_id: dict | None = None,
) -> None:
    """Render one per-view PDF page.

    F-SKY7 layout (replaces F-SKY4 cyan-overlay + bottom legend table):
      - Top-left: Street View image with skyline-segment overlays.
      - Bottom-left: SegFormer building mask in its own panel (faint
        photo background + cyan mask) — direct side-by-side comparison
        with the segment panel above lets you spot "mask present but
        no segment" failures at a glance.
      - Right: minimap (full height) — taller than before since the
        bottom legend table was removed.

    Flag counts (F / B / P) that used to live in the legend table now
    appear in the figure title.
    """
    fig = plt.figure(figsize=(14, 8.5))

    # ── Title: pose + per-flag counts ─────────────────────────────────────
    aerial_tag = "  [AERIAL — height skipped]" if sv.is_aerial else ""
    if getattr(sv, "is_negative", False):
        aerial_tag += "  [NEGATIVE EXAMPLE — estimates excluded]"
    effective_heading = (sv.heading + sv.best_offset) % 360.0
    n_fov_fail, n_not_closest, n_implausible = _count_seg_flags(
        sv.matched_segments)
    n_total = len(sv.matched_segments)
    flags_str = (
        f"  flags={n_fov_fail}F/{n_not_closest}B/{n_implausible}P of {n_total}"
        if n_total else ""
    )
    # View-level cross-checks (set by _seed_multiview_registration):
    #   bearing_delta_deg per match → median + MAD across the view
    #   multi_building_candidate per match → count of wide/cluster segments
    deltas = [
        float(s["bearing_delta_deg"]) for s in sv.matched_segments
        if s.get("matched_projection") is not None
        and s.get("bearing_delta_deg") is not None
        and np.isfinite(s.get("bearing_delta_deg", float("nan")))
    ]
    heading_str = ""
    if len(deltas) >= 3:
        med = float(np.median(deltas))
        mad = float(np.median([abs(d - med) for d in deltas]))
        heading_str = f"  Δh̃={med:+.1f}°(MAD={mad:.0f}°)"
    n_wide = sum(
        1 for s in sv.matched_segments
        if s.get("matched_projection") is not None
        and s.get("multi_building_candidate")
    )
    wide_str = f"  multi-bldg={n_wide}" if n_wide else ""
    n_corrected = sum(
        1 for s in sv.matched_segments
        if s.get("match_corrected")
    )
    corr_str = f"  ✎{n_corrected}" if n_corrected else ""
    # F-CLEAN5: surface the F-SKY10 cross-view score in the per-view header.
    # Each matched segment's `match_diagnostics[0]` carries the per-candidate
    # scoring breakdown including `cv` when the cross-view scorer ran.
    # We aggregate as mean+min across matched segments — the mean gives a
    # whole-view "how well does the cross-view signal AGREE with this match
    # set" indicator; the min surfaces single suspect matches without
    # forcing the user to dig into per-segment metadata.
    cv_vals = []
    for _s in sv.matched_segments:
        if _s.get("matched_projection") is None:
            continue
        _d = (_s.get("match_diagnostics") or [{}])[0]
        if "cv" in _d:
            try:
                cv_vals.append(float(_d["cv"]))
            except (TypeError, ValueError):
                continue
    cv_str = (
        f"  cv̄={float(np.mean(cv_vals)):.2f}/min={float(np.min(cv_vals)):.2f}"
        if cv_vals else ""
    )
    fig.suptitle(
        f"Seed view — {sv.seed_name}  "
        f"effective_heading={effective_heading:.1f}°  "
        f"(api={sv.heading:.0f}° + offset={sv.best_offset:+.1f}°)  "
        f"reg_score={sv.registration_score:.2f}px  iou={sv.iou:.2f}  "
        f"segments={n_total}  "
        f"estimates={sv.estimates_count}"
        f"{flags_str}{heading_str}{wide_str}{corr_str}{cv_str}"
        f"{aerial_tag}",
        fontsize=11,
    )
    # Decoder for the F/B/P + new diagnostics shorthand in the title.
    if n_total and (
        n_fov_fail or n_not_closest or n_implausible
        or len(deltas) >= 3 or n_wide or n_corrected
    ):
        fig.text(
            0.5, 0.955,
            "F = matched bearing outside FOV   "
            "B = matched building not closest in column bin   "
            "P = predicted height implausible vs sqrt-area proxy   "
            "Δh̃ = median(true-bearing − effective-heading) across matches  "
            "(non-zero ⇒ heading bias)   "
            "multi-bldg = wide segment that spans multiple OSM polygons   "
            "✎N = N matches corrected by post-hoc rescue   "
            "cv̄/min = mean/min F-SKY10 cross-view score across matched segments",
            ha="center", va="top", fontsize=7, color="0.35",
        )

    # ── Crop the displayed image to the building band ────────────────────
    # Views looking across water otherwise devote 60-70% of the panel to
    # empty sea/sky. Segment x_left/x_right/top_y/base_y stay in original-
    # image coords so overlays still register against the crop.
    display_img = sv.image
    crop_y0 = crop_y1 = None
    if sv.band_y is not None:
        y0, y1 = sv.band_y
        h_img = sv.image.shape[0]
        y0 = max(0, min(h_img - 1, int(y0)))
        y1 = max(y0 + 30, min(h_img, int(y1) + 1))
        if y1 - y0 > 60:
            display_img = sv.image[y0:y1, :, ...]
            crop_y0, crop_y1 = y0, y1

    # ── Top-left: image + skyline segment overlays ────────────────────────
    ax_img = fig.add_axes([0.03, 0.56, 0.55, 0.34])
    ax_img.imshow(display_img)
    ax_img.axis("off")
    img_title = "Street View + skyline segments"
    if crop_y0 is not None:
        img_title += f"  (cropped to band y={crop_y0}..{crop_y1})"
    ax_img.set_title(img_title, fontsize=10)

    # ── Mid-left: SegFormer building mask on its own ─────────────────────
    ax_mask = fig.add_axes([0.03, 0.21, 0.55, 0.32])
    if sv.building_mask is not None:
        mask_disp = sv.building_mask
        if crop_y0 is not None:
            mask_disp = mask_disp[crop_y0:crop_y1, :]
        # Faint greyscale photo as background context so you can see what
        # the mask is covering, then the mask as a cyan layer on top.
        ax_mask.imshow(display_img, alpha=0.35)
        rgba = np.zeros((*mask_disp.shape, 4), dtype=np.float32)
        rgba[..., 1] = 0.85
        rgba[..., 2] = 1.0
        rgba[..., 3] = np.where(mask_disp, 0.65, 0.0)
        ax_mask.imshow(rgba)
        ax_mask.set_title(
            "SegFormer building mask (cyan = building class)",
            fontsize=10,
        )
    else:
        ax_mask.text(
            0.5, 0.5, "(no SegFormer mask available)",
            ha="center", va="center", transform=ax_mask.transAxes,
            fontsize=10, color="grey",
        )
        ax_mask.set_title("SegFormer building mask", fontsize=10)
    ax_mask.axis("off")

    # ── Bottom-left: column-coverage strip with peak markers ──────────────
    # The 1-D signal `detect_building_silhouettes` works on. Visible peaks
    # (kept = match; unmatched = no match) plotted as vertical bars so the
    # user can tell apart "silhouette detector missed the tower" (no bar
    # above a tall mask column) from "matcher rejected the peak" (bar
    # present but unmatched). Critical for diagnosing seed_5-style cases.
    ax_strip = fig.add_axes([0.03, 0.05, 0.55, 0.13])
    if sv.building_mask is not None:
        coverage = sv.building_mask.astype(np.float32).mean(axis=0)
        xs = np.arange(coverage.size)
        ax_strip.fill_between(xs, 0.0, coverage,
                              color=(0.20, 0.55, 0.75, 0.45), linewidth=0)
        ax_strip.plot(xs, coverage, color=(0.10, 0.30, 0.55, 0.9),
                      linewidth=0.7)
        for seg in sv.matched_segments:
            try:
                peak_x = int(seg.get(
                    "peak_x",
                    0.5 * (float(seg.get("x_left", 0))
                           + float(seg.get("x_right", 0))),
                ))
            except Exception:
                continue
            if peak_x < 0 or peak_x >= coverage.size:
                continue
            matched = seg.get("matched_projection") is not None
            color = (0.0, 0.65, 0.0, 0.95) if matched else (
                0.85, 0.40, 0.0, 0.95)
            ax_strip.axvline(peak_x, color=color, linewidth=1.0)
        ax_strip.set_xlim(0, max(1, coverage.size - 1))
        ax_strip.set_ylim(0, 1.0)
        ax_strip.set_xlabel("column x (px)", fontsize=8)
        ax_strip.set_ylabel("mask coverage", fontsize=8)
        ax_strip.tick_params(labelsize=7)
        ax_strip.grid(alpha=0.20)
        ax_strip.set_title(
            "Per-column building mask coverage  "
            "(green = matched peak, orange = unmatched peak)",
            fontsize=9,
        )
    else:
        ax_strip.axis("off")

    # ── Right: minimap, full page height ─────────────────────────────────
    ax_map = fig.add_axes([0.62, 0.05, 0.35, 0.85])
    # Use heading + best_offset for the minimap arrow because building
    # projections were computed with this effective heading during
    # registration.
    _draw_view_minimap(
        ax_map,
        sv.seed_lat,
        sv.seed_lon,
        sv.heading + sv.best_offset,
        sv.fov,
        osm_data,
        sv.matched_segments,
        buildings_by_id=buildings_by_id,
        image_width=sv.image.shape[1],
        pano_osm_iou=sv.pano_osm_iou,
        pano_osm_n_keypoints=sv.pano_osm_n_keypoints,
        pano_projected_coastline=sv.pano_projected_coastline,
    )

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)
