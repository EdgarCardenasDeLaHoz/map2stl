"""SKYLINE_LOCAL_DIR routes the single-use skyline caches out of runs/."""
from __future__ import annotations

from city2stl.skyline import local_dirs


def test_default_is_runs(monkeypatch):
    monkeypatch.delenv(local_dirs.ENV, raising=False)
    assert local_dirs.local_dir("image_cache") == local_dirs.RUNS / "image_cache"


def test_env_moves_it(monkeypatch, tmp_path):
    monkeypatch.setenv(local_dirs.ENV, str(tmp_path))
    assert local_dirs.local_dir("benchmark") == tmp_path / "benchmark"


def test_blank_env_is_unset(monkeypatch):
    monkeypatch.setenv(local_dirs.ENV, "  ")
    assert local_dirs.local_dir("x") == local_dirs.RUNS / "x"


def test_cache_paths_follow_a_per_run_config(tmp_path):
    """The Street View, satellite and benchmark folders are read at call time, so a run's
    ``SkylineConfig.local_dir`` override (not only the import-time env) reaches them."""
    from city2stl.skyline import benchmark, config, satellite_image, streetview_io

    cfg = config.SkylineConfig(local_dir=str(tmp_path))
    with config.use(cfg):
        assert streetview_io._sv_image_cache_dir() == tmp_path / "image_cache"
        assert streetview_io._negative_cache_path("k") == tmp_path / "image_cache" / "k.none"
        assert satellite_image._cache_dir() == tmp_path / "satellite_image_cache"
        assert benchmark.benchmark_runs() == tmp_path / "benchmark"
    assert streetview_io._sv_image_cache_dir() != tmp_path / "image_cache"
