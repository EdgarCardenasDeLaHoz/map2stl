// ============================================================
// FILE-TOP HELPERS (available before DOMContentLoaded)
// ============================================================

// ============================================================
// GLOBAL STATE
// All application state lives as closure variables inside
// DOMContentLoaded (or at file-top scope for pre-init state).
// Shared state is mirrored to window.appState (see modules/state.js).
// ============================================================

// Global variables
let map;
let globeScene, globeCamera, globeRenderer, globe;
let drawnItems;
let preloadedLayer;  // layer for rectangles loaded from saved coordinates
let editMarkersLayer;  // permanent Edit buttons inside each bbox
let boundingBox;
let coordinatesData = [];
let selectedRegion = null;

// Shared state object — exposed on window so extracted modules can read/write it.
// Updated wherever these variables change throughout this file.
// Initialise state keys on the reactive Proxy created by modules/state.js.
// Do NOT reassign window.appState — that would destroy the Proxy and its listeners.
if (!window.appState?.set) window.appState = {};   // fallback if state.js not loaded
window.appState.selectedRegion = null;
window.appState.currentDemBbox = null;
window.appState.osmCityData = null;
window.appState.lastDemData = null;
window.appState.lastWaterMaskData = null;
window.appState.showToast = null;
window.appState.haversineDiagKm = null;
let lastDemData = null;

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
let layerBboxes = {
    dem: null,
    water: null,
    landCover: null
};

// Layer loading status: 'empty' | 'loading' | 'loaded' | 'error'
let layerStatus = {
    dem: 'empty',
    water: 'empty',
    landCover: 'empty'
};

// Mirror layerBboxes and layerStatus to window.appState (shared references; property
// mutations are auto-visible to modules).  Full-object reassignments (clearLayerCache)
// must re-sync window.appState manually — see clearLayerCache() below.
window.appState.layerBboxes = layerBboxes;
window.appState.layerStatus = layerStatus;

// DEM + export parameters — single source of truth, replaces hidden DOM inputs.
window.appState.demParams = {
    dim:           200,
    depthScale:    0.5,
    waterScale:    0.05,
    subtractWater: true,
    satScale:      500,
    height:        10,
    base:          2,
};

// (lastAppliedPresetName moved to modules/presets.js)

/**
 * Clear all cached layer data
 * Call this when changing regions to prevent stale data
 */
function clearLayerCache() {
    lastDemData = null;
    window.clearLastWaterMaskData?.();
    currentDemBbox = null;
    window.appState.currentDemBbox = null;
    window.appState.lastDemData = null;
    window._setDemEmptyState?.(true);
    window.appState.originalDemValues = null;  // Reset so next Apply uses new region's data
    window.appState.curveDataVmin = null;  // Reset stable curve coordinate system
    window.appState.curveDataVmax = null;

    // Reset layer tracking
    layerBboxes = { dem: null, water: null, landCover: null };
    layerStatus = { dem: 'empty', water: 'empty', landCover: 'empty' };
    window.appState.layerBboxes = layerBboxes;
    window.appState.layerStatus = layerStatus;
    window._clearCityRasterCache?.();
    window.appState.cityRasterSourceCanvas = null;
    window.appState.compositeDemSourceCanvas = null;
    window.appState.compositeFeatures = null;
    window.appState.compositeCityRaster = null;
    window.appState.satImgSourceCanvas = null;
    window.appState._satImgRawCanvas = null;
    window.appState._satImgBbox = null;

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
    if (boundingBox) {
        bounds = boundingBox;
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
    const layerBbox = layerBboxes[layerName];

    if (!currentBbox || !layerBbox) return false;

    const epsilon = 0.0001;
    return Math.abs(currentBbox.north - layerBbox.north) < epsilon &&
        Math.abs(currentBbox.south - layerBbox.south) < epsilon &&
        Math.abs(currentBbox.east - layerBbox.east) < epsilon &&
        Math.abs(currentBbox.west - layerBbox.west) < epsilon;
}

window.getCurrentBboxObject = getCurrentBboxObject;
window.isLayerCurrent = isLayerCurrent;

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
    window.setupCacheManagement?.();

    // Start in expanded sidebar state by default
    sidebarState = 'expanded';
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


    console.log('App initialization complete');
});

// Expose closure vars + functions needed by extracted modules.
window.getCoordinatesData = () => coordinatesData;
window.getBoundingBox = () => boundingBox;

// Setters so extracted modules can write back to app.js closure vars.
window.setCoordinatesData = (d) => { coordinatesData = d; };
window.setSelectedRegion = (r) => { selectedRegion = r; };
window['getSelectedRegion'] = () => selectedRegion;
window.setBoundingBox = (b) => { boundingBox = b; };
window.setMap = (m) => { map = m; };
window.getMap = () => map;
window.setPreloadedLayer = (l) => { preloadedLayer = l; };
window.getPreloadedLayer = () => preloadedLayer;
window.setEditMarkersLayer = (l) => { editMarkersLayer = l; };
window.getEditMarkersLayer = () => editMarkersLayer;
window.setDrawnItems = (d) => { drawnItems = d; };
window.getDrawnItems = () => drawnItems;
window.setGlobeScene = (s) => { globeScene = s; };
window.getGlobeScene = () => globeScene;
window.setGlobeCamera = (c) => { globeCamera = c; };
window.setGlobeRenderer = (r) => { globeRenderer = r; };
window.setGlobe = (g) => { globe = g; };
window.setSidebarState = (s) => { sidebarState = s; };

window.clearLayerDisplays = clearLayerDisplays;
window.clearLayerCache = clearLayerCache;

// Unified opacity values
let waterOpacity = 0.7;
window.getWaterOpacity = () => waterOpacity;
window.setWaterOpacity = (v) => { waterOpacity = v; };

// ============================================================
// DEM LOADING & RENDERING
// ============================================================

// Current bounding box for gridlines (updated when DEM loads)
let currentDemBbox = null;

// ============================================================
// SIDEBAR
// ============================================================

let sidebarState = 'normal'; // 'normal', 'expanded', 'hidden'
window.getSidebarState = () => sidebarState;

// Open sidebar from floating button (goes to normal state)
document.getElementById('openSidebarBtn').addEventListener('click', () => {
    const sidebar = document.getElementById('sidebar');
    const toggleBtn = document.getElementById('sidebarToggleBtn');
    const icon = toggleBtn.querySelector('.state-icon');
    const label = toggleBtn.querySelector('.state-label');

    sidebarState = 'normal';
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
