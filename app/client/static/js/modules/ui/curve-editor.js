/**
 * modules/curve-editor.js — Elevation curve editor.
 *
 * Loaded as a plain <script> after curve-editor-state.js.
 * All state logic is delegated to `window.CurveEditorState`.
 * This file handles DOM rendering, event wiring, and DEM application.
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
 *   interpolateCurve(x)             — evaluate curve at x in [0,1]
 *   resetDemToOriginal()            — restore DEM to pre-curve values
 *
 * External dependencies (accessed via window / window.appState):
 *   window.CurveEditorState          — from curve-editor-state.js
 *   window.CURVE_PRESETS             — from curve-editor-state.js
 *   window.appState.lastDemData
 *   window.appState.originalDemValues   (read + written)
 *   window.appState.curveDataVmin/Vmax  (written by renderDEMCanvas, read here)
 *   window.appState.curvePoints         (kept in sync with state.points)
 *   window.appState.activeCurvePreset   (kept in sync)
 *   window.events / window.EV            — event bus (DEM_LOADED listener)
 *   window.recolorDEM()                 — from dem-loader.js
 *   window.showToast(msg, type)         — global from app.js
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state — delegated to CurveEditorState
// ─────────────────────────────────────────────────────────────────────────────

/** @type {CurveEditorState} */
let _state = null;

let curveCanvas = null;
let curveCtx = null;
let _dragStartX = null;
let _curveRafPending = false;

// Unsubscribe function for the DEM_LOADED event bus listener.
let _demLoadedUnsubscribe = null;

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function _fmtElev(v) {
    return Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'k' : Math.round(v) + 'm';
}

function _syncToAppState() {
    if (!_state) return;
    window.appState.curvePoints = _state.points;
    window.appState.activeCurvePreset = _state.preset;
}

function _updateCurveUndoRedoBtns() {
    if (!_state) return;
    const undoBtn = document.getElementById('undoCurveBtn');
    const redoBtn = document.getElementById('redoCurveBtn');
    if (undoBtn) undoBtn.disabled = !_state.canUndo();
    if (redoBtn) redoBtn.disabled = !_state.canRedo();
}

function _getState() {
    if (!_state) _state = new window.CurveEditorState();
    return _state;
}

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────

function initCurveEditor() {
    curveCanvas = document.getElementById('curveCanvas');
    if (!curveCanvas) return;

    _state = new window.CurveEditorState();
    curveCtx = curveCanvas.getContext('2d');

    const container = curveCanvas.parentElement;
    const containerWidth = container.clientWidth || 200;
    const containerHeight = container.clientHeight || 150;
    curveCanvas.width = Math.max(containerWidth, 150);
    curveCanvas.height = Math.max(containerHeight, 100);

    _syncToAppState();

    _setupCurveEventListeners();

    // Subscribe to DEM_LOADED via the event bus so curve-editor re-normalises
    // control points whenever new terrain data arrives.
    _demLoadedUnsubscribe?.();
    _demLoadedUnsubscribe = window.events?.on(window.EV?.DEM_LOADED, function (vmin, vmax) {
        const oldVmin = window.appState.curveDataVmin;
        const oldVmax = window.appState.curveDataVmax;
        if (oldVmin != null && oldVmax != null && (oldVmin !== vmin || oldVmax !== vmax)) {
            _state.rescalePoints(oldVmin, oldVmax, vmin, vmax);
            _syncToAppState();
        }
        window.appState.curveDataVmin = vmin;
        window.appState.curveDataVmax = vmax;
        drawCurve();
    });

    let _curveResizeRaf = null;
    let _lastCurveWidth = 0;
    let _lastCurveHeight = 0;
    const _applyCurveResize = () => {
        if (container.clientWidth > 0 && container.clientHeight > 0) {
            const nextWidth = container.clientWidth;
            const nextHeight = container.clientHeight;
            if (nextWidth !== _lastCurveWidth || nextHeight !== _lastCurveHeight) {
                _lastCurveWidth = nextWidth;
                _lastCurveHeight = nextHeight;
                curveCanvas.width = nextWidth;
                curveCanvas.height = nextHeight;
                drawCurve();
            }
        }
    };
    const resizeObserver = new ResizeObserver(() => {
        if (_curveResizeRaf) return;
        _curveResizeRaf = requestAnimationFrame(() => { _curveResizeRaf = null; _applyCurveResize(); });
    });
    resizeObserver.observe(container);
    _applyCurveResize();

    // Expose a CurveEditor-like instance on window for programmatic access
    window.curveEditor = {
        getPoints()         { return _state.getPoints(); },
        setPoints(pts)      { applyCurveSettings(pts, 'custom'); },
        redraw()            { drawCurve(); },
        serialize()         { return _state.serialize(); },
        deserialize(data)   { if (data?.points) applyCurveSettings(data.points, data.preset ?? 'custom'); },
    };
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
        if (!_state.findPointNear(x, y)) addCurvePoint(x, y);
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
        draggingPoint = _state.findPointNear(x, y);
        if (draggingPoint) {
            _state.pushHistory();
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

        const idx = _state.points.indexOf(draggingPoint);
        const isFirst = idx === 0;
        const isLast = idx === _state.points.length - 1;

        let newX = rawX;
        if (isFirst) newX = 0;
        else if (isLast) newX = 1;
        else newX = _dragStartX ?? draggingPoint.x;

        const prevY = isFirst ? 0 : _state.points[idx - 1].y;
        const nextY = isLast ? 1 : _state.points[idx + 1].y;
        const newY = Math.max(prevY, Math.min(nextY, rawY));

        _state.movePoint(draggingPoint, newX, newY);
        drawCurve();
    });

    const _endDrag = () => {
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
    };
    curveCanvas.addEventListener('mouseup', _endDrag);
    curveCanvas.addEventListener('mouseleave', _endDrag);

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
            _state.pushHistory();
            const slX = Math.max(0.01, Math.min(0.98, (0 - vmin) / ((vmax - vmin) || 1)));
            const depthScale = 0.3;

            _state.points = _state.points.filter(p =>
                p === _state.points[0] || p === _state.points[_state.points.length - 1] || p.x > slX + 0.02
            );

            _state.points[0] = { x: 0, y: 0 };
            if (_state.points[_state.points.length - 1].x < 1) _state.points.push({ x: 1, y: 1 });

            const shelfY = slX * depthScale;
            _state.points.push({ x: slX - 0.005, y: shelfY });
            _state.points.push({ x: slX, y: shelfY + 0.015 });
            _state.points.push({ x: slX + 0.02, y: shelfY + 0.04 });

            _state.points.sort((a, b) => a.x - b.x);
            for (let i = 1; i < _state.points.length; i++) {
                if (_state.points[i].y < _state.points[i - 1].y)
                    _state.points[i].y = _state.points[i - 1].y;
            }
            _syncToAppState();
            drawCurve();
            applyCurveTodemSilent();
            window.showToast('Sea level shelf applied', 'success');
        });
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Curve operations — delegate to _state
// ─────────────────────────────────────────────────────────────────────────────

function setCurvePreset(presetName) {
    _getState().loadPreset(presetName);
    _syncToAppState();
    drawCurve();
}

function addCurvePoint(x, y) {
    _getState().addPoint(x, y);
    _syncToAppState();
    drawCurve();
}

function removeCurvePointNear(x, y) {
    _getState().removePoint(x, y);
    _syncToAppState();
    drawCurve();
}

function findCurvePointNear(x, y) {
    return _getState().findPointNear(x, y);
}

function drawCurve() {
    const state = _getState();
    state._lut = null;
    if (!curveCtx || !curveCanvas) return;

    const w = curveCanvas.width;
    const h = curveCanvas.height;
    const curvePoints = state.points;

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

    // Sea-level marker
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
            curveCtx.fillText('\u00d7', px, py + 3);
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
    for (let i = 0; i <= 4; i++) {
        const t = i / 4;
        const px = t * w;
        const label = hasElev ? _fmtElev(vmin + t * (vmax - vmin)) : (t * 100 | 0) + '%';
        if (i === 0) { curveCtx.textAlign = 'left'; curveCtx.fillText(label, 2, h - 3); curveCtx.textAlign = 'center'; }
        else if (i === 4) { curveCtx.textAlign = 'right'; curveCtx.fillText(label, w - 2, h - 3); curveCtx.textAlign = 'center'; }
        else curveCtx.fillText(label, px, h - 3);
    }
    curveCtx.textAlign = 'left';
    for (let i = 1; i <= 4; i++) {
        const t = i / 4;
        const py = (1 - t) * h;
        const label = hasElev ? _fmtElev(vmin + t * (vmax - vmin)) : (t * 100 | 0) + '%';
        curveCtx.fillText(label, 2, py - 2);
    }
    curveCtx.save();
    curveCtx.fillStyle = '#555';
    curveCtx.font = '9px sans-serif';
    curveCtx.translate(w - 4, h / 2);
    curveCtx.rotate(Math.PI / 2);
    curveCtx.textAlign = 'center';
    curveCtx.fillText('\u2190 Output', 0, 0);
    curveCtx.restore();
    curveCtx.textAlign = 'left';
}

function interpolateCurve(x) {
    return _getState().interpolate(x);
}

function _applyCurrentCurve() {
    const state = _getState();
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

    const remapped = values.map(v => {
        const t = (v - vmin) / range;
        const curved_t = state.interpolate(Math.max(0, Math.min(1, t)));
        return vmin + curved_t * range;
    });

    return { remapped, vmin, vmax };
}

function applyCurveTodem() {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.values || _getState().points.length < 2) {
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

function applyCurveTodemSilent() {
    const lastDemData = window.appState.lastDemData;
    if (!lastDemData || !lastDemData.values || _getState().points.length < 2) return;
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

function undoCurve() {
    const state = _getState();
    if (!state.undo()) return;
    _syncToAppState();
    drawCurve();
    applyCurveTodemSilent();
    _updateCurveUndoRedoBtns();
}

function redoCurve() {
    const state = _getState();
    if (!state.redo()) return;
    _syncToAppState();
    drawCurve();
    applyCurveTodemSilent();
    _updateCurveUndoRedoBtns();
}

function resetDemToOriginal() {
    const lastDemData = window.appState.lastDemData;
    const originalDemVals = window.appState.originalDemValues;
    if (originalDemVals && lastDemData) {
        lastDemData.values = [...originalDemVals];
        window.recolorDEM?.();
        window.showToast('DEM reset to original', 'info');
    }
}

function applyCurveSettings(points, presetName) {
    _getState().setPoints(points, presetName || 'custom');
    _syncToAppState();
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
window.curvePresets = window.CURVE_PRESETS;
