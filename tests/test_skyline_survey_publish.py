"""F-SKY26 2d: survey LiDAR heights published as tier ``survey`` (``skyline/survey_publish.py``),
the stale flags, and the benchmark never scoring them on the survey they came from."""

import importlib
import json

import pytest
from shapely.geometry import Polygon

from city2stl.skyline import benchmark as bm
from city2stl.skyline import survey_heights as sh
from city2stl.skyline import survey_publish as sp
from city2stl.skyline._core.types import BuildingRecord

PROJECT = "USGS_LPC_PR_PRVI_E_2018"


def _box(x0, y0, w=0.0004, h=0.0004):
    return Polygon([(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)])


def _rec(fid, poly, tag=None, src="default", name=None):
    c = poly.centroid
    return BuildingRecord(fid, name or fid, poly, c.y, c.x, tag, src, 900.0)


def _key(rec):
    return bm.footprint_key([[round(x, 6), round(y, 6)] for x, y in rec.geometry.exterior.coords])


def _survey(m, years=(2018, 2018), project=None, **kw):
    return {"survey_m": m, "survey_cells": 300, "provider": "usgs_3dep_ept", "years": list(years),
            "stat": bm.STAT_VERSION, "roof_stats": bm.ROOF_STATS_VERSION,
            "roof_m": None if m is None else {"p50": m - 2, "p70": m - 1, "p90": m - 0.5, "p95": m,
                                              "max": m + 1},
            "ground_p5_m": 0.1, "ground_cells": 80, **({"project": project} if project else {}), **kw}


@pytest.fixture(autouse=True)
def _tmp_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "SURVEY_ROOT", tmp_path / "survey")
    monkeypatch.setattr(bm, "BENCHMARK_ROOT", tmp_path / "benchmark")
    monkeypatch.delenv("SKYLINE_SURVEY_HEIGHTS", raising=False)


def _overlay(recs, survey_by_fid, rows=(), truth=None, osm_data=None, start_dates=None,
             drone_seeds=("seed_1", "seed_2")):
    sh.save_cache("testville", {_key(r): survey_by_fid[r.feature_id] for r in recs
                                if r.feature_id in survey_by_fid})
    if truth:
        bm.save_truth_cache("testville", truth)
    if start_dates:
        p = bm.BENCHMARK_ROOT / "truth" / "testville.start_dates.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"start_dates": start_dates}), encoding="utf-8")
    return sp.build_overlay("testville", list(rows), recs, osm_data, drone_seeds)


def _row(fid, h, tier="single", **kw):
    return {"feature_id": fid, "effective_height_m": h, "effective_height_source": "withheld:elevated",
            "tier": tier, "tier_methods": ["drone:seed_1"], "verified": False,
            "no_survey_height_m": h, "no_survey_source": "withheld:elevated",
            "no_survey_tier": tier, **kw}


def test_survey_outranks_tag_and_readings_and_keeps_the_evidence():
    a = _rec("a", _box(-66.0, 18.40), tag=22.0, src="osm_tag")      # tag-only
    b = _rec("b", _box(-66.01, 18.40))                              # measured, untagged
    row = _row("b", 30.0, per_seed_median_m={"seed_1": 30.0}, median_height_m=30.0)
    ov = _overlay([a, b], {"a": _survey(25.0, project=PROJECT), "b": _survey(28.0, project=PROJECT)},
                  rows=[row])
    assert ov.apply(row) == "survey"
    assert row["tier"] == "survey" and row["effective_height_m"] == 28.0
    assert row["effective_height_source"] == f"survey:{PROJECT}"
    assert row["survey_provider"] == PROJECT and row["survey_source"] == "usgs_3dep_ept"
    assert row["survey_year"] == 2018 and row["survey_stat"] == "p95" and row["survey_m"] == 28.0
    assert row["survey_roof_m"]["p50"] == 26.0 and row["survey_stale"] is False
    assert row["selection_reason"].startswith("survey lidar height (p95, " + PROJECT)
    # the image reading and the survey-blind answer stay on the row
    assert row["per_seed_median_m"] == {"seed_1": 30.0}
    assert (row["no_survey_height_m"], row["no_survey_tier"]) == (30.0, "single")
    tag_row = {"feature_id": "a", "effective_height_m": 22.0, "effective_height_source": "osm_tag",
               "tier": "tag", "tier_methods": ["osm_tag"], "no_survey_height_m": 22.0,
               "no_survey_source": "osm_tag", "no_survey_tier": "tag"}
    assert ov.apply(tag_row) == "survey"
    assert tag_row["tier"] == "survey" and tag_row["effective_height_m"] == 25.0
    assert tag_row["no_survey_tier"] == "tag"
    assert ov.apply({"feature_id": "zzz", "tier": "tag"}) is None     # no survey record


def test_project_falls_back_to_the_truth_cache_then_the_provider():
    a = _rec("a", _box(-66.0, 18.40))
    b = _rec("b", _box(-66.01, 18.40))
    ov = _overlay([a, b], {"a": _survey(10.0), "b": _survey(12.0)},
                  rows=[_row("a", 9.0), _row("b", 11.0)],
                  truth={_key(a): {"survey_project": PROJECT, "status": "survey_only"}})
    ra, rb = _row("a", 9.0), _row("b", 11.0)
    ov.apply(ra)
    ov.apply(rb)
    assert ra["survey_provider"] == PROJECT and rb["survey_provider"] == "usgs_3dep_ept"


def test_uncached_error_and_old_stat_records_are_not_published():
    recs = [_rec(f, _box(-66.0 + i * 0.01, 18.4)) for i, f in enumerate("abc")]
    ov = _overlay(recs, {"a": _survey(None), "b": {**_survey(9.0), "error": "down"},
                         "c": {**_survey(9.0), "stat": 1}}, rows=[_row(r.feature_id, 9.0) for r in recs])
    assert ov is None


def test_stale_when_the_osm_start_date_is_not_before_the_survey():
    a = _rec("a", _box(-66.0, 18.40))
    b = _rec("b", _box(-66.01, 18.40))
    osm = {"buildings": {"features": [
        {"geometry": {"type": "Polygon", "coordinates": [list(a.geometry.exterior.coords)]},
         "properties": {"osm_id": "way/1"}},
        {"geometry": {"type": "Polygon", "coordinates": [list(b.geometry.exterior.coords)]},
         "properties": {"osm_id": "way/2"}}]}}
    ov = _overlay([a, b], {"a": _survey(20.0, project=PROJECT), "b": _survey(20.0, project=PROJECT)},
                  rows=[_row("a", 12.0), _row("b", 12.0)], osm_data=osm, start_dates={"way/1": "2021-03-01", "way/2": "1975"})
    new, old = _row("a", 12.0), _row("b", 12.0)
    assert ov.apply(new) == "stale" and ov.apply(old) == "survey"
    assert new["tier"] == "survey" and new["survey_stale"] is True
    assert new["survey_stale_codes"] == ["start_date"] and "2021-03-01" in new["survey_stale_reason"]
    assert new["survey_start_date"] == "2021-03-01" and new["verified"] is False
    assert "flagged stale" in new["selection_reason"]
    assert old["survey_stale"] is False and old["survey_stale_codes"] == []


@pytest.mark.parametrize("readings, survey_m, stale", [
    ({"seed_1": 60.0, "seed_2": 64.0}, 20.0, True),     # agree, 3x the survey: a new building
    ({"seed_1": 60.0, "seed_2": 64.0}, 45.0, False),    # agree, within 2x
    ({"seed_1": 60.0, "seed_2": 110.0}, 20.0, False),   # do not agree: nothing to trust over the survey
    ({"seed_1": 60.0}, 20.0, False),                    # one reading is not an agreeing pair
])
def test_stale_when_agreeing_image_readings_sit_over_2x_from_the_survey(readings, survey_m, stale):
    a = _rec("a", _box(-66.0, 18.40))
    row = _row("a", 62.0, per_seed_median_m=readings)
    ov = _overlay([a], {"a": _survey(survey_m, project=PROJECT)}, rows=[row])
    assert ov.apply(row) == ("stale" if stale else "survey")
    assert row["tier"] == "survey" and row["effective_height_m"] == survey_m
    assert row["survey_stale"] is stale
    if stale:
        assert row["survey_stale_codes"] == ["readings_2x"]
        assert "likely a new building" in row["survey_stale_reason"]


def test_a_corroborated_row_far_from_the_survey_is_stale():
    a = _rec("a", _box(-66.0, 18.40), tag=80.0, src="osm_tag")
    row = _row("a", 80.0, tier="corroborated", tier_methods=["osm_tag", "lean"])
    ov = _overlay([a], {"a": _survey(15.0, project=PROJECT)})
    assert ov.apply(row) == "stale" and row["survey_stale_codes"] == ["readings_2x"]


def test_nested_part_does_not_publish_the_survey_value():
    tower = _rec("tower", _box(-66.0, 18.40, 0.0008, 0.0008), tag=60.0, src="osm_tag")
    podium = _rec("podium", _box(-65.9998, 18.4002, 0.0003, 0.0003), tag=12.0, src="osm_tag")
    ov = _overlay([tower, podium], {"tower": _survey(58.0, project=PROJECT),
                                    "podium": _survey(58.0, project=PROJECT)})
    p = {"feature_id": "podium", "effective_height_m": 12.0, "effective_height_source": "osm_tag",
         "tier": "tag", "no_survey_height_m": 12.0, "no_survey_tier": "tag"}
    t = {"feature_id": "tower", "effective_height_m": 60.0, "effective_height_source": "osm_tag",
         "tier": "tag", "no_survey_height_m": 60.0, "no_survey_tier": "tag"}
    assert ov.apply(p) == "nested" and ov.apply(t) == "survey"
    assert p["tier"] == "tag" and p["effective_height_m"] == 12.0
    assert p["survey_withheld_m"] == 58.0 and "top of the stack" in p["survey_withheld_reason"]
    assert "survey_m" not in p
    s = sp.summarize([p, t])
    assert s["n_survey"] == 1 and s["n_nested_withheld"] == 1 and s["n_stale"] == 0


def test_enabled_by_site_flag_with_env_override(monkeypatch):
    assert sp.enabled(True) and not sp.enabled(False)
    monkeypatch.setenv("SKYLINE_SURVEY_HEIGHTS", "0")
    assert not sp.enabled(True)
    monkeypatch.setenv("SKYLINE_SURVEY_HEIGHTS", "1")
    assert sp.enabled(False)


def test_heights_json_counts_survey_rows_and_the_reports_see_them(tmp_path):
    from city2stl.skyline.region_pdf import _write_heights_json
    from city2stl.skyline.tier_display import tier_counts, with_unmeasured_tags

    class BBox:
        north, south, east, west = 18.5, 18.3, -65.9, -66.1

    a = _rec("a", _box(-66.0, 18.40), tag=22.0, src="osm_tag", name="Tagged")
    b = _rec("b", _box(-66.01, 18.40), name="Measured")
    c = _rec("c", _box(-66.02, 18.40), tag=30.0, src="osm_tag", name="No survey")
    recs = [a, b, c]
    rows = [_row("b", 62.0, per_seed_median_m={"seed_1": 60.0, "seed_2": 64.0})]
    ov = _overlay(recs, {"a": _survey(25.0, project=PROJECT), "b": _survey(20.0, project=PROJECT)},
                  rows=rows)
    for r in rows:
        ov.apply(r)
    sp.set_active(ov)
    try:
        counts = tier_counts(with_unmeasured_tags(rows, recs))
        assert counts["survey"] == 2 and counts["tag"] == 1
        path = tmp_path / "heights.json"
        _write_heights_json(path, region_name="x", bbox=BBox, building_heights=rows,
                            building_records=recs, known_heights=None, survey=ov)
    finally:
        sp.set_active(None)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["tier_counts"]["survey"] == 2 and doc["tier_counts"]["tag"] == 1
    assert doc["survey"] == {"n_survey": 2, "n_stale": 1, "stale_by_code": {"readings_2x": 1},
                             "n_nested_withheld": 0, "providers": {PROJECT: 2}}
    by = {r["feature_id"]: r for r in doc["buildings"]}
    assert by["a"]["tier"] == "survey" and by["a"]["effective_height_m"] == 25.0
    assert by["a"]["no_survey_height_m"] == 22.0 and by["a"]["no_survey_tier"] == "tag"
    assert by["b"]["survey_stale"] is True and by["c"]["tier"] == "tag"


def test_survey_attribution_for_a_lidar_project_name():
    from city2stl.skyline.tier_display import survey_attributions

    assert survey_attributions([PROJECT]) == ["Survey heights: USGS 3D Elevation Program (3DEP) "
                                              "lidar. Public domain."]


# --------------------------------------------------------------------------- the benchmark


def _published(blind_m, survey_m, tier="single", key="k1", **kw):
    """A heights row as 2d writes it: the survey published, the survey-blind answer beside it."""
    return {"key": key, "effective_height_m": survey_m, "effective_height_source": "survey:p",
            "tier": "survey", "no_survey_height_m": blind_m, "no_survey_source": "withheld:elevated",
            "no_survey_tier": tier, "n_views": 1, "n_seeds": 1, **kw}


def test_survey_blind_puts_the_pre_survey_answer_back_and_drops_rows_it_cannot():
    rows = [_published(12.0, 30.0), {"key": "k2", "effective_height_m": 9.0, "tier": "tag"},
            {"key": "k3", "effective_height_m": 30.0, "tier": "survey"}]   # no no_survey_*: circular
    out = bm.survey_blind(rows)
    assert [r["key"] for r in out] == ["k1", "k2"]
    assert out[0]["effective_height_m"] == 12.0 and out[0]["tier"] == "single"
    assert out[0]["effective_height_source"] == "withheld:elevated"
    assert out[1] is rows[1]


def test_survey_rows_never_count_in_the_accuracy_scores():
    truth = {"k1": {"status": "survey_only", "truth_m": 30.0, "tiles_m": None},
             "k2": {"status": "survey_only", "truth_m": 20.0, "tiles_m": None}}
    rows = [_published(12.0, 30.0), {"key": "k2", "effective_height_m": 18.0, "tier": "prior",
                                      "no_survey_height_m": 18.0, "no_survey_tier": "prior",
                                      "n_views": 1, "n_seeds": 1}]
    blind = bm.survey_blind(rows)
    s = bm.score_buildings(blind, truth, pred_field="no_survey_height_m", statuses=("survey_only",))
    assert s["overall"]["n"] == 2
    assert s["overall"]["mae_m"] == pytest.approx((18.0 + 2.0) / 2)      # not (0 + 2) / 2
    tiers = bm.score_by_tier(blind, truth)
    assert "survey" not in tiers
    bands = bm.bench_tables(blind, {k: {**v, "status": "confirmed"} for k, v in truth.items()})
    assert "survey" not in json.dumps(bands["tiers"] if "tiers" in bands else bands)
    # the survey rows are checked against 3D Tiles only, and stale ones are counted apart
    rows[0]["survey_stale"] = True
    assert bm.score_survey_rows(rows, truth) == {"n_survey_rows": 1, "n_stale": 1,
                                                  "vs_tiles": {"n": 0}}


def test_score_report_headline_ignores_survey_rows(tmp_path, monkeypatch):
    """End to end through ``10_benchmark.score_report``: the headline and the tier tables are the
    survey-blind ones; the survey rows appear only in ``survey_rows``."""
    bench = importlib.import_module("city2stl.skyline.scripts.10_benchmark")
    ring = [[-66.0, 18.4], [-65.9996, 18.4], [-65.9996, 18.4004], [-66.0, 18.4004], [-66.0, 18.4]]
    ring2 = [[x - 0.01, y] for x, y in ring]
    key1, key2 = bm.footprint_key(ring), bm.footprint_key(ring2)
    base = {"n_views": 1, "n_seeds": 1, "measured": True, "height_tag_m": None,
            "height_source": "default"}
    rows = [
        {**base, "feature_id": "a", "footprint_lonlat": ring, "effective_height_m": 30.0,
         "effective_height_source": "survey:p", "tier": "survey", "no_survey_height_m": 12.0,
         "no_survey_source": "withheld:prior_gbm", "no_survey_tier": "prior"},
        {**base, "feature_id": "b", "footprint_lonlat": ring2, "effective_height_m": 18.0,
         "effective_height_source": "withheld:prior_gbm", "tier": "prior",
         "no_survey_height_m": 18.0, "no_survey_source": "withheld:prior_gbm",
         "no_survey_tier": "prior"}]
    heights = tmp_path / "testville_skyline_report" / "heights.json"
    heights.parent.mkdir()
    heights.write_text(json.dumps({"schema_version": 3, "region": "testville",
                                   "n_building_records": 2, "buildings": rows}), encoding="utf-8")
    truth = {key1: {"status": "confirmed", "truth_m": 30.0, "survey_m": 30.0, "tiles_m": 31.0},
             key2: {"status": "confirmed", "truth_m": 20.0, "survey_m": 20.0, "tiles_m": 20.0}}
    monkeypatch.setattr(bm, "REGIONS", {**bm.REGIONS, "testville": "usgs_3dep_ept"})
    monkeypatch.setattr(bm, "load_truth_cache", lambda region: truth)
    monkeypatch.setattr(bm, "score_known", lambda *_a, **_k: None)
    res = bench.score_report(heights, "testville", use_tiles=False, cached_truth=True)
    assert res["overall"]["n"] == 2
    assert res["overall"]["mae_m"] == pytest.approx((18.0 + 2.0) / 2)    # 12 vs 30 and 18 vs 20
    assert res["tiers"]["prior"]["n"] == 2 and "survey" not in res["tiers"]
    assert res["survey_rows"]["n_survey_rows"] == 1
    assert res["survey_rows"]["vs_tiles"]["mae_m"] == pytest.approx(1.0)  # survey 30 vs tiles 31
    assert "survey" not in json.dumps(res["bench"]["measured"]["tier_counts"]
                                      if "tier_counts" in res["bench"]["measured"] else {})
