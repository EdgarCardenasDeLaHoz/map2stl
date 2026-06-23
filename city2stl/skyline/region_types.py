"""city2stl.skyline.region_types - frozen dataclasses shared across the
skyline orchestration modules.

Split out of ``region_pdf.py`` (F-CLEAN14, 2026-06-07) so region_data,
streetview_io, seed_selection, pano_registration and region_render can all
import the shared result/structure types from one place without importing the
heavyweight orchestrator. ``region_pdf`` re-exports these names, so
``from city2stl.skyline.region_pdf import SeedViewRegistration`` still works.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np  # noqa: F401  (referenced by lazy string annotations below)


@dataclass(frozen=True)
class RegionBBox:
    name: str
    north: float
    south: float
    east: float
    west: float


@dataclass(frozen=True)
class SkylinePoint:
    name: str
    lat: float
    lon: float
    heading: float
    source: str
    score: float
    fov: float = 80.0
    pitch: float = 0.0
    # if set, requests target this exact pano (no snapping)
    pano_id: str | None = None


@dataclass(frozen=True)
class StitchedPanoResult:
    """One pano-level pipeline result per seed — the alternative path to the
    per-view aggregation. Used to build the comparison page in the PDF."""
    seed_name: str
    seed_lat: float
    seed_lon: float
    pano_image: np.ndarray
    band_y: tuple[int, int] | None
    matched_segments: list  # list[dict]
    n_segments: int
    n_matched: int
    n_buildings_in_view: int
    anchor_offset_deg: float = 0.0  # joint-optimized pano-to-geo offset
    # Per-column compass heading (matches pano_image.shape[1]). Used by the
    # PDF page to overlay yellow heading ticks so the user can verify the
    # column-to-heading mapping visually.
    headings_per_col: "np.ndarray | None" = None
    # Per-class pano-stitched masks (same view set + sort as
    # ``pano_image`` so the column geometry agrees). Populated by
    # ``_build_and_detect_pano`` for the HTML report's SegFormer-mask
    # pano layer; ``None`` when unavailable.
    pano_building_mask: "np.ndarray | None" = None
    pano_water_mask: "np.ndarray | None" = None
    pano_sky_mask: "np.ndarray | None" = None
    pano_vegetation_mask: "np.ndarray | None" = None
    # F-SKY24: pano-wide depth map (Depth Anything V2 inverse depth,
    # [0, 1] scaled). Computed once in ``_build_and_detect_pano`` so the
    # splitter + downstream renderers (depth pano, reconstruction polar
    # plot) all share one inference instead of recomputing per-tab.
    pano_depth: "np.ndarray | None" = None
    # OSM-anchored depth→distance scale: median(osm_dist / sqrt(d_inv))
    # over matched towers, so ``dist_m = sqrt(1 - depth_rel) * depth_scale``
    # is calibrated to real metres. Computed once in the pipeline and
    # shared by BOTH the Reconstruction polar plot and the Distance scan
    # so they agree (previously the scan used a hardcoded 1450 while the
    # recon used this anchor, giving the ~600 vs ~1000 m mismatch).
    # Falls back to 1450.0 when no OSM anchor is derivable.
    depth_scale: float = 1450.0
    # F-SKY25 horizon-pitch geometric distance model:
    #   dist_m = geom_K / (base_row - geom_horizon_row)
    # Fitted (2 params) from matched towers' base rows vs OSM distance.
    # Unlike the saturated depth value, the base-row position tracks real
    # near/far variation, so this gives a non-flat distance estimate for
    # every column. ``None`` when the fit wasn't possible (<3 towers or
    # degenerate base-row spread).
    geom_K: float | None = None
    geom_horizon_row: float | None = None
    # Bearing-recovery shift actually APPLIED to pano_headings (deg); 0.0
    # when the gate skipped (anchor kept). Surfaced in the index summary.
    bearing_shift_deg: float = 0.0


@dataclass(frozen=True)
class SeedViewRegistration:
    seed_name: str
    seed_lat: float
    seed_lon: float
    heading: float
    fov: float
    registration_score: float
    best_offset: float
    estimates_count: int
    image: np.ndarray
    matched_segments: list  # list[dict] from match_segments_to_buildings
    is_aerial: bool = False
    iou: float = 0.0  # semantic mIoU at best offset (heading-quality signal)
    # Vertical building band (y_top, y_bot) computed from the SegFormer
    # building mask. Used to vertically crop the displayed image so views
    # with lots of water/sky don't waste page area. None = no crop, show
    # full frame.
    band_y: tuple[int, int] | None = None
    # True for seeds declared in sites/<region>.json `negative_seeds`. The
    # view is still rendered (so the user can verify the pipeline isn't
    # producing spurious estimates) but no heights are aggregated.
    is_negative: bool = False
    # Why this seed was marked bad (e.g. "low building coverage 3%") when
    # auto-rejected by the pano coverage screen; None for config-declared
    # negatives. Surfaced in the report so bad panos are labelled.
    negative_reason: str | None = None
    # F-SKY4: the SegFormer building mask for this view. Persisted into the
    # registration so the PDF renderer can overlay it without depending on
    # the bounded in-memory neural cache (which evicts after 16 entries and
    # otherwise misses by PDF-render time on multi-seed runs).
    building_mask: "np.ndarray | None" = None
    # Additional SegFormer class masks captured at the same time as
    # ``building_mask``. Free piggy-back on the already-cached label_map
    # forward pass. Used by the HTML report to render a "all
    # segmentations on grayscale" diagnostic image so reviewers can see
    # what every class label looked like, not just buildings.
    sky_mask: "np.ndarray | None" = None
    water_mask: "np.ndarray | None" = None
    vegetation_mask: "np.ndarray | None" = None
    # Raw RGB frame (pre-overlay) used as the grayscale base for the
    # multi-class mask diagnostic. Kept separately from ``image`` because
    # ``image`` is overwritten with the registration-overlay drawing.
    raw_image: "np.ndarray | None" = None
    # F-SKY13 Phase B: pano↔OSM-coastline registration score at the
    # recovered (or manually overridden) heading offset. Per-seed, not
    # per-view — populated only when pano-recovery is enabled and the
    # seed has OSM coastline within the 1 km window. None elsewhere.
    pano_osm_iou: float | None = None
    pano_osm_n_keypoints: int | None = None
    # F-SKY11.1 pano-coastline heading recovery diagnostics. The sweep
    # over candidate heading offsets returns the argmax (recovered_offset),
    # the peak score at that offset, and the score-curve sigma (a
    # confidence proxy — flat curves get high sigma). These already drive
    # the drive_anchor decision; surfacing them lets the HTML reader see
    # WHY a seed's registration succeeded or failed.
    pano_recovered_offset_deg: float | None = None
    pano_recovered_peak: float | None = None
    pano_recovered_sigma: float | None = None
    pano_water_frac: float | None = None
    # F-SKY13 Phase B: pano-derived coastline projected back to lon/lat
    # (one point per pano column where water was detected, sampled at a
    # coarse stride). Drawn as a dashed orange polyline on the minimap so
    # the user can see where the pano "thinks" the coast is. None when
    # pano-recovery is disabled or there's no water in the pano.
    pano_projected_coastline: "list[tuple[float, float]] | None" = None
    # F-SKY18: depth-snapped pano vegetation base points (lon/lat). Second
    # bearing-landmark class alongside coastline; drawn as green dots on the
    # minimap and (Phase 3) fed into heading registration.
    pano_projected_vegetation: "list[tuple[float, float]] | None" = None
    # F-SKY15: per-view list of ``RegisteredBuildingEstimate`` records
    # (with F-SKY12 depth fields when SKYLINE_CV_F_SKY12=1). Persisted so
    # the HTML diagnostic report can render the depth diagnostics today
    # without waiting for the PDF rendering path. None elsewhere.
    view_estimates: "list | None" = None

