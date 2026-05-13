"""city2stl.skyline_cv — experimental skyline-to-height research pipeline.

This package is intentionally separate from the production height stack.
It stages a Cartagena baseline that:
  1. resolves a known viewpoint,
  2. fetches OSM buildings,
  3. captures multiple Google Street View images,
  4. registers the skyline against OSM footprints, and
  5. fuses per-view height estimates.
"""

from .pipeline import aggregate_building_heights, detect_skyline_contour
