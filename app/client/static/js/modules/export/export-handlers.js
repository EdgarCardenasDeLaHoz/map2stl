/**
 * modules/export-handlers.js — Model generation and file export handlers.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   _setExportButtonsEnabled(enabled) — toggle export button states
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
        el.disabled = !enabled;
        el.style.opacity = enabled ? '' : '0.4';
        el.style.cursor = enabled ? '' : 'not-allowed';
    }
    const emptyEl = document.getElementById('modelEmptyState');
    if (emptyEl) emptyEl.classList.toggle('hidden', enabled);
}

function _triggerDownload(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    window.URL.revokeObjectURL(url); a.remove();
}

function _progressEl() {
    return {
        wrap: document.getElementById('modelProgress'),
        bar: document.getElementById('modelProgressBar'),
        text: document.getElementById('modelProgressText'),
        set(pct, msg) {
            if (this.wrap) this.wrap.classList.remove('hidden');
            if (this.bar) this.bar.style.width = pct + '%';
            if (this.text) this.text.textContent = msg;
        },
        done(msg) { this.set(100, msg); setTimeout(() => { if (this.wrap) this.wrap.classList.add('hidden'); }, 1000); },
        error(msg) {
            if (this.text) this.text.textContent = msg;
            if (this.bar) this.bar.style.backgroundColor = '#e74c3c';
            setTimeout(() => {
                if (this.wrap) this.wrap.classList.add('hidden');
                if (this.bar) this.bar.style.backgroundColor = '';
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
    const proj = window.getProjectionParams();
    const settings = {
        bbox: {
            north: bbox.north, south: bbox.south,
            east: bbox.east, west: bbox.west,
        },
        dem: {
            dim: parseInt(document.getElementById('paramDim')?.value) || 200,
            dem_source: document.getElementById('paramDemSource')?.value || 'local',
            projection: proj.projection,
            depth_scale: p.depthScale ?? 0.5,
            water_scale: p.waterScale ?? 0.05,
            subtract_water: p.subtractWater ?? true,
            maintain_dimensions: proj.maintainDimensions,
            clip_nans: proj.clipValidRegion,
            show_sat: false,
        },
    };
    // Composite DEM panel (composite-dem.js): applyCompositeToDem() already
    // computed the merged values client-side and wrote them into
    // lastDemData — send them inline so export uses exactly what the
    // preview shows, instead of resolve_dem_from_cache() re-reading the
    // plain (non-composite) DEM from the server-side cache.
    if (window.appState?._newCompositeApplied) {
        const dem = window.appState?.lastDemData;
        if (dem?.values?.length) {
            settings.dem_values = Array.from(dem.values);
            settings.height = dem.height;
            settings.width = dem.width;
        }
    }

    // Legacy merge panel: if the user has configured + applied a composite
    // there, send the spec so the server rebuilds the same merged DEM.
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
        model_height: md.modelHeight,
        base_height: md.baseHeight,
        exaggeration: md.exaggeration,
        mm_per_pixel: md.mmPerPixel,
        sea_level_cap: document.getElementById('exportSeaLevelCap')?.checked || false,
        engrave_label: document.getElementById('exportEngraveLabel')?.checked || false,
        label_text: document.getElementById('exportLabelText')?.value || window.appState?.selectedRegion?.name || _regionName(),
        contours: document.getElementById('exportContours')?.checked || false,
        contour_interval: parseInt(document.getElementById('exportContourInterval')?.value) || 100,
        contour_style: document.getElementById('exportContourStyle')?.value || 'engraved',
        name: _regionName()
    };
}

// Note: the 3D model is built by the Extrude-tab auto-rebuild preview
// (`model-viewer.js:previewModelIn3D`), which sets
// `appState.generatedModelData` and enables the export buttons. The old
// `generateModelFromTab()` here referenced a removed `#modelResolution`
// element (it always threw) and was never wired to any button — removed.

// ─────────────────────────────────────────────────────────────────────────────
// Async export helper (start → poll → download)
// ─────────────────────────────────────────────────────────────────────────────

async function _asyncExport(format) {
    const pr = _progressEl();
    const name = _regionName();
    pr.set(0, `Starting ${format.toUpperCase()} export...`);

    try {
        // 1. Start the export task
        const startResp = await fetch('/api/export/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ format, ..._exportParams() })
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
        const faceCount = dlResp.headers.get('X-Face-Count');

        _triggerDownload(blob, `${name}.${format}`);

        if (format === 'stl' && faceCount) {
            const faces = `${parseInt(faceCount).toLocaleString()} faces`;
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
    const cutAxis = document.getElementById('crossSectionAxis')?.value || 'lat';
    const cutValue = parseFloat(document.getElementById('crossSectionValue')?.value);
    if (isNaN(cutValue)) { window.showToast('Enter a cut coordinate first', 'warning'); return; }
    const thickness = parseFloat(document.getElementById('crossSectionThickness')?.value) || 5;
    const statusEl = document.getElementById('crossSectionStatus');
    if (statusEl) statusEl.textContent = 'Generating…';

    const r = window.appState?.selectedRegion || {};
    const name = _regionName();
    const md = window.appState.generatedModelData;

    const ds = _demSettings();
    fetch('/api/export/crosssection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            ...ds,
            north: ds.bbox.north, south: ds.bbox.south,
            east: ds.bbox.east, west: ds.bbox.west,
            cut_axis: cutAxis,
            cut_value: cutValue,
            model_height: md.resolution,
            base_height: md.baseHeight,
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
            const axis = cutAxis === 'lat' ? `lat${cutValue.toFixed(4)}` : `lon${cutValue.toFixed(4)}`;
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
window._demSettings = _demSettings;
window.downloadSTL = downloadSTL;
window.downloadModel = downloadModel;
window.downloadCrossSection = downloadCrossSection;
