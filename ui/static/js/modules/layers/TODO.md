# layers/ — Open Tasks & Improvement Plans

## Performance

### [~] PERF6B — Web Worker for city polygon rendering (Part A done)
**File:** `city-render.js`

Pre-baking Float32Array buffers is done. Main-thread polygon rendering still causes jank with 500+ buildings.

**Remaining work:**
1. Create `workers/city-worker.js` — receives `{type:'init', buildings, roads, waterways}` with Float32Array buffers as Transferable
2. `city-render.js`: call `canvas.transferControlToOffscreen()` on city overlay canvas; pass to worker
3. For stacked view: worker renders to `OffscreenCanvas`, posts back `ImageBitmap`; main thread composites
4. Main thread posts `{type:'render', zoom, offset}` on each pan/zoom; generation counter cancels stale renders

**Tricky:** `transferControlToOffscreen()` is one-shot; worker cannot access `window`/`appState`.

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
