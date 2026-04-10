# layers/ — Open Tasks & Improvement Plans

## Performance

### [x] PERF6B — Web Worker for city polygon rendering (done)
**Files:** `city-render.js`, `workers/city-worker.js`

Worker receives pre-baked Float32Array buffers + DOM-extracted style/toggle values,
renders to OffscreenCanvas, posts back ImageBitmap. Generation counter discards stale
replies. Sync fallback (per-layer OffscreenCanvas, Part A) preserved for browsers
without Worker support or on worker error.

---

## New Features

### [ ] UX-M — Lazy-allocate hidden layer canvases
**File:** `stacked-layers.js`, `index.html`

All 7 canvas elements are allocated at full resolution simultaneously, occupying GPU memory even when hidden. Create each canvas only when its data first loads; free (zero-dimension) canvases when switching away.

**Risk:** `canvas.width = 0` loses context — must recreate via `document.createElement('canvas')` on reactivation.

---

## Improvement Plans

### Plan A — Unified layer pipeline
A `LayerBuffer` class that allocates/resizes canvases in one place, tracks dirty state, and provides `render(sourceCanvas) → targetCanvas`.

### Plan B — Progressive composite preview
Show a downsampled preview (every 4th pixel) immediately while `computeCompositeDem()` runs.

### Plan C — Layer blend modes
Composite multiple layers using canvas `globalCompositeOperation` for effects beyond simple alpha.
