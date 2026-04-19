# Plan: Building Height Estimation — Detailed Implementation

> **Status:** Approved — implementation not yet started
> **Scope:** Backend only, session API. No frontend changes.
> **Target cities:** Granada, Barcelona (test examples; tool is generic)
> **Target resolution:** ~5m/pixel
> **Last updated:** 2026-04-18

## TL;DR

Integrate 3 additional height data sources (Microsoft Footprints, Google 3D Tiles, Copernicus), build a CNN height-from-satellite pipeline, and create an STL→heightmap→AI-infill system. Backend only, session API. Phased with segment tests at each boundary.

---

## Challenged Assumptions

### A1: "Depth Anything V2 works for satellite imagery"

**WRONG.** Depth Anything V2 is trained on ground-level perspective photos. Satellite images are near-orthographic (nadir view). "Depth" from a satellite means "height above ground" — fundamentally different from perspective depth. DA2 will produce meaningless output.

**Correction:** Use models specifically designed for aerial/satellite height estimation: e.g., "Height estimation from single aerial images using a deep ordinal regression network" or train a custom U-Net. The pretrained quick-start should use a **DSM super-resolution** approach instead — take coarse SRTM (30m) + satellite RGB → predict fine-grained (5m) height map. This is a well-studied problem with available pretrained models.

### A2: "Microsoft Planetary Computer has per-building heights globally"

**PARTIALLY WRONG.** Microsoft's GlobalMLBuildingFootprints has footprint polygons globally but heights only in certain regions (US, parts of Europe). The "Microsoft Building Heights" dataset (separate from footprints) is US-only. For Spain/Colombia, footprints exist but heights are spotty.

**Correction:** Use MS footprints primarily for footprint geometry (to identify buildings OSM misses), not heights. For heights, Google 3D Tiles + Copernicus are more reliable in Europe.

### A3: "7+ data sources is the right approach"

**WRONG for initial scope.** Each source has different coordinate systems, formats, authentication, rate limits, and failure modes. Starting with 7 creates an untestable surface.

**Correction:** Start with 3 sources that give best coverage for target cities:
1. OSM (already have) — keep as primary
2. Google 3D Tiles (photogrammetric, best quality for Barcelona/Granada)
3. Copernicus Building Height (10m raster, free, covers all Europe)

Add more only after these are working and tested. Microsoft + LiDAR + shadow become Phase 1b.

### A4: "PConv is the right inpainting approach for heightmaps"

**QUESTIONABLE.** PConv was designed for RGB images (3-channel, smooth textures). Heightmaps are single-channel with sharp discontinuities at building edges. Building heights have spatial structure (nearby buildings have similar heights, terrain is smooth between them).

**Correction:** Start with **deterministic infill** (IDW interpolation + terrain DEM baseline) as the "dumb" baseline. Then try PConv only if baseline is insufficient. The satellite-conditioned GAN is actually the most promising ML approach because it can learn "these pixels look like tall buildings."

### A5: "Google 3D Tiles are easy to sample"

**HARDER THAN EXPECTED.** Google 3D Tiles use Cesium 3D Tiles format with Draco-compressed glTF. Meshes are in ECEF coordinates (Earth-Centered-Earth-Fixed), not lat/lon. Need:
- `py3dtiles` or custom glTF parser
- ECEF → WGS84 coordinate transforms
- Spatial indexing to find which tiles cover the bbox
- Ray-casting from above to get roof heights
- Ground height subtraction (from DEM) to get building height

**Correction:** This is a standalone module with significant complexity. Budget it as its own development phase with its own integration test.

### A6: "STL files have no coordinate system"

**TRUE AND PROBLEMATIC.** STL files are unit-less meshes. The user's STLs could be in mm, cm, or m. They have no georeference. The user must provide:
- Real-world bbox (lat/lon corners)
- Which axis is "up" (usually Z, but not guaranteed)
- Scale factor or real-world dimensions

**Correction:** Make geo-registration explicit in the session API. Provide a `preview_stl()` method that shows the mesh dimensions before committing.

---

## Architecture

### Core Design: HeightProvider Protocol

Instead of a monolithic cascade, use a **provider pattern** matching the existing dispatcher in `dem.py`:

```
HeightProvider (protocol/interface):
  - name: str
  - covers(bbox) → bool            # can this provider serve this region?
  - fetch_heights(bbox, dim) → HeightResult
    where HeightResult = {
      raster: np.ndarray (H,W) float32 in metres, NaN = unknown,
      confidence: np.ndarray (H,W) float32 [0,1],
      source_name: str,
      resolution_m: float
    }
```

Each source becomes a HeightProvider. The cascade module tries providers in priority order and merges results — filling NaN pixels from lower-priority providers.

**Why this is better than modifying `_fill_heights()`:** The current `_fill_heights()` works per-footprint (vector-based). But most new sources provide **raster** data (height per pixel, not per building). Mixing vector and raster in one function is messy. Better to:
1. Keep `_fill_heights()` for OSM-only vector heights (levels, explicit height tag)
2. Add a new `merge_height_rasters()` function that composites raster sources
3. Merge the two: OSM vector heights override raster where available

### Module Layout

```
app/server/core/
├── height/                    # NEW package
│   ├── __init__.py            # HeightProvider protocol, merge_height_rasters()
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── copernicus.py      # EU Building Height 10m raster
│   │   ├── google_3d.py       # Google 3D Tiles → per-footprint heights
│   │   └── msft_footprints.py # Microsoft Building Footprints (Phase 1b)
│   ├── predict.py             # CNN inference (Phase 2)
│   ├── train.py               # CNN training pipeline (Phase 2)
│   ├── stl_import.py          # STL → heightmap (Phase 3)
│   └── infill.py              # Heightmap inpainting (Phase 3)
├── osm.py                     # existing, minimal changes
└── ...
```

**Why a `height/` package instead of flat modules:** These modules share types (HeightResult, HeightProvider), configuration (API keys, cache paths), and test fixtures. Grouping them prevents import cycles and makes the test surface clear.

### Cache Strategy for New Sources

Follow existing pattern in `cache.py`:

| Namespace | TTL | Format | Notes |
|-----------|-----|--------|-------|
| `copernicus` | 90 days | .npz + .json | Raster tiles, large, rarely change |
| `google3d` | 30 days | .npz + .json | Expensive API calls, cache aggressively |
| `height_merged` | 7 days | .npz + .json | Composite of all providers for a bbox |

Key generation follows existing `make_cache_key(namespace, N, S, E, W, extra)`.

### Session API Extensions

```python
# New methods on TerrainSession:

# Phase 1: Data sources
s.fetch_building_heights(providers=["osm", "copernicus", "google3d"])
  → self.building_heights: HeightResult (merged raster)
  → replaces self.city_raster["buildings"] with merged data

# Phase 2: CNN
s.predict_heights(model="dsm_super_res")
  → self.predicted_heights: HeightResult (CNN output)
  → slots into merge as lowest-priority raster

s.train_height_model(ground_truth_cities=["Barcelona"], epochs=50)
  → trains model, saves to models/ directory
  → returns training metrics dict

# Phase 3: STL import + infill
s.load_stl(path, bbox, up_axis="z", scale="auto")
  → self.stl_heightmap: np.ndarray (H,W) with NaN outside mesh
  → self.stl_mask: np.ndarray (H,W) bool (True where STL has data)

s.preview_stl()
  → matplotlib figure showing imported heightmap extent + values

s.infill_heights(method="idw"|"pconv"|"satellite_gan")
  → self.infilled_heights: np.ndarray (H,W) complete
  → uses self.stl_heightmap as known region, fills NaN
```

**Chaining with existing pipeline:**
```python
with TerrainSession().start() as s:
    s.select_bbox(north, south, east, west)
    s.fetch_dem()                          # terrain baseline
    s.fetch_cities()                       # OSM footprints
    s.fetch_building_heights()             # multi-source heights
    s.predict_heights()                    # CNN fills remaining gaps
    s.composite_city_raster()              # merge all into final raster
    s.export_stl()
```

---

## Phase 1a.0: Height Package Scaffolding

**Goal:** Create the `height/` package with `HeightResult` dataclass, `HeightProvider` protocol, and `merge_height_rasters()` function. No external dependencies.

**Files:**
- `app/server/core/height/__init__.py` — HeightResult, HeightProvider, merge_height_rasters()
- `app/server/core/height/providers/__init__.py` — empty
- `tests/test_height/__init__.py`
- `tests/test_height/test_merge.py`

### Segment Tests

- `test_merge_priority_osm_wins()` — OSM says 25m, Copernicus says 20m → result = 25m
- `test_merge_fills_gaps()` — OSM has NaN, Copernicus has 20m → result = 20m
- `test_merge_all_nan()` — no provider has data → result stays NaN (not default 10m)
- `test_confidence_reflects_source()` — verify confidence values match source priority
- `test_merge_different_resolutions()` — Copernicus 10m, target 5m → verify bilinear resampling applied before merge

---

## Phase 1a.1: Copernicus Building Height Provider

**Module:** `app/server/core/height/providers/copernicus.py`

**Data source:** Copernicus Land Monitoring Service — Urban Atlas Building Height 2012
- Format: GeoTIFF raster, 10m resolution
- Coverage: 800+ European cities including Barcelona, Granada
- Access: Direct HTTPS download, no API key
- Challenge: Data is organized by "Functional Urban Areas" not by arbitrary bbox. Need to map bbox → FUA code → download URL.
- Alternative: WCS (Web Coverage Service) endpoint allows bbox-based queries directly (simpler).

**Implementation steps:**
1. Query WCS GetCapabilities to find available layers for bbox
2. GetCoverage request with bbox + CRS → GeoTIFF response
3. Parse with rasterio (or PIL + manual georef if avoiding rasterio dep)
4. Resample to target resolution (dim parameter)
5. Return HeightResult with NaN where no data

**New dependency:** `rasterio` (~15MB)

### Segment Tests

**Unit tests** (no network, synthetic data):
- `test_geotiff_to_heightresult()` — load a tiny test GeoTIFF (10x10 pixels, hardcoded), verify output shape, dtype, NaN handling
- `test_resample_preserves_range()` — resample 10x10 → 50x50, verify min/max unchanged
- `test_cache_key_deterministic()` — same bbox → same cache key
- `test_covers_barcelona()` — verify `covers(barcelona_bbox)` returns True
- `test_covers_cartagena_returns_false()` — Copernicus EU doesn't cover Colombia

**Integration test** (mock HTTP, uses cached response):
- Save a real 100x100 Copernicus GeoTIFF response for Barcelona as test fixture
- `test_fetch_from_fixture()` — monkeypatch requests.get to return fixture → verify HeightResult
- `test_cache_hit()` — call twice → second call reads from cache, no HTTP
- `test_merge_with_osm()` — merge Copernicus raster with OSM vector heights → verify OSM values take priority where both exist

---

## Phase 1a.2: Google 3D Tiles Provider

**Module:** `app/server/core/height/providers/google_3d.py`

**Data source:** Google Map Tiles API — 3D Tiles (Photorealistic)
- Format: Cesium 3D Tiles (tileset.json → .glb tiles with Draco compression)
- Auth: Google Maps API key (available)
- Coverage: Most major cities globally, excellent for Barcelona/Granada
- Endpoint: `https://tile.googleapis.com/v1/3dtiles/root.json?key=API_KEY`

**Implementation steps:**
1. Fetch root tileset.json
2. Traverse tile tree to find tiles intersecting bbox (bounding volume check)
3. Download .glb tiles (Draco-compressed glTF binary)
4. Parse glTF with `trimesh.load()` (trimesh already a dependency, supports glTF)
5. Transform mesh vertices from ECEF to WGS84 (lon, lat, altitude)
6. Ray-cast Z-down from a grid of points at target resolution → max Z per pixel = DSM
7. Subtract DEM (terrain) to get building height = DSM - DTM
8. Return HeightResult

**Dependency notes:**
- Draco decompression requires `google-draco` or `trimesh[easy]` extras
- ECEF → geodetic: numpy vectorized, no extra dependency

**Cost management:** Cache aggressively. A typical city bbox (~2km x 2km) needs ~20-50 tiles. Free tier ~1000 req/month. Add `max_tiles` guard with clear error message.

### Segment Tests

**Unit tests** (no network):
- `test_ecef_to_wgs84()` — known ECEF coords for Barcelona landmarks → verify lat/lon/alt within 1m
- `test_tile_bbox_intersection()` — synthetic bounding volumes → verify correct tiles selected
- `test_raycast_flat_plane()` — flat triangle mesh at z=100 → raycast → verify all pixels = 100
- `test_raycast_box()` — 10x10x20m box mesh → raycast → verify 20m height in box area, NaN elsewhere
- `test_building_height_from_dsm_dtm()` — DSM=150m, DTM=130m → building_height=20m

**Integration test** (mock HTTP):
- Save a real .glb tile from Google for a small Barcelona area as test fixture
- `test_parse_glb_fixture()` — load fixture → verify mesh has vertices, faces
- `test_full_pipeline_fixture()` — mock tile fetch → parse → raycast → verify HeightResult with reasonable values (10-50m buildings)
- `test_max_tiles_guard()` — bbox covering all of Spain → verify raises error before downloading 10000 tiles

---

## Phase 1a.3: Height Merge + Session Integration

**Module:** `app/server/core/height/__init__.py` (merge logic already scaffolded in 1a.0)

**Core function:**
```
merge_height_sources(
    bbox, dim,
    providers: list[str] = ["osm", "copernicus", "google3d"],
    osm_heights: Optional[np.ndarray] = None,
) → HeightResult

Algorithm:
1. Start with result = np.full((dim, dim), NaN)
2. For each provider in REVERSE priority order:
   a. If provider.covers(bbox):
      result_i = provider.fetch_heights(bbox, dim)
      mask = ~np.isnan(result_i.raster)
      result[mask] = result_i.raster[mask]   # lower priority fills first
3. If osm_heights provided:
   mask = osm_heights > 0
   result[mask] = osm_heights[mask]         # OSM always wins (highest priority)
4. Return HeightResult(raster=result, confidence=..., source_name="merged")
```

**Confidence array:** Tracks where each pixel came from:
- 1.0 = OSM explicit height tag
- 0.9 = Google 3D (photogrammetric)
- 0.7 = Copernicus (10m raster, coarser)
- 0.3 = CNN prediction (Phase 2)
- 0.0 = default fallback

**Session API:** Add `fetch_building_heights()` method to `TerrainSession`.

---

## Phase 2: CNN Height Prediction

### Architecture

**NOT monocular depth estimation.** Instead, two approaches:

#### Approach A: DSM Super-Resolution (pretrained, quick start)
- Input: coarse SRTM DEM (30m) + satellite RGB (0.5-5m)
- Output: fine-grained DSM (5m) that includes building heights
- Model: SRCNN or ESPCN adapted for DEM super-resolution
- This is a well-studied remote sensing task with available pretrained weights
- Calibration: compare predicted DSM vs known building heights in same tile

#### Approach B: Satellite → Building Height Map (custom training)
- Input: 256×256 RGB satellite tile at ~5m/px
- Output: 256×256 float32 height map (metres above ground, 0 for non-buildings)
- Architecture: U-Net with EfficientNet-B4 encoder
- Key insight: this is a **semantic regression** task, not depth estimation
- Loss: Masked L1 (only penalize where ground truth exists) + edge-aware gradient loss
- Training data: paired tiles from Phase 1 ground truth cities

### Module Structure

**`app/server/core/height/predict.py`:**
```python
class HeightPredictor:
    def __init__(self, model_path, device="cuda"):
        self.model = load_model(model_path)
        self.device = device

    def predict(self, satellite_rgb, coarse_dem) → np.ndarray:
        # Preprocess → inference → post-process
        # Return: (H, W) float32 metres

    def predict_for_bbox(self, bbox, dim) → HeightResult:
        # Fetch satellite + DEM using existing pipelines
        # Tile into 256x256 patches with overlap
        # Predict each patch, stitch with blending
        # Return HeightResult
```

**`app/server/core/height/train.py`:**
```python
class HeightDataset(torch.utils.data.Dataset):
    # Loads paired (satellite, height_map) .npz tiles

def train(config: TrainConfig) → TrainResult:
    # Standard training loop, saves checkpoints to models/
```

**New dependencies:** `torch` (~2GB CUDA), `torchvision` (~30MB), `timm` (~5MB)

### Segment Tests

**Unit tests (no GPU):**
- `test_normalize_satellite()` — RGB normalization produces [0,1] range
- `test_tiling_256()` — 512x512 image → 4 tiles of 256x256 with correct overlap
- `test_stitch_tiles()` — 4 predicted tiles → stitched 512x512 with smooth blending
- `test_dataset_loads_npz()` — create synthetic .npz pair → verify shapes, dtypes
- `test_mask_loss()` — verify loss is 0 where mask=False

**Integration tests (needs GPU):**
- `test_pretrained_inference()` — load pretrained model → predict on fixture tile → verify output shape/dtype/range
- `test_overfit_single_tile()` — train 100 epochs on 1 tile → verify loss < threshold
- `test_predict_for_bbox()` — mock satellite + DEM fetchers → full pipeline → verify HeightResult

**Validation tests (ground truth):**
- `test_barcelona_mae()` — predict Barcelona, compare against ground truth → MAE < 10m (pretrained) or < 5m (custom)
- `test_granada_generalization()` — train on Barcelona, test on Granada → MAE < 8m

---

## Phase 3: STL → Heightmap → AI Infill

### 3.0: STL Import

**Module:** `app/server/core/height/stl_import.py`

```python
def stl_to_heightmap(
    stl_path: str | Path,
    bbox: dict,           # {north, south, east, west}
    resolution_m: float = 5.0,
    up_axis: str = "z",
) → tuple[np.ndarray, np.ndarray]:  # (heightmap, mask)
    # trimesh.load → scale to grid → ray-cast Z-down → max Z per pixel
```

### 3.1: IDW Infill (deterministic, no ML)

**Module:** `app/server/core/height/infill.py`

```python
def infill_idw(heightmap, mask, dem_baseline=None, power=2) → np.ndarray:
    # For NaN pixels: weighted average of nearest known pixels
    # If dem_baseline provided: blend toward DEM far from known data
    # Uses scipy.ndimage.distance_transform_edt
```

### 3.2: PConv Infill (requires trained model)

```python
def infill_pconv(heightmap, mask, model_path) → np.ndarray:
    # Partial convolution inpainting adapted for single-channel heightmaps
```

### 3.3: Satellite-Conditioned Infill (best quality)

```python
def infill_satellite(heightmap, mask, satellite_rgb, model_path) → np.ndarray:
    # Pix2Pix-style: input = [satellite_rgb, partial_height, mask] → complete height
```

### Segment Tests

**Unit tests:**
- `test_stl_to_heightmap_cube()` — 10x10x5 cube STL → import → verify height=5 in center, NaN outside
- `test_stl_to_heightmap_resolution()` — same cube, resolution 1 vs 5 → different grid sizes
- `test_up_axis_rotation()` — cube with Y-up → verify rotation applied correctly
- `test_idw_infill_simple()` — 10x10 array, center 4 pixels known (height=20), rest NaN → smooth falloff
- `test_idw_with_dem_baseline()` — known=20m, DEM=100m → far pixels approach 100m
- `test_infill_preserves_known()` — any infill method → known pixels unchanged

**Integration tests:**
- `test_stl_roundtrip()` — heightmap → mesh → STL → re-import → RMSE < 1m
- `test_session_load_stl()` — mock STL → `s.load_stl(...)` → verify self.stl_heightmap
- `test_session_infill_idw()` — load + infill → verify no NaN in output

---

## Implementation Order & Dependencies

```
Phase 1a.0: height/ package scaffolding + HeightResult type + merge logic
  ↓ (no external deps, pure Python)
Phase 1a.1: Copernicus provider (simplest, validates architecture)
  ↓ (needs: rasterio or manual GeoTIFF parsing)
Phase 1a.2: Google 3D Tiles provider (most complex, best quality)
  ↓ (needs: trimesh glTF support, coordinate transforms)
Phase 1a.3: Session API integration (fetch_building_heights)
  ↓ (needs: Phase 1a.0-1a.2 working)
Phase 2.0: CNN model architecture + dataset class
  ↓ (needs: torch, torchvision)
Phase 2.1: Training data generation from Phase 1 ground truth
  ↓ (needs: Phase 1a.3 working for Barcelona/Granada)
Phase 2.2: Training pipeline + pretrained DSM super-res
  ↓ (needs: Phase 2.0-2.1 + GPU)
Phase 2.3: Session API integration (predict_heights)
  ↓
Phase 3.0: STL import module
  ↓ (needs: trimesh, already available)
Phase 3.1: IDW infill (deterministic, no ML)
  ↓ (needs: scipy, already available)
Phase 3.2: PConv infill (needs trained model from Phase 2)
  ↓
Phase 3.3: Session API integration (load_stl, infill_heights)
```

Phases 1a.1 and 1a.2 can run in parallel.
Phases 3.0 and 3.1 can start anytime (no dependency on Phase 1 or 2).

---

## New Dependencies

| Package | Purpose | Phase | Size |
|---------|---------|-------|------|
| `rasterio` | GeoTIFF parsing for Copernicus | 1a.1 | ~15MB |
| `torch` | CNN inference + training | 2.0 | ~2GB (CUDA) |
| `torchvision` | EfficientNet encoder | 2.0 | ~30MB |
| `timm` | Model zoo (EfficientNet variants) | 2.0 | ~5MB |
| `laspy` | LiDAR point clouds (Phase 1b) | 1b | ~2MB |

Note: `trimesh` (4.6.8) already installed. Verify glTF/Draco support: `pip install trimesh[easy]`.

---

## Router Design

New router: `app/server/routers/height.py`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/height/sources` | POST | List available providers for a bbox |
| `/api/height/fetch` | POST | Fetch + merge heights from specified providers |
| `/api/height/predict` | POST | Run CNN prediction |
| `/api/height/import-stl` | POST | Upload STL + bbox → heightmap |
| `/api/height/infill` | POST | Infill heightmap NaN regions |

Follows existing pattern: async endpoint → `run_sync(core_func)` → cache → JSONResponse.

---

## Verification Milestones

| Milestone | Test | Pass Criteria |
|-----------|------|---------------|
| Height package scaffolds | `pytest tests/test_height/test_merge.py` | All merge tests pass |
| Copernicus works | `pytest tests/test_height/test_copernicus.py` | Barcelona fetch returns (H,W) array, >50% non-NaN |
| Google 3D works | `pytest tests/test_height/test_google3d.py` | Barcelona fetch returns heights in [0,300]m range |
| Merge improves coverage | Session notebook | % buildings with real height: OSM-only < merged |
| CNN inference runs | `pytest tests/test_height/test_predict.py` | Output shape matches input, values in [0,300] |
| CNN improves MAE | Validation notebook | MAE on held-out city < 10m (pretrained) or < 5m (custom) |
| STL import works | `pytest tests/test_height/test_stl_import.py` | Roundtrip RMSE < 1m |
| IDW infill works | `pytest tests/test_height/test_infill.py` | No NaN in output, known pixels unchanged |
| End-to-end session | Notebook | Full pipeline produces valid exportable model |
