/**
 * modules/dem-merge.js — DEM merge panel and DEM source initialisation.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   _initDemSources()      — fetch DEM sources from server, populate dropdown
 *   setupMergePanel()      — wire merge panel button events
 *   _refreshPipelinePanel() — sync pipeline quick-settings from hidden param inputs
 *
 * External dependencies:
 *   window.api                       — from modules/api.js
 *   window.appState.currentDemBbox   — current DEM bbox
 *   window.appState.selectedRegion   — currently selected region
 *   window.appState.originalDemValues — DEM values before edits
 *   window.appState.lastWaterMaskData — water mask data (for _syncMergeFromCurrentLayers)
 *   window.renderDEMCanvas(...)       — from app.js
 *   applyProjection(canvas, bbox)    — global from dem-loader.js
 *   drawColorbar(vmin, vmax, cm)     — global from dem-loader.js
 *   drawHistogram(values)            — global from dem-loader.js
 *   drawGridlinesOverlay(id)         — global from dem-loader.js
 *   updateStackedLayers()            — global from stacked-layers.js
 *   showToast(msg, type)             — global from app.js file-top
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let _mergeSources = [
    { id: 'local',     label: 'Local SRTM Tiles' },
    { id: 'water_esa', label: 'Water Mask (ESA)' },
];

let _mergeLayers = [];
let _mergeLayerSeq = 0;

// ─────────────────────────────────────────────────────────────────────────────
// DEM source initialisation
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetch available DEM sources from `/api/terrain/sources` and populate
 * the `#paramDemSource` dropdown and API key warning.
 */
async function _initDemSources() {
    try {
        const { data, error } = await window.api.dem.sources();
        if (error) return;
        const select = document.getElementById('paramDemSource');
        const warning = document.getElementById('demSourceApiKeyWarning');
        if (!select) return;

        select.innerHTML = '';
        for (const src of data.sources) {
            const opt = document.createElement('option');
            opt.value = src.id;
            opt.textContent = src.label + (src.resolution_m ? ` (${src.resolution_m}m)` : '');
            if (!src.available) opt.disabled = true;
            select.appendChild(opt);
        }

        _mergeSources = [
            { id: 'local',     label: 'Local SRTM Tiles' },
            { id: 'water_esa', label: 'Water Mask (ESA WorldCover)' },
            ...data.sources
                .filter(s => s.id !== 'local')
                .map(s => ({ id: s.id, label: s.label + (s.resolution_m ? ` (${s.resolution_m}m)` : '') })),
        ];
        _renderMergePanel();

        const checkWarning = () => {
            const val = select.value;
            const needsKey = val !== 'local' && !data.opentopo_api_key_configured;
            if (warning) warning.style.display = needsKey ? 'block' : 'none';
        };
        select.addEventListener('change', checkWarning);
        checkWarning();
    } catch (_) { /* non-fatal */ }
}

// ─────────────────────────────────────────────────────────────────────────────
// Merge layer helpers
// ─────────────────────────────────────────────────────────────────────────────

function _mergeSourceOptions(selectedId) {
    return _mergeSources.map(s =>
        `<option value="${s.id}"${s.id === selectedId ? ' selected' : ''}>${s.label}</option>`
    ).join('');
}

function _createMergeLayerObj(overrides = {}) {
    return {
        id: ++_mergeLayerSeq,
        source: 'local',
        dim: 300,
        blend_mode: 'base',
        weight: 1.0,
        smooth_sigma: 0,
        clip_min: '',
        clip_max: '',
        extract_rivers: false,
        river_max_width_px: 8,
        normalize: false,
        invert: false,
        sharpen: false,
        processingOpen: false,
        ...overrides,
    };
}

function _renderMergeLayerCard(layer, idx) {
    const isBase = idx === 0;
    const blendOptions = [
        ['base',    'Base (first layer)'],
        ['blend',   'Blend (weighted)'],
        ['rivers',  'Carve Rivers / Water'],
        ['replace', 'Replace'],
        ['max',     'Max (higher wins)'],
        ['min',     'Min (lower wins)'],
    ].map(([v, l]) =>
        `<option value="${v}"${v === layer.blend_mode ? ' selected' : ''}>${l}</option>`
    ).join('');

    const procDisplay = layer.processingOpen ? '' : 'display:none;';

    return `
<div class="merge-layer-card" data-layer-id="${layer.id}">
  <div class="merge-layer-header">
    <span class="merge-layer-num">${idx + 1}</span>
    <select class="merge-src" title="Elevation or mask source">
      ${_mergeSourceOptions(layer.source)}
    </select>
    <div class="merge-layer-actions">
      <button class="merge-up" title="Move up">↑</button>
      <button class="merge-dn" title="Move down">↓</button>
      <button class="merge-rm" title="Remove layer">✕</button>
    </div>
  </div>
  <div class="merge-layer-body">
    <div class="param-group">
      <label>Resolution (px):</label>
      <input type="number" class="merge-dim" value="${layer.dim}" min="50" max="2000" step="50">
    </div>
    <div class="param-group" ${isBase ? 'style="opacity:0.4;pointer-events:none;"' : ''}>
      <label>Blend mode:</label>
      <select class="merge-mode">${blendOptions}</select>
    </div>
    <div class="param-group merge-weight-row" ${isBase || layer.blend_mode === 'base' ? 'style="opacity:0.4;pointer-events:none;"' : ''}>
      <label>Weight:</label>
      <input type="range" class="merge-weight" min="0" max="3" step="0.05" value="${layer.weight}">
      <span class="val-label merge-weight-val">${layer.weight.toFixed(2)}</span>
    </div>

    <button class="merge-processing-toggle">⚙ Processing ${layer.processingOpen ? '▲' : '▼'}</button>
    <div class="merge-processing-body" style="${procDisplay}">
      <div class="param-group">
        <label>Smooth σ:</label>
        <input type="range" class="merge-smooth" min="0" max="15" step="0.5" value="${layer.smooth_sigma}">
        <span class="val-label merge-smooth-val">${layer.smooth_sigma}</span>
      </div>
      <div class="param-group">
        <label>Clip min (m):</label>
        <input type="number" class="merge-clip-min" placeholder="none" value="${layer.clip_min}" style="width:70px;">
      </div>
      <div class="param-group">
        <label>Clip max (m):</label>
        <input type="number" class="merge-clip-max" placeholder="none" value="${layer.clip_max}" style="width:70px;">
      </div>
      <div class="checkbox-row">
        <label><input type="checkbox" class="merge-extract-rivers"${layer.extract_rivers ? ' checked' : ''}> Rivers only</label>
        <label><input type="checkbox" class="merge-sharpen"${layer.sharpen ? ' checked' : ''}> Sharpen</label>
        <label><input type="checkbox" class="merge-normalize"${layer.normalize ? ' checked' : ''}> Normalize</label>
        <label><input type="checkbox" class="merge-invert"${layer.invert ? ' checked' : ''}> Invert</label>
      </div>
      <div class="param-group merge-river-width-row" style="${layer.extract_rivers ? '' : 'display:none;'}">
        <label>Max river width (px):</label>
        <input type="number" class="merge-river-width" value="${layer.river_max_width_px}" min="1" max="100">
      </div>
    </div>
  </div>
</div>`;
}

function _renderMergePanel() {
    const list = document.getElementById('mergeLayerList');
    if (!list) return;
    list.innerHTML = _mergeLayers.map((l, i) => _renderMergeLayerCard(l, i)).join('');

    list.querySelectorAll('.merge-layer-card').forEach(card => {
        const id = parseInt(card.dataset.layerId);
        const layer = _mergeLayers.find(l => l.id === id);
        if (!layer) return;
        const idx = _mergeLayers.indexOf(layer);

        card.querySelector('.merge-src').addEventListener('change', e => {
            layer.source = e.target.value;
            _renderMergePanel();
        });
        card.querySelector('.merge-dim').addEventListener('change', e => {
            layer.dim = parseInt(e.target.value) || 300;
        });
        card.querySelector('.merge-mode').addEventListener('change', e => {
            layer.blend_mode = e.target.value;
            _renderMergePanel();
        });

        const wSlider = card.querySelector('.merge-weight');
        const wVal = card.querySelector('.merge-weight-val');
        wSlider.addEventListener('input', e => {
            layer.weight = parseFloat(e.target.value);
            if (wVal) wVal.textContent = layer.weight.toFixed(2);
        });

        card.querySelector('.merge-processing-toggle').addEventListener('click', () => {
            layer.processingOpen = !layer.processingOpen;
            _renderMergePanel();
        });

        const smSlider = card.querySelector('.merge-smooth');
        const smVal = card.querySelector('.merge-smooth-val');
        smSlider.addEventListener('input', e => {
            layer.smooth_sigma = parseFloat(e.target.value);
            if (smVal) smVal.textContent = layer.smooth_sigma;
        });

        card.querySelector('.merge-clip-min').addEventListener('change', e => { layer.clip_min = e.target.value; });
        card.querySelector('.merge-clip-max').addEventListener('change', e => { layer.clip_max = e.target.value; });

        card.querySelector('.merge-extract-rivers').addEventListener('change', e => {
            layer.extract_rivers = e.target.checked;
            _renderMergePanel();
        });
        card.querySelector('.merge-sharpen').addEventListener('change', e => { layer.sharpen = e.target.checked; });
        card.querySelector('.merge-normalize').addEventListener('change', e => { layer.normalize = e.target.checked; });
        card.querySelector('.merge-invert').addEventListener('change', e => { layer.invert = e.target.checked; });
        card.querySelector('.merge-river-width').addEventListener('change', e => {
            layer.river_max_width_px = parseInt(e.target.value) || 8;
        });

        card.querySelector('.merge-up').addEventListener('click', () => {
            if (idx > 0) {
                [_mergeLayers[idx - 1], _mergeLayers[idx]] = [_mergeLayers[idx], _mergeLayers[idx - 1]];
                _renderMergePanel();
            }
        });
        card.querySelector('.merge-dn').addEventListener('click', () => {
            if (idx < _mergeLayers.length - 1) {
                [_mergeLayers[idx + 1], _mergeLayers[idx]] = [_mergeLayers[idx], _mergeLayers[idx + 1]];
                _renderMergePanel();
            }
        });
        card.querySelector('.merge-rm').addEventListener('click', () => {
            _mergeLayers.splice(idx, 1);
            _renderMergePanel();
        });
    });
}

function _mergeLayerToSpec(layer) {
    return {
        source: layer.source,
        dim: layer.dim,
        blend_mode: layer.blend_mode,
        weight: layer.weight,
        label: layer.source,
        processing: {
            smooth_sigma: layer.smooth_sigma,
            sharpen: layer.sharpen,
            clip_min: layer.clip_min !== '' ? parseFloat(layer.clip_min) : null,
            clip_max: layer.clip_max !== '' ? parseFloat(layer.clip_max) : null,
            normalize: layer.normalize,
            invert: layer.invert,
            extract_rivers: layer.extract_rivers,
            river_max_width_px: layer.river_max_width_px,
        },
    };
}

// ─────────────────────────────────────────────────────────────────────────────
// Run merge
// ─────────────────────────────────────────────────────────────────────────────

async function runMerge(apply = false) {
    if (!_mergeLayers.length) {
        showToast('Add at least one layer first', 'warning');
        return;
    }

    const currentDemBbox = window.appState.currentDemBbox;
    const selectedRegion = window.appState.selectedRegion;

    if (!currentDemBbox && !selectedRegion) {
        showToast('Load a region first', 'warning');
        return;
    }

    const bbox = currentDemBbox || {
        north: selectedRegion.north, south: selectedRegion.south,
        east: selectedRegion.east,  west: selectedRegion.west,
    };

    const outDim = parseInt(document.getElementById('paramDim')?.value) || 300;

    const status = document.getElementById('mergeStatus');
    if (status) status.textContent = '⏳ Merging…';

    const previewBtn = document.getElementById('mergePreviewBtn');
    const applyBtn  = document.getElementById('mergeApplyBtn');
    if (previewBtn) previewBtn.disabled = true;
    if (applyBtn)  applyBtn.disabled  = true;

    try {
        const body = {
            bbox,
            dim: outDim,
            layers: _mergeLayers.map(_mergeLayerToSpec),
        };
        if (body.layers.length > 0) body.layers[0].blend_mode = 'base';

        const { data, error: mergeErr } = await window.api.dem.merge(body);
        if (mergeErr) {
            if (status) status.textContent = '❌ ' + mergeErr;
            showToast('Merge failed: ' + mergeErr, 'error');
            return;
        }
        if (data.error) {
            if (status) status.textContent = '❌ ' + data.error;
            showToast('Merge failed: ' + data.error, 'error');
            return;
        }

        if (status) {
            status.textContent = `✓ Done — ${data.dimensions?.[1]}×${data.dimensions?.[0]}px, `
                + `${data.min_elevation?.toFixed(1)}–${data.max_elevation?.toFixed(1)} m`;
        }

        let demVals = data.dem_values;
        let h = Number(data.dimensions[0]);
        let w = Number(data.dimensions[1]);
        if (Array.isArray(demVals[0])) { h = demVals.length; w = demVals[0].length; demVals = demVals.flat(); }
        const vmin = data.min_elevation ?? 0;
        const vmax = data.max_elevation ?? 1;
        const colormap = document.getElementById('demColormap')?.value || 'terrain';

        window.appState.currentDemBbox = { north: bbox.north, south: bbox.south, east: bbox.east, west: bbox.west };
        const rawCanvas = window.renderDEMCanvas(demVals, w, h, colormap, vmin, vmax);
        const canvas = applyProjection(rawCanvas, window.appState.currentDemBbox);
        const container = document.getElementById('demImage');
        container.innerHTML = '';
        container.appendChild(canvas);
        canvas.style.width = '100%'; canvas.style.height = 'auto';
        drawColorbar(vmin, vmax, colormap);
        drawHistogram(demVals);
        requestAnimationFrame(() => { drawGridlinesOverlay('demImage'); updateStackedLayers(); });

        if (apply) {
            window.appState.originalDemValues = [...demVals];
            document.getElementById('rescaleMin').value = Math.floor(vmin);
            document.getElementById('rescaleMax').value = Math.ceil(vmax);
            showToast('Merged DEM applied', 'success');
        } else {
            showToast('Merge preview rendered', 'info');
        }
    } catch (e) {
        if (status) status.textContent = '❌ ' + e.message;
        showToast('Merge error: ' + e.message, 'error');
    } finally {
        if (previewBtn) previewBtn.disabled = false;
        if (applyBtn)  applyBtn.disabled  = false;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Panel sync helpers
// ─────────────────────────────────────────────────────────────────────────────

function _syncMergeFromCurrentLayers() {
    const source = document.getElementById('paramDemSource')?.value || 'local';
    const dim    = parseInt(document.getElementById('paramDim')?.value) || 300;
    _mergeLayers = [_createMergeLayerObj({ source, dim, blend_mode: 'base' })];
    if (window.appState.lastWaterMaskData) {
        _mergeLayers.push(_createMergeLayerObj({ source: 'water_esa', dim, blend_mode: 'rivers' }));
    }
    _renderMergePanel();
}

function _refreshPipelinePanel() {
    const get = id => document.getElementById(id);
    const pairs = [
        ['pipelineDim',         'paramDim'],
        ['pipelineDepthScale',  'paramDepthScale'],
        ['pipelineWaterScale',  'paramWaterScale'],
        ['pipelineHeight',      'paramHeight'],
        ['pipelineBase',        'paramBase'],
        ['pipelineSatScale',    'paramSatScale'],
    ];
    for (const [pipeId, paramId] of pairs) {
        const pEl = get(pipeId); const hEl = get(paramId);
        if (pEl && hEl && hEl.value) pEl.value = hEl.value;
    }
    const swPipe  = get('pipelineSubtractWater');
    const swParam = get('paramSubtractWater');
    if (swPipe && swParam) swPipe.checked = swParam.value !== 'false';
    const srcPipe  = get('pipelineSource');
    const srcParam = get('paramDemSource');
    if (srcPipe && srcParam) srcPipe.value = srcParam.value;
}

// ─────────────────────────────────────────────────────────────────────────────
// Setup
// ─────────────────────────────────────────────────────────────────────────────

function setupMergePanel() {
    const get = id => document.getElementById(id);
    const pipelineBindings = [
        ['pipelineDim',        'paramDim',        null],
        ['pipelineDepthScale', 'paramDepthScale', 'modelDepthScale'],
        ['pipelineWaterScale', 'paramWaterScale', 'modelWaterScale'],
        ['pipelineHeight',     'paramHeight',     null],
        ['pipelineBase',       'paramBase',       'modelBaseHeight'],
        ['pipelineSatScale',   'paramSatScale',   null],
    ];
    for (const [pipeId, paramId, mirrorId] of pipelineBindings) {
        const pEl = get(pipeId);
        if (!pEl) continue;
        pEl.addEventListener('change', () => {
            const h = get(paramId); if (h) h.value = pEl.value;
            if (mirrorId) { const m = get(mirrorId); if (m) m.value = pEl.value; }
        });
    }
    const swPipe = get('pipelineSubtractWater');
    if (swPipe) {
        swPipe.addEventListener('change', () => {
            const h = get('paramSubtractWater'); if (h) h.value = String(swPipe.checked);
            const m = get('modelSubtractWater'); if (m) m.checked = swPipe.checked;
        });
    }
    const srcPipe = get('pipelineSource');
    if (srcPipe) {
        srcPipe.addEventListener('change', () => {
            const h = get('paramDemSource'); if (h) h.value = srcPipe.value;
        });
    }

    get('pipelineReloadBtn')?.addEventListener('click', () => {
        const pDim = get('pipelineDim'); const hDim = get('paramDim');
        if (pDim && hDim) hDim.value = pDim.value;
        const pSrc = get('pipelineSource'); const hSrc = get('paramDemSource');
        if (pSrc && hSrc) hSrc.value = pSrc.value;
        if (typeof window.loadDEM === 'function') window.loadDEM();
    });

    get('mergeSyncBtn')?.addEventListener('click', _syncMergeFromCurrentLayers);

    if (_mergeLayers.length === 0) _syncMergeFromCurrentLayers();

    get('mergeAddLayerBtn')?.addEventListener('click', () => {
        const mode = _mergeLayers.length === 0 ? 'base' : 'blend';
        _mergeLayers.push(_createMergeLayerObj({ blend_mode: mode }));
        _renderMergePanel();
    });

    get('mergePreviewBtn')?.addEventListener('click', () => runMerge(false));
    get('mergeApplyBtn')?.addEventListener('click', () => runMerge(true));
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window._initDemSources      = _initDemSources;
window._refreshPipelinePanel = _refreshPipelinePanel;
window.setupMergePanel       = setupMergePanel;
