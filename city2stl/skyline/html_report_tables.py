"""Part of ``city2stl.skyline.html_report`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from geo2stl.geo import M_PER_DEG_SPHERE, m_per_deg_lon

if TYPE_CHECKING:
    pass
# PNG / plot renderers live in the _report_plots/ subpackage (F-CLEAN14,
# 2026-06-07); the HTML-assembly functions below call them unchanged.
from .tier_display import (  # noqa: E402
    TIER_COLORS,
    TIER_HINTS,
    TIER_LABELS,
    TIERS,
    VERIFIED_TIERS,
    height_attributions,
    is_unverified,
    row_tier,
    source_label,
    tier_counts,
    tier_hover,
    withheld_note,
)


def _reading_status(seg: dict, row: dict | None, seed: str) -> tuple[str, str]:
    """(used, reason) for one seed's reading of one building. Used = the seed's reading is in
    the building's published aggregate (``per_seed_median_m``), so it was trusted and not left
    out by the tower-behind check. The reason comes from the segment when the pipeline wrote it
    (``untrusted_reason`` / ``top_edge`` / ``behind``); otherwise only "not used" is known. A
    footprint with no measured row (e.g. a tagged tower whose readings were all untrusted,
    ``measured: false``) still shows the segment's reason; "no published row" only when there is
    none (review 2026-10-09: 27 tagged towers, Palmetto among them, showed only that)."""
    if row is not None and seed in (row.get("per_seed_median_m") or {}):
        return "yes", ""
    reason = seg.get("untrusted_reason") or ""
    if not reason and seg.get("behind"):
        reason = "tower behind"
    if not reason and seg.get("top_edge") and seg.get("top_edge") != "sky":
        reason = f"top edge: {seg.get('top_edge')}"
    if row is None and not reason:
        reason = "no published row"
    return "no", reason


def _segments_table_html(primary_sv, pano_result, published_by_id: dict | None = None) -> str:
    """The numbered badges on the pano images, as a table: badge -> building (record id, name),
    measured height, distance, bearing and a map link at the building's position, so a number
    seen on an image is looked up directly (user, 2026-10-06). With ``published_by_id``
    (coordinator review, 2026-10-08) each reading also says whether it was used, and shows the
    building's published value and tier, so a wrong-looking reading is not read as the
    answer."""
    import math

    segs = [s for s in (getattr(pano_result, "matched_segments", None) or [])
            if s.get("matched_projection")]
    if not segs:
        return ""
    pub = published_by_id or {}
    seed = str(getattr(pano_result, "seed_name", "") or getattr(primary_sv, "seed_name", ""))
    lat0 = float(getattr(pano_result, "seed_lat", primary_sv.seed_lat))
    lon0 = float(getattr(pano_result, "seed_lon", primary_sv.seed_lon))
    rows = []
    for s in sorted(segs, key=lambda s: int(s.get("seed_index") or 0)):
        m = s["matched_projection"]
        d = float(m.get("distance_m") or m.get("forward_m") or 0.0)
        b = float(s.get("true_bearing_deg") or 0.0)
        lat = lat0 + d * math.cos(math.radians(b)) / M_PER_DEG_SPHERE
        lon = lon0 + d * math.sin(math.radians(b)) / float(m_per_deg_lon(lat0))
        h = s.get("height_m")
        name = str(m.get("name") or "")
        fid = str(m.get("feature_id") or "")
        n = int(s.get("seed_index") or 0)
        base = "yes" if s.get("base_visible") else ("no" if "base_visible" in s else "")
        extra = ""
        if published_by_id is not None:
            row = pub.get(fid)
            used, reason = _reading_status(s, row, seed)
            ph = (row or {}).get("effective_height_m")
            t = row_tier(row) if row else None
            extra = (f'<td class="{"used" if used == "yes" else "unused"}">{used}'
                     f'{f" <span class=small>({html.escape(reason)})</span>" if reason else ""}'
                     f"</td><td>{'' if ph is None else f'{float(ph):.0f}'}</td>"
                     + (f'<td><span class="tier" title="{html.escape(tier_hover(row))}">'
                        f'<span class="tsw" style="background:{TIER_COLORS[t]}"></span>'
                        f"{html.escape(TIER_LABELS[t])}</span></td>" if t else "<td></td>"))
        rows.append(
            f'<tr id="seg-{n}"><td>{n}</td><td>{html.escape(fid)}</td>'
            f"<td>{html.escape(name if name != fid else '')}</td>"
            f"<td>{'' if h is None else f'{float(h):.0f}'}</td><td>{d:.0f}</td><td>{b:.0f}°</td>"
            f"<td>{base}</td>{extra}"
            f'<td><a href="https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}'
            f'#map=18/{lat:.5f}/{lon:.5f}">{lat:.5f}, {lon:.5f}</a></td></tr>')
    extra_head = ("<th title='whether this reading is in the published value'>used</th>"
                  "<th>published m</th><th>tier</th>") if published_by_id is not None else ""
    note = (" <b>Height m</b> is this seed's reading; <b>published m</b> is the building's "
            "answer from all evidence. A reading not used (untrusted, or a tower behind) is "
            "not the answer.") if published_by_id is not None else ""
    return ("<section class=\"segments\">\n  <h2>Numbered buildings</h2>\n"
            "  <p class=\"small\">The numbers drawn on the pano images. Position = the camera plus the "
            f"building's distance along its bearing.{note}</p>\n  <table>\n"
            "    <tr><th>#</th><th>building</th><th>name</th><th>height m</th><th>distance m</th>"
            f"<th>bearing</th><th>base visible</th>{extra_head}<th>position</th></tr>\n    "
            + "\n    ".join(rows) + "\n  </table>\n</section>")


# ---------------------------------------------------------------------------
# Region index
# ---------------------------------------------------------------------------

#: F-DET4b: a resolved pano this far from the proposed seed sees another street.
MISMATCH_SNAP_M = 100.0


#: Below this median column IoU the matched towers only graze their footprints.
MISMATCH_OVERLAP = 0.35


def _mismatch_cause(snap_m: float | None, overlap: float | None, shift_deg: float) -> str:
    """Likely cause of a Type 2 (detected but unmatched) pano, from the A7 instruments."""
    if snap_m is not None and snap_m > MISMATCH_SNAP_M:
        return "far snap"
    if overlap is not None and overlap < MISMATCH_OVERLAP:
        return "bearing" if abs(shift_deg) >= 1.0 else "segmentation"
    return "OSM gap?"


def _is_footprint_first(pr) -> bool:
    """A drone (elevated) seed's pano result: one segment per measured footprint, all with
    ``height_src == "footprint"``, so detected == matched by construction."""
    segs = getattr(pr, "matched_segments", None) or []
    return bool(segs) and all(isinstance(x, dict) and x.get("height_src") == "footprint"
                              for x in segs)


def _tags_by_id(rows: list[dict], records=()) -> dict[str, float]:
    """feature_id -> OSM height tag (osm_tag / osm_levels), from rows and records."""
    out: dict[str, float] = {}
    for rec in records or ():
        if getattr(rec, "height_source", None) in ("osm_tag", "osm_levels") \
                and getattr(rec, "height_tag_m", None):
            out[rec.feature_id] = float(rec.height_tag_m)
    for r in rows:
        if r.get("height_source") in ("osm_tag", "osm_levels") and r.get("height_tag_m"):
            out[r["feature_id"]] = float(r["height_tag_m"])
    return out


def _seed_reading_stats(seed: str, rows: list[dict],
                        tags: dict[str, float]) -> tuple[int, int, int]:
    """For one seed: (readings that reached the published aggregate, of those on a tagged
    building, of those within 25 % of the tag)."""
    used = tagged = within = 0
    for r in rows:
        v = (r.get("per_seed_median_m") or {}).get(seed)
        if not isinstance(v, (int, float)):
            continue
        used += 1
        t = tags.get(r.get("feature_id"))
        if t:
            tagged += 1
            within += abs(float(v) - t) <= 0.25 * t
    return used, tagged, within


def _fmt_seed_values(row: dict, seeds: set[str]) -> str:
    vals = [(s, v) for s, v in sorted((row.get("per_seed_median_m") or {}).items())
            if s in seeds and isinstance(v, (int, float))]
    return ", ".join(f"{html.escape(s)} {float(v):.0f}" for s, v in vals) or "—"


def _fmt_satellite(row: dict) -> str:
    sat = row.get("satellite") or {}
    parts = []
    for k, v in sorted(sat.items()):
        try:
            m, conf = (v[0], v[1]) if isinstance(v, (list, tuple)) else (v, None)
            parts.append(f"{html.escape(str(k))} {float(m):.0f}"
                         + (f' <span class="small">({float(conf):.2f})</span>'
                            if conf is not None else ""))
        except (TypeError, ValueError, IndexError):
            continue
    return ", ".join(parts) or "—"


def _towers_table_html(known: list[dict], rows: list[dict], drone_set: set[str]) -> str:
    """The published towers (``known_heights``: CTBUH-style reference heights) against our
    published value, its source and tier, and the drone and satellite readings behind it."""
    if not known:
        return ('<p class="small">No reference tower heights for this region '
                '(sites/&lt;region&gt;.json <code>known_heights_m</code>).</p>')
    by_id = {r.get("feature_id"): r for r in rows}
    body = []
    for k in sorted(known, key=lambda k: -float(k.get("ctbuh_m") or 0)):
        ref = k.get("ctbuh_m")
        r = by_id.get(k.get("matched_id"))
        if r is None:
            body.append(f"<tr><td>{html.escape(str(k.get('name')))}</td>"
                        f"<td class='num'>{float(ref or 0):.0f}</td>"
                        "<td colspan='6' class='note'>no published row (footprint not matched "
                        "or not measured)</td></tr>")
            continue
        ours = r.get("effective_height_m")
        off = (f"{100.0 * (float(ours) - float(ref)) / float(ref):+.0f} %"
               if isinstance(ours, (int, float)) and ref else "—")
        t = row_tier(r)
        body.append(
            f"<tr><td>{html.escape(str(k.get('name')))} "
            f"<span class='small'>{html.escape(str(k.get('matched_id')))}</span></td>"
            f"<td class='num'>{float(ref or 0):.0f}</td>"
            f"<td class='num'><b>{'' if ours is None else f'{float(ours):.0f}'}</b></td>"
            f"<td class='num'>{off}</td>"
            f"<td>{html.escape(source_label(r))}</td>"
            f'<td><span class="tier" title="{html.escape(tier_hover(r))}">'
            f'<span class="tsw" style="background:{TIER_COLORS[t]}"></span>'
            f"{html.escape(TIER_LABELS[t])}</span></td>"
            f"<td>{_fmt_seed_values(r, drone_set)}</td><td>{_fmt_satellite(r)}</td></tr>")
    return ("<table class='towers'><thead><tr><th>Tower</th><th>Reference m</th>"
            "<th>Ours m</th><th>Off by</th><th>Source</th><th>Tier</th><th>Drone m</th>"
            "<th>Satellite m (conf)</th></tr></thead><tbody>\n" + "\n".join(body)
            + "\n</tbody></table>\n<p class='small'>Reference = the published (CTBUH-style) "
            "height in sites/&lt;region&gt;.json. Drone = per-seed medians of trusted readings "
            "that reached the published value.</p>")


def _tier_summary_html(rows: list[dict], stored: dict | None = None) -> str:
    """Tier counts for the heights section: a verified share, a stacked bar and a legend with
    counts (F-SKY26 2f), plus the survey licence lines when survey rows exist."""
    counts = tier_counts(rows, stored)
    total = sum(counts.values())
    if not total:
        return ""
    n_surv, n_corr = counts.get("survey", 0), counts.get("corroborated", 0)
    n_ver = sum(counts.get(t, 0) for t in VERIFIED_TIERS)
    pct = 100.0 * n_ver / total
    bar = "".join(
        f'<span style="flex:{n};background:{TIER_COLORS[t]}" '
        f'title="{html.escape(TIER_LABELS[t])}: {n}"></span>'
        for t, n in counts.items() if n)
    chips = "".join(
        f'<span class="tchip" title="{html.escape(TIER_HINTS[t])}">'
        f'<span class="tsw" style="background:{TIER_COLORS[t]}"></span>'
        f'{html.escape(TIER_LABELS[t])} <b>{n}</b> '
        f'<span class="small">({100.0 * n / total:.0f} %)</span></span>'
        for t, n in counts.items())
    attributions = height_attributions(rows)
    attr_html = "".join(f'<p class="small attr">{html.escape(a)}</p>' for a in attributions)
    return (f'<p><b>{n_surv} survey, {n_corr} corroborated of {total}</b> published heights '
            f'({pct:.0f} %); {total - n_ver} are unverified.</p>\n'
            f'<div class="tierbar">{bar}</div>\n<div class="tchips">{chips}</div>\n{attr_html}')


def _anchor_dev_cell(row: dict) -> str:
    """The row's photo anchor deviation in metres (``anchor_dev_m``), or an em dash when no
    photo anchored it (every Street View only row)."""
    v = row.get("anchor_dev_m")
    return f"{float(v):.1f}" if isinstance(v, (int, float)) else "&mdash;"


def _heights_table_html(rows: list[dict]) -> str:
    """Every published height with its tier (methods on hover), an "unverified" mark on
    tag/single/prior rows and a note where a single reading was withheld for the prior.
    Best tier first, tallest first within a tier; a tier filter above it. Reliability is shown
    by the signals that track error, the number of views (``n_views``) and the photo anchor
    deviation (``anchor_dev_m``), never by Street View confidence or seed disagreement."""
    rank = {t: i for i, t in enumerate((*TIERS, "unlabelled"))}

    def _h(r):
        v = r.get("effective_height_m")
        return float(v) if isinstance(v, (int, float)) else None

    ordered = sorted(rows, key=lambda r: (rank[row_tier(r)], -(_h(r) or 0.0)))
    body = []
    for r in ordered:
        t = row_tier(r)
        h = _h(r)
        notes = []
        w = withheld_note(r)
        if w:
            notes.append(w)
        elif r.get("prior_disagrees") and t == "single":
            notes.append("more than 2x the prior")
        if t == "survey" and r.get("survey_stale"):
            notes.append("survey may be stale: " + str(r.get("survey_stale_reason") or ""))
        if r.get("survey_withheld_reason"):
            notes.append("survey not published: " + str(r["survey_withheld_reason"]))
        if r.get("measured") is False:
            notes.append("not seen by a seed")
        name = str(r.get("name") or r.get("feature_id") or "")
        fid = str(r.get("feature_id") or "")
        name_cell = html.escape(name) if name == fid else (
            f'{html.escape(name)} <span class="small">{html.escape(fid)}</span>')
        body.append(
            f'<tr data-tier="{t}"><td>{name_cell}</td>'
            f'<td class="num">{"" if h is None else f"{h:.0f}"}</td>'
            f'<td><span class="tier" title="{html.escape(tier_hover(r))}">'
            f'<span class="tsw" style="background:{TIER_COLORS[t]}"></span>'
            f'{html.escape(TIER_LABELS[t])}</span></td>'
            f'<td>{"<span class=unv>unverified</span>" if is_unverified(r) else ""}</td>'
            f'<td>{html.escape(source_label(r))}</td>'
            f'<td class="num">{int(r.get("n_seeds") or 0)}</td>'
            f'<td class="num">{int(r.get("n_views") or 0)}</td>'
            f'<td class="num">{_anchor_dev_cell(r)}</td>'
            f'<td class="note">{html.escape("; ".join(notes))}</td></tr>')
    options = "".join(
        f'<option value="{t}">{html.escape(TIER_LABELS[t])}</option>'
        for t in (*TIERS, "unlabelled") if any(row_tier(r) == t for r in rows))
    return f"""
<p class="small">Hover a tier for the methods behind it. Show:
  <select id="tier-filter" onchange="for (const tr of document.querySelectorAll('#heights-table tbody tr'))
    tr.style.display = (!this.value || tr.dataset.tier === this.value) ? '' : 'none';">
    <option value="">all tiers ({len(rows)})</option>{options}</select></p>
<table id="heights-table">
  <thead><tr><th>Building</th><th>Height (m)</th><th>Tier</th><th></th><th>Source</th>
    <th>Seeds</th><th title="Street View images behind the height: 4+ views average 46 m error against 109 m for one">Views</th>
    <th title="Photo anchor deviation (m): how far the photo's own fit disagrees with the footprints; larger means less reliable">Anchor dev (m)</th>
    <th>Note</th></tr></thead>
  <tbody>
{chr(10).join(body)}
  </tbody>
</table>
"""
