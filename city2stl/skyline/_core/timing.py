"""skyline._core.timing -- step timer shared by the pipeline and the reports.

Lives in ``_core`` so pipeline code (``_pano/detect.py``) need not import the
report module to time its steps.
"""
from __future__ import annotations

import time
from contextlib import contextmanager


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
        print(f"[timing]{'  ' * level} {label}: +{dt:.2f}s")

    @contextmanager
    def timed(self, label: str, level: int = 0):
        # Register order on *enter* so parents precede their children in the
        # rendered table; finalise the duration on *exit*.
        key = self._register(label, level)
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            self._totals[key] += dt
            print(f"[timing]{'  ' * level} {label}: +{dt:.2f}s")

    @property
    def rows(self) -> list[tuple[str, float, int]]:
        return [(lbl, self._totals[(lbl, lvl)], lvl) for (lbl, lvl) in self._order]


__all__ = ["_StepTimer"]
