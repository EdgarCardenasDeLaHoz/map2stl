"""city2stl.skyline.region_config - shared skyline feature flags + palette.

Split out of region_pdf.py (F-CLEAN14, 2026-06-07) so region_render and
pano_registration can read the F-SKY env toggles without importing the
orchestrator (which would be circular). region_pdf re-imports these.
"""

import os


# F-SKY12: enable Depth Anything V2 verifier on each view. Off by default
# while Phase A is validated. Set env var SKYLINE_CV_F_SKY12=1 to turn on.
_F_SKY12_ENABLED = os.environ.get("SKYLINE_CV_F_SKY12", "0").strip().lower() in (
    "1", "true", "yes", "on"
)

# F-SKY13: draw OSM coastline + 1 km consideration window on per-view minimap.
# OSM is the primary coastline ground truth (see feedback memory
# feedback_satellite_coastline_hsv_unreliable). Default ON; set
# SKYLINE_CV_F_SKY13=0 to disable the overlay.
_F_SKY13_ENABLED = os.environ.get("SKYLINE_CV_F_SKY13", "1").strip().lower() in (
    "1", "true", "yes", "on"
)
_F_SKY13_RADIUS_M = 1000.0

# F-SKY13 Phase C: OSM-primary registration — make OSM the source for the
# heading-sweep keypoints (was satellite HSV). The sweep's peak then IS the
# pano↔OSM IoU directly, no separate Phase B verifier step needed. Default
# OFF until validated on Cartagena; opt in with SKYLINE_CV_PHASE_C=1.
_PHASE_C_ENABLED = os.environ.get("SKYLINE_CV_PHASE_C", "0").strip().lower() in (
    "1", "true", "yes", "on"
)
# Optional: also render the ESRI satellite image as the minimap background.
# Costs one network fetch per seed on first run (then disk-cached). Off by
# default; enable with SKYLINE_CV_F_SKY13_SAT_BG=1. Independent of the
# F-SKY11.1 satellite HSV path — purely visual, no algorithmic role.
_F_SKY13_SAT_BG_ENABLED = os.environ.get(
    "SKYLINE_CV_F_SKY13_SAT_BG", "0"
).strip().lower() in ("1", "true", "yes", "on")

# F-SKY5: MobileSAM instance head — splits merged blobs using SAM point prompts
# sourced from OSM centroids. Fires only on segments with ≥ 2 contained OSM
# markers (the cases F-SKY2 gap-splitting does not resolve). Default OFF; set
# SKYLINE_CV_F_SKY5=1 to enable. Requires MobileSAM installed + checkpoint at
# MOBILESAM_CHECKPOINT_PATH (default ~/.cache/mobile_sam/vit_t.pth).
_F_SKY5_ENABLED = os.environ.get("SKYLINE_CV_F_SKY5", "0").strip().lower() in (
    "1", "true", "yes", "on"
)

# F-SKY11.1 Phase B: wire the pano-recovered heading offset into the joint
# anchor optimizer as its seed when the peak is sharp (sigma ≤ 0.10, peak >
# 0.40). Default OFF until validated on a second region (Miami). Set
# SKYLINE_CV_F_SKY11_1=1 to opt in. Falls back gracefully when no coastline
# is visible (flat peak → quality gate fails → coarse sweep runs as before).
_F_SKY11_1_ENABLED = os.environ.get("SKYLINE_CV_F_SKY11_1", "0").strip().lower() in (
    "1", "true", "yes", "on"
)

# F-SKY1: floor-strip facade periodicity. Detects the per-floor horizontal
# banding in a building's mask region (1D autocorrelation on a row-mean
# intensity profile), reports the dominant pixel period, and back-derives
# floor count + an OSM-independent height/distance estimate. This is the
# "striations ≈ 1 storey" signal — a distance/height cue that does NOT
# depend on the depth model (so it sidesteps DA2's far-distance
# saturation). Default ON now (was diagnostic-only); set
# SKYLINE_CV_F_SKY1=0 to disable. Cost: one autocorrelation per building
# per view.
_F_SKY1_ENABLED = os.environ.get("SKYLINE_CV_F_SKY1", "1").strip().lower() in (
    "1", "true", "yes", "on"
)


# Flickr REST API key for web skyline image seeds (F-WEB1).
# Free at https://www.flickr.com/services/api/keys/.
# When absent, Wikimedia Commons is tried as a keyless fallback.
FLICKR_API_KEY = os.environ.get("FLICKR_API_KEY", "").strip()

_SEGMENT_PALETTE = (
    (255, 80, 80),
    (80, 200, 255),
    (180, 255, 80),
    (255, 180, 60),
    (200, 120, 255),
    (255, 240, 100),
    (120, 255, 200),
    (255, 120, 200),
)
