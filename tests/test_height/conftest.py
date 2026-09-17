"""Shared fixtures for the height-provider tests."""
import pytest


@pytest.fixture()
def tmp_cache_root(monkeypatch, tmp_path):
    """Redirect app.server.core.cache.CACHE_ROOT to tmp_path/cache."""
    import app.server.core.cache as cache_mod
    root = tmp_path / "cache"
    monkeypatch.setattr(cache_mod, "CACHE_ROOT", root)
    return root
