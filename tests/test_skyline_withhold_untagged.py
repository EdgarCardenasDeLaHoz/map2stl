"""T28: Street View heights withheld on untagged buildings, kept for scoring."""

from city2stl.skyline import benchmark as bm
from city2stl.skyline._core.height import (
    UNTAGGED_FALLBACK_M,
    withhold_untagged_street_view,
)
from city2stl.skyline._core.types import BuildingRecord


def _rec(fid, source, tag=None):
    return BuildingRecord(fid, fid, None, 25.77, -80.19, tag, source, 400.0)


def _rows():
    return [{"feature_id": "t", "effective_height_m": 95.0, "effective_height_source": "geometric"},
            {"feature_id": "l", "effective_height_m": 40.0, "effective_height_source": "geometric"},
            {"feature_id": "u", "effective_height_m": 120.0, "effective_height_source": "f_sky1"},
            {"feature_id": "x", "effective_height_m": 50.0, "effective_height_source": "geometric"}]


RECS = [_rec("t", "osm_tag", 100.0), _rec("l", "osm_levels", 38.4), _rec("u", "default")]


def test_untagged_gets_the_fallback_and_keeps_the_street_view_value(monkeypatch):
    monkeypatch.delenv("SKYLINE_WITHHOLD_UNTAGGED", raising=False)
    rows = _rows()
    assert withhold_untagged_street_view(rows, RECS) == 1
    t, lv, u, x = rows
    assert t["effective_height_m"] == 95.0 and "street_view_m" not in t      # tagged
    assert lv["effective_height_m"] == 40.0 and "street_view_m" not in lv    # levels count
    assert u["effective_height_m"] == UNTAGGED_FALLBACK_M
    assert u["effective_height_source"] == "withheld:default"
    assert (u["street_view_m"], u["street_view_source"]) == (120.0, "f_sky1")
    assert "street_view_m" not in x                                        # no record: left
    assert withhold_untagged_street_view(rows, RECS) == 0                  # idempotent
    assert u["street_view_m"] == 120.0


def test_flag_off_and_custom_fallback(monkeypatch):
    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "0")
    rows = _rows()
    assert withhold_untagged_street_view(rows, RECS) == 0
    assert rows[2]["effective_height_m"] == 120.0
    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "1")
    assert withhold_untagged_street_view(rows, RECS, fallback=lambda r: (None, "gba")) == 0
    assert withhold_untagged_street_view(rows, RECS, fallback=lambda r: (14.5, "gba")) == 1
    assert rows[2]["effective_height_m"] == 14.5
    assert rows[2]["effective_height_source"] == "withheld:gba"


def test_benchmark_scores_both(monkeypatch):
    monkeypatch.delenv("SKYLINE_WITHHOLD_UNTAGGED", raising=False)
    rows = _rows()
    withhold_untagged_street_view(rows, RECS)
    for r, k in zip(rows, "abcd", strict=True):
        r["key"] = k
    truth = {k: {"status": "confirmed", "truth_m": h}
             for k, h in zip("abcd", (100.0, 38.0, 12.0, 50.0), strict=True)}
    published = bm.score_buildings(rows, truth)
    sv = bm.street_view_buildings(rows)
    unwithheld = bm.score_buildings(sv, truth)
    assert published["overall"]["mae_m"] < unwithheld["overall"]["mae_m"]
    assert rows[2]["effective_height_m"] == UNTAGGED_FALLBACK_M             # input unchanged
    assert bm.street_view_buildings([{"effective_height_m": 1.0}]) is None
