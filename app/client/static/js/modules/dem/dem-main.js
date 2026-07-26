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

/** True for both plain Array and typed arrays (Float32Array, etc.) */
const _isArrayLike = (v) => Array.isArray(v) || ArrayBuffer.isView(v);

// Delegate to shared helpers from ui-helpers.js (loaded before this module).
const _getBboxCoords = (...a) => window.getBboxCoords(...a);
const _showErr = (...a) => window.showErrInEl(...a);

// ---------------------------------------------------------------------------
// Off-thread DEM rendering worker (Plan A)
// ---------------------------------------------------------------------------

// Lazy-initialised singleton. null = not yet created; false = unavailable.
let _demWorker = null;
let _demWorkerOk = null;   // null = untested, true = ok, false = failed
let _demWorkerGen = 0;     // incremented each call; stale responses are discarded

/** Map from gen → { canvas, ctx } for pending worker renders. */
const _demWorkerPending = new Map();

function _getDemWorker() {
    if (_demWorkerOk === false) return null;
    if (_demWorker) return _demWorker;
    try {
        _demWorker = new Worker('/static/js/workers/dem-render-worker.js');
        _demWorker.onmessage = _onDemWorkerMessage;
        _demWorker.onerror = () => { _demWorkerOk = false; _demWorker = null; };
        _demWorkerOk = true;
    } catch (_e) {
        _demWorkerOk = false;
        _demWorker = null;
    }
    return _demWorker;
}

function _onDemWorkerMessage({ data }) {
    const { type, gen, pixels, width, height, message } = data;
    const pending = _demWorkerPending.get(gen);
    _demWorkerPending.delete(gen);
    if (!pending) return;  // stale — discard

    if (type === 'error') {
        console.warn('[dem-render-worker] error:', message);
        return;
    }

    const { canvas, ctx, onReady } = pending;
    const img = new ImageData(pixels, width, height);
    ctx.putImageData(img, 0, 0);
    onReady?.(canvas);
}

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
    let demVals = window.decodeDemValues(data);
    let h = Number(data.dimensions[0]);
    let w = Number(data.dimensions[1]);

    // Handle nested arrays (legacy plain-array path only)
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

    // A freshly-fetched DEM invalidates any previously-applied composite —
    // export must not ship stale composite values against new terrain.
    if (window.appState) window.appState._newCompositeApplied = false;

    // Render DEM canvas — projection applied server-side, no client warp needed
    const canvas = window.renderDEMCanvas?.(demVals, w, h, colormap, vmin, vmax);
    const container = document.getElementById('demImage');
    container.innerHTML = '';
    container.appendChild(canvas);
    canvas.classList.add('dem-canvas-responsive');
    container.classList.add('dem-container-relative');

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
        if (landuseContainer && satCanvas) {
            landuseContainer.innerHTML = '';
            landuseContainer.appendChild(satCanvas);
        }
        landuseWrapper?.classList.remove('hidden');
    } else {
        landuseWrapper?.classList.add('hidden');
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
    const _anyLayerOn = ['cityLayerBuildings', 'cityLayerRoads', 'cityLayerWaterways']
        .some(id => document.getElementById(id)?.checked);
    if (_anyLayerOn && !window.appState?.osmCityData && typeof window.loadCityData === 'function') {
        window.loadCityData?.();
    }

    // Update print dimensions panel (Extrude tab)
    window.updatePrintDimensions?.();

    const clipOn = document.getElementById('paramClipNans')?.checked ? 'on' : 'off';
    const dimsText = `${h}x${w}`;
    window.showToast?.(
        `DEM loaded (${vmin.toFixed(0)}m - ${vmax.toFixed(0)}m, ${dimsText}, clip:${clipOn})`,
        'success',
    );
}

// ---------------------------------------------------------------------------
// loadDEM
// ---------------------------------------------------------------------------

/**
 * Main DEM loader. Fetches DEM data from /api/terrain/dem for the current bbox,
 * renders to canvas with colormap and projection, draws histogram and colorbar,
 * stores result in appState.lastDemData, and updates stacked layers.
 * Exposed as window.loadDEM for HTML onclick access.
 * @param {boolean} [highRes=false] - Use 600px dim instead of the form value
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
    const proj = window.getProjectionParams();

    const params = new URLSearchParams({
        north, south, east, west,
        dim: highRes ? 600 : document.getElementById('paramDim').value,
        depth_scale: p.depthScale,
        water_scale: p.waterScale,
        subtract_water: p.subtractWater,
        dataset: 'esa',
        dem_source: demSource,
        projection: proj.projection,
        maintain_dimensions: proj.maintainDimensions,
        clip_valid_region: proj.clipValidRegion,
    });

    // Clear DEM cache before loading new DEM
    window.clearLayerCache?.();
    // While loading, show spinner/canvas area and hide the empty-state message.
    window._setDemEmptyState?.(false);

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

        // The server flags DEMs that came back with no real relief (the source
        // had no coverage for this bbox — e.g. a continent-scale region on the
        // local SRTM tiles). Warn the user instead of showing a flat map that
        // then fails to export. Record it so export/preview can react too.
        window.appState.lastDemEmpty = !!data.dem_empty;
        if (data.dem_empty) {
            const msg = data.dem_warning ||
                'No elevation data covers this region.';
            // The layer technically loaded (a flat array), so leave the status
            // as 'loaded'; the toast carries the actionable warning.
            window.showToast?.(msg + ' (See 🩺 Diagnostics or 🔑 Keys.)', 'warning', 9000);
        }

        // Remove loading overlay from stacked layers
        const stackC = document.getElementById('dem-image-section');
        if (stackC) window.hideLoading?.(stackC);

        // Client-side rendering of DEM data
        if ((data.dem_values || data.dem_values_b64) && data.dimensions) {
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

    // Notify curve-editor.js (and any other listeners) that a new DEM is loaded.
    window.events?.emit(window.EV?.DEM_LOADED, vmin, vmax);
    // Auto-rebuild the 3D model if the Extrude view is currently open.
    window._modelViewerAutoRebuild?.();
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
    canvas.style.maxWidth = '100%';
    canvas.classList.add('dem-canvas-responsive');
    const ctx = canvas.getContext('2d');

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
        _lutCache.set(colormap, window.buildColorLUT(colormap));
    }
    const colorLUT = _lutCache.get(colormap);

    // Attempt off-thread rendering via dem-render-worker.js
    const worker = _getDemWorker();
    if (worker) {
        const gen = ++_demWorkerGen;
        // Clone the LUT before transferring so the cache entry remains valid.
        const lutCopy = colorLUT.slice();
        // Always copy values to a fresh Float32Array before transferring its
        // buffer — the original `flat` may alias lastDemData.values.
        const flatValues = new Float32Array(flat);
        _demWorkerPending.set(gen, {
            canvas,
            ctx,
            onReady: (c) => window._onDemCanvasReady?.(c),
        });
        worker.postMessage(
            { gen, values: flatValues, width, height, lut: lutCopy, vmin: min, vmax: max },
            [flatValues.buffer, lutCopy.buffer],
        );
        // Return the canvas immediately — pixels are filled asynchronously.
        return canvas;
    }

    // Sync fallback — runs when Worker API is unavailable.
    const img = new ImageData(width, height);
    const data = img.data;
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

    return canvas;
};

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

    // Never show the empty-state message if a DEM canvas is already present.
    const hasDemCanvas = !!document.querySelector(`#demImage ${window.DEM_CANVAS_SELECTOR}`)
        || ((document.getElementById('layerDemCanvas')?.width || 0) > 0);
    if (isEmpty && hasDemCanvas) {
        if (emptyEl) emptyEl.classList.add('hidden');
        return;
    }

    if (emptyEl) emptyEl.classList.toggle('hidden', !isEmpty);
    if (layersEl) layersEl.classList.toggle('hidden', isEmpty);

    // Guard against a broken subtab state where every render pane is hidden.
    if (!isEmpty) {
        const layersHidden = document.getElementById('layersContainer')?.classList.contains('hidden') ?? true;
        const compareHidden = document.getElementById('compareInlineContainer')?.classList.contains('hidden') ?? true;
        const combinedHidden = document.getElementById('combinedContainer')?.classList.contains('hidden') ?? true;
        const mergeHidden = document.getElementById('mergePanel')?.classList.contains('hidden') ?? true;
        if (layersHidden && compareHidden && combinedHidden && mergeHidden) {
            window.switchDemSubtab?.(window.appState?.activeDemSubtab || 'layers');
        }
    }
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
    if (!panel) return;  // Extrude tab not mounted yet — nothing to update
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.width || !lastDemData.height) {
        panel.classList.add('hidden');
        return;
    }

    // Use projected canvas dimensions — projection can change aspect ratio (e.g. lambert)
    const demCanvas = document.querySelector(`#demImage ${window.DEM_CANVAS_SELECTOR}`);
    const gridW = demCanvas?.width || lastDemData.width;
    const gridH = demCanvas?.height || lastDemData.height;
    const mmPerPx = parseFloat(document.getElementById('mmPerPixel')?.value) || 1.0;
    const modelH = parseFloat(document.getElementById('exportModelHeight')?.value) || 30;
    const baseH = parseFloat(document.getElementById('exportBaseHeight')?.value) || 0;
    const totalH = modelH + baseH;
    const footW = Math.round(gridW * mmPerPx);
    const footH = Math.round(gridH * mmPerPx);

    document.getElementById('dimFootprint').textContent = `${footW} × ${footH} mm`;
    document.getElementById('dimHeight').textContent = `${totalH} mm (${modelH} terrain + ${baseH} base)`;

    const selectedRegion = window.appState.selectedRegion;
    const bbox = lastDemData.bbox || (selectedRegion ? {
        north: selectedRegion.north, south: selectedRegion.south,
        east: selectedRegion.east, west: selectedRegion.west
    } : null);

    if (bbox) {
        const midLat = (bbox.north + bbox.south) / 2;
        const latCos = Math.cos(midLat * Math.PI / 180);
        const realW_m = Math.abs(bbox.east - bbox.west) * window.GEO_M_PER_DEG_LON * latCos;
        const realH_m = Math.abs(bbox.north - bbox.south) * window.GEO_M_PER_DEG_LAT;
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
            fitRow.classList.add('fit-row-ok');
            fitRow.classList.remove('fit-row-warn');
        } else {
            fitText.textContent = '⚠ exceeds standard beds';
            fitRow.classList.add('fit-row-warn');
            fitRow.classList.remove('fit-row-ok');
        }
    } else {
        document.getElementById('dimRealArea').textContent = '—';
        document.getElementById('dimScale').textContent = '—';
        document.getElementById('dimBedFitText').textContent = '—';
    }

    panel.classList.remove('hidden');

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
    const realW_m = Math.abs(bbox.east - bbox.west) * window.GEO_M_PER_DEG_LON * latCos;
    const realH_m = Math.abs(bbox.north - bbox.south) * window.GEO_M_PER_DEG_LAT;

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
    // Keep legacy callers functional, but route through the dedicated ESA loader
    // so initial auto-load and manual "Load ESA Land Cover" use identical settings
    // (resolution, projection, rendering) and cannot race/overwrite each other.
    if (typeof window.loadEsaLandCover === 'function') {
        return await window.loadEsaLandCover();
    }

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
    const resolution = document.getElementById('waterResolution')?.value || '600';
    const dataset = document.getElementById('waterDataset')?.value || 'esa';
    const projection = document.getElementById('paramProjection')?.value || 'none';
    const clipNans = document.getElementById('paramClipNans')?.checked ? 'true' : 'false';

    const params = new URLSearchParams({
        north, south, east, west,
        dim: resolution,
        show_sat: true,
        dataset,
        projection,
        clip_valid_region: clipNans,
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
            if (dataset === 'esa' && typeof window.renderEsaLandCover === 'function') {
                // ESA values are categorical class IDs; render with class palette,
                // not a continuous viridis gradient.
                window.renderEsaLandCover({
                    esa_values: data.sat_values,
                    esa_values_b64: data.sat_values_b64,
                    esa_dimensions: data.sat_dimensions,
                });
            } else {
                const sat_h = data.sat_dimensions[0];
                const sat_w = data.sat_dimensions[1];
                const canvas = window.renderSatelliteCanvas?.(data.sat_values, sat_w, sat_h);
                canvas.classList.add('dem-canvas-responsive');
                document.getElementById('satelliteImage').innerHTML = '';
                document.getElementById('satelliteImage').appendChild(canvas);
            }
            window.appState.satEsaLoaded = true;
            window.emitStackUpdate();
        } else {
            window.appState.satEsaLoaded = false;
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
        document.getElementById('paramDim')?.value || 600
    );
    const satProj = window.getProjectionParams();
    const params = new URLSearchParams({
        north, south, east, west, dim,
        projection: satProj.projection,
        maintain_dimensions: satProj.maintainDimensions,
        clip_valid_region: satProj.clipValidRegion,
    });

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
                window.appState._satImgRawCanvas = raw;
                window.appState._satImgBbox = bbox;
                window.appState.satImgSourceCanvas = raw;
                resolve();
            };
            img.onerror = reject;
            img.src = `data:image/jpeg;base64,${data.image}`;
        });

        window.events?.emit(window.EV?.STACKED_UPDATE);
        window.showToast?.('Satellite imagery loaded', 'success');
    } catch (err) {
        if (err.name === 'AbortError' || signal.aborted) return;
        console.error('loadSatelliteRGBImage error:', err);
        window.showToast?.(`Satellite load failed: ${err.message}`, 'error');
    }
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
