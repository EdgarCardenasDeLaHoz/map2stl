"""DEM handles: an opaque id for the exact grid /api/terrain/dem returned.

Export used to find the DEM by re-deriving its disk-cache key from settings the
client sent a second time. Any disagreement about a field, a default or a
coercion made the lookup miss, and the user got "Missing DEM data" long after
the mistake (it happened twice). With a handle the server keeps the grid the
user is looking at, after projection and clipping, and export asks for it by
id: nothing is re-derived, so nothing can disagree.

Grids stay in memory up to MEMORY_LIMIT, then spill to .npy under the cache
root; handles expire after TTL_SECONDS. Handles do not survive a restart; an
unknown id raises DemGone, which the server maps to 410 "reload the DEM".
See docs/plans/active/F-FE1-vue-consolidation.md.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from app.server.core.cache import CACHE_ROOT

logger = logging.getLogger(__name__)

TTL_SECONDS = 60 * 60
MEMORY_LIMIT = 8
STORE_DIR = CACHE_ROOT / "dem_handles"


class DemGone(LookupError):
    """A dem_id that never existed, expired, or was lost to a server restart."""

    def __init__(self, dem_id: str):
        super().__init__(f"DEM {dem_id} is no longer available; reload the DEM and try again")
        self.dem_id = dem_id


@dataclass
class _Entry:
    dem_id: str
    bbox: dict
    created_at: float = field(default_factory=time.time)
    array: np.ndarray | None = None   # None once spilled to disk

    @property
    def path(self):
        return STORE_DIR / f"{self.dem_id}.npy"


class DemStore:
    """Thread-safe id -> grid store: LRU resident set, disk spill, TTL sweep."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    def put(self, grid: np.ndarray, bbox: dict) -> str:
        entry = _Entry(uuid.uuid4().hex[:16], dict(bbox), array=np.asarray(grid, dtype=np.float32))
        with self._lock:
            self._entries[entry.dem_id] = entry
            self._sweep_locked()
            self._evict_locked()
        return entry.dem_id

    def get(self, dem_id: str) -> tuple[np.ndarray, dict]:
        """Return (grid, bbox) for a handle, reloading a spilled grid. Raises DemGone."""
        with self._lock:
            entry = self._entries.get(dem_id)
            if entry is None or time.time() - entry.created_at > TTL_SECONDS:
                self._drop_locked(dem_id)
                raise DemGone(dem_id)
            self._entries.move_to_end(dem_id)
            if entry.array is None:
                try:
                    entry.array = np.load(entry.path)
                except OSError:
                    logger.warning("DEM handle %s spilled but %s is unreadable", dem_id, entry.path)
                    self._drop_locked(dem_id)
                    raise DemGone(dem_id) from None
                self._evict_locked()
            return entry.array, entry.bbox

    def _evict_locked(self) -> None:
        resident = [e for e in self._entries.values() if e.array is not None]
        for entry in resident[: max(0, len(resident) - MEMORY_LIMIT)]:
            try:
                STORE_DIR.mkdir(parents=True, exist_ok=True)
                np.save(entry.path, entry.array)
                entry.array = None
            except OSError as exc:
                # Keeping it resident beats losing a DEM an export may be about to use.
                logger.warning("Could not spill DEM handle %s: %s", entry.dem_id, exc)

    def _sweep_locked(self) -> None:
        cutoff = time.time() - TTL_SECONDS
        for dem_id in [k for k, e in self._entries.items() if e.created_at < cutoff]:
            self._drop_locked(dem_id)

    def _drop_locked(self, dem_id: str) -> None:
        entry = self._entries.pop(dem_id, None)
        if entry is not None:
            entry.path.unlink(missing_ok=True)


dem_store = DemStore()
