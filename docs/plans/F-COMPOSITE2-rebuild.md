# F-COMPOSITE2 — Composite DEM rebuild

**Status: done (2026-07-19).** All 5 items implemented and verified in a
real browser (Playwright, mocked `/api/cities`/`/api/composite/city-raster`
network calls to avoid gating on live Overpass latency for an 18km SF
query) plus 828 backend tests (823 + 6 new coarse-tier tests, net +5 after
one dup-index fix) and 20/20 `tests/e2e/`.

## Post-implementation notes (deviations / findings not anticipated above)

- **Found the actual server was running without `--reload`** (`python -m
  uvicorn app.server.server:app --host 127.0.0.1 --port 9000`, no reload
  flag) — schema/config edits (`CityRequest.detail`,
  `MAX_BBOX_DIAGONAL_KM_COARSE`) silently had no effect until the process
  was manually restarted. First curl test against the "live" server 422'd
  because it was still running pre-edit code. Confirmed via `/openapi.json`
  diff before vs. after restart. No code change from this — just a
  deploy-process note for future sessions on this project.
- **Found and fixed a real bug during the initial `cities.py` edit**: the
  coarse-tier branch computed a locally-resolved `min_area` (raised to
  `COARSE_MIN_BUILDING_AREA_M2`) but the `_fetch_osm_data` call still passed
  the original unresolved `city_req.min_area` — the resolved value was only
  used for the cache-key computation, so coarse requests would have cached
  correctly but fetched with the wrong (small) area filter every time,
  silently returning small buildings that should have been excluded. Fixed
  by using the resolved `min_area` in both places. Caught by the deeper
  pytest test that asserts on the actual argument the mock received, not
  just the response status.
- **Playwright verification against a real 18km live Overpass query is
  impractically slow** for CI-style verification (multi-minute, and SF is
  one of the densest OSM regions on earth) — switched to `page.route()`
  interception of `/api/cities` and `/api/composite/city-raster` with a
  realistic mock response. This still exercises 100% of the real
  client-side logic (which tier gets requested, which layers get dropped,
  toggle/histogram/split-view behavior) — only the live network round-trip
  to Overpass itself is stubbed, and that server-side behavior is already
  covered by `tests/test_cities.py`'s mocked pytest suite.
- **Scope note**: `_cityContributionFromRaster` and the new
  `_cityComponentContributions` do overlapping work (recombine the same 4
  arrays two different ways) — acceptable for now since both are O(n)
  single passes and the code stays readable; would be worth collapsing into
  one function if a future change needs a third view of the same data.

User-requested 2026-07-19, follow-up to the `esa_values_b64` land-cover fix
(same session). While verifying that fix the user asked for a broader
rebuild of the composite panel:

1. Make the base DEM itself a toggleable/weighted contribution (currently
   it's an unconditional starting point, not a channel).
2. Every layer should be able to "try to contribute some channels" — split
   the single City/OSM `cityWeight` into independent buildings/roads/
   waterways/walls toggles + weights.
3. Histograms for each layer (contribution distribution) — both per-layer
   and one combined view.
4. SF test region (~18.3 km diagonal) only ever showed tree height, never
   buildings — root-caused to `CITY_MAX_DIAG_KM = 10` (client) and
   `MAX_BBOX_DIAGONAL_KM = 15` (server) silently gating out all OSM
   building/road/wall data above those sizes, while land cover has no such
   gate. User decision: keep the existing detailed-building tier at 10 km,
   add a second coarser tier up to 25 km for "more reasonable" requests
   (major roads + water + large buildings only, no walls/small-building
   detail).
5. Split view: composite DEM next to the satellite image, synced pan/zoom.

## Design decisions (resolved via AskUserQuestion 2026-07-19)

- City gate: **two tiers**. Tier 1 (existing) ≤10 km — full per-building
  detail (buildings + walls + roads + waterways), unchanged. Tier 2 (new)
  10–25 km — roads + water + large buildings only (area-thresholded, e.g.
  ≥2000 m² footprint), no walls, no small-building detail.
- City sub-layers: **independent toggle + weight** per component
  (buildings, roads, waterways, walls) inside the City contribution group,
  replacing the single `cityWeight` slider.
- Histograms: **both** — a small inline canvas histogram under each
  contribution group (water/city-sub/landcover/satellite/DEM) showing that
  layer's raw height-delta distribution, AND one combined histogram of the
  final composite output.
- Split view: basic dual-pane (composite | satellite), independently
  rendered canvases with synced `stackZoom` pan/zoom — not a full redesign
  of the stacked-layers architecture, just a second concurrent render
  target.

## Approach

### 1. DEM as a toggleable channel (`composite-dem.js`)
- Add `demEnabled` (bool) + `demWeight` (default 1.0) to `params`/`DEFAULTS`.
- `computeCompositeDem()`: when `demEnabled` is false, start the composite
  from an all-zero baseline (same shape) instead of `values`; when true,
  scale the base DEM contribution by `demWeight` before adding deltas ontop
  (`composite[i] = demEnabled ? values[i]*demWeight : 0`).
- Add a DEM group to `CompositeDemSection.vue` (toggle + weight slider),
  mirroring the existing per-layer groups.

### 2. City sub-layer toggles (`composite-dem.js`, `cities.py` unaffected)
- `_fetchCityRaster` already returns separate `buildings/roads/waterways/
  walls` arrays — only `_cityContributionFromRaster` currently collapses
  them with one scale/cut/depth set gated by one `cityWeight`.
- Replace `cityWeight` with four independent enable+weight pairs:
  `buildingsEnabled/buildingScale`, `roadsEnabled/roadCut`,
  `waterwaysEnabled/riverDepth`, `wallsEnabled/wallScale` (new — walls
  currently reuse `buildingScale`, split it out).
- `_cityContributionFromRaster` sums only the enabled components at their
  own weight; still a single city-raster fetch (no extra network cost).
- Vue: replace the single "Weight" slider in the City/OSM group with four
  toggle+slider rows.

### 3. Two-tier city size gate
- Client (`city-overlay.js`, `model-viewer.js`): keep `CITY_MAX_DIAG_KM =
  10` for the existing detailed `loadCityData()` path (unchanged — still
  fetches buildings/roads/waterways/walls in full via `/api/cities`).
- Add `window.CITY_COARSE_MAX_DIAG_KM = 25` and a new coarse fetch path
  (reuses `/api/cities` with a new `detail: "coarse"` request field, OR a
  server-side min-area filter param — see below) that's used when
  `10 < diagKm <= 25`.
- Server (`cities.py` `/api/cities`, `schemas.CityRequest`): accept an
  optional `detail: Literal["full","coarse"] = "full"` field. When
  `"coarse"`: raise `MAX_BBOX_DIAGONAL_KM` cap to 25 km for this request
  only (pass an explicit `max_km=25` to `validate_bbox_diagonal`), restrict
  fetched `layers` to `["roads", "waterways", "buildings"]` (no walls), and
  post-filter buildings to `area_m2 >= COARSE_MIN_BUILDING_AREA_M2` (new
  config constant, default 2000) before caching/returning. Cache key must
  include `detail` so full/coarse results don't collide.
- `_fetchCityRaster` (composite-dem.js) passes the resolved tier through so
  the raster endpoint / OSM-cache lookup uses the matching cache entry.

### 4. Histograms
- New shared helper `_renderHistogram(canvas, values, {bins=32})` in
  composite-dem.js: computes a simple binned count over the array, draws
  bars via canvas 2D (no chart library — consistent with the "no comments
  unless non-obvious" / no new deps style already in this file).
- Call it for each computed feature (`waterFeat`, city sub-arrays,
  `lcFeat`, `satFeat`, base `values` scaled by `demWeight`) into small
  `<canvas>` elements added under each contribution group in
  `CompositeDemSection.vue`, plus one larger canvas for the final
  `composite` array under the existing stats line.
- Recompute alongside the existing debounced `_scheduleRecompute()` — no
  new timer.

### 5. Split view (composite | satellite)
- `stacked-layers.js` currently renders one active mode into `#layersStack`.
  Add a new mode `'CompositeSplit'`: renders two canvases side by side in a
  flex container (`#layersStack` gets a `.split-view` class), left =
  CompositeDem canvas, right = SatImg canvas, both driven by the same
  `stackZoom` transform so pan/zoom stays in sync (they already share the
  same CSS-transform mechanism — just apply it to both canvases instead of
  one).
- Add a "Split view" toggle button near the existing Preview/Apply buttons
  in `CompositeDemSection.vue` that calls
  `window.setStackMode('CompositeSplit')`.

## Target files
- `app/client/static/js/modules/layers/composite-dem.js` (DEM toggle, city
  sub-toggles, histograms)
- `app/client/static/js/modules/layers/city-overlay.js` (coarse-tier fetch
  path, `CITY_COARSE_MAX_DIAG_KM`)
- `app/client/static/js/modules/layers/stacked-layers.js` (split-view mode)
- `app/client/static/js/vue/components/dem/CompositeDemSection.vue` (all
  new UI: DEM group, city sub-toggle rows, histogram canvases, split-view
  button) — requires `npm run build`
- `app/server/routers/cities.py`, `app/server/schemas.py` (`detail` field,
  coarse filtering)
- `app/server/core/validation.py` / `app/server/config.py`
  (`COARSE_MIN_BUILDING_AREA_M2`, coarse max-km wiring)
- `tests/test_composite.py`, `tests/test_cities.py` (coarse-tier tests)
- `tests/e2e/` (split-view + histogram smoke test)

## Success criteria
- DEM toggle off → composite is pure sum of enabled contributions, no base
  elevation.
- Disabling e.g. `roadsEnabled` removes only the road cut from the
  composite while buildings/waterways/walls still contribute.
- A region >10km and ≤25km (like the SF test bbox) now shows major roads,
  water, and large buildings in the composite even though the detailed
  10km gate still blocks small-building/wall detail.
- A region >25km still cleanly shows "region too large" rather than
  silently omitting city data.
- Histograms render and visibly change when weights/toggles change.
- Split view shows composite and satellite panes that pan/zoom in lockstep.

## Risks
- Coarse-tier caching: must not collide with existing full-detail cache
  entries for the same bbox (cache key needs the `detail` discriminator).
- Split view doubles per-frame canvas draw cost — verify no jank on
  pan/zoom before calling it done.
