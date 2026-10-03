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

## Decisions

- (to be lifted into `docs/decisions/` when done)
