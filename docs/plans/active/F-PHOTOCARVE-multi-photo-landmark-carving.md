# F-PHOTOCARVE — Landmark shapes carved from several registered photos

Scope: refine landmark geometry (church towers, domes, spires) by carving it from the sky
outlines of 10–20 registered photos per landmark, then fitting building parts. Registration of
Cartagena photos against our own city model is part of this plan, because Google's 3D tiles
there do not match photos (see [building-heights decisions](../../decisions/building-heights.md),
2026-10-25 entry).

Status: approved 2026-10-26, Cartagena matcher first (step 2), then photos and carving. User choices (2026-10-25 form): multi-photo
3D carving; test on Granada cathedral and Cartagena churches; download 10–20 Commons photos per
landmark; output as fitted building parts; Cartagena cameras registered against our own model.

## Evidence so far

- Cartagena, Google tiles: the batch registered 0 of 9 Commons photos. A correctly aimed La Popa
  render gave 78 matches and 0 inliers.
- Cartagena Street View frames (15, known pose from `server.py::pano_seed`): every one stops at
  "only 6 matches have a depth" against the Google render (2026-10-26). Their known pose is
  already their camera; they become the anchors.
- Granada: started from the saved, hand-solved camera of `granada_cathedral`, auto register still
  fell below 25 inliers and went to the heading sweep (2026-10-26 test).

## Approach

1. **Photos.** Fetch 10–20 Commons photos per landmark through the F-WEB2 pipeline (category
   and geosearch), screened by `screen.py`; Granada cathedral first, then Cartagena's cathedral,
   San Pedro Claver and Santo Domingo.
2. **Cameras.**
   - Anchors: Street View frames (known pose) and hand-solved photos.
   - New matcher `register_model.py`: render our own city model (`model_glb.py` glb) at the
     start pose with a depth buffer, then match photo edges and silhouettes against it rather
     than texture (the model has no texture). Chamfer distance on the building outlines plus
     the skyline, optimised over pose (6 DoF + focal) from the guess.
   - Photo-to-photo SIFT between a new photo and registered anchors adds 2D–2D constraints to
     the same solve.
3. **Silhouettes.** Per photo, the sky mask (SegFormer, already in skyline) gives each
   landmark's upper outline; projected footprints (`terrain_solve.py::project_buildings`) say
   which outline columns belong to which building.
4. **Carving.** Voxel visual hull over each landmark footprint, 0.5 m voxels up to 100 m: a voxel
   survives only if every registered view that sees its column shows it below the sky line.
   A view with a bad camera shows up as a carving that contradicts the others and is dropped.
5. **Fitting.** The hull is fitted to parts: base prism, then towers (prism + spire, dome or
   pyramid cap) found as local height maxima, using `city2stl/roofs.py` shapes. Written as
   `building:part` overrides through `city2stl/landmarks.py`, so they show and are editable in
   the Landmarks panel.
6. **Validation.** Granada: compare part heights against REDIAM lidar (tower top, nave ridge).
   Cartagena: no lidar; check reprojection of the fitted parts onto held-out photos.

## Target files

`tools/photo_label/register_model.py` (new), `tools/photo_label/carve.py` (new),
`tools/photo_label/server.py` (routes), `tools/photo_label/index.html` (carve view, Register vs
model button), `city2stl/landmarks.py` (override writer), tests
`tests/test_photo_label_register_model.py`, `tests/test_photo_label_carve.py`.

## Success criteria

- Cartagena: at least half of the downloaded church photos registered against our model with
  a median reprojection below 15 px.
- Granada cathedral tower top within 2 m of REDIAM lidar; Cartagena tower tops consistent
  within 2 m across held-out views.
- Overrides load in the Landmarks panel and export to STL and 3MF.

## Risks

- Our model is coarse (OSM footprints, flat or guessed roofs): outline matching may lock onto the
  wrong building. Mitigation: start from anchors and title viewpoints, and limit the search.
- Commons photos of churches are mostly street-level and close; few show the whole tower against
  the sky. The hull needs 3+ views per tower.
- Carving cost: 20 views × 10⁶ voxels is fine on the CPU; no GPU needed.

## Decisions

(none yet)
