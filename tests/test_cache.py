"""
Unit tests for core/cache.py.

Tests cache key generation, array cache read/write/TTL, OSM cache read/write/TTL,
and cache pruning.
"""
import asyncio
import gzip
import json
import os
import time
import zipfile
from unittest.mock import patch

import numpy as np
import pytest

from app.server.core.cache import (
    NAMESPACE_TTL,
    ORPHAN_GRACE_SECONDS,
    make_cache_key,
    osm_cache_key,
    prune_cache,
    read_array_cache,
    read_osm_cache,
    write_array_cache,
    write_array_cache_async,
    write_osm_cache,
)
from app.server.core.cache import _savez_fast as _savez_fast_orig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def patched_cache(tmp_path, monkeypatch):
    """Redirect CACHE_ROOT to a temp directory for all cache operations."""
    import geo2stl.cache as cache_mod
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
        """If the npz writer raises, no partial files remain."""
        with patch("app.server.core.cache._savez_fast", side_effect=OSError("disk full")):
            write_array_cache("dem", "broken", {"v": np.zeros(4)})
        assert not (patched_cache / "dem" / "broken.npz").exists()
        assert not (patched_cache / "dem" / "broken.json").exists()
        assert not list((patched_cache / "dem").glob("*.tmp"))

    def test_failed_rewrite_keeps_no_temp_file(self, patched_cache):
        """A write that dies mid-way never truncates the file readers see."""
        write_array_cache("dem", "k", {"v": np.ones(4)})
        with patch("app.server.core.cache._savez_fast", side_effect=OSError("disk full")):
            write_array_cache("dem", "k", {"v": np.zeros(4)})
        assert not list((patched_cache / "dem").glob("*.tmp"))

    def test_cloud_placeholder_is_a_miss_and_dropped(self, patched_cache):
        """A dehydrated OneDrive placeholder (open() -> Errno 22) reads as a miss
        and is removed so the re-fetch can write a local copy."""
        import geo2stl.cache as cache_mod
        write_array_cache("wsf3d", "ph", {"height": np.ones(4)})
        with patch.object(cache_mod, "_is_cloud_placeholder", return_value=True):
            assert read_array_cache("wsf3d", "ph") is None
        assert not (patched_cache / "wsf3d" / "ph.npz").exists()
        assert not (patched_cache / "wsf3d" / "ph.json").exists()


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

def _touch(path, age_s: float = 0.0, size: int = 1):
    """Create *path* with *size* bytes and an mtime *age_s* seconds ago."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    ts = time.time() - age_s
    os.utime(path, (ts, ts))
    return path


def _pair(ns_dir, key, age_s: float = 0.0, size: int = 1):
    """An array entry: ``key.npz`` plus its ``key.json`` sidecar."""
    return (_touch(ns_dir / f"{key}.npz", age_s, size),
            _touch(ns_dir / f"{key}.json", age_s))


OLD = ORPHAN_GRACE_SECONDS + 60  # past the in-progress-write grace, inside every TTL


class TestPruneCache:
    def test_prune_deletes_stale_pairs_together(self, patched_cache):
        npz, js = _pair(patched_cache / "dem", "old", NAMESPACE_TTL["dem"] + 1)
        assert prune_cache("dem") == 2
        assert not npz.exists() and not js.exists()

    def test_prune_keeps_fresh_pairs(self, patched_cache):
        npz, js = _pair(patched_cache / "dem", "fresh")
        assert prune_cache("dem") == 0
        assert npz.exists() and js.exists()

    def test_ttl_uses_the_newest_file_of_an_entry(self, patched_cache):
        """A stale .npz whose sidecar was just rewritten is one fresh entry."""
        d = patched_cache / "dem"
        npz = _touch(d / "k.npz", NAMESPACE_TTL["dem"] + 1)
        js = _touch(d / "k.json")
        assert prune_cache("dem") == 0
        assert npz.exists() and js.exists()

    def test_cap_counts_entries_not_files(self, patched_cache):
        """5 pairs, cap 3: the 2 oldest pairs go, both halves each (4 files)."""
        d = patched_cache / "dem"
        pairs = [_pair(d, f"k{i}", age_s=50 - i) for i in range(5)]
        assert prune_cache("dem", max_entries=3) == 4
        assert [p[0].exists() and p[1].exists() for p in pairs] == [False, False, True, True, True]
        assert not any(p[0].exists() or p[1].exists() for p in pairs[:2])

    def test_cap_by_bytes_evicts_oldest_entries(self, patched_cache):
        d = patched_cache / "dem"
        pairs = [_pair(d, f"k{i}", age_s=50 - i, size=100) for i in range(4)]
        prune_cache("dem", max_bytes=250)  # each pair is 101 bytes
        assert [p[0].exists() for p in pairs] == [False, False, True, True]
        assert [p[1].exists() for p in pairs] == [False, False, True, True]

    def test_orphan_halves_are_removed(self, patched_cache):
        d = patched_cache / "dem"
        lone_npz = _touch(d / "a.npz", OLD)
        lone_json = _touch(d / "b.json", OLD)
        lone_params = _touch(patched_cache / "osm" / "c.params.json", OLD)
        keep = _pair(d, "ok", OLD)
        assert prune_cache("dem") == 2
        assert prune_cache("osm") == 1
        assert not lone_npz.exists() and not lone_json.exists() and not lone_params.exists()
        assert keep[0].exists() and keep[1].exists()

    def test_young_orphans_are_left_for_the_writer(self, patched_cache):
        """The .npz lands before its sidecar: a fresh lone half may be mid-write."""
        d = patched_cache / "dem"
        npz = _touch(d / "a.npz")
        tmp = _touch(d / "b.json.123.456.tmp")
        assert prune_cache("dem") == 0
        assert npz.exists() and tmp.exists()

    def test_old_temp_files_are_removed(self, patched_cache):
        tmp = _touch(patched_cache / "dem" / "a.npz.123.456.tmp", OLD)
        assert prune_cache("dem") == 1
        assert not tmp.exists()

    def test_single_file_entries_are_complete(self, patched_cache):
        """``.json.gz`` (+ osm ``.params.json``) and opentopo ``.tif`` are not orphans."""
        osm = patched_cache / "osm"
        gz, params = _touch(osm / "o.json.gz", OLD), _touch(osm / "o.params.json", OLD)
        tif = _touch(patched_cache / "opentopo" / "t.tif", OLD)
        assert prune_cache("osm") == 0 and prune_cache("opentopo") == 0
        assert gz.exists() and params.exists() and tif.exists()

    def test_prune_leaves_subdirectories_alone(self, patched_cache):
        inner = _touch(patched_cache / "dem" / "sub" / "x.npz", NAMESPACE_TTL["dem"] + 1)
        assert prune_cache("dem") == 0
        assert inner.exists()

    def test_prune_returns_zero_for_nonexistent_namespace(self, patched_cache):
        assert prune_cache("nonexistent") == 0


# ---------------------------------------------------------------------------
# Fast array writer: format compatibility and the async wrapper
# ---------------------------------------------------------------------------

class TestFastArrayWrite:
    def test_old_format_npz_still_loads(self, patched_cache):
        """An entry written by the old np.savez_compressed code reads back."""
        d = patched_cache / "dem"
        d.mkdir()
        dem = np.random.default_rng(0).normal(100, 20, (40, 30)).astype(np.float32)
        np.savez_compressed(d / "legacy.npz", dem=dem)
        (d / "legacy.json").write_text(json.dumps({"_cached_at": time.time(), "shape": [40, 30]}))
        arrays, meta = read_array_cache("dem", "legacy")
        np.testing.assert_array_equal(arrays["dem"], dem)
        assert meta["shape"] == [40, 30]

    def test_new_save_round_trips(self, patched_cache):
        dem = np.random.default_rng(1).normal(100, 20, (64, 48)).astype(np.float32)
        dem[3, 4] = np.nan
        faces = np.arange(12, dtype=np.int64).reshape(4, 3)
        write_array_cache("dem", "k", {"dem": dem}, {"bbox": [1, 2, 3, 4]})
        write_array_cache("mesh", "m", {"faces": faces}, keep_dtype=True)
        arrays, meta = read_array_cache("dem", "k")
        np.testing.assert_array_equal(arrays["dem"], dem)  # NaN == NaN here
        assert meta["bbox"] == [1, 2, 3, 4] and "_cached_at" in meta
        m, _ = read_array_cache("mesh", "m")
        assert m["faces"].dtype == np.int64
        np.testing.assert_array_equal(m["faces"], faces)

    def test_new_save_is_zlib_level_1_npz(self, patched_cache):
        write_array_cache("dem", "z", {"dem": np.zeros((50, 50))})
        with zipfile.ZipFile(patched_cache / "dem" / "z.npz") as zf:
            (info,) = zf.infolist()
        assert info.filename == "dem.npy"
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert info.compress_size < info.file_size

    def test_stored_level_also_loads(self, patched_cache, monkeypatch):
        import app.server.core.cache as app_cache
        monkeypatch.setattr(app_cache, "_savez_fast",
                            lambda f, a: _savez_fast_orig(f, a, level=None))
        a = np.arange(6, dtype=np.float32)
        write_array_cache("dem", "s", {"v": a})
        np.testing.assert_array_equal(read_array_cache("dem", "s")[0]["v"], a)

    def test_async_save_runs_off_the_loop_and_round_trips(self, patched_cache):
        import threading
        seen = {}
        import app.server.core.cache as app_cache
        real = app_cache.write_array_cache

        def spy(*args, **kw):
            seen["thread"] = threading.get_ident()
            return real(*args, **kw)

        a = np.linspace(0, 1, 20, dtype=np.float32)

        async def go():
            seen["loop"] = threading.get_ident()
            with patch.object(app_cache, "write_array_cache", spy):
                await write_array_cache_async("dem", "a", {"v": a}, {"n": 20})

        asyncio.run(go())
        assert seen["thread"] != seen["loop"]
        arrays, meta = read_array_cache("dem", "a")
        np.testing.assert_array_equal(arrays["v"], a)
        assert meta["n"] == 20
