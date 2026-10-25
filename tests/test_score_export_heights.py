"""Offline checks of the F-PANOVIEW height score and pano helpers (synthetic data only)."""

import json

import numpy as np
import pytest

from tools.eval import pano_manifest, render_pano_view
from tools.eval import score_export_heights as sc


def test_stats_mae_bias_within():
    est = np.array([10.0, 20.0, 30.0])
    truth = np.array([10.0, 10.0, 40.0])
    s = sc.stats(est, truth)
    assert s["n"] == 3
    assert s["mae_m"] == pytest.approx(6.67, abs=0.01)
    assert s["bias_m"] == pytest.approx(0.0)
    assert s["within_25pct"] == pytest.approx(66.7, abs=0.1)   # exact and 30 vs 40


def test_score_splits_by_source_and_matches_by_osm_id():
    res = sc.score(np.array([12.0, 9.0, 50.0, np.nan]), ["osm_tag", "survey", "survey", "survey"],
                   ["way/1", "way/2", "way/3", "way/1"], {"way/1": 10.0, "way/2": 10.0})
    assert res["overall"]["n"] == 2          # way/3 has no truth, the NaN is skipped
    assert res["by_source"]["osm_tag"]["bias_m"] == 2.0
    assert res["by_source"]["survey"]["bias_m"] == -1.0
    assert "survey" in sc.format_table("t", res)


def test_exclude_sources_holds_survey_rows_out():
    keep = sc.exclude_sources(["osm_tag", "survey", "osm_levels"], ["survey"])
    assert keep == [True, False, True]
    assert sc.exclude_sources(["survey"], []) == [True]


def test_load_truth_keeps_positive_and_tallest(tmp_path):
    p = tmp_path / "truth.json"
    p.write_text(json.dumps({"a": {"osm_id": "way/1", "truth_m": 8.0}, "b": {"osm_id": "way/1", "truth_m": 12.0},
                             "c": {"osm_id": "way/2", "truth_m": None}, "d": {"osm_id": "way/3", "truth_m": 0}}))
    assert sc.load_truth(p) == {"way/1": 12.0}


def test_georef_round_trip_and_true_scale():
    g = sc.Georef()
    x, y = g.to_mm(-3.5993, 37.1766)
    lon, lat = g.to_lonlat(x, y)
    assert (lon, lat) == pytest.approx((-3.5993, 37.1766))
    assert 0.3 < g.z_mm_per_m < 0.4          # 999 mm over ~2.8 km


def test_bearing_and_camera_basis():
    assert pano_manifest.bearing(37.0, -3.0, 37.1, -3.0) == pytest.approx(0.0, abs=0.01)
    assert pano_manifest.bearing(37.0, -3.0, 37.0, -2.9) == pytest.approx(90.0, abs=0.2)
    fwd, right, up = render_pano_view.camera_basis(90.0, 0.0)   # looking east
    assert fwd == pytest.approx([1, 0, 0], abs=1e-9)
    assert right == pytest.approx([0, -1, 0], abs=1e-9)         # south is to the right
    assert up == pytest.approx([0, 0, 1], abs=1e-9)


def test_is_google_distinguishes_user_spheres():
    assert pano_manifest.is_google({"copyright": "\u00a9 2026 Google"})
    assert not pano_manifest.is_google({"copyright": "\u00a9 Nicola Mastrogiacomo"})


def test_scene_outside_moves_a_camera_out_of_footprints():
    import shapely
    scene = object.__new__(render_pano_view.Scene)
    scene.polys = np.array([shapely.box(0, 0, 10, 10), shapely.box(-5, -10, 15, -0.5)], dtype=object)
    scene._ptree = shapely.STRtree(scene.polys)
    assert scene.outside(20.0, 20.0) == (20.0, 20.0, False)
    e, n, moved = scene.outside(5.0, 1.0)   # the first step out lands in the neighbour below
    assert moved
    assert not shapely.contains_xy(shapely.union_all(scene.polys), e, n)
    assert shapely.union_all(scene.polys).distance(shapely.Point(e, n)) == pytest.approx(
        render_pano_view.CLEAR_M, abs=0.05)
    scene.polys = None
    assert scene.outside(5.0, 1.0) == (5.0, 1.0, False)
