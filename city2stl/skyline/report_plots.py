"""city2stl.skyline.report_plots - matplotlib/PIL PNG renderers for the HTML report.

Split out of ``html_report.py`` (F-CLEAN14, 2026-06-07). Each function builds
a figure/image and writes a PNG (returning bool/None); no HTML assembly lives
here. ``html_report.py`` imports these back and stitches their output into the
static pages. matplotlib/numpy stay lazily imported inside the functions.
"""

# split into the _report_plots/ subpackage; façade re-exports all names.
from ._report_plots._plot_utils import *  # noqa: F401,F403
from ._report_plots._view_plots import *  # noqa: F401,F403
from ._report_plots._pano_plots import *  # noqa: F401,F403
