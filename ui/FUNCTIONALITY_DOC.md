# 3D Maps UI Functionality Documentation

## Overview
This document catalogs all UI features in `index.html`, their implementation status, and identifies unused code.

---

## Code Cleanup Completed (This Session)

### Removed Dead Code
1. **`renderLanduseCanvas()`** - Never called, replaced by `renderEsaLandCover()`
2. **`toggleCoordsTable()` / `populateCoordsTable()`** - Never called, no button to trigger
3. **`drawZoomedAndPannedCanvas()`** - Empty function, CSS transforms used instead
4. **`#coordsTable` CSS** - Orphaned styles for removed functionality
5. **`.dem-subtab` CSS and JS** - Replaced by `.layer-tab`, all references removed

### Lines Removed: ~100 lines
### Current File Size: ~8163 lines (was ~8303)

---

## Expected UI Features (Based on Conversation History)

### ✅ IMPLEMENTED & WORKING

#### 1. Main Tab Navigation (Explore/Edit/Extrude)
- **Location**: Lines 192-217 (CSS), ~2200 (HTML)
- **Function**: `switchView(view)` - Line 6115
- **Status**: ✅ Working, tabs renamed from Map/DEM/Model

#### 2. Layer Tabs (Layers/Compare)
- **Location**: Lines 1593-1630 (CSS), ~2450 (HTML)
- **Function**: `switchDemSubtab(subtab)` - Line 7555
- **Status**: ✅ Working, smaller horizontal layout

#### 3. Stacked Layers View with Alignment
- **Location**: Lines ~5528-5790 (JS)
- **Functions**: 
  - `setupStackedLayers()` - Line 5528
  - `updateStackedLayers()` - Line 5557
  - `drawLayerToTarget()` - Line 5609
  - `applyStackedTransform()` - Line 5776
- **Status**: ✅ Working, layers aligned using common bbox-based target rectangle

#### 4. Collapsible Histogram + Elevation Curves (Merged)
- **Location**: Lines ~2580 (HTML), ~6855 (JS)
- **Functions**: 
  - `drawHistogram()` - Line 6855
  - `toggleCollapsible()` - Line 3180
- **Status**: ✅ Working, histogram collapsible with cumulative (80px height)

#### 5. Elevation Curve Editor
- **Location**: Lines ~4525-4820 (JS)
- **Functions**:
  - `initCurveEditor()` - Line 4525
  - `setupCurveEventListeners()` - Line 4543
  - `setCurvePreset()` - Line 4625
  - `drawCurve()` - Line 4667
  - `applyCurveTodem()` - Line 4738
  - `applyCurveTodemSilent()` - Line 4770 (auto-apply)
- **Status**: ✅ Working with auto-rerender on curve changes

#### 6. Collapsible Layer Settings Panel
- **Location**: Lines 1593-1680 (CSS), ~2485 (HTML)
- **Function**: `toggleLayersControls(header)` - Line 3205
- **Status**: ✅ Working with toggle header

#### 7. 3-State Sidebar (Normal → Expanded → Hidden)
- **Location**: Lines 6950-7050 (JS)
- **Functions**:
  - `cycleSidebarState()` - Line 6953
- **Status**: ✅ Working

#### 8. Leaflet Map with DEM Overlay
- **Location**: Lines ~3771-3940 (JS)
- **Functions**:
  - `initMap()` - Line 3771
  - `toggleDemOverlay()` - Line 3610
  - `toggleTerrainOverlay()` - Line 3717
  - `setTileLayer()` - Line 3591
- **Status**: ✅ Working

#### 9. Globe View (Three.js)
- **Location**: Lines ~3950-4100 (JS)
- **Functions**:
  - `initGlobe()` - Line 3950
  - `animateGlobe()` - Line 3993
  - `updateGlobeMarkers()` - Line 4078
  - `createGlobeMarker()` - Line 4105
- **Status**: ✅ Working

#### 10. Coordinate/Region Selection
- **Location**: Lines ~4000-4175 (JS)
- **Functions**:
  - `loadCoordinates()` - Line 4000
  - `selectCoordinate()` - Line 4121
  - `renderCoordinatesList()` - Line 5106
  - `populateRegionsTable()` - Line 5167
- **Status**: ✅ Working

#### 11. DEM Rendering with Colormaps
- **Location**: Lines ~6555-6810 (JS)
- **Functions**:
  - `renderDEMCanvas()` - Line 6555
  - `mapElevationToColor()` - Line 6747
  - `drawColorbar()` - Line 6815
  - `recolorDEM()` - Line 6021
- **Status**: ✅ Working

#### 12. Water Mask / ESA Land Cover
- **Location**: Lines ~7700-7850 (JS)
- **Functions**:
  - `loadWaterMask()` - Line 7588
  - `renderWaterMask()` - Line 7775
  - `renderEsaLandCover()` - Line 7800
- **Status**: ✅ Working with layer stack integration

#### 13. 3D Model Generation & Export
- **Location**: Lines ~7100-7500 (JS)
- **Functions**:
  - `generateModelFromTab()` - Line 7175
  - `downloadSTL()` - Line 7241
  - `downloadModel()` - Line 7305
  - `initModelViewer()` - Line 7378
  - `createTerrainMesh()` - Line 7464
  - `previewModelIn3D()` - Line 7509
- **Status**: ✅ Working

#### 14. Preset Profiles (Save/Load/Delete)
- **Location**: Lines ~4881-5080 (JS)
- **Functions**:
  - `initPresetProfiles()` - Line 4881
  - `loadSelectedPreset()` - Line 4949
  - `applyPreset()` - Line 4973
  - `saveNewPreset()` - Line 5025
  - `deleteSelectedPreset()` - Line 5057
- **Status**: ✅ Working

#### 15. Cache Management (Client + Server)
- **Location**: Lines ~3364-3500 (JS)
- **Functions**:
  - `updateCacheStatusUI()` - Line 3364
  - `fetchServerCacheStatus()` - Line 3377
  - `preloadAllRegions()` - Line 3396
  - `clearClientCache()` - Line 3476
  - `clearServerCache()` - Line 3484
- **Status**: ✅ Working

#### 16. Region Notes Modal
- **Location**: Lines ~5263-5330 (JS)
- **Functions**:
  - `initRegionNotes()` - Line 5263
  - `showNotesModal()` - Line 5292
  - `saveRegionNotes()` - Line 5310
- **Status**: ✅ Working

#### 17. Favorites System
- **Location**: Lines ~5084-5105 (JS)
- **Functions**:
  - `initFavorites()` - Line 5084
  - `toggleFavorite()` - Line 5241
- **Status**: ✅ Working

#### 18. Compare Mode
- **Location**: Lines ~5336-5440 (JS)
- **Functions**:
  - `initCompareMode()` - Line 5336
  - `updateCompareCanvases()` - Line 5360
  - `loadCompareRegion()` - Line 5365
- **Status**: ✅ Working (inline mode)

#### 19. DEM Hover Tooltip
- **Location**: Lines ~8085-8150 (JS)
- **Function**: `setupHoverTooltip()` - Line 8085
- **Status**: ✅ Working

#### 20. Zoom/Pan on Layers
- **Location**: Lines ~5724-5785 (stacked), ~8153-8250 (DEM)
- **Functions**:
  - `enableStackedZoomPan()` - Line 5724
  - `enableZoomAndPan()` - Line 8153
- **Status**: ✅ Working

---

## ⚠️ USEFUL BUT UNDERUTILIZED FEATURES

### 1. High-Resolution DEM Loading
- **Function**: `loadHighResDEM()` - Line 6433
- **Usage**: Available but button may be hidden
- **Recommendation**: Keep - useful for detailed exports

### 2. Grid Overlay Toggle
- **Function**: `drawGridlinesOverlay()` - Line 6444
- **Controls**: `showGridlines`, `gridlineCount` checkboxes
- **Usage**: In visualization settings, works but less discoverable
- **Recommendation**: Keep

### 3. Rescale DEM Values
- **Functions**: `rescaleDEM()` - Line 6050, `resetRescale()` - Line 6089
- **Usage**: Working but inputs may be hidden in collapsed panel
- **Recommendation**: Keep

### 4. Auto-Reload on Changes
- **Function**: `setupAutoReload()` - Line 5913
- **Usage**: Sets up observer, but may not be fully utilized
- **Recommendation**: Keep

### 5. Satellite Color Mapping Table
- **Location**: HTML around line ~2780
- **Usage**: ESA land cover legend - informational only
- **Recommendation**: Keep

### 6. Keyboard Shortcuts
- **Function**: `setupKeyboardShortcuts()` - Line 4441
- **Usage**: Defined but shortcuts may not be documented to user
- **Recommendation**: Keep, add user documentation

---

## ✅ CLEANED UP (This Session)

The following dead code was removed:

### 1. `renderLanduseCanvas()` function
- **Was at**: Line ~6650
- **Reason**: Never called - replaced by `renderEsaLandCover()`

### 2. `toggleCoordsTable()` and `populateCoordsTable()` functions
- **Was at**: Lines ~7035-7060
- **Reason**: Button `#toggleCoords` doesn't exist - functions never called

### 3. `drawZoomedAndPannedCanvas()` function  
- **Was at**: Line ~8276
- **Reason**: Empty function - CSS transforms used instead

### 4. `#coordsTable` CSS styles
- **Was at**: Lines ~1109-1135
- **Reason**: No HTML element uses these styles

### 5. `.dem-subtab` CSS and JS references
- **Was at**: CSS lines ~859-890, JS multiple locations
- **Reason**: All tabs now use `.layer-tab` class only

---

## ⚠️ REMAINING ITEMS TO CONSIDER

### Hidden Containers (KEEP - Still Used)
The following containers appear hidden but their child divs are used as render targets:
```html
<div id="demSubtabContent" class="hidden">  <!-- Contains #demImage -->
<div id="waterMaskContainer" class="hidden"> <!-- Contains #waterMaskImage -->
<div id="satelliteContainer" class="hidden"> <!-- Contains #satelliteImage -->
<div id="combinedContainer" class="hidden">  <!-- Contains #combinedImage -->
```
**Status**: These ARE still referenced by JavaScript for canvas rendering. DO NOT REMOVE.

### Legacy `#regionsContainer`
- May be fully unused - needs further verification
- Contains old regions table structure

---

## CSS Classes to Review
- `.compare-container` - Legacy compare view styles
- `.satellite-container` - Legacy satellite view
- `.water-container` - Legacy water mask view
- `.combined-container` - Legacy combined view

### CSS that should be kept
- `.layers-controls` - Active layer settings panel
- `.collapsible-*` - Used for all collapsible sections
- `.layer-tab` - Current tab system
- `.stacked-*` - Stacked layers view

---

## Recommendations Summary

### Keep but Document
1. `loadHighResDEM()` - useful feature
2. Keyboard shortcuts
3. Preset profiles system
4. Cache management

---

## File Size Analysis

### Before Cleanup
- Total Lines: ~8303

### After Cleanup  
- Total Lines: ~8163
- **Lines Removed: ~140 lines**

### Breakdown
- CSS: ~2070 lines
- HTML: ~1000 lines
- JavaScript: ~5090 lines

---

## Session Summary

### Completed Tasks
1. ✅ Audited full code structure (8303 lines)
2. ✅ Documented 20+ working UI features
3. ✅ Identified 6+ unused code sections
4. ✅ Removed 5 dead code sections (~140 lines)
5. ✅ Created this functionality documentation

### Code Quality Improvements
- Removed never-called functions
- Cleaned up orphaned CSS
- Unified tab class references (`.layer-tab` only)
- Removed empty placeholder functions

---

*Last Updated: Current Session*
*Author: Code Audit Tool*
