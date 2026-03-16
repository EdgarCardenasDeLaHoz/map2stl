/**
 * DEM Viewer Component Module
 * Digital Elevation Model visualization and interaction
 */

import { updateCache, getCache } from '../state.js';
import {
  renderDEMCanvas,
  renderWaterMaskCanvas,
  renderCombinedCanvas,
  renderEsaCanvas,
  safeMinMax,
  drawGridlines,
  drawHistogram,
  drawColorbar
} from '../utils/canvas.js';
import { fetchDEM, fetchWaterMask } from '../api.js';

// Current DEM bounding box for gridlines
let currentDemBbox = null;

/**
 * Load and render DEM for current selection
 * @param {Object} bounds - Bounding box { north, south, east, west }
 * @param {Object} params - DEM parameters
 * @returns {Promise<Object>} DEM data
 */
export async function loadDEM(bounds, params = {}) {
  const containerId = params.containerId || 'demImage';
  const container = document.getElementById(containerId);

  if (!container) {
    console.error(`Container ${containerId} not found`);
    return null;
  }

  // Show loading
  container.innerHTML = '<p style="text-align:center;padding:50px;">Loading DEM...</p>';

  try {
    const demParams = {
      ...bounds,
      dim: params.dim || 200,
      depth_scale: params.depth_scale || 0.5,
      water_scale: params.water_scale || 0.05,
      height: params.height || 10,
      base: params.base || 2,
      subtract_water: params.subtract_water !== false,
      dataset: params.dataset || 'esa',
      show_landuse: params.show_landuse || false
    };

    const data = await fetchDEM(demParams);

    if (!data.dem_values || !data.dimensions) {
      container.innerHTML = '<p>No DEM data available</p>';
      return null;
    }

    // Process DEM values
    let demVals = data.dem_values;
    let h = Number(data.dimensions[0]);
    let w = Number(data.dimensions[1]);

    // Handle nested arrays
    if (Array.isArray(demVals) && demVals.length && Array.isArray(demVals[0])) {
      h = demVals.length;
      w = demVals[0].length;
      demVals = demVals.flat();
    }

    // Calculate min/max
    const colormap = params.colormap || document.getElementById('demColormap')?.value || 'terrain';
    const { min: calcMin, max: calcMax } = safeMinMax(demVals);
    const vmin = data.min_elevation !== undefined ? data.min_elevation : calcMin;
    const vmax = data.max_elevation !== undefined ? data.max_elevation : calcMax;

    // Store in cache
    const demData = { values: demVals, width: w, height: h, colormap, vmin, vmax };
    updateCache('dem', demData);
    currentDemBbox = bounds;

    // Render canvas
    const canvas = renderDEMCanvas(demVals, w, h, colormap, vmin, vmax);
    container.innerHTML = '';
    container.appendChild(canvas);
    canvas.style.width = '100%';
    canvas.style.height = 'auto';
    container.style.position = 'relative';

    // Update UI elements
    updateAxesOverlay(bounds);
    updateColorbar(vmin, vmax, colormap);
    updateHistogram(demVals, colormap);
    updateCoordinatesDisplay(bounds, vmin, vmax);

    // Draw gridlines after canvas is sized
    requestAnimationFrame(() => drawGridlinesOverlay(containerId));

    // Enable zoom/pan
    enableCanvasZoomPan(canvas);

    return demData;

  } catch (error) {
    console.error('Error loading DEM:', error);
    container.innerHTML = `<p>Failed to load DEM: ${error.message}</p>`;
    return null;
  }
}

/**
 * Recolor DEM using cached data (no server request)
 * @param {string} colormap - New colormap name
 */
export function recolorDEM(colormap) {
  const demData = getCache('dem');

  if (!demData || !demData.values || !demData.values.length) {
    console.log('No DEM data cached, cannot recolor');
    return;
  }

  const { values, width, height, vmin, vmax } = demData;

  // Re-render with new colormap
  const canvas = renderDEMCanvas(values, width, height, colormap, vmin, vmax);
  const container = document.getElementById('demImage');

  if (container) {
    container.innerHTML = '';
    container.appendChild(canvas);
    canvas.style.width = '100%';
    canvas.style.height = 'auto';

    // Update cache with new colormap
    updateCache('dem', { ...demData, colormap });

    // Update UI
    updateColorbar(vmin, vmax, colormap);
    updateHistogram(values, colormap);

    // Re-enable zoom/pan
    enableCanvasZoomPan(canvas);

    // Redraw gridlines
    requestAnimationFrame(() => drawGridlinesOverlay('demImage'));
  }
}

/**
 * Load water mask
 * @param {Object} bounds - Bounding box
 * @param {Object} params - Water mask parameters
 * @returns {Promise<Object>} Water mask data
 */
export async function loadWaterMask(bounds, params = {}) {
  const container = document.getElementById('waterMaskImage');

  if (!container) return null;

  container.innerHTML = '<p style="text-align:center;padding:50px;">Loading water mask...</p>';

  try {
    const data = await fetchWaterMask({
      ...bounds,
      sat_scale: params.sat_scale || 500,
      dim: params.dim || 200
    });

    updateCache('waterMask', data);

    // Render
    renderWaterMask(data);

    // Update stats
    const statsEl = document.getElementById('waterMaskStats');
    if (statsEl) {
      statsEl.innerHTML = `Water pixels: ${data.water_pixels} / ${data.total_pixels} (${data.water_percentage.toFixed(1)}%)`;
    }

    return data;

  } catch (error) {
    console.error('Error loading water mask:', error);
    container.innerHTML = `<p>Error: ${error.message}</p>`;
    return null;
  }
}

/**
 * Render water mask data
 * @param {Object} data - Water mask data from API
 */
function renderWaterMask(data) {
  const container = document.getElementById('waterMaskImage');
  if (!container) return;

  const { water_mask_values, water_mask_dimensions } = data;
  const h = water_mask_dimensions[0];
  const w = water_mask_dimensions[1];

  const canvas = renderWaterMaskCanvas(water_mask_values, w, h);
  container.innerHTML = '';
  container.appendChild(canvas);
  canvas.style.width = '100%';
  canvas.style.height = 'auto';
}

/**
 * Render combined DEM + water view
 * @param {Object} options - Rendering options
 */
export function renderCombinedView(options = {}) {
  const demData = getCache('dem');
  const waterData = getCache('waterMask');

  const container = document.getElementById('combinedImage');
  if (!container) return;

  if (!demData) {
    container.innerHTML = '<p style="text-align:center;padding:20px;">Load DEM first.</p>';
    return;
  }

  const colormap = options.colormap || demData.colormap || 'terrain';
  const waterScale = options.waterScale || parseFloat(document.getElementById('waterScaleSlider')?.value || 0.05);
  const opacity = options.opacity || parseFloat(document.getElementById('waterOpacity')?.value || 0.5);

  const { values, width, height, vmin, vmax } = demData;
  const waterVals = waterData?.water_mask_values || [];

  const canvas = renderCombinedCanvas(
    values, waterVals, width, height,
    colormap, vmin, vmax, waterScale, opacity
  );

  container.innerHTML = '';
  container.appendChild(canvas);
  canvas.style.width = '100%';
  canvas.style.height = 'auto';

  enableCanvasZoomPan(canvas);
}

/**
 * Render ESA land cover data
 * @param {Object} data - ESA data from API
 */
export function renderEsaLandCover(data) {
  const container = document.getElementById('satelliteImage');
  if (!container || !data.esa_values) return;

  const { esa_values, esa_dimensions } = data;
  const h = esa_dimensions[0];
  const w = esa_dimensions[1];

  const canvas = renderEsaCanvas(esa_values, w, h);
  container.innerHTML = '';
  container.appendChild(canvas);
  canvas.style.width = '100%';
  canvas.style.height = 'auto';

  // Also render in sat preview
  const previewContainer = document.getElementById('satImage');
  if (!previewContainer) {
    console.error('satImage element not found');
    return;
  }
  const previewCanvas = renderEsaCanvas(esa_values, w, h);
  previewContainer.innerHTML = '';
  previewContainer.appendChild(previewCanvas);
  previewCanvas.style.width = '100%';
  previewCanvas.style.height = 'auto';
}

/**
 * Apply water subtraction to DEM
 */
export function applyWaterSubtract() {
  const demData = getCache('dem');
  const waterData = getCache('waterMask');

  if (!demData || !waterData) {
    alert('Please load both DEM and Water Mask first.');
    return;
  }

  const waterScale = parseFloat(document.getElementById('paramWaterScale')?.value || 0.05);
  const { values, vmin, vmax } = demData;
  const waterVals = waterData.water_mask_values;
  const ptp = vmax - vmin;

  // Apply subtraction
  const adjustedDem = values.map((v, i) => {
    const waterVal = waterVals[i] || 0;
    return v - (waterVal * ptp * waterScale);
  });

  // Update cache
  const { min: newMin, max: newMax } = safeMinMax(adjustedDem);
  updateCache('dem', {
    ...demData,
    values: adjustedDem,
    vmin: newMin,
    vmax: newMax
  });

  // Re-render
  recolorDEM(demData.colormap);

  alert('Water subtraction applied to DEM.');
}

/**
 * Update axes overlay with coordinates
 * @param {Object} bounds - Bounding box coordinates
 */
function updateAxesOverlay(bounds) {
  // Prefer the global implementation if present (defined in index.html)
  try {
    if (typeof window !== 'undefined' && typeof window.updateAxesOverlay === 'function') {
      window.updateAxesOverlay(bounds);
      return;
    }
  } catch (e) {
    // fall through to local implementation
  }

  const container = document.getElementById('demImage');
  if (!container) return;

  let overlay = container.querySelector('.dem-axes-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'dem-axes-overlay';
    overlay.innerHTML = `
            <div class="top"></div>
            <div class="bottom"></div>
            <div class="left"></div>
            <div class="right"></div>
        `;
    container.appendChild(overlay);
  }

  const fmt = (v) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(5);
  overlay.querySelector('.top').textContent = `N: ${fmt(bounds?.north)}`;
  overlay.querySelector('.bottom').textContent = `S: ${fmt(bounds?.south)}`;
  overlay.querySelector('.left').textContent = `W: ${fmt(bounds?.west)}`;
  overlay.querySelector('.right').textContent = `E: ${fmt(bounds?.east)}`;
}

/**
 * Update colorbar display
 * @param {number} vmin - Minimum value
 * @param {number} vmax - Maximum value
 * @param {string} colormap - Colormap name
 */
function updateColorbar(vmin, vmax, colormap) {
  const barContainer = document.getElementById('colorbar');
  if (!barContainer) return;

  barContainer.innerHTML = '';

  const canvas = document.createElement('canvas');
  canvas.width = 300;
  canvas.height = 50;

  drawColorbar(canvas, vmin, vmax, colormap);

  canvas.style.width = '100%';
  canvas.style.height = 'auto';
  barContainer.appendChild(canvas);
}

/**
 * Update histogram display
 * @param {Array<number>} values - DEM values
 * @param {string} colormap - Colormap name
 */
function updateHistogram(values, colormap) {
  const container = document.getElementById('histogram');
  if (!container) return;

  container.innerHTML = '';

  const canvas = document.createElement('canvas');
  canvas.width = 300;
  canvas.height = 140;

  drawHistogram(canvas, values, colormap);

  canvas.style.width = '100%';
  canvas.style.height = 'auto';
  container.appendChild(canvas);
}

/**
 * Update coordinates display
 * @param {Object} bounds - Bounding box
 * @param {number} vmin - Min elevation
 * @param {number} vmax - Max elevation
 */
function updateCoordinatesDisplay(bounds, vmin, vmax) {
  const el = document.getElementById('demCoordinates');
  if (!el) return;

  el.textContent =
    `N:${bounds.north.toFixed(5)} S:${bounds.south.toFixed(5)} ` +
    `E:${bounds.east.toFixed(5)} W:${bounds.west.toFixed(5)} | ` +
    `Elevation: ${vmin.toFixed(1)}m to ${vmax.toFixed(1)}m`;
}

/**
 * Draw gridlines overlay on DEM container
 * @param {string} containerId - Container element ID
 */
export function drawGridlinesOverlay(containerId = 'demImage') {
  const container = document.getElementById(containerId);
  if (!container || !currentDemBbox) return;

  const canvas = container.querySelector('canvas:not(.dem-gridlines-overlay)');
  if (!canvas) return;

  const showGridlines = document.getElementById('showGridlines');
  if (!showGridlines || !showGridlines.checked) {
    const existing = container.querySelector('.dem-gridlines-overlay');
    if (existing) existing.remove();
    return;
  }

  // Create or get overlay canvas
  let overlay = container.querySelector('.dem-gridlines-overlay');
  if (!overlay) {
    overlay = document.createElement('canvas');
    overlay.className = 'dem-gridlines-overlay';
    container.appendChild(overlay);
  }

  // Position overlay to match displayed canvas
  container.style.position = 'relative';

  const canvasRect = canvas.getBoundingClientRect();
  const containerRect = container.getBoundingClientRect();

  const offsetLeft = canvasRect.left - containerRect.left;
  const offsetTop = canvasRect.top - containerRect.top;

  overlay.width = canvasRect.width;
  overlay.height = canvasRect.height;
  overlay.style.position = 'absolute';
  overlay.style.left = offsetLeft + 'px';
  overlay.style.top = offsetTop + 'px';
  overlay.style.width = canvasRect.width + 'px';
  overlay.style.height = canvasRect.height + 'px';
  overlay.style.pointerEvents = 'none';

  const gridCount = parseInt(document.getElementById('gridlineCount')?.value || '5');
  drawGridlines(overlay, currentDemBbox, gridCount);
}

/**
 * Enable zoom and pan on canvas
 * @param {HTMLCanvasElement} canvas - Target canvas
 */
function enableCanvasZoomPan(canvas) {
  let isPanning = false;
  let startX, startY;
  let offsetX = 0, offsetY = 0;
  let scale = 1;

  // Capture the already-rendered image for redraw during pan/zoom
  const offscreen = document.createElement('canvas');
  offscreen.width = canvas.width;
  offscreen.height = canvas.height;
  offscreen.getContext('2d').drawImage(canvas, 0, 0);

  const redraw = () => {
    const ctx = canvas.getContext('2d');
    ctx.save();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.translate(offsetX, offsetY);
    ctx.scale(scale, scale);
    ctx.drawImage(offscreen, 0, 0);
    ctx.restore();
    // expose current transform so overlays can read it
    try {
      canvas.dataset.offsetX = offsetX;
      canvas.dataset.offsetY = offsetY;
      canvas.dataset.scale = scale;
    } catch (e) { }
    // update overlays to match new view
    requestAnimationFrame(() => {
      drawGridlinesOverlay('demImage');
      // compute and update axes for visible bbox
      const visible = computeVisibleBounds(canvas, currentDemBbox, offsetX, offsetY, scale);
      if (visible) updateAxesOverlay(visible);
    });
  };

  canvas.addEventListener('mousedown', (e) => {
    isPanning = true;
    startX = e.clientX - offsetX;
    startY = e.clientY - offsetY;
    canvas.style.cursor = 'grabbing';
  });

  canvas.addEventListener('mousemove', (e) => {
    if (!isPanning) return;
    offsetX = e.clientX - startX;
    offsetY = e.clientY - startY;
    redraw();
  });

  canvas.addEventListener('mouseup', () => {
    isPanning = false;
    canvas.style.cursor = 'grab';
  });

  canvas.addEventListener('mouseleave', () => {
    isPanning = false;
    canvas.style.cursor = 'default';
  });

  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const zoomFactor = 0.1;
    scale += e.deltaY > 0 ? -zoomFactor : zoomFactor;
    scale = Math.max(0.5, Math.min(3, scale));
    redraw();
  });

  canvas.style.cursor = 'grab';
}

/**
 * Compute visible geographic bounds given canvas transform
 * Returns {north,south,east,west}
 */
function computeVisibleBounds(canvas, fullBbox, offsetX, offsetY, scale) {
  if (!canvas || !fullBbox) return null;
  const width = canvas.width;
  const height = canvas.height;
  // visible region in canvas pixel coordinates
  const x0 = (-offsetX) / (scale || 1);
  const y0 = (-offsetY) / (scale || 1);
  const visW = (canvas.getBoundingClientRect().width) / (scale || 1);
  const visH = (canvas.getBoundingClientRect().height) / (scale || 1);

  const west = fullBbox.west + (x0 / width) * (fullBbox.east - fullBbox.west);
  const east = fullBbox.west + ((x0 + visW) / width) * (fullBbox.east - fullBbox.west);
  const north = fullBbox.north - (y0 / height) * (fullBbox.north - fullBbox.south);
  const south = fullBbox.north - ((y0 + visH) / height) * (fullBbox.north - fullBbox.south);

  return { north, south, east, west };
}

/**
 * Setup grid toggle event listeners
 */
export function setupGridToggle() {
  const showGridlines = document.getElementById('showGridlines');
  const gridlineCount = document.getElementById('gridlineCount');

  if (showGridlines) {
    showGridlines.addEventListener('change', () => {
      drawGridlinesOverlay('demImage');
    });
  }

  if (gridlineCount) {
    gridlineCount.addEventListener('change', () => {
      drawGridlinesOverlay('demImage');
    });
  }

  // Redraw on resize
  window.addEventListener('resize', () => {
    if (currentDemBbox) {
      requestAnimationFrame(() => drawGridlinesOverlay('demImage'));
    }
  });
}

// Export default object
export default {
  loadDEM,
  recolorDEM,
  loadWaterMask,
  renderCombinedView,
  renderEsaLandCover,
  applyWaterSubtract,
  drawGridlinesOverlay,
  setupGridToggle
};

// Compatibility: expose key functions to global scope for legacy inline scripts
if (typeof window !== 'undefined') {
  window.enableZoomAndPan = function enableZoomAndPan(canvas, renderFn) {
    let isPanning = false;
    let startX, startY;
    let offsetX = 0, offsetY = 0;
    let scale = 1;

    const redraw = () => {
      const ctx = canvas.getContext('2d');
      ctx.save();
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.translate(offsetX, offsetY);
      ctx.scale(scale, scale);
      renderFn(ctx);
      ctx.restore();
    };

    canvas.addEventListener('mousedown', (e) => {
      isPanning = true;
      startX = e.clientX - offsetX;
      startY = e.clientY - offsetY;
    });

    canvas.addEventListener('mousemove', (e) => {
      if (isPanning) {
        offsetX = e.clientX - startX;
        offsetY = e.clientY - startY;
        redraw();
      }
    });

    canvas.addEventListener('mouseup', () => { isPanning = false; });

    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      const zoomFactor = 0.1;
      const delta = e.deltaY > 0 ? -zoomFactor : zoomFactor;
      scale = Math.min(Math.max(0.1, scale + delta), 5);
      redraw();
    });
  };

  window.computeVisibleBounds = computeVisibleBounds;
  window.drawGridlinesOverlay = drawGridlinesOverlay;
  window.updateAxesOverlay = (bounds) => updateAxesOverlay(bounds);
}
