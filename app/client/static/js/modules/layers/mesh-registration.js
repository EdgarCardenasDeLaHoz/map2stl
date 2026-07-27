/**
 * modules/layers/mesh-registration.js — Manual side-by-side point-pair
 * registration picker for the imported mesh heightmap layer.
 *
 * Two independent pan/zoom canvases: left = reference (current DEM render),
 * right = the mesh heightmap (from mesh-layer.js's computeMeshHeightmap).
 * Click a point on each side to add a matched pair; once >=3 pairs exist,
 * "Compute Registration" posts them to the server, which fits a 2D affine
 * and warps the mesh heightmap onto the reference's pixel grid.
 *
 * Point pairs are collected in each canvas's *native* (unzoomed) pixel
 * space — pan/zoom is undone client-side before a pair is recorded, so the
 * server-side fit never needs to know about viewport state.
 *
 * Public API (all on window):
 *   openMeshRegistrationModal()   — show the picker, populate both canvases
 *   closeMeshRegistrationModal()
 *   undoLastMeshPointPair()
 *   clearMeshPointPairs()
 *   computeMeshRegistration()     — fit + warp, then hand off to mesh-layer.js
 *
 * Dependencies:
 *   window.appState.meshImport.heightmap  — set by computeMeshHeightmap()
 *   window.appState.lastDemData           — reference raster (read once at
 *                                            modal-open time; never write
 *                                            back through it — pass
 *                                            {skipStateUpdate: true} to
 *                                            renderDEMCanvas, since it would
 *                                            otherwise clobber the real DEM's
 *                                            lastDemData with this modal's
 *                                            preview canvases, see F-MESHIMPORT
 *                                            auto-mode bug notes)
 *   window.renderDEMCanvas                — shared heightmap colorizer
 *   window.api.mesh.register
 *   window.applyMeshRegistration          — modules/layers/mesh-layer.js
 *   window.decodeMeshValues/Mask          — modules/core/ui-helpers.js
 *   window.showToast
 */

// Independent zoom/pan state per side (mirrors stacked-layers.js's stackZoom,
// but this module intentionally keeps its own small implementation rather
// than generalizing enableStackedZoomPan, which is hard-wired to #layersStack).
const _zoom = {
    ref: { scale: 1, offsetX: 0, offsetY: 0 },
    mesh: { scale: 1, offsetX: 0, offsetY: 0 },
};

let _pairs = []; // [{ref: {x,y}, mesh: {x,y}}]
let _pending = null; // {x,y} on ref side, awaiting the matching mesh-side click
let _initialized = false;

function _els() {
    return {
        modal: document.getElementById('meshRegistrationModal'),
        refCanvas: document.getElementById('meshRegRefCanvas'),
        meshCanvas: document.getElementById('meshRegMeshCanvas'),
        refWrap: document.getElementById('meshRegRefWrap'),
        meshWrap: document.getElementById('meshRegMeshWrap'),
        pairList: document.getElementById('meshRegPairList'),
        computeBtn: document.getElementById('meshRegComputeBtn'),
        residualLabel: document.getElementById('meshRegResidualLabel'),
    };
}

/** Show the picker and (re)draw both reference and mesh canvases from current state. */
window.openMeshRegistrationModal = function openMeshRegistrationModal() {
    const heightmap = window.appState?.meshImport?.heightmap;
    const dem = window.appState?.lastDemData;
    if (!heightmap) {
        window.showToast?.('Compute a mesh heightmap first.', 'warning');
        return;
    }
    if (!dem) {
        window.showToast?.('No DEM loaded to register against.', 'warning');
        return;
    }

    const { modal } = _els();
    if (!modal) return;

    _pairs = [];
    _pending = null;
    _zoom.ref = { scale: 1, offsetX: 0, offsetY: 0 };
    _zoom.mesh = { scale: 1, offsetX: 0, offsetY: 0 };

    _drawSide('ref', dem.values, dem.width, dem.height, dem.min, dem.max);
    _drawSide('mesh', heightmap.values, heightmap.width, heightmap.height,
        heightmap.minElevation, heightmap.maxElevation);

    _initZoomPan();
    _renderPairList();
    modal.classList.remove('hidden');
};

window.closeMeshRegistrationModal = function closeMeshRegistrationModal() {
    _els().modal?.classList.add('hidden');
};

window.undoLastMeshPointPair = function undoLastMeshPointPair() {
    if (_pending) { _pending = null; } else { _pairs.pop(); }
    _renderPairList();
    _redrawMarkers();
};

window.clearMeshPointPairs = function clearMeshPointPairs() {
    _pairs = [];
    _pending = null;
    _renderPairList();
    _redrawMarkers();
};

/** Fit + warp on the server, then hand the result to mesh-layer.js. */
window.computeMeshRegistration = async function computeMeshRegistration() {
    if (_pairs.length < 3) {
        window.showToast?.('Add at least 3 point pairs before computing.', 'warning');
        return;
    }
    const state = window.appState?.meshImport;
    const dem = window.appState?.lastDemData;
    if (!state?.heightmap || !dem) return;

    const { computeBtn, residualLabel } = _els();
    const body = {
        point_pairs: _pairs.map(p => ({
            ref_x: p.ref.x, ref_y: p.ref.y, mesh_x: p.mesh.x, mesh_y: p.mesh.y,
        })),
        ref_width: dem.width, ref_height: dem.height,
        mesh_width: state.heightmap.width, mesh_height: state.heightmap.height,
    };

    computeBtn?.setAttribute('disabled', 'true');
    const { data, error } = state.uploadId
        ? await window.api.mesh.register(state.uploadId, body)
        : await window.api.mesh.libraryRegister(state.libraryRelPath, body);
    computeBtn?.removeAttribute('disabled');

    if (error) {
        window.showToast?.('Registration failed: ' + error, 'error');
        return;
    }

    const values = window.decodeMeshValues(data);
    const mask = window.decodeMeshMask(data.mesh_mask_b64);
    const [h, w] = data.dimensions;

    if (residualLabel) {
        residualLabel.textContent = `RMS residual: ${data.rms_residual_px.toFixed(1)} px`;
    }

    window.applyMeshRegistration({
        values, mask, width: w, height: h,
        minElevation: data.min_elevation, maxElevation: data.max_elevation,
        rmsResidualPx: data.rms_residual_px,
    });
    window.closeMeshRegistrationModal();
};

// ─────────────────────────────────────────────────────────────────────────────
// Rendering
// ─────────────────────────────────────────────────────────────────────────────

function _drawSide(side, values, width, height, vmin, vmax) {
    const canvas = side === 'ref' ? _els().refCanvas : _els().meshCanvas;
    if (!canvas) return;
    const rendered = window.renderDEMCanvas?.(values, width, height, 'terrain', vmin, vmax,
        { skipStateUpdate: true });
    if (!rendered) return;
    canvas.width = rendered.width;
    canvas.height = rendered.height;
    const ctx = canvas.getContext('2d');
    const draw = () => { ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.drawImage(rendered, 0, 0); };
    draw();
    // renderDEMCanvas may fill pixels asynchronously via a Worker; redraw
    // shortly after to pick up the completed pixels (dem-main.js does the
    // same via its onReady callback — this module uses a fixed delay since
    // it doesn't have a hook into that callback).
    setTimeout(draw, 50);
}

function _renderPairList() {
    const { pairList, computeBtn } = _els();
    if (pairList) {
        pairList.innerHTML = '';
        _pairs.forEach((p, i) => {
            const row = document.createElement('div');
            row.className = 'mesh-reg-pair-row';
            row.innerHTML = `<span>#${i + 1}</span>` +
                `<span>ref (${p.ref.x.toFixed(0)}, ${p.ref.y.toFixed(0)})</span>` +
                `<span>mesh (${p.mesh.x.toFixed(0)}, ${p.mesh.y.toFixed(0)})</span>` +
                `<button class="mesh-reg-pair-remove" data-idx="${i}" title="Remove pair" aria-label="Remove pair ${i + 1}">×</button>`;
            row.querySelector('button').addEventListener('click', () => {
                _pairs.splice(i, 1);
                _renderPairList();
                _redrawMarkers();
            });
            pairList.appendChild(row);
        });
        if (_pending) {
            const row = document.createElement('div');
            row.className = 'mesh-reg-pair-row mesh-reg-pair-pending';
            row.textContent = `#${_pairs.length + 1}: click the matching point on the mesh side…`;
            pairList.appendChild(row);
        }
    }
    if (computeBtn) computeBtn.toggleAttribute('disabled', _pairs.length < 3);
}

function _redrawMarkers() {
    const heightmap = window.appState?.meshImport?.heightmap;
    const dem = window.appState?.lastDemData;
    if (dem) _drawSide('ref', dem.values, dem.width, dem.height, dem.min, dem.max);
    if (heightmap) {
        _drawSide('mesh', heightmap.values, heightmap.width, heightmap.height,
            heightmap.minElevation, heightmap.maxElevation);
    }
    setTimeout(() => {
        _pairs.forEach((p, i) => {
            _drawMarker(_els().refCanvas, p.ref.x, p.ref.y, i + 1);
            _drawMarker(_els().meshCanvas, p.mesh.x, p.mesh.y, i + 1);
        });
        if (_pending) _drawMarker(_els().refCanvas, _pending.x, _pending.y, _pairs.length + 1);
    }, 60);
}

function _drawMarker(canvas, nativeX, nativeY, label) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.save();
    ctx.fillStyle = '#ff4444';
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(nativeX, nativeY, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 10px monospace';
    ctx.fillText(String(label), nativeX + 7, nativeY - 7);
    ctx.restore();
}

// ─────────────────────────────────────────────────────────────────────────────
// Pan/zoom + click-to-place (per side, independent)
// ─────────────────────────────────────────────────────────────────────────────

function _applyZoomCss(side) {
    const canvas = side === 'ref' ? _els().refCanvas : _els().meshCanvas;
    if (!canvas) return;
    const z = _zoom[side];
    canvas.style.transformOrigin = '0 0';
    canvas.style.transform = `translate(${z.offsetX}px, ${z.offsetY}px) scale(${z.scale})`;
}

function _nativePointFromEvent(side, e) {
    const canvas = side === 'ref' ? _els().refCanvas : _els().meshCanvas;
    const wrap = side === 'ref' ? _els().refWrap : _els().meshWrap;
    if (!canvas || !wrap) return null;
    const rect = wrap.getBoundingClientRect();
    const z = _zoom[side];
    const localX = (e.clientX - rect.left - z.offsetX) / z.scale;
    const localY = (e.clientY - rect.top - z.offsetY) / z.scale;
    if (localX < 0 || localY < 0 || localX > canvas.width || localY > canvas.height) return null;
    return { x: localX, y: localY };
}

function _wireSide(side) {
    const wrap = side === 'ref' ? _els().refWrap : _els().meshWrap;
    if (!wrap) return;

    let isPanning = false, startX = 0, startY = 0;

    wrap.addEventListener('wheel', (e) => {
        e.preventDefault();
        const z = _zoom[side];
        const rect = wrap.getBoundingClientRect();
        const mouseX = e.clientX - rect.left, mouseY = e.clientY - rect.top;
        const factor = e.deltaY > 0 ? 0.9 : 1.1;
        const newScale = Math.max(0.5, Math.min(8, z.scale * factor));
        const change = newScale / z.scale;
        z.offsetX = mouseX - (mouseX - z.offsetX) * change;
        z.offsetY = mouseY - (mouseY - z.offsetY) * change;
        z.scale = newScale;
        _applyZoomCss(side);
    });

    wrap.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        isPanning = true;
        startX = e.clientX - _zoom[side].offsetX;
        startY = e.clientY - _zoom[side].offsetY;
        wrap.classList.add('mesh-reg-panning');
    });

    window.addEventListener('mousemove', (e) => {
        if (!isPanning) return;
        _zoom[side].offsetX = e.clientX - startX;
        _zoom[side].offsetY = e.clientY - startY;
        _applyZoomCss(side);
    });

    window.addEventListener('mouseup', () => {
        isPanning = false;
        wrap.classList.remove('mesh-reg-panning');
    });

    wrap.addEventListener('click', (e) => {
        if (isPanning) return;
        const pt = _nativePointFromEvent(side, e);
        if (!pt) return;
        _handleClick(side, pt);
    });

    wrap.addEventListener('dblclick', () => {
        _zoom[side] = { scale: 1, offsetX: 0, offsetY: 0 };
        _applyZoomCss(side);
    });
}

function _handleClick(side, pt) {
    if (side === 'ref') {
        _pending = pt;
    } else {
        if (!_pending) {
            window.showToast?.('Click the reference (left) side first.', 'info');
            return;
        }
        _pairs.push({ ref: _pending, mesh: pt });
        _pending = null;
    }
    _renderPairList();
    _redrawMarkers();
}

function _initZoomPan() {
    if (_initialized) return;
    _initialized = true;
    _wireSide('ref');
    _wireSide('mesh');

    const { modal, computeBtn } = _els();
    modal?.addEventListener('click', (e) => { if (e.target === modal) window.closeMeshRegistrationModal(); });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
            window.closeMeshRegistrationModal();
        }
    });
    computeBtn?.addEventListener('click', () => window.computeMeshRegistration());
    document.getElementById('meshRegUndoBtn')?.addEventListener('click', () => window.undoLastMeshPointPair());
    document.getElementById('meshRegClearBtn')?.addEventListener('click', () => window.clearMeshPointPairs());
    document.getElementById('meshRegCloseBtn')?.addEventListener('click', () => window.closeMeshRegistrationModal());
}
