"""
Projected rectangle frame (F-FRAME, phase P1)

A print is an axis-aligned rectangle in the projection's own x/y plane (kilometres on a
sphere of radius 6371 km). This module holds the pure geometry; it touches no network and
no cache.

* :class:`ProjectionModel` is the forward and inverse map of one projection, anchored on the
  region bbox (centre, standard parallels, average cosine). Moving the frame never moves the
  anchor, so every layer of a region shares one projection.
* :class:`Frame` is the rectangle ``(x0, x1, y0, y1)`` in km. :func:`cover_frame` is the
  bounding box of the projected bbox outline (today's rectangle), :func:`inside_frame` the
  largest rectangle whose every point lies inside the bbox, :func:`custom_frame` a user
  rectangle placed by centre and size.
* :func:`fetch_bbox` says which lat/lon area a source grid must cover to fill a frame, so the
  "wings" of sinusoidal, laea and lcc get real terrain instead of NaN.
* :func:`project_to_frame` resamples a plate-carree source onto a frame: each output pixel
  centre is inverse-projected and sampled.

Conventions. Bboxes are ``(north, south, east, west)`` in degrees, as in
:mod:`geo2stl.projections`, which supplies the conic/azimuthal maths, the Gall and Miller
latitude functions and :func:`largest_valid_rect`. A source grid has row 0 at the north edge
and spans the bbox edge to edge (``linspace``), the convention of ``_sample_grid``. Output
pixel ``(i, j)`` of a frame sits at the pixel *centre* ``x0 + (j + 0.5) dx``,
``y1 - (i + 0.5) dy``.

Where the model differs from the raster code that exists today (``cosine`` is modelled as its
nominal ``equidistant`` shape; ``sinusoidal`` over a bbox that straddles the equator is wider
than the legacy raster) is stated on :class:`ProjectionModel`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from geo2stl.projections import (
    _X_SCALE,
    EARTH_RADIUS,
    _conic_azimuthal_model,
    _gall_y,
    _miller_y,
    largest_valid_rect,
    lcc_parameters,
)

#: Sphere radius in km. Every projection here is spherical, in km.
R_KM = EARTH_RADIUS / 1000.0

#: Every projection name the app supports.
PROJECTIONS = ('none', 'cosine', 'equidistant', 'mercator', 'lambert', 'miller',
               'gall', 'sinusoidal', 'laea', 'lcc')
_CONIC = ('laea', 'lcc')
#: Projections whose bbox outline is not an axis-aligned rectangle.
NON_RECTANGULAR = ('sinusoidal', 'laea', 'lcc')
_MERCATOR_MAX_LAT = 85.0
_POLE_LAT = 89.5
_EDGE_SAMPLES = 1001        # samples per bbox edge when measuring an outline
_INSIDE_EPS = 1e-9          # degrees of slack when testing "inside the bbox"
_FETCH_MARGIN_PX = 2
#: A fetch bbox larger than this multiple of the region bbox falls back to Inside.
MAX_FETCH_AREA_FACTOR = 3.0


class FrameError(ValueError):
    """A frame cannot be used (outside the projectable area, bad size...)."""


class AntimeridianError(FrameError):
    """The frame's fetch area crosses longitude +/-180."""


@dataclass(frozen=True)
class Frame:
    """An axis-aligned rectangle ``(x0, x1, y0, y1)`` in the projection plane, in km."""
    x0: float
    x1: float
    y0: float
    y1: float

    @property
    def w_km(self) -> float:
        return self.x1 - self.x0

    @property
    def h_km(self) -> float:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.x0, self.x1, self.y0, self.y1


def _centered(cx: float, cy: float, w: float, h: float) -> Frame:
    return Frame(cx - w / 2.0, cx + w / 2.0, cy - h / 2.0, cy + h / 2.0)


def _mercator_y(lat_deg):
    return np.log(np.tan(np.pi / 4 + np.radians(np.clip(lat_deg, -_MERCATOR_MAX_LAT,
                                                        _MERCATOR_MAX_LAT)) / 2))


class ProjectionModel:
    """Forward and inverse map of one projection, anchored on the region bbox.

    ``forward(lat, lon) -> (x_km, y_km)`` and ``inverse(x_km, y_km) -> (lat, lon)`` are
    vectorised and take degrees. The anchor (centre latitude/longitude, the cosine of the
    centre latitude, the lcc parallels) comes from ``region_bbox`` only.

    Agreement with the raster code in :mod:`geo2stl.projections` (aspect of the Cover frame
    equals ``expected_aspect_ratio``):

    * ``none``: x and y are degrees times ``R * pi / 180``, so the aspect is the degree ratio.
    * ``cosine`` and ``equidistant`` share one map, x scaled by the cosine of the centre
      latitude. The cosine raster itself scales by the mean cosine of its rows; its nominal
      shape (``expected_aspect_ratio``) is the centre one, which is what this model gives.
    * ``mercator`` clamps latitude at 85 degrees, as the raster code does.
    * ``sinusoidal`` is x = R dlon cos(lat), y = R dlat about the bbox centre. The legacy raster
      sizes its width from the cosine of the nearer bbox edge, so for a bbox that straddles
      the equator the Cover here is wider (the equator row is the widest); otherwise equal.
    * ``laea`` and ``lcc`` wrap ``_ConicAzimuthalModel`` (unit sphere) scaled to km; ``lcc``
      over a bbox with no cone (see ``lcc_parameters``) behaves as ``equidistant``.

    Cylindrical y values are measured from the bbox centre latitude, so coordinates stay small.
    """

    def __init__(self, region_bbox, projection: str):
        if projection not in PROJECTIONS:
            raise FrameError(f"Unknown projection: {projection}")
        north, south, east, west = (float(v) for v in region_bbox)
        self.region_bbox = (north, south, east, west)
        self.projection = projection
        self.lat0 = (north + south) / 2.0
        self.lon0 = (east + west) / 2.0
        self._cos0 = float(np.cos(np.radians(self.lat0)))
        self._kind = projection
        if projection == 'cosine':
            self._kind = 'equidistant'
        if projection == 'lcc' and lcc_parameters(self.region_bbox) is None:
            self._kind = 'equidistant'
        self._conic = (_conic_azimuthal_model(self.region_bbox, projection)
                       if self._kind in _CONIC else None)
        self._y_of_lat = {'mercator': _mercator_y, 'lambert': lambda a: np.sin(np.radians(a)),
                          'miller': _miller_y, 'gall': _gall_y}.get(self._kind)
        self._y_ref = (float(self._y_of_lat(np.array(self.lat0)))
                       if self._y_of_lat is not None else 0.0)

    # -- forward ----------------------------------------------------------------
    def forward(self, lat, lon):
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        k = self._kind
        dlon = np.radians(lon - self.lon0)
        if k in _CONIC:
            x, y = self._conic.forward(lat, lon)
            return R_KM * x, R_KM * y
        if k == 'none':
            return R_KM * dlon, R_KM * np.radians(lat - self.lat0)
        if k == 'equidistant':
            return R_KM * dlon * self._cos0, R_KM * np.radians(lat - self.lat0)
        if k == 'sinusoidal':
            return (R_KM * dlon * np.cos(np.radians(lat)),
                    R_KM * np.radians(lat - self.lat0))
        # cylindrical y = f(lat): mercator, lambert, miller, gall
        scale = _X_SCALE.get(k, 1.0)
        return R_KM * dlon * scale, R_KM * (self._y_of_lat(lat) - self._y_ref)

    # -- inverse ----------------------------------------------------------------
    def inverse(self, x_km, y_km):
        x = np.asarray(x_km, dtype=np.float64)
        y = np.asarray(y_km, dtype=np.float64)
        k = self._kind
        if k in _CONIC:
            return self._conic.inverse(x / R_KM, y / R_KM)
        xr, yr = x / R_KM, y / R_KM
        if k == 'none':
            return self.lat0 + np.degrees(yr), self.lon0 + np.degrees(xr)
        if k == 'equidistant':
            return (self.lat0 + np.degrees(yr),
                    self.lon0 + np.degrees(xr / self._cos0))
        if k == 'sinusoidal':
            lat = self.lat0 + np.degrees(yr)
            cl = np.cos(np.radians(lat))
            with np.errstate(divide='ignore', invalid='ignore'):
                lon = self.lon0 + np.degrees(np.where(np.abs(cl) > 1e-12, xr / cl, np.nan))
            return lat, lon
        lon = self.lon0 + np.degrees(xr / _X_SCALE.get(k, 1.0))
        yy = yr + self._y_ref
        if k == 'mercator':
            lat = np.degrees(2.0 * np.arctan(np.exp(yy)) - np.pi / 2)
        elif k == 'lambert':
            lat = np.degrees(np.arcsin(np.clip(yy, -1.0, 1.0)))
        elif k == 'miller':
            lat = np.degrees((2.0 * np.arctan(np.exp(yy / 1.25)) - np.pi / 2) / 0.8)
        else:   # gall
            lat = np.degrees(2.0 * np.arctan(yy / (1 + np.sqrt(2) / 2)))
        return lat, lon

    @property
    def rectangular(self) -> bool:
        """True when the bbox outline is an axis-aligned rectangle (Cover == Inside)."""
        return self._kind not in NON_RECTANGULAR


# ---------------------------------------------------------------------------
# Cover, Inside, Custom
# ---------------------------------------------------------------------------

def _bbox_outline(bbox, n: int = _EDGE_SAMPLES):
    north, south, east, west = bbox
    t = np.linspace(0.0, 1.0, n)
    lat = south + t * (north - south)
    lon = west + t * (east - west)
    return (np.concatenate([lat, lat, np.full(n, north), np.full(n, south)]),
            np.concatenate([np.full(n, west), np.full(n, east), lon, lon]))


def _apply_aspect_grow(frame: Frame, aspect) -> Frame:
    """Grow the short side about the centre until ``w / h == aspect``."""
    if not aspect or aspect <= 0:
        return frame
    cx, cy = frame.center
    w, h = frame.w_km, frame.h_km
    if w / h < aspect:
        w = h * aspect
    else:
        h = w / aspect
    return _centered(cx, cy, w, h)


def cover_frame(model: ProjectionModel, aspect=None) -> Frame:
    """Bounding box of the projected bbox outline: today's rectangle.

    ``aspect`` (width / height, or None for free) grows the short side about the centre.
    """
    lat, lon = _bbox_outline(model.region_bbox)
    x, y = model.forward(lat, lon)
    return _apply_aspect_grow(Frame(float(x.min()), float(x.max()),
                                    float(y.min()), float(y.max())), aspect)


def _in_bbox(model: ProjectionModel, lat, lon, eps: float = _INSIDE_EPS) -> np.ndarray:
    north, south, east, west = model.region_bbox
    e = eps
    with np.errstate(invalid='ignore'):
        return ((lat >= south - e) & (lat <= north + e)
                & (lon >= west - e) & (lon <= east + e))      # NaN is False


def _frame_boundary(frame: Frame, n: int = 129):
    t = np.linspace(0.0, 1.0, n)
    xs = frame.x0 + t * frame.w_km
    ys = frame.y0 + t * frame.h_km
    return (np.concatenate([xs, xs, np.full(n, frame.x0), np.full(n, frame.x1)]),
            np.concatenate([np.full(n, frame.y0), np.full(n, frame.y1), ys, ys]))


def _frame_inside(model: ProjectionModel, frame: Frame) -> bool:
    """True when every point of the frame boundary inverse-projects into the bbox.

    No slack, so a frame that passes also passes a later check with ``_INSIDE_EPS`` slack.
    """
    lat, lon = model.inverse(*_frame_boundary(frame))
    return bool(_in_bbox(model, lat, _wrap_lon(lon, model.lon0), 0.0).all())


def _wrap_lon(lon, ref: float):
    """Longitude shifted by a multiple of 360 to lie within 180 degrees of ``ref``."""
    return ref + (np.asarray(lon, dtype=np.float64) - ref + 180.0) % 360.0 - 180.0


def _largest_free_rect(model: ProjectionModel, cover: Frame, cells: int = 400) -> Frame:
    """Largest axis-aligned rectangle of the Cover whose cells all lie inside the bbox."""
    # (rows, cols): longest side = cells
    if cover.w_km >= cover.h_km:
        rows, cols = max(1, int(round(cells * cover.h_km / cover.w_km))), cells
    else:
        rows, cols = cells, max(1, int(round(cells * cover.w_km / cover.h_km)))
    dx, dy = cover.w_km / cols, cover.h_km / rows
    xs = cover.x0 + (np.arange(cols) + 0.5) * dx
    ys = cover.y1 - (np.arange(rows) + 0.5) * dy
    lat, lon = model.inverse(xs[None, :], ys[:, None])
    ok = _in_bbox(model, lat, _wrap_lon(lon, model.lon0))
    grid = np.where(ok, 0.0, np.nan)
    r0, r1, c0, c1 = largest_valid_rect(grid, max_cells=max(rows, cols))
    return Frame(cover.x0 + c0 * dx, cover.x0 + c1 * dx,
                 cover.y1 - r1 * dy, cover.y1 - r0 * dy)


def _shrink_until_inside(model: ProjectionModel, frame: Frame) -> Frame:
    """Trim the frame about its centre in 0.05 % steps until its boundary is inside."""
    cx, cy = frame.center
    w, h = frame.w_km, frame.h_km
    for _ in range(400):
        f = _centered(cx, cy, w, h)
        if _frame_inside(model, f):
            return f
        w *= 0.9995
        h *= 0.9995
    raise FrameError("no rectangle fits inside the region bbox")


def inside_frame(model: ProjectionModel, aspect=None) -> Frame:
    """Largest rectangle whose every point lies inside the bbox.

    Cylindrical projections give the bbox itself (equal to Cover). For sinusoidal, laea and
    lcc the largest wing-free rectangle of the rasterised outline is found with
    :func:`geo2stl.projections.largest_valid_rect`, then trimmed until its sampled boundary
    inverse-projects inside the bbox. With an aspect lock the scale is binary-searched about
    the centre of that rectangle until the edges fall inside the bbox.
    """
    cover = cover_frame(model)
    base = cover if model.rectangular else _shrink_until_inside(
        model, _largest_free_rect(model, cover))
    if not aspect or aspect <= 0:
        return base
    cx, cy = base.center
    hi = max(base.w_km, base.h_km * aspect)
    lo = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if mid > 0 and _frame_inside(model, _centered(cx, cy, mid, mid / aspect)):
            lo = mid
        else:
            hi = mid
    if lo <= 0:
        raise FrameError("no rectangle of that aspect fits inside the region bbox")
    return _centered(cx, cy, lo, lo / aspect)


def custom_frame(model: ProjectionModel, center_lat: float, center_lon: float,
                 w_km: float, h_km: float) -> Frame:
    """Rectangle of ``w_km`` x ``h_km`` centred on a lat/lon point, in the model's plane."""
    if not (w_km > 0 and h_km > 0 and np.isfinite(w_km) and np.isfinite(h_km)):
        raise FrameError("frame size must be positive")
    cx, cy = model.forward(float(center_lat), float(center_lon))
    if not (np.isfinite(cx) and np.isfinite(cy)):
        raise FrameError("frame centre is outside the projectable area")
    return _centered(float(cx), float(cy), float(w_km), float(h_km))


# ---------------------------------------------------------------------------
# Fetch bbox, shape, resampling
# ---------------------------------------------------------------------------

def touches_pole(model: ProjectionModel, frame: Frame) -> bool:
    """True when a pole projects inside the frame, or the frame reaches a pole latitude."""
    for pole in (90.0, -90.0):
        px, py = model.forward(pole, model.lon0)
        if (np.isfinite(px) and np.isfinite(py)
                and frame.x0 <= px <= frame.x1 and frame.y0 <= py <= frame.y1):
            return True
    lat, _ = model.inverse(*_frame_boundary(frame))
    with np.errstate(invalid='ignore'):
        return bool(np.nanmax(np.abs(lat)) >= _POLE_LAT)


def fetch_bbox(model: ProjectionModel, frame: Frame, src_px_deg: float):
    """Lat/lon bbox ``(north, south, east, west)`` a source grid must cover to fill ``frame``.

    The densely sampled frame boundary is inverse-projected and its min/max taken, plus a
    margin of 2 source pixels (``src_px_deg`` degrees each). A frame that contains a pole
    returns latitude up to +/-90 and the full longitude range -180..180. A frame whose area is
    not entirely on the sphere raises :class:`FrameError`; one crossing longitude +/-180
    raises :class:`AntimeridianError`.
    """
    lat, lon = model.inverse(*_frame_boundary(frame, 257))
    if not (np.isfinite(lat).all() and np.isfinite(lon).all()):
        raise FrameError("frame leaves the projectable area")
    margin = _FETCH_MARGIN_PX * float(src_px_deg)
    if touches_pole(model, frame):
        north = 90.0 if (lat.max() >= _POLE_LAT or _pole_inside(model, frame, 90.0))             else min(90.0, lat.max() + margin)
        south = -90.0 if (lat.min() <= -_POLE_LAT or _pole_inside(model, frame, -90.0))             else max(-90.0, lat.min() - margin)
        return (float(north), float(south), 180.0, -180.0)
    lon = _wrap_lon(lon, model.lon0)
    west, east = float(lon.min()) - margin, float(lon.max()) + margin
    if west < -180.0 or east > 180.0:
        raise AntimeridianError("frame crosses longitude +/-180")
    return (float(min(90.0, lat.max() + margin)), float(max(-90.0, lat.min() - margin)),
            east, west)


def _pole_inside(model: ProjectionModel, frame: Frame, pole: float) -> bool:
    px, py = model.forward(pole, model.lon0)
    return bool(np.isfinite(px) and np.isfinite(py)
                and frame.x0 <= px <= frame.x1 and frame.y0 <= py <= frame.y1)


def frame_shape(frame: Frame, dim: int) -> tuple[int, int]:
    """``(rows, cols)`` of a raster of the frame whose longest side is ``dim``.

    Same rounding as ``projected_shape``, so the default Cover frame gets the shape it has today.
    """
    dim = max(1, int(dim))
    aspect = frame.w_km / frame.h_km
    if not np.isfinite(aspect) or aspect <= 0:
        aspect = 1.0
    if aspect >= 1.0:
        return max(1, int(round(dim / aspect))), dim
    return dim, max(1, int(round(dim * aspect)))


def project_to_frame(src: np.ndarray, src_bbox, model: ProjectionModel, frame: Frame,
                     out_shape: tuple[int, int], order: int = 1,
                     fill_value: float = np.nan) -> np.ndarray:
    """Resample a plate-carree ``src`` onto ``frame``; returns a float64 ``out_shape`` grid.

    ``src`` row 0 is the north edge of ``src_bbox`` and rows/columns span the bbox edge to
    edge (``linspace``), as in ``geo2stl.projections``. Output pixel ``(i, j)`` is at the
    centre ``(x0 + (j + 0.5) dx, y1 - (i + 0.5) dy)``; it is inverse-projected and sampled
    (bilinear, or nearest for ``order=0`` so categorical values stay exact). Pixels whose
    position falls outside ``src_bbox`` (or off the sphere) get ``fill_value`` (NaN).
    """
    m, n = src.shape
    rows, cols = out_shape
    north, south, east, west = (float(v) for v in src_bbox)
    dx, dy = frame.w_km / cols, frame.h_km / rows
    xs = frame.x0 + (np.arange(cols) + 0.5) * dx
    ys = frame.y1 - (np.arange(rows) + 0.5) * dy
    lat, lon = model.inverse(xs[None, :], ys[:, None])
    lon = _wrap_lon(lon, (east + west) / 2.0)
    r = (north - lat) / (north - south) * (m - 1)
    c = (lon - west) / (east - west) * (n - 1)
    eps = 1e-6
    with np.errstate(invalid='ignore'):
        valid = (r >= -eps) & (r <= m - 1 + eps) & (c >= -eps) & (c <= n - 1 + eps)
    r = np.clip(np.where(valid, r, 0.0), 0, m - 1)
    c = np.clip(np.where(valid, c, 0.0), 0, n - 1)
    out = ndimage.map_coordinates(np.asarray(src, dtype=np.float64), [r, c],
                                  order=0 if order == 0 else 1, mode='nearest')
    out[~valid] = fill_value
    return out


def needs_frame(projection: str, frame_mode: str) -> bool:
    """True when the frame path must run: a Custom frame, or a non-rectangular outline.

    Everything else (none, cosine, equidistant, mercator, lambert, miller, gall with a
    Cover or Inside frame) keeps the legacy ``project_grid`` path unchanged.
    """
    return frame_mode == 'custom' or projection in NON_RECTANGULAR
