"""city2stl.skyline.html_report — F-SKY15 HTML diagnostic report.

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

# PNG / plot renderers split out to report_plots.py (F-CLEAN14, 2026-06-07).
# Imported back so the HTML-assembly functions below call them unchanged.
from .report_plots import (  # noqa: E402,F401
    POLAR_MAX_M,
    _save_view_image_png,
    _render_screening_map_png,
    _render_view_mask_png,
    _render_view_depth_png,
    _render_view_reconstruction_png,
    _render_seed_minimap_png,
    _fmt_optional_float,
    _fmt_optional_bool,
    _draw_pano_bboxes_inplace,
    _draw_pano_north_line_inplace,
    _render_pano_minimap_polar_png,
    _render_pano_heights_polar_png,
    _render_pano_segformer_overlay_png,
    _render_pano_bearing_scan_png,
    _render_pano_depth_png,
    _render_pano_reconstruction_png,
)

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
    # "All" panels reuse the SAME panel HTML (same image files) stacked
    # vertically — adds only a few <img> tags, no extra image data.
    pano_all_panel = (
        f'<div class="stack-all">{pano_rgb_panel}{pano_seg_panel}'
        f'{pano_depth_panel}{pano_scan_panel}</div>'
    )
    topdown_all_panel = (
        f'<div class="stack-all stack-td">{footprints_panel}{satellite_panel}'
        f'{recon_panel}{heights_panel}</div>'
    )
    pano_space_tabs = (
        f'    <figure class="tabs panospace">\n'
        f'      <input type="radio" name="pano_{tab_id}" id="pano_{tab_id}_rgb" checked>\n'
        f'      <input type="radio" name="pano_{tab_id}" id="pano_{tab_id}_seg">\n'
        f'      <input type="radio" name="pano_{tab_id}" id="pano_{tab_id}_depth">\n'
        f'      <input type="radio" name="pano_{tab_id}" id="pano_{tab_id}_scan">\n'
        f'      <input type="radio" name="pano_{tab_id}" id="pano_{tab_id}_all">\n'
        f'      <div class="tab-labels">\n'
        f'        <label for="pano_{tab_id}_rgb">Street view</label>\n'
        f'        <label for="pano_{tab_id}_seg">SegFormer mask</label>\n'
        f'        <label for="pano_{tab_id}_depth">Depth</label>\n'
        f'        <label for="pano_{tab_id}_scan">Distance scan</label>\n'
        f'        <label for="pano_{tab_id}_all">All</label>\n'
        f'      </div>\n'
        f'      <div class="tab-panel pano-rgb">{pano_rgb_panel}</div>\n'
        f'      <div class="tab-panel pano-seg">{pano_seg_panel}</div>\n'
        f'      <div class="tab-panel pano-depth">{pano_depth_panel}</div>\n'
        f'      <div class="tab-panel pano-scan">{pano_scan_panel}</div>\n'
        f'      <div class="tab-panel pano-all">{pano_all_panel}</div>\n'
        f'    </figure>'
    )
    topdown_tabs = (
        f'    <figure class="tabs topdown">\n'
        f'      <input type="radio" name="td_{tab_id}" id="td_{tab_id}_fp" checked>\n'
        f'      <input type="radio" name="td_{tab_id}" id="td_{tab_id}_sat">\n'
        f'      <input type="radio" name="td_{tab_id}" id="td_{tab_id}_recon">\n'
        f'      <input type="radio" name="td_{tab_id}" id="td_{tab_id}_heights">\n'
        f'      <input type="radio" name="td_{tab_id}" id="td_{tab_id}_all">\n'
        f'      <div class="tab-labels">\n'
        f'        <label for="td_{tab_id}_fp">Footprints</label>\n'
        f'        <label for="td_{tab_id}_sat">Satellite</label>\n'
        f'        <label for="td_{tab_id}_recon">Reconstruction</label>\n'
        f'        <label for="td_{tab_id}_heights">Heights</label>\n'
        f'        <label for="td_{tab_id}_all">All</label>\n'
        f'      </div>\n'
        f'      <div class="tab-panel pano-fp">{footprints_panel}</div>\n'
        f'      <div class="tab-panel pano-sat">{satellite_panel}</div>\n'
        f'      <div class="tab-panel pano-recon">{recon_panel}</div>\n'
        f'      <div class="tab-panel pano-heights">{heights_panel}</div>\n'
        f'      <div class="tab-panel pano-all">{topdown_all_panel}</div>\n'
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
  figure.tabs.panospace > input:nth-of-type(4):checked ~ .tab-labels label:nth-of-type(4),
  figure.tabs.panospace > input:nth-of-type(5):checked ~ .tab-labels label:nth-of-type(5) {{
      background: #fff; border-color: #0a3070; color: #0a3070;
  }}
  figure.tabs.panospace > input:nth-of-type(1):checked ~ .pano-rgb {{ display: block; }}
  figure.tabs.panospace > input:nth-of-type(2):checked ~ .pano-seg {{ display: block; }}
  figure.tabs.panospace > input:nth-of-type(3):checked ~ .pano-depth {{ display: block; }}
  figure.tabs.panospace > input:nth-of-type(4):checked ~ .pano-scan {{ display: block; }}
  figure.tabs.panospace > input:nth-of-type(5):checked ~ .pano-all {{ display: block; }}
  /* Top-down tab group (5 tabs: footprints/satellite/reconstruction/heights/all). */
  figure.tabs.topdown > input:nth-of-type(1):checked ~ .tab-labels label:nth-of-type(1),
  figure.tabs.topdown > input:nth-of-type(2):checked ~ .tab-labels label:nth-of-type(2),
  figure.tabs.topdown > input:nth-of-type(3):checked ~ .tab-labels label:nth-of-type(3),
  figure.tabs.topdown > input:nth-of-type(4):checked ~ .tab-labels label:nth-of-type(4),
  figure.tabs.topdown > input:nth-of-type(5):checked ~ .tab-labels label:nth-of-type(5) {{
      background: #fff; border-color: #0a3070; color: #0a3070;
  }}
  figure.tabs.topdown > input:nth-of-type(1):checked ~ .pano-fp {{ display: block; }}
  figure.tabs.topdown > input:nth-of-type(2):checked ~ .pano-sat {{ display: block; }}
  figure.tabs.topdown > input:nth-of-type(3):checked ~ .pano-recon {{ display: block; }}
  figure.tabs.topdown > input:nth-of-type(4):checked ~ .pano-heights {{ display: block; }}
  figure.tabs.topdown > input:nth-of-type(5):checked ~ .pano-all {{ display: block; }}
  /* Center top-down square plots so they don't stretch to full width.
     The "All" stack overrides this to a 2-col grid below. */
  figure.tabs.topdown .tab-panel:not(.pano-all) img {{ max-width: 700px;
                                         display: block; margin: 0 auto; }}
  /* "All" stacked panels: each sub-image on its own row, small gap. */
  .stack-all > * {{ margin-bottom: 0.6em; }}
  .stack-td {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.6em; }}
  .stack-td img {{ max-width: 100%; }}
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
  Generated by city2stl.skyline.html_report (F-SKY15 pano-layered)
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

    neg_reason = getattr(sv, "negative_reason", None)
    if neg_reason:
        summary_rows.append(("Rejection reason", neg_reason))

    summary_html = "\n".join(
        f"    <tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>"
        for k, v in summary_rows
    )

    _rejection_banner = (
        f'<div style="background:#fff3cd;border-left:4px solid #e07b20;'
        f'border-radius:0 4px 4px 0;padding:0.7em 1.2em;margin:0.8em 0;color:#7d4600;">'
        f'<strong>Bad / negative seed</strong> — '
        f'{html.escape(neg_reason or "declared negative in config")}. '
        f'No per-view Street View analysis was run.</div>'
    ) if sv.is_negative else ""

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
{_rejection_banner}
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
  Generated by city2stl.skyline.html_report (F-SKY15)
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
    pano_results: list | None = None,
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
<h2>Pipeline timing</h2>
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
            corr = (f"{shift:+.0f}&deg;" if abs(shift) >= 1.0
                    else "<span class='small'>&mdash;</span>")
            slug = (pr.seed_name[5:] if pr.seed_name.startswith("seed_")
                    else pr.seed_name)
            srows.append(
                f"      <tr><td><a href=\"seed_{html.escape(slug)}.html\">"
                f"{html.escape(pr.seed_name)}</a></td>"
                f"<td>{corr}</td><td>{nseg}</td><td>{nm}</td>"
                f"<td>{mrate:.0f}%</td><td>{ncov}</td>"
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
   bearing shift applied (&mdash; = anchor already good). <b>Detected</b> =
   towers the splitter found. <b>Matched</b> = towers matched to OSM.
   <b>Match rate</b> = matched / detected (precision; ⚠ = fewer than 10
   segments detected — percentage is not statistically meaningful).
   <b>Coverage</b> = distinct OSM buildings matched.
   <b>Quality</b> = good requires ≥65% rate, ≥15 buildings, ≥10 segments;
   medium ≥50%/≥5/≥4; otherwise weak.</p>
<table>
  <thead><tr><th>seed</th><th>heading</th><th>detected</th>
    <th>matched</th><th>match rate</th><th>coverage</th>
    <th>quality</th></tr></thead>
  <tbody>
{chr(10).join(srows)}
  </tbody>
</table>
"""

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
</style>
</head>
<body>
<nav class="breadcrumb"><a href="../index.html">&#8592; All regions</a></nav>
<h1>{html.escape(title)}</h1>

<div class="stats">
  <div class="stat"><div class="num">{n_seeds}</div>seeds</div>
  <div class="stat"><div class="num">{n_buildings}</div>aggregated buildings</div>
</div>
{pano_summary_html}
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
  Generated by city2stl.skyline.html_report (F-SKY15)
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
                pano_rel_paths=pano_rels,
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

    index_html = render_region_index(
        region_name, seed_views, building_heights, step_timings=step_timings,
        screening_map_rel=screening_map_rel, pano_results=pano_results)
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")

    logger.info(
        "F-SKY15 HTML report written: %s (%d seeds, %d total views)",
        out_dir, len(views_by_seed), sum(len(v) for v in views_by_seed.values()),
    )
