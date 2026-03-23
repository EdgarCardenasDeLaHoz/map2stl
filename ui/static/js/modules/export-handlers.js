/**
 * modules/export-handlers.js — Model generation and file export handlers.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   _setExportButtonsEnabled(enabled) — toggle export button states
 *   generateModelFromTab()            — validate params, set generatedModelData
 *   downloadSTL()                     — POST to /api/export/stl → download
 *   downloadModel(format)             — POST to /api/export/{format} → download
 *   downloadCrossSection()            — POST to /api/export/crosssection → download
 *
 * External dependencies:
 *   window.appState.lastDemData
 *   window.appState.selectedRegion
 *   window.appState.generatedModelData  (written here, read by _updateWorkflowStepper)
 *   window.appState._updateWorkflowStepper()
 *   showLoading(el, msg), hideLoading(el)   — file-top globals in app.js
 *   showToast(msg, type)                    — file-top global in app.js
 */

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function _setExportButtonsEnabled(enabled) {
    const ids = ['downloadSTLBtn', 'downloadOBJBtn', 'download3MFBtn',
                 'exportCityBtn', 'exportCrossSectionBtn', 'exportPuzzleBtn'];
    for (const id of ids) {
        const el = document.getElementById(id);
        if (!el) continue;
        el.disabled      = !enabled;
        el.style.opacity = enabled ? '' : '0.4';
        el.style.cursor  = enabled ? '' : 'not-allowed';
    }
    const emptyEl = document.getElementById('modelEmptyState');
    if (emptyEl) emptyEl.style.display = enabled ? 'none' : 'flex';
}

function _triggerDownload(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const a   = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    window.URL.revokeObjectURL(url); a.remove();
}

function _progressEl() {
    return {
        wrap:  document.getElementById('modelProgress'),
        bar:   document.getElementById('modelProgressBar'),
        text:  document.getElementById('modelProgressText'),
        set(pct, msg) {
            if (this.wrap) this.wrap.style.display = 'block';
            if (this.bar)  this.bar.style.width    = pct + '%';
            if (this.text) this.text.textContent   = msg;
        },
        done(msg) { this.set(100, msg); setTimeout(() => { if (this.wrap) this.wrap.style.display = 'none'; }, 1000); },
        error(msg) {
            if (this.text) this.text.textContent     = msg;
            if (this.bar)  this.bar.style.backgroundColor = '#e74c3c';
            setTimeout(() => {
                if (this.wrap) this.wrap.style.display = 'none';
                if (this.bar)  this.bar.style.backgroundColor = '';
            }, 2000);
        }
    };
}

function _regionName() {
    const r = window.appState?.selectedRegion;
    return r?.name ? r.name.replace(/[^a-zA-Z0-9]/g, '_') : 'terrain';
}

function _exportParams() {
    const md = window.appState?.generatedModelData;
    return {
        dem_values:       md.values,
        height:           md.height,
        width:            md.width,
        model_height:     md.resolution,
        base_height:      md.baseHeight,
        exaggeration:     md.exaggeration,
        sea_level_cap:    document.getElementById('modelSeaLevelCap')?.checked   || false,
        engrave_label:    document.getElementById('modelEngraveLabel')?.checked  || false,
        label_text:       window.appState?.selectedRegion?.name || _regionName(),
        contours:         document.getElementById('modelContours')?.checked      || false,
        contour_interval: parseInt(document.getElementById('modelContourInterval')?.value) || 100,
        contour_style:    document.getElementById('modelContourStyle')?.value    || 'engraved',
        name:             _regionName()
    };
}

// ─────────────────────────────────────────────────────────────────────────────
// Generate
// ─────────────────────────────────────────────────────────────────────────────

function generateModelFromTab() {
    const lastDemData = window.appState?.lastDemData;
    if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
        showToast('Please load a DEM first by selecting a region on the map.', 'warning');
        return;
    }

    const resolution  = parseInt(document.getElementById('modelResolution').value);
    const exaggeration = parseFloat(document.getElementById('modelExaggeration').value);
    const baseHeight   = parseFloat(document.getElementById('modelBaseHeight').value);

    if (!resolution || resolution < 1 || resolution > 2000) {
        showToast('Resolution must be between 1 and 2000.', 'warning'); return;
    }
    if (!exaggeration || exaggeration <= 0 || exaggeration > 100) {
        showToast('Exaggeration must be between 0 and 100.', 'warning'); return;
    }
    if (isNaN(baseHeight) || baseHeight < 0 || baseHeight > 100) {
        showToast('Base height must be between 0 and 100 mm.', 'warning'); return;
    }

    const pr = _progressEl();
    pr.set(0, 'Preparing data...');

    const viewportEl = document.querySelector('.model-viewport');
    if (viewportEl) showLoading(viewportEl, 'Generating model...');

    setTimeout(() => pr.set(30, 'Generating mesh...'), 200);
    setTimeout(() => pr.set(70, 'Applying parameters...'), 500);
    setTimeout(() => {
        window.appState.generatedModelData = {
            values:      lastDemData.values,
            width:       lastDemData.width,
            height:      lastDemData.height,
            resolution,
            exaggeration,
            baseHeight,
            vmin:        lastDemData.vmin,
            vmax:        lastDemData.vmax
        };
        _setExportButtonsEnabled(true);
        window.appState._updateWorkflowStepper?.();
        const statusEl = document.getElementById('modelStatus');
        if (statusEl) statusEl.textContent = `Model ready (${resolution}x${resolution}, ${exaggeration}x exaggeration)`;
        if (viewportEl) hideLoading(viewportEl);
        pr.done('Complete!');
    }, 800);
}

// ─────────────────────────────────────────────────────────────────────────────
// Downloads
// ─────────────────────────────────────────────────────────────────────────────

function downloadSTL() {
    if (!window.appState?.generatedModelData) {
        showToast('Please generate a model first.', 'warning'); return;
    }
    const pr   = _progressEl();
    const name = _regionName();
    pr.set(20, 'Preparing STL export...');

    fetch('/api/export/stl', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(_exportParams())
    })
        .then(response => {
            pr.set(80, 'Downloading STL...');
            if (!response.ok) return response.json().then(e => { throw new Error(e.error || 'STL generation failed'); });
            const isWatertight = response.headers.get('X-Watertight') === 'true';
            const faceCount    = response.headers.get('X-Face-Count');
            return response.blob().then(blob => ({ blob, isWatertight, faceCount }));
        })
        .then(({ blob, isWatertight, faceCount }) => {
            _triggerDownload(blob, `${name}.stl`);
            const faces   = faceCount ? `${parseInt(faceCount).toLocaleString()} faces` : '';
            const quality = isWatertight ? '✓ watertight' : '⚠ not watertight';
            showToast(`STL ready — ${faces} ${quality}`, isWatertight ? 'success' : 'info', 4000);
            pr.done('Complete!');
        })
        .catch(e => { console.error('STL download error:', e); pr.error('Error: ' + e.message); });
}

function downloadModel(format) {
    if (!window.appState?.generatedModelData) {
        showToast('Please generate a model first.', 'warning'); return;
    }
    const pr   = _progressEl();
    const name = _regionName();
    pr.set(20, `Preparing ${format.toUpperCase()} export...`);

    fetch(`/api/export/${format}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(_exportParams())
    })
        .then(response => {
            pr.set(80, `Downloading ${format.toUpperCase()}...`);
            if (!response.ok) return response.json().then(e => { throw new Error(e.error || `${format.toUpperCase()} generation failed`); });
            return response.blob();
        })
        .then(blob => { _triggerDownload(blob, `${name}.${format}`); pr.done('Complete!'); })
        .catch(e => { console.error(`${format} download error:`, e); pr.error('Error: ' + e.message); });
}

function downloadCrossSection() {
    if (!window.appState?.generatedModelData) {
        showToast('Please generate a model first.', 'warning'); return;
    }
    const cutAxis    = document.getElementById('crossSectionAxis')?.value || 'lat';
    const cutValue   = parseFloat(document.getElementById('crossSectionValue')?.value);
    if (isNaN(cutValue)) { showToast('Enter a cut coordinate first', 'warning'); return; }
    const thickness  = parseFloat(document.getElementById('crossSectionThickness')?.value) || 5;
    const statusEl   = document.getElementById('crossSectionStatus');
    if (statusEl) statusEl.textContent = 'Generating…';

    const r    = window.appState?.selectedRegion || {};
    const name = _regionName();
    const md   = window.appState.generatedModelData;

    fetch('/api/export/crosssection', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
            dem_values:   md.values,
            height:       md.height,
            width:        md.width,
            north:        r.north ?? 0, south: r.south ?? 0,
            east:         r.east  ?? 0, west:  r.west  ?? 0,
            cut_axis:     cutAxis,
            cut_value:    cutValue,
            model_height: md.resolution,
            base_height:  md.baseHeight,
            exaggeration: md.exaggeration,
            thickness_mm: thickness,
            name
        })
    })
        .then(response => {
            if (!response.ok) return response.json().then(e => { throw new Error(e.error || 'Cross-section failed'); });
            return response.blob();
        })
        .then(blob => {
            const axis     = cutAxis === 'lat' ? `lat${cutValue.toFixed(4)}` : `lon${cutValue.toFixed(4)}`;
            _triggerDownload(blob, `${name}_cross_${axis}.stl`);
            if (statusEl) statusEl.textContent = 'Downloaded.';
            showToast('Cross-section STL ready', 'success');
        })
        .catch(e => {
            console.error('Cross-section error:', e);
            if (statusEl) statusEl.textContent = 'Error: ' + e.message;
            showToast('Cross-section error: ' + e.message, 'error');
        });
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window._setExportButtonsEnabled = _setExportButtonsEnabled;
window.generateModelFromTab     = generateModelFromTab;
window.downloadSTL              = downloadSTL;
window.downloadModel            = downloadModel;
window.downloadCrossSection     = downloadCrossSection;
