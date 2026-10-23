"""``city_model._open_pinches``: GEOS returned an empty polygon for a negative buffer of a valid one.

The Palacio de Carlos V (``relation/2640220``, an outer ring with a round courtyard) was snapped
to the 0.01 mm grid and then vanished from the Granada model ("missing" in the Alhambra
reference landmarks) because ``buffer(-SNAP_MM)`` of this exact polygon is empty.
"""

import numpy as np
import shapely
from shapely import wkt

from city2stl.city_model import SNAP_MM, _open_pinches

PALACIO = wkt.loads(
    "POLYGON ((126.69 166.13, 101.85 170.95, 106.61 195.45, 131.45 190.62, 126.69 166.13), "
    "(112.49 185.71, 111.29 184.16, 110.67 182.49, 110.56 180.34, 111.11 178.46, 112.21 176.83, "
    "113.76 175.63, 115.68 174.95, 117.58 174.9, 119.46 175.44, 121.08 176.55, 122.29 178.1, "
    "122.99 180.07, 123.02 181.91, 122.47 183.8, 121.37 185.42, 119.82 186.63, 117.97 187.3, "
    "116.01 187.36, 114.12 186.81, 112.49 185.71))")


def test_open_pinches_keeps_a_polygon_geos_buffers_to_nothing():
    plain = shapely.buffer(shapely.buffer(PALACIO, -SNAP_MM, join_style="mitre"), SNAP_MM,
                           join_style="mitre")
    out = _open_pinches(np.array([PALACIO], dtype=object))
    assert shapely.area(out[0]) > 0.9 * PALACIO.area
    if shapely.area(plain) > 0.9 * PALACIO.area:      # a GEOS that no longer has the bug
        assert shapely.area(out[0]) == shapely.area(plain)


def test_open_pinches_opens_a_touching_hole():
    # A hole touching its outline at one point: opened, area change far below print resolution.
    p = wkt.loads("POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0), (0 5, 3 4, 3 6, 0 5))")
    out = _open_pinches(np.array([p], dtype=object))
    assert abs(shapely.area(out[0]) - p.area) < 0.5
