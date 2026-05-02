## Plan: Frontend UI/UX Improvements

_Last updated: 2026-04-30 (Phase 5 complete — all items shipped)._

This plan tracked impactful UI/UX improvements for the `strm2stl` frontend. All items have now shipped.

---

## Status

### Done

| Step | Item | Where it shipped |
|---|---|---|
| 1 | CLEAN-1: replace inline styles with CSS utility classes | `app.css` `.row`, `.col`, `.row-gap*`, `.check-label` |
| 2 | UX-2: text labels on floating map buttons | `map/floating-toolbar.js` |
| 3 | UX-3: explicit sidebar toggle states | `ui/sidebar-state.js` |
| 4 | **MAP-2**: keyboard accessibility on bbox drag handles | `map/bbox-panel.js` — `BBOX_KEYBOARD_NUDGE_STEP` + `keydown` ArrowUp/Down/Left/Right on all four bbox inputs |
| 5 | PERF-RAF: RAF gating in curve editor | `ui/curve-editor.js` |
| 6 | UX-M: lazy-allocate hidden canvases | `layers/stacked-layers.js` |
| 7 | **DEM caching**: fast repeated DEM loads | Client-side `_demResponseCache` in `dem/dem-main.js` (serves repeated same-params loads instantly); server-side `_dem_mem_cache` `OrderedDict` in `routers/terrain.py` (avoids .npz re-read + base64 re-encode across requests) |
| 8 | **EXP-1**: progress indicator for STL / OBJ / 3MF / cross-section export | `export-handlers.js` — `_asyncExport` uses `_progressEl()` for STL/OBJ/3MF; `downloadCrossSection` now also uses `_progressEl()` |
| 9 | **UX-1 (empty state hint)**: "Draw a region on the map to begin" | `regions/region-ui.js` lines 134-137 |
| 10 | **UX-1 (single entry point)**: consolidate region creation buttons | `vue/components/views/MapContainer.vue` — `floatingDrawBtn` is the sole entry point |
| 11 | CLEAN-3: magic numbers → named constants | `regions/regions.js` (`AUTO_SCALE` block), `export-handlers.js` (`EXPORT_RESOLUTION_*`, `EXPORT_EXAGGERATION_*`, `EXPORT_BASE_HEIGHT_*`), `event-listeners-map.js` (`DEM_RES_WARNING_THRESHOLD`, `WATER_RES_WARNING_THRESHOLD`) |
| 12 | CLEAN-1 in regions: inline handlers → listeners | `regions/regions.js`, `dem/dem-main.js` (cancel button), `templates/index.html` (dev error overlay) |
| 13 | **DEM-CLEAN-2**: dynamic progress-bar `.style.*` → CSS classes | `export-handlers.js` uses `.hidden` / `.progress-bar--error`; `dem-main.js` DEM progress fill uses CSS `width:0` default; `.model-progress` hidden via `.hidden` class |

### Open

_None — all tracked items shipped._

---

## Verification

Each open item should ship with:
- Playwright E2E test (visual regression where applicable)
- Manual accessibility check (tab order, ARIA, contrast)
- Doc update in the relevant `modules/<m>/TODO.md` and `docs/modules.md`

## Decisions

- Scope strictly frontend / browser client (backend ML work tracked separately in `docs/plans/height-training-status.md`)
- Playwright is the primary UI test harness
- All changes reflected in both code and the matching module's `TODO.md`

---

## Verification

Each open item should ship with:
- Playwright E2E test (visual regression where applicable)
- Manual accessibility check (tab order, ARIA, contrast)
- Doc update in the relevant `modules/<m>/TODO.md` and `docs/modules.md`

## Decisions

- Scope strictly frontend / browser client (backend ML work tracked separately in `docs/plans/height-training-status.md`)
- Playwright is the primary UI test harness
- All changes reflected in both code and the matching module's `TODO.md`
