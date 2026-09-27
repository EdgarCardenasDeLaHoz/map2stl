"""Shared fixtures for the height-provider tests."""
import pytest


@pytest.fixture()
def tmp_cache_root(monkeypatch, tmp_path):
    """Redirect geo2stl.cache.CACHE_ROOT to tmp_path/cache."""
    import geo2stl.cache as cache_mod
    root = tmp_path / "cache"
    monkeypatch.setattr(cache_mod, "CACHE_ROOT", root)
    return root
