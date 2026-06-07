# F-SKY18 — Bearing landmarks: depth-snap + vegetation

Status: **Phase 1 + 2 implemented (2026-05-28)**.
- Phase 1 (coastline depth-snap): shipped + validated on Cartagena (per-seed 332-336 dots → 195-225 snapped onto OSM).
- Phase 2 (vegetation): SegFormer green class + pano stitch + OSM green layer (parks/grass/forest) + green polygon fill + green dots; ICP diagnostic preserved on raw projection.
- Phase 3 (bearing landmarks into registration): pending.

## Why
- Pano-projected coastline dots don't land on the real coast.
  - Column→**bearing** is exact (pano stitch geometry).
  - Range = `camera_h / tan(elevation)` → blows up near the horizon → dots scatter radially.
  - So: **trust bearings, distrust ranges.**
- Two consequences, two fixes:
  - **Depth-snap** — replace each projected point's bad range with the distance to the nearest OSM feature *along its (good) bearing*. Dots land on the real coast.
  - **More landmark classes** — coastline alone is sparse + 180°-symmetric. Vegetation (parks, tree lines) gives a second, asymmetric set of bearing anchors → better-constrained heading.

## Approach
- **A. Depth-snap (coastline first)**
  - New `snap_points_to_osm_along_bearing(points, osm_points, seed_lat, seed_lon, *, max_bearing_tol_deg=4)`.
  - For each pano point: compute its bearing from seed; find the OSM point whose bearing is closest (within tol); take that OSM point's range; emit (seed + range·dir). Bearing preserved, range corrected.
  - Apply to `pano_projected_coastline` before it's stored/overlaid → blue dots snap onto OSM coast.
- **B. Vegetation detection**
  - ADE20K green classes: tree=4, grass=9, plant=17, field=29 (confirm indices). Add `_ADE20K_VEGETATION` + a vegetation mask path in `pipeline._neural_sky_and_building_masks` (return alongside sky/building/water).
  - Thread a vegetation pano mask through the same stitch path the water mask uses → `pano_vegetation_mask` + `headings_per_col`.
  - Project per column (mirror `pano_water_top_to_lonlat`, but use the vegetation band BASE row = ground contact) → vegetation points; then depth-snap to OSM green.
- **C. OSM green extraction** (`osm_water.py`)
  - `extract_green_features(osm_data)` → leisure=park, landuse=grass/forest/recreation_ground/meadow, natural=wood/scrub/grassland.
  - `sample_green_points(features, spacing_m=20)` mirroring `sample_coastline_points`.
- **D. Overlay** (`region_pdf._draw_view_minimap` / `_draw_osm_coastline_overlay`)
  - Green dots (snapped) for vegetation; faint green fill for OSM green polygons. Coastline stays blue.
- **E. Registration bearing-landmarks**
  - Feed vegetation bearings into the keypoint sweep (`score_pano_offset_keypoints`) / ICP (`coastline_icp_offset`) as a second class, summed/weighted with coastline cost.
  - Vegetation is asymmetric where coastline is symmetric → should help the 180° bay ambiguity.

## Phasing
- **Phase 1**: A (coastline depth-snap) — self-contained, directly fixes the visible mismatch. Validate on Cartagena seed_4/seed_5 overlays.
- **Phase 2**: B + C + D (vegetation detect + OSM green + green overlay, both depth-snapped).
- **Phase 3**: E (bearing-landmarks into registration), measure heading vs manual ground truth.

## Target files
- `city2stl/skyline/coastline_registration.py` — snap fn, vegetation projection.
- `city2stl/skyline/pipeline.py` — vegetation mask.
- `city2stl/skyline/osm_water.py` — green extraction/sampling.
- `city2stl/skyline/region_pdf.py` — stitch vegetation mask, overlay, registration wiring.

## Success criteria
- Phase 1: blue coastline dots visibly land on the OSM coastline polyline.
- Phase 2: green dots appear on/near OSM green polygons.
- Phase 3: vegetation+coastline heading recovery matches manual offsets at least as well as coastline-only, and ideally cracks a 180°-symmetry seed.

## Known risks
- **Vegetation base is occluded/ambiguous** — tree canopy hides its ground contact; base-row projection noisier than water-top. Mitigate with snap (bearings only) + heavy trimming.
- **Snap can mis-correspond** when two OSM features share a bearing — gate by bearing tol + keep nearest range; flag when ambiguous.
- **ADE20K vegetation vs water confusion** on murky shorelines — keep classes disjoint, prefer water where both fire.
