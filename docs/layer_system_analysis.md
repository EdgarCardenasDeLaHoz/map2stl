# Layer System Analysis & Improvement Plan

_Last updated: 2026-03-16_

> **Status summary:** Issues 1 & 2 (stale cache, water mask on region change) are fixed in the current codebase via `clearLayerCache()`. Issues 3 & 4 are partially addressed — the stacked canvas view replaces the old separate-container tabs, and the layer status panel shows per-layer load state. The `LayerManager` class and split API endpoints (Phases 2–5) remain proposals, not implemented.

---

## Current Issues Identified

### 1. **Water Mask Bug on Region Change**

**Root Cause:** When selecting a new region, the `selectCoordinate()` function calls:
- `loadDEM()` - loads new DEM data
- `loadSatelliteImage()` - but NOT `loadWaterMask()`

However, `lastWaterMaskData` is **never cleared** when changing regions. This causes:
- Old water mask data from previous region to be displayed
- Dimension mismatch between new DEM and old water mask
- Combined view shows incorrect data

**Current Flow:**
```
selectCoordinate() 
  → switchView('dem')
  → loadDEM()          ✓ Gets new data
  → loadSatelliteImage() ✓ Gets new satellite
  → lastWaterMaskData remains OLD ✗
```

### 2. **Stale Cache Data Architecture**

**Problem:** Multiple global cache variables that are never coordinated:
```javascript
let lastDemData = null;        // Cleared manually, but not on region change
let lastWaterMaskData = null;  // NEVER cleared on region change  
let lastEsaData = null;        // Not used consistently
```

**Impact:**
- Water mask from Region A displayed with DEM from Region B
- Combined view produces incorrect visualizations
- User sees confusing/broken renders

### 3. **Tab/Layer State Not Synchronized**

**Current Tab System:**
```html
<button class="dem-subtab" data-subtab="dem">DEM</button>
<button class="dem-subtab" data-subtab="water">Water Mask</button>
<button class="dem-subtab" data-subtab="satellite">Satellite</button>
<button class="dem-subtab" data-subtab="combined">Combined</button>
```

**Issues:**
- Each "layer" is in a separate container, not actual layers
- No loading state indicators per tab
- Satellite tab only works if water mask is already loaded
- No visual feedback when data is stale

### 4. **Inconsistent Data Flow**

**loadWaterMask()** returns ESA data too (bundled together):
```javascript
// loadWaterMask() stores BOTH water mask AND ESA data in lastWaterMaskData
lastWaterMaskData = data;  // Contains: esa_values, water_mask_values, etc.
```

**loadSatelliteForTab()** depends on `loadWaterMask()`:
```javascript
if (lastWaterMaskData && lastWaterMaskData.esa_values) {
    renderEsaLandCover(lastWaterMaskData);
    return;
}
// Otherwise load the water mask first (which includes ESA data)
await loadWaterMask();
```

This is confusing and couples unrelated data sources.

---

## Proposed Architecture

### A. **Unified Layer State Manager**

```javascript
class LayerManager {
    constructor() {
        this.currentBbox = null;
        this.layers = {
            dem: { data: null, status: 'empty', bbox: null },
            water: { data: null, status: 'empty', bbox: null },
            satellite: { data: null, status: 'empty', bbox: null },
            combined: { data: null, status: 'empty', bbox: null }
        };
        this.activeLayer = 'dem';
        this.listeners = [];
    }

    // Check if layer data is stale (different bbox)
    isStale(layerName) {
        const layer = this.layers[layerName];
        return !layer.bbox || 
               !this.currentBbox ||
               !this.bboxEquals(layer.bbox, this.currentBbox);
    }

    // Update region - invalidates all layers
    setRegion(bbox) {
        this.currentBbox = { ...bbox };
        // Mark all layers as stale
        Object.keys(this.layers).forEach(name => {
            if (!this.bboxEquals(this.layers[name].bbox, bbox)) {
                this.layers[name].status = 'stale';
            }
        });
        this.notifyListeners('region-changed');
    }

    // Set layer data
    setLayerData(layerName, data) {
        this.layers[layerName] = {
            data,
            status: 'loaded',
            bbox: { ...this.currentBbox }
        };
        this.notifyListeners('layer-loaded', layerName);
    }

    // Clear all cache on region change
    clearAllLayers() {
        Object.keys(this.layers).forEach(name => {
            this.layers[name] = { data: null, status: 'empty', bbox: null };
        });
    }
}
```

### B. **Separate API Endpoints for Clarity**

Currently `/api/water_mask` returns both water and ESA data. Split into:

1. **`/api/dem`** - DEM elevation data (exists)
2. **`/api/water_mask`** - Just water mask binary values
3. **`/api/land_cover`** - ESA land cover classification (new)

Benefits:
- Each layer loads independently
- Clearer caching strategy
- Smaller payloads when you only need one

### C. **Progressive Layer Loading UI**

```html
<div class="layer-tabs">
    <button class="layer-tab" data-layer="dem">
        <span class="layer-icon">🏔️</span>
        <span class="layer-name">DEM</span>
        <span class="layer-status" id="dem-status">●</span>
    </button>
    <button class="layer-tab" data-layer="water">
        <span class="layer-icon">💧</span>
        <span class="layer-name">Water</span>
        <span class="layer-status" id="water-status">○</span>
    </button>
    <!-- etc -->
</div>
```

Status indicators:
- `●` = Loaded and current
- `○` = Not loaded
- `◐` = Loading
- `⚠️` = Stale (different region)

### D. **Compositing System for Combined View**

Instead of separate containers, use canvas compositing:

```javascript
class LayerCompositor {
    constructor(container) {
        this.container = container;
        this.canvas = document.createElement('canvas');
        this.layers = new Map(); // layer name -> canvas
        this.visibility = new Map(); // layer name -> boolean
        this.opacity = new Map(); // layer name -> 0-1
    }

    // Add a layer canvas
    addLayer(name, canvas, zIndex = 0) {
        this.layers.set(name, { canvas, zIndex });
        this.visibility.set(name, true);
        this.opacity.set(name, 1.0);
        this.recomposite();
    }

    // Toggle layer visibility
    toggleLayer(name, visible) {
        this.visibility.set(name, visible);
        this.recomposite();
    }

    // Recomposite all visible layers
    recomposite() {
        const ctx = this.canvas.getContext('2d');
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

        // Sort layers by zIndex
        const sorted = [...this.layers.entries()]
            .sort((a, b) => a[1].zIndex - b[1].zIndex);

        for (const [name, { canvas }] of sorted) {
            if (this.visibility.get(name)) {
                ctx.globalAlpha = this.opacity.get(name);
                ctx.drawImage(canvas, 0, 0);
            }
        }
        ctx.globalAlpha = 1.0;
    }
}
```

---

## Implementation Plan

### Phase 1: Fix Immediate Bug (Quick Fix)
- Clear `lastWaterMaskData` when region changes
- Add loading state to water mask tab
- Verify combined view uses matching data

### Phase 2: Refactor State Management
- Create `LayerManager` class
- Track bbox per layer
- Implement stale data detection
- Add visual status indicators

### Phase 3: Separate API Endpoints
- Create `/api/land_cover` endpoint
- Update `/api/water_mask` to return only water data
- Update frontend to use separate endpoints

### Phase 4: Layer Compositing
- Implement `LayerCompositor` class
- Enable layer toggling with checkboxes
- Add opacity controls per layer
- Support multiple layers visible simultaneously

### Phase 5: Testing
- Unit tests for LayerManager
- Integration tests for API endpoints
- E2E tests for layer loading/switching
- Visual regression tests for rendering

---

## Quick Fix Implementation

To fix the immediate bug, modify `selectCoordinate()`:

```javascript
function selectCoordinate(index) {
    selectedRegion = coordinatesData[index];
    
    // CRITICAL: Clear cached layer data when region changes
    lastDemData = null;
    lastWaterMaskData = null;
    lastRawDemData = null;
    
    // Update UI...
    // ... rest of function
}
```

Also clear in `clearAllBoundingBoxes()`:
```javascript
function clearAllBoundingBoxes() {
    // ... existing code
    
    // Clear cached layer data
    lastDemData = null;
    lastWaterMaskData = null;
    lastRawDemData = null;
}
```

---

## File Structure for Improved System

```
strm2stl/ui/
├── static/
│   ├── js/
│   │   ├── core/
│   │   │   ├── LayerManager.js      # State management
│   │   │   ├── LayerCompositor.js   # Canvas compositing
│   │   │   └── EventBus.js          # Event communication
│   │   ├── layers/
│   │   │   ├── DemLayer.js          # DEM loading/rendering
│   │   │   ├── WaterLayer.js        # Water mask loading/rendering
│   │   │   ├── LandCoverLayer.js    # ESA data loading/rendering
│   │   │   └── CombinedLayer.js     # Composite view
│   │   ├── api/
│   │   │   └── LayerApi.js          # API calls for layers
│   │   └── main.js                  # Entry point
│   └── css/
│       └── layers.css               # Layer UI styles
└── templates/
    └── index.html                   # Simplified HTML
```

This modular structure will make the code maintainable and testable.
