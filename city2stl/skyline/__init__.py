"""city2stl.skyline — Street-View → OSM building-height research module.

Estimates per-building heights for a city region by:
  1. Capturing 12-view spin panos at each seed location via Google Street
     View Static API.
  2. Running SegFormer-b0 (ADE20K) for building/sky/water segmentation.
  3. Jointly optimizing the pano-to-geographic heading offset across all
     spin views (width-weighted per-building IoU objective).
  4. Projecting OSM footprints into each registered view and extracting
     pinhole-y → height_m per building.
  5. Aggregating per-view estimates with outlier-seed downweighting.

Layout:
  - ``pipeline.py``          -> ``_core/``          CV/geometry primitives, step timer
                                (public façade for external callers; skyline's
                                own modules import from ``_core.*`` directly)
  - ``_pano/``               — per-seed capture, heading, detection
  - ``_region_render/``      — PDF drawing and pages
  - ``_report_plots/``       — HTML-report figures
  - ``region_pdf.py``        — region orchestration; ``region_config``,
    ``region_data`` and ``region_types`` hold its flags, I/O and dataclasses
  - ``height/``              — ML height stack and external height providers

Entry point: ``city2stl/skyline/scripts/08_region_skyline_pdf.py``.

This is the cross-view research pipeline. The ML height stack it builds on
lives alongside it in ``city2stl/skyline/height/`` (moved from
``city2stl/height/`` on 2026-06-07). See ``README.md`` and ``STATUS.md`` for
current strengths, weaknesses, and known failure modes.
"""

from ._core.height import aggregate_building_heights
from ._core.skyline import detect_skyline_contour

__all__ = ["aggregate_building_heights", "detect_skyline_contour"]
