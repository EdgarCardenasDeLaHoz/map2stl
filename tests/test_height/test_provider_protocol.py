"""Every height provider satisfies the runtime-checkable HeightProvider protocol.

Constructs each provider and checks its shape only; nothing here touches the
network. The list covers every provider class under
``city2stl/skyline/height/providers/`` (a superset of the server registry in
``app/server/core/height/service.py``).
"""
from __future__ import annotations

import importlib

import numpy as np
import pytest

from city2stl.skyline.height import HeightProvider, HeightResult

_PROVIDERS = [
    ("copernicus", "CopernicusProvider"),
    ("gba", "GBAProvider"),
    ("ghsl", "GHSLProvider"),
    ("google_3d", "Google3DProvider"),
    ("lidar_3dep", "LiDAR3DEPProvider"),
    ("ndsm", "NDSMProvider"),
    ("open_buildings", "OpenBuildingsProvider"),
    ("shadow_height", "ShadowHeightProvider"),
    ("wsf3d", "WSF3DProvider"),
]


@pytest.mark.parametrize(("module", "cls_name"), _PROVIDERS, ids=[m for m, _ in _PROVIDERS])
def test_provider_satisfies_protocol(module: str, cls_name: str) -> None:
    mod = importlib.import_module(f"city2stl.skyline.height.providers.{module}")
    provider = getattr(mod, cls_name)()

    assert isinstance(provider, HeightProvider)
    assert isinstance(provider.name, str) and provider.name
    assert callable(provider.covers)
    assert callable(provider.fetch_heights)


def test_protocol_rejects_non_provider() -> None:
    class NotAProvider:
        name = "nope"

        def covers(self, bbox):
            return False

    assert not isinstance(NotAProvider(), HeightProvider)


def test_height_result_empty() -> None:
    res = HeightResult.empty((3, 4), "demo", 10.0)

    assert res.raster.shape == (3, 4) and res.confidence.shape == (3, 4)
    assert res.raster.dtype == np.float32 and res.confidence.dtype == np.float32
    assert np.isnan(res.raster).all()
    assert not res.confidence.any()
    assert res.source_name == "demo" and res.resolution_m == 10.0


_EMPTY_NAMES = [
    ("copernicus", "copernicus"),
    ("gba", "gba"),
    ("ghsl", "ghsl"),
    ("google_3d", "google3d"),
    ("lidar_3dep", "lidar_3dep"),
    ("open_buildings", "open_buildings"),
    ("shadow_height", "shadow_height"),
    ("wsf3d", "wsf3d"),
]


@pytest.mark.parametrize(("module", "source_name"), _EMPTY_NAMES, ids=[m for m, _ in _EMPTY_NAMES])
def test_module_empty_result_keeps_source_name(module: str, source_name: str) -> None:
    mod = importlib.import_module(f"city2stl.skyline.height.providers.{module}")
    res = mod._empty_result((2, 5))

    assert res.source_name == source_name
    assert res.resolution_m == mod._RESOLUTION_M
    assert res.raster.shape == (2, 5) and np.isnan(res.raster).all()
