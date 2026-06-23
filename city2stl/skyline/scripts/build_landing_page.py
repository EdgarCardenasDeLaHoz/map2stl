"""build_landing_page.py — master landing page linking every region report.

One row per SEED/PANO across all regions. Parses each region's pano
summary table (already rendered in index.html) so no pipeline re-run is
needed — just re-run this script after any batch of city runs.

Each row has a three-state feedback toggle (Unreviewed / Good / Bad)
stored in browser localStorage, keyed by "region:seed_name".

Web-skyline images (Flickr / Wikimedia downloads) are shown in a
dedicated thumbnail gallery so they're easy to find.

Run:
    python -m city2stl.skyline.scripts.build_landing_page
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path

_REPORTS = (Path(__file__).resolve().parent.parent
            / "runs" / "region_reports")

_CURATED = {"cartagena", "chicago", "miami"}

_BACKLINK = ('<nav class="breadcrumb" style="font-size:0.9em;margin-bottom:1em;">'
             '&#8592; <a href="../index.html">strm2stl home</a></nav>')

# --- parsers ------------------------------------------------------------------

def _stat(txt: str, label: str) -> int:
    m = re.search(
        r'<div class="num">\s*([0-9]+)\s*</div>\s*' + re.escape(label), txt)
    return int(m.group(1)) if m else 0


# Matches a row in the pano summary <tbody>:
#   <tr><td><a href="seed_N.html">seed_1</a></td>
#       <td>corr</td><td>nseg</td><td>nm</td>
#       <td>rate%</td><td>ncov</td>
#       <td style="background:rgba(...)">quality_label</td></tr>
_ROW_RE = re.compile(
    r'<tr><td><a href="([^"]+)">([^<]+)</a></td>'   # (url, name)
    r'<td>.*?</td>'                                   # heading correction
    r'<td>(\d+)</td>'                                 # detected
    r'<td>(\d+)</td>'                                 # matched
    r'<td>([^<]+)</td>'                               # match rate  e.g. "84%"
    r'<td>(\d+)</td>'                                 # coverage
    r'<td[^>]*?background:([^"]+)"[^>]*>([^<]+)</td>'# bgcolor, quality label
    r'</tr>',
    re.DOTALL,
)

# Quality background colour → canonical state (coarse: good / medium / weak)
_BG_TO_QUAL = {
    "rgba(46,160,67":  "good",
    "rgba(230,180,40": "medium",
    "rgba(214,40,40":  "weak",    # original red + F-DET3 "no detection"
    "rgba(200,60,20":  "weak",    # F-DET3 "mismatch"
    "rgba(200,130,20": "weak",    # F-DET3 "low coverage"
}


def _parse_pano_rows(txt: str, region: str, report_dir: Path
                     ) -> list[dict]:
    """Extract per-pano stats from a region's rendered index.html."""
    rows = []
    for m in _ROW_RE.finditer(txt):
        rel_url, seed_name, nseg, nm, rate, ncov, bgcolor, qlabel = m.groups()
        qual = next((v for k, v in _BG_TO_QUAL.items() if k in bgcolor), "weak")
        # Seed source type
        if seed_name.startswith("web_"):
            src = "web"
        elif seed_name.startswith("auto"):
            src = "auto"
        else:
            src = "curated" if region in _CURATED else "seed"
        rows.append(dict(
            region=region,
            seed=seed_name,
            url=f"{report_dir.name}/{rel_url}",
            src=src,
            nseg=int(nseg),
            nm=int(nm),
            rate=rate.strip(),
            ncov=int(ncov),
            qual=qual,
            qlabel=qlabel.strip(),
        ))
    return rows


def _web_images(report_dir: Path) -> list[Path]:
    """Return web-skyline JPEGs cached inside this region's report dir."""
    web_dir = report_dir / "web_images"
    if not web_dir.is_dir():
        return []
    return sorted(web_dir.glob("*.jpg")) + sorted(web_dir.glob("*.png"))


# --- HTML helpers -------------------------------------------------------------

_QUAL_STYLE = {
    "good":   "color:#1a7a1a;font-weight:600",
    "medium": "color:#856404;font-weight:600",
    "weak":   "color:#cc0000;font-weight:600",
}
_QUAL_ICON = {"good": "✓", "medium": "~", "weak": "✗"}
_SRC_BADGE = {
    "curated": '<span class="badge-curated">👤</span>',
    "seed":    '<span class="badge-seed">📍</span>',
    "auto":    '<span class="badge-auto">🤖</span>',
    "web":     '<span class="badge-web">🌐</span>',
}


def _det_cell_style(nseg: int) -> str:
    if nseg < 10:
        return "text-align:right;background:rgba(214,40,40,0.18);color:#990000;font-weight:600"
    if nseg < 20:
        return "text-align:right;background:rgba(230,180,40,0.25);color:#6b5000;font-weight:600"
    return "text-align:right"


def _row_html(r: dict) -> str:
    icon = _QUAL_ICON.get(r["qual"], "?")
    style = _QUAL_STYLE.get(r["qual"], "")
    badge = _SRC_BADGE.get(r["src"], "")
    fb_key = html.escape(f"{r['region']}:{r['seed']}")
    det_style = _det_cell_style(r["nseg"])
    # Derive a canonical sub-label slug for JS filtering from the raw qlabel
    ql = r["qlabel"].replace(" ⚠", "").strip()
    if ql.startswith("weak"):
        if "no detection" in ql:
            qlslug = "weak-nodet"
        elif "mismatch" in ql:
            qlslug = "weak-mismatch"
        elif "low coverage" in ql:
            qlslug = "weak-lowcov"
        else:
            qlslug = "weak"
    else:
        qlslug = ql  # "good" / "medium"
    return (
        f'<tr data-region="{html.escape(r["region"])}"'
        f' data-qual="{r["qual"]}"'
        f' data-qlslug="{qlslug}">'
        f'<td class="region-cell">{html.escape(r["region"].replace("_"," ").title())}</td>'
        f'<td>{badge}</td>'
        f'<td><a href="{html.escape(r["url"])}">{html.escape(r["seed"])}</a></td>'
        f'<td style="{det_style}">{r["nseg"]}</td>'
        f'<td style="text-align:right">{r["nm"]}</td>'
        f'<td style="text-align:right">{r["rate"]}</td>'
        f'<td style="text-align:right">{r["ncov"]}</td>'
        f'<td style="{style}">{icon} {html.escape(r["qlabel"])}</td>'
        f'<td><button class="fb-btn" data-fbkey="{fb_key}"'
        f' onclick="toggleFb(this)">&#10067;</button></td>'
        f'</tr>'
    )


# --- main ---------------------------------------------------------------------

def main() -> None:
    all_rows: list[dict] = []
    web_gallery: list[tuple[str, Path]] = []   # (region_title, img_path)
    total_buildings = 0

    for idx in sorted(_REPORTS.glob("*_skyline_report/index.html")):
        try:
            txt = idx.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Backlink injection (idempotent)
        if "All regions" not in txt and "<body>" in txt:
            txt = txt.replace("<body>", "<body>\n" + _BACKLINK, 1)
            idx.write_text(txt, encoding="utf-8")

        region = idx.parent.name.replace("_skyline_report", "")
        total_buildings += _stat(txt, "aggregated buildings")
        all_rows.extend(_parse_pano_rows(txt, region, idx.parent))

        for img in _web_images(idx.parent):
            web_gallery.append((region.replace("_", " ").title(), img))

    # --- Build table rows grouped by region -----------------------------------
    # Keep region order stable (alphabetical within curated vs auto)
    seen_regions: list[str] = []
    for r in all_rows:
        if r["region"] not in seen_regions:
            seen_regions.append(r["region"])

    table_rows_html = ""
    for region in seen_regions:
        region_rows = [r for r in all_rows if r["region"] == region]
        curated = region in _CURATED
        region_label = region.replace("_", " ").title()
        badge = "👤 curated" if curated else "🤖 auto"
        badge_cls = "badge-curated" if curated else "badge-auto"
        last_idx = _REPORTS / f"{region}_skyline_report" / "index.html"
        last_run = (
            datetime.fromtimestamp(last_idx.stat().st_mtime).strftime("%b %d")
            if last_idx.exists() else "—"
        )
        # Region header row
        table_rows_html += (
            f'<tr class="region-header">'
            f'<td colspan="9"><a href="{html.escape(region)}_skyline_report/index.html">'
            f'<strong>{html.escape(region_label)}</strong></a>'
            f' &nbsp;<span class="{badge_cls}">{badge}</span>'
            f' &nbsp;<span class="run-date">{last_run}</span></td></tr>\n'
        )
        for r in region_rows:
            table_rows_html += _row_html(r) + "\n"

    # --- Web gallery ----------------------------------------------------------
    web_gallery_html = ""
    if web_gallery:
        thumbs = ""
        for reg_title, img_path in web_gallery:
            rel = img_path.relative_to(_REPORTS).as_posix()
            thumbs += (
                f'<figure style="margin:0;text-align:center">'
                f'<img src="{html.escape(rel)}" loading="lazy"'
                f' style="height:120px;width:auto;border:1px solid #ddd;border-radius:4px;">'
                f'<figcaption style="font-size:0.75em;color:#666;margin-top:3px">'
                f'{html.escape(reg_title)}</figcaption></figure>'
            )
        web_gallery_html = f"""
<section style="margin-top:2em">
  <h2 style="color:#0a3070;font-size:1.05em;border-bottom:1px solid #dde3ee;
             padding-bottom:0.2em">
    Web-sourced skyline images
    <span style="font-weight:400;color:#666;font-size:0.85em">
      (Flickr / Wikimedia Commons downloads used as additional seeds)</span>
  </h2>
  <div style="display:flex;flex-wrap:wrap;gap:0.75em;margin-top:0.75em">
    {thumbs}
  </div>
</section>"""

    # --- Write index.html -----------------------------------------------------
    out = _REPORTS / "index.html"
    out.write_text(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skyline pipeline — all regions</title>
<style>
  body {{ font-family: -apple-system,"Segoe UI",Roboto,sans-serif;
         margin: 2em auto; max-width: 1350px; color: #222; padding: 0 1.2em; }}
  h1   {{ color: #0a3070; border-bottom: 2px solid #0a3070; padding-bottom:.2em; }}
  .summary {{ color:#666; font-size:.9em; margin-bottom:1em; }}

  /* Filter bar */
  .filters {{ display:flex; gap:.5em; flex-wrap:wrap; margin-bottom:.8em;
              align-items:center; font-size:.85em; }}
  .filters label {{ color:#444; }}
  .filters select, .filters input {{ padding:3px 7px; border:1px solid #bbb;
              border-radius:4px; font-size:inherit; }}
  .filters button {{ padding:3px 9px; border:1px solid #0a3070; border-radius:4px;
              background:#0a3070; color:#fff; cursor:pointer; font-size:inherit; }}
  .filters button:hover {{ opacity:.85; }}

  table {{ border-collapse:collapse; width:100%; font-size:.88em; }}
  th,td {{ padding:.35em .65em; border:1px solid #dde3ee; vertical-align:middle; }}
  th    {{ background:#f4f6fa; font-weight:600; color:#333; position:sticky;
           top:0; z-index:1; }}
  .region-header td {{ background:#e8edf8; font-size:.9em; padding:.3em .65em; }}
  .run-date {{ color:#999; font-size:.85em; }}
  tr:hover td {{ background:#f0f5ff; }}
  td a {{ color:#0a3070; text-decoration:none; font-weight:500; }}
  td a:hover {{ text-decoration:underline; }}

  /* Source badges */
  .badge-curated {{ background:#d4edda; color:#155724; border-radius:10px;
                    padding:1px 8px; font-size:.8em; }}
  .badge-seed    {{ background:#cce5ff; color:#004085; border-radius:10px;
                    padding:1px 8px; font-size:.8em; }}
  .badge-auto    {{ background:#fff3cd; color:#856404; border-radius:10px;
                    padding:1px 8px; font-size:.8em; }}
  .badge-web     {{ background:#f3e6ff; color:#5a0099; border-radius:10px;
                    padding:1px 8px; font-size:.8em; }}

  /* Region-level badges (used in header rows) */
  .badge-curated, .badge-auto {{ display:inline-block; }}

  /* Feedback button */
  .fb-btn {{ border:1px solid #bbb; border-radius:12px; background:#fff;
             cursor:pointer; padding:2px 10px; font-size:.82em; white-space:nowrap; }}
  .fb-btn:hover {{ opacity:.8; }}
  .fb-good {{ border-color:#1a7a1a; color:#1a7a1a; background:#eafaee; }}
  .fb-bad  {{ border-color:#cc0000; color:#cc0000; background:#faeeee; }}
  .fb-none {{ border-color:#bbb;    color:#888;    background:#fff; }}

  /* Legend key */
  .key {{ display:flex; gap:1.2em; font-size:.8em; color:#555;
          margin-bottom:.8em; flex-wrap:wrap; }}
</style>
</head>
<body>
<h1>Skyline pipeline — all regions</h1>
<p class="summary">
  {len(seen_regions)} regions &middot; {total_buildings:,} aggregated buildings &middot;
  {len(all_rows)} panos total.
  Feedback (&#10067; / &#9989; / &#10060;) is saved in your browser
  and survives page reloads and landing-page regeneration.
</p>

<div class="key">
  <span><span class="badge-curated">👤 curated</span> user-selected seeds</span>
  <span><span class="badge-auto">🤖 auto</span> geometry-proposed</span>
  <span><span class="badge-web">🌐 web</span> Flickr / Wikimedia image</span>
  <span><span class="badge-seed">📍 seed</span> URL seed (unvalidated city)</span>
</div>

<div class="filters">
  <label>Region: <select id="flt-region" onchange="applyFilters()">
    <option value="">All</option>
    {''.join(f'<option value="{html.escape(r)}">{html.escape(r.replace("_"," ").title())}</option>' for r in seen_regions)}
  </select></label>
  <label>Quality: <select id="flt-qual" onchange="applyFilters()">
    <option value="">All</option>
    <option value="good">Good only</option>
    <option value="medium">Medium+</option>
    <option value="weak">Weak (any)</option>
    <option value="weak-nodet">Weak — no detection</option>
    <option value="weak-mismatch">Weak — mismatch</option>
    <option value="weak-lowcov">Weak — low coverage</option>
  </select></label>
  <label>Feedback: <select id="flt-fb" onchange="applyFilters()">
    <option value="">All</option>
    <option value="good">Marked good</option>
    <option value="bad">Marked bad</option>
    <option value="none">Unreviewed</option>
  </select></label>
  <button onclick="clearFilters()">Clear</button>
</div>

<table id="pano-table">
  <thead>
    <tr>
      <th>Region</th><th>Src</th><th>Seed</th>
      <th title="Segments detected">Det</th>
      <th title="Segments matched to OSM">Mat</th>
      <th title="Match rate">Rate</th>
      <th title="Distinct OSM buildings matched">Cov</th>
      <th>Quality</th>
      <th>Feedback</th>
    </tr>
  </thead>
  <tbody>
{table_rows_html}
  </tbody>
</table>
{web_gallery_html}

<script>
const SK = 'skyline_feedback_v1';
const STATES  = ['none','good','bad'];
const LABELS  = {{none:'\\u2753',good:'\\u2705 Good',bad:'\\u274C Bad'}};
const CLS     = {{none:'fb-none',good:'fb-good',bad:'fb-bad'}};

function _load(){{try{{return JSON.parse(localStorage.getItem(SK)||'{{}}');}}catch(e){{return{{}};}}}}
function _save(d){{try{{localStorage.setItem(SK,JSON.stringify(d));}}catch(e){{}}}}

function _applyBtn(btn,state){{
  btn.textContent=LABELS[state]; btn.className='fb-btn '+CLS[state];
  btn.closest('tr').dataset.fb=state;
}}
function toggleFb(btn){{
  const key=btn.dataset.fbkey, d=_load();
  const next=STATES[(STATES.indexOf(d[key]||'none')+1)%STATES.length];
  d[key]=next; _save(d); _applyBtn(btn,next);
}}

// Restore on load
(function(){{
  const d=_load();
  document.querySelectorAll('.fb-btn').forEach(function(btn){{
    _applyBtn(btn,d[btn.dataset.fbkey]||'none');
  }});
}})();

// Filters
function applyFilters(){{
  const fRegion=document.getElementById('flt-region').value;
  const fQual  =document.getElementById('flt-qual').value;
  const fFb    =document.getElementById('flt-fb').value;
  document.querySelectorAll('#pano-table tbody tr').forEach(function(tr){{
    if(tr.classList.contains('region-header')){{tr.style.display='';return;}}
    const region=tr.dataset.region||'';
    const qual  =tr.dataset.qual||'';
    const qlslug=tr.dataset.qlslug||'';
    const fb    =tr.dataset.fb||'none';
    let show=true;
    if(fRegion && region!==fRegion) show=false;
    if(fQual==='good' && qual!=='good') show=false;
    if(fQual==='medium' && qual!=='good' && qual!=='medium') show=false;
    if(fQual==='weak' && qual!=='weak') show=false;
    if(fQual==='weak-nodet' && qlslug!=='weak-nodet') show=false;
    if(fQual==='weak-mismatch' && qlslug!=='weak-mismatch') show=false;
    if(fQual==='weak-lowcov' && qlslug!=='weak-lowcov') show=false;
    if(fFb && fb!==fFb) show=false;
    tr.style.display=show?'':'none';
  }});
  // Hide region headers with no visible data rows
  document.querySelectorAll('.region-header').forEach(function(hdr){{
    let next=hdr.nextElementSibling;
    let any=false;
    while(next&&!next.classList.contains('region-header')){{
      if(next.style.display!=='none') any=true;
      next=next.nextElementSibling;
    }}
    hdr.style.display=any?'':'none';
  }});
}}
function clearFilters(){{
  ['flt-region','flt-qual','flt-fb'].forEach(id=>document.getElementById(id).value='');
  applyFilters();
}}
</script>
</body>
</html>
""", encoding="utf-8")

    print(f"landing page: {len(seen_regions)} regions, "
          f"{len(all_rows)} panos, "
          f"{total_buildings:,} buildings -> {out}")
    if web_gallery:
        print(f"  web images: {len(web_gallery)} thumbnails shown")


if __name__ == "__main__":
    main()
