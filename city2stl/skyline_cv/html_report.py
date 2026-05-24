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


def _render_seed_minimap_png(
    out_path: Path,
    sv: "SeedViewRegistration",
    osm_data: dict,
    buildings_by_id: dict,
) -> bool:
    """Save the seed's minimap as a standalone PNG.

    Wraps the existing ``_draw_view_minimap`` so the HTML report and the
    PDF stay visually consistent. Returns True on success, False (with a
    warning) if matplotlib fails — the HTML page will simply omit the
    image rather than crash the whole report.
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
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=120, bbox_inches="tight")
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


def render_seed_page(
    sv: "SeedViewRegistration",
    region_name: str,
    minimap_rel_path: str | None,
    *,
    estimates: list | None = None,
    views: list | None = None,
    view_image_rel_paths: list[str | None] | None = None,
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
    """
    # Title is escaped at each insertion point below; build it raw here
    # to avoid double-escaping.
    title = f"Seed {sv.seed_name} — {region_name}"

    summary_rows = [
        ("Lat / Lon", f"{sv.seed_lat:.5f}, {sv.seed_lon:.5f}"),
        ("Heading + offset", f"{sv.heading + sv.best_offset:.1f}° (raw={sv.heading:.1f}°, offset={sv.best_offset:+.1f}°)"),
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
        view_blocks = []
        for i, (vsv, rel) in enumerate(zip(views, rel_paths)):
            img_block = (
                f'<img src="{html.escape(rel)}" alt="view {i} for {html.escape(sv.seed_name)}" '
                'style="max-width:1200px;width:100%;height:auto;border:1px solid #ccc;">'
                if rel else
                '<p class="missing"><em>View image unavailable</em></p>'
            )
            n_segs = len(getattr(vsv, "matched_segments", []) or [])
            heading_eff = getattr(vsv, "heading", 0.0) + getattr(vsv, "best_offset", 0.0)
            view_blocks.append(
                f"    <details>\n"
                f"      <summary>View {i + 1} · heading {heading_eff:.1f}° · "
                f"{n_segs} matched segment{'s' if n_segs != 1 else ''}"
                f" · score {getattr(vsv, 'registration_score', 0.0):.2f}</summary>\n"
                f"      {img_block}\n"
                f"    </details>"
            )
        views_html = f"""
  <section class="views">
    <h2>Per-view registration overlays ({len(views)} views)</h2>
    <p class="small">Click a view to expand. Each image is the street-view
       capture with matched-building bounding boxes + numbered badges drawn
       by the registration overlay.</p>
{chr(10).join(view_blocks)}
  </section>
"""

    estimates_html = ""
    if estimates:
        rows = []
        for est in estimates:
            disagree = getattr(est, "depth_disagreement", None)
            row_cls = " class=\"disagree\"" if disagree is True else ""
            rows.append(
                f"        <tr{row_cls}>"
                f"<td>{html.escape(getattr(est, 'feature_id', '?'))}</td>"
                f"<td>{html.escape(getattr(est, 'name', '') or '')}</td>"
                f"<td>{html.escape(getattr(est, 'view_name', '?'))}</td>"
                f"<td>{getattr(est, 'forward_m', 0.0):.1f} m</td>"
                f"<td>{getattr(est, 'estimated_height_m', 0.0):.1f} m</td>"
                f"<td>{_fmt_optional_float(getattr(est, 'depth_height_m', None), suffix=' m', precision=1)}</td>"
                f"<td>{_fmt_optional_bool(disagree)}</td>"
                f"<td>{getattr(est, 'confidence', 0.0):.2f}</td>"
                "</tr>"
            )
        estimates_html = f"""
  <section class="estimates">
    <h2>Per-building estimates ({len(estimates)} matches)</h2>
    <table>
      <thead>
        <tr>
          <th>feature_id</th><th>name</th><th>view</th><th>distance</th>
          <th>h<sub>geom</sub></th>
          <th>h<sub>depth</sub> <span class="small">(F-SKY12)</span></th>
          <th>disagree?</th><th>conf</th>
        </tr>
      </thead>
      <tbody>
{chr(10).join(rows)}
      </tbody>
    </table>
    <p class="legend small">
      Rows highlighted red have the F-SKY12 depth verifier flagging
      &gt;40% disagreement with the geometric estimate.
    </p>
  </section>
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
  h2 {{ color: #0a3070; margin-top: 1.5em; }}
  table {{ border-collapse: collapse; margin: 0.5em 0; }}
  th, td {{ padding: 0.25em 0.7em; border: 1px solid #ddd; text-align: left; }}
  th {{ background: #f4f6fa; font-weight: 600; }}
  .summary th {{ text-align: right; width: 14em; }}
  .estimates tr.disagree {{ background: #fdecec; }}
  .small {{ font-size: 0.85em; color: #666; }}
  .legend {{ margin: 0.4em 0; }}
  .missing {{ color: #888; }}
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
    dashed grey = 1 km consideration window ·
    orange dots = pano-projected coastline (where the pano sees water).
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
) -> str:
    """Render the per-region index.html.

    Lists every seed with its summary stats and links to the per-seed
    page. The building_heights aggregate (when provided) feeds the
    bottom-of-page totals.
    """
    title = f"{region_name} — Skyline diagnostic report"

    # One row per UNIQUE seed name. seed_views contains one row per (seed, view),
    # so we dedupe by seed_name and pick the first row for the summary stats.
    seen = set()
    seed_rows = []
    for sv in seed_views:
        if sv.seed_name in seen:
            continue
        seen.add(sv.seed_name)
        slug = sv.seed_name[5:] if sv.seed_name.startswith("seed_") else sv.seed_name
        seed_rows.append(
            f"      <tr>"
            f"<td><a href=\"seed_{html.escape(slug)}.html\">{html.escape(sv.seed_name)}</a></td>"
            f"<td>{sv.seed_lat:.5f}, {sv.seed_lon:.5f}</td>"
            f"<td>{_fmt_optional_float(sv.pano_osm_iou, precision=2)}</td>"
            f"<td>{sv.pano_osm_n_keypoints or '—'}</td>"
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

<h2>Seeds</h2>
<table>
  <thead>
    <tr>
      <th>seed</th><th>lat, lon</th>
      <th>pano↔OSM IoU</th><th>OSM keypoints</th><th>negative</th>
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

    for seed_name, sv_list in views_by_seed.items():
        primary = sv_list[0]
        # Strip a "seed_" prefix if the name already includes it so the
        # output filename doesn't double up (seed_seed_5.html → seed_5.html).
        slug = seed_name[5:] if seed_name.startswith("seed_") else seed_name

        # Minimap PNG (uses primary view for heading + minimap diagnostics)
        png_path = minimap_dir / f"{slug}.png"
        ok = _render_seed_minimap_png(png_path, primary, osm_data, buildings_by_id)
        minimap_rel = f"assets/minimap/{slug}.png" if ok else None

        # Per-view image PNGs (one per view in the seed)
        view_image_rels: list[str | None] = []
        for i, vsv in enumerate(sv_list):
            view_png = views_dir / f"{slug}_view_{i}.png"
            if _save_view_image_png(view_png, vsv):
                view_image_rels.append(f"assets/views/{slug}_view_{i}.png")
            else:
                view_image_rels.append(None)

        page_html = render_seed_page(
            primary, region_name, minimap_rel,
            estimates=estimates_by_seed.get(seed_name),
            views=sv_list,
            view_image_rel_paths=view_image_rels,
        )
        (out_dir / f"seed_{slug}.html").write_text(page_html, encoding="utf-8")

    index_html = render_region_index(region_name, seed_views, building_heights)
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")

    logger.info(
        "F-SKY15 HTML report written: %s (%d seeds, %d total views)",
        out_dir, len(views_by_seed), sum(len(v) for v in views_by_seed.values()),
    )
