# F-VIEW-AO — Ambient occlusion in the 3D view

_Proposed 2026-10-14 (user choice from the "more realistic City Model" form). Status: planned.
3D view only: the print does not change._

## Goal

Darken the 3D view softly where light would not reach: narrow streets, the foot of walls,
courtyards and the corners between buildings. This gives the finished City Model the depth of a
real architectural model under room light, which flat shading with two directional lights does
not.

## What exists today

- The viewer loads three.js r128 as a global from cdnjs
  (`app/client/templates/index.html`), core only, with no add-ons (no OrbitControls, no
  postprocessing; see `docs/decisions/frontend.md`, 2026-10-13, "the page loads three.js core
  only").
- Lighting is a hemisphere light and two directional lights, with no shadows and no
  post-processing (`app/client/static/js/modules/export/model-viewer.js::previewModelIn3D`).
  There is no SSAO, no baked occlusion and no vertex darkening.
- The finished model's parts are flat-shaded (`model-viewer.js::_showCityFull`); the terrain
  part already uses vertex colours (`model-viewer.js::_heightColors`), and building parts use a
  plain material colour (`city-fullmodel.js::partColor`).
- The viewer renders on demand (`needsRender`), not every frame.

## Approach

- **Bake it, don't compute it per frame.** Ambient occlusion is computed once per finished
  model, in a Web Worker, from a top-down height raster of all parts (the model is a
  heightfield plus vertical walls, so a 2.5D method is enough). For each raster cell, look in
  8–16 directions for the highest horizon angle within a radius of a few millimetres (horizon-
  based ambient occlusion); the result is a 0–1 visibility per cell.
- **Apply per vertex, and as a map for the ground.** Building, wall and roof vertices take the
  visibility at their x, y, reduced further by how far below the local top surface they are,
  so a wall darkens towards its foot. The terrain and draped parts, whose triangles can be
  large in flat streets, take the raster as a light map texture with the same planar UVs as
  F-VIEW-SATPARTS (three.js r128 `aoMap` reads the `uv2` attribute), so streets darken along
  the walls even where the terrain has few vertices.
- **Multiply, don't replace.** Occlusion multiplies the existing colours: the height ramp, the
  part colour, or the satellite drape, so it works with every colour mode.
- **A switch.** "Soft shadows" in the viewer's settings, on by default for the finished model,
  off for the quick view; mocked up as a PNG before it is built (`design-guidelines.md`).
- **Fallback if the bake is too slow or looks wrong:** three.js r128's `SAOPass` with
  `EffectComposer` from `three@0.128.0/examples/js/` (pinned on jsdelivr), which is screen-space
  and needs no bake but costs time on every frame and shows halos at silhouettes.

## Target files

- `app/client/static/js/modules/export/model-viewer.js::_showCityFull`,
  `model-viewer.js::previewModelIn3D`, `model-viewer.js::_rebuildColors`.
- `app/client/static/js/modules/export/ao-bake.js` (new: raster and horizon AO) and
  `app/client/static/js/workers/ao-worker.js` (new).
- `app/client/static/js/modules/export/city-fullmodel.js::toViewerFrame` (planar UVs, shared
  with F-VIEW-SATPARTS).
- `app/client/static/js/vue/components/views/ModelContainer.vue` (the switch),
  `docs/reference/frontend-modules.md`.

## Success criteria

- Bake time under 2 s for the Granada finished model in the worker, with the page responsive
  during it.
- Orbiting the model costs no more frame time than before (no per-frame pass).
- A screenshot pair (off and on) of a Granada street shows the street floor next to a wall at
  least 25 % darker than an open square, and roofs unchanged within 5 %. The pair is compared
  with the approved mockup.
- A Vitest test of the horizon function on a synthetic street (two walls 4 mm apart): the
  centre of the street is darker than open ground, the foot of the wall darker than its top.

## Risks

- **Raster resolution vs model size**: a 400 mm model at 0.25 mm per cell is a 1600 × 1600
  raster; the horizon search must stay within a few millimetres or the bake is slow.
- **Overhangs are invisible to a top-down raster** (bridge decks from F-BRIDGES, spire
  details): the ground under a bridge stays lit. Acceptable for a preview.
- **`aoMap` needs a second UV set** in r128 and only darkens ambient light, not the
  directional lights; the strength may need the light balance retuned, which also changes the
  look the 2026-10-13 "Calmer colours" decision settled. Compare against the current look before
  changing light levels.
