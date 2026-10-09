"""19_refresh_survey_truth helpers (review 2026-10-09 item 1): survey-only truth records, the p95
re-read check and the retirement of full-precision keys. No network, no survey reads."""

import importlib
import json

import pytest

from city2stl.skyline import benchmark as bm


@pytest.fixture
def script(monkeypatch, tmp_path):
    import city2stl.resources as res

    monkeypatch.setattr(res, "scratch_guard", lambda *a, **k: 1)   # no RAM wait in a test
    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path / "benchmark")
    return importlib.import_module("city2stl.skyline.scripts.19_refresh_survey_truth")


def test_truth_record_is_single_source_with_roof_stats_and_age(script):
    s = {"survey_m": 64.2, "survey_cells": 300, "years": [2018, 2018],
         "roof_m": {"p50": 60.0, "p70": 62.0, "p90": 63.9, "p95": 64.2, "max": 66.0},
         "ground_p5_m": 0.1, "roof_stats": bm.ROOF_STATS_VERSION}
    rec = script.truth_record(s, "USGS_LPC_PR_PRVI_E_2018", "way/1", {"way/1": "2019-03"})
    assert rec["status"] == "survey_only" and rec["truth_m"] == 64.2 and rec["tiles_m"] is None
    assert rec["tiles_read"] is False and rec["key_ring"] == "report"
    assert rec["roof_m"]["max"] == 66.0 and rec["ground_p5_m"] == 0.1
    assert rec["built_year"] == 2019 and rec["temporal"] == bm.TEMPORAL_MAY_POSTDATE
    no_dates = script.truth_record(s, None, None, None)
    assert "temporal" not in no_dates                     # unknown until --temporal runs
    unmeasured = script.truth_record({**s, "survey_m": None}, None, None, {})
    assert unmeasured["status"] == "unmeasured" and unmeasured["truth_m"] is None


def test_p95_agreement(script):
    before = {k: {"survey_m": v, "stat": bm.STAT_VERSION} for k, v in
              (("a", 10.0), ("b", 20.0), ("c", 30.0))}
    before["old"] = {"survey_m": 5.0}                          # stat 1: not compared
    before["none"] = {"survey_m": None, "stat": bm.STAT_VERSION}
    after = {"a": {"roof_m": {"p95": 10.0}}, "b": {"roof_m": {"p95": 20.004}},
             "c": {"roof_m": {"p95": 31.0}}, "old": {"roof_m": {"p95": 9.0}}}
    got = script.p95_agreement(before, after)
    assert got["compared"] == 3 and got["equal"] == 2 and got["changed"] == 1
    assert got["largest_changes_m"] == [1.0] and got["pass_99pct"] is False


def test_full_precision_keys_retire_once_the_report_key_exists(script):
    truth = {"k7a": {"truth_m": 1.0}, "k6a": {"truth_m": 1.0}, "k7b": {"truth_m": 2.0}}
    metas = [{"key7": "k7a", "key6": "k6a"}, {"key7": "k7b", "key6": "k6b"},
             {"key7": "same", "key6": "same"}]
    assert script._retire_full_precision_keys("san_juan", truth, metas) == 1
    assert set(truth) == {"k6a", "k7b"}                       # k7b has no report-key record yet
    moved = json.loads((bm.BENCHMARK_ROOT / "truth" / "san_juan.k7.json").read_text())
    assert moved == {"k7a": {"truth_m": 1.0, "superseded_by": "k6a"}}


def test_truth_areas_are_in_the_review_order(script):
    assert list(script.TRUTH_AREAS["san_juan"])[:3] == ["old_san_juan", "condado", "isla_verde"]
    assert list(script.TRUTH_AREAS["fort_lauderdale"])[0] == "ftl_beach"
    assert set(script.TRUTH_AREAS) <= set(bm.REGIONS)
