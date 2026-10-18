"""Tests for geo2stl.frame and app.server.core.frame_params (F-FRAME P1). Offline and fast."""

import json
from pathlib import Path

import numpy as np
import pytest

from app.server.core import frame_params as fp
from geo2stl import frame as fr
from geo2stl.projections import (
    _conic_azimuthal_model,
    expected_aspect_ratio,
    projected_shape,
)

EUROPE = (60.0, 35.0, 30.0, -10.0)          # north, south, east, west
CITY = (48.90, 48.82, 2.42, 2.25)
SOUTH = (-33.0, -45.0, -60.0, -75.0)
BBOXES = {'europe': EUROPE, 'city': CITY, 'south': SOUTH}
FIXTURE = Path(__file__).parent / 'fixtures' / 'frame_cases.json'


def _grid(bbox, n=7):
    north, south, east, west = bbox
    lat, lon = np.meshgrid(np.linspace(south, north, n), np.linspace(west, east, n),
                           indexing='ij')
    return lat.ravel(), lon.ravel()


@pytest.mark.parametrize('name', list(BBOXES))
@pytest.mark.parametrize('proj', fr.PROJECTIONS)
def test_round_trip(proj, name):
    bbox = BBOXES[name]
    m = fr.ProjectionModel(bbox, proj)
    lat, lon = _grid(bbox)
    x, y = m.forward(lat, lon)
    lat2, lon2 = m.inverse(x, y)
    assert np.abs(lat2 - lat).max() < 1e-9
    assert np.abs(lon2 - lon).max() < 1e-9


@pytest.mark.parametrize('name', list(BBOXES))
@pytest.mark.parametrize('proj', fr.PROJECTIONS)
def test_cover_matches_today(proj, name):
    bbox = BBOXES[name]
    cover = fr.cover_frame(fr.ProjectionModel(bbox, proj))
    want = expected_aspect_ratio(bbox, 'equidistant' if proj == 'cosine' else proj)
    assert cover.w_km / cover.h_km == pytest.approx(want, rel=1e-9)
    if proj in ('laea', 'lcc') and not (proj == 'lcc' and bbox[1] < 0 < bbox[0]):
        x0, x1, y0, y1 = _conic_azimuthal_model(bbox, proj).extents
        assert cover.as_tuple() == pytest.approx(
            (x0 * fr.R_KM, x1 * fr.R_KM, y0 * fr.R_KM, y1 * fr.R_KM), rel=1e-12)


def test_sinusoidal_straddling_equator_is_wider_than_legacy():
    bbox = (10.0, -10.0, 20.0, 0.0)
    cover = fr.cover_frame(fr.ProjectionModel(bbox, 'sinusoidal'))
    legacy = expected_aspect_ratio(bbox, 'sinusoidal')
    assert cover.w_km / cover.h_km > legacy
    # the equator row is the widest: dlon * R
    assert cover.w_km == pytest.approx(np.radians(20.0) * fr.R_KM, rel=1e-9)


@pytest.mark.parametrize('proj', fr.PROJECTIONS)
def test_bbox_boundary_inside_cover(proj):
    m = fr.ProjectionModel(EUROPE, proj)
    cover = fr.cover_frame(m)
    lat, lon = fr._bbox_outline(EUROPE, 4001)
    x, y = m.forward(lat, lon)
    tol = 1e-9
    assert x.min() >= cover.x0 - tol and x.max() <= cover.x1 + tol
    assert y.min() >= cover.y0 - tol and y.max() <= cover.y1 + tol


@pytest.mark.parametrize('aspect', [None, 1.0, 4 / 3])
@pytest.mark.parametrize('name', list(BBOXES))
@pytest.mark.parametrize('proj', fr.PROJECTIONS)
def test_inside_corners_in_bbox(proj, name, aspect):
    bbox = BBOXES[name]
    m = fr.ProjectionModel(bbox, proj)
    f = fr.inside_frame(m, aspect)
    if aspect:
        assert f.w_km / f.h_km == pytest.approx(aspect, rel=1e-6)
    lat, lon = m.inverse(np.array([f.x0, f.x1, f.x0, f.x1]), np.array([f.y0, f.y0, f.y1, f.y1]))
    north, south, east, west = bbox
    assert (lat <= north + 1e-9).all() and (lat >= south - 1e-9).all()
    assert (lon <= east + 1e-9).all() and (lon >= west - 1e-9).all()
    cover = fr.cover_frame(m)
    assert f.w_km <= cover.w_km + 1e-5 and f.h_km <= cover.h_km + 1e-5


def test_inside_equals_cover_for_cylindrical():
    for proj in ('mercator', 'miller', 'none'):
        m = fr.ProjectionModel(EUROPE, proj)
        assert fr.inside_frame(m).as_tuple() == pytest.approx(fr.cover_frame(m).as_tuple())


def test_cover_aspect_grows():
    m = fr.ProjectionModel(EUROPE, 'laea')
    base = fr.cover_frame(m)
    f = fr.cover_frame(m, 1.0)
    assert f.w_km == pytest.approx(f.h_km)
    assert f.w_km >= base.w_km - 1e-9 and f.h_km >= base.h_km - 1e-9
    assert f.center == pytest.approx(base.center)


@pytest.mark.parametrize('proj', ['laea', 'lcc', 'sinusoidal'])
def test_project_to_frame_no_nan_europe(proj):
    m = fr.ProjectionModel(EUROPE, proj)
    cover = fr.cover_frame(m)
    fb = fr.fetch_bbox(m, cover, 0.1)
    n, s, e, w = fb
    lat = np.linspace(n, s, 120)[:, None]
    lon = np.linspace(w, e, 240)[None, :]
    src = lat * 1000.0 + lon + 0.0 * lat                      # smooth synthetic terrain
    shape = fr.frame_shape(cover, 200)
    out = fr.project_to_frame(src, fb, m, cover, shape)
    assert out.shape == shape
    assert not np.isnan(out).any()
    i, j = shape[0] // 3, shape[1] // 2
    dx, dy = cover.w_km / shape[1], cover.h_km / shape[0]
    la, lo = m.inverse(cover.x0 + (j + 0.5) * dx, cover.y1 - (i + 0.5) * dy)
    assert out[i, j] == pytest.approx(la * 1000.0 + lo, abs=60.0)


def test_project_to_frame_order0_exact_and_outside_nan():
    m = fr.ProjectionModel(CITY, 'none')
    src = np.arange(20, dtype=float).reshape(4, 5)
    f = fr.cover_frame(m)
    out = fr.project_to_frame(src, CITY, m, f, (8, 10), order=0)
    assert set(np.unique(out)) <= set(src.ravel())
    big = fr.Frame(f.x0 * 3, f.x1 * 3, f.y0 * 3, f.y1 * 3)
    assert np.isnan(fr.project_to_frame(src, CITY, m, big, (8, 10))).any()


def test_frame_shape_matches_projected_shape():
    for proj in ('laea', 'mercator', 'sinusoidal', 'lcc'):
        f = fr.cover_frame(fr.ProjectionModel(EUROPE, proj))
        assert fr.frame_shape(f, 800) == projected_shape(EUROPE, proj, 800)


def test_needs_frame():
    assert fr.needs_frame('mercator', 'custom')
    assert fr.needs_frame('laea', 'cover') and fr.needs_frame('lcc', 'inside')
    assert fr.needs_frame('sinusoidal', 'cover')
    assert not fr.needs_frame('mercator', 'cover') and not fr.needs_frame('none', 'inside')


def test_custom_frame_and_fetch_bbox():
    m = fr.ProjectionModel(EUROPE, 'laea')
    f = fr.custom_frame(m, 48.0, 10.0, 500.0, 400.0)
    assert (f.w_km, f.h_km) == (500.0, 400.0)
    assert m.inverse(*f.center) == pytest.approx((48.0, 10.0), abs=1e-9)
    n, s, e, w = fr.fetch_bbox(m, f, 0.01)
    assert s < 48.0 < n and w < 10.0 < e
    with pytest.raises(fr.FrameError):
        fr.custom_frame(m, 48.0, 10.0, -1.0, 5.0)


def test_fetch_bbox_pole_and_antimeridian():
    m = fr.ProjectionModel((80.0, 60.0, 30.0, -30.0), 'laea')
    f = fr.custom_frame(m, 90.0, 0.0, 2000.0, 2000.0)
    n, s, e, w = fr.fetch_bbox(m, f, 0.1)
    assert (n, e, w) == (90.0, 180.0, -180.0)
    m2 = fr.ProjectionModel((10.0, -10.0, 180.0, 150.0), 'laea')
    with pytest.raises(fr.AntimeridianError):
        fr.fetch_bbox(m2, fr.cover_frame(m2), 0.1)


# -- frame_params -------------------------------------------------------------

def test_parse_frame():
    assert fp.parse_frame(None) is None and fp.parse_frame({'frame_mode': ''}) is None
    d = fp.parse_frame({'frame_mode': 'cover'})
    assert d['mode'] == 'cover' and d['aspect'] == 'free'
    d = fp.parse_frame({'frame_mode': 'custom', 'frame_clat': '48.5', 'frame_clon': 2,
                        'frame_w_km': '300', 'frame_h_km': 200, 'frame_aspect': '4:3'})
    assert d['center_lat'] == 48.5 and d['w_km'] == 300.0 and d['aspect'] == '4:3'
    for bad in ({'frame_mode': 'x'}, {'frame_mode': 'custom'},
                {'frame_mode': 'custom', 'frame_clat': 95, 'frame_clon': 0,
                 'frame_w_km': 1, 'frame_h_km': 1},
                {'frame_aspect': 'wide'}, {'frame_mode': 'cover', 'frame_w_km': -3}):
        with pytest.raises(ValueError):
            fp.parse_frame(bad)


def test_resolve_frame_legacy_and_cover():
    assert fp.resolve_frame(EUROPE, 'mercator', {}, {'frame_mode': 'cover'}) is None
    assert fp.resolve_frame(EUROPE, 'mercator', {}, None) is None
    r = fp.resolve_frame(EUROPE, 'laea', {'dim': 500}, None)
    assert r.mode == 'cover' and r.notice is None
    assert max(r.out_shape) == 500
    assert r.fetch_bbox[0] > EUROPE[0] and r.fetch_bbox[3] < EUROPE[3]
    r = fp.resolve_frame(EUROPE, 'laea', {'dim': 500}, {'frame_mode': 'inside'})
    assert r.fetch_bbox == EUROPE and r.mode == 'inside'
    r = fp.resolve_frame(EUROPE, 'mercator', {'dim': 500},
                         {'frame_mode': 'custom', 'frame_clat': 48, 'frame_clon': 10,
                          'frame_w_km': 800, 'frame_h_km': 600})
    assert r.frame.w_km == 800 and r.out_shape == (375, 500)


def test_resolve_bed_aspect():
    r = fp.resolve_frame(EUROPE, 'laea', {'dim': 400, 'bed_aspect': 1.5},
                         {'frame_aspect': 'bed'})
    assert r.frame.w_km / r.frame.h_km == pytest.approx(1.5)


def test_fallback_notices():
    r = fp.resolve_frame((10.0, -10.0, 180.0, 150.0), 'laea', {}, {'frame_mode': 'cover'})
    assert r.mode == 'inside' and '180' in r.notice
    r = fp.resolve_frame((80.0, 60.0, 30.0, -30.0), 'laea', {},
                         {'frame_mode': 'custom', 'frame_clat': 90, 'frame_clon': 0,
                          'frame_w_km': 2000, 'frame_h_km': 2000})
    assert r.mode == 'inside' and 'pole' in r.notice
    r = fp.resolve_frame((60.0, 40.0, 20.0, 0.0), 'laea', {},
                         {'frame_mode': 'custom', 'frame_clat': 50, 'frame_clon': 10,
                          'frame_w_km': 5000, 'frame_h_km': 4000})
    assert r.mode == 'inside' and '3x' in r.notice


# -- fixture for the JS port --------------------------------------------------

def _cases():
    """Forward/inverse/Cover/Inside/fetch-bbox cases the JS port is checked against (1e-9)."""
    out = []
    for name, bbox in BBOXES.items():
        for proj in fr.PROJECTIONS:
            m = fr.ProjectionModel(bbox, proj)
            lat, lon = _grid(bbox, 3)
            x, y = m.forward(lat, lon)
            cover = fr.cover_frame(m)
            out.append({
                'name': f'{name}-{proj}', 'bbox': list(bbox), 'projection': proj,
                'points': [[float(a), float(b), float(c), float(d)]
                           for a, b, c, d in zip(lat, lon, x, y, strict=True)],
                'cover': list(cover.as_tuple()),
                'cover_1to1': list(fr.cover_frame(m, 1.0).as_tuple()),
                'fetch_bbox_cover': list(fr.fetch_bbox(m, cover, 0.01)),
                # raster-based for non-rectangular outlines: compare with a loose tolerance
                'inside': list(fr.inside_frame(m).as_tuple()),
                'inside_tol': 1e-9 if m.rectangular else 0.02 * cover.w_km,
            })
    return out


def test_fixture_matches_generator():
    cases = json.loads(json.dumps(_cases()))
    if not FIXTURE.exists():
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_text(json.dumps({'tol': 1e-9, 'cases': cases}, indent=1), encoding='utf-8')
    saved = json.loads(FIXTURE.read_text(encoding='utf-8'))['cases']
    assert len(saved) == len(cases)
    for a, b in zip(saved, cases, strict=True):
        assert a['name'] == b['name']
        for key in ('points', 'cover', 'cover_1to1', 'fetch_bbox_cover'):
            assert np.asarray(a[key]) == pytest.approx(np.asarray(b[key]), abs=1e-9), (a['name'], key)
        assert np.asarray(a['inside']) == pytest.approx(
            np.asarray(b['inside']), abs=max(a['inside_tol'], 1e-9))
