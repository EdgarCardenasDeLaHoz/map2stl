/**
 * modules/dem-loader.js — DEM rendering, projection, zoom/pan helpers.
 *
 * Loaded as a plain <script> before app.js. All functions become globals.
 *
 * Functions:
 *   Colour math:
 *     hslToRgb(h, s, l)
 *     mapElevationToColor(t, cmap)
 *   Canvas renderers:
 *     renderSatelliteCanvas(values, width, height)
 *     updateAxesOverlay(north, south, east, west)
 *     drawColorbar(min, max, colormap)
 *     drawHistogram(values)
 *   Projection:
 *     applyProjection(srcCanvas, bbox)
 *   Zoom / pan:
 *     enableZoomAndPan(canvas)
 *
 * Key external dependencies:
 *   window.appState         — shared state proxy (currentDemBbox, lastDemData)
 *   updateStackedLayers()   — global from stacked-layers.js
 */

// ─────────────────────────────────────────────────────────────────────────────
// Colour math
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Convert HSL colour values to an RGB triple.
 * All parameters and return values are normalised to [0, 1].
 * @param {number} h - Hue in [0, 1]
 * @param {number} s - Saturation in [0, 1]
 * @param {number} l - Lightness in [0, 1]
 * @returns {[number, number, number]} RGB values each in [0, 1]
 */
function hslToRgb(h, s, l) {
    let r, g, b;
    if (s === 0) {
        r = g = b = l; // achromatic
    } else {
        const hue2rgb = (p, q, t) => {
            if (t < 0) t += 1;
            if (t > 1) t -= 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        };
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        r = hue2rgb(p, q, h + 1 / 3);
        g = hue2rgb(p, q, h);
        b = hue2rgb(p, q, h - 1 / 3);
    }
    return [r, g, b];
}

/**
 * Map a normalised elevation value (0–1) to an RGB triple using a named colormap.
 * Supports: 'jet', 'rainbow', 'viridis', 'hot', 'gray', and the default terrain scheme.
 * @param {number} t - Normalised elevation in [0, 1]
 * @param {string} cmap - Colormap name
 * @returns {[number, number, number]} RGB values each in [0, 1]
 */
function mapElevationToColor(t, cmap) {
    t = Math.max(0, Math.min(1, t));
    // alias common misspelling
    if (cmap === 'raindow') cmap = 'rainbow';
    if (cmap === 'jet') {
        const clip = x => Math.max(0, Math.min(1, x));
        const r = clip(1.5 - Math.abs(4 * t - 3));
        const g = clip(1.5 - Math.abs(4 * t - 2));
        const b = clip(1.5 - Math.abs(4 * t - 1));
        return [r, g, b];
    }
    if (cmap === 'rainbow') {
        const h = 0.66 * (1 - t);
        return hslToRgb(h, 1, 0.5);
    }
    if (cmap === 'viridis') {
        const h = 0.7 - 0.7 * t;
        const s = 0.9;
        const l = 0.5;
        return hslToRgb(h, s, l);
    } else if (cmap === 'hot') {
        const r = Math.min(1, 3 * t);
        const g = Math.min(1, Math.max(0, 3 * t - 1));
        const b = Math.min(1, Math.max(0, 3 * t - 2));
        return [r, g, b];
    } else if (cmap === 'gray') {
        return [t, t, t];
    }
    // default: terrain-like (green → brown → white)
    if (t < 0.4) {
        const tt = t / 0.4;
        return [0.0 * (1 - tt) + 0.4 * tt, 0.3 * (1 - tt) + 0.25 * tt + 0.45 * tt, 0.0 + 0.0 * tt];
    } else if (t < 0.8) {
        const tt = (t - 0.4) / 0.4;
        return [0.4 * (1 - tt) + 0.55 * tt, 0.6 * (1 - tt) + 0.45 * tt, 0.2 * (1 - tt) + 0.15 * tt];
    } else {
        const tt = (t - 0.8) / 0.2;
        return [0.55 * (1 - tt) + 0.9 * tt, 0.45 * (1 - tt) + 0.9 * tt, 0.15 * (1 - tt) + 0.9 * tt];
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Canvas renderers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Render a satellite or land-use pixel array to a canvas using the viridis colormap.
 * Uses a pre-computed 256-entry LUT for performance.
 * @param {number[]} values - Flat array of pixel intensity values (row-major)
 * @param {number} width - Canvas width in pixels
 * @param {number} height - Canvas height in pixels
 * @returns {HTMLCanvasElement} The rendered canvas element
 */
function renderSatelliteCanvas(values, width, height) {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, width, height);
    const img = ctx.createImageData(width, height);
    const data = img.data;
    const flat = Array.isArray(values) ? values : [];
    const len = flat.length;

    let vmin = Infinity, vmax = -Infinity;
    for (let i = 0; i < len; i++) {
        const v = flat[i];
        if (Number.isFinite(v)) {
            if (v < vmin) vmin = v;
            if (v > vmax) vmax = v;
        }
    }
    if (vmin === Infinity) vmin = 0;
    if (vmax === -Infinity) vmax = 1;

    const range = (vmax - vmin) || 1;
    const invRange = 1 / range;

    const colorLUT = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
        const t = i / 255;
        const [r, g, b] = mapElevationToColor(t, 'viridis');
        colorLUT[i * 3]     = Math.round(r * 255);
        colorLUT[i * 3 + 1] = Math.round(g * 255);
        colorLUT[i * 3 + 2] = Math.round(b * 255);
    }

    const total = width * height;
    for (let i = 0; i < total; i++) {
        const v = (i < len && Number.isFinite(flat[i])) ? flat[i] : vmin;
        const t = (v - vmin) * invRange;
        const tClamped = t < 0 ? 0 : (t > 1 ? 1 : t);
        const lutIdx = Math.round(tClamped * 255) * 3;
        const idx = i << 2;
        data[idx]     = colorLUT[lutIdx];
        data[idx + 1] = colorLUT[lutIdx + 1];
        data[idx + 2] = colorLUT[lutIdx + 2];
        data[idx + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    canvas.style.maxWidth = '100%';
    canvas.style.height = 'auto';
    return canvas;
}

// ─────────────────────────────────────────────────────────────────────────────
// DEM overlay helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Draw N/S/E/W coordinate labels on the axes overlay element inside `#demImage`.
 * Creates the overlay element if it does not yet exist.
 * Accepts either four separate arguments or a single bounding-box object with
 * `north`/`south`/`east`/`west` properties.
 * @param {number|Object} north - Northern latitude, or a bbox object
 * @param {number} [south] - Southern latitude
 * @param {number} [east] - Eastern longitude
 * @param {number} [west] - Western longitude
 */
function updateAxesOverlay(north, south, east, west) {
    let N, S, E, W;
    if (north && typeof north === 'object') {
        const b = north;
        N = b.north ?? b.N ?? b.n ?? null;
        S = b.south ?? b.S ?? b.s ?? null;
        E = b.east  ?? b.E ?? b.e ?? null;
        W = b.west  ?? b.W ?? b.w ?? null;
    } else {
        N = north; S = south; E = east; W = west;
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
    overlay.querySelector('.top').textContent    = `N: ${fmt(N)}`;
    overlay.querySelector('.bottom').textContent = `S: ${fmt(S)}`;
    overlay.querySelector('.left').textContent   = `W: ${fmt(W)}`;
    overlay.querySelector('.right').textContent  = `E: ${fmt(E)}`;
}

/**
 * Render a compact colour-gradient bar into the `#colorbar` element.
 * The bar is 256 × 18 px and covers the full colormap range from `min` to `max`.
 * @param {number} min - Minimum elevation value (metres)
 * @param {number} max - Maximum elevation value (metres)
 * @param {string} colormap - Colormap name used for gradient colours
 */
function drawColorbar(min, max, colormap) {
    const bar = document.getElementById('colorbar');
    if (!bar) return;
    bar.innerHTML = '';
    const canvas = document.createElement('canvas');
    canvas.width  = Math.max(64, bar.clientWidth || 256);
    canvas.height = 18;
    const ctx = canvas.getContext('2d');
    const img = ctx.createImageData(256, 18);
    for (let x = 0; x < 256; x++) {
        const t = x / 255;
        const [r, g, b] = mapElevationToColor(t, colormap);
        for (let y = 0; y < 18; y++) {
            const idx = (y * 256 + x) * 4;
            img.data[idx]     = Math.round(r * 255);
            img.data[idx + 1] = Math.round(g * 255);
            img.data[idx + 2] = Math.round(b * 255);
            img.data[idx + 3] = 255;
        }
    }
    ctx.putImageData(img, 0, 0);
    canvas.style.width  = '100%';
    canvas.style.height = '18px';
    bar.title = `Colorbar: ${Math.round(min)} m (left) → ${Math.round(max)} m (right) — ${colormap}`;
    bar.appendChild(canvas);
}

/**
 * Render an elevation histogram with a cumulative distribution curve into `#histogram`.
 * Bars are coloured using the currently selected colormap. A red dashed line marks
 * sea level when the data range spans negative values.
 * Also triggers `updateStackedLayers()` after rendering via `requestAnimationFrame`.
 * @param {number[]} values - Flat array of elevation values (may contain NaN/Infinity)
 */
function drawHistogram(values) {
    const container = document.getElementById('histogram');
    if (!container) return;
    container.innerHTML = '';

    const canvas = document.createElement('canvas');
    canvas.width  = Math.max(100, container.clientWidth || 280);
    canvas.height = 200;
    const ctx = canvas.getContext('2d');
    const colormap = document.getElementById('demColormap').value;

    const valid = values.filter(v => Number.isFinite(v));
    if (valid.length === 0) {
        ctx.fillStyle = '#888';
        ctx.fillText('No data', 100, 60);
        container.appendChild(canvas);
        return;
    }

    const min = valid.reduce((a, b) => a < b ? a : b, valid[0]);
    const max = valid.reduce((a, b) => a > b ? a : b, valid[0]);
    const range = (max - min) || 1;
    const numBins = 40;
    const bins = new Array(numBins).fill(0);

    valid.forEach(v => {
        const idx = Math.min(numBins - 1, Math.floor(((v - min) / range) * numBins));
        bins[idx]++;
    });

    const maxBin = Math.max(...bins);
    const barWidth = canvas.width / numBins;
    const histTop = 8;
    const histHeight = 70;
    const cumulTop = histTop + histHeight + 12;
    const cumulHeight = 80;

    // Background
    ctx.fillStyle = '#252525';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Grid lines for main histogram
    ctx.strokeStyle = 'rgba(100, 100, 100, 0.3)';
    ctx.lineWidth = 1;
    for (let i = 1; i <= 4; i++) {
        const y = histTop + (histHeight / 5) * i;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
    }
    for (let i = 1; i <= 4; i++) {
        const x = (canvas.width / 5) * i;
        ctx.beginPath(); ctx.moveTo(x, histTop); ctx.lineTo(x, histTop + histHeight); ctx.stroke();
    }

    // Bars coloured by colormap
    bins.forEach((count, i) => {
        const t = i / (numBins - 1);
        const [r, g, b] = mapElevationToColor(t, colormap);
        ctx.fillStyle = `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;
        const barHeight = (count / maxBin) * histHeight;
        ctx.fillRect(i * barWidth, histTop + histHeight - barHeight, barWidth - 1, barHeight);
    });

    // Cumulative distribution
    const cumulative = [];
    let sum = 0;
    bins.forEach(count => { sum += count; cumulative.push(sum); });
    const totalCount = sum;

    ctx.fillStyle = 'rgba(80, 160, 255, 0.3)';
    ctx.beginPath();
    ctx.moveTo(0, cumulTop + cumulHeight);
    cumulative.forEach((csum, i) => {
        const x = (i + 0.5) * barWidth;
        const y = cumulTop + cumulHeight - (csum / totalCount) * cumulHeight;
        ctx.lineTo(x, y);
    });
    ctx.lineTo(canvas.width, cumulTop);
    ctx.lineTo(canvas.width, cumulTop + cumulHeight);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = '#50a0ff';
    ctx.lineWidth = 2;
    ctx.beginPath();
    cumulative.forEach((csum, i) => {
        const x = (i + 0.5) * barWidth;
        const y = cumulTop + cumulHeight - (csum / totalCount) * cumulHeight;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = '#888';
    ctx.font = '9px sans-serif';
    ctx.fillText('0%',         2,                cumulTop + cumulHeight - 2);
    ctx.fillText('100%',       2,                cumulTop + 8);
    ctx.fillText('Cumulative', canvas.width / 2 - 25, cumulTop + cumulHeight + 10);

    // Sea-level zero line
    if (min < 0 && max > 0) {
        const zeroX = ((0 - min) / range) * canvas.width;
        ctx.strokeStyle = '#ff4444';
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 2]);
        ctx.beginPath(); ctx.moveTo(zeroX, histTop); ctx.lineTo(zeroX, histTop + histHeight); ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = '#ff4444';
        ctx.font = 'bold 10px sans-serif';
        ctx.fillText('0', zeroX - 3, histTop - 2);
    }

    // Axis labels
    ctx.fillStyle = '#aaa';
    ctx.font = '10px sans-serif';
    ctx.fillText(min.toFixed(0) + 'm', 2,                   histTop + histHeight + 12);
    ctx.fillText(max.toFixed(0) + 'm', canvas.width - 35,   histTop + histHeight + 12);

    canvas.style.width  = '100%';
    canvas.style.height = 'auto';
    container.appendChild(canvas);

    // Trigger stacked layers update now that DEM data is available
    requestAnimationFrame(() => window.events?.emit(window.EV?.STACKED_UPDATE));
}

// ─────────────────────────────────────────────────────────────────────────────
// Canvas projection
// Extracted from app.js. Reads the #paramProjection select; pure canvas math.
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Apply a client-side map projection to a rendered canvas.
 * Returns a new (or the same) canvas transformed for the chosen projection.
 * @param {HTMLCanvasElement} srcCanvas
 * @param {{north,south,east,west}} bbox
 * @returns {HTMLCanvasElement}
 */
function applyProjection(srcCanvas, bbox) {
    const projection = document.getElementById('paramProjection')?.value || 'none';
    if (!projection || projection === 'none') return srcCanvas;
    if (!bbox) return srcCanvas;

    const W = srcCanvas.width, H = srcCanvas.height;
    const { north, south, east, west } = bbox;
    const latRange = north - south;
    const lonRange = east - west;
    if (!latRange || !lonRange) return srcCanvas;
    const midLat = ((north + south) / 2) * Math.PI / 180;

    if (projection === 'cosine') {
        const newW = Math.max(1, Math.round(W * Math.cos(midLat)));
        const dst = document.createElement('canvas');
        dst.width = W; dst.height = H;
        const offsetX = Math.floor((W - newW) / 2);
        dst.getContext('2d').drawImage(srcCanvas, 0, 0, W, H, offsetX, 0, newW, H);
        return dst;
    }

    if (projection === 'mercator') {
        const toRad = d => d * Math.PI / 180;
        const mercY = l => Math.log(Math.tan(Math.PI / 4 + toRad(l) / 2));
        const yN = mercY(Math.min(85, north)), yS = mercY(Math.max(-85, south));
        const yRange = yN - yS;
        if (Math.abs(yRange) < 1e-10) return srcCanvas;
        const srcCtx = srcCanvas.getContext('2d');
        const srcImg = srcCtx.getImageData(0, 0, W, H);
        const dst = document.createElement('canvas');
        dst.width = W; dst.height = H;
        const dstCtx = dst.getContext('2d');
        const dstImg = dstCtx.createImageData(W, H);
        for (let dstY = 0; dstY < H; dstY++) {
            const t = dstY / (H - 1);
            const mv = yN - t * yRange;
            const lat = (2 * Math.atan(Math.exp(mv)) - Math.PI / 2) * 180 / Math.PI;
            const srcY = Math.round((north - lat) / latRange * (H - 1));
            if (srcY < 0 || srcY >= H) continue;
            const dstBase = dstY * W * 4, srcBase = srcY * W * 4;
            dstImg.data.set(srcImg.data.subarray(srcBase, srcBase + W * 4), dstBase);
        }
        dstCtx.putImageData(dstImg, 0, 0);
        return dst;
    }

    if (projection === 'lambert') {
        const cosLat = Math.cos(midLat);
        const newW = Math.max(1, Math.round(W * cosLat));
        const newH = Math.max(1, Math.round(H / cosLat));
        const dst = document.createElement('canvas');
        dst.width = W; dst.height = newH;
        const offsetX = Math.floor((W - newW) / 2);
        dst.getContext('2d').drawImage(srcCanvas, 0, 0, W, H, offsetX, 0, newW, newH);
        return dst;
    }

    if (projection === 'sinusoidal') {
        const srcCtx = srcCanvas.getContext('2d');
        const srcImg = srcCtx.getImageData(0, 0, W, H);
        const dst = document.createElement('canvas');
        dst.width = W; dst.height = H;
        const dstCtx = dst.getContext('2d');
        const dstImg = dstCtx.createImageData(W, H);
        for (let y = 0; y < H; y++) {
            const lat = north - (y / (H - 1)) * latRange;
            const scale = Math.cos(lat * Math.PI / 180);
            const rowW = Math.max(1, Math.round(W * scale));
            const offset = Math.round((W - rowW) / 2);
            const srcBase = y * W * 4;
            const dstBase = y * W * 4;
            for (let dstX = offset; dstX < offset + rowW && dstX < W; dstX++) {
                const srcX = Math.round((dstX - offset) / rowW * (W - 1));
                const si = srcBase + srcX * 4, di = dstBase + dstX * 4;
                dstImg.data[di] = srcImg.data[si];
                dstImg.data[di+1] = srcImg.data[si+1];
                dstImg.data[di+2] = srcImg.data[si+2];
                dstImg.data[di+3] = srcImg.data[si+3];
            }
        }
        dstCtx.putImageData(dstImg, 0, 0);
        return dst;
    }

    return srcCanvas;
}

// ─────────────────────────────────────────────────────────────────────────────
// Zoom / pan
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Attach wheel-zoom and drag-pan handlers to a canvas element.
 * Guards against double-init via canvas._zoomPanInited.
 * @param {HTMLCanvasElement} canvas
 */
function enableZoomAndPan(canvas) {
    if (!canvas) return;
    if (canvas._zoomPanInited) return;
    canvas._zoomPanInited = true;

    let scale = 1, tx = 0, ty = 0;
    let dragging = false, lastX = 0, lastY = 0;

    function applyTransform() {
        canvas.style.transformOrigin = '0 0';
        canvas.style.transform = `translate(${tx}px,${ty}px) scale(${scale})`;
        canvas.style.cursor = dragging ? 'grabbing' : (scale > 1 ? 'grab' : 'default');
    }

    canvas.addEventListener('wheel', e => {
        e.preventDefault();
        const rect   = canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        const delta  = e.deltaY < 0 ? 1.15 : (1 / 1.15);
        const newScale = Math.max(1, Math.min(10, scale * delta));
        tx = mouseX - (mouseX - tx) * (newScale / scale);
        ty = mouseY - (mouseY - ty) * (newScale / scale);
        scale = newScale;
        if (scale === 1) { tx = 0; ty = 0; }
        applyTransform();
    }, { passive: false });

    canvas.addEventListener('mousedown', e => {
        if (scale <= 1) return;
        dragging = true;
        lastX = e.clientX;
        lastY = e.clientY;
        e.preventDefault();
    });

    const onMouseMove = e => {
        if (!dragging) return;
        tx += e.clientX - lastX;
        ty += e.clientY - lastY;
        lastX = e.clientX;
        lastY = e.clientY;
        applyTransform();
    };

    const onMouseUp = () => {
        dragging = false;
        if (scale > 1) canvas.style.cursor = 'grab';
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);

    canvas._zoomPanCleanup = () => {
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
    };

    canvas.addEventListener('dblclick', () => {
        scale = 1; tx = 0; ty = 0;
        applyTransform();
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window (ES module — functions are not auto-global)
// ─────────────────────────────────────────────────────────────────────────────
window.mapElevationToColor = mapElevationToColor;
window.renderSatelliteCanvas = renderSatelliteCanvas;
window.updateAxesOverlay = updateAxesOverlay;
window.drawColorbar = drawColorbar;
window.drawHistogram = drawHistogram;
window.applyProjection = applyProjection;
window.enableZoomAndPan = enableZoomAndPan;
