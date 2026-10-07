"""Shared machine resources: wait for GPU memory, check free RAM before a heavy job.

The rules (CLAUDE.md › "Shared machine resources"): GPU jobs wait for free VRAM instead of
falling back to CPU because the GPU looks busy, and a heavy job starts only with enough free
RAM. Several agents share one laptop (6 cores, 32 GB RAM, 4 GB VRAM).

    from city2stl.resources import wait_for_gpu, wait_for_ram, free_gpu_cache
    wait_for_ram()             # 6 GB free before a heavy job (ram_ok() to check without waiting)
    wait_for_gpu(2.0)          # SegFormer b3, batch 4
    ...
    free_gpu_cache()           # between photos / models

``wait_for_gpu`` also takes a machine-wide GPU lock (a file under ``~/.cache``) that
``free_gpu_cache`` releases, so only one process runs a GPU model at a time: checking free memory
alone let two jobs that started together both load and spill into shared system RAM
(2026-10-07). The OS drops the lock when its process dies. ``MAP2STL_GPU_LOCK=off`` disables it.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: Free RAM a heavy job (region PDF, photo profiles, river carve, training) needs to start.
HEAVY_JOB_MIN_FREE_GB: float = 6.0
#: How often wait_for_gpu checks, and how long it waits before giving up.
GPU_POLL_S: float = 30.0
GPU_TIMEOUT_S: float = 3600.0


def free_ram_gb() -> float:
    """Available RAM in GB (what a new process can use without swapping)."""
    import psutil
    return psutil.virtual_memory().available / 1e9


def ram_ok(min_free_gb: float = HEAVY_JOB_MIN_FREE_GB) -> bool:
    """True when at least *min_free_gb* of RAM is available; logs a warning otherwise."""
    free = free_ram_gb()
    if free < min_free_gb:
        logger.warning("Only %.1f GB RAM free (need %.1f GB) for a heavy job", free, min_free_gb)
        return False
    return True


def wait_for_ram(min_free_gb: float = HEAVY_JOB_MIN_FREE_GB, *, poll_s: float = GPU_POLL_S,
                 timeout_s: float = 3 * GPU_TIMEOUT_S, _sleep=time.sleep) -> None:
    """Block until *min_free_gb* of RAM is free (a heavy job's start rule); ``TimeoutError``
    after *timeout_s*."""
    waited = 0.0
    while (free := free_ram_gb()) < min_free_gb:
        if waited >= timeout_s:
            raise TimeoutError(f"RAM: {free:.1f} GB free after {waited:.0f} s, need {min_free_gb:.1f} GB")
        if waited == 0:
            logger.info("Waiting for %.1f GB of free RAM (%.1f GB free)", min_free_gb, free)
        _sleep(poll_s)
        waited += poll_s


def free_vram_gb(device: int = 0) -> float | None:
    """Free GPU memory in GB, or None without CUDA."""
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    free, _total = torch.cuda.mem_get_info(device)
    return free / 1e9


#: Lock file shared by every process on the machine (home, not %TEMP%: the desktop app's
#: sandbox redirects its temp folder, so its processes and the user's would not see one file).
GPU_LOCK_PATH = Path.home() / ".cache" / "map2stl_gpu.lock"
_gpu_lock_fd: int | None = None


def _try_gpu_lock() -> bool:
    """Take the machine-wide GPU lock without blocking; True when this process holds it."""
    global _gpu_lock_fd
    if _gpu_lock_fd is not None or os.environ.get("MAP2STL_GPU_LOCK", "").lower() == "off":
        return True
    GPU_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(GPU_LOCK_PATH, os.O_RDWR | os.O_CREAT)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _gpu_lock_fd = fd
    return True


def release_gpu_lock() -> None:
    """Let the next process have the GPU (called by :func:`free_gpu_cache`)."""
    global _gpu_lock_fd
    if _gpu_lock_fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(_gpu_lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(_gpu_lock_fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    os.close(_gpu_lock_fd)
    _gpu_lock_fd = None


def wait_for_gpu(need_gb: float, *, device: int = 0, poll_s: float = GPU_POLL_S,
                 timeout_s: float = GPU_TIMEOUT_S, _sleep=time.sleep) -> bool:
    """Block until this process holds the GPU lock and *need_gb* of GPU memory is free; True
    when it is, False without CUDA.

    Raises ``TimeoutError`` after *timeout_s*. Never falls back to the CPU: the caller asked
    for the GPU, and a CPU run of a GPU model takes the CPU from every other job.
    """
    free = free_vram_gb(device)
    if free is None:
        return False
    waited = 0.0
    while not _try_gpu_lock():
        if waited >= timeout_s:
            raise TimeoutError(f"GPU lock still held by another process after {waited:.0f} s")
        if waited == 0:
            logger.info("Waiting for the GPU lock (%s)", GPU_LOCK_PATH)
        _sleep(poll_s)
        waited += poll_s
    while free < need_gb:
        if waited >= timeout_s:
            raise TimeoutError(f"GPU memory: {free:.1f} GB free after {waited:.0f} s, "
                               f"need {need_gb:.1f} GB")
        if waited == 0:
            logger.info("Waiting for %.1f GB of GPU memory (%.1f GB free)", need_gb, free)
        _sleep(poll_s)
        waited += poll_s
        _empty_cache()
        free = free_vram_gb(device)
    return True


def free_gpu_cache() -> None:
    """Release this process's cached GPU memory and the GPU lock (call between photos and
    between models)."""
    _empty_cache()
    release_gpu_lock()


def _empty_cache() -> None:
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
