/**
 * Map Component Module
 * Leaflet map initialization and handlers
 */

import { getState, updateState, getNextBboxColor, BBOX_COLORS } from '../state.js';

/**
 * Initialize the Leaflet map
 * @param {string} containerId - DOM element ID for the map
 * @param {Object} options - Map options
 * @returns {Object} Leaflet map instance
 */
export function initMap(containerId = 'map', options = {}) {
  const {
    center = [20, 0],
    zoom = 2,
    tileUrl = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  } = options;

  // Check if Leaflet is loaded
  if (typeof L === 'undefined') {
    console.error('Leaflet library not loaded!');
    return null;
  }

  const state = getState();

  // Create map
  const map = L.map(containerId).setView(center, zoom);

  // Add tile layer
  L.tileLayer(tileUrl, { attribution }).addTo(map);

  // Create feature groups for layers
  const preloadedLayer = new L.FeatureGroup().addTo(map);
  const drawnItems = new L.FeatureGroup().addTo(map);

  // Add draw control
  const drawControl = new L.Control.Draw({
    draw: {
      rectangle: {
        shapeOptions: {
          color: '#ff4444',
          weight: 3,
          fillOpacity: 0.2
        }
      },
      polygon: false,
      circle: false,
      marker: false,
      polyline: false
    },
    edit: {
      featureGroup: drawnItems
    }
  });
  map.addControl(drawControl);

  // Handle draw events
  map.on(L.Draw.Event.CREATED, function (event) {
    const layer = event.layer;
    const bboxColor = getNextBboxColor();

    layer.setStyle({
      color: bboxColor,
      weight: 3,
      fillColor: bboxColor,
      fillOpacity: 0.15
    });

    drawnItems.addLayer(layer);

    // Update state
    updateState({
      boundingBox: layer.getBounds()
    });

    // Dispatch event for other components
    document.dispatchEvent(new CustomEvent('boundingBoxCreated', {
      detail: {
        bounds: layer.getBounds(),
        color: bboxColor
      }
    }));
  });

  // Store references in state
  updateState({
    map,
    preloadedLayer,
    drawnItems
  });

  return map;
}

/**
 * Draw rectangles for all saved regions
 * @param {Array} regions - Array of region objects
 */
export function drawRegionRectangles(regions) {
  const state = getState();
  const { preloadedLayer } = state;

  if (!preloadedLayer) {
    console.warn('Preloaded layer not initialized');
    return;
  }

  // Clear existing rectangles
  preloadedLayer.clearLayers();

  // Add rectangle for each region
  regions.forEach((region, index) => {
    const bounds = [
      [region.south, region.west],
      [region.north, region.east]
    ];

    const colorObj = BBOX_COLORS[index % BBOX_COLORS.length];

    const rect = L.rectangle(bounds, {
      color: colorObj.color,
      weight: 2,
      fill: true,
      fillColor: colorObj.color,
      fillOpacity: 0.15
    });

    // Add click handler
    rect.on('click', () => {
      document.dispatchEvent(new CustomEvent('regionClicked', {
        detail: { index, region }
      }));
    });

    preloadedLayer.addLayer(rect);
  });
}

/**
 * Zoom map to specific bounds
 * @param {Object} bounds - Bounds object { north, south, east, west }
 */
export function zoomToBounds(bounds) {
  const state = getState();
  const { map } = state;

  if (!map) return;

  const latLngBounds = L.latLngBounds(
    [bounds.south, bounds.west],
    [bounds.north, bounds.east]
  );

  map.fitBounds(latLngBounds);
}

/**
 * Add a rectangle for a selected region
 * @param {Object} region - Region object
 * @returns {Object} Leaflet bounds
 */
export function addRegionRectangle(region) {
  const state = getState();
  const { drawnItems } = state;

  if (!drawnItems) return null;

  const bounds = [
    [region.south, region.west],
    [region.north, region.east]
  ];

  const bboxColor = getNextBboxColor();

  const rectangle = L.rectangle(bounds, {
    color: bboxColor,
    weight: 3,
    fillColor: bboxColor,
    fillOpacity: 0.15
  });

  drawnItems.addLayer(rectangle);

  const latLngBounds = rectangle.getBounds();
  updateState({ boundingBox: latLngBounds });

  return latLngBounds;
}

/**
 * Clear all drawn bounding boxes
 */
export function clearAllBoundingBoxes() {
  const state = getState();
  const { drawnItems, preloadedLayer } = state;

  if (drawnItems) {
    drawnItems.clearLayers();
  }

  if (preloadedLayer) {
    preloadedLayer.clearLayers();
  }

  updateState({
    boundingBox: null
  });

  // Reset color index
  state.currentBboxColorIndex = 0;
}

/**
 * Get current bounding box as object
 * @returns {Object|null} Bounds object or null
 */
export function getCurrentBounds() {
  const state = getState();
  const { boundingBox, selectedRegion } = state;

  if (boundingBox) {
    return {
      north: boundingBox.getNorth(),
      south: boundingBox.getSouth(),
      east: boundingBox.getEast(),
      west: boundingBox.getWest()
    };
  }

  if (selectedRegion) {
    return {
      north: selectedRegion.north,
      south: selectedRegion.south,
      east: selectedRegion.east,
      west: selectedRegion.west
    };
  }

  return null;
}

// Export default object
export default {
  initMap,
  drawRegionRectangles,
  zoomToBounds,
  addRegionRectangle,
  clearAllBoundingBoxes,
  getCurrentBounds
};
