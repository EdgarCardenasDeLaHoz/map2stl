# Frontend Refactoring History

_Last updated: 2026-04-09_

---

## Summary of completed work

| Phase | Status | Description |
|-------|--------|-------------|
| Extract HTML/CSS/JS | Done (2026-03-17) | Split 9 700-line `index.html` monolith into separate files |
| Split `app.js` into modules | Done (2026-03-18) | Full ES-module split under `static/js/modules/` |
| Relocate frontend assets | Done (2026-04-09) | Moved from `ui/static/`, `ui/templates/` to `static/`, `templates/` |

---

## Current File Layout

```
strm2stl/
├── templates/
│   └── index.html              ← ~1 448 lines: HTML shell + module imports
├── static/
│   ├── css/
│   │   └── app.css             ← ~3 180 lines: all styles
│   └── js/
│       ├── main.js             ← entry point: bootstraps module imports
│       ├── app.js              ← thin shim / legacy compat (~333 lines)
│       └── modules/
│           ├── core/           ← state.js, api.js, cache.js, ui-helpers.js, events.js
│           ├── dem/            ← dem-loader.js, dem-main.js, dem-gridlines.js, dem-merge.js
│           ├── layers/         ← stacked-layers.js, water-mask.js, city-overlay.js, city-render.js, composite-dem.js
│           ├── map/            ← map-globe.js, bbox-panel.js, compare-view.js
│           ├── regions/        ← regions.js, region-ui.js
│           ├── export/         ← export-handlers.js, model-viewer.js
│           ├── ui/             ← app-setup.js, curve-editor.js, keyboard-shortcuts.js, presets.js, view-management.js
│           └── events/         ← event-listeners.js, event-listeners-map.js, event-listeners-ui.js, event-listeners-export.js
```

---

## Phase 1 — Extract HTML/CSS/JS (done 2026-03-17)

| File | Before | After |
|------|--------|-------|
| `index.html` | 9 708 lines | ~915 lines |
| `static/css/app.css` | (inline) | 2 558 lines |
| `static/js/app.js` | (inline) | 6 233 lines |

HTML/CSS/JS split into separate files with no logic changes.

---

## Phase 2 — Split `app.js` into ES modules (done 2026-03-18)

`app.js` was split into the module tree above. All functions are now in focused files
loaded as ES modules via `main.js`. The old `app.js` is a thin compatibility shim.

---

## Phase 3 — CSS cleanup (pending)

`app.css` is currently ordered chronologically. Suggested reorganisation into labelled
sections:

1. Reset & CSS variables
2. Layout — app-container, sidebar, main, header
3. Navigation — tabs, view containers
4. Map view — controls, floating buttons, bbox popups
5. Globe view — Three.js container
6. Edit view — DEM container, stacked layers, axes
7. Settings panel — right panel, strip buttons, collapsibles
8. Bbox editor — inputs, mini-map, colorbar
9. Extrude / model view — 3D viewer, controls
10. Regions — sidebar table, coordinate items, notes
11. Modals & overlays
12. Form controls — inputs, selects, buttons, sliders
13. Status & feedback — toasts, loading, status dots
14. Utilities — `.hidden`, `.flex-row`, `.text-muted`
15. Responsive — media queries

Also: 30+ inline `style="..."` attributes in `index.html` should move to CSS classes:

| Pattern | Count | Suggested class |
|---------|-------|----------------|
| `display:flex; align-items:center; gap:Npx` | 20+ | `.flex-row`, `.gap-sm`, `.gap-md` |
| `font-size:12px; color:#aaa` | 12+ | `.text-muted` |
| `display:none` | 30+ | `.hidden` (already exists — use consistently) |
| `background:#404040; color:#ccc; ...` | 8+ | `.input-dark` |
| `margin-top:Xpx; border-top:1px solid #444` | 10+ | `.section-divider` |

---

## Framework analysis (reference)

**Recommendation: stay vanilla ES modules.**

The app is canvas/map-heavy (DEM, stacked layers, Three.js globe, Leaflet). Frameworks add
overhead without commensurate benefit:

- **React/Vue** — require a build step; canvas and Leaflet/Three.js need awkward escape
  hatches; full rewrite risk.
- **Alpine.js** — no build step, good for toggle/collapsible boilerplate, but not suited
  for canvas-heavy data visualisation. Could complement vanilla JS in Phase 3 for purely
  UI toggle patterns.
- **Vanilla + ES modules** — no build step, full control over canvas/Leaflet/Three.js,
  already working. Best fit.
