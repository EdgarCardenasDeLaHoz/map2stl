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
