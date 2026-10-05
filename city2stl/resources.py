"""Shared machine resources: wait for GPU memory, check free RAM before a heavy job.

The rules (CLAUDE.md › "Shared machine resources"): GPU jobs wait for free VRAM instead of
falling back to CPU because the GPU looks busy, and a heavy job starts only with enough free
RAM. Several agents share one laptop (6 cores, 32 GB RAM, 4 GB VRAM).

    from city2stl.resources import wait_for_gpu, wait_for_ram, free_gpu_cache
    wait_for_ram()             # 6 GB free before a heavy job (ram_ok() to check without waiting)
    wait_for_gpu(2.0)          # SegFormer b3, batch 4
    ...
    free_gpu_cache()           # between photos / models
"""
from __future__ import annotations

import logging
import time

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


def wait_for_gpu(need_gb: float, *, device: int = 0, poll_s: float = GPU_POLL_S,
                 timeout_s: float = GPU_TIMEOUT_S, _sleep=time.sleep) -> bool:
    """Block until *need_gb* of GPU memory is free; True when it is, False without CUDA.

    Raises ``TimeoutError`` after *timeout_s*. Never falls back to the CPU: the caller asked
    for the GPU, and a CPU run of a GPU model takes the CPU from every other job.
    """
    free = free_vram_gb(device)
    if free is None:
        return False
    waited = 0.0
    while free < need_gb:
        if waited >= timeout_s:
            raise TimeoutError(f"GPU memory: {free:.1f} GB free after {waited:.0f} s, "
                               f"need {need_gb:.1f} GB")
        if waited == 0:
            logger.info("Waiting for %.1f GB of GPU memory (%.1f GB free)", need_gb, free)
        _sleep(poll_s)
        waited += poll_s
        free_gpu_cache()
        free = free_vram_gb(device)
    return True


def free_gpu_cache() -> None:
    """Release this process's cached GPU memory (call between photos and between models)."""
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
