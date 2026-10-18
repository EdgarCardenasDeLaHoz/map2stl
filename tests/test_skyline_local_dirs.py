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
