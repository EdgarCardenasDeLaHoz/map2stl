"""Part of ``city2stl.skyline.html_report`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .region_types import SeedViewRegistration
# PNG / plot renderers live in the _report_plots/ subpackage (F-CLEAN14,
# 2026-06-07); the HTML-assembly functions below call them unchanged.
from ._report_plots._plot_utils import (  # noqa: E402
    _fmt_optional_bool,
)
from .html_report_tables import _segments_table_html
from .report_render import (  # noqa: E402
    BREADCRUMB_CSS,
    THEME_CSS,
    body_h1_css,
    kv_rows,
    page,
)


def render_seed_pano_page(
    primary_sv: SeedViewRegistration,
    sv_list: list,
    pano_result,
    region_name: str,
    minimap_rel_path: str | None,
    *,
    pano_rel_paths: dict | None = None,
    published_by_id: dict | None = None,
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
    summary_html = kv_rows(summary_rows)
    segments_html = _segments_table_html(primary_sv, pano_result, published_by_id)

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

    css = f"""{body_h1_css(1500)}  h2 {{ color: #0a3070; margin-top: 1.5em; }}
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
{BREADCRUMB_CSS}  img {{ cursor: zoom-in; }}
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
{THEME_CSS}"""
    body = f"""<nav class="breadcrumb"><a href="index.html">← {html.escape(region_name)} index</a></nav>
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
{segments_html}

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
</script>"""
    return page(html.escape(title), css, body)


def render_seed_page(
    sv: SeedViewRegistration,
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

    summary_html = kv_rows(summary_rows)

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
                    depth_paths, recon_paths, strict=False)):
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

    css = f"""{body_h1_css(1100)}  h2 {{ color: #0a3070; margin-top: 1.5em; }}
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
{BREADCRUMB_CSS}{THEME_CSS}"""
    body = f"""<nav class="breadcrumb"><a href="index.html">← {html.escape(region_name)} index</a></nav>
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
</footer>"""
    return page(html.escape(title), css, body)
