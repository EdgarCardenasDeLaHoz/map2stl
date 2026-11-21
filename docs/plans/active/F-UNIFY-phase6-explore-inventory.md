# F-UNIFY Phase 6, step 1: Explore page (inventory, mockups, migration plan)

Status: **waiting for the user to approve the mockups.** No code has been migrated.

- Scope: the Explore page only (region list, map, new-region flow, globe). Edit and Extrude stay as they are.
- Sources: `modules/{map,regions,ui,events}/**`, the Vue shell/sidebar/map components, plus a click-through of the running app (worktree build, scratch DB copy, 125 regions, 1300 x 820).
- Real screenshots of today's page: `claude/mockups/2026-11-02-explore-vue/current/` (11 PNGs).
- Mockups of the Vue page: `claude/mockups/2026-11-02-explore-vue/png/` (10 PNGs). Built by `html/build.py`; the maps in them are real renders (`img/`).

## 1. State of the page today

- Half Vue already:
  - Vue: shell, header, sidebar chrome, `NewRegionCard`, selection card, `LandmarkSearch`, `EdgeLandmarkWarnings`, Map ▾ panel markup (`MapContainer.vue`).
  - Legacy JS fills the rest: region list/table rendering (innerHTML into `#coordinatesList`), region boxes, editor logic, Leaflet, Globe, new-region geometry.
- Hazard: Vue components that wrap legacy DOM by element id. Legacy `innerHTML` replaces nodes Vue still holds.
- Biggest coupling: `selectCoordinate` also resets Edit-page state (layer caches, city overlay, mesh layer, borders, viewer model, city fetch), loads saved settings into the `#param*` inputs, and calls `loadAllLayers` when Edit is visible.

## 2. Capability inventory (71)

IDs are stable; the mockup column says where each lands (mockup numbers: 01 home, 02 selected, 03 Map menu, 04 editor, 05 new-region search, 06 new-region box, 07 wide table, 08 globe, 09 Regions ⋯ menu, 10 panel hidden).

### 2.1 Region list (sidebar)
| ID | Capability | Legacy source | Mockup |
|---|---|---|---|
| S1 | Export all regions + saved settings as JSON | `exportRegionsJson` | 09 menu |
| S2 | Import regions from a JSON file | `importRegionsJsonFile` | 09 menu |
| S3 | Show/hide all region boxes (👁 / 🙈) | `toggleBboxLayerVisibility` | 01 icon, 09 switch |
| S4 | Widen / Narrow the list into the table | `sidebarMode` | 01 icon, 07 |
| S5 | Hide the panel; floating button restores it; last visible mode remembered | `#openSidebarBtn`, `lastVisibleMode` | 10 |
| S6 | Search regions by name (`#coordSearch`) | `setupCoordinateSearch` | 01 |
| S7 | Continent filter | `#coordContinentFilter` | 01 chip |
| S8 | Scope toggle "In view (N) / All (total)"; In view = viewport regions, at most 20, largest first, plus the selected one | `viewport-regions.js` | 01 |
| S9 | Paged list (20 per page) when searching or showing All | `region-ui.js` | 01 (footer pager, not drawn) |
| S10 | Collapsible continent groups with counts | `region-ui.js` | 01 |
| S11 | Row: colour swatch, name, dims and description, ✎ edit | `region-ui.js` | 01, 02 |
| S12 | Row keyboard: Enter/Space select, Up/Down move focus | `region-ui.js` | 01 |
| S13 | Hover a row highlights its box on the map; hover a box highlights its row | `region-boxes.js` | 01 |
| S14 | Empty-state text when nothing matches | `region-ui.js` | 01 (not drawn) |
| S15 | "＋ New region" button | `#floatingDrawBtn` | 01 |

### 2.2 Wide table
| ID | Capability | Mockup |
|---|---|---|
| T1 | Search | 07 |
| T2 | Category (group) filter | 07 |
| T3 | Collapsible groups | 07 |
| T4 | N / S / E / W columns | 07 |
| T5 | ✏ Edit (`goToEdit`: opens the Edit page for that region) | 07 |
| T6 | 🗑 Delete with confirm (`deleteRegion`) | 07 |

### 2.3 Region editor (sidebar)
| ID | Capability | Mockup |
|---|---|---|
| R1 | Name | 04 |
| R2 | Group (datalist of existing groups) | 04 |
| R3 | N/S/E/W inputs; the box on the map follows as you type | 04 |
| R4 | Live size readout (km, km²) | 04 |
| R5 | Notes (browser storage `map2stl_regionNotes`) | 04, under More settings |
| R6 | Save (`api.regions.update`; also renames) | 04 |
| R7 | Delete (confirm) | 04 |
| R8 | Back restores the saved box | 04 |
| R9 | Esc = Back | 04 |
| R10 | Form refills when another region is selected (`REGION_SELECTED`) | 04 |

### 2.4 Map
| ID | Capability | Mockup |
|---|---|---|
| M1 | Click a box to select the region | 02 |
| M2 | Hover: name tooltip, list-row highlight, "✏ Edit" marker on the box | 01 |
| M3 | Edit marker hidden when the box is under 40 px | 01 |
| M4 | Box styling: per-name colour, halo, selected state | 01, 02 |
| M5 | Boxes redraw on moveend / zoomend / resize (150 ms debounce) | all |
| M6 | Seven base styles, remembered in `map2stl_mapStyle` (default OpenTopoMap) | 03 |
| M7 | Terrain relief overlay with opacity (global DEM PNG, or on-demand `api.dem.load`) | 03 |
| M8 | Grid lines (default on) with graticule labels at zoom 5 and up | 03 |
| M9 | Map (place-name) labels | 03 |
| M10 | Globe (Three.js sphere with region markers; button hidden if WebGL fails) | 08 |
| M11 | Zoom control | all |
| M12 | Draw toolbar: rectangle, edit, delete | all (left column) |
| M13 | Landmark search (Nominatim, on submit only) | 05 |
| M14 | "Extend the box to include it" (with a region selected) | 05 (shown when a region is selected; not drawn) |
| M15 | Edge-landmark chip "N landmarks near the box edge · Show", dismissible per box | 02 (not drawn; fills `appState.edgeLandmarks`) |
| M16 | Click a listed edge landmark to pan to it | 02 |
| M17 | Legacy hidden floating toggles (`floatingTerrainToggle`, `floatingGridToggle`, `floatingLabelsToggle`) | dropped (hidden today, same controls as 03) |

### 2.5 New-region flow
| ID | Capability | Mockup |
|---|---|---|
| N1 | Phases: place, drawing, pending | 05, 06 |
| N2 | A searched place gets a dashed suggestion box at the print-bed aspect (`placeBox`; bed from `#bedSizeSelect` / `#bedCustomW` / `#bedCustomH`) | 05 |
| N3 | "Draw my own" | 05 hint |
| N4 | "Make a region here ›" | 05 |
| N5 | Draggable, resizable box | 06 |
| N6 | Live size tooltip (≈ W × H mm on bed · pieces) | 06 |
| N7 | Name field | 06 |
| N8 | Group chips (existing groups + New) | 06 |
| N9 | Save (`api.regions.create` with `parameters`, reload list, select it) | 06 |
| N10 | Cancel / Esc | 06 |
| N11 | Ctrl+S saves | 06 |

### 2.6 Selection card, shortcuts, links, header
| ID | Capability | Mockup |
|---|---|---|
| C1 | Selection card: name, dims, position | 02 |
| C2 | "✎ Edit box" | 02 |
| C3 | "Load DEM ›" (`loadSelectedRegionDem`: clicks the Edit tab, then `#loadDemBtn`) | 02 |
| K1 | Ctrl+1/2/3 switch view (with toast) | no UI |
| K2 | Ctrl+S saves the pending new region | 06 |
| K3 | Ctrl+R reloads layers (Edit side) | no UI |
| K4 | Esc cancels draw / editor | no UI |
| H1 | `#region=<name>&tab=explore\|globe\|edit\|extrude` opens on load and on `hashchange` | no UI |
| H2 | Hash kept in sync with the selection (`replaceState`; loose name match) | no UI |
| H3 | Toast for an unknown name | no UI |
| P1 | Header region pill: pick another region (`goExplore`) | all |
| P2 | Header tabs Explore / Edit / Extrude | all |

Total: S 15 + T 6 + R 10 + M 17 + N 11 + C 3 + K 4 + H 3 + P 2 = **71**. Ctrl+Z/Y and G are Edit-side, not counted.

### 2.7 API calls
- `api.regions.list / create / update / delete`
- `api.dem.load` (terrain overlay fallback)
- `api.geocode.search` (landmarks), `api.geocode.edgeLandmarks` (Overpass, cached server-side)
- Static: `/static/global_dem.png` + `global_dem_meta.json`
- `POST /api/usage` (usage log)

### 2.8 `window.appState` and events
- Read/written: `selectedRegion`, `activeView`, `boundingBox` (raw Leaflet layer), `currentDemBbox`, `edgeLandmarks`, `cityFetch`, `demParams` (depthScale, waterScale, subtractWater, dim, height, base), `coordinatesData`, `sidebarMode`, the Leaflet map/layers.
- Events: `REGION_SELECTED`, `BBOX_CHANGED`, `VIEW_CHANGED`; window events `map2stl:new-region`, `map2stl:new-region-save`.
- Globals other code calls: `getMap`, `setMap`, `setPreloadedLayer`, `setBoundingBox`, `getCoordinatesData`, `setCoordinatesData`, `setSelectedRegion`, `selectCoordinate`, `setBboxRectangle`, `setBboxInputValues`, `newRegion.*`, `clearLandmarkSearch`.

### 2.9 Findings from the click-through
- Globe in headless Edge showed a plain blue sphere with no texture or markers after 2.5 s (`current/08_globe.png`). Needs a check on a real GPU before the redesign promises markers.
- With the new-region flow active, the Draw hint pill stayed visible over the Globe (`current/08_globe.png`). Worth confirming whether Esc really clears it.
- The Map ▾ panel (03) and the landmark search sit in the map's top row; the Draw toolbar sits under the search box. Edge-landmark chip only appears after a 1.5 s debounce and an Overpass call.

## 3. Mockup design decisions

- Style: shell from `2026-10-24-unify` (dark, blue accent `#0a84ff`, iOS cards and switches). Manager-with-map page type: list left, map centre.
- Tools stay out of the way: no new Settings panel. Map options live under one "Map ▾" popover; region-file actions (Export, Import, Show boxes, Wide table, Hide panel) fold into a "⋯" menu in the list header (09).
- Beginner path unchanged: pick or search, select, "Load DEM ›". Notes move under "More settings" in the editor.
- Differences from today, all visible in the PNGs:
  - Header icons Export / Import / 👁 / » / ✕ become ⋯ / 👁 / ⤢ / ✕.
  - The Draw toolbar and zoom control are restyled to the dark floating buttons.
  - Editor fields become iOS cards.

## 4. Migration plan (after approval)

Order keeps the app working after every step; each step is its own commit.

1. **Store.** `stores/regions.ts`: `regions[]`, `selectedName`, `scope`, `search`, `continent`, `page`, `sidebarMode`, `boxesVisible`; actions `load / create / update / remove / select`. Mirrors into `appState.coordinatesData` / `selectedRegion` for legacy readers.
2. **`RegionList.vue`** (S6–S14, T1–T6): renders from the store, no innerHTML. Pure helpers (viewport set, grouping, paging) move to a tested `.ts` module.
3. **`RegionEditor.vue`** (R1–R10), replacing `region-editor.js`. The live-box edit goes through a small `exploreMap` API (`setBoxPreview`, `restoreBox`).
4. **`ExploreMap.vue`** owns Leaflet for Explore: tile layers, boxes, hover/click (M1–M6, M11, M12), terrain overlay (M7), grid (M8), labels (M9). Exposes the existing `getMap()` globals so Edit and Extrude keep working.
5. **`MapMenu.vue`** (M6–M9, popover from 03).
6. **`NewRegionFlow`**: fold `new-region.js` geometry into a composable; `NewRegionCard.vue` already exists (N1–N11).
7. **`GlobeView.vue`** (M10), still on `window.THREE`.
8. **`RegionsMenu.vue`** (S1–S5, popover from 09) and the panel-hidden state (10).
9. Bridge cleanup: delete the replaced legacy files and their `window.*` exports once nothing reads them (`graphify` to confirm).

### Bridge fields (kept for Edit / Extrude)
- Keep as `appState` fields: `selectedRegion`, `activeView`, `boundingBox`, `currentDemBbox`, `edgeLandmarks`, `cityFetch`, `demParams`, `sidebarMode`, `coordinatesData`.
- Keep the events `REGION_SELECTED`, `BBOX_CHANGED`, `VIEW_CHANGED` and the globals in 2.8.
- `selectCoordinate` keeps today's Edit reset behaviour (decided 2026-11-13: no Edit-side listener). The store calls `window.selectCoordinate` for selection, so Edit sees the same trigger as today.

## 5. Test plan

- **Unit (vitest):** viewport scope (≤ 20, largest first, selected pinned), grouping, paging, search/filter, `bedAspect` box sizing, hash parse/format, store actions with a fake `api`.
- **Component:** `RegionList` keyboard (Enter/Space/arrows), `RegionEditor` Back/Esc/refill, `RegionsMenu` toggles, `MapMenu` persistence.
- **Python/API:** existing region endpoint tests stay green; one backend change: region notes (`regions.notes` column, `GET/PUT /api/regions/{name}/notes`, 9 tests in `tests/test_regions.py::TestRegionNotes`).
- **Capability check:** walk the 71 IDs above in the built app (Playwright, scratch DB, `MAP2STL_NO_CACHE_MAINTENANCE=1`); fail on any missing ID.
- **Screenshot comparison:** capture the 10 mockup states from the build at 1300 x 820 (the same states and region as `current/`) and compare each with `claude/mockups/2026-11-02-explore-vue/png/` by eye and by a structural diff (layout boxes, not pixels, because the map tiles change).
- **Layout audit:** run `claude/scripts/ui_layout_audit.js` (`mapLayoutAudit()`) in each state: nested scrollers, hidden-but-shown, off-screen. Must be clean.
- **End-to-end pass:** click through the app: select, edit and save, new region by search and by drawing, Map menu, Globe, wide table, hide panel, `#region=` link.
- **Regression for the seam:** select a region on Explore, open Edit, confirm saved settings load and no stale layers remain.

## 6. Open questions

### Decided 2026-11-13
1. Approved: one "⋯" menu holds Export / Import / Show boxes / Wide table / Hide panel (mockup 09).
2. Globe: keep it. Check it in a real (GPU) browser render; if untextured, fix it.
3. Print bed: only sets the aspect ratio of the dashed suggestion box (N2). No printer row in the card. The box uses the bed chosen in Extrude/Settings (persisted preference, square if none). No bed control on Explore.
4. Notes (R5): move into the region record on the server (field, API, one-time migration of browser-local notes: if the server note is empty and localStorage has one, upload it; localStorage stays only as fallback). Python tests added.
5. Drop the three hidden floating toggles (M17); the same controls stay in the Map menu.
6. NOT approved: the `selectCoordinate` Edit reset stays as today; no Edit-side REGION_SELECTED listener.
