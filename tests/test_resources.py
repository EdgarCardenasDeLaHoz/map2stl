"""city2stl/resources.py: wait_for_gpu and ram_ok, with the GPU and RAM readings stubbed."""
import pytest

from city2stl import resources


def test_ram_ok(monkeypatch):
    monkeypatch.setattr(resources, "free_ram_gb", lambda: 8.0)
    assert resources.ram_ok(6.0)
    monkeypatch.setattr(resources, "free_ram_gb", lambda: 4.0)
    assert not resources.ram_ok(6.0)


def test_wait_for_gpu_without_cuda(monkeypatch):
    monkeypatch.setattr(resources, "free_vram_gb", lambda device=0: None)
    assert resources.wait_for_gpu(2.0) is False


def test_wait_for_gpu_waits_until_free(monkeypatch):
    readings = iter([0.5, 1.0, 2.5])
    monkeypatch.setattr(resources, "free_vram_gb", lambda device=0: next(readings))
    monkeypatch.setattr(resources, "free_gpu_cache", lambda: None)
    sleeps = []
    assert resources.wait_for_gpu(2.0, poll_s=30, _sleep=sleeps.append) is True
    assert sleeps == [30, 30]


def test_wait_for_gpu_times_out(monkeypatch):
    monkeypatch.setattr(resources, "free_vram_gb", lambda device=0: 0.5)
    monkeypatch.setattr(resources, "free_gpu_cache", lambda: None)
    with pytest.raises(TimeoutError):
        resources.wait_for_gpu(2.0, poll_s=30, timeout_s=90, _sleep=lambda s: None)
