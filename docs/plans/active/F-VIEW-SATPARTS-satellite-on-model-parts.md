# F-VIEW-SATPARTS — Satellite image on the finished model's parts

_Proposed 2026-10-14 (user choice from the "more realistic City Model" form). Status: implemented 2026-10-23 (T49), not yet checked on a real model in the browser.
3D view only: the print does not change._

## Goal

When the 3D view's colour is set to *Satellite image*, drape the satellite image on the
finished City Model's terrain, roads and green parts as well, not only on the quick-view
terrain, so the finished model looks like the place it was made from.

## What exists today

- `app/client/static/js/modules/export/model-viewer.js::_applySatelliteTexture` drapes the Edit
  tab's satellite canvas (`appState.satImgSourceCanvas`, same box and projection as the DEM,
  fetched at 1024–2048 px when missing) on the quick-view terrain mesh as a
  `THREE.CanvasTexture`. Its UVs come from each vertex's DEM pixel
  (`model-viewer.js::_buildMeshFromPreview`: u = column, v = 1 at row 0, north). `_satToken`
  drops a drape that was overtaken by a newer choice, and `_realMaterial` returns a mesh's own
  material while the Normals view shows a stand-in.
- The finished model's parts (`model-viewer.js::_showCityFull`, parsed by
  `city-fullmodel.js::parseModelParts`, placed by `city-fullmodel.js::toViewerFrame`) have no
  UVs. In `model-viewer.js::_rebuildColors` the terrain part "takes the colour ramp either way",
  so *Satellite* shows the terrain colour ramp on the finished model (`_heightColors` maps
  `satellite` to `terrain`); other parts take flat colours from `city-fullmodel.js::partColor`.
- The selector is `#viewerColormap` in
  `app/client/static/js/vue/components/views/ModelContainer.vue` ("Satellite image").

## Approach

- **Planar UVs from model millimetres.** The parts are in model mm with x east and y north
  from the last DEM row, on the DEM's own grid (`city2stl/city_model.py::Terrain`), so
  u = x / ((cols − 1) · mm_per_px) and v = y / ((rows − 1) · mm_per_px) put every part
  vertex on the same satellite pixel as the quick-view terrain. Compute them in
  `toViewerFrame` (or beside it) from the same `_full.frame`, before the frame change.
- **One texture, shared.** Reuse the canvas texture `_applySatelliteTexture` already loads,
  share one `CanvasTexture` across the textured parts, and extend `_satToken` and
  `_disposeMesh` so a drape that arrives late, or a part rebuilt meanwhile, does not leak or
  paint over a newer choice.
- **Which parts.** Terrain, roads, railways, trails and green get the image (their tops are
  horizontal, so a top-down image fits). Buildings keep their colour by default, because a
  vertical image on walls smears and satellite roofs lean away from their footprints; an
  option can put the image on roofs only. Water keeps its blue, so that open water is still
  readable where the image has glare.
- **Fallback.** With no satellite image for the box, the parts keep today's colours and the
  existing toast is shown once.
- **Design step.** The control is the existing *Satellite image* choice; a "roofs from
  satellite" switch, if added, goes in the viewer's settings, mocked up as a PNG first
  (`design-guidelines.md`).

## Done 2026-10-23 (T49)

- [x] Planar UVs: `city-fullmodel.js::satelliteUVs` (from viewer-frame positions and `_full.frame`, which now carries `cols`).
- [x] One shared `CanvasTexture` (`model-viewer.js::_partsTex`), made in `_applySatelliteTexture`, freed by `_dropPartsTexture` / `_disposeMesh`.
- [x] Which parts: `takesSatelliteImage` (terrain, roads, railways, trails, green; buildings with the roofs switch; water stays blue).
- [x] Edge rule (user choice): faces steeper than 60 deg get soil colour (`splitFacesBySlope`, material groups); walls under a photo roof keep the building colour.
- [x] `_paintFullParts` re-colours in place; `_rebuildColors` respects the mode; no refetch on either toggle.
- [x] Switch "Satellite image on roofs" in `ModelContainer.vue` (3D view card), `appState.viewerSatRoofs`, off by default.
- [x] Vitest: `tests/js/citySatParts.test.js`.
- [ ] Open: screenshot check against the quick-view drape; `renderer.info.memory.textures` count after ten toggles.

## Target files

- `app/client/static/js/modules/export/model-viewer.js::_applySatelliteTexture`,
  `model-viewer.js::_showCityFull`, `model-viewer.js::_rebuildColors`,
  `model-viewer.js::_disposeMesh`.
- `app/client/static/js/modules/export/city-fullmodel.js::toViewerFrame` (UVs),
  `city-fullmodel.js::partColor`.
- `docs/reference/frontend-modules.md` (function index).

## Success criteria

- With *Satellite image* chosen on Granada and Cartagena, the finished model's terrain, roads
  and green parts show the image, and it lines up with the quick-view drape: switching between
  the two shows no shift greater than one DEM pixel (checked on a screenshot pair at a road
  junction).
- A Vitest unit test of the UV maths: the four model corners map to UV (0,0), (1,0), (0,1),
  (1,1).
- Switching the colour mode back and forth ten times leaves one texture in
  `renderer.info.memory.textures`, not ten.

## Risks

- **A different box or projection**: the satellite canvas is fetched per box
  (`_sameBbox`); if the finished model was built for a box edited since, the image is wrong.
  Check the box before draping, as the quick view already does.
- **Resolution**: 2048 px over a 400 mm model is about 0.2 mm per pixel, coarser than the
  model's roads; roads may look blurred. Acceptable for a preview; a higher-zoom fetch is a
  later option.
- **The image includes building roofs and their shadows**, which will show on the terrain next
  to tall buildings (a satellite image is not an orthophoto). This is expected and should be
  said in the guide.
