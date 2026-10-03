# F-TREES — tell buildings from trees and terrain bumps in printed city models

**Requested:** user, 2026-10-03 ("proceed" on the neural-network review, which ranked this
first). Part of F-STL2NUMPY ([plan](F-STL2NUMPY-decompose.md)): the building table
over-detects on prints that carry trees (Salzburg: 51 % of the plate called built, OSM 25 %).

## Goal

Each region the building table finds gets a probability of being a building rather than
vegetation or another raised thing (viaducts, terrain bumps); the table keeps buildings only.
Measured by footprint IoU against OSM on the 13 registration packs, leave-one-city-out.

## Approach

1. **Labels from OSM, per region** (`tools/align_tool/data/<slug>`: the plate's
   `stl_relief.npy`, `register_transform`, `osm_buildings.npy`, and
   `city2stl.osm_raster.get_osm_semantic_masks` for vegetation and elevated roads, as
   registration's `semantic_exclusion` uses them). A region over half covered by OSM
   buildings is a building; one under a tenth covered and over half under vegetation or a
   viaduct is not; the rest is left out of training.
2. **Features per region** in `numpy2stl` (geo-free): height, area, roof plane RMS and slope,
   surface roughness (second differences), boundary wall sharpness (how abruptly it rises),
   compactness and rectangularity of the footprint.
3. **First model: gradient boosting** (scikit-learn), leave-one-city-out. Ship it if it lifts
   IoU on most packs.
4. **Only if that falls short: a small U-Net** on surface-height tiles (GPU), trained on the
   same OSM labels per cell.

## Target files

- `numpy2stl/src/numpy2stl/stl2numpy/buildings.py` (region features, a `keep` filter)
- `map2stl/tools/ml/` or `claude/scripts/` (training and evaluation), model in `map2stl/models/`
- tests in `numpy2stl/tests/test_stl2numpy_buildings.py`

## Success criteria

- Footprint IoU against OSM above the registration mask's (today's) on at least 10 of 13
  packs, leave-one-city-out, and no pack worse by more than 0.03.
- Salzburg's built fraction within 10 points of OSM's.

## Risks

- Label noise: registration error (a few cells), OSM gaps (missing buildings counted as
  "not building" are excluded by the 10 % rule but not entirely).
- Block-level prints (Valencia, Barcelona) merge buildings and courtyards; region features
  of whole blocks differ from single buildings.
- 13 cities is a small set for leave-one-out; per-region samples number in the tens of
  thousands, but cities differ in print style.

## Progress

2026-10-03, first two attempts (scripts `claude/scripts/trees_eval.py`, `trees_cells.py`;
features `numpy2stl/stl2numpy/buildings.py::region_features`). Both failed:

- **Per region, gradient boosting, leave-one-city-out:** AUC 0.51-0.82 (most 0.5-0.6);
  filtering lowered IoU on 12 of 14 packs. Cause, from Salzburg's map
  (`claude/e2e/trees/salzburg_regions.png`): canopies break into many small regions (their
  steps read as walls), buildings into merged blocks, so region features compare fragments
  with blocks.
- **Per cell, window texture at 3 scales:** AUC 0.48-0.90 (half the cities at chance);
  better than today on 1 of 14.
- **Suspected cause: the labels**, not only the features. OSM is placed on the plate by the
  registration (correlation 0.28-0.81 per pack) and OSM vegetation polygons are land use,
  not printed trees. At 5-10 m cells (the packs' 512 px grid, coarser than the print)
  canopy texture is mostly gone.
- The region features themselves work on a controlled case (test
  `test_region_features_tell_a_roof_from_a_canopy`).

Options to continue (user to choose): re-grid the packs from the STLs at the print's own
resolution (1-2 m, z-buffer) and check label alignment per tile; hand-label a few tiles as
clean truth; or a small U-Net, which would face the same label noise.

## Decisions
