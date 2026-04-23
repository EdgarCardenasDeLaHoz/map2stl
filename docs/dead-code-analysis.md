# Dead Code, Repetitive Workflows & Bloat Analysis

_Generated: 2026-05-08. Analysis scope: `app/server/`, `app/session/`, `app/client/static/js/`._  
_Updated: 2026-05-09. Items 1–8 in the priority table are **COMPLETED** and all 440 Python + 93 JS tests pass._

---

## Summary

| Category | Count | Risk |
|----------|-------|------|
| Dead / unreachable functions | 5 | Low–Medium |
| Unnecessary `window.*` exports | 3 | Low |
| Duplicate class definitions | 1 | Medium |
| Unused imports (Python) | ~25+ | Low |
| Repetitive boilerplate patterns | 4 types | Medium |
| Library-reimplementation debt | 5 items | High (see issues.md B-LIB) |
| Oversized files | 4 | Low–Medium |

---

## 1. Dead / Unreachable Code

### 1.1 `app/server/core/db.py` — `db_exists()` ✅ DONE

```python
def db_exists(path: Optional[Path] = None) -> bool:   # line 112
```

**Zero external callers** in any server or session file. Only defined, never called.  
**Action**: Remove. If needed for testing, cover via `Path.exists()` inline.

---

### 1.2 `app/server/core/responses.py` — `success_response()` ✅ DONE

```python
def success_response(data: dict, status: int = 200) -> JSONResponse:   # line 18
```

**Zero callers** in the codebase. Every router constructs `JSONResponse(...)` directly.  
`error_response()` in the same file is used widely; this one is not.  
**Action**: Remove.

---

### 1.3 `app/session/terrain_session.py` — `_decode_satellite_image()` / `_encode_satellite_image()` ✅ DONE

```python
def _decode_satellite_image(self, b64_string: str) -> np.ndarray:   # line 389
def _encode_satellite_image(self, arr: np.ndarray, quality: int = 85) -> str:  # line 394
```

Both methods are defined but **never called anywhere** (only their definition lines appear in a grep across the whole project, plus the sphinx doc build artefact).  
**Action**: Remove both. If satellite round-tripping is needed later, re-add at that point.

---

### 1.4 `app/server/core/height/providers/shadow_height.py` — private helpers

Three private functions appear to only be referenced from within their own file:

| Function | Context |
|---|---|
| `_detect_shadows()` | Implementation helper, only called inside `shadow_height.py` |
| `_estimate_sun_elevation()` | Same |
| `_shadow_length_to_height()` | Same |

These are normal helpers for `ShadowHeightProvider`, so they are _not_ dead — but `shadow_height` itself may merit review. `ShadowHeightProvider` **is** registered in `routers/height.py` (line 72) and is actively callable. No action needed here; listing for completeness.

---

### 1.5 `app/server/routers/composite.py` — ambiguously-named `CityRasterRequest` ✅ DONE

```python
# composite.py (was line 55)
class CityRasterRequest(BaseModel):   # renamed → CompositeCityRasterRequest
    north:  float
    south:  float
    east:   float
    west:   float
    width:  int = 512
    height: int = 512
    projection: str = "none"
    clip_nans: bool = True
```

**Correction from original analysis**: This is _not_ a duplicate of `schemas.CityRasterRequest`. They serve different endpoints with different fields:
- `composite.py` version: reads from OSM disk cache — fields `width`, `height`, `projection`, `clip_nans`
- `schemas.py` version: takes GeoJSON features directly — fields `dim`, `buildings`, `roads`, `waterways`, `building_scale`, `road_depression_m`, `water_depression_m`

**Actual action taken**: Renamed `composite.py`'s class to `CompositeCityRasterRequest` (not merged/removed) to eliminate the name ambiguity. All 3 references in the file updated via language-server rename.
    width:  int = 512
    height: int = 512
    projection: str = "none"
    clip_nans: bool = True
```

An **identical-purpose** `CityRasterRequest` is defined in `app/server/schemas.py` (line 159) and is already imported in `cities.py`. `composite.py` defines its own local copy without importing from `schemas.py`.  
~~**Action**: In `composite.py`, replace the local `class CityRasterRequest` with `from app.server.schemas import CityRasterRequest`.~~ _(See correction in updated section 1.5 above.)_
_Note_: Check field parity before removing — schemas version may have additional validators.

---

## 2. Unnecessary `window.*` Exports (JS)

These functions are assigned to the global `window` namespace but have **no external callers** — nothing outside the file that defines them ever calls them via `window.*`.

### 2.1 `window._reprojectSatelliteImage` — `dem-main.js` line 685 ✅ DONE (removed)

Defined as a `window.*` function, evidently intended to be called when the projection dropdown changes. However, `event-listeners-map.js` handles projection changes by calling `window.loadSatelliteRGBImage?.()` directly (a full re-fetch), bypassing this function entirely.  
**Action**: ~~Remove `window.*` prefix — make it a module-private function, or remove if it is never called.~~ Removed entirely.

### 2.2 `window._reprojectCityRaster` — `city-render.js` line 542 ✅ DONE (removed)

Same situation as above. Projection changes trigger a full `window.loadCityRaster?.()` re-fetch; this function is never invoked by any caller.  
**Action**: ~~Same as 2.1.~~ Removed entirely.

### 2.3 `window.getLayerOrder` — `stacked-layers.js` line 149 — KEPT

Exposed on `window` but no JS module, HTML template, or event handler references it. May have been added as a future-use API.  
**Decision**: Kept as a stable public API — it is a clean, single-responsibility accessor that any future UI consumer can use without modifying `stacked-layers.js`.

---

### 3 Unused Python Imports ✅ DONE

All high-priority unused imports listed below were removed manually (ruff binary unavailable in this environment).

### High-priority (meaningful symbols)

| File | Unused import |
|---|---|
| `server.py` | `_Response`, `Optional`, `List`, `Dict`, `Any` (all from `typing`) |
| `hydrology.py` | `math`, `List` |
| `hydrorivers.py` | `os` |
| `ndsm.py` | `re` |
| `open_buildings.py` | `io`, `requests` |
| `shadow_height.py` | `_resample` (imported from projection but never called) |
| `wsf3d.py` | `Path`, `from_bounds` |
| `cities.py` (router) | `asyncio`, `math`, `Path`, `MAX_BBOX_DIAGONAL_KM` |
| `composite.py` (router) | `asyncio`, `partial` |
| `terrain.py` (router) | `asyncio`, `partial` |
| `height.py` (router) | `run_sync` |

### Low-priority (`from __future__ import annotations`)

Present in: `config.py`, `schemas.py`, `cache.py`, `cities_3d.py`, `db.py`, `dem.py`, `export.py`, `hydrology.py`, `hydrorivers.py`, `osm.py`, `responses.py`, `sat.py`, `validation.py`, `__init__.py`, and all height providers.  
Python 3.11+ does not require this for `TYPE_CHECKING` usage. Safe to remove globally, but low value — a linter pass (`ruff --fix`) would handle this automatically.

**Action**: Run `ruff check --fix app/server/` — this clears all of the above in one pass without manual edits.

---

## 4. Repetitive Boilerplate Patterns

### 4.1 Sys-path injection in routers ✅ DONE

Two routers independently inject the project root into `sys.path`:

```python
# terrain.py line 60  AND  composite.py line 46
_STRM2STL_DIR = str(Path(__file__).parent.parent.parent.parent)
if _STRM2STL_DIR not in sys.path:
    sys.path.insert(0, _STRM2STL_DIR)
```

This is already handled at startup in `server.py`. The router-level copies are defensive but redundant.  
**Action**: ~~Remove the duplicated blocks from `terrain.py` and `composite.py`.~~ Removed. `sys` and `Path` (and `os`) imports also removed from both routers as they became unused.

---

### 4.2 Repeated `(north, south, east, west)` GET query parameters

11 Python files each declare the same four float query parameters independently. No shared Pydantic model is used for query params (FastAPI does not support Pydantic models for `GET` query params directly, but a `Depends()` helper could extract and validate them once).

**Current pattern (repeated 6× in terrain.py, plus in cities, composite, etc.):**

```python
north: float = Query(..., description="Northern latitude"),
south: float = Query(..., description="Southern latitude"),
east:  float = Query(..., description="Eastern longitude"),
west:  float = Query(..., description="Western longitude"),
```

Plus in `dem.py`, `sat.py`, and multiple `height/providers/` files:

```python
make_cache_key(_NAMESPACE, north, south, east, west, {...})
```

**Action**: Create a `BboxParams` FastAPI `Depends` class in `core/validation.py` that handles the four bbox params plus `_validate_bbox()` call in one place. Reduces each endpoint's parameter list by 4 lines and centralizes bbox validation. See [proposals.md](proposals.md) for a proposal slot.

---

### 4.3 JS layer load / status triplet pattern

Every layer loader (dem-main.js, water-mask.js, city-render.js, hydrology-overlay.js, etc.) independently implements:

```js
window.setLayerStatus('xxx', 'loading');
// ... fetch ...
window.setLayerStatus('xxx', 'error');    // in catch
window.setLayerStatus('xxx', 'loaded');   // on success
```

The pattern is correct and consistent, but the surrounding boilerplate (toast on error, clearing stale canvas refs, checking `isLayerCurrent()`) is re-typed per layer.

Files with the most repetition:
| File | `setLayerStatus` calls | `showToast` calls |
|---|---|---|
| `dem-main.js` | 8 | 11 |
| `water-mask.js` | 10 | 15 |
| `city-overlay.js` | — | 13 |
| `event-listeners-map.js` | — | 24 |
| `presets.js` | — | 18 |

**Action (medium-term)**: A shared `loadLayer(name, fetchFn, options)` wrapper in `ui-helpers.js` could handle status transitions and error toasts once. However this requires refactoring 5+ files and risks breaking subtle per-layer logic — document as a proposal before implementing.

---

### 4.4 `window.api.*` calls duplicated across modules

The following API call patterns appear in more than one module:

| Call | Count | Files |
|---|---|---|
| `window.api.dem.load` | 4× | Multiple |
| `window.api.regions.list` | 3× | Multiple |
| `window.api.dem.water` | 2× | Multiple |
| `window.api.regions.create/update/save` | 2× each | Multiple |

Most of these are intentional — different UI flows trigger the same endpoint. No action needed, but worth noting if a call signature changes.

---

## 5. Library Reimplementation Debt

Already tracked in [issues.md](issues.md#library-integration-debt) under B-LIB1–B-LIB5. Summarized here for completeness:

| ID | Description | Severity |
|---|---|---|
| B-LIB1 | `cities_3d._terrain_mesh` (~90 lines) reimplements `numpy2stl.array_to_mesh` | High |
| B-LIB2 | `cities_3d._extrude_ring` / `_ear_clip` reimplements `numpy2stl.polygon` functions | High |
| B-LIB3 | `dem.py` uses legacy `proj_map_geo_to_2D` instead of `core/projection` | Medium |
| B-LIB4 | `sat.py` manually computes tile scale instead of using a `geo2stl` helper | Medium |
| B-LIB5 | `terrain.py` router imports `make_dem_image` bypassing `core/dem` | Small |

---

## 6. Oversized / Unfocused Files

| File | Lines | Notes |
|---|---|---|
| `app/session/terrain_session.py` | 2576 | Session SDK + notebook helpers + encoding helpers all in one file. Split into: `terrain_session.py` (API client), `session_utils.py` (notebook helpers), `session_encoding.py` (image encode/decode). |
| `app/server/core/export.py` | 804 | Async export pipeline + STL helpers + colour-map logic. Could split STL helpers into `core/stl_helpers.py`. |
| `app/server/core/osm.py` | 641 | OSM fetch + rasterize + geometry helpers. Rasterize could live in `core/rasterize.py`. |
| `app/server/routers/terrain.py` | 764 | 6 GET endpoints + ~5 sync helper functions + projection wrappers. Each endpoint is already well-separated; splitting into `routers/terrain_dem.py` + `routers/terrain_overlay.py` is possible but low urgency. |

---

## 7. Redundant Inline Fallback in `server.py` ✅ DONE

`server.py` lines 295–343 defined an `ImportError` fallback that re-declared `BoundingBox` and `BoundingBoxLegacy` inline. Since `schemas.py` has been stable and mandatory since the backend refactor, this fallback is dead code in any working deployment.  
**Action**: ~~Remove the `except ImportError` fallback block.~~ Removed. `BoundingBoxLegacy` (which only existed inside the fallback) is also gone. The `pydantic` `BaseModel`, `validator`, `Field` imports it required were also removed from `server.py`.

---

## Priority Order for Implementation

| Priority | Item | Effort | Risk | Status |
|---|---|---|---|---|
| 1 | Run `ruff check --fix` (unused imports) | 5 min | Zero | ✅ Done manually (ruff binary unavailable) |
| 2 | Remove `db_exists()` | 2 min | Zero | ✅ Done |
| 3 | Remove `success_response()` | 2 min | Zero | ✅ Done |
| 4 | Remove `_decode_satellite_image` / `_encode_satellite_image` | 5 min | Low | ✅ Done |
| 5 | Fix `CityRasterRequest` naming in `composite.py` | 10 min | Low | ✅ Done — renamed to `CompositeCityRasterRequest` |
| 6 | Remove `window._reprojectSatelliteImage` / `window._reprojectCityRaster` | 10 min | Low | ✅ Done |
| 7 | Remove sys-path injection from routers | 5 min | Low | ✅ Done |
| 8 | Remove `server.py` `ImportError` fallback | 10 min | Low | ✅ Done |
| 9 | `BboxParams` `Depends` helper for bbox params | 2–4 hrs | Medium | Not started |
| 10 | `loadLayer()` JS wrapper for status/toast boilerplate | 4–8 hrs | Medium | Not started |
| 11 | Split `terrain_session.py` | 4–8 hrs | Medium | Not started |

---

_See [issues.md](issues.md) for library debt details and [proposals.md](proposals.md) for approved/denied proposal history._
