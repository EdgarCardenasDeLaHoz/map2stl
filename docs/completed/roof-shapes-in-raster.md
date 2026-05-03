# Plan: Slanted Roofs in the City Heights Raster (F-ROOF1)

_Status: **complete** — F-ROOF1 shipped. Rasterizer paints per-pixel roof surfaces for gabled / hipped / pyramidal / skillion / dome shapes. See [completed/todo-history.md](../completed/todo-history.md) and [completed/building-roof-pipeline-plan.md](../completed/building-roof-pipeline-plan.md)._
_Created: 2026-04-28. Completed: 2026-05._

## Goal

Replace the flat-top building rasterization in `city2stl/rasterize.py` with per-building roof-shape rendering. Each building polygon writes a sloped surface (gabled, pyramidal, skillion, hipped) into the heightmap based on its `roof:shape` and `roof:height` OSM tags, falling back to a flat top when shape is unknown or a building is too small to resolve.

The OSM tags are already preserved end-to-end (`city2stl/fetch.py:113-120` keeps `roof:shape`, `roof:height`, `roof:levels`, `roof:direction`, `roof:orientation`). The 3D mesh export at `city2stl/mesh.py:197-364` already implements all six shape types. This plan brings that detail into the **2D heightmap layer** that drives the in-browser city raster preview, the colormap, and any DEM-derived export that consumes the raster.

## Current state

- **Rasterizer** (`city2stl/rasterize.py:116-147`): each building burns a constant `height_m × building_scale` into the grid via `rasterio.features.rasterize`, then taller buildings win via `np.maximum`. Flat tops only.
- **Mesh extruder** (`city2stl/mesh.py:_extrude_ring_with_roof`, lines 197-364): supports `flat`, `gabled`, `hipped`, `skillion`, `pyramidal`, `dome`. Uses PCA principal axis for ridge placement.
- **OSM tags preserved**: `roof:shape`, `roof:height`, `roof:direction`, `roof:orientation`, `building:levels`, `min_height`, `height` — all flow through `_fetch_buildings` → `_reduce_buildings` → `_fill_heights` → the per-feature properties dict that the rasterizer reads.

## Approach

Add a helper `_paint_building_roof(footprint_mask, eaves_h, roof_h, shape, axis_deg)` that, given a per-building boolean mask in raster space, writes a per-pixel height surface and returns it. The existing per-building loop substitutes this for the constant-fill rasterize call.

### Per-shape pixel formulas

For a footprint mask of size (h, w) in raster pixels:

- **flat**: `z = eaves_h + roof_h` (constant). Same as today.
- **gabled**: ridge along the principal axis (PCA of mask coords or `roof:direction` tag if present). For each pixel, `d_ridge = perpendicular distance to ridge line, normalized so the eaves edge = 1`. `z = eaves_h + roof_h × (1 − d_ridge)`.
- **pyramidal**: `d_centre = euclidean distance from centroid, normalized so the farthest pixel = 1`. `z = eaves_h + roof_h × (1 − d_centre)`.
- **skillion**: linear ramp along `roof:direction` (or principal axis if untagged). `t = projection of pixel onto axis, normalized 0..1`. `z = eaves_h + roof_h × t`.
- **hipped**: like gabled but tapered at the ridge ends. Easiest implementation: use `scipy.ndimage.distance_transform_edt` of the mask boundary; `z = eaves_h + roof_h × min(1, dt / max_dt_for_ridge)`. Naturally produces hip lines at the corners.
- **dome**: `z = eaves_h + roof_h × sqrt(max(0, 1 − d_centre²))`.

### Resolution gating

At 200 px raster over a 10 km region, a 30 m building only spans ~0.6 px. Roof bumps need at least ~3 pixels of footprint to be visible. Add a guard:

```python
if mask.sum() < 9:           # smaller than 3×3 raster pixels
    surface = np.full_like(mask, eaves_h + roof_h, dtype=np.float32)
else:
    surface = _paint_roof(mask, eaves_h, roof_h, shape, axis_deg)
```

### Default `roof_h`

If `roof:height` tag missing: `roof_h = max(2.0, 0.3 × height_m)` capped at `0.5 × height_m`. Matches the default in `city2stl/mesh.py:418`.

### Eaves height

`eaves_h = height_m − roof_h`. If a building has `min_height` (multipart structures), use that as `z0` floor instead of 0. The rasterizer doesn't currently use `min_height` — adding it is out of scope for this plan unless it falls out naturally.

## Files to modify

| File | Change |
|---|---|
| `city2stl/rasterize.py` | Add `_paint_building_roof()` helper. Replace `_rasterize` flat-fill loop at lines 116-147 with per-building mask + roof painting. |
| `city2stl/heights.py` | Add `roof_height_m` to the per-feature properties dict so rasterizer doesn't have to recompute. Reuse the same default formula as `mesh.py:418`. |
| `app/server/routers/cities.py` | The `/api/cities/raster` endpoint currently passes a `building_scale` parameter. Add an optional `roof_shapes: bool = True` parameter so the frontend can toggle the feature. |
| `app/server/schemas.py` | Add `roof_shapes` to the city raster request schema. |
| `app/client/static/js/vue/components/dem/FetchLayersSection.vue` | Add a checkbox under the Cities section: "☐ Slanted roofs (uses OSM `roof:shape`)". Default ON. ID: `cityRoofShapes`. |
| `app/client/static/js/modules/layers/city-render.js` | Read the new checkbox in `loadCityRaster()` and include `roof_shapes` in the request body. |
| `tests/test_rasterize.py` (new or extend existing) | Unit tests: each shape produces correctly-shaped output for a 20×20 square footprint; small footprints fall back to flat; missing `roof:shape` falls back to flat; `roof:direction` rotates the ridge for gabled/skillion. |

## Implementation order

1. **Roof painter helper** — `city2stl/rasterize.py:_paint_building_roof()`. Pure function, easy to unit-test in isolation. Implement and test gabled + pyramidal + flat first; those cover ~70% of OSM-tagged buildings.
2. **Wire into rasterizer** — replace the `_rasterize` building loop with the per-building mask path. Keep the `np.maximum` aggregation so taller buildings still win at overlap.
3. **`roof:direction` parsing** — accept compass degrees ("90", "E", "ESE") and convert to radians. Use principal axis (PCA of mask coords) as fallback. PCA fallback is what `mesh.py` already does, so reuse the helper if cheap.
4. **Add hipped + skillion + dome** — once the framework is in place these are small additions.
5. **Endpoint flag + UI toggle** — add `roof_shapes` boolean. Default ON; allow disabling for performance comparison.
6. **Tests** — golden-image tests on a synthetic 5-building scene.
7. **Verification** — restart server, fetch Philadelphia (already in cache from the user's session), confirm visible roof slopes at ≥600 px raster resolution.

## Tradeoffs and risks

- **Compute cost**: today's rasterizer is one batched `_rasterize` call. The new path is per-building (44k buildings on the user's recent Philadelphia fetch). Distance-transform per building is the hot path. Mitigation: batch by skipping buildings where `mask.sum() < 9` (small buildings still use flat fill, cheaply).
- **Resolution dependency**: at 200 px the city raster is too coarse to show roof bumps for typical buildings. The toggle defaults ON but the visible improvement only kicks in at ≥400 px. Document this in the UI tooltip.
- **`np.maximum` aggregation**: with sloped roofs the max-pooling still works because each building's surface is bounded by `eaves_h ≤ z ≤ height_m`. A slightly-shorter-but-pitched neighbour can poke above a slightly-taller-but-flat neighbour at the ridge — this is correct behavior, not a bug.
- **OSM tag completeness**: only ~15-30% of buildings in dense cities have `roof:shape` tagged (varies by region). For the rest the heightmap is unchanged (flat top). A future follow-up (`F-ROOF2`?) could call `roof_classifier.classify_roof_shapes()` to predict shapes from satellite imagery before rasterization.

## Out of scope

- Roof colour, material, or texture rendering (no shading in the heightmap).
- Multi-segment building parts with mixed roof types (handled in mesh export already; raster path keeps single-shape per-building).
- The 3MF/STL export path — already supports all shapes, no changes needed.
- The frontend vector overlay (city polygon overlay layer) — could later draw 2D roof-line schematics but that is a separate `F-ROOF3` proposal.

## Verification

After implementation:

1. `python -m pytest tests/test_rasterize.py -v` — all roof-shape unit tests pass.
2. Restart server, click the user's existing Philadelphia region, set City raster res to 600px, click "📥 Load Cities".
3. Toggle the new "Slanted roofs" checkbox off and on; confirm the city raster preview changes shape (visible ridges on tagged buildings).
4. Verify the heights returned in `/api/cities/raster` response do not exceed `max(building.height_m)` of the bbox.
5. Spot-check at least one OSM building with `roof:shape=gabled` (Philadelphia City Hall and Independence Hall both have this tag — easy ground-truth).
