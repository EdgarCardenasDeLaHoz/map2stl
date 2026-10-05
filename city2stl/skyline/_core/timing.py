"""skyline._core.timing -- step timer shared by the pipeline and the reports.

Lives in ``_core`` so pipeline code (``_pano/detect.py``) need not import the
report module to time its steps.
"""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager


def _rss_gb() -> float | None:
    """Resident memory of this process in GiB (None without psutil). Printed with every step
    so a run's log shows which step holds memory (Miami reached 8 GB, 2026-10-05)."""
    try:
        import psutil

        return psutil.Process().memory_info().rss / 2**30
    except Exception:
        return None


def _mem(m0: float | None) -> str:
    m1 = _rss_gb()
    if m1 is None:
        return ""
    out = f"  rss {m1:.2f} GB" + ("" if m0 is None else f" ({m1 - m0:+.2f})")
    torch = sys.modules.get("torch")                      # only when the run already loaded it
    try:
        if torch is not None and torch.cuda.is_available() and torch.cuda.is_initialized():
            out += f", gpu reserved {torch.cuda.memory_reserved() / 2**30:.2f} GB"
    except Exception:
        pass
    return out


class _StepTimer:
    """Accumulating wall-clock timer for pipeline steps.

    Each ``timed(label, level)`` block records its duration. Repeated calls
    with the same (label, level) **sum** — so a sub-step run once per seed
    reports the total across all seeds rather than N separate rows. ``level``
    drives indentation in the HTML timing table (0 = top-level step, 1 =
    sub-step). First-seen order is preserved.
    """

    def __init__(self) -> None:
        self._order: list[tuple[str, int]] = []
        self._totals: dict[tuple[str, int], float] = {}

    def _register(self, label: str, level: int) -> tuple[str, int]:
        """Reserve the row's position in render order. Called at *enter* so a
        parent step is listed above its children, even though the parent's
        duration is finalised after the children's records arrive."""
        key = (label, level)
        if key not in self._totals:
            self._order.append(key)
            self._totals[key] = 0.0
        return key

    def record(self, label: str, dt: float, level: int = 0) -> None:
        """Add a pre-measured duration (for blocks that can't use ``timed``,
        e.g. a large try/except where wrapping would force a re-indent)."""
        key = self._register(label, level)
        self._totals[key] += dt
        print(f"[timing]{'  ' * level} {label}: +{dt:.2f}s{_mem(None)}")

    @contextmanager
    def timed(self, label: str, level: int = 0):
        # Register order on *enter* so parents precede their children in the
        # rendered table; finalise the duration on *exit*.
        key = self._register(label, level)
        t0, m0 = time.perf_counter(), _rss_gb()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            self._totals[key] += dt
            print(f"[timing]{'  ' * level} {label}: +{dt:.2f}s{_mem(m0)}")

    @property
    def rows(self) -> list[tuple[str, float, int]]:
        return [(lbl, self._totals[(lbl, lvl)], lvl) for (lbl, lvl) in self._order]


__all__ = ["_StepTimer"]
