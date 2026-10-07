"""skyline._pano.stage_cache: the drone-seed per-stage disk cache."""

import os
import threading
import time

import numpy as np
import pytest
from shapely.geometry import Polygon

from city2stl.skyline import footprint_detect as fd
from city2stl.skyline._pano import stage_cache as sc


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setenv(sc.ENV_ROOT, str(tmp_path / "cache"))
    monkeypatch.delenv(sc.ENV_SWITCH, raising=False)
    return tmp_path / "cache"


class Counter:
    def __init__(self, value):
        self.n, self.value = 0, value

    def __call__(self):
        self.n += 1
        return self.value


def _depth(h=60, w=200, seed=0):
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, h)[:, None] * np.linspace(0.2, 1, w)[None, :]
    return (y + rng.normal(0, 1e-3, (h, w))).astype(np.float32)


def _instances(h=60, w=200):
    a = np.zeros((h, w), np.int32)
    a[10:40, 20:60], a[5:55, 100:180] = 7, 219
    return a


# --------------------------------------------------------------------------- hit / miss


def test_miss_then_hit_and_other_key_misses(_root):
    c = Counter(_instances())
    a = sc.cached("instances", 3, ("pano", "abc"), c)
    b = sc.cached("instances", 3, ("pano", "abc"), c)
    assert c.n == 1 and np.array_equal(a, b) and b.dtype == np.int32
    sc.cached("instances", 3, ("pano", "abd"), c)
    assert c.n == 2
    assert len(sc.entries(root=_root)) == 2


def test_version_bump_invalidates_only_that_stage():
    d, i = Counter(_depth()), Counter(_instances())
    sc.cached("depth", 1, ("p",), d)
    sc.cached("instances", 3, ("p",), i)
    sc.cached("depth", 2, ("p",), d)                    # bumped: recomputed
    assert d.n == 2
    sc.cached("instances", 3, ("p",), i)                # untouched stage: still a hit
    sc.cached("depth", 1, ("p",), d)                    # old version kept until pruned
    assert (d.n, i.n) == (2, 1)
    assert {(e.stage, e.version) for e in sc.entries()} == {("depth", "1"), ("depth", "2"),
                                                           ("instances", "3")}


def test_off_switch_computes_and_writes_nothing(_root, monkeypatch):
    monkeypatch.setenv(sc.ENV_SWITCH, "off")
    c = Counter(_depth())
    sc.cached("depth", 1, ("p",), c)
    sc.cached("depth", 1, ("p",), c)
    assert c.n == 2 and not _root.exists()


def test_refresh_recomputes_and_overwrites(monkeypatch):
    sc.cached("depth", 1, ("p",), Counter(_depth(seed=1)))
    monkeypatch.setenv(sc.ENV_SWITCH, "refresh")
    c = Counter(_depth(seed=2))
    sc.cached("depth", 1, ("p",), c)
    assert c.n == 1
    monkeypatch.setenv(sc.ENV_SWITCH, "on")
    assert np.array_equal(sc.cached("depth", 1, ("p",), Counter(None)), _depth(seed=2))


def test_none_is_not_cached_unless_asked():
    c = Counter(None)
    assert sc.cached("pose", 1, ("p",), c, kind="pickle") is None
    assert sc.cached("pose", 1, ("p",), c, kind="pickle") is None
    assert c.n == 2
    c2 = Counter(None)
    sc.cached("pose", 1, ("q",), c2, kind="pickle", cache_none=True)
    assert sc.cached("pose", 1, ("q",), c2, kind="pickle", cache_none=True) is None
    assert c2.n == 1


def test_corrupt_entry_is_recomputed():
    c = Counter(_instances())
    sc.cached("instances", 3, ("p",), c)
    path = sc.entries()[0].path
    path.write_bytes(b"not a zip")
    out = sc.cached("instances", 3, ("p",), c)
    assert c.n == 2 and np.array_equal(out, _instances())
    assert np.array_equal(sc.read_entry(path)[0], _instances())       # rewritten


# --------------------------------------------------------------------------- atomic writes


def test_failed_write_leaves_no_partial_file(monkeypatch):
    sc.cached("depth", 1, ("p",), Counter(_depth(seed=1)))
    path = sc.entries()[0].path
    before = path.read_bytes()

    def boom(fh, **kw):
        fh.write(b"PK partial")
        raise OSError("disk full")

    monkeypatch.setattr(sc.np, "savez_compressed", boom)
    monkeypatch.setenv(sc.ENV_SWITCH, "refresh")
    out = sc.cached("depth", 1, ("p",), Counter(_depth(seed=2)))     # write fails: still returns
    assert np.array_equal(out, _depth(seed=2))
    assert path.read_bytes() == before                                # old entry intact
    assert [p.name for p in path.parent.iterdir()] == [path.name]     # no temp left behind


def test_parallel_writers_of_one_entry():
    errors, outs = [], []

    def work(k):
        try:
            outs.append(sc.cached("instances", 3, ("same",), Counter(_instances() + 0 * k)))
        except Exception as exc:                                      # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and all(np.array_equal(o, _instances()) for o in outs)
    es = sc.entries()
    assert len(es) == 1 and not list(es[0].path.parent.glob("*.tmp"))
    assert np.array_equal(sc.read_entry(es[0].path)[0], _instances())


# --------------------------------------------------------------------------- compact storage


@pytest.mark.parametrize("a", [
    _instances(),
    np.where(_instances() > 0, _instances(), -1).astype(np.int16),          # labels with -1
    np.arange(-70000, 70000, 7, dtype=np.int64).reshape(-1, 200),
    _instances() > 0,
    (np.random.default_rng(3).integers(0, 255, (40, 90, 3))).astype(np.uint8),   # rgb
    _depth(),
    _depth().astype(np.float64),
    np.array([np.nan, np.inf, -0.0, 1e-30, 3.5], np.float32),
    np.zeros((0, 5), np.float32),
    np.array(2.5, np.float32),
])
def test_round_trip_is_exact(a, tmp_path):
    p = tmp_path / "e.npz"
    sc.write_entry(p, a, "npy")
    back, meta = sc.read_entry(p)
    assert back.dtype == a.dtype and back.shape == a.shape and not meta["lossy"]
    assert back.tobytes() == np.ascontiguousarray(a).tobytes()      # bitwise: NaN, -0.0 too


def test_instances_and_depth_are_much_smaller_than_raw(tmp_path):
    inst = np.zeros((600, 2000), np.int32)
    for k in range(1, 120):
        r, c = (k * 37) % 550, (k * 113) % 1900
        inst[r:r + 50, c:c + 100] = k
    depth = _depth(600, 2000)
    for name, a in (("i", inst), ("d", depth)):
        p = tmp_path / f"{name}.npz"
        sc.write_entry(p, a)
        ratio = a.nbytes / p.stat().st_size
        assert ratio >= (20 if name == "i" else 1.5), (name, ratio)


def test_float16_is_lossy_within_tolerance_and_not_served_as_lossless():
    d = _depth()
    out = sc.cached("depth", 1, ("p",), Counter(d), lossy_float16=True)
    assert out is d                                          # the computed array itself
    back = sc.cached("depth", 1, ("p",), Counter(None), lossy_float16=True)
    assert back.dtype == np.float32
    assert np.allclose(back, d, rtol=4.9e-4, atol=1e-7) and not np.array_equal(back, d)
    c = Counter(d)
    exact = sc.cached("depth", 1, ("p",), c)                 # lossless asked: recomputed
    assert c.n == 1 and np.array_equal(exact, d)


def test_pickle_kind_keeps_dataclasses_and_their_arrays():
    pano = fd.Pano("seed_9", 10.4, -75.55, (np.arange(30 * 40 * 3) % 251).astype(np.uint8)
                   .reshape(30, 40, 3), np.full((30, 40), 2, np.int16),
                   np.linspace(0, 359, 40), 57.3, -4.0, horizon_deg=-1.25)
    pose = fd.PanoPose(30.0, 80.0, 0.1, 0.3, 40)
    val = {"pano": pano, "pose": pose, "list": [1, "a", None]}
    sc.cached("pano", 1, ("v",), Counter(val), kind="pickle")
    got = sc.cached("pano", 1, ("v",), Counter(None), kind="pickle")
    assert got["pose"] == pose and got["list"] == [1, "a", None]
    p = got["pano"]
    assert isinstance(p, fd.Pano) and p.horizon_deg == -1.25 and p.name == "seed_9"
    assert np.array_equal(p.rgb, pano.rgb) and p.labels.dtype == np.int16
    assert np.array_equal(p.frame_heading, pano.frame_heading)


def test_npy_kind_rejects_non_arrays_without_failing_the_caller():
    assert sc.cached("x", 1, ("p",), Counter([1, 2])) == [1, 2]
    assert sc.entries() == []


# --------------------------------------------------------------------------- keys


def test_hash_parts_is_stable_and_discriminating():
    poly = Polygon([(0, 0), (1, 0), (1, 1)])
    a = np.arange(6, dtype=np.int32)
    k = sc.hash_parts({"b": 1, "a": [poly, a]}, fd.PanoPose(1.0, 2.0, 0.0, 0.1, 9))
    assert k == sc.hash_parts({"a": [poly, a], "b": 1}, fd.PanoPose(1.0, 2.0, 0.0, 0.1, 9))
    assert k != sc.hash_parts({"b": 1, "a": [poly, a.astype(np.int64)]},
                              fd.PanoPose(1.0, 2.0, 0.0, 0.1, 9))
    assert k != sc.hash_parts({"b": 1, "a": [poly, a]}, fd.PanoPose(1.0, 2.0, 0.0, 0.1, 10))
    assert sc.hash_parts(float("nan")) == sc.hash_parts(np.float64("nan"))
    assert sc.hash_parts(1) != sc.hash_parts(1.0) != sc.hash_parts("1")


def test_source_hash_follows_the_code():
    assert sc.source_hash(fd) == sc.source_hash(fd)
    assert sc.source_hash(fd) != sc.source_hash(sc)
    assert sc.source_hash(sc.cached) != sc.source_hash(sc.prune)


# --------------------------------------------------------------------------- legacy pano_cache


def test_legacy_names_map_to_the_wiring_keys():
    h = "6097651140c43298f1cf"
    assert sc.legacy_target(f"{h}_depth_v1.npy") == ("depth", "1", sc.depth_parts(h))
    assert sc.legacy_target(f"{h}_inst_v3_w1283_g34.npy") == (
        "instances", "3", sc.instances_parts(h, 1283, 34))
    assert sc.legacy_target("readme.npy") is None
    rgb = np.zeros((4, 4, 3), np.uint8)
    import hashlib
    assert sc.pano_digest(rgb) == hashlib.sha1(rgb.tobytes()).hexdigest()[:20]


def test_legacy_file_is_read_and_converted(tmp_path):
    leg = tmp_path / "pano_cache"
    leg.mkdir()
    h = "0123456789abcdef0123"
    np.save(leg / f"{h}_depth_v1.npy", _depth())
    c = Counter(None)
    out = sc.cached("depth", 1, sc.depth_parts(h), c,
                    legacy_path=sc.legacy_path(h, "depth_v1", leg))
    assert c.n == 0 and np.array_equal(out, _depth())
    (leg / f"{h}_depth_v1.npy").unlink()
    assert np.array_equal(sc.cached("depth", 1, sc.depth_parts(h), c), _depth())
    assert c.n == 0


def test_migrate_pano_cache(tmp_path):
    leg = tmp_path / "pano_cache"
    leg.mkdir()
    h = "0123456789abcdef0123"
    np.save(leg / f"{h}_depth_v1.npy", _depth())
    np.save(leg / f"{h}_inst_v3_w448_g12.npy", _instances())
    np.save(leg / "junk.npy", np.zeros(3))
    assert all(r["status"] in ("would convert", "skipped (unknown name)")
               for r in sc.migrate_pano_cache(leg, dry_run=True))
    rows = sc.migrate_pano_cache(leg, delete=True)
    st = {r["file"]: r["status"] for r in rows}
    assert st[f"{h}_depth_v1.npy"] == "converted, legacy deleted"
    assert st["junk.npy"].startswith("skipped")
    assert sorted(p.name for p in leg.iterdir()) == ["junk.npy"]
    c = Counter(None)                                    # the wiring's keys now hit
    assert np.array_equal(sc.cached("depth", 1, sc.depth_parts(h), c), _depth())
    assert np.array_equal(sc.cached("instances", 3, sc.instances_parts(h, 448, 12), c),
                          _instances())
    assert c.n == 0


# --------------------------------------------------------------------------- housekeeping


def test_prune_by_version_age_and_size():
    for v in (1, 2):
        for k in range(3):
            sc.cached("pose", v, (k,), Counter({"k": k}), kind="pickle")
    sc.cached("depth", 1, ("p",), Counter(_depth()))
    assert len(sc.prune(stage="pose", keep_version="2", dry_run=True)) == 3
    assert len(sc.entries()) == 7                                       # dry run kept them
    assert {e.version for e in sc.prune(stage="pose", keep_version="2")} == {"1"}
    assert {(e.stage, e.version) for e in sc.entries()} == {("pose", "2"), ("depth", "1")}
    old = sc.entries("pose")[0].path
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    assert [e.path for e in sc.prune(older_than_days=30)] == [old]
    assert sc.prune() == []                                              # no filter: no-op
    total = sum(e.size for e in sc.entries())
    gone = sc.prune(max_total_mb=(total - 1) / 1e6)
    assert len(gone) == 1 and sum(e.size for e in sc.entries()) <= total - 1


def test_cli_list_prune_migrate(capsys, tmp_path):
    sc.cached("depth", 1, ("p",), Counter(_depth()))
    assert sc.main(["list", "--entries"]) == 0
    out = capsys.readouterr().out
    assert "depth" in out and "v1" in out
    assert sc.main(["prune"]) == 2
    assert sc.main(["prune", "--stage", "depth", "--version", "1"]) == 0
    assert "removed 1 entries" in capsys.readouterr().out
    assert sc.entries() == []
    leg = tmp_path / "legacy"
    leg.mkdir()
    np.save(leg / "0123456789abcdef0123_depth_v1.npy", _depth())
    assert sc.main(["migrate", "--src", str(leg)]) == 0
    assert "converted" in capsys.readouterr().out
