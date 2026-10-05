"""Photo cards on the benchmark page (``photo_cards``): the photo with its towers numbered and a
location map with the same numbers, on a synthetic city."""

import math

import numpy as np

from city2stl.skyline import photo_cards as pc
from city2stl.skyline import photo_heights as ph
from city2stl.skyline import skyline_match as sm

LAT0, LON0 = 25.77, -80.19


def _city(seed=5, n=40):
    rng = np.random.default_rng(seed)
    verts, hs = [], []
    for _ in range(n):
        cx, cy = rng.uniform(-1000, 1000, 2)
        w, d = rng.uniform(20, 60, 2)
        verts.append(np.array([[cx - w, cy - d], [cx + w, cy - d], [cx + w, cy + d], [cx - w, cy + d],
                               [cx - w, cy - d]]))
        hs.append(rng.uniform(40, 250))
    return sm.Towers(LAT0, LON0, verts, np.array(hs), [f"t{i}" for i in range(n)])


def _photo(towers, cam_xy, heading_deg, hfov_deg, width=1200, height=800, tilt_deg=2.0):
    model = sm.predicted_outline(towers, cam_xy)
    f = (width / 2) / math.tan(math.radians(hfov_deg / 2))
    az = np.degrees(np.arctan((np.arange(width) + 0.5 - width / 2) / f))
    el = model[np.round((heading_deg + az) / sm.BIN_DEG).astype(int) % sm.N_BINS]
    return sm.PhotoProfile(np.where(el > 0, height / 2 - f * np.tan(np.radians(el + tilt_deg)), np.nan),
                           width, height)


def _features(towers):
    return [{"geometry": {"type": "Polygon",
                          "coordinates": [[list(towers.to_ll(x, y))[::-1] for x, y in v]]}}
            for v in towers.verts]


def test_background_polygons_are_in_the_tower_frame():
    towers = _city()
    polys, cent = pc.background_polygons(_features(towers), towers)
    assert len(polys) == len(towers.verts) and cent.shape == (len(polys), 2)
    assert np.allclose(polys[3], towers.verts[3], atol=0.05)


def test_render_photo_card_numbers_the_towers_left_to_right(tmp_path):
    towers = _city()
    cam, heading, fov = (-2400.0, 300.0), 95.0, 40.0
    prof = _photo(towers, cam, heading, fov)
    pose = ph.PhotoPose(*towers.to_ll(*cam), heading, fov)
    ms = ph.measure_towers(prof, towers, pose)
    assert len(ms) >= 4
    rows = [{"tower": m.index, "name": m.name} for m in reversed(ms)]
    out = pc.render_photo_card(np.full((800, 1200, 3), 200, np.uint8), prof, pose, 2.0, towers, rows,
                               pc.background_polygons(_features(towers), towers),
                               tmp_path / "assets" / "photos", "p1")
    assert out["overlay"] == "assets/photos/p1_osm.jpg" and out["map"] == "assets/photos/p1_map.png"
    assert (tmp_path / out["overlay"]).stat().st_size > 0 and (tmp_path / out["map"]).stat().st_size > 0
    labels = out["labels"]
    assert sorted(labels.values()) == list(range(1, len(ms) + 1))
    centre = {m.index: (m.x0 + m.x1) / 2 for m in ms}
    by_label = sorted(labels, key=labels.get)
    assert [centre[i] for i in by_label] == sorted(centre.values())


def test_photos_html_shows_the_numbered_photo_and_map():
    from city2stl.skyline.benchmark_report import _photos_html

    card = {"title": "Skyline.jpg", "page": "https://commons/x", "thumb": "https://thumb/x",
            "overlay": "assets/photos/p1_osm.jpg", "map": "assets/photos/p1_map.png",
            "camera": {"lat": 25.78, "lon": -80.18, "heading_deg": 215, "hfov_deg": 30, "source": "s"},
            "status": "measured (labels)", "score": {},
            "towers": [{"tower": 7, "name": "Second", "label": 2, "dist_m": 900, "photo_m": 150.0,
                        "osm_m": 140.0, "truth_m": 145.0},
                       {"tower": 3, "name": "First", "label": 1, "dist_m": 800, "photo_m": 100.0,
                        "osm_m": 110.0, "truth_m": None}]}
    page = _photos_html([card])
    assert 'class="card wide"' in page
    assert "assets/photos/p1_osm.jpg" in page and "assets/photos/p1_map.png" in page
    assert "<th>#</th>" in page and "<th>OSM m</th>" in page
    assert page.index(">First<") < page.index(">Second<")          # rows in label order
    plain = _photos_html([{**card, "overlay": None, "map": None, "towers": []}])
    assert "https://thumb/x" in plain and 'class="card"' in plain


def test_a_tower_hidden_behind_something_in_front_is_flagged_and_left_out(monkeypatch):
    """A tree in front of one tower makes the outline there low, so that tower reads below 0 m:
    it is flagged hidden, the others are refitted without it, and it stays out of the medians."""
    import importlib

    pl = importlib.import_module("city2stl.skyline.scripts.17_photo_pipeline")
    towers = _city()
    cam, heading, fov = (-2400.0, 300.0), 95.0, 40.0
    prof = _photo(towers, cam, heading, fov)
    pose = ph.PhotoPose(*towers.to_ll(*cam), heading, fov)
    ms = ph.measure_towers(prof, towers, pose)
    victim = max(ms, key=lambda m: m.x1 - m.x0)
    y = prof.y_top.copy()
    y[victim.x0:victim.x1 + 1] = prof.height * 0.9          # the outline drops to a foreground tree
    monkeypatch.setitem(pl._W, "towers", towers)
    rows, dev = pl._measure(sm.PhotoProfile(y, prof.width, prof.height), pose, 5000.0)
    by = {r["tower"]: r for r in rows}
    assert by[victim.index].get("hidden") and by[victim.index]["photo_m"] <= 0
    assert not any(r.get("hidden") for r in rows if r["tower"] != victim.index)
    errs = [abs(r["photo_m"] - r["osm_m"]) for r in rows if not r.get("hidden")]
    assert np.median(errs) < 5.0 and dev == np.median(errs)  # the others are still right
    assert pl._hidden(by[victim.index]) and not pl._hidden({"photo_m": 12.0})
