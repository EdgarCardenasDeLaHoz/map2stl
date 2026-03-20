/* DEAD — not loaded by app.js or index.html. Editing this file has no effect on the running app. */
/**
 * Global State Management Module
 * Centralized state for the 3D Maps application
 *
 * NOTE (2026-03-18): This file is an ES module and is NOT currently imported by app.js.
 * The running application uses loose closure variables in app.js plus window.appState
 * for cross-module access. This file documents the intended unified state schema.
 * See: ui/static/js/app.js window.appState block for the live state object.
 */

// Application state singleton
const state = {
  // Map state
  map: null,
  drawnItems: null,
  preloadedLayer: null,
  boundingBox: null,
  currentBboxColorIndex: 0,

  // Globe state (Three.js)
  globe: {
    scene: null,
    camera: null,
    renderer: null,
    mesh: null
  },

  // Region data
  coordinates: [],           // coordinatesData — array of {name, label, north, south, east, west}
  selectedRegion: null,      // currently selected region object

  // DEM & layer bounding boxes
  currentDemBbox: null,      // {north, south, east, west} for the currently rendered DEM
  layerBboxes: {             // bbox recorded when each layer was last loaded
    dem: null,
    water: null,
    landCover: null
  },

  // Layer loading status: 'empty' | 'loading' | 'loaded' | 'error'
  layerStatus: {
    dem: 'empty',
    water: 'empty',
    landCover: 'empty'
  },

  // Active DEM sub-tab: 'dem' | 'water' | 'landcover' | 'combined' | 'satellite'
  activeDemSubtab: 'dem',

  // Cached data from API responses
  cache: {
    dem: null,               // lastDemData — {values, width, height, min, max, bbox}
    waterMask: null,         // lastWaterMaskData
    esa: null,               // lastEsaData
    rawDem: null,            // lastRawDemData
    model: null              // generatedModelData
  },

  // City / OSM overlay data
  osmCityData: null,         // {nodes, ways, relations} from Overpass API
};

// Color palette for different bounding boxes (distinct, easy to tell apart)
export const BBOX_COLORS = [
  { color: '#ff4444', name: 'Red' },
  { color: '#44aaff', name: 'Blue' },
  { color: '#44ff44', name: 'Green' },
  { color: '#ffaa00', name: 'Orange' },
  { color: '#ff44ff', name: 'Magenta' },
  { color: '#00ffff', name: 'Cyan' },
  { color: '#ffff44', name: 'Yellow' },
  { color: '#aa44ff', name: 'Purple' },
];

/**
 * Get the current application state.
 * @returns {Object} The current state
 */
export function getState() {
  return state;
}

/**
 * Update state properties (shallow merge).
 * @param {Object} updates - Object with properties to update
 */
export function updateState(updates) {
  Object.assign(state, updates);
}

/**
 * Update a specific cache entry.
 * @param {string} key - Cache key ('dem', 'waterMask', 'esa', 'rawDem', 'model')
 * @param {*} data - Data to cache
 */
export function updateCache(key, data) {
  if (Object.prototype.hasOwnProperty.call(state.cache, key)) {
    state.cache[key] = data;
  } else {
    console.warn(`Unknown cache key: ${key}`);
  }
}

/**
 * Get a cached value.
 * @param {string} key - Cache key
 * @returns {*} The cached data or null
 */
export function getCache(key) {
  return state.cache[key] || null;
}

/**
 * Clear all cached layer data.
 */
export function clearCache() {
  Object.keys(state.cache).forEach(key => {
    state.cache[key] = null;
  });
  state.currentDemBbox = null;
  state.layerBboxes = { dem: null, water: null, landCover: null };
  state.layerStatus = { dem: 'empty', water: 'empty', landCover: 'empty' };
}

/**
 * Get the next bounding box color and increment the index.
 * @returns {string} The color hex code
 */
export function getNextBboxColor() {
  const colorObj = BBOX_COLORS[state.currentBboxColorIndex % BBOX_COLORS.length];
  state.currentBboxColorIndex++;
  return colorObj.color;
}

/**
 * Reset the bounding box color index.
 */
export function resetBboxColorIndex() {
  state.currentBboxColorIndex = 0;
}

/**
 * Get current bounds from either bounding box or selected region.
 * @returns {Object|null} Bounds object with north, south, east, west or null
 */
export function getCurrentBounds() {
  if (state.boundingBox) {
    return {
      north: state.boundingBox.getNorth(),
      south: state.boundingBox.getSouth(),
      east: state.boundingBox.getEast(),
      west: state.boundingBox.getWest()
    };
  }
  if (state.selectedRegion) {
    return {
      north: state.selectedRegion.north,
      south: state.selectedRegion.south,
      east: state.selectedRegion.east,
      west: state.selectedRegion.west
    };
  }
  return null;
}

/**
 * Check if we have a valid selection (bounding box or region).
 * @returns {boolean}
 */
export function hasSelection() {
  return state.boundingBox !== null || state.selectedRegion !== null;
}

// Expose state for debugging
if (typeof window !== 'undefined') {
  window.__appState = state;
}