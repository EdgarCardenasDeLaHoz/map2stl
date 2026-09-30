/**
 * modules/model-viewer.js — Three.js 3D terrain viewer + puzzle export.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   previewModelIn3D()                          — build/replace terrain in viewer
 *   haversineDiagKm(N, S, E, W)                — bbox diagonal in km
 *   updatePuzzlePreview()                       — draw puzzle cut lines in viewer
 *   window.setViewerAutoRotate(val)             — set auto-rotate flag from app.js
 *   window.resetViewerCamera()                  — fit camera to current mesh
 *   window.rebuildViewerColors(cmap)            — recolor mesh from a colormap name
 *   window.setViewerNormals(bool)               — toggle normals-debug material
 *   window.updateBedOutline()                   — redraw the printer-bed outline
 *   window.puzzleEdgesFor(cols, rows)           — dragged cut positions {col_edges_mm,
 *                                                 row_edges_mm} for that grid, or null
 *   window.resetPuzzleEdges()                   — back to the even split
 *
 * Puzzle cut lines (updatePuzzlePreview) are drawn at appState.puzzleEdges when
 * they belong to the current grid, else evenly; a plain left drag near a line
 * moves that cut (modules/export/puzzle-cuts.js) and fires 'puzzle-edges-changed'.
 *
 * State exposed on window.appState:
 *   window.appState.terrainMesh  — current terrain mesh (or null)
 *   window.appState.viewerScene  — the THREE.Scene
 *   window.appState.modelPreviewState — 'idle' | 'building' | 'ready' | 'error'
 *
 * External dependencies:
 *   THREE                                       — global Three.js
 *   window.appState.generatedModelData
 *   window.appState.lastDemData
 *   window.showToast(msg, type)                 — file-top global in app.js
 *   window.mapElevationToColor(t, cmap)         — from dem-loader.js (loaded first)
 */

import { parseBedSize, piecesNeeded } from './print-scale.js';
import {
    evenEdges, gridKey, isCustom, minPieceMm, moveEdge, nearestEdge, roundEdges,
} from './puzzle-cuts.js';

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let modelScene    = null;
let modelCamera   = null;
let modelRenderer = null;
let terrainMesh   = null;
let viewerAutoRotate = false;
let needsRender   = true;
let _normalsActive = false;     // true when MeshNormalMaterial is active
let _resizeHandler = null;      // reference for cleanup on re-init
// Latest mesh scale info, set by _buildMeshFromPreview, read by _updateSceneOverlays
let geometry_scale_for_overlays = { scale: 1, cols: 0, rows: 0, totalHeightMm: 0 };

// Orbit drag state
let _isDragging   = false;
let _isPanning    = false;
let _prevMouse    = { x: 0, y: 0 };

// Orbit target (world-space point the camera orbits around)
let orbitTarget   = null;   // THREE.Vector3 — initialised in initModelViewer

// Reusable scratch objects — avoids per-drag allocations in the hot path
// Lazily initialised after THREE is guaranteed available (inside initModelViewer)
let _rotSph    = null;   // THREE.Spherical  for _orbitRotate
let _rotOffset = null;   // THREE.Vector3    for _orbitRotate
let _panRight  = null;   // THREE.Vector3    for _orbitPan
let _panUp     = null;   // THREE.Vector3    for _orbitPan
let _panFwd    = null;   // THREE.Vector3    for _orbitPan
let _panDelta  = null;   // THREE.Vector3    for _orbitPan

// ─────────────────────────────────────────────────────────────────────────────
// Viewer init
// ─────────────────────────────────────────────────────────────────────────────

function initModelViewer() {
    const container = document.getElementById('modelViewer');
    if (!container) return;

    // Dispose old renderer so the GPU context is released on re-init
    if (modelRenderer) {
        modelRenderer.dispose();
        modelRenderer = null;
    }
    if (_resizeHandler) {
        window.removeEventListener('resize', _resizeHandler);
        _resizeHandler = null;
    }

    container.innerHTML = '';
    container.classList.add('pos-relative');

    modelScene = new THREE.Scene();
    modelScene.background = new THREE.Color(0x1a1a1a);

    orbitTarget = new THREE.Vector3(0, 0, 0);

    // Initialise reusable scratch vectors now that THREE is confirmed available
    _rotSph    = new THREE.Spherical();
    _rotOffset = new THREE.Vector3();
    _panRight  = new THREE.Vector3();
    _panUp     = new THREE.Vector3();
    _panFwd    = new THREE.Vector3();
    _panDelta  = new THREE.Vector3();

    const aspect = container.clientWidth / Math.max(container.clientHeight, 1);
    modelCamera  = new THREE.PerspectiveCamera(50, aspect, 0.1, 2000);
    modelCamera.position.set(0, 120, 160);
    modelCamera.lookAt(orbitTarget);

    try {
        modelRenderer = new THREE.WebGLRenderer({ antialias: true });
    } catch (e) {
        console.error('WebGL unavailable for 3D viewer:', e);
        container.innerHTML = '<div class="viewer-unavailable">3D preview unavailable (WebGL not supported by this browser/GPU)</div>';
        return;
    }
    modelRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    modelRenderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(modelRenderer.domElement);

    // Lighting: hemisphere + two directional lights
    modelScene.add(new THREE.HemisphereLight(0x8aafd4, 0x4a4035, 0.6));
    const dl1 = new THREE.DirectionalLight(0xffffff, 0.9);
    dl1.position.set(60, 120, 80);
    modelScene.add(dl1);
    const dl2 = new THREE.DirectionalLight(0xffd0a0, 0.3);
    dl2.position.set(-60, 40, -80);
    modelScene.add(dl2);

    // HUD overlay
    const hud = document.createElement('div');
    hud.id = 'viewerHud';
    hud.className = 'hud-overlay';
    container.appendChild(hud);

    _setupOrbitControls();

    _resizeHandler = () => {
        if (!container.offsetParent) return;
        const w = container.clientWidth, h = container.clientHeight;
        modelCamera.aspect = w / h;
        modelCamera.updateProjectionMatrix();
        modelRenderer.setSize(w, h);
        needsRender = true;
    };
    window.addEventListener('resize', _resizeHandler);

    window.appState.viewerScene = modelScene;

    (function animate() {
        requestAnimationFrame(animate);
        if (viewerAutoRotate && terrainMesh) {
            terrainMesh.rotation.y += 0.005;
            needsRender = true;
        }
        if (needsRender) {
            modelRenderer.render(modelScene, modelCamera);
            needsRender = false;
        }
    })();
}

// ─────────────────────────────────────────────────────────────────────────────
// Orbit / pan / zoom controls
// Left-drag = orbit,  Shift+drag = pan,  wheel = zoom toward target
// ─────────────────────────────────────────────────────────────────────────────

function _setupOrbitControls() {
    const el = modelRenderer.domElement;

    el.addEventListener('mousedown', e => {
        if (e.button === 0 && !e.shiftKey && _startCutDrag(e)) { e.preventDefault(); return; }
        _isDragging = true;
        _isPanning  = e.shiftKey || e.button === 1;
        _prevMouse  = { x: e.clientX, y: e.clientY };
        e.preventDefault();
    });

    el.addEventListener('mousemove', e => {
        if (_cutDrag) { _moveCutDrag(e); return; }
        if (!_isDragging) { el.style.cursor = _cutUnderMouse(e)?.cursor || ''; return; }
        const dx = e.clientX - _prevMouse.x;
        const dy = e.clientY - _prevMouse.y;
        _prevMouse = { x: e.clientX, y: e.clientY };
        if (_isPanning) _orbitPan(dx, dy); else _orbitRotate(dx, dy);
    });

    el.addEventListener('mouseup',    () => { _isDragging = false; _endCutDrag(); });
    el.addEventListener('mouseleave', () => { _isDragging = false; _endCutDrag(); });

    el.addEventListener('wheel', e => {
        e.preventDefault();
        const dir     = e.deltaY > 0 ? 1 : -1;
        const dist    = modelCamera.position.distanceTo(orbitTarget);
        const newDist = Math.max(10, Math.min(800, dist * (1 + dir * 0.1)));
        _rotOffset.copy(modelCamera.position).sub(orbitTarget).normalize().multiplyScalar(newDist);
        modelCamera.position.copy(orbitTarget).add(_rotOffset);
        needsRender = true;
    }, { passive: false });

    // Touch: single-finger orbit, two-finger pinch-zoom
    let touches = [];
    let pinchDist0 = 0;

    el.addEventListener('touchstart', e => {
        touches = Array.from(e.touches);
        if (touches.length === 1) {
            _isDragging = true; _isPanning = false;
            _prevMouse = { x: touches[0].clientX, y: touches[0].clientY };
        } else if (touches.length === 2) {
            _isDragging = false;
            pinchDist0 = Math.hypot(
                touches[0].clientX - touches[1].clientX,
                touches[0].clientY - touches[1].clientY
            );
        }
        e.preventDefault();
    }, { passive: false });

    el.addEventListener('touchmove', e => {
        e.preventDefault();
        const ts = Array.from(e.touches);
        if (ts.length === 1 && _isDragging) {
            const dx = ts[0].clientX - _prevMouse.x;
            const dy = ts[0].clientY - _prevMouse.y;
            _prevMouse = { x: ts[0].clientX, y: ts[0].clientY };
            _orbitRotate(dx, dy);
        } else if (ts.length === 2) {
            const dist    = Math.hypot(ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);
            const camDist = modelCamera.position.distanceTo(orbitTarget);
            const newDist = Math.max(10, Math.min(800, camDist * (pinchDist0 / Math.max(dist, 1))));
            _rotOffset.copy(modelCamera.position).sub(orbitTarget).normalize().multiplyScalar(newDist);
            modelCamera.position.copy(orbitTarget).add(_rotOffset);
            pinchDist0 = dist;
            needsRender = true;
        }
    }, { passive: false });

    el.addEventListener('touchend', () => { _isDragging = false; });
}

function _orbitRotate(dx, dy) {
    _rotOffset.copy(modelCamera.position).sub(orbitTarget);
    _rotSph.setFromVector3(_rotOffset);
    _rotSph.theta -= dx * 0.012;
    _rotSph.phi    = Math.max(0.05, Math.min(Math.PI - 0.05, _rotSph.phi - dy * 0.012));
    _rotOffset.setFromSpherical(_rotSph);
    modelCamera.position.copy(orbitTarget).add(_rotOffset);
    modelCamera.lookAt(orbitTarget);
    needsRender = true;
}

function _orbitPan(dx, dy) {
    const dist     = modelCamera.position.distanceTo(orbitTarget);
    const panSpeed = dist * 0.001;
    modelCamera.getWorldDirection(_panFwd);
    _panRight.crossVectors(_panFwd, modelCamera.up).normalize().negate();
    _panUp.copy(modelCamera.up).normalize();
    _panDelta.copy(_panRight).multiplyScalar(dx * panSpeed)
        .addScaledVector(_panUp, -dy * panSpeed);
    orbitTarget.add(_panDelta);
    modelCamera.position.add(_panDelta);
    needsRender = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// Camera fit
// ─────────────────────────────────────────────────────────────────────────────

function _fitCameraToMesh(mesh) {
    if (!mesh || !modelCamera) return;
    const box     = new THREE.Box3().setFromObject(mesh);
    const center  = box.getCenter(new THREE.Vector3());
    const size    = box.getSize(new THREE.Vector3());
    const maxDim  = Math.max(size.x, size.y, size.z);
    const fov     = modelCamera.fov * Math.PI / 180;
    const fitDist = (maxDim / 2) / Math.tan(fov / 2) * 1.4;
    orbitTarget.copy(center);
    modelCamera.position.set(
        center.x,
        center.y + fitDist * 0.55,
        center.z + fitDist * 0.85
    );
    modelCamera.lookAt(orbitTarget);
    needsRender = true;
}

function resetViewerCamera() {
    if (terrainMesh) _fitCameraToMesh(terrainMesh);
}

// ─────────────────────────────────────────────────────────────────────────────
// Scene overlays — grid, axes, vertical scale bar
// ─────────────────────────────────────────────────────────────────────────────

function _makeTextSprite(text, { fontSize = 22, color = '#dddddd', bg = 'rgba(0,0,0,0.45)', pad = 3 } = {}) {
    const canvas = document.createElement('canvas');
    const ctx    = canvas.getContext('2d');
    ctx.font = `${fontSize}px monospace`;
    const tw = ctx.measureText(text).width;
    canvas.width  = Math.ceil(tw + pad * 2 + 2);
    canvas.height = Math.ceil(fontSize + pad * 2);
    ctx.font = `${fontSize}px monospace`;
    if (bg) { ctx.fillStyle = bg; ctx.fillRect(0, 0, canvas.width, canvas.height); }
    ctx.fillStyle = color;
    ctx.fillText(text, pad + 1, fontSize + pad - 2);
    const tex = new THREE.Texture(canvas);
    tex.needsUpdate = true;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
    const aspect = canvas.width / canvas.height;
    sprite.scale.set(aspect * 5.5, 5.5, 1);
    return sprite;
}

function _niceInterval(range) {
    const raw = range / 6;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const mult of [1, 2, 2.5, 5, 10]) {
        if (mult * mag >= raw) return mult * mag;
    }
    return mag * 10;
}

/**
 * Add/refresh ground grid, axes helper, and vertical scale bar.
 * Mesh is normalized to x∈[-50,+50], z∈[-50,+50], y∈[0,30] in display units;
 * physical scale is read from data.model_height + data.base_height.
 */
function _updateSceneOverlays(data) {
    if (!modelScene) return;

    // Remove previous overlays so this is idempotent on re-preview
    ['groundGrid', 'axesHelper', 'vertScale', 'horizScale'].forEach(name => {
        const old = modelScene.getObjectByName(name);
        if (old) {
            old.traverse(c => { c.geometry?.dispose(); c.material?.map?.dispose(); c.material?.dispose(); });
            modelScene.remove(old);
        }
    });

    // Use the same scale factor _buildMeshFromPreview applied — preserves real
    // physical proportions (e.g. 600×800×20 mm renders as 75×100×2.5 display u).
    const SCALE = geometry_scale_for_overlays.scale || 1;
    const mmPerPx = data.mm_per_pixel ?? 1.0;
    const widthMm = geometry_scale_for_overlays.widthMm
        ?? (data.cols || 0) * mmPerPx;
    const depthMm = geometry_scale_for_overlays.depthMm
        ?? (data.rows || 0) * mmPerPx;
    const totalHeightMm = geometry_scale_for_overlays.totalHeightMm
        || (data.model_height || 0) + (data.base_height || 0);

    const viewW = widthMm * SCALE;         // physical width in display units (mm × SCALE)
    const viewD = depthMm * SCALE;         // physical depth
    const heightScale = SCALE;             // display units per mm — same for all axes

    // Ground grid — cell size is a "nice" mm value covering the model footprint
    const cellMm     = _niceInterval(Math.max(widthMm, depthMm));
    const gridCellsW = Math.max(1, Math.ceil(widthMm / cellMm));
    const gridCellsD = Math.max(1, Math.ceil(depthMm / cellMm));
    const gridDivs   = Math.max(gridCellsW, gridCellsD);
    const gridSize   = gridDivs * cellMm * SCALE;
    const gridHelper = new THREE.GridHelper(gridSize, gridDivs, 0x555555, 0x303030);
    gridHelper.name  = 'groundGrid';
    modelScene.add(gridHelper);

    // Axes helper at front-left corner
    const axisLen    = Math.max(viewW, viewD) * 0.18;
    const axesHelper = new THREE.AxesHelper(axisLen);
    axesHelper.name  = 'axesHelper';
    axesHelper.position.set(-viewW / 2, 0, viewD / 2);
    modelScene.add(axesHelper);

    // Vertical scale bar (back-right corner) — min and max only
    if (totalHeightMm > 0) {
        const vertGroup = new THREE.Group();
        vertGroup.name  = 'vertScale';
        const totalU    = totalHeightMm * heightScale;
        const lineMat   = new THREE.LineBasicMaterial({ color: 0xaaaaaa, depthTest: false });

        // Vertical line + end ticks
        const lineGeo = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(0, 0, 0),
            new THREE.Vector3(0, totalU, 0),
        ]);
        vertGroup.add(new THREE.Line(lineGeo, lineMat));
        for (const yU of [0, totalU]) {
            const tickGeo = new THREE.BufferGeometry().setFromPoints([
                new THREE.Vector3(-2, yU, 0),
                new THREE.Vector3( 2, yU, 0),
            ]);
            vertGroup.add(new THREE.Line(tickGeo, lineMat));
        }
        // Min (0 mm) and max (totalHeightMm) labels only
        const minLabel = _makeTextSprite(`0 mm`, { fontSize: 19, color: '#bbddff' });
        minLabel.position.set(10, 0, 0);
        vertGroup.add(minLabel);
        const maxLabel = _makeTextSprite(`${Math.round(totalHeightMm)} mm`, { fontSize: 19, color: '#ff9966' });
        maxLabel.position.set(10, totalU, 0);
        vertGroup.add(maxLabel);

        vertGroup.position.set(viewW / 2 + 6, 0, -viewD / 2);
        modelScene.add(vertGroup);
    }

    // Horizontal callouts: footprint limits + grid cell size + (optional) km diagonal
    const horizGroup = new THREE.Group();
    horizGroup.name = 'horizScale';
    if (widthMm > 0) {
        const xLabel = _makeTextSprite(`W ${Math.round(widthMm)} mm`, { fontSize: 18, color: '#ff9966' });
        xLabel.position.set(0, -1, viewD / 2 + 5);
        horizGroup.add(xLabel);
    }
    if (depthMm > 0) {
        const zLabel = _makeTextSprite(`D ${Math.round(depthMm)} mm`, { fontSize: 18, color: '#9fdb9f' });
        zLabel.position.set(-viewW / 2 - 5, -1, 0);
        horizGroup.add(zLabel);
    }
    const cellLabel = _makeTextSprite(`grid ${Math.round(cellMm)} mm`, { fontSize: 16, color: '#cccccc' });
    cellLabel.position.set(viewW / 2 - 8, -1, -viewD / 2 - 6);
    horizGroup.add(cellLabel);

    const r = window.appState?.currentRegion;
    if (r && typeof haversineDiagKm === 'function') {
        const diagKm = haversineDiagKm(r.north, r.south, r.east, r.west);
        const label = _makeTextSprite(`${diagKm.toFixed(1)} km diag`, { fontSize: 16, color: '#cccccc' });
        label.position.set(viewW / 2 - 12, -1, viewD / 2 + 10);
        horizGroup.add(label);
    }
    if (horizGroup.children.length > 0) modelScene.add(horizGroup);

    updateBedOutline();
    needsRender = true;
}

/**
 * Outline of the selected printer bed (#bedSizeSelect, or the custom W×H),
 * centred under the model at the mesh's display scale. Orange when the model
 * footprint does not fit the bed in either orientation.
 */
function updateBedOutline() {
    if (!modelScene) return;
    const old = modelScene.getObjectByName('bedOutline');
    if (old) {
        old.traverse(c => { c.geometry?.dispose(); c.material?.map?.dispose(); c.material?.dispose(); });
        modelScene.remove(old);
    }
    const g = geometry_scale_for_overlays;
    if (!terrainMesh || !g.widthMm) { needsRender = true; return; }

    const bed = parseBedSize(
        document.getElementById('bedSizeSelect')?.value,
        document.getElementById('bedCustomW')?.value,
        document.getElementById('bedCustomH')?.value,
    );
    const fits = (g.widthMm <= bed.w && g.depthMm <= bed.h) || (g.widthMm <= bed.h && g.depthMm <= bed.w);
    const color = fits ? 0x4a9fd4 : 0xe67e22;
    const hw = bed.w * g.scale / 2;
    const hd = bed.h * g.scale / 2;
    const y = 0.05;   // just above the ground grid so the two do not z-fight

    const group = new THREE.Group();
    group.name = 'bedOutline';
    const geo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-hw, y, -hd), new THREE.Vector3(hw, y, -hd),
        new THREE.Vector3(hw, y, hd), new THREE.Vector3(-hw, y, hd),
        new THREE.Vector3(-hw, y, -hd),
    ]);
    group.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color })));
    const label = _makeTextSprite(`bed ${bed.w}×${bed.h} mm${fits ? '' : ' (too small)'}`,
        { fontSize: 16, color: fits ? '#8cc8f0' : '#f0a060' });
    label.position.set(-hw + 12, y, -hd - 4);
    group.add(label);
    modelScene.add(group);
    needsRender = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// Preview
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Read a numeric form field. `parseFloat(x) || default` turned a legitimate 0
 * (no base plate) into the default, so this falls back only on a value that
 * is not a number, or - for fields where zero is meaningless - not positive.
 */
function _num(id, fallback, { positive = false, int = false } = {}) {
    const raw = document.getElementById(id)?.value;
    const v = int ? parseInt(raw, 10) : parseFloat(raw);
    if (!Number.isFinite(v) || (positive && v <= 0)) return fallback;
    return v;
}

/**
 * Every field that shapes the mesh, read from the Extrude form in one place.
 * The preview sends exactly this, stores it on generatedModelData, and every
 * export reuses the stored copy - so a downloaded file is always the model the
 * viewer last showed, never a mix of that and whatever the form says now.
 */
function _readBuildParams() {
    const checked = (id) => document.getElementById(id)?.checked || false;
    return {
        model_height:     _num('exportModelHeight', 30, { positive: true }),
        base_height:      _num('exportBaseHeight', 5),
        exaggeration:     _num('exportExaggeration', 1.0, { positive: true }),
        mm_per_pixel:     _num('mmPerPixel', 1.0, { positive: true }),
        z_mode:           document.getElementById('exportZMode')?.value || 'auto',
        median_size:      parseInt(document.getElementById('exportMedian')?.value ?? '3', 10),
        sea_level_cap:    checked('exportSeaLevelCap'),
        engrave_label:    checked('exportEngraveLabel'),
        label_text:       document.getElementById('exportLabelText')?.value
                          || window.appState?.selectedRegion?.name || 'terrain',
        contours:         checked('exportContours'),
        contour_interval: _num('exportContourInterval', 100, { positive: true, int: true }),
        contour_style:    document.getElementById('exportContourStyle')?.value || 'engraved',
    };
}

/**
 * Remembers the last preview request so an identical one is not sent again.
 *
 * Every DEM re-render (recolour, rescale, a layer finishing its load) calls the
 * auto-rebuild, and so does entering the Extrude view; none of them change the
 * mesh, but each used to POST the same body and have the server rebuild the same
 * 700k-face mesh. The request body fully determines the mesh (it carries the DEM
 * handle, or the edited values themselves), so its serialisation is the key.
 *
 * A request is skipped while an identical one is in flight, or once it has
 * succeeded and its mesh is still the one shown; a failed one may be retried.
 * Pure (no DOM), so it is unit-tested directly.
 */
function _createPreviewGate() {
    let last = null;   // { key, state: 'pending' | 'done' | 'failed' }
    return {
        /** True if a request with this key would repeat the in-flight or shown mesh. */
        isDuplicate(key, meshShown) {
            if (last === null || last.key !== key) return false;
            return last.state === 'pending' || (last.state === 'done' && meshShown);
        },
        /** Record a request about to be sent; returns the ticket to settle. */
        begin(key) {
            last = { key, state: 'pending' };
            return last;
        },
        /** Mark a request finished. A ticket superseded by a newer begin() is ignored. */
        settle(ticket, ok) {
            if (ticket === last) last.state = ok ? 'done' : 'failed';
        },
    };
}

const _previewGate = _createPreviewGate();

// Rebuilds fire on every settings change, so two can overlap. Each request
// takes a number; a response that is not the latest is dropped, or a slow
// older answer could land last and become the model the export ships.
let _previewSeq = 0;

async function previewModelIn3D() {
    const ldd = window.appState?.lastDemData;
    if (!ldd?.values?.length) {
        window.showToast('Load a DEM first (Edit tab → Reload).', 'warning'); return;
    }

    const build = _readBuildParams();
    const ds = window._demSettings ? window._demSettings() : {};
    const body = {
        ...ds,
        ...build,
        // View-only: the exported file is always a closed solid. Absent
        // checkbox means solid, matching the server default.
        solid: document.getElementById('viewerSolidPreview')?.checked ?? true,
    };
    const key = JSON.stringify(body);
    // Once generatedModelData is cleared (a failed rebuild, a reset) the viewer no
    // longer shows this key's mesh, so the same body is worth sending again.
    if (_previewGate.isDuplicate(key, !!window.appState.generatedModelData)) return;

    const seq = ++_previewSeq;
    const ticket = _previewGate.begin(key);
    const statusEl = document.getElementById('modelStatus');
    if (statusEl) statusEl.textContent = '⏳ Building mesh…';
    window.appState.modelPreviewState = 'building';
    document.getElementById('modelViewerContainer')?.classList.add('mesh-building');

    if (!modelRenderer) initModelViewer();

    const hadModel = !!window.appState.generatedModelData;
    try {
        const { data, error: previewErr } = await window.api.export.preview(body);
        if (seq !== _previewSeq) return;          // superseded by a newer rebuild
        if (previewErr) throw new Error(previewErr);

        const cmap = document.getElementById('viewerColormap')?.value || 'terrain';
        _replaceMesh(_buildMeshFromPreview(data, cmap));
        if (cmap === 'satellite') _applySatelliteTexture();
        _fitCameraToMesh(terrainMesh);
        _updateHud(data);
        _updateSceneOverlays(data);

        window.appState.generatedModelData = {
            values: ldd.values, width: ldd.width, height: ldd.height,
            vmin: ldd.vmin, vmax: ldd.vmax,
            // What the export sends: the same DEM settings and build fields
            // this preview was made from.
            demSettings: ds,
            buildParams: build,
            // Legacy names, still read by the cross-section export.
            mmPerPixel: build.mm_per_pixel,
            modelHeight: build.model_height,
            exaggeration: build.exaggeration,
            baseHeight: build.base_height,
        };
        _previewGate.settle(ticket, true);
        window.appState.modelPreviewState = 'ready';
        window._setExportButtonsEnabled?.(true);
        window.appState._updateWorkflowStepper?.();
        if (statusEl) {
            const warn = data.composite_error ? `  ⚠ composite failed, raw DEM shown` : '';
            statusEl.textContent = `Preview: ${ldd.width}×${ldd.height}, `
                + `${data.face_count.toLocaleString()} faces, `
                + `${(data.z_max ?? 0).toFixed(1)} mm tall${warn}`;
        }
        if (data.composite_error) {
            window.showToast('Server composite failed - preview shows the raw DEM: '
                + data.composite_error, 'error', 8000);
        } else if (!hadModel) {
            // Only on the first build; a toast per keystroke piles up.
            window.showToast('3D preview ready — drag to rotate, shift+drag to pan, scroll to zoom.', 'success');
        }
    } catch (e) {
        _previewGate.settle(ticket, false);
        if (seq !== _previewSeq) return;
        // The form no longer matches any mesh, so there is nothing honest to
        // export: drop the stale model and disable the buttons until a rebuild
        // succeeds.
        window.appState.generatedModelData = null;
        window.appState.modelPreviewState = 'error';
        window._setExportButtonsEnabled?.(false);
        window.appState._updateWorkflowStepper?.();
        if (statusEl) statusEl.textContent = '❌ ' + e.message;
        window.showToast('Preview failed: ' + e.message, 'error');
    } finally {
        if (seq === _previewSeq) {
            document.getElementById('modelViewerContainer')?.classList.remove('mesh-building');
        }
    }
}

function _buildMeshFromPreview(data, cmap) {
    // numpy2stl vertex layout: [col, row, z_mm] — x and y are PIXEL indices
    // (server keeps them as ints to halve JSON payload), z is in mm.
    // Multiply pixel coords by mm_per_pixel here so the viewer renders the
    // real physical dimensions (1 px → mm_per_pixel mm).
    // Three.js layout: x=col (→right), y=z_mm (→up), z=row (→back).
    //
    // Then scale all three axes by the SAME factor so the viewer preserves
    // the real aspect ratio.
    const rawVerts = data.vertices;
    const rawFaces = data.faces;
    const mmPerPx  = data.mm_per_pixel ?? 1.0;
    const widthMm  = Math.max(data.cols * mmPerPx, 1);
    const depthMm  = Math.max(data.rows * mmPerPx, 1);
    // The server reports the mesh's real top; model_height + base ignores
    // exaggeration, which multiplies the relief.
    const totalHeightMm = Number.isFinite(data.z_max)
        ? data.z_max
        : (data.model_height || 0) * (data.exaggeration || 1) + (data.base_height || 0);
    // Longest physical dimension maps to 100 display units; others scale equally.
    const longest = Math.max(widthMm, depthMm, totalHeightMm, 1);
    const SCALE = 100 / longest;

    const xOffset = (widthMm * SCALE) / 2;
    const zOffset = (depthMm * SCALE) / 2;
    const zMin    = data.z_min;
    const zRange  = Math.max(data.z_max - data.z_min, 1);

    const positions = new Float32Array(rawVerts.length * 3);
    const colors    = new Float32Array(rawVerts.length * 3);
    // Texture coordinates from the DEM pixel of each vertex, for the satellite
    // drape (_applySatelliteTexture): u = column, v = 1 at row 0 (north).
    const uvs       = new Float32Array(rawVerts.length * 2);
    const uDen = Math.max(data.cols - 1, 1), vDen = Math.max(data.rows - 1, 1);
    const vertexCmap = cmap === 'satellite' ? 'terrain' : cmap;
    for (let i = 0; i < rawVerts.length; i++) {
        const [c, r, z] = rawVerts[i];
        uvs[i * 2] = c / uDen;
        uvs[i * 2 + 1] = 1 - r / vDen;
        positions[i * 3]     = c * mmPerPx * SCALE - xOffset; // x (mm in display units)
        positions[i * 3 + 1] = z * SCALE;                     // y (z is already mm)
        positions[i * 3 + 2] = r * mmPerPx * SCALE - zOffset; // z (mm in display units)

        const rgb = _elevColor((z - zMin) / zRange, vertexCmap);
        colors[i * 3] = rgb[0]; colors[i * 3 + 1] = rgb[1]; colors[i * 3 + 2] = rgb[2];
    }

    // Stash physical dims (mm) and the display-scale factor for overlay code.
    geometry_scale_for_overlays = {
        scale: SCALE,
        widthMm, depthMm, totalHeightMm,
        // The mesh spans pixel centres 0..cols-1: the model the server cuts.
        modelWidthMm: Math.max(data.cols - 1, 1) * mmPerPx,
        modelDepthMm: Math.max(data.rows - 1, 1) * mmPerPx,
        xOffset, zOffset,
    };

    const indices = new Uint32Array(rawFaces.length * 3);
    for (let i = 0; i < rawFaces.length; i++) {
        indices[i * 3] = rawFaces[i][0]; indices[i * 3 + 1] = rawFaces[i][1]; indices[i * 3 + 2] = rawFaces[i][2];
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute('uv',       new THREE.BufferAttribute(uvs, 2));
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    geometry.computeVertexNormals();

    const material = new THREE.MeshStandardMaterial({
        vertexColors: true, flatShading: false, side: THREE.DoubleSide,
    });
    return new THREE.Mesh(geometry, material);
}

function _replaceMesh(newMesh) {
    if (terrainMesh) {
        modelScene.remove(terrainMesh);
        terrainMesh.geometry.dispose();
        // In normals mode the saved original material also needs disposal
        if (terrainMesh._savedMaterial) terrainMesh._savedMaterial.dispose();
        (terrainMesh._savedMaterial || terrainMesh.material).map?.dispose();
        terrainMesh.material.dispose();
    }
    terrainMesh = newMesh;
    terrainMesh.material.wireframe = document.getElementById('viewerWireframe')?.checked ?? false;
    // Re-apply normals mode to the new mesh if it was active
    if (_normalsActive) {
        terrainMesh._savedMaterial = terrainMesh.material;
        terrainMesh.material = new THREE.MeshNormalMaterial({ side: THREE.DoubleSide });
    }
    modelScene.add(terrainMesh);
    window.appState.terrainMesh = terrainMesh;
    needsRender = true;
    updatePuzzlePreview();
}

// ─────────────────────────────────────────────────────────────────────────────
// Color helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Return [r,g,b] in 0–1 range for elevation t in [0,1] using colormap cmap. */
function _elevColor(t, cmap) {
    if (cmap === 'none') return [0.55, 0.55, 0.55];
    // dem-loader.js is always loaded before this file
    return window.mapElevationToColor(t, cmap);
}

/** Recolor the current mesh with a new colormap (no server round-trip). */
function _rebuildColors(cmap) {
    if (!terrainMesh) return;
    if (cmap === 'satellite') { _applySatelliteTexture(); return; }
    const mat = terrainMesh._savedMaterial || terrainMesh.material;
    if (mat.map) { mat.map.dispose(); mat.map = null; }
    const geo    = terrainMesh.geometry;
    const posArr = geo.attributes.position.array;
    const n      = posArr.length / 3;

    let yMin = Infinity, yMax = -Infinity;
    for (let i = 0; i < n; i++) {
        const y = posArr[i * 3 + 1];
        if (y < yMin) yMin = y;
        if (y > yMax) yMax = y;
    }
    const yRange = Math.max(yMax - yMin, 1);

    const colors = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
        const rgb = _elevColor((posArr[i * 3 + 1] - yMin) / yRange, cmap);
        colors[i * 3] = rgb[0]; colors[i * 3 + 1] = rgb[1]; colors[i * 3 + 2] = rgb[2];
    }

    if (cmap === 'none') {
        terrainMesh.material.vertexColors = false;
        terrainMesh.material.color.set(0x8fbc8f);
    } else {
        geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        geo.attributes.color.needsUpdate = true;
        terrainMesh.material.vertexColors = true;
        terrainMesh.material.color.set(0xffffff);
    }
    terrainMesh.material.needsUpdate = true;
    needsRender = true;
}

/** Same box as the loaded DEM (the satellite canvas is fetched per box). */
function _sameBbox(a, b) {
    return !!a && !!b && ['north', 'south', 'east', 'west']
        .every(k => Math.abs(Number(a[k]) - Number(b[k])) < 1e-9);
}

/**
 * Drape the satellite image of the loaded DEM box over the mesh as a texture.
 * Uses the Edit tab's satellite canvas (appState.satImgSourceCanvas, same box and
 * projection as the DEM, so pixel (col, row) of the mesh lands on the same place
 * in the image) and fetches it first when it is missing or for another box.
 * Texture detail does not depend on the adaptive mesh density.
 */
async function _applySatelliteTexture() {
    const mesh = terrainMesh;
    if (!mesh) return;
    const st = window.appState || {};
    // At least the DEM grid's resolution (texture detail is free), at most 2048 px.
    const grid = st.generatedModelData ? Math.max(st.generatedModelData.width, st.generatedModelData.height) : 1024;
    const want = Math.min(2048, Math.max(1024, grid));
    const have = st.satImgSourceCanvas ? Math.max(st.satImgSourceCanvas.width, st.satImgSourceCanvas.height) : 0;
    if (!have || have < 0.9 * want || !_sameBbox(st._satImgBbox, st.currentDemBbox)) {
        try { await window.loadSatelliteRGBImage?.({ dim: want }); } catch (_) { /* toast shown there */ }
    }
    const canvas = window.appState?.satImgSourceCanvas;
    if (mesh !== terrainMesh) return;            // rebuilt while fetching: the new mesh re-drapes
    if (!canvas) {
        window.showToast?.('No satellite image for this region - showing the terrain colormap.', 'warning');
        _rebuildColors('terrain');
        return;
    }
    const mat = mesh._savedMaterial || mesh.material;
    mat.map?.dispose();
    const tex = new THREE.CanvasTexture(canvas);
    tex.anisotropy = modelRenderer?.capabilities?.getMaxAnisotropy?.() || 1;
    mat.map = tex;
    mat.vertexColors = false;
    mat.color.set(0xffffff);
    mat.needsUpdate = true;
    needsRender = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// Normals-debug material toggle
// ─────────────────────────────────────────────────────────────────────────────

function setViewerNormals(active) {
    _normalsActive = active;
    if (!terrainMesh) return;
    if (active && !(terrainMesh.material instanceof THREE.MeshNormalMaterial)) {
        terrainMesh._savedMaterial = terrainMesh.material;
        terrainMesh.material = new THREE.MeshNormalMaterial({ side: THREE.DoubleSide });
    } else if (!active && terrainMesh._savedMaterial) {
        terrainMesh.material.dispose();
        terrainMesh.material = terrainMesh._savedMaterial;
        delete terrainMesh._savedMaterial;
    }
    terrainMesh.material.needsUpdate = true;
    needsRender = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// HUD
// ─────────────────────────────────────────────────────────────────────────────

function _updateHud(data) {
    const hud = document.getElementById('viewerHud');
    if (!hud) return;
    const pv = data.preview;
    const lines = [`${data.face_count.toLocaleString()} faces  |  ${data.cols}×${data.rows} pts`
        + (pv?.adaptive ? `  |  adaptive ±${pv.max_error_mm} mm${pv.stride > 1 ? `, every ${pv.stride} px` : ''}` : '')];
    const r = window.appState?.selectedRegion;
    if (r) lines.push(`~${haversineDiagKm(r.north, r.south, r.east, r.west).toFixed(1)} km diagonal`);
    hud.textContent = lines.join('\n');
}

// ─────────────────────────────────────────────────────────────────────────────
// Puzzle preview
// ─────────────────────────────────────────────────────────────────────────────

/**
 * The puzzle grid the Export tab describes: Split/Puzzle's Columns × Rows when
 * that is enabled, else the City Model's grid from its max piece size (the
 * server's plan_grid rule). null when neither puzzle is on or no mesh is shown.
 */
function _puzzleGrid() {
    const g = geometry_scale_for_overlays;
    if (!g.modelWidthMm) return null;
    const num = (id, fallback) => _num(id, fallback, { positive: true });
    let cols, rows;
    if (document.getElementById('puzzleEnabled')?.checked) {
        cols = Math.max(1, Math.round(num('splitCols', 3)));
        rows = Math.max(1, Math.round(num('splitRows', 3)));
    } else if (document.getElementById('cityPuzzleEnabled')?.checked) {
        const piece = num('cityPieceMm', 200);
        ({ cols, rows } = piecesNeeded(g.modelWidthMm, g.modelDepthMm, { w: piece, h: piece }, piece));
    } else {
        return null;
    }
    const key = gridKey(cols, rows, g.modelWidthMm, g.modelDepthMm);
    const saved = window.appState?.puzzleEdges;
    const own = saved && saved.key === key;
    return {
        cols, rows, key,
        colEdges: own ? saved.cols : evenEdges(g.modelWidthMm, cols),
        rowEdges: own ? saved.rows : evenEdges(g.modelDepthMm, rows),
        minGap: minPieceMm(num('splitKnobWidth', 20), num('splitKnobDepth', 8),
            _num('splitClearance', 0.3)),
    };
}

/**
 * Dragged cut positions for the export request, if the drawn grid is cols × rows
 * (or whatever is drawn, when called without arguments); null for an even split.
 */
function puzzleEdgesFor(cols, rows) {
    const grid = _puzzleGrid();
    if (!grid || (cols !== undefined && (grid.cols !== cols || grid.rows !== rows))) return null;
    if (!isCustom(grid.colEdges) && !isCustom(grid.rowEdges)) return null;
    return { col_edges_mm: roundEdges(grid.colEdges), row_edges_mm: roundEdges(grid.rowEdges) };
}

function updatePuzzlePreview() {
    if (!terrainMesh || !modelScene) return;
    const old = modelScene.getObjectByName('puzzleCuts');
    if (old) {
        old.traverse(c => { c.geometry?.dispose(); c.material?.map?.dispose(); c.material?.dispose(); });
        modelScene.remove(old);
    }
    const grid = _puzzleGrid();
    if (!grid) { needsRender = true; return; }

    // Model mm (x from west, y from south) -> display units, as the mesh is mapped.
    const g = geometry_scale_for_overlays;
    const S = g.scale || 1;
    const dx = (xMm) => xMm * S - g.xOffset;
    const dz = (yMm) => (g.modelDepthMm - yMm) * S - g.zOffset;
    const x0 = dx(0), x1 = dx(g.modelWidthMm), zS = dz(0), zN = dz(g.modelDepthMm);
    const verts = [];
    for (let i = 1; i < grid.cols; i++) { const x = dx(grid.colEdges[i]); verts.push(x, 0, zS, x, 0, zN); }
    for (let j = 1; j < grid.rows; j++) { const z = dz(grid.rowEdges[j]); verts.push(x0, 0, z, x1, 0, z); }
    const group = new THREE.Group();
    group.name = 'puzzleCuts';
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
    group.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0xff2222, depthTest: false })));
    if (grid.cols * grid.rows > 1) {
        const custom = isCustom(grid.colEdges) || isCustom(grid.rowEdges);
        const label = _makeTextSprite(`${grid.cols}×${grid.rows}${custom ? ' custom' : ''} · drag cuts`,
            { fontSize: 15, color: '#ff9999' });
        label.scale.multiplyScalar(0.6);
        label.position.set(0, 0, zS + 6);
        group.add(label);
    }
    group.position.y = (g.totalHeightMm || 0) * S + 0.5;
    modelScene.add(group);
    needsRender = true;
}

// ── Dragging cut lines ──────────────────────────────────────────────────────
let _cutDrag = null;    // { axis: 'cols' | 'rows', index, grid } while a cut is dragged
let _raycaster = null;

/** Model mm {x from west, y from south} under the mouse, in the cut lines' plane. */
function _mouseToModelMm(e) {
    const lines = modelScene?.getObjectByName('puzzleCuts');
    if (!lines || !modelCamera) return null;
    const rect = modelRenderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1);
    _raycaster = _raycaster || new THREE.Raycaster();
    _raycaster.setFromCamera(ndc, modelCamera);
    const hit = new THREE.Vector3();
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -lines.position.y);
    if (!_raycaster.ray.intersectPlane(plane, hit)) return null;
    const g = geometry_scale_for_overlays;
    const S = g.scale || 1;
    return { x: (hit.x + g.xOffset) / S, y: g.modelDepthMm - (hit.z + g.zOffset) / S };
}

/** The interior cut line under the mouse: { axis, index, grid, cursor } or null. */
function _cutUnderMouse(e) {
    if (!modelScene?.getObjectByName('puzzleCuts')) return null;
    const grid = _puzzleGrid();
    const p = grid && _mouseToModelMm(e);
    if (!p) return null;
    const g = geometry_scale_for_overlays;
    if (p.x < 0 || p.y < 0 || p.x > g.modelWidthMm || p.y > g.modelDepthMm) return null;
    const tol = Math.max(2, 0.015 * Math.max(g.modelWidthMm, g.modelDepthMm));
    const ci = nearestEdge(grid.colEdges, p.x, tol);
    const ri = nearestEdge(grid.rowEdges, p.y, tol);
    const dc = ci > 0 ? Math.abs(grid.colEdges[ci] - p.x) : Infinity;
    const dr = ri > 0 ? Math.abs(grid.rowEdges[ri] - p.y) : Infinity;
    if (dc === Infinity && dr === Infinity) return null;
    return dc <= dr ? { axis: 'cols', index: ci, grid, cursor: 'ew-resize' }
        : { axis: 'rows', index: ri, grid, cursor: 'ns-resize' };
}

function _startCutDrag(e) {
    _cutDrag = _cutUnderMouse(e);
    return !!_cutDrag;
}

function _moveCutDrag(e) {
    const p = _mouseToModelMm(e);
    if (!p) return;
    const d = _cutDrag;
    const key = d.axis === 'cols' ? 'colEdges' : 'rowEdges';
    d.grid[key] = moveEdge(d.grid[key], d.index, d.axis === 'cols' ? p.x : p.y, d.grid.minGap);
    window.appState.puzzleEdges = { cols: d.grid.colEdges, rows: d.grid.rowEdges, key: d.grid.key };
    updatePuzzlePreview();
}

function _endCutDrag() {
    if (!_cutDrag) return;
    _cutDrag = null;
    window.dispatchEvent(new CustomEvent('puzzle-edges-changed'));
}

/** Back to the even split (the Export tab's "Reset cuts"). */
function resetPuzzleEdges() {
    window.appState.puzzleEdges = null;
    updatePuzzlePreview();
    window.dispatchEvent(new CustomEvent('puzzle-edges-changed'));
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function haversineDiagKm(north, south, east, west) {
    const R    = 6371;
    const dLat = (north - south) * Math.PI / 180;
    const mid  = ((north + south) / 2) * Math.PI / 180;
    const dLon = (east - west) * Math.PI / 180;
    const dy   = R * dLat;
    const dx   = R * Math.cos(mid) * dLon;
    return Math.sqrt(dx * dx + dy * dy);
}

// Single source of truth for the city/building-data region-size limit.
// Previously the manual "Load Cities" button, its disable-gate, and the bulk
// loadAllLayers() path each hardcoded their own number (10 in two places, 15
// in the third) — unified here so they can never drift apart again.
window.CITY_MAX_DIAG_KM = 10;

// Above CITY_MAX_DIAG_KM, city data is not skipped outright — a coarser tier
// (roads + water + large buildings only, no walls/small-building detail)
// fetches up to this larger diagonal instead. Must match
// config.MAX_BBOX_DIAGONAL_KM_COARSE server-side.
window.CITY_COARSE_MAX_DIAG_KM = 25;

function setViewerAutoRotate(val) {
    viewerAutoRotate = val;
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.previewModelIn3D     = previewModelIn3D;
window.haversineDiagKm      = haversineDiagKm;
window.updatePuzzlePreview  = updatePuzzlePreview;
window._readBuildParams     = _readBuildParams;
window.setViewerAutoRotate  = setViewerAutoRotate;
window.resetViewerCamera    = resetViewerCamera;
window.rebuildViewerColors  = _rebuildColors;
window.setViewerNormals     = setViewerNormals;
window.updateBedOutline     = updateBedOutline;
window.puzzleEdgesFor       = puzzleEdgesFor;
window.resetPuzzleEdges     = resetPuzzleEdges;

// ─────────────────────────────────────────────────────────────────────────────
// Auto-rebuild wiring
// ─────────────────────────────────────────────────────────────────────────────
// Replaces the manual Generate/Preview buttons. The mesh is rebuilt:
//   1. when any Fetch-tab input changes (debounced),
//   2. when the model container becomes visible (entering the Extrude view),
//   3. when a fresh DEM is loaded while the Extrude view is already open.
// All triggers funnel into _scheduleRebuild() which guards on (DEM available)
// AND (model container visible) before firing previewModelIn3D().

const _FETCH_INPUT_IDS = [
    'mmPerPixel', 'exportModelHeight', 'exportBaseHeight',
    'exportExaggeration', 'exportZMode', 'exportMedian', 'exportSeaLevelCap', 'viewerSolidPreview',
    'exportEngraveLabel', 'exportContours', 'exportContourInterval', 'exportContourStyle',
];
// Text input: use 'input' (not 'change') so the preview updates as you type,
// not only after the field loses focus.
const _FETCH_INPUT_IDS_LIVE = ['exportLabelText'];

let _rebuildTimer = null;

function _scheduleRebuild() {
    if (_rebuildTimer) clearTimeout(_rebuildTimer);
    _rebuildTimer = setTimeout(_doAutoRebuild, 200);
}

function _doAutoRebuild() {
    _rebuildTimer = null;
    const ldd = window.appState?.lastDemData;
    if (!ldd?.values?.length) return;            // no DEM yet — silent skip
    const mc = document.getElementById('modelContainer');
    if (!mc) return;
    if (mc.classList.contains('hidden')) return; // not visible — silent skip
    if (mc.style.display === 'none') return;
    previewModelIn3D();
}

function _attachAutoRebuildListeners() {
    for (const id of _FETCH_INPUT_IDS) {
        document.getElementById(id)?.addEventListener('change', _scheduleRebuild);
    }
    for (const id of _FETCH_INPUT_IDS_LIVE) {
        document.getElementById(id)?.addEventListener('input', _scheduleRebuild);
    }
    // First build when the user switches into the Extrude view.
    const mc = document.getElementById('modelContainer');
    if (mc) {
        new MutationObserver(_scheduleRebuild)
            .observe(mc, { attributes: true, attributeFilter: ['class', 'style'] });
    }
}

window._modelViewerAutoRebuild = _scheduleRebuild;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _attachAutoRebuildListeners);
} else {
    // Defer one tick so the Vue components have mounted their inputs.
    setTimeout(_attachAutoRebuildListeners, 0);
}
