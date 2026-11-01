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

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
# PNG / plot renderers live in the _report_plots/ subpackage (F-CLEAN14,
# 2026-06-07); the HTML-assembly functions below call them unchanged.

from .html_report_region import (  # noqa: F401
    render_region_index,
    write_region_report,
)
from .html_report_seed import (  # noqa: F401
    render_seed_page,
    render_seed_pano_page,
)
from .html_report_tables import (  # noqa: F401
    MISMATCH_OVERLAP,
    MISMATCH_SNAP_M,
    _anchor_dev_cell,
    _fmt_satellite,
    _fmt_seed_values,
    _heights_table_html,
    _is_footprint_first,
    _mismatch_cause,
    _reading_status,
    _seed_reading_stats,
    _segments_table_html,
    _tags_by_id,
    _tier_summary_html,
    _towers_table_html,
)

logger = logging.getLogger(__name__)
