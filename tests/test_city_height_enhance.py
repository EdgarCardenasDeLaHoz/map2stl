"""
Tests for city2stl.height.service.enhance_city_data provider handling.

US bboxes used to return early ("osm_only_us") and never reach any provider;
coarse providers used to be downloaded and then discarded.
"""
import numpy as np
import pytest

from city2stl.height import BUILDING_RESOLUTION_LIMIT_M, HeightResult, service

# Breckenridge, CO
N, S, E, W = 39.51, 39.43, -106.03, -106.125
DIM = 8


class _FakeProvider:
    def __init__(self, name, resolution_m, value=12.0):
        self.name = name
        self.resolution_m = resolution_m
        self.value = value
        self.calls = 0

    def covers(self, bbox):
        return True

    def fetch_heights(self, bbox, dim):
        self.calls += 1
        raster = np.full(dim, self.value, dtype=np.float32)
        return HeightResult(raster, np.full(dim, 0.9, dtype=np.float32),
                            self.name, self.resolution_m)


def _payload():
    lon, lat = (E + W) / 2, (N + S) / 2
    d = 1e-4
    ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
    return {"buildings": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
         "properties": {"height_m": 10.0, "height_source": "default"}},
    ]}}


@pytest.fixture
def providers(monkeypatch):
    """A fine and a coarse raster provider, and no lidar (it is network-bound)."""
    from city2stl.height.providers import lidar_3dep_copc
    monkeypatch.setattr(lidar_3dep_copc, "available", lambda: False)
    fine = _FakeProvider("fine", 3.0)
    coarse = _FakeProvider("coarse", BUILDING_RESOLUTION_LIMIT_M * 3, value=2.0)
    monkeypatch.setattr(service, "_select_providers", lambda bbox, requested=None: ([fine, coarse], []))
    monkeypatch.setattr(service, "_PROVIDER_MAP", {
        "fine": service.RegisteredProvider(fine, 0.6, fine.resolution_m),
        "coarse": service.RegisteredProvider(coarse, 0.8, coarse.resolution_m),
    })
    return fine, coarse


def test_us_bbox_is_enhanced(providers):
    out = service.enhance_city_data(_payload(), N, S, E, W, dim=DIM)

    props = out["buildings"]["features"][0]["properties"]
    assert out["height_enhancement"]["source_name"] != "osm_only_us"
    assert props["height_source"] != "default"
    assert props["height_m"] == pytest.approx(12.0)


def test_coarse_provider_is_not_fetched(providers):
    fine, coarse = providers
    out = service.enhance_city_data(_payload(), N, S, E, W, dim=DIM)

    assert fine.calls == 1
    assert coarse.calls == 0, "a source the resolution limit would discard is not downloaded"
    assert out["height_enhancement"]["providers_too_coarse"] == ["coarse"]
    assert out["height_enhancement"]["providers_used"] == ["fine"]


def _lidar(monkeypatch, heights, tiles=1):
    from city2stl.height.providers import lidar_3dep_copc
    monkeypatch.setattr(lidar_3dep_copc, "available", lambda: True)
    seen = {}

    def fake(polygons, bbox):
        seen.update(polygons)
        return heights, {"tiles": tiles, "measured": len(heights)}

    monkeypatch.setattr(lidar_3dep_copc, "footprint_heights", fake)
    return seen


def test_lidar_measures_before_rasters(providers, monkeypatch):
    fine, _ = providers
    seen = _lidar(monkeypatch, {0: 7.4})
    out = service.enhance_city_data(_payload(), N, S, E, W, dim=DIM)

    props = out["buildings"]["features"][0]["properties"]
    assert list(seen) == [0]
    assert (props["height_source"], props["height_m"]) == ("lidar_3dep_copc", 7.4)
    assert fine.calls == 0, "nothing left on default, so no raster is fetched"
    assert out["height_enhancement"]["providers_used"] == ["lidar_3dep_copc"]


def test_rasters_fill_what_lidar_could_not_measure(providers, monkeypatch):
    fine, _ = providers
    _lidar(monkeypatch, {})
    out = service.enhance_city_data(_payload(), N, S, E, W, dim=DIM)

    props = out["buildings"]["features"][0]["properties"]
    assert fine.calls == 1
    assert props["height_m"] == pytest.approx(12.0)
    assert out["height_enhancement"]["providers_used"] == ["lidar_3dep_copc", "fine"]


def test_no_lidar_outside_the_us(providers, monkeypatch):
    seen = _lidar(monkeypatch, {0: 7.4})
    service.enhance_city_data(_payload(), 10.4295, 10.3845, -75.5221, -75.5679, dim=DIM)
    assert seen == {}
