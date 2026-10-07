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


def _field_ndsm(bbox, resolution_m=1.0):
    """A provider's answer: an analytic roof field sampled at the cell centres of the
    ``lonlat_grid`` of ``bbox`` (what ČÚZK / CNIG return), so the values under a footprint
    move whenever the grid's phase or cell size does."""
    from city2stl.height.providers._survey import lonlat_grid

    h, wd, t = lonlat_grid(bbox, resolution_m)
    rows, cols = np.mgrid[0:h, 0:wd]
    lon, lat = t * (cols + 0.5, rows + 0.5)
    x_m, y_m = (lon - LON) * KX, (lat - LAT) * bm.M_PER_DEG_LAT
    return (20 + 40 * np.abs(np.sin(x_m / 7.3) * np.cos(y_m / 5.1))).astype(np.float32), t


def test_a_footprint_reads_the_same_whatever_else_is_in_the_run(monkeypatch):
    # F-SKY26 2b finding: tiles were the union of the run's footprints, so the raster under a
    # footprint (cell size, phase) and its p95 changed with the others (Prague: 30 of 79 equal)
    monkeypatch.setattr(bm, "survey_ndsm", lambda p, b, r=1.0: _field_ndsm(b, r))
    target = {"t": _ring(37.3, 11.9, 23.0)}
    sets = [target,
            {**target, "w": _ring(-310.0, -150.0)},                     # widens the old tile
            {**target, "n": _ring(120.0, 260.0), "e": _ring(430.0, 40.0)},
            {**target, "big": _ring(-700.0, -40.0, 900.0)}]             # spans tiles: own tile
    got = []
    for i, fps in enumerate(sets):
        rec = sh.survey_footprint_heights(f"benidorm_{i}", fps, "cnig_mdsn")["t"]
        got.append((rec["survey_m"], rec["survey_cells"], rec["stat"]))
    assert len(set(got)) == 1 and got[0][2] == bm.STAT_VERSION
    assert got[0][0] is not None
    truth = bm.footprint_truth("benidorm", sets[2], "cnig_mdsn", use_tiles=False)["t"]
    assert (truth["survey_m"], truth["survey_cells"], truth["stat"]) == got[0]


def test_old_stat_records_are_re_measured(monkeypatch):
    monkeypatch.setattr(bm, "survey_ndsm", lambda p, b, r=1.0: _field_ndsm(b, r))
    sh.save_cache("benidorm", {k: {"survey_m": 1.0, "survey_cells": 9, "provider": "cnig_mdsn",
                                   "years": None} for k in FOOTPRINTS})
    got = sh.survey_footprint_heights("benidorm", FOOTPRINTS)
    assert all(r["stat"] == bm.STAT_VERSION and r["survey_m"] != 1.0 for r in got.values())


def test_ept_project_follows_the_footprint_not_the_tile(monkeypatch):
    # Miami: a newer topobathy project clipping a tile's corner won the whole tile, which then
    # had no points. Each footprint now picks the newest project meeting itself.
    from city2stl.height.providers import lidar_3dep_ept_laspy as ept

    coast_lon = LON + 100 / KX                     # the "coast" project lies east of here

    def projects(bbox):
        n, s, e, w = bbox
        out = [{"name": "CITY_2018", "url": "", "year": 2018}]
        return ([{"name": "COAST_2019", "url": "", "year": 2019}] if e > coast_lon else []) + out

    reads = []

    def fake(provider, bbox, res=1.0, part=None):
        reads.append(part)
        arr, t = _synthetic_ndsm(bbox, res)
        return (arr * (0 if part == "COAST_2019" else 1)) + (0 if part else np.nan), t

    monkeypatch.setattr(ept, "projects_for_bbox", projects)
    monkeypatch.setattr(bm, "survey_ndsm", fake)
    got = sh.survey_footprint_heights("miami", FOOTPRINTS, "usgs_3dep_ept")
    assert sorted(reads) == ["CITY_2018", "COAST_2019"]          # one tile per project
    assert 12.0 < got["low"]["survey_m"] < 15.0                     # read from its own project
    assert got["low"]["years"] == [2018, 2018] and got["tall"]["years"] == [2019, 2019]
