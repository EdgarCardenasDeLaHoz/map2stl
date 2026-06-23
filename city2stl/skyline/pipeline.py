"""Skyline-based building-height estimation — pure CV / geometry primitives.

This module is the "math layer" of skyline. It is intentionally free of
HTTP I/O, matplotlib, and Street View concerns so its functions can be
unit-tested without API keys. The orchestration that ties these primitives
to a real region run lives in ``region_pdf.py``.

Public surface (in dependency order):

  Dataclasses
    Viewpoint                       — camera pose (lat/lon/heading/pitch/fov)
    BuildingRecord                  — OSM building + tagged/derived height proxy
    CapturedView                    — RGB image + Viewpoint
    RegisteredBuildingEstimate      — per-view per-building height estimate

  SegFormer integration
    _neural_sky_and_building_masks  — anchored-LRU-cached forward pass
    _neural_water_mask              — water-class accessor (same cache)

  Skyline detection
    detect_skyline_contour          — top-of-non-sky row per column
    detect_building_silhouettes     — contour-peak silhouettes (per-tower)
    detect_buildings_from_mask      — connected-component silhouettes from mask
    compute_building_band           — vertical band where buildings exist
    _merge_silhouette_sources       — IoU-based de-dup of two silhouette lists

  OSM projection + registration
    _project_building               — single-building rectilinear projection
    _project_all_buildings_vectorized — batched numpy projection (the perf win)
    _projected_building_x_ranges    — per-building (x_min, x_max) for IoU scoring
    register_view_to_osm            — find pano-to-geo heading offset per view
    _score_offset_semantic_iou      — the offset objective
                                       (per-building IoU − water − miss penalty)

  Matching + culling
    _cull_occluded_projections      — keep only the closest-per-bin building
    osm_anchor_silhouettes          — F-SKY2 anchored split of merged segments
    match_segments_to_buildings     — interval-IoU + width-ratio scorer
    (F-SKY3 / osm_marker_voronoi_silhouettes removed 2026-05-18 after a
     measured MAE regression; see docs/plans/F-SKY3-osm-marker-instances.md)
    osm_sam_instance_silhouettes    — F-SKY5 MobileSAM instance head
                                       (optional; no-op if MobileSAM not installed)

  Pano helpers
    stitch_pano_views               — stitch RGB strip from spin views
    stitch_pano_masks               — stitch per-view masks (faster, seam-free)
    project_buildings_to_pano       — bearing-to-column projection for pano

  Height extraction
    estimate_heights_from_registration — per-view pinhole-y → height_m
    aggregate_building_heights      — per-building median + outlier downweighting
    _floor_period_for_building      — F-SKY1 facade-period diagnostic
                                       (OSM-independent height + distance check)

See STATUS.md for what currently works and what doesn't. Hard dependency on
SegFormer-b0 (ADE20K) via the `transformers` library — the registration
objective and per-building mask sampling both rely on it; no fallback path
is intended to be functional without the model.
"""

# A1 split: this module is now a thin façade. The implementation lives in the
# _core/ subpackage (types, util, segmentation, projection, skyline, pano,
# registration, height). Every name that callers imported from
# ``city2stl.skyline.pipeline`` is re-exported here, including the private
# helpers other skyline modules and tests rely on.
from ._core.types import *          # noqa: F401,F403
from ._core.util import *           # noqa: F401,F403
from ._core.segmentation import *   # noqa: F401,F403
from ._core.projection import *     # noqa: F401,F403
from ._core.skyline import *        # noqa: F401,F403
from ._core.pano import *           # noqa: F401,F403
from ._core.registration import *   # noqa: F401,F403
from ._core.height import *         # noqa: F401,F403
