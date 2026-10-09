"""Review items 5 and 6 (2026-10-09): the banded agreement rule, measured vs tag, and the selection
reason of every published row (``_core/tiers.py``, ``tier_display``, ``region_pdf``)."""

import json
from types import SimpleNamespace as NS

import pytest

from city2stl.skyline import tier_display as td
from city2stl.skyline._core import tiers
from city2stl.skyline._core.height import withhold_untagged_street_view
from city2stl.skyline._core.tiers import (
    AGREE_TOL,
    agree,
    agree_band,
    reading,
    selection_reason,
    tag_measurement,
    tier_fields,
)
from city2stl.skyline._core.types import BuildingRecord

R = reading


def _clean_env(monkeypatch):
    for k in ("SKYLINE_WITHHOLD_UNTAGGED", "SKYLINE_PREFER_TAGS", "SKYLINE_WITHHOLD_SINGLE"):
        monkeypatch.delenv(k, raising=False)


# --------------------------------------------------------------------------- agreement

def test_bands_are_by_the_smaller_value():
    assert [agree_band(h) for h in (0, 14.9, 15, 39.9, 40, 99.9, 100, 500)] == \
        ["<15", "<15", "15-40", "15-40", "40-100", "40-100", ">100", ">100"]
    assert set(AGREE_TOL) == {"<15", "15-40", "40-100", ">100"}
    assert tiers.agree_tol(95.0, 130.0) == AGREE_TOL["40-100"]          # the smaller decides


def test_band_tolerance_applies_on_the_smaller(monkeypatch):
    monkeypatch.setitem(tiers.AGREE_TOL, "40-100", 0.10)
    assert not agree(50.0, 56.0) and agree(50.0, 54.9)                   # 1.12 > 1.10
    assert not agree(95.0, 105.0)                                        # banded on 95: 40-100
    assert agree(110.0, 135.0)                                           # > 100 m keeps 25 %


def test_metre_floor_is_off_by_default_and_only_below_15_m(monkeypatch):
    assert tiers.AGREE_FLOOR_M == 0.0 and tiers.REVIEW_FLOOR_M == 2.0
    assert not agree(4.0, 6.0)                                           # 1.5x, no floor
    assert agree(4.0, 6.0, floor_m=2.0) and not agree(4.0, 6.5, floor_m=2.0)
    assert agree(14.9, 19.0, floor_m=5.0) and not agree(15.0, 19.2, floor_m=5.0)
    monkeypatch.setattr(tiers, "AGREE_FLOOR_M", 2.0)                     # switched on
    assert agree(4.0, 6.0) and agree(6.0, 4.0)
    assert tier_fields([R("drone", 4.0, "seed_1"), R("drone", 6.0, "seed_2")],
                       published_m=5.0)["tier"] == "verified_2"


def test_the_closest_pair_is_by_ratio():
    rs = [R("drone", 100.0, "seed_1"), R("drone", 112.0, "seed_2"), R("lean", 90.0)]
    # 100/90 = 1.111 is closer than 112/100 = 1.12
    assert tiers.verified_pair(rs) == (rs[0], rs[2])


def test_corroboration_keeps_the_strict_agree():
    """The single-support ratio (``SUPPORT_RATIO`` 1.5, the user, 2026-10-09) does not loosen
    corroboration: a 1.3x pair of independent readings is still not a ``verified_2``, nor does it
    witness a tag."""
    assert tiers.SUPPORT_RATIO == 1.5 and not agree(60.0, 78.0) and agree(60.0, 74.0)
    pair = [R("drone", 60.0, "seed_1"), R("drone", 78.0, "seed_2")]
    assert tier_fields(pair, published_m=69.0)["tier"] != "verified_2"
    assert tiers.verified_pair(pair) is None
    near = [R("drone", 60.0, "seed_1"), R("drone", 74.0, "seed_2")]          # 1.23x
    assert tier_fields(near, published_m=67.0)["tier"] == "verified_2"
    assert tiers.tag_witness([R("drone", 78.0, "seed_1")], 60.0, "osm_tag") is None
    assert tiers.tag_witness([R("drone", 74.0, "seed_1")], 60.0, "osm_tag") is not None


# --------------------------------------------------------------------------- measured vs tag

def test_palmetto_measurement_disagrees_with_its_tag():
    rs = [R("lean", 136.4), R("shadow", 135.3), R("stereo", 101.5)]
    m = tag_measurement(rs, 156.0)
    assert m["tag_disagrees"] is True and m["measured_methods"] == ["lean", "shadow"]
    assert m["measured_m"] == pytest.approx(135.85, abs=0.06)
    f = tier_fields(rs, published_m=156.0, tag_m=156.0, tag_source="osm_tag")
    assert f["tier"] == "verified_2" and f["tag_disagrees"] and f["measured_m"] == m["measured_m"]


@pytest.mark.parametrize("rs, tag", [
    ([R("drone", 145.5, "seed_4"), R("stereo", 161.0)], 160.0),        # Ravello: mean 153, 4 %
    ([R("drone", 150.0, "seed_1"), R("drone", 145.0, "seed_2")], 156.0),   # 6 % off
    ([R("lean", 136.4)], 156.0),                                       # one reading: no pair
    ([R("lean", 136.4), R("shadow", 135.3)], None),                    # no tag
])
def test_no_tag_disagreement(rs, tag):
    assert tag_measurement(rs, tag) == {}
    f = tier_fields(rs, published_m=tag, tag_m=tag, tag_source="osm_tag")
    assert "tag_disagrees" not in f and "measured_m" not in f


def test_a_disputing_pair_is_shown_beside_the_tag():
    rs = [R("drone", 100.0, "seed_1"), R("drone", 102.0, "seed_2")]
    f = tier_fields(rs, published_m=150.0, tag_m=150.0, tag_source="osm_tag")
    assert f["tier"] == "tag" and f["disputed_by"] == ["drone:seed_1", "drone:seed_2"]
    assert (f["measured_m"], f["measured_methods"], f["tag_disagrees"]) == \
        (101.0, ["drone:seed_1", "drone:seed_2"], True)


def test_untagged_rows_never_carry_tag_fields():
    rs = [R("drone", 100.0, "seed_1"), R("drone", 102.0, "seed_2")]
    assert "tag_disagrees" not in tier_fields(rs, published_m=60.0)


# --------------------------------------------------------------------------- wiring

def _rec(fid, source, tag=None):
    return BuildingRecord(fid, fid, None, 10.40, -75.55, tag, source, 400.0)


def test_wiring_tag_disagreement_and_selection_reasons(monkeypatch):
    _clean_env(monkeypatch)
    sat = {"palm": [NS(method="lean", height_m=136.4, conf=0.92),
                    NS(method="shadow", height_m=135.3, conf=0.29)]}
    rows = [
        {"feature_id": "palm", "effective_height_m": 150.0, "effective_height_source": "geometric",
         "per_seed_median_m": {"auto_090": 150.0}},
        {"feature_id": "tag", "effective_height_m": 95.0, "effective_height_source": "geometric",
         "per_seed_median_m": {"seed_1": 98.0}},
        {"feature_id": "d2", "effective_height_m": 40.0, "effective_height_source": "geometric",
         "per_seed_median_m": {"seed_1": 60.0, "seed_2": 64.0}},
        {"feature_id": "d1", "effective_height_m": 40.0, "effective_height_source": "geometric",
         "per_seed_median_m": {"seed_1": 61.0}},
        {"feature_id": "st", "effective_height_m": 120.0, "effective_height_source": "f_sky1",
         "per_seed_median_m": {"seed_9": 120.0}},
    ]
    recs = [_rec("palm", "osm_tag", 156.0), _rec("tag", "osm_tag", 100.0), _rec("d2", "default"),
            _rec("d1", "default"), _rec("st", "default")]
    withhold_untagged_street_view(rows, recs, fallback=lambda r: (14.0, "prior_gbm"),
                                  measured_seeds={"seed_1", "seed_2"}, satellite=sat)
    by = {r["feature_id"]: r for r in rows}
    p = by["palm"]
    assert p["effective_height_m"] == 156.0 and p["tier"] == "verified_2"   # the tag stays
    assert p["tag_disagrees"] and p["measured_methods"] == ["lean", "shadow"]
    assert "measured 136 m (lean, shadow) disagrees" in p["selection_reason"]
    assert "tag_disagrees" not in by["tag"]
    assert by["tag"]["selection_reason"] == tiers.SELECTION_REASONS["osm_tag"]
    assert by["d2"]["selection_reason"] == tiers.SELECTION_REASONS["elevated"]
    assert by["d1"]["selection_reason"] == \
        "the height prior: the reading was withheld (single over 2x prior)"
    assert by["st"]["selection_reason"] == tiers.SELECTION_REASONS["prior"]
    for r in rows:
        assert r["selection_reason"]
    json.dumps(rows)


@pytest.mark.parametrize("row, expect", [
    ({"effective_height_source": "survey:usgs_3dep", "tier": "survey"}, "survey lidar height"),
    ({"effective_height_source": "osm_levels"}, tiers.SELECTION_REASONS["osm_levels"]),
    ({"effective_height_source": "withheld:satellite"}, tiers.SELECTION_REASONS["satellite"]),
    ({"effective_height_source": "withheld:high_rise", "single_support": ["high_rise_floors"]},
     tiers.SELECTION_REASONS["high_rise"]
     + "; over 2x the prior, kept: supported by high_rise_floors"),
    ({"effective_height_source": "withheld:elevated", "single_support": ["lean"]},
     tiers.SELECTION_REASONS["elevated"] + "; over 2x the prior, kept: supported by lean"),
    ({"effective_height_source": "withheld:prior_gbm", "drone_seen": ["seed_1"]},
     tiers.SELECTION_REASONS["prior"] + " (the drone seeds that saw it gave none)"),
    ({"effective_height_source": "withheld:cadastre"}, tiers.SELECTION_REASONS["cadastre"]),
    ({"effective_height_source": "geometric"}, tiers.SELECTION_REASONS["street"]),
    ({}, "no published height"),
])
def test_selection_reason(row, expect):
    assert selection_reason(row) == expect


def test_heights_json_lists_tag_disagreements(tmp_path):
    from shapely.geometry import Polygon

    from city2stl.skyline.region_pdf import _write_heights_json

    class BBox:
        north, south, east, west = 10.43, 10.38, -75.52, -75.57

    poly = Polygon([(-75.557, 10.4026), (-75.5566, 10.4026), (-75.5566, 10.403), (-75.557, 10.403)])
    recs = [BuildingRecord("b0598", "Palmetto Eliptic", poly, 10.4028, -75.5568, 156.0, "osm_tag",
                           462.0),
            BuildingRecord("c", "Plain", poly, 10.4028, -75.5568, 60.0, "osm_tag", 462.0)]
    sat = {"b0598": [NS(method="lean", height_m=136.4, conf=0.92),
                     NS(method="shadow", height_m=135.3, conf=0.29)]}
    path = tmp_path / "heights.json"
    _write_heights_json(path, region_name="x", bbox=BBox, building_heights=[],
                        building_records=recs, known_heights=None, satellite=sat)
    doc = json.loads(path.read_text(encoding="utf-8"))
    by = {b["feature_id"]: b for b in doc["buildings"]}
    assert by["b0598"]["tag_disagrees"] and by["b0598"]["effective_height_m"] == 156.0
    assert [d["feature_id"] for d in doc["tag_disagreements"]] == ["b0598"]
    d = doc["tag_disagreements"][0]
    assert d["tag_m"] == 156.0 and d["measured_methods"] == ["lean", "shadow"]
    assert d["off_pct"] == pytest.approx(-12.9, abs=0.1)
    assert by["c"]["selection_reason"] == tiers.SELECTION_REASONS["osm_tag"]
    assert "measured 136 m" in by["b0598"]["selection_reason"]


# --------------------------------------------------------------------------- display

def test_tag_note_hover_and_lines():
    row = {"feature_id": "b0598", "name": "Palmetto Eliptic", "tier": "verified_2",
           "tier_methods": ["lean", "shadow"], "effective_height_m": 156.0, "height_tag_m": 156.0,
           "measured_m": 135.9, "measured_methods": ["lean", "shadow"], "tag_disagrees": True,
           "selection_reason": "OSM height tag: ..."}
    assert td.tag_note(row) == ("Measured 136 m (lean, shadow) vs OSM tag 156 m (13 % lower); "
                                "the tag is published until the user decides")
    hover = td.tier_hover(row)
    assert "Measured 136 m (lean, shadow) vs OSM tag 156 m" in hover
    assert "Published because: OSM height tag" in hover
    assert td.tag_note({**row, "tag_disagrees": False}) is None
    other = {**row, "feature_id": "x", "name": "Small", "measured_m": 70.0, "height_tag_m": 60.0}
    ds = td.tag_disagreements([other, row, {"feature_id": "y"}])
    assert [d["feature_id"] for d in ds] == ["x", "b0598"]              # largest first
    lines = td.tag_disagreement_lines([row])
    assert lines[0].startswith("Measured vs OSM tag (> 10% apart")
    assert "Palmetto Eliptic" in lines[1] and "measured  136 m (lean, shadow)" in lines[1]
    assert td.tag_disagreement_lines([]) == []
    assert "of the smaller" in td.TIER_HINTS["verified_2"]


def test_unmeasured_tag_rows_carry_a_selection_reason():
    rec = NS(feature_id="t", name="T", height_source="osm_tag", height_tag_m=50.0, geometry=None)
    rows = td.with_unmeasured_tags([], [rec])
    assert rows[0]["selection_reason"] == tiers.SELECTION_REASONS["osm_tag"]
