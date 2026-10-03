# F-STL2NUMPY — decompose 3D city models into heightmaps, a building table, water and roads

**Requested:** user, 2026-10-03: "I would like STL2numpy module inside of numpy2stl to be able
to reverse engineer, or decompose the 3d renders into different base forms such as
heightmaps and building tables." Outputs chosen: surface heightmap (DSM), terrain heightmap
(DTM), building table, water and roads layers. Test models chosen: "Micropolitan + ours".

## Goal

One geo-free entry point in `numpy2stl.stl2numpy` that turns a city print model (STL, OBJ,
3MF; one file or a pack of part files) into:

| Output | Form |
|---|---|
| `dsm` | top surface, float32 grid, NaN off the model |
| `dtm` | ground with buildings removed, estimated under them (slope-aware) |
| `buildings` | table: id, footprint polygon, area, base z, height, roof shape and planes |
| `water`, `roads` | boolean grids (and polygons) when the model carries them as parts or levels |
| `meta` | cell size, units per metre if known, up axis, source parts |

Coordinates in model units with a known cell size; metres when a scale is given. No geo
code (numpy2stl rule): placing the result on the map stays in city2stl/registration.

## Approach (reuse first — survey 2026-10-03)

1. **Load parts, keep their names.** Extend `numpy2stl/io/readers.py` with a loader that keeps
   3MF objects and a pack's sibling files (Micropolitan `*_Solid`, `*_Water`; our own
   `buildings`, `roads`, `waterways` parts) apart. `load_trimesh` merges them today. One
   normalisation helper for up axis and units.
2. **DSM:** `stl2numpy/heightmap.py::mesh_to_heightmap` as is.
3. **DTM:** move `tools/align_tool/plate_vectors.py::estimate_terrain` into `numpy2stl.raster`
   and make it slope-aware: progressive morphological filter or slope-thresholded ground, with
   `street_place.py`'s mesa rule (add back buildings wider than 80 m). The 2026-05 finding
   stands: a frequency detrend removes hillside buildings, so no detrend.
4. **Building table:** `raster/segment.py::building_mask` + `split_touching_buildings` +
   `raster/vectorize.py::vectorize_buildings` on `dsm - dtm`. Per building: base = DTM under
   it, height = robust top. The roof is fitted with planes (RANSAC/region growing on the
   heightmap; `prism.py::_fit_plane`) and classified flat / pitched / complex. Lift
   `plate_vectors.py::heightmap_to_polygons` into numpy2stl rather than writing a second one.
5. **Water and roads:** from parts when present (`locate.py::plate_water_mask` moves into
   numpy2stl). Otherwise from geometry: water = flat cells at the lowest levels; roads = long,
   narrow, flat strips slightly above or below the ground. Report confidence; never guess
   silently.
6. **Round trip on our own models:** build a City Model from known inputs (OSM buildings, DEM),
   decompose it, compare (DTM error, footprint IoU via union `footprint_iou`, height error).
   Then the 9 Micropolitan packs, scored against OSM through the existing registration critic.

## Target files

- `numpy2stl/src/numpy2stl/stl2numpy/decompose.py` (new: `decompose(path_or_parts, ...)` →
  `Decomposition`), `buildings.py` (table), `terrain.py` (DTM) or extensions of `raster/`
- `numpy2stl/src/numpy2stl/io/readers.py` (parts loader)
- moved from `map2stl/tools/align_tool/`: `estimate_terrain`, `heightmap_to_polygons`,
  `plate_water_mask` (the tools then import them)
- tests: `numpy2stl/tests/test_stl2numpy_decompose.py` (synthetic + round trip),
  a map2stl round-trip test on a small City Model
- docs: numpy2stl README, `docs/INDEX.md`, `docs/reference/packages.md`, a decision entry

## Success criteria

- Our City Model round trip (Granada, Philadelphia): DTM within 1 m median of the source DEM;
  footprint IoU ≥ 0.85 against the input buildings; median height error ≤ 1 m.
- Micropolitan: footprint IoU against OSM no worse than the current registration pipeline,
  better on Lisbon and Salzburg (terrain leak today).
- Water: the `*_Water` parts and our `waterways` part recovered exactly.
- One call, no geo imports, runs on a 700 k-face model in under a minute.

## Risks

- Micropolitan models print whole blocks as one solid (Valencia, Barcelona): per-building
  splitting may only be possible by height steps. Report blocks as blocks.
- Unknown vertical scale on third-party models: needs a reference (tallest roof, stated plate
  span) or the table stays in model units.
- Roads have no geometric signature on many models; may only come from parts.

## Progress

Order set by the user (2026-10-03): DTM, then parts, then the building table, try the round
trip scoring, water and roads last once the others are proven.

- **DTM** (done): `numpy2stl/raster/terrain.py::estimate_dtm`. Ground = the cells both the
  progressive filter (`ground_mask_pmf`, slope 0.15) and the wall-step regions
  (`ground_mask_steps`) call ground; under buildings, linear from the block-median grid.
  Fixtures: our City Models of Cartagena, Granada, Philadelphia (`claude/scripts/stl2numpy_fixtures.py`),
  scored by `claude/scripts/dtm_eval.py` (truth = the terrain part), terrain + buildings at 2 m:

  | Error under buildings | Wide opening (before) | Steps | Progressive | Both (default) |
  |---|---|---|---|---|
  | Cartagena | 5.0 m | 0.27 m | 0.73 m | **0.19 m** |
  | Granada | 7.3 m | 4.2 m | 0.50 m | **0.46 m** |
  | Philadelphia | 6.4 m | 1.28 m | 1.18 m | **0.68 m** |

  - Walls: `neighbour_edges` - a step over 1.5 m that differs from both steps beside it, so
    steep roofs and slopes are never walls (a plain |dz| cut them into strips).
  - Our models raise roads 0.4 mm (4 m at Granada's scale); with them every method's error
    grows - the roads step's job.
  - Surface grids: `mesh_to_heightmap(method="zbuffer")`, the raycast answer (to 1e-14)
    vectorized; raycast took over an hour at 2,500 x 2,500 cells, `bin` misread walls.
- **Building table** (done, first version): `numpy2stl/stl2numpy/buildings.py::building_table`
  - footprint, area, base, height (per cell over its ground), top, roof (flat / sloped /
  complex from one plane fit); buildings split at walls. Scored by
  `claude/scripts/buildings_eval.py` (2 m cells, terrain + buildings surface):

  | | Table on estimated vs true ground: IoU, matched, height error (median / p90) | vs the OSM input: IoU, height error (median / p90) |
  |---|---|---|
  | Cartagena | 0.92, 100 %, 0.03 / 0.13 m | 0.74, 1.5 / 6.2 m |
  | Granada | 0.97, 100 %, 0.26 / 0.86 m | 0.84, 2.4 / 12.8 m |
  | Philadelphia | 0.97, 100 %, 0.06 / 0.25 m | 0.67, 1.6 / 6.1 m |

  The OSM gap is mostly the print model, not the decomposition: buildings print at least
  0.4 mm tall (about 9.5 m at Philadelphia's scale), Philadelphia's heights partly come from
  3DEP lidar, and outlines are simplified and merged by print layer. A fair round trip
  needs the model's own building inputs (after height enhancement): open.
- **Round trip** (tried): above. Next: the 9 Micropolitan packs (no truth; scored against
  OSM through the registration critic).
- **Water and roads**: last, as asked. Our models raise roads 0.4 mm and green 0.2 mm, which
  every terrain method reads as raised ground today.
- **Parts** (done): `numpy2stl/io/parts.py::load_parts` keeps 3MF objects, pack files and parts
  files apart; `part_role` from names; stdlib `read3MF` (trimesh's needs lxml, not installed);
  the parts-file writer moved here from `city_model_task.py`.

## Decisions

- (to be lifted into `docs/decisions/` when done)
