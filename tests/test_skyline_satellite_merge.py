"""Satellite (MS Buildings) records get metric areas (audit 2026-10-05)."""

import pytest
from shapely.geometry import MultiPolygon, box

from city2stl.skyline.satellite_footprints import merge_satellite_into_osm

# ~40 m x 30 m at 25.77 N: 0.0004 deg lon x 0.00027 deg lat.
_SQUARE = box(-80.1900, 25.7700, -80.1896, 25.77027)


def _merged(geom):
    out = merge_satellite_into_osm([], [{"geometry": geom}], register_to_osm=False)
    assert len(out) == 1 and out[0].feature_id.startswith("ms_")
    return out[0]


def test_area_is_square_metres_not_degrees():
    assert _merged(_SQUARE).area_m2 == pytest.approx(40.1 * 29.9, rel=0.05)


def test_multipolygon_area_sums_parts():
    other = box(-80.1890, 25.7700, -80.1886, 25.77027)
    rec = _merged(MultiPolygon([_SQUARE, other]))
    assert rec.area_m2 == pytest.approx(2 * 40.1 * 29.9, rel=0.05)
