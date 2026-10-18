"""city2stl.panorama_heights: panorama rows replace default / raster heights, nothing measured."""

import json

import pytest

from city2stl.panorama_heights import apply_panorama_heights, load_rows, resolve_report


def _square(x, y, s=0.0004):
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]


def _feature(x, y, height=10.0, source="default", s=0.0004):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [_square(x, y, s)]},
            "properties": {"height_m": height, "height_source": source}}


def _row(x, y, tier="single", height=30.0, s=0.0004):
    return {"tier": tier, "effective_height_m": height, "footprint_lonlat": _square(x, y, s)}


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def test_corroborated_and_single_replace_default_and_raster_heights():
    fc = _fc(_feature(0, 0), _feature(1, 1, 7.0, "ms_buildings"), _feature(2, 2, 12.0, "default"))
    out, summary = apply_panorama_heights(
        fc, [_row(0, 0, "single", 31.2), _row(1, 1, "corroborated", 44.0)])
    props = [f["properties"] for f in out["features"]]
    assert (props[0]["height_m"], props[0]["height_source"]) == (31.2, "panorama_single")
    assert (props[1]["height_m"], props[1]["height_source"]) == (44.0, "panorama_corroborated")
    assert props[1]["height_prev_m"] == 7.0 and props[1]["height_prev_source"] == "ms_buildings"
    assert props[2]["height_m"] == 12.0
    assert summary["applied"] == {"single": 1, "corroborated": 1}


def test_input_is_not_modified():
    fc = _fc(_feature(0, 0))
    apply_panorama_heights(fc, [_row(0, 0)])
    assert fc["features"][0]["properties"] == {"height_m": 10.0, "height_source": "default"}


@pytest.mark.parametrize("source", ["osm_tag", "osm_levels", "landmark", "survey", "lidar_3dep_copc"])
def test_measured_or_stated_heights_are_kept(source):
    out, summary = apply_panorama_heights(_fc(_feature(0, 0, 25.0, source)), [_row(0, 0)])
    assert out["features"][0]["properties"]["height_m"] == 25.0
    assert summary["skipped"] == {"kept_measured": 1}
    assert summary["applied"] == {}


def test_tag_prior_and_survey_tiers_are_ignored():
    rows = [_row(0, 0, "tag"), _row(0, 0, "prior"), _row(0, 0, "survey")]
    out, summary = apply_panorama_heights(_fc(_feature(0, 0)), rows)
    assert out["features"][0]["properties"]["height_source"] == "default"
    assert summary["rows"] == {}


def test_withheld_or_missing_height_rows_are_ignored():
    rows = [_row(0, 0, height=None), _row(0, 0, height=0)]
    out, _ = apply_panorama_heights(_fc(_feature(0, 0)), rows)
    assert out["features"][0]["properties"]["height_source"] == "default"


def test_footprint_must_overlap_enough():
    # shifted by a quarter of the width: IoU 0.6 -> matches; shifted by a whole width: none
    near = _row(0.0001, 0, height=20.0)
    far = _row(0.0004, 0, height=40.0)
    out, summary = apply_panorama_heights(_fc(_feature(0, 0)), [far])
    assert out["features"][0]["properties"]["height_source"] == "default"
    assert summary["skipped"] == {"no_match": 1}
    out, _ = apply_panorama_heights(_fc(_feature(0, 0)), [near])
    assert out["features"][0]["properties"]["height_m"] == 20.0


def test_each_feature_takes_its_best_overlapping_row():
    rows = [_row(0.0001, 0, height=20.0), _row(0.00002, 0, height=33.0)]
    out, summary = apply_panorama_heights(_fc(_feature(0, 0)), rows)
    assert out["features"][0]["properties"]["height_m"] == 33.0
    assert summary["skipped"] == {"lost_to_better_overlap": 1}
    assert summary["applied"] == {"single": 1}


def test_row_without_footprint_and_empty_layer():
    row = {"tier": "single", "effective_height_m": 30.0}
    _, summary = apply_panorama_heights(_fc(_feature(0, 0)), [row])
    assert summary["skipped"] == {"no_footprint": 1}
    out, summary = apply_panorama_heights(_fc(), [_row(0, 0)])
    assert out["features"] == [] and summary["skipped"] == {"no_match": 1}


def test_load_rows_resolves_name_folder_and_file(tmp_path, monkeypatch):
    report = tmp_path / "Demo_v1_skyline_report"
    report.mkdir()
    (report / "heights.json").write_text(json.dumps({"buildings": [_row(0, 0), "junk"]}), "utf-8")
    monkeypatch.setenv("MAP2STL_SKYLINE_REPORTS", str(tmp_path))
    for spec in ("Demo_v1", "Demo_v1_skyline_report", report, report / "heights.json"):
        assert len(load_rows(spec)) == 1
    with pytest.raises(FileNotFoundError):
        resolve_report("Nope")
    with pytest.raises(FileNotFoundError):
        resolve_report("../escape")
