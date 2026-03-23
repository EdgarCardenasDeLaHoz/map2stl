/**
 * modules/presets.js — Preset profiles and per-region settings persistence.
 *
 * Loaded as a plain <script> before app.js. All public functions are exposed
 * on window so app.js can call them directly.
 *
 * Public API (all on window):
 *   initPresetProfiles()                    — load localStorage presets + wire UI
 *   applyPreset(preset)                     — apply a preset object to form controls
 *   getCurrentSettings()                    — return partial settings snapshot
 *   collectAllSettings()                    — return full settings snapshot
 *   applyAllSettings(s)                     — restore full settings snapshot to form
 *   saveRegionSettings()                    — POST current settings for selected region
 *   loadAndApplyRegionSettings(regionName)  — GET + apply saved region settings
 *
 * External dependencies (accessed via window / window.appState):
 *   window.api                  — api.js module
 *   window.curvePresets         — curve preset key→points map (set by app.js)
 *   window.setCurvePreset(name) — apply a curve preset (set by app.js)
 *   window.loadAllLayers()      — trigger full layer reload (set by app.js)
 *   window.appState.selectedRegion
 *   window.appState.activeCurvePreset
 *   window.appState.curvePoints
 *   window.appState._applyCurveSettings(points, presetName)
 *   showToast(msg, type)        — global from app.js file-top
 */

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────

const builtInPresets = {
    'default': {
        dim: 200, depthScale: 0.5, waterScale: 0.05,
        colormap: 'terrain', subtractWater: true, satScale: 500, elevationCurve: 'linear'
    },
    'high-detail': {
        dim: 600, depthScale: 0.3, waterScale: 0.03,
        colormap: 'terrain', subtractWater: true, satScale: 250, elevationCurve: 'linear'
    },
    'print-ready': {
        dim: 400, depthScale: 0.8, waterScale: 0.1,
        colormap: 'gray', subtractWater: true, satScale: 500, elevationCurve: 's-curve'
    },
    'mountain': {
        dim: 400, depthScale: 0.2, waterScale: 0.02,
        colormap: 'terrain', subtractWater: false, satScale: 500, elevationCurve: 'enhance-peaks'
    },
    'coastal': {
        dim: 300, depthScale: 0.7, waterScale: 0.15,
        colormap: 'viridis', subtractWater: true, satScale: 300, elevationCurve: 'compress-depths'
    }
};

let _userPresets = {};
let _lastAppliedPresetName = null;

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────

function initPresetProfiles() {
    let saved = null;
    try { saved = localStorage.getItem('strm2stl_userPresets'); } catch (_) {}
    if (saved) {
        try {
            _userPresets = JSON.parse(saved);
            updatePresetSelect();
        } catch (e) {
            console.warn('Failed to load user presets:', e);
        }
    }
    _setupPresetEventListeners();
}

function _setupPresetEventListeners() {
    const loadBtn      = document.getElementById('loadPresetBtn');
    const saveBtn      = document.getElementById('savePresetBtn');
    const deleteBtn    = document.getElementById('deletePresetBtn');
    const confirmBtn   = document.getElementById('confirmSavePresetBtn');
    const cancelBtn    = document.getElementById('cancelSavePresetBtn');
    const presetSelect = document.getElementById('presetSelect');

    if (loadBtn)    loadBtn.addEventListener('click', loadSelectedPreset);
    if (saveBtn)    saveBtn.addEventListener('click', showSavePresetDialog);
    if (deleteBtn)  deleteBtn.addEventListener('click', deleteSelectedPreset);
    if (confirmBtn) confirmBtn.addEventListener('click', saveNewPreset);
    if (cancelBtn)  cancelBtn.addEventListener('click', hideSavePresetDialog);
    if (presetSelect) presetSelect.addEventListener('dblclick', loadSelectedPreset);
}

// ─────────────────────────────────────────────────────────────────────────────
// Preset select
// ─────────────────────────────────────────────────────────────────────────────

function updatePresetSelect() {
    const select = document.getElementById('presetSelect');
    if (!select) return;

    select.innerHTML = '<option value="">-- Select Preset --</option>';

    Object.keys(builtInPresets).forEach(name => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
        select.appendChild(option);
    });

    if (Object.keys(_userPresets).length > 0) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = '📁 My Presets';
        Object.keys(_userPresets).forEach(name => {
            const option = document.createElement('option');
            option.value = 'user:' + name;
            option.textContent = name;
            optgroup.appendChild(option);
        });
        select.appendChild(optgroup);
    }
}

function loadSelectedPreset() {
    const select = document.getElementById('presetSelect');
    if (!select || !select.value) { showToast('Select a preset first', 'warning'); return; }

    _lastAppliedPresetName = select.value;
    const preset = select.value.startsWith('user:')
        ? _userPresets[select.value.substring(5)]
        : builtInPresets[select.value];

    if (!preset) { showToast('Preset not found', 'error'); return; }

    applyPreset(preset);
    showToast('Preset loaded!', 'success');
}

// ─────────────────────────────────────────────────────────────────────────────
// Apply / collect settings
// ─────────────────────────────────────────────────────────────────────────────

function applyPreset(preset) {
    if (preset.dim)                    document.getElementById('paramDim').value = preset.dim;
    if (preset.depthScale !== undefined) document.getElementById('paramDepthScale').value = preset.depthScale;
    if (preset.waterScale !== undefined) document.getElementById('paramWaterScale').value = preset.waterScale;
    if (preset.colormap)               document.getElementById('demColormap').value = preset.colormap;
    if (preset.subtractWater !== undefined) document.getElementById('paramSubtractWater').checked = preset.subtractWater;
    if (preset.satScale)               document.getElementById('paramSatScale').value = preset.satScale;

    if (preset.elevationCurve && window.curvePresets?.[preset.elevationCurve]) {
        window.setCurvePreset?.(preset.elevationCurve);
        document.querySelectorAll('.curve-presets button').forEach(b => {
            b.classList.toggle('active', b.dataset.preset === preset.elevationCurve);
        });
    }

    if (document.getElementById('autoReloadLayers')?.checked && window.appState.selectedRegion) {
        window.loadAllLayers?.();
    }
}

/**
 * Return a partial settings object for the preset save dialog.
 * @returns {{dim, depthScale, waterScale, colormap, subtractWater, satScale, elevationCurve}}
 */
function getCurrentSettings() {
    return {
        dim:          parseInt(document.getElementById('paramDim')?.value) || 200,
        depthScale:   parseFloat(document.getElementById('paramDepthScale')?.value) || 0.5,
        waterScale:   parseFloat(document.getElementById('paramWaterScale')?.value) || 0.05,
        colormap:     document.getElementById('demColormap')?.value || 'terrain',
        subtractWater: document.getElementById('paramSubtractWater')?.checked ?? true,
        satScale:     parseInt(document.getElementById('paramSatScale')?.value) || 500,
        elevationCurve: window.appState.activeCurvePreset || 'linear'
    };
}

/**
 * Collect all editable settings panel values into a flat object for persistence.
 * @returns {Object} Full settings snapshot
 */
function collectAllSettings() {
    const rescaleMin = document.getElementById('rescaleMin')?.value;
    const rescaleMax = document.getElementById('rescaleMax')?.value;
    return {
        dim:           parseInt(document.getElementById('paramDim')?.value) || 200,
        depth_scale:   parseFloat(document.getElementById('paramDepthScale')?.value) || 0.5,
        water_scale:   parseFloat(document.getElementById('paramWaterScale')?.value) || 0.05,
        height:        parseFloat(document.getElementById('paramHeight')?.value) || 10,
        base:          parseFloat(document.getElementById('paramBase')?.value) || 2,
        subtract_water: document.getElementById('paramSubtractWater')?.checked ?? true,
        sat_scale:     parseInt(document.getElementById('paramSatScale')?.value) || 500,
        colormap:      document.getElementById('demColormap')?.value || 'terrain',
        projection:    document.getElementById('paramProjection')?.value || 'none',
        rescale_min:   rescaleMin && rescaleMin !== '' ? parseFloat(rescaleMin) : null,
        rescale_max:   rescaleMax && rescaleMax !== '' ? parseFloat(rescaleMax) : null,
        gridlines_show:  document.getElementById('showGridlines')?.checked ?? false,
        gridlines_count: parseInt(document.getElementById('gridlineCount')?.value) || 5,
        elevation_curve: window.appState.activeCurvePreset || 'linear',
        elevation_curve_points: (window.appState.curvePoints || []).map(p => [p.x, p.y]),
        dem_source:    document.getElementById('paramDemSource')?.value || 'local',
    };
}

/**
 * Apply a full settings snapshot back to all form controls and restore curve state.
 * @param {Object} s — settings object as returned by collectAllSettings()
 */
function applyAllSettings(s) {
    if (!s) return;
    const set    = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.value = val; };
    const setChk = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.checked = val; };

    set('paramDim',        s.dim);
    set('paramDepthScale', s.depth_scale);
    set('paramWaterScale', s.water_scale);
    set('paramHeight',     s.height);
    set('paramBase',       s.base);
    setChk('paramSubtractWater', s.subtract_water);
    set('paramSatScale',   s.sat_scale);
    set('demColormap',     s.colormap);
    set('paramProjection', s.projection);
    if (s.rescale_min != null) set('rescaleMin', s.rescale_min);
    if (s.rescale_max != null) set('rescaleMax', s.rescale_max);
    setChk('showGridlines', s.gridlines_show);
    set('gridlineCount',   s.gridlines_count);
    set('paramDemSource',  s.dem_source);

    if (s.elevation_curve_points && Array.isArray(s.elevation_curve_points) && s.elevation_curve_points.length >= 2) {
        const points = s.elevation_curve_points.map(p => ({ x: p[0], y: p[1] }));
        window.appState._applyCurveSettings?.(points, s.elevation_curve || 'custom');
    }

    const projSelect = document.getElementById('paramProjection');
    const projDesc   = document.getElementById('projectionDescription');
    if (projSelect && projDesc) {
        const descs = {
            'none':       'No correction — raw lat/lon grid displayed as-is.',
            'cosine':     'Horizontal scaling by cos(latitude). Correct east-west distances.',
            'mercator':   'Web Mercator — vertical stretching increases towards poles.',
            'lambert':    'Lambert Cylindrical Equal-Area — preserves area at the cost of shape.',
            'sinusoidal': 'Sinusoidal — each row scaled by cos(lat), centred on meridian.'
        };
        projDesc.textContent = descs[projSelect.value] || '';
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Region settings persistence
// ─────────────────────────────────────────────────────────────────────────────

async function saveRegionSettings() {
    const region = window.appState.selectedRegion;
    if (!region) { showToast('Select a region first', 'warning'); return; }
    const settings = collectAllSettings();
    const statusEl = document.getElementById('saveSettingsStatus');
    if (statusEl) statusEl.textContent = 'Saving…';
    try {
        const { error } = await api.regions.saveSettings(region.name, settings);
        if (!error) {
            if (statusEl) { statusEl.textContent = 'Saved ✓'; setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2000); }
            showToast('Settings saved for ' + region.name, 'success');
        } else {
            showToast('Save failed: ' + error, 'error');
            if (statusEl) statusEl.textContent = 'Error';
        }
    } catch (e) {
        showToast('Save failed: ' + e.message, 'error');
        if (statusEl) statusEl.textContent = 'Error';
    }
}

async function loadAndApplyRegionSettings(regionName) {
    try {
        const { data, error } = await api.regions.getSettings(regionName);
        if (!error && data) { applyAllSettings(data.settings); return true; }
    } catch (_) { /* network error — use defaults */ }
    return false;
}

// ─────────────────────────────────────────────────────────────────────────────
// Preset save / delete dialog
// ─────────────────────────────────────────────────────────────────────────────

function showSavePresetDialog() {
    const dialog = document.getElementById('presetSaveDialog');
    const input  = document.getElementById('newPresetName');
    if (dialog) { dialog.classList.remove('hidden'); if (input) { input.value = ''; input.focus(); } }
}

function hideSavePresetDialog() {
    document.getElementById('presetSaveDialog')?.classList.add('hidden');
}

function saveNewPreset() {
    const input = document.getElementById('newPresetName');
    const name  = input?.value?.trim();
    if (!name) { showToast('Enter a preset name', 'warning'); return; }
    if (builtInPresets[name.toLowerCase()]) { showToast('Cannot overwrite built-in preset', 'error'); return; }

    _userPresets[name] = getCurrentSettings();
    try { localStorage.setItem('strm2stl_userPresets', JSON.stringify(_userPresets)); }
    catch (_) { showToast('Could not save preset — storage full or unavailable', 'warning'); }

    updatePresetSelect();
    const select = document.getElementById('presetSelect');
    if (select) select.value = 'user:' + name;
    hideSavePresetDialog();
    showToast(`Preset "${name}" saved!`, 'success');
}

function deleteSelectedPreset() {
    const select = document.getElementById('presetSelect');
    if (!select || !select.value) { showToast('Select a preset to delete', 'warning'); return; }
    if (!select.value.startsWith('user:')) { showToast('Cannot delete built-in presets', 'warning'); return; }

    const name = select.value.substring(5);
    if (confirm(`Delete preset "${name}"?`)) {
        delete _userPresets[name];
        try { localStorage.setItem('strm2stl_userPresets', JSON.stringify(_userPresets)); }
        catch (_) { showToast('Could not save preset — storage full or unavailable', 'warning'); }
        updatePresetSelect();
        showToast(`Preset "${name}" deleted`, 'info');
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.initPresetProfiles          = initPresetProfiles;
window.applyPreset                 = applyPreset;
window.getCurrentSettings          = getCurrentSettings;
window.collectAllSettings          = collectAllSettings;
window.applyAllSettings            = applyAllSettings;
window.saveRegionSettings          = saveRegionSettings;
window.loadAndApplyRegionSettings  = loadAndApplyRegionSettings;
window.updatePresetSelect          = updatePresetSelect;
