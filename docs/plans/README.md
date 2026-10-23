# Roadmap — map2stl

_Last updated: 2026-10-14._ The one place for "what's next".

- One line per open item, grouped by area; the plan link holds the detail.
- Bugs and debt live in [../issues.md](../issues.md); unreviewed ideas in [../proposals.md](../proposals.md).
- Why things are the way they are: `docs/decisions/<topic>.md`.
- Plan files:
  - `active/` — work in progress.
  - `done/` — finished; the "Decisions" section points to `docs/decisions/`.
  - `archive/` — abandoned, failed or superseded.
  - `proposals/` — written proposals awaiting a decision (2026-10-05: the skyline improvement plan
    and technology review; the approved items are tasks T28–T34 on `docs/agents/TASKS.md`).
- When an item ships, delete its line here and update the plan's Progress section. A plan
  moves to `done/` when its last line here is gone.

## Active plans

| Plan | State | Next step |
|---|---|---|
| [F-FRAME](active/F-FRAME-projected-frame.md) | approved 2026-10-17 | P0, P1, P6 mockups done; paused for F-DETAIL |
| [F-DETAIL](active/F-DETAIL-survey-detail.md) | in progress 2026-10-17 | D1-D5 built and exported on Granada; roof results cached on disk (cold 124 s, warm 3.4 s); open faults in issues.md |
| [F-ARCH](active/F-ARCH-consolidation.md) | mostly done (steps 1–9, 12, 13, two-stage pipeline) | the leftover rows listed under Architecture below |
| [F-CITYMODEL](active/F-CITYMODEL-vector-city-model.md) | mostly done (merged solid, 3MF, puzzle, build speed) | volume check and build spec (below) |
| [F-COMPOSITE3](active/F-COMPOSITE3-server-side-composite.md) | pass 1 done | pass 2: land cover and the parity test |
| [F-FE1 + F-DEMID](active/F-FE1-vue-consolidation.md) | DEM handles in use; step 7 (delete `v2/`) done; steps 0–6 not pursued | F-DEMID leftovers only |
| [F-LANDMARK](active/F-LANDMARK-roofs-and-building-parts.md) | §1–§6 done | check the result on the landmark set |
| [F-USAGE](active/F-USAGE-local-usage-log.md) | in progress | usage log, then a flow report from real sessions |
| [F-EDITPANEL](active/F-EDITPANEL-fetch-view-composite.md) | Edit right panel: selected layer only, Fetch / View / Composite / Canvas, one control per setting, resolution follows Detail | build to the approved mockup |
| [F-DESIGN](active/F-DESIGN-guidelines-redesign.md) | Explore, Edit and Extrude rebuilt (cards, layers, More settings in each right panel, ✨, live composite) | retake old guide screenshots; consistency pass |
| [F-SKYBENCH](active/F-SKYBENCH-height-benchmark.md) | planned | truth per footprint (survey + 3D Tiles), then the scorer |
| [F-COARSEBLD](active/F-COARSEBLD-coarse-building-layer.md) | planned (proposal pending) | block polygons from OSM landuse + roads, then the GHS-BUILT raster lookup |
| [F-VIEW-SATPARTS](active/F-VIEW-SATPARTS-satellite-on-model-parts.md) | approved 2026-10-14 | planar UVs in `toViewerFrame`, then share the quick-view satellite texture |
| [F-PLINTH](active/F-PLINTH-building-plinths-on-slopes.md) | approved 2026-10-14 | flatten DEM cells under footprints before the terrain TIN; heights from the pad |
| [F-WATERSURF](active/F-WATERSURF-flat-water-surface.md) | planned (proposal pending) | shallower lake cut, then level river pieces, then the sea polygon |
| [F-VIEW-AO](active/F-VIEW-AO-ambient-occlusion.md) | planned (proposal pending) | PNG mockup of the switch, then the horizon-AO bake in a worker |
| [F-CITYTREES](active/F-CITYTREES-trees-in-the-city-model.md) | planned (proposal pending) | fetch `natural=tree` / `tree_row`, then crowns, then woods; measure faces |
| [F-BRIDGES](active/F-BRIDGES-bridges-and-tunnels.md) | planned (proposal pending) | keep the tags and drop tunnels (can ship alone), then decks and piers |
| [F-WATERLAYER](active/F-WATERLAYER-one-water-layer.md) | approved 2026-10-18 | PNG mockup of the merged Rivers & lakes group, then the single-cut rule on the server |
| [F-PISTES](active/F-PISTES-ski-runs-on-the-print.md) | approved 2026-10-18 | piste line width, then trails on by default where pistes exist |
| [F-ROOFLIDAR](active/F-ROOFLIDAR-roof-shape-from-survey.md) | approved 2026-10-18 | probe a buildings-bearing 1 m Granada nDSM (REDIAM MDS − MDT), then score the plane-fit reader on Granada's tagged roofs against the GBM's 0.783 balanced accuracy before wiring |
| [F-ROOFIMG](active/F-ROOFIMG-roof-detail-from-imagery.md) | built 2026-10-20 | ridge direction shipped (imagery 0.75 vs long axis 0.58 on the confident subset); hip and courtyards measured, gated off; retry courtyards with finer lidar |
| [F-PANOVIEW](active/F-PANOVIEW-pano-vs-model-views.md) | in progress 2026-10-20 | export height score vs Granada lidar truth; 6 Street View panos and model renders from the same camera |
| [F-KEYS](active/F-KEYS-keys-and-folders-panel.md) | approved 2026-10-14; PNG mockups still need approval | approve the mockups, then a manual spike of the loopback Google sign-in on this PC |
| [F-DATADIR](active/F-DATADIR-portable-data-dir.md) | approved 2026-10-14 | `DATA_DIR` in `app/paths.py`, then route the writers through it; runs before F-KEYS on the same PC |
| [F-DESKTOP](active/F-DESKTOP-standalone-app.md) | approved 2026-10-14 | `[project.scripts]` plus `app/cli.py`, then a `numpy2stl` wheel; needs F-DATADIR first |
| [F-CLOUD](active/F-CLOUD-hosted-multi-user.md) | plan approved 2026-10-14; implementation not approved | phase 1 only (stateless and bounded), which also improves the desktop build |
| [F-SPEED](active/F-SPEED-region-open-and-fetch.md) | approved 2026-10-14 (phases 1 and 2); phases 1 and 2 done | phase 3: post-water grid cache (#17), binary DEM response (#16), hydrology keyed on the shared size (#18) |
| F-DET, registration learning | skyline and registration plans | see the Skyline section |

## Mesh pipeline and city model

- **Build spec round-trip**: write the full build spec into `report.json` and accept it back
  (`TerrainSession.build(spec)` / CLI). [F-CITYMODEL](active/F-CITYMODEL-vector-city-model.md)
- **Cold-build cost**: after the 2026-09-29 pass (Granada 183 → 92 s) the rest is assembly
  (~19 s of manifold booleans, sequential by measurement), the buildings layer (16 s) and puzzle
  file writing (~8 s). A "Topological inconsistency" message is still printed by the simplifier.
  Why: [mesh-pipeline decisions](../decisions/mesh-pipeline.md).
  [F-CITYMODEL](active/F-CITYMODEL-vector-city-model.md)
- **Next profiling pass: large regions** (user, 2026-09-30). Profile the region endpoints, not
  only the city build, on the Amazon region (40° x 34°, `h5_local`) plus Grand Canyon, Rhine and
  Sierra Nevada, and add them to the profiler (`Code/claude/scripts/profile_city.py`, which only
  covers city builds today):
  - DEM: the H5 read is now block-averaged (8 s, 128 MB, was a ~20 GB crash); check the rest of
    `/api/terrain/dem` (payload encoding, projection) at this size.
  - Hydrology: `rasterize_hydrorivers` took 99 s for 88,799 rivers at 600 px (111 s total, plus
    9 s simplify + buffer) on the Amazon.
  - Satellite tiles: 10.6 s (63 tiles at zoom 6).
  - Earth Engine layers (water mask, ESA land cover) now that `earthengine-api` is installed.
  - The 2D layer stack and composite at this size in the browser (usage log timings).
  - GEBCO local store (`geo2stl/tiles.py::stitch_tiles_no_rasterio`, source `local`): reads whole
    90° GeoTIFF tiles to crop a box (0.9 GB peak for Granada, 2.2 GB for the Amazon). Read
    windows instead (rasterio / tifffile), as the H5 reader now does.
- **Region-size guards found in use (2026-09-30)**:
  - DEM source vs area: SRTMGL3 caps at 4.05 M km²; the Amazon box (17.1 M km²) was sent
    anyway and failed. Check the source's area limit before the request and suggest GEBCO.
  - Hydrology on small mountain boxes returns almost nothing (Breckenridge: 1 HydroRIVERS
    feature, order ≥ 3) and reads as "failed". Say so in the toast, or fall back to OSM
    waterways below a size.
  - `ocean_root` in `config.json` is per PC and pointed at a moved folder (0 TIFs, silent).
    Warn at startup when the local store is empty.
- **Incremental rebuild**: after a layer or setting change, rebuild only the affected layer
  solids. The layer and model caches exist; the UI still asks for a full build.
  [city guide](../guides/city-stl-and-puzzle-sop.md)
- **Server-side cancel**: ✕ Cancel only stops the browser waiting. The export task has no cancel
  route and runs to the end. [city guide §5](../guides/city-stl-and-puzzle-sop.md)
- **Draped layers on steep ground**: slabs repeat terrain detail (Breckenridge green).
  [city guide §5](../guides/city-stl-and-puzzle-sop.md)
- **Courtyard roofs**: a pitched roof on a footprint with an inner ring comes out flat at
  mid-roof height, as does an unknown `roof:shape`.
  [F-LANDMARK](active/F-LANDMARK-roofs-and-building-parts.md)
- **Realistic City Model (user-chosen 2026-10-14)**, in this order, by value for effort:
  1. [F-VIEW-SATPARTS](active/F-VIEW-SATPARTS-satellite-on-model-parts.md) (Small): satellite
     image on the finished model's terrain, roads and green. Reuses the existing quick-view
     drape; view only, no print risk.
  2. [F-PLINTH](active/F-PLINTH-building-plinths-on-slopes.md) (Medium): flat pads under
     buildings on slopes. Fixes heights overstated on every hillside build (a 10 m building on a 20 % slope prints 40 % too tall
     at Granada scale), not only the look.
  3. [F-WATERSURF](active/F-WATERSURF-flat-water-surface.md) (Medium): flat water just below the
     banks. Builds on the existing `water` mode; lakes are already flat, so the work is rivers,
     the sea and a shallower cut.
  4. [F-VIEW-AO](active/F-VIEW-AO-ambient-occlusion.md) (Medium): soft occlusion in the 3D
     view. View only, but a new bake and a mockup step.
  5. [F-CITYTREES](active/F-CITYTREES-trees-in-the-city-model.md) (Medium–Large): tree crowns.
     Visible gain at street scale only, and a face and boolean-time budget to hold.
  6. [F-BRIDGES](active/F-BRIDGES-bridges-and-tunnels.md) (Large): decks and piers. Needs a
     re-fetch of every cached road layer, a change to the assembly order and fragile thin
     decks; its first step (drop tunnels and culverts) is small and can ship early.

## Landmarks and heights

- **Landmark validation**: render Granada Cathedral, the Alhambra towers and Cartagena city hall
  from OSM parts. An nDSM override must match lidar to ≤ 1 m, and footprint IoU must not get
  worse. [F-LANDMARK](active/F-LANDMARK-roofs-and-building-parts.md)
- **Roof error tracking** on the landmark set, against a ≤ 1 m reference (lidar nDSM or a
  surveyed pack such as Old San Juan). [F-LANDMARK §6](active/F-LANDMARK-roofs-and-building-parts.md)
- **Landmark ranking**: in *Find landmarks*, rank by prominence (height, parts, roof detail,
  type), not by category alone. [city guide §2.4](../guides/city-stl-and-puzzle-sop.md)

## Terrain stage, composite and large regions

- **Composite pass 2**: register `esa_landcover` as a server source and add the
  browser-vs-server parity test. Trails no longer need a composite source: they are a City
  Model layer. [F-COMPOSITE3](active/F-COMPOSITE3-server-side-composite.md)
- **Composite design doc**: once F-COMPOSITE3 is done, archive the composite DEM design doc (reference/composite-dem-design.md) to
  history and lift its reasoning into `docs/decisions/composite.md`.
- **Large-region guide §6**, process and pipeline:
  - Composite warnings reach the export and pre-flight (`ExportContext`), plus an
    `X-Composite-Warning` header.
  - Overpass: prefetch lakes when the Region preset is applied (a mirror that failed is
    already tried last, 2026-10-02).
  - Lakes as absolute levels applied after the median on the export grid, so rims are flat too.
  - Pre-flight face estimate: under-counts rugged terrain 1.3–3×; calibrate it.

  [large-region guide §6](../guides/large-region-sop.md)
- **F-TREES** (first): buildings vs trees / viaducts in printed models, inside F-STL2NUMPY
  ([plan](active/F-TREES-buildings-vs-vegetation.md)). Four attempts; the U-Net wins on the 4 packs
  with aligned labels, loses on 7, not shipped. Next step needs clean truth (user to choose). Then the neural proposals F-NN-WATER and
  F-NN-ROOF ([proposals](../proposals.md)).
- **F-STL2NUMPY**: decompose 3D city models into DSM, slope-aware DTM, a building table and
  water/road layers ([plan](active/F-STL2NUMPY-decompose.md)). Includes measuring the mesh
  simplifiers on our own outputs.
- **HydroLAKES** for continent-scale boxes: global lake polygons ≥ 10 ha with surface
  elevation and mean depth (one 820 MB download from data.hydrosheds.org, then offline). Today
  boxes over 500 km² take lakes from the ESA water mask (`geo2stl/water_layers.py::water_mask_lakes`).
- **Large-region guide §6**, UI:
  - Region preset: untick *Water (ESA)* without Earth Engine, set Base 5 mm, and set mm/px from
    the bed.
  - Apply to DEM: list skipped layers in the toast.
  - Terrain puzzle: a *max piece (mm)* option.

  [large-region guide §6](../guides/large-region-sop.md)
- **H5 fallback**: when the local H5 store is absent, fall back to OpenTopography SRTMGL3 or
  Earth Engine (`app/server/config.py`). `geo2stl.dem.default_dem_source` already picks SRTMGL1
  when a key exists.

## Frontend and UX (city workflow)

- **F-DEMID leftovers**:
  - derived-values handles (`POST /api/dem/{id}/values`)
  - remove the settings-derived cache-key fallback and the `_demSettings` DOM fallback
  - port v2's degenerate-grid guard

  [F-FE1](active/F-FE1-vue-consolidation.md)
- **Region rotation** (user, 2026-10-02: after the new-region flow, as its own plan): a rotate
  handle on the box and `rotation_deg` on the region; every layer (terrain, water, satellite,
  hydrology, composite carve, OSM city geometry) fetched over the north-up envelope, rotated and
  cropped identically; scale, puzzle and pre-flight in the rotated frame; an alignment regression test.
- **Rethink progressive disclosure** (user, 2026-10-02): the Beginner / Custom / Everything mode was
  removed; pages now show essentials plus per-section "More settings" switches. Decide whether
  anything replaces the mode (e.g. remembering sections per region, or a single "show all").
- **UI audit 2026-09-30** ([audit](../history/audits/ui-audit-2026-09-30.md)), in priority order:
  1. ~~contrast tokens (`--text-muted` / `--text-dim` fail on the dark theme, down to 1.1:1)~~ (done 2026-10-01);
  2. keyboard: `CollapsibleSection` header as a button, global `:focus-visible`, region list as one tab stop;
  3. shortcuts: ~~page-wide arrow keys~~ (removed 2026-10-01), bare `G`, ~~Esc clears every region box~~ (Esc now cancels drawing / a new region, 2026-10-02), Ctrl+R (Ctrl+2 crash fixed 2026-09-30);
  4. ~~toasts announced, errors persistent, escaped text~~ (done 2026-10-01); validation errors inline still open;
  5. ignored controls: Fit height under Vertical = Auto;
  6. ~~printed-size readout and clearer "model larger than bed"~~ (done 2026-10-01); ~~presets set mm/px from the bed~~ (every new DEM fills the bed; ✨ Make it printable, 2026-10-01);
  7. preview progress with stage and Cancel;
  8. Export sub-tab: pre-flight open, city build primary;
  9. 12 px text floor, 24 px targets;
  10. one term per concept, units in labels (Extrude "Fetch" → "Build" done 2026-10-01);
  11. duplicate ids removed, reduced motion (orphan components removed 2026-09-30);
  12. satellite image aspect (continent detection fixed 2026-09-30).
  13. Edit tab box editor (N/S/E/W, Reload, Map, Save) sits below the window and appears only on
      mouse hover over a 14 px handle: unreachable by keyboard, easy never to find (layout audit).
- **SDK leftovers**:
  - `TerrainSession` still uses the blocking `/api/cities`.
  - `TerrainSession` and `schemas.py` still default `dem_source` to `local`.

  [F-UX part A](done/F-UX-sop-followups.md)
- **Edge-landmark warning** does not re-run when the saved region list changes without a box
  move. [F-UX part A](done/F-UX-sop-followups.md)
- **Pre-flight precision**:
  - face estimates are ±40 %
  - one flow rate for print time
  - the thinnest feature includes slivers clipped at the model edge
  - the preview shows the buildings as a quick view, then the finished City Model (built in
    the background, 75–140 s cold for Philadelphia); the build cannot be cancelled, so a
    changed setting leaves the old build running to its end

  [F-UX part B](done/F-UX-sop-followups.md)
- **Presets and mm/px**: presets do not set mm/px, so City at 1000 px is a 1 m model cut into
  many pieces. [F-UX batch 1](done/F-UX-sop-followups.md)
- **City guide learnings** (2026-09-27 walk-through, [city guide](../guides/city-stl-and-puzzle-sop.md)):
  - *Size first, live pre-flight*: choose the printed size and bed first and derive
    resolution/mm-per-px from it; update the pre-flight live instead of on a button.
  - *Apply to DEM automatically*: an export should carry the current composite without a
    manual Apply (today the Region preset's toast has to tell the user to Apply).
  - *Hidden fetches*: layers the build needs that Load Cities does not fetch (railways, green,
    trails) are fetched during the build with no progress. Fetch them in Load Cities or show
    their progress.
  - *Presets and auto-save*: a preset needs a second click on Load DEM. Make preset → load →
    auto-save one step. (Cosine is the default since 2026-10-05.)
  - *Result summary*: show the `report.json` `check` block (size vs bed, faces, watertight,
    widened/clamped, filament and time) in the UI after a build.
  - *Map rendering*: the 2D map still draws fetched heights, not the user's height overrides.
    It also does not show the layers the build adds.

## Deployment and packaging

From the 2026-10-14 deployment audit. Standalone is about two weeks of work once the shared
prerequisite lands; hosted is two to three months, and its blocker is single-tenancy and
local-filesystem state, not dependency weight.

- [F-DATADIR](active/F-DATADIR-portable-data-dir.md) is the shared prerequisite and also fixes a
  live desktop bug: ~9 GB of the 12 GB cache sits outside any eviction policy and `output/` (1.9 GB)
  has none at all, all inside the OneDrive-synced repository folder. Do this one first regardless of
  which track is wanted.
- [F-DESKTOP](active/F-DESKTOP-standalone-app.md) then needs an entry point, a `numpy2stl` wheel, the
  Vite bundle as package data, and an installer. The F-KEYS Earth Engine sign-in is a hard
  prerequisite for any Earth Engine feature in a packaged build, because an installed app cannot use
  the developer's cached credentials.
- [F-CLOUD](active/F-CLOUD-hosted-multi-user.md) phase 1 overlaps F-DESKTOP's prerequisites. Phases 2
  to 4 (identity, job queue, quotas) only pay off if hosting is actually wanted, and a public
  instance without phase 4 quotas is an open invoice on the paid Google services.

## Architecture (F-ARCH leftovers)

- `numpy2stl.utils.image.engrave_text` (label engraving, still `export._apply_label_engraving`).
  [F-ARCH](active/F-ARCH-consolidation.md)
- `numpy2stl.io.write_mesh` (mesh I/O) and `numpy2stl.utils.cache` do not exist yet.
  [F-ARCH](active/F-ARCH-consolidation.md)
- Remaining burns: `plate_vectors._burn` (cv2 edge rule), roof / city-model burns, and the
  composite line draws. [F-ARCH](active/F-ARCH-consolidation.md)
- Tool dedupes left: `tune_osmnx` and a public affine-decompose helper.
  [F-ARCH](active/F-ARCH-consolidation.md)
- Split `terrain_session.py` (3.2k lines) and `app/server/routers/terrain.py`.
- Break up the long functions named by the 2026-09-17 code-quality audit:
  `_build_and_detect_pano`, `_render_pdf`, `classify_roof_shapes`, numpy2stl `register_global`.
- Pick mkdocs or Sphinx.

## Registration and tooling

- Registration learning plan (L0–L3 gates, `tools/align_tool/eval_registration.py`) is active;
  it moves to `active/` with the reference docs.
- Street placement leftovers (2026-09-14; why in
  [registration-refinement](../decisions/registration-refinement.md)):
  - Lisbon's size 1.125 is confident but unexplained.
  - Woodland on hills reads as built in the plate (Salzburg, the Alhambra). Texture and wood
    masks failed; the next idea is a canopy source (lidar DSM − DTM, or NDVI).
  - `built_mask` drops a low building within 10 m of one twice its height.
  - Re-fetch water with the patched `_geometries`; nine plates have an empty water mask.
  - The on-disk `placement.json` files carry pre-park `unique` values until the next export.
  - Delete `tools/align_tool/data/align_data.js.bak`.

## Skyline

- **Height benchmark on surveyed truth** (before any further height tuning): eight cities,
  lidar + 3D Tiles cross-checked. [F-SKYBENCH](active/F-SKYBENCH-height-benchmark.md)
- **Skyline photos from Wikimedia Commons** with a solved camera (any vantage, not only Street
  View); Miami first, judged on relative heights. [F-WEB2](active/F-WEB2-commons-skyline-photos.md)
- **Research signals into publishing** (floors as a fusion source and check, high-rise flags,
  satellite readings): [F-SKY26](active/F-SKY26-skyline-signals-to-publish.md)
- All skyline open items (F-DET, F-SKY13/16/18, F-SKY5 validation, tests, depth > 1.2 km) are
  kept in one list: [skyline README → Open items](../../city2stl/skyline/README.md#open-items).

## Not pursuing

Dropped by the user on 2026-09-29 ("not interested"). Do not suggest them again unless the
user brings them back; the plan files keep the detail.

- More map projections: F-PROJ-EXPAND phases 2–3 (Mollweide, Robinson, Winkel Tripel, …).
  Phase 1 (Miller, Gall) stays shipped.
- Satellite vegetation as a composite layer (F-COMPOSITE3 pass 3).
- Chasing more survey / surface sources (CNIG 0.5 m, Lisbon, Barcelona ICGC, Salzburg BEV,
  Cartagena de Indias, REDIAM voids).
- Frontend refactor F-FE1 steps 0–6 (Pinia state, one module graph, `window.*` removal):
  internal only, no visible change. The F-DEMID leftovers stay.
- Rivers and lakes as a separate 3MF part. River depth in print mm and a river width floor
  stay (large-region §6).
