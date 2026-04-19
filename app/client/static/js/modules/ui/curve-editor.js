/**
 * modules/curve-editor.js — Elevation curve editor.
 *
 * Loaded as a plain <script> before app.js. All public functions are exposed
 * on window so app.js can call them directly.
 *
 * Public API (all on window):
 *   initCurveEditor()                — set up canvas, events, initial curve
 *   setCurvePreset(name)             — load a named curve preset
 *   addCurvePoint(x, y)             — add a control point
 *   drawCurve()                      — re-render the curve canvas
 *   applyCurveTodem()               — apply curve to DEM with toast
 *   applyCurveTodemSilent()         — apply curve silently (drag updates)
 *   applyCurveSettings(pts, preset) — restore curve state (called by presets.js)
 *   undoCurve()                      — step back in history
 *   redoCurve()                      — step forward in history
 *   interpolateCurve(x)             — evaluate curve at x ∈ [0,1]
 *   resetDemToOriginal()            — restore DEM to pre-curve values
 *
 * External dependencies (accessed via window / window.appState):
 *   window.appState.lastDemData
 *   window.appState.originalDemValues   (read + written)
 *   window.appState.curveDataVmin/Vmax  (written by renderDEMCanvas, read here)
 *   window.appState.curvePoints         (kept in sync with module curvePoints)
 *   window.appState.activeCurvePreset   (kept in sync)
 *   window.appState._onDemLoaded        (registered here for DEM-load events)
 *   window.recolorDEM()                 — from dem-loader.js
 *   window.showToast(msg, type)               — global from app.js
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let curvePoints = [{ x: 0, y: 0 }, { x: 1, y: 1 }];
let curveCanvas = null;
let curveCtx = null;
let activeCurvePreset = 'linear';
let _curveLUT = null;
let _dragStartX = null;
let _curveRafPending = false;  // RAF-gate for DEM recolor during drag

const _CURVE_HISTORY_MAX = 30;
let _curveHistory = [];
let _curveHistoryIdx = -1;

const curvePresets = {
    'linear': [[0, 0], [1, 1]],
    'enhance-peaks': [[0, 0], [0.3, 0.2], [0.5, 0.4], [0.7, 0.7], [0.85, 0.9], [1, 1]],
    'compress-depths': [[0, 0.2], [0.2, 0.3], [0.4, 0.45], [0.6, 0.6], [0.8, 0.8], [1, 1]],
    's-curve': [[0, 0], [0.25, 0.1], [0.5, 0.5], [0.75, 0.9], [1, 1]]
};

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function _fmtElev(v) {
    return Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'k' : Math.round(v) + 'm';
}

function _syncCurvePoints() {
    window.appState.curvePoints = curvePoints;
}

/**
 * Rescale curve points so that their absolute elevation positions are
 * preserved when the DEM vmin/vmax range changes.  Each point's x is
 * converted from old-normalised → absolute elevation → new-normalised.
 * Endpoints at x=0 and x=1 are always kept.
 */
function _rescaleCurvePoints(oldMin, oldMax, newMin, newMax) {
    const oldRange = oldMax - oldMin;
    const newRange = newMax - newMin;
    if (!oldRange || !newRange) return;

    for (let i = 0; i < curvePoints.length; i++) {
        const pt = curvePoints[i];
        // Convert normalised x to absolute elevation, then to new normalised
        const absElev = pt.x * oldRange + oldMin;
        pt.x = Math.max(0, Math.min(1, (absElev - newMin) / newRange));
    }
    // Ensure endpoints exist at 0 and 1
    if (curvePoints[0].x !== 0) curvePoints[0].x = 0;
    if (curvePoints[curvePoints.length - 1].x !== 1) curvePoints[curvePoints.length - 1].x = 1;

    // Remove duplicate x-positions (can happen when range narrows)
    const seen = new Set();
    curvePoints = curvePoints.filter(pt => {
        const key = pt.x.toFixed(6);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });

    _syncCurvePoints();
    _curveLUT = null;  // invalidate cached LUT
}

function _pushCurveHistory() {
    _curveHistory.splice(_curveHistoryIdx + 1);
    _curveHistory.push(curvePoints.map(p => ({ x: p.x, y: p.y })));
    if (_curveHistory.length > _CURVE_HISTORY_MAX) _curveHistory.shift();
    _curveHistoryIdx = _curveHistory.length - 1;
    _updateCurveUndoRedoBtns();
}

function _updateCurveUndoRedoBtns() {
    const undoBtn = document.getElementById('undoCurveBtn');
    const redoBtn = document.getElementById('redoCurveBtn');
    if (undoBtn) undoBtn.disabled = _curveHistoryIdx <= 0;
    if (redoBtn) redoBtn.disabled = _curveHistoryIdx >= _curveHistory.length - 1;
}

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────

function initCurveEditor() {
    curveCanvas = document.getElementById('curveCanvas');
    if (!curveCanvas) return;

    curveCtx = curveCanvas.getContext('2d');

    const container = curveCanvas.parentElement;
    const containerWidth = container.clientWidth || 200;
    const containerHeight = container.clientHeight || 150;
    curveCanvas.width = Math.max(containerWidth, 150);
    curveCanvas.height = Math.max(containerHeight, 100);

    setCurvePreset('linear');
    _pushCurveHistory();

    _setupCurveEventListeners();

    // Re-register _onDemLoaded so drawCurve gets updated vmin/vmax on each DEM load.
    // Called before renderDEMCanvas writes curveDataVmin/Vmax to appState, so we
    // propagate the new values ourselves before redrawing.
    // If the elevation range changed, rescale curve points so that absolute
    // elevation anchors (e.g. sea level at 0 m) stay in the same position.
    window.appState._onDemLoaded = function (vmin, vmax) {
        const oldVmin = window.appState.curveDataVmin;
        const oldVmax = window.appState.curveDataVmax;
        if (oldVmin != null && oldVmax != null && (oldVmin !== vmin || oldVmax !== vmax)) {
            _rescaleCurvePoints(oldVmin, oldVmax, vmin, vmax);
        }
        window.appState.curveDataVmin = vmin;
        window.appState.curveDataVmax = vmax;
        drawCurve();
    };

    let _curveResizeRaf = null;
    const _applyCurveResize = () => {
        if (container.clientWidth > 0 && container.clientHeight > 0) {
            curveCanvas.width = container.clientWidth;
            curveCanvas.height = container.clientHeight;
            drawCurve();
        }
    };
    const resizeObserver = new ResizeObserver(() => {
        if (_curveResizeRaf) return;
        _curveResizeRaf = requestAnimationFrame(() => { _curveResizeRaf = null; _applyCurveResize(); });
    });
    resizeObserver.observe(container);
    _applyCurveResize();
}

// ─────────────────────────────────────────────────────────────────────────────
// Event wiring
// ─────────────────────────────────────────────────────────────────────────────

function _setupCurveEventListeners() {
    if (!curveCanvas || curveCanvas._curveWired) return;
    curveCanvas._curveWired = true;

    // Preset buttons
    document.querySelectorAll('.curve-presets button').forEach(btn => {
        btn.addEventListener('click', () => {
            const preset = btn.dataset.preset;
            _pushCurveHistory();
            setCurvePreset(preset);
            document.querySelectorAll('.curve-presets button').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            applyCurveTodemSilent();
        });
    });

    // Undo/Redo buttons
    const undoBtn = document.getElementById('undoCurveBtn');
    const redoBtn = document.getElementById('redoCurveBtn');
    if (undoBtn) undoBtn.addEventListener('click', undoCurve);
    if (redoBtn) redoBtn.addEventListener('click', redoCurve);

    let draggingPoint = null;
    let didDrag = false;

    curveCanvas.addEventListener('click', (e) => {
        if (didDrag) { didDrag = false; return; }
        const rect = curveCanvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = 1 - (e.clientY - rect.top) / rect.height;
        if (!findCurvePointNear(x, y)) addCurvePoint(x, y);
    });

    curveCanvas.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        const rect = curveCanvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = 1 - (e.clientY - rect.top) / rect.height;
        removeCurvePointNear(x, y);
    });

    curveCanvas.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        const rect = curveCanvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = 1 - (e.clientY - rect.top) / rect.height;
        draggingPoint = findCurvePointNear(x, y);
        if (draggingPoint) {
            _pushCurveHistory();
            _dragStartX = draggingPoint.x;
        }
        didDrag = false;
    });

    curveCanvas.addEventListener('mousemove', (e) => {
        if (!draggingPoint) return;
        didDrag = true;
        const rect = curveCanvas.getBoundingClientRect();
        const rawX = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        const rawY = Math.max(0, Math.min(1, 1 - (e.clientY - rect.top) / rect.height));

        const idx = curvePoints.indexOf(draggingPoint);
        const isFirst = idx === 0;
        const isLast = idx === curvePoints.length - 1;

        let newX = rawX;
        if (isFirst) newX = 0;
        else if (isLast) newX = 1;
        else newX = _dragStartX ?? draggingPoint.x;

        const prevY = isFirst ? 0 : curvePoints[idx - 1].y;
        const nextY = isLast ? 1 : curvePoints[idx + 1].y;
        const newY = Math.max(prevY, Math.min(nextY, rawY));

        draggingPoint.x = newX;
        draggingPoint.y = newY;
        drawCurve();
    });

    curveCanvas.addEventListener('mouseup', () => {
        if (draggingPoint) {
            draggingPoint = null;
            if (!_curveRafPending) {
                _curveRafPending = true;
                requestAnimationFrame(() => {
                    _curveRafPending = false;
                    applyCurveTodemSilent();
                });
            }
        }
    });
    curveCanvas.addEventListener('mouseleave', () => {
        if (draggingPoint) {
            draggingPoint = null;
            if (!_curveRafPending) {
                _curveRafPending = true;
                requestAnimationFrame(() => {
                    _curveRafPending = false;
                    applyCurveTodemSilent();
                });
            }
        }
    });

    const applyBtn = document.getElementById('applyCurveBtn');
    if (applyBtn) applyBtn.addEventListener('click', applyCurveTodem);

    const resetBtn = document.getElementById('resetCurveBtn');
    if (resetBtn) {
        resetBtn.addEventListener('click', () => {
            setCurvePreset('linear');
            document.querySelectorAll('.curve-presets button').forEach(b => b.classList.remove('active'));
            document.querySelector('.curve-presets button[data-preset="linear"]')?.classList.add('active');
            resetDemToOriginal();
        });
    }

    const seaLvlBtn = document.getElementById('seaLevelBufferBtn');
    if (seaLvlBtn) {
        seaLvlBtn.addEventListener('click', () => {
            const vmin = window.appState.curveDataVmin;
            const vmax = window.appState.curveDataVmax;
            if (vmin === null || vmin === undefined) {
                window.showToast('Load DEM data first', 'warning');
                return;
            }
            if (vmin >= 0) {
                window.showToast('No sub-sea-level data in this region', 'info');
                return;
            }
            _pushCurveHistory();
            const slX = Math.max(0.01, Math.min(0.98, (0 - vmin) / ((vmax - vmin) || 1)));
            const depthScale = 0.3;

            curvePoints = curvePoints.filter(p =>
                p === curvePoints[0] || p === curvePoints[curvePoints.length - 1] || p.x > slX + 0.02
            );
            _syncCurvePoints();

            curvePoints[0] = { x: 0, y: 0 };
            if (curvePoints[curvePoints.length - 1].x < 1) curvePoints.push({ x: 1, y: 1 });

            const shelfY = slX * depthScale;
            curvePoints.push({ x: slX - 0.005, y: shelfY });
            curvePoints.push({ x: slX, y: shelfY + 0.015 });
            curvePoints.push({ x: slX + 0.02, y: shelfY + 0.04 });

            curvePoints.sort((a, b) => a.x - b.x);
            for (let i = 1; i < curvePoints.length; i++) {
                if (curvePoints[i].y < curvePoints[i - 1].y)
                    curvePoints[i].y = curvePoints[i - 1].y;
            }
            _syncCurvePoints();
            drawCurve();
            applyCurveTodemSilent();
            window.showToast('Sea level shelf applied', 'success');
        });
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Curve operations
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Load a named curve preset (linear, enhance-peaks, etc.).
 * Updates curvePoints, appState, and redraws the canvas.
 * @param {string} presetName - One of: 'linear', 'enhance-peaks', 'compress-depths', 's-curve'
 */
function setCurvePreset(presetName) {
    activeCurvePreset = presetName;
    window.appState.activeCurvePreset = presetName;
    const preset = curvePresets[presetName];
    if (!preset) return;
    curvePoints = preset.map(p => ({ x: p[0], y: p[1] }));
    _syncCurvePoints();
    drawCurve();
}

/**
 * Add a new control point to the curve at (x, y) ∈ [0,1]×[0,1].
 * Snaps to existing points within threshold to avoid duplicates.
 * Clamps Y between neighbors' Y values to ensure monotonic sections.
 * @param {number} x - Normalized X coordinate [0, 1] (input elevation)
 * @param {number} y - Normalized Y coordinate [0, 1] (output elevation)
 */
function addCurvePoint(x, y) {
    const threshold = 0.08;
    for (const p of curvePoints) {
        if (Math.abs(p.x - x) < threshold && Math.abs(p.y - y) < threshold) return;
    }
    curvePoints.sort((a, b) => a.x - b.x);
    let prevY = 0, nextY = 1;
    for (let i = 0; i < curvePoints.length; i++) {
        if (curvePoints[i].x <= x) prevY = curvePoints[i].y;
        else { nextY = curvePoints[i].y; break; }
    }
    y = Math.max(prevY, Math.min(nextY, y));
    _pushCurveHistory();
    curvePoints.push({ x, y });
    curvePoints.sort((a, b) => a.x - b.x);
    _syncCurvePoints();
    drawCurve();
}

/**
 * Remove a control point near (x, y) ∈ [0,1]×[0,1] if found within threshold.
 * Cannot remove endpoint points (index 0 or last).
 * @param {number} x - Normalized X coordinate
 * @param {number} y - Normalized Y coordinate
 */
function removeCurvePointNear(x, y) {
    const threshold = 0.12;
    const index = curvePoints.findIndex(p =>
        Math.abs(p.x - x) < threshold && Math.abs(p.y - y) < threshold
    );
    if (index !== -1 && index !== 0 && index !== curvePoints.length - 1) {
        _pushCurveHistory();
        curvePoints.splice(index, 1);
        _syncCurvePoints();
        drawCurve();
    }
}

/**
 * Find a control point near (x, y) ∈ [0,1]×[0,1] within threshold distance.
 * Returns the matching point object or undefined.
 * @param {number} x - Normalized X coordinate
 * @param {number} y - Normalized Y coordinate
 * @returns {Object|undefined} Control point {x, y} if found, else undefined
 */
function findCurvePointNear(x, y) {
    const threshold = 0.12;
    return curvePoints.find(p =>
        Math.abs(p.x - x) < threshold && Math.abs(p.y - y) < threshold
    );
}

function drawCurve() {
    _curveLUT = null;
    if (!curveCtx || !curveCanvas) return;

    const w = curveCanvas.width;
    const h = curveCanvas.height;

    curveCtx.fillStyle = '#252525';
    curveCtx.fillRect(0, 0, w, h);

    curveCtx.strokeStyle = '#3a3a3a';
    curveCtx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
        const gx = w * i / 4;
        const gy = h * i / 4;
        curveCtx.beginPath(); curveCtx.moveTo(gx, 0); curveCtx.lineTo(gx, h); curveCtx.stroke();
        curveCtx.beginPath(); curveCtx.moveTo(0, gy); curveCtx.lineTo(w, gy); curveCtx.stroke();
    }

    curveCtx.strokeStyle = '#555';
    curveCtx.setLineDash([5, 5]);
    curveCtx.beginPath();
    curveCtx.moveTo(0, h);
    curveCtx.lineTo(w, 0);
    curveCtx.stroke();
    curveCtx.setLineDash([]);

    // Sea-level marker (stable: uses curveDataVmin/Vmax from load time)
    const vminSL = window.appState.curveDataVmin;
    const vmaxSL = window.appState.curveDataVmax;
    if (vminSL !== null && vminSL !== undefined && vmaxSL !== null && vmaxSL !== undefined) {
        const slX = (0 - vminSL) / ((vmaxSL - vminSL) || 1);
        if (slX > 0.01 && slX < 0.99) {
            const px = slX * w;
            curveCtx.strokeStyle = 'rgba(64,180,255,0.6)';
            curveCtx.lineWidth = 1;
            curveCtx.setLineDash([3, 3]);
            curveCtx.beginPath();
            curveCtx.moveTo(px, 0);
            curveCtx.lineTo(px, h);
            curveCtx.stroke();
            curveCtx.setLineDash([]);
            curveCtx.fillStyle = 'rgba(64,180,255,0.8)';
            curveCtx.font = '9px monospace';
            curveCtx.fillText('0m', px + 2, 10);
        }
    }

    if (curvePoints.length >= 2) {
        curveCtx.strokeStyle = '#00aaff';
        curveCtx.lineWidth = 2;
        curveCtx.beginPath();
        curveCtx.moveTo(curvePoints[0].x * w, (1 - curvePoints[0].y) * h);
        for (let i = 1; i < curvePoints.length; i++) {
            curveCtx.lineTo(curvePoints[i].x * w, (1 - curvePoints[i].y) * h);
        }
        curveCtx.stroke();
    }

    curvePoints.forEach((p, i) => {
        const isEndpoint = (i === 0 || i === curvePoints.length - 1);
        const px = p.x * w, py = (1 - p.y) * h;
        curveCtx.beginPath();
        curveCtx.arc(px, py, isEndpoint ? 7 : 8, 0, Math.PI * 2);
        curveCtx.fillStyle = isEndpoint ? '#ff6600' : '#00aaff';
        curveCtx.fill();
        curveCtx.strokeStyle = 'rgba(255,255,255,0.9)';
        curveCtx.lineWidth = 2;
        curveCtx.stroke();
        if (!isEndpoint) {
            curveCtx.fillStyle = 'rgba(255,255,255,0.5)';
            curveCtx.font = '9px sans-serif';
            curveCtx.textAlign = 'center';
            curveCtx.fillText('×', px, py + 3);
            curveCtx.textAlign = 'left';
        }
    });

    // Axis tick labels
    const vmin = window.appState?.curveDataVmin;
    const vmax = window.appState?.curveDataVmax;
    const hasElev = vmin != null && vmax != null && isFinite(vmin) && isFinite(vmax);
    curveCtx.fillStyle = '#888';
    curveCtx.font = '9px monospace';
    curveCtx.textAlign = 'center';
    // X-axis labels (Input elevation) — 0%, 25%, 50%, 75%, 100%
    for (let i = 0; i <= 4; i++) {
        const t = i / 4;
        const px = t * w;
        const label = hasElev ? _fmtElev(vmin + t * (vmax - vmin)) : (t * 100 | 0) + '%';
        if (i === 0) { curveCtx.textAlign = 'left'; curveCtx.fillText(label, 2, h - 3); curveCtx.textAlign = 'center'; }
        else if (i === 4) { curveCtx.textAlign = 'right'; curveCtx.fillText(label, w - 2, h - 3); curveCtx.textAlign = 'center'; }
        else curveCtx.fillText(label, px, h - 3);
    }
    // Y-axis labels (Output) — 25%, 50%, 75%, 100% (skip 0% to avoid overlap with X label)
    curveCtx.textAlign = 'left';
    for (let i = 1; i <= 4; i++) {
        const t = i / 4;
        const py = (1 - t) * h;
        const label = hasElev ? _fmtElev(vmin + t * (vmax - vmin)) : (t * 100 | 0) + '%';
        curveCtx.fillText(label, 2, py - 2);
    }
    // Rotated "Out" axis label
    curveCtx.save();
    curveCtx.fillStyle = '#555';
    curveCtx.font = '9px sans-serif';
    curveCtx.translate(w - 4, h / 2);
    curveCtx.rotate(Math.PI / 2);
    curveCtx.textAlign = 'center';
    curveCtx.fillText('← Output', 0, 0);
    curveCtx.restore();
    curveCtx.textAlign = 'left';
}

/**
 * Evaluate the curve at a normalized input value x ∈ [0, 1].
 * Uses linear interpolation between adjacent control points.
 * Returns the normalized output value y ∈ [0, 1].
 * @param {number} x - Input coordinate [0, 1]
 * @returns {number} Output coordinate via Catmull-Rom-like interpolation
 */
function interpolateCurve(x) {
    if (curvePoints.length < 2) return x;
    let left = curvePoints[0];
    let right = curvePoints[curvePoints.length - 1];
    for (let i = 0; i < curvePoints.length - 1; i++) {
        if (curvePoints[i].x <= x && curvePoints[i + 1].x >= x) {
            left = curvePoints[i];
            right = curvePoints[i + 1];
            break;
        }
    }
    const t = (x - left.x) / (right.x - left.x || 1);
    return left.y + t * (right.y - left.y);
}

function _buildLUT() {
    if (!_curveLUT) {
        _curveLUT = new Float32Array(1024);
        for (let i = 0; i < 1024; i++) _curveLUT[i] = interpolateCurve(i / 1023);
    }
}

function _applyCurrentCurve() {
    const lastDemData = window.appState.lastDemData;
    let originalDemVals = window.appState.originalDemValues;

    if (!originalDemVals) {
        originalDemVals = [...lastDemData.values];
        window.appState.originalDemValues = originalDemVals;
    }

    const values = [...originalDemVals];
    const vmin = window.appState.curveDataVmin ?? (() => { let m = Infinity; for (const v of values) if (v < m) m = v; return m; })();
    const vmax = window.appState.curveDataVmax ?? (() => { let m = -Infinity; for (const v of values) if (v > m) m = v; return m; })();
    const range = vmax - vmin || 1;

    // Apply curve to values: map each value to curve output
    const remapped = values.map(v => {
        const t = (v - vmin) / range;
        const curved_t = interpolateCurve(Math.max(0, Math.min(1, t)));
        return vmin + curved_t * range;
    });

    return { remapped, vmin, vmax };
}

/**
 * Apply the current curve to the DEM with toast notification and auto-rescale.
 * Requires a loaded DEM in appState.lastDemData.
 * Shows error toast if no DEM is loaded.
 */
function applyCurveTodem() {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.values || curvePoints.length < 2) {
        window.showToast('Load a DEM first', 'warning');
        return;
    }

    const { remapped, vmin, vmax } = _applyCurrentCurve();
    lastDemData.values = remapped;

    if (document.getElementById('autoRescale')?.checked) {
        let newMin = Infinity, newMax = -Infinity;
        for (const v of remapped) {
            if (isFinite(v)) { if (v < newMin) newMin = v; if (v > newMax) newMax = v; }
        }
        if (isFinite(newMin) && isFinite(newMax)) {
            lastDemData.vmin = newMin;
            lastDemData.vmax = newMax;
            document.getElementById('rescaleMin').value = Math.floor(newMin);
            document.getElementById('rescaleMax').value = Math.ceil(newMax);
        }
    }

    window.recolorDEM?.();
    window.showToast('Elevation curve applied!', 'success');
}

/**
 * Apply the current curve to the DEM silently (no toast, no UI update).
 * Used during drag operations and continuous updates.
 * Requires a loaded DEM in appState.lastDemData.
 */
function applyCurveTodemSilent() {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.values || curvePoints.length < 2) return;
    const { remapped } = _applyCurrentCurve();
    lastDemData.values = remapped;
    let newMin = Infinity, newMax = -Infinity;
    for (const v of remapped) {
        if (isFinite(v)) { if (v < newMin) newMin = v; if (v > newMax) newMax = v; }
    }
    if (isFinite(newMin) && isFinite(newMax)) {
        lastDemData.vmin = newMin;
        lastDemData.vmax = newMax;
    }
    window.recolorDEM?.();
}

/**
 * Step backward in the curve edit history.
 * Updates curvePoints, redraws, and re-applies curve to DEM.
 * Disabled at history start.
 */
function undoCurve() {
    if (_curveHistoryIdx <= 0) return;
    _curveHistoryIdx--;
    curvePoints = _curveHistory[_curveHistoryIdx].map(p => ({ x: p.x, y: p.y }));
    _syncCurvePoints();
    drawCurve();
    applyCurveTodemSilent();
    _updateCurveUndoRedoBtns();
}

/**
 * Step forward in the curve edit history.
 * Updates curvePoints, redraws, and re-applies curve to DEM.
 * Disabled at history end.
 */
function redoCurve() {
    if (_curveHistoryIdx >= _curveHistory.length - 1) return;
    _curveHistoryIdx++;
    curvePoints = _curveHistory[_curveHistoryIdx].map(p => ({ x: p.x, y: p.y }));
    _syncCurvePoints();
    drawCurve();
    applyCurveTodemSilent();
    _updateCurveUndoRedoBtns();
}

/**
 * Restore the DEM to its original pre-curve values.
 * Requires originalDemValues to exist in appState (saved on first curve application).
 */
function resetDemToOriginal() {
    const lastDemData = window.appState.lastDemData;
    const originalDemVals = window.appState.originalDemValues;
    if (originalDemVals && lastDemData) {
        lastDemData.values = [...originalDemVals];
        window.recolorDEM?.();
        window.showToast('DEM reset to original', 'info');
    }
}

/**
 * Restore curve state from a saved settings object (called by presets.js applyAllSettings).
 * @param {Array<{x,y}>} points - Control point array
 * @param {string} presetName   - Named preset key or 'custom'
 */
function applyCurveSettings(points, presetName) {
    curvePoints = points;
    activeCurvePreset = presetName || 'custom';
    window.appState.activeCurvePreset = activeCurvePreset;
    _syncCurvePoints();
    _curveLUT = null;
    drawCurve();
    applyCurveTodemSilent();
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.initCurveEditor = initCurveEditor;
window.setCurvePreset = setCurvePreset;
window.drawCurve = drawCurve;
window.applyCurveTodem = applyCurveTodem;
window.applyCurveSettings = applyCurveSettings;
window.undoCurve = undoCurve;
window.redoCurve = redoCurve;
window.curvePresets = curvePresets;
