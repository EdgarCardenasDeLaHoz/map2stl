"""F-SKY26 2b: per-footprint survey heights (``skyline/survey_heights.py``), on a synthetic nDSM."""

import json

import numpy as np
import pytest
from rasterio.transform import from_bounds

from city2stl.height.providers import survey
from city2stl.height.providers._survey import SurveyError
from city2stl.skyline import benchmark as bm
from city2stl.skyline import survey_heights as sh

# two 30 x 30 m blocks in Benidorm, ~200 m apart (one tile)
LAT, LON = 38.535, -0.13
KX = bm.M_PER_DEG_LAT * np.cos(np.radians(LAT))


def _ring(dx_m, dy_m, w_m=30.0):
    x0, y0 = LON + dx_m / KX, LAT + dy_m / bm.M_PER_DEG_LAT
    x1, y1 = x0 + w_m / KX, y0 + w_m / bm.M_PER_DEG_LAT
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


FOOTPRINTS = {"low": _ring(0, 0), "tall": _ring(200, 0)}


def _synthetic_ndsm(bbox, resolution_m=1.0):
    """Ground 0; 'low' block 12-15 m, 'tall' block 60-66 m (a gradient, so p95 is not the max)."""
    n, s, e, w = bbox
    h, wd = bm._grid_dim(bbox, resolution_m)
    t = from_bounds(w, s, e, n, wd, h)
    rows, cols = np.mgrid[0:h, 0:wd]
    lon, lat = t * (cols + 0.5, rows + 0.5)
    arr = np.zeros((h, wd), np.float32)
    for name, base, span in (("low", 12.0, 3.0), ("tall", 60.0, 6.0)):
        ring = np.array(FOOTPRINTS[name])
        inside = ((lon >= ring[:, 0].min()) & (lon <= ring[:, 0].max())
                  & (lat >= ring[:, 1].min()) & (lat <= ring[:, 1].max()))
        frac = (lon - ring[:, 0].min()) / (ring[:, 0].max() - ring[:, 0].min())
        arr[inside] = base + span * frac[inside]
    return arr, t


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    """Survey cache in tmp; any Google 3D Tiles use fails the test."""
    monkeypatch.setattr(sh, "SURVEY_ROOT", tmp_path / "survey")
    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path / "benchmark")

    def no_tiles(*_a, **_k):
        raise AssertionError("Google3DProvider must never be constructed")

    from city2stl.height.providers import google_3d
    monkeypatch.setattr(google_3d.Google3DProvider, "__init__", no_tiles)
    monkeypatch.setattr(bm, "tiles_ndsm", no_tiles)


def test_same_p95_as_the_benchmark_truth_and_cached(monkeypatch):
    calls = []

    def fake(provider, bbox, res=1.0):
        calls.append(provider)
        return _synthetic_ndsm(bbox, res)

    monkeypatch.setattr(bm, "survey_ndsm", fake)
    got = sh.survey_footprint_heights("benidorm", FOOTPRINTS)
    assert calls == ["cnig_mdsn"]                                   # the region's provider
    truth = bm.footprint_truth("benidorm", FOOTPRINTS, "cnig_mdsn", use_tiles=False)
    for k in FOOTPRINTS:
        assert got[k]["survey_m"] == truth[k]["survey_m"]           # one statistic
        assert got[k]["survey_cells"] == truth[k]["survey_cells"]
        assert got[k]["provider"] == "cnig_mdsn" and got[k]["years"] == [2008, 2015]
    assert 12.0 < got["low"]["survey_m"] < 15.0 and 60.0 < got["tall"]["survey_m"] < 66.0
    cached = json.loads(sh.cache_path("benidorm").read_text(encoding="utf-8"))
    assert set(cached) == set(FOOTPRINTS)
    assert sh.survey_footprint_heights("benidorm", FOOTPRINTS) == got  # from the cache
    assert calls == ["cnig_mdsn", "cnig_mdsn"]  # (the truth call above read once too)


def test_survey_error_is_returned_but_not_cached(monkeypatch):
    def down(*_a, **_k):
        raise SurveyError("IDEE WCS 503")

    monkeypatch.setattr(bm, "survey_ndsm", down)
    got = sh.survey_footprint_heights("benidorm", FOOTPRINTS)
    assert all(r["survey_m"] is None and "503" in r["error"] for r in got.values())
    assert not sh.cache_path("benidorm").exists() or sh.load_cache("benidorm") == {}
    monkeypatch.setattr(bm, "survey_ndsm", lambda p, b, r=1.0: _synthetic_ndsm(b, r))
    again = sh.survey_footprint_heights("benidorm", FOOTPRINTS)    # re-read, not pinned
    assert all(r["survey_m"] is not None and "error" not in r for r in again.values())


def test_not_covered_is_cached_as_none(monkeypatch):
    monkeypatch.setattr(bm, "survey_ndsm", lambda *a, **k: None)
    got = sh.survey_footprint_heights("benidorm", FOOTPRINTS)
    assert all(r["survey_m"] is None and r["years"] is None for r in got.values())
    assert set(sh.load_cache("benidorm")) == set(FOOTPRINTS)


def test_pick_provider(monkeypatch):
    assert sh.pick_provider((26, 25, -80, -81), "Miami, FL (2)") == "usgs_3dep_ept"
    avail = [{"name": "usgs_3dep", "available": True}, {"name": "usgs_3dep_ept", "available": True}]
    monkeypatch.setattr(survey, "available_for_bbox", lambda b: avail)
    assert sh.pick_provider((40, 39, -100, -101), "Nowhere") == "usgs_3dep_ept"  # preferred
    monkeypatch.setattr(survey, "available_for_bbox",
                        lambda b: [{"name": "cnig_mdsn", "available": True}])
    assert sh.pick_provider((40, 39, -3, -4)) == "cnig_mdsn"
    monkeypatch.setattr(survey, "available_for_bbox", lambda b: [])
    assert sh.pick_provider((0, -1, 1, 0)) is None
    assert sh.survey_footprint_heights("nowhere", {"a": _ring(0, 0)}) == {}


def test_years_for_bbox(monkeypatch):
    assert survey.years_for_bbox("cnig_mdsn", (40, 39, -3, -4)) == (2008, 2015)
    assert survey.years_for_bbox("usgs_3dep", (26, 25, -80, -81)) is None
    from city2stl.height.providers import lidar_3dep_ept_laspy as ept
    monkeypatch.setattr(ept, "projects_for_bbox", lambda b: [
        {"name": "FL_Miami_2021", "year": 2021}, {"name": "FL_2015", "year": 2015}])
    assert survey.years_for_bbox("usgs_3dep_ept", (26, 25, -80, -81)) == (2021, 2021)
    with pytest.raises(KeyError):
        survey.years_for_bbox("nope", (1, 0, 1, 0))
    assert set(survey.YEARS) == set(survey.PROVIDERS)
