// ============================================================
// DEM MAIN — modules/dem-main.js
// Extracted from app.js (DOMContentLoaded closure).
// Handles main DEM loading, canvas rendering, DEM empty-state,
// workflow stepper, print dimensions, bed optimizer, and
// satellite image loading.
//
// Loaded as a plain <script> before app.js.
// All functions exposed on window.*
// Closure vars accessed via window.appState.* or window.get*()/set*() getters.
// ============================================================

'use strict';

// Colormap LUT cache — keyed by colormap name; rebuilt only on first use per colormap.
const _lutCache = new Map();

/** Invalidate LUT cache entries. Pass a colormap name to drop one entry, or omit to clear all. */
window._invalidateLutCache = (colormap) => {
    if (colormap) _lutCache.delete(colormap);
    else _lutCache.clear();
};

// Reusable ImageData — avoids re-allocating Uint8ClampedArray on every render.
let _demImageData = null;

/** True for both plain Array and typed arrays (Float32Array, etc.) */
const _isArrayLike = (v) => Array.isArray(v) || ArrayBuffer.isView(v);

// Delegate to shared helpers from ui-helpers.js (loaded before this module).
const _getBboxCoords = (...a) => window.getBboxCoords(...a);
const _showErr       = (...a) => window.showErrInEl(...a);

// Geographic scale factors (WGS-84 approximation)
const GEO_M_PER_DEG_LON = 111320;  // metres per degree longitude at equator
const GEO_M_PER_DEG_LAT = 110540;  // metres per degree latitude

// ---------------------------------------------------------------------------
// _applyDemResult — post-fetch DEM rendering pipeline
// ---------------------------------------------------------------------------

/**
 * Render a successful DEM API response to the canvas and update all dependent UI.
 * Called by loadDEM after a successful fetch. Not exposed on window.*.
 *
 * @param {Object} data            - Parsed API response with dem_values, dimensions, etc.
 * @param {number} north/south/east/west - Bbox bounds for the loaded DEM
 */
function _applyDemResult(data, north, south, east, west) {
    let demVals = data.dem_values;
    let h = Number(data.dimensions[0]);
    let w = Number(data.dimensions[1]);

    // Handle nested arrays
    if (Array.isArray(demVals) && demVals.length && Array.isArray(demVals[0])) {
        h = demVals.length;
        w = demVals[0].length;
        demVals = demVals.flat();
    }

    const colormap = document.getElementById('demColormap').value;
    const finiteVals = demVals.filter(Number.isFinite);
    const calcMin = finiteVals.length ? finiteVals.reduce((a, b) => a < b ? a : b, finiteVals[0]) : 0;
    const calcMax = finiteVals.length ? finiteVals.reduce((a, b) => a > b ? a : b, finiteVals[0]) : 1;
    const vmin = data.min_elevation !== undefined ? data.min_elevation : calcMin;
    const vmax = data.max_elevation !== undefined ? data.max_elevation : calcMax;

    // Store bounding box for gridlines
    window.appState.currentDemBbox = { north, south, east, west };

    // Render DEM canvas — projection applied server-side, no client warp needed
    const canvas = window.renderDEMCanvas?.(demVals, w, h, colormap, vmin, vmax);
    const container = document.getElementById('demImage');
    container.innerHTML = '';
    container.appendChild(canvas);
    // Fill container width, preserve aspect ratio
    canvas.style.width = '100%';
    canvas.style.height = 'auto';
    container.style.position = 'relative';

    // Update overlays
    window.updateAxesOverlay?.(window.appState.currentDemBbox);
    window.drawColorbar?.(vmin, vmax, colormap);
    window.drawHistogram?.(demVals);

    // Draw gridlines after canvas is appended and sized
    requestAnimationFrame(() => window.drawGridlinesOverlay?.('demImage'));

    // Update stacked layers view
    window.emitStackUpdate();

    // Populate bbox fine-tune inputs
    window.setBboxInputValues?.(north, south, east, west);
    const elevRange = document.getElementById('bboxElevRange');
    if (elevRange) elevRange.textContent = `Elevation: ${vmin.toFixed(1)}m — ${vmax.toFixed(1)}m`;

    // Sync mini-map rectangle to new bbox
    window.syncBboxMiniMap?.();

    // Update rescale inputs with current values
    document.getElementById('rescaleMin').value = Math.floor(vmin);
    document.getElementById('rescaleMax').value = Math.ceil(vmax);

    // Handle landuse/satellite data if available
    const landuseContainer = document.getElementById('demLanduse');
    const landuseWrapper = document.querySelector('.dem-landuse-container');
    if (data.sat_values && data.sat_dimensions && data.sat_available) {
        const sat_h = data.sat_dimensions[0];
        const sat_w = data.sat_dimensions[1];
        const satCanvas = window.renderSatelliteCanvas?.(data.sat_values, sat_w, sat_h);
        landuseContainer.innerHTML = '';
        landuseContainer.appendChild(satCanvas);
        landuseWrapper.classList.remove('hidden');
    } else {
        landuseWrapper.classList.add('hidden');
    }

    // Enable zoom/pan on new canvas
    window.enableZoomAndPan?.(canvas);

    // Capture a small thumbnail for the sidebar
    const currentSelectedRegion = window.appState.selectedRegion;
    if (currentSelectedRegion?.name) {
        try {
            const thumbCanvas = document.createElement('canvas');
            thumbCanvas.width = 48; thumbCanvas.height = 30;
            thumbCanvas.getContext('2d').drawImage(canvas, 0, 0, 48, 30);
            window.saveRegionThumbnail?.(currentSelectedRegion.name, thumbCanvas.toDataURL('image/jpeg', 0.6));
            window.renderCoordinatesList?.();
        } catch (_) { }
    }

    // Store bbox on lastDemData for physical dimensions calculation
    if (window.appState.lastDemData) window.appState.lastDemData.bbox = { north, south, east, west };

    // Cities: refresh city overlay on DEM canvas after reload
    if (window.appState?.osmCityData) requestAnimationFrame(() => window.renderCityOnDEM?.());

    // Auto-load city data if any city layer toggle is enabled and region is small enough
    const _anyLayerOn = ['layerBuildingsToggle', 'layerRoadsToggle', 'layerWaterwaysToggle']
        .some(id => document.getElementById(id)?.checked);
    if (_anyLayerOn && !window.appState?.osmCityData && typeof window.loadCityData === 'function') {
        window.loadCityData?.();
    }

    // Update print dimensions panel (Extrude tab)
    window.updatePrintDimensions?.();

    window.showToast?.(`DEM loaded (${vmin.toFixed(0)}m - ${vmax.toFixed(0)}m)`, 'success');
}

// ---------------------------------------------------------------------------
// loadDEM
// ---------------------------------------------------------------------------

/**
 * Main DEM loader. Fetches DEM data from /api/terrain/dem for the current bbox,
 * renders to canvas with colormap and projection, draws histogram and colorbar,
 * stores result in appState.lastDemData, and updates stacked layers.
 * Exposed as window.loadDEM for HTML onclick access.
 * @param {boolean} [highRes=false] - Use 400px dim instead of the form value
 * @returns {Promise<void>}
 */
window.loadDEM = async function loadDEM(highRes = false) {
    // Abort any in-flight DEM request before starting a new one
    if (window.loadDEM._controller) {
        window.loadDEM._controller.abort();
    }
    window.loadDEM._controller = new AbortController();
    const signal = window.loadDEM._controller.signal;

    const boundingBox = window.getBoundingBox?.();
    const selectedRegion = window.appState.selectedRegion;

    const coords = _getBboxCoords(boundingBox, selectedRegion);
    if (!coords) {
        document.getElementById('demImage').innerHTML = '<p>Please select a region or draw a bounding box first.</p>';
        window.showToast?.('Please select a region first', 'warning');
        return;
    }
    const { north, south, east, west } = coords;

    const demSource = document.getElementById('paramDemSource')?.value || 'local';
    const p = window.appState.demParams;

    const params = new URLSearchParams({
        north, south, east, west,
        dim: highRes ? 400 : document.getElementById('paramDim').value,
        depth_scale: p.depthScale,
        water_scale: p.waterScale,
        subtract_water: p.subtractWater,
        dataset: 'esa',
        dem_source: demSource,
        projection: document.getElementById('paramProjection')?.value || 'none',
        maintain_dimensions: true,
    });

    // Clear DEM cache before loading new DEM
    window.clearLayerCache?.();

    // Update layer status
    window.setLayerStatus('dem', 'loading');

    // Show loading overlay on stacked layers view
    const stackContainer = document.getElementById('dem-image-section');
    if (stackContainer) window.showLoading?.(stackContainer, 'Loading DEM...');

    // Show loading indicator and clear old DEM
    const demImageContainer = document.getElementById('demImage');
    demImageContainer.innerHTML = `<div class="loading"><span class="spinner"></span>Loading DEM... <button onclick="window.loadDEM._controller&&window.loadDEM._controller.abort()" class="dem-cancel-btn">✕ Cancel</button></div>`;
    window.showToast?.('Loading DEM data...', 'info');

    // Optionally, show a progress bar
    let progressBar = document.createElement('div');
    progressBar.className = 'dem-progress-bar';
    progressBar.innerHTML = '<div style="width:0%" id="demProgress"></div>';
    demImageContainer.appendChild(progressBar);

    try {
        const { data, error: loadErr } = await window.api.dem.load(params, signal);
        if (signal.aborted) return;  // intentional cancellation — not an error
        if (loadErr) {
            console.error('Failed to load /api/terrain/dem:', loadErr);
            _showErr('demImage', loadErr);
            window.setLayerStatus('dem', 'error');
            window.showToast?.('Failed to load DEM: ' + loadErr, 'error');
            return;
        }

        if (data.error) {
            _showErr('demImage', data.error);
            window.setLayerStatus('dem', 'error');
            window.showToast?.('Failed to load DEM: ' + data.error, 'error');
            return;
        }

        // Track bbox and update status
        window.appState.layerBboxes.dem = { north, south, east, west };
        window.setLayerStatus('dem', 'loaded');

        // Remove loading overlay from stacked layers
        const stackC = document.getElementById('dem-image-section');
        if (stackC) window.hideLoading?.(stackC);

        // Client-side rendering of DEM data
        if (data.dem_values && data.dimensions) {
            _applyDemResult(data, north, south, east, west);
        } else {
            document.getElementById('demImage').innerHTML = '<p>No DEM data available</p>';
            window.setLayerStatus('dem', 'error');
            window.showToast?.('No DEM data available', 'warning');
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            document.getElementById('demImage').innerHTML = '<p>DEM load cancelled.</p>';
            window.setLayerStatus('dem', 'empty');
            return;
        }
        console.error('Error loading DEM:', error);
        console.error('Error stack:', error.stack);
        _showErr('demImage', `Failed to load DEM: ${error.message || error}`);
        window.setLayerStatus('dem', 'error');
        window.showToast?.('Failed to load DEM', 'error');
    } finally {
        const stackF = document.getElementById('dem-image-section');
        if (stackF) window.hideLoading?.(stackF);
    }
};

// ---------------------------------------------------------------------------
// renderDEMCanvas
// ---------------------------------------------------------------------------

/**
 * Render elevation values to a canvas element using a colour lookup table.
 * Stores data in appState.lastDemData, then updates layer status.
 * @param {number[]} values - Flat array of elevation values (row-major)
 * @param {number} width - Canvas width in pixels
 * @param {number} height - Canvas height in pixels
 * @param {string} colormap - Colormap name ('terrain','viridis','jet','rainbow','hot','gray')
 * @param {number} [vmin] - Minimum value for colour mapping
 * @param {number} [vmax] - Maximum value for colour mapping
 * @returns {HTMLCanvasElement} The rendered canvas element
 */
window.renderDEMCanvas = function renderDEMCanvas(values, width, height, colormap, vmin, vmax) {
    // Store last DEM data
    const lastDemData = { values: _isArrayLike(values) ? values : [], width, height, colormap, vmin, vmax };
    window.appState.lastDemData = lastDemData;

    window._setDemEmptyState?.(false);
    window._updateWorkflowStepper?.();

    // Let curve-editor.js re-normalize control points and insert sea-level marker.
    window.appState._onDemLoaded?.(vmin, vmax);
    window.appState.curveDataVmin = vmin;
    window.appState.curveDataVmax = vmax;

    // Track DEM layer bbox
    const currentDemBbox = window.appState.currentDemBbox;
    if (currentDemBbox) {
        window.appState.layerBboxes.dem = { ...currentDemBbox };
        window.setLayerStatus('dem', 'loaded');
    }

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    // Always create a fresh ImageData — never reuse across renders.
    // Reusing _demImageData when dim changes causes putImageData to write a
    // mismatched buffer onto the new canvas, corrupting the display.
    const img = new ImageData(width, height);
    _demImageData = img;

    const data = img.data;
    const flat = _isArrayLike(values) ? values : [];
    const len = flat.length;

    // Find min/max
    let calcMin = Infinity, calcMax = -Infinity;
    for (let i = 0; i < len; i++) {
        const v = flat[i];
        if (Number.isFinite(v)) {
            if (v < calcMin) calcMin = v;
            if (v > calcMax) calcMax = v;
        }
    }
    if (calcMin === Infinity) calcMin = 0;
    if (calcMax === -Infinity) calcMax = 1;

    const min = (typeof vmin === 'number') ? vmin : calcMin;
    const max = (typeof vmax === 'number') ? vmax : calcMax;
    const range = (max - min) || 1;
    const invRange = 1 / range;

    // Pre-compute colour lookup table (cached by colormap name)
    if (!_lutCache.has(colormap)) {
        const lut = new Uint8Array(1024 * 3);
        for (let i = 0; i < 1024; i++) {
            const t = i / 1023;
            const [r, g, b] = window.mapElevationToColor?.(t, colormap) || [0, 0, 0];
            lut[i * 3] = Math.round((r || 0) * 255);
            lut[i * 3 + 1] = Math.round((g || 0) * 255);
            lut[i * 3 + 2] = Math.round((b || 0) * 255);
        }
        _lutCache.set(colormap, lut);
    }
    const colorLUT = _lutCache.get(colormap);

    const total = width * height;
    for (let i = 0; i < total; i++) {
        const val = (i < len) ? flat[i] : NaN;
        const idx = i << 2;

        if (Number.isFinite(val)) {
            const t = (val - min) * invRange;
            const tClamped = t < 0 ? 0 : (t > 1 ? 1 : t);
            const lutIdx = (tClamped * 1023 + 0.5 | 0) * 3;
            data[idx] = colorLUT[lutIdx];
            data[idx + 1] = colorLUT[lutIdx + 1];
            data[idx + 2] = colorLUT[lutIdx + 2];
            data[idx + 3] = 255;
        } else {
            data[idx] = data[idx + 1] = data[idx + 2] = data[idx + 3] = 0;
        }
    }
    ctx.putImageData(img, 0, 0);
    canvas.style.maxWidth = '100%';
    canvas.style.height = 'auto';
    canvas.style.display = 'block';

    return canvas;
};

// Resize handler: ensure canvas scales to container
window.addEventListener('resize', () => {
    const container = document.getElementById('demImage');
    if (!container) return;
    const canvas = container.querySelector('canvas');
    if (canvas) {
        canvas.style.width = '100%';
        canvas.style.height = '100%';
    }
});

// ---------------------------------------------------------------------------
// _setDemEmptyState
// ---------------------------------------------------------------------------

/**
 * Show or hide the DEM empty state and layers container.
 * @param {boolean} isEmpty
 */
window._setDemEmptyState = function _setDemEmptyState(isEmpty) {
    const emptyEl = document.getElementById('demEmptyState');
    const layersEl = document.getElementById('layersContainer');
    if (emptyEl) emptyEl.style.display = isEmpty ? 'flex' : 'none';
    if (layersEl) layersEl.style.display = isEmpty ? 'none' : '';
};

// ---------------------------------------------------------------------------
// _updateWorkflowStepper
// ---------------------------------------------------------------------------

/**
 * Update the workflow stepper in the header.
 * Three steps: (1) region selected, (2) DEM loaded, (3) model generated.
 */
window._updateWorkflowStepper = function _updateWorkflowStepper() {
    const step1Done = !!window.appState.selectedRegion;
    const step2Done = !!window.appState.lastDemData;
    const step3Done = !!window.appState.generatedModelData;

    document.getElementById('tabExplore')?.classList.toggle('step-done', step1Done);
    document.getElementById('tabEdit')?.classList.toggle('step-done', step2Done);
    document.getElementById('tabExtrude')?.classList.toggle('step-done', step3Done);

    const hint = document.getElementById('workflowHint');
    const hintText = document.getElementById('workflowHintText');
    if (!hint || !hintText) return;

    if (step1Done && step2Done && step3Done) {
        hint.hidden = true;
        return;
    }
    hint.hidden = false;

    function _stepEl(n, label, state) {
        const icon = state === 'done' ? '✓' : String(n);
        return `<span class="workflow-hint-step ${state}">${icon} ${label}</span>`;
    }

    const s1 = _stepEl(1, 'Select region', step1Done ? 'done' : 'active');
    const s2 = _stepEl(2, 'Load DEM', step2Done ? 'done' : (step1Done ? 'active' : 'pending'));
    const s3 = _stepEl(3, 'Generate model', step3Done ? 'done' : (step2Done ? 'active' : 'pending'));

    hintText.innerHTML = `${s1} <span class="workflow-hint-sep">›</span> ${s2} <span class="workflow-hint-sep">›</span> ${s3}`;
};

// ---------------------------------------------------------------------------
// updatePrintDimensions
// ---------------------------------------------------------------------------

/**
 * Update the physical dimensions panel in the Extrude tab.
 * Calculates real-world bbox area, print footprint in mm, map scale,
 * model height, and bed fit. Pure JS — no backend call needed.
 */
window.updatePrintDimensions = function updatePrintDimensions() {
    const panel = document.getElementById('printDimensions');
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.width || !lastDemData.height) {
        panel.style.display = 'none';
        return;
    }

    // Use projected canvas dimensions — projection can change aspect ratio (e.g. lambert)
    const demCanvas = document.querySelector('#demImage canvas:not(.dem-gridlines-overlay):not(.city-dem-overlay):not(.water-dem-overlay):not(.sat-dem-overlay)');
    const gridW = demCanvas?.width || lastDemData.width;
    const gridH = demCanvas?.height || lastDemData.height;
    const modelH = parseFloat(document.getElementById('modelResolution').value) || 200;
    const baseH = parseFloat(document.getElementById('exportBaseHeight')?.value) || 0;
    const totalH = modelH + baseH;

    document.getElementById('dimFootprint').textContent = `${gridW} × ${gridH} mm`;
    document.getElementById('dimHeight').textContent = `${totalH} mm (${modelH} terrain + ${baseH} base)`;

    const selectedRegion = window.appState.selectedRegion;
    const bbox = lastDemData.bbox || (selectedRegion ? {
        north: selectedRegion.north, south: selectedRegion.south,
        east: selectedRegion.east, west: selectedRegion.west
    } : null);

    if (bbox) {
        const midLat = (bbox.north + bbox.south) / 2;
        const latCos = Math.cos(midLat * Math.PI / 180);
        const realW_m = Math.abs(bbox.east - bbox.west) * GEO_M_PER_DEG_LON * latCos;
        const realH_m = Math.abs(bbox.north - bbox.south) * GEO_M_PER_DEG_LAT;
        const realW_km = realW_m / 1000;
        const realH_km = realH_m / 1000;

        document.getElementById('dimRealArea').textContent =
            `${realW_km.toFixed(1)} × ${realH_km.toFixed(1)} km`;

        const scale = Math.round(realW_m / (gridW / 1000));
        document.getElementById('dimScale').textContent = `1 : ${scale.toLocaleString()}`;

        const beds = [
            { name: 'Ender 220', w: 220, h: 220 },
            { name: 'Prusa 250', w: 250, h: 210 },
            { name: 'Bambu 256', w: 256, h: 256 },
            { name: 'Bambu 350', w: 350, h: 350 },
        ];
        const fitting = beds.filter(b => gridW <= b.w && gridH <= b.h);
        const fitRow = document.getElementById('dimBedFitRow');
        const fitText = document.getElementById('dimBedFitText');
        if (fitting.length > 0) {
            fitText.textContent = '✓ ' + fitting.map(b => b.name).join(', ');
            fitRow.style.color = '#52b788';
        } else {
            fitText.textContent = '⚠ exceeds standard beds';
            fitRow.style.color = '#e67e22';
        }
    } else {
        document.getElementById('dimRealArea').textContent = '—';
        document.getElementById('dimScale').textContent = '—';
        document.getElementById('dimBedFitText').textContent = '—';
    }

    panel.style.display = 'block';

    // Bed optimizer
    window._updateBedOptimizer?.(bbox);
};

// ---------------------------------------------------------------------------
// _updateBedOptimizer
// ---------------------------------------------------------------------------

/**
 * Compute the recommended resolution and print scale for the selected printer bed.
 * @param {Object|null} bbox - {north, south, east, west} or null
 */
window._updateBedOptimizer = function _updateBedOptimizer(bbox) {
    const resultEl = document.getElementById('bedOptimizerResult');
    if (!resultEl || !bbox) return;

    const sel = document.getElementById('bedSizeSelect')?.value || '250x210';
    let bedW, bedH;
    if (sel === 'custom') {
        bedW = parseFloat(document.getElementById('bedCustomW')?.value) || 220;
        bedH = parseFloat(document.getElementById('bedCustomH')?.value) || 220;
    } else {
        [bedW, bedH] = sel.split('x').map(Number);
    }

    const midLat = (bbox.north + bbox.south) / 2;
    const latCos = Math.cos(midLat * Math.PI / 180);
    const realW_m = Math.abs(bbox.east - bbox.west) * 111320 * latCos;
    const realH_m = Math.abs(bbox.north - bbox.south) * 110540;

    const aspectRatio = realW_m / realH_m;
    let printW, printH;
    if (aspectRatio >= bedW / bedH) {
        printW = bedW; printH = bedW / aspectRatio;
    } else {
        printH = bedH; printW = bedH * aspectRatio;
    }

    const scale = Math.round(realW_m / (printW / 1000));
    const pieces = (printW > bedW || printH > bedH) ? Math.ceil(printW / bedW) * Math.ceil(printH / bedH) : 1;
    const recRes = Math.min(600, Math.max(100, Math.round(printW / 0.5 / 100) * 100));

    let html = `<b>Fit to ${bedW}×${bedH} mm bed:</b><br>`;
    html += `Print size: ${printW.toFixed(0)} × ${printH.toFixed(0)} mm<br>`;
    html += `Scale: 1 : ${scale.toLocaleString()}<br>`;
    html += `Recommended resolution: ${recRes}×${recRes}<br>`;
    if (pieces > 1) {
        html += `<span style="color:#e67e22;">⚠ ${printW.toFixed(0)}×${printH.toFixed(0)} mm exceeds bed — needs ${pieces}-piece puzzle</span>`;
    } else {
        html += `<span style="color:#52b788;">✓ Fits bed with ${(bedW - printW).toFixed(0)}×${(bedH - printH).toFixed(0)} mm margin</span>`;
    }
    resultEl.innerHTML = html;
};

// ---------------------------------------------------------------------------
// loadSatelliteImage
// ---------------------------------------------------------------------------

let _satelliteAbortController = null;
let _satelliteRGBAbortController = null;

/**
 * Load satellite/land cover imagery from /api/terrain/dem with show_sat=true.
 * Renders the result to the #satelliteImage container.
 * @returns {Promise<void>}
 */
window.loadSatelliteImage = async function loadSatelliteImage() {
    if (_satelliteAbortController) _satelliteAbortController.abort();
    _satelliteAbortController = new AbortController();
    const signal = _satelliteAbortController.signal;

    const boundingBox = window.getBoundingBox?.();
    const selectedRegion = window.appState.selectedRegion;

    const coords = _getBboxCoords(boundingBox, selectedRegion);
    if (!coords) {
        document.getElementById('satelliteImage').innerHTML = '<p>Please select a region or draw a bounding box first.</p>';
        return;
    }
    const { north, south, east, west } = coords;
    const resolution = document.getElementById('waterLayerResolution')?.value ||
                       document.getElementById('waterResolution')?.value || '200';
    const dataset    = document.getElementById('waterDataset')?.value   || 'esa';

    const params = new URLSearchParams({
        north, south, east, west,
        dim: resolution,
        show_sat: true,
        dataset
    });

    document.getElementById('satelliteImage').innerHTML = '<p class="loading">Loading satellite data...</p>';

    try {
        const { data, error: satErr } = await window.api.dem.load(params, signal);
        if (satErr) {
            _showErr('satelliteImage', satErr);
            return;
        }

        if (data.error) {
            _showErr('satelliteImage', data.error);
            return;
        }

        if (data.sat_values && data.sat_dimensions && data.sat_available) {
            const sat_h = data.sat_dimensions[0];
            const sat_w = data.sat_dimensions[1];
            const canvas = window.renderSatelliteCanvas?.(data.sat_values, sat_w, sat_h);
            canvas.style.width = '100%';
            canvas.style.height = 'auto';
            document.getElementById('satelliteImage').innerHTML = '';
            document.getElementById('satelliteImage').appendChild(canvas);
            window.emitStackUpdate();
        } else {
            document.getElementById('satelliteImage').innerHTML =
                '<div class="sat-unavailable"><p>Satellite data not available</p><p>Earth Engine module required</p></div>';
        }
    } catch (error) {
        if (error.name === 'AbortError') return;
        console.error('Error loading satellite image:', error);
        document.getElementById('satelliteImage').innerHTML = '<p>Failed to load satellite image.</p>';
    }
};

// ---------------------------------------------------------------------------
// loadSatelliteRGBImage — fetch real satellite tiles from ESRI WMTS
// ---------------------------------------------------------------------------

/**
 * Fetch real satellite imagery from /api/terrain/satellite (ESRI World Imagery).
 * Renders the base64 JPEG to a source canvas stored on appState.satImgSourceCanvas,
 * then triggers a stacked layers update.
 * @returns {Promise<void>}
 */
window.loadSatelliteRGBImage = async function loadSatelliteRGBImage() {
    if (_satelliteRGBAbortController) _satelliteRGBAbortController.abort();
    _satelliteRGBAbortController = new AbortController();
    const signal = _satelliteRGBAbortController.signal;

    const boundingBox = window.getBoundingBox?.();
    const selectedRegion = window.appState.selectedRegion;

    const coords = _getBboxCoords(boundingBox, selectedRegion);
    if (!coords) {
        window.showToast?.('Please select a region or draw a bounding box first.', 'warning');
        return;
    }
    const { north, south, east, west } = coords;

    const dim = parseInt(
        document.getElementById('satImgResolution')?.value ||
        document.getElementById('paramDim')?.value || 400
    );
    const params = new URLSearchParams({ north, south, east, west, dim });

    window.showToast?.('Loading satellite imagery...', 'info');

    try {
        const { data, error: satImgErr } = await window.api.dem.satellite(params, signal);
        if (satImgErr) throw new Error(satImgErr);

        // Draw the JPEG into a canvas, apply projection, and store as source
        const bbox = window.appState?.currentDemBbox || { north, south, east, west };
        await new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => {
                const raw = document.createElement('canvas');
                raw.width = img.naturalWidth;
                raw.height = img.naturalHeight;
                raw.getContext('2d').drawImage(img, 0, 0);
                // Store raw canvas for re-projection when projection setting changes
                window.appState._satImgRawCanvas = raw;
                window.appState._satImgBbox = bbox;
                // Apply the same projection as DEM/water canvases so layers align
                const proj = window.applyProjection ? window.applyProjection(raw, bbox) : raw;
                window.appState.satImgSourceCanvas = proj;
                resolve();
            };
            img.onerror = reject;
            img.src = `data:image/jpeg;base64,${data.image}`;
        });

        window.events?.emit(window.EV?.STACKED_UPDATE);
        window.showToast?.('Satellite imagery loaded', 'success');
    } catch (err) {
        if (err.name === 'AbortError') return;
        console.error('loadSatelliteRGBImage error:', err);
        window.showToast?.(`Satellite load failed: ${err.message}`, 'error');
    }
};

/**
 * Re-project the satellite RGB canvas using the current projection setting.
 * Called when the projection dropdown changes (matches _reprojectCityRaster pattern).
 */
window._reprojectSatelliteImage = function _reprojectSatelliteImage() {
    const raw = window.appState?._satImgRawCanvas;
    const bbox = window.appState?._satImgBbox;
    if (!raw || !bbox) return;
    const proj = window.applyProjection ? window.applyProjection(raw, bbox) : raw;
    window.appState.satImgSourceCanvas = proj;
};

// ---------------------------------------------------------------------------
// DOMContentLoaded: initialise empty state and workflow stepper
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    window._setExportButtonsEnabled?.(false);
    window._setDemEmptyState?.(true);
    window._updateWorkflowStepper?.();

    // Wire appState callbacks so other modules (e.g., presets.js) can trigger them
    window.appState._setDemEmptyState = window._setDemEmptyState;
    window.appState._updateWorkflowStepper = window._updateWorkflowStepper;
});
