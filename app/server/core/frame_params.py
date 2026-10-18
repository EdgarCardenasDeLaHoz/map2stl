"""Frame request parameters (F-FRAME, phase P1).

Parses the wire format (``frame_mode``, ``frame_clat``, ``frame_clon``, ``frame_w_km``,
``frame_h_km``, ``frame_aspect``) and resolves it against a region bbox and projection into a
:class:`ResolvedFrame`. All geometry lives in :mod:`geo2stl.frame`; this module only validates
input and applies the fallback rules (an oversized fetch, a pole, or a +/-180 crossing falls
back to Inside with a one-line notice).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from geo2stl.frame import (
    MAX_FETCH_AREA_FACTOR,
    AntimeridianError,
    Frame,
    FrameError,
    ProjectionModel,
    cover_frame,
    custom_frame,
    fetch_bbox,
    frame_shape,
    inside_frame,
    needs_frame,
    touches_pole,
)

MODES = ('cover', 'inside', 'custom')
ASPECTS = {'free': None, '1:1': 1.0, '4:3': 4.0 / 3.0}
_KEYS = ('frame_mode', 'frame_clat', 'frame_clon', 'frame_w_km', 'frame_h_km', 'frame_aspect')
_DEFAULT_DIM = 1000


@dataclass(frozen=True)
class ResolvedFrame:
    """A frame ready for the raster routes.

    ``mode`` is the mode actually used (``inside`` after a fallback); ``fetch_bbox`` is
    ``(north, south, east, west)``; ``out_shape`` is ``(rows, cols)``; ``notice`` is a one-line
    message when the requested mode was replaced, else None.
    """
    model: ProjectionModel
    frame: Frame
    fetch_bbox: tuple[float, float, float, float]
    out_shape: tuple[int, int]
    mode: str
    notice: str | None = None


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _number(raw: Mapping, key: str, lo: float, hi: float, positive: bool = False):
    v = raw.get(key)
    if _blank(v):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number") from None
    if not math.isfinite(f) or not (lo <= f <= hi) or (positive and f <= 0):
        raise ValueError(f"{key} out of range")
    return f


def parse_frame(raw: Mapping | None) -> dict | None:
    """Read the frame parameters from a request mapping (strings or numbers).

    Returns None when none of the six keys is present (no frame requested). Otherwise a dict
    ``{mode, center_lat, center_lon, w_km, h_km, aspect}``: ``mode`` defaults to ``cover``,
    ``aspect`` to ``free`` (``free``, ``1:1``, ``4:3``, ``bed`` or a positive number w/h), the
    centre and size to None. A Custom frame must carry all four. Raises ValueError on bad input.
    """
    if not raw or all(_blank(raw.get(k)) for k in _KEYS):
        return None
    mode = raw.get('frame_mode')
    mode = 'cover' if _blank(mode) else str(mode).strip().lower()
    if mode not in MODES:
        raise ValueError(f"frame_mode must be one of {', '.join(MODES)}")
    aspect = raw.get('frame_aspect')
    aspect = 'free' if _blank(aspect) else str(aspect).strip().lower()
    if aspect not in ASPECTS and aspect != 'bed':
        try:
            a = float(aspect)
        except ValueError:
            raise ValueError("frame_aspect must be free, 1:1, 4:3, bed or a number") from None
        if not math.isfinite(a) or a <= 0:
            raise ValueError("frame_aspect must be positive")
        aspect = a
    out = {
        'mode': mode,
        'center_lat': _number(raw, 'frame_clat', -90.0, 90.0),
        'center_lon': _number(raw, 'frame_clon', -180.0, 180.0),
        'w_km': _number(raw, 'frame_w_km', 0.0, 1e5, positive=True),
        'h_km': _number(raw, 'frame_h_km', 0.0, 1e5, positive=True),
        'aspect': aspect,
    }
    if mode == 'custom' and any(out[k] is None for k in ('center_lat', 'center_lon', 'w_km', 'h_km')):
        raise ValueError("a custom frame needs frame_clat, frame_clon, frame_w_km and frame_h_km")
    return out


def _aspect_value(aspect, md: Mapping | None):
    """Numeric w/h for an aspect setting; ``bed`` reads ``md['bed_aspect']`` (None if absent)."""
    if aspect == 'bed':
        v = (md or {}).get('bed_aspect')
        return float(v) if v and float(v) > 0 else None
    if isinstance(aspect, float):
        return aspect
    return ASPECTS.get(aspect)


def _area(b) -> float:
    n, s, e, w = b
    return (e - w) * (math.sin(math.radians(n)) - math.sin(math.radians(s)))


def resolve_frame(bbox, projection: str, md: Mapping | None, raw: Mapping | None):
    """Resolve the request into a :class:`ResolvedFrame`, or None for the legacy path.

    ``bbox`` is ``(north, south, east, west)``. ``md`` is an optional settings mapping; only
    three keys are read: ``dim`` (longest output side in pixels, default 1000), ``src_px_deg``
    (degrees per source pixel, for the 2 px fetch margin; default longest bbox span / dim) and
    ``bed_aspect`` (print-bed w/h, for aspect ``bed``). None is returned when no frame is
    requested or :func:`geo2stl.frame.needs_frame` says the legacy ``project_grid`` runs.

    A Cover or Custom frame whose fetch bbox crosses +/-180, touches a pole, is not on the
    sphere, or exceeds ``MAX_FETCH_AREA_FACTOR`` times the bbox area is replaced by Inside, with
    a one-line ``notice``.
    """
    spec = parse_frame(raw)
    mode = spec['mode'] if spec else 'cover'
    if not needs_frame(projection, mode):
        return None
    spec = spec or parse_frame({'frame_mode': 'cover'})
    md = md or {}
    bbox = tuple(float(v) for v in bbox)
    dim = int(md.get('dim') or _DEFAULT_DIM)
    north, south, east, west = bbox
    px = float(md.get('src_px_deg') or max(north - south, east - west) / dim)
    aspect = _aspect_value(spec['aspect'], md)
    model = ProjectionModel(bbox, projection)

    notice = None
    fbbox = None
    if mode == 'inside':
        frame = inside_frame(model, aspect)
    else:
        frame = (custom_frame(model, spec['center_lat'], spec['center_lon'],
                              spec['w_km'], spec['h_km']) if mode == 'custom'
                 else cover_frame(model, aspect))
        try:
            fbbox = fetch_bbox(model, frame, px)
            if touches_pole(model, frame):
                notice = "Frame reaches a pole; using Inside."
            elif _area(fbbox) > MAX_FETCH_AREA_FACTOR * _area(bbox):
                notice = "Frame needs more than 3x the region's area; using Inside."
        except AntimeridianError:
            notice = "Frame crosses longitude +/-180; using Inside."
        except FrameError:
            notice = "Frame leaves the projectable area; using Inside."
        if notice:
            mode, frame, fbbox = 'inside', inside_frame(model, aspect), None
    if fbbox is None:
        fbbox = bbox
    shape = frame_shape(frame, dim)
    return ResolvedFrame(model, frame, fbbox, shape, mode, notice)
