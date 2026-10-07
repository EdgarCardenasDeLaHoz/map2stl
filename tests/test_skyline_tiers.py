"""F-SKY26 2a: verification tiers (``_core/tiers.py``) and their wiring into published rows."""

import json

import pytest

from city2stl.skyline._core.height import withhold_untagged_street_view
from city2stl.skyline._core.tiers import (
    INDEPENDENT,
    agree,
    independent,
    reading,
    tier_counts,
    tier_fields,
    verification_tier,
)
from city2stl.skyline._core.types import BuildingRecord


def R(kind, v, seed=None):
    return reading(kind, v, seed)


# --------------------------------------------------------------------------- independence

@pytest.mark.parametrize("a, b, expected", [
    # drone + drone: different seeds only
    (R("drone", 50, "seed_1"), R("drone", 52, "seed_2"), True),
    (R("drone", 50, "seed_1"), R("drone", 52, "seed_1"), False),
    (R("drone", 50), R("drone", 52), False),                         # seeds unknown
    # drone + satellite (lean, multiview, stereo): satellite at 40 m or more
    (R("drone", 60, "seed_1"), R("lean", 58), True),
    (R("drone", 30, "seed_1"), R("lean", 30), False),
    (R("drone", 60, "seed_1"), R("multiview", 40), True),
    (R("drone", 39, "seed_1"), R("multiview", 39.9), False),
    (R("stereo", 45), R("drone", 44, "seed_2"), True),
    (R("stereo", 35), R("drone", 35, "seed_2"), False),
    # lean + shadow: over 100 m only
    (R("lean", 150), R("shadow", 140), True),
    (R("shadow", 90), R("lean", 95), False),
    # floors + drone: different seeds only
    (R("floors", 40, "seed_1"), R("drone", 42, "seed_2"), True),
    (R("floors", 40, "seed_1"), R("drone", 42, "seed_1"), False),
    # never: shadow + shadow, stereo + stereo, street + street, street + drone, drone + shadow
    (R("shadow", 150), R("shadow", 150), False),
    (R("stereo", 150), R("stereo", 150), False),
    (R("street", 30, "seed_1"), R("street", 30, "seed_2"), False),
    (R("street", 30, "seed_1"), R("drone", 30, "seed_2"), False),
    (R("drone", 150, "seed_1"), R("shadow", 150), False),
])
def test_independent_rules(a, b, expected):
    assert independent(a, b) is expected
    assert independent(b, a) is expected                              # symmetric


def test_rule_table_lists_exactly_the_addendum_pairs():
    assert set(INDEPENDENT) == {frozenset(p) for p in (
        {"drone"}, {"drone", "lean"}, {"drone", "multiview"}, {"drone", "stereo"},
        {"lean", "shadow"}, {"floors", "drone"})}


def test_agree_is_25_percent_of_the_larger():
    assert agree(100, 75) and agree(75, 100)
    assert not agree(100, 74)


# --------------------------------------------------------------------------- tiers

def test_survey_beats_everything():
    assert verification_tier([R("drone", 50, "a"), R("drone", 50, "b")], 40.0, 51.0) == \
        ("survey", ["survey"])


def test_verified_2_from_two_drone_seeds():
    tier, methods = verification_tier([R("drone", 50, "seed_1"), R("drone", 55, "seed_2")])
    assert tier == "verified_2" and methods == ["drone:seed_1", "drone:seed_2"]


def test_correlated_pairs_do_not_verify():
    for rs in ([R("shadow", 150), R("shadow", 148)], [R("stereo", 150), R("stereo", 150)],
               [R("street", 30, "s1"), R("street", 31, "s2")],
               [R("floors", 40, "seed_1"), R("drone", 41, "seed_1")]):
        assert verification_tier(rs)[0] == "single"


def test_disagreeing_independent_readings_stay_single():
    assert verification_tier([R("drone", 50, "seed_1"), R("drone", 90, "seed_2")])[0] == "single"


def test_tag_stands_unless_the_pair_agrees_with_it():
    pair = [R("drone", 50, "seed_1"), R("drone", 52, "seed_2")]
    assert verification_tier(pair, tag_m=48.0)[0] == "verified_2"
    assert verification_tier(pair, tag_m=100.0) == ("tag", ["osm_tag"])
    assert verification_tier([R("drone", 50, "seed_1")], tag_m=100.0) == ("tag", ["osm_tag"])


def test_single_and_prior():
    assert verification_tier([R("drone", 50, "seed_1")]) == ("single", ["drone:seed_1"])
    assert verification_tier([]) == ("prior", [])


def test_tier_fields_dispute_and_prior_disagrees():
    pair = [R("drone", 50, "seed_1"), R("drone", 52, "seed_2")]
    f = tier_fields(pair, published_m=100.0, tag_m=100.0)
    assert f["tier"] == "tag" and f["disputed_by"] == ["drone:seed_1", "drone:seed_2"]
    assert f["verified"] is False
    f = tier_fields(pair, published_m=51.0)
    assert f["tier"] == "verified_2" and f["verified"] and f["disputed_by"] == []
    # a pair the published value disagrees with never verifies it
    f = tier_fields(pair + [R("drone", 200, "seed_3")], published_m=120.0)
    assert f["tier"] == "single" and f["disputed_by"]
    assert tier_fields([], published_m=14.0, prior_m=14.0, prior_source="prior_gbm") == {
        "tier": "prior", "tier_methods": ["prior_gbm"], "verified": False,
        "disputed_by": [], "prior_disagrees": False}
    # prior_disagrees: a single reading more than 2x from the prior, either way
    one = [R("drone", 50, "seed_1")]
    assert tier_fields(one, published_m=50.0, prior_m=20.0)["prior_disagrees"] is True
    assert tier_fields(one, published_m=50.0, prior_m=26.0)["prior_disagrees"] is False
    assert tier_fields(one, published_m=10.0, prior_m=21.0)["prior_disagrees"] is True
    assert tier_fields(pair, published_m=51.0, prior_m=10.0)["prior_disagrees"] is False


def test_tier_counts():
    rows = [{"tier": "tag"}, {"tier": "tag"}, {"tier": "prior"}, {}]
    assert tier_counts(rows) == {"survey": 0, "verified_2": 0, "tag": 2, "single": 0,
                                 "prior": 1, "unlabelled": 1}


# --------------------------------------------------------------------------- wiring

def _rec(fid, source, tag=None):
    return BuildingRecord(fid, fid, None, 25.77, -80.19, tag, source, 400.0)


def _fixture():
    """One row of each publishing path, as aggregate_building_heights hands them over."""
    rows = [
        {"feature_id": "tag", "effective_height_m": 95.0, "effective_height_source": "geometric",
         "per_seed_median_m": {"seed_1": 98.0, "seed_9": 60.0}},
        {"feature_id": "drone2", "effective_height_m": 40.0, "effective_height_source": "geometric",
         "per_seed_median_m": {"seed_1": 60.0, "seed_2": 64.0, "seed_9": 30.0}},
        {"feature_id": "drone1", "effective_height_m": 40.0, "effective_height_source": "geometric",
         "per_seed_median_m": {"seed_1": 61.0, "seed_9": 30.0}},
        {"feature_id": "street", "effective_height_m": 120.0, "effective_height_source": "f_sky1",
         "per_seed_median_m": {"seed_9": 120.0}},
    ]
    recs = [_rec("tag", "osm_tag", 100.0), _rec("drone2", "default"),
            _rec("drone1", "default"), _rec("street", "default")]
    return rows, recs


EXPECTED_PUBLISHED = {  # what the code before tiers published (commit a22afec)
    "tag": (100.0, "osm_tag"), "drone2": (62.0, "withheld:elevated"),
    "drone1": (61.0, "withheld:elevated"), "street": (14.0, "withheld:prior_gbm")}


def test_wiring_keeps_published_values_and_labels_tiers(monkeypatch):
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS"):
        monkeypatch.delenv(k, raising=False)
    rows, recs = _fixture()
    n = withhold_untagged_street_view(rows, recs, fallback=lambda r: (14.0, "prior_gbm"),
                                      measured_seeds={"seed_1", "seed_2"})
    assert n == 4
    by = {r["feature_id"]: r for r in rows}
    assert {k: (r["effective_height_m"], r["effective_height_source"]) for k, r in by.items()} \
        == EXPECTED_PUBLISHED
    assert by["tag"]["tier"] == "tag"
    assert by["drone2"]["tier"] == "verified_2" and by["drone2"]["verified"]
    assert by["drone2"]["tier_methods"] == ["drone:seed_1", "drone:seed_2"]
    assert by["drone1"]["tier"] == "single" and by["drone1"]["prior_disagrees"]  # 61 vs 14
    assert by["street"]["tier"] == "prior" and by["street"]["tier_methods"] == ["prior_gbm"]
    for r in rows:
        assert set(r) >= {"tier", "tier_methods", "verified", "disputed_by", "prior_disagrees"}
    json.dumps(rows)                                                   # serialisable


def test_flag_off_labels_street_view_as_single(monkeypatch):
    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "0")
    rows, recs = _fixture()
    assert withhold_untagged_street_view(rows, recs, measured_seeds={"seed_1"}) == 0
    assert rows[3]["effective_height_m"] == 120.0 and rows[3]["tier"] == "single"
    assert rows[3]["tier_methods"] == ["street:seed_9"]


def test_heights_json_has_schema_2_and_tier_counts(tmp_path):
    from shapely.geometry import Polygon

    from city2stl.skyline.region_pdf import _write_heights_json

    class BBox:
        north, south, east, west = 10.43, 10.38, -75.52, -75.57

    poly = Polygon([(-75.553, 10.402), (-75.5526, 10.402), (-75.5526, 10.4024), (-75.553, 10.4024)])
    recs = [BuildingRecord("a", "Measured", poly, 10.4022, -75.5528, None, "default", 900.0),
            BuildingRecord("b", "Allure", poly, 10.4022, -75.5528, 190.0, "osm_tag", 900.0)]
    rows = [{"feature_id": "a", "effective_height_m": 12.0,
             "effective_height_source": "withheld:default", "tier": "prior"}]
    path = tmp_path / "heights.json"
    _write_heights_json(path, region_name="x", bbox=BBox, building_heights=rows,
                        building_records=recs, known_heights=None)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 2
    assert doc["tier_counts"] == {"survey": 0, "verified_2": 0, "tag": 1, "single": 0, "prior": 1}
    allure = next(b for b in doc["buildings"] if b["feature_id"] == "b")
    assert allure["tier"] == "tag" and allure["effective_height_m"] == 190.0


# --------------------------------------------------------------------------- 2c: survey-blind benchmark

def test_no_survey_fields_always_written(monkeypatch):
    for flag in ("1", "0"):
        monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", flag)
        monkeypatch.delenv("SKYLINE_PREFER_TAGS", raising=False)
        rows, recs = _fixture()
        withhold_untagged_street_view(rows, recs, fallback=lambda r: (14.0, "prior_gbm"),
                                      measured_seeds={"seed_1", "seed_2"})
        for r in rows:
            assert r["no_survey_height_m"] == r["effective_height_m"]
            assert r["no_survey_source"] == r["effective_height_source"]
            assert r["no_survey_tier"] == r["tier"]


def _scored():
    truth = {"a": {"status": "confirmed", "truth_m": 50.0, "tiles_m": 50.0},
             "b": {"status": "confirmed", "truth_m": 20.0, "tiles_m": 21.0},
             "c": {"status": "survey_only", "truth_m": 30.0, "tiles_m": None},
             "d": {"status": "disputed", "truth_m": None, "tiles_m": 100.0, "survey_m": 60.0}}
    buildings = [
        {"key": "a", "effective_height_m": 52.0, "no_survey_height_m": 52.0, "tier": "verified_2"},
        {"key": "b", "effective_height_m": 40.0, "no_survey_height_m": 40.0, "tier": "prior"},
        {"key": "c", "effective_height_m": 30.0, "no_survey_height_m": 12.0, "tier": "survey"},
        # a survey row on a disputed footprint: scored against 3D Tiles only (60 vs 100)
        {"key": "d", "effective_height_m": 60.0, "no_survey_height_m": 12.0, "tier": "survey"},
    ]
    return buildings, truth


def test_score_buildings_scores_the_survey_blind_field_and_falls_back():
    from city2stl.skyline import benchmark as bm
    buildings, truth = _scored()
    buildings[0]["no_survey_height_m"] = 25.0                     # differs from published
    s = bm.score_buildings(buildings, truth, pred_field="no_survey_height_m")
    assert s["overall"]["n"] == 2 and s["overall"]["mae_m"] == pytest.approx((25 + 20) / 2)
    old = [{k: v for k, v in b.items() if k != "no_survey_height_m"} for b in buildings]
    assert bm.score_buildings(old, truth, pred_field="no_survey_height_m")["overall"] == \
        bm.score_buildings(old, truth)["overall"]                 # pre-2c report: published


def test_score_by_tier_and_survey_rows():
    from city2stl.skyline import benchmark as bm
    buildings, truth = _scored()
    t = bm.score_by_tier(buildings, truth)
    assert t["verified_2"]["n"] == 1 and t["verified_2"]["within_25pct"] == 1.0
    assert t["prior"]["within_25pct"] == 0.0
    assert t["survey"]["n"] == 1 and t["survey"]["mae_m"] == 40.0   # d vs tiles; c has none
    sr = bm.score_survey_rows(buildings, truth)
    assert sr["n_survey_rows"] == 2 and sr["vs_tiles"]["n"] == 1
    truth["d"]["tiles_flat"] = True                                  # flat mesh: not truth
    assert bm.score_survey_rows(buildings, truth)["vs_tiles"] == {"n": 0}


def test_label_tiers_on_an_old_report():
    from city2stl.skyline import benchmark as bm
    old = [
        {"key": "t", "effective_height_m": 100.0, "effective_height_source": "osm_tag",
         "height_source": "osm_tag", "height_tag_m": 100.0, "per_seed_median_m": {"s9": 60.0}},
        {"key": "p", "effective_height_m": 14.0, "effective_height_source": "withheld:prior_gbm",
         "height_source": "default", "per_seed_median_m": {"s9": 90.0}},
        {"key": "e", "effective_height_m": 51.0, "effective_height_source": "withheld:elevated",
         "height_source": "default", "per_seed_median_m": {"seed_1": 50.0, "seed_2": 52.0}},
        {"key": "g", "effective_height_m": 33.0, "effective_height_source": "geometric",
         "height_source": "default", "per_seed_median_m": {"s9": 33.0}},
        {"key": "x", "tier": "survey", "effective_height_m": 9.0},
    ]
    got = {b["key"]: b for b in bm.label_tiers(old, {"seed_1", "seed_2"})}
    assert got["t"]["tier"] == "tag"
    assert got["p"]["tier"] == "prior" and got["p"]["tier_methods"] == ["prior_gbm"]
    assert got["e"]["tier"] == "verified_2"
    assert got["g"]["tier"] == "single" and got["g"]["tier_methods"] == ["street:s9"]
    assert got["x"]["tier"] == "survey"                              # labelled rows kept
