/**
 * modules/dem-gridlines.js — DEM gridlines overlay and recolor/rescale helpers.
 *
 * Loaded as a plain <script> after dem-loader.js and before app.js.
 * All functions become globals.
 *
 * Functions:
 *   Gridlines:
 *     drawGridlinesOverlay(containerId?)         — reads window.appState.currentDemBbox
 *   DEM recolor / rescale:
 *     getFiniteMinMax(values)
 *     recolorDEM()                               — reads window.appState.lastDemData/currentDemBbox
 *     rescaleDEM(newVmin, newVmax)               — reads window.appState.lastDemData/currentDemBbox
 *     resetRescale()                             — reads window.appState.lastDemData
 *
 * Key external dependencies:
 *   window.renderDEMCanvas  — defined in app.js (writes closure lastDemData), exposed on window
 *   window.appState         — shared state proxy (currentDemBbox, lastDemData)
 *   applyProjection()       — global from dem-loader.js
 *   drawColorbar()          — global from dem-loader.js
 *   drawHistogram()         — global from dem-loader.js
 *   enableZoomAndPan()      — global from dem-loader.js
 *   updateAxesOverlay()     — global from dem-loader.js
 *   updateStackedLayers()   — global from stacked-layers.js
 *   showToast()             — global from app.js file-top scope
 */

// ─────────────────────────────────────────────────────────────────────────────
// Gridlines overlay
// Reads window.appState.currentDemBbox (set by window.loadDEM in app.js).
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Draw lat/lon gridlines with axis tick labels on a DEM canvas overlay.
 * Respects the active projection. Removes the overlay if the #showGridlines
 * checkbox is unchecked.
 * @param {string} [containerId='demImage']
 */
function drawGridlinesOverlay(containerId = 'demImage') {
    const currentDemBbox = window.appState.currentDemBbox;
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

    const { north, south, east, west } = currentDemBbox;
    const latRange = north - south;
    const lonRange = east - west;

    const projection = document.getElementById('paramProjection')?.value || 'none';
    const toRad = d => d * Math.PI / 180;
    const mercY = l => Math.log(Math.tan(Math.PI / 4 + toRad(Math.max(-85, Math.min(85, l))) / 2));
    const mercN = mercY(Math.min(85, north));
    const mercS = mercY(Math.max(-85, south));
    const mercRange = mercN - mercS;

    function geoToFrac(lat, lon) {
        let xFrac = (lon - west) / lonRange;
        let yFrac;
        switch (projection) {
            case 'mercator': {
                const my = mercY(lat);
                yFrac = (mercN - my) / mercRange;
                break;
            }
            case 'sinusoidal': {
                yFrac = (north - lat) / latRange;
                xFrac = null;
                break;
            }
            default:
                yFrac = (north - lat) / latRange;
        }
        return { xFrac, yFrac };
    }

    let overlay = container.querySelector('.dem-gridlines-overlay');
    if (!overlay) {
        overlay = document.createElement('canvas');
        overlay.className = 'dem-gridlines-overlay';
        container.appendChild(overlay);
    }

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

    const ctx = overlay.getContext('2d');
    // No clearRect needed — setting overlay.width above already clears the canvas.

    const gridCount = parseInt(document.getElementById('gridlineCount')?.value || '5');
    const W = overlay.width, H = overlay.height;

    const midLat = ((north + south) / 2) * Math.PI / 180;
    let xOffset = 0, contentW = W;
    if (projection === 'cosine' || projection === 'lambert') {
        contentW = Math.max(1, Math.round(W * Math.cos(midLat)));
        xOffset = Math.floor((W - contentW) / 2);
    }

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
    ctx.lineWidth = 1;
    ctx.font = '11px sans-serif';
    ctx.fillStyle = '#fff';
    ctx.shadowColor = 'rgba(0, 0, 0, 0.8)';
    ctx.shadowBlur = 2;

    if (projection !== 'sinusoidal') {
        for (let i = 0; i <= gridCount; i++) {
            const lon = west + (i / gridCount) * lonRange;
            const xFrac = i / gridCount;
            const x = xOffset + xFrac * contentW;
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.moveTo(x, 0);
            ctx.lineTo(x, H);
            ctx.stroke();
            ctx.setLineDash([]);
            const label = lon.toFixed(2) + '°';
            const textWidth = ctx.measureText(label).width;
            const labelX = Math.max(textWidth / 2, Math.min(x, W - textWidth / 2));
            ctx.fillText(label, labelX - textWidth / 2, H - 5);
        }
    } else {
        for (let i = 0; i <= gridCount; i++) {
            const lon = west + (i / gridCount) * lonRange;
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            let first = true;
            for (let row = 0; row <= H; row++) {
                const lat = north - (row / H) * latRange;
                const scale = Math.cos(toRad(lat));
                const xFrac = 0.5 + (lon - (west + lonRange / 2)) / lonRange * scale;
                const x = xFrac * W;
                const y = row;
                if (first) { ctx.moveTo(x, y); first = false; }
                else ctx.lineTo(x, y);
            }
            ctx.stroke();
            ctx.setLineDash([]);
        }
    }

    for (let i = 0; i <= gridCount; i++) {
        const lat = north - (i / gridCount) * latRange;
        const { yFrac } = geoToFrac(lat, west);
        if (yFrac < 0 || yFrac > 1) continue;
        const y = yFrac * H;

        let lineX0, lineX1;
        if (projection === 'sinusoidal') {
            const cosLat = Math.cos(toRad(lat));
            lineX0 = W * (0.5 - 0.5 * cosLat);
            lineX1 = W * (0.5 + 0.5 * cosLat);
        } else {
            lineX0 = xOffset;
            lineX1 = xOffset + contentW;
        }

        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.moveTo(lineX0, y);
        ctx.lineTo(lineX1, y);
        ctx.stroke();
        ctx.setLineDash([]);

        const label = lat.toFixed(2) + '°';
        ctx.fillText(label, lineX0 + 5, y + 4);
    }

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.6)';
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    if (projection === 'sinusoidal') {
        ctx.beginPath();
        for (let row = 0; row <= H; row++) {
            const lat = north - (row / H) * latRange;
            const x = W * (0.5 - 0.5 * Math.cos(toRad(lat)));
            if (row === 0) ctx.moveTo(x, 0); else ctx.lineTo(x, row);
        }
        for (let row = H; row >= 0; row--) {
            const lat = north - (row / H) * latRange;
            const x = W * (0.5 + 0.5 * Math.cos(toRad(lat)));
            ctx.lineTo(x, row);
        }
        ctx.closePath();
        ctx.stroke();
    } else {
        ctx.strokeRect(xOffset, 0, contentW, H);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// DEM recolor / rescale
// These read window.appState.lastDemData and window.appState.currentDemBbox,
// which are kept in sync by app.js. They call window.renderDEMCanvas (exposed
// on window by app.js after its definition inside DOMContentLoaded).
// ─────────────────────────────────────────────────────────────────────────────

/** Return {min, max} of all finite values in an array. */
function getFiniteMinMax(values) {
    let min = Infinity, max = -Infinity;
    for (const v of values) {
        if (isFinite(v)) {
            if (v < min) min = v;
            if (v > max) max = v;
        }
    }
    return { min, max };
}

/**
 * Re-render the DEM canvas using the current colormap selection.
 * If #autoRescale is checked, recalculates vmin/vmax from the data first.
 */
function recolorDEM() {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
        console.log('No DEM data cached, cannot recolor');
        return;
    }
    const colormap = document.getElementById('demColormap').value;
    if (document.getElementById('autoRescale')?.checked) {
        const { min: calcMin, max: calcMax } = getFiniteMinMax(lastDemData.values);
        if (isFinite(calcMin) && isFinite(calcMax)) {
            lastDemData.vmin = calcMin;
            lastDemData.vmax = calcMax;
            document.getElementById('rescaleMin').value = Math.floor(calcMin);
            document.getElementById('rescaleMax').value = Math.ceil(calcMax);
        }
    }
    const { values, width, height, vmin, vmax } = lastDemData;

    const rawCanvas = window.renderDEMCanvas(values, width, height, colormap, vmin, vmax);
    const canvas = applyProjection(rawCanvas, window.appState.currentDemBbox);
    const container = document.getElementById('demImage');
    container.querySelector('canvas')?._zoomPanCleanup?.();
    container.innerHTML = '';
    container.appendChild(canvas);
    canvas.style.width = '100%';
    canvas.style.height = 'auto';

    drawColorbar(vmin, vmax, colormap);
    drawHistogram(values);
    enableZoomAndPan(canvas);

    requestAnimationFrame(() => {
        drawGridlinesOverlay('demImage');
        drawGridlinesOverlay('inlineLayersCanvas');
        updateStackedLayers();
    });
}

/**
 * Rescale DEM display range client-side (no server request).
 * Updates lastDemData.vmin/vmax and redraws canvas, colorbar, and histogram.
 * @param {number} newVmin
 * @param {number} newVmax
 */
function rescaleDEM(newVmin, newVmax) {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
        showToast('No DEM data loaded', 'warning');
        return;
    }

    const colormap = document.getElementById('demColormap').value;
    const { values, width, height } = lastDemData;

    lastDemData.vmin = newVmin;
    lastDemData.vmax = newVmax;

    const rawCanvas = window.renderDEMCanvas(values, width, height, colormap, newVmin, newVmax);
    const canvas = applyProjection(rawCanvas, window.appState.currentDemBbox);
    const container = document.getElementById('demImage');
    container.querySelector('canvas')?._zoomPanCleanup?.();
    container.innerHTML = '';
    container.appendChild(canvas);
    canvas.style.width = '100%';
    canvas.style.height = 'auto';

    drawColorbar(newVmin, newVmax, colormap);
    drawHistogram(values);
    enableZoomAndPan(canvas);
    requestAnimationFrame(() => updateStackedLayers());

    showToast(`Rescaled to ${newVmin.toFixed(0)}m - ${newVmax.toFixed(0)}m`, 'success');
}

/**
 * Reset the DEM display range to the auto-computed min/max from the data.
 */
function resetRescale() {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
        showToast('No DEM data loaded', 'warning');
        return;
    }

    const { min: calcMin, max: calcMax } = getFiniteMinMax(lastDemData.values);

    document.getElementById('rescaleMin').value = Math.floor(calcMin);
    document.getElementById('rescaleMax').value = Math.ceil(calcMax);

    rescaleDEM(calcMin, calcMax);
    showToast('Reset to auto range', 'info');
}
