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
 *   window.showToast(msg, type)                    — file-top global in app.js
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

/**
 * Return bbox + DEM settings so the server can look up the cached DEM
 * instead of receiving the full array over the wire.
 */
function _demSettings() {
    const bbox = window.appState?.currentDemBbox || window.appState?.selectedRegion || {};
    const p = window.appState?.demParams || {};
    const settings = {
        bbox: {
            north: bbox.north, south: bbox.south,
            east:  bbox.east,  west:  bbox.west,
        },
        dem: {
            dim:          parseInt(document.getElementById('paramDim')?.value) || 200,
            dem_source:   document.getElementById('paramDemSource')?.value || 'local',
            projection:   document.getElementById('paramProjection')?.value || 'cosine',
            depth_scale:  p.depthScale ?? 0.5,
            water_scale:  p.waterScale ?? 0.05,
            subtract_water:      p.subtractWater ?? true,
            maintain_dimensions: true,
            clip_nans:    document.getElementById('paramClipNans')?.checked ?? false,
            show_sat:     false,
        },
    };
    // If the user has configured + applied a composite, send the spec so the
    // server rebuilds the same merged DEM for the 3D output.
    const compositeSpec = window.getActiveCompositeSpec?.();
    if (compositeSpec) {
        settings.composite_layers = compositeSpec;
        settings.composite_dim = settings.dem.dim;
    }
    return settings;
}

function _exportParams() {
    const md = window.appState?.generatedModelData;
    return {
        ..._demSettings(),
        // model_height is the physical height in mm from #exportModelHeight.
        model_height:     md.modelHeight,
        base_height:      md.baseHeight,
        exaggeration:     md.exaggeration,
        mm_per_pixel:     md.mmPerPixel,
        sea_level_cap:    document.getElementById('exportSeaLevelCap')?.checked   || false,
        engrave_label:    document.getElementById('exportEngraveLabel')?.checked  || false,
        label_text:       document.getElementById('exportLabelText')?.value || window.appState?.selectedRegion?.name || _regionName(),
        contours:         document.getElementById('exportContours')?.checked      || false,
        contour_interval: parseInt(document.getElementById('exportContourInterval')?.value) || 100,
        contour_style:    document.getElementById('exportContourStyle')?.value    || 'engraved',
        name:             _regionName()
    };
}

// ─────────────────────────────────────────────────────────────────────────────
// Generate
// ─────────────────────────────────────────────────────────────────────────────

function generateModelFromTab() {
    const lastDemData = window.appState?.lastDemData;
    if (!lastDemData || !lastDemData.values || !lastDemData.values.length) {
        window.showToast('Please load a DEM first by selecting a region on the map.', 'warning');
        return;
    }

    const resolution   = parseInt(document.getElementById('modelResolution').value);
    const modelHeight  = parseFloat(document.getElementById('exportModelHeight')?.value) || 30;
    const exaggeration = parseFloat(document.getElementById('exportExaggeration')?.value) || 1.0;
    const baseHeight   = parseFloat(document.getElementById('exportBaseHeight')?.value) || 0;

    if (!resolution || resolution < 1 || resolution > 2000) {
        window.showToast('Resolution must be between 1 and 2000.', 'warning'); return;
    }
    if (!modelHeight || modelHeight <= 0 || modelHeight > 500) {
        window.showToast('Model height must be between 0 and 500 mm.', 'warning'); return;
    }
    if (!exaggeration || exaggeration <= 0 || exaggeration > 100) {
        window.showToast('Exaggeration must be between 0 and 100.', 'warning'); return;
    }
    if (isNaN(baseHeight) || baseHeight < 0 || baseHeight > 100) {
        window.showToast('Base height must be between 0 and 100 mm.', 'warning'); return;
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
            modelHeight,
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
// Async export helper (start → poll → download)
// ─────────────────────────────────────────────────────────────────────────────

async function _asyncExport(format) {
    const pr   = _progressEl();
    const name = _regionName();
    pr.set(0, `Starting ${format.toUpperCase()} export...`);

    try {
        // 1. Start the export task
        const startResp = await fetch('/api/export/start', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ format, ..._exportParams() })
        });
        if (!startResp.ok) {
            const err = await startResp.json();
            throw new Error(err.error || 'Failed to start export');
        }
        const { task_id } = await startResp.json();

        // 2. Poll for progress
        let status = { status: 'running', progress: 0, message: 'Starting...' };
        while (status.status === 'running') {
            await new Promise(r => setTimeout(r, 250));
            const pollResp = await fetch(`/api/export/status/${encodeURIComponent(task_id)}`);
            if (!pollResp.ok) throw new Error('Lost connection to export task');
            status = await pollResp.json();
            pr.set(status.progress, status.message);
        }

        if (status.status === 'error') {
            throw new Error(status.message || 'Export failed');
        }

        // 3. Download the result
        pr.set(98, `Downloading ${format.toUpperCase()}...`);
        const dlResp = await fetch(`/api/export/download/${encodeURIComponent(task_id)}`);
        if (!dlResp.ok) throw new Error('Download failed');

        const blob = await dlResp.blob();
        // Grab extra headers for STL quality info
        const isWatertight = dlResp.headers.get('X-Watertight') === 'true';
        const faceCount    = dlResp.headers.get('X-Face-Count');

        _triggerDownload(blob, `${name}.${format}`);

        if (format === 'stl' && faceCount) {
            const faces   = `${parseInt(faceCount).toLocaleString()} faces`;
            const quality = isWatertight ? 'watertight' : 'not watertight';
            window.showToast(`STL ready - ${faces} ${quality}`, isWatertight ? 'success' : 'info', 4000);
        } else {
            window.showToast(`${format.toUpperCase()} ready`, 'success');
        }
        pr.done('Complete!');
    } catch (e) {
        console.error(`${format} export error:`, e);
        pr.error('Error: ' + e.message);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Downloads
// ─────────────────────────────────────────────────────────────────────────────

function downloadSTL() {
    if (!window.appState?.generatedModelData) {
        window.showToast('Please generate a model first.', 'warning'); return;
    }
    _asyncExport('stl');
}

function downloadModel(format) {
    if (!window.appState?.generatedModelData) {
        window.showToast('Please generate a model first.', 'warning'); return;
    }
    _asyncExport(format);
}

function downloadCrossSection() {
    if (!window.appState?.generatedModelData) {
        window.showToast('Please generate a model first.', 'warning'); return;
    }
    const cutAxis    = document.getElementById('crossSectionAxis')?.value || 'lat';
    const cutValue   = parseFloat(document.getElementById('crossSectionValue')?.value);
    if (isNaN(cutValue)) { window.showToast('Enter a cut coordinate first', 'warning'); return; }
    const thickness  = parseFloat(document.getElementById('crossSectionThickness')?.value) || 5;
    const statusEl   = document.getElementById('crossSectionStatus');
    if (statusEl) statusEl.textContent = 'Generating…';

    const r    = window.appState?.selectedRegion || {};
    const name = _regionName();
    const md   = window.appState.generatedModelData;

    const ds = _demSettings();
    fetch('/api/export/crosssection', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
            ...ds,
            north:        ds.bbox.north, south: ds.bbox.south,
            east:         ds.bbox.east,  west:  ds.bbox.west,
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
            window.showToast('Cross-section STL ready', 'success');
        })
        .catch(e => {
            console.error('Cross-section error:', e);
            if (statusEl) statusEl.textContent = 'Error: ' + e.message;
            window.showToast('Cross-section error: ' + e.message, 'error');
        });
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window._setExportButtonsEnabled = _setExportButtonsEnabled;
window._demSettings             = _demSettings;
window.generateModelFromTab     = generateModelFromTab;
window.downloadSTL              = downloadSTL;
window.downloadModel            = downloadModel;
window.downloadCrossSection     = downloadCrossSection;
