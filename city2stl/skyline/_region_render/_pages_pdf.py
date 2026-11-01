"""Part of ``city2stl.skyline._region_render._pages`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from .._core.types import BuildingRecord
from ..region_types import RegionBBox, SeedViewRegistration, SkylinePoint, StitchedPanoResult
from ._draw import _draw_location_map
from ._pages_seed import _render_seed_view_page, _render_stitched_pano_page
from ._pages_tier import _render_heights_page


def _render_pdf(
    out_pdf: Path,
    bbox: RegionBBox,
    osm_source: str,
    osm_data: dict,
    buildings_count: int,
    high_rise_count: int,
    screened: list[dict],
    seed_views: list[SeedViewRegistration],
    building_heights: list[dict],
    building_records: list[BuildingRecord],
    known_heights: list[dict] | None = None,
    pano_results: list[StitchedPanoResult] | None = None,
    pano_only: bool = False,
) -> None:
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out_pdf) as pdf:
        # Page 1: summary
        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle(
            f"Skyline Region Report — {bbox.name}", fontsize=18, fontweight="bold")
        ax = fig.add_subplot(111)
        ax.axis("off")
        n_aerial_views = sum(1 for sv in seed_views if sv.is_aerial)
        lines = [
            f"Region: {bbox.name}",
            f"BBox: N {bbox.north:.5f}  S {bbox.south:.5f}  E {bbox.east:.5f}  W {bbox.west:.5f}",
            f"OSM source: {osm_source}",
            f"Buildings in dataset: {buildings_count}",
            f"High-rise candidates: {high_rise_count}",
            f"StreetView-screened points: {len(screened)}",
            f"Seed multiview registrations: {len(seed_views)}  (aerial/skipped: {n_aerial_views})",
            f"Extracted buildings from seed registrations: {len(building_heights)}",
            "",
            "Coverage screening rule:",
            "- good: skyline score >= 0.30",
            "- medium: 0.15 <= score < 0.30",
            "- weak: score < 0.15",
            "- rejected: failed sky-fraction or contour-range gate",
            "- aerial: elevated camera detected, height estimation skipped",
        ]
        top = [r for r in screened if r["status"] == "OK"][:8]
        lines.append("")
        lines.append("Top screened viewpoints:")
        for row in top:
            p: SkylinePoint = row["point"]
            lines.append(
                f"- {p.name} ({p.source})  lat={p.lat:.6f}, lon={p.lon:.6f}, heading={p.heading:.1f}  "
                f"score={row['screen_score']:.2f}  coverage={row['coverage']}"
            )
        ax.text(0.03, 0.96, "\n".join(lines), va="top",
                ha="left", family="monospace", fontsize=10)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Page 2: map screen
        fig, ax = plt.subplots(figsize=(11, 8.5))
        _draw_location_map(ax, bbox, screened, osm_data)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Point screenshots — consolidated into one (or a few) montage pages.
        # Previously rendered one full-page screenshot per screened location,
        # which inflated the PDF and added ~2 s/page of matplotlib draw time.
        # The same images appear in detail on the seed-view registration
        # pages later, so the screenshots are only useful as a quick at-a-
        # glance "which locations got chosen" overview.
        ok_rows = [r for r in screened
                   if r["status"] == "OK" and r["image"] is not None]
        if ok_rows:
            tiles_per_page = 6  # 2 rows × 3 cols
            for page_start in range(0, len(ok_rows), tiles_per_page):
                page_rows = ok_rows[page_start: page_start + tiles_per_page]
                fig = plt.figure(figsize=(11, 8.5))
                fig.suptitle(
                    f"Screened locations  "
                    f"({page_start + 1}–{page_start + len(page_rows)} of {len(ok_rows)})",
                    fontsize=14,
                )
                for tile_idx, row in enumerate(page_rows):
                    p: SkylinePoint = row["point"]
                    ax = fig.add_subplot(2, 3, tile_idx + 1)
                    ax.imshow(row["image"])
                    ax.axis("off")
                    ax.set_title(
                        f"{p.name}\n"
                        f"{p.source}  cov={row['coverage']}  "
                        f"score={row['screen_score']:.2f}\n"
                        f"hdg={p.heading:.0f}°  lat={p.lat:.4f}  lon={p.lon:.4f}",
                        fontsize=7,
                    )
                fig.tight_layout(rect=[0, 0, 1, 0.95])
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)

        # Seed registration pages: image with segments + matched-footprint mini-map
        # buildings_by_id lets the mini-map draw the actual matched footprint
        # polygons in the segment colours (rather than just bearing rays).
        # Skipped entirely under `pano_only_pdf` — these are the dominant
        # contributor to PDF file size (~26 pages × ~1 MB each on Cartagena),
        # and the per-seed stitched pano page below carries the same info
        # at lower fidelity for compact-orientation reviews.
        buildings_by_id = {b.feature_id: b for b in building_records}
        if not pano_only:
            for sv in seed_views:
                _render_seed_view_page(
                    pdf, sv, osm_data, buildings_by_id=buildings_by_id)

        # Stitched-pano comparison pages — one per seed that produced a
        # stitched result. Lets you compare the 360° pano-level segmentation
        # against the per-view results on the preceding pages. Skip seeds
        # whose stitched pano detected no segments and matched nothing:
        # the page is pure noise (an empty pano image + zero-rows table)
        # and just inflates the PDF page count.
        if pano_results:
            for pr in pano_results:
                if pr.n_segments == 0 and pr.n_matched == 0:
                    continue
                _render_stitched_pano_page(
                    pdf, pr, osm_data, buildings_by_id=buildings_by_id,
                    seed_views=seed_views)

        # Heights page: tier map + counts (F-SKY26 2f); the per-row dump lives in the HTML.
        _render_heights_page(pdf, building_heights, building_records, pano_only=pano_only)

        # Residuals page: predicted vs OSM-tagged height
        tagged_by_id = {
            b.feature_id: b for b in building_records if b.height_tag_m is not None
        }
        pairs: list[tuple[str, float, float, int, int, float]] = []
        for row in building_heights:
            tag = tagged_by_id.get(row["feature_id"])
            if tag is None:
                continue
            pairs.append(
                (
                    row["name"],
                    float(tag.height_tag_m or 0.0),
                    float(row["weighted_height_m"]),
                    int(row.get("n_seeds", 0)),
                    int(row.get("n_views", 0)),
                    float(row.get("mean_confidence", 0.0)),
                )
            )

        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle("Validation: Predicted vs OSM-tagged Heights",
                     fontsize=16, fontweight="bold")
        if pairs:
            tagged_h = np.array([p[1] for p in pairs], dtype=np.float32)
            pred_h = np.array([p[2] for p in pairs], dtype=np.float32)
            n_seeds_arr = np.array([p[3] for p in pairs], dtype=np.int32)
            resid = pred_h - tagged_h
            mae = float(np.mean(np.abs(resid)))
            rmse = float(np.sqrt(np.mean(resid * resid)))
            bias = float(np.median(resid))

            ax_scatter = fig.add_axes([0.08, 0.42, 0.55, 0.45])
            colors = np.where(n_seeds_arr >= 2, "tab:blue", "tab:orange")
            ax_scatter.scatter(tagged_h, pred_h, c=colors, alpha=0.7, s=24)
            lim = float(max(tagged_h.max(), pred_h.max()) * 1.05)
            ax_scatter.plot([0, lim], [0, lim], "k--",
                            linewidth=0.8, alpha=0.5)
            ax_scatter.set_xlabel("OSM-tagged height (m)")
            ax_scatter.set_ylabel("Predicted weighted height (m)")
            ax_scatter.set_xlim(0, lim)
            ax_scatter.set_ylim(0, lim)
            ax_scatter.set_title(
                f"n={len(pairs)} tagged buildings (blue=cross-seed, orange=single-seed)")
            ax_scatter.grid(alpha=0.25)

            ax_hist = fig.add_axes([0.70, 0.42, 0.25, 0.45])
            ax_hist.hist(resid, bins=20, color="tab:gray", edgecolor="black")
            ax_hist.axvline(0.0, color="black", linewidth=0.8)
            ax_hist.set_title("Residual (pred − tag)")
            ax_hist.set_xlabel("m")

            cs_mask = n_seeds_arr >= 2
            ss_mask = ~cs_mask

            def _stats(mask):
                if not mask.any():
                    return None
                r = resid[mask]
                return (
                    float(np.mean(np.abs(r))),
                    float(np.sqrt(np.mean(r * r))),
                    float(np.median(r)),
                    int(mask.sum()),
                )

            cs = _stats(cs_mask)
            ss = _stats(ss_mask)
            stats_lines = [
                f"All           n={len(pairs):3d}  MAE={mae:6.2f}m  RMSE={rmse:6.2f}m  bias={bias:+6.2f}m",
            ]
            if cs is not None:
                stats_lines.append(
                    f"Cross-seed    n={cs[3]:3d}  MAE={cs[0]:6.2f}m  RMSE={cs[1]:6.2f}m  bias={cs[2]:+6.2f}m   <-- trust this"
                )
            if ss is not None:
                stats_lines.append(
                    f"Single-seed   n={ss[3]:3d}  MAE={ss[0]:6.2f}m  RMSE={ss[1]:6.2f}m  bias={ss[2]:+6.2f}m   (unreliable)"
                )
            if not pano_only:
                # Per-building "worst residuals" listing — table content
                # belongs in the HTML report, not the orientation PDF.
                stats_lines += ["", "Worst residuals:"]
                order = np.argsort(-np.abs(resid))[:10]
                for i in order:
                    name, t, p, ns, nv, _ = pairs[int(i)]
                    stats_lines.append(
                        f"  {name[:24]:<24}  tag={t:5.1f}  pred={p:5.1f}  Δ={p-t:+5.1f}  seeds={ns}"
                    )
            fig.text(
                0.08,
                0.34,
                "\n".join(stats_lines),
                family="monospace",
                fontsize=9,
                va="top",
            )
        else:
            fig.text(
                0.5,
                0.5,
                "No buildings with both OSM-tagged height and a prediction.",
                ha="center",
                va="center",
                fontsize=12,
            )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── Diagnostic: matched-segment width histogram ──────────────────────
        # A bimodal distribution of matched-segment widths is the headline
        # signal that SegFormer is over-merging adjacent towers into one
        # silhouette (one wide segment that wins a match instead of N
        # narrow segments matching N OSM polygons). The per-seed bar to the
        # right shows matched count vs OSM markers projected into the FOV
        # cone — a seed leaving many markers unmatched is a candidate for
        # an `anchor_offsets_deg` override or extra Voronoi splitting.
        widths_px: list[float] = []
        ms_widths_px: list[float] = []
        per_seed_matched: dict[str, int] = {}
        per_seed_in_cone: dict[str, int] = {}
        for sv in seed_views:
            if getattr(sv, "is_negative", False) or sv.is_aerial:
                continue
            seed = sv.seed_name
            per_seed_matched.setdefault(seed, 0)
            per_seed_in_cone.setdefault(seed, 0)
            for seg in sv.matched_segments:
                m = seg.get("matched_projection")
                if m is None:
                    continue
                try:
                    w = float(seg.get("x_right", 0.0)) - float(seg.get("x_left", 0.0))
                except Exception:
                    w = 0.0
                if w > 0:
                    widths_px.append(w)
                    if str(m.get("feature_id", "")).startswith("ms_"):
                        ms_widths_px.append(w)
                per_seed_matched[seed] += 1
            # Markers projected into the FOV cone — match_diagnostics holds
            # every candidate the matcher considered; that's the OSM/MS pool
            # for this view. Use a set of feature_ids to avoid double-counting
            # the same building across segments.
            in_cone_ids: set[str] = set()
            for seg in sv.matched_segments:
                for d in seg.get("match_diagnostics", []) or []:
                    fid = d.get("feature_id")
                    if fid:
                        in_cone_ids.add(str(fid))
                m = seg.get("matched_projection")
                if m and m.get("feature_id"):
                    in_cone_ids.add(str(m["feature_id"]))
            per_seed_in_cone[seed] = max(per_seed_in_cone[seed], len(in_cone_ids))

        if widths_px:
            fig = plt.figure(figsize=(11, 8.5))
            fig.suptitle(
                "Matcher Coverage Diagnostic — segment widths + per-seed yield",
                fontsize=14, fontweight="bold",
            )

            ax_h = fig.add_axes([0.07, 0.12, 0.55, 0.72])
            arr = np.asarray(widths_px, dtype=np.float32)
            ax_h.hist(arr, bins=24, color=(0.45, 0.55, 0.75, 0.85),
                      edgecolor="black", label="OSM-matched")
            if ms_widths_px:
                ax_h.hist(np.asarray(ms_widths_px, dtype=np.float32),
                          bins=24, color=(0.85, 0.65, 0.20, 0.6),
                          edgecolor="black", label="ms_buildings-matched")
            p50 = float(np.median(arr))
            p75 = float(np.percentile(arr, 75))
            over_thr = 2.0 * p75
            ax_h.axvline(p50, color="green", linestyle="--",
                         linewidth=1.0, label=f"median={p50:.0f}px")
            ax_h.axvline(p75, color="orange", linestyle=":",
                         linewidth=1.0, label=f"p75={p75:.0f}px")
            ax_h.axvline(over_thr, color="red", linestyle="--",
                         linewidth=1.0, label=f"over-merge ≥{over_thr:.0f}px")
            ax_h.set_xlabel("Matched segment width (px)")
            ax_h.set_ylabel("Count")
            ax_h.set_title(
                f"n={arr.size} matched segments  "
                f"({int((arr >= over_thr).sum())} suspected over-merges)",
                fontsize=10,
            )
            ax_h.legend(fontsize=8, loc="upper right")
            ax_h.grid(alpha=0.25)

            ax_b = fig.add_axes([0.68, 0.12, 0.28, 0.72])
            seeds = sorted(per_seed_matched.keys())
            x = np.arange(len(seeds))
            matched_vals = [per_seed_matched[s] for s in seeds]
            cone_vals = [per_seed_in_cone[s] for s in seeds]
            bar_w = 0.4
            ax_b.bar(x - bar_w / 2, cone_vals, width=bar_w,
                     color=(0.65, 0.65, 0.65, 0.85),
                     edgecolor="black", label="in FOV cone")
            ax_b.bar(x + bar_w / 2, matched_vals, width=bar_w,
                     color=(0.30, 0.50, 0.75, 0.95),
                     edgecolor="black", label="matched")
            ax_b.set_xticks(x)
            ax_b.set_xticklabels(seeds, rotation=45, ha="right", fontsize=8)
            ax_b.set_ylabel("Count")
            ax_b.set_title("Per-seed matcher yield", fontsize=10)
            ax_b.legend(fontsize=8)
            ax_b.grid(axis="y", alpha=0.25)

            fig.text(
                0.07, 0.04,
                "Read: a bimodal width histogram = SegFormer over-merging adjacent towers "
                "(one wide segment that wins one match instead of N narrow segments matching "
                "N buildings). A low matched/in-cone ratio for a seed flags that seed as a "
                "candidate for an anchor_offsets_deg override or extra Voronoi splitting.",
                fontsize=8, color="0.30", wrap=True,
            )
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

        # ── CTBUH / ground-truth validation page ─────────────────────────────
        # Only rendered when the sites/<region>.json contains known_heights_m.
        if known_heights:
            brec_by_id = {b.feature_id: b for b in building_records}
            pred_by_id = {
                row["feature_id"]: float(row["weighted_height_m"])
                for row in building_heights
            }

            fig = plt.figure(figsize=(11, 8.5))
            fig.suptitle(
                "Ground-Truth Validation — Surveyed Building Heights",
                fontsize=16, fontweight="bold",
            )

            matched = [
                kh for kh in known_heights if kh["matched_id"] is not None]
            unmatched = [
                kh for kh in known_heights if kh["matched_id"] is None]

            hdr = (
                f"{'Building':<32} {'Survey m':>9} {'Floors':>7} "
                f"{'OSM tag':>8} {'Pred m':>8} {chr(0x394)+' m':>8} {'Dist m':>8}"
            )
            sep = "-" * 86
            lines = [
                f"Known heights loaded: {len(known_heights)}  "
                f"(OSM-matched: {len(matched)}, unmatched: {len(unmatched)})",
                "",
                hdr,
                sep,
            ]
            # (ctbuh_m, pred_m, name)
            pairs: list[tuple[float, float, str]] = []
            for kh in known_heights:
                fid = kh["matched_id"]
                ctbuh_m = kh["ctbuh_m"]
                floors_s = str(kh["floors"]) if kh["floors"] else "-"
                dist_m = kh["matched_dist_m"]
                osm_h_s = "-"
                pred_s = "-"
                delta_s = "-"
                if fid is not None:
                    brec = brec_by_id.get(fid)
                    if brec and brec.height_tag_m is not None:
                        osm_h_s = f"{brec.height_tag_m:.1f}"
                    if fid in pred_by_id:
                        pred_m = pred_by_id[fid]
                        pred_s = f"{pred_m:.1f}"
                        delta_s = f"{pred_m - ctbuh_m:+.1f}"
                        pairs.append((ctbuh_m, pred_m, kh["name"]))
                no_match = "  ⚠ no OSM footprint" if fid is None else ""
                lines.append(
                    f"{kh['name'][:32]:<32} {ctbuh_m:>9.1f} {floors_s:>7} "
                    f"{osm_h_s:>8} {pred_s:>8} {delta_s:>8} {dist_m:>8.0f}{no_match}"
                )

            if not pano_only:
                # Per-building survey-vs-pred table — dropped under
                # pano_only since the same info is rendered as a sortable
                # HTML table by html_report.py.
                ax_t = fig.add_axes([0.03, 0.40, 0.94, 0.52])
                ax_t.axis("off")
                ax_t.text(
                    0.0, 1.0, "\n".join(lines),
                    va="top", ha="left", family="monospace", fontsize=8.5,
                )

            if len(pairs) >= 2:
                ctbuh_arr = np.array([p[0] for p in pairs])
                pred_arr = np.array([p[1] for p in pairs])
                mae = float(np.mean(np.abs(pred_arr - ctbuh_arr)))
                rmse = float(np.sqrt(np.mean((pred_arr - ctbuh_arr) ** 2)))
                bias = float(np.median(pred_arr - ctbuh_arr))

                ax_s = fig.add_axes([0.06, 0.05, 0.46, 0.32])
                ax_s.scatter(ctbuh_arr, pred_arr, c="tab:blue",
                             s=60, alpha=0.85, zorder=3)
                lim = float(max(ctbuh_arr.max(), pred_arr.max()) * 1.10)
                ax_s.plot([0, lim], [0, lim], "k--", linewidth=0.8, alpha=0.5)
                for (ct, pr, bname) in pairs:
                    ax_s.annotate(
                        bname[:14], (ct, pr), fontsize=7,
                        xytext=(3, 3), textcoords="offset points",
                    )
                ax_s.set_xlabel("Surveyed height (m)")
                ax_s.set_ylabel("Predicted height (m)")
                ax_s.set_xlim(0, lim)
                ax_s.set_ylim(0, lim)
                ax_s.set_title(f"Surveyed vs Predicted (n={len(pairs)})")
                ax_s.grid(alpha=0.25)

                ax_m = fig.add_axes([0.58, 0.05, 0.38, 0.32])
                ax_m.axis("off")
                ax_m.text(
                    0.02, 0.95,
                    f"n = {len(pairs)}\n"
                    f"MAE  = {mae:.1f} m\n"
                    f"RMSE = {rmse:.1f} m\n"
                    f"bias = {bias:+.1f} m\n\n"
                    "Source: surveyed / CTBUH verified heights\n"
                    "Unmatched = building not found in OSM\n"
                    "Pred = pipeline weighted estimate",
                    va="top", ha="left", family="monospace", fontsize=10,
                    transform=ax_m.transAxes,
                )
            else:
                fig.text(
                    0.5, 0.22,
                    "No predictions yet for surveyed buildings.\n"
                    "Run with seed URLs from good vantage points to populate.",
                    ha="center", va="center", fontsize=12,
                )

            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

    # Post-process: stamp "Page X / N" on every page. Done after PdfPages
    # closes the file so the total count is exact. PyMuPDF is the only
    # cleanly-available editor in this stack; falls back silently if not
    # installed.
    try:
        import fitz  # noqa: PLC0415
    except Exception:
        return
    try:
        doc = fitz.open(out_pdf)
        total = doc.page_count
        for i, page in enumerate(doc, start=1):
            rect = page.rect
            text = f"Page {i} / {total}"
            # Bottom-right corner, small monospace, with a thin white
            # background box so it's legible over any underlying plot.
            page.insert_text(
                fitz.Point(rect.width - 80, rect.height - 12),
                text,
                fontsize=8, fontname="helv",
                color=(0.25, 0.25, 0.25),
                render_mode=0,
            )
        doc.saveIncr()
        doc.close()
    except Exception:
        # Page numbering is cosmetic; never let it break the report.
        pass
