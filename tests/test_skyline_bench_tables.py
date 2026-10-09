"""Review 2026-10-09 item 1: the benchmark's band x tier / band x method tables, the pipeline
metrics and single-source (survey-only) scoring (``benchmark.bench_tables``), on hand-made rows."""

import math

import pytest

from city2stl.skyline import benchmark as bm


def _row(key, pred, tier, **kw):
    return {"key": key, "no_survey_height_m": pred, "effective_height_m": pred,
            "no_survey_tier": tier, "tier": tier, "n_views": 1, "n_seeds": 1,
            "centroid_lat": 25.0, "centroid_lon": -80.0, **kw}


def _truth(status="confirmed", **heights):
    return {k: {"status": status, "truth_m": v} for k, v in heights.items()}


def test_band_errors_metrics():
    e = bm.band_errors([10.0, 12.0, 30.0, 100.0], [10.0, 10.0, 20.0, 120.0])
    # errors 0, +2, +10, -20; tol = max(25 %, 2 m): 2.5, 2.5, 5, 30
    assert e["n"] == 4 and e["mae_m"] == 8.0 and e["median_ae_m"] == 6.0
    assert e["bias_m"] == -2.0
    assert e["p90_ae_m"] == pytest.approx(17.0)
    assert e["tol"] == 0.75 and e["over_5m"] == 0.5 and e["over_10m"] == 0.25
    assert bm.band_errors([], []) == {"n": 0}


def test_tol_has_a_two_metre_floor():
    assert bm.tol_m(4.0) == 2.0 and bm.tol_m(40.0) == 10.0
    assert bm.band_errors([5.9], [4.0])["tol"] == 1.0    # 1.9 m off a 4 m house: right


@pytest.mark.parametrize("h,review,eubucco", [
    (0.0, "<15", "0-5"), (4.99, "<15", "0-5"), (5.0, "<15", "5-10"), (14.99, "<15", "10-20"),
    (15.0, "15-40", "10-20"), (20.0, "15-40", "20+"), (40.0, "40-100", "20+"),
    (100.0, ">100", "20+"), (250.0, ">100", "20+")])
def test_bands(h, review, eubucco):
    assert bm.band_of(h) == review
    assert bm.band_of(h, bm.EUBUCCO_BANDS, bm.EUBUCCO_LABELS) == eubucco


def test_band_labels():
    assert bm.REVIEW_LABELS == ("<15", "15-40", "40-100", ">100")
    assert bm.EUBUCCO_LABELS == ("0-5", "5-10", "10-20", "20+")
    assert bm.DISTANCE_LABELS == ("<500", "500-1000", ">1000")


def test_truth_mode():
    rows = [_row("a", 10, "prior"), _row("b", 50, "tag")]
    assert bm.truth_mode(rows, {"a": {"status": "confirmed"}, "b": {"status": "survey_only"}}) \
        == bm.TWO_SOURCE
    assert bm.truth_mode(rows, {"a": {"status": "disputed"}}) == bm.TWO_SOURCE
    assert bm.truth_mode(rows, {"a": {"status": "survey_only"}}) == bm.SINGLE_SOURCE
    assert bm.truth_mode(rows, {"z": {"status": "confirmed"}}) == bm.NO_TRUTH  # not a row


def test_two_source_scores_confirmed_only_and_matches_the_headline():
    rows = [_row("a", 12, "prior"), _row("b", 55, "tag"), _row("c", 140, "verified_2"),
            _row("d", 30, "single")]
    truth = {**_truth(a=10.0, b=50.0, c=120.0), **_truth("survey_only", d=31.0)}
    t = bm.bench_tables(rows, truth)
    assert t["truth_mode"] == bm.TWO_SOURCE and t["n_scored_truth"] == 3
    head = bm.score_buildings(rows, truth, pred_field="no_survey_height_m")["overall"]
    allc = t["band_tier"]["all"]["all"]
    assert allc["n"] == head["n"] == 3 and allc["mae_m"] == head["mae_m"]
    assert allc["bias_m"] == head["bias_m"]
    assert t["band_tier"]["<15"]["prior"]["n"] == 1
    assert t["band_tier"]["40-100"]["tag"]["mae_m"] == 5.0
    assert t["band_tier"][">100"]["verified_2"]["bias_m"] == 20.0
    assert t["band_tier"]["15-40"] == {}                        # no truth there
    assert t["band_tier_eubucco"]["10-20"]["prior"]["n"] == 1      # 10 m: [10, 20)


def test_single_source_scores_survey_only_and_drops_buildings_newer_than_the_survey():
    rows = [_row("a", 12, "prior"), _row("b", 55, "tag"), _row("new", 90, "single")]
    truth = {"a": {"status": "survey_only", "truth_m": 10.0, "temporal": "predates"},
             "b": {"status": "survey_only", "truth_m": 50.0, "temporal": "unknown"},
             "new": {"status": "survey_only", "truth_m": 4.0, "temporal": bm.TEMPORAL_MAY_POSTDATE}}
    t = bm.bench_tables(rows, truth)
    assert t["truth_mode"] == bm.SINGLE_SOURCE and t["truth_status"] == "survey_only"
    assert t["n_scored_truth"] == 2 and t["excluded"] == {"may_postdate": 1}
    assert t["truth_age"] == {"predates": 1, "unknown": 1, "may_postdate": 1}
    assert bm.score_buildings(rows, truth)["overall"] == {"n": 0}     # the headline: as before
    scored, _ = bm.scoring_truth(truth, bm.SINGLE_SOURCE)
    ss = bm.score_buildings(rows, scored, pred_field="no_survey_height_m",
                            statuses=("survey_only",))
    assert ss["overall"]["n"] == 2 and ss["overall"]["mae_m"] == 3.5


def test_method_readings_and_distance_bands(monkeypatch):
    seeds = {"seed_1": (25.0, -80.0 + 300 / (bm.M_PER_DEG_LAT * math.cos(math.radians(25.0)))),
             "auto_090_1400m": (25.0 + 1400 / bm.M_PER_DEG_LAT, -80.0)}
    row = _row("a", 20.0, "prior", per_seed_median_m={"seed_1": 61.0, "auto_090_1400m": 40.0,
                                                      "commons_2": 33.0},
               floors=10, storey_m=3.0, satellite={"lean": [52.0, 0.8], "shadow": [30.0, 0.2]},
               height_tag_m=48.0, height_source="osm_tag",
               no_survey_source="withheld:prior_gbm")
    got = {(r["method"], None if r["dist_m"] is None else round(r["dist_m"]))
           : r["value_m"] for r in bm.method_readings(row, elevated={"seed_1"}, seeds=seeds)}
    assert got == {("drone", 300): 61.0, ("street", 1400): 40.0, ("photo", None): 33.0,
                   ("floors", None): 30.0, ("lean", None): 52.0, ("shadow", None): 30.0,
                   ("osm_tag", None): 48.0, ("prior", None): 20.0}
    truth = {"a": {"status": "confirmed", "truth_m": 50.0,
                   "roof_m": {"p50": 44.0, "p70": 46.0, "p90": 49.0, "p95": 50.0, "max": 58.0}}}
    tab = bm.band_method_table([row], truth, elevated={"seed_1"}, seeds=seeds)["40-100"]
    assert tab["drone"]["<500"]["mae_m"] == 11.0 and tab["street"][">1000"]["n"] == 1
    assert set(tab["lean"]) == {"all"}                          # no camera: no distance band
    assert tab["drone"]["<500"]["matched"] == {"stat": "max", "n": 1, "mae_m": 3.0,
                                               "bias_m": 3.0, "tol": 1.0}
    assert tab["floors"]["all"]["matched"]["stat"] == "p70"      # 30 vs 46
    assert tab["floors"]["all"]["matched"]["mae_m"] == 16.0
    assert tab["osm_tag"]["all"]["matched"]["stat"] == "p95"


def test_matched_truth_shifts_two_source_truth_by_the_survey_statistics():
    rec = {"truth_m": 51.0, "survey_roof_m": {"p70": 46.0, "p95": 50.0, "max": 58.0}}
    assert bm.matched_truth(rec, "max") == 59.0 and bm.matched_truth(rec, "p70") == 47.0
    assert bm.matched_truth(rec, "p95") == 51.0
    assert bm.matched_truth({"truth_m": 51.0}, "max") is None


def test_attach_roof_stats_from_the_survey_cache():
    truth = {"a": {"truth_m": 50.0}, "b": {"truth_m": 9.0, "roof_m": {"p95": 9.0}}}
    cache = {"a": {"roof_m": {"p95": 50.0, "max": 55.0}, "ground_p5_m": 0.1},
             "b": {"roof_m": {"p95": 99.0}}}
    got = bm.attach_roof_stats(truth, cache)
    assert got["a"]["roof_m"]["max"] == 55.0 and got["a"]["ground_p5_m"] == 0.1
    assert got["b"]["roof_m"] == {"p95": 9.0}                    # its own stats are kept
    assert "roof_m" not in truth["a"]                            # input untouched


def test_pipeline_metrics():
    rows = [
        _row("ok2", 52.0, "verified_2"),                         # corroborated, right
        _row("bad2", 90.0, "verified_2"),                        # corroborated, wrong
        _row("single", 21.0, "single"),                          # published reading, right
        _row("held", 10.0, "prior", single_reading_m=45.0,       # withheld single: wrong
             no_survey_source="withheld:prior_gbm", street_view_m=11.0),  # + street: right
        _row("tag", 30.0, "tag"),
        _row("notruth", 30.0, "prior"),
    ]
    truth = _truth(ok2=50.0, bad2=60.0, single=20.0, held=10.0, tag=31.0)
    p = bm.pipeline_metrics(rows, truth)
    assert p["coverage"] == {"rows": 6, "non_prior": 4, "share": 0.667, "rows_with_truth": 5,
                             "non_prior_with_truth": 4, "share_with_truth": 0.8}
    assert p["false_corroborated"] == {"n": 2, "wrong": 1, "rate": 0.5}
    w = p["withhold"]["all"]
    # readings: 3 published (ok2, bad2 wrong, single) + 2 withheld (single wrong, street right)
    assert w["readings"] == 5 and w["withheld"] == 2 and w["withheld_wrong"] == 1
    assert w["precision"] == 0.5 and w["wrong"] == 2 and w["recall"] == 0.5
    assert p["withhold"]["street"]["withheld"] == 1 and p["withhold"]["street"]["precision"] == 0.0
    assert p["withhold"]["single"]["precision"] == 1.0


def test_corroborated_tier_name_counts_too():
    rows = [_row("a", 90.0, "corroborated")]
    assert bm.pipeline_metrics(rows, _truth(a=60.0))["false_corroborated"]["wrong"] == 1


def test_seed_positions_from_site_and_auto_proposals(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path / "benchmark")
    (tmp_path / "auto_proposals").mkdir()
    (tmp_path / "auto_proposals" / "miami.json").write_text(json.dumps(
        {"bbox_nsew": [0, 0, 0, 0], "points": [{"name": "auto_180_0900m", "lat": 25.76,
                                                 "lon": -80.18}]}), encoding="utf-8")
    pos = bm.seed_positions("miami")                     # the real site file: seed_urls
    assert pos["auto_180_0900m"] == (25.76, -80.18)
    assert pos["seed_1"] == pytest.approx((25.7753, -80.1868))
