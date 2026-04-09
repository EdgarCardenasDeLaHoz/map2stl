"""
Unit tests for core/cache.py.

Tests cache key generation, array cache read/write/TTL, OSM cache read/write/TTL,
and cache pruning.
"""
import gzip
import json
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from core.cache import (
    make_cache_key,
    osm_cache_key,
    write_array_cache,
    read_array_cache,
    write_osm_cache,
    read_osm_cache,
    prune_cache,
    NAMESPACE_TTL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def patched_cache(tmp_path, monkeypatch):
    """Redirect CACHE_ROOT to a temp directory for all cache operations."""
    import core.cache as cache_mod
    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Cache key generation
# ---------------------------------------------------------------------------

class TestMakeCacheKey:
    def test_deterministic(self):
        k1 = make_cache_key("dem", 40.0, 39.9, -75.1, -75.2)
        k2 = make_cache_key("dem", 40.0, 39.9, -75.1, -75.2)
        assert k1 == k2

    def test_32_char_hex(self):
        key = make_cache_key("dem", 40.0, 39.9, -75.1, -75.2)
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)

    def test_namespace_affects_key(self):
        k_dem = make_cache_key("dem",   40.0, 39.9, -75.1, -75.2)
        k_wat = make_cache_key("water", 40.0, 39.9, -75.1, -75.2)
        assert k_dem != k_wat

    def test_extra_params_affect_key(self):
        k1 = make_cache_key("dem", 40.0, 39.9, -75.1, -75.2, {"dim": 100})
        k2 = make_cache_key("dem", 40.0, 39.9, -75.1, -75.2, {"dim": 200})
        assert k1 != k2

    def test_extra_params_sorted(self):
        k1 = make_cache_key("dem", 40.0, 39.9, -75.1, -75.2, {"a": 1, "b": 2})
        k2 = make_cache_key("dem", 40.0, 39.9, -75.1, -75.2, {"b": 2, "a": 1})
        assert k1 == k2


class TestOsmCacheKey:
    def test_deterministic(self):
        k1 = osm_cache_key(40.0, 39.9, -75.1, -75.2)
        k2 = osm_cache_key(40.0, 39.9, -75.1, -75.2)
        assert k1 == k2

    def test_default_params_included(self):
        """Key with explicit defaults must equal key with no extra args."""
        k_default = osm_cache_key(40.0, 39.9, -75.1, -75.2)
        k_explicit = osm_cache_key(40.0, 39.9, -75.1, -75.2, tol=0.5, min_area=5.0)
        assert k_default == k_explicit

    def test_tol_affects_key(self):
        k1 = osm_cache_key(40.0, 39.9, -75.1, -75.2, tol=0.5)
        k2 = osm_cache_key(40.0, 39.9, -75.1, -75.2, tol=2.0)
        assert k1 != k2

    def test_min_area_affects_key(self):
        k1 = osm_cache_key(40.0, 39.9, -75.1, -75.2, min_area=5.0)
        k2 = osm_cache_key(40.0, 39.9, -75.1, -75.2, min_area=20.0)
        assert k1 != k2

    def test_bbox_affects_key(self):
        k1 = osm_cache_key(40.0, 39.9, -75.1, -75.2)
        k2 = osm_cache_key(51.0, 50.9,   0.1,   0.0)
        assert k1 != k2


# ---------------------------------------------------------------------------
# Array cache
# ---------------------------------------------------------------------------

class TestArrayCache:
    def test_write_creates_npz_and_json(self, patched_cache):
        arrays = {"elev": np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)}
        write_array_cache("dem", "testkey", arrays, {"width": 2, "height": 2})

        assert (patched_cache / "dem" / "testkey.npz").exists()
        assert (patched_cache / "dem" / "testkey.json").exists()

    def test_read_returns_arrays_and_meta(self, patched_cache):
        arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        write_array_cache("dem", "k1", {"values": arr}, {"note": "hi"})

        result = read_array_cache("dem", "k1")
        assert result is not None
        arrays, meta = result
        np.testing.assert_array_almost_equal(arrays["values"], arr)
        assert meta["note"] == "hi"

    def test_read_returns_none_when_missing(self, patched_cache):
        assert read_array_cache("dem", "nonexistent") is None

    def test_read_returns_none_when_stale(self, patched_cache):
        arr = np.zeros(4, dtype=np.float32)
        write_array_cache("dem", "stale", {"v": arr})
        # Backdate the sidecar JSON so TTL check fails
        json_path = patched_cache / "dem" / "stale.json"
        meta = json.loads(json_path.read_text())
        meta["_cached_at"] = time.time() - NAMESPACE_TTL["dem"] - 1
        json_path.write_text(json.dumps(meta))

        assert read_array_cache("dem", "stale") is None

    def test_float32_downcast(self, patched_cache):
        """float64 arrays should be stored as float32."""
        arr64 = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        write_array_cache("dem", "f64", {"v": arr64})
        arrays, _ = read_array_cache("dem", "f64")
        assert arrays["v"].dtype == np.float32

    def test_write_failure_leaves_no_partial_files(self, patched_cache):
        """If np.savez_compressed raises, no partial files remain."""
        with patch("numpy.savez_compressed", side_effect=OSError("disk full")):
            write_array_cache("dem", "broken", {"v": np.zeros(4)})
        assert not (patched_cache / "dem" / "broken.npz").exists()
        assert not (patched_cache / "dem" / "broken.json").exists()


# ---------------------------------------------------------------------------
# OSM / GeoJSON cache
# ---------------------------------------------------------------------------

class TestOsmCache:
    SAMPLE = {
        "buildings": {"type": "FeatureCollection", "features": []},
        "roads":     {"type": "FeatureCollection", "features": []},
    }

    def test_write_creates_gz_file(self, patched_cache):
        write_osm_cache("osmkey1", self.SAMPLE)
        assert (patched_cache / "osm" / "osmkey1.json.gz").exists()

    def test_round_trip(self, patched_cache):
        write_osm_cache("osmkey2", self.SAMPLE)
        result = read_osm_cache("osmkey2")
        assert result == self.SAMPLE

    def test_read_returns_none_when_missing(self, patched_cache):
        assert read_osm_cache("no_such_key") is None

    def test_read_returns_none_when_stale(self, patched_cache):
        write_osm_cache("stale_osm", self.SAMPLE)
        gz_path = patched_cache / "osm" / "stale_osm.json.gz"
        # Backdate mtime past OSM TTL
        old_time = time.time() - NAMESPACE_TTL["osm"] - 1
        import os
        os.utime(gz_path, (old_time, old_time))
        assert read_osm_cache("stale_osm") is None

    def test_written_file_is_valid_gzip(self, patched_cache):
        write_osm_cache("gzip_check", self.SAMPLE)
        raw = (patched_cache / "osm" / "gzip_check.json.gz").read_bytes()
        decoded = json.loads(gzip.decompress(raw).decode())
        assert decoded == self.SAMPLE


# ---------------------------------------------------------------------------
# Cache pruning
# ---------------------------------------------------------------------------

class TestPruneCache:
    def test_prune_deletes_stale_files(self, patched_cache):
        ns_dir = patched_cache / "dem"
        ns_dir.mkdir()
        old_file = ns_dir / "old.npz"
        old_file.write_bytes(b"x")
        old_time = time.time() - NAMESPACE_TTL["dem"] - 1
        import os
        os.utime(old_file, (old_time, old_time))

        deleted = prune_cache("dem")
        assert deleted >= 1
        assert not old_file.exists()

    def test_prune_keeps_fresh_files(self, patched_cache):
        ns_dir = patched_cache / "dem"
        ns_dir.mkdir()
        fresh_file = ns_dir / "fresh.npz"
        fresh_file.write_bytes(b"x")

        deleted = prune_cache("dem")
        assert deleted == 0
        assert fresh_file.exists()

    def test_prune_limits_to_max_files(self, patched_cache):
        ns_dir = patched_cache / "dem"
        ns_dir.mkdir()
        # Create 5 fresh files; set max_files=3 → 2 oldest should be removed
        files = []
        for i in range(5):
            f = ns_dir / f"file{i}.npz"
            f.write_bytes(b"x")
            files.append(f)
            time.sleep(0.01)  # ensure distinct mtimes

        deleted = prune_cache("dem", max_files=3)
        assert deleted == 2

    def test_prune_returns_zero_for_nonexistent_namespace(self, patched_cache):
        assert prune_cache("nonexistent") == 0
