"""city2stl.skyline.pano_registration - per-seed multi-view registration.

Split out of region_pdf.py (F-CLEAN14, 2026-06-07). The core per-seed loop:
capture the 12-view spin, recover pano heading + joint anchor offset, per-view
match, cross-view smoothing, the 360 pano stitch + splitters, and the
_seed_multiview_registration orchestrator. Calls region_render for the
overlay/negative-seed helpers (one-directional; render does not call back).
region_pdf re-imports these.
"""

# A2 split: thin façade. Implementation in the _pano/ subpackage; all names that
# region_pdf imports are re-exported here.
from ._pano.capture import *       # noqa: F401,F403
from ._pano.heading import *       # noqa: F401,F403
from ._pano.detect import *        # noqa: F401,F403
from ._pano.orchestrator import *  # noqa: F401,F403
