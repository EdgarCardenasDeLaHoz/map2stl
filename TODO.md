# TODO — strm2stl

> See `docs/` for architecture reference. Completed items: see `ui/static/FUNCTIONALITY_DOC.md`.
> Per-module TODOs and improvement plans are in each module's `TODO.md`.

---

## Module TODO Files

| Module | File | Key open items |
|--------|------|----------------|
| `dem/` | [`modules/dem/TODO.md`](ui/static/js/modules/dem/TODO.md) | Plan A (off-thread render) |
| `layers/` | [`modules/layers/TODO.md`](ui/static/js/modules/layers/TODO.md) | PERF6B (city worker), UX-M (lazy canvas) |
| `ui/` | [`modules/ui/TODO.md`](ui/static/js/modules/ui/TODO.md) | Curve editor bugs, presets versioning |
| `core/` | [`modules/core/TODO.md`](ui/static/js/modules/core/TODO.md) | ARCH4 (Vite), ARCH5 (Vitest) |
| `events/` | [`modules/events/TODO.md`](ui/static/js/modules/events/TODO.md) | Event bus migration |
| `map/` | [`modules/map/TODO.md`](ui/static/js/modules/map/TODO.md) | UX-1/2/3, MAP-2 accessibility |
| `regions/` | [`modules/regions/TODO.md`](ui/static/js/modules/regions/TODO.md) | REG-1 pagination, REG-2 import/export |
| `export/` | [`modules/export/TODO.md`](ui/static/js/modules/export/TODO.md) | P6 elevation bands, EXP-1 progress |

---

---

## Performance Optimizations

- **PERF6B** (`layers/city-render.js`) — Web Worker for city polygon rendering (Part A — pre-baked Float32Array buffers — done)
- **PERF-RAF** (`ui/curve-editor.js`) — RAF-gate `applyCurveTodemSilent` in mousemove so DEM recolors at ≤60fps during drag

---

## Code Cleanup

- **CLEAN-1** (`ui/`) — Replace remaining inline styles with CSS utility classes (UX-12, incremental)
- **CLEAN-2** (`map/bbox-panel.js`) — MAP-2: add keyboard accessibility to bbox drag handles

---

## New Features

- **P6** (`export/`) — Elevation band multi-material STL export
- **EXP-1** (`export/`) — Progress indicator during STL generation
- **REG-1** (`regions/`) — Region list pagination (virtual scroll or 20-per-page)
- **REG-2** (`regions/`) — Region import/export as JSON
- **UX-1** (`map/`) — Consolidate region creation to single entry point
- **UX-2** (`map/`) — Add text labels to floating map buttons
- **UX-3** (`map/`) — Clarify sidebar 3-state toggle
- **UX-M** (`layers/`) — Lazy-allocate hidden layer canvases (GPU memory)

---

## Requires External Setup

- **ARCH4** — `npm install` (Vite; config already written at `strm2stl/vite.config.js`)
- **ARCH5** — Vitest unit tests for pure functions (requires ARCH4)
