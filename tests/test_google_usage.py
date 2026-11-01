"""geo2stl.google_usage: counting, month rollover, cache hits, concurrency. No network."""

from __future__ import annotations

import threading

import pytest

from geo2stl import google_usage as gu


def test_record_and_counts():
    assert gu.month_counts()["streetview_image"] == {"count": 0, "cap": 10_000}
    assert gu.record("streetview_image", 3) == 3
    gu.record("tiles_root")
    c = gu.month_counts()
    assert c["streetview_image"]["count"] == 3
    assert c["tiles_root"] == {"count": 1, "cap": 1_000}


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        gu.record("nope")


def test_month_rollover():
    gu.record("streetview_image", 5, month="2026-09")
    gu.record("streetview_image", 2, month="2026-10")
    assert gu.month_counts("2026-09")["streetview_image"]["count"] == 5
    assert gu.month_counts("2026-10")["streetview_image"]["count"] == 2
    assert gu.month_counts("2026-11")["streetview_image"]["count"] == 0


def test_concurrent_writers():
    def work():
        for _ in range(20):
            gu.record("streetview_image")

    ts = [threading.Thread(target=work) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert gu.month_counts()["streetview_image"]["count"] == 40


def _fake_get(monkeypatch, status=200):
    from city2stl.skyline import streetview_io as sv

    calls = []

    class R:
        status_code = status
        content = b"img"

    def get(url, timeout=0):
        calls.append(url)
        return R()

    monkeypatch.setattr(sv.requests, "get", get)
    monkeypatch.setattr(sv, "_sign_streetview_url", lambda u: u)
    return sv, calls


def test_send_point_counts_each_request(monkeypatch):
    sv, calls = _fake_get(monkeypatch)
    assert sv._streetview_get("http://x/a") == (b"img", False)
    assert len(calls) == 1
    assert gu.month_counts()["streetview_image"]["count"] == 1


def test_retry_counts_each_attempt(monkeypatch):
    sv, calls = _fake_get(monkeypatch, status=503)
    monkeypatch.setattr(sv.time, "sleep", lambda s: None)
    sv._streetview_get("http://x/a")
    assert gu.month_counts()["streetview_image"]["count"] == len(calls) == 3


def test_cache_only_sends_nothing(monkeypatch):
    sv, calls = _fake_get(monkeypatch)
    with gu.cache_only():
        assert sv._streetview_get("http://x/a") == (None, False)
    assert calls == []
    assert gu.month_counts()["streetview_image"]["count"] == 0
    assert gu.blocked_counts(reset=True) == {"streetview_image": 1}
    assert gu.paid_allowed()


def test_real_cache_hit_not_counted(monkeypatch, tmp_path):
    import cv2
    import numpy as np

    sv, calls = _fake_get(monkeypatch)
    monkeypatch.setattr(sv, "_sv_image_cache_dir", lambda: tmp_path / "img")
    monkeypatch.setattr(sv, "_negative_cache_path", lambda k: tmp_path / f"{k}.none")
    noise = np.random.default_rng(1).integers(0, 255, (64, 64, 3), dtype=np.uint8)
    png = cv2.imencode(".png", noise)[1].tobytes()
    monkeypatch.setattr(sv, "_streetview_get", lambda url: (calls.append(url), gu.record("streetview_image"), (png, False))[2])
    args = ("k", 1.0, 2.0, 90.0)
    assert sv._streetview_image(*args, pano_id="abc", width=64, height=64) is not None
    assert gu.month_counts()["streetview_image"]["count"] == 1
    assert sv._streetview_image(*args, pano_id="abc", width=64, height=64) is not None
    assert len(calls) == 1
    assert gu.month_counts()["streetview_image"]["count"] == 1
    assert sv.image_is_cached({"heading": 90.0, "pitch": 0.0, "fov": 80.0, "size": "64x64", "key": "k", "pano": "abc"})


def test_cache_only_is_per_job_and_reaches_pool_workers():
    """Two jobs at once: the cache-only one is blocked (also in its pool workers), the paid one is
    not, and neither sees the other's skipped counts."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    both_in = threading.Barrier(2, timeout=5)
    out = {}

    def probe(_):
        return gu.paid_allowed()

    def job(name, cache_only):
        def body():
            both_in.wait()                       # both scopes are open at the same moment
            with ThreadPoolExecutor(2) as pool:
                out[name] = list(pool.map(gu.bind(probe), range(4)))
                out[name + "_unbound"] = list(pool.map(probe, range(2)))
            if not gu.paid_allowed():
                gu.note_blocked("streetview_image", 3)
        if cache_only:
            with gu.cache_only() as skipped:
                body()
            out[name + "_skipped"] = dict(skipped)
        else:
            body()
            out[name + "_skipped"] = {}

    ts = [threading.Thread(target=job, args=("run", True)),
          threading.Thread(target=job, args=("bench", False))]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert out["run"] == [False] * 4 and out["bench"] == [True] * 4
    assert out["run_skipped"] == {"streetview_image": 3} and out["bench_skipped"] == {}
    assert out["bench_unbound"] == [True, True]
    assert gu.paid_allowed()                     # the main thread was never affected
