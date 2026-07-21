# F-PROJ-EXPAND — Expand map projections (MapChart guide set)

User-requested 2026-07-19. Goal (all three): (a) accurate real-space shape for
printed terrain, (b) visual variety of world-map looks, (c) teaching distortion
(pairs with the new meters distortion grid).

Source list: https://blog.mapchart.net/misc/quick-guide-to-map-projections/

## Current state (6 projections)

Implemented in `geo2stl/projections.py`, registered in `get_projection_info()`,
dispatched in `project_coordinates()`, listed in the UI
(`DemSourceSection.vue`), and warped by the graticule
(`dem-gridlines.js:geoToFrac`).

| id | family | preserves |
|----|--------|-----------|
| none (Plate Carrée) | cylindrical | equidistant |
| cosine | cylindrical | approx local scale |
| mercator | cylindrical | conformal |
| equidistant | cylindrical | meridian distance |
| lambert | cylindrical equal-area | area |
| sinusoidal | pseudocylindrical | area |

## Target additions (from the guide)

| id | name | family | preserves | phase |
|----|------|--------|-----------|-------|
| miller | Miller Cylindrical | cylindrical | compromise | 1 |
| gall | Gall Stereographic | cylindrical | compromise | 1 |
| mollweide | Mollweide | pseudocylindrical | equal-area | 2 |
| eckert4 | Eckert IV | pseudocylindrical | equal-area | 2 |
| robinson | Robinson | pseudocylindrical | compromise | 2 |
| winkel3 | Winkel Tripel | modified azimuthal | compromise | 3 |
| vandergrinten | Van der Grinten I | polyconic | compromise | 3 |
| times | Times | pseudocylindrical | compromise | 2 |

(Guide also mentions Lambert Conformal Conic — conic, deferred; conic needs a
standard-parallel model that doesn't fit the current bbox pipeline cleanly.)

## Architectural constraint (why this is phased)

Projections here reproject the **actual DEM raster** that becomes the printed
mesh — not just a display overlay. The pipeline assumes:

1. `maintain_dimensions=True` — output grid same size as input.
2. Every layer (DEM, water, ESA, city, hydrology) reprojects identically and
   stays pixel-aligned (see `project_water_arrays`, `project_categorical_layer`,
   `project_city_raster`).
3. The export mesh is built from the projected grid.

For **cylindrical** projections x depends only on lon and y only on lat — each
output row is a simple horizontal resample. **Pseudocylindrical / azimuthal /
polyconic** make x depend on lat too (meridians curve), producing
non-rectangular output; sinusoidal already required the 2D inverse-sample path
(`_project_sinusoidal`, now vectorized).

## Phase 1 — Cylindrical (drop-in, low risk)

Each is a new `lat→y` function mirroring `_project_mercator` / `_project_lambert`
exactly (linear x, single vectorized `map_coordinates`). No pipeline changes.

- **Miller:** `y = 1.25 · ln(tan(π/4 + 0.4·φ))`, inverse
  `φ = 2.5·atan(e^{0.8y}) − 5π/8`.
- **Gall Stereographic:** `y = R(1+√2/2)·tan(φ/2)`; inverse
  `φ = 2·atan(y / (1+√2/2))`. x scaled by `1/√2` (handled by aspect in the
  maintain-dimensions resample).

Steps:
1. Add `_project_miller`, `_project_gall`; wire into `project_coordinates`.
2. Add to `get_projection_info()`.
3. Add `<option>`s in `DemSourceSection.vue` (+ `FetchLayersSection.vue` if it
   duplicates the list) and the projection-description map in
   `event-listeners-map.js`.
4. Add matching branches to `dem-gridlines.js:geoToFrac` so the graticule warps.
5. Tests: extend `tests/` projection suite (shape, round-trip finite, relief
   preserved).

## Phase 2 — Pseudocylindrical (2D-warp path, moderate risk)

Follow the vectorized `_project_sinusoidal` template: build full (out_m,out_n)
`lon_px/lat_px` grids, single `map_coordinates`, mask out-of-domain to fill.

- **Mollweide:** solve auxiliary θ (Newton: `2θ+sin2θ=π·sinφ`),
  `x ∝ (λ−λ0)·cosθ`, `y ∝ sinθ`. Equal-area.
- **Eckert IV:** similar auxiliary-angle solve; equal-area.
- **Robinson:** table-lookup of (X,Y) scaling per 5° lat, interpolated;
  compromise.
- **Times:** Gall-based pseudocylindrical variant (compromise).

Extra work vs phase 1:
- `geoToFrac` gains true 2D branches (curved meridians) for each.
- Verify `project_water_arrays` / categorical / city rasters produce the same
  non-rect shape and clip identically (shared valid-mask path).
- Confirm export mesh handles the fill/NaN wings (already handled for
  sinusoidal — reuse that clipping).

## Phase 3 — Azimuthal / polyconic (highest risk)

- **Winkel Tripel:** average of equirectangular and Aitoff; no closed-form
  inverse → needs iterative inverse (Newton on the 2×2 Jacobian) per output
  pixel, or a forward-scatter + grid-fill. Meridians AND parallels curve.
- **Van der Grinten I:** circle-based polyconic; closed-form inverse exists but
  is heavy; whole globe fits in a circle (strong distortion for large bboxes).

These break the row-strip assumption entirely. Decision point before starting:
whether a full 3D-print reproject is worth it for these, or whether they should
be **display-only** (project the graticule + a preview image, but keep the
export DEM on a cylindrical projection). Recommend display-only unless a
concrete print use-case appears.

## Cross-cutting

- **Graticule parity:** every new id needs a `geoToFrac` branch or the overlay
  silently falls back to `none` and won't show the right distortion.
- **Distortion grid:** the new meters section grid already visualizes any
  projection via `geoToFrac`, so phases land there automatically once the
  branch exists.
- **Perf:** keep every projection single-`map_coordinates` (vectorized) — the
  sinusoidal loop bug (619ms→26ms) is the cautionary tale; add a perf assertion
  in tests (<150ms at 600² on CI hardware is generous).
- **Naming:** ids lowercase, stable; display names match the guide.

## Success criteria

- Phases 1–2 reproject the DEM correctly (equal-area ones pass an area-ratio
  check; conformal/compromise pass shape/round-trip checks), warp the graticule,
  and export a valid mesh.
- No regression in existing 6 projections or layer alignment.
- Phase 3 either fully lands or is explicitly shipped display-only with the DEM
  export falling back to a cylindrical projection.
