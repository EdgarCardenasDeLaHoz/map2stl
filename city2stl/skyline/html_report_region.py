"""Part of ``city2stl.skyline.html_report`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .region_types import SeedViewRegistration
# PNG / plot renderers live in the _report_plots/ subpackage (F-CLEAN14,
# 2026-06-07); the HTML-assembly functions below call them unchanged.
from ._report_plots._pano_plots import (  # noqa: E402
    _draw_pano_bboxes_inplace,
    _draw_pano_north_line_inplace,
    _render_pano_bearing_scan_png,
    _render_pano_depth_png,
    _render_pano_heights_polar_png,
    _render_pano_minimap_polar_png,
    _render_pano_reconstruction_png,
    _render_pano_segformer_overlay_png,
)
from ._report_plots._plot_utils import (  # noqa: E402
    _fmt_optional_float,
)
from ._report_plots._view_plots import (  # noqa: E402
    _render_screening_map_png,
    _render_seed_minimap_png,
    _render_view_depth_png,
    _render_view_mask_png,
    _render_view_reconstruction_png,
    _save_view_image_png,
)
from .html_report_seed import render_seed_page, render_seed_pano_page
from .html_report_tables import (
    _heights_table_html,
    _is_footprint_first,
    _mismatch_cause,
    _seed_reading_stats,
    _tags_by_id,
    _tier_summary_html,
    _towers_table_html,
)
from .report_render import (  # noqa: E402
    THEME_CSS,
    body_h1_css,
    page,
)
from .tier_display import (  # noqa: E402
    drone_disagreements,
    drone_seeds,
    with_unmeasured_tags,
)

logger = logging.getLogger('city2stl.skyline.html_report')


def render_region_index(
    region_name: str,
    seed_views: list,
    building_heights: list[dict] | None = None,
    *,
    step_timings: list[tuple[str, float]] | None = None,
    screening_map_rel: str | None = None,
    pano_results: list | None = None,
    building_records=None,
    tier_counts_stored: dict | None = None,
    known_heights: list[dict] | None = None,
    tier_map_rel: str | None = None,
    elevated_seeds=(),
) -> str:
    """Render the per-region index.html.

    Lists every seed with its summary stats and links to the per-seed
    page. The building_heights aggregate (when provided) feeds the
    bottom-of-page totals. ``step_timings`` (label, seconds) renders a
    pipeline-timing table so contributors can see where a run spent its
    time.

    The heights section shows the verification tiers (F-SKY26 2f): counts, a table with the
    tier per building (methods on hover), "unverified" on tag/single/prior rows, a note where
    a single reading was withheld for the prior, and survey licence lines. ``building_records``
    adds the OSM-tagged footprints no seed measured, as ``heights.json`` does;
    ``tier_counts_stored``: a ``heights.json``'s own ``tier_counts`` (re-rendering from one).

    Layout (coordinator review, 2026-10-08): the answer first (published towers against their
    reference heights, tier counts and the tier map at ``tier_map_rel``), then the seeds and
    the full heights table; pipeline timing last, collapsed. ``known_heights``: the
    ``_load_known_heights`` list. ``elevated_seeds``: drone seed names (also found from
    ``tier_methods`` and from footprint-first pano results).
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
        unpacked = [_unpack(r) for r in step_timings]

        # Per-step input → output descriptions (matched by substring of
        # the step label, most-specific first). Lets the table explain
        # WHAT each phase consumes and produces, not just how long it took.
        _STEP_IO = [
            ("OSM fetch", "region bbox", "OSM buildings + waterways"),
            ("Drop buildings in water", "OSM buildings + water polys",
             "land-only buildings"),
            ("cross-view satellite", "region bbox", "satellite raster"),
            ("pano-recovery precompute", "OSM coastline",
             "recovery keypoints"),
            ("Screen locations", "candidate seed points",
             "screened seeds + coverage"),
            ("Auto-replace bad seeds", "seeds + screening",
             "cleaned seed list"),
            ("Multiview registration", "seeds + buildings",
             "per-seed registrations + pano results"),
            ("capture pano views", "seed lat/lon", "12 spin-view images"),
            ("SegFormer prefetch", "spin-view images",
             "per-view sky/building/water masks"),
            ("recover pano heading", "pano masks + OSM coastline",
             "heading offset"),
            ("recover anchor offset", "masks + OSM projections",
             "anchor offset (deg)"),
            ("register views + heights", "views + buildings",
             "per-building height estimates"),
            ("register_view_to_osm", "view mask + OSM projections",
             "matched segments"),
            ("estimate_heights_from_registration", "matched segments",
             "per-building heights"),
            ("augment_estimates_with_depth", "estimates + depth",
             "depth-checked heights"),
            ("pano detection", "spin views + masks",
             "pano + tower↔OSM matches (stitch+depth+split+match)"),
            ("project OSM buildings", "OSM buildings + per-col headings",
             "per-column building projections"),
            ("match pano segments", "tower segments + projections",
             "matched tower↔building pairs"),
            ("pano depth", "stitched pano RGB (building band)",
             "depth map [0,1]"),
            ("pano splitter", "pano building mask + OSM projections",
             "per-tower segments"),
            ("OSM-anchored split", "merged segments + projections",
             "per-building split segments"),
            ("bearing recovery", "depth silhouette + OSM distances",
             "corrected per-column headings"),
            ("match_segments_to_buildings", "segments + OSM projections",
             "matched tower↔building pairs"),
            ("anchor coarse sweep", "masks + OSM", "coarse offset"),
            ("anchor fine sweep", "masks + OSM", "refined offset"),
            ("sweep_pano_heading_offset", "pano mask + coastline",
             "heading score curve"),
            ("stitch pano RGB", "spin-view images", "360° pano image"),
            ("stitch pano masks", "per-view masks", "360° mask strips"),
            ("Render HTML", "all results", "HTML report"),
        ]

        def _io_for(label: str) -> tuple[str, str]:
            for key, inp, outp in _STEP_IO:
                if key.lower() in label.lower():
                    return inp, outp
            return "—", "—"

        def _pct_bg(pct: float) -> str:
            # Colour the % cell by magnitude: light → strong red as the
            # share grows (alpha ∝ pct, capped). High-cost steps pop.
            a = max(0.06, min(0.85, pct / 100.0))
            return f"background: rgba(214,40,40,{a:.2f});"

        # Drop steps below 5% of total — the table keeps only the
        # sections that actually matter (child rows are always ≤ their
        # parent, so a <5% parent's children are dropped too, keeping the
        # indent structure consistent).
        MIN_PCT = 5.0
        rows = []
        for label, dt, level in unpacked:
            pct = (dt / total * 100.0) if total > 0 else 0.0
            if pct < MIN_PCT:
                continue
            indent = ("&nbsp;&nbsp;&nbsp;&nbsp;" * level + "↳ ") if level else ""
            row_cls = f' class="lvl{level}"' if level else ""
            inp, outp = _io_for(label)
            rows.append(
                f"      <tr{row_cls}><td>{indent}{html.escape(label)}</td>"
                f"<td class='small'>{html.escape(inp)}</td>"
                f"<td class='small'>{html.escape(outp)}</td>"
                f"<td>{dt:.2f} s</td>"
                f"<td style=\"{_pct_bg(pct)}\">{pct:.1f}%</td></tr>"
            )

        # Time-allocation chart uses LEAF steps — the deepest work units,
        # not the parent phases that merely contain them. A level-0 chart
        # is useless here (one "Multiview ≈ 96%" bar swallows everything);
        # the leaves show WHERE the wall-clock actually goes (SegFormer
        # prefetch, heading sweeps, pano depth, matching, …). A step is a
        # leaf when the next row's level is not deeper than its own.
        leaves: list[tuple[str, float]] = []
        for i, (label, dt, level) in enumerate(unpacked):
            nxt_level = unpacked[i + 1][2] if i + 1 < len(unpacked) else level
            if nxt_level <= level:  # no deeper child follows → leaf
                leaves.append((label, dt))
        leaves.sort(key=lambda t: t[1], reverse=True)
        TOP_N = 12
        shown = leaves[:TOP_N]
        other = sum(dt for _, dt in leaves[TOP_N:])
        if other > 0:
            shown.append((f"other ({len(leaves) - TOP_N} steps)", other))
        chart_max = max((dt for _, dt in shown), default=1.0) or 1.0

        # CSS-only horizontal bars; width ∝ this step's share of the
        # LARGEST leaf (so the longest bar fills the column), labelled
        # with absolute time + % of the wall-clock total.
        _bar_palette = [
            "#0a3070", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd",
            "#d62728", "#17becf", "#8c564b", "#bcbd22", "#e377c2",
        ]
        bar_rows = []
        for i, (label, dt) in enumerate(shown):
            pct = (dt / total * 100.0) if total > 0 else 0.0
            color = "#aaaaaa" if label.startswith("other (") else \
                _bar_palette[i % len(_bar_palette)]
            w = max(1.0, dt / chart_max * 100.0)
            bar_rows.append(
                f'      <div class="talloc-row">'
                f'<span class="talloc-label" title="{html.escape(label)}">'
                f'{html.escape(label)}</span>'
                f'<span class="talloc-track">'
                f'<span class="talloc-bar" style="width:{w:.2f}%;'
                f'background:{color};"></span></span>'
                f'<span class="talloc-num">{dt:.0f}s · {pct:.0f}%</span>'
                f'</div>'
            )

        # CHART ABOVE the table (stacked, both full-width). The chart
        # answers "where does the wall-clock actually go?" (leaf work
        # units); the table below shows the full call HIERARCHY with each
        # step's input/output.
        timings_html = f"""
<details class="timing">
<summary>Pipeline timing and call hierarchy ({total:.0f} s)</summary>
<h3 class="talloc-title">Where the time goes — top {TOP_N} leaf work steps
   (of {total:.0f}s total)</h3>
<div class="talloc">
{chr(10).join(bar_rows)}
</div>
<p class="small">Each bar is one <b>leaf</b> step (an actual unit of work,
   not a containing phase), longest first. Bar length is relative to the
   largest step; the number is absolute time &middot; share of the
   {total:.0f}s wall-clock total.</p>

<h3 class="talloc-title">Call hierarchy</h3>
<table class="timings">
  <thead><tr><th>step</th><th>input</th><th>output</th>
    <th>duration</th><th>% of total</th></tr></thead>
  <tbody>
{chr(10).join(rows)}
      <tr class="ttotal"><th>total (level-0 steps)</th><th></th><th></th>
        <th>{total:.2f} s</th><th>100.0%</th></tr>
  </tbody>
</table>
<p class="small">Indentation shows nesting: a <code>&#8627;</code> row is a
   sub-step of the nearest less-indented row above it, summed across all
   seeds. Every step's "% of total" shares one denominator (the
   {total:.0f}s wall-clock), so a child's % is directly comparable to its
   parent's — that's how to spot which inner operation dominates a phase.
   Only steps &ge; 5% are listed; the % cell is shaded by magnitude. The
   HTML render itself runs after this table is built and isn't included.</p>
</details>
"""

    # Aggregated building heights (headline output, ported from the PDF's
    # "Seed-Derived Building Heights" page). Each row is one OSM building with
    # its cross-seed median/weighted height and a disagreement metric — the
    # key "are matches pointing at the same building?" signal.
    heights_html = ""
    headline_html = ""
    all_rows = with_unmeasured_tags(building_heights or [], building_records or ())
    drone_set = drone_seeds(all_rows, elevated_seeds)
    drone_set |= {pr.seed_name for pr in (pano_results or ()) if _is_footprint_first(pr)}
    if building_heights:
        measured = [r for r in building_heights if r.get("measured", True) is not False]
        cross = [r for r in measured if r.get("n_seeds", 1) >= 2]
        single = [r for r in measured if r.get("n_seeds", 1) < 2]
        disagreements = sorted(
            float(r.get("seed_disagreement_m", 0.0)) for r in cross)
        drone_dis = drone_disagreements(measured, drone_set)

        def _pctile(vals, p):
            if not vals:
                return None
            k = (len(vals) - 1) * (p / 100.0)
            lo = int(k)
            hi = min(lo + 1, len(vals) - 1)
            return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)

        def _dis_line(label, vals, what):
            if not vals:
                return f"{label}: no building {what}."
            return (f"{label}: median {_pctile(vals, 50):.1f} m · p90 {_pctile(vals, 90):.1f} m"
                    f" · max {vals[-1]:.1f} m over {len(vals)} buildings {what}.")

        agree_lines = [
            _dis_line("Drone seeds only (trusted readings that reached the published value)",
                      drone_dis, "read by ≥2 drone seeds"),
            _dis_line("All seeds, street level and drone mixed", disagreements,
                      "seen from ≥2 seeds"),
        ]
        headline_html = f"""
<h2>Answer: published heights</h2>
{_towers_table_html(known_heights or [], all_rows, drone_set)}
<div class="headline">
  <div class="headline-text">
{_tier_summary_html(all_rows, tier_counts_stored)}
  </div>
  {f'<a href="{html.escape(tier_map_rel)}"><img class="tiermap" src="{html.escape(tier_map_rel)}" alt="tier map"></a>' if tier_map_rel else ''}
</div>
"""
        heights_html = f"""
<h2>Building heights</h2>
<p>{len(measured)} buildings seen by a seed — {len(cross)} cross-seed (≥2 seeds), {len(single)} single-seed.
   Cross-seed spread (a diagnostic only: it does not track error, so it is not an accuracy or an error bar.
   Judge a height by its view count and photo anchor deviation in the table below):</p>
<ul class="small">{''.join(f"<li>{html.escape(x)}</li>" for x in agree_lines)}</ul>
<details>
<summary>All {len(all_rows)} published heights, by tier</summary>
{_heights_table_html(all_rows)}
</details>
"""

    # One row per UNIQUE seed name. seed_views contains one row per (seed, view),
    # so we dedupe by seed_name and pick the first row for the summary stats.
    def _neg_cell(sv) -> str:
        # Bad/negative marker: a coverage-rejected pano shows WHY in red;
        # a config-declared negative shows "yes"; otherwise "no".
        if not getattr(sv, "is_negative", False):
            return "no"
        reason = getattr(sv, "negative_reason", None)
        if reason:
            return (f'<span style="color:#d62728;font-weight:600">BAD</span> '
                    f'<span class="small">({html.escape(reason)})</span>')
        return "yes"

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
            f"<td>{_neg_cell(sv)}</td>"
            f"</tr>"
        )

    n_buildings = len(building_heights) if building_heights else 0
    n_seeds = len(seen)

    # Per-pano summary: heading correction, matches, coverage, quality.
    pano_summary_html = ""
    if pano_results:
        srows = []
        tags = _tags_by_id(all_rows, building_records or ())
        for pr in pano_results:
            nm = int(getattr(pr, "n_matched", 0) or 0)
            nseg = int(getattr(pr, "n_segments", 0) or 0)
            shift = float(getattr(pr, "bearing_shift_deg", 0.0) or 0.0)
            # Distinct OSM buildings covered (unique matched feature_ids) —
            # an honest "coverage" count. n_buildings_in_view is ALL
            # projected buildings in radius (~thousands), so a ratio to it
            # is meaningless; the absolute distinct-building count is the
            # useful signal.
            fids = set()
            for s in (getattr(pr, "matched_segments", None) or []):
                m = s.get("matched_projection") if isinstance(s, dict) else None
                if m and m.get("feature_id"):
                    fids.add(str(m["feature_id"]))
            ncov = len(fids)
            mrate = (nm / nseg * 100.0) if nseg else 0.0   # precision
            # Quality from precision AND absolute coverage AND minimum
            # segment count.  A pano with 3 detected + 3 matched = 100%
            # match-rate is not "good" — the percentage is meaningless
            # when nseg < 10.  Require nseg >= 10 for "good" so the label
            # reflects statistical weight, not just ratio.
            if mrate >= 65 and ncov >= 15 and nseg >= 10:
                qlabel, qbg = "good", "rgba(46,160,67,0.30)"
            elif mrate >= 50 and ncov >= 5 and nseg >= 4:
                qlabel, qbg = "medium", "rgba(230,180,40,0.30)"
            elif nseg < 10:
                # Type 1: camera not facing a skyline — F-DET1 should have
                # already caught these, but keep the sub-label for any that
                # slip through (e.g. curated seeds with an explicit override).
                qlabel, qbg = "weak — no detection", "rgba(214,40,40,0.25)"
            elif mrate < 40:
                # Type 2: buildings visible but most don't match OSM —
                # heading drift or OSM coverage gaps.
                qlabel, qbg = "weak — mismatch", "rgba(200,60,20,0.28)"
            else:
                # Type 3: moderate detection but low matched-building coverage.
                qlabel, qbg = "weak — low coverage", "rgba(200,130,20,0.28)"
            # Warn when a high rate is computed from a tiny sample —
            # the percentage is real but not statistically meaningful.
            if nseg < 10 and mrate >= 65:
                qlabel += " ⚠"
            used, tagged, within = _seed_reading_stats(pr.seed_name, all_rows, tags)
            tag_cell = (f"{within} of {tagged} ({100.0 * within / tagged:.0f}%)"
                        if tagged else "<span class='small'>none tagged</span>")
            drone = _is_footprint_first(pr)
            rate_cell = f"{mrate:.0f}%"
            if drone:
                # footprint-first: one segment per measured footprint, so detected == matched
                # and a match rate says nothing; judge it by the tagged buildings instead
                rate_cell = ("<span class='small' title='footprint-first: every reading is a "
                             "matched footprint'>n/a</span>")
                share = within / tagged if tagged else None
                if share is None or tagged < 3:
                    qlabel, qbg = "drone — too few tags", "rgba(160,160,160,0.25)"
                elif share >= 0.8:
                    qlabel, qbg = "drone — good", "rgba(46,160,67,0.30)"
                elif share >= 0.6:
                    qlabel, qbg = "drone — medium", "rgba(230,180,40,0.30)"
                else:
                    qlabel, qbg = "drone — weak", "rgba(214,40,40,0.25)"
            corr = (f"{shift:+.0f}&deg;" if abs(shift) >= 1.0
                    else "<span class='small'>&mdash;</span>")
            snap = getattr(pr, "snap_m", None)
            snap_cell = (f"{snap:.0f} m" if snap is not None
                         else "<span class='small'>&mdash;</span>")
            ov = None if drone else getattr(pr, "footprint_overlap", None)
            ov_cell = (f"{ov:.2f}" if ov is not None
                       else "<span class='small'>&mdash;</span>")
            if qlabel == "weak — mismatch":
                qlabel += f" ({_mismatch_cause(snap, ov, shift)})"
            slug = (pr.seed_name[5:] if pr.seed_name.startswith("seed_")
                    else pr.seed_name)
            srows.append(
                f"      <tr><td><a href=\"seed_{html.escape(slug)}.html\">"
                f"{html.escape(pr.seed_name)}</a></td>"
                f"<td>{'drone' if drone else 'street'}</td>"
                f"<td>{corr}</td><td>{snap_cell}</td><td>{ov_cell}</td>"
                f"<td>{nseg}</td><td>{nm}</td>"
                f"<td>{rate_cell}</td><td>{ncov}</td><td>{used}</td><td>{tag_cell}</td>"
                f"<td style=\"background:{qbg}\">{qlabel}</td></tr>"
            )
        n_corr = sum(1 for pr in pano_results
                     if abs(float(getattr(pr, "bearing_shift_deg", 0.0)
                                  or 0.0)) >= 1.0)
        n_withmatch = sum(1 for pr in pano_results
                          if int(getattr(pr, "n_matched", 0) or 0) > 0)
        pano_summary_html = f"""
<h2>Pano summary</h2>
<p class="small">{len(pano_results)} panos &middot; {n_corr} got a bearing
   correction &middot; {n_withmatch} produced matches. <b>Heading</b> =
   bearing shift applied (&mdash; = anchor already good). <b>Snap</b> = metres from the
   proposed seed position to the Street View pano it resolved to. <b>Overlap</b> = median
   column IoU of each matched tower with its footprint's projection (street seeds).
   A <b>mismatch</b> label names the likely cause (F-DET4): far snap, low overlap
   (segmentation or bearing), else an OSM gap. <b>Detected</b> =
   towers the splitter found. <b>Matched</b> = towers matched to OSM.
   <b>Match rate</b> = matched / detected (precision; ⚠ = fewer than 10
   segments detected — percentage is not statistically meaningful).
   <b>Coverage</b> = distinct OSM buildings matched.
   <b>Used</b> = buildings whose published aggregate has this seed's reading (trusted
   readings only). <b>Tag within 25 %</b> = of those on an OSM-tagged building, how many
   read within 25 % of the tag.
   <b>Quality</b> (street) = good requires ≥65% rate, ≥15 buildings, ≥10 segments;
   medium ≥50%/≥5/≥4; otherwise weak. Drone seeds are footprint-first (every reading is
   a matched footprint, so no match rate): quality is the tag-within-25 % share
   (good ≥80 %, medium ≥60 %, at least 3 tagged).</p>
<table>
  <thead><tr><th>seed</th><th>kind</th><th>heading</th><th>snap</th>
    <th>overlap</th><th>detected</th>
    <th>matched</th><th>match rate</th><th>coverage</th><th>used</th>
    <th>tag within 25 %</th><th>quality</th></tr></thead>
  <tbody>
{chr(10).join(srows)}
  </tbody>
</table>
"""

    css = f"""{body_h1_css(1100)}  table {{ border-collapse: collapse; margin: 0.5em 0; }}
  th, td {{ padding: 0.3em 0.7em; border: 1px solid #ddd; text-align: left; }}
  th {{ background: #f4f6fa; font-weight: 600; }}
  a {{ color: #0a3070; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  /* Call-hierarchy table: tint + left-accent per nesting level so the
     parent→child structure reads at a glance. */
  table.timings tr.lvl1 td {{ background: #fafbfd; color: #444;
       box-shadow: inset 3px 0 #c7d4ea; }}
  table.timings tr.lvl2 td {{ background: #fdfdfe; color: #666;
       font-size: 0.9em; box-shadow: inset 6px 0 #e2e8f4; }}
  table.timings tr.ttotal th {{ background: #eef1f6; }}
  table.timings td:nth-child(4), table.timings td:nth-child(5),
  table.timings th:nth-child(4), table.timings th:nth-child(5) {{
       text-align: right; white-space: nowrap; }}
  .stats {{ display: flex; gap: 2em; margin: 1em 0; }}
  .stat {{ background: #f4f6fa; padding: 0.6em 1em; border-radius: 4px; }}
  .stat .num {{ font-size: 1.4em; font-weight: 600; color: #0a3070; }}
  .small {{ font-size: 0.85em; color: #666; }}
  /* Verification tiers (F-SKY26 2f); colours from tier_display (Okabe-Ito). */
  .tierbar {{ display: flex; height: 18px; border-radius: 4px; overflow: hidden;
             max-width: 900px; margin: 0.4em 0; }}
  .tierbar span {{ display: block; height: 100%; }}
  .tchips {{ display: flex; flex-wrap: wrap; gap: 0.3em 1.2em; margin: 0.3em 0 0.8em; }}
  .tchip {{ font-size: 0.9em; cursor: help; }}
  .tsw {{ display: inline-block; width: 11px; height: 11px; border-radius: 3px;
          margin-right: 5px; vertical-align: -1px; }}
  .tier {{ white-space: nowrap; cursor: help; border-bottom: 1px dotted #999; }}
  .unv {{ font-size: 0.8em; color: #7a3300; background: #fdeee4; border-radius: 8px;
          padding: 1px 6px; white-space: nowrap; }}
  td.num {{ text-align: right; white-space: nowrap; }}
  td.note {{ font-size: 0.85em; color: #555; }}
  .headline {{ display: flex; gap: 1.5em; align-items: flex-start; flex-wrap: wrap; }}
  .headline-text {{ flex: 1 1 420px; min-width: 0; }}
  img.tiermap {{ width: 420px; max-width: 100%; height: auto; border: 1px solid #ccc;
                 background: #fff; }}
  /* Time-allocation chart: stacked ABOVE the table, full width. Each row
     is a 3-col grid (label | bar track | number) so bars never overlap
     the labels. */
  .talloc-title {{ color: #0a3070; font-size: 1em; margin: 1.2em 0 0.6em; }}
  .talloc {{ max-width: 900px; margin-bottom: 0.4em; }}
  .talloc-row {{ display: grid; grid-template-columns: 320px 1fr 78px;
       align-items: center; gap: 0.6em; margin-bottom: 0.35em; }}
  .talloc-label {{ font-size: 0.82em; color: #333; white-space: nowrap;
       overflow: hidden; text-overflow: ellipsis; }}
  .talloc-num {{ font-size: 0.82em; color: #555; font-weight: 600;
       text-align: right; white-space: nowrap; }}
  .talloc-track {{ background: #eef1f6; border-radius: 3px; height: 15px;
       width: 100%; overflow: hidden; }}
  .talloc-bar {{ display: block; height: 100%; border-radius: 3px;
       min-width: 2px; }}
{THEME_CSS}"""
    body = f"""<nav class="breadcrumb"><a href="../index.html">&#8592; All regions</a></nav>
<h1>{html.escape(title)}</h1>

<div class="stats">
  <div class="stat"><div class="num">{n_seeds}</div>seeds</div>
  <div class="stat"><div class="num">{n_buildings}</div>aggregated buildings</div>
</div>
{headline_html}
{pano_summary_html}
{screening_map_html}
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
{timings_html}
<footer class="small" style="margin-top:2em;color:#888;">
  Generated by city2stl.skyline.html_report (F-SKY15)
</footer>"""
    return page(html.escape(title), css, body)


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
    published_by_id = {r.get("feature_id"): r for r in (building_heights or ())}

    # Group seed_views by seed_name. Each value is the ordered list of
    # per-view rows for that seed. The first entry is used for the
    # seed-level summary panel; all entries are walked for the per-view
    # image gallery.
    views_by_seed: dict[str, list[SeedViewRegistration]] = {}
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

    from .config import current as _cfg  # noqa: PLC0415
    _depth_enabled = _cfg().html_depth
    _pano_layered = _cfg().pano_layered

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
                # The anchor_offset must match what ``_build_and_detect_pano``
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
                # Depth PNG: slow (predict_pano_depth on CPU) — gated.
                # Reconstruction + scan use pano_result.pano_depth (already
                # computed during the pipeline run) so they always render.
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
                # Reconstruction: uses cached pano_depth — always render.
                recon_png = views_pano_dir / f"{slug}_pano_recon.png"
                if _render_pano_reconstruction_png(
                        recon_png, pano_result, buildings_by_id,
                        osm_data=osm_data):
                    pano_rels["pano_reconstruction"] = (
                        f"assets/pano/{slug}_pano_recon.png")
                # 2D distance-vs-bearing strip (depth silhouette vs OSM
                # nearest). Uses pano_result data — always render.
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
                pano_rel_paths=pano_rels, published_by_id=published_by_id,
            )
            (out_dir / f"seed_{slug}.html").write_text(page_html, encoding="utf-8")
            continue

        # Bad / negative seeds: skip ALL per-view PNG generation and write a
        # minimal summary page (minimap + rejection reason only). Per-view
        # Street View images have no diagnostic value when a seed failed the
        # coverage screen or was declared negative — rendering them just bloats
        # the report with imagery from a pano that was already discarded.
        if primary.is_negative:
            page_html = render_seed_page(
                primary, region_name, minimap_rel,
                estimates=None,
                views=None,
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
        _depth_enabled = _cfg().html_depth
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
                        recon_png, vsv, buildings_by_id,
                        osm_data=osm_data):
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

    records = list(buildings_by_id.values())
    known_heights = None
    tier_map_rel = None
    if building_heights:
        try:
            from ._region_render._pages import _load_known_heights, render_tier_map_png
            known_heights = _load_known_heights(region_name, records)
            if render_tier_map_png(out_dir / "assets" / "tiers_map.png",
                                   with_unmeasured_tags(building_heights, records),
                                   buildings_by_id):
                tier_map_rel = "assets/tiers_map.png"
        except Exception as e:  # the headline must not break the report
            logger.warning("HTML report: tier map / known heights failed: %s", e)
    index_html = render_region_index(
        region_name, seed_views, building_heights, step_timings=step_timings,
        screening_map_rel=screening_map_rel, pano_results=pano_results,
        building_records=records, known_heights=known_heights, tier_map_rel=tier_map_rel)
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")

    logger.info(
        "F-SKY15 HTML report written: %s (%d seeds, %d total views)",
        out_dir, len(views_by_seed), sum(len(v) for v in views_by_seed.values()),
    )
