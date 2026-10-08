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


# --------------------------------------------------------------------------- without a footprint

def _instance_map(pano, pose, bld, top):
    """Label 1 on the facade's pixels (from the scene geometry, as MobileSAM would give)."""
    d, _ = fl.facade_ranges(pano, pose, bld[0], np.arange(pano.width), max_incidence_deg=90.0)
    inst = np.zeros((pano.height, pano.width), np.int32)
    for x in np.flatnonzero(np.isfinite(d)):
        r_top = fd._row_of(pano, pose, math.degrees(math.atan2(top - CAM_H, d[x])))
        r_base = fd._row_of(pano, pose, math.degrees(math.atan2(-CAM_H, d[x])))
        inst[max(0, int(math.ceil(r_top))):min(pano.height, int(r_base)), x] = 1
    return inst


def test_storeys_and_distance_without_a_footprint():
    """The user's point (2026-10-06): floors are an estimate even without heights, and they
    give the distance and whether a plot is a high-rise, before any footprint match."""
    pano, bld, top = _direct_pano(400.0)
    inst = _instance_map(pano, POSE, bld, top)
    gray = pano.rgb[..., 0].astype(np.float32)
    est = fl.instance_floors(pano, POSE, gray, inst, 1)
    assert est is not None and est.accepted, est
    assert 12.5 <= est.floors_visible <= 15.0                 # 13 storeys + ground floor extra
    assert est.high_rise
    d_true = float(np.nanmedian(fl.facade_ranges(pano, POSE, bld[0], [est.col], 90.0)[0]))
    assert abs(est.dist_m - d_true * 3.1 / 3.2) / d_true < 0.06      # nominal 3.1 m floor
    near = bld[0] * 0.5                                        # a plot in front, same bearing
    far = bld[0] * 2.0                                         # and one behind
    hits = fl.match_plot(est, pano, POSE, [near, bld[0], far])
    assert hits and hits[0][0] == 1


def _two_floor_module(fine=11.8, n=600):
    """A floor every ``fine`` lags with every second floor different (a 2-floor module)."""
    x = np.arange(n)
    return np.sin(2 * np.pi * x / fine) + 0.5 * np.sin(np.pi * x / fine)


def test_subharmonic_peak_within_a_lag():
    """The finer peak may sit a lag off period / k: rounding period / 2 to the nearest lag
    missed seed_6's 19-floor tower read at every second floor (2026-10-06)."""
    prof = _two_floor_module()                    # floors at 11.8 lags, the module at 23.6
    a = fl._acf(prof, 40)
    assert np.isfinite(fl._peak_near(a, 12.7, 1.0))
    # a coarse period refined to 25.4 lags: its half (12.7) rounds to 13, the peak is at 12
    assert fl._subharmonic(prof, 1.0, 25.4, float(np.nanmax(a[20:30])), 3) == 2
    plain = np.sin(2 * np.pi * np.arange(600) / 23.6)
    assert fl._subharmonic(plain, 1.0, 23.6, 0.9, 3) == 1


def test_strip_candidates_offer_the_finer_period():
    prof = _two_floor_module()
    g = (24.0, 0.025, 0.6, 23.6, 0.0, 0.0, 0, prof, 0.001)      # (floors, period, acf, px, ...)
    ks = {c[4]: c for c in fl._strip_candidates(g)}
    assert set(ks) >= {1, 2}
    assert ks[2][0] == pytest.approx(48.0) and ks[2][3] == pytest.approx(11.8)
    plain = (24.0, 0.025, 0.6, 23.6, 0.0, 0.0, 0, np.sin(2 * np.pi * np.arange(600) / 23.6), 0.001)
    assert [c[4] for c in fl._strip_candidates(plain)] == [1]


def test_base_ratio_allows_tall_tower_floors():
    """Cartagena's towers are 4.3-4.4 m a floor (Allure 190 m / 43): within the ratio."""
    assert fl.MAX_BASE_RATIO >= 4.4 / fl.NOMINAL_FLOOR_M


def test_footprint_on_the_bearing_must_match_the_floor_range():
    """With footprints given, the floor-implied range must land on one of their walls, base
    seen or not (seed_5's "3 floors at 58 m", every wall on its bearing past 450 m)."""
    pano, bld, top = _direct_pano(400.0)
    inst = _instance_map(pano, POSE, bld, top)
    gray = pano.rgb[..., 0].astype(np.float32)
    ok = fl.instance_floors(pano, POSE, gray, inst, 1, rings_xy=[bld[0] * 0.3, bld[0]])
    assert ok is not None and ok.accepted, ok
    off = fl.instance_floors(pano, POSE, gray, inst, 1, rings_xy=[bld[0] * 0.3, bld[0] * 3.0])
    assert off is not None and not off.accepted and "no covering footprint" in off.reason, off
    assert off.floors_visible == pytest.approx(ok.floors_visible)
    none = fl.instance_floors(pano, POSE, gray, inst, 1, rings_xy=[])
    assert none is not None and none.accepted                  # nothing on the bearing: no check


def test_storey_height_comes_from_the_footprint_range():
    """Distance from the geometry, not an assumed floor (2026-10-07): a footprint 1.45 x farther
    than a 3.1 m floor allows means 4.6 m floors (Cartagena's towers run 4.3-4.8 m), accepted
    with that storey height; the nearest covering footprint at a plausible one wins."""
    pano, bld, top = _direct_pano(400.0)
    inst = _instance_map(pano, POSE, bld, top)
    gray = pano.rgb[..., 0].astype(np.float32)
    pose = fd.PanoPose(0.0, 0.0, 0.0, 0.1, 100)                  # no base row to bound the range
    tall = fl.instance_floors(pano, pose, gray, inst, 1, rings_xy=[bld[0] * 0.3, bld[0] * 1.45])
    assert tall is not None and tall.accepted and tall.plot == 1, tall
    assert tall.storey_m == pytest.approx(3.2 * 1.45, rel=0.05)
    assert tall.dist_m == pytest.approx(tall.plot_dist_m)
    both = fl.instance_floors(pano, pose, gray, inst, 1, rings_xy=[bld[0] * 1.45, bld[0]])
    assert both.plot == 1 and both.storey_m == pytest.approx(3.2, rel=0.05), both
    assert fl.match_plot(both, pano, pose, [bld[0] * 1.45, bld[0]])[0][0] == 1


def test_one_plot_one_instance_and_split_masks_count_together():
    """2026-10-07, seed_5: a tower split into masks (its podium below) has one plot; the small
    mask must not take it alone, and the two count together for the storeys."""
    pano, bld, top = _direct_pano(400.0)
    inst = _instance_map(pano, POSE, bld, top)
    gray = pano.rgb[..., 0].astype(np.float32)
    pose = fd.PanoPose(0.0, 0.0, 0.0, 0.1, 100)
    whole = fl.instance_floors(pano, pose, gray, inst, 1, rings_xy=[bld[0]])
    rows = np.flatnonzero((inst == 1).any(1))
    split = inst.copy()
    cut = rows[0] + int(0.62 * (rows[-1] - rows[0]))
    split[cut:][split[cut:] == 1] = 2                         # the lower ~5 floors: a second mask
    res = fl.pano_floors(pano, pose, gray, split, [1, 2], rings_xy=[bld[0]])
    plots = [hit[0] for _e, hit in res if hit]
    assert plots == [0], res                                  # one instance holds the plot
    e = next(e for e, hit in res if hit)
    assert e.members == (1, 2)
    assert e.floors_visible == pytest.approx(whole.floors_visible, rel=0.1), (e, whole)


def test_base_hidden_makes_the_count_a_lower_bound_and_a_flat_strip_is_refused():
    """User review (2026-10-07): 7 of 16 floor labels were too few floors, all with the base or
    lower floors hidden, so a count is a lower bound unless ground is seen under the mask; one
    "3 fl" was a road (seed_6 inst 288, 18 rows over 276 columns)."""
    import dataclasses

    pano, bld, top = _direct_pano(400.0)
    inst = _instance_map(pano, POSE, bld, top)
    gray = pano.rgb[..., 0].astype(np.float32)
    lab = pano.labels.copy()
    rows = np.flatnonzero(inst.any(1))
    lab[rows.max() + 1:, :] = fd.BUILDING_CLASSES[0]            # a nearer building under it
    hidden = fl.instance_floors(dataclasses.replace(pano, labels=lab), POSE, gray, inst, 1)
    assert hidden.accepted and not hidden.base_seen and hidden.lower_bound
    lab[rows.max() + 1:, :] = 6                                   # road: the base is in view
    seen = fl.instance_floors(dataclasses.replace(pano, labels=lab), POSE, gray, inst, 1)
    assert seen.base_seen and not seen.lower_bound
    # the facade is 121 rows over ~154 trimmed columns (0.79): refused once that counts as flat
    assert "flat strip" not in seen.reason
    fl_min, fl.MIN_ASPECT = fl.MIN_ASPECT, 0.9
    try:
        flat = fl.instance_floors(pano, POSE, gray, inst, 1)
    finally:
        fl.MIN_ASPECT = fl_min
    assert not flat.accepted and "flat strip" in flat.reason


# ---- F-SKY26 steps 4/5: storey calibration, high-rise flag, floors readings, publisher hook ---


def test_storey_from_height_tags_with_a_leave_one_out_sigma():
    samples = [(20, 20 * 4.3 + 3), (30, 30 * 4.1 + 3), (40, 40 * 4.5 + 3), (12, 12 * 4.2 + 3)]
    cal = fl.calibrate_storey(samples)
    assert cal.n == 4 and not cal.fallback and cal.reliable
    assert cal.storey_m == pytest.approx(4.25)
    assert 0.0 < cal.sigma_log < 0.1
    few = fl.calibrate_storey(samples[:2])                     # under MIN_STOREY_SAMPLES
    assert few.fallback and few.storey_m == fl.NOMINAL_FLOOR_M and not few.reliable
    noisy = fl.calibrate_storey([(10, 20.0), (10, 90.0), (10, 45.0), (10, 200.0)])
    assert not noisy.fallback and not noisy.reliable          # Miami-like: sigma over 0.25


def test_high_rise_plots_count_lower_bounds_and_skip_satellite_low_plots():
    ents = [{"fid": "a", "floors": 12.0, "lower_bound": True},
            {"fid": "a", "floors": 12.0, "lower_bound": False},   # same count, base seen
            {"fid": "b", "floors": 9.5, "lower_bound": False},    # under 10
            {"fid": "c", "floors": 30.0, "lower_bound": True},
            {"fid": "d", "floors": 25.0, "lower_bound": True},
            {"fid": None, "floors": 40.0, "lower_bound": False}]  # no plot
    got = fl.high_rise_plots(ents, low={"d"})
    assert got == {"a": (12.0, False), "c": (30.0, True)}


def test_floor_readings_weigh_by_sigma_and_keep_the_seed():
    cal = fl.StoreyCalibration(4.0, 6, 0.12)
    ents = [{"fid": "a", "seed": "seed_1", "floors": 20.0, "lower_bound": False, "spread": 0.05},
            {"fid": "b", "seed": "seed_4", "floors": 15.0, "lower_bound": True, "spread": 0.0}]
    got = fl.floor_readings(ents, cal)
    (ra,), (rb,) = got["floors:seed_1"], got["floors:seed_4"]
    assert ra["height_m"] == pytest.approx(83.0) and ra["kind"] == "floors" and ra["seed"] == "seed_1"
    assert ra["dist_m"] == pytest.approx(3300 * math.hypot(0.12, 0.05))
    assert rb["lower_bound"] and rb["dist_m"] == pytest.approx(3300 * 0.12)
    assert fl.floor_readings(ents, fl.StoreyCalibration(4.0, 6, 0.6)) == {}   # unreliable


def test_high_rise_hook_only_raises_the_prior():
    info = {"high_rise_seen": True, "floors": 20.0, "storey_m": 4.0}
    assert fl.high_rise_height(12.0, info) == pytest.approx(83.0)
    assert fl.high_rise_height(100.0, info) == 100.0
    assert fl.high_rise_height(12.0, dict(info, storey_m=None)) == pytest.approx(20 * 3.1 + 3)
    assert fl.high_rise_height(12.0, dict(info, high_rise_seen=False)) is None
    assert fl.high_rise_height(12.0, None) is None
