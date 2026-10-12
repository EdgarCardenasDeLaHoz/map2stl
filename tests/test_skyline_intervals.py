"""Audit 2026-10-09 item 9: split-conformal height intervals per tier x band and the tag bias
(``skyline/intervals.py``): the quantile rule, the fit's fallbacks, coverage on held-out rows,
and what the rows carry (intervals and a bias figure, never a corrected value)."""

import json
import math

import numpy as np
import pytest

from city2stl.skyline import benchmark as bm
from city2stl.skyline import intervals as iv


def _res(tier, band, pred, truth):
    return {"tier": tier, "band": band, "pred": pred, "truth": truth, "e": math.log(pred / truth)}


def _rows(n, tier="tag", pred=50.0, scale=0.1, bias=-0.05, seed=0):
    """``n`` residual records at a published ``pred`` whose log error is bias + N(0, scale):
    the truth is pred / exp(e)."""
    rng = np.random.default_rng(seed)
    out = []
    for e in bias + scale * rng.standard_normal(n):
        out.append(_res(tier, bm.band_of(pred), pred, pred / math.exp(e)))
    return out


def test_conformal_quantile_takes_the_ceil_rank():
    v = list(range(1, 11))                       # n = 10
    assert iv.conformal_quantile(v, 0.8) == 9    # ceil(11 * 0.8) = 9th smallest
    assert iv.conformal_quantile(v, 0.9) == 10   # ceil(11 * 0.9) = 10
    assert iv.conformal_quantile(v, 0.99) == 10  # capped at n
    with pytest.raises(ValueError):
        iv.conformal_quantile([], 0.8)


def test_fit_cells_bias_quantile_and_not_testable():
    res = _rows(200, pred=50.0, bias=-0.1) + _rows(5, pred=120.0)
    f = iv.fit(res, cities=["a"])
    c = f["cells"]["tag"]["40-100"]
    assert c["testable"] and c["n"] == 200
    assert c["bias_log"] == pytest.approx(-0.1, abs=0.03)
    assert c["q"]["0.8"] < c["q"]["0.9"]
    assert f["cells"]["tag"][">100"] == {"n": 5, "testable": False}
    assert f["cells"]["tag"]["all"]["n"] == 205
    assert f["n"] == 205 and f["cities"] == ["a"]


def test_cell_for_falls_back_to_the_tier_all_band_then_none():
    f = iv.fit(_rows(100, pred=50.0) + _rows(10, pred=120.0))
    assert iv.cell_for(f, "tag", "40-100")[1] == "40-100"
    assert iv.cell_for(f, "tag", ">100")[1] == "all"      # 10 rows: the tier's pooled cell
    assert iv.cell_for(f, "prior", "<15") == (None, None)  # no prior rows at all
    assert iv.cell_for(f, "verified_2", "40-100") == (None, None)


def test_interval_and_tag_bias_follow_the_bias_term():
    cell = {"bias_log": math.log(0.8), "q": {"0.8": 0.1}}     # published reads 0.8 x truth
    lo, hi = iv.interval(80.0, cell, 0.8)
    assert lo < 100.0 < hi                                   # centred on 80 / 0.8 = 100
    assert lo == pytest.approx(100.0 * math.exp(-0.1)) and hi == pytest.approx(100.0 * math.exp(0.1))
    assert iv.tag_bias_m(80.0, cell) == pytest.approx(-20.0)  # the tag sits 20 m below the truth


def test_held_out_coverage_near_nominal_when_exchangeable():
    train = _rows(2000, pred=50.0, scale=0.2, bias=-0.1, seed=1)
    test = _rows(1000, pred=50.0, scale=0.2, bias=-0.1, seed=2)
    f = iv.fit(train)
    cov = iv.coverage(f, test, 0.8)
    c = cov["tag"]["40-100"]
    assert c["testable"] and 0.75 <= c["covered"] <= 0.85
    assert abs(c["bias_after"]) <= 0.05 and c["pass"] is True
    assert cov["no_interval"] == 0
    bad = iv.coverage(f, _rows(100, pred=50.0, scale=0.2, bias=0.4, seed=3), 0.8)
    assert bad["tag"]["40-100"]["pass"] is False           # a different city's bias


def test_coverage_marks_small_bands_not_testable_and_counts_rows_without_a_cell():
    f = iv.fit(_rows(100, pred=50.0))
    cov = iv.coverage(f, _rows(10, pred=50.0, seed=5) + _rows(40, tier="prior", pred=8.0), 0.8)
    assert cov["tag"]["40-100"]["pass"] is None and not cov["tag"]["40-100"]["testable"]
    assert cov["no_interval"] == 40 and "prior" not in cov


def test_leave_one_out_fits_on_the_other_cities():
    by_city = {"a": _rows(100, seed=1), "b": _rows(100, seed=2), "c": _rows(100, seed=3)}
    loo = iv.leave_one_out(by_city, 0.8)
    assert set(loo) == {"a", "b", "c"}
    assert loo["a"]["tag"]["40-100"]["n"] == 100


def test_bias_table_per_tier_and_band():
    t = iv.bias_table(_rows(50, pred=50.0, bias=-0.2, scale=0.0) + _rows(20, tier="prior", pred=8.0))
    c = t["tag"]["40-100"]
    assert c["n"] == 50 and c["median_log_bias"] == pytest.approx(-0.2, abs=1e-3)
    assert c["bias_m"] == pytest.approx(50.0 - 50.0 / math.exp(-0.2), abs=0.01)
    assert c["tol"] == 1.0           # 50 m vs 61.1 m truth: 11.1 m off, inside 25 % (15.3 m)
    assert c["mae_m"] == pytest.approx(11.07, abs=0.01)
    assert t["prior"]["<15"]["n"] == 20 and t["tag"]["all"]["n"] == 50


def test_annotate_writes_intervals_and_tag_bias_but_never_changes_the_value():
    f = iv.fit(_rows(100, pred=50.0, bias=-0.1, scale=0.05) + _rows(100, tier="prior", pred=8.0))
    rows = [{"tier": "tag", "effective_height_m": 60.0, "tag_bias_m": 99.0},
            {"tier": "prior", "effective_height_m": 10.0},
            {"tier": "survey", "effective_height_m": 70.0, "interval_lo_m": 1.0},
            {"tier": "single", "effective_height_m": 30.0},
            {"tier": "tag", "effective_height_m": None}]
    assert iv.annotate(rows, f, 0.8) == 2
    tag, prior, survey, single, none = rows
    assert tag["effective_height_m"] == 60.0 and tag["interval_lo_m"] < 60.0 / math.exp(-0.1) < tag["interval_hi_m"]
    assert tag["tag_bias_m"] == pytest.approx(60.0 - 60.0 / math.exp(f["cells"]["tag"]["40-100"]["bias_log"]), abs=0.06)
    assert "interval_lo_m" in prior and "tag_bias_m" not in prior
    assert "interval_lo_m" not in survey                     # never on a survey row
    assert "interval_lo_m" not in single                     # no single rows in the fit
    assert "interval_lo_m" not in none


def test_annotate_report_round_trip(tmp_path):
    f = iv.fit(_rows(100, pred=50.0))
    f["cities"] = ["a", "b"]
    src = tmp_path / "heights.json"
    src.write_text(json.dumps({"region": "x", "buildings": [
        {"feature_id": "b1", "tier": "tag", "effective_height_m": 55.0},
        {"feature_id": "b2", "tier": "verified_2", "effective_height_m": 55.0}]}), encoding="utf-8")
    block = iv.annotate_report(src, f, 0.8, note="no truth here")
    data = json.loads(src.read_text(encoding="utf-8"))
    assert block["n_rows"] == 1 and data["intervals"]["note"] == "no truth here"
    assert data["intervals"]["fit_cities"] == ["a", "b"] and "40-100" in data["intervals"]["cells"]["tag"]
    assert "interval_lo_m" in data["buildings"][0] and "interval_lo_m" not in data["buildings"][1]
    assert data["buildings"][1]["tier"] == "corroborated"


def test_residuals_use_the_survey_blind_height_and_skip_nested_and_unscored():
    rows = [{"key": "k1", "tier": "survey", "effective_height_m": 70.0,
             "no_survey_height_m": 60.0, "no_survey_tier": "tag"},
            {"key": "k2", "tier": "tag", "effective_height_m": 20.0},
            {"key": "k3", "tier": "tag", "effective_height_m": 20.0},
            {"key": "k4", "tier": "tag", "effective_height_m": 20.0}]
    truth = {"k1": {"status": "confirmed", "truth_m": 65.0},
             "k2": {"status": "confirmed", "truth_m": 25.0},
             "k3": {"status": "confirmed", "truth_m": 25.0},
             "k4": {"status": "disputed", "truth_m": 25.0}}
    res = iv.residuals(bm.survey_blind(rows), truth, nested={"k3"})
    assert [(r["tier"], r["pred"], r["band"]) for r in res] == [("tag", 60.0, "40-100"), ("tag", 20.0, "15-40")]
    assert res[0]["e"] == pytest.approx(math.log(60.0 / 65.0))
