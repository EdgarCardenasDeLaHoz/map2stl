# F-PROJ-DIMS — Variable-dimension projection output (maintain_dimensions default off)

**Status: done (2026-07-19).** Implemented in one pass per user instruction
("execute the plan, test it, don't leave planned work for later"). Verified
via Playwright (aspect ratio genuinely changes between projections by
default; `maintain_dimensions=True` opt-in keeps shape fixed; DEM and water
mask stay aligned in aspect ratio despite different resolutions) plus 861
backend tests and 20/20 e2e.

## Post-implementation notes (bugs found and fixed along the way, not anticipated in the plan)

- **Real bug in `_landcoverContribution`-adjacent per-endpoint caching**:
  the ESA land-cover and hydrology endpoints both cached the
  *already-projected* array under a cache key that excluded projection —
  and their cache-hit read paths never re-projected at all. A second
  request for the same bbox with a different `projection` (or
  `maintain_dimensions`) silently got back whichever projection/shape was
  baked in by the *first* request. Fixed by caching the raw (unprojected)
  array and applying projection fresh on every request, including cache
  hits — matching the pattern the water-mask and DEM endpoints already used
  correctly.
- **Real bug: DEM endpoint's own `projection` default was `"cosine"`**,
  while every other terrain endpoint (water-mask, ESA, satellite,
  hydrology) defaulted to `"none"`. This cross-layer inconsistency was
  masked for years by `maintain_dimensions=True` forcing every endpoint
  back to the same `(m,n)` regardless of which projection was silently
  applied — F-PROJ-DIMS's default flip is what surfaced it (a raw
  `/api/terrain/dem` call with no explicit `projection` param and
  `/api/terrain/hydrology` for the same bbox/dim returned different pixel
  counts). Fixed by aligning the DEM endpoint's default to `"none"`,
  matching the Python SDK's own `_DEFAULT_SETTINGS` (which was already
  `"none"` — confirming `"none"` was the intended default all along, not an
  arbitrary choice made this session).
- **Real bug: `clip_nans=True` silently violated `maintain_dimensions=True`'s
  documented contract** ("output has same dimensions as input", per
  `project_coordinates`'s own docstring). A lat-warping projection
  (mercator/lambert/equidistant/sinusoidal/miller/gall) can introduce
  genuine edge NaNs even when `out_m == m` (the northernmost/southernmost
  output rows can sample outside the valid domain), and the NaN-border-trim
  step ran unconditionally after projection, regardless of
  `maintain_dimensions`. Found via the Playwright verification script
  (mercator returned `(128,200)` instead of the input's `(129,200)` with
  both flags on) — reproduced directly via curl against the live
  `/api/terrain/dem` endpoint to confirm it wasn't a test artifact. Fixed:
  `clip_nans` is now skipped when `maintain_dimensions=True` (maintaining
  dimensions wins). This was a pre-existing bug, not introduced by this
  session's changes — it was simply never exercised, since most prior
  tests used `clip_nans=False` or didn't check exact shape preservation at
  a latitude where mercator's edge sampling falls outside the domain.
- **Client hardcode found**: `dem-main.js`'s DEM fetch and
  `export-handlers.js`'s export-settings builder both hardcoded
  `maintain_dimensions: true` regardless of the (previously nonexistent)
  UI toggle — this was the direct, most visible cause of the user's
  original report ("why does the aspect ratio never change"). Added a
  shared `window.getProjectionParams()` helper in `ui-helpers.js` and wired
  every real fetch call site (DEM, water, ESA, satellite, hydrology,
  combined water+hydrology, city raster, composite city raster, export) to
  read the new `#paramMaintainDimensions` checkbox through it, rather than
  each call site reading `#paramProjection` ad hoc with inconsistent
  fallback defaults (`'none'` in most places, `'cosine'` in one).
- **Reminder (recorded here since it cost real time twice this session)**:
  the dev server on port 9000 runs `uvicorn` **without** `--reload`. Python
  source edits (schemas, routers, `geo2stl/projections.py`) have zero
  effect on the live server until it's manually restarted — static
  JS/CSS/Vite `dist/` files ARE picked up live since they're served
  directly from disk. Always restart after backend `.py` changes before
  trusting a live/curl verification.

User-requested 2026-07-19, follow-up to an audit of "why does the aspect
ratio stay the same when I change projection, and are 'none' pixels really
square?"

## Root cause (confirmed by tracing the code)

1. `projection='none'` skips `project_coordinates` entirely (`geo2stl/dem.py`
   `make_dem_image`) — raw SRTM tile pixels are equal-**angle** (equal
   degrees lat/lon), not equal-**distance**. At non-equatorial latitudes
   (e.g. SF, 37.7°N) this makes "square" raster pixels actually rectangular
   on the ground (`cos(37.7°) ≈ 0.79` narrower in true E-W distance than
   N-S). **Decision: keep this as-is** (standard Plate Carrée convention) —
   just add a clarifying UI note. No code change needed for this part beyond
   the note.
2. `maintain_dimensions` defaults to `True` server-wide
   (`terrain.py:_parse_bool(params, "maintain_dimensions", True)`) with no
   client-facing toggle. Every projection call in the live path
   (`project_grid`, `project_water_arrays`, `project_categorical_layer`,
   `project_city_raster`, `project_rgb_image`) hardcodes
   `maintain_dimensions=True`. This forces every projection's output shape
   to equal the unprojected input shape `(m, n)` — so the rendered raster's
   aspect ratio never visibly changes when switching projections; only the
   *content* within that fixed grid warps.

**Decision: implement variable-dimension output, default OFF for
`maintain_dimensions` (i.e., true per-projection aspect ratio becomes the
new default), across the full pipeline in one pass — DEM, water, ESA, city,
hydrology, satellite, gridlines, export. No deferred phases.**

## Why this is tractable in one pass (not a multi-session effort)

Cross-layer pixel alignment does NOT depend on `maintain_dimensions=True`
per se — it depends on every layer being projected from the **same input
shape** `(m, n)` under the **same bbox** and the **same projection**, with
each projection's output-shape formula being a **pure deterministic
function of `(m, n, bbox, projection)`**. Given that:

- `_project_mercator`, `_project_lambert`, `_project_equidistant` already
  compute a correct true-aspect-ratio `(out_m, out_n)` in their
  `maintain_dimensions=False` branch (untested/unused today, but correct).
- `_project_cosine`'s `False` branch is broken (per-row integer truncation,
  "can cause layer misalignment" per its own comment) — needs a real fix,
  not just wiring up.
- `_project_sinusoidal`, `_project_cylindrical_y` (miller/gall) currently
  **ignore** the flag entirely (`out_m, out_n = m, n` either way) — need a
  real true-aspect-ratio computation added.
- `'none'` has no aspect-ratio concept (identity transform) — output shape
  stays `(m, n)` regardless of the flag; nothing to change here.

So the fix is: (a) give every non-cosine-broken projection function a
correct, deterministic true-aspect-ratio formula in its `False` branch, (b)
flip the default, (c) thread the resulting variable `(out_m, out_n)`
through every consumer that currently assumes a fixed shape (canvas sizing,
gridlines, export mesh generation), (d) since every layer independently
recomputes the same deterministic formula from the same `(m, n, bbox,
projection)` it already receives, no new cross-layer registration step is
needed — alignment is a corollary of determinism, not something to build.

## Approach

### 1. Fix `geo2stl/projections.py` per-projection `False` branches
- `_project_cosine`: replace the broken integer-truncated remap with a
  proper resize — compute `avg_cos` as today, then
  `out_n = max(1, round(n * avg_cos))`, `out_m = m`, and resample via
  `cv2.resize` (matching the pattern already used in `_project_equidistant`)
  instead of the per-row loop. Delete the old broken branch entirely (it's
  unused/untested — confirm via grep before deleting).
- `_project_sinusoidal`: the "wings" of a sinusoidal projection are
  genuinely non-rectangular (it's pseudocylindrical) — for
  `maintain_dimensions=False`, compute `out_n` from the bbox's true
  longitude range at the *widest* row (the equator-ward edge of the bbox,
  where `cos(lat)` is largest) so no valid data is clipped, then let
  `clip_nans` (already wired) trim the empty corners as it does today.
- `_project_cylindrical_y` (miller/gall): compute true aspect ratio from
  `y_range` (already computed) vs. `radians(east-west)`, mirroring
  `_project_mercator`'s existing `False` branch pattern.
- `project_coordinates`'s `'none'` branch: unchanged (identity, no aspect
  concept).

### 2. Flip the default
- `terrain.py`: `_parse_bool(params, "maintain_dimensions", True)` →
  default `False`.
- `CityRasterRequest`/`CompositeCityRasterRequest`/hydrology-merge schemas:
  audit for any hardcoded `maintain_dimensions=True` passed to
  `project_grid`/`project_categorical_layer`/etc. and remove the hardcode,
  passing through a request-level `maintain_dimensions` field (new,
  default `False`) instead — so every layer's endpoint honors the same
  per-request choice the client made for the DEM.
- `project_water_arrays`/`project_rgb_image`: same — accept
  `maintain_dimensions` as a parameter instead of hardcoding `True`.

### 3. Client: request + response shape handling
- `dem-main.js` / `water-mask.js` / `city-render.js` / hydrology loader:
  each already reads `dimensions`/`width`/`height` from its JSON response
  rather than assuming a fixed size (confirm during implementation) — so
  canvases should already size themselves from the response. Audit each for
  any place that assumes DEM dims == water dims == city dims (should now
  read each response's own dims).
- `dem-gridlines.js`: `geoToFrac` already warps content within the
  overlay's own `W,H` (read from the *display* canvas's bounding rect, not
  a hardcoded shape) — should need no change, but verify the border outline
  (lines 248-267) still traces correctly against a non-square canvas.
- Add a `maintainDimensionsCheckbox` (or similar) to `DemSourceSection.vue`
  next to the projection dropdown, default unchecked, sent as
  `maintain_dimensions` on every layer fetch (dem/water/esa/city/hydrology/
  satellite) alongside `projection`.
- Add the "none = equal-angle, not equal-distance" clarifying note next to
  the projection dropdown (from decision #1 above).

### 4. Export pipeline
- `app/server/core/export.py` / `export_params.py`: confirm the export mesh
  is built from whatever shape the (now-variable) projected DEM actually
  has, not a hardcoded `dim×dim` assumption. Any STL/3MF sizing math that
  assumes square output needs to read the actual array shape instead.

### 5. Tests
- `tests/test_projection_alignment.py` / `test_projection_city_alignment.py`
  / `tests/test_e2e_projection_pipeline.py`: extend to cover
  `maintain_dimensions=False` for every projection (not just the ones that
  already worked), asserting: (a) output aspect ratio matches the true
  geographic aspect ratio for that projection, (b) two independently
  projected layers (e.g. DEM + water mask) from the same bbox/dim/projection
  produce identical output shapes (the alignment guarantee), (c) export
  pipeline builds successfully from non-square projected output.

## Target files
- `geo2stl/projections.py` (core fix)
- `app/server/routers/terrain.py` (default flip + per-endpoint plumbing)
- `app/server/routers/composite.py`, `app/server/routers/cities.py`
  (maintain_dimensions plumbing for city/composite rasters)
- `app/server/core/export.py`, `app/server/core/export_params.py`
  (variable-shape export)
- `app/server/schemas.py` (add `maintain_dimensions` field where missing)
- `app/client/static/js/vue/components/dem/DemSourceSection.vue` (UI
  toggle + clarifying note)
- `app/client/static/js/modules/dem/dem-main.js`,
  `layers/water-mask.js`, `layers/city-render.js`,
  `dem/dem-gridlines.js` (shape-handling audit)
- `tests/test_projection_alignment.py`,
  `tests/test_projection_city_alignment.py`,
  `tests/test_e2e_projection_pipeline.py` (extended coverage)

## Success criteria
- Switching projections (with the new default) visibly changes the
  rendered aspect ratio to match that projection's true shape.
- DEM, water, ESA, city, and hydrology layers for the same
  bbox/dim/projection/maintain_dimensions request all come back with
  identical output dimensions (verified by test, not just by construction).
- Export (STL/3MF) succeeds and produces a correctly-proportioned mesh for
  a non-square projected shape.
- `maintain_dimensions=True` (opt-in) still works exactly as it does today,
  for users who want the old fixed-canvas-size behavior.
- Full backend + e2e suites green after the change.

## Risks
- `_project_cosine`'s broken branch may have been relied upon by some
  untested caller — grep for direct callers before deleting, not just
  `project_coordinates`'s dispatch.
- Sinusoidal's non-rectangular "wings": must not silently drop valid data
  at the bbox edges when computing `out_n` from the widest row.
- Export mesh code may have square-grid assumptions buried in triangulation
  or UV-mapping math — needs a real non-square test case, not just a shape
  assertion.
