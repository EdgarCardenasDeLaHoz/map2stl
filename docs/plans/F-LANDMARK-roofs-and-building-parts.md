# F-LANDMARK — Roofs, building parts and landmark detail

Status: planned 2026-09-27 (user: "we still have not finalized any pipelines for adding
rooftop details for rendering complex key buildings like cathedrals, city hall").
Survey: this session's read-only audit (roofs, parts, sources) — findings below.

## Where it stands

- `city2stl/mesh._extrude_ring_with_roof`: flat, pyramidal, gabled = hipped, skillion;
  dome / onion / cone map to pyramidal. **Gabled and hipped come out flat on any convex
  footprint**: roof z is a function of distance from the ridge axis but no ridge vertices
  are added, so every outline vertex is at the eave (confirmed on a 10 × 6 rectangle).
- `city_model._roofed`: pitched roofs on concave footprints fall back to flat at mid-roof.
- `building:part`, `min_height`, `building:min_level`, `roof:direction`, `roof:orientation`,
  `roof:levels` are fetched (`fetch.py`) but unused; parts are unioned with the outline.
- Roof classifier (`roof_classifier.py`) only decides pitched vs flat, SDK-only.
- Heights: OSM tag > levels × 3.2 > 10 m; US lidar gives one number per building.
- Google 3D Tiles provider is still live code although ToS-prohibited (decision needed).

## Approach

1. **Roof geometry** (`city2stl/roofs.py`, replaces the mesh.py generator):
   - near-rectangular footprints (area ≥ 0.9 × min rotated rectangle): exact gable / hip /
     half-hip / gambrel / mansard / pyramid with ridge vertices, along `roof:direction` /
     `roof:orientation` when tagged, long axis otherwise;
   - any other footprint: straight-skeleton hip roof (faces at one pitch from each eave);
     gable = skeleton with gable-end edges held vertical;
   - dome, onion, cone, round: surfaces of revolution over the footprint's inscribed
     circle (segment count from print resolution), on a drum up to the eave;
   - every roof validated closed; failure → flat at mid-roof (as now) and counted.
2. **Building parts (S3DB)**: where parts exist, the outline contributes only the area no
   part covers; each part is its own solid from `min_height` (or `building:min_level` ×
   3.2 m) to its height with its own roof. Cathedral towers, spires (cone/pyramid), domes
   and apses then come from OSM tags alone.
3. **Landmark overrides** (per OSM id): (a) a mesh (STL/OBJ/glTF) placed by footprint
   alignment and scaled to the model, or (b) an nDSM patch (lidar DSM − DTM) turned into a
   roof solid clipped to the footprint. Stored per region; applied by `build_on_terrain`.
4. **Survey sources** (for 3b): promote the lidar readers from scratch into
   `city2stl/height/providers/`: USGS 3DEP (exists), Spain CNIG PNOA / REDIAM (Granada,
   Cartagena ES), France IGN LiDAR HD, Prague ČÚZK; 1 m nDSM per landmark footprint.
5. **UI**: a Landmarks panel — notable buildings (place of worship, town hall, castle,
   attraction, tallest N) with part count, roof shapes, height source; per landmark:
   choose OSM parts / nDSM / uploaded mesh, preview in 3D.
6. **Scoring**: promote the scratch "plate critic" (generated model vs plate / lidar) to
   `tools/critic/` and track roof error on the landmark set.

## Target files

`city2stl/roofs.py` (new), `city2stl/city_model.py` (parts + overrides), `city2stl/fetch.py`
(part fields), `city2stl/height/providers/*` (EU lidar), `app/server/routers/cities.py`
(landmark list / override API), client Landmarks panel, `tests/test_roofs.py`.

## Success criteria

- Gable/hip roofs rise to `roof:height` on rectangles and L-shapes; closed solids.
- Granada Cathedral, Alhambra towers, Cartagena city hall rendered from OSM parts with
  spires/domes where tagged; a landmark with an nDSM override matches lidar to ≤ 1 m.
- Registration/critic run shows no regression on footprint IoU.

## Risks

- Straight skeleton robustness on messy OSM outlines (use a tested implementation, fall back
  to flat); part/outline overlap tagging errors in OSM; EU lidar download endpoints are
  undocumented.
