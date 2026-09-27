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
 *   cancelExport()                    — abandon the in-flight export (client-side)
 *
 * External dependencies:
 *   window.appState.lastDemData
 *   window.appState.selectedRegion
 *   window.appState.generatedModelData  (written here, read by _updateWorkflowStepper)
 *   window.appState._updateWorkflowStepper()
 *   window.appState.osmCityData, cityHeightOverrides  (City Model layer_data)
 *   showLoading(el, msg), hideLoading(el)   — file-top globals in app.js
 *   window.showToast(msg, type)                    — file-top global in app.js
 */

import { buildingsWithOverrides, hasOverrides } from '../layers/building-heights.js';

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function _setExportButtonsEnabled(enabled) {
    const ids = ['downloadSTLBtn', 'downloadOBJBtn', 'download3MFBtn',
        'exportCityBtn', 'downloadCrossSectionBtn', 'exportPuzzle3MFBtn'];
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
            if (this.bar) {
                this.bar.style.width = pct + '%';
                // Clear any red left by a previous failed export.
                this.bar.style.backgroundColor = '';
            }
            if (this.text) this.text.textContent = msg;
        },
        done(msg) { this.set(100, msg); setTimeout(() => { if (this.wrap) this.wrap.classList.add('hidden'); }, 1000); },
        /** Show a failure and leave it on screen.
         *
         * This used to auto-hide after 2s, which meant a failed export could
         * erase its own only explanation before the user looked back at the
         * tab. The bar now stays until the next export starts (`set()` clears
         * the red), and the caller also raises a toast.
         */
        error(msg) {
            if (this.wrap) this.wrap.classList.remove('hidden');
            if (this.text) this.text.textContent = msg;
            if (this.bar) { this.bar.style.width = '100%'; this.bar.style.backgroundColor = '#e74c3c'; }
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
 *
 * These have to hash to the same cache key the server used when it *wrote*
 * the DEM (see app/server/core/dem_cache.py), so the settings must describe
 * the DEM that is currently loaded — not whatever the form says right now.
 * loadDEM() snapshots its own request into appState.lastDemRequest for
 * exactly this purpose; the DOM read below is only a fallback for DEMs
 * loaded before that snapshot existed, and its defaults are kept in step
 * with DEM_SETTING_DEFAULTS on the server.
 */
function _demSettings() {
    const snapshot = window.appState?.lastDemRequest;
    const bbox = snapshot?.bbox || window.appState?.currentDemBbox
        || window.appState?.selectedRegion || {};
    const p = window.appState?.demParams || {};
    const proj = window.getProjectionParams();
    const settings = {
        // Preferred by the server; bbox + dem below are the fallback for a DEM
        // loaded before handles existed, and are still needed by composite exports.
        dem_id: snapshot?.dem_id,
        bbox: {
            north: bbox.north, south: bbox.south,
            east: bbox.east, west: bbox.west,
        },
        dem: snapshot?.dem ? { ...snapshot.dem } : {
            dim: parseInt(document.getElementById('paramDim')?.value, 10) || 600,
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
    // Values edited in the browser (elevation curve, imported-mesh blend) exist
    // nowhere on the server, so the only way the model can carry them is to
    // ship the array. That array already contains any applied composite, so
    // it wins over the layer spec below.
    const edited = window.appState?.lastDemData;
    if (window.appState?.demValuesEdited && edited?.values?.length) {
        settings.dem_values = Array.from(edited.values);
        settings.height = edited.height;
        settings.width = edited.width;
        return settings;
    }

    // Composite DEM panel (composite-dem.js). Preferred path: send the layer
    // spec and let the server add and subtract the layers itself, so the mesh
    // and the export are built from the server's arithmetic rather than from
    // an array the browser shipped. The inline values are the fallback for a
    // channel the server cannot build yet (land cover, vegetation, trails) —
    // exporting the spec then would silently drop it.
    if (window.appState?._newCompositeApplied) {
        const spec = window.appState?.compositeLayerSpec
            || window.buildCompositeLayerSpec?.();
        if (spec?.layers?.length && !spec.unsupported?.length) {
            settings.composite_layers = spec.layers;
            settings.composite_dim = settings.dem.dim;
        } else {
            if (spec?.unsupported?.length) {
                console.info('[export] composite computed in-browser — no server '
                    + `source for: ${spec.unsupported.join(', ')}`);
            }
            const dem = window.appState?.lastDemData;
            if (dem?.values?.length) {
                settings.dem_values = Array.from(dem.values);
                settings.height = dem.height;
                settings.width = dem.width;
            }
        }
    }

    // Legacy merge panel: if the user has configured + applied a composite
    // there, send the spec so the server rebuilds the same merged DEM.
    const compositeSpec = window.getActiveCompositeSpec?.();
    if (compositeSpec && !settings.composite_layers) {
        settings.composite_layers = compositeSpec;
        settings.composite_dim = settings.dem.dim;
    }
    return settings;
}

/**
 * The export request: exactly what the last successful preview was built
 * from. This used to take four fields from the preview and the rest from the
 * live form, so a file could mix two different models.
 */
function _exportParams() {
    const md = window.appState?.generatedModelData;
    return {
        ...(md.demSettings || _demSettings()),
        ...(md.buildParams || window._readBuildParams?.() || {}),
        name: _regionName(),
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

// A running export can only be abandoned client-side — the server task has no
// cancel route — so "cancel" means: stop polling, stop waiting, and let the
// orphaned task expire under the normal TTL sweep.
let _exportAbort = null;

/** Stop waiting on the in-flight export. Wired to the progress bar's ✕. */
function cancelExport() {
    _exportAbort?.abort();
}

function _setCancelVisible(visible) {
    document.getElementById('modelProgressCancel')
        ?.classList.toggle('hidden', !visible);
}

/**
 * City Model: terrain + every enabled OSM layer merged server-side into one solid
 * (city2stl/city_model.py). Layer rows come from the City Model table in
 * ModelContainer.vue; value is a height multiplier for extruded layers and
 * mm above/below the terrain for surface layers.
 */
function _cityLayerSettings() {
    const layers = {};
    document.querySelectorAll('[id^="cityLayer_"][id$="_enabled"]').forEach((box) => {
        const id = box.id.slice('cityLayer_'.length, -'_enabled'.length);
        const mode = document.getElementById(`cityLayer_${id}_mode`)?.value || 'raised';
        const value = parseFloat(document.getElementById(`cityLayer_${id}_value`)?.value);
        const style = { enabled: box.checked, mode };
        if (Number.isFinite(value)) {
            if (mode === 'extrude') style.height_scale = value;
            else style.offset_mm = value;
        }
        layers[id] = style;
    });
    return layers;
}

/** Terrain jigsaw puzzle: a .zip of OBJ pieces + a 3MF (app/server/core/puzzle.py). */
function exportPuzzle() {
    const num = (id, fallback) => parseFloat(document.getElementById(id)?.value) || fallback;
    const cols = parseInt(document.getElementById('splitCols')?.value, 10) || 3;
    const rows = parseInt(document.getElementById('splitRows')?.value, 10) || 3;
    if (cols * rows > 64) {
        window.showToast?.('Too many pieces (max 64 total)', 'warning');
        return;
    }
    return _asyncExport('puzzle', {
        split_cols: cols,
        split_rows: rows,
        knob_width_mm: num('splitKnobWidth', 20),
        knob_depth_mm: num('splitKnobDepth', 8),
        clearance_mm: num('splitClearance', 0.3),
    }, `${_regionName()}_puzzle.zip`);
}

function exportCityModel() {
    if (!window.appState?.lastDemRequest?.dem_id) {
        window.showToast?.('Load the DEM first', 'warning');
        return;
    }
    const extra = { layers: _cityLayerSettings() };
    // Heights edited in the Buildings panel exist only here; send the edited
    // buildings so the server uses them in place of its cached OSM copy.
    const overrides = window.appState?.cityHeightOverrides;
    const buildings = window.appState?.osmCityData?.buildings?.features;
    if (extra.layers.buildings?.enabled && buildings?.length && hasOverrides(overrides)) {
        extra.layer_data = { buildings: buildingsWithOverrides(buildings, overrides) };
    }
    if (document.getElementById('cityPuzzleEnabled')?.checked) {
        extra.puzzle = {
            piece_mm: parseFloat(document.getElementById('cityPieceMm')?.value) || 200,
            knob_width_mm: parseFloat(document.getElementById('splitKnobWidth')?.value) || 20,
            knob_depth_mm: parseFloat(document.getElementById('splitKnobDepth')?.value) || 8,
            clearance_mm: parseFloat(document.getElementById('splitClearance')?.value) || 0.3,
        };
    }
    return _asyncExport('city', extra, `${_regionName()}_city.zip`);
}

// Upper bound on how long we will poll before giving up. A stuck task used to
// spin the 250 ms poll loop forever with no way out; the bound turns that into
// a visible error the user can act on.
const _EXPORT_POLL_TIMEOUT_MS = 10 * 60 * 1000;

async function _asyncExport(format, extra = {}, fileName = null) {
    const pr = _progressEl();
    const name = _regionName();

    _exportAbort?.abort();          // supersede any earlier export
    const abort = new AbortController();
    _exportAbort = abort;
    const startedAt = Date.now();

    pr.set(0, `Starting ${format.toUpperCase()} export...`);
    _setCancelVisible(true);

    try {
        // 1. Start the export task
        const startResp = await fetch('/api/export/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ format, ..._exportParams(), ...extra }),
            signal: abort.signal,
        });
        if (!startResp.ok) {
            const err = await startResp.json().catch(() => ({}));
            throw new Error(err.error || err.detail || `Failed to start export (HTTP ${startResp.status})`);
        }
        const { task_id } = await startResp.json();

        // 2. Poll for progress
        let status = { status: 'running', progress: 0, message: 'Starting...' };
        while (status.status === 'running') {
            await new Promise(r => setTimeout(r, 250));
            if (abort.signal.aborted) throw new DOMException('Cancelled', 'AbortError');
            if (Date.now() - startedAt > _EXPORT_POLL_TIMEOUT_MS) {
                throw new Error(
                    `Export timed out after ${Math.round(_EXPORT_POLL_TIMEOUT_MS / 60000)} minutes. ` +
                    'Try a smaller resolution, or check server.log.');
            }
            const pollResp = await fetch(
                `/api/export/status/${encodeURIComponent(task_id)}`, { signal: abort.signal });
            if (!pollResp.ok) throw new Error('Lost connection to export task');
            status = await pollResp.json();
            pr.set(status.progress, status.message);
        }

        if (status.status === 'error') {
            throw new Error(status.message || 'Export failed');
        }

        // 3. Download the result
        pr.set(98, `Downloading ${format.toUpperCase()}...`);
        const dlResp = await fetch(
            `/api/export/download/${encodeURIComponent(task_id)}`, { signal: abort.signal });
        if (!dlResp.ok) throw new Error('Download failed');

        const blob = await dlResp.blob();
        // Grab extra headers for STL quality info
        const isWatertight = dlResp.headers.get('X-Watertight') === 'true';
        const faceCount = dlResp.headers.get('X-Face-Count');

        _triggerDownload(blob, fileName || `${name}.${format}`);

        // All three formats go through the same repair and send these headers.
        if (faceCount) {
            const faces = `${parseInt(faceCount).toLocaleString()} faces`;
            const quality = isWatertight ? 'watertight' : 'NOT watertight';
            window.showToast(`${format.toUpperCase()} ready - ${faces}, ${quality}`,
                isWatertight ? 'success' : 'warning', 5000);
        } else {
            window.showToast(`${format.toUpperCase()} ready`, 'success');
        }
        pr.done('Complete!');
    } catch (e) {
        if (e.name === 'AbortError') {
            pr.set(0, 'Export cancelled.');
            window.showToast?.('Export cancelled', 'info');
            return;
        }
        console.error(`${format} export error:`, e);
        // Both surfaces: the bar (persistent, next to the button that failed)
        // and a toast (visible even if the user has switched tabs).
        pr.error(`${format.toUpperCase()} export failed: ${e.message}`);
        window.showToast?.(`${format.toUpperCase()} export failed: ${e.message}`, 'error', 6000);
    } finally {
        _setCancelVisible(false);
        if (_exportAbort === abort) _exportAbort = null;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Downloads
// ─────────────────────────────────────────────────────────────────────────────

function downloadSTL() {
    if (!window.appState?.generatedModelData) {
        window.showToast('Load a DEM first - the 3D preview builds from it, and export needs that preview.', 'warning'); return;
    }
    _asyncExport('stl');
}

function downloadModel(format) {
    if (!window.appState?.generatedModelData) {
        window.showToast('Load a DEM first - the 3D preview builds from it, and export needs that preview.', 'warning'); return;
    }
    _asyncExport(format);
}

function downloadCrossSection() {
    if (!window.appState?.generatedModelData) {
        window.showToast('Load a DEM first - the 3D preview builds from it, and export needs that preview.', 'warning'); return;
    }
    const cutAxis = document.getElementById('crossSectionAxis')?.value || 'lat';
    const cutValue = parseFloat(document.getElementById('crossSectionValue')?.value);
    if (isNaN(cutValue)) { window.showToast('Enter a cut coordinate first', 'warning'); return; }
    const thickness = parseFloat(document.getElementById('crossSectionThickness')?.value) || 5;
    const statusEl = document.getElementById('crossSectionStatus');
    if (statusEl) statusEl.textContent = 'Generating…';

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
            model_height: md.modelHeight,
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
window.exportCityModel = exportCityModel;
window.exportPuzzle = exportPuzzle;
window.cancelExport = cancelExport;
