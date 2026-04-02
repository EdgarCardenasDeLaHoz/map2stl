# Rotation Support Plan

## Overview

A `rotation` angle (degrees clockwise) is already stored per region in `region_settings.settings_json` for imported CoOrLists regions (e.g. `{"rotation": 35.0}`). This plan promotes rotation to a first-class field throughout the stack: database column, API, frontend state, UI controls, and rendering.

---

## Current State

- **DB**: `regions` table has no `rotation` column. Rotation lives in `region_settings.settings_json` as `{"rotation": 35.0}` (workaround used during CoOrLists import).
- **API**: `RegionCreate` / `RegionResponse` have no `rotation` field. `RegionSettings` has no `rotation` field.
- **Frontend**: No rotation state variable, no UI control, no rendering code.

---

## Step 1 — Database Migration

### 1a. Add column to schema

**File:** `ui/core/db.py`

Add `rotation REAL DEFAULT 0` to the `_CREATE_REGIONS` CREATE TABLE statement:

```sql
    rotation       REAL    DEFAULT 0,
```

### 1b. Migration for existing databases

Since `data.db` already exists (no migrations framework), add a migration call inside `init_db()`:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    """Apply any schema migrations that may be missing."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(regions)")}
    if "rotation" not in cols:
        conn.execute("ALTER TABLE regions ADD COLUMN rotation REAL DEFAULT 0")
        logger.info("Migration: added rotation column to regions")
```

Call `_migrate(conn)` at the end of `init_db()` before the final commit.

### 1c. Backfill from region_settings

After adding the column, backfill rotation values already stored in `region_settings`:

```sql
UPDATE regions
SET rotation = (
    SELECT json_extract(rs.settings_json, '$.rotation')
    FROM region_settings rs
    WHERE rs.region_name = regions.name
      AND json_extract(rs.settings_json, '$.rotation') IS NOT NULL
)
WHERE EXISTS (
    SELECT 1 FROM region_settings rs
    WHERE rs.region_name = regions.name
      AND json_extract(rs.settings_json, '$.rotation') IS NOT NULL
);
```

This can be run once as a one-off script or added to `_migrate()`.

---

## Step 2 — API / Pydantic Schemas

**File:** `ui/schemas.py`

### 2a. `RegionCreate` — add rotation field

```python
class RegionCreate(BoundingBox):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=512)
    label: Optional[str] = Field(None, max_length=64)
    rotation: float = Field(0.0, ge=-360.0, le=360.0, description="Clockwise rotation in degrees")
    parameters: Optional[RegionParameters] = None
```

### 2b. `RegionResponse` — expose rotation in responses

```python
class RegionResponse(BoundingBox):
    name: str
    description: Optional[str] = None
    label: Optional[str] = None
    rotation: float = 0.0
    parameters: Optional[RegionParameters] = None
```

### 2c. `RegionSettings` — add rotation for settings blob

```python
class RegionSettings(BaseModel):
    ...
    rotation: Optional[float] = None
```

---

## Step 3 — Backend Router

**File:** `ui/routers/regions.py`

### 3a. List regions — include rotation in SELECT

```python
rows = conn.execute(
    "SELECT name, label, description, north, south, east, west, rotation, "
    "dim, depth_scale, water_scale, height, base, subtract_water, sat_scale "
    "FROM regions ORDER BY name"
).fetchall()
```

### 3b. `_row_to_region` — include rotation in response dict

```python
def _row_to_region(row) -> dict:
    r = dict(row)
    params = {k: r.pop(k) for k in _PARAM_FIELDS if k in r}
    if "subtract_water" in params:
        params["subtract_water"] = bool(params["subtract_water"])
    r["parameters"] = params
    # rotation stays at top level (not inside parameters)
    return r
```

### 3c. Create/update region — write rotation column

Add `rotation` to the INSERT:

```python
conn.execute(
    "INSERT OR REPLACE INTO regions "
    "(name, label, description, north, south, east, west, rotation, "
    " dim, depth_scale, water_scale, height, base, subtract_water, sat_scale) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    (
        region.name, region.label, region.description,
        region.north, region.south, region.east, region.west,
        getattr(region, 'rotation', 0.0),
        params.dim, ...
    ),
)
```

---

## Step 4 — Frontend State

**File:** `ui/static/js/app.js` (or `modules/regions/`)

### 4a. Add `currentRotation` to `window.appState`

```js
window.appState.currentRotation = 0;   // degrees clockwise
```

### 4b. Load rotation when a region is selected

In the region-selection handler (where `currentRegion` is set):

```js
window.appState.currentRotation = region.rotation ?? 0;
document.getElementById('regionRotation').value = window.appState.currentRotation;
```

### 4c. Save rotation when saving a region

Include `rotation: window.appState.currentRotation` in the POST/PUT body sent to `/api/regions`.

---

## Step 5 — UI Control

**File:** `ui/static/index.html` (bbox strip or region settings section)

Add a rotation input near the bounding box controls:

```html
<label for="regionRotation">Rotation °</label>
<input id="regionRotation" type="number" min="-360" max="360" step="5" value="0"
       title="Clockwise rotation applied before DEM fetch">
```

Wire in JS:

```js
document.getElementById('regionRotation').addEventListener('change', e => {
    window.appState.currentRotation = parseFloat(e.target.value) || 0;
    // Optionally re-render preview
    window.reloadDEM?.();
});
```

---

## Step 6 — Rendering (DEM Canvas Rotation)

The DEM pixel grid is always axis-aligned (north up). Rotation means the *bounding box* is rotated relative to north before fetching, so the terrain slab is tilted — not that the canvas is spun.

### Option A — Server-side (recommended)

Pass `rotation` to `POST /api/terrain` (or `POST /api/dem`). On the backend:

1. Expand the bbox by `rotation_padding_factor` (e.g. 1.5×) to avoid black corners after rotation.
2. Fetch the larger DEM.
3. Use `scipy.ndimage.rotate(dem_array, angle, reshape=False, order=1)` to rotate the DEM data.
4. Crop back to the requested dimensions.
5. Return the rotated+cropped DEM.

**File to modify:** `ui/core/dem.py` (the DEM fetch/processing function)

```python
if rotation:
    from scipy.ndimage import rotate as ndimage_rotate
    dem = ndimage_rotate(dem, -rotation, reshape=False, order=1)
```

### Option B — Client-side canvas CSS transform (simpler, visual only)

Apply CSS `transform: rotate(Xdeg)` to the DEM canvas element. This is purely visual — the underlying data is not rotated, so water masking and composite layers stay misaligned. Suitable only as a quick preview.

**Recommendation**: Implement Option B first for immediate visual feedback; follow up with Option A for correct data alignment.

---

## Step 7 — Stacked Layer View

When multiple layers are rendered in the stacked view, all must be rotated consistently. The simplest approach: wrap all layer canvases in a container `<div>` and apply the CSS rotation to the container. The JS `drawLayerGrid()` in `stacked-layers.js` will need to account for the rotation when computing the grid lat/lon lines (or be temporarily disabled when rotation ≠ 0).

---

## File Change Summary

| File | Change |
|------|--------|
| `ui/core/db.py` | Add `rotation REAL DEFAULT 0` to schema; add `_migrate()` helper in `init_db()` |
| `ui/schemas.py` | Add `rotation: float = 0.0` to `RegionCreate`, `RegionResponse`, `RegionSettings` |
| `ui/routers/regions.py` | Include `rotation` in SELECT, INSERT, `_row_to_region` |
| `ui/static/index.html` | Add `#regionRotation` number input to bbox strip or settings panel |
| `ui/static/js/app.js` | Add `currentRotation` to `appState`; wire rotation input; include in save payload |
| `ui/core/dem.py` | (Option A) Apply `scipy.ndimage.rotate` after DEM fetch when rotation ≠ 0 |
| `ui/static/js/modules/layers/stacked-layers.js` | (Option B) Apply CSS rotation to layer container |

---

## Execution Order

1. DB migration (`db.py`) — run `init_db()` once to add column and backfill.
2. Schema (`schemas.py`) — add `rotation` fields.
3. Router (`regions.py`) — read/write `rotation` column.
4. State + UI (`app.js` + `index.html`) — add input, wire to state, include in save.
5. Rendering — Option B (CSS) first for quick visual result, then Option A (scipy) for correctness.
6. Test: add a region with `rotation=45`, save, reload, confirm rotation persists and DEM renders rotated.

---

## Open Questions

- Should rotation be clamped to `[0, 360)` or allowed as `[-360, 360]`? The import currently uses raw values from CoOrLists (e.g. `35`, `-15`, `45`). Keeping signed range is more intuitive.
- The water mask and city overlay fetch their own tiles. Option A (server-side rotation) must be applied consistently to all layer fetches, or those layers will be misaligned. Coordinate this in the terrain router.
- `drawLayerGrid()` draws axis-aligned lat/lon lines. With rotation, these lines no longer align with the rotated slab edges. Possible fix: skip grid lines when `|rotation| > 5°`, or transform the grid canvas with the same rotation.
