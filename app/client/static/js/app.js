// ============================================================
// FILE-TOP HELPERS (available before DOMContentLoaded)
// ============================================================

// ============================================================
// GLOBAL STATE
// All application state lives on window.appState (reactive Proxy
// from modules/state.js).  Modules subscribe via .on('key', fn).
// Legacy window.get*/set* aliases kept for backward compatibility.
// ============================================================

// Shared state object — exposed on window so extracted modules can read/write it.
// All application state lives on the reactive Proxy created by modules/state.js.
// Modules can subscribe via window.appState.on('key', fn).
// Do NOT reassign window.appState — that would destroy the Proxy and its listeners.
if (!window.appState?.set) window.appState = {};   // fallback if state.js not loaded

// Map & globe instances (set once during init by map-globe.js)
window.appState.map = null;
window.appState.globeScene = null;
window.appState.globeCamera = null;
window.appState.globeRenderer = null;
window.appState.globe = null;

// Map layers
window.appState.drawnItems = null;
window.appState.preloadedLayer = null;    // layer for rectangles loaded from saved coordinates
window.appState.editMarkersLayer = null;  // permanent Edit buttons inside each bbox
window.appState.boundingBox = null;

// Region management
window.appState.coordinatesData = [];
window.appState.selectedRegion = null;

// DEM / layer data
window.appState.currentDemBbox = null;
window.appState.osmCityData = null;
window.appState.lastDemData = null;
window.appState.lastWaterMaskData = null;
window.appState.satEsaLoaded = false;

// Shared helpers (set later by modules)
window.appState.showToast = null;
window.appState.haversineDiagKm = null;

// Land cover configuration — owned by water-mask.js; exposed on window.appState.
window.appState.landCoverConfig = {
    10: { name: 'Tree Cover', color: [0, 100, 0], elevation: 0.1 },
    20: { name: 'Shrubland', color: [255, 187, 34], elevation: 0.05 },
    30: { name: 'Grassland', color: [255, 255, 76], elevation: 0.02 },
    40: { name: 'Cropland', color: [240, 150, 255], elevation: 0.0 },
    50: { name: 'Built-up', color: [250, 0, 0], elevation: 0.15 },
    60: { name: 'Bare/Sparse', color: [180, 180, 180], elevation: 0.0 },
    70: { name: 'Snow/Ice', color: [240, 240, 240], elevation: 0.0 },
    80: { name: 'Water', color: [0, 100, 200], elevation: -0.1 },
    90: { name: 'Wetland', color: [0, 150, 160], elevation: -0.02 },
    95: { name: 'Mangroves', color: [0, 207, 117], elevation: 0.0 },
    100: { name: 'Moss/Lichen', color: [250, 230, 160], elevation: 0.0 },
    0: { name: 'No Data/Ocean', color: [0, 50, 150], elevation: -0.15 }
};
window.appState.landCoverConfigDefaults = JSON.parse(JSON.stringify(window.appState.landCoverConfig));

// Track the bbox that each layer was loaded for
window.appState.layerBboxes = {
    dem: null,
    water: null,
    landCover: null
};

// Layer loading status: 'empty' | 'loading' | 'loaded' | 'error'
window.appState.layerStatus = {
    dem: 'empty',
    water: 'empty',
    landCover: 'empty'
};

// DEM + export parameters — single source of truth, replaces hidden DOM inputs.
window.appState.demParams = {
    dim: 200,
    depthScale: 0.5,
    waterScale: 0.05,
    subtractWater: true,
    satScale: 500,
    height: 10,
    base: 2,
};

// (lastAppliedPresetName moved to modules/presets.js)

/**
 * Clear all cached layer data
 * Call this when changing regions to prevent stale data
 */
function clearLayerCache() {
    window.appState.lastDemData = null;
    window.clearLastWaterMaskData?.();
    window.appState.currentDemBbox = null;
    window._setDemEmptyState?.(true);
    window.appState.originalDemValues = null;  // Reset so next Apply uses new region's data
    window.appState.curveDataVmin = null;  // Reset stable curve coordinate system
    window.appState.curveDataVmax = null;

    // Reset layer tracking
    window.appState.layerBboxes = { dem: null, water: null, landCover: null };
    window.appState.layerStatus = { dem: 'empty', water: 'empty', landCover: 'empty' };
    window._clearCityRasterCache?.();
    window.appState.cityRasterSourceCanvas = null;
    window.appState.compositeDemSourceCanvas = null;
    window.appState.compositeFeatures = null;
    window.appState.compositeCityRaster = null;
    window.appState.satImgSourceCanvas = null;
    window.appState._satImgRawCanvas = null;
    window.appState._satImgBbox = null;
    window.appState.satEsaLoaded = false;
    window.appState.hydrologySourceCanvas = null;

    // Free all DOM layer buffer canvases and reset the stacked view
    window.clearAllLayerBuffers?.();

    // Update status indicators
    window['events']?.emit(window.EV?.STATUS_UPDATE);
}

/**
 * Clear any visual layer displays from the UI (DEM, water, satellite, combined)
 * Call this when changing regions so previously-loaded images/overlays are removed.
 */
function clearLayerDisplays() {
    const placeholders = {
        demImage: '<p style="text-align:center;padding:50px;color:#888;">Select a region to view DEM</p>',
        waterMaskImage: '<p style="text-align:center;padding:50px;color:#888;">Select a region to view water mask</p>',
        satelliteImage: '<p style="text-align:center;padding:50px;color:#888;">Select a region to view land cover</p>',
        combinedImage: '<p style="text-align:center;padding:50px;color:#888;">Select a region to view combined layers</p>'
    };

    Object.keys(placeholders).forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            // remove any attached canvases or images
            el.innerHTML = placeholders[id];
        }
    });

    // remove any gridline overlays
    document.querySelectorAll('.dem-gridlines-overlay').forEach(n => n.remove());
}

/**
 * Get current bounding box as object
 */
function getCurrentBboxObject() {
    let bounds;
    const boundingBox = window.appState.boundingBox;
    const selectedRegion = window.appState.selectedRegion;
    if (boundingBox) {
        // boundingBox is a Leaflet layer (L.rectangle) — extract its LatLngBounds
        bounds = typeof boundingBox.getBounds === 'function'
            ? boundingBox.getBounds()
            : boundingBox;
    } else if (selectedRegion) {
        return {
            north: selectedRegion.north,
            south: selectedRegion.south,
            east: selectedRegion.east,
            west: selectedRegion.west
        };
    } else {
        return null;
    }

    return {
        north: bounds.getNorth(),
        south: bounds.getSouth(),
        east: bounds.getEast(),
        west: bounds.getWest()
    };
}

/**
 * Check if a layer's bbox matches current bbox
 */
function isLayerCurrent(layerName) {
    const currentBbox = getCurrentBboxObject();
    const layerBbox = window.appState.layerBboxes[layerName];

    if (!currentBbox || !layerBbox) return false;

    const epsilon = 0.0001;
    return Math.abs(currentBbox.north - layerBbox.north) < epsilon &&
        Math.abs(currentBbox.south - layerBbox.south) < epsilon &&
        Math.abs(currentBbox.east - layerBbox.east) < epsilon &&
        Math.abs(currentBbox.west - layerBbox.west) < epsilon;
}
window.getCurrentBboxObject = getCurrentBboxObject;

// BBOX_COLORS and currentBboxColorIndex defined in modules/map-globe.js
// and exposed as window.BBOX_COLORS / window.resetBboxColorIndex there.

// ============================================================
// INITIALIZATION
// ============================================================

document.addEventListener('DOMContentLoaded', async function () {
    console.log('DOM loaded, initializing app...');

    // Check if required libraries are loaded
    if (typeof L === 'undefined') {
        console.error('Leaflet library not loaded!');
        document.getElementById('coordinatesList').innerHTML = '<div class="loading" style="color:red">Error: Leaflet library failed to load. Try refreshing or use a different browser.</div>';
        // Still try to load coordinates without map
        await loadCoordinates();
        return;
    }

    console.log('Leaflet loaded:', typeof L);
    console.log('Three.js loaded:', typeof THREE);

    try {
        window.initMap?.();
        console.log('Map initialized');
    } catch (e) {
        console.error('Error initializing map:', e);
    }

    try {
        window.initGlobe?.();
        console.log('Globe initialized');
    } catch (e) {
        console.error('Error initializing globe:', e);
    }

    await window.loadCoordinates?.();
    console.log('Coordinates loaded');

    window.setupEventListeners?.();
    window.setupDemSubtabs?.();
    window.setupWaterMaskListeners?.();
    window.setupGridToggle?.();
    window.setupBboxKeyboardNav?.();
    window.setupCacheManagement?.();

    // Start in expanded sidebar state by default
    window.appState.sidebarState = 'expanded';
    const _sidebar = document.getElementById('sidebar');
    if (_sidebar) { _sidebar.classList.remove('collapsed'); _sidebar.classList.add('expanded'); }
    const _toggleBtn = document.getElementById('sidebarToggleBtn');
    if (_toggleBtn) {
        const _icon = _toggleBtn.querySelector('.state-icon');
        const _lbl = _toggleBtn.querySelector('.state-label');
        if (_icon) _icon.textContent = '⇐';
        if (_lbl) _lbl.textContent = 'Hide';
    }
    window._setSidebarViews?.('expanded');

    // Load available DEM sources and show API key warning if needed
    window._initDemSources?.();

    // Initialize merge panel
    window.setupMergePanel?.();

    // Activate default tab (map) so tab button gets .active class
    window.switchView?.('map');

    console.log('App initialization complete');
});

// Backward-compat aliases — modules should prefer window.appState.xxx directly.
window.getCoordinatesData = () => window.appState.coordinatesData;
window.getBoundingBox = () => window.appState.boundingBox;

window.setCoordinatesData = (d) => { window.appState.coordinatesData = d; };
window.setSelectedRegion = (r) => { window.appState.selectedRegion = r; };
window['getSelectedRegion'] = () => window.appState.selectedRegion;
window.setBoundingBox = (b) => { window.appState.boundingBox = b; };
window.setMap = (m) => { window.appState.map = m; };
window.getMap = () => window.appState.map;
window.setPreloadedLayer = (l) => { window.appState.preloadedLayer = l; };
window.getPreloadedLayer = () => window.appState.preloadedLayer;
window.setEditMarkersLayer = (l) => { window.appState.editMarkersLayer = l; };
window.getEditMarkersLayer = () => window.appState.editMarkersLayer;
window.setDrawnItems = (d) => { window.appState.drawnItems = d; };
window.getDrawnItems = () => window.appState.drawnItems;
window.setGlobeScene = (s) => { window.appState.globeScene = s; };
window.getGlobeScene = () => window.appState.globeScene;
window.setGlobeCamera = (c) => { window.appState.globeCamera = c; };
window.setGlobeRenderer = (r) => { window.appState.globeRenderer = r; };
window.setGlobe = (g) => { window.appState.globe = g; };
window.setSidebarState = (s) => { window.appState.sidebarState = s; };

window.clearLayerDisplays = clearLayerDisplays;
window.clearLayerCache = clearLayerCache;
window.isLayerCurrent = isLayerCurrent;

// Appearance
window.appState.waterOpacity = 0.7;
window.getWaterOpacity = () => window.appState.waterOpacity;
window.setWaterOpacity = (v) => { window.appState.waterOpacity = v; };

// ============================================================
// SIDEBAR
// ============================================================

window.appState.sidebarState = 'expanded'; // 'normal', 'expanded', 'hidden'
window.getSidebarState = () => window.appState.sidebarState;

// Open sidebar from floating button (goes to normal state)
document.getElementById('openSidebarBtn').addEventListener('click', () => {
    const sidebar = document.getElementById('sidebar');
    const toggleBtn = document.getElementById('sidebarToggleBtn');
    const icon = toggleBtn.querySelector('.state-icon');
    const label = toggleBtn.querySelector('.state-label');

    window.appState.sidebarState = 'normal';
    window.__setSidebarModeFromLegacy?.('normal');
    window._setSidebarViews?.('normal');
    sidebar.classList.remove('collapsed', 'expanded');
    document.getElementById('regionParamsSection').classList.add('hidden');
    document.getElementById('openSidebarBtn').classList.add('hidden');
    icon.textContent = '⇔';
    label.textContent = 'Expand';
});

// ============================================================
// 3D MODEL VIEWER & EXPORT
// ============================================================

window.appState.generatedModelData = null;

window.appState._applyCurveSettings = function (points, presetName) {
    window.applyCurveSettings?.(points, presetName);
};

document.addEventListener('DOMContentLoaded', () => {
    window._setExportButtonsEnabled?.(false);
    window._setDemEmptyState?.(true);
    window._updateWorkflowStepper?.();
});

window.appState.haversineDiagKm = (...args) => window.haversineDiagKm?.(...args);
