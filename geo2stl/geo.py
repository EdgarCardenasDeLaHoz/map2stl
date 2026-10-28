"""Geographic constants and bbox / pixel-grid helpers.

The one home for metres-per-degree in map2stl. Values are WGS84 on the
spherical-cos approximation used throughout the project:

* ``M_PER_DEG_LAT = 110 574 m`` (one degree of latitude, taken as constant);
* ``m_per_deg_lon(lat) = 111 320 m * cos(lat)`` (``M_PER_DEG_LON_EQ`` at the equator).

``M_PER_DEG_SPHERE`` (111 320 m for latitude too) is the older convention that the
skyline, survey, OSM-raster and photo-label maths were tuned and scored with. Those
modules import it rather than re-declaring it, so their outputs do not move (moving
them to 110 574 m is a separate, measured change: docs/decisions/architecture.md).

A bbox is a dict with ``north``, ``south``, ``east``, ``west`` in degrees. Sizes
are measured at the bbox mid-latitude.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

M_PER_DEG_LAT: float = 110_574.0
M_PER_DEG_LON_EQ: float = 111_320.0
#: One degree on the equatorial sphere, used for latitude by the modules named above.
M_PER_DEG_SPHERE: float = M_PER_DEG_LON_EQ
#: Mean Earth radius (spherical maths: haversine, great circles).
EARTH_RADIUS_M: float = 6_371_000.0
#: WGS84 equatorial circumference (web-mercator tile scale).
EARTH_CIRCUMFERENCE_M: float = 40_075_016.686


def m_per_deg_lon(lat_deg):
    """Metres per degree of longitude at ``lat_deg`` (scalar or array)."""
    return M_PER_DEG_LON_EQ * np.cos(np.radians(lat_deg))


def lonlat_to_local_m(lon, lat, lon0, lat0):
    """East/north metres of ``(lon, lat)`` from ``(lon0, lat0)``, equirectangular.

    Longitude is scaled at ``lat0``; good to a few metres over a city.
    """
    return (lon - lon0) * m_per_deg_lon(lat0), (lat - lat0) * M_PER_DEG_LAT


def bbox_size_m(bbox: dict) -> tuple[float, float]:
    """Return ``(width_m, height_m)`` of ``bbox``, width at the mid-latitude."""
    mid_lat = (bbox["north"] + bbox["south"]) / 2.0
    width = abs(bbox["east"] - bbox["west"]) * float(m_per_deg_lon(mid_lat))
    height = abs(bbox["north"] - bbox["south"]) * M_PER_DEG_LAT
    return width, height


def bbox_diagonal_km(bbox: dict) -> float:
    """Diagonal of ``bbox`` in kilometres (see :func:`bbox_size_m`)."""
    return math.hypot(*bbox_size_m(bbox)) / 1000.0


@dataclass(frozen=True)
class GeoGrid:
    """A raster of ``shape = (H, W)`` pixels covering ``bbox`` in plate carrée.

    Pixel-centre convention: pixel ``(col, row)`` covers
    ``[west + col*dx, west + (col+1)*dx]`` and its centre is at integer
    ``(col, row)``; the bbox edges sit at ``-0.5`` and ``W - 0.5`` / ``H - 0.5``.
    ``row0`` names the bbox edge that row 0 lies against (``"north"`` for
    image / GeoTIFF order, ``"south"`` for y-up arrays).
    """

    bbox: dict
    shape: tuple[int, int]
    row0: Literal["north", "south"] = "north"

    def __post_init__(self) -> None:
        if self.row0 not in ("north", "south"):
            raise ValueError(f"row0 must be 'north' or 'south', got {self.row0!r}")

    @property
    def deg_per_px(self) -> tuple[float, float]:
        """``(dlon, dlat)`` degrees per pixel."""
        h, w = self.shape
        b = self.bbox
        return (b["east"] - b["west"]) / w, (b["north"] - b["south"]) / h

    @property
    def m_per_px(self) -> float:
        """Ground metres per pixel, mean of the x and y spacing."""
        width, height = bbox_size_m(self.bbox)
        h, w = self.shape
        return (width / w + height / h) / 2.0

    def lonlat_to_px(self, lon, lat):
        """Return fractional ``(col, row)`` for ``lon``/``lat`` (scalars or arrays)."""
        dlon, dlat = self.deg_per_px
        b = self.bbox
        col = (np.asarray(lon, dtype=float) - b["west"]) / dlon - 0.5
        if self.row0 == "north":
            row = (b["north"] - np.asarray(lat, dtype=float)) / dlat - 0.5
        else:
            row = (np.asarray(lat, dtype=float) - b["south"]) / dlat - 0.5
        return col, row

    def px_to_lonlat(self, col, row):
        """Return ``(lon, lat)`` of fractional pixel ``(col, row)``; inverse of
        :meth:`lonlat_to_px`."""
        dlon, dlat = self.deg_per_px
        b = self.bbox
        lon = b["west"] + (np.asarray(col, dtype=float) + 0.5) * dlon
        if self.row0 == "north":
            lat = b["north"] - (np.asarray(row, dtype=float) + 0.5) * dlat
        else:
            lat = b["south"] + (np.asarray(row, dtype=float) + 0.5) * dlat
        return lon, lat
