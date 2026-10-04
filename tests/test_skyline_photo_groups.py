"""F-WEB2 step G: outline similarity between photos of the same spot."""

import numpy as np

from city2stl.skyline import photo_groups as pg


def _outline(seed, width=1600):
    """A random city outline: towers as plateaus of varying height and width."""
    rng = np.random.default_rng(seed)
    y = np.full(width, np.nan)
    x = 0
    while x < width:
        w = int(rng.integers(20, 120))
        if rng.random() < 0.8:
            y[x:x + w] = rng.uniform(200, 800)
        x += w
    return y


def _view(y, width, zoom, shift_cols, tilt_rows, out_width):
    """The same outline seen with another zoom, pan and tilt (cols scale with rows)."""
    out = np.full(out_width, np.nan)
    for x in range(out_width):
        src = int((x - shift_cols) / zoom)
        if 0 <= src < width and np.isfinite(y[src]):
            out[x] = y[src] * zoom + tilt_rows
    return out


def test_same_spot_matches_other_zoom_and_pan():
    a = _outline(1)
    b = _view(a, 1600, zoom=1.6, shift_cols=-500, tilt_rows=40, out_width=1600)
    s = pg.outline_similarity(a, 1600, b, 1600)
    assert s is not None and s.misfit < 0.05
    assert abs(1 / s.scale - 1.6) / 1.6 < 0.03     # B is zoomed 1.6x


def test_other_city_does_not_match():
    a, c = _outline(1), _outline(2)
    s = pg.outline_similarity(a, 1600, c, 1600)
    assert s is None or s.misfit > 0.3


def test_cliques_join_same_spot_only():
    a = _outline(3)
    profs = {"a": (a, 1600),
             "a_zoom": (_view(a, 1600, 1.3, -200, 10, 1600), 1600),
             "other": (_outline(4), 1600)}
    keys = list(profs)
    pairs = []
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            s = pg.outline_similarity(profs[ka][0], 1600, profs[kb][0], 1600)
            if s is not None:
                pairs.append((ka, kb, s.misfit, s.scale, s.shift, s.overlap))
    assert pg.cliques(pairs, set(keys)) == [{"a", "a_zoom"}]


def test_links_and_cliques_do_not_chain():
    pairs = [("u1", "L", 0.02, 1, 0, 1), ("u2", "u1", 0.03, 1, 0, 1),   # u2 only via u1
             ("u3", "u4", 0.04, 1, 0, 1), ("u4", "u5", 0.04, 1, 0, 1)]  # u3~u4~u5 but not u3~u5
    links = pg.link_to_located(pairs, {"L"})
    assert links == {"u1": ("L", 0.02)}
    groups = pg.cliques(pairs, {"u1", "u2", "u3", "u4", "u5"})
    assert {"u3", "u4", "u5"} not in groups and all(len(g) == 2 for g in groups)


def test_embed_links_need_both_signals():
    keys = ["L1", "L2", "u_good", "u_lookalike", "u_far"]
    v = np.array([[1, 0, 0], [0, 1, 0], [0.99, 0.14, 0], [0.98, 0.2, 0], [0, 0, 1]], float)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    pairs = [("u_good", "L1", 0.04, 1, 0, 1), ("u_lookalike", "L1", 0.4, 1, 0, 1),
             ("u_far", "L2", 0.03, 1, 0, 1)]
    got = pg.embed_links(keys, v, pairs, {"L1", "L2"}, min_cos=0.9, max_misfit=0.1)
    assert set(got) == {"u_good"} and got["u_good"][0] == "L1"
