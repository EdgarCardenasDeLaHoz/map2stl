"""skyline._pano.elevated — drone seeds measured footprint first (F-DET6) in the region report.

The street-level path assumes a camera 1.7 m up (``_register_views``). A drone Photo Sphere
hovers 40-100 m over the bay: its roofs sit below the horizon and every per-view height is
wrong by construction (2026-10-05 review). Seeds listed in the site's ``elevated_seeds`` go
through :func:`measure_elevated_seed` instead of heading recovery, anchor, registration and the
pano path:

1. the stitched pano and its ADE20K labels from the views already captured;
2. heading offset, camera height and pitch from the waterline, then the camera position:
   waterline over +-600 m, parks and streets nearby (``footprint_detect.fit_camera_position``;
   seed_4's recorded position was ~360 m off), then the bearing from the OSM-tagged towers'
   outline (``footprint_detect.refine_on_outline``: the seeds were 4-6 deg off); a camera whose
   tower outline still misfits, or that the outline drags to the edge of its search, is not a
   drone over water (:func:`outline_gate`: boat decks and street panos at sea level);
3. Depth Anything V2 and MobileSAM building instances (``building_instances``) on the pano,
   and every OSM footprint in view measured (``footprint_detect.measure_footprints``: a run
   never stops inside one instance; 2026-10-06 on Cartagena seeds 1/4/5, tagged readings within
   25 % of their tag 50/67/50 % -> 80/67/100 %), then a roof fit for the footprints without a
   trusted reading (:func:`measure_waterline`).

The report keeps working unchanged: each seed gives a ``StitchedPanoResult`` (pano page and
boxes) and frames-only view rows, and after all seeds :func:`elevated_estimates` fuses the
seeds (``footprint_detect.fuse_heights``: the nearer, better-seen view wins a disagreement) and
hands the kept measurements to ``aggregate_building_heights`` as ordinary estimates.
"""

from __future__ import annotations

import logging

from .elevated_behind import (  # noqa: F401
    BEHIND_DIST_RATIO,
    BEHIND_FILLS,
    BEHIND_INST_DIFF,
    BEHIND_TOL_PX,
    CREDIT_MIN_M,
    CREDIT_TOL,
    SAT_LOW_CONF,
    SAT_LOW_FACTOR,
    SAT_LOW_FLOOR_M,
    SAT_LOW_M,
    SAT_LOW_METHODS,
    SAT_LOW_PEAK_CONF,
    SAT_MIN_M,
    _inst_change,
    _lean_peak_conf,
    _mark_not_owner,
    _sat_fields,
    _sat_max,
    _tag_behind,
    _untrust_behind,
    behind_map,
    not_owned,
    recredited,
    seed_ownership,
    tower_behind,
)
from .elevated_floors import (  # noqa: F401
    FLOORS_IN_FUSION,
    FLOORS_MIN_PX,
    FLOORS_PUBLISH_ALONE,
    FLOORS_RANGE_M,
    FLOORS_SAME_SEED_AGREE,
    ElevatedEstimates,
    _split_satellite,
    drop_contradicted_floors,
    elevated_estimates,
    floor_entries,
    floors_info,
    seed_floors,
)
from .elevated_ground import (  # noqa: F401
    _LAYER_CACHE,
    DEPTH_CACHE_VERSION,
    FLOORS_CACHE_VERSION,
    GROUND_HALF_M,
    GROUND_RES_M,
    HEIGHT_FACTORS,
    HIRES_FOV_DEG,
    HIRES_PITCHES_DEG,
    HIRES_SIZE_PX,
    INSTANCES_CACHE_VERSION,
    LABELS_CACHE_VERSION,
    MAX_OUTLINE_MISFIT_DEG,
    MAX_OUTLINE_MOVE_M,
    MEASURED_CACHE_VERSION,
    MIN_ELEVATED_H_M,
    OVERHEAD_FROM_H_M,
    OVERHEAD_H_GRID_M,
    OVERHEAD_MIN_GROUND,
    PANO_CACHE_DIR,
    POSE_CACHE_VERSION,
    ROAD_WIDTH_M,
    ROOF_TRUST,
    ROOF_TRUST_MAX_DIST_M,
    SKY_SEEN_TRUST,
    STITCH_CACHE_VERSION,
    _bbox_key,
    _cached_layers,
    _mark_tower_behind,
    _towers,
    _waterline_camera,
    estimate_confidence,
    ground_layers,
    outline_gate,
    overhead_pose,
    trusted,
    untrusted_reason,
)
from .elevated_measure import (  # noqa: F401
    FILL_MAX_DIST_M,
    ROOF_FILL_WEIGHT,
    measure_elevated_seed,
    measure_waterline,
)
from .elevated_pano import (  # noqa: F401
    ElevatedSeed,
    _cached_labels,
    _cached_level_pano,
    _camera_axes,
    _label_masks,
    _ring,
    _tilt_rotation,
    _view_rows,
    capture_sphere_pano,
    footprints_from_records,
    horizon_tilt,
    level_pano,
    level_pano_from_spin,
    pano_result,
    sphere_pano,
)

logger = logging.getLogger(__name__)

__all__ = ["ElevatedSeed", "ElevatedEstimates", "measure_elevated_seed", "elevated_estimates",
           "seed_floors", "floors_info", "drop_contradicted_floors", "trusted", "pano_result",
           "seed_ownership", "not_owned", "recredited",
           "footprints_from_records", "ground_layers", "MIN_ELEVATED_H_M", "measure_waterline",
           "outline_gate"]
