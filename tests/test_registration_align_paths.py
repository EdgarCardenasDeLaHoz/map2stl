"""city2stl.registration.align_paths: where packs and ground truth live (env overrides)."""

from __future__ import annotations

from pathlib import Path

from city2stl.registration import align_paths


def test_defaults_sit_beside_the_align_tool(monkeypatch):
    monkeypatch.delenv("ALIGN_DATA_DIR", raising=False)
    monkeypatch.delenv("ALIGN_GROUND_TRUTH_DIR", raising=False)
    assert align_paths.data_dir() == align_paths.ALIGN_TOOL / "data"
    assert align_paths.ground_truth_dir() == align_paths.ALIGN_TOOL / "ground_truth"
    assert align_paths.ALIGN_TOOL == align_paths.REPO_ROOT / "tools" / "align_tool"


def test_empty_env_var_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("ALIGN_DATA_DIR", "")
    monkeypatch.setenv("ALIGN_GROUND_TRUTH_DIR", "")
    assert align_paths.data_dir() == align_paths.ALIGN_TOOL / "data"
    assert align_paths.ground_truth_dir() == align_paths.ALIGN_TOOL / "ground_truth"


def test_env_vars_redirect_and_are_read_fresh(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("ALIGN_DATA_DIR", str(a))
    monkeypatch.setenv("ALIGN_GROUND_TRUTH_DIR", str(b))
    assert align_paths.data_dir() == a
    assert align_paths.ground_truth_dir() == b
    assert isinstance(align_paths.data_dir(), Path)
    # Read at call time, not import time: a second change is seen at once.
    monkeypatch.setenv("ALIGN_DATA_DIR", str(b))
    assert align_paths.data_dir() == b


def test_derived_cache_locations():
    assert align_paths.WATER_CACHE == align_paths.ALIGN_TOOL / "data" / "water_cache"
    assert align_paths.STREET_CACHE == align_paths.ALIGN_TOOL / "cache" / "street_place"
    assert align_paths.H5_ROOT.name  # a path with a leaf, whatever MAPS_ROOT is
