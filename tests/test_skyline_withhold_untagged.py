"""T28: Street View heights withheld on untagged buildings, kept for scoring."""

import pytest

from city2stl.skyline import benchmark as bm
from city2stl.skyline._core.height import (
    UNTAGGED_FALLBACK_M,
    withhold_untagged_street_view,
)
from city2stl.skyline._core.types import BuildingRecord


@pytest.fixture(autouse=True)
def _tags_not_preferred(monkeypatch):
    """These tests cover the untagged side; tagged rows publishing their tag has its own test."""
    monkeypatch.setenv("SKYLINE_PREFER_TAGS", "0")


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


def test_old_report_simulated_withholding():
    old = [{"key": "a", "effective_height_m": 95.0, "height_source": "osm_tag"},
           {"key": "b", "effective_height_m": 120.0, "height_source": "default"},
           {"key": "c", "effective_height_m": 60.0, "height_source": None}]
    w = bm.withheld_buildings(old)
    assert [b["effective_height_m"] for b in w] == [95.0, UNTAGGED_FALLBACK_M, UNTAGGED_FALLBACK_M]
    assert w[1]["street_view_m"] == 120.0 and old[1]["effective_height_m"] == 120.0


def test_untagged_building_a_drone_seed_measured_keeps_the_drone_height(monkeypatch):
    """Cartagena 2026-10-05: Hotel Estelar (untagged) read 185 m from drone seed_1 and 113 m
    from a street seed; withholding gave it the 10 m fallback."""
    from types import SimpleNamespace

    from city2stl.skyline._core import height as hm

    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "1")
    rec = SimpleNamespace(feature_id="f1", height_source="default")
    row = {"feature_id": "f1", "effective_height_m": 113.0, "effective_height_source": "geometric",
           "per_seed_median_m": {"seed_1": 185.0, "auto_090_1400m": 113.0}}
    other = {"feature_id": "f2", "effective_height_m": 90.0, "effective_height_source": "geometric",
             "per_seed_median_m": {"auto_090_1400m": 90.0}}
    rec2 = SimpleNamespace(feature_id="f2", height_source="default")
    n = hm.withhold_untagged_street_view([row, other], [rec, rec2], measured_seeds={"seed_1"})
    assert n == 2
    assert row["effective_height_m"] == 185.0 and row["effective_height_source"] == "withheld:elevated"
    assert row["street_view_m"] == 113.0
    assert other["effective_height_m"] == hm.UNTAGGED_FALLBACK_M


def test_tagged_building_publishes_its_osm_height(monkeypatch):
    from types import SimpleNamespace

    from city2stl.skyline._core import height as hm

    monkeypatch.setenv("SKYLINE_WITHHOLD_UNTAGGED", "1")
    monkeypatch.setenv("SKYLINE_PREFER_TAGS", "1")
    rec = SimpleNamespace(feature_id="t", height_source="osm_tag", height_tag_m=190.0)
    row = {"feature_id": "t", "effective_height_m": 97.0, "effective_height_source": "geometric"}
    assert hm.withhold_untagged_street_view([row], [rec]) == 1
    assert row["effective_height_m"] == 190.0 and row["effective_height_source"] == "osm_tag"
    assert row["street_view_m"] == 97.0
    monkeypatch.setenv("SKYLINE_PREFER_TAGS", "0")
    row2 = {"feature_id": "t", "effective_height_m": 97.0, "effective_height_source": "geometric"}
    assert hm.withhold_untagged_street_view([row2], [rec]) == 0 and row2["effective_height_m"] == 97.0


def test_records_keep_the_parsed_height_tag():
    """The OSM loader parses ``height`` into ``height_m`` and drops it; the records used to fall
    back to levels x 3.2 (Cartagena's Allure: tag 190 m, 43 levels -> 140.8 m)."""
    from city2stl.skyline.region_data import _osm_to_building_records

    ring = [[-75.5530, 10.4024], [-75.5526, 10.4024], [-75.5526, 10.4028], [-75.5530, 10.4028], [-75.5530, 10.4024]]
    osm = {"buildings": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
         "properties": {"name": "Allure", "height_m": 190.0, "height_source": "osm_tag", "building:levels": "43"}}]}}
    (rec,) = _osm_to_building_records(osm)
    assert rec.height_tag_m == 190.0 and rec.height_source == "osm_tag"


def test_heights_json_lists_unmeasured_tagged_buildings_and_the_benchmark_skips_them(tmp_path):
    from shapely.geometry import Polygon

    from city2stl.skyline.region_pdf import _write_heights_json

    poly = Polygon([(-75.553, 10.402), (-75.5526, 10.402), (-75.5526, 10.4024), (-75.553, 10.4024)])
    recs = [BuildingRecord("a", "Measured", poly, 10.4022, -75.5528, None, "default", 900.0),
            BuildingRecord("b", "Allure", poly, 10.4022, -75.5528, 190.0, "osm_tag", 900.0),
            BuildingRecord("c", "Plain", poly, 10.4022, -75.5528, None, "default", 900.0)]
    rows = [{"feature_id": "a", "effective_height_m": 12.0, "effective_height_source": "withheld:default"}]
    path = tmp_path / "heights.json"
    _write_heights_json(path, region_name="x", bbox=SimpleBBox, building_heights=rows,
                        building_records=recs, known_heights=None)
    import json
    doc = json.loads(path.read_text(encoding="utf-8"))
    names = {b["name"] if "name" in b else b["feature_id"]: b for b in doc["buildings"]}
    assert names["Allure"]["measured"] is False and names["Allure"]["effective_height_m"] == 190.0
    assert "Plain" not in names                                   # untagged, unmeasured: not listed
    _region, scored = bm.load_report(path)
    assert [b["feature_id"] for b in scored] == ["a"]


class SimpleBBox:
    north, south, east, west = 10.43, 10.38, -75.52, -75.57
