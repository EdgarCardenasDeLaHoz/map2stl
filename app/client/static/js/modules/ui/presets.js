/**
 * modules/presets.js — Preset profiles and per-region settings persistence.
 *
 * Loaded as a plain <script> before app.js. All public functions are exposed
 * on window so app.js can call them directly.
 *
 * Public API (all on window):
 *   initPresetProfiles()                    — load localStorage presets + wire UI
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
 *   window.showToast(msg, type)        — global from app.js file-top
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
    setupAutoSave();
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
    if (!select || !select.value) { window.showToast('Select a preset first', 'warning'); return; }

    _lastAppliedPresetName = select.value;
    const preset = select.value.startsWith('user:')
        ? _userPresets[select.value.substring(5)]
        : builtInPresets[select.value];

    if (!preset) { window.showToast('Preset not found', 'error'); return; }

    applyPreset(preset);
    window.showToast('Preset loaded!', 'success');
}

// ─────────────────────────────────────────────────────────────────────────────
// Apply / collect settings
// ─────────────────────────────────────────────────────────────────────────────

// Reusable form field accessors — shared by applyPreset, getCurrentSettings,
// collectAllSettings, and applyAllSettings.
const _get    = id => document.getElementById(id);
const _flt    = (id, def) => { const v = parseFloat(_get(id)?.value); return isNaN(v) ? def : v; };
const _int    = (id, def) => { const v = parseInt(_get(id)?.value);   return isNaN(v) ? def : v; };
const _chk    = (id, def) => _get(id) ? _get(id).checked : def;
const _str    = (id, def) => _get(id)?.value || def;
const _set    = (id, val) => { const el = _get(id); if (el && val != null) el.value = val; };
const _setChk = (id, val) => { const el = _get(id); if (el && val != null) el.checked = Boolean(val); };

function applyPreset(preset) {
    const set = _set;
    const setChk = _setChk;
    if (preset.dim)        set('paramDim', preset.dim);
    if (preset.colormap)   set('demColormap', preset.colormap);
    if (preset.satScale !== undefined) {
        set('waterResolution', preset.satScale);
        window.appState.demParams.satScale = preset.satScale;
    }
    if (preset.depthScale !== undefined) {
        set('paramDepthScale', preset.depthScale);
        window.appState.demParams.depthScale = preset.depthScale;
    }
    if (preset.waterScale !== undefined) {
        set('paramWaterScale', preset.waterScale);
        window.appState.demParams.waterScale = preset.waterScale;
    }
    if (preset.subtractWater !== undefined) {
        setChk('paramSubtractWater', preset.subtractWater);
        window.appState.demParams.subtractWater = preset.subtractWater;
    }

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
        dim:           _int('paramDim', 200),
        depthScale:    _flt('paramDepthScale', 0.5),
        waterScale:    _flt('paramWaterScale', 0.05),
        colormap:      _str('demColormap', 'terrain'),
        subtractWater: _chk('paramSubtractWater', true),
        satScale:      _int('waterResolution', 500),
        elevationCurve: window.appState.activeCurvePreset || 'linear',
    };
}

/**
 * Collect all editable settings into a grouped object that mirrors
 * terrain_session.py's _DEFAULT_SETTINGS structure exactly.
 * @returns {Object}
 */
function collectAllSettings() {
    // city.layers from individual checkboxes
    const cityLayers = [];
    if (_chk('cityLayerBuildings', true))  cityLayers.push('buildings');
    if (_chk('cityLayerRoads', true))      cityLayers.push('roads');
    if (_chk('cityLayerWaterways', true))  cityLayers.push('waterways');

    const rescaleMin = _get('rescaleMin')?.value;
    const rescaleMax = _get('rescaleMax')?.value;
    return {
        dem: {
            dim:            _int('paramDim', 200),
            depth_scale:    _flt('paramDepthScale', 0.5),
            water_scale:    _flt('paramWaterScale', 0.05),
            subtract_water: _chk('paramSubtractWater', true),
            dem_source:     _str('paramDemSource', 'local'),
            show_sat:       false,
        },
        projection: {
            projection: _str('paramProjection', 'none'),
            clip_nans:  _chk('paramClipNans', true),
        },
        view: {
            colormap:               _str('demColormap', 'terrain'),
            rescale_min:            rescaleMin && rescaleMin !== '' ? parseFloat(rescaleMin) : null,
            rescale_max:            rescaleMax && rescaleMax !== '' ? parseFloat(rescaleMax) : null,
            gridlines_show:         _chk('showGridlines', true),
            gridlines_count:        _int('gridlineCount', 10),
            elevation_curve:        window.appState.activeCurvePreset || null,
            elevation_curve_points: (window.appState.curvePoints || []).map(pt => [pt.x, pt.y]),
            elevation_curve_vmin:   window.appState.curveDataVmin ?? null,
            elevation_curve_vmax:   window.appState.curveDataVmax ?? null,
        },
        water: {
            sat_scale: _int('waterResolution', 500),
            dataset:   _str('waterDataset', 'esa'),
        },
        esa: {
            sat_scale: _int('esaResolution', 200),
        },
        satellite: {
            dim: _int('paramDim', 800),
        },
        export: {
            model_height:     _flt('exportModelHeight', 30.0),
            base_height:      _flt('exportBaseHeight', 10.0),
            exaggeration:     _flt('exportExaggeration', 1.0),
            sea_level_cap:    _chk('exportSeaLevelCap', false),
            floor_val:        _flt('exportFloorVal', 0.0),
            engrave_label:    _chk('exportEngraveLabel', false),
            label_text:       _str('exportLabelText', ''),
            contours:         _chk('exportContours', false),
            contour_interval: _flt('exportContourInterval', 100.0),
            contour_style:    _str('exportContourStyle', 'engraved'),
            puzzle_z:         null,
        },
        split: {
            split_rows:     _int('splitRows', 4),
            split_cols:     _int('splitCols', 4),
            puzzle_m:       _int('splitPuzzleM', 50),
            puzzle_base_n:  _int('splitPuzzleBaseN', 10),
            border_height:  _flt('splitBorderHeight', 1.0),
            border_offset:  _flt('splitBorderOffset', 5.0),
            include_border: _chk('splitIncludeBorder', true),
        },
        city: {
            layers:             cityLayers.length ? cityLayers : ['buildings', 'roads', 'waterways'],
            simplify_tolerance: _flt('citySimplifyTolerance', 0.5),
            min_area:           _flt('cityMinArea', 5.0),
            building_scale:     _flt('cityBuildingScale', 0.5),
            road_depression_m:  _flt('cityRoadDepression', 0.0),
            water_depression_m: _flt('cityWaterOffset', -2.0),
            simplify_terrain:   true,
        },
        hydrology: {
            source:         _str('hydroSource', 'hydrorivers'),
            dim:            _int('hydroDim', 300),
            depression_m:   _flt('hydroDepressionM', -5.0),
            min_order:      _int('hydroMinOrder', 3),
            order_exponent: _flt('hydroOrderExponent', 1.5),
        },
    };
}

/**
 * Apply a grouped settings object (same structure as collectAllSettings) back
 * to all form controls and appState. Accepts both grouped and legacy flat shapes.
 * @param {Object} s
 */
function applyAllSettings(s) {
    if (!s) return;
    const set    = _set;
    const setChk = _setChk;

    // Support both grouped (new) and flat (legacy preset) shapes
    const dem  = s.dem        || s;
    const proj = s.projection || {};
    const view = s.view       || s;
    const wat  = s.water      || s;
    const exp  = s.export     || s;
    const spl  = s.split      || {};
    const city = s.city       || {};

    // dem group — new IDs: paramDepthScale, paramWaterScale, paramSubtractWater
    if (dem.dim            != null) set('paramDim',           dem.dim);
    if (dem.depth_scale    != null) set('paramDepthScale',    dem.depth_scale);
    if (dem.water_scale    != null) set('paramWaterScale',    dem.water_scale);
    if (dem.subtract_water != null) setChk('paramSubtractWater', dem.subtract_water);
    if (dem.dem_source     != null) set('paramDemSource',     dem.dem_source);

    // projection group — new: paramMaintainDimensions
    const projVal = proj.projection ?? s.projection;
    if (projVal != null) {
        set('paramProjection', projVal);
        const projDesc = document.getElementById('projectionDescription');
        if (projDesc) {
            const descs = {
                'none':       'No correction — raw lat/lon grid displayed as-is.',
                'cosine':     'Horizontal scaling by cos(latitude). Corrects east-west distances.',
                'mercator':   'Web Mercator — vertical stretching increases towards poles.',
                'lambert':    'Lambert Cylindrical Equal-Area — preserves area at the cost of shape.',
                'sinusoidal': 'Sinusoidal — each row scaled by cos(lat), centred on meridian.',
            };
            projDesc.textContent = descs[projVal] || '';
        }
    }
    if (proj.clip_nans != null) setChk('paramClipNans', proj.clip_nans);

    // view group — gridlines now use #showGridlines (VisualizationSection)
    if (view.colormap    != null) set('demColormap', view.colormap);
    if (view.rescale_min != null) set('rescaleMin',  view.rescale_min);
    if (view.rescale_max != null) set('rescaleMax',  view.rescale_max);
    if (view.gridlines_show  != null) setChk('showGridlines', view.gridlines_show);
    if (view.gridlines_count != null) set('gridlineCount',    view.gridlines_count);
    if (view.elevation_curve_points && Array.isArray(view.elevation_curve_points) && view.elevation_curve_points.length >= 2) {
        const points = view.elevation_curve_points.map(pt => ({ x: pt[0], y: pt[1] }));
        // If the saved curve was created with a different elevation range,
        // rescale points so absolute elevation anchors are preserved.
        const savedVmin = view.elevation_curve_vmin;
        const savedVmax = view.elevation_curve_vmax;
        const curVmin = window.appState.curveDataVmin;
        const curVmax = window.appState.curveDataVmax;
        if (savedVmin != null && savedVmax != null && curVmin != null && curVmax != null
            && (savedVmin !== curVmin || savedVmax !== curVmax)) {
            const oldRange = savedVmax - savedVmin;
            const newRange = curVmax - curVmin;
            if (oldRange && newRange) {
                for (const pt of points) {
                    const absElev = pt.x * oldRange + savedVmin;
                    pt.x = Math.max(0, Math.min(1, (absElev - curVmin) / newRange));
                }
            }
        }
        window.appState._applyCurveSettings?.(points, view.elevation_curve || 'custom');
    }

    // water group
    const satScaleVal = wat.sat_scale ?? s.sat_scale;
    if (satScaleVal != null) set('waterResolution', satScaleVal);
    if (wat.dataset != null) set('waterDataset', wat.dataset);

    // esa group — independent ESA land cover resolution
    const esa = s.esa || {};
    if (esa.sat_scale != null) set('esaResolution', esa.sat_scale);

    // export group — new IDs: exportModelHeight, exportBaseHeight, exportExaggeration, etc.
    if (exp.model_height     != null) set('exportModelHeight',    exp.model_height);
    if (exp.base_height      != null) set('exportBaseHeight',     exp.base_height);
    if (exp.exaggeration     != null) set('exportExaggeration',   exp.exaggeration);
    if (exp.sea_level_cap    != null) setChk('exportSeaLevelCap', exp.sea_level_cap);
    if (exp.floor_val        != null) set('exportFloorVal',       exp.floor_val);
    if (exp.engrave_label    != null) setChk('exportEngraveLabel', exp.engrave_label);
    if (exp.label_text       != null) set('exportLabelText',      exp.label_text);
    if (exp.contours         != null) setChk('exportContours',    exp.contours);
    if (exp.contour_interval != null) set('exportContourInterval', exp.contour_interval);
    if (exp.contour_style    != null) set('exportContourStyle',   exp.contour_style);

    // split group — new IDs: splitRows, splitCols, splitPuzzleM, splitPuzzleBaseN, etc.
    if (spl.split_rows     != null) set('splitRows',         spl.split_rows);
    if (spl.split_cols     != null) set('splitCols',         spl.split_cols);
    if (spl.puzzle_m       != null) set('splitPuzzleM',      spl.puzzle_m);
    if (spl.puzzle_base_n  != null) set('splitPuzzleBaseN',  spl.puzzle_base_n);
    if (spl.border_height  != null) set('splitBorderHeight', spl.border_height);
    if (spl.border_offset  != null) set('splitBorderOffset', spl.border_offset);
    if (spl.include_border != null) setChk('splitIncludeBorder', spl.include_border);

    // city group — road_depression_m → #cityRoadDepression
    if (city.simplify_tolerance != null) set('citySimplifyTolerance', city.simplify_tolerance);
    if (city.min_area           != null) set('cityMinArea',           city.min_area);
    if (city.building_scale     != null) set('cityBuildingScale',     city.building_scale);
    if (city.road_depression_m  != null) set('cityRoadDepression',    city.road_depression_m);
    if (city.water_depression_m != null) set('cityWaterOffset',       city.water_depression_m);
    if (Array.isArray(city.layers)) {
        setChk('cityLayerBuildings',  city.layers.includes('buildings'));
        setChk('cityLayerRoads',      city.layers.includes('roads'));
        setChk('cityLayerWaterways',  city.layers.includes('waterways'));
    }

    // hydrology group
    const hydro = s.hydrology || {};
    if (hydro.source         != null) set('hydroSource',        hydro.source);
    if (hydro.dim            != null) set('hydroDim',           hydro.dim);
    if (hydro.depression_m   != null) set('hydroDepressionM',   hydro.depression_m);
    if (hydro.min_order      != null) set('hydroMinOrder',      hydro.min_order);
    if (hydro.order_exponent != null) set('hydroOrderExponent',  hydro.order_exponent);
}

// ─────────────────────────────────────────────────────────────────────────────
// Region settings persistence
// ─────────────────────────────────────────────────────────────────────────────

async function saveRegionSettings() {
    const region = window.appState.selectedRegion;
    if (!region) { window.showToast('Select a region first', 'warning'); return; }
    const settings = collectAllSettings();
    const statusEl = document.getElementById('saveSettingsStatus');
    if (statusEl) statusEl.textContent = 'Saving…';
    try {
        const { error } = await window.api.regions.saveSettings(region.name, settings);
        if (!error) {
            if (statusEl) { statusEl.textContent = 'Saved ✓'; setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2000); }
            window.showToast('Settings saved for ' + region.name, 'success');
        } else {
            window.showToast('Save failed: ' + error, 'error');
            if (statusEl) statusEl.textContent = 'Error';
        }
    } catch (e) {
        window.showToast('Save failed: ' + e.message, 'error');
        if (statusEl) statusEl.textContent = 'Error';
    }
}

async function loadAndApplyRegionSettings(regionName) {
    try {
        const { data, error } = await window.api.regions.getSettings(regionName);
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
    if (!name) { window.showToast('Enter a preset name', 'warning'); return; }
    if (builtInPresets[name.toLowerCase()]) { window.showToast('Cannot overwrite built-in preset', 'error'); return; }

    _userPresets[name] = getCurrentSettings();
    try { localStorage.setItem('strm2stl_userPresets', JSON.stringify(_userPresets)); }
    catch (_) { window.showToast('Could not save preset — storage full or unavailable', 'warning'); }

    updatePresetSelect();
    const select = document.getElementById('presetSelect');
    if (select) select.value = 'user:' + name;
    hideSavePresetDialog();
    window.showToast(`Preset "${name}" saved!`, 'success');
}

function deleteSelectedPreset() {
    const select = document.getElementById('presetSelect');
    if (!select || !select.value) { window.showToast('Select a preset to delete', 'warning'); return; }
    if (!select.value.startsWith('user:')) { window.showToast('Cannot delete built-in presets', 'warning'); return; }

    const name = select.value.substring(5);
    if (confirm(`Delete preset "${name}"?`)) {
        delete _userPresets[name];
        try { localStorage.setItem('strm2stl_userPresets', JSON.stringify(_userPresets)); }
        catch (_) { window.showToast('Could not save preset — storage full or unavailable', 'warning'); }
        updatePresetSelect();
        window.showToast(`Preset "${name}" deleted`, 'info');
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Auto-save
// ─────────────────────────────────────────────────────────────────────────────

let _autoSaveTimer = null;

function setupAutoSave() {
    // Restore preference from localStorage
    const chk = document.getElementById('autoSaveEnabled');
    if (!chk) return;
    try {
        chk.checked = localStorage.getItem('strm2stl_autoSave') === 'true';
    } catch (_) {}

    chk.addEventListener('change', () => {
        try { localStorage.setItem('strm2stl_autoSave', chk.checked); } catch (_) {}
    });

    // Delegated listener on the settings container
    const container = document.getElementById('demControlsInner');
    if (!container) return;

    container.addEventListener('change', _scheduleAutoSave);
    container.addEventListener('input', _scheduleAutoSave);
}

function _scheduleAutoSave(e) {
    const chk = document.getElementById('autoSaveEnabled');
    if (!chk?.checked) return;
    // Don't auto-save when there's no region selected
    if (!window.appState?.selectedRegion) return;
    // Ignore the auto-save checkbox itself
    if (e?.target?.id === 'autoSaveEnabled') return;

    clearTimeout(_autoSaveTimer);
    _autoSaveTimer = setTimeout(async () => {
        const status = document.getElementById('saveSettingsStatus');
        try {
            await saveRegionSettings();
            if (status) {
                status.textContent = 'Auto-saved ✓';
                status.style.color = '#4CAF50';
                setTimeout(() => { status.textContent = ''; }, 2000);
            }
        } catch (err) {
            console.warn('Auto-save failed:', err);
            if (status) {
                status.textContent = 'Auto-save failed';
                status.style.color = '#f44';
                setTimeout(() => { status.textContent = ''; }, 3000);
            }
        }
    }, 3000);
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.initPresetProfiles          = initPresetProfiles;
window.collectAllSettings          = collectAllSettings;
window.applyAllSettings            = applyAllSettings;
window.saveRegionSettings          = saveRegionSettings;
window.loadAndApplyRegionSettings  = loadAndApplyRegionSettings;
window.setupAutoSave               = setupAutoSave;
