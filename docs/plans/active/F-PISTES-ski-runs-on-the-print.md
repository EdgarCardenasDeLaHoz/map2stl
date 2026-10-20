# F-PISTES — Ski runs that read on the print

_Approved 2026-10-18 (user: "On + wider line runs"). Status: implemented 2026-10-18, awaiting Breckenridge check (face count, visible runs)._

Done: `city_model.py::PISTE_LINE_WIDTH_M` / `is_downhill_piste`; server default `city_model_task.py::wants_ski_trails` (cache first, fetch only at relief >= 300 m, explicit setting wins; no JS change). Tests: `tests/test_city_model.py::TestSkiRuns`. Decision: `docs/decisions/trails.md`.

## Goal

On a mountain region such as Breckenridge, the ski runs should show on the printed City Model
without the user hunting for a switch, and a run should be about as wide on the print as it is on
the mountain.

## What exists today

- `city2stl/city_model.py::DEFAULT_LAYERS["trails"]` is `raised`, 0.3 mm, `line_width_m=3.0`,
  `enabled=False`. Only the Mountain preset turns it on.
- `geo2stl/trails.py` fetches `piste:type` / `route=piste` and hiking paths. A piste mapped as a
  closed way is an area and keeps its real outline. A piste mapped only as a line is buffered by
  the same 3 m as a footpath, which at 1:15,000 is 0.2 mm and gets widened only to the printable
  minimum.
- `city2stl/city_model.py::filter_trails` drops trails under buildings and along roads before the
  layer is built.
- At 1:14,843 (Breckenridge, 1 mm = 15 m) a 30 m run prints 2 mm wide.

## Approach

1. **Per-feature width.** In the trails layer, buffer a line feature by `PISTE_LINE_WIDTH_M = 30`
   when it is a downhill piste (`piste:type=downhill`, or `route=piste`/`ski`), and keep 3 m for
   hiking paths, nordic and skitour lines. Areas keep their outline. Follow the existing per-class
   width pattern (`_WATERWAY_WIDTH_M`, the waterway branch of `build_layer`).
2. **On where pistes exist.** In `app/server/core/city_model_task.py`, or on the client where the
   finished-model request is built (`window.cityExportBody` / `cityDetailFields`), turn the trails
   layer on by default when the box contains OSM downhill pistes. An explicit Off from the user
   still wins. Decide where to place it from how the trees default (`wants_trees`) is done, and do
   it the same way.
3. **Tests.** A unit test that a downhill line piste comes out about 30 m wide and a footpath 3 m.
   A test that the default turns on only when pistes exist and respects an explicit off.

## Target files

`city2stl/city_model.py`, `app/server/core/city_model_task.py` (or the client request builder),
`tests/test_city_model.py`, possibly `tests/js/cityDetail.test.js`.

## Success criteria

- Breckenridge's finished model has a `trails` part. Its runs are visible in the 3D view without
  changing any setting.
- Granada and Cartagena (no pistes) build exactly as before.
- The new and touched tests pass; ruff is clean; `npm run build` is OK if JS changed.

## Risks

- 30 m buffers on dense run networks merge into large raised sheets. That is acceptable for
  downhill runs, which are open slopes, but check the face count on Breckenridge.
- The piste check needs the trails fetch. Do not add an Overpass call to every city build: reuse
  the trails cache, or check only when the box is on a mountain (relief or preset).
