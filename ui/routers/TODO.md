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

### [x] CLEAN-1 — Remove duplicate CityRasterRequest in cities.py
**File:** `ui/routers/cities.py`

`CityRasterRequest` was defined twice: once as an inline fallback Pydantic class
inside a `try/except ImportError` block, and once imported from `schemas`.
The 5 import guards are for different modules (config, cache, osm, cities_3d,
schemas) with different availability profiles — they are appropriate as-is.

**Fix:** Removed the fallback schemas block; `CityRequest` and `CityRasterRequest`
are now unconditionally imported from `schemas`.

---

## Dead Code Removal (continued)

### [x] DEAD-2 — Remove unused `dim` param from get_terrain_water_mask (terrain.py)
**File:** `ui/routers/terrain.py` line 318

`dim` is parsed and validated but never used in the real fetch path — only in TEST_MODE's
hardcoded response (lines 363–377). The cache key (lines 328–329) doesn't include it.
A TODO comment already marks it for removal.

**Fix:**
- Remove `dim = _parse_int(params, "dim", 200)` (line 318)
- Remove `_validate_dim(dim)` from the `_validate_bbox(...) or _validate_dim(dim)` call (line 323)
- In TEST_MODE block, replace `h, w = dim, dim` with `h, w = 50, 50` (a small fixed test size)

---

### [x] DEAD-4 — Move `import math` out of endpoint body in terrain.py
**File:** `ui/routers/terrain.py` line 352

`import math as _math` is inside `get_terrain_water_mask`. It is used only in that function
but Python imports are cached — the local form is just noise. Move to module-level imports.

---

## Extraction Candidates (business logic in routers)

### [x] EXTRACT-1 — Move water mask fetch+merge out of get_terrain_water_mask (terrain.py)
**File:** `ui/routers/terrain.py` lines 306–435

`get_terrain_water_mask` (130 lines) mixes HTTP handling with:
- Auto-scaling `sat_scale` to avoid EE pixel limits (lines 351–361)
- Earth Engine/ESA fetch via `_fetch_water_mask_images` (lines 379–382)
- JRC vs ESA mask selection + SRTM bathymetry augmentation (lines 392–411)
- Array merge and reshape (lines 413–419)

**What to extract:** Create `fetch_water_mask(north, south, east, west, sat_scale, dataset)` in
`ui/core/dem.py` that:
1. Auto-scales `sat_scale` using the same bbox pixel-count formula (currently lines 352–361)
2. Calls `_fetch_water_mask_images` (already in `core/dem.py` as `_fetch_water_mask_images`)
3. Selects JRC vs ESA mask, applies SRTM bathymetry augmentation
4. Returns `(water_mask: np.ndarray, esa: np.ndarray, sat_scale_used: int)`

Router endpoint after extraction: parse params → cache check → `run_in_executor(fetch_water_mask)`
→ cache write → JSON response. ~40 lines instead of 130.

**Note:** `_fetch_water_mask_images` is already in `core/dem.py` (confirmed). The `cv2`
import currently sneaks in at line 379; moving it to `core/dem.py` where it belongs unblocks
the test mode path. The TEST_MODE early-return (lines 363–377) stays in the router.

**Risk:** Medium — touches Earth Engine + cv2 integration path. No unit test coverage.
Verify manually: run server, hit `/api/terrain/water-mask`, confirm water mask renders.
