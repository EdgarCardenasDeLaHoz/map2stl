"""The DEM store — v2's answer to v1's central architectural flaw.

In v1 the pipeline was split across two unrelated HTTP requests joined only by a disk
cache. The browser asked for a DEM; the server hashed the bounding box and seven settings
into a cache key and wrote the array under it. Later the browser asked for an export,
sending the settings again, and the server rebuilt the same key from scratch and read the
array back. Nothing tied the two computations together except that both sides were meant
to agree on the field list, the defaults, and the coercions. They disagreed twice, and each
time the symptom was an export that failed with "Missing DEM data" long after the mistake
was made.

v2 removes the possibility. The server issues an opaque id when it fetches a DEM and keeps
the array under that id. Export takes the id. The client never derives a key, never resends
the settings that produced the array, and therefore cannot disagree with the server about
them. The settings still travel with the response so the UI can show what it got, but they
are descriptive, not load-bearing.

Arrays are held in memory up to a small limit and spill to .npy files, so a long-lived
session does not accumulate hundreds of megabytes of float64 grids while still letting a
user come back to a DEM fetched twenty minutes ago.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import DEM_MEMORY_LIMIT, DEM_STORE_DIR, DEM_TTL_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class DemEntry:
    """One fetched DEM and the description of how it was produced."""

    dem_id: str
    bbox: dict[str, float]
    settings: dict[str, Any]
    shape: tuple[int, int]
    min_elevation: float
    max_elevation: float
    mean_elevation: float
    created_at: float = field(default_factory=time.time)
    fetch_seconds: float = 0.0
    source_used: str = ""
    # Populated when the array is resident; None once it has spilled to disk.
    _array: np.ndarray | None = None

    @property
    def path(self):
        return DEM_STORE_DIR / f"{self.dem_id}.npy"

    def describe(self) -> dict[str, Any]:
        """The JSON the client sees. Deliberately excludes the array itself."""
        return {
            "demId": self.dem_id,
            "bbox": self.bbox,
            "settings": self.settings,
            "height": self.shape[0],
            "width": self.shape[1],
            "minElevation": self.min_elevation,
            "maxElevation": self.max_elevation,
            "meanElevation": self.mean_elevation,
            "sourceUsed": self.source_used,
            "fetchSeconds": round(self.fetch_seconds, 3),
        }


class DemStore:
    """Thread-safe id-to-array store with an LRU resident set and a TTL sweep."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, DemEntry] = OrderedDict()
        DEM_STORE_DIR.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        array: np.ndarray,
        bbox: dict[str, float],
        settings: dict[str, Any],
        *,
        source_used: str = "",
        fetch_seconds: float = 0.0,
    ) -> DemEntry:
        array = np.asarray(array, dtype=np.float64)
        entry = DemEntry(
            dem_id=uuid.uuid4().hex[:16],
            bbox=dict(bbox),
            settings=dict(settings),
            shape=(int(array.shape[0]), int(array.shape[1])),
            min_elevation=float(np.nanmin(array)),
            max_elevation=float(np.nanmax(array)),
            mean_elevation=float(np.nanmean(array)),
            source_used=source_used,
            fetch_seconds=fetch_seconds,
        )
        entry._array = array
        with self._lock:
            self._entries[entry.dem_id] = entry
            self._entries.move_to_end(entry.dem_id)
            self._evict_locked()
            self._sweep_locked()
        logger.info(
            "DEM %s stored: %dx%d from %s in %.2fs",
            entry.dem_id, entry.shape[0], entry.shape[1], source_used, fetch_seconds,
        )
        return entry

    def get(self, dem_id: str) -> DemEntry | None:
        """Return the entry, or None if it never existed or has expired."""
        with self._lock:
            entry = self._entries.get(dem_id)
            if entry is None:
                return None
            if time.time() - entry.created_at > DEM_TTL_SECONDS:
                self._drop_locked(dem_id)
                return None
            self._entries.move_to_end(dem_id)
            return entry

    def array(self, dem_id: str) -> np.ndarray | None:
        """Return the array for an id, reloading it from disk if it has spilled."""
        entry = self.get(dem_id)
        if entry is None:
            return None
        with self._lock:
            if entry._array is not None:
                return entry._array
            if not entry.path.exists():
                # Spilled and then swept, or the disk write failed. Treat as gone rather
                # than as an empty array, so the caller reports a missing DEM honestly.
                logger.warning("DEM %s spilled but %s is absent", dem_id, entry.path.name)
                self._drop_locked(dem_id)
                return None
            entry._array = np.load(entry.path)
            self._evict_locked()
            return entry._array

    # -- internals ---------------------------------------------------------

    def _evict_locked(self) -> None:
        """Spill the least recently used arrays to disk until under the memory limit."""
        resident = [e for e in self._entries.values() if e._array is not None]
        for entry in resident[: max(0, len(resident) - DEM_MEMORY_LIMIT)]:
            try:
                np.save(entry.path, entry._array)
                entry._array = None
            except OSError as exc:
                # Keep it resident rather than lose it. Memory pressure is the lesser
                # failure; a vanished DEM breaks an export the user already started.
                logger.warning("Could not spill DEM %s: %s", entry.dem_id, exc)

    def _sweep_locked(self) -> None:
        cutoff = time.time() - DEM_TTL_SECONDS
        for dem_id in [k for k, e in self._entries.items() if e.created_at < cutoff]:
            self._drop_locked(dem_id)

    def _drop_locked(self, dem_id: str) -> None:
        entry = self._entries.pop(dem_id, None)
        if entry is None:
            return
        try:
            entry.path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover
            logger.warning("Could not remove %s: %s", entry.path.name, exc)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "count": len(self._entries),
                "resident": sum(1 for e in self._entries.values() if e._array is not None),
                "ids": list(self._entries),
            }


dem_store = DemStore()
