"""city2stl.skyline.region_render - PDF report rendering for skyline.

Split out of region_pdf.py (F-CLEAN14, 2026-06-07). All matplotlib/PdfPages
page builders, minimap + overlay drawing, the region location map, negative-seed
view construction, and the _StepTimer. Pure rendering/diagnostics; consumes the
results produced by pano_registration. region_pdf re-imports these and
run_region_pdf_report calls them.
"""

# A3 split: façade re-exports all names from the _region_render/ subpackage.
from ._region_render._draw import *  # noqa: F401,F403
from ._region_render._pages import *  # noqa: F401,F403
