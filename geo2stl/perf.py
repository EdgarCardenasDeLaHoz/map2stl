"""Timing log for expensive steps: one ``perf {json}`` log line per step.

Each line carries the step name, the inputs that decide its result (``key``), the
seconds it took and the thread, so a log can be checked for the same step running
twice for one key - the duplicate work that made large regions slow (Colombia,
2026-10-03: the composite and the rivers preview each built the same river carve).
``claude/scripts/perf_audit.py`` reads these lines.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager

import numpy as np

logger = logging.getLogger("perf")


def _plain(v):
    if isinstance(v, float):
        return round(v, 5)
    if isinstance(v, np.ndarray):
        return f"array{list(v.shape)}"
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in sorted(v.items())}
    if v is None or isinstance(v, (int, str, bool)):
        return v
    return str(v)


@contextmanager
def perf_step(step: str, **key):
    """Time the block and log ``perf {"step", "key", "s", "thread", "t"}``.

    Yields a dict; set ``info["cache"] = "hit"`` (or anything else) inside the
    block to tag the line, e.g. when the step was answered from a cache.
    """
    info: dict = {}
    t0 = time.perf_counter()
    try:
        yield info
    finally:
        logger.info("perf %s", json.dumps({
            "step": step, "key": _plain(key), "s": round(time.perf_counter() - t0, 3),
            "thread": threading.get_ident(), "t": round(time.time(), 3), **info}))
