#!/usr/bin/env python3
"""Diagnostic PDF for cartagena seed_5 — visualises every pipeline step
to expose exactly where the "10 bounding boxes" pano limit originates.

Usage (from the strm2stl/ directory)::

    PYTHONPATH=. python city2stl/skyline/scripts/14_seed5_diagnostic.py \
        [--region cartagena] [--seed seed_5] [--out <path>]

The script re-uses the same pipeline functions as the main report
(region_pdf + pipeline) so results are byte-for-byte identical to the
production run.  No new Street View API calls are made — images are
served from the ``runs/image_cache/`` disk cache.

Output: a 9-page A3-landscape PDF, one page per pipeline stage.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — must be set before pyplot
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle

# ---------------------------------------------------------------------------
# Path bootstrap — makes "PYTHONPATH=. python scripts/14_..." work.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[3]  # …/strm2stl/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Pipeline imports (must come after path bootstrap)
# ---------------------------------------------------------------------------
from city2stl.skyline.region_pdf import (  # noqa: E402
    SkylinePoint,
    _attach_building_terrain,
    _capture_pano_views,
    _load_osm_for_region,
    _load_region_bbox,
    _load_site_anchor_overrides,
    _load_site_negative_seeds,
    _load_site_seed_urls,
    _osm_to_building_records,
    _parse_streetview_url,
    _recover_anchor_offset,
    _stitch_pano_composite,
    _resolve_api_key,
)
from city2stl.skyline.pipeline import (  # noqa: E402
    BuildingRecord,
    CapturedView,
    _merge_silhouette_sources,
    _neural_sky_and_building_masks,
    _neural_water_mask,
    detect_building_silhouettes,
    detect_buildings_from_mask,
    match_segments_to_buildings,
    osm_anchor_silhouettes,
    osm_sam_instance_silhouettes,
    register_view_to_osm,
    stitch_pano_masks,
    stitch_pano_views,
    project_buildings_to_pano,
    detect_buildings_from_mask as _det_pano,
    match_segments_to_buildings as _match_pano,
    compute_building_band,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = 20.0, 14.0  # inches — A3 landscape
SPIN_STEP_DEG = 30.0
SPIN_HEADINGS = tuple(float(h) for h in np.arange(0.0, 360.0, SPIN_STEP_DEG))
# Key views toward the main building cluster (updated at runtime to nearest
# actual captures).
_PREFERRED_KEY_HEADINGS = (225.0, 270.0, 315.0)

# Segment colours (tab10 cycle, index 0-based)
_CMAP_TAB10 = plt.get_cmap("tab10")


def _seg_color(i: int):
    return _CMAP_TAB10(i % 10)


# ---------------------------------------------------------------------------
# Tiny draw helpers
# ---------------------------------------------------------------------------

def _draw_seg_boxes(ax, segments, color="lime", label_prefix=""):
    """Draw bounding boxes for a list of segment dicts on *ax*."""
    for i, seg in enumerate(segments):
        x0 = float(seg.get("x_left", seg.get("x_left_px", 0)))
        x1 = float(seg.get("x_right", seg.get("x_right_px", x0 + 20)))
        y0 = float(seg.get("y_top", 0))
        y1 = float(seg.get("y_bot", seg.get("base_y", y0 + 20)))
        w = max(x1 - x0, 1)
        h = max(y1 - y0, 1)
        rect = Rectangle((x0, y0), w, h,
                          linewidth=1.5, edgecolor=color, facecolor="none")
        ax.add_patch(rect)
        if label_prefix:
            ax.text(x0 + 2, y0 + 2, f"{label_prefix}{i}",
                    color=color, fontsize=7, va="top")


def _overlay_mask(ax, mask, cmap="Blues", alpha=0.4):
    """Overlay a boolean/uint8 mask as a semi-transparent colour.

    *cmap* may be a matplotlib colormap name OR a plain colour string (e.g.
    "cyan").  Plain colours are turned into a solid-colour overlay.
    """
    if mask is None:
        return
    rgba = np.zeros((*mask.shape[:2], 4), dtype=np.float32)
    try:
        base_color = plt.get_cmap(cmap)(0.7)
    except ValueError:
        import matplotlib.colors as _mc
        base_color = _mc.to_rgba(cmap)
    rgba[..., :3] = base_color[:3]
    rgba[..., 3] = np.clip(mask.astype(np.float32) * alpha, 0, alpha)
    ax.imshow(rgba, interpolation="nearest")


def _draw_osm_projections(ax, projections, color="yellow", lw=1.0):
    """Draw vertical projection lines for each OSM building projection."""
    for proj in projections:
        x = float(proj.get("x_px", 0))
        ax.axvline(x=x, color=color, linewidth=lw, alpha=0.7)


def _show_image(ax, img, title=""):
    ax.imshow(img)
    ax.set_title(title, fontsize=9)
    ax.axis("off")


# ---------------------------------------------------------------------------
# Segment field normalisation
# ---------------------------------------------------------------------------

def _seg_x_left(s):  return float(s.get("x_left", s.get("x_left_px", 0)))
def _seg_x_right(s): return float(s.get("x_right", s.get("x_right_px", 1)))
def _seg_y_top(s):   return float(s.get("y_top", 0))
def _seg_y_bot(s):   return float(s.get("y_bot", s.get("base_y", 1)))


# ---------------------------------------------------------------------------
# Helper: pick the 3 key views nearest the preferred headings
# ---------------------------------------------------------------------------

def _pick_key_views(cached_views: list[dict],
                    preferred=(225.0, 270.0, 315.0)) -> list[dict]:
    """Return the 3 cached_view entries whose geo_heading is closest to each
    element of *preferred*. Falls back gracefully if fewer than 3 views exist.
    """
    result: list[dict] = []
    for target in preferred:
        best = min(cached_views,
                   key=lambda cv, t=target:
                   abs((cv["geo_heading"] - t + 180) % 360 - 180))
        if best not in result:
            result.append(best)
    # Pad if we ended up with duplicates / too few
    for cv in cached_views:
        if len(result) >= 3:
            break
        if cv not in result:
            result.append(cv)
    return result[:3]


# ---------------------------------------------------------------------------
# Per-view pipeline execution — captures intermediate state
# ---------------------------------------------------------------------------

def _run_per_view_pipeline(
    cv: dict,
    seed_buildings: list[BuildingRecord],
    anchor_offset: float,
    enable_fsky5: bool = False,
) -> dict:
    """Run the full per-view detection + matching pipeline on one view dict.

    Returns a state dict with keys:
        image, heading, sky_mask, building_mask, water_mask,
        contour, contour_segs, mask_segs, merged_segs,
        fsky2_segs, fsky5_segs,
        all_projections, matched_segs,
        n_contour, n_mask, n_merged, n_fsky2, n_fsky5, n_matched
    """
    image = cv["image"]
    cap: CapturedView = cv["cap"]
    geo_heading = cv["geo_heading"]

    reg = register_view_to_osm(
        cap, seed_buildings,
        heading_search_deg=8.0, heading_step_deg=1.0,
        forced_center_deg=anchor_offset,
    )

    sky_mask = reg.get("sky_mask")
    contour = reg.get("contour")
    _, bmask = _neural_sky_and_building_masks(image)
    wmask = _neural_water_mask(image)

    contour_segs = detect_building_silhouettes(contour, image)
    mask_segs = detect_buildings_from_mask(bmask, contour=contour, image=image)
    merged_segs = _merge_silhouette_sources(contour_segs, mask_segs)

    all_proj = reg.get("all_projections") or reg.get("projections", [])
    n_matches = int(reg.get("n_matches", 0))

    fsky2_segs = list(merged_segs)
    if n_matches >= 3:
        fsky2_segs = osm_anchor_silhouettes(merged_segs, all_proj, building_mask=bmask)

    fsky5_segs = list(fsky2_segs)
    if enable_fsky5 and n_matches >= 3:
        try:
            fsky5_segs = osm_sam_instance_silhouettes(
                image, fsky2_segs, all_proj, building_mask=bmask)
        except Exception as exc:
            print(f"[F-SKY5] skipped on heading {geo_heading}: {exc}")

    buildings_by_id = {b.feature_id: b for b in seed_buildings}
    matched = match_segments_to_buildings(fsky5_segs, all_proj, buildings_by_id)

    return {
        "image": image,
        "heading": geo_heading,
        "sky_mask": sky_mask,
        "building_mask": bmask,
        "water_mask": wmask,
        "contour": contour,
        "contour_segs": contour_segs,
        "mask_segs": mask_segs,
        "merged_segs": merged_segs,
        "fsky2_segs": fsky2_segs,
        "fsky5_segs": fsky5_segs,
        "all_projections": all_proj,
        "matched_segs": matched,
        "reg": reg,
        "n_contour": len(contour_segs),
        "n_mask": len(mask_segs),
        "n_merged": len(merged_segs),
        "n_fsky2": len(fsky2_segs),
        "n_fsky5": len(fsky5_segs),
        "n_matched": sum(1 for s in matched if s.get("matched_projection")),
    }


# ---------------------------------------------------------------------------
# PDF page renderers
# ---------------------------------------------------------------------------

def _page_title(pdf: PdfPages,
                seed: SkylinePoint,
                pano_result,
                view_states: list[dict]):
    """Page 1 — Title and summary table."""
    fig, ax = plt.subplots(figsize=(PAGE_W, PAGE_H),
                           constrained_layout=True)
    ax.axis("off")

    fig.suptitle(
        f"Cartagena seed_5 — Pipeline Diagnostic\n"
        f"Goal: identify where 10-segment pano limit originates",
        fontsize=18, fontweight="bold", y=0.95,
    )

    # ── Build summary table ──────────────────────────────────────────────────
    pano_segs    = pano_result.n_segments if pano_result else "N/A"
    pano_matched = pano_result.n_matched  if pano_result else "N/A"

    all_per_view_matched = [s for v in view_states for s in v["matched_segs"]
                             if s.get("matched_projection")]
    per_view_segs_total  = sum(v["n_fsky5"] for v in view_states)
    per_view_matched_n   = sum(v["n_matched"] for v in view_states)
    distinct_bids = {s["matched_projection"]["feature_id"]
                     for v in view_states for s in v["matched_segs"]
                     if s.get("matched_projection")}
    n_distinct = len(distinct_bids)

    pano_bids: set = set()
    if pano_result:
        for seg in pano_result.matched_segments:
            m = seg.get("matched_projection")
            if m:
                pano_bids.add(m["feature_id"])
    overlap      = len(pano_bids & distinct_bids)
    pano_only    = len(pano_bids - distinct_bids)
    perv_only    = len(distinct_bids - pano_bids)

    rows = [
        ("Pano segments detected",          str(pano_segs)),
        ("Pano segments matched",           str(pano_matched)),
        ("Per-view segments (total)",       str(per_view_segs_total)),
        ("Per-view matched (total)",        str(per_view_matched_n)),
        ("Distinct buildings (per-view)",   str(n_distinct)),
        ("Overlap (both paths)",            str(overlap)),
        ("Pano-only matches",               str(pano_only)),
        ("Per-view-only matches",           str(perv_only)),
    ]

    table_ax = fig.add_axes([0.25, 0.25, 0.5, 0.55])
    table_ax.axis("off")
    col_labels  = ["Metric", "Value"]
    cell_text   = [[r[0], r[1]] for r in rows]
    tbl = table_ax.table(
        cellText=cell_text,
        colLabels=col_labels,
        cellLoc="left",
        loc="center",
        colWidths=[0.65, 0.35],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(12)
    tbl.scale(1.2, 2.0)
    # Style header
    for j in range(2):
        tbl[(0, j)].set_facecolor("#2a4d8f")
        tbl[(0, j)].set_text_props(color="white", fontweight="bold")
    # Alternate row shading
    for i in range(1, len(rows) + 1):
        fc = "#eef2ff" if i % 2 == 0 else "white"
        for j in range(2):
            tbl[(i, j)].set_facecolor(fc)

    ax.text(0.5, 0.08,
            "Pages: 1=Summary | 2=Raw spin images | 3=SegFormer masks | "
            "4=Contour+mask detectors | 5=F-SKY2 gap split | "
            "6=F-SKY5 SAM split | 7=Per-view matching | "
            "8=Pano vs per-view | 9=Bottleneck table",
            ha="center", va="bottom", transform=ax.transAxes,
            fontsize=9, style="italic", color="#555555")

    pdf.savefig(fig)
    plt.close(fig)


def _page_spin_grid(pdf: PdfPages, prefetch: list[dict]):
    """Page 2 — 3×4 grid of the 12 spin views."""
    fig, axes = plt.subplots(3, 4, figsize=(PAGE_W, PAGE_H),
                             constrained_layout=True)
    fig.suptitle("Spin views — all 12 headings (Street View cache)",
                 fontsize=14, fontweight="bold")

    key_headings_set = set(_PREFERRED_KEY_HEADINGS)

    for ax, entry in zip(axes.flat, prefetch):
        img = entry.get("image")
        gh  = float(entry.get("geo_heading", 0))
        label = entry.get("sv_label", "?")
        is_key = any(abs((gh - kh + 180) % 360 - 180) < 16
                     for kh in key_headings_set)
        if img is not None:
            ax.imshow(img)
        else:
            ax.set_facecolor("#cccccc")
            ax.text(0.5, 0.5, "no image", ha="center", va="center",
                    transform=ax.transAxes, fontsize=10)
        title_color = "red" if is_key else "black"
        ax.set_title(f"{gh:.0f}°  [{label}]",
                     fontsize=9, color=title_color,
                     fontweight="bold" if is_key else "normal")
        ax.axis("off")
        if is_key:
            for spine in ax.spines.values():
                spine.set_edgecolor("red")
                spine.set_linewidth(3)
                spine.set_visible(True)

    # Blank out any unused axes (if prefetch has < 12 entries)
    for ax in axes.flat[len(prefetch):]:
        ax.axis("off")

    fig.text(0.5, 0.01,
             "Red border = key views (~225°, ~270°, ~315°) toward main building cluster",
             ha="center", fontsize=9, color="red")
    pdf.savefig(fig)
    plt.close(fig)


def _page_segformer_masks(pdf: PdfPages, key_view_states: list[dict]):
    """Page 3 — SegFormer masks for the 3 key views."""
    n = len(key_view_states)
    fig, axes = plt.subplots(n, 4, figsize=(PAGE_W, PAGE_H),
                             constrained_layout=True)
    if n == 1:
        axes = [axes]  # ensure 2-D indexing
    fig.suptitle("SegFormer semantic masks — 3 key views",
                 fontsize=14, fontweight="bold")

    for row_i, vs in enumerate(key_view_states):
        img    = vs["image"]
        bmask  = vs.get("building_mask")
        sky    = vs.get("sky_mask")
        wmask  = vs.get("water_mask")
        h_deg  = vs["heading"]

        # Coverage percentage
        if bmask is not None:
            bfrac = float(np.count_nonzero(bmask)) / bmask.size * 100
        else:
            bfrac = float("nan")

        row = axes[row_i]
        row[0].imshow(img); row[0].set_title(f"View {h_deg:.0f}° — raw", fontsize=9)
        row[0].axis("off")

        row[1].imshow(img); _overlay_mask(row[1], bmask, "Blues")
        row[1].set_title(f"Building mask  ({bfrac:.1f}%)", fontsize=9)
        row[1].axis("off")

        row[2].imshow(img)
        if sky is not None:
            # sky_mask from detect_skyline_contour is a row array — convert
            # to a 2-D boolean mask for display
            if sky.ndim == 1 and img is not None:
                _sky2d = np.zeros(img.shape[:2], dtype=bool)
                for col, y_sky in enumerate(sky.astype(int)):
                    if 0 <= col < _sky2d.shape[1]:
                        _sky2d[:max(0, y_sky), col] = True
                _overlay_mask(row[2], _sky2d, "YlOrBr")
            else:
                _overlay_mask(row[2], sky, "YlOrBr")
        row[2].set_title("Sky mask (from contour)", fontsize=9)
        row[2].axis("off")

        row[3].imshow(img); _overlay_mask(row[3], wmask, "cyan")
        row[3].set_title("Water mask", fontsize=9)
        row[3].axis("off")

    pdf.savefig(fig)
    plt.close(fig)


def _page_detectors(pdf: PdfPages, key_view_states: list[dict]):
    """Page 4 — contour detector / mask detector / merged, per key view."""
    n = len(key_view_states)
    fig, axes = plt.subplots(n, 3, figsize=(PAGE_W, PAGE_H),
                             constrained_layout=True)
    if n == 1:
        axes = [axes]
    fig.suptitle("Segment detectors — contour | mask | merged",
                 fontsize=14, fontweight="bold")

    for row_i, vs in enumerate(key_view_states):
        img  = vs["image"]
        h    = vs["heading"]
        row  = axes[row_i]

        # Left: contour detector
        row[0].imshow(img)
        _draw_seg_boxes(row[0], vs["contour_segs"], color="lime", label_prefix="C")
        # Draw skyline contour line
        if vs.get("contour") is not None:
            c = np.asarray(vs["contour"])
            row[0].plot(np.arange(len(c)), c, color="red", linewidth=1.0, alpha=0.8)
        row[0].set_title(
            f"View {h:.0f}° — Contour detector: {vs['n_contour']} segs",
            fontsize=9)
        row[0].axis("off")

        # Middle: mask detector
        row[1].imshow(img)
        _draw_seg_boxes(row[1], vs["mask_segs"], color="deepskyblue", label_prefix="M")
        row[1].set_title(
            f"Mask detector: {vs['n_mask']} segs", fontsize=9)
        row[1].axis("off")

        # Right: after merge
        row[2].imshow(img)
        _draw_seg_boxes(row[2], vs["merged_segs"], color="orange", label_prefix="X")
        row[2].set_title(
            f"After merge (IoU de-dup): {vs['n_merged']} segs", fontsize=9)
        row[2].axis("off")

    pdf.savefig(fig)
    plt.close(fig)


def _page_fsky2(pdf: PdfPages, key_view_states: list[dict]):
    """Page 5 — F-SKY2 gap splitting."""
    n = len(key_view_states)
    fig, axes = plt.subplots(n, 2, figsize=(PAGE_W, PAGE_H),
                             constrained_layout=True)
    if n == 1:
        axes = [axes]
    fig.suptitle("F-SKY2 — gap splitting (OSM-anchored blob split)",
                 fontsize=14, fontweight="bold")

    for row_i, vs in enumerate(key_view_states):
        img      = vs["image"]
        h        = vs["heading"]
        row      = axes[row_i]
        projs    = vs["all_projections"]

        before_segs = vs["merged_segs"]
        after_segs  = vs["fsky2_segs"]

        # Count blobs that contained ≥ 2 OSM markers (candidate for split)
        def _count_multi_marker(segs, projs_list):
            n_multi = 0
            for seg in segs:
                xl, xr = _seg_x_left(seg), _seg_x_right(seg)
                inside = [p for p in projs_list
                          if xl <= float(p.get("x_px", -1)) <= xr]
                if len(inside) >= 2:
                    n_multi += 1
            return n_multi

        n_multi = _count_multi_marker(before_segs, projs)
        n_created = len(after_segs) - len(before_segs)

        # Left: before F-SKY2
        row[0].imshow(img)
        _draw_seg_boxes(row[0], before_segs, color="orange")
        _draw_osm_projections(row[0], projs)
        # Highlight multi-marker blobs in a different colour
        for seg in before_segs:
            xl, xr = _seg_x_left(seg), _seg_x_right(seg)
            inside = [p for p in projs if xl <= float(p.get("x_px", -1)) <= xr]
            if len(inside) >= 2:
                yt = _seg_y_top(seg); yb = _seg_y_bot(seg)
                row[0].add_patch(Rectangle(
                    (xl, yt), xr - xl, yb - yt,
                    linewidth=2.5, edgecolor="orangered",
                    facecolor="none", linestyle="--"))
        row[0].set_title(
            f"View {h:.0f}° — Before F-SKY2: {len(before_segs)} segs "
            f"({n_multi} multi-marker, highlighted)",
            fontsize=9)
        row[0].axis("off")

        # Right: after F-SKY2
        row[1].imshow(img)
        _draw_seg_boxes(row[1], after_segs, color="lime")
        _draw_osm_projections(row[1], projs)
        net_txt = f"+{n_created}" if n_created >= 0 else str(n_created)
        row[1].set_title(
            f"After F-SKY2: {len(after_segs)} segs  "
            f"({n_multi} blobs → {net_txt} children created)",
            fontsize=9)
        row[1].axis("off")

    pdf.savefig(fig)
    plt.close(fig)


def _page_fsky5(pdf: PdfPages, key_view_states: list[dict]):
    """Page 6 — F-SKY5 SAM instance splitting."""
    n = len(key_view_states)
    fig, axes = plt.subplots(n, 2, figsize=(PAGE_W, PAGE_H),
                             constrained_layout=True)
    if n == 1:
        axes = [axes]
    fig.suptitle("F-SKY5 — MobileSAM instance splitting",
                 fontsize=14, fontweight="bold")

    for row_i, vs in enumerate(key_view_states):
        img  = vs["image"]
        h    = vs["heading"]
        row  = axes[row_i]

        before_segs = vs["fsky2_segs"]
        after_segs  = vs["fsky5_segs"]

        n_diff = len(after_segs) - len(before_segs)
        if before_segs is after_segs or n_diff == 0:
            note = "F-SKY5: no qualifying blobs (or disabled)"
        else:
            note = f"F-SKY5 fired: {n_diff:+d} children created"

        # Left: entering F-SKY5
        row[0].imshow(img)
        _draw_seg_boxes(row[0], before_segs, color="darkorange")
        _draw_osm_projections(row[0], vs["all_projections"])
        row[0].set_title(
            f"View {h:.0f}° — Entering F-SKY5: {len(before_segs)} segs",
            fontsize=9)
        row[0].axis("off")

        # Right: after F-SKY5
        row[1].imshow(img)
        _draw_seg_boxes(row[1], after_segs, color="deepskyblue")
        _draw_osm_projections(row[1], vs["all_projections"])
        row[1].set_title(
            f"After F-SKY5: {len(after_segs)} segs — {note}",
            fontsize=9)
        row[1].axis("off")

    pdf.savefig(fig)
    plt.close(fig)


def _page_per_view_matching(pdf: PdfPages, key_view_states: list[dict]):
    """Page 7 — per-view final matching with building IDs."""
    n = len(key_view_states)
    # Each view: image + table below → use (n, 1) grid with extra space for table
    fig = plt.figure(figsize=(PAGE_W, PAGE_H), constrained_layout=True)
    fig.suptitle("Per-view matched segments", fontsize=14, fontweight="bold")
    outer = fig.add_gridspec(1, n, hspace=0.05)

    for col_i, vs in enumerate(key_view_states):
        inner = outer[col_i].subgridspec(2, 1, height_ratios=[3, 1], hspace=0.25)
        ax_img = fig.add_subplot(inner[0])
        ax_tbl = fig.add_subplot(inner[1])

        img      = vs["image"]
        h        = vs["heading"]
        matched  = vs["matched_segs"]
        projs    = vs["all_projections"]
        n_bld    = sum(1 for s in matched if s.get("matched_projection"))

        ax_img.imshow(img)
        _draw_osm_projections(ax_img, projs, color="yellow", lw=0.8)

        for si, seg in enumerate(matched):
            xl = _seg_x_left(seg);   xr = _seg_x_right(seg)
            yt = _seg_y_top(seg);    yb = _seg_y_bot(seg)
            is_matched = bool(seg.get("matched_projection"))
            col = _seg_color(si) if is_matched else (0.8, 0.8, 0.8, 0.7)
            ax_img.add_patch(Rectangle(
                (xl, yt), xr - xl, yb - yt,
                linewidth=2.0, edgecolor=col, facecolor="none"))
            if is_matched:
                fid = seg["matched_projection"]["feature_id"]
                ax_img.text(xl + 2, yt + 2, fid[-4:],
                            color=col, fontsize=6, va="top",
                            bbox=dict(facecolor="black", alpha=0.4, pad=1))

        ax_img.set_title(
            f"View {h:.0f}°: {len(matched)} segs, {n_bld} matched",
            fontsize=9)
        ax_img.axis("off")

        # Table of matched buildings
        tbl_rows = []
        for seg in matched:
            m = seg.get("matched_projection")
            if not m:
                continue
            fid     = m.get("feature_id", "?")
            xl      = int(_seg_x_left(seg));  xr = int(_seg_x_right(seg))
            h_est   = seg.get("predicted_height_m", float("nan"))
            tbl_rows.append([fid[-6:], f"{xl}..{xr}",
                             f"{h_est:.1f}m" if math.isfinite(h_est) else "—"])

        ax_tbl.axis("off")
        if tbl_rows:
            t = ax_tbl.table(
                cellText=tbl_rows,
                colLabels=["building_id", "x_left..x_right", "height_est"],
                cellLoc="center", loc="center",
                colWidths=[0.35, 0.35, 0.30],
            )
            t.auto_set_font_size(False)
            t.set_fontsize(7)
            t.scale(1, 1.4)
        else:
            ax_tbl.text(0.5, 0.5, "no matches", ha="center", va="center",
                        transform=ax_tbl.transAxes, fontsize=9, color="grey")

    pdf.savefig(fig)
    plt.close(fig)


def _page_pano_vs_perv(pdf: PdfPages, pano_result, view_states: list[dict]):
    """Page 8 — pano path vs per-view path comparison."""
    fig = plt.figure(figsize=(PAGE_W, PAGE_H), constrained_layout=True)
    fig.suptitle("Pano path vs per-view path", fontsize=14, fontweight="bold")
    gs = fig.add_gridspec(2, 2, height_ratios=[2.5, 1.5], hspace=0.35)

    ax_pano = fig.add_subplot(gs[0, :])
    ax_bar  = fig.add_subplot(gs[1, :])

    # ── Top: stitched pano ───────────────────────────────────────────────────
    if pano_result is not None:
        pano_img = pano_result.pano_image
        ax_pano.imshow(pano_img)

        # Draw pano-level segment boxes (green)
        for seg in pano_result.matched_segments:
            xl = _seg_x_left(seg);   xr = _seg_x_right(seg)
            yt = _seg_y_top(seg);    yb = _seg_y_bot(seg)
            is_m = bool(seg.get("matched_projection"))
            col = "lime" if is_m else "grey"
            ax_pano.add_patch(Rectangle(
                (xl, yt), max(xr - xl, 1), max(yb - yt, 1),
                linewidth=1.5, edgecolor=col, facecolor="none"))

        # Highlight gap columns — columns where per-view matched but pano didn't.
        # Build per-view matched column ranges
        pano_col_ranges: list[tuple[float, float]] = [
            (_seg_x_left(s), _seg_x_right(s))
            for s in pano_result.matched_segments
            if s.get("matched_projection")
        ]
        per_view_bids = {
            s["matched_projection"]["feature_id"]
            for v in view_states
            for s in v["matched_segs"]
            if s.get("matched_projection")
        }
        pano_bids = {
            s["matched_projection"]["feature_id"]
            for s in pano_result.matched_segments
            if s.get("matched_projection")
        }
        missed_bids = per_view_bids - pano_bids

        # Overlay a translucent red band across the full height for gap columns
        # (rough: use headings_per_col to find column ranges for missed buildings).
        if pano_result.headings_per_col is not None and missed_bids:
            ax_pano.set_title(
                f"Pano: {pano_result.n_segments} segs detected, "
                f"{pano_result.n_matched} matched  |  "
                f"Per-view found {len(missed_bids)} additional buildings (shown red)",
                fontsize=9)
        else:
            ax_pano.set_title(
                f"Pano: {pano_result.n_segments} segs, {pano_result.n_matched} matched",
                fontsize=9)

        ax_pano.axis("off")
    else:
        ax_pano.text(0.5, 0.5, "Pano result not available",
                     ha="center", va="center", transform=ax_pano.transAxes,
                     fontsize=14, color="grey")
        ax_pano.axis("off")

    # ── Bottom: stacked bar chart per view ───────────────────────────────────
    headings   = [v["heading"] for v in view_states]
    n_contour  = [v["n_contour"] for v in view_states]
    n_mask     = [v["n_mask"]    for v in view_states]
    n_merged   = [v["n_merged"]  for v in view_states]
    n_fsky2    = [v["n_fsky2"]   for v in view_states]
    n_fsky5    = [v["n_fsky5"]   for v in view_states]
    n_matched  = [v["n_matched"] for v in view_states]

    x = np.arange(len(headings))
    w = 0.55

    # Each bar group: absolute counts at each stage (not stacked increments)
    ax_bar.bar(x - w*0.36, n_contour, w*0.18, label="contour segs",  color="#4c72b0", alpha=0.85)
    ax_bar.bar(x - w*0.18, n_mask,    w*0.18, label="mask segs",     color="#55a868", alpha=0.85)
    ax_bar.bar(x,          n_merged,  w*0.18, label="after merge",   color="#c44e52", alpha=0.85)
    ax_bar.bar(x + w*0.18, n_fsky2,   w*0.18, label="after F-SKY2",  color="#8172b2", alpha=0.85)
    ax_bar.bar(x + w*0.36, n_matched, w*0.18, label="matched",       color="#937860", alpha=0.85)

    ax_bar.set_xticks(x)
    ax_bar.set_xticklabels([f"{h:.0f}°" for h in headings], fontsize=8)
    ax_bar.set_ylabel("Segment count")
    ax_bar.set_title("Segment counts per view at each pipeline stage", fontsize=10)
    ax_bar.legend(loc="upper right", fontsize=8)
    ax_bar.grid(axis="y", alpha=0.3)

    pdf.savefig(fig)
    plt.close(fig)


def _page_bottleneck_table(pdf: PdfPages, view_states: list[dict],
                           pano_result):
    """Page 9 — bottleneck summary table ranking where buildings are lost."""
    fig, ax = plt.subplots(figsize=(PAGE_W, PAGE_H), constrained_layout=True)
    ax.axis("off")
    fig.suptitle("Bottleneck summary — where segments are lost",
                 fontsize=16, fontweight="bold")

    # Aggregate across all views (totals)
    total_contour  = sum(v["n_contour"] for v in view_states)
    total_mask     = sum(v["n_mask"]    for v in view_states)
    raw_sum        = total_contour + total_mask
    total_merged   = sum(v["n_merged"]  for v in view_states)
    total_fsky2    = sum(v["n_fsky2"]   for v in view_states)
    total_fsky5    = sum(v["n_fsky5"]   for v in view_states)
    total_matched  = sum(v["n_matched"] for v in view_states)

    pano_segs      = pano_result.n_segments if pano_result else "—"
    pano_matched_n = pano_result.n_matched  if pano_result else "—"

    def _lost(a, b):
        try:
            return int(a) - int(b)
        except (TypeError, ValueError):
            return "—"

    rows = [
        # (Step,             Input,      Output,       Lost)
        ("Contour detection",   "—",           str(total_contour), "—"),
        ("Mask detection",      "—",           str(total_mask),    "—"),
        ("After merge (IoU de-dup)", str(raw_sum), str(total_merged),
         str(_lost(raw_sum, total_merged))),
        ("After F-SKY2 (gap split)", str(total_merged), str(total_fsky2),
         str(_lost(total_merged, total_fsky2))),
        ("After F-SKY5 (SAM split)", str(total_fsky2), str(total_fsky5),
         str(_lost(total_fsky2, total_fsky5))),
        ("Matcher (IoU threshold)",  str(total_fsky5),  str(total_matched),
         str(_lost(total_fsky5, total_matched))),
        ("── PANO path ──", "──", "──", "──"),
        ("Pano segs detected",  "—",           str(pano_segs),      "—"),
        ("Pano segs matched",   str(pano_segs), str(pano_matched_n),
         str(_lost(pano_segs, pano_matched_n))),
    ]

    col_labels = ["Step", "Input segments", "Output segments", "Lost"]
    tbl_ax = fig.add_axes([0.10, 0.15, 0.80, 0.70])
    tbl_ax.axis("off")
    tbl = tbl_ax.table(
        cellText=[[r[0], r[1], r[2], r[3]] for r in rows],
        colLabels=col_labels,
        cellLoc="center",
        loc="center",
        colWidths=[0.45, 0.18, 0.18, 0.19],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(11)
    tbl.scale(1.2, 2.2)
    # Header style
    for j in range(4):
        tbl[(0, j)].set_facecolor("#2a4d8f")
        tbl[(0, j)].set_text_props(color="white", fontweight="bold")

    # Find step with largest "Lost"
    numeric_losses: list[tuple[int, int]] = []  # (row_index_in_rows, lost_val)
    for ri, row in enumerate(rows):
        try:
            lv = int(row[3])
            numeric_losses.append((ri, lv))
        except (ValueError, TypeError):
            pass

    primary_row_idx, primary_loss = max(
        numeric_losses, key=lambda x: x[1], default=(-1, 0))

    # Highlight the primary bottleneck row in orange
    if primary_row_idx >= 0:
        for j in range(4):
            tbl[(primary_row_idx + 1, j)].set_facecolor("#ffe0b2")

    primary_name = (rows[primary_row_idx][0]
                    if primary_row_idx >= 0 else "unknown")
    ax.text(
        0.5, 0.06,
        f"Primary bottleneck (largest loss):  \"{primary_name}\"   "
        f"({primary_loss} segments lost)",
        ha="center", va="bottom", transform=ax.transAxes,
        fontsize=12, fontweight="bold",
        bbox=dict(facecolor="#fff3cd", edgecolor="#f0ad4e",
                  boxstyle="round,pad=0.5"),
    )

    pdf.savefig(fig)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_diagnostic(region: str, seed_name: str, out_path: Path) -> None:
    print(f"[diag] region={region}  seed={seed_name}  out={out_path}")

    # ── 1. Bootstrap: load region data (uses disk cache — no new API calls) ──
    api_key = _resolve_api_key(None)   # reads GOOGLE_MAPS_API_KEY env var
    bbox    = _load_region_bbox(region)
    print(f"[diag] bbox loaded: {bbox}")

    osm_data, osm_source = _load_osm_for_region(bbox)
    print(f"[diag] OSM source={osm_source}")

    building_records = _osm_to_building_records(osm_data)
    building_records = _attach_building_terrain(building_records)
    print(f"[diag] {len(building_records)} building records")

    # ── 2. Load seed_5 SkylinePoint ─────────────────────────────────────────
    seed_urls = _load_site_seed_urls(region)
    target_seed: SkylinePoint | None = None
    original_pano_id: str | None = None
    for idx, url in enumerate(seed_urls):
        parsed = _parse_streetview_url(url)
        if parsed is None:
            continue
        lat, lon, heading, fov, pitch, pano_id = parsed
        sname = f"seed_{idx + 1}"
        if sname == seed_name:
            original_pano_id = pano_id
            target_seed = SkylinePoint(
                name=sname, lat=lat, lon=lon, heading=heading,
                source="seed", score=1.0, fov=fov, pitch=pitch,
                pano_id=pano_id,
            )
            break

    if target_seed is None:
        raise ValueError(
            f"Seed '{seed_name}' not found among {len(seed_urls)} seed_urls "
            f"for region '{region}'")

    # Apply seed resolution cache — production pipeline resolves pano_ids to
    # their expanded form (the same form used when the image cache was
    # populated).  Without this step, cache keys won't match and all views
    # return None.
    _res_cache_path = (
        Path(__file__).resolve().parents[1] / "runs" / "seed_resolution_cache.json"
    )
    if _res_cache_path.exists():
        try:
            _res_cache = json.loads(_res_cache_path.read_text(encoding="utf-8"))
            _res_key = (f"{target_seed.name}|{target_seed.lat:.6f}|"
                        f"{target_seed.lon:.6f}|{target_seed.pano_id or ''}")
            if _res_key in _res_cache:
                r = _res_cache[_res_key]
                target_seed = SkylinePoint(
                    name=target_seed.name,
                    lat=float(r["lat"]),
                    lon=float(r["lon"]),
                    heading=target_seed.heading,
                    source=target_seed.source,
                    score=target_seed.score,
                    fov=target_seed.fov,
                    pitch=target_seed.pitch,
                    pano_id=r.get("pano_id") or None,
                )
                print(f"[diag] resolved seed pano_id: {target_seed.pano_id!r}")
        except Exception as exc:
            print(f"[diag] resolution cache read failed: {exc} — using URL pano_id")

    print(f"[diag] seed: lat={target_seed.lat:.5f} lon={target_seed.lon:.5f} "
          f"pano_id={target_seed.pano_id!r}")

    # ── 3. Filter buildings near seed ────────────────────────────────────────
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(target_seed.lat))
    radius_m = 4500.0
    rad_sq   = radius_m * radius_m
    seed_buildings = [
        b for b in building_records
        if (b.centroid_lon - target_seed.lon) ** 2 * mlon ** 2
        + (b.centroid_lat - target_seed.lat) ** 2 * mlat ** 2 <= rad_sq
    ]
    print(f"[diag] {len(seed_buildings)} buildings within {radius_m:.0f}m")

    # ── 4. Capture spin views (uses disk image cache) ────────────────────────
    # is_photosphere is False when the resolved pano_id differs from the URL
    # pano_id (the production pipeline's same logic), so location-based
    # fallback fetches are allowed even though a pano_id is provided.
    is_photosphere = (
        original_pano_id is not None
        and target_seed.pano_id == original_pano_id
    )
    prefetch, effective_pitch, cached_views = _capture_pano_views(
        target_seed, api_key, SPIN_HEADINGS, is_photosphere)
    print(f"[diag] cached_views: {len(cached_views)} passed screening")

    if not cached_views:
        raise RuntimeError("No cached views returned — check image cache.")

    # ── 5. Resolve anchor offset (joint optimization) ────────────────────────
    anchor_overrides = _load_site_anchor_overrides(region)
    anchor_offset    = _recover_anchor_offset(
        target_seed, cached_views, seed_buildings,
        pano_recovered_offset=None,
        pano_recovered_peak=None,
        pano_recovered_sigma=None,
        pano_recovery_state=None,
        anchor_overrides=anchor_overrides,
    )
    print(f"[diag] anchor_offset={anchor_offset:.2f}°")

    # ── 6. Run full per-view pipeline for ALL views ──────────────────────────
    view_states: list[dict] = []
    for cv in cached_views:
        print(f"[diag] processing view heading={cv['geo_heading']:.0f}°")
        vs = _run_per_view_pipeline(
            cv, seed_buildings, anchor_offset, enable_fsky5=False)
        view_states.append(vs)
        print(f"       contour={vs['n_contour']} mask={vs['n_mask']} "
              f"merged={vs['n_merged']} fsky2={vs['n_fsky2']} "
              f"matched={vs['n_matched']}")

    # ── 7. Pick key views ─────────────────────────────────────────────────────
    key_views_cv = _pick_key_views(cached_views, _PREFERRED_KEY_HEADINGS)
    key_headings  = {cv["geo_heading"] for cv in key_views_cv}
    key_view_states = [vs for vs in view_states if vs["heading"] in key_headings]
    # Ensure order matches key_views_cv order
    key_view_states = sorted(
        key_view_states,
        key=lambda vs: next((i for i, cv in enumerate(key_views_cv)
                             if cv["geo_heading"] == vs["heading"]), 99),
    )
    print(f"[diag] key view headings: {[vs['heading'] for vs in key_view_states]}")

    # ── 8. Run pano-path pipeline ─────────────────────────────────────────────
    pano_result = _stitch_pano_composite(
        target_seed, cached_views, seed_buildings, anchor_offset, SPIN_STEP_DEG)
    if pano_result:
        print(f"[diag] pano: segs={pano_result.n_segments} "
              f"matched={pano_result.n_matched}")
    else:
        print("[diag] pano composite failed — page 8 will note this")

    # ── 9. Render PDF ─────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[diag] writing PDF to {out_path}")

    with PdfPages(str(out_path)) as pdf:
        _page_title(pdf, target_seed, pano_result, view_states)
        _page_spin_grid(pdf, prefetch)
        _page_segformer_masks(pdf, key_view_states)
        _page_detectors(pdf, key_view_states)
        _page_fsky2(pdf, key_view_states)
        _page_fsky5(pdf, key_view_states)
        _page_per_view_matching(pdf, key_view_states)
        _page_pano_vs_perv(pdf, pano_result, view_states)
        _page_bottleneck_table(pdf, view_states, pano_result)

    print(f"[diag] done -> {out_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Diagnostic PDF — cartagena seed_5 pipeline step-by-step")
    p.add_argument("--region", default="cartagena",
                   help="Region name (default: cartagena)")
    p.add_argument("--seed", default="seed_5",
                   help="Seed name to diagnose (default: seed_5)")
    p.add_argument("--out", default=None,
                   help="Output PDF path")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    default_out = (
        ROOT / "city2stl" / "skyline" / "runs" /
        "region_reports" / f"{args.region}_seed5_diagnostic.pdf"
    )
    out_path = Path(args.out) if args.out else default_out
    run_diagnostic(region=args.region, seed_name=args.seed, out_path=out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
