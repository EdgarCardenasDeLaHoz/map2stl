/* DEAD — not loaded by app.js or index.html. Editing this file has no effect on the running app. */
/**
 * Canvas Utilities Module
 * Reusable canvas rendering functions
 */

import { mapElevationToColor, getEsaColor } from './colors.js';

/**
 * Safely calculate min/max from an array without stack overflow
 * Uses reduce instead of spread operator for large arrays
 * @param {Array<number>} arr - Array of numbers
 * @returns {Object} { min, max }
 */
export function safeMinMax(arr) {
  const finite = arr.filter(Number.isFinite);
  if (finite.length === 0) return { min: 0, max: 1 };

  const min = finite.reduce((a, b) => a < b ? a : b, finite[0]);
  const max = finite.reduce((a, b) => a > b ? a : b, finite[0]);

  return { min, max };
}

/**
 * Render pixel data to a canvas using a mapping function
 * @param {Array<number>} values - Flat array of values
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @param {Function} mapFn - Function(value, index) => [r, g, b, a] (0-255)
 * @returns {HTMLCanvasElement}
 */
export function renderPixelCanvas(values, width, height, mapFn) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  const imgData = ctx.createImageData(width, height);

  const total = width * height;
  for (let i = 0; i < total; i++) {
    const value = i < values.length ? values[i] : NaN;
    const [r, g, b, a] = mapFn(value, i);
    const idx = i * 4;
    imgData.data[idx] = r;
    imgData.data[idx + 1] = g;
    imgData.data[idx + 2] = b;
    imgData.data[idx + 3] = a;
  }

  ctx.putImageData(imgData, 0, 0);
  return canvas;
}

/**
 * Render DEM values to canvas with colormap
 * @param {Array<number>} values - DEM values
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @param {string} colormap - Colormap name
 * @param {number} vmin - Minimum value for normalization
 * @param {number} vmax - Maximum value for normalization
 * @returns {HTMLCanvasElement}
 */
export function renderDEMCanvas(values, width, height, colormap, vmin, vmax) {
  const range = (vmax - vmin) || 1;

  return renderPixelCanvas(values, width, height, (val) => {
    if (!Number.isFinite(val)) {
      return [0, 0, 0, 0]; // Transparent for invalid values
    }

    const t = (val - vmin) / range;
    const [r, g, b] = mapElevationToColor(t, colormap);

    return [
      Math.round(r * 255),
      Math.round(g * 255),
      Math.round(b * 255),
      255
    ];
  });
}

/**
 * Render water mask to canvas
 * @param {Array<number>} values - Water mask values (0 = land, 1 = water)
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @returns {HTMLCanvasElement}
 */
export function renderWaterMaskCanvas(values, width, height) {
  return renderPixelCanvas(values, width, height, (val) => {
    if (val > 0.5) {
      // Water - blue
      return [0, 100, 255, 200];
    } else {
      // Land - brown
      return [100, 80, 60, 255];
    }
  });
}

/**
 * Render ESA land cover data to canvas
 * @param {Array<number>} values - ESA WorldCover class values
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @returns {HTMLCanvasElement}
 */
export function renderEsaCanvas(values, width, height) {
  return renderPixelCanvas(values, width, height, (val) => {
    const [r, g, b] = getEsaColor(val);
    return [r, g, b, 255];
  });
}

/**
 * Render combined DEM + water overlay
 * @param {Array<number>} demValues - DEM values
 * @param {Array<number>} waterValues - Water mask values
 * @param {number} width - Canvas width
 * @param {number} height - Canvas height
 * @param {string} colormap - Colormap for DEM
 * @param {number} vmin - Min elevation
 * @param {number} vmax - Max elevation
 * @param {number} waterScale - Water subtraction scale
 * @param {number} opacity - Water overlay opacity
 * @returns {HTMLCanvasElement}
 */
export function renderCombinedCanvas(
  demValues, waterValues, width, height,
  colormap, vmin, vmax, waterScale = 0.05, opacity = 0.5
) {
  const range = (vmax - vmin) || 1;

  return renderPixelCanvas(demValues, width, height, (val, i) => {
    // Apply water subtraction
    const waterVal = waterValues[i] || 0;
    if (waterVal > 0.5) {
      val = val - (waterVal * range * waterScale);
    }

    const t = Math.max(0, Math.min(1, (val - vmin) / range));
    const [r, g, b] = mapElevationToColor(t, colormap);

    // Blend with water overlay
    if (waterVal > 0.5 && opacity > 0) {
      return [
        Math.round((r * 255) * (1 - opacity) + 30 * opacity),
        Math.round((g * 255) * (1 - opacity) + 100 * opacity),
        Math.round((b * 255) * (1 - opacity) + 220 * opacity),
        255
      ];
    }

    return [
      Math.round(r * 255),
      Math.round(g * 255),
      Math.round(b * 255),
      255
    ];
  });
}

/**
 * Enable zoom and pan on a canvas element
 * @param {HTMLCanvasElement} canvas - The canvas to enable zoom/pan on
 * @param {Function} renderFn - Function to re-render the canvas content
 */
export function enableZoomAndPan(canvas, renderFn) {
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

  canvas.addEventListener('mouseup', () => {
    isPanning = false;
  });

  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const zoomFactor = 0.1;
    const delta = e.deltaY > 0 ? -zoomFactor : zoomFactor;
    scale = Math.min(Math.max(0.1, scale + delta), 5);
    redraw();
  });
}

/**
 * Draw histogram of values
 * @param {HTMLCanvasElement} canvas - Target canvas
 * @param {Array<number>} values - Data values
 * @param {string} colormap - Colormap for bar colors
 * @param {Object} options - Drawing options
 */
export function drawHistogram(canvas, values, colormap, options = {}) {
  const {
    numBins = 40,
    padding = { top: 10, right: 10, bottom: 25, left: 10 },
    backgroundColor = '#252525',
    showZeroLine = true
  } = options;

  const ctx = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;

  // Filter valid values
  const valid = values.filter(Number.isFinite);
  if (valid.length === 0) {
    ctx.fillStyle = '#888';
    ctx.fillText('No data', width / 2 - 30, height / 2);
    return;
  }

  // Calculate bins
  const { min, max } = safeMinMax(valid);
  const range = (max - min) || 1;
  const bins = new Array(numBins).fill(0);

  valid.forEach(v => {
    const idx = Math.min(numBins - 1, Math.floor(((v - min) / range) * numBins));
    bins[idx]++;
  });

  const maxBin = Math.max(...bins);

  // Draw background
  ctx.fillStyle = backgroundColor;
  ctx.fillRect(0, 0, width, height);

  // Draw bars
  const barWidth = (width - padding.left - padding.right) / numBins;
  const histHeight = height - padding.top - padding.bottom;

  bins.forEach((count, i) => {
    const t = i / (numBins - 1);
    const [r, g, b] = mapElevationToColor(t, colormap);
    ctx.fillStyle = `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;

    const barHeight = (count / maxBin) * histHeight;
    const x = padding.left + i * barWidth;
    const y = padding.top + histHeight - barHeight;

    ctx.fillRect(x, y, barWidth - 1, barHeight);
  });

  // Draw zero line if within range
  if (showZeroLine && min < 0 && max > 0) {
    const zeroX = padding.left + ((0 - min) / range) * (width - padding.left - padding.right);
    ctx.strokeStyle = '#ff4444';
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 2]);
    ctx.beginPath();
    ctx.moveTo(zeroX, padding.top);
    ctx.lineTo(zeroX, height - padding.bottom);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = '#ff4444';
    ctx.font = 'bold 10px sans-serif';
    ctx.fillText('0', zeroX - 3, padding.top - 2);
  }

  // Axis labels
  ctx.fillStyle = '#aaa';
  ctx.font = '10px sans-serif';
  ctx.fillText(`${min.toFixed(0)}m`, padding.left, height - 4);
  ctx.fillText(`${max.toFixed(0)}m`, width - padding.right - 35, height - 4);
}

/**
 * Draw colorbar with tick marks
 * @param {HTMLCanvasElement} canvas - Target canvas
 * @param {number} min - Minimum value
 * @param {number} max - Maximum value
 * @param {string} colormap - Colormap name
 * @param {Object} options - Drawing options
 */
export function drawColorbar(canvas, min, max, colormap, options = {}) {
  const {
    barHeight = 20,
    numTicks = 5
  } = options;

  const ctx = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;

  // Draw gradient bar
  const imgData = ctx.createImageData(width, barHeight);

  for (let x = 0; x < width; x++) {
    const t = x / (width - 1);
    const [r, g, b] = mapElevationToColor(t, colormap);

    for (let y = 0; y < barHeight; y++) {
      const idx = (y * width + x) * 4;
      imgData.data[idx] = Math.round(r * 255);
      imgData.data[idx + 1] = Math.round(g * 255);
      imgData.data[idx + 2] = Math.round(b * 255);
      imgData.data[idx + 3] = 255;
    }
  }

  ctx.putImageData(imgData, 0, 0);

  // Draw tick marks and labels
  ctx.fillStyle = '#ccc';
  ctx.font = '11px sans-serif';

  for (let i = 0; i <= numTicks; i++) {
    const x = Math.round((i / numTicks) * (width - 1));
    const value = min + (i / numTicks) * (max - min);

    ctx.fillRect(x, barHeight, 1, 5); // Tick mark
    ctx.fillText(value.toFixed(0), x - 12, barHeight + 18); // Label
  }
}

/**
 * Draw gridlines overlay on a canvas
 * @param {HTMLCanvasElement} canvas - Target canvas
 * @param {Object} bbox - Bounding box { north, south, east, west }
 * @param {number} gridCount - Number of grid lines
 */
export function drawGridlines(canvas, bbox, gridCount = 5) {
  const ctx = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  const { north, south, east, west } = bbox;

  ctx.clearRect(0, 0, width, height);

  ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
  ctx.lineWidth = 1;
  ctx.font = '11px sans-serif';
  ctx.fillStyle = '#fff';
  ctx.shadowColor = 'rgba(0, 0, 0, 0.8)';
  ctx.shadowBlur = 2;

  // Vertical gridlines (longitude)
  for (let i = 0; i <= gridCount; i++) {
    const x = (i / gridCount) * width;
    const lon = west + (i / gridCount) * (east - west);

    ctx.beginPath();
    ctx.setLineDash([4, 4]);
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
    ctx.setLineDash([]);

    // Label at bottom
    const label = lon.toFixed(2) + '°';
    const textWidth = ctx.measureText(label).width;
    const labelX = Math.max(textWidth / 2, Math.min(x, width - textWidth / 2));
    ctx.fillText(label, labelX - textWidth / 2, height - 5);
  }

  // Horizontal gridlines (latitude)
  for (let i = 0; i <= gridCount; i++) {
    const y = (i / gridCount) * height;
    const lat = north - (i / gridCount) * (north - south);

    ctx.beginPath();
    ctx.setLineDash([4, 4]);
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
    ctx.setLineDash([]);

    // Label at left
    const label = lat.toFixed(2) + '°';
    ctx.fillText(label, 5, y + 4);
  }

  // Draw border
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.6)';
  ctx.setLineDash([]);
  ctx.strokeRect(0, 0, width, height);
}

// Export default object
export default {
  safeMinMax,
  renderPixelCanvas,
  renderDEMCanvas,
  renderWaterMaskCanvas,
  renderEsaCanvas,
  renderCombinedCanvas,
  enableZoomAndPan,
  drawHistogram,
  drawColorbar,
  drawGridlines
};