"""city2stl.skyline.region_config - shared skyline constants + palette.

Split out of region_pdf.py (F-CLEAN14, 2026-06-07) so region_render and
pano_registration can share them without importing the orchestrator (which
would be circular). The feature flags moved to ``config.SkylineConfig``.
"""

import os

# The F-SKY feature flags that lived here as import-time env reads are fields of
# ``config.SkylineConfig`` since F-UNIFY Phase 2 (2026-10-31), read at call time through
# ``config.current()``; the env vars (SKYLINE_CV_F_SKY1/5/11_1/12/13/13_SAT_BG,
# SKYLINE_CV_PHASE_C) still override. What each one does:
# - f_sky12 (default off): Depth Anything V2 verifier on each view.
# - f_sky13 (on): OSM coastline + 1 km consideration window on the per-view minimap (OSM is
#   the coastline ground truth; feedback_satellite_coastline_hsv_unreliable).
# - phase_c (off): OSM-primary registration, OSM as the heading-sweep keypoint source.
# - f_sky13_sat_bg (off): ESRI satellite image as the minimap background (one fetch per seed).
# - f_sky5 (on since 2026-10-06): MobileSAM instance head that splits merged blobs with OSM
#   centroid prompts; a no-op without MobileSAM or its checkpoint.
# - f_sky11_1 (off): pano-recovered heading offset seeds the joint anchor optimizer when sharp.
# - f_sky1 (on): floor-strip facade periodicity (one autocorrelation per building per view).
_F_SKY13_RADIUS_M = 1000.0


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
