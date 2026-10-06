"""Web image seeds: placement (no network; the fetchers are mocked)."""

import numpy as np

from city2stl.skyline import web_image_seed as wis

# Cartagena's region box (sites/cartagena.json), north, south, east, west
BOX = (10.4295, 10.3845, -75.5221, -75.5679)


def _mock(monkeypatch, items):
    monkeypatch.setattr(wis, "_wikipedia_lead_image", lambda city: [])
    monkeypatch.setattr(wis, "_wikimedia_search", lambda city, n=3: items)
    monkeypatch.setattr(wis, "_download_image", lambda url, cache: np.zeros((4, 4, 3), np.uint8))


def test_a_web_seed_far_outside_the_region_is_dropped(monkeypatch):
    """Images without GPS take the curated viewpoint, which for Cartagena is 4.3 km east of
    the region: those seeds looked at the city from the wrong place."""
    _mock(monkeypatch, [{"url": "a", "title": "no gps"},
                        {"url": "b", "title": "in the box", "lat": 10.40, "lon": -75.55}])
    seeds, cache = wis.web_skyline_seeds("cartagena", max_images=3, bbox_nsew=BOX)
    assert [(s.lat, s.lon) for s in seeds] == [(10.40, -75.55)]
    assert set(cache) == {s.name for s in seeds}


def test_without_a_box_the_viewpoint_still_places_seeds(monkeypatch):
    _mock(monkeypatch, [{"url": "a", "title": "no gps"}])
    seeds, _ = wis.web_skyline_seeds("cartagena", max_images=3)
    assert len(seeds) == 1


def test_outside_distance():
    assert wis._outside_m(10.40, -75.55, BOX) == 0.0
    assert 4000 < wis._outside_m(10.3932, -75.4832, BOX) < 4600
