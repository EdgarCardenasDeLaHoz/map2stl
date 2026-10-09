"""F-SKY26 2a: verification tiers (``_core/tiers.py``) and their wiring into published rows."""

import json

import pytest

from city2stl.skyline._core.height import withhold_untagged_street_view
from city2stl.skyline._core.tiers import (
    INDEPENDENT,
    SUPPORT_RATIO,
    agree,
    independent,
    reading,
    single_support,
    single_withheld,
    tag_witness,
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


def test_agree_is_25_percent_of_the_smaller():
    # review item 5 (2026-10-09): |ln a/b| <= ln 1.25, symmetric; was 25 % of the larger (1.33)
    assert agree(100, 80) and agree(80, 100) and agree(125, 100)
    assert not agree(100, 75) and not agree(75, 100) and not agree(126, 100)
    # Palmetto: lean + shadow 136 against the tag 156 (1.15) still agrees
    assert agree(135.9, 156.0)
    assert agree(0, 0) and not agree(0, 5) and not agree(-5, 5)
    assert agree(100, 74, rel=0.40) and not agree(100, 90, rel=0.05)    # an explicit tolerance


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


# --------------------------------------------------------------------------- tag + one reading

@pytest.mark.parametrize("r", [R("drone", 170, "seed_1"), R("lean", 168), R("multiview", 175),
                               R("stereo", 160)])
def test_osm_tag_verified_by_one_reading(r):
    tier, methods = verification_tier([r], tag_m=190.0, tag_source="osm_tag")
    want = "drone:seed_1" if r["seed"] else r["kind"]
    assert tier == "verified_2" and methods == ["osm_tag", want]
    f = tier_fields([r], published_m=190.0, tag_m=190.0, tag_source="osm_tag")
    assert f["tier"] == "verified_2" and f["verified"] and f["disputed_by"] == []


@pytest.mark.parametrize("readings,source", [
    ([R("shadow", 185)], "osm_tag"),                     # a lower bound
    ([R("drone", 185, "seed_1")], "osm_levels"),         # a level count, not a height tag
    ([R("drone", 133, "seed_1")], "osm_tag"),            # 30 % off the tag
    ([R("lean", 260)], "osm_tag"),                       # 27 % of the larger
    ([R("floors", 185, "seed_1")], "osm_tag"),           # floors alone
    ([R("street", 185, "seed_9")], "osm_tag"),           # Street View
    ([R("stereo", 36)], "osm_tag"),                      # stereo under 40 m
    ([R("drone", 185, "seed_1")], None),                 # source unknown (old callers)
])
def test_osm_tag_not_verified(readings, source):
    tag = 36.0 if readings[0]["kind"] == "stereo" else 190.0
    assert verification_tier(readings, tag_m=tag, tag_source=source) == ("tag", ["osm_tag"])
    assert tag_witness(readings, tag, source) is None


def test_tag_witness_picks_the_closest_and_a_disputing_pair_blocks_it():
    rs = [R("lean", 160), R("drone", 185, "seed_1")]
    assert tag_witness(rs, 190.0, "osm_tag")["kind"] == "drone"
    # two drone seeds agree with each other at 100 m against the tag: no verification by one
    pair = [R("drone", 100, "seed_1"), R("drone", 102, "seed_2"), R("lean", 185)]
    f = tier_fields(pair, published_m=190.0, tag_m=190.0, tag_source="osm_tag")
    assert f["tier"] == "tag" and f["disputed_by"] == ["drone:seed_1", "drone:seed_2"]


def test_wiring_tagged_row_verified_by_a_satellite_lean(monkeypatch):
    from types import SimpleNamespace as NS
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS", "SKYLINE_WITHHOLD_SINGLE"):
        monkeypatch.delenv(k, raising=False)
    sat = {"allure": [NS(method="lean", height_m=167.5, conf=1.0),
                      NS(method="shadow", height_m=12.3, conf=0.26)],
           "lv": [NS(method="lean", height_m=60.0, conf=1.0)]}
    rows = [{"feature_id": f, "effective_height_m": 207.0, "effective_height_source": "geometric",
             "per_seed_median_m": {"auto_090_1400m": 207.0}} for f in ("allure", "lv")]
    recs = [_rec("allure", "osm_tag", 190.0), _rec("lv", "osm_levels", 62.0)]
    withhold_untagged_street_view(rows, recs, measured_seeds={"seed_1"}, satellite=sat)
    a, lv = rows
    assert a["effective_height_m"] == 190.0 and a["tier"] == "verified_2"
    assert a["tier_methods"] == ["osm_tag", "lean"] and a["no_survey_tier"] == "verified_2"
    assert lv["tier"] == "tag"                                       # levels: never by one reading


# --------------------------------------------------------------------------- high-rise hook

def _floors_info(flagged=True, floors=30):
    return {"floors": floors, "lower_bound": True, "storey_m": 3.5, "floors_m": floors * 3.5 + 3,
            "seeds": ["seed_6"], "high_rise_seen": flagged}


def test_high_rise_publishes_floors_and_is_exempt_from_the_2x_rule(monkeypatch):
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS", "SKYLINE_WITHHOLD_SINGLE"):
        monkeypatch.delenv(k, raising=False)
    rows = [{"feature_id": f, "effective_height_m": 40.0, "effective_height_source": "geometric",
             "per_seed_median_m": {"seed_9": 40.0}} for f in ("hr", "notflagged", "drone")]
    rows[2]["per_seed_median_m"] = {"seed_1": 99.0}
    recs = [_rec(f, "default") for f in ("hr", "notflagged", "drone")]
    floors = {"hr": _floors_info(), "notflagged": _floors_info(False), "drone": _floors_info()}
    withhold_untagged_street_view(rows, recs, fallback=lambda r: (14.0, "prior_gbm"),
                                  measured_seeds={"seed_1"}, floors=floors)
    hr, nf, dr = rows
    assert (hr["effective_height_m"], hr["effective_height_source"]) == (108.0, "withheld:high_rise")
    assert hr["tier"] == "single" and hr["tier_methods"] == ["floors"]   # 108 > 2x 14: kept
    assert "withheld_reason" not in hr and hr["high_rise"]["floors"] == 30
    assert hr["no_survey_height_m"] == 108.0
    assert hr["single_support"] == ["high_rise_floors"]
    assert (hr["high_rise_seen"], hr["floors"], hr["storey_m"]) == (True, 30, 3.5)
    assert (nf["effective_height_m"], nf["tier"]) == (14.0, "prior")    # not flagged: prior
    assert nf["high_rise_seen"] is False                              # the flag is visible
    # a drone reading comes first; on a flagged plot the flag supports it (refined rule,
    # the user 2026-10-09: Cartagena v10 withheld flagged plots' drone readings)
    assert (dr["effective_height_m"], dr["tier"]) == (99.0, "single")
    assert dr["single_support"] == ["high_rise_floors"] and "withheld_reason" not in dr
    # never lowers the prior
    rows = [{"feature_id": "hr", "effective_height_m": 40.0, "effective_height_source": "g",
             "per_seed_median_m": {}}]
    withhold_untagged_street_view(rows, recs[:1], fallback=lambda r: (200.0, "prior_gbm"),
                                  floors={"hr": _floors_info(floors=10)})
    assert rows[0]["effective_height_m"] == 200.0


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


EXPECTED_PUBLISHED = {  # what the code before tiers published (commit a22afec), except drone1:
    # a single reading over 2x the prior publishes the prior (the user's rule, 2026-10-08)
    "tag": (100.0, "osm_tag"), "drone2": (62.0, "withheld:elevated"),
    "drone1": (14.0, "withheld:prior_gbm"), "street": (14.0, "withheld:prior_gbm")}


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
    # the osm_tag (100 m) and drone seed_1 (98 m) agree: verified by one reading (2026-10-08)
    assert by["tag"]["tier"] == "verified_2"
    assert by["tag"]["tier_methods"] == ["osm_tag", "drone:seed_1"]
    assert by["drone2"]["tier"] == "verified_2" and by["drone2"]["verified"]
    assert by["drone2"]["tier_methods"] == ["drone:seed_1", "drone:seed_2"]
    d1 = by["drone1"]                                                 # 61 vs 14: withheld
    assert d1["tier"] == "prior" and d1["prior_disagrees"] and not d1["verified"]
    assert d1["tier_methods"] == ["prior_gbm"]
    assert (d1["single_reading_m"], d1["single_source"]) == (61.0, "withheld:elevated")
    assert d1["single_methods"] == ["drone:seed_1"]
    assert d1["withheld_reason"] == "single over 2x prior"
    assert by["street"]["tier"] == "prior" and by["street"]["tier_methods"] == ["prior_gbm"]
    for r in rows:
        assert set(r) >= {"tier", "tier_methods", "verified", "disputed_by", "prior_disagrees"}
    json.dumps(rows)                                                   # serialisable


def test_single_withheld_rule():
    assert single_withheld("single", 41.0, 20.0) is True
    assert single_withheld("single", 40.0, 20.0) is False           # exactly 2x publishes
    assert single_withheld("single", 5.0, 20.0) is False            # under the prior: kept
    assert single_withheld("verified_2", 200.0, 20.0) is False
    assert single_withheld("single", 50.0, None) is False


def _single_rows(value, sat=None):
    rows = [{"feature_id": "s", "effective_height_m": 30.0, "effective_height_source": "geometric",
             "per_seed_median_m": {"seed_1": value}}]
    return rows, [_rec("s", "default")]


def test_single_within_2x_still_publishes_as_single(monkeypatch):
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS", "SKYLINE_WITHHOLD_SINGLE"):
        monkeypatch.delenv(k, raising=False)
    rows, recs = _single_rows(27.0)
    withhold_untagged_street_view(rows, recs, fallback=lambda r: (14.0, "prior_gbm"),
                                  measured_seeds={"seed_1"})
    r = rows[0]
    assert (r["effective_height_m"], r["tier"]) == (27.0, "single")
    assert "single_reading_m" not in r and "withheld_reason" not in r
    assert r["no_survey_height_m"] == 27.0 and r["no_survey_tier"] == "single"


def test_single_over_2x_publishes_prior_and_keeps_the_reading(monkeypatch):
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS", "SKYLINE_WITHHOLD_SINGLE"):
        monkeypatch.delenv(k, raising=False)
    rows, recs = _single_rows(99.0)
    withhold_untagged_street_view(rows, recs, fallback=lambda r: (14.0, "prior_gbm"),
                                  measured_seeds={"seed_1"})
    r = rows[0]
    assert (r["effective_height_m"], r["effective_height_source"]) == (14.0, "withheld:prior_gbm")
    assert r["tier"] == "prior" and r["prior_disagrees"] is True
    assert (r["single_reading_m"], r["single_source"]) == (99.0, "withheld:elevated")
    assert r["no_survey_height_m"] == 14.0 and r["no_survey_tier"] == "prior"
    assert r["street_view_m"] == 30.0                                # what aggregate had
    json.dumps(rows)
    # the flag turns the rule off
    monkeypatch.setenv("SKYLINE_WITHHOLD_SINGLE", "0")
    rows, recs = _single_rows(99.0)
    withhold_untagged_street_view(rows, recs, fallback=lambda r: (14.0, "prior_gbm"),
                                  measured_seeds={"seed_1"})
    assert (rows[0]["effective_height_m"], rows[0]["tier"]) == (99.0, "single")


def test_single_support_rule():
    """The refined 2x rule (the user, 2026-10-09): validated satellite >= 40 m within 1.5x
    (``SUPPORT_RATIO``), or the high-rise flag; never stereo alone, never on a satellite-low
    plot."""
    assert single_support(62.0, [("lean", 60.4, 1.0), ("stereo", 64.2, 1.0)]) == ["lean"]
    assert single_support(62.0, [("lean", 60.4, 0.7)]) == ["lean"]
    assert single_support(54.0, [("lean", 53.5, 0.64), ("stereo", 54.0, 0.83)]) == []
    assert single_support(80.0, [("stereo", 80.0, 1.0)]) == []          # stereo alone: ~59 %
    assert single_support(80.0, [("multiview", 75.0, 0.3)]) == ["multiview"]
    assert single_support(80.0, [("multiview", 75.0, 0.29)]) == []
    assert single_support(120.0, [("ls", 110.0, 0.3)]) == ["ls"]
    assert single_support(39.0, [("lean", 39.0, 1.0)]) == []            # under 40 m
    assert single_support(100.0, [("lean", 70.0, 1.0)]) == ["lean"]     # 1.43x: within 1.5x
    assert single_support(100.0, [("lean", 60.0, 1.0)]) == []           # 1.67x: too far
    assert single_support(100.0, [("shadow", 100.0, 1.0)]) == []        # a lower bound
    assert single_support(100.0, [("lean", 95.0, 0.9), ("multiview", 98.0, 0.5)],
                          high_rise=True) == ["lean", "multiview", "high_rise_floors"]
    assert single_support(100.0, [], high_rise=True) == ["high_rise_floors"]
    assert single_support(100.0, [("lean", 95.0, 0.9)], high_rise=True, satellite_low=True) == []
    assert single_support(None, [("lean", 95.0, 0.9)]) == []


def test_support_ratio_is_one_and_a_half(monkeypatch):
    """The user's decision of 2026-10-09: a validated tall satellite reading supports a single
    within 1.5x (either side), the strict ``agree`` (1.25) having dropped b0691 and b0806."""
    assert SUPPORT_RATIO == 1.5
    assert single_support(55.9, [("lean", 72.5, 1.0)]) == ["lean"]         # b0691: 1.30x
    assert single_support(62.8, [("lean", 48.4, 1.0)]) == ["lean"]         # b0806: 1.30x, below
    assert single_support(60.0, [("lean", 90.0, 1.0)]) == ["lean"]         # exactly 1.5x
    assert single_support(90.0, [("lean", 60.0, 1.0)]) == ["lean"]
    assert single_support(60.0, [("lean", 96.0, 1.0)]) == []               # 1.6x
    assert single_support(96.0, [("lean", 60.0, 1.0)]) == []
    assert single_support(60.0, [("lean", 96.0, 1.0), ("multiview", 80.0, 0.5)]) == ["multiview"]
    # the real b0806 lean (48.4 m) has conf 0.37: not validated, so it supports nothing (replay)
    assert single_support(62.8, [("lean", 48.4, 0.37)]) == []
    assert single_support(80.0, [("lean", 53.0, 0.69)]) == []              # still needs conf 0.7
    assert single_support(80.0, [("lean", 39.0, 1.0)]) == []               # and 40 m
    assert not agree(55.9, 72.5) and not agree(60.0, 78.0)                 # agree stays 1.25


def test_b0691_b0806_publish_their_drone_readings(monkeypatch):
    """Cartagena v12 replay: a drone single and a validated lean (conf 1.0) 1.3x off it keep the
    single, a lean 1.6x off it does not. The values are b0691's and b0806's; the real b0806 lean
    has conf 0.37, so only b0691 was restored in the replay."""
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS", "SKYLINE_WITHHOLD_SINGLE"):
        monkeypatch.delenv(k, raising=False)
    cases = {"b0691": (55.9, [("lean", 72.5, 1.0)]), "b0806": (62.8, [("lean", 48.4, 1.0)]),
             "far": (60.0, [("lean", 96.0, 1.0)])}
    rows, sat = [], {}
    for fid, (drone, rs) in cases.items():
        row, sat[fid] = _sat_single(fid, rs, {"seed_1": drone})
        rows.append(row)
    withhold_untagged_street_view(rows, [_rec(f, "default") for f in cases],
                                  fallback=lambda r: (17.0, "prior_gbm"),
                                  measured_seeds={"seed_1"}, satellite=sat)
    a, b, far = rows
    assert (a["tier"], a["effective_height_m"], a["single_support"]) == ("single", 55.9, ["lean"])
    assert (b["tier"], b["effective_height_m"], b["single_support"]) == ("single", 62.8, ["lean"])
    assert (far["tier"], far["effective_height_m"]) == ("prior", 17.0)
    assert "single_support" not in far


def _sat_single(fid, readings, per_seed=None):
    from types import SimpleNamespace as NS
    row = {"feature_id": fid, "effective_height_m": 150.0, "effective_height_source": "geometric",
           "per_seed_median_m": per_seed or {"auto_090_1400m": 150.0}}
    return row, [NS(method=m, height_m=h, conf=c) for m, h, c in readings]


def test_single_over_2x_kept_when_supported(monkeypatch):
    """Cartagena v10: b1429 (lean 60.4 / 1.0 + stereo 64.2) and b0112 published the ~10-20 m
    prior; a validated lean that agrees keeps them. b1211's lean (conf 0.64) is not validated
    and stereo alone never supports, so it stays withheld."""
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS", "SKYLINE_WITHHOLD_SINGLE"):
        monkeypatch.delenv(k, raising=False)
    cases = {"b1429": [("lean", 60.4, 1.0), ("stereo", 64.2, 1.0)],
             "b1211": [("lean", 53.5, 0.64), ("stereo", 54.0, 0.83)],
             "mv": [("multiview", 70.0, 0.4)]}
    rows, sat = [], {}
    for fid, rs in cases.items():
        row, sat[fid] = _sat_single(fid, rs)
        rows.append(row)
    recs = [_rec(f, "default") for f in cases]
    withhold_untagged_street_view(rows, recs, fallback=lambda r: (10.0, "prior_gbm"),
                                  measured_seeds={"seed_1"}, satellite=sat)
    a, b, mv = rows
    assert a["effective_height_source"] == "withheld:satellite" and a["tier"] == "single"
    assert a["single_support"] == ["lean"] and "withheld_reason" not in a
    assert a["prior_disagrees"] is True and a["no_survey_tier"] == "single"
    assert 60.0 < a["effective_height_m"] < 65.0
    assert b["tier"] == "prior" and b["withheld_reason"] == "single over 2x prior"
    assert b["effective_height_m"] == 10.0 and "single_support" not in b
    assert mv["tier"] == "single" and mv["single_support"] == ["multiview"]
    json.dumps(rows)


def test_supported_drone_single_and_the_tower_behind_veto(monkeypatch):
    """A drone single on a flagged plot is kept; with the plot's confident satellite readings all
    under 40 m (a tower-behind case) neither the flag nor anything else keeps it."""
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS", "SKYLINE_WITHHOLD_SINGLE"):
        monkeypatch.delenv(k, raising=False)
    kept, _ = _sat_single("kept", [], {"seed_1": 99.0})
    low, low_sat = _sat_single("low", [("stereo", 12.0, 1.0)], {"seed_1": 99.0})
    plain, _ = _sat_single("plain", [], {"seed_1": 99.0})
    floors = {f: _floors_info() for f in ("kept", "low")}
    rows = [kept, low, plain]
    withhold_untagged_street_view(rows, [_rec(f, "default") for f in ("kept", "low", "plain")],
                                  fallback=lambda r: (12.0, "prior_gbm"),
                                  measured_seeds={"seed_1"}, satellite={"low": low_sat},
                                  floors=floors)
    assert (kept["tier"], kept["effective_height_m"]) == ("single", 99.0)
    assert kept["single_support"] == ["high_rise_floors"]
    assert (low["tier"], low["effective_height_m"]) == ("prior", 12.0)       # satellite says low
    assert low["single_source"] == "withheld:elevated" and "single_support" not in low
    assert (plain["tier"], plain["effective_height_m"]) == ("prior", 12.0)   # unsupported
    assert "high_rise_seen" not in plain


def test_flag_off_labels_street_view_as_single(monkeypatch):
    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "0")
    rows, recs = _fixture()
    assert withhold_untagged_street_view(rows, recs, measured_seeds={"seed_1"}) == 0
    assert rows[3]["effective_height_m"] == 120.0 and rows[3]["tier"] == "single"
    assert rows[3]["tier_methods"] == ["street:seed_9"]


def test_drone_seen_footprint_without_a_reading_gets_a_prior_row(monkeypatch):
    """Cartagena v9: footprints whose only drone reading the tower-behind check left out had
    no estimate, so no row; they now get the prior like any untagged footprint."""
    from types import SimpleNamespace

    from city2stl.skyline.region_pdf import _drone_seen_rows, _fill_unread_heights
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS", "SKYLINE_WITHHOLD_SINGLE"):
        monkeypatch.delenv(k, raising=False)

    def seg(fid, src="footprint"):
        return {"height_src": src, "matched_projection": {"feature_id": fid}}

    recs = [_rec("kept", "default"), _rec("behind", "default"), _rec("tagged", "osm_tag", 50.0),
            _rec("street_only", "default")]
    rows = [{"feature_id": "kept", "effective_height_m": 30.0, "effective_height_source": "x",
             "per_seed_median_m": {"seed_1": 30.0}}]
    prs = [SimpleNamespace(seed_name="seed_1", matched_segments=[seg("kept"), seg("behind"),
                                                                 seg("tagged")]),
           SimpleNamespace(seed_name="seed_4", matched_segments=[seg("behind")]),
           SimpleNamespace(seed_name="seed_9", matched_segments=[seg("street_only", "pano")])]
    extra = _drone_seen_rows(rows, recs, prs, {"seed_1", "seed_4"})
    assert [(r["feature_id"], r["drone_seen"]) for r in extra] == [("behind", ["seed_1", "seed_4"])]
    assert _drone_seen_rows(rows, recs, prs, set()) == []
    rows.extend(extra)
    withhold_untagged_street_view(rows, recs, fallback=lambda r: (14.0, "prior_gbm"),
                                  measured_seeds={"seed_1", "seed_4"})
    _fill_unread_heights(rows)
    b = next(r for r in rows if r["feature_id"] == "behind")
    assert (b["effective_height_m"], b["effective_height_source"]) == (14.0, "withheld:prior_gbm")
    assert b["tier"] == "prior" and b["weighted_height_m"] == 14.0
    json.dumps(rows)
    # the prior predicts only the records it was built on: the drone-seen ones come from a
    # second, wider prior (v10 run 1: 368 rows added, all but the satellite / high-rise ones
    # dropped again for want of a prior)
    from city2stl.skyline.region_pdf import _chain_fallbacks
    narrow = lambda r: (14.0 if r.feature_id == "kept" else None, "prior_gbm")  # noqa: E731
    wide = lambda r: (20.0, "prior_gbm")                                         # noqa: E731
    fb = _chain_fallbacks(narrow, wide)
    assert fb(recs[0]) == (14.0, "prior_gbm") and fb(recs[1]) == (20.0, "prior_gbm")
    assert _chain_fallbacks(None, wide) is None                       # the constant stays
    assert _chain_fallbacks(narrow, None)(recs[1]) == (None, "prior_gbm")
    rows3 = _drone_seen_rows([rows[0]], recs, prs, {"seed_1", "seed_4"})
    withhold_untagged_street_view(rows3, recs, fallback=fb, measured_seeds={"seed_1", "seed_4"})
    _fill_unread_heights(rows3)
    assert [(r["feature_id"], r["effective_height_m"]) for r in rows3] == [("behind", 20.0)]
    # with the withhold flag off there is no prior: the row is dropped again
    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "0")
    rows2 = _drone_seen_rows([], recs, prs, {"seed_1"})
    withhold_untagged_street_view(rows2, recs, measured_seeds={"seed_1"})
    _fill_unread_heights(rows2)
    assert rows2 == []


def test_heights_json_unmeasured_tag_verified_by_a_satellite_reading(tmp_path):
    from types import SimpleNamespace as NS

    from shapely.geometry import Polygon

    from city2stl.skyline.region_pdf import _write_heights_json

    class BBox:
        north, south, east, west = 10.43, 10.38, -75.52, -75.57

    poly = Polygon([(-75.553, 10.402), (-75.5526, 10.402), (-75.5526, 10.4024), (-75.553, 10.4024)])
    recs = [BuildingRecord("b", "Allure", poly, 10.4022, -75.5528, 190.0, "osm_tag", 900.0),
            BuildingRecord("c", "Levels", poly, 10.4022, -75.5528, 60.0, "osm_levels", 900.0)]
    sat = {"b": [NS(method="lean", height_m=168.0, conf=1.0)],
           "c": [NS(method="lean", height_m=58.0, conf=1.0)]}
    path = tmp_path / "heights.json"
    _write_heights_json(path, region_name="x", bbox=BBox, building_heights=[],
                        building_records=recs, known_heights=None, satellite=sat)
    doc = json.loads(path.read_text(encoding="utf-8"))
    by = {b["feature_id"]: b for b in doc["buildings"]}
    assert by["b"]["tier"] == "verified_2" and by["b"]["tier_methods"] == ["osm_tag", "lean"]
    assert by["b"]["no_survey_tier"] == "verified_2" and by["b"]["satellite"] == {"lean": [168.0, 1.0]}
    assert by["c"]["tier"] == "tag"
    assert doc["tier_counts"]["verified_2"] == 1 and doc["tier_counts"]["tag"] == 1


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
