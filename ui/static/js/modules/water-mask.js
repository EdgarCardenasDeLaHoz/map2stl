/**
 * modules/water-mask.js — Water mask fetch, render, land cover editor.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   loadWaterMask()           — fetch water mask + ESA land cover
 *   renderWaterMask(data)     — render water mask canvas
 *   renderEsaLandCover(data)  — render ESA land cover canvas
 *   renderCombinedView()      — composite DEM + water overlay
 *   loadSatelliteForTab()     — load ESA land cover for satellite sub-tab
 *   previewWaterSubtract()    — preview DEM with water lowered
 *   applyWaterSubtract()      — permanently apply water subtraction
 *   renderLandCoverLegend()   — render land cover colour-picker legend
 *   setupLandCoverEditor()    — wire land cover editor events
 *   setupWaterMaskListeners() — wire water mask tab events
 *   getLastWaterMaskData()    — accessor for lastWaterMaskData
 *
 * External dependencies:
 *   window.api                          — from modules/api.js
 *   window.waterMaskCache               — from modules/cache.js
 *   window.getBoundingBox()             — L.Rectangle | null, from app.js
 *   window.getWaterOpacity()            — current water opacity (0-1), from app.js
 *   window.switchDemSubtab(name)        — from app.js
 *   window.appState.selectedRegion
 *   window.appState.lastDemData
 *   window.appState.currentDemBbox
 *   window.appState.layerBboxes
 *   window.appState.layerStatus
 *   window.appState.lastWaterMaskData   — mirrored here; also set on appState
 *   window.appState.landCoverConfig     — ESA class colour/elevation map
 *   window.appState.landCoverConfigDefaults — deep-copy of defaults
 *   applyProjection(canvas, bbox)       — global from dem-loader.js
 *   enableZoomAndPan(canvas)            — global from dem-loader.js
 *   mapElevationToColor(t, cmap)        — global from dem-loader.js
 *   recolorDEM()                        — global from dem-loader.js
 *   isLayerCurrent(name)                — global from app.js file-top
 *   updateLayerStatusIndicators()       — global from app.js file-top
 *   updateCacheStatusUI()               — global from app.js file-top
 *   updateStackedLayers()               — global from stacked-layers.js
 *   showToast(msg, type)                — global from app.js file-top
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let _waterMaskAbortController = null;
let lastWaterMaskData = null;

// ─────────────────────────────────────────────────────────────────────────────
// Fetch
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetch water mask data from `/api/water_mask` for the current bbox.
 * Uses `window.waterMaskCache` to avoid redundant requests. Stores result
 * in module-scope `lastWaterMaskData` (and mirrors to `window.appState`).
 */
async function loadWaterMask() {
    if (_waterMaskAbortController) _waterMaskAbortController.abort();
    _waterMaskAbortController = new AbortController();
    const signal = _waterMaskAbortController.signal;

    const boundingBox = window.getBoundingBox?.();
    const selectedRegion = window.appState.selectedRegion;

    if (!boundingBox && !selectedRegion) {
        document.getElementById('waterMaskImage').innerHTML = '<p>Please select a region first.</p>';
        return;
    }

    let bounds;
    if (boundingBox) {
        bounds = boundingBox;
    } else {
        bounds = L.latLngBounds(
            [selectedRegion.south, selectedRegion.west],
            [selectedRegion.north, selectedRegion.east]
        );
    }

    const north = bounds.getNorth();
    const south = bounds.getSouth();
    const east  = bounds.getEast();
    const west  = bounds.getWest();
    const bbox  = { north, south, east, west };

    const waterRes    = parseInt(document.getElementById('waterResolution')?.value || '200');
    const landCoverRes = parseInt(document.getElementById('landCoverResolution')?.value || '200');
    const satScale    = Math.min(waterRes, landCoverRes);
    const lastDemData = window.appState.lastDemData;
    const dim         = lastDemData ? Math.max(lastDemData.width, lastDemData.height) : 400;

    const waterDataset = document.getElementById('waterDataset')?.value || 'esa';
    let cacheKey = { ...bbox, sat_scale: satScale, dataset: waterDataset };
    if (lastDemData?.width && lastDemData?.height) {
        cacheKey.demWidth  = lastDemData.width;
        cacheKey.demHeight = lastDemData.height;
    }

    const cachedData = window.waterMaskCache.get(cacheKey);
    if (cachedData) {
        const dimsMatch = !lastDemData ||
            (cachedData.water_mask_dimensions &&
                cachedData.water_mask_dimensions[0] === lastDemData.height &&
                cachedData.water_mask_dimensions[1] === lastDemData.width);

        if (dimsMatch) {
            _setLastWaterMaskData(cachedData);
            window.appState.layerBboxes.water    = bbox;
            window.appState.layerBboxes.landCover = bbox;
            window.appState.layerStatus.water     = 'loaded';
            window.appState.layerStatus.landCover = 'loaded';
            updateLayerStatusIndicators();
            updateCacheStatusUI();
            renderWaterMask(cachedData);
            renderEsaLandCover(cachedData);
            requestAnimationFrame(() => updateStackedLayers());
            document.getElementById('waterMaskStats').innerHTML =
                `Water pixels: ${cachedData.water_pixels} / ${cachedData.total_pixels} (${cachedData.water_percentage.toFixed(1)}%) <span style="color:#4CAF50;font-size:10px;">[CACHED]</span>`;
            showToast('Water & land cover loaded from cache', 'success');
            return;
        }
    }

    const params = new URLSearchParams({ north, south, east, west, sat_scale: satScale, dim, dataset: waterDataset });
    if (lastDemData?.width && lastDemData?.height) {
        params.set('target_width',  lastDemData.width);
        params.set('target_height', lastDemData.height);
    }

    window.appState.layerStatus.water    = 'loading';
    window.appState.layerStatus.landCover = 'loading';
    updateLayerStatusIndicators();

    document.getElementById('waterMaskImage').innerHTML = '<div class="loading"><span class="spinner"></span>Loading water mask from Earth Engine...</div>';
    showToast('Loading water mask from Earth Engine...', 'info');

    try {
        const { data, error: wmErr } = await window.api.dem.waterMask(params, signal);
        if (wmErr) {
            const _p = document.createElement('p'); _p.textContent = `Error: ${wmErr}`; document.getElementById('waterMaskImage').replaceChildren(_p);
            window.appState.layerStatus.water    = 'error';
            window.appState.layerStatus.landCover = 'error';
            updateLayerStatusIndicators();
            showToast('Failed to load water mask: ' + wmErr, 'error');
            return;
        }
        if (data.error) {
            const _p = document.createElement('p'); _p.textContent = `Error: ${data.error}`; document.getElementById('waterMaskImage').replaceChildren(_p);
            window.appState.layerStatus.water    = 'error';
            window.appState.layerStatus.landCover = 'error';
            updateLayerStatusIndicators();
            showToast('Failed to load water mask: ' + data.error, 'error');
            return;
        }

        window.waterMaskCache.set(cacheKey, data);
        updateCacheStatusUI();
        _setLastWaterMaskData(data);

        window.appState.layerBboxes.water    = bbox;
        window.appState.layerBboxes.landCover = bbox;
        window.appState.layerStatus.water     = 'loaded';
        window.appState.layerStatus.landCover = 'loaded';
        updateLayerStatusIndicators();

        renderWaterMask(data);
        renderEsaLandCover(data);
        requestAnimationFrame(() => updateStackedLayers());

        document.getElementById('waterMaskStats').innerHTML =
            `Water pixels: ${data.water_pixels} / ${data.total_pixels} (${data.water_percentage.toFixed(1)}%)`;
        showToast('Water & land cover loaded', 'success');

    } catch (error) {
        if (error.name === 'AbortError') return;
        console.error('Error loading water mask:', error);
        const _p = document.createElement('p'); _p.textContent = `Error: ${error.message}`; document.getElementById('waterMaskImage').replaceChildren(_p);
        window.appState.layerStatus.water    = 'error';
        window.appState.layerStatus.landCover = 'error';
        updateLayerStatusIndicators();
        showToast('Failed to load water mask', 'error');
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Render helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Render the water mask array to the `#waterMaskImage` canvas.
 * Water pixels are blue, land pixels are brown.
 */
function renderWaterMask(data) {
    const container = document.getElementById('waterMaskImage');
    const values = data.water_mask_values;
    const h = data.water_mask_dimensions[0];
    const w = data.water_mask_dimensions[1];

    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(w, h);

    for (let i = 0; i < values.length; i++) {
        const val = values[i];
        const idx = i * 4;
        if (val > 0.5) {
            imgData.data[idx]     = 0;
            imgData.data[idx + 1] = 100;
            imgData.data[idx + 2] = 255;
            imgData.data[idx + 3] = 200;
        } else {
            imgData.data[idx]     = 100;
            imgData.data[idx + 1] = 80;
            imgData.data[idx + 2] = 60;
            imgData.data[idx + 3] = 255;
        }
    }

    ctx.putImageData(imgData, 0, 0);
    const currentDemBbox = window.appState.currentDemBbox;
    const projectedCanvas = currentDemBbox ? applyProjection(canvas, currentDemBbox) : canvas;
    container.innerHTML = '';
    container.appendChild(projectedCanvas);
    projectedCanvas.style.width = '100%';
    projectedCanvas.style.height = 'auto';
    requestAnimationFrame(() => updateStackedLayers());
}

/**
 * Render ESA WorldCover land cover classes to the `#satelliteImage` canvas.
 */
function renderEsaLandCover(data) {
    const container = document.getElementById('satelliteImage');
    const values = data.esa_values;
    const h = data.esa_dimensions[0];
    const w = data.esa_dimensions[1];

    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(w, h);

    const landCoverConfig = window.appState.landCoverConfig;
    const defaultColor = landCoverConfig[0]?.color || [0, 50, 150];

    for (let i = 0; i < values.length; i++) {
        const val = Math.round(values[i]);
        const idx = i * 4;
        const config = landCoverConfig[val];
        const color = config ? config.color : defaultColor;
        imgData.data[idx]     = color[0];
        imgData.data[idx + 1] = color[1];
        imgData.data[idx + 2] = color[2];
        imgData.data[idx + 3] = 255;
    }

    ctx.putImageData(imgData, 0, 0);
    const currentDemBbox = window.appState.currentDemBbox;
    const projLandCanvas = currentDemBbox ? applyProjection(canvas, currentDemBbox) : canvas;
    container.innerHTML = '';
    container.appendChild(projLandCanvas);
    projLandCanvas.style.width = '100%';
    projLandCanvas.style.height = 'auto';

    requestAnimationFrame(() => updateStackedLayers());
}

// ─────────────────────────────────────────────────────────────────────────────
// Combined view
// ─────────────────────────────────────────────────────────────────────────────

async function renderCombinedView() {
    const container = document.getElementById('combinedImage');
    const lastDemData = window.appState.lastDemData;

    if (!lastDemData || !isLayerCurrent('dem')) {
        container.innerHTML = '<p style="text-align:center;padding:20px;">Load DEM first.</p>';
        return;
    }

    if (!lastWaterMaskData || !isLayerCurrent('water')) {
        container.innerHTML = '<p style="text-align:center;padding:20px;">Loading water mask for combined view...</p>';
        await loadWaterMask();
    }

    if (lastWaterMaskData && lastDemData) {
        const demSize   = lastDemData.width * lastDemData.height;
        const waterSize = lastWaterMaskData.water_mask_values?.length ?? 0;
        if (demSize !== waterSize) {
            console.warn('DEM and water mask dimension mismatch - reloading water mask');
            await loadWaterMask();
        }
    }

    const colormap = document.getElementById('demColormap').value;
    const { values, width, height, vmin, vmax } = lastDemData;

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(width, height);

    const waterScale = parseFloat(document.getElementById('waterScaleSlider')?.value || 0.05);
    const opacityVal = window.getWaterOpacity?.() ?? 0.7;
    const waterVals  = lastWaterMaskData?.water_mask_values || [];
    const ptp = vmax - vmin;

    for (let i = 0; i < values.length; i++) {
        let val = values[i];
        if (waterVals[i] && waterVals[i] > 0.5) {
            val = val - (waterVals[i] * ptp * waterScale);
        }
        const t = Math.max(0, Math.min(1, (val - vmin) / (ptp || 1)));
        const [r, g, b] = mapElevationToColor(t, colormap);
        const idx = i * 4;
        if (waterVals[i] && waterVals[i] > 0.5 && opacityVal > 0) {
            imgData.data[idx]     = Math.round((r * 255) * (1 - opacityVal) + 30  * opacityVal);
            imgData.data[idx + 1] = Math.round((g * 255) * (1 - opacityVal) + 100 * opacityVal);
            imgData.data[idx + 2] = Math.round((b * 255) * (1 - opacityVal) + 220 * opacityVal);
        } else {
            imgData.data[idx]     = Math.round(r * 255);
            imgData.data[idx + 1] = Math.round(g * 255);
            imgData.data[idx + 2] = Math.round(b * 255);
        }
        imgData.data[idx + 3] = 255;
    }

    ctx.putImageData(imgData, 0, 0);
    container.innerHTML = '';
    container.appendChild(canvas);
    canvas.style.width = '100%';
    canvas.style.height = 'auto';
    enableZoomAndPan(canvas);
}

async function loadSatelliteForTab() {
    const container = document.getElementById('satelliteImage');

    if (lastWaterMaskData && lastWaterMaskData.esa_values && isLayerCurrent('landCover')) {
        renderEsaLandCover(lastWaterMaskData);
        return;
    }

    container.innerHTML = '<p style="text-align:center;padding:50px;">Loading land cover data...</p>';
    await loadWaterMask();

    if (lastWaterMaskData?.esa_values) {
        renderEsaLandCover(lastWaterMaskData);
    } else {
        container.innerHTML = '<p style="text-align:center;padding:50px;">No land cover data available. Please select a region first.</p>';
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Water subtract
// ─────────────────────────────────────────────────────────────────────────────

async function previewWaterSubtract() {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastWaterMaskData) {
        document.getElementById('combinedImage').innerHTML = '<p>Load DEM and Water Mask first.</p>';
        return;
    }

    const waterScale = parseFloat(document.getElementById('waterScaleSlider').value);
    const opacityVal = window.getWaterOpacity?.() ?? 0.7;
    const demVals    = lastDemData.values;
    const waterVals  = lastWaterMaskData.water_mask_values;
    const w = lastDemData.width;
    const h = lastDemData.height;
    const ptp = lastDemData.vmax - lastDemData.vmin;

    const adjustedDem = demVals.map((v, i) => v - ((waterVals[i] ?? 0) * ptp * waterScale));

    const colormap = document.getElementById('demColormap').value;
    const finiteVals = adjustedDem.filter(Number.isFinite);
    const vmin = finiteVals.reduce((a, b) => a < b ? a : b, finiteVals[0]);
    const vmax = finiteVals.reduce((a, b) => a > b ? a : b, finiteVals[0]);

    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(w, h);

    for (let i = 0; i < adjustedDem.length; i++) {
        const t = (adjustedDem[i] - vmin) / (vmax - vmin);
        const [r, g, b] = mapElevationToColor(t, colormap);
        const idx = i * 4;
        const waterVal = waterVals[i] ?? 0;
        if (waterVal > 0.5 && opacityVal > 0) {
            imgData.data[idx]     = Math.round((r * 255) * (1 - opacityVal));
            imgData.data[idx + 1] = Math.round((g * 255) * (1 - opacityVal) + 100 * opacityVal);
            imgData.data[idx + 2] = Math.round((b * 255) * (1 - opacityVal) + 255 * opacityVal);
        } else {
            imgData.data[idx]     = Math.round(r * 255);
            imgData.data[idx + 1] = Math.round(g * 255);
            imgData.data[idx + 2] = Math.round(b * 255);
        }
        imgData.data[idx + 3] = 255;
    }

    ctx.putImageData(imgData, 0, 0);
    const container = document.getElementById('combinedImage');
    container.innerHTML = '';
    container.appendChild(canvas);
    canvas.style.width = '100%';
    canvas.style.height = 'auto';
}

function applyWaterSubtract() {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastWaterMaskData) {
        showToast('Please load both DEM and Water Mask first.', 'warning');
        return;
    }

    const waterScale = parseFloat(document.getElementById('paramWaterScale').value);
    const demVals    = lastDemData.values;
    const waterVals  = lastWaterMaskData.water_mask_values;
    const ptp = lastDemData.vmax - lastDemData.vmin;

    const adjustedDem = demVals.map((v, i) => v - ((waterVals[i] ?? 0) * ptp * waterScale));
    lastDemData.values = adjustedDem;
    const finiteVals = adjustedDem.filter(Number.isFinite);
    lastDemData.vmin = finiteVals.reduce((a, b) => a < b ? a : b, finiteVals[0]);
    lastDemData.vmax = finiteVals.reduce((a, b) => a > b ? a : b, finiteVals[0]);

    recolorDEM();
    window.switchDemSubtab?.('dem');
    showToast('Water subtraction applied to DEM.', 'success');
}

// ─────────────────────────────────────────────────────────────────────────────
// Land cover editor
// ─────────────────────────────────────────────────────────────────────────────

function renderLandCoverLegend() {
    const container = document.getElementById('landCoverLegend');
    if (!container) return;

    const landCoverConfig = window.appState.landCoverConfig;
    const sortedKeys = Object.keys(landCoverConfig).map(Number).sort((a, b) => a - b);

    let html = '<div style="display:grid;grid-template-columns:28px 1fr 52px;gap:3px 6px;align-items:center;">';
    html += '<div style="font-size:9px;color:#666;grid-column:1">Color</div>';
    html += '<div style="font-size:9px;color:#666;">Type</div>';
    html += '<div style="font-size:9px;color:#666;">Elev</div>';

    for (const val of sortedKeys) {
        const config = landCoverConfig[val];
        const colorHex = '#' + config.color.map(c => c.toString(16).padStart(2, '0')).join('');
        html += `<input type="color" value="${colorHex}" data-lc-color="${val}"
            title="${config.name}" style="width:26px;height:22px;border:1px solid #555;padding:1px;cursor:pointer;border-radius:3px;background:none;">`;
        html += `<span style="font-size:11px;color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${config.name}">${config.name}</span>`;
        html += `<input type="number" value="${config.elevation}" data-lc-elev="${val}"
            step="0.01" min="-1" max="1"
            style="width:100%;background:#3a3a3a;color:#ccc;border:1px solid #444;padding:2px;border-radius:3px;font-size:10px;">`;
    }
    html += '</div>';
    container.innerHTML = html;

    container.querySelectorAll('input[data-lc-color]').forEach(input => {
        input.addEventListener('change', e => {
            const val = parseInt(e.target.dataset.lcColor);
            const hex = e.target.value;
            landCoverConfig[val].color = [
                parseInt(hex.substr(1, 2), 16),
                parseInt(hex.substr(3, 2), 16),
                parseInt(hex.substr(5, 2), 16),
            ];
        });
    });
    container.querySelectorAll('input[data-lc-elev]').forEach(input => {
        input.addEventListener('change', e => {
            const val = parseInt(e.target.dataset.lcElev);
            landCoverConfig[val].elevation = parseFloat(e.target.value) || 0;
        });
    });
}

function setupLandCoverEditor() {
    renderLandCoverLegend();

    const applyBtn = document.getElementById('applyLandCoverMapping');
    if (applyBtn) {
        applyBtn.onclick = () => {
            if (lastWaterMaskData?.esa_values) {
                renderEsaLandCover(lastWaterMaskData);
                showToast('Land cover colors applied', 'success');
            } else {
                showToast('No land cover data loaded', 'warning');
            }
        };
    }

    const resetBtn = document.getElementById('resetLandCoverMapping');
    if (resetBtn) {
        resetBtn.onclick = () => {
            const landCoverConfig   = window.appState.landCoverConfig;
            const landCoverDefaults = window.appState.landCoverConfigDefaults;
            for (const key of Object.keys(landCoverDefaults)) {
                landCoverConfig[key] = JSON.parse(JSON.stringify(landCoverDefaults[key]));
            }
            renderLandCoverLegend();
            if (lastWaterMaskData?.esa_values) renderEsaLandCover(lastWaterMaskData);
            showToast('Land cover colors reset to defaults', 'info');
        };
    }

    const resolutionDropdown = document.getElementById('landCoverResolution');
    if (resolutionDropdown) resolutionDropdown.onchange = () => loadWaterMask();
}

// ─────────────────────────────────────────────────────────────────────────────
// Event wiring
// ─────────────────────────────────────────────────────────────────────────────

function setupWaterMaskListeners() {
    setupLandCoverEditor();

    document.getElementById('applyWaterSubtractBtn')?.addEventListener('click', applyWaterSubtract);
    document.getElementById('previewWaterSubtractBtn')?.addEventListener('click', previewWaterSubtract);

    const waterScaleSlider = document.getElementById('waterScaleSlider');
    if (waterScaleSlider) {
        waterScaleSlider.oninput = () => {
            document.getElementById('waterScaleValue').textContent = waterScaleSlider.value;
        };
    }

    const waterOpacityEl = document.getElementById('waterOpacity');
    if (waterOpacityEl) {
        waterOpacityEl.oninput = () => {
            document.getElementById('waterOpacityValue').textContent = waterOpacityEl.value;
        };
    }

    const waterThreshold = document.getElementById('waterThreshold');
    if (waterThreshold) {
        waterThreshold.oninput = () => {
            document.getElementById('waterThresholdValue').textContent = waterThreshold.value;
        };
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal state helpers
// ─────────────────────────────────────────────────────────────────────────────

function _setLastWaterMaskData(data) {
    lastWaterMaskData = data;
    window.appState.lastWaterMaskData = data;
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.loadWaterMask           = loadWaterMask;
window.renderWaterMask         = renderWaterMask;
window.renderEsaLandCover      = renderEsaLandCover;
window.renderCombinedView      = renderCombinedView;
window.loadSatelliteForTab     = loadSatelliteForTab;
window.previewWaterSubtract    = previewWaterSubtract;
window.applyWaterSubtract      = applyWaterSubtract;
window.renderLandCoverLegend   = renderLandCoverLegend;
window.setupLandCoverEditor    = setupLandCoverEditor;
window.setupWaterMaskListeners = setupWaterMaskListeners;
window.getLastWaterMaskData    = () => lastWaterMaskData;
window.clearLastWaterMaskData  = () => { lastWaterMaskData = null; window.appState.lastWaterMaskData = null; };
