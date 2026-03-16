/**
 * Global State Management Module
 * Centralized state for the 3D Maps application
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

  // Data state
  coordinates: [],
  selectedRegion: null,
  currentBbox: null,

  // Cached data from API responses
  cache: {
    dem: null,           // lastDemData
    waterMask: null,     // lastWaterMaskData
    esa: null,           // lastEsaData
    rawDem: null,        // lastRawDemData
    model: null          // generatedModelData
  }
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
 * Get the current application state
 * @returns {Object} The current state
 */
export function getState() {
  return state;
}

/**
 * Update state properties
 * @param {Object} updates - Object with properties to update
 */
export function updateState(updates) {
  Object.assign(state, updates);
}

/**
 * Update a specific cache entry
 * @param {string} key - Cache key ('dem', 'waterMask', 'esa', 'rawDem', 'model')
 * @param {*} data - Data to cache
 */
export function updateCache(key, data) {
  if (state.cache.hasOwnProperty(key)) {
    state.cache[key] = data;
  } else {
    console.warn(`Unknown cache key: ${key}`);
  }
}

/**
 * Get a cached value
 * @param {string} key - Cache key
 * @returns {*} The cached data or null
 */
export function getCache(key) {
  return state.cache[key] || null;
}

/**
 * Clear all cached data
 */
export function clearCache() {
  Object.keys(state.cache).forEach(key => {
    state.cache[key] = null;
  });
}

/**
 * Get the next bounding box color and increment the index
 * @returns {string} The color hex code
 */
export function getNextBboxColor() {
  const colorObj = BBOX_COLORS[state.currentBboxColorIndex % BBOX_COLORS.length];
  state.currentBboxColorIndex++;
  return colorObj.color;
}

/**
 * Reset the bounding box color index
 */
export function resetBboxColorIndex() {
  state.currentBboxColorIndex = 0;
}

/**
 * Get current bounds from either bounding box or selected region
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
 * Check if we have a valid selection (bounding box or region)
 * @returns {boolean}
 */
export function hasSelection() {
  return state.boundingBox !== null || state.selectedRegion !== null;
}

// Export state for debugging (remove in production)
if (typeof window !== 'undefined') {
  window.__appState = state;
}
