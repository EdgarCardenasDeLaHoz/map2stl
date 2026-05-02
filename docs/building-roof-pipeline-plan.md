# Plan: Building Height & Roof Geometry Pipeline

_Last updated: 2026-04-30_
_Status: ROOF-1 done | ROOF-2 done | ROOF-3 done | F-ROOF1 (slanted roofs in raster) done. See [`docs/plans/height-training-status.md`](plans/height-training-status.md) for current ML model state._

## Context

The shadow height inference provider is working (pixel-scale bug fixed, eval script created). The pipeline needs to expand in three directions:

1. **Write inferred heights back to the OSM building table** -- so heights from shadow analysis, GHSL, nDSM, etc. replace the `default` placeholder heights before mesh generation.
2. **Detect and store roof geometry** -- not just height, but roof shape (gabled, hipped, flat, dome, etc.) using OSM tags where available and satellite inference where not.
3. **Generate non-flat roof meshes** -- buildings shouldn't be flat-topped prisms; they should have shaped roofs.

---

## Research: Useful OSM Data for City DEM

Beyond buildings, OSM has many tagged features that affect terrain and should feed into composite DEM generation. Here's what we **currently fetch** vs **what we're missing**.

### Currently fetched layers (`city2stl/fetch.py`)

| Layer | OSM Tags | Properties Kept | Height Source |
|-------|----------|-----------------|---------------|
| buildings | `building=*` | geometry, height_m, height_source | `height`, `building:levels`, or default 10m |
| roads | `highway=*` (graph) | geometry, highway, name, lanes, maxspeed, road_width_m | N/A (cut into DEM) |
| waterways | `waterway=*`, `natural=water/wetland/coastline/bay`, `landuse=reservoir/basin`, `place=ocean/sea` | geometry, waterway, natural, name, water | N/A (depression) |
| pois | `amenity=*`, `tourism=*`, `historic=*` | geometry, amenity, tourism, historic, name | N/A (point features) |
| walls | `historic=city_wall`, `barrier=city_wall` | geometry, name, height_m | default 8m |
| towers | `historic=tower/watchtower/fortification`, `man_made=defensive_works`, `tower:type=*` | geometry, name, height_m | default 20m |
| churches | `amenity=place_of_worship` | geometry, name, amenity, religion, height_m | default 15m |
| fortifications | `historic=fort/castle/fortress/fortification` | geometry, name, historic, height_m | default 12m |

### Tags being discarded (fetched by OSM but dropped at `keep` filter)

**Building tags thrown away at `fetch.py:75`:**
- `roof:shape`, `roof:height`, `roof:levels`, `roof:direction`, `roof:orientation` -- roof geometry
- `roof:colour`, `roof:material` -- visual properties
- `building:material`, `building:colour`, `building:architecture` -- facade info
- `building:levels` -- currently used only in `_fill_heights()` then dropped
- `building:part` -- sub-building sections with different heights (critical for complex buildings)
- `min_height` -- elevated building bases (e.g. buildings on stilts, elevated walkways)
- `construction_date`, `start_date` -- age/era info (useful for style inference)

### New OSM layers worth fetching for DEM

| Feature | OSM Tags | DEM Effect | Priority |
|---------|----------|------------|----------|
| **Retaining walls** | `barrier=retaining_wall` | Terrain step/cliff in DEM | High -- creates visible elevation discontinuities |
| **Embankments** | `man_made=embankment` | Raised linear feature | High -- railway/road embankments change terrain |
| **Bridges** | `man_made=bridge` (area) or `bridge=yes` on ways | Elevated surface above terrain | Medium -- flat deck at road height |
| **Piers/docks** | `man_made=pier` | Flat surface over water | Medium |
| **Cliffs** | `natural=cliff` | Sharp elevation drop | Medium -- already in DEM usually but OSM has precise line |
| **Hedges** | `barrier=hedge` | Low linear feature (1-3m) | Low -- subtle terrain effect |
| **Fences/walls** | `barrier=wall`, `barrier=fence` | Low linear feature (1-2m) | Low |
| **Land use parcels** | `landuse=quarry` (depression), `landuse=landfill` (raised) | Terrain modification | Low -- already in DEM |
| **Man-made structures** | `man_made=chimney/silo/water_tower/storage_tank` | Tall point/cylinder features | Medium -- standalone tall structures |
| **Stadiums** | `leisure=stadium` | Tiered bowl shape | Low -- complex geometry |
| **Swimming pools** | `leisure=swimming_pool` | Depression | Low |

### Building parts (`building:part=yes`)

This is the most impactful missing data. Many complex buildings (cathedrals, malls, L-shaped buildings) have `building:part` sub-sections with **different heights per section**. Currently we fetch the outer building polygon and apply one height to the whole thing. With building parts, a cathedral could have:
- Main nave: `height=15`
- Bell tower: `height=45`, `roof:shape=pyramidal`
- Transept: `height=12`, `roof:shape=gabled`
- Apse: `height=10`, `roof:shape=dome`

### Legacy features in `city2stl/osm2stl.py` (notebook-only, not in server)

These exist but are **not wired into the modern server pipeline**:

| Function | OSM Tags | What it does | Server equivalent? |
|----------|----------|--------------|-------------------|
| `get_pistes_osmnx()` | `piste:type=downhill` | Fetches ski pistes with difficulty ratings | None |
| `get_boundries_osmnx()` | Geocoded place name | Fetches country/state/city boundary polygons | None |

Both use the old `Line` class with matplotlib. They need to be modernized into `_fetch_*` helpers that return GeoJSON like the other layers.

### Additional linear features useful for DEM carving

| Feature | OSM Tags | DEM Effect | Notes |
|---------|----------|------------|-------|
| **Hiking trails** | `highway=path/track/footway` + `sac_scale=*` | Carve trail line into DEM | Use `sac_scale` for trail difficulty |
| **Ski pistes** | `piste:type=downhill/nordic`, `piste:difficulty=*` | Carve run into DEM with width | Legacy `get_pistes_osmnx()` exists |
| **Country/state borders** | `admin_level=2` (country), `4` (state), `6` (county) | Carve boundary line into DEM | Legacy `get_boundries_osmnx()` exists |
| **Railways** | `railway=rail/narrow_gauge` | Embankment or cut in terrain | Affect DEM profile |
| **Canals/ditches** | `waterway=canal/ditch/drain` | Linear depression | Already partially covered by waterways layer |

### Recommended additions (priority order)

1. **Preserve existing building tags** (ROOF-1) -- zero extra API calls, just stop discarding
2. **Fetch `building:part=yes`** -- separate query, merges with parent building footprint
3. **Fetch `man_made=*` tall structures** -- chimneys, silos, water towers, masts
4. **Modernize pistes + boundaries** -- port `osm2stl.py` legacy functions into `fetch.py` as proper layers
5. **Fetch hiking trails** -- `highway=path/track/footway` with `sac_scale`
6. **Fetch `barrier=retaining_wall`** -- affects terrain steps
7. **Fetch `man_made=embankment`** -- raised linear features
8. **Fetch `man_made=bridge` areas** -- elevated deck surfaces

---

## Current State (what exists now)

### OSM Building Pipeline
- `osm.py:_fetch_buildings()` queries `{"building": True}` but **discards all tags except geometry + height_m + height_source** (line 334)
- OSM already returns `roof:shape`, `roof:height`, `roof:levels`, `roof:colour`, `roof:material`, `roof:direction` when tagged -- we just throw them away
- `_fill_heights()` uses `height` tag -> `building:levels * 4` -> `default_m=10` fallback
- `_reduce_buildings()` dissolves touching buildings with same height -> loses per-building identity

### Height Provider Pipeline
- 8 providers (nDSM, WSF3D, Copernicus, LiDAR 3DEP, Google 3D, GHSL, Open Buildings, Shadow) -> `merge_height_rasters()` -> single `HeightResult` raster
- `enhance_buildings_with_raster()` already exists in `city2stl/heights.py:153` -- samples raster at building centroids and updates `height_m` for buildings with `height_source == "default"`. Currently hardcodes `"google3d"` as source name (line 240); needs to be parameterized.
- Composite DEM rasterizes buildings from OSM's `height_m` (which is often just `10.0` default for buildings without OSM tags, unless enhancement has been applied)

### 3D Mesh Generation
- `city2stl/mesh.py:_extrude_ring()` -- flat-topped prisms only
- `_build_building_meshes()` reads `height_m` and `terrain_z` from GeoJSON properties
- No concept of roof shape, ridge direction, or non-flat geometry

---

## Research: Building Height Estimation Approaches

### What we have (shadow-based, now fixed)
- HSV thresholding -> connected component labelling -> shadow length -> `h = L * tan(sun_elev)`
- Works globally (any satellite imagery), zero cost, ~3-5m accuracy
- Weakness: dense urban areas with overlapping shadows, terrain shadows misidentified

### Alternative approaches (from literature review)

| Approach | Accuracy | Data Required | Complexity | Open Source? |
|----------|----------|---------------|------------|--------------|
| **Shadow + YOLOv7 detection** ([arxiv 2411.09411](https://arxiv.org/abs/2411.09411)) | ~2-3m | RGB satellite + sun metadata | Medium | Paper only |
| **Sentinel-1 SAR + Sentinel-2 MSI** (T-SwinUNet) | RMSE 1.89m | SAR + multispectral time series | High | [GitHub](https://github.com/RituYadav92/Large-Scale-Building-Height-Estimation-RSE24-) |
| **BRAILS roof classifier** (SimCenter) | ~85% roof type | RGB satellite | Low | [Docs](https://nheri-simcenter.github.io/BRAILS-Documentation/) |
| **TEMPO** (global density + height) | ~5m at 37.6m/px | PlanetScope quarterly | High | Paper + dataset |
| **Street view + deep learning** | ~2m | Street-level imagery | Medium | [StatCan](https://www150.statcan.gc.ca/n1/pub/18-001-x/18-001-x2020002-eng.htm) |
| **Multi-view stereo DSM** | <1m | Stereo satellite pairs | High | Commercial mostly |
| **LiDAR** (already have via 3DEP) | <0.5m | LiDAR point cloud | Low (data access) | Yes |
| **M4Heights** multi-modal dataset (2025) | benchmark | Sentinel-1/2 + aerial | Reference | [Nature](https://www.nature.com/articles/s41597-025-06495-3) |

**Recommendation:** Our current multi-provider merge approach is already sound. The main gap is that **inferred heights don't flow back into the building table**. Fixing that pipeline gap will have more impact than adding new estimation methods.

### Roof Shape Detection Approaches

| Approach | Categories | Data Required | Open Source? |
|----------|------------|---------------|--------------|
| **OSM tags** (`roof:shape`) | 20+ types | Already fetched, just discarded | N/A |
| **BRAILS ResNet50** | flat/gabled/hipped | Satellite image crops per building | Yes (pretrained model ships with BRAILS) |
| **RoofNet** (2025) | 14 material classes | Satellite + OSM footprints | [arxiv](https://arxiv.org/html/2505.19358v1) |
| **RooFormer** (2025) | detailed 3D planes | High-res remote sensing | [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0924271625002369) |
| **LiDAR plane fitting** | arbitrary | LiDAR point cloud | Various |
| **Satellite shadow geometry** | gabled/flat/hipped | RGB + shadow analysis | Could extend current shadow provider |

**Recommendation:** Phase 1 should preserve OSM roof tags (free data, already available). Phase 2 can add BRAILS classifier for buildings without OSM roof tags.

---

## Research: OSM Roof Schema (OSM-4D/Roof_table)

Source: [OSM-4D/Roof_table wiki](https://wiki.openstreetmap.org/wiki/OSM-4D/Roof_table)

The OSM-4D roof table defines a parametric roof model:

### Core tags to preserve from OSM
```
roof:shape       -- flat, gabled, hipped, pyramidal, skillion, gambrel,
                    mansard, dome, onion, round, saltbox, half-hipped
roof:height      -- height of roof portion in metres
roof:levels      -- number of usable floors in the roof
roof:direction   -- compass bearing of the ridge line (degrees)
roof:orientation -- along/across (relative to longest building axis)
roof:colour      -- roof colour
roof:material    -- tiles, slate, metal, etc.
building:levels  -- (already used for height fallback)
```

### OSM-4D parametric model (3dr:* tags)
```
3dr:type         -- numeric roof type (0.0=flat, 2.0=gabled, 2.1=hipped, etc.)
3dr:height1/2    -- vertical parameters
3dr:length1..4   -- horizontal parameters relative to bounding rectangle
3dr:dormers:*    -- dormer placement per face
```

### Numeric type mapping
| Type | Range | Examples |
|------|-------|---------|
| Flat roofs | 0.0-0.4 | flat, flat_with_terrace |
| Single slope | 1.0-1.1 | skillion, skillion_diagonal |
| Two-face roofs | 2.0-2.9 | gabled, hipped, pyramidal |
| Multi-face roofs | 3.0-9.2 | saltbox, mansard, dome, cone |

### Mapping to mesh generation

| Shape | Mesh modification |
|-------|-------------------|
| flat | Current behavior (no change) |
| gabled | Ridge line along longest axis at `roof:height` above wall top |
| hipped | Ridge + sloped ends |
| pyramidal | Single apex point at centroid |
| skillion | One edge higher than opposite |
| dome | Hemisphere approximation (segmented) |
| mansard | Two-slope profile per side |

---

## Design: Three-Phase Pipeline

### Phase 1: Preserve OSM Roof Tags + Write Heights Back (ROOF-1) ✅ DONE

**Goal:** Stop discarding roof data; feed inferred heights into building features.

**Implemented changes:**

1. **`city2stl/fetch.py:_fetch_buildings()`** -- `keep` list expanded to include all roof tags and `min_height`.

2. **`city2stl/heights.py:_reduce_buildings()`** -- rewrote dissolve step to preserve `height_source` + all `_ROOF_COLS` by picking tags from the **largest-area** building per merge group.

3. **`city2stl/heights.py:enhance_buildings_with_raster()`** -- added `source_name: str = "raster"` parameter (was hardcoded `"google3d"`). The cities.py router now passes `source_name="google3d"` explicitly.

4. **`app/session/terrain_session.py`** -- added `enrich_buildings_with_heights(providers, source_name)` method that auto-calls `fetch_building_heights()` if needed, then applies the raster to the cached building GeoJSON.

### Phase 2: Roof Shape Detection -- satellite inference (ROOF-2) ✅ DONE

**Goal:** For buildings without OSM `roof:shape`, classify from satellite imagery.

**Implemented (inference pipeline complete — training data not yet collected):**

1. **Multi-signal heuristic classifier** in `city2stl/roof_classifier.py`:
   - Shadow geometry (symmetric → flat, triangular → gabled, trapezoidal → hipped)
   - RGB gradient features (edge orientation, corner density)
   - Elevation profile from pseudo-nDSM or real LiDAR height raster
   - Multi-temporal shadow triangulation (N≥2 images)
   - CNN path (MobileNetV3 stub or `RoofNet` if checkpoint loaded)
   - `roof_source` tag set to `"heuristic"`, `"cnn"`, `"osm_tag"`, etc.

2. **`RoofNet` multi-task architecture** in `tools/networks.py`:
   - Shared `Retna_V2` backbone (ResBlocks + GroupNorm)
   - `height_head` → dense pseudo-nDSM B×1×H×W
   - `shape_head` → 6-class logits (flat/gabled/hipped/pyramidal/skillion/dome)
   - Wired into classifier via `_roofnet_classify_patch()`

3. **Session client** in `app/session/terrain_session.py`:
   - `load_roof_model(checkpoint_path)` loads `.pt` checkpoint
   - `classify_roof_shapes()` auto-uses loaded model

4. **Evaluation scripts:**
   - `tools/seed_eval_regions.py` — seed 5 eval cities + pre-cache rasters
   - `tools/eval_pseudo_ndsm.py` — height raster coverage + statistics
   - `tools/eval_roof_classifier.py` — GT-strip accuracy + confusion matrix

5. **Tests:** `tests/test_roof_classifier.py` (53 tests) + `tests/test_networks.py`

Next: collect training data and train `RoofNet` (see `docs/roof-ml-architecture.md` Phase 2b).

### Phase 3: Non-Flat Roof Mesh Generation (ROOF-3) ✅ DONE

**Goal:** Replace flat-topped prisms with shaped roofs.

**Implemented changes:**

1. **`city2stl/mesh.py`** -- new `_extrude_ring_with_roof()`:
   - Supports `flat`, `pyramidal`, `gabled`, `hipped`, `skillion` plus dome/onion/cone aliases.
   - Unsupported or unknown shapes fall back to flat.
   - Gabled/hipped: PCA-based principal-axis detection; per-vertex roof height drops linearly with perpendicular distance from ridge.
   - Skillion: height increases linearly from one end of the principal axis to the other.
   - Pyramidal: fan of triangles from each roof edge to a central apex at z1 + roof_height_mm.

2. **`_build_building_meshes()`** -- reads `roof:shape` and `roof:height` from GeoJSON properties:
   - If `roof:shape` is non-flat and `roof_height_mm > 0`, calls `_extrude_ring_with_roof()`.
   - `roof:height` converted to mm via `building_z_scale`; defaults to 30 % of building height when absent.

---

## What to Build NOW (testing scripts only, no migration-sensitive files)

Since a migration is in progress, only create:

1. **`tools/eval_shadow_heights.py`** -- DONE (created this session)
2. **`tools/eval_roof_tags.py`** -- new script that:
   - Fetches OSM buildings for several cities
   - Reports what % have `roof:shape`, `roof:height`, `roof:levels` tags
   - Shows distribution of roof types per city
3. **Test for height writeback** -- add to `tests/test_height/`:
   - Create synthetic building GeoJSON + HeightResult
   - Verify `apply_height_raster_to_buildings()` correctly updates default-height buildings
   - Verify it leaves `osm_tag`-sourced heights untouched

---

## Files to Modify (when migration completes)

| File | Phase | Changes |
|------|-------|---------|
| `app/server/core/osm.py` | ROOF-1 | Expand `keep` cols, preserve roof tags through dissolve |
| `app/server/core/building_enrichment.py` | ROOF-1 | NEW: `apply_height_raster_to_buildings()` |
| `app/session/terrain_session.py` | ROOF-1 | `enrich_buildings_with_heights()` method |
| `app/server/schemas.py` | ROOF-1 | Add roof fields to building response |
| `city2stl/mesh.py` | ROOF-3 | `_extrude_ring_with_roof()`, modify `_build_building_meshes()` |
| `tools/eval_roof_tags.py` | now | Roof tag coverage evaluation script |

---

## Verification

1. **Roof tag eval:** `python -m tools.eval_roof_tags` -- shows roof tag coverage per city
2. **Height writeback test:** `pytest tests/test_height/test_building_enrichment.py -v`
3. **Shadow eval:** `python -m tools.eval_shadow_heights` -- already working
4. **End-to-end (after migration):** Load a city in the app, verify buildings show varied heights (not all 10m default) and non-flat roofs in 3D export
