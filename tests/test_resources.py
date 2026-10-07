"""city2stl/resources.py: wait_for_gpu, ram_ok and wait_for_ram, with the GPU and RAM readings stubbed."""
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


def test_wait_for_ram_blocks_until_enough_is_free(monkeypatch):
    r = resources
    free = iter([2.0, 4.0, 7.5])
    monkeypatch.setattr(r, "free_ram_gb", lambda: next(free))
    slept = []
    r.wait_for_ram(6.0, poll_s=30.0, _sleep=slept.append)
    assert slept == [30.0, 30.0]
    monkeypatch.setattr(r, "free_ram_gb", lambda: 1.0)
    with pytest.raises(TimeoutError):
        r.wait_for_ram(6.0, poll_s=30.0, timeout_s=60.0, _sleep=lambda s: None)


def test_gpu_lock_is_exclusive_across_processes(monkeypatch, tmp_path):
    """A second process cannot take the GPU lock until the first releases it."""
    import subprocess
    import sys
    lock = tmp_path / "gpu.lock"
    monkeypatch.setattr(resources, "GPU_LOCK_PATH", lock)
    monkeypatch.setattr(resources, "_gpu_lock_fd", None)
    monkeypatch.delenv("MAP2STL_GPU_LOCK", raising=False)
    probe = ("import sys; from pathlib import Path; import city2stl.resources as r; "
             f"r.GPU_LOCK_PATH = Path(r'{lock}'); sys.exit(0 if r._try_gpu_lock() else 3)")

    def other_gets_it():
        return subprocess.run([sys.executable, "-c", probe], cwd=str(resources.Path(resources.__file__).parents[1])).returncode == 0

    assert resources._try_gpu_lock()
    assert not other_gets_it()
    resources.release_gpu_lock()
    assert other_gets_it()


def test_free_gpu_cache_releases_the_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(resources, "GPU_LOCK_PATH", tmp_path / "gpu.lock")
    monkeypatch.setattr(resources, "_gpu_lock_fd", None)
    monkeypatch.delenv("MAP2STL_GPU_LOCK", raising=False)
    assert resources._try_gpu_lock() and resources._gpu_lock_fd is not None
    resources.free_gpu_cache()
    assert resources._gpu_lock_fd is None
