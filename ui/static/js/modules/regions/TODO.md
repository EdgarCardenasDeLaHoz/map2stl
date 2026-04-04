# regions/ — Open Tasks & Improvement Plans

## Bug Fixes

### [ ] CLEAN-1 — Replace inline onclick in divIcon with Leaflet event listener
**File:** `regions.js` line 106

The edit button inside `L.divIcon` uses `onclick="goToEdit(${originalIndex})"` — the only remaining inline event handler in the codebase. The marker already has mouseover/mouseout wired via `editMarker.on(...)`.

**Fix:** Remove the `onclick=` attribute from the div HTML; add `editMarker.on('click', () => window.goToEdit(originalIndex))` after marker creation.

---

### [ ] CLEAN-2 — Fix haversineDiagKm called as appState method
**File:** `regions.js` line 329

`window.appState?.haversineDiagKm?.()` calls a utility function as a method on appState — wrong location. Line 276 already calls it correctly as `window.haversineDiagKm?.(n, s, e, w)`.

**Fix:** Replace with `window.haversineDiagKm?.(selectedRegion.north, selectedRegion.south, selectedRegion.east, selectedRegion.west)`. First grep to confirm whether `appState.haversineDiagKm` is set anywhere.

---

### [ ] CLEAN-3 — Extract auto-scale thresholds to named constants
**File:** `regions.js` lines 281–304

Four groups of magic `diagKm` breakpoints (for satellite resolution and DEM dimensions) are inline ternary chains. Tuning any threshold requires reading all four to understand the relationship.

**Fix:** Add an `AUTO_SCALE` constant block at module scope:
```js
const AUTO_SCALE = {
    satScale: [
        { maxKm: 10,  scale: 10  },
        { maxKm: 30,  scale: 30  },
        { maxKm: 100, scale: 100 },
        { maxKm: 500, scale: 500 },
        { maxKm: Infinity, scale: 1000 },
    ],
    dim: [
        { maxKm: 10,  dim: 600 },
        { maxKm: 50,  dim: 500 },
        { maxKm: 200, dim: 300 },
        { maxKm: Infinity, dim: 200 },
    ],
};
```
Replace ternary chains with `AUTO_SCALE.satScale.find(t => diagKm <= t.maxKm)?.scale ?? 1000` etc.

---

### [ ] CLEAN-4 — Pass BBOX_COLORS to createGlobeMarker
**File:** `regions.js`, `createGlobeMarker()`

Globe markers are always red (`0xff0000`) regardless of which region they represent. Map rectangles already use `BBOX_COLORS` for per-index colours.

**Fix:** Add `color = 0xff0000` param to `createGlobeMarker`. In `updateGlobeMarkers`, convert the CSS hex from `BBOX_COLORS[i % BBOX_COLORS.length]` to an integer: `parseInt(color.slice(1), 16)`.

---

### [ ] CLEAN-5 — Expand JSDoc on selectCoordinate
**File:** `regions.js`, `selectCoordinate()`

The function is the most complex in the module — it triggers map pan, layer loads, settings load, and auto-scaling — but the JSDoc only describes the parameter.

**Fix:** Document side effects (appState keys written, window.* calls that may fail silently), add `@fires` tags for any events emitted, and note the `try/catch` around `map.fitBounds`.

---

## New Features

### [ ] REG-1 — Region list pagination
**File:** `region-ui.js`

All saved regions load at once. With 50+ regions this becomes slow. Add virtual scrolling or simple pagination (20 per page).

---

### [ ] REG-2 — Region import/export as JSON
**Files:** `regions.js`, `region-ui.js`

- **Export:** Download all regions as `regions.json` (calls existing `/api/regions` GET, triggers browser download)
- **Import:** File picker POSTs each region to `/api/regions` (POST endpoint already exists)

---

### [ ] REG-3 — Region settings inheritance
**Files:** `regions.js`, `routers/regions.py`

Each region stores its own settings snapshot. Add a "use global defaults" override so users can opt out of per-region settings when global defaults change.

---

## Improvement Plans

### Plan A — Duplicate detection
Before saving a new region, warn if an existing region has a nearly identical bbox (±0.01° tolerance).

### Plan B — Region folders / tags
Allow regions to be tagged (e.g. "mountain", "city") and filtered by tag in the sidebar. Store tags as JSON array in `region_settings`.

### Plan C — Batch DEM generation
A "Generate all" button that iterates saved regions and generates preview images without loading each manually.
