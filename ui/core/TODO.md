# ui/core/ — Open Tasks

> Core modules own business logic. No HTTP handling here.

---

## Refactoring — Separation of Concerns

### [ ] REFACTOR-1 — Split fetch_osm_data into per-layer functions (osm.py)
**File:** `ui/core/osm.py` lines 181–405

`fetch_osm_data` is 224 lines with 8 near-identical if-blocks, one per layer
(buildings, roads, waterways, pois, walls, towers, churches, fortifications).
Pattern per block: fetch → filter geometry types → simplify → log → column-select → to_json.

**Fix:** Extract each layer to a private function:
```python
def _fetch_buildings(bbox, tol_deg, min_area) -> dict: ...
def _fetch_roads(bbox) -> dict: ...
def _fetch_waterways(bbox, tol_deg) -> dict: ...
def _fetch_pois(bbox) -> dict: ...
def _fetch_polygon_layer(bbox, tags, geom_types, height_args, keep_cols, label) -> dict: ...
```
`_fetch_polygon_layer` is a shared template for walls/towers/churches/fortifications
(all use `features_from_bbox`, polygon filter, `_fill_heights`, same column pattern).
`fetch_osm_data` becomes a ~15-line dispatcher.

**Also:** Extract `_count_verts` inner function (line ~286) to module scope.

---

### [ ] REFACTOR-2 — Merge _fill_heights / _fill_building_heights (osm.py)
**File:** `ui/core/osm.py` lines 84–155

`_fill_heights` (lines 84–111) and `_fill_building_heights` (lines 114–155) share
~80% code: same OSM `height` tag regex, same clip-to-bounds, same `height_m` assignment.
Only difference: buildings also check `building:levels` as a fallback.

**Fix:** Add `levels_col: str | None = None` param to `_fill_heights`. When set,
compute `levels * 4.0` as fallback before the final `fillna`. Remove
`_fill_building_heights`. Update call sites in `fetch_osm_data` (only caller).

---

### [ ] REFACTOR-3 — Split _rasterize_city into per-layer helpers (composite.py)
**File:** `ui/routers/composite.py` lines 56–178

`_rasterize_city` is 122 lines handling 4 separate layers (buildings, roads,
waterways, walls) each with its own PIL draw loop. The `geo_to_px`/`coords_to_px`
inner helpers (lines 76–82) are shared across all layers but are nested.

**Fix:**
- Promote `geo_to_px` and `coords_to_px` to module-level helpers.
- Extract:
  ```python
  def _rasterize_buildings(features, coords_to_px, PW, PH) -> np.ndarray: ...
  def _rasterize_roads(features, coords_to_px, PW, PH, m_per_px) -> np.ndarray: ...
  def _rasterize_waterways(features, coords_to_px, PW, PH, m_per_px) -> np.ndarray: ...
  def _rasterize_walls(features, coords_to_px, PW, PH, m_per_px) -> np.ndarray: ...
  ```
- `_rasterize_city` becomes a thin coordinator (~20 lines).

**Note:** These 4 functions are pure computation; a later pass could move them
from `ui/routers/composite.py` to a new `ui/core/rasterize.py`.

---

### [ ] REFACTOR-4 — Extract tile coordinate math from fetch_h5_dem (dem.py)
**File:** `ui/core/dem.py` lines 113–205

`fetch_h5_dem` has a nested `_geo_to_tile_pixel` function (lines ~148–154) and
hardcoded constants `TILE_PX = 6000`, `TILE_DEG = 5.0` (lines ~145–146).

**Fix:**
- Promote `TILE_PX` and `TILE_DEG` to module-level constants.
- Extract `_geo_to_tile_pixel(lat, lon, TILE_PX, TILE_DEG)` to module scope
  so it can be unit-tested independently.
