# Test Coverage Audit

**Date:** Current session (post dead-code cleanup)
**Suite baseline:** 440 Python + 93 JS tests passing
**After E2E additions:** 481 Python tests passing

---

## 1. Coverage by Layer

### 1.1 Server routers

| Router | File | Covered? | Notes |
|---|---|---|---|
| Regions CRUD | `test_regions.py` | ✅ Good | create/select/update/delete, 404s, validation |
| Terrain DEM | `test_terrain.py` | ⚠️ Partial | `POST /api/terrain/dem` covered; water-mask, satellite, hydrology **NOT** covered |
| Export STL/OBJ | `test_export.py` | ⚠️ Partial | STL and OBJ export covered; split/puzzle export, slice, verify, inspect **NOT** covered |
| Cache | `test_cache.py` | ✅ Good | status, clear, region-clear all tested |
| Cities | `test_cities.py` | ⚠️ Partial | `POST /api/cities` covered; `/api/cities/raster` and `/api/cities/export3mf` **NOT** covered |
| Composite | `test_composite.py` | ⚠️ Partial | city-raster covered; dem-merge, hydrology-merge **NOT** covered |
| Settings | `test_settings.py` (if any) | ❌ None found | `/api/settings/projections`, `/api/settings/colormaps`, `/api/settings/datasets` not directly tested (covered by new E2E class) |

### 1.2 TerrainSession SDK (new — `test_session_e2e.py`)

| Feature | Tests Added | Notes |
|---|---|---|
| Region CRUD via SDK | ✅ 8 tests | create, select, update, delete, error paths |
| Settings persistence round-trip | ✅ 3 tests | save → re-select → verify values |
| DEM fetch | ✅ 5 tests | dimensions, keys, guard clauses, numeric values |
| Settings validation (`_validate_settings`) | ✅ 10 tests | all validator branches |
| Cache status/clear via SDK | ✅ 3 tests | status, clear, post-clear |
| Bbox guard (`_ensure_bbox`) | ✅ 3 tests | raises/passes |
| Multi-step pipelines | ✅ 4 tests | create→dem, save→reload→dem, list, delete |
| `/api/settings/*` sub-endpoints | ✅ 4 tests | projections, colormaps, datasets |
| `server_settings()` broken endpoint | ✅ 1 test | documents that GET /api/settings returns 404 |

---

## 2. Specific Gaps

### 2.1 Server endpoints with no test coverage

| Endpoint | Router | Risk | Suggested action |
|---|---|---|---|
| `GET /api/terrain/water-mask` | terrain | Medium | Add to `test_terrain.py`; needs mock for EE calls or TEST_MODE override |
| `GET /api/terrain/satellite` | terrain | Medium | Add to `test_terrain.py`; needs WMTS tile mock |
| `GET /api/terrain/hydrology` | terrain | Medium | Natural Earth path works offline; add smoke test |
| `POST /api/composite/dem-merge` | composite | Medium | Add layer-merge smoke test |
| `POST /api/composite/hydrology-merge` | composite | Low | Tested implicitly by `merge_hydrology_with_dem()` in notebooks |
| `GET /api/export/obj/verify` | export | Low | Mesh health-check; depends on a previously exported OBJ |
| `GET /api/export/obj/inspect` | export | Low | Same dependency |
| `POST /api/export` (format=obj_split) | export | Medium | Split puzzle export; slow but important |
| `POST /api/export/slice` | export | Low | PrusaSlicer integration; can't run in CI without slicer binary |
| `POST /api/cities/raster` | cities | Low | City raster compositing |
| `POST /api/cities/export3mf` | cities | Low | 3MF export |

### 2.2 SDK methods with no test coverage

| Method | Risk | Why not covered |
|---|---|---|
| `server_settings()` | **Bug** | Calls `GET /api/settings` which does not exist; always raises 404. Should call `/api/settings/projections` etc. or be updated to a combined endpoint |
| `fetch_water_mask()` | Medium | Calls `_fetch_water_endpoint()` which uses `requests.get` directly; requires TEST_MODE override for EE |
| `fetch_esa_landcover()` | Low | Same as above |
| `fetch_satellite()` | Medium | Calls `requests.get` directly; WMTS tile needs mock |
| `merge_dem()` | Medium | Calls `requests.post` directly; needs layers fixture |
| `fetch_cities()` | Low | Calls `requests.post` directly; too large bbox guard tested elsewhere |
| `export_obj()` | Medium | Returns binary ZIP; calls `requests.post` directly |
| `verify()` | Low | Depends on prior export |
| `inspect_obj()` | Low | Same |
| `slice()` | Low | Requires PrusaSlicer binary |
| `run_all()` | Low | Composite convenience method |
| `show_dem()`, `show_*` | Low | Display/matplotlib methods; not worth unit testing |
| `check_alignment()` | Low | Requires all fetch* data; complex visualization |
| `fetch_building_heights()` | Low | Calls external height providers directly (not via HTTP API) |

### 2.3 Session internals not tested

| Method | Notes |
|---|---|
| `_extract_flat_settings()` | Only tested implicitly via `save_settings()` round-trip |
| `_build_region_payload()` | Tested implicitly via `create_region()` and `update_region()` |
| `_decode_b64_grid()` | Tested implicitly via `fetch_dem()` DEM decode |
| `_apply_projection()` | Not tested; projection=none in all E2E tests |
| `_rescale_layer()` | Not tested directly |
| `_colorize_dem()`, `_colorize_esa()` | Static colour-mapping; low risk |

---

## 3. Fixed Issues

### Issue: `server_settings()` bug — RESOLVED ✅

**Original problem:** Called `GET /api/settings` which did not exist (was split into `/api/settings/projections`, `/api/settings/colormaps`, `/api/settings/datasets`)

**Solution implemented:** 
- Added combined `GET /api/settings` endpoint in `routers/settings.py` that aggregates all three sub-endpoints
- Keeps the separate endpoints for fine-grained clients (frontend, mobile)
- Provides the SDK with a single convenient initialization call

**Files changed:**
- `app/server/routers/settings.py`: Added combined endpoint (30 lines)
- `app/session/terrain_session.py`: Updated `server_settings()` docstring to reflect the endpoint now exists
- `tests/test_session_e2e.py`: Changed test from expecting failure to verifying success

**Result:** `server_settings()` now works correctly; SDK tests pass.

---

## 3. Known Bugs Discovered (remaining)

### Bug 1: `server_settings()` calls non-existent endpoint

**RESOLVED** — See "Fixed Issues" section above.

---

## 4. Assessment Summary

| Tier | Verdict |
|---|---|
| Region CRUD (server + SDK) | ✅ Well covered |
| DEM fetch pipeline (server + SDK) | ✅ Well covered (test mode) |
| Settings validation (SDK) | ✅ Well covered |
| Cache operations (server + SDK) | ✅ Well covered |
| Water mask / satellite / hydrology (server) | ❌ Not covered |
| Export STL/OBJ (basic) | ⚠️ Smoke test only |
| Export puzzle split / slicer | ❌ Not covered |
| Cities raster / 3MF | ❌ Not covered |
| Composite dem-merge / hydrology-merge | ❌ Not covered |
| SDK methods using `requests` directly | ⚠️ Need `_FakeRequests` + additional E2E tests |
| SDK display methods (`show_*`) | N/A — not worth testing |

**Priority additions for next session:**
1. ✅ **DONE:** Fix `server_settings()` bug (added combined endpoint)
2. ✅ **DONE:** Add water-mask smoke test using TEST_MODE (2 tests added)
3. ✅ **DONE:** Add dem-merge composite test (2 tests added)
4. ✅ **DONE:** Add hydrology smoke test (2 tests added)
