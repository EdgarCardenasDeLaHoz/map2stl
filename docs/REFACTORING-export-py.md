# Refactoring Plan: export.py Module Split

**Status**: Optional refactoring. Current code is functional; this improves maintainability.  
**Priority**: Low (do only if adding more async export features)

---

## Motivation

`app/server/core/export.py` (879 lines) mixes three concerns:

1. **Task Lifecycle Management** (70 lines)
   - `ExportTask` dataclass
   - `_export_tasks` dict + locking
   - `get_task_status()`, `get_task_file()`, `start_export_task()`, `_cleanup_stale_tasks()`

2. **Export Parameter Parsing & Caching** (150 lines)
   - `ExportContext` dataclass + `.from_request()`
   - `resolve_dem_from_cache()`
   - `_parse_export_params()`

3. **Export Pipeline Logic** (600+ lines)
   - DEM preparation, label engraving, contours
   - STL/OBJ/3MF generation
   - Mesh repair and export
   - Puzzle export with alignment features
   - Cross-section generation

**Problem**: 
- Task tracking logic is tangled with export business logic
- Hard to unit-test task management independently
- Routing layer (`app/server/routers/export.py`) imports everything together

**Benefit of Split**:
- Each module has a single responsibility
- Task tracking is decoupled from export generation
- Easier to test and reason about
- Clearer imports in `routers/export.py`

---

## Proposed Structure

### Current Structure (Single File)

```
app/server/core/export.py (879 lines)
├── ExportTask (dataclass)
├── Task tracking functions
├── Export context & parameters
└── Export pipeline & generators
```

### Proposed Structure (Split)

```
app/server/core/
├── export.py (600 lines — generators + business logic)
│   ├── ExportContext, resolve_dem_from_cache
│   ├── _prepare_dem_array, _numpy2stl_mesh, _repair_and_export
│   ├── _apply_label_engraving, _apply_contour_lines
│   ├── generate_stl, generate_obj, generate_3mf, generate_mesh_preview
│   ├── generate_puzzle_3mf, _add_alignment_features
│   └── generate_crosssection
│
├── export_tasks.py (120 lines — async task tracking)
│   ├── ExportTask (dataclass)
│   ├── _export_tasks dict + lock
│   ├── _cleanup_stale_tasks()
│   ├── get_task_status(task_id)
│   ├── get_task_file(task_id)
│   ├── start_export_task(data, fmt)
│   └── _run_export_pipeline() [delegated to export.py]
│
└── export_params.py (100 lines — parameter handling)
    ├── ExportContext (dataclass)
    ├── ExportContext.from_request()
    ├── resolve_dem_from_cache()
    └── _parse_export_params()
```

### Backward Compatibility

Public API remains in `export.py`:

```python
# app/server/core/export.py

# Import and re-export from export_tasks.py
from app.server.core.export_tasks import (
    ExportTask, get_task_status, get_task_file, start_export_task
)

# Import and re-export from export_params.py
from app.server.core.export_params import (
    ExportContext, resolve_dem_from_cache
)

# Keep all public generators here
def generate_stl(data: dict): ...
def generate_obj(data: dict): ...
def generate_3mf(data: dict): ...
def generate_puzzle_3mf(data: dict, task: ExportTask | None = None): ...
def generate_crosssection(data: dict): ...
def generate_mesh_preview(data: dict): ...
```

**Consumers** (e.g., `app/server/routers/export.py`) don't need to change:

```python
# No changes needed in routers/export.py
from app.server.core.export import (
    get_task_status, get_task_file, start_export_task,
    generate_stl, generate_obj, generate_3mf, ...
)
```

---

## Implementation Steps

### Phase 1: Create export_tasks.py

**File**: `app/server/core/export_tasks.py`

```python
"""
core/export_tasks.py — Async export task tracking and lifecycle.

Manages the state of long-running export jobs (STL/OBJ/3MF generation).
Tracks progress, stores results in temp files, handles cleanup.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from starlette.background import BackgroundTask

logger = logging.getLogger(__name__)


@dataclass
class ExportTask:
    """Tracks the state of an async export job."""
    task_id: str
    status: str = "running"          # running | complete | error
    progress: int = 0                # 0-100
    message: str = "Starting..."
    result_path: Optional[str] = None
    filename: Optional[str] = None
    media_type: str = "application/octet-stream"
    headers: Dict[str, str] = field(default_factory=dict)
    created: float = field(default_factory=time.time)

    def update(self, progress: int, message: str) -> None:
        self.progress = progress
        self.message = message

    def complete(self, result_path: str, filename: str, headers: dict = None) -> None:
        self.status = "complete"
        self.progress = 100
        self.message = "Complete"
        self.result_path = result_path
        self.filename = filename
        if headers:
            self.headers = headers

    def fail(self, message: str) -> None:
        self.status = "error"
        self.message = message


_export_tasks: Dict[str, ExportTask] = {}
_export_tasks_lock = threading.Lock()
_TASK_TTL = 300  # seconds before stale tasks are cleaned up


def _cleanup_stale_tasks() -> None:
    """Remove tasks older than _TASK_TTL seconds."""
    cutoff = time.time() - _TASK_TTL
    with _export_tasks_lock:
        stale = [tid for tid, t in _export_tasks.items() if t.created < cutoff]
    for tid in stale:
        with _export_tasks_lock:
            task = _export_tasks.pop(tid, None)
        if task and task.result_path and os.path.exists(task.result_path):
            try:
                os.unlink(task.result_path)
            except OSError:
                pass


def get_task_status(task_id: str) -> Optional[dict]:
    """Return progress info for a task, or None if not found."""
    with _export_tasks_lock:
        task = _export_tasks.get(task_id)
    if task is None:
        return None
    return {
        "task_id": task.task_id,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
    }


def get_task_file(task_id: str):
    """Return a FileResponse for a completed task, or None."""
    from fastapi.responses import FileResponse
    with _export_tasks_lock:
        task = _export_tasks.get(task_id)
    if not task or task.status != "complete" or not task.result_path:
        return None

    def _cleanup():
        try:
            os.unlink(task.result_path)
        except OSError:
            pass
        with _export_tasks_lock:
            _export_tasks.pop(task_id, None)

    return FileResponse(
        task.result_path,
        filename=task.filename,
        media_type=task.media_type,
        background=BackgroundTask(_cleanup),
        headers=task.headers,
    )


def start_export_task(data: dict, fmt: str) -> str:
    """Start an export in a background thread. Returns task_id."""
    from app.server.core.export import _run_export_pipeline, generate_puzzle_3mf
    
    _cleanup_stale_tasks()

    task_id = uuid.uuid4().hex[:12]
    task = ExportTask(task_id=task_id)
    with _export_tasks_lock:
        _export_tasks[task_id] = task

    def _run():
        try:
            if fmt == "puzzle":
                generate_puzzle_3mf(data, task=task)
            else:
                _run_export_pipeline(data, fmt, task)
        except Exception as exc:
            logger.exception("Export task %s failed", task_id)
            task.fail(str(exc))

    thread = threading.Thread(target=_run, daemon=True, name=f"export-{task_id}")
    thread.start()
    return task_id
```

### Phase 2: Create export_params.py

**File**: `app/server/core/export_params.py`

```python
"""
core/export_params.py — Export parameter parsing and DEM cache resolution.

Extracts, validates, and type-casts export parameters from client requests.
Supports both inline DEM arrays and cache-based DEM resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


def resolve_dem_from_cache(data: dict) -> tuple[list, int, int] | None:
    """Look up a cached DEM from bbox + DEM settings.

    The DEM endpoint caches processed arrays under a key derived from
    bbox + {dim, src, proj, ds, ws, sw, md, cn, sat}.  If the caller
    provides these settings instead of raw ``dem_values``, we can
    reconstruct the key and read from disk — eliminating the need to
    retransmit the (potentially multi-MB) array.

    Returns ``(dem_values_list, height, width)`` or ``None`` on cache miss.
    """
    from app.server.core.cache import make_cache_key, read_array_cache

    bbox = data.get("bbox") or data
    north = bbox.get("north")
    south = bbox.get("south")
    east  = bbox.get("east")
    west  = bbox.get("west")
    if None in (north, south, east, west):
        return None

    # DEM settings — match the key structure in terrain.py get_terrain_dem()
    dem = data.get("dem") or data
    dim         = int(dem.get("dim", 200))
    dem_source  = dem.get("dem_source", "local")
    projection  = dem.get("projection", "cosine")
    depth_scale = float(dem.get("depth_scale", 0.5))
    water_scale = float(dem.get("water_scale", 0.05))
    subtract_water     = bool(dem.get("subtract_water", True))
    maintain_dimensions = bool(dem.get("maintain_dimensions", True))
    clip_nans   = bool(dem.get("clip_nans", False))
    show_sat    = bool(dem.get("show_sat", False))

    cache_key = make_cache_key("dem", north, south, east, west, {
        "dim": dim, "src": dem_source, "proj": projection,
        "ds": depth_scale, "ws": water_scale,
        "sw": subtract_water, "md": maintain_dimensions,
        "cn": clip_nans, "sat": show_sat,
    })

    cached = read_array_cache("dem", cache_key)
    if cached is None or cached[0].get("dem") is None:
        logger.debug("DEM cache miss for export (key %s)", cache_key[:8])
        return None

    dem_arr = cached[0]["dem"]  # np.ndarray (H, W)
    h, w = dem_arr.shape
    logger.info("DEM resolved from cache for export (key %s, %dx%d)", cache_key[:8], w, h)
    return dem_arr.ravel().tolist(), h, w


@dataclass
class ExportContext:
    """Typed container for parsed export parameters.

    Replaces the raw dict returned by _parse_export_params, giving IDE
    autocompletion and catching typos at attribute-access time.
    """
    dem_values: List[float]
    height: int
    width: int
    model_height: float = 20.0
    base_height: float = 5.0
    exaggeration: float = 1.0
    sea_level_cap: bool = False
    name: str = "terrain"

    @classmethod
    def from_request(cls, data: dict) -> "ExportContext":
        """Construct from an incoming request dict.

        Supports two modes:
        - **Legacy (array):** ``dem_values``, ``height``, ``width`` in the dict.
        - **Settings-only:** ``bbox`` + ``dem`` settings — DEM is read from
          the server-side disk cache (populated when the user loaded the DEM
          in the browser).  This avoids retransmitting multi-MB arrays.
        """
        dem_values = data.get("dem_values", [])
        height = data.get("height", 0)
        width = data.get("width", 0)

        # Settings-only mode: resolve DEM from cache
        if not dem_values:
            resolved = resolve_dem_from_cache(data)
            if resolved is not None:
                dem_values, height, width = resolved

        return cls(
            dem_values=dem_values,
            height=height,
            width=width,
            model_height=float(data.get("model_height", 20)),
            base_height=float(data.get("base_height", 5)),
            exaggeration=float(data.get("exaggeration", 1.0)),
            sea_level_cap=bool(data.get("sea_level_cap", False)),
            name=data.get("name", "terrain"),
        )


def _parse_export_params(data: dict) -> ExportContext:
    """Extract and type-cast the common export parameters from a request dict."""
    return ExportContext.from_request(data)
```

### Phase 3: Update export.py

Remove the moved code and add re-exports:

```python
# At the top of app/server/core/export.py, after imports:

# Re-export public task management API from export_tasks
from app.server.core.export_tasks import (
    ExportTask, get_task_status, get_task_file, start_export_task
)

# Re-export public parameter handling from export_params
from app.server.core.export_params import (
    ExportContext, resolve_dem_from_cache
)

# Keep all the existing generator functions in this file
# Remove: ExportTask dataclass, _export_tasks dict, task management functions
# Remove: ExportContext class, resolve_dem_from_cache, _parse_export_params
```

Then delete the moved code from export.py.

### Phase 4: Update routers/export.py

No changes needed! Imports remain the same:

```python
from app.server.core.export import (
    get_task_status, get_task_file, start_export_task,
    generate_stl, generate_obj, generate_3mf, ...
)
```

### Phase 5: Run Tests

```bash
cd strm2stl
python -m pytest tests/ -v
# Should pass all 532 tests
```

---

## Risks & Mitigation

| Risk | Mitigation |
|------|-----------|
| Circular imports (export.py → export_tasks.py → export.py) | Use lazy imports in `export_tasks.py` (done above: imports inside `start_export_task()`) |
| Broken public API | Re-export from export.py; no changes to routers |
| Test failures | Run full test suite; circular import protection catches issues |

---

## Timeline

- **Phase 1-3**: ~1 hour (straightforward file splitting)
- **Phase 4**: 0 hours (no changes needed)
- **Phase 5**: 10 minutes (test run)

**Total**: ~1.5 hours

---

## Approval

**Status**: Pending user decision. Current code is functional. Refactor only if:
- [ ] You plan to add more async export features
- [ ] You want to simplify task management (e.g., add webhooks, persistent storage)
- [ ] You're doing a broad refactoring sprint

**Decision**:
- [ ] Approve & implement now
- [ ] Mark as approved for later
- [ ] Skip (keep current structure)

---

**Document created**: 2026-05-09  
**Author**: Claude Haiku 4.5  
**Complexity**: Low (mechanical split, no logic changes)
