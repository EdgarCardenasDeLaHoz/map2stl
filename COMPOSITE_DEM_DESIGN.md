# Composite DEM Layer — Design Document

> **Purpose**: Define a new "Composite DEM" layer that appears in the stacked layers view and predicts/adjusts elevation by combining contributions from every loaded data source. Each source contributes an adjustable offset (in metres) to the base DEM. The architecture is designed so that a neural network can later replace the per-layer scalers with learned weights.
>
> **Status**: Phase 1 complete — all contribution functions, UI controls, layer integration, and feature array storage implemented.

---

## 1. Concept

Today the merge panel (`dem-merge.js`) is a separate tab that produces a one-shot blended DEM array via `/api/dem/merge`. The user must manually configure layers, preview, and "Apply as DEM." This is powerful but disconnected from the live layer stack.

The **Composite DEM** layer is different:

| Aspect | Current Merge | Composite DEM Layer |
|--------|--------------|---------------------|
| Where it lives | Separate "Merge" tab | Inline in the stacked layers view |
| Inputs | User-configured source list | All currently-loaded layers automatically |
| Output | Replaces `lastDemData` | A new canvas in the layer stack (`layerCompositeDemCanvas`) |
| Units | Raw elevation (metres) | **Delta** from base DEM (metres), applied additively |
| Update trigger | Manual "Preview" / "Apply" | Automatic when any input layer changes |
| Future | Static blend modes | Per-pixel neural network prediction |

### Core formula (current phase)

```
composite_dem[x,y] = base_dem[x,y]
                   + w_water   * water_contribution[x,y]
                   + w_city    * city_contribution[x,y]
                   + w_landcover * landcover_contribution[x,y]
                   + w_satellite * satellite_contribution[x,y]
```

Where each `*_contribution` is a per-pixel height delta in metres, and each `w_*` is a user-adjustable scalar (default values listed below).

---

## 2. Per-Layer Contribution Functions

Each layer produces a **signed height delta array** (Float32Array, same dimensions as DEM) in metres. These are computed client-side from already-loaded data.

### 2.1 Water (`water_contribution`)

**Source data**: `appState.lastWaterMaskData.water_mask_values` (0 = land, 1 = water)

**Logic**: Where water is detected, subtract elevation to carve river/lake beds.

```
water_contribution[x,y] = -depth_m   if water_mask[x,y] == 1
                           0          otherwise
```

**Parameters**:
| Param | ID | Default | Range | Description |
|-------|----|---------|-------|-------------|
| Depth | `compositeWaterDepth` | 5.0 | 0–50 m | How deep to carve water features |
| Weight | `compositeWaterWeight` | 1.0 | 0–5 | Multiplier on the contribution |

**Notes**: ESA water mask resolution is coarse (~10m). For small urban bboxes, water pixels may be 0%. This contribution will be zero in that case — that's correct. Higher-resolution river data (from OSM waterways) is handled separately via city overlay geometry (see 2.2).

### 2.2 City / OSM (`city_contribution`)

**Source data**: `appState.osmCityData` (buildings, roads, waterways, walls GeoJSON)

**Logic**: Rasterize OSM features to a height-delta grid:
- **Buildings**: Add `height_m` (from OSM tags, or default 10m) at each building footprint pixel
- **Roads**: Subtract a small amount (roads are typically slightly below surrounding terrain)
- **Waterways** (LineString rivers): Subtract depth along river centreline with falloff
- **Walls**: Add wall height at wall pixels

```
city_contribution[x,y] = +building_height_m   if pixel is inside a building polygon
                          -road_cut_m          if pixel is on a road
                          -river_depth_m       if pixel is on a waterway LineString
                          +wall_height_m       if pixel is on a wall
                           0                   otherwise
```

**Parameters**:
| Param | ID | Default | Range | Description |
|-------|----|---------|-------|-------------|
| Building scale | `compositeBuildingScale` | 1.0 | 0–5 | Multiplier on building heights |
| Road cut | `compositeRoadCut` | 0.5 | 0–5 m | Depth to subtract for roads |
| River depth | `compositeRiverDepth` | 3.0 | 0–20 m | Depth to carve for waterway lines |
| Weight | `compositeCityWeight` | 1.0 | 0–5 | Master multiplier |

**Notes**: The existing city raster layer (`loadCityRaster` → `/api/cities/raster`) already produces a height raster. We can reuse `appState.cityRasterSourceCanvas` if available, or rasterize client-side from the GeoJSON. Server-side rasterization is preferred for accuracy.

### 2.3 Land Cover / ESA (`landcover_contribution`)

**Source data**: `appState.lastWaterMaskData.esa_values` (ESA WorldCover class per pixel)

**Logic**: Map ESA land cover classes to height offsets. Key classes:
- **Tree cover (class 10)**: Add canopy height
- **Built-up (class 50)**: Add building height estimate
- **Cropland (class 40)**: Slight addition (crop height)
- **Water (class 80)**: Subtract (handled by water layer, set to 0 here to avoid double-counting)
- **All others**: 0

```
landcover_contribution[x,y] = class_height_table[esa_class[x,y]]
```

**Default class height table** (metres):
| ESA Class | Label | Default Height (m) |
|-----------|-------|-------------------|
| 10 | Tree cover | +8.0 |
| 20 | Shrubland | +1.5 |
| 30 | Grassland | +0.3 |
| 40 | Cropland | +0.8 |
| 50 | Built-up | +6.0 |
| 60 | Bare / sparse | 0 |
| 70 | Snow and ice | 0 |
| 80 | Water bodies | 0 (handled by water layer) |
| 90 | Herbaceous wetland | -0.5 |
| 95 | Mangroves | +3.0 |
| 100 | Moss and lichen | 0 |

**Parameters**:
| Param | ID | Default | Range | Description |
|-------|----|---------|-------|-------------|
| Tree height | `compositeTreeHeight` | 8.0 | 0–40 m | Height to add for tree cover pixels |
| Weight | `compositeLandcoverWeight` | 0.0 | 0–5 | Master multiplier (default OFF) |

**Notes**: Default weight is 0 because land cover heights are speculative. User enables when they want tree canopy in the model.

### 2.4 Satellite Imagery (`satellite_contribution`)

**Source data**: `appState.lastSatData` (RGB pixel array) — if available

**Logic**: Derive a vegetation index (pseudo-NDVI from RGB) and map to height. This is a rough heuristic:
```
greenness = (G - R) / (G + R + 1)
satellite_contribution[x,y] = greenness * veg_height_m   if greenness > threshold
                               0                          otherwise
```

**Parameters**:
| Param | ID | Default | Range | Description |
|-------|----|---------|-------|-------------|
| Veg height | `compositeVegHeight` | 5.0 | 0–30 m | Max vegetation height from greenness |
| Weight | `compositeSatWeight` | 0.0 | 0–5 | Master multiplier (default OFF) |

**Notes**: This is the weakest signal — RGB-derived NDVI is noisy. Default weight is 0. Useful as a feature input for future neural network training.

---

## 3. Architecture

### 3.1 Client-Side Computation

All contribution functions run **client-side** in JavaScript. No new API endpoints needed for the basic version. The computation is:

```javascript
function computeCompositeDem() {
    const dem = appState.lastDemData;
    if (!dem) return null;

    const { values, width, height } = dem;
    const composite = new Float32Array(values.length);

    // Start from base DEM
    for (let i = 0; i < values.length; i++) composite[i] = values[i];

    // Add each contribution (resized to match DEM dimensions)
    composite += weight_water     * computeWaterContribution(width, height);
    composite += weight_city      * computeCityContribution(width, height);
    composite += weight_landcover * computeLandcoverContribution(width, height);
    composite += weight_satellite * computeSatContribution(width, height);

    return { values: composite, width, height,
             min: Math.min(...composite), max: Math.max(...composite) };
}
```

### 3.2 Layer Integration

The composite DEM is rendered as a new canvas in the stacked layers view:

```
LAYER_STACK = ['Dem', 'Water', 'Sat', 'SatImg', 'CityRaster', 'CompositeDem'];
```

New HTML elements:
```html
<canvas class="layer-canvas" id="layerCompositeDemCanvas" style="display:none;"></canvas>
```

Visibility toggle + opacity slider in the layers control panel, like other layers.

### 3.3 Rendering

The composite DEM canvas uses the same colormap rendering as the base DEM (`renderDEMCanvas`), but with the blended values. The colorbar updates to reflect the composite range when this layer is active.

### 3.4 "Apply to DEM" Action

A button allows the user to replace `lastDemData.values` with the composite values, making the composite the new base DEM for STL export.

### 3.5 File Organization

New module: `ui/static/js/modules/composite-dem.js`

Responsibilities:
- Contribution computation functions (water, city, landcover, satellite)
- `computeCompositeDem()` — orchestrator (also handles rendering inline; `renderCompositeDemLayer` was not extracted as a separate export)
- `setupCompositeDemControls()` — wire UI sliders
- Event subscriptions: auto-recompute when `lastDemData`, `lastWaterMaskData`, `osmCityData` change

> **Implementation note:** `renderCompositeDemLayer()` was originally planned as a standalone export but rendering was kept inline within `computeCompositeDem()` via `_renderCompositeCanvas()`. See `layers/composite-dem.js` for actual implementation.

---

## 4. UI Design

### 4.1 Merge Tab Removal

The current "Merge" tab button in the `dem-strip` is removed. The merge panel's layer-stacking functionality is subsumed by the composite DEM layer.

The existing `/api/dem/merge` endpoint and `dem-merge.js` module are **kept** as internal infrastructure — they can still be used programmatically or re-exposed later.

### 4.2 Composite DEM Controls

Added as a new collapsible section in the right panel's settings area (inside `#demControlsInner`), after the existing "Visualization & Display" section:

```
▼ Composite DEM
  [x] Enable composite layer

  Water contribution
    Depth:  [====5.0====] m     Weight: [===1.0===]

  City / OSM contribution
    Building scale: [===1.0===]
    Road cut:       [===0.5===] m
    River depth:    [===3.0===] m
    Weight:         [===1.0===]

  Land cover contribution
    Tree height: [===8.0===] m
    Weight:      [===0.0===]

  Satellite vegetation
    Veg height: [===5.0===] m
    Weight:     [===0.0===]

  [ Apply to DEM ]
```

### 4.3 Layer Stack Display

When enabled, the composite DEM appears as a new layer in the stacked view with its own visibility toggle and opacity slider, just like DEM/Water/Sat layers.

---

## 5. Neural Network Integration (Future)

### 5.1 Training Data Collection

The composite DEM architecture is designed so that each contribution function produces a **feature channel** — a 2D array of the same dimensions as the DEM. Together these form a multi-channel tensor:

```
input_tensor[x, y, :] = [
    base_dem_elevation,
    water_mask,
    building_height,
    road_mask,
    waterway_mask,
    esa_class_onehot[0..10],
    satellite_r, satellite_g, satellite_b,
    greenness_index,
    latitude, longitude,        // geo context
    slope, aspect,              // derived from DEM
]
```

### 5.2 Ground Truth

Ground truth comes from high-resolution LiDAR data (e.g., USGS 3DEP 1m DSM) which includes buildings, trees, and infrastructure. The training target is:

```
target[x,y] = lidar_dsm[x,y] - srtm_dem[x,y]
```

i.e., the **height delta** that the network should predict.

### 5.3 Model Architecture

A lightweight per-pixel MLP or small U-Net that takes the feature channels and predicts the height delta. The model runs either:
- **Server-side** (Python, PyTorch/ONNX) via a new `/api/composite/predict` endpoint
- **Client-side** (ONNX.js / TensorFlow.js) for real-time preview

### 5.4 Why This Design Enables It

The current scaler-based system and the future neural network share the same interface:

```
contribution[x,y] = f(features[x,y])  →  height delta in metres
```

Today, `f` is a simple linear function (`weight * single_feature`).
Tomorrow, `f` is a neural network taking all features as input.

The UI sliders become "prior weights" that initialize or constrain the network. The `computeCompositeDem()` function becomes the inference wrapper. No architectural changes needed — only the contribution functions change from linear scalers to model inference calls.

---

## 6. Implementation Plan

### Phase 1: Infrastructure (Current Sprint)
1. Create `composite-dem.js` module with contribution functions
2. Add `layerCompositeDemCanvas` to the layer stack
3. Add composite DEM controls to the settings panel
4. Wire auto-recompute on data changes
5. Add "Apply to DEM" button
6. Remove "Merge" tab button from the strip (keep `dem-merge.js` internally)

### Phase 2: City Rasterization Improvements
7. Use server-side `/api/cities/raster` for building heights when available
8. Client-side fallback: rasterize GeoJSON buildings/roads/waterways to grid
9. Add wall contribution (stroke-width rasterization)

### Phase 3: Land Cover & Satellite
10. Implement ESA class → height table with per-class UI sliders
11. Implement RGB → pseudo-NDVI vegetation height
12. Add satellite imagery as a feature channel

### Phase 4: Neural Network (Future)
13. Build training data pipeline (SRTM + LiDAR pairs)
14. Train per-pixel height-delta predictor
15. Serve model via `/api/composite/predict` or client-side ONNX
16. Replace linear scalers with model inference
17. Keep sliders as "adjustment priors" that bias the prediction

---

## 7. Data Flow Diagram

```
┌─────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
│  Base DEM   │   │  Water Mask  │   │  OSM City     │   │  ESA Land    │
│  (SRTM/     │   │  (ESA band)  │   │  (buildings,  │   │  Cover       │
│   COP30)    │   │              │   │   roads,      │   │  (classes)   │
│             │   │  0/1 array   │   │   waterways,  │   │              │
│  float32[]  │   │              │   │   walls)      │   │  uint8[]     │
└──────┬──────┘   └──────┬───────┘   └──────┬────────┘   └──────┬───────┘
       │                 │                   │                    │
       │          ┌──────▼───────┐   ┌──────▼────────┐   ┌──────▼───────┐
       │          │  water_      │   │  city_        │   │  landcover_  │
       │          │  contribution│   │  contribution │   │  contribution│
       │          │  × weight    │   │  × weight     │   │  × weight    │
       │          └──────┬───────┘   └──────┬────────┘   └──────┬───────┘
       │                 │                   │                    │
       ▼                 ▼                   ▼                    ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │                    SUM  (per-pixel addition)                       │
  │         composite[x,y] = dem + Σ (weight_i × contribution_i)      │
  └───────────────────────────┬────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  Composite DEM  │
                    │  Canvas Layer   │
                    │  (colormap      │
                    │   rendered)     │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ "Apply to DEM"  │
                    │  (optional)     │
                    │  replaces       │
                    │  lastDemData    │
                    └─────────────────┘
```

---

## 8. Key Design Decisions

1. **Client-side computation**: All contributions computed in JS from already-loaded data. No new API round-trips for the basic version. Fast iteration on slider changes.

2. **Additive model**: Each layer contributes a signed height delta. This is the simplest model that's also compatible with neural network regression (predict a delta, not an absolute).

3. **Units in metres**: All contributions and weights are in metres/multipliers. No abstract "blend weights" — the user sees physical quantities.

4. **Default-off for speculative layers**: Land cover and satellite weights default to 0. Only water and city contributions are active by default, as they have the most reliable data.

5. **Reuse existing data**: No new fetches. Water mask, OSM data, ESA classes, and satellite imagery are already loaded by other tabs. The composite layer just reinterprets them as height contributions.

6. **Keep merge infrastructure**: `dem-merge.js` and `/api/dem/merge` remain available internally. The composite DEM layer is a higher-level abstraction built on top.
