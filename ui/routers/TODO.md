# ui/routers/ — Open Tasks

> Routers are thin HTTP adapters: validate input, call core/, return response.
> Business logic belongs in `ui/core/`, not here.

---

## Dead Code Removal

### [x] DEAD-1 — Remove JSON fallback in regions.py
**File:** `ui/routers/regions.py`

Six functions (`_list_regions_json`, `_create_region_json`, `_update_region_json`,
`_delete_region_json`, `_get_region_settings_json`, `_save_region_settings_json`)
were dead code — only reachable if SQLite init failed (`_DB_AVAILABLE = False`).
The DB has been stable since session 1. Removed all 6 functions, the
`_DB_AVAILABLE` guard branches, and the `COORDINATES_PATH` / `REGION_SETTINGS_PATH`
constants that only served the fallback.

**Risk:** Low — tests cover only the DB path; JSON path had no tests.

---

## Code Cleanup

### [ ] CLEAN-1 — Remove duplicate CityRasterRequest + redundant ImportError guards in cities.py
**File:** `ui/routers/cities.py`

Five `try/except ImportError` blocks at module level all guard the same
optional deps (rasterio, osmnx, shapely). Only one guard is needed.
Also `CityRasterRequest` is defined twice (local fallback Pydantic class
+ import from schemas).

**Fix:**
- Import `CityRasterRequest` from `schemas` only; remove local definition.
- Collapse the 5 import guards into a single top-level try/except that
  sets `_DEPS_AVAILABLE = True/False`, checked once per endpoint.

---

## Extraction Candidates (business logic in routers)

### [ ] EXTRACT-1 — Move water mask fetch+merge out of get_terrain_water_mask (terrain.py)
**File:** `ui/routers/terrain.py` lines 306–435

`get_terrain_water_mask` (130 lines) mixes HTTP handling with:
- Auto-scaling bbox → sat_scale math
- Earth Engine API calls
- Image resize / numpy array merge

**Fix:** Extract the EE fetch + image merge to `ui/core/dem.py` as
`fetch_water_mask(north, south, east, west, scale, source)`.
The endpoint becomes: param validation + cache check + `run_in_executor`.
