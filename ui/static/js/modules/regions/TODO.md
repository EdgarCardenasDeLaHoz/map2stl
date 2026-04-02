# regions/ — Open Tasks & Improvement Plans

## Open TODOs

### [ ] REG-1 — Region list pagination
**File:** `region-ui.js`

All saved regions load into the sidebar list at once. With 50+ regions this becomes slow to render and scroll. Add virtual scrolling or simple pagination (20 per page).

---

### [ ] REG-2 — Region import/export as JSON
**File:** `regions.js`, `region-ui.js`

Users currently can't share or back up their saved regions. Add:
- **Export:** Download all regions as `regions.json` (calls existing `/api/regions` GET endpoint, triggers browser download)
- **Import:** File picker that POSTs each region to `/api/regions` (POST endpoint already exists)

**Files:** New buttons in the regions sidebar; handlers in `region-ui.js`.

---

### [ ] REG-3 — Region settings inheritance
**Files:** `regions.js`, backend `routers/regions.py`

Each region stores its own settings snapshot. When global defaults change (e.g. default colormap), existing regions keep their old settings. Add a "use global defaults" override per region so users can opt out of per-region settings.

---

## Improvement Plans

### Plan A — Duplicate detection
Before saving a new region, check if an existing region has the same (or nearly the same) bbox. Show a warning: "This looks like an existing region (Philadelphia). Save anyway?" Compare bboxes with a tolerance of ±0.01°.

### Plan B — Region folders / tags
Allow regions to be tagged (e.g. "mountain", "city", "coastal") and filtered by tag in the sidebar. Store tags as a JSON array in `region_settings`. Display as colored badges in the region list.

### Plan C — Batch DEM generation
A "Generate all" button that iterates all saved regions and calls `loadDEM` for each, saving the resulting canvas as a preview image. Useful for building a catalog of regions without loading each manually.
