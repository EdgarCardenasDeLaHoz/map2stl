/**
 * modules/dem-gridlines.js — DEM gridlines overlay and recolor/rescale helpers.
 *
 * Loaded as a plain <script> after dem-loader.js and before app.js.
 * All functions become globals.
 *
 * Functions:
 *   Gridlines:
 *     window.drawGridlinesOverlay(containerId?)         — reads window.appState.currentDemBbox
 *   DEM recolor / rescale:
 *     getFiniteMinMax(values)
 *     recolorDEM()                               — reads window.appState.lastDemData/currentDemBbox
 *     rescaleDEM(newVmin, newVmax)               — reads window.appState.lastDemData/currentDemBbox
 *     resetRescale()                             — reads window.appState.lastDemData
 *
 * Key external dependencies:
 *   window.renderDEMCanvas  — defined in app.js (writes closure lastDemData), exposed on window
 *   window.appState         — shared state proxy (currentDemBbox, lastDemData)
 *   window.drawColorbar()          — global from dem-loader.js
 *   window.drawHistogram()         — global from dem-loader.js
 *   window.enableZoomAndPan()      — global from dem-loader.js
 *   updateAxesOverlay()     — global from dem-loader.js
 *   updateStackedLayers()   — global from stacked-layers.js
 *   window.showToast()             — global from app.js file-top scope
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

    const canvas = container.querySelector(window.DEM_CANVAS_SELECTOR);
    if (!canvas) return;

    // The geographic graticule and the pixel grid are independent overlays.
    const geoGridOn = !!document.getElementById('showGridlines')?.checked;
    const pixelGridOn = !!document.getElementById('showPixelGrid')?.checked;
    if (!geoGridOn && !pixelGridOn) {
        const existing = container.querySelector('.dem-gridlines-overlay');
        if (existing) existing.remove();
        const dimsLabel = document.getElementById('pixelGridDimsLabel');
        if (dimsLabel) dimsLabel.classList.add('hidden');
        return;
    }

    const { north, south, east, west } = currentDemBbox;
    const latRange = north - south;
    const lonRange = east - west;

    const projection = document.getElementById('paramProjection')?.value || 'none';
    const toRad = d => d * Math.PI / 180;

    // --- Per-projection geo -> normalized-fraction mapping ------------------
    // These MUST match the server-side transforms in geo2stl/projections.py
    // (maintain_dimensions=True) so the graticule aligns with the reprojected
    // DEM raster and the curvature of the lines shows the true distortion.
    const mercY = l => Math.log(Math.tan(Math.PI / 4 + toRad(Math.max(-85, Math.min(85, l))) / 2));
    const mercN = mercY(Math.min(85, north));
    const mercS = mercY(Math.max(-85, south));
    const mercRange = (mercN - mercS) || 1;

    // cosine / equidistant share a factor: each row's width scales by
    // cos(lat)/mean(cos(lat over the column)), centred.
    const latSamplesForAvg = [];
    for (let k = 0; k <= 64; k++) latSamplesForAvg.push(Math.cos(toRad(north - (k / 64) * latRange)));
    const avgCos = latSamplesForAvg.reduce((a, b) => a + b, 0) / latSamplesForAvg.length || 1;

    // lambert equal-area: y = sin(lat)
    const lY = l => Math.sin(toRad(l));
    const lN = lY(north), lS = lY(south);
    const lRange = (lN - lS) || 1;

    // Generic cylindrical y = f(lat) for Miller / Gall (match geo2stl exactly).
    const clampLat = l => Math.max(-89.9, Math.min(89.9, l));
    const millerY = l => 1.25 * Math.log(Math.tan(Math.PI / 4 + 0.4 * toRad(clampLat(l))));
    const gallY = l => (1 + Math.SQRT2 / 2) * Math.tan(toRad(clampLat(l)) / 2);
    const milN = millerY(north), milRange = (milN - millerY(south)) || 1;
    const galN = gallY(north), galRange = (galN - gallY(south)) || 1;

    const centerLon = (east + west) / 2;

    // Returns {xFrac, yFrac} in [0,1] (or null if the point projects outside
    // the raster, e.g. sinusoidal wings). Mirrors the server exactly.
    function geoToFrac(lat, lon) {
        const linX = (lon - west) / lonRange;
        const linY = (north - lat) / latRange;
        switch (projection) {
            case 'mercator':
                return { xFrac: linX, yFrac: (mercN - mercY(lat)) / mercRange };
            case 'lambert':
                return { xFrac: linX, yFrac: (lN - lY(lat)) / lRange };
            case 'miller':
                return { xFrac: linX, yFrac: (milN - millerY(lat)) / milRange };
            case 'gall':
                return { xFrac: linX, yFrac: (galN - gallY(lat)) / galRange };
            case 'cosine':
            case 'equidistant': {
                const scale = Math.cos(toRad(lat)) / avgCos;
                const xFrac = 0.5 + (linX - 0.5) * scale;
                return { xFrac, yFrac: linY };
            }
            case 'sinusoidal': {
                const xFrac = 0.5 + ((lon - centerLon) / lonRange) * Math.cos(toRad(lat));
                return { xFrac, yFrac: linY };
            }
            default: // none
                return { xFrac: linX, yFrac: linY };
        }
    }

    let overlay = container.querySelector('.dem-gridlines-overlay');
    if (!overlay) {
        overlay = document.createElement('canvas');
        overlay.className = 'dem-gridlines-overlay';
        container.appendChild(overlay);
    }

    container.classList.add('pos-relative');

    const canvasRect = canvas.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const offsetLeft = canvasRect.left - containerRect.left;
    const offsetTop = canvasRect.top - containerRect.top;

    overlay.width = canvasRect.width;
    overlay.height = canvasRect.height;
    overlay.classList.add('overlay-abs');
    overlay.style.left = offsetLeft + 'px';
    overlay.style.top = offsetTop + 'px';
    overlay.style.width = canvasRect.width + 'px';
    overlay.style.height = canvasRect.height + 'px';

    const ctx = overlay.getContext('2d');
    // No clearRect needed — setting overlay.width above already clears the canvas.

    const gridCount = parseInt(document.getElementById('gridlineCount')?.value || '5');
    const W = overlay.width, H = overlay.height;
    const SEG = 48;  // polyline segments per graticule line (smooth curves)

    // Line colour is user-configurable and defaults to black.
    const lineColor = document.getElementById('gridLineColor')?.value || '#000000';
    // Grid mode: 'degrees' = lat/lon graticule; 'meters' = constant real-world
    // distance section grid (constant-km cells) so real-space distortion shows.
    const unitMode = document.getElementById('gridUnitMode')?.value || 'degrees';

    // A readable label colour that contrasts with the (possibly dark) lines.
    ctx.font = 'bold 12px sans-serif';
    ctx.fillStyle = lineColor;
    ctx.shadowColor = 'rgba(255, 255, 255, 0.85)';  // halo so labels read on dark terrain
    ctx.shadowBlur = 3;

    const px = f => f * W;
    const py = f => f * H;

    // Draw one graticule polyline by sampling geoToFrac along it. `pts` is an
    // array of {lat, lon}. Returns the first on-canvas point (for labels).
    function drawLine(pts, { bold = false } = {}) {
        ctx.beginPath();
        ctx.lineWidth = bold ? 2.5 : 1.25;
        ctx.strokeStyle = lineColor;
        ctx.globalAlpha = bold ? 1.0 : 0.8;
        ctx.setLineDash(bold ? [] : [6, 4]);
        let started = false, anchor = null;
        for (const p of pts) {
            const f = geoToFrac(p.lat, p.lon);
            if (f.xFrac == null || !isFinite(f.xFrac) || !isFinite(f.yFrac)) { started = false; continue; }
            const x = px(f.xFrac), y = py(f.yFrac);
            if (!started) { ctx.moveTo(x, y); started = true; if (!anchor) anchor = { x, y }; }
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1.0;
        return anchor;
    }

    // --- Build the list of meridian longitudes + parallel latitudes ---------
    // Degrees mode: evenly spaced by count. Meters mode: spaced by a constant
    // real-world distance (km), which makes the cells non-square in lat/lon
    // (real space), and then geoToFrac warps them per projection.
    const midLat = (north + south) / 2;
    const KM_PER_DEG_LAT = 110.574;
    const kmPerDegLon = 111.320 * Math.cos(toRad(midLat)) || 1e-6;

    let lonLines = [], latLines = [];
    let spacingLabel = '';
    if (unitMode === 'meters') {
        const spacingKm = parseFloat(document.getElementById('gridMetersSpacing')?.value || '5');
        const dLat = spacingKm / KM_PER_DEG_LAT;         // deg latitude per cell
        const dLon = spacingKm / kmPerDegLon;            // deg longitude per cell
        // Anchor the grid on the region centre so cells are symmetric.
        const cLon = (east + west) / 2, cLat = midLat;
        for (let lon = cLon; lon <= east + 1e-9; lon += dLon) lonLines.push(lon);
        for (let lon = cLon - dLon; lon >= west - 1e-9; lon -= dLon) lonLines.push(lon);
        for (let lat = cLat; lat <= north + 1e-9; lat += dLat) latLines.push(lat);
        for (let lat = cLat - dLat; lat >= south - 1e-9; lat -= dLat) latLines.push(lat);
        lonLines.sort((a, b) => a - b); latLines.sort((a, b) => b - a);
        // Guard against pathological tiny spacing on huge regions.
        if (lonLines.length > 200 || latLines.length > 200) {
            lonLines = lonLines.filter((_, i) => i % Math.ceil(lonLines.length / 100) === 0);
            latLines = latLines.filter((_, i) => i % Math.ceil(latLines.length / 100) === 0);
        }
        spacingLabel = `${spacingKm} km cells`;
    } else {
        for (let i = 0; i <= gridCount; i++) lonLines.push(west + (i / gridCount) * lonRange);
        for (let i = 0; i <= gridCount; i++) latLines.push(north - (i / gridCount) * latRange);
    }

    const eqLat = (north >= 0 && south <= 0) ? 0 : null;      // equator in view?
    const pmLon = (east >= 0 && west <= 0) ? 0 : null;        // prime meridian in view?

    // ── Geographic graticule (degrees / meters) ─────────────────────────────
    if (geoGridOn) {
    // Meridians (constant lon): sample down the latitude span so the line
    // curves/tilts exactly as the projection bends it.
    for (const lon of lonLines) {
        const pts = [];
        for (let s = 0; s <= SEG; s++) pts.push({ lat: north - (s / SEG) * latRange, lon });
        const isBold = pmLon != null && Math.abs(lon - pmLon) < lonRange / 200;
        const a = drawLine(pts, { bold: isBold });
        if (a && unitMode === 'degrees') {
            const label = lon.toFixed(2) + '°';
            const tw = ctx.measureText(label).width;
            ctx.fillText(label, Math.max(2, Math.min(a.x - tw / 2, W - tw - 2)), H - 4);
        }
    }

    // Parallels (constant lat): sample across the longitude span.
    for (const lat of latLines) {
        const pts = [];
        for (let s = 0; s <= SEG; s++) pts.push({ lat, lon: west + (s / SEG) * lonRange });
        const isBold = eqLat != null && Math.abs(lat - eqLat) < latRange / 200;
        const a = drawLine(pts, { bold: isBold });
        if (a && unitMode === 'degrees') ctx.fillText(lat.toFixed(2) + '°', Math.max(2, Math.min(a.x + 4, W - 46)), Math.max(12, a.y - 3));
    }

    // Outline the reprojected data footprint (the warped border) so the overall
    // shape distortion reads at a glance.
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.beginPath();
    let started = false;
    const border = [];
    for (let s = 0; s <= SEG; s++) border.push({ lat: north, lon: west + (s / SEG) * lonRange });
    for (let s = 0; s <= SEG; s++) border.push({ lat: north - (s / SEG) * latRange, lon: east });
    for (let s = 0; s <= SEG; s++) border.push({ lat: south, lon: east - (s / SEG) * lonRange });
    for (let s = 0; s <= SEG; s++) border.push({ lat: south + (s / SEG) * latRange, lon: west });
    for (const p of border) {
        const f = geoToFrac(p.lat, p.lon);
        if (f.xFrac == null || !isFinite(f.xFrac) || !isFinite(f.yFrac)) { started = false; continue; }
        const x = px(f.xFrac), y = py(f.yFrac);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();

    // Projection name badge (top-left) so the active transform is unmistakable.
    const projLabel = {
        none: 'Plate Carrée (no projection)', cosine: 'Cosine correction',
        mercator: 'Web Mercator', equidistant: 'Equidistant Cylindrical',
        lambert: 'Lambert Equal-Area', sinusoidal: 'Sinusoidal',
        miller: 'Miller Cylindrical', gall: 'Gall Stereographic',
    }[projection] || projection;
    const badge = spacingLabel ? `${projLabel}  ·  ${spacingLabel}` : projLabel;
    ctx.font = 'bold 13px sans-serif';
    const bw = ctx.measureText(badge).width + 14;
    ctx.shadowBlur = 0;
    ctx.fillStyle = 'rgba(0, 0, 0, 0.55)';
    ctx.fillRect(6, 6, bw, 22);
    ctx.fillStyle = '#ffec78';
    ctx.fillText(badge, 13, 22);
    }  // end geographic graticule

    // ── Pixel grid (independent, DEM pixel space, default red) ──────────────
    if (pixelGridOn) {
        _drawPixelGrid(ctx, W, H, canvas);
    } else {
        const dimsLabel = document.getElementById('pixelGridDimsLabel');
        if (dimsLabel) dimsLabel.classList.add('hidden');
    }
}

/**
 * Draw a grid in DEM *pixel* space (independent of the geographic graticule).
 * Lines every `pixelGridSpacing` DEM pixels, mapped onto the display canvas.
 * Colour defaults to red. Also writes the raster's pixel dimensions to the
 * #pixelGridDimsLabel readout and labels the on-canvas grid.
 */
function _drawPixelGrid(ctx, W, H, canvas) {
    const dem = window.appState?.lastDemData;
    // DEM raster pixel dimensions (fall back to the display canvas if absent).
    const demW = dem?.width || canvas.width || 0;
    const demH = dem?.height || canvas.height || 0;
    if (!demW || !demH) return;

    const color = document.getElementById('pixelGridColor')?.value || '#ff0000';
    let spacing = parseInt(document.getElementById('pixelGridSpacing')?.value, 10);
    if (!Number.isFinite(spacing) || spacing < 1) spacing = 1000;

    // Map DEM-pixel coordinates onto the (possibly rescaled) display canvas.
    const sx = W / demW, sy = H / demH;

    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.25;
    ctx.setLineDash([]);
    ctx.shadowColor = 'rgba(0,0,0,0.6)';
    ctx.shadowBlur = 2;
    ctx.font = 'bold 11px sans-serif';

    // Vertical lines every `spacing` px in the X (column) direction.
    for (let cx = 0; cx <= demW; cx += spacing) {
        const x = cx * sx;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, H);
        ctx.stroke();
        ctx.fillText(`${cx}px`, Math.min(x + 3, W - 32), 12);
    }
    // Always draw the far edge so the full width reads.
    if (demW % spacing !== 0) {
        ctx.beginPath(); ctx.moveTo(W, 0); ctx.lineTo(W, H); ctx.stroke();
    }

    // Horizontal lines every `spacing` px in the Y (row) direction.
    for (let cy = 0; cy <= demH; cy += spacing) {
        const y = cy * sy;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
        ctx.fillText(`${cy}px`, 3, Math.min(y + 12, H - 3));
    }
    if (demH % spacing !== 0) {
        ctx.beginPath(); ctx.moveTo(0, H); ctx.lineTo(W, H); ctx.stroke();
    }
    ctx.restore();

    // Dimensions readout in the sidebar.
    const dimsLabel = document.getElementById('pixelGridDimsLabel');
    if (dimsLabel) {
        dimsLabel.textContent = `Pixel grid: ${demW} × ${demH} px  (every ${spacing} px)`;
        dimsLabel.classList.remove('hidden');
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

    const canvas = window.renderDEMCanvas(values, width, height, colormap, vmin, vmax);
    const container = document.getElementById('demImage');
    container.querySelector('canvas')?._zoomPanCleanup?.();
    container.innerHTML = '';
    container.appendChild(canvas);
    canvas.classList.add('canvas-responsive');

    window.drawColorbar(vmin, vmax, colormap);
    window.drawHistogram(values);
    window.enableZoomAndPan(canvas);

    requestAnimationFrame(() => {
        window.drawGridlinesOverlay('demImage');
        window.drawGridlinesOverlay('inlineLayersCanvas');
        window.events?.emit(window.EV?.STACKED_UPDATE);
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
        window.showToast('No DEM data loaded', 'warning');
        return;
    }

    const colormap = document.getElementById('demColormap').value;
    const { values, width, height } = lastDemData;

    lastDemData.vmin = newVmin;
    lastDemData.vmax = newVmax;

    const canvas = window.renderDEMCanvas(values, width, height, colormap, newVmin, newVmax);
    const container = document.getElementById('demImage');
    container.querySelector('canvas')?._zoomPanCleanup?.();
    container.innerHTML = '';
    container.appendChild(canvas);
    canvas.classList.add('canvas-responsive');

    window.drawColorbar(newVmin, newVmax, colormap);
    window.drawHistogram(values);
    window.enableZoomAndPan(canvas);
    window.emitStackUpdate();

    window.showToast(`Rescaled to ${newVmin.toFixed(0)}m - ${newVmax.toFixed(0)}m`, 'success');
}

/**
 * Reset the DEM display range to the auto-computed min/max from the data.
 */
function resetRescale() {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
        window.showToast('No DEM data loaded', 'warning');
        return;
    }

    const { min: calcMin, max: calcMax } = getFiniteMinMax(lastDemData.values);

    document.getElementById('rescaleMin').value = Math.floor(calcMin);
    document.getElementById('rescaleMax').value = Math.ceil(calcMax);

    rescaleDEM(calcMin, calcMax);
    window.showToast('Reset to auto range', 'info');
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window (ES module — functions are not auto-global)
// ─────────────────────────────────────────────────────────────────────────────
window.drawGridlinesOverlay = drawGridlinesOverlay;
window.recolorDEM = recolorDEM;
window.rescaleDEM = rescaleDEM;
window.resetRescale = resetRescale;
