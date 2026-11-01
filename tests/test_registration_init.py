"""city2stl.registration (package __init__): OSMReference and register_city_stl, with osm_raster faked."""

from __future__ import annotations

import math

import numpy as np
import pytest

import city2stl.registration as reg
from city2stl import osm_raster
from city2stl.registration import OSMReference
from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

BBOX = (40.01, 40.0, -75.0, -75.01)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Every osm_raster entry point the class can reach fails loudly unless a test fakes it."""
    def boom(*a, **k):
        raise AssertionError("network call in an offline test")
    for name in ("estimate_bbox_from_stl", "get_osm_building_heightmap", "get_osm_semantic_masks",
                 "get_city_center_point", "derive_scale_m_per_unit", "resolve_bbox"):
        monkeypatch.setattr(osm_raster, name, boom)
    monkeypatch.setattr("geo2stl.osm.overpass_query", boom)


# ------------------------------------------------------------------------------ construction

def test_constructor_normalises_a_bbox_and_names_it():
    ref = OSMReference([40.01, 40, -75, -75.01])
    assert ref.city == (40.01, 40.0, -75.0, -75.01)
    assert all(isinstance(v, float) for v in ref.city)
    assert ref.name == f"bbox {ref.city}"


def test_constructor_keeps_a_city_name_and_defaults():
    ref = OSMReference("Philadelphia, PA, USA")
    assert ref.city == ref.name == "Philadelphia, PA, USA"
    assert (ref.default_height, ref.levels_to_meters) == (10.0, 3.5)
    assert ref.center is None and ref.tallest_m is None and ref.scale_m_per_unit is None
    assert (ref.search_radius_km, ref.search_step_km) == (4.0, 1.0)


# ------------------------------------------------------------------------------ resolve_target

def test_resolve_target_returns_the_tight_bbox_for_a_named_city(monkeypatch):
    seen = {}

    def fake(city, z, extent, **kw):
        seen.update(city=city, z=z, extent=extent, kw=kw)
        return BBOX

    monkeypatch.setattr(osm_raster, "estimate_bbox_from_stl", fake)
    ref = OSMReference("Testville", center=(40.0, -75.0), tallest_m=120.0, scale_m_per_unit=0.5)
    assert ref.resolve_target(30.0, 200.0, 1.4) == (BBOX, True)
    assert seen["city"] == "Testville" and seen["z"] == 30.0 and seen["extent"] == 200.0
    assert seen["kw"] == {"osm_margin": 1.4, "center": (40.0, -75.0),
                          "tallest_m": 120.0, "scale_m_per_unit": 0.5}


def test_resolve_target_falls_back_to_the_whole_city_without_an_anchor(monkeypatch):
    monkeypatch.setattr(osm_raster, "estimate_bbox_from_stl", lambda *a, **k: None)
    assert OSMReference("Testville").resolve_target(30.0, 200.0, 1.4) == ("Testville", False)


def test_resolve_target_for_a_bbox_never_estimates():
    # estimate_bbox_from_stl is the loud fake: a bbox city must not reach it
    ref = OSMReference(BBOX)
    assert ref.resolve_target(30.0, 200.0, 1.4) == (BBOX, False)


# ------------------------------------------------------------------------------ delegation

def test_building_heightmap_passes_the_height_settings(monkeypatch):
    seen = {}

    def fake(target, **kw):
        seen["target"], seen["kw"] = target, kw
        return {"heightmap": np.zeros((2, 2))}

    monkeypatch.setattr(osm_raster, "get_osm_building_heightmap", fake)
    ref = OSMReference("Testville", default_height=7.0, levels_to_meters=3.0)
    out = ref.building_heightmap(BBOX, 256)
    assert out["heightmap"].shape == (2, 2)
    assert seen["target"] == BBOX
    assert seen["kw"] == {"resolution": 256, "default_height": 7.0, "levels_to_meters": 3.0}


def test_semantic_masks_delegates(monkeypatch):
    monkeypatch.setattr(osm_raster, "get_osm_semantic_masks",
                        lambda target, resolution: {"water": (target, resolution)})
    assert OSMReference("Testville").semantic_masks(BBOX, 128) == {"water": (BBOX, 128)}


def test_ndsm_resolves_the_bbox_and_calls_the_lidar_provider(monkeypatch):
    from city2stl.height.providers import lidar_3dep_ept
    seen = {}
    monkeypatch.setattr(osm_raster, "resolve_bbox", lambda target: ("resolved", target))

    def fake(bbox, resolution):
        seen["args"] = (bbox, resolution)
        return np.ones((3, 3), np.float32)

    monkeypatch.setattr(lidar_3dep_ept, "get_ndsm", fake)
    out = OSMReference("Testville").ndsm("Testville", 64)
    assert out.shape == (3, 3)
    assert seen["args"] == (("resolved", "Testville"), 64)


def test_ndsm_passes_none_through(monkeypatch):
    from city2stl.height.providers import lidar_3dep_ept
    monkeypatch.setattr(osm_raster, "resolve_bbox", lambda t: BBOX)
    monkeypatch.setattr(lidar_3dep_ept, "get_ndsm", lambda bbox, resolution: None)
    assert OSMReference(BBOX).ndsm(BBOX, 64) is None


def test_m_per_unit_uses_the_anchors(monkeypatch):
    seen = {}

    def fake(city, z, **kw):
        seen.update(city=city, z=z, kw=kw)
        return 0.25

    monkeypatch.setattr(osm_raster, "derive_scale_m_per_unit", fake)
    ref = OSMReference("Testville", tallest_m=300.0)
    assert ref.m_per_unit(12.0) == 0.25
    assert seen["city"] == "Testville" and seen["z"] == 12.0
    assert seen["kw"] == {"tallest_m": 300.0, "scale_m_per_unit": None}


# ------------------------------------------------------------------------------ candidate_targets

def _echo_bbox(monkeypatch, record=None):
    def fake(city, z, extent, osm_margin, center, tallest_m, scale_m_per_unit):
        if record is not None:
            record.append(center)
        lat, lon = center
        return (lat + 0.005, lat - 0.005, lon + 0.005, lon - 0.005)
    monkeypatch.setattr(osm_raster, "estimate_bbox_from_stl", fake)


def test_candidate_targets_has_the_initial_plus_rings_of_eight(monkeypatch):
    _echo_bbox(monkeypatch)
    ref = OSMReference("Testville", center=(40.0, -75.0))
    cands = ref.candidate_targets(30.0, 200.0, 1.4)
    assert len(cands) == 1 + 8 * 4                 # radius 4 km, step 1 km
    (c0, bbox0) = cands[0]
    assert c0 == (40.0, -75.0)
    assert bbox0 == (40.005, 39.995, -74.995, -75.005)
    # ring r (1..4 km): 8 centres at that distance, compass order starting north
    mlon = float(m_per_deg_lon(40.0))
    for ring in range(1, 5):
        for k in range(8):
            (lat, lon), _ = cands[1 + (ring - 1) * 8 + k]
            dy = (lat - 40.0) * M_PER_DEG_LAT
            dx = (lon + 75.0) * mlon
            assert math.hypot(dx, dy) == pytest.approx(ring * 1000.0, rel=1e-6)
            theta = 2 * math.pi * k / 8
            assert dy == pytest.approx(ring * 1000.0 * math.cos(theta), abs=1e-6)
            assert dx == pytest.approx(ring * 1000.0 * math.sin(theta), abs=1e-6)
    assert cands[1][0][0] > 40.0 and cands[1][0][1] == pytest.approx(-75.0)      # first is due north
    assert cands[3][0][1] > -75.0 and cands[3][0][0] == pytest.approx(40.0, abs=1e-6)   # third is east


def test_candidate_targets_respects_radius_and_step(monkeypatch):
    _echo_bbox(monkeypatch)
    ref = OSMReference("Testville", center=(10.0, 20.0), search_radius_km=2.0, search_step_km=0.5)
    assert len(ref.candidate_targets(30.0, 200.0, 1.4)) == 1 + 8 * 4
    ref = OSMReference("Testville", center=(10.0, 20.0), search_radius_km=0.4, search_step_km=1.0)
    assert len(ref.candidate_targets(30.0, 200.0, 1.4)) == 1        # no ring fits


def test_candidate_targets_skip_centres_without_a_bbox(monkeypatch):
    def fake(city, z, extent, osm_margin, center, tallest_m, scale_m_per_unit):
        return None if center[0] > 40.0 else (1.0, 0.0, 1.0, 0.0)
    monkeypatch.setattr(osm_raster, "estimate_bbox_from_stl", fake)
    cands = OSMReference("Testville", center=(40.0, -75.0)).candidate_targets(30.0, 200.0, 1.4)
    assert 1 < len(cands) < 33
    assert all(c[0] <= 40.0 for c, _ in cands)


def test_candidate_targets_geocodes_when_there_is_no_centre(monkeypatch):
    _echo_bbox(monkeypatch)
    monkeypatch.setattr(osm_raster, "get_city_center_point", lambda city: (41.0, -73.0))
    cands = OSMReference("Testville").candidate_targets(30.0, 200.0, 1.4)
    assert len(cands) == 33 and cands[0][0] == (41.0, -73.0)


def test_candidate_targets_explicit_centre_beats_geocoding(monkeypatch):
    # get_city_center_point is the loud fake: the explicit centre must be used instead
    _echo_bbox(monkeypatch)
    cands = OSMReference("Testville", center=(12.0, 34.0)).candidate_targets(30.0, 200.0, 1.4)
    assert cands[0][0] == (12.0, 34.0)


def test_candidate_targets_empty_when_geocoding_fails(monkeypatch, caplog):
    monkeypatch.setattr(osm_raster, "get_city_center_point", lambda city: None)
    with caplog.at_level("WARNING", logger="city2stl.registration"):
        assert OSMReference("Nowhere").candidate_targets(30.0, 200.0, 1.4) == []
    assert "geocoding failed" in caplog.text


def test_candidate_targets_empty_for_a_bbox():
    assert OSMReference(BBOX).candidate_targets(30.0, 200.0, 1.4) == []


def test_candidate_targets_passes_the_scale_anchors_and_margin(monkeypatch):
    seen = []

    def fake(city, z, extent, **kw):
        seen.append((city, z, extent, kw))
        return BBOX

    monkeypatch.setattr(osm_raster, "estimate_bbox_from_stl", fake)
    ref = OSMReference("Testville", center=(40.0, -75.0), tallest_m=90.0, scale_m_per_unit=0.3,
                       search_radius_km=1.0)
    ref.candidate_targets(21.0, 150.0, 1.7)
    assert len(seen) == 9
    for city, z, extent, kw in seen:
        assert (city, z, extent) == ("Testville", 21.0, 150.0)
        assert kw["osm_margin"] == 1.7 and kw["tallest_m"] == 90.0 and kw["scale_m_per_unit"] == 0.3


# ------------------------------------------------------------------------------ register_city_stl

def test_register_city_stl_builds_the_reference_and_forwards_the_rest(monkeypatch):
    seen = {}

    def fake(stl_file, ref, **kw):
        seen.update(stl_file=stl_file, ref=ref, kw=kw)
        return "report"

    monkeypatch.setattr(reg, "_register", fake)
    out = reg.register_city_stl(
        "city.stl", "Testville", 512, 8.0, 3.0, center=(40.0, -75.0), tallest_m=100.0,
        scale_m_per_unit=0.4, out_dir="somewhere", free_scale=True)
    assert out == "report"
    ref = seen["ref"]
    assert isinstance(ref, OSMReference) and ref.city == "Testville"
    assert (ref.default_height, ref.levels_to_meters) == (8.0, 3.0)
    assert ref.center == (40.0, -75.0) and ref.tallest_m == 100.0 and ref.scale_m_per_unit == 0.4
    assert seen["stl_file"] == "city.stl"
    assert seen["kw"] == {"resolution": 512, "out_dir": "somewhere", "free_scale": True}


def test_register_city_stl_defaults_and_bbox_city(monkeypatch):
    seen = {}
    monkeypatch.setattr(reg, "_register", lambda f, ref, **kw: seen.update(ref=ref, kw=kw))
    reg.register_city_stl("city.stl", BBOX)
    assert seen["ref"].city == BBOX and seen["ref"].name.startswith("bbox")
    assert seen["kw"] == {"resolution": 1024}
    assert (seen["ref"].default_height, seen["ref"].levels_to_meters) == (10.0, 3.5)


def test_public_names():
    assert sorted(reg.__all__) == ["OSMReference", "register_city_stl"]
