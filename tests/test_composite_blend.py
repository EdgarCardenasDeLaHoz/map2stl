"""
Direct tests for the composite DEM arithmetic (F-COMPOSITE3).

These functions had no real coverage before: the only dem-merge tests run
under MAP2STL_TEST_MODE, which replaces the whole pipeline with a linspace
stub, so blend_layers, apply_layer_processing and fetch_layer_data were never
executed. They are pure functions, so they are tested here directly, outside
the endpoint and outside TEST_MODE.
"""
import numpy as np
import pytest

from app.server.schemas import MergeLayerSpec, ProcessingSpec
from geo2stl.dem import (
    fetch_layer_data,
    register_layer_source,
    registered_layer_sources,
)
from geo2stl.processing import apply_layer_processing, blend_layers, upsample_dem

# ---------------------------------------------------------------------------
# blend_layers
# ---------------------------------------------------------------------------

@pytest.fixture
def base():
    return np.array([[10.0, 20.0], [30.0, 40.0]])


@pytest.fixture
def layer():
    return np.array([[1.0, 0.0], [2.0, 0.0]])


def test_base_mode_ignores_the_layer(base, layer):
    out = blend_layers(base, layer, "base", 5.0, base.shape)
    assert np.array_equal(out, base)
    assert out is not base  # a copy, so later blends cannot mutate the input


def test_replace_mode_only_touches_nonzero_layer_pixels(base, layer):
    out = blend_layers(base, layer, "replace", 1.0, base.shape)
    assert np.array_equal(out, np.array([[1.0, 20.0], [2.0, 40.0]]))


def test_add_mode_raises_terrain_by_weighted_layer(base, layer):
    out = blend_layers(base, layer, "add", 3.0, base.shape)
    assert np.array_equal(out, np.array([[13.0, 20.0], [36.0, 40.0]]))


def test_rivers_mode_is_the_exact_inverse_of_add(base, layer):
    added = blend_layers(base, layer, "add", 3.0, base.shape)
    back = blend_layers(added, layer, "rivers", 3.0, base.shape)
    assert np.allclose(back, base)


def test_blend_mode_is_a_weighted_average_not_an_addition(base, layer):
    out = blend_layers(base, layer, "blend", 0.25, base.shape)
    assert np.allclose(out, base * 0.75 + layer * 0.25)


def test_max_and_min_ignore_weight(base, layer):
    assert np.array_equal(blend_layers(base, layer, "max", 99.0, base.shape), base)
    assert np.array_equal(blend_layers(base, layer, "min", 99.0, base.shape), layer)


def test_layer_is_resized_to_the_base_shape(base):
    big = np.ones((8, 8))
    out = blend_layers(base, big, "add", 1.0, base.shape)
    assert out.shape == base.shape
    assert np.allclose(out, base + 1.0)


def test_unknown_mode_names_the_valid_ones(base, layer):
    with pytest.raises(ValueError) as exc:
        blend_layers(base, layer, "subtract", 1.0, base.shape)
    assert "add" in str(exc.value)


def test_every_schema_blend_mode_is_implemented(base, layer):
    """The Literal in MergeLayerSpec and the dispatch in blend_layers must not
    drift apart — an accepted mode that raises is a 500 on a valid request."""
    modes = MergeLayerSpec.model_fields["blend_mode"].annotation.__args__
    for mode in modes:
        blend_layers(base, layer, mode, 1.0, base.shape)


# ---------------------------------------------------------------------------
# apply_layer_processing
# ---------------------------------------------------------------------------

def test_processing_defaults_are_a_passthrough():
    arr = np.array([[1.0, 5.0], [9.0, 3.0]])
    out = apply_layer_processing(arr, ProcessingSpec())
    assert np.allclose(out, arr)


def test_clip_bounds_are_applied():
    arr = np.array([[0.0, 50.0], [100.0, 25.0]])
    out = apply_layer_processing(arr, ProcessingSpec(clip_min=10, clip_max=60))
    assert out.min() == 10.0 and out.max() == 60.0


def test_normalize_maps_onto_zero_to_one():
    arr = np.array([[100.0, 200.0], [300.0, 400.0]])
    out = apply_layer_processing(arr, ProcessingSpec(normalize=True))
    assert out.min() == 0.0 and out.max() == 1.0


def test_invert_mirrors_around_the_range():
    arr = np.array([[0.0, 1.0], [2.0, 3.0]])
    out = apply_layer_processing(arr, ProcessingSpec(invert=True))
    assert np.allclose(out, np.array([[3.0, 2.0], [1.0, 0.0]]))


def test_smoothing_pulls_a_spike_down():
    arr = np.zeros((9, 9))
    arr[4, 4] = 100.0
    out = apply_layer_processing(arr, ProcessingSpec(smooth_sigma=1.5))
    assert out[4, 4] < 100.0
    assert out[3, 4] > 0.0
    assert np.isclose(out.sum(), arr.sum(), rtol=1e-3)


# ---------------------------------------------------------------------------
# upsample_dem
# ---------------------------------------------------------------------------

def test_upsample_only_grows_a_smaller_grid():
    small = np.zeros((50, 50), dtype=np.float32)
    assert upsample_dem(small, 200).shape == (200, 200)
    assert upsample_dem(small, 10).shape == (50, 50)


# ---------------------------------------------------------------------------
# fetch_layer_data registry
# ---------------------------------------------------------------------------

def test_registered_source_wins_over_the_builtin_dispatch():
    calls = {}

    def provider(north, south, east, west, dim, options):
        calls.update(dim=dim, options=options, north=north)
        return np.full((dim, dim), 7.0)

    register_layer_source("unit_test_source", provider)
    try:
        out = fetch_layer_data("unit_test_source", 40.0, 39.0, -74.0, -75.0,
                               4, {"detail": "coarse"})
        assert out.shape == (4, 4)
        assert calls["options"] == {"detail": "coarse"}
        assert calls["north"] == 40.0
        assert "unit_test_source" in registered_layer_sources()
    finally:
        from geo2stl.dem import _LAYER_SOURCES
        _LAYER_SOURCES.pop("unit_test_source", None)


def test_provider_receives_an_empty_bag_when_options_are_omitted():
    seen = {}

    def provider(north, south, east, west, dim, options):
        seen["options"] = options
        return np.zeros((dim, dim))

    register_layer_source("unit_test_source2", provider)
    try:
        fetch_layer_data("unit_test_source2", 1.0, 0.0, 1.0, 0.0, 2)
        assert seen["options"] == {}
    finally:
        from geo2stl.dem import _LAYER_SOURCES
        _LAYER_SOURCES.pop("unit_test_source2", None)


def test_the_city_channels_are_registered_by_the_server():
    """Importing the composite router must expose the OSM channels, or a
    composite spec naming them falls through to the built-in dispatch."""
    import app.server.routers.composite  # noqa: F401  (registers on import)
    names = registered_layer_sources()
    for channel in ("osm_buildings", "osm_roads", "osm_waterways", "osm_walls"):
        assert channel in names


# ---------------------------------------------------------------------------
# compute_composite_dem, with real arithmetic
# ---------------------------------------------------------------------------

def test_composite_adds_and_subtracts_registered_layers(monkeypatch, tmp_path):
    """A base plus an added layer minus a subtracted one, through the real
    pipeline — TEST_MODE is switched off so the arithmetic actually runs."""
    import app.server.config as config_mod
    from app.server.routers import composite as composite_mod

    monkeypatch.setattr(config_mod, "TEST_MODE", False, raising=False)
    # Both cache calls resolve at call time inside compute_composite_dem, so
    # they are patched on the cache module, not on the router.
    monkeypatch.setattr("app.server.core.cache.read_array_cache",
                        lambda *a, **k: None)
    monkeypatch.setattr("app.server.core.cache.write_array_cache",
                        lambda *a, **k: None)

    def flat(value):
        def provider(north, south, east, west, dim, options):
            return np.full((dim, dim), float(value))
        return provider

    register_layer_source("unit_base", flat(100.0))
    register_layer_source("unit_up", flat(2.0))
    register_layer_source("unit_down", flat(5.0))

    try:
        out = composite_mod.compute_composite_dem(
            {"north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2},
            dim=64,
            layers=[
                {"source": "unit_base", "dim": 64, "blend_mode": "base"},
                {"source": "unit_up", "dim": 64, "blend_mode": "add", "weight": 3.0},
                {"source": "unit_down", "dim": 64, "blend_mode": "rivers", "weight": 2.0},
            ],
        )
    finally:
        from geo2stl.dem import _LAYER_SOURCES
        for name in ("unit_base", "unit_up", "unit_down"):
            _LAYER_SOURCES.pop(name, None)

    # 100 + 2*3 - 5*2 = 96
    assert out.shape == (64, 64)
    assert np.allclose(out, 96.0)


def test_composite_cache_key_separates_projections():
    from app.server.routers.composite import _composite_cache_key
    layers = [MergeLayerSpec(source="local", blend_mode="base")]
    plain = _composite_cache_key(40.0, 39.0, -74.0, -75.0, 600, layers, "none")
    merc = _composite_cache_key(40.0, 39.0, -74.0, -75.0, 600, layers, "mercator")
    assert plain != merc


def test_composite_projects_every_layer(monkeypatch):
    """A projected composite must go through project_grid, not cv2.resize
    alone: an unprojected composite does not line up with the DEM it
    replaces."""
    import app.server.config as config_mod
    import geo2stl.projections as proj_mod
    from app.server.routers import composite as composite_mod

    monkeypatch.setattr(config_mod, "TEST_MODE", False, raising=False)
    monkeypatch.setattr("app.server.core.cache.read_array_cache",
                        lambda *a, **k: None)
    monkeypatch.setattr("app.server.core.cache.write_array_cache",
                        lambda *a, **k: None)

    def provider(north, south, east, west, dim, options):
        return np.ones((dim, dim))

    register_layer_source("unit_proj", provider)

    seen = []
    real_project = proj_mod.project_grid

    def spy(arr, *args, **kwargs):
        seen.append(args[4] if len(args) > 4 else kwargs.get("projection"))
        return real_project(arr, *args, **kwargs)

    monkeypatch.setattr(proj_mod, "project_grid", spy)

    try:
        out = composite_mod.compute_composite_dem(
            {"north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2},
            dim=64,
            layers=[{"source": "unit_proj", "dim": 64, "blend_mode": "base"}],
            projection="mercator",
        )
    finally:
        from geo2stl.dem import _LAYER_SOURCES
        _LAYER_SOURCES.pop("unit_proj", None)

    assert seen == ["mercator"]
    assert out.ndim == 2


def test_base_layer_weight_scales_the_grid(monkeypatch):
    """The first layer sets the grid and never reaches blend_layers, so its
    weight has to be applied where the grid is built or the panel's DEM weight
    is silently dropped."""
    import app.server.config as config_mod
    from app.server.routers import composite as composite_mod

    monkeypatch.setattr(config_mod, "TEST_MODE", False, raising=False)
    monkeypatch.setattr("app.server.core.cache.read_array_cache",
                        lambda *a, **k: None)
    monkeypatch.setattr("app.server.core.cache.write_array_cache",
                        lambda *a, **k: None)

    register_layer_source(
        "unit_scaled",
        lambda north, south, east, west, dim, options: np.full((dim, dim), 10.0))
    try:
        out = composite_mod.compute_composite_dem(
            {"north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2},
            dim=64,
            layers=[{"source": "unit_scaled", "dim": 64,
                     "blend_mode": "base", "weight": 2.5}],
        )
    finally:
        from geo2stl.dem import _LAYER_SOURCES
        _LAYER_SOURCES.pop("unit_scaled", None)

    assert np.allclose(out, 25.0)
