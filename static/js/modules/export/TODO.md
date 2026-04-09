# export/ — Open Tasks & Improvement Plans

## New Features

### [ ] P6 — Elevation band export (multi-material STL)
Split the STL mesh into discrete elevation bands, each as a separate solid. Useful for multi-material 3D printing.

**Backend:** New endpoint `/api/export/elevation-bands` — takes DEM values + band count + colormap, returns ZIP of N STL files.
**Frontend:** New export option in export panel; `export-handlers.js` wires the download.

---

### [ ] EXP-1 — Export progress indicator
**File:** `export-handlers.js`

STL export for large DEMs (400px+) can take 5–15 seconds. Button disables but gives no feedback. Add a progress bar or spinner driven by polling `/api/export/status` or a streaming response.

---

## Improvement Plans

### Plan A — OBJ with texture atlas
Cross-section OBJ export currently produces geometry only. Add UV map + PNG texture from current colormap/satellite image.

### Plan B — Streaming STL generation
Large DEM meshes exhaust RAM. Use Python generators to stream face data via `StreamingResponse`.

### Plan C — Print-bed aware multi-piece export
Auto-tile regions larger than a given print bed into N×M pieces with alignment tabs; export each as a separate STL.
