# TODO — strm2stl

> See `docs/` for architecture reference. Completed items: see `ui/static/FUNCTIONALITY_DOC.md`.
> Per-module TODOs and improvement plans are in each module's `TODO.md`.

---

## Module TODO Files

| Module | File | Key open items |
|--------|------|----------------|
| `dem/` | [`modules/dem/TODO.md`](ui/static/js/modules/dem/TODO.md) | Plan A (off-thread render) |
| `layers/` | [`modules/layers/TODO.md`](ui/static/js/modules/layers/TODO.md) | PERF10, PERF6B |
| `ui/` | [`modules/ui/TODO.md`](ui/static/js/modules/ui/TODO.md) | PERF-B (curve RAF), UX shortcuts, presets versioning |
| `core/` | [`modules/core/TODO.md`](ui/static/js/modules/core/TODO.md) | ARCH4 (Vite), ARCH5 (Vitest), typed events |
| `events/` | [`modules/events/TODO.md`](ui/static/js/modules/events/TODO.md) | onchange cleanup, event bus migration |
| `map/` | [`modules/map/TODO.md`](ui/static/js/modules/map/TODO.md) | Globe init guard, compare view leak, bbox accessibility |
| `regions/` | [`modules/regions/TODO.md`](ui/static/js/modules/regions/TODO.md) | Pagination, import/export JSON, settings inheritance |
| `export/` | [`modules/export/TODO.md`](ui/static/js/modules/export/TODO.md) | P6 (elevation bands), progress indicator, OBJ texture |

---

## Top-Priority Items (cross-module view)

### High Impact
- ~~**PERF10**~~ — done (RAF+scheduler.yield chunking with cancel token)
- **PERF6B** (`layers/city-render.js`) — Web Worker for city polygon rendering
- **PERF7** (`layers/stacked-layers.js`) — DOM cache for 60fps hover *(partially done)*
- ~~**UX-10**~~ — done (status dots removed; mergePanel/regionsContainer retained — still in use)
- ~~**UX-9**~~ — done (hidden param inputs removed; values now in `appState.demParams`)

### Medium Impact
- ~~**PERF8**~~ — done
- ~~**PERF9** + **PERF9-dep**~~ — done
- **P6** (`export/`) — elevation band multi-material STL
- ~~**UX-4**~~ — done (2×2 coord grid + action row; font 12px; linked labels)
- ~~**UX-E**~~ — done (leaflet + leaflet-draw vendored to `ui/static/vendor/`)
- ~~**UX-11**~~ — done (dead main.css deleted)

### Low Impact / Quick Wins
- ~~**UX-7**~~ — done (icon was already `▼`; no change needed)
- ~~**UX-8**~~ — done (added `(exported)` / `(preview only)` badges to clarify depth scale vs exaggeration)
- ~~**MAP-3**~~ — done (verified: no Leaflet instances in compare view; no leak)
- ~~**EXP-2**~~ — done (verified: WebGL failure already shows fallback message; errors shown in statusEl + toast)
- ~~**PERF11** + **PERF11-wire** + **PERF12** + **PERF13**~~ — done
- ~~**MAP-1**~~ — done (globe init guard silenced; TODO updated)
- ~~**EV-1**~~ — done (`onchange`/`oninput` → `addEventListener` in all JS modules)
- ~~**EV-2**~~ — done (`onclick`/`onchange` HTML attrs removed from index.html; compare panel wired in event-listeners.js)

### UX Improvements (from Chrome audit `docs/ux-audit.md`)
- **UX-1** (`map/`) — consolidate region creation to single entry point
- **UX-2** (`map/`) — add text labels to floating map buttons
- **UX-3** (`map/`) — clarify sidebar 3-state toggle
- ~~**UX-5**~~ — done (collapsed tab now uses btn-primary blue, high-contrast #007acc)
- ~~**UX-6**~~ — done (added large Load DEM button to empty state; triggers bboxReloadBtn)
- **UX-12** (`ui/`) — replace inline styles with CSS classes (incremental)
- **UX-M** (`layers/`) — lazy-allocate hidden layer canvases (GPU memory)

### Requires External Setup
- **ARCH4** — `npm install` (Vite; config already written)
- **ARCH5** — Vitest unit tests (requires ARCH4)
