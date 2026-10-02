# map2stl UI capability inventory (2026-10-01)

Baseline for the F-DESIGN redesign ([plan](../../plans/active/F-DESIGN-guidelines-redesign.md)):
every capability here must stay reachable (visible, or one Settings switch away).
Sources: `app/client/templates/index.html`, `static/js/vue/components/**`, `static/js/modules/**`.

Notation: `label` — type — `#id`. **[ADV]** advanced / expert / debug / registration / ML.
(ro) readout. dyn = rendered by JS at runtime.

**Totals: 462 controls** — header 27, Explore 60, Edit 288 (Fetch 70, View 116, Composite 61),
Extrude 87. Shortcuts and canvas gestures are listed but not counted.

## 0. Global shell
- `📍 Regions` reopen button `#openSidebarBtn` (only while the sidebar is hidden)
- Toasts `#toastContainer` (each with ✕); dev error overlay `#devErrorOverlay` **[ADV]**
- Loading overlays; DEM overlay has `✕ Cancel`

## 1. Header (MainHeader.vue) — 27
- Title "3D Maps"; tabs `#tabExplore` `#tabEdit` `#tabExtrude` with step badges (done / next)
- `#saveSettingsStatus` "Saving… / ✓ Saved / ⚠ Not saved" (ro)
- `📘 Guides` (/guides), `📊 Results` `#pipelineReportsLink` (/reports), `🩺 Diagnostics`, `🔑 Keys`,
  `📖 Docs` `#docsMenuBtn` → Swagger, ReDoc, Project docs, Python API **[ADV]**
- **Keys modal:** Earth Engine (status, Authenticate, auth link, code + Submit, new link, sign up);
  OpenTopography (status, key + Save, get key link); Local SRTM tiles (status + count, path,
  Browse…, Save) **[ADV]**
- **Diagnostics modal:** key status + `Add key`; DEM sources ready/unavailable; selected region
  span + coverage advice; cache size + path **[ADV]**

## 2. Explore — 60
### 2.1 Sidebar header
`Export` (regions JSON), `Import` (file), `👁` `#bboxVisToggleBtn` (boxes on map), `» Widen`
`#sidebarToggleBtn`, `✕` `#sidebarHideBtn`
### 2.2 Region list
- `#coordSearch`, continent filter `#coordContinentFilter`
- "Showing N of M in view" + `Show all` / `Only those in view`
- continent groups (collapse, count); rows (click / Enter / Space, ↑↓ focus, hover highlights box)
- `✎` per row → Region editor; paging `#listPagePrev` / `#listPageNext`; empty state
### 2.3 Region table (widened)
`#sidebarTableSearch`, `#sidebarCategoryFilter`, columns Name/N/S/E/W by continent; per row:
select, `✏ Edit` (→ Edit tab), `🗑` delete
### 2.4 Region editor (SidebarEditView)
`← Back` `#sbBackBtn` (Esc), name `#regionNameEdit`, group `#regionLabelEdit` (datalist),
N/S/E/W `#sbNorth…`, size readout `#sbSizeReadout`, `Save` `#sbSaveBtn`, `Delete` `#sbDeleteBtn`,
Notes `#sbNotesTextarea` (browser-only)
### 2.5 New region form
name `#regionName`, group `#regionLabel`, `💾 Save Region` `#saveRegionBtn`
### 2.6 Map
- Floating: `Terrain` `#floatingTerrainToggle`, `Grid` `#floatingGridToggle`, `Globe`
  `#floatingGlobeToggle` (3D globe), `Labels` `#floatingLabelsToggle`, `Settings`
  `#floatingMapSettingsBtn`
- Map settings popover: tile style `#mapTileLayerExplore` (OSM, OpenTopoMap, ESRI Imagery, ESRI
  Topo, Carto Positron, Carto Dark, Stamen Terrain), terrain overlay + opacity, grid lines, labels
- Landmark search `#landmarkSearchInput` (Nominatim): results, "✓ inside the box" or
  `⤢ Extend the box` `#landmarkExtendBtn`, clear
- Edge-landmark chip: Show/Hide list, dismiss, items pan the map
- `+ New Region` `#floatingDrawBtn`, `Load DEM ›` `#exploreLoadDemBtn`
- Leaflet: zoom, Draw (rectangle, edit, delete), click box to select, hover tooltip, ✎ corner
  marker (→ Edit tab), grid labels, globe orbit/zoom
### 2.7 Unreachable legacy views (no caller)
Regions table `#regionsContainer`; region compare `#compareContainer` (two regions, colormaps,
exaggeration) — candidates for deletion, not capabilities to keep.

## 3. Edit — 288
### 3.1 Centre view
- Empty state + `↺ Load DEM` `#emptyStateLoadBtn`
- Stacked layers canvas `#stackViewCanvas` + axes + grid overlay: wheel zoom, drag pan,
  double-click reset, hover tooltip (elevation, coords), click a building to select it
- Inline compare (two layer selects: DEM / Water / Satellite-Land cover / Combined)
- BBox bar `#demInfoBar`: handle, N/S/E/W (arrows nudge 0.01, Enter reloads), `↺ Reload`
  `#bboxReloadBtn`, `🗺 Map` mini-map drag-edit, `💾 Save` `#saveBboxBtn`, colorbar + elevation
  range, `⚙` settings toggle
### 3.2 Buildings table panel
resize, hide, height summary (counts, source chips, default-height warning, histogram), tallest
list, per-building override (height + Set / Reset), search, table (Building/H/Levels/Source/Type/
Centroid), paging, collapsed tab `📋 Buildings`
### 3.3 Settings panel
- Resize; tabs Fetch / View / Composite; `? Guide`; `◀ Hide`; `{ } JSON` editor (Apply/Cancel)
  **[ADV]**; collapsed tab `⚙ Settings`
#### Fetch (70)
- `🏔 Load DEM` `#loadDemBtn`, `🗑️` clear region cache
- Workflow presets City / Mountain / Region / Coast; edge-landmark chip
- **Projection:** projection select (None, Cosine, Web Mercator, Equidistant, Lambert, Miller,
  Gall, Sinusoidal), `Clip edges` **[ADV]**, description, `Keep fixed canvas shape` **[ADV]**,
  `Auto-reload on bbox change`
- **Fetch Layers:**
  - DEM: source `#paramDemSource` (h5_local, SRTMGL1, COP30, local, SRTMGL3, AW3D30, COP90,
    SRTM15Plus), resolution `#paramDim`, key / high-res warnings, sampling info
  - Hydrology: water dataset (ESA/JRC) + resolution; river source (Natural Earth / HydroRIVERS),
    `Min ord` **[ADV]**, `Width ×` **[ADV]**, Load / clear / status
  - ESA land cover: resolution, Load
  - Satellite: resolution, Load / clear / status
  - Cities: raster res, tolerance **[ADV]**, min area **[ADV]**, m/floor **[ADV]**, Load / clear /
    status, fetch progress + Cancel, counts, buildings-table toggle, Google 3D height
    enhancement **[ADV]**
  - Trails: source (All/OSM/USFS), res, width, Load / clear / status
- **Landmarks** **[ADV]:** Find / Refresh, tallest N, table; editor with source OSM parts / nDSM
  (provider, resolution) / uploaded mesh (upload, fit, up axis, rotate, scale, offsets E/N,
  height mode + m); Preview (3D), Save, Reset to OSM
- **Parameter presets:** select (workflow + view + user presets), Load, revert, Save (name
  dialog), Delete
#### View (116)
- **Layers:** quick strip (9 layers) + 9 rows: visibility, kind · resolution (ro), opacity, ▲▼ order
- **Canvas:** Layers / Compare view, load-status dots, gridlines + density + units
  (deg/m) + colour + spacing, `px` grid mode (`G`) **[ADV]**, pixel grid + spacing + colour
  **[ADV]**, pixel-size readouts, terrain overlay opacity (mirror)
- **DEM:** colormap (Rainbow, Terrain, Viridis, Jet, Hot, Gray), elevation range min/max + Apply +
  Auto button + Auto checkbox, histogram, curve editor (points, presets Linear/Peaks/Depths/
  S-Curve, undo/redo, Apply, Reset, 🌊 Sea shelf)
- **Land use cover:** 12 ESA classes × colour + elevation **[ADV]**, Apply, Reset
- **Hydrology display:** colour by depth / Strahler order, order legend
- **City display:** colormap, buildings/roads/waterways toggles + colours, table toggle
- **Trails display:** ski / hiking / area fill, colours, colour by difficulty + legend
- **Composite display:** colormap
#### Composite (61)
- Pipeline note; `Enable composite layer`
- Base DEM: include, weight, histogram; next-load request: depth **[ADV]**, water **[ADV]**,
  subtract water
- Water: enable, depth, weight
- Rivers & lakes: rivers + depth ×, lakes + depth + min area (ha)
- City / OSM (2D preview only): buildings + scale, roads + cut, waterways + depth, walls + scale;
  city raster (bldg mm/m, road dep, water offset) **[ADV]**; slanted roofs
- Land cover: enable, tree height, weight. Satellite vegetation: enable, height, weight.
- Trails: relief (m), enable, ski, hiking, weight
- Histograms per layer + combined; `👁 Preview`, `✓ Apply to DEM`, `⬓ Split view`; stats
- **Mesh import** **[ADV]:** upload, library browse, up axis, m/px, gap fill, save location,
  auto geocode+register (+ report link), preview heightmap, Register… (modal), Apply to DEM
- **Plate registration** **[ADV]:** plate, pack, street placement, tile correction, Place + verify,
  Cancel, progress, verdict + result table, show on map, save to sidecar, reports link
### 3.4 Mesh registration modal **[ADV]**
OSM overlay, two point-picking canvases (zoom/pan/reset), pair list with remove, Undo, Clear,
Compute, residual

## 4. Extrude — 87
- **Viewer:** orbit / pan / zoom, drag puzzle cut lines; status, scale info, HUD (faces, points,
  error, km); empty / no-WebGL states
- Tabs Build / View / Export, `? Guide`; progress + Cancel
- **Build** (auto-rebuild): printed size vs bed `#modelPrintSize`; mm/px `#mmPerPixel`; fit height
  `#exportModelHeight`; base `#exportBaseHeight`; exaggeration `#exportExaggeration`; vertical
  mode `#exportZMode` (Auto / true × exag / fit); smoothing `#exportMedian` **[ADV]**; sea-level
  cap; solid mesh
- **View:** colormap (Terrain, Viridis, Gray, Satellite, None), wireframe, normals **[ADV]**,
  auto-rotate, reset camera
- **Export:**
  - Pre-flight: format (city / terrain puzzle), Run; report (size vs bed, scale, pieces, faces,
    thinnest, tallest spike, filament, time, per-layer table, warnings)
  - Downloads STL / OBJ / 3MF
  - Engraving & contours: label + text; contours + interval + engraved/raised
  - Split / puzzle: enable, bed-fit note + Use, columns, rows, knob width/depth/clearance
    **[ADV]**, knob shape, engrave ids + north arrow, lay out on plates, reset cuts, export .zip
  - Cross-section: axis, position + Mid, slab depth, download STL
  - City model: 10 layers × enable / mode (extrude, raised, engraved, water) / value; puzzle
    pieces + max mm; Build city model (.zip); score panel **[ADV]** (source, reference, metrics)
  - Printer: real area, footprint, scale, peak height, bed fit; bed select (presets + custom W×H);
    optimizer result

## 5. Keyboard
Ctrl+1/2/3 tabs, Ctrl+S save region, Ctrl+R reload layers, Ctrl+Z/Y curve undo/redo, Esc clear
boxes, `G` pixel grid. Local: list rows Enter/Space/↑↓, editor Esc, bbox arrows/Enter, modal Esc,
landmark search Esc, Keys inputs Enter, height override Enter.
(The global ↑/↓ handler was broken and removed 2026-10-01.)

## 6. Duplicates and name collisions (inputs to the redesign)
1. Load DEM ×3 buttons (+ ↺ Reload, Ctrl+R)
2. Workflow presets: preset bar and the preset select's Workflow group
3. Terrain overlay ×3 toggles, opacity ×2; map style + hidden mirror; labels ×2; map grid ×2 (not
   synced)
4. "Grid" means the Explore map grid, the Edit DEM gridlines, the pixel grid and px mode
5. Settings panel show/hide ×3; buildings table toggle ×4
6. Layer visibility: quick strip and row checkboxes; composite row vs `Enable composite layer`
7. Region bounds edited in 5 places; save region ×4 (incl. Ctrl+S); delete ×2
8. "Edit": list ✎ opens the sidebar editor, table ✏ and map marker open the Edit tab
9. Region search ×2 (+ legacy); "Landmark" means place search, edge warnings and building overrides
10. Building height overrides ×2 (table, landmarks)
11. Water depth ×5, river depth ×2, building scale ×3, road depth ×3, trails relief ×2
12. Puzzle ×2 (terrain split, city pieces); "Resolution" ×4 meanings; colormaps ×4
13. Vertical scale: exaggeration, mode, fit height (+ presets); "Auto" ×2 in elevation range
14. Compare ×3 (inline, split view, legacy); registration flows ×2; help ×2; "Sea" ×2 (curve shelf,
    sea-level cap)

## 7. Orphaned ids (modules look them up; no template has them)
`activeLayerOpacity(Label)`, `applyParamsBtn`, `bboxColorIndicator`, `citiesPanel`,
`citiesSettingsBadge`, `citiesSettingsSection`, `clearBboxBtn`, `compositePreviewThumb`,
`genGlobalDemBtn`/`Status`, `hydroStatus`, `layerCityRasterOpacity(Label)`,
`layerCityRasterVisible`, `loadHydrologyBtn`, `loadSatBtn`, `modelViewerContainer`,
`regionParamsBody`/`Section`, `settingsStripBtn`, `statusPanel`, `statusToggleBtn`,
`waterOpacity`/`Value`, `waterScaleSlider`/`Value`, `waterThreshold`/`Value`.
