"""Flat-earth distance and bearing between two lat/lon points, shared by the photo-label modules.

Both use the equirectangular approximation (`M_PER_DEG_LAT` per degree of latitude, longitude scaled
by cos(mean latitude)); at the few-km range of a photo's search area it is within a metre of the
great circle.
"""
from __future__ import annotations

import math

from geo2stl.geo import M_PER_DEG_SPHERE as M_PER_DEG_LAT


def dist_m(lat1, lon1, lat2, lon2) -> float:
    """Metres between two points."""
    kx = M_PER_DEG_LAT * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * M_PER_DEG_LAT)


def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    """Compass bearing from point 1 to point 2 in degrees, 0 = north, clockwise, in [0, 360)."""
    kx = M_PER_DEG_LAT * math.cos(math.radians((lat1 + lat2) / 2))
    return math.degrees(math.atan2((lon2 - lon1) * kx, (lat2 - lat1) * M_PER_DEG_LAT)) % 360.0
