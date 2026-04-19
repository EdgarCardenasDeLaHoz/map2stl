# TODO — strm2stl

> See `docs/` for architecture reference. Completed items: see `docs/functionality_doc.md` and `docs/issues.md`.
> Per-module TODOs and improvement plans are in each module's `TODO.md`.
> **AI-proposed features live in [`docs/proposals.md`](docs/proposals.md) — set status to `approved` there to queue implementation.**

---

## Module TODO Files

| Module | File | Key open items |
|--------|------|----------------|
| `dem/` | [`modules/dem/TODO.md`](app/client/static/js/modules/dem/TODO.md) | Plan A (off-thread render) |
| `layers/` | [`modules/layers/TODO.md`](app/client/static/js/modules/layers/TODO.md) | PERF6B (city worker), UX-M (lazy canvas) |
| `ui/` | [`modules/ui/TODO.md`](app/client/static/js/modules/ui/TODO.md) | Curve editor bugs, presets versioning |
| `core/` | [`modules/core/TODO.md`](app/client/static/js/modules/core/TODO.md) | ~~ARCH4 (Vite)~~, ~~ARCH5 (Vitest)~~ |
| `events/` | [`modules/events/TODO.md`](app/client/static/js/modules/events/TODO.md) | Event bus migration |
| `map/` | [`modules/map/TODO.md`](app/client/static/js/modules/map/TODO.md) | UX-1, ~~UX-2/3~~, ~~MAP-2 accessibility~~ |
| `regions/` | [`modules/regions/TODO.md`](app/client/static/js/modules/regions/TODO.md) | REG-1 pagination, REG-2 import/export |
| `export/` | [`modules/export/TODO.md`](app/client/static/js/modules/export/TODO.md) | P6 elevation bands, EXP-1 progress |

---

## Recently Completed

### Python Session Client (`app/session/terrain_session.py`)
- ~~**PEP8** — 51 PEP 8 violations fixed~~
- ~~**REFACTOR-1** — 7 helper methods added (`_get_extent`, `_require_attribute`, `_ensure_available_for_fetch`, `_handle_api_response`, `_prepare_array_response`, `_project_rgb_channels`, `_print_grid_info`)~~
- ~~**REFACTOR-2** — 5 settings properties (`dem_settings`, `view_settings`, `export_settings`, `city_settings`, `water_settings`)~~
- ~~**REFACTOR-3** — Matplotlib consolidation via enhanced `_plot_geo_image()` (5 show_* methods refactored, ~39 lines eliminated)~~
- ~~**REFACTOR-4** — Fetch method consolidation (6 fetch_* methods use shared helpers)~~
- ~~**TOTAL** — ~150 lines reduced (~8.3% of original 1805 lines)~~

### HydroRIVERS (`app/server/core/hydrorivers.py`)
- ~~**HYDRO-1** — Geometry simplification pipeline (`_collinear_point_reduction`, `_simplify_geometry`, `_simplify_and_cache_shapefile`)~~
- ~~**HYDRO-2** — Region bounding box coverage fix (SA extended to +15°N, NA to -10°S, eliminating gaps)~~
- ~~**HYDRO-3** — Simplified cache validation with probe before trusting~~

### Server Lifecycle
- ~~**SRV-1** — `start()` reuses healthy server instead of killing it~~
- ~~**SRV-2** — `_ensure_bbox()` validates all 4 keys with descriptive error~~
- ~~**SRV-3** — Server wait timeout increased to 60 attempts~~

---

## Performance Optimizations

- **PERF6B** (`layers/city-render.js`) — Web Worker for city polygon rendering (Part A — pre-baked Float32Array buffers — done)
- ~~**PERF-RAF** (`ui/curve-editor.js`) — RAF-gate `applyCurveTodemSilent` in mousemove so DEM recolors at ≤60fps during drag~~

---

## Code Cleanup

- **CLEAN-1** (`ui/`) — Replace remaining inline styles with CSS utility classes (UX-12, incremental)
- ~~**CLEAN-2** (`map/bbox-panel.js`) — MAP-2: add keyboard accessibility to bbox drag handles~~
- ~~**CLEAN-1–5** (`regions/regions.js`) — done: inline onclick, haversineDiagKm fix, AUTO_SCALE constants, globe marker colors, JSDoc~~

---

## New Features

- **P6** (`export/`) — Elevation band multi-material STL export
- **EXP-1** (`export/`) — Progress indicator during STL generation
- **REG-1** (`regions/`) — Region list pagination (virtual scroll or 20-per-page)
- **REG-2** (`regions/`) — Region import/export as JSON
- **UX-1** (`map/`) — Consolidate region creation to single entry point
- ~~**UX-2** (`map/`) — Add text labels to floating map buttons~~
- ~~**UX-3** (`map/`) — Clarify sidebar 3-state toggle~~
- **UX-M** (`layers/`) — Lazy-allocate hidden layer canvases (GPU memory)

---

## Requires External Setup

- ~~**ARCH4** — `npm install` (Vite; config already written at `strm2stl/vite.config.js`)~~
- ~~**ARCH5** — Vitest unit tests for pure functions (requires ARCH4)~~
