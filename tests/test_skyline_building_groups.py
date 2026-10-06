"""Building identification by groups of buildings (synthetic skylines, no images or GPU)."""

import numpy as np

from city2stl.skyline import building_groups as bg
from city2stl.skyline import skyline_match as sm


def _towers(seed=7, n=60):
    rng = np.random.default_rng(seed)
    verts, hs = [], []
    for _ in range(n):
        cx, cy = rng.uniform(-1500, 1500), rng.uniform(-1500, 1500)
        w = rng.uniform(12, 30)
        verts.append(np.array([[cx - w, cy - w], [cx + w, cy - w], [cx + w, cy + w],
                               [cx - w, cy + w], [cx - w, cy - w]]))
        hs.append(rng.uniform(60, 200))
    return sm.Towers(10.40, -75.55, verts, np.array(hs), [f"t{i}" for i in range(n)])


def _render(towers, cam, h, heading, px_per_deg, W=1200, H=800, drop=(), extra=False):
    """An instance image of the skyline seen from ``cam``: each model segment's columns
    painted with its tower index + 1 up to its top; sky above."""
    segs = bg.model_segments(towers, cam, h)
    inst = np.zeros((H, W), np.int32)
    sky = np.ones((H, W), bool)
    horizon = H * 0.7
    for s in segs:
        if s.ident in drop:
            continue
        x0 = (s.centre - s.width / 2 - heading) * px_per_deg + W / 2
        x1 = (s.centre + s.width / 2 - heading) * px_per_deg + W / 2
        # wrap bearings near the image
        for k in (-360, 0, 360):
            a, b = int(round(x0 + k * px_per_deg)), int(round(x1 + k * px_per_deg))
            if b < 0 or a >= W:
                continue
            top = int(round(horizon - s.top * px_per_deg))
            inst[max(0, top):, max(0, a):min(W, b)] = s.ident + 1
            sky[max(0, top):, max(0, a):min(W, b)] = False
    sky[int(horizon):, :] = False
    if extra:                                   # a building OSM does not have
        inst[int(horizon) - 60:, 600:614] = 999
        sky[int(horizon) - 60:, 600:614] = False
    return inst, sky, segs


def test_groups_vote_for_the_camera_and_identify_the_towers():
    towers = _towers()
    cams = [(float(x), float(y), 2.0) for x in (-2500, -2200, -1900) for y in (-300, 0, 300)]
    true = 4                                    # (-2200, 0)
    index = bg.build_index(towers, cams)
    inst, sky, segs = _render(towers, cams[true][:2], 2.0, heading=90.0, px_per_deg=25.0)
    img = bg.image_segments(inst, sky)
    assert len(img) >= 5
    poses = bg.identify(img, inst.shape[1], index, towers)
    assert poses and poses[0].camera == true
    assert abs((poses[0].heading_deg - 90.0 + 180) % 360 - 180) <= 2.0
    right = sum(1 for i, (t, _n) in poses[0].ids.items() if t == i - 1)
    assert right >= 0.8 * len(poses[0].ids)


def test_a_missing_and_a_spurious_building_do_not_break_the_match():
    towers = _towers()
    cams = [(float(x), float(y), 2.0) for x in (-2500, -2200, -1900) for y in (-300, 0, 300)]
    index = bg.build_index(towers, cams)
    _i, _s, segs = _render(towers, cams[4][:2], 2.0, 90.0, 25.0)
    visible = [s for s in segs if abs((s.centre - 90 + 180) % 360 - 180) < 20]
    inst, sky, _ = _render(towers, cams[4][:2], 2.0, 90.0, 25.0, drop={visible[2].ident}, extra=True)
    poses = bg.identify(bg.image_segments(inst, sky), inst.shape[1], index, towers)
    assert poses and poses[0].camera == 4
