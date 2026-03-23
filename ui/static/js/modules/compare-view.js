/**
 * modules/compare-view.js — Side-by-side region DEM comparison panel.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   initCompareMode()               — wire layer selects (idempotent)
 *   renderCompareLayer(side)        — copy a layer canvas into the compare panel
 *   updateCompareCanvases()         — init + render both sides
 *   loadCompareRegion(side)         — async: fetch DEM for one side
 *   applyCompareColormap(side)      — reload with new colormap
 *   updateCompareExagLabel(side)    — update exaggeration label + reload
 *   updateRegionParamsTable(region) — populate param table for region
 *   applyRegionParams()             — read param table → apply to form + reload layers
 *
 * External dependencies:
 *   window.getCoordinatesData()     — accessor for coordinatesData closure var
 *   window.appState.selectedRegion
 *   window.loadAllLayers()          — trigger DEM + layers reload
 *   mapElevationToColor(t, cmap)    — global from dem-loader.js
 *   showToast(msg, type)            — file-top global in app.js
 *   api.dem.load(params)            — from api.js
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let compareData = {
    left:  { region: null, image: null },
    right: { region: null, image: null }
};

// ─────────────────────────────────────────────────────────────────────────────
// Inline compare (simple canvas copy)
// ─────────────────────────────────────────────────────────────────────────────

function initCompareMode() {
    const leftSel  = document.getElementById('compareInlineLeft');
    const rightSel = document.getElementById('compareInlineRight');
    if (leftSel && !leftSel._wired) {
        leftSel._wired    = true;
        leftSel.onchange  = () => renderCompareLayer('left');
        rightSel.onchange = () => renderCompareLayer('right');
    }
}

function renderCompareLayer(side) {
    const cap    = side.charAt(0).toUpperCase() + side.slice(1);
    const select = document.getElementById(`compareInline${cap}`);
    const canvas = document.getElementById(`compareInline${cap}Canvas`);
    if (!select || !canvas) return;

    const sourceSelectors = {
        dem:      '#demImage canvas:not(.dem-gridlines-overlay):not(.city-dem-overlay)',
        water:    '#waterMaskImage canvas',
        sat:      '#satelliteImage canvas',
        combined: '#combinedImage canvas',
    };
    const srcCanvas = document.querySelector(sourceSelectors[select.value]);
    const ctx = canvas.getContext('2d');

    if (!srcCanvas || !srcCanvas.width || !srcCanvas.height) {
        canvas.width = 300; canvas.height = 150;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#444'; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
        ctx.fillText('Load this layer first', 150, 80);
        return;
    }
    canvas.width  = srcCanvas.width;
    canvas.height = srcCanvas.height;
    ctx.drawImage(srcCanvas, 0, 0);
}

function updateCompareCanvases() {
    initCompareMode();
    renderCompareLayer('left');
    renderCompareLayer('right');
}

// ─────────────────────────────────────────────────────────────────────────────
// Region DEM load for compare panel
// ─────────────────────────────────────────────────────────────────────────────

async function loadCompareRegion(side) {
    const cap      = side.charAt(0).toUpperCase() + side.slice(1);
    const select   = document.getElementById(`compare${cap}Region`);
    const nameSpan = document.getElementById(`compare${cap}Name`);
    const imageEl  = document.getElementById(`compare${cap}Image`);
    const empty    = document.getElementById(`compare${cap}Empty`);

    if (!select || !select.value) {
        if (nameSpan) nameSpan.textContent = '--';
        if (imageEl)  imageEl.style.display = 'none';
        if (empty)  { empty.textContent = 'Select a region to compare'; empty.style.display = 'block'; }
        compareData[side].region = null;
        return;
    }

    const coordinatesData = window.getCoordinatesData?.() || [];
    const region = coordinatesData[parseInt(select.value)];
    if (!region) return;

    if (nameSpan) nameSpan.textContent = region.name;
    if (empty)  { empty.textContent = 'Loading…'; empty.style.display = 'block'; }
    if (imageEl)  imageEl.style.display = 'none';

    try {
        const colormap = document.getElementById(`compare${cap}Colormap`)?.value || 'terrain';
        const params   = new URLSearchParams({ north: region.north, south: region.south, east: region.east, west: region.west, dim: 200 });
        const { data, error: demErr } = await api.dem.load(params);
        if (demErr) throw new Error(demErr);
        if (!data.dem_values || !data.dimensions) throw new Error(data.error || 'No DEM data returned');

        let demVals = data.dem_values;
        let h = Number(data.dimensions[0]);
        let w = Number(data.dimensions[1]);
        if (Array.isArray(demVals[0])) { h = demVals.length; w = demVals[0].length; demVals = demVals.flat(); }

        const vmin  = data.min_elevation ?? demVals.filter(Number.isFinite).reduce((a, b) => a < b ? a : b, Infinity);
        const vmax  = data.max_elevation ?? demVals.filter(Number.isFinite).reduce((a, b) => a > b ? a : b, -Infinity);
        const range = (vmax - vmin) || 1;

        const off = document.createElement('canvas');
        off.width = w; off.height = h;
        const ctx     = off.getContext('2d');
        const imgData = ctx.createImageData(w, h);
        for (let i = 0; i < w * h; i++) {
            const t = Math.max(0, Math.min(1, (demVals[i] - vmin) / range));
            const [r, g, b] = mapElevationToColor(t, colormap);
            imgData.data[i * 4]     = Math.round((r || 0) * 255);
            imgData.data[i * 4 + 1] = Math.round((g || 0) * 255);
            imgData.data[i * 4 + 2] = Math.round((b || 0) * 255);
            imgData.data[i * 4 + 3] = 255;
        }
        ctx.putImageData(imgData, 0, 0);

        if (imageEl) { imageEl.src = off.toDataURL(); imageEl.style.display = 'block'; }
        if (empty)   empty.style.display = 'none';
        compareData[side].region = region;
        compareData[side].image  = data;
    } catch (e) {
        console.error('Compare load error:', e);
        if (empty)  { empty.textContent = 'Error: ' + e.message; empty.style.display = 'block'; }
        if (imageEl)  imageEl.style.display = 'none';
    }
}

function applyCompareColormap(side) { loadCompareRegion(side); }

function updateCompareExagLabel(side) {
    const cap      = side.charAt(0).toUpperCase() + side.slice(1);
    const exagInput = document.getElementById(`compare${cap}Exag`);
    const exagLabel = document.getElementById(`compare${cap}ExagLabel`);
    if (exagLabel && exagInput) exagLabel.textContent = parseFloat(exagInput.value).toFixed(1) + 'x';
    loadCompareRegion(side);
}

// ─────────────────────────────────────────────────────────────────────────────
// Region params table
// ─────────────────────────────────────────────────────────────────────────────

function updateRegionParamsTable(region) {
    const tbody = document.getElementById('regionParamsBody');
    if (!tbody) return;
    if (!region) {
        tbody.innerHTML = '<tr><td colspan="2" style="color:#888;text-align:center;">Select a region</td></tr>';
        return;
    }
    const params = [
        { key: 'name',       label: 'Name',             value: region.name || '',                                           type: 'text',   readonly: true },
        { key: 'north',      label: 'North',            value: region.north || '',                                          type: 'number', step: '0.0001' },
        { key: 'south',      label: 'South',            value: region.south || '',                                          type: 'number', step: '0.0001' },
        { key: 'east',       label: 'East',             value: region.east  || '',                                          type: 'number', step: '0.0001' },
        { key: 'west',       label: 'West',             value: region.west  || '',                                          type: 'number', step: '0.0001' },
        { key: 'dim',        label: 'Dimension',        value: document.getElementById('paramDim')?.value        || 200,    type: 'number', min: 50, max: 1000 },
        { key: 'depth_scale',label: 'Depth Scale',      value: document.getElementById('paramDepthScale')?.value || 0.5,   type: 'number', step: '0.1' },
        { key: 'water_scale',label: 'Water Scale',      value: document.getElementById('paramWaterScale')?.value || 0.05,  type: 'number', step: '0.01' },
        { key: 'sat_scale',  label: 'Satellite Scale',  value: document.getElementById('paramSatScale')?.value   || 500,   type: 'number', min: 100, max: 5000 }
    ];
    tbody.innerHTML = params.map(p => `
        <tr>
            <td>${p.label}</td>
            <td>
                <input type="${p.type}" data-param="${p.key}" value="${p.value}"
                       ${p.readonly ? 'readonly' : ''}
                       ${p.step ? `step="${p.step}"` : ''}
                       ${p.min  !== undefined ? `min="${p.min}"` : ''}
                       ${p.max  !== undefined ? `max="${p.max}"` : ''}
                       style="width:100%;background:#404040;color:#fff;border:1px solid #555;padding:4px;border-radius:3px;">
            </td>
        </tr>`).join('');
}

function applyRegionParams() {
    const tbody  = document.getElementById('regionParamsBody');
    if (!tbody) return;
    const inputs = tbody.querySelectorAll('input[data-param]');
    const region = window.appState?.selectedRegion;

    inputs.forEach(input => {
        const p = input.dataset.param;
        const v = input.value;
        switch (p) {
            case 'dim':         document.getElementById('paramDim')?.setAttribute('value', v);        break;
            case 'depth_scale': document.getElementById('paramDepthScale')?.setAttribute('value', v); break;
            case 'water_scale': document.getElementById('paramWaterScale')?.setAttribute('value', v); break;
            case 'sat_scale':   document.getElementById('paramSatScale')?.setAttribute('value', v);   break;
            case 'north': case 'south': case 'east': case 'west':
                if (region) region[p] = parseFloat(v);
                break;
        }
        // Also set .value directly for live inputs
        const el = document.getElementById({ dim: 'paramDim', depth_scale: 'paramDepthScale', water_scale: 'paramWaterScale', sat_scale: 'paramSatScale' }[p]);
        if (el) el.value = v;
    });

    showToast('Parameters applied! Loading layers...', 'success');
    window.loadAllLayers?.();
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.initCompareMode          = initCompareMode;
window.renderCompareLayer       = renderCompareLayer;
window.updateCompareCanvases    = updateCompareCanvases;
window.loadCompareRegion        = loadCompareRegion;
window.applyCompareColormap     = applyCompareColormap;
window.updateCompareExagLabel   = updateCompareExagLabel;
window.updateRegionParamsTable  = updateRegionParamsTable;
window.applyRegionParams        = applyRegionParams;
