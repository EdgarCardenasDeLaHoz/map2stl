# Frontend

Choices about the browser client: framework, state ownership, layer toggles, map overlays and layout
rules. Related: [composite.md](composite.md), [trails.md](trails.md).

### 2026-10-02 — Explore: one map menu, a selection card, sizes in the list
- **Decision:** the map's five floating buttons become two in the corner: 🌍 Globe and Map ▾ (the
  existing map-settings panel: style, terrain relief, grid, labels). The selected region gets a
  card at the bottom of the map (size, position, ✎ Edit box, Load DEM ›); ＋ New region moves
  under the region list; the list's "Showing N of M · Show all" line becomes an "In view (N) /
  All (M)" segmented control, and each row shows the box's size. Sizes everywhere (header pill,
  list, card, editor) come from `region-geometry.js::formatBboxDims`.
- **Why:** design guidelines §1.5 (terrain, grid and labels each had two controls, and the grid
  button did not re-sync the menu's checkbox), §2 Manager (details of the selection with its one
  primary action), §1.10 (segmented control for two options). A first cut of the size formatter
  in `print-scale.js` disagreed with the region editor (3,758 vs 3,760 km) because it used
  different metres per degree; the existing geometry module was reused instead.
- **Rejected:** deleting the hidden floating buttons — `map-globe.js` and the listeners keep
  terrain / grid / label state on them by id.
- **Supersedes / superseded by:** —
- **Source:** [F-DESIGN](../plans/active/F-DESIGN-guidelines-redesign.md)

### 2026-10-02 — Edit: a layer's switch means "in the print"; server channels need no Apply
- **Decision:** the Edit page lists the print's layers (Terrain, Rivers & lakes, Buildings &
  roads, Satellite colour, + trails / land cover / imported mesh) with a switch each. A switch
  sets the model flags (composite rivers / lakes / open water; City model layers; satellite
  colour) and shows the matching preview layer. With the composite on and only channels the
  server can build, the layer spec goes with every preview and export, so the browser never
  rewrites the DEM; Apply to DEM stays for land cover, vegetation and trails. Switching water
  off turns the composite off unless land cover or vegetation contributes (weight > 0). The
  region list leaves the Edit page (the header pill switches regions); the debug pixel grid
  starts off; the full Fetch / View / Composite panels are ⚙ tools.
- **Why:** design guidelines §1.5 / §1.6 (no Apply buttons; what you see is what prints) and the
  review's severe items 4–5. Apply replaced `lastDemData.values` with the composite, which the
  next composite then used as its base, so running Apply automatically would have carved the
  rivers twice. Checked in the app 2026-10-02: one switch on Granada put HydroRIVERS + lakes into
  the 3D preview (80,612 faces vs 79,626); off left no composite layers.
- **Rejected:** auto-pressing Apply after each change — compounds the carve (above); keeping the
  region list on Edit — two columns before the canvas, and the pill already says what is open.
- **Supersedes / superseded by:** —
- **Source:** [F-DESIGN](../plans/active/F-DESIGN-guidelines-redesign.md)

### 2026-10-01 — Pages follow the design guidelines; Extrude is rebuilt first
- **Decision:** adopt the shared `Projects/design-guidelines.md` with the map2stl appendix
  ([../design-guidelines.md](../design-guidelines.md)). Mockup round 1 (layers left, result
  centre, the selection's settings right, everything else in ⚙ Settings with Beginner / Custom /
  Everything) approved as is; build order Extrude → Edit → Explore. Tabs keep the names Explore /
  Edit / Extrude. Default printer bed is **Ender 220 × 220** (was "Prusa 250 × 210").
  "✨ Make it printable" does the full job: pick the preset (City for a small box with buildings,
  otherwise Mountain / Region), load the DEM and layers, size the model to fill the bed, build it,
  open Extrude, and offer Undo.
- **Why:** user choices in the mockup form, 2026-10-01. The default model was 797 × 802 mm on a
  250 × 210 bed ([review](../history/audits/design-review-2026-10-01.md), severe 2); Extrude
  fixes that with the least code.
- **Rejected:** renaming tabs to Area / Layers / Print (user kept the known names); a magic button
  that only resizes, or asks first (user chose the full job).
- **Supersedes / superseded by:** —
- **Source:** [F-DESIGN](../plans/active/F-DESIGN-guidelines-redesign.md)

### 2026-10-01 — Explore map draws a viewport set of ≤ 20 outlined region boxes, shared with the list
- **Decision:** saved-region boxes are outlines (1.5 px, white 55 % over a dark halo, no visible
  fill); selected = accent `#4a9eff`, 3 px, fill 0.08; hover brightens and shows the name. Only the
  viewport set is drawn: regions that intersect the view **and** fit in it, largest first, ≤ 20,
  plus the selected one (`viewport-regions.js::selectViewportRegions`). The sidebar list shows the
  same set; a search, or "Show all", lists every region while the map keeps the viewport set.
- **Why:** 125 filled, palette-coloured boxes overlapped and big ones tinted everything
  (SierraNevada over Granada). One pure helper used by map and list keeps them from drifting.
  "Show all" leaves the map alone because drawing all 125 boxes is the clutter being removed; a
  listed region's box still appears while its row is hovered.
- **Rejected:** fading boxes > 4× the view area (first cut this session) — still drew every box
  that fit, so the world view stayed cluttered; replaced by the user's viewport-set rule.
- **Supersedes / superseded by:** —

### 2026-10-01 — Regions are renamed through PUT /api/regions/{name}, edited in the sidebar editor
- **Decision:** a body `name` different from the path renames the region (copy row, repoint
  `region_settings` / `region_landmarks`, delete old; 409 on a clash). The row's ✎ button opens
  `SidebarEditView.vue` (name, group, bounds with size readout, delete, notes); the 📝 notes icon
  and `RegionNotesModal.vue` are gone (the usage log showed the icon was never clicked).
- **Why:** there was no way to rename; the editor view existed but nothing opened it. The child
  tables' FKs have no ON UPDATE action, so an in-place `UPDATE regions SET name` fails.
- **Rejected:** a separate `/rename` route — the PUT already carries `name`, and the SDK always
  sends the current name, so no caller changes meaning.
- **Supersedes / superseded by:** —

### 2026-10-01 — Edit-tab panels: Fetch = data, View = display, Composite = 3D render
- **Decision:** each Edit-tab control lives in the sub-tab that matches what it changes.
  - **Fetch:** data settings that affect everything: sources, resolutions, projection, which
    rivers exist (HydroRIVERS min order, width).
  - **View:** display only: colormaps, colour modes (hydrology by depth / Strahler order),
    overlays, gridlines, layer visibility.
  - **Composite:** only what the 3D render does with the data: cut depths (river Depth ×,
    lakes, roads, water subtraction), heights and scales (buildings, trails relief), weights.
  - One control per setting: no duplicates across panels (river source, min order and width
    were in both Fetch and Composite).
  - The hydrology preview is the print carve (`/api/terrain/hydrology` with `dem_source`), so
    its settings are the export's.
- **Why:** user, 2026-10-01: "Fetch should handle the details of settings that effect all
  things, View should handle settings that are only related to visualization, Composite should
  handle only settings that refer to rendering to 3D, like cut depth of rivers."
  - An audit found depth and scale settings in Fetch, colormaps in Fetch and Composite, and
    river settings duplicated in two panels.
- **How to apply:** when adding a control, put it by this rule. Keep the element id when
  moving one; modules read controls by id.
- **Rejected:** keeping mesh import and plate registration out of Composite — user chose to
  leave them in Composite for now.
- **Supersedes / superseded by:** —

### 2026-09-26 — Standardise on Vue and retire v2
- **Decision:** one frontend, Vue. The Svelte `v2/` probe is retired (deleted 2026-09-27); its
  good ideas are ported into v1 one at a time (F-DEMID DEM handle first, then F-FE1 steps).
- **Why:**
  - Vue already renders the whole live shell: 31 components with Pinia, PrimeVue, TypeScript,
    ESLint/Vite/vitest in place. v2 had 9 Svelte components.
  - Svelte would mean rewriting all 31 Vue components *and* the 22.6k lines of vanilla JS; Vue means
    moving the vanilla code behind components and stores.
  - v2's real value was backend/data-model design (DEM handle, typed settings, cancel, pushed
    progress, settings versioning), which ports regardless of UI framework.
  - The riskiest coupling was the DEM cache-key handshake ("Missing DEM data"), so the DEM handle
    shipped first: `app/server/core/dem_store.py`, `/api/terrain/dem` returns `dem_id`.
- **Rejected:** Svelte / keep v2 — 4x the rewrite for no backend gain; merging v2 wholesale — it
  exposed 15 of 58 routes and could not write a region.
- **Supersedes / superseded by:** supersedes [v2 second implementation](#2026-08-26--v2-a-second-implementation-of-the-render-pipeline-superseded)
  and [v2 is a design probe](#2026-09-04--v2-is-a-design-probe-not-a-migration-target-superseded);
  answers the "Vue commit-or-drop" call left open in [the UI pass](#2026-08-26--ui-pass-left-three-design-calls-open).
- **Source:** claude/memory-bank/activeContext.md 2026-09-26/27; [F-FE1 plan](../plans/active/F-FE1-vue-consolidation.md)

### 2026-09-05 — Occasionally needed chrome hides behind a peek, not a toggle
- **Decision:** the bbox bar shows a 14 px peek and reveals on hover, `:focus-within`, while its
  mini-map is open, and when pinned. Apply the same shape (peek, hover, focus-within, pin) to any
  future chrome in this class.
- **Why:** read too often for a collapse button, too rarely to justify 57 px of permanent height.
  Focus-within and pin are required — hover alone makes the bar keyboard-unreachable and unusable
  while typing coordinates.
- **Rejected:** a collapse toggle — would be clicked constantly.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-05 entry

### 2026-09-04 — A setup function that re-runs must bind idempotently
- **Decision:** guard the element with a dataset flag before `addEventListener` in any setup that
  re-runs (`setupDemSubtabs` runs at init and on every Edit-view entry); prefer an explicit boolean
  argument over a bare toggle where the control means one direction.
- **Why:** `addEventListener` does not deduplicate fresh closures; with an even listener count a
  toggle opens and closes on one click and reads as dead — the bug appeared or vanished depending
  on how often the user had entered the Edit view.
- **Rejected:** —
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry; `app/client/static/js/modules/ui/view-management.js`

### 2026-09-04 — v2 is a design probe, not a migration target [superseded]
- **Decision:** never merge v2 (15 routes vs v1's 58, read-only on `data.db`); port specific ideas.
  Its advantage was one-way dataflow plus typed field paths; portable list: throwing `request()`,
  non-auto-hiding errors, disabled-reason titles, source fallback, stay dirty on failed save,
  `schemaVersion`, SSE progress, pre-render size readout.
- **Why:** building v2 found v1 defects — inert vertical exaggeration (cancels through min-max
  normalisation), partial region saves wiping settings, two resolutions of `STRM_H5_ROOT`,
  degenerate DEM returning 200.
- **Supersedes / superseded by:** superseded by [Standardise on Vue](#2026-09-26--standardise-on-vue-and-retire-v2)
- **Source:** decisions.md 2026-09-04 entry

### 2026-08-30 — Raster overlays are resampled to Mercator before they touch the map
- **Decision:** any `L.imageOverlay` image must already be Web Mercator; resample next to where the
  image loads, output height `width * mercatorRange / lonSpanRadians`. Basemap-like rasters go in
  their own pane between tiles (z 200) and vectors (z 400).
- **Why:** Leaflet only stretches linearly between the projected bounds, so an equirectangular
  raster is off by ~21 degrees at 30-45N. The fallback path was right and the cached primary path
  was not, so the bug showed in normal use.
- **Also:** when a feature has several controls, every handler mirrors the whole set by assignment
  (never by dispatching change events, which loop).
- **Rejected:** default `overlayPane` — covers every vector on the map.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry

### 2026-08-30 — Vue owns the sidebar mode and nothing else touches its DOM
- **Decision:** `SidebarPanel.vue` publishes `window.setSidebarMode(mode)`; non-Vue code calls it
  and never writes the sidebar's classes or button text. One control per intent (width, hide)
  instead of a three-state cycle; reopen restores the previous width. Flex headers in
  `overflow: hidden` panels wrap and ellipsise the title.
- **Why:** three call sites wrote the nodes by hand with three label vocabularies; the component
  re-renders from a store they never updated, so the hand-written side loses silently.
  `document.elementFromPoint` proved a clipped button was covered by the page header.
- **Rejected:** a single cycling button — makes one intent a two-click detour.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry; `app/client/static/js/vue/components/sidebar/SidebarPanel.vue`

### 2026-08-28 — One helper owns appState.boundingBox
- **Decision:** anything that changes the viewed area calls `window.setBboxRectangle(n, s, e, w)`;
  it updates in place when a rectangle exists (inputs call it per keystroke).
- **Why:** the input fields are display, `appState.boundingBox` is what every fetch reads;
  `selectCoordinate` updated only the fields, so UI and data disagreed with no error. General form:
  state with several representations has exactly one writer.
- **Rejected:** hand-rolled remove/create/add sequences (three copies existed).
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 entry; `app/client/static/js/modules/map/bbox-panel.js::setBboxRectangle`

### 2026-08-28 — Layers fetch their own data when switched on
- **Decision:** a `LAYER_AUTOLOAD` registry of `{ready, load}` per layer (Dem, WaterHydrology,
  Sat, SatImg, CityRaster, CityOverlay, Trails); load fires un-awaited after the UI sync.
  `loadAllLayers` uses `Promise.allSettled`; loaders self-activate only when asked
  (`loadTrails({activate})`).
- **Why:** an empty pane on toggle hid the separate load button; two hardcoded exceptions skipped
  UI sync; one flaky source (Overpass 500s) must not discard layers that arrived.
- **Rejected:** autoloading MeshImport (user file) and CompositeDem (built from loaded layers);
  `Promise.all`.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 entry; `app/client/static/js/modules/layers/stacked-layers.js::LAYER_AUTOLOAD`

### 2026-08-28 — A collapse rule zeroes min-width and clears inline width
- **Decision:** collapsed panels set `min-width: 0` with `width: 0`, and JS clears the inline width
  (stashing it for restore) rather than out-specifying it.
- **Why:** a leftover `min-width: 200px` left an empty column; inline widths from drag/restore
  outrank any stylesheet class.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 entry

### 2026-08-27 — disabled is a user-input rule, not a selection rule
- **Decision:** wherever a select is repopulated or restored from saved settings, explicitly select
  the first non-disabled option.
- **Why:** `disabled` does not stop `select.value = x` nor the browser resting on index 0, and saved
  settings outlive the sources they name (gone tiles or keys).
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry

### 2026-08-26 — v2, a second implementation of the render pipeline [superseded]
- **Decision:** build a from-scratch Svelte 5 client + thin server (`v2/`, port 9100) reusing
  geo2stl/numpy2stl, to compare. Defining ideas: an opaque DEM handle instead of the MD5 cache-key
  handshake, and one typed settings object instead of 464 DOM ids.
- **Why:** it found v1 defects (base thickness inert — 31 mm for 40 asked; sea-level cap inverted).
- **Supersedes / superseded by:** superseded by [Standardise on Vue](#2026-09-26--standardise-on-vue-and-retire-v2)
- **Source:** decisions.md 2026-08-26 entry

### 2026-08-26 — UI pass left three design calls open
- **Decision:** implemented DEM sources from `GET /api/terrain/sources`, persistent export errors,
  export poll cap + cancel, auto-save on with dirty indicator, one workflow vocabulary, GZip, lazy
  region markers. Left open: Vue commit-or-drop, tiering the 306 controls, dead
  `DemSourceSection.vue`.
- **Why:** the half-adopted Vue middle (464 ids, 712 `getElementById`, 258 globals) is the
  expensive state; that call belonged to the user.
- **Note:** `dist/` is gitignored and served as fallback — `.vue` edits need `npm run build`.
- **Supersedes / superseded by:** Vue call answered by [Standardise on Vue](#2026-09-26--standardise-on-vue-and-retire-v2)
- **Source:** decisions.md 2026-08-26 entry
