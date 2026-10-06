"""Storey counts from facade floor bands (floor_bands), on facades ray-cast into a pano."""

import math

import numpy as np
import pytest

from city2stl.skyline import floor_bands as fl
from city2stl.skyline import footprint_detect as fd

F_PX = 1192.0                     # ~20.8 px/deg, the elevated hi-res captures
CAM_H = 120.0
BEARING = 60.0


def _building(d, inc_deg=35.0, width=60.0, depth=30.0):
    """Rectangle (local metres, camera at the origin) whose front wall's centre is ``d`` away
    at BEARING, its normal ``inc_deg`` off the ray. Returns (ring, centre, along, normal)."""
    b = math.radians(BEARING)
    ray = np.array([math.sin(b), math.cos(b)])
    a = math.radians(inc_deg)
    nrm = -np.array([ray[0] * math.cos(a) - ray[1] * math.sin(a),
                     ray[0] * math.sin(a) + ray[1] * math.cos(a)])   # towards the camera
    along = np.array([nrm[1], -nrm[0]])
    front = ray * d
    c = front - nrm * depth / 2
    hw, hd = width / 2, depth / 2
    ring = np.array([c + sa * hw * along + sn * hd * nrm
                     for sa, sn in ((-1, 1), (1, 1), (1, -1), (-1, -1), (-1, 1))])
    return ring, c, along, nrm, hw, hd


def _texture(h, u, kind, top, rng):
    """Facade intensity at height ``h`` above ground, ``u`` metres along the wall."""
    g = 4.5
    P = 6.4 if kind == "duplex" else 3.2
    sp = 1.6
    hf = np.mod(h - g, P)
    glass = np.mod(u, 1.5) < 1.2
    out = np.where(hf < sp, 170.0, np.where(glass, 55.0, 165.0))
    if kind == "trap":            # strong sill and head ledges, half a floor apart
        out = np.where((hf < 0.25) | (np.abs(hf - sp) < 0.25), 245.0, out)
        out = np.where((hf >= sp) & glass, 120.0, out)            # weaker window contrast
    out = np.where(h < g, np.where(np.mod(u, 4.0) < 3.0, 70.0, 140.0), out)
    out = np.where(h > top - 0.8, 190.0, out)                     # parapet
    return out


def _shade(v, bld, top, kind, rng):
    """Gray for unit rays ``v`` (..., 3: east, north, up) from the camera CAM_H above ground."""
    with np.errstate(all="ignore"):                   # rays that miss: inf/NaN, masked out
        return _shade_rays(v, bld, top, kind, rng)


def _shade_rays(v, bld, top, kind, rng):
    ring, c, along, nrm, hw, hd = bld
    hn = np.hypot(v[..., 0], v[..., 1])
    ux, uy, slope = v[..., 0] / hn, v[..., 1] / hn, v[..., 2] / hn
    out = np.where(slope > 0, 215.0, 0.0)
    ground_t = np.where(slope < 0, CAM_H / np.maximum(-slope, 1e-9), np.inf)
    gx, gy = ux * ground_t, uy * ground_t
    out = np.where(slope <= 0, 95.0 + 25.0 * (np.mod(np.floor(gx / 7) + np.floor(gy / 7), 2)), out)
    best = np.full(hn.shape, np.inf)
    u_at = np.zeros(hn.shape)
    for p, q in zip(ring[:-1], ring[1:], strict=True):
        e = q - p
        den = ux * e[1] - uy * e[0]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (p[0] * e[1] - p[1] * e[0]) / den
            s = (p[0] * uy - p[1] * ux) / den
        hit = (t > 0) & (s >= 0) & (s <= 1) & (t < best)
        best = np.where(hit, t, best)
        u_at = np.where(hit, s * np.hypot(*e), u_at)
    z_wall = CAM_H + slope * best
    wall = np.isfinite(best) & (z_wall >= 0) & (z_wall <= top)
    roof_t = np.where(slope < 0, (CAM_H - top) / np.maximum(-slope, 1e-9), np.inf)
    rx, ry = ux * roof_t - c[0], uy * roof_t - c[1]
    roof = (np.isfinite(best) & (z_wall > top) & (np.abs(rx * along[0] + ry * along[1]) <= hw)
            & (np.abs(rx * nrm[0] + ry * nrm[1]) <= hd))
    out = np.where(roof, 125.0, out)
    tex = _texture(z_wall, u_at, kind, top, rng)
    return np.where(wall, tex, out)


def _direct_pano(d, kind="plain", n_up=12, inc=35.0, pitch=-18.0, span=26.0):
    """A pano (Pano model: pinhole rows at ``pitch``, uniform heading columns) 2x2 supersampled."""
    rng = np.random.default_rng(0)
    top = 4.5 + n_up * (6.4 if kind == "duplex" else 3.2)
    bld = _building(d, inc)
    H = int(2 * F_PX * math.tan(math.radians(27.0)))
    W = int(2 * span * F_PX * math.pi / 180)
    frame = BEARING - span + np.arange(W) * 360.0 / (2 * math.pi * F_PX)
    acc = np.zeros((H, W))
    for dr in (-0.25, 0.25):
        for dc in (-0.25, 0.25):
            e = np.radians(pitch + np.degrees(np.arctan((H / 2.0 - (np.arange(H) + dr)) / F_PX)))
            a = np.radians(frame + dc * 360.0 / (2 * math.pi * F_PX))
            v = np.stack([np.sin(a)[None, :] * np.cos(e)[:, None],
                          np.cos(a)[None, :] * np.cos(e)[:, None],
                          np.broadcast_to(np.sin(e)[:, None], (H, W))], -1)
            acc += 0.25 * _shade(v, bld, top, kind, rng)
    acc += rng.normal(0, 3.0, acc.shape)
    rgb = np.repeat(np.clip(acc, 0, 255).astype(np.uint8)[..., None], 3, axis=2)
    pano = fd.Pano("synthetic", 10.4, -75.55, rgb, np.zeros((H, W), np.int16), frame, F_PX, pitch)
    return pano, bld, top


def _measured(pano, pose, bld, top):
    """The footprint's Measured box, from the scene geometry (as measure_footprints would)."""
    ring = bld[0]
    d, _ = fl.facade_ranges(pano, pose, ring, np.arange(pano.width), max_incidence_deg=90.0)
    cols = np.flatnonzero(np.isfinite(d))
    dc = d[cols]
    top_row = float(np.median(fd._row_of(pano, pose, np.degrees(np.arctan2(top - CAM_H, dc)))))
    base_row = float(np.median(fd._row_of(pano, pose, np.degrees(np.arctan2(-CAM_H, dc)))))
    return fd.Measured(0, "b", int(cols[0]), int(cols[-1]), top_row, base_row, base_row,
                       float(np.median(dc)), top, len(cols), True, None)


POSE = fd.PanoPose(0.0, CAM_H, 0.0, 0.1, 100)


def _estimate(d, kind="plain", ring_scale=1.0, **kw):
    pano, bld, top = _direct_pano(d, kind, **kw)
    m = _measured(pano, POSE, bld, top)
    return fl.estimate_floors(pano, POSE, m, bld[0] * ring_scale)


# --------------------------------------------------------------------------- unit parts

def test_facade_ranges_give_range_and_incidence():
    pano, bld, _top = _direct_pano(400.0)
    col = int(np.argmin(np.abs(pano.frame_heading - BEARING)))
    d, inc = fl.facade_ranges(pano, POSE, bld[0], [col, 0])
    assert d[0] == pytest.approx(400.0, abs=1.0) and inc[0] == pytest.approx(35.0, abs=0.5)
    assert np.isnan(d[1])                                          # misses the building
    d, inc = fl.facade_ranges(pano, POSE, bld[0], [col], max_incidence_deg=30.0)
    assert np.isnan(d[0]) and inc[0] == pytest.approx(35.0, abs=0.5)


def test_period_is_the_shortest_strong_peak():
    dz = 0.1
    z = np.arange(0, 45, dz)
    sq = np.sign(np.sin(2 * np.pi * z / 3.2))
    fit = fl.floor_period(sq, dz)
    assert fit.period_m == pytest.approx(3.2, rel=0.02) and not fit.octave_flag
    fit = fl.floor_period(np.sin(2 * np.pi * z / 6.4), dz)
    assert fit.period_m == pytest.approx(6.4, rel=0.02) and fit.octave_flag
    assert np.isnan(fl.floor_period(np.full(400, np.nan), dz).period_m)


def test_height_from_floors():
    est = fl.FloorEstimate(13.4, 13, False, 3.2, 6.4, 0.9, False, 9.0, 0.01, 42.9, 400.0,
                           True, 388.0, 80, True, "ok")
    h, s = fl.height_from_floors(est, "residential")
    assert h == pytest.approx(13 * 3.0 + 3.0) and 2.0 < s < 6.0
    assert fl.height_from_floors(est, "colonial")[0] == pytest.approx(13 * 4.2 + 3.0)


# --------------------------------------------------------------------------- whole facades

def test_counts_floors_and_period_at_400_m():
    est = _estimate(400.0)
    assert est is not None and est.accepted, est
    assert abs(est.n_floors - 13) <= 1                    # 4.5 m ground floor + 12 x 3.2 m
    assert est.period_m == pytest.approx(3.2, rel=0.03)
    assert est.px_per_floor > 5 and est.period_plausible and not est.lower_bound


def test_half_period_trap_reads_the_floor():
    est = _estimate(400.0, "trap")
    assert est is not None and est.period_m == pytest.approx(3.2, rel=0.03), est


def test_duplex_is_flagged_as_an_octave():
    est = _estimate(400.0, "duplex", n_up=8)
    assert est is not None and est.octave_flag and not est.accepted, est
    assert est.period_m == pytest.approx(6.4, rel=0.03)


def test_wrong_distance_keeps_the_count_and_flags_the_period():
    ok, far = _estimate(400.0), _estimate(400.0, ring_scale=1.3)
    assert far.n_floors == ok.n_floors and far.n_visible == pytest.approx(ok.n_visible, rel=0.03)
    assert far.period_m == pytest.approx(1.3 * 3.2, rel=0.04)
    assert not far.period_plausible and "implausible" in far.reason
    # the true range comes back through the period (the facade's own median range is ~400 m)
    assert far.implied_dist_m == pytest.approx(ok.dist_m * 3.1 / 3.2, rel=0.04)


def test_refuses_a_facade_too_far_to_resolve():
    est = _estimate(1200.0, span=8.0)
    assert est is not None and not est.accepted and "px per floor" in est.reason, est


def test_sphere_pano_pitch_rows_agree():
    """Pinhole views at pitches 8, -18 and -36 deg, stitched by exact reprojection: the facade
    (250 m, 120 m below) crosses the seam between the -18 and -36 rows. Only the headings
    around the building are rendered (sphere_pano leaves the other columns black)."""
    pytest.importorskip("cv2")
    from city2stl.skyline._pano import elevated as el

    rng = np.random.default_rng(1)
    d, n_up, fov, size = 250.0, 12, 30.0, 640
    top = 4.5 + n_up * 3.2
    bld = _building(d)
    f = 0.5 * size / math.tan(math.radians(fov / 2))
    ii = np.arange(size, dtype=float)
    views = {}
    for hd in (30, 60, 90):                    # the building's heading and its neighbours
        for p in (8.0, -18.0, -36.0):
            r, dn, fw = el._camera_axes(hd, p)
            ray = (fw[None, None, :] + ((ii[None, :, None] - size / 2) / f) * r[None, None, :]
                   + ((ii[:, None, None] - size / 2) / f) * dn[None, None, :])
            ray /= np.linalg.norm(ray, axis=-1, keepdims=True)
            g = _shade(ray, bld, top, "plain", rng) + rng.normal(0, 3.0, (size, size))
            views[(float(hd), p)] = np.repeat(np.clip(g, 0, 255).astype(np.uint8)[..., None], 3, 2)
    pano = el.sphere_pano("s", 10.4, -75.55, views, fov)
    m = _measured(pano, POSE, bld, top)
    est = fl.estimate_floors(pano, POSE, m, bld[0])
    assert est is not None and est.accepted, est
    assert est.period_m == pytest.approx(3.2, rel=0.03)
    assert est.strip_spread <= 0.02 and abs(est.n_floors - 13) <= 1
