# export/ — Open Tasks & Improvement Plans

## Open TODOs

### [ ] P6 — Elevation band export (multi-material STL)
**Status:** Pending (see `docs/issues.md`)

Split the STL mesh into discrete elevation bands, each as a separate solid. Useful for multi-material 3D printing where different materials represent different elevation zones (e.g. blue for water, brown for land, white for peaks).

**Backend:** New endpoint `/api/export/elevation-bands` — takes the DEM values + band count + colormap, returns a ZIP of N STL files (one per band).
**Frontend:** New export option in the export panel; `export-handlers.js` wires the download.

---

### [ ] EXP-1 — Export progress indicator
**File:** `export-handlers.js`

STL export for large DEMs (400px+) can take 5–15 seconds. The button disables but gives no feedback. Add a progress bar or spinner with an estimated time, driven by polling `/api/export/status` or using a streaming response.

---

### [x] EXP-2 — Export preview in model-viewer
**File:** `model-viewer.js`

Verified done: WebGL failure already shows a fallback message (model-viewer.js line ~103). Errors are shown in `statusEl` and via `showToast`. No change needed.

---

## Improvement Plans

### Plan A — OBJ with texture atlas
Currently cross-section OBJ export (P7) produces geometry only. Add a UV map and export a PNG texture based on the current colormap / satellite image. The combined `.obj` + `.mtl` + `.png` would display with colour in Blender/Meshlab.

**Files:** `export-handlers.js`, backend `routers/export.py` + `core/export.py`

### Plan B — Streaming STL generation
Large DEM meshes exhaust RAM during STL generation because the entire mesh is held in memory before writing. Use Python generators to stream face data row by row, chunked via `StreamingResponse`. Reduces peak memory for 400×400 DEMs.

**Files:** `ui/core/export.py`

### Plan C — Print-bed aware multi-piece export
For regions larger than a given print bed, automatically tile the DEM into N×M pieces with alignment tabs. Each piece exports as a separate STL. Users specify bed dimensions (from the existing Physical Dimensions panel) and an overlap tolerance.
