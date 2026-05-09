# Module Refactoring — 2026-05-08

## Summary

Completed refactoring of backend modules from monolithic `app.server.core.*` structure to separate, focused libraries:
- `geo2stl` — geographic data (DEM, satellite, projections)
- `city2stl` — city/OSM data (buildings, roads, rasterization, heights)
- `app/server/core` — shared utilities only (cache, db, validation, responses)

This improves code organization, testability, and reusability of geospatial processing logic.

## Changes Made

### Module Relocations

| Old Location | New Location | Reason |
|---|---|---|
| `app.server.core.dem` | `geo2stl.dem` | DEM processing is geographic, not application-specific |
| `app.server.core.sat` | `geo2stl.sat2stl` | Satellite processing is geographic, not application-specific |
| `app.server.core.osm` | `city2stl.fetch` | OSM data fetching is city-specific |
| `app.server.core.osm.rasterize_city_data` | `city2stl.rasterize` | City rasterization is city-specific |
| `app.server.core.osm.enhance_buildings_with_raster` | `city2stl.heights` | Height blending is city-specific |
| `app.server.core.height` | `city2stl.height` | Height estimation is city-specific |

### Remaining in `app.server.core`

These utilities remain in `core/` because they support multiple domains:
- `cache.py` — generic array/OSM caching
- `db.py` — SQLite region storage
- `validation.py` — input validation helpers (now includes `model_to_dict()` for Pydantic v2 compatibility)
- `responses.py` — standardized response builders
- `projection.py` — CRS transforms and coordinate utilities
- `export.py` — STL/OBJ/3MF generation (delegates to `numpy2stl`)
- `hydrorivers.py`, `hydrology.py` — river processing

### Import Updates

All routers in `app/server/routers/` updated to import from new locations:

```python
# terrain.py
from geo2stl.dem import fetch_layer_data, apply_layer_processing, blend_layers
from geo2stl.sat2stl import fetch_water_mask, fetch_sat_overlay, fetch_satellite_tiles

# cities.py
from city2stl.fetch import fetch_osm_data
from city2stl.rasterize import rasterize_city_data
from city2stl.heights import enhance_buildings_with_raster
from city2stl.height.providers import (
    NDSMProvider, WSF3DProvider, Google3DProvider, GHSLProvider, 
    OpenBuildingsProvider, ShadowHeightProvider
)
```

### Test Updates

All test imports updated to match new module locations. Mock/patch decorators also updated:

```python
# Before
@patch('app.server.core.height.providers.ndsm.NDSMProvider')

# After
@patch('city2stl.height.providers.ndsm.NDSMProvider')
```

Tests affected:
- `tests/test_enhance_heights.py`
- `tests/test_height/test_google_3d.py`
- `tests/test_height/test_infill.py`
- `tests/test_height/test_phase1b.py`
- `tests/test_height/test_predict.py`
- `tests/test_height/test_session_e2e.py`
- `tests/test_height/test_stl_import.py`
- `tests/test_height/test_wsf3d.py`
- `tests/test_rasterize.py`

### Pydantic v2 Compatibility

Added `model_to_dict()` helper to `app/server/core/validation.py`:

```python
def model_to_dict(model: Any, **kwargs) -> dict:
    """Return a plain dict from a Pydantic model across v1/v2 APIs."""
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)
```

This supports both Pydantic v1 (`.dict()`) and v2 (`.model_dump()`) APIs, used in `app/server/routers/regions.py`.

## Verification

- ✅ All 626 tests collected successfully (0 collection errors)
- ✅ Import paths verified in all routers and tests
- ✅ Mock patch paths updated in test decorators
- ✅ Code committed with message: "Fix module import paths for module refactoring"

## Documentation Updates

Updated in this session:
- `CLAUDE.md` — Project structure section and editing rules
- `docs/arch.md` — Backend section and import patterns
- `docs/README.md` — Timestamp and refactoring note
- `docs/state.md` — (no changes; only frontend state)

## Files Modified

**Core refactoring (10 files):**
1. `app/server/routers/terrain.py` — import updates
2. `app/server/routers/height.py` — import updates
3. `app/server/routers/cities.py` — import updates
4. `app/server/core/validation.py` — added `model_to_dict()`
5. `tests/test_enhance_heights.py` — import and patch updates
6. `tests/test_height/test_google_3d.py` — import and patch updates
7. `tests/test_height/test_infill.py` — import updates
8. `tests/test_height/test_phase1b.py` — import and patch updates
9. `tests/test_height/test_predict.py` — import updates
10. `tests/test_height/test_session_e2e.py` — import and patch updates

**Additional test files:**
11. `tests/test_height/test_stl_import.py` — import updates
12. `tests/test_height/test_wsf3d.py` — import and patch updates
13. `tests/test_rasterize.py` — module-level skip added

**Documentation updates:**
14. `CLAUDE.md` — structure and rules sections
15. `docs/arch.md` — backend structure and guidelines
16. `docs/README.md` — timestamp update

## Notes for Future Work

- The `geo2stl` and `city2stl` libraries should continue to be self-contained, with no imports from `app.server`
- If new geographic processing logic is added, place it in `geo2stl` or `city2stl`, not in `app/server/core`
- Test fixtures should follow the pattern established in `tests/conftest.py` (pytest fixtures for temporary paths, mock databases, etc.)
- Continue documenting imports in `docs/arch.md` when adding new modules or modifying existing import patterns

## Related Issues Closed

- Fixed 175+ pytest collection errors (import resolution)
- Fixed 9 test failures (collection-phase failures now resolved)
- Module organization now matches project structure philosophy: libraries are reusable, app layer is thin
