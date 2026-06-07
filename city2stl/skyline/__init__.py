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

Two-file architecture:
  - ``pipeline.py``   — CV/geometry primitives, pure functions, unit-tested
  - ``region_pdf.py`` — orchestration, I/O, seed selection, PDF rendering

Entry point: ``city2stl/skyline/scripts/08_region_skyline_pdf.py``.

This is a research module, NOT the production height pipeline (that's
``city2stl/height/``). See ``README.md`` and ``STATUS.md`` for current
strengths, weaknesses, and known failure modes.
"""

from .pipeline import aggregate_building_heights, detect_skyline_contour
