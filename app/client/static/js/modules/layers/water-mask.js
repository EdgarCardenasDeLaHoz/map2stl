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
 *   setupWaterMaskListeners() — wire water mask tab events
 *   clearLastWaterMaskData()  — reset cached water mask data
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
 *   window.applyProjection(canvas, bbox)       — global from dem-loader.js
 *   window.enableZoomAndPan(canvas)            — global from dem-loader.js
 *   window.mapElevationToColor(t, cmap)        — global from dem-loader.js
 *   recolorDEM()                        — global from dem-loader.js
 *   window.isLayerCurrent(name)                — global from app.js file-top
 *   updateLayerStatusIndicators()       — global from app.js file-top
 *   updateCacheStatusUI()               — global from app.js file-top
 *   updateStackedLayers()               — global from stacked-layers.js
 *   window.showToast(msg, type)                — global from app.js file-top
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

    const bbox = window.getBboxCoords(boundingBox, selectedRegion);
    if (!bbox) {
        document.getElementById('waterMaskImage').innerHTML = '<p>Please select a region first.</p>';
        return;
    }
    const { north, south, east, west } = bbox;

    // waterLayerResolution is the per-layer load control; waterResolution is the persisted setting.
    // The load button reads from waterLayerResolution; they are synced on region select.
    const satScale = parseInt(
        document.getElementById('waterLayerResolution')?.value ||
        document.getElementById('waterResolution')?.value || '200'
    );

    const waterDataset = document.getElementById('waterDataset')?.value || 'esa';
    const cacheKey = { ...bbox, sat_scale: satScale, dataset: waterDataset };

    const cachedData = window.waterMaskCache.get(cacheKey);
    if (cachedData) {
        _setLastWaterMaskData(cachedData);
        window.appState.layerBboxes.water = bbox;
        window.appState.layerBboxes.landCover = bbox;
        window.setLayerStatus(['water', 'landCover'], 'loaded');
        updateCacheStatusUI();
        renderWaterMask(cachedData);
        renderEsaLandCover(cachedData);
        window.emitStackUpdate();
        document.getElementById('waterMaskStats').innerHTML =
            `Water pixels: ${cachedData.water_pixels} / ${cachedData.total_pixels} (${cachedData.water_percentage.toFixed(1)}%) <span style="color:#4CAF50;font-size:10px;">[CACHED]</span>`;
        window.showToast('Water & land cover loaded from cache', 'success');
        return;
    }

    const params = new URLSearchParams({ north, south, east, west, sat_scale: satScale, dataset: waterDataset });

    window.setLayerStatus(['water', 'landCover'], 'loading');

    document.getElementById('waterMaskImage').innerHTML = '<div class="loading"><span class="spinner"></span>Loading water mask from Earth Engine...</div>';
    window.showToast('Loading water mask from Earth Engine...', 'info');

    try {
        const { data, error: wmErr } = await window.api.dem.waterMask(params, signal);
        if (wmErr) {
            window.showErrInEl('waterMaskImage', wmErr);
            window.setLayerStatus(['water', 'landCover'], 'error');
            window.showToast('Failed to load water mask: ' + wmErr, 'error');
            return;
        }
        if (data.error) {
            window.showErrInEl('waterMaskImage', data.error);
            window.setLayerStatus(['water', 'landCover'], 'error');
            window.showToast('Failed to load water mask: ' + data.error, 'error');
            return;
        }

        window.waterMaskCache.set(cacheKey, data);
        updateCacheStatusUI();
        _setLastWaterMaskData(data);

        window.appState.layerBboxes.water = bbox;
        window.appState.layerBboxes.landCover = bbox;
        window.setLayerStatus(['water', 'landCover'], 'loaded');

        renderWaterMask(data);
        renderEsaLandCover(data);
        window.emitStackUpdate();

        document.getElementById('waterMaskStats').innerHTML =
            `Water pixels: ${data.water_pixels} / ${data.total_pixels} (${data.water_percentage.toFixed(1)}%)`;
        window.showToast('Water & land cover loaded', 'success');

    } catch (error) {
        if (error.name === 'AbortError') return;
        console.error('Error loading water mask:', error);
        window.showErrInEl('waterMaskImage', error.message);
        window.setLayerStatus(['water', 'landCover'], 'error');
        window.showToast('Failed to load water mask', 'error');
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
            // Water pixel — semi-transparent blue
            imgData.data[idx] = 0;
            imgData.data[idx + 1] = 100;
            imgData.data[idx + 2] = 255;
            imgData.data[idx + 3] = 200;
        } else {
            // Land pixel — fully transparent so DEM/buildings show through
            imgData.data[idx] = 0;
            imgData.data[idx + 1] = 0;
            imgData.data[idx + 2] = 0;
            imgData.data[idx + 3] = 0;
        }
    }

    ctx.putImageData(imgData, 0, 0);
    const currentDemBbox = window.appState.currentDemBbox;
    const projectedCanvas = currentDemBbox ? window.applyProjection(canvas, currentDemBbox) : canvas;
    container.innerHTML = '';
    container.appendChild(projectedCanvas);
    projectedCanvas.style.width = '100%';
    projectedCanvas.style.height = 'auto';
    window.emitStackUpdate();
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
        imgData.data[idx] = color[0];
        imgData.data[idx + 1] = color[1];
        imgData.data[idx + 2] = color[2];
        imgData.data[idx + 3] = 255;
    }

    ctx.putImageData(imgData, 0, 0);
    const currentDemBbox = window.appState.currentDemBbox;
    const projLandCanvas = currentDemBbox ? window.applyProjection(canvas, currentDemBbox) : canvas;
    container.innerHTML = '';
    container.appendChild(projLandCanvas);
    projLandCanvas.style.width = '100%';
    projLandCanvas.style.height = 'auto';

    window.emitStackUpdate();
}

// ─────────────────────────────────────────────────────────────────────────────
// Combined view
// ─────────────────────────────────────────────────────────────────────────────

async function renderCombinedView() {
    const container = document.getElementById('combinedImage');
    const lastDemData = window.appState.lastDemData;

    if (!lastDemData || !window.isLayerCurrent('dem')) {
        container.innerHTML = '<p style="text-align:center;padding:20px;">Load DEM first.</p>';
        return;
    }

    if (!lastWaterMaskData || !window.isLayerCurrent('water')) {
        container.innerHTML = '<p style="text-align:center;padding:20px;">Loading water mask for combined view...</p>';
        await loadWaterMask();
    }

    if (lastWaterMaskData && lastDemData) {
        const demSize = lastDemData.width * lastDemData.height;
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
    const waterVals = lastWaterMaskData?.water_mask_values || [];
    const ptp = vmax - vmin;

    for (let i = 0; i < values.length; i++) {
        let val = values[i];
        if (waterVals[i] && waterVals[i] > 0.5) {
            val = val - (waterVals[i] * ptp * waterScale);
        }
        const t = Math.max(0, Math.min(1, (val - vmin) / (ptp || 1)));
        const [r, g, b] = window.mapElevationToColor(t, colormap);
        const idx = i * 4;
        if (waterVals[i] && waterVals[i] > 0.5 && opacityVal > 0) {
            imgData.data[idx] = Math.round((r * 255) * (1 - opacityVal) + 30 * opacityVal);
            imgData.data[idx + 1] = Math.round((g * 255) * (1 - opacityVal) + 100 * opacityVal);
            imgData.data[idx + 2] = Math.round((b * 255) * (1 - opacityVal) + 220 * opacityVal);
        } else {
            imgData.data[idx] = Math.round(r * 255);
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
    window.enableZoomAndPan(canvas);
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

    document.getElementById('applyLandCoverMapping')?.addEventListener('click', () => {
        if (lastWaterMaskData?.esa_values) {
            renderEsaLandCover(lastWaterMaskData);
            window.showToast('Land cover colors applied', 'success');
        } else {
            window.showToast('No land cover data loaded', 'warning');
        }
    });

    document.getElementById('resetLandCoverMapping')?.addEventListener('click', () => {
        const landCoverConfig = window.appState.landCoverConfig;
        const landCoverDefaults = window.appState.landCoverConfigDefaults;
        for (const key of Object.keys(landCoverDefaults)) {
            landCoverConfig[key] = JSON.parse(JSON.stringify(landCoverDefaults[key]));
        }
        renderLandCoverLegend();
        if (lastWaterMaskData?.esa_values) renderEsaLandCover(lastWaterMaskData);
        window.showToast('Land cover colors reset to defaults', 'info');
    });

    document.getElementById('waterResolution')?.addEventListener('change', () => loadWaterMask());
}

// ─────────────────────────────────────────────────────────────────────────────
// Event wiring
// ─────────────────────────────────────────────────────────────────────────────

function setupWaterMaskListeners() {
    setupLandCoverEditor();

    // applyWaterSubtract / previewWaterSubtract removed (unused feature)

    const waterScaleSlider = document.getElementById('waterScaleSlider');
    if (waterScaleSlider) {
        waterScaleSlider.addEventListener('input', () => {
            document.getElementById('waterScaleValue').textContent = waterScaleSlider.value;
        });
    }

    const waterOpacityEl = document.getElementById('waterOpacity');
    if (waterOpacityEl) {
        waterOpacityEl.addEventListener('input', () => {
            document.getElementById('waterOpacityValue').textContent = waterOpacityEl.value;
        });
    }

    const waterThreshold = document.getElementById('waterThreshold');
    if (waterThreshold) {
        waterThreshold.addEventListener('input', () => {
            document.getElementById('waterThresholdValue').textContent = waterThreshold.value;
        });
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

window.loadWaterMask = loadWaterMask;
window.renderWaterMask = renderWaterMask;
window.renderEsaLandCover = renderEsaLandCover;
window.renderCombinedView = renderCombinedView;
window.setupWaterMaskListeners = setupWaterMaskListeners;
window.clearLastWaterMaskData = () => { lastWaterMaskData = null; window.appState.lastWaterMaskData = null; };
