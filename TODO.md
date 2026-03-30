# TODO — strm2stl

> See `docs/` for architecture reference. Completed items: see `ui/static/FUNCTIONALITY_DOC.md`.

---

## Open Items

### Frontend Architecture

#### [~] ARCH4. Add Vite as bundler — config files done, npm install pending
Already using ESM imports (`main.js` as `type="module"`), so no module-system conversion needed — this is purely adding a build/dev tool layer.

**Work:**
1. `npm create vite@latest -- --template vanilla` — generates `package.json`, `vite.config.js`
2. Add proxy in `vite.config.js`: `'/api/' → 'http://localhost:9000'` (~5 lines)
3. Point Vite's entry at `main.js`; update the `<script type="module">` reference in `index.html`
4. `npm run dev` gives HMR; `npm run build` produces minified `/dist`

**Value:** HMR, minified production build, source maps, unlocks ARCH5.
**Status:** `package.json` and `vite.config.js` created at `strm2stl/`. Run `npm install` when network is available, then `npm run dev` for HMR on port 5173 (API proxied to 9000).
**Risk:** Low — CDN globals (`window.L`, `window.THREE`, `window.Plotly`) already handled correctly.

#### [ ] ARCH5. Unit tests for pure functions (requires ARCH4)
Add Vitest once Vite is set up.

**Work:**
1. `npm install -D vitest`; add `"test": "vitest"` to `package.json`
2. Write `modules/__tests__/*.test.js` for pure functions

**Priority targets:** `interpolateCurve(x)`, `mapElevationToColor(t, cmap)`, `detectContinent(lat, lon)`, `haversineDiagKm()`, cache key generation, `isLayerCurrent()`.

---

### Performance

#### [~] PERF6B. Web Worker for city rendering (Part A done)
Part A (pre-baking `Float32Array` buffers + `_bbox` per feature) is done. City rendering still runs on the main thread, causing jank on zoom/pan with 500+ buildings.

**Work:**
1. Create `workers/city-worker.js` — self-contained draw logic; receives `{type:'init', buildings, roads, waterways}` with `Float32Array` buffers as `Transferable`
2. In `city-render.js`: call `canvas.transferControlToOffscreen()` on the city overlay canvas; pass the `OffscreenCanvas` to the worker via `postMessage`
3. For the stacked-layers canvas: worker renders to its own `OffscreenCanvas` and posts back an `ImageBitmap`; main thread composites
4. Main thread posts `{type:'render', zoom, offset}` on each zoom/pan; worker responds with `{type:'done'}`
5. Cancel in-flight renders with a generation counter

**Tricky parts:** `transferControlToOffscreen()` is one-shot; worker can't access `window`/`appState`.
**Value:** For Philadelphia (~800 buildings), render takes 15–40ms on main thread. With worker: < 1ms.
