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
 *   window.applyProjection(canvas, bbox)    — global from dem-loader.js
 *   window.drawColorbar(vmin, vmax, cm)     — global from dem-loader.js
 *   window.drawHistogram(values)            — global from dem-loader.js
 *   window.drawGridlinesOverlay(id)         — global from dem-loader.js
 *   updateStackedLayers()            — global from stacked-layers.js
 *   window.showToast(msg, type)             — global from app.js file-top
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module State
// ─────────────────────────────────────────────────────────────────────────────
const state = {
    mergeSources: [
        { id: 'local', label: 'Local SRTM Tiles' },
        { id: 'water_esa', label: 'Water Mask (ESA)' },
    ],
    mergeLayers: [],
    mergeLayerSeq: 0,
};

// ─────────────────────────────────────────────────────────────────────────────
// Utility Functions
// ─────────────────────────────────────────────────────────────────────────────
function createMergeLayer(overrides = {}) {
    return {
        id: ++state.mergeLayerSeq,
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

function renderMergePanel() {
    const list = document.getElementById('mergeLayerList');
    if (!list) return;
    list.innerHTML = state.mergeLayers.map((layer, idx) => renderMergeLayerCard(layer, idx)).join('');

    // Attach event listeners for each layer card
    list.querySelectorAll('.merge-layer-card').forEach((card) => {
        const id = parseInt(card.dataset.layerId);
        const layer = state.mergeLayers.find((l) => l.id === id);
        if (!layer) return;
        attachLayerEventListeners(card, layer);
    });
}

function attachLayerEventListeners(card, layer) {
    card.querySelector('.merge-src').addEventListener('change', (e) => {
        layer.source = e.target.value;
        renderMergePanel();
    });
    card.querySelector('.merge-dim').addEventListener('change', (e) => {
        layer.dim = parseInt(e.target.value) || 300;
    });
    // ... Attach other event listeners as needed
}

function renderMergeLayerCard(layer, idx) {
    const isBase = idx === 0;
    const blendOptions = [
        ['base', 'Base (first layer)'],
        ['blend', 'Blend (weighted)'],
        ['rivers', 'Carve Rivers / Water'],
        ['replace', 'Replace'],
        ['max', 'Max (higher wins)'],
        ['min', 'Min (lower wins)'],
    ]
        .map(([v, l]) => `<option value="${v}"${v === layer.blend_mode ? ' selected' : ''}>${l}</option>`)
        .join('');

    return `
<div class="merge-layer-card" data-layer-id="${layer.id}">
  <div class="merge-layer-header">
    <span class="merge-layer-num">${idx + 1}</span>
    <select class="merge-src" title="Elevation or mask source">
      ${state.mergeSources.map((s) => `<option value="${s.id}"${s.id === layer.source ? ' selected' : ''}>${s.label}</option>`).join('')}
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
    <!-- Additional UI elements -->
  </div>
</div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// DEM Sources
// ─────────────────────────────────────────────────────────────────────────────

async function _initDemSources() {
    const { data, error } = await window.api.dem.sources();
    if (error || !data) return;
    const extra = (data.sources || data).filter(s => s.id !== 'local' && s.id !== 'water_esa');
    for (const s of extra) {
        if (!state.mergeSources.find(m => m.id === s.id)) {
            state.mergeSources.push({ id: s.id, label: s.label || s.id });
        }
    }
    // Update the hidden paramDemSource select if present
    const sel = document.getElementById('paramDemSource');
    if (sel && sel.options.length <= 1) {
        for (const s of state.mergeSources) {
            if (!Array.from(sel.options).find(o => o.value === s.id)) {
                const opt = document.createElement('option');
                opt.value = s.id; opt.textContent = s.label;
                sel.appendChild(opt);
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Merge Logic
// ─────────────────────────────────────────────────────────────────────────────

function _mergeLayerToSpec(layer) {
    return {
        source: layer.source,
        dim: layer.dim,
        blend_mode: layer.blend_mode,
        weight: layer.weight,
        processing: {
            clip_min: layer.clip_min !== '' ? parseFloat(layer.clip_min) : null,
            clip_max: layer.clip_max !== '' ? parseFloat(layer.clip_max) : null,
            smooth_sigma: layer.smooth_sigma,
            sharpen: layer.sharpen,
            normalize: layer.normalize,
            invert: layer.invert,
            extract_rivers: layer.extract_rivers,
            river_max_width_px: layer.river_max_width_px,
        },
    };
}

async function runMerge(apply = false) {
    if (!state.mergeLayers.length) {
        window.showToast('Add at least one layer first', 'warning');
        return;
    }

    const currentDemBbox = window.appState.currentDemBbox;
    const selectedRegion = window.appState.selectedRegion;

    if (!currentDemBbox && !selectedRegion) {
        window.showToast('Load a region first', 'warning');
        return;
    }

    const bbox = currentDemBbox || {
        north: selectedRegion.north, south: selectedRegion.south,
        east: selectedRegion.east, west: selectedRegion.west,
    };

    const outDim = parseInt(document.getElementById('paramDim')?.value) || 300;

    const status = document.getElementById('mergeStatus');
    if (status) status.textContent = '⏳ Merging…';

    const previewBtn = document.getElementById('mergePreviewBtn');
    const applyBtn = document.getElementById('mergeApplyBtn');
    if (previewBtn) previewBtn.disabled = true;
    if (applyBtn) applyBtn.disabled = true;

    try {
        const body = {
            bbox,
            dim: outDim,
            layers: state.mergeLayers.map(_mergeLayerToSpec),
        };
        if (body.layers.length > 0) body.layers[0].blend_mode = 'base';

        const { data, error: mergeErr } = await window.api.dem.merge(body);
        if (mergeErr) {
            if (status) status.textContent = '❌ ' + mergeErr;
            window.showToast('Merge failed: ' + mergeErr, 'error');
            return;
        }
        if (data.error) {
            if (status) status.textContent = '❌ ' + data.error;
            window.showToast('Merge failed: ' + data.error, 'error');
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
        const canvas = window.applyProjection(rawCanvas, window.appState.currentDemBbox);
        const container = document.getElementById('demImage');
        container.innerHTML = '';
        container.appendChild(canvas);
        canvas.style.width = '100%'; canvas.style.height = 'auto';
        window.drawColorbar(vmin, vmax, colormap);
        window.drawHistogram(demVals);
        requestAnimationFrame(() => { window.drawGridlinesOverlay('demImage'); window['events']?.emit(window.EV?.STACKED_UPDATE); });

        if (apply) {
            window.appState.originalDemValues = [...demVals];
            document.getElementById('rescaleMin').value = Math.floor(vmin);
            document.getElementById('rescaleMax').value = Math.ceil(vmax);
            window.showToast('Merged DEM applied', 'success');
        } else {
            window.showToast('Merge preview rendered', 'info');
        }
    } catch (e) {
        if (status) status.textContent = '❌ ' + e.message;
        window.showToast('Merge error: ' + e.message, 'error');
    } finally {
        if (previewBtn) previewBtn.disabled = false;
        if (applyBtn) applyBtn.disabled = false;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Panel sync helpers
// ─────────────────────────────────────────────────────────────────────────────

function _syncMergeFromCurrentLayers() {
    const source = document.getElementById('paramDemSource')?.value || 'local';
    const dim = parseInt(document.getElementById('paramDim')?.value) || 300;
    state.mergeLayers = [createMergeLayer({ source, dim, blend_mode: 'base' })];
    if (window.appState.lastWaterMaskData) {
        state.mergeLayers.push(createMergeLayer({ source: 'water_esa', dim, blend_mode: 'rivers' }));
    }
    renderMergePanel();
}

function _refreshPipelinePanel() {
    const get = id => document.getElementById(id);
    const pairs = [
        ['pipelineDim', 'paramDim'],
        ['pipelineDepthScale', 'paramDepthScale'],
        ['pipelineWaterScale', 'paramWaterScale'],
        ['pipelineHeight', 'paramHeight'],
        ['pipelineBase', 'paramBase'],
        ['pipelineSatScale', 'paramSatScale'],
    ];
    for (const [pipeId, paramId] of pairs) {
        const pEl = get(pipeId); const hEl = get(paramId);
        if (pEl && hEl && hEl.value) pEl.value = hEl.value;
    }
    const swPipe = get('pipelineSubtractWater');
    const swParam = get('paramSubtractWater');
    if (swPipe && swParam) swPipe.checked = swParam.value !== 'false';
    const srcPipe = get('pipelineSource');
    const srcParam = get('paramDemSource');
    if (srcPipe && srcParam) srcPipe.value = srcParam.value;
}

// ─────────────────────────────────────────────────────────────────────────────
// Setup
// ─────────────────────────────────────────────────────────────────────────────

function setupMergePanel() {
    const get = id => document.getElementById(id);
    const pipelineBindings = [
        ['pipelineDim', 'paramDim', null],
        ['pipelineDepthScale', 'paramDepthScale', 'modelDepthScale'],
        ['pipelineWaterScale', 'paramWaterScale', 'modelWaterScale'],
        ['pipelineHeight', 'paramHeight', null],
        ['pipelineBase', 'paramBase', 'modelBaseHeight'],
        ['pipelineSatScale', 'paramSatScale', null],
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

    if (state.mergeLayers.length === 0) _syncMergeFromCurrentLayers();

    get('mergeAddLayerBtn')?.addEventListener('click', () => {
        const mode = state.mergeLayers.length === 0 ? 'base' : 'blend';
        state.mergeLayers.push(createMergeLayer({ blend_mode: mode }));
        renderMergePanel();
    });

    get('mergePreviewBtn')?.addEventListener('click', () => runMerge(false));
    get('mergeApplyBtn')?.addEventListener('click', () => runMerge(true));
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window._initDemSources = _initDemSources;
window._refreshPipelinePanel = _refreshPipelinePanel;
window.setupMergePanel = setupMergePanel;
window._syncMergeFromCurrentLayers = _syncMergeFromCurrentLayers;
