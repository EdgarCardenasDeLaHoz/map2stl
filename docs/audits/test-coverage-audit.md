# Test Coverage Audit

**Date:** 2026-04-24
**Suite baseline:** 532 Python + 93 JS tests passing

---

## 1. Coverage by Layer

### 1.1 Server routers

| Router | File | Covered? | Notes |
|---|---|---|---|
| Regions CRUD | `test_regions.py` | ✅ Good | create/select/update/delete, 404s, validation |
| Terrain DEM | `test_terrain.py`, `test_session_e2e.py` | ✅ Good | DEM, water-mask, satellite, hydrology all covered (router + E2E paths) |
| Export STL/OBJ | `test_export.py` | ⚠️ Partial | STL and OBJ export covered; split/puzzle export, slice, verify, inspect **NOT** covered |
| Cache | `test_cache.py` | ✅ Good | status, clear, region-clear all tested |
| Cities | `test_cities.py` | ⚠️ Partial | `POST /api/cities` covered; `/api/cities/raster` and `/api/cities/export3mf` **NOT** covered |
| Composite | `test_composite.py`, `test_session_e2e.py` | ✅ Good | city-raster, dem-merge, hydrology-merge all covered |
| Settings | `test_session_e2e.py` | ✅ Good | `/api/settings/projections`, `/api/settings/colormaps`, `/api/settings/datasets` covered by E2E class |

### 1.2 TerrainSession SDK (`test_session_e2e.py`)

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
| `server_settings()` combined endpoint | ✅ 1 test | verifies SDK bootstrap through `GET /api/settings` |

---

## 2. Active Follow-Through

Most of the originally missing coverage has already been closed. The remaining active gaps are intentionally small and should be tracked from `../todos/README.md`, not from this audit.

### 2.1 Remaining API and SDK gaps worth tracking

| Area | Remaining gap | Where it should be tracked |
|---|---|---|
| Export | `obj_split`, `slice`, and dependent `verify`/`inspect` flows still lack strong test coverage | `../todos/README.md` (`TEST-1`) |
| Cities | `POST /api/cities/raster` and `POST /api/cities/export3mf` still need direct coverage | `../todos/README.md` (`TEST-1`) |
| Session helpers | Projection and rescaling internals remain mostly implicitly tested | Keep as low-priority follow-up unless a regression appears |
| Display helpers | `show_*` and visualization helpers remain intentionally untested | No current action |

## 3. Closed Since The Original Audit

- `server_settings()` is fixed and covered.
- Water-mask, satellite, hydrology, and DEM-merge smoke paths are covered.
- The broad "settings endpoint missing" bug and the largest router gaps are no longer active.

## 4. Rule For This Document

Keep this file as the evidence summary. If a remaining gap becomes active work, create or update the owning row in `../todos/README.md` and trim this audit rather than rebuilding a second backlog here.
