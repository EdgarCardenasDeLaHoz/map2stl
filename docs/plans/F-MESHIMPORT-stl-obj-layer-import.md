# F-MESHIMPORT — Import STL/OBJ as a registered layer

**Status: done (2026-07-19), extended same-day with an "Auto" mode.** Full
manual flow (upload/library select → heightmap preview → side-by-side manual
registration → Apply to DEM) verified live in a real browser and covered by
`tests/test_mesh_import.py` (24 tests) + `tests/e2e/test_mesh_import.py` (3
tests, strict console-error gate). Auto mode (geocode filename → automatic
OSM registration → match/create region → open picker pre-filled for
confirmation) added same-day per user request, along with two real bugs
found and fixed in the underlying `numpy2stl.registration` algorithm — see
"Auto mode" section below.

## Auto mode (added 2026-07-19, same day as the base feature)

User asked to try running the existing *automatic* registration pipeline
(`numpy2stl.registration`, the one already flagged as "has not worked well")
as an "auto mode": given an uploaded/library mesh, automatically determine
its real-world location, register it, and match/create a saved region —
always handing off to the manual picker afterward rather than silently
trusting the result.

**What "auto mode" can and can't do, concretely:**
- The only usable location signal for pre-made mesh sets (e.g. the
  micropolitan STL packs) is the **filename/foldername** — checked real STL
  files (Miami, Barcelona, Paris, etc.): 80-byte headers are all zero, no
  companion metadata files, no OBJ files in the set at all. No embedded
  georeferencing exists to parse.
- `parse_city_name_from_path()` (`core/mesh_import.py`) parses a city-name
  string from a library rel_path's parent folder (e.g.
  `"Miami,_FL_-_L_&_XL/Miami, FL_L_Solid.stl"` → `"Miami, FL"`) or an
  uploaded filename, then geocodes it via
  `numpy2stl.applications.cities.get_city_bbox` (osmnx/Nominatim under the
  hood — confirmed installed in the venv despite being commented out in
  `requirements.txt`).
- `numpy2stl.registration.pipeline.register_city_stl` always needs a
  *known* city name/bbox already — there is no "search the whole world
  blind" capability to build on. It fetches OSM building data for that
  city, then registers the STL against it via 2D affine search.

**Two real bugs found and fixed in `numpy2stl/registration/align/global_search.py`**
(found by running the pipeline against a real Miami STL and inspecting the
diagnostic plots — `scale_sweep.png`/`rot_sweep.png`/`mask_overlay.png` from
the HTML report were essential here):

1. **Scale selection picked the wrong signal.** With no geometric scale
   anchor (true for every city except the 4 hardcoded in
   `_CITY_CONFIG`), scale was chosen by the peak of raw-heightmap
   cross-correlation (`scale_metrics[k][2]`) — dominated by absolute height
   magnitude, nearly flat/noisy across scale for Miami, and the "peak"
   landed on the search-range boundary (1.5x) rather than a real optimum.
   Footprint Dice/edge-IoU (`scale_metrics[k][0]`/`[1]`) had a clean, sharp
   peak at the true scale (~0.75-0.85x) the entire time. **Fix**: prefer the
   Dice-peak scale when it clears a sharpness margin over the sweep's own
   median (`_DICE_PEAK_MARGIN = 0.10`) and isn't at the sweep boundary;
   otherwise fall back to the old xcorr-peak behavior unchanged.
2. **Rotation refinement overrode a correct histogram estimate with noise.**
   The line-angle histogram estimate was already correct (its overlay curve
   matched OSM's dominant wall-angle peak almost exactly — visible in
   `angle_hist.png`), but the full-frame edge-IoU sweep across Miami's
   dense, near-uniform building grid produced several comparably-tall,
   noisy local maxima a few degrees apart (`rot_sweep.png`) — the old fixed
   `_ROT_IOU_MARGIN = 0.02` let a spurious ~20° "improvement" win. **Fix**:
   additionally require the winning rotation candidate to clear the sweep's
   own median by `_ROT_IOU_MARGIN` → `_ROT_PEAK_MARGIN = 0.10` (a
   peak-sharpness/isolation check, same idea as the scale fix), not just
   beat the histogram pose by the old fixed margin.

Both fixes are additive/guarded (fall back to the pre-existing behavior when
the new sharpness check doesn't clear), validated against the full
`numpy2stl` registration test suite (159 tests, zero regressions) plus two
new targeted regression tests
(`numpy2stl/tests/test_registration_register.py::TestScaleSelectionRobustness`,
`::TestRotationRefinementRobustness`) that exercise the exact decision logic
changed. Visually confirmed via the Miami HTML diagnostic report: the
"STL Aligned" panel went from a plausible-looking-but-wrong-orientation grid
to correctly matching OSM's real block/street pattern.

**New surface added:**
- `core/mesh_import.py`: `parse_city_name_from_path`, `auto_register`,
  `auto_register_upload`, `auto_register_library` — all guarded behind
  `_AUTO_REGISTER_AVAILABLE` (same delegation pattern as the rest of the
  file), degrading to `status: "unavailable"` rather than raising when
  `numpy2stl.registration.pipeline`/`applications.cities` can't import.
- `routers/regions.py`: `_bbox_iou`, `find_overlapping_region`,
  `_unique_region_name`, `find_or_create_region_for_bbox` — bbox-overlap
  region matching (IoU ≥ 0.5 default) reused by auto-register so repeated
  imports of the same city don't create duplicate regions; falls back to
  creating a new region (de-duplicating the name, e.g. `"Miami, FL (2)"`)
  when no existing region overlaps enough.
- `POST /api/layers/mesh/{upload_id}/auto-register` and the
  `library/{rel_path:path}/auto-register` equivalent. Always returns 200 —
  the `status` field (`"ok" | "geocode_failed" | "unavailable"`) tells the
  caller what happened; `confidence` is explicitly documented as an
  uncalibrated raw xcorr peak, not a pass/fail gate.
- `window.autoRegisterMesh()` (`mesh-layer.js`): calls the API, sets
  `appState.selectedRegion`/`currentDemBbox` to the matched/created region
  (mirrors the minimum state `regions.js`'s `selectCoordinate()` sets, not
  its full side-effect chain), calls `loadDEM()` then
  `computeMeshHeightmap()`, then **always** opens the manual picker
  (`openMeshRegistrationModal()`) rather than calling
  `applyMeshRegistration()` directly — auto mode is a smart starting point
  for the manual flow, never a silent auto-accept.
- "🤖 Auto (geocode + register)" button in `MeshImportSection.vue`, with a
  status line surfacing city name, confidence, footprint IoU, RMSE, and
  whether a region was matched or created.

**Bug found and fixed while wiring the UI**: `autoRegisterMesh()` initially
forwarded its own `opts` (containing `resolution`, the *OSM raster*
resolution parameter) straight into `computeMeshHeightmap(opts)`, which
reads a *different* field (`resolutionM`, the mesh heightmap's m/px) — the
name collision meant `computeMeshHeightmap` silently fell back to its 5m
default, which the grid-size guard then rejected for a full city bbox (400
error, and the registration modal never opened since it requires a
successfully computed heightmap). Fixed by giving `computeMeshHeightmap` its
own bbox-aware resolution default (`window.suggestedMeshResolutionM`,
promoted out of `MeshImportSection.vue` into `mesh-layer.js` so both the UI
slider and any non-UI caller like auto mode share one calculation) instead
of relying on the caller to pass the right one.

**Verified end-to-end against the real Miami micropolitan STL** (941,630
faces): FastAPI `TestClient` integration test, a full manual Playwright
browser run (screenshot: registration modal opens with DEM and mesh
heightmap panels showing visually matching block/street patterns), and an
opt-in e2e test (`tests/e2e/test_mesh_import.py::test_auto_register_geocodes_and_opens_prefilled_picker`,
gated behind `STRM2STL_RUN_SLOW_NETWORK_TESTS=1` — real network + ~90-200s+
even with a warm cache, since `live_server_url` intentionally gives each
test session a fresh, isolated `STRM2STL_CACHE` dir per `conftest.py`'s own
docstring, so real mesh imports always pay a cold ray-cast in e2e).

**Known follow-up, not fixed in this pass**: `auto_register()` and the
client's separate `/heightmap` call each independently ray-cast the same
STL from scratch — `register_city_stl`'s internal `stl_heightmap` (on its
own square `resolution`-parameter grid) isn't reused by
`compute_library_heightmap`'s bbox-derived grid (different shape/cache key).
Measured cost: ~35s per cold ray-cast against the real Miami mesh, paid
twice per auto-mode run (~70s of pure redundant ray-casting on top of the
OSM fetch). Reusing the pipeline's internal heightmap would roughly halve
cold-cache auto-mode latency, but requires matching the two code paths'
grid conventions carefully to avoid a subtle misalignment bug — deferred as
its own follow-up rather than rushed.

User-requested (2026-07-19): bring `city2stl`'s STL/OBJ import
(`stl_to_heightmap`) into the web app as a real layer, and fix the weak
point in the existing (automatic, edge-IoU) registration by adding a manual
side-by-side control-point picker to establish ground truth.

## Post-implementation notes (deviations / findings not anticipated in the plan below)

- **Mid-turn scope addition**: user asked for a mesh *library* (browse
  pre-existing STL sets, e.g. a commercial "micropolitan" city pack, with no
  embedded georeference) in addition to ad-hoc upload. Added
  `config.MICROPOLITAN_STL_DIR` (env `STRM2STL_MICROPOLITAN_DIR`, default
  `../Cities/micropolitan/_extracted`, relative to `Code/`), per-city
  location sidecars (`<file>.location.json`, shared bbox across
  Solid/Water/print-bed-tile siblings in the same folder), and a disk-cached
  library heightmap/register route family
  (`/api/layers/mesh/library/{rel_path:path}/...`).
- **Found and fixed a latent bug**: `city2stl/skyline/height/stl_import.py`'s
  `stl_to_heightmap` always returned an all-NaN heightmap —
  `np.maximum.at()` was accumulating into a NaN-filled array, and
  `np.maximum(nan, x)` is always `nan`. Existing tests had two vacuous
  `if mask.any():` guards that hid this for years. Fixed (accumulate into
  `-inf`, convert back to `nan` after) and hardened the two tests. See
  `docs/issues.md`.
- **Found and fixed a real perf/hang bug of our own**: a naive resolution
  default (5m/px) combined with a typical region-sized bbox (e.g. ADKs,
  ~0.5°) produces a 100M+ pixel ray-cast grid — observed hanging
  indefinitely in manual browser testing. Added a server-side grid-size
  guard (`core/mesh_import._check_grid_size`, caps at `config.MAX_DIM`
  =2000px/side, same limit every other raster endpoint uses) that rejects
  fast with a clear message instead of hanging, plus a client-side
  `_suggestedResolution()` in `MeshImportSection.vue` that seeds a sane
  default from the current DEM bbox (target ~300px/side — ray-casting cost
  scales with pixel count and is not fast even for simple meshes: ~13s for
  a 583K-pixel grid against a 12-face test box).
- **sys.path ordering gotcha**: `numpy2stl` (needed for the registration
  warp) lives in `Code/`, a sibling of `strm2stl/`, not inside it.
  `server.py`'s sys.path bootstrap runs *after* router imports at module
  load time, so a module-level `numpy2stl` import in `core/mesh_import.py`
  failed silently (caught by the `_AVAILABLE` guard) until routed through
  `routers/layers.py`. Fixed with the same local `sys.path.insert` guard
  `core/export.py` already uses for the same reason.
- **Registration is upload_id/rel_path-keyed, not bbox-keyed**: the
  `/register` endpoint needs the *last computed* heightmap for a given
  mesh source, tracked via a small `last_heightmap.npy`/`last_mask.npy`
  cache per upload dir (or a hashed per-rel_path "library session" dir).
  Recomputing the heightmap (e.g. changing resolution) invalidates it —
  by design, matches the "pairs invalidated on recompute" behavior.
- **Registration UI does not reuse `stacked-layers.js`'s pan/zoom.** Built
  a small dedicated implementation in `mesh-registration.js` instead of
  generalizing `enableStackedZoomPan()`, which is hard-wired to
  `#layersStack` — avoided touching a working, load-bearing function for a
  one-off two-canvas modal.
- **`window.appState.meshImport` reactivity gap**: it's a plain object
  mutated by `mesh-layer.js` outside Vue's reactivity, so a naive
  `computed()` in `MeshImportSection.vue` reading it never re-evaluated
  (button stayed disabled after upload). Fixed by tracking `hasSource` as a
  local `ref()` set directly by the upload/select handlers, and by firing a
  `mesh-import-registered` `window` `CustomEvent` (rather than polling) so
  the Vue component can react to registration completing.

## Goal

Let a user upload an STL/OBJ mesh, see it as a heightmap, manually register
it against the current DEM/city view by clicking matching point pairs, then
either display it as a toggleable stacked layer or merge it into the DEM
(patch DEM heights in the overlap region), reusing the existing layer and
composite-DEM UI patterns.

## Decisions (from user Q&A, 2026-07-19)

- Registration UI: **two side-by-side pan/zoom canvases** (reference DEM+city
  view on the left, imported mesh heightmap on the right). Click a point on
  each side to add a pair; 3+ pairs; "Compute Registration" fits a full 2D
  affine (translate + rotate + scale) by least squares. Not a draggable-pin
  overlay, not rigid-only.
- Scope: build **both** the visual stacked layer (toggle/opacity/reorder,
  like `Sat`/`CityRaster`) **and** the DEM-merge action ("Apply to DEM",
  mirroring `composite-dem.js`'s `applyCompositeToDem`) in this pass — not
  phased.
- Placement: new collapsible step in the **Edit tab**, alongside the other
  DEM/layer sections (`CompositeDemSection.vue`, `WaterSection.vue`, etc.),
  not a standalone modal.
- Reference-side canvas shows the **current DEM hillshade + city overlay**
  (whatever Edit tab is currently rendering), not satellite imagery.
- Upload: **STL + OBJ only, 200 MB cap.**
- Transform: **reuse `numpy2stl.registration.align.transform.apply_transform`**
  (`cv2.warpAffine`-based, NaN-aware) rather than writing a local warp.

## Why manual registration, not the existing automatic path

`numpy2stl/registration/pipeline.py:register_city_stl` already does
automatic 2D affine/similarity search via edge-IoU against OSM building
heights — but it's a standalone CLI/report tool, never wired to the app, and
per the user this general approach "has not worked well" as ground truth.
`city2stl/skyline/height/stl_import.py:stl_to_heightmap` maps the mesh's
bounding box **linearly** onto a caller-supplied geographic bbox — i.e. no
real georeferencing happens at import time, just a guess. A manual
control-point picker sidesteps both problems: the user supplies the ground
truth directly instead of relying on automatic feature matching.

## Approach

### 1. Backend — upload + heightmap conversion

- New `app/server/core/mesh_import.py`: delegates to
  `city2stl.skyline.height.stl_import.stl_to_heightmap` and
  `city2stl.skyline.height.infill.infill_idw`/`infill_nearest`, following the
  existing `_XXX_AVAILABLE` guarded-import delegation pattern (see
  `docs/arch.md` "numpy2stl Delegation Pattern", also used in
  `city2stl/mesh.py`). Persist uploaded mesh files under
  `cache/mesh_imports/<upload_id>/` (gitignored `cache/`, matches existing
  cache dir convention) — keep original file, computed heightmap (`.npy`),
  and mask.
- New `app/server/routers/layers.py`:
  - `POST /api/layers/mesh/upload` — multipart `UploadFile`, format
    allow-list (`.stl`, `.obj`), 200 MB size cap enforced via
    `Content-Length` check + streamed read abort, returns `{upload_id,
    filename, mesh_bounds}`.
  - `POST /api/layers/mesh/{upload_id}/heightmap` — body: `bbox`,
    `resolution_m`, `up_axis`, infill options. Calls `mesh_import.py`,
    caches result, returns the heightmap as a base64 PNG (or reuses the
    existing terrain raster response shape used by `/api/terrain/dem` —
    check `core/responses.py` for the established raster-response helper
    before inventing a new shape) plus `{width, height, min, max,
    valid_pct}`.
  - `POST /api/layers/mesh/{upload_id}/register` — body:
    `point_pairs: [{ref: [x,y], mesh: [x,y]}, ...]` (pixel coords in each
    canvas's own space, plus each canvas's known dimensions so the router
    can convert to a consistent coordinate frame). Fits a 2×3 affine via
    least squares (`cv2.estimateAffinePartial2D` if available for a
    robust/RANSAC fit, else a plain `numpy.linalg.lstsq` solve — decide at
    implementation time based on what's already a dependency; `cv2` is
    already required by `numpy2stl.registration`). Applies it with
    `numpy2stl.registration.align.transform.apply_transform`. Returns the
    warped heightmap + `{rms_residual_px, per_pair_residuals}` so the UI can
    show fit quality.
  - Validate `bbox`/`point_pairs` shapes with new Pydantic models in
    `app/server/schemas.py` (matches existing pattern — all routers use
    schemas.py models, per `docs/api.md`).
- Add these routes to `docs/api.md` under a new "Mesh Import / Layer Routes
  (`routers/layers.py`)" section, following the existing table format.

### 2. Frontend — upload + heightmap preview

- New `app/client/static/js/modules/layers/mesh-layer.js` (vanilla JS
  module, `window.*` exports per `docs/state.md`/editing rule 3):
  - `window.uploadMeshLayer(file)` — posts to the upload route, stores
    `{upload_id, filename}` on `window.appState.meshImport`.
  - `window.computeMeshHeightmap(opts)` — posts to the heightmap route,
    decodes the raster into an offscreen canvas, stores it as
    `window.appState.meshSourceCanvas` (mirrors `satImgSourceCanvas` /
    `compositeDemSourceCanvas` naming from `docs/state.md`).
  - Register the new layer with the existing stacked-layer engine in
    `stacked-layers.js`: add `MeshImport` to `_layerOrder`,
    `LAYER_CANVAS_IDS`, `_layerOpacities` default, and a `sourceMap` entry
    `MeshImport: () => window.appState?.meshSourceCanvas` in
    `updateStackedLayers()`.
- New `app/client/static/js/vue/components/dem/MeshImportSection.vue`
  (mirrors `CompositeDemSection.vue`'s structure — `CollapsibleSection`
  wrapper, plain `window.*` calls in `<script setup>`, `onMounted` wiring):
  - File input (`.stl,.obj` accept), upload button, filename/size/status
    display.
  - `up_axis` selector (x/y/z/-x/-y/-z, matches `stl_to_heightmap`'s
    supported values), resolution input.
  - "Preview Heightmap" button → calls `computeMeshHeightmap`, shows a small
    thumbnail (same `_updatePreviewThumb` idea as composite-dem.js) and a
    validity-% stat.
  - "Register…" button → opens the registration picker (below).
  - Once registered: opacity slider + on/off toggle row added to
    `LayerViewSection.vue`'s `layerRows` (new `MeshImport` row, plus adding
    `'MeshImport'` to the `layers` array in that component's `onMounted`
    wiring loop), and a layer-mode button in `layerModeSelector`.
  - "✓ Apply to DEM" button (see Merge section below).

### 3. Frontend — manual registration picker

- New `app/client/static/js/modules/layers/mesh-registration.js`:
  - Renders two `<canvas>` elements side by side in a modal/panel opened
    from "Register…": left = current DEM render (reuse the existing DEM
    canvas pixels, e.g. snapshot `window.appState.lastDemData`/hillshade
    canvas + city overlay composited, not a live re-render) at a fixed
    size; right = the mesh heightmap canvas from step 2, colorized similarly
    (reuse whatever colormap helper the DEM canvas uses, e.g.
    `dem-loader.js`'s LUT, so both sides are visually comparable).
  - Independent pan/zoom per canvas (reuse the wheel/drag pattern from
    `stacked-layers.js`'s `enableStackedZoomPan`, factored or duplicated
    minimally — check if it's easily parameterized over a target canvas
    before duplicating).
  - Click-to-place: click left canvas places/updates the pending pair's
    reference point (numbered marker, e.g. "3"); click right canvas
    completes pair 3. A pair list UI shows each pair with an "×" to remove
    and highlights numbers on both canvases so mismatched pairs are easy to
    spot.
  - "Compute Registration" (enabled at 3+ pairs) posts pairs + both canvas
    dimensions to `POST /api/layers/mesh/{upload_id}/register`, receives
    the warped heightmap + RMS residual, updates
    `window.appState.meshSourceCanvas` with the warped result, shows the
    residual (flag visually if e.g. > some threshold like 15px — pick a
    number empirically once real data is available, don't hard-code a false
    precision).
  - "Undo last pair" / "Clear all" controls.
  - Persist `point_pairs` + last `upload_id` in `window.appState.meshImport`
    so re-opening the picker (e.g. after adjusting resolution and
    recomputing the heightmap) doesn't lose prior clicks — the pairs are in
    normalized/geo-ish space if possible, but simplest correct approach is:
    invalidate stored pairs whenever the heightmap is recomputed (different
    pixel grid), and tell the user why via a toast.

### 4. Merge into DEM

- `window.applyMeshToDem()` in `mesh-layer.js`, modeled directly on
  `composite-dem.js:applyCompositeToDem` (`app/client/static/js/modules/layers/composite-dem.js:405-426`):
  read `window.appState.lastDemData`, blend the registered mesh heightmap
  into `dem.values` only where the mesh's valid-mask (post-warp) is true,
  using a per-pixel replace-or-blend policy — default to **replace** (mesh
  wins where valid, same as how composite layers patch DEM) with a
  "blend weight" slider (0-1, lerp) for the case where the mesh is a rough
  supplement rather than authoritative, since a hard replace can produce
  visible seams at the mask boundary. Reuses `window.recolorDEM?.()` and
  `window.showToast?.()` afterward, same as the composite pattern.
- No new server route needed for this step — it's a pure client-side array
  patch like the composite DEM apply, since both `dem.values` and the
  warped mesh heightmap are already in the browser after the register call.

### 5. Tests

- Python unit tests for `core/mesh_import.py` (upload validation, size cap,
  format allow-list, heightmap caching) — `tests/test_mesh_import.py`,
  following `tests/conftest.py` fixtures.
- Router tests for the three new endpoints (upload happy path, oversized
  file rejected, bad format rejected, register with <3 pairs rejected,
  register with valid pairs returns expected residual shape) in the same
  file or `tests/test_routers_layers.py` per existing per-router test file
  convention (check naming convention against existing `tests/` files
  before creating).
- **Playwright e2e** (`tests/e2e/test_mesh_import.py`, using the
  `strict_page`/`live_server_url_testmode` fixtures from
  `tests/e2e/conftest.py`, same style as `test_interactions.py`):
  1. Upload a small fixture STL (add a tiny synthetic test fixture, e.g. a
     unit cube or simple terrain-like STL generated in a `conftest.py`
     fixture or checked into `tests/e2e/fixtures/`) via the Edit tab's new
     Mesh Import section; assert the upload response and that a heightmap
     preview renders (canvas has non-empty pixel data or a status text
     changes).
  2. Open the registration picker; simulate 3 point-pair clicks on
     known/deterministic canvas coordinates (test-mode DEM is a
     deterministic gradient, per `live_server_url_testmode`, so exact pixel
     expectations are possible); click "Compute Registration"; assert the
     `/api/layers/mesh/{id}/register` response is 200 and the residual is
     reported in the UI.
  3. Toggle the new `MeshImport` layer on/off via `layerModeSelector` and
     confirm the stacked canvas updates (e.g. via the existing
     `updateStackedLayers` hook or a visible opacity-row assertion, matching
     how `test_workflow_tab_navigation` asserts on DOM state rather than
     pixels where possible).
  4. Click "Apply to DEM" and assert `window.appState.lastDemData` changed
     (e.g. via `page.evaluate`) or that a success toast appears.
  - All under the `strict_page` console-error gate, per existing e2e
    convention — any JS error during the flow fails the test.

## Target files

- `app/server/core/mesh_import.py` (new)
- `app/server/routers/layers.py` (new) + registered in `app/server/server.py`
- `app/server/schemas.py` (new Pydantic models: mesh upload response,
  heightmap request/response, register request/response)
- `app/client/static/js/modules/layers/mesh-layer.js` (new)
- `app/client/static/js/modules/layers/mesh-registration.js` (new)
- `app/client/static/js/modules/layers/stacked-layers.js` (add `MeshImport`
  to `_layerOrder`, `LAYER_CANVAS_IDS`, `_layerOpacities`, `sourceMap`)
- `app/client/static/js/vue/components/dem/MeshImportSection.vue` (new)
- `app/client/static/js/vue/components/dem/LayerViewSection.vue` (add
  `MeshImport` row + layer-mode button)
- `app/client/static/js/vue/components/views/DemContainer.vue` (mount the
  new section — check current mount order before editing)
- `docs/api.md` (new route section), `docs/modules.md` (new module
  entries), `docs/state.md` (new `appState.meshImport` /
  `meshSourceCanvas` keys)
- `tests/test_mesh_import.py` / `tests/test_routers_layers.py` (new)
- `tests/e2e/test_mesh_import.py` (new) + a small fixture STL under
  `tests/e2e/fixtures/`

## Success criteria

- User can upload an STL or OBJ (≤200 MB) from the Edit tab and see a
  heightmap preview without touching a notebook.
- User can open a side-by-side picker, click ≥3 matching point pairs
  between the current DEM/city view and the mesh heightmap, and get a
  fitted affine registration with a visible fit-quality (RMS residual)
  readout.
- The registered mesh appears as a normal toggleable/opacity-adjustable
  stacked layer (`MeshImport`), reorderable like existing layers.
- "Apply to DEM" patches `lastDemData.values` in the mesh's footprint and
  the change is reflected in the DEM canvas and downstream export/3D
  preview, exactly as `applyCompositeToDem` does today.
- New Playwright e2e test exercises upload → register → toggle layer →
  apply-to-DEM end to end under the strict console-error gate, and passes
  alongside the existing 651+ pytest suite.
- `docs/api.md`, `docs/modules.md`, `docs/state.md`, `docs/issues.md` (if
  any known limitation surfaces, e.g. affine-only registration can't
  correct for non-linear mesh distortion) updated per the Documentation
  Update Checklist in `CLAUDE.md`.

## Known risks / open implementation questions

- **Raster response shape** — RESOLVED: reused `geo2stl.dem.make_dem_payload`'s
  scheme (base64 little-endian float32, `{name}_values_b64` field) for
  `mesh_values_b64`; added a parallel `mesh_mask_b64` (packed uint8) for the
  validity mask, since DEM responses don't need one.
- **DEM+city reference snapshot** — RESOLVED, simpler than anticipated: the
  reference side renders `window.appState.lastDemData.values` directly via
  the shared `renderDEMCanvas()` helper — no city-overlay compositing. If a
  future pass wants OSM buildings visible in the reference picker for
  better landmark matching, that's a separate follow-up, not required for
  registration to work.
- **cv2 dependency** — RESOLVED: `opencv-python` was already in
  `requirements.txt` (dependency of `numpy2stl.registration`), so no new
  hard dependency was added. Ran into a *different*, unanticipated
  `numpy2stl` import-ordering issue instead — see "Post-implementation
  notes" above (sys.path ordering gotcha).
- **Affine-only limitation** — still true, deliberately out of scope. A
  global 2D affine can't correct local mesh distortion. Not flagged in
  `docs/issues.md` since it hasn't been observed as a problem in practice
  yet (no TPS/piecewise warping built preemptively).
- **Coordinate frame for point pairs** — RESOLVED as planned: the client
  (`mesh-registration.js`) undoes pan/zoom before recording a pair (`
  _nativePointFromEvent`), so the server always receives canvas-native
  (unzoomed) pixel coordinates and never needs to know about viewport state.
- **Grid-size / performance** (not anticipated in the original plan): ray-
  casting cost scales with pixel count and mesh face count, not just
  per-pixel resolution — see "Post-implementation notes" above. A
  region-sized bbox at a naive default resolution can produce a 100M+ pixel
  grid; addressed with a server-side `MAX_DIM` guard and a client-side
  bbox-aware default resolution. For meshes with many more faces than the
  synthetic test box (e.g. real micropolitan city packs), even a
  ~300px/side grid may be slow — no batching/vectorization improvement was
  made to `stl_to_heightmap`'s ray-cast itself in this pass; revisit if
  real-world mesh previews prove too slow in practice.
