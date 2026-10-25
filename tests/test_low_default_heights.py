"""Untagged ruins and fortress enclosures default to 3 m, not 10 m, and a
ruin's levels tag is ignored (city2stl.heights.low_height_override /
apply_low_default_heights, and the
post-cache pass app.server.core.city_data.with_low_default_heights)."""

from __future__ import annotations

import copy

import geopandas as gpd
import pytest
from shapely.geometry import box

from app.server.core.city_data import with_low_default_heights
from city2stl.heights import (
    LOW_DEFAULT_HEIGHT_M,
    _fill_heights,
    apply_levels_heights,
    apply_low_default_heights,
    default_height_for,
    is_low_default_footprint,
    low_height_override,
)


@pytest.mark.parametrize("props", [
    {"building": "ruins"},
    {"building": "yes", "historic": "ruins"},
    {"building": "yes", "ruins": "yes"},
    {"building": "courtyard"},
    {"building": "yes", "historic": "castle"},          # Granada's Alcazaba
    {"building": "yes", "historic": "fort"},
    {"building": "yes", "historic": "fortification"},
    {"building": "yes", "historic": "citywalls"},
    {"building": "yes", "historic": "castle", "castle_type": "defensive"},
    {"building": "Ruins "},
])
def test_low_default_footprints(props):
    assert is_low_default_footprint(props)
    assert default_height_for(props, 10.0) == LOW_DEFAULT_HEIGHT_M == 3.0


@pytest.mark.parametrize("props", [
    {"building": "yes"},
    {"building": "yes", "historic": "castle", "castle_type": "palace"},
    {"building": "yes", "historic": "castle", "castle_type": "Schloss"},
    {"building": "yes", "historic": "castle", "castle_type": "château"},
    {"building": "yes", "historic": "castle", "castle_type": "stately"},
    {"building": "yes", "historic": "monument"},
    {"building": "yes", "ruins": "no"},
    {"building": float("nan"), "historic": float("nan")},
])
def test_other_footprints_keep_flat_default(props):
    assert not is_low_default_footprint(props)
    assert default_height_for(props, 10.0) == 10.0


def test_never_raises_a_lower_default():
    assert default_height_for({"building": "ruins"}, 2.0) == 2.0
    assert low_height_override({"building": "ruins"}, 2.5, "osm_levels", 10.0) == (2.5, "default")
    assert low_height_override({"building": "ruins"}, 9.0, "osm_tag", 10.0) == (9.0, "osm_tag")


def _gdf(rows):
    return gpd.GeoDataFrame(rows, geometry=[box(i, 0, i + 1, 1) for i in range(len(rows))],
                            crs="EPSG:4326")


def test_fill_heights_low_default_and_tags_win():
    gdf = _gdf([
        {"building": "ruins"},
        {"building": "yes", "historic": "castle"},
        {"building": "yes", "historic": "castle", "castle_type": "palace"},
        {"building": "ruins", "height": "12"},
        {"building": "yes", "historic": "castle", "building:levels": "2"},
        {"building": "courtyard"},
        {"building": "yes"},
        {"building": "ruins", "building:levels": "3"},          # levels ignored on a ruin
        {"building": "yes", "ruins": "yes", "building:levels": "2"},
        {"building": "ruins", "height": "8", "building:levels": "3"},   # height wins
    ])
    out = _fill_heights(gdf, default_m=10.0, lo=3.0, hi=300.0,
                        levels_col="building:levels", low_default=True)
    assert out["height_m"].tolist() == [3.0, 3.0, 10.0, 12.0, 6.4, 3.0, 10.0, 3.0, 3.0, 8.0]
    assert out["height_source"].tolist() == [
        "default", "default", "default", "osm_tag", "osm_levels", "default", "default",
        "default", "default", "osm_tag"]


def test_fill_heights_without_flag_is_flat():
    out = _fill_heights(_gdf([{"building": "ruins"}]), default_m=10.0, lo=3.0)
    assert out["height_m"].tolist() == [10.0]


def _fc(*props):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": p} for p in props]}


def test_post_cache_lowers_only_default_rows_and_is_idempotent():
    fc = _fc(
        {"building": "yes", "historic": "castle", "height_m": 10.0, "height_source": "default"},
        {"building": "ruins", "height_m": 10.0, "height_source": "default"},
        {"building": "ruins", "height_m": 7.0, "height_source": "osm_tag"},
        {"building": "ruins", "height_m": 6.5, "height_source": "lidar"},
        {"building": "yes", "height_m": 10.0, "height_source": "default"},
        {"building": "yes", "historic": "castle", "castle_type": "palace",
         "height_m": 10.0, "height_source": "default"},
        {"building": "ruins", "height_m": 12.8, "height_source": "osm_levels"},
        {"building": "yes", "historic": "castle", "height_m": 9.6, "height_source": "osm_levels"},
    )
    before = copy.deepcopy(fc)
    out, n = apply_low_default_heights(fc)
    assert fc == before                              # input untouched
    assert n == 3
    hs = [f["properties"]["height_m"] for f in out["features"]]
    assert hs == [3.0, 3.0, 7.0, 6.5, 10.0, 10.0, 3.0, 9.6]
    srcs = [f["properties"]["height_source"] for f in out["features"]]
    assert srcs == ["default", "default", "osm_tag", "lidar", "default", "default",
                    "default", "osm_levels"]
    again, n2 = apply_low_default_heights(out)
    assert n2 == 0 and again is out


def test_with_low_default_heights_payload():
    payload = {"buildings": _fc({"building": "ruins", "height_m": 10.0,
                                 "height_source": "default"}),
               "roads": _fc({"highway": "primary"}), "cache_key": "k"}
    out = with_low_default_heights(payload)
    assert out["buildings"]["features"][0]["properties"]["height_m"] == 3.0
    assert out["buildings"]["features"][0]["properties"]["height_source"] == "default"
    assert out["roads"] is payload["roads"] and out["cache_key"] == "k"
    assert payload["buildings"]["features"][0]["properties"]["height_m"] == 10.0
    assert with_low_default_heights(out) is out
    assert with_low_default_heights({}) == {}


def test_post_cache_rederives_levels_without_the_flat_roof_floor():
    # Cached before 2026-10-20: every levels-tagged building got a roof floor.
    fc = _fc(
        {"building:levels": "4", "height_m": 16.0, "height_source": "osm_levels"},
        {"building:levels": "4", "roof:shape": "flat", "height_m": 16.0,
         "height_source": "osm_levels"},
        {"building:levels": "4", "roof:shape": "gabled", "height_m": 16.0,
         "height_source": "osm_levels"},
        {"building:levels": "4", "roof:levels": "2", "height_m": 16.0,
         "height_source": "osm_levels"},
        {"building:levels": "4", "height_m": 22.0, "height_source": "osm_tag"},
        {"building:levels": "4", "height_m": 19.0, "height_source": "survey"},
        {"height_m": 9.6, "height_source": "osm_levels"},       # tags lost: keep
    )
    before = copy.deepcopy(fc)
    out, n = apply_levels_heights(fc)
    assert fc == before
    assert n == 3
    hs = [f["properties"]["height_m"] for f in out["features"]]
    assert hs == [12.8, 12.8, 16.0, 19.2, 22.0, 19.0, 9.6]
    assert all(f["properties"]["height_source"] == g["properties"]["height_source"]
               for f, g in zip(out["features"], fc["features"], strict=True))
    again, n2 = apply_levels_heights(out)
    assert n2 == 0 and again is out


def test_with_low_default_heights_rederives_levels_before_lowering_ruins():
    payload = {"buildings": _fc(
        {"building:levels": "3", "height_m": 12.8, "height_source": "osm_levels"},
        {"building": "ruins", "building:levels": "3", "height_m": 12.8,
         "height_source": "osm_levels"})}
    props = [f["properties"] for f in with_low_default_heights(payload)["buildings"]["features"]]
    assert [p["height_m"] for p in props] == [9.6, 3.0]
    assert [p["height_source"] for p in props] == ["osm_levels", "default"]
