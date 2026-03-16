import { drawColorbar, renderDEMCanvas, renderHistogram } from './dem_renderer.js';

async function fetchPreview(params) {
  const query = params ? `?${params}` : '';
  const resp = await fetch(`/api/preview_dem${query}`);
  return resp.json();
}

// Prefer existing page-level loadDEM (which supplies bbox and params).
// If not present, use module-level update that builds params from inputs.
export async function initUI() {
  const updateBtn = document.getElementById('updateDemBtn');
  const colormapSelect = document.getElementById('demColormap');

  // Check if page has loadDEM function
  const usePageLoad = (typeof window.loadDEM === 'function');

  if (usePageLoad) {
    // Page already handles all event listeners in setupEventListeners()
    // Don't add duplicate listeners or call loadDEM automatically
    // The page's DOMContentLoaded will handle initial load
    console.log('ui_init.js: Using page-level loadDEM, skipping module initialization');
    return;
  }

  // Fallback: if no page-level loadDEM, use module implementation
  if (updateBtn) {
    updateBtn.addEventListener('click', async () => { await moduleUpdateDEM(); });
  }
  if (colormapSelect) {
    colormapSelect.addEventListener('change', async () => { await moduleUpdateDEM(); });
  }
  // Don't auto-load since we don't have bbox info
  console.log('ui_init.js: Module fallback mode - waiting for user action');
}

async function moduleUpdateDEM() {
  const colormap = document.getElementById('demColormap').value;
  // Collect minimal params from inputs if present
  const paramsObj = {
    colormap,
    dim: document.getElementById('paramDim') ? document.getElementById('paramDim').value : 150,
    depth_scale: document.getElementById('paramDepthScale') ? document.getElementById('paramDepthScale').value : 0.5,
    water_scale: document.getElementById('paramWaterScale') ? document.getElementById('paramWaterScale').value : 0.05,
    height: document.getElementById('paramHeight') ? document.getElementById('paramHeight').value : 10,
    base: document.getElementById('paramBase') ? document.getElementById('paramBase').value : 2,
    subtract_water: document.getElementById('paramSubtractWater') ? document.getElementById('paramSubtractWater').checked : true
  };
  const params = new URLSearchParams(paramsObj).toString();
  const data = await fetchPreview(params);
  if (data.error) {
    document.getElementById('demImage').innerHTML = `<p>Error: ${data.error}</p>`;
    return;
  }
  const demVals = data.dem_values && Array.isArray(data.dem_values[0]) ? data.dem_values.flat() : data.dem_values;
  const dims = data.dimensions || [0, 0];
  let h = dims[0];
  let w = dims[1];
  if (demVals && demVals.length && w * h === 0) {
    h = data.dimensions[0];
    w = data.dimensions[1];
  }
  // Render image
  if (demVals && demVals.length) {
    renderDEMCanvas(demVals, w, h, colormap, data.min_elevation, data.max_elevation, 'demImage');
  } else if (data.image) {
    document.getElementById('demImage').innerHTML = `<img src="${data.image}" alt="DEM Image" style="max-width:100%;height:auto;"/>`;
  }
  // Draw colorbar
  drawColorbar(data.min_elevation || 0, data.max_elevation || 1, colormap, 'colorbar');
  // Histogram
  if (demVals && demVals.length) {
    renderHistogram(demVals, colormap, 'histogram');
  } else if (data.histogram) {
    document.getElementById('histogram').innerHTML = `<img src="${data.histogram}" alt="Histogram" style="max-width:100%;height:auto;"/>`;
  }
}

// auto-init when loaded in browser (non-module fallback)
if (typeof window !== 'undefined') {
  window.addEventListener('load', () => {
    // If browser supports modules, the last script include will call initUI; otherwise try simple init
    const maybeInit = async () => {
      try {
        if (window.initUI) await window.initUI();
      } catch (e) {
        console.warn('UI init failed', e);
      }
    };
    maybeInit();
  });
}
