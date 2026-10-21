/**
 * modules/model-viewer.js — Three.js 3D terrain viewer + puzzle export.
 *
 * Loaded as a plain <script> before app.js.
 *
 * previewModelIn3D() (build/replace terrain in the viewer) is module-local; the
 * auto-preview watcher at the bottom of this file calls it.
 *
 * Public API (all on window):
 *   haversineDiagKm(N, S, E, W)                — bbox diagonal in km
 *   updatePuzzlePreview()                       — draw puzzle cut lines in viewer
 *   window.setViewerAutoRotate(val)             — orbit the camera around the model
 *   window.resetViewerCamera()                  — fit camera to everything shown
 *   window.rebuildViewerColors(cmap)            — recolor mesh from a colormap name
 *   window.setViewerNormals(bool)               — toggle normals-debug material (every mesh)
 *   window.setViewerWireframe(bool)             — toggle wireframe (every mesh)
 *   window.refreshCityLayers()                  — City model layer edited: quick view now,
 *                                                 finished model rebuilt after 1.5 s
 *   window.updateBedOutline()                   — redraw the printer-bed outline
 *   window.puzzleEdgesFor(cols, rows)           — dragged cut positions {col_edges_mm,
 *                                                 row_edges_mm} for that grid, or null
 *   window.resetPuzzleEdges()                   — back to the even split
 *   window.clearViewerModel()                   — drop the previous region's mesh,
 *                                                 overlays, HUD and dragged cuts
 *
 * Puzzle cut lines (updatePuzzlePreview) are drawn at appState.puzzleEdges when
 * they belong to the current grid, else evenly, with the jigsaw knobs the
 * export sends (modules/export/puzzle-cuts.js jigsawCuts), draped on the terrain.
 * appState.cutView picks hidden (the default), shown, or 'edit', where a plain
 * left drag near a line moves that cut and fires 'puzzle-edges-changed'.
 *
 * Zoom: the wheel scales the camera distance by exp(deltaY * 0.0017) (~18 % per notch), toward
 * the terrain point under the cursor when zooming in; the minimum distance is 2 % of the fitted
 * distance. While dragging or zooming (and 150 ms after) the drawing buffer is at pixel ratio 1.
 *
 * The HUD (#viewerHud, top left) holds the mesh stats and the printer bed's
 * size and fit warning, so nothing is written over the model itself.
 *
 * State exposed on window.appState:
 *   window.appState.terrainMesh  — current terrain mesh (or null)
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
import { buildingPrisms, demGroundMm } from './city-quickview.js';
import { parseModelParts, partColor, staleParts, toViewerFrame } from './city-fullmodel.js';
import { buildingsWithOverrides, hasOverrides } from '../layers/building-heights.js';
import {
    MIN_AUTO_PIECE_MM, evenEdges, gridKey, isCustom, jigsawCuts, minPieceMm, moveEdge,
    nearestEdge, roundEdges,
} from './puzzle-cuts.js';

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let modelScene    = null;
let modelCamera   = null;
let modelRenderer = null;
let terrainMesh   = null;
let cityQuickMesh = null;      // buildings on the preview until the City Model replaces them
let cityFullGroup = null;      // the finished City Model's parts (city-fullmodel.js)
// The background City Model build: its request key and layers, state ('idle' |
// 'scheduled' | 'running' | 'done' | 'error'), finished parts file, the preview
// status line its progress is appended to, and the display frame of its preview.
const _full = { key: '', layers: {}, state: 'idle', buffer: null, timer: null, frame: null,
                status: '', run: null };
// What a City model layer edit needs from the last good preview (refreshCityLayers):
// the few fields of its data and its status line for a given quick-view count.
let _lastPreview = null;
let viewerAutoRotate = false;
let needsRender   = true;
let _normalsActive = false;     // true when MeshNormalMaterial is active
let _wireframeActive = false;   // Wireframe checkbox, applied to every displayed mesh
let _satToken = 0;              // latest satellite drape request (_applySatelliteTexture)
let _resizeObserver = null;     // reference for cleanup on re-init
let _loopId = 0;                // the render loop that is current; a re-init starts a new one
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
    if (_resizeObserver) {
        _resizeObserver.disconnect();
        _resizeObserver = null;
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
    // The checkbox may have been ticked before the first preview built the viewer.
    if (document.getElementById('viewerWireframe')?.checked) _wireframeActive = true;

    // Watches the container rather than the window: a window resize while the
    // Extrude view was hidden used to be ignored, leaving the canvas at the old
    // size when the view came back. A hidden container reports 0×0 and is sized
    // again when it is shown.
    _resizeObserver = new ResizeObserver(() => {
        const w = container.clientWidth, h = container.clientHeight;
        if (!w || !h) return;
        modelCamera.aspect = w / h;
        modelCamera.updateProjectionMatrix();
        modelRenderer.setSize(w, h);
        needsRender = true;
    });
    _resizeObserver.observe(container);

    const loop = ++_loopId;
    (function animate() {
        if (loop !== _loopId) return;
        requestAnimationFrame(animate);
        // Nothing to draw while the Extrude view or the browser tab is hidden; a
        // change made meanwhile stays pending in needsRender until it is shown.
        if (document.hidden || !container.isConnected || container.offsetParent === null) return;
        if (viewerAutoRotate && !_isDragging && !_cutDrag) _orbitBy(0.005, 0);
        if (needsRender) {
            modelRenderer.render(modelScene, modelCamera);
            needsRender = false;
        }
    })();
}

// ─────────────────────────────────────────────────────────────────────────────
// Orbit / pan / zoom controls
// Left-drag = orbit,  Shift+drag = pan,  wheel = zoom toward the cursor (exponential in deltaY)
// ─────────────────────────────────────────────────────────────────────────────

function _setupOrbitControls() {
    const el = modelRenderer.domElement;

    el.addEventListener('mousedown', e => {
        if (e.button === 0 && !e.shiftKey && _startCutDrag(e)) { e.preventDefault(); return; }
        _isDragging = true;
        _isPanning  = e.shiftKey || e.button === 1;
        _prevMouse  = { x: e.clientX, y: e.clientY };
        _interacting();
        e.preventDefault();
    });

    el.addEventListener('mousemove', e => {
        if (_cutDrag) { _moveCutDrag(e); return; }
        if (!_isDragging) { el.style.cursor = _cutUnderMouse(e)?.cursor || ''; return; }
        const dx = e.clientX - _prevMouse.x;
        const dy = e.clientY - _prevMouse.y;
        _prevMouse = { x: e.clientX, y: e.clientY };
        _interacting();
        if (_isPanning) _orbitPan(dx, dy); else _orbitRotate(dx, dy);
    });

    el.addEventListener('mouseup',    () => { _isDragging = false; _endCutDrag(); });
    el.addEventListener('mouseleave', () => { _isDragging = false; _endCutDrag(); });

    el.addEventListener('wheel', e => {
        e.preventDefault();
        _wheelZoom(e);
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
            _interacting();
            _orbitRotate(dx, dy);
        } else if (ts.length === 2) {
            const dist    = Math.hypot(ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);
            const camDist = modelCamera.position.distanceTo(orbitTarget);
            const newDist = Math.max(_zoomMinDist, Math.min(ZOOM_MAX_DIST, camDist * (pinchDist0 / Math.max(dist, 1))));
            _rotOffset.copy(modelCamera.position).sub(orbitTarget).normalize().multiplyScalar(newDist);
            modelCamera.position.copy(orbitTarget).add(_rotOffset);
            pinchDist0 = dist;
            _interacting();
            needsRender = true;
        }
    }, { passive: false });

    el.addEventListener('touchend', () => { _isDragging = false; });
}

// ── Zoom ────────────────────────────────────────────────────────────────────
// A wheel notch (deltaY 100 px) changes the distance by ~18 %, a trackpad's small
// deltas by proportionally less. Zooming in dollies toward the point under the cursor.
const WHEEL_ZOOM_K     = 0.0017;   // ln(1.18) / 100 px
const WHEEL_MAX_PX     = 300;      // per event, so a fast flick cannot jump the whole range
const ZOOM_MAX_DIST    = 800;
const ZOOM_MIN_DEFAULT = 10;
const ZOOM_MIN_FRAC    = 0.02;     // of the fitted distance (~3 for a 150 fit)
const INTERACT_IDLE_MS = 150;      // full resolution returns this long after the last input
let _zoomMinDist = ZOOM_MIN_DEFAULT;
let _zoomHit = null;               // { x, y, point } cursor hit cached for the current wheel burst
let _zoomHitAt = 0;
let _interactTimer = null;
let _lowRes = false;

/** Drawing-buffer pixel ratio only; the canvas CSS size is left alone. */
function _setPixelRatio(ratio) {
    if (!modelRenderer || modelRenderer.getPixelRatio() === ratio) return;
    const sz = modelRenderer.getSize(new THREE.Vector2());
    modelRenderer.setPixelRatio(ratio);
    modelRenderer.setSize(sz.x, sz.y, false);
    needsRender = true;
}

/** Drag or zoom in progress: draw at pixel ratio 1, restore the full ratio after a short idle. */
function _interacting() {
    if (!modelRenderer) return;
    if (!_lowRes) { _lowRes = true; _setPixelRatio(1); }
    clearTimeout(_interactTimer);
    _interactTimer = setTimeout(() => {
        _lowRes = false;
        _interactTimer = null;
        _zoomHit = null;
        _setPixelRatio(Math.min(window.devicePixelRatio, 2));
    }, INTERACT_IDLE_MS);
}

/** Point on the terrain under the client position (cached within a wheel burst), or null. */
function _cursorHit(e) {
    const now = performance.now();
    if (_zoomHit && now - _zoomHitAt < 400
        && Math.abs(_zoomHit.x - e.clientX) < 3 && Math.abs(_zoomHit.y - e.clientY) < 3) {
        return _zoomHit.point;
    }
    // The terrain mesh stands in for the city parts: they hold ~2 M triangles and
    // a raycast through them per wheel event would cost more than the zoom saves.
    let point = null;
    if (terrainMesh && modelCamera) {
        const rect = modelRenderer.domElement.getBoundingClientRect();
        const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1,
            -((e.clientY - rect.top) / rect.height) * 2 + 1);
        _raycaster = _raycaster || new THREE.Raycaster();
        _raycaster.setFromCamera(ndc, modelCamera);
        point = _raycaster.intersectObject(terrainMesh, false)[0]?.point.clone() || null;
    }
    _zoomHit = { x: e.clientX, y: e.clientY, point };
    _zoomHitAt = now;
    return point;
}

/** Wheel zoom: exponential in deltaY; zooming in keeps the point under the cursor in place. */
function _wheelZoom(e) {
    const px = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
    const delta = Math.max(-WHEEL_MAX_PX, Math.min(WHEEL_MAX_PX, px));
    if (!delta) return;
    const dist    = modelCamera.position.distanceTo(orbitTarget);
    const newDist = Math.max(_zoomMinDist, Math.min(ZOOM_MAX_DIST, dist * Math.exp(delta * WHEEL_ZOOM_K)));
    const f = newDist / dist;
    if (f === 1) return;
    const hit = f < 1 ? _cursorHit(e) : null;
    if (hit) {
        // Scale both camera and target about the hit point: it stays under the cursor.
        modelCamera.position.sub(hit).multiplyScalar(f).add(hit);
        orbitTarget.sub(hit).multiplyScalar(f).add(hit);
    } else {
        _rotOffset.copy(modelCamera.position).sub(orbitTarget).multiplyScalar(f);
        modelCamera.position.copy(orbitTarget).add(_rotOffset);
    }
    _interacting();
    needsRender = true;
}

function _orbitRotate(dx, dy) {
    _orbitBy(dx * 0.012, dy * 0.012);
}

/** Turn the camera about orbitTarget: dTheta around the vertical, dPhi towards the top (radians). */
function _orbitBy(dTheta, dPhi) {
    _rotOffset.copy(modelCamera.position).sub(orbitTarget);
    _rotSph.setFromVector3(_rotOffset);
    _rotSph.theta -= dTheta;
    _rotSph.phi    = Math.max(0.05, Math.min(Math.PI - 0.05, _rotSph.phi - dPhi));
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

/** Bounds of every displayed model mesh: the terrain or the finished model, and the buildings. */
function _visibleBounds() {
    const box = new THREE.Box3();
    // Box3.setFromObject counts hidden children too (the preview terrain under the
    // finished model, a disabled layer's part), so only the visible meshes go in.
    for (const root of [terrainMesh, cityQuickMesh, cityFullGroup]) {
        root?.traverseVisible(o => { if (o.isMesh) box.expandByObject(o); });
    }
    return box;
}

function _fitCamera(box) {
    if (!modelCamera || box.isEmpty()) return;
    const center  = box.getCenter(new THREE.Vector3());
    const size    = box.getSize(new THREE.Vector3());
    const maxDim  = Math.max(size.x, size.y, size.z);
    const fov     = modelCamera.fov * Math.PI / 180;
    const fitDist = (maxDim / 2) / Math.tan(fov / 2) * 1.4;
    _zoomMinDist = Math.max(1, fitDist * ZOOM_MIN_FRAC);
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
    _fitCamera(_visibleBounds());
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
    // The grid's cell size is a HUD line, not a sprite: as a sprite it sat over
    // the model itself from most camera angles. The km diagonal is already a HUD
    // line (_updateHud), so it has no sprite either.
    _hudGrid = `grid ${Math.round(cellMm)} mm`;
    _renderHud();
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
    if (!terrainMesh || !g.widthMm) { _hudBed = null; _renderHud(); needsRender = true; return; }

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
    // The bed's size and the fit warning read in the HUD, off the model.
    _hudBed = { text: `bed ${bed.w}×${bed.h} mm${fits ? '' : ' — model larger'}`, fits };
    _renderHud();
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
        model_height:     _num('exportModelHeight', 20, { positive: true }),
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
        /** Forget the last request (the viewer was cleared): an identical one is sent again. */
        reset() {
            last = null;
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

    if (!modelRenderer) initModelViewer();

    const hadModel = !!window.appState.generatedModelData;
    try {
        const { data, error: previewErr } = await window.api.export.preview(body);
        if (seq !== _previewSeq) {                // superseded by a newer rebuild or a clear
            _previewGate.settle(ticket, false);
            return;
        }
        if (previewErr) throw new Error(previewErr);

        const cmap = document.getElementById('viewerColormap')?.value || 'terrain';
        _replaceMesh(_buildMeshFromPreview(data, cmap));
        if (cmap === 'satellite') _applySatelliteTexture();
        _updateHud(data);
        _updateSceneOverlays(data);
        const quick = _updateCityQuickView(data);

        window.appState.generatedModelData = {
            values: ldd.values, width: ldd.width, height: ldd.height,
            vmin: ldd.vmin, vmax: ldd.vmax,
            // What the export sends: the same DEM settings and build fields
            // this preview was made from.
            demSettings: ds,
            buildParams: build,
            // Printed height (mm) of this preview, base included: the Printed size card.
            zMaxMm: data.z_max,
            // Legacy names, still read by the cross-section export.
            mmPerPixel: build.mm_per_pixel,
            modelHeight: build.model_height,
            exaggeration: build.exaggeration,
            baseHeight: build.base_height,
        };
        _previewGate.settle(ticket, true);
        window.appState.modelPreviewState = 'ready';
        window._setExportButtonsEnabled?.(true);
        window.updatePrintDimensions?.();     // peak height from this preview's z_max
        window.appState._updateWorkflowStepper?.();
        const w = ldd.width, h = ldd.height;
        const warn = data.composite_error ? `  ⚠ composite failed, raw DEM shown` : '';
        const statusFor = (n) => `Preview: ${w}×${h}, `
            + `${data.face_count.toLocaleString()} faces, `
            + `${(data.z_max ?? 0).toFixed(1)} mm tall`
            + (n ? `, ${n.toLocaleString()} buildings (quick view)` : '') + warn;
        // Only what the quick view and the City Model frame read: the vertices are large.
        _lastPreview = {
            data: { scale: data.scale, cols: data.cols, rows: data.rows, mm_per_pixel: data.mm_per_pixel },
            statusFor,
        };
        if (statusEl) statusEl.textContent = statusFor(quick);
        _scheduleFullModel(_lastPreview.data, statusFor(quick));
        // The request reads survey coverage synchronously; asked for the first time it is
        // still 'unknown' and the trees are left out. Rebuild once the answer is in.
        const preview = _lastPreview;
        window.citySurveyCoverageReady?.().then(() => {
            if (_lastPreview === preview && _fullModelRequest()?.key !== _full.key) refreshCityLayers();
        });
        resetViewerCamera();
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
        // The background City Model belongs to the last good preview: stop it, or
        // its poll writes over the error below and its result is drawn anyway.
        _lastPreview = null;
        _cancelFullModel();
        window._setExportButtonsEnabled?.(false);
        window.appState._updateWorkflowStepper?.();
        if (statusEl) statusEl.textContent = '❌ ' + e.message;
        window.showToast('Preview failed: ' + e.message, 'error');
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

    if (cmap !== 'none') _paintWater(colors, data.water_idx, data.water_pct);

    // Top surface height (mm) per DEM pixel, so the cut lines can lie on the terrain.
    const topZ = _topSurfaceGrid(rawVerts, rawFaces, data.cols, data.rows);

    // Stash physical dims (mm) and the display-scale factor for overlay code.
    geometry_scale_for_overlays = {
        scale: SCALE,
        widthMm, depthMm, totalHeightMm,
        // The mesh spans pixel centres 0..cols-1: the model the server cuts.
        modelWidthMm: Math.max(data.cols - 1, 1) * mmPerPx,
        modelDepthMm: Math.max(data.rows - 1, 1) * mmPerPx,
        xOffset, zOffset,
        topZ, cols: data.cols, rows: data.rows, mmPerPx,
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
    const mesh = new THREE.Mesh(geometry, material);
    // Kept so a colormap change (_rebuildColors) paints the water again.
    mesh.userData.water = { idx: data.water_idx, pct: data.water_pct };
    return mesh;
}

/**
 * Highest mesh surface (mm) over each DEM pixel. The mesh is adaptive, so most pixels
 * have no vertex: every face is rasterised over the pixels it covers (side walls have
 * no area in plan and drop out), keeping the highest z, as the cut lines need it.
 */
function _topSurfaceGrid(verts, faces, cols, rows) {
    const top = new Float32Array(cols * rows).fill(-Infinity);
    for (const [a, b, c] of faces) {
        const [x0, y0, z0] = verts[a], [x1, y1, z1] = verts[b], [x2, y2, z2] = verts[c];
        const det = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
        if (Math.abs(det) < 1e-9) continue;
        const cMin = Math.max(0, Math.ceil(Math.min(x0, x1, x2))), cMax = Math.min(cols - 1, Math.floor(Math.max(x0, x1, x2)));
        const rMin = Math.max(0, Math.ceil(Math.min(y0, y1, y2))), rMax = Math.min(rows - 1, Math.floor(Math.max(y0, y1, y2)));
        for (let r = rMin; r <= rMax; r++) {
            for (let col = cMin; col <= cMax; col++) {
                const l0 = ((y1 - y2) * (col - x2) + (x2 - x1) * (r - y2)) / det;
                const l1 = ((y2 - y0) * (col - x2) + (x0 - x2) * (r - y2)) / det;
                const l2 = 1 - l0 - l1;
                if (l0 < -1e-6 || l1 < -1e-6 || l2 < -1e-6) continue;
                const z = l0 * z0 + l1 * z1 + l2 * z2, k = r * cols + col;
                if (z > top[k]) top[k] = z;
            }
        }
    }
    return top;
}

/**
 * Colour the carved rivers, lakes and the open sea like the Edit map does
 * (water-hydrology-combined.js): rivers (30, 100, 200), more opaque the deeper,
 * open sea (0, 100, 255). The server sends the water vertices and their relative
 * carve depth in % (200 = open sea). Opacity starts at 0, not Edit's 60/255: vertex
 * colours spread over whole triangles, so the Amazon's order-1 streams (62 % of the
 * cells) tinted all the land blue.
 */
function _paintWater(colors, idx, pct) {
    if (!idx?.length) return;
    for (let k = 0; k < idx.length; k++) {
        const sea = pct[k] > 100;
        const [r, g, b] = sea ? [0, 100 / 255, 1] : [30 / 255, 100 / 255, 200 / 255];
        const a = sea ? 150 / 255 : Math.min(1, pct[k] / 100) * 220 / 255;
        const i = idx[k] * 3;
        colors[i] = colors[i] * (1 - a) + r * a;
        colors[i + 1] = colors[i + 1] * (1 - a) + g * a;
        colors[i + 2] = colors[i + 2] * (1 - a) + b * a;
    }
}

/** The plain object behind a Vue reactive proxy (or the value itself). */
const _raw = (o) => o?.__v_raw ?? o;

/** True for a box the Download button builds as a City Model (≤ CITY_COARSE_MAX_DIAG_KM). */
function _isCityBox() {
    const b = window.appState?.currentDemBbox || window.appState?.selectedRegion;
    return !!b && haversineDiagKm(b.north, b.south, b.east, b.west) <= (window.CITY_COARSE_MAX_DIAG_KM ?? 25);
}

function _removeCityFull() {
    if (!cityFullGroup) return;
    modelScene.remove(cityFullGroup);
    cityFullGroup.traverse(o => { if (o.isMesh) _disposeMesh(o); });
    cityFullGroup = null;
    if (terrainMesh) terrainMesh.visible = true;   // hidden while the model's own terrain was shown
    needsRender = true;
}

/** Back to the preview terrain and the quick view when a shown finished model goes stale. */
function _fallBackToQuickView() {
    if (!cityFullGroup) return;
    _removeCityFull();
    if (_lastPreview) _full.status = _lastPreview.statusFor(_updateCityQuickView(_lastPreview.data));
}

/** Append to the preview's status line; a building or failed preview owns the line instead. */
function _setStatusSuffix(base, suffix) {
    if (window.appState?.modelPreviewState !== 'ready') return;
    const el = document.getElementById('modelStatus');
    if (el) el.textContent = base + (suffix ? ` · ${suffix}` : '');
}

/** Drop the background City Model build: a running poll returns on its next tick. */
function _cancelFullModel() {
    clearTimeout(_full.timer);
    Object.assign(_full, { key: '', layers: {}, state: 'idle', buffer: null, timer: null,
        status: '', run: null });
}

/** The City Model request the Download button would send now, or null outside a city box. */
function _fullModelRequest() {
    if (!_isCityBox() || typeof window.cityExportBody !== 'function') return null;
    const body = window.cityExportBody();
    // The key spans the whole body, so it already carries reference_landmarks / roof_shapes;
    // named here so a change to any of them can never be served the cached model.
    const key = JSON.stringify({ ...body, reference_landmarks: !!body.reference_landmarks,
        roof_shapes: body.roof_shapes ?? 'auto', trees: body.layers?.trees?.enabled === true });
    return { key, layers: body.layers || {} };
}

/**
 * After a city preview, build the finished City Model in the background (the same
 * request the Download button sends, so the download then hits the model cache) and
 * show its parts in place of the quick view. Waits 1.5 s for the settings to settle.
 *
 * A new request takes over at once, not when its timer fires: the older build's
 * poll stops and its result is dropped, so it can never be drawn in the new
 * preview's frame (the server task still runs to its end). `keepShown` (a layer
 * edit, same preview) leaves the shown model up until the new one arrives.
 */
function _scheduleFullModel(data, baseStatus, { keepShown = false, req = _fullModelRequest() } = {}) {
    clearTimeout(_full.timer);
    _full.timer = null;
    if (!keepShown) _removeCityFull();
    if (!req) { _cancelFullModel(); _removeCityFull(); return; }
    const { scale, xOffset, zOffset } = geometry_scale_for_overlays;
    _full.frame = { scale, xOffset, zOffset, rows: data.rows, mmPerPx: data.mm_per_pixel ?? 1 };
    _full.status = baseStatus;
    const shown = cityFullGroup?.userData;
    if (shown?.key === req.key) {
        // A layer edit undone before its rebuild ran: the shown model is current again.
        Object.assign(_full, { key: req.key, layers: req.layers, state: 'done', buffer: shown.buffer, run: null });
        _setStatusSuffix(_withoutQuickView(baseStatus), _fullModelLabel(shown.faces));
        return;
    }
    if (req.key === _full.key && _full.buffer) { _showCityFull(); return; }
    if (req.key === _full.key && _full.state === 'running') return;
    Object.assign(_full, { key: req.key, layers: req.layers, state: 'scheduled', buffer: null, run: null });
    _full.timer = setTimeout(() => { _full.timer = null; _full.run = _runFullModel(req.key); }, 1500);
}

/**
 * The background build of this exact request, started now if it is only scheduled, or
 * null. The Download button waits for it (export-handlers.js::exportCityModel), so its
 * own build is a model-cache hit instead of a second full build (Granada: two
 * identical builds at once took 320 s).
 */
window.cityFullModelRun = function cityFullModelRun(key) {
    if (_full.timer && _full.key === key) {
        clearTimeout(_full.timer);
        _full.timer = null;
        _full.run = _runFullModel(key);
    }
    return _full.key === key && _full.state === 'running' ? _full.run : null;
};

async function _runFullModel(key) {
    if (_full.key !== key) return;
    _full.state = 'running';
    try {
        const start = await fetch('/api/export/start', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: key,
        });
        if (!start.ok) throw new Error(`HTTP ${start.status}`);
        const { task_id: id } = await start.json();
        for (;;) {
            await new Promise(r => setTimeout(r, 1000));
            if (_full.key !== key) return;                     // superseded or cancelled
            const st = await fetch(`/api/export/status/${id}`).then(r => (r.ok ? r.json() : null));
            if (_full.key !== key) return;
            if (!st) throw new Error('the build task is gone');
            if (st.status === 'error') throw new Error(st.message);
            if (st.status === 'complete') break;
            _setStatusSuffix(_full.status, `finished model ${st.progress}%: ${st.message}`);
        }
        const res = await fetch(`/api/export/model-parts/${id}`);
        if (!res.ok) throw new Error(`parts HTTP ${res.status}`);
        const buffer = await res.arrayBuffer();
        if (_full.key !== key) return;
        Object.assign(_full, { state: 'done', buffer });
        _showCityFull();
    } catch (e) {
        if (_full.key !== key) return;
        _full.state = 'error';
        console.warn('[preview] finished model failed:', e);
        _fallBackToQuickView();
        _setStatusSuffix(_full.status, `finished model failed: ${e.message}`);
    }
}

const _withoutQuickView = (status) => status.replace(/, [\d,]+ buildings \(quick view\)/, '');
const _fullModelLabel = (faces) => `finished model as printed (${faces.toLocaleString()} faces)`;

/**
 * Replace the quick view and the preview terrain with the finished model's parts
 * (_full.buffer, in _full.frame). Only over a good preview: a building or failed
 * one has no frame to put them in. A part file that cannot be drawn leaves the
 * preview as it is and says so in the status line; it never fails the preview.
 */
function _showCityFull() {
    if (window.appState?.modelPreviewState !== 'ready' || !_full.buffer || !modelScene) return;
    const buildingsColor = document.getElementById('layerBuildingsColor')?.value || '#c8b89a';
    const cmap = document.getElementById('viewerColormap')?.value || 'terrain';
    const group = new THREE.Group();
    let faces = 0;
    try {
        // parse a copy: toViewerFrame works in place and the buffer is shown again later
        for (const part of parseModelParts(_full.buffer.slice(0))) {
            toViewerFrame(part, _full.frame);
            // flatShading: the parts are welded solids, so a roof vertex is shared
            // with the walls under it; averaged normals smear that corner and make a
            // block of prisms read as torn, tilted shards.
            const mesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial({
                color: partColor(part.name, buildingsColor), flatShading: true }));
            mesh.name = part.name;                 // the layer's own name, for recolouring
            group.add(mesh);                       // before filling it, so a failure still frees it
            const g = mesh.geometry;
            g.setAttribute('position', new THREE.BufferAttribute(part.positions, 3));
            g.setIndex(new THREE.BufferAttribute(part.indices, 1));
            g.computeVertexNormals();
            if (part.name === 'terrain') {
                // The model's own terrain, not the preview mesh: every other part's
                // walls run from below the ground up (city_model.build_layer), and
                // against the preview terrain, which the layers are not cut into,
                // that buried wall showed. (The "hollow outline" look itself was
                // toViewerFrame turning every part inside out; fixed there.)
                // Flat-shaded like the rest: the solid is welded, so smooth normals
                // would round its vertical sides into the ground.
                mesh.material.vertexColors = true;
                g.setAttribute('color', new THREE.BufferAttribute(_heightColors(part.positions, cmap), 3));
            }
            _applyViewMode(mesh);
            faces += part.indices.length / 3;
        }
    } catch (e) {
        group.traverse(o => { if (o.isMesh) _disposeMesh(o); });
        console.warn('[preview] finished model could not be drawn:', e);
        _fallBackToQuickView();
        _setStatusSuffix(_full.status, `finished model could not be drawn: ${e.message}`);
        return;
    }
    _removeCityFull();
    _removeCityQuick();
    // The preview terrain is the same ground, one stage earlier: drawn together
    // they z-fight. Kept when the parts bring no ground of their own. Restored by
    // _removeCityFull.
    if (terrainMesh && group.getObjectByName('terrain')) terrainMesh.visible = false;
    // What refreshCityLayers compares a layer edit against, and restores on undo.
    group.userData = { key: _full.key, layers: _full.layers, buffer: _full.buffer, faces };
    cityFullGroup = group;
    modelScene.add(group);
    needsRender = true;
    _setStatusSuffix(_withoutQuickView(_full.status), _fullModelLabel(faces));
}

/**
 * A City model layer was edited (ModelContainer.vue's cityLayer_* controls). The
 * quick-view buildings follow the Buildings row at once; on a shown finished model,
 * the parts of layers now off are hidden at once and changed buildings give way
 * to the quick view. The finished model itself is rebuilt after the usual 1.5 s,
 * since the layers are part of its request (export-handlers.js::cityExportBody).
 */
function refreshCityLayers() {
    const last = _lastPreview;
    if (!last || !modelScene || window.appState?.modelPreviewState !== 'ready') return;
    const req = _fullModelRequest();
    let quick = 0;
    if (cityFullGroup) {
        const hide = staleParts(cityFullGroup.userData.layers, req?.layers);
        for (const m of cityFullGroup.children) m.visible = !hide.has(m.name);
        if (hide.has('buildings')) quick = _updateCityQuickView(last.data);
        else _removeCityQuick();
        needsRender = true;
    } else {
        quick = _updateCityQuickView(last.data);
    }
    const base = last.statusFor(quick);
    _setStatusSuffix(base, cityFullGroup ? 'finished model updates for the layer change' : '');
    _scheduleFullModel(last.data, base, { keepShown: true, req });
}

/**
 * Buildings on the terrain preview (city-quickview.js) for a city-sized box with the
 * Buildings layer on and loaded; removed otherwise. Returns the building count.
 */
function _updateCityQuickView(data) {
    _removeCityQuick();
    const st = window.appState;
    const bbox = st?.currentDemBbox || st?.selectedRegion;
    let feats = st?.osmCityData?.buildings?.features;
    const on = document.getElementById('cityLayer_buildings_enabled')?.checked ?? true;
    if (!bbox || !feats?.length || !on || !data.scale || !st.lastDemData?.values?.length
        || haversineDiagKm(bbox.north, bbox.south, bbox.east, bbox.west) > (window.CITY_COARSE_MAX_DIAG_KM ?? 25)) {
        return 0;
    }
    if (hasOverrides(st.cityHeightOverrides)) feats = buildingsWithOverrides(feats, st.cityHeightOverrides);
    const geoToPx = window._buildGeoToPx(bbox.north, bbox.south, bbox.east, bbox.west, 0, 0, data.cols, data.rows);
    const t0 = performance.now();
    const g = buildingPrisms(feats, {
        // geoToPx gives pixel-edge coordinates; preview vertices sit on pixel centres.
        toPx: (lat, lon) => { const p = geoToPx(lat, lon); return { x: p.x - 0.5, y: p.y - 0.5 }; },
        // lastDemData stays reactive (vmin/vmax change in place); read its raw grid.
        groundMm: demGroundMm(_raw(st.lastDemData), data.cols, data.rows, data.scale),
        zMmPerM: data.scale.z_mm_per_m,
        mmPerPx: data.mm_per_pixel ?? 1,
        multiplier: parseFloat(document.getElementById('cityLayer_buildings_value')?.value) || 1,
        triangulate: (contour, holes) => THREE.ShapeUtils.triangulateShape(
            contour.map(([x, y]) => new THREE.Vector2(x, y)),
            holes.map(h => h.map(([x, y]) => new THREE.Vector2(x, y)))),
    });
    const { scale, xOffset, zOffset } = geometry_scale_for_overlays;
    const p = g.positions;
    for (let i = 0; i < p.length; i += 3) {
        p[i] = p[i] * scale - xOffset; p[i + 1] *= scale; p[i + 2] = p[i + 2] * scale - zOffset;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(p, 3));
    geometry.setIndex(new THREE.BufferAttribute(g.indices, 1));
    geometry.computeVertexNormals();
    const color = document.getElementById('layerBuildingsColor')?.value || '#c8b89a';
    // flatShading for the same reason as the finished parts: extruded footprints
    // share roof and wall vertices, and smooth normals round their edges away.
    cityQuickMesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
        color, side: THREE.DoubleSide, flatShading: true }));
    _applyViewMode(cityQuickMesh);
    modelScene.add(cityQuickMesh);
    needsRender = true;
    console.info(`[preview] ${g.count} buildings (quick view) in ${Math.round(performance.now() - t0)} ms`);
    return g.count;
}

function _removeCityQuick() {
    if (!cityQuickMesh) return;
    modelScene.remove(cityQuickMesh);
    _disposeMesh(cityQuickMesh);
    cityQuickMesh = null;
    needsRender = true;
}

/**
 * Drop everything the previous region left in the viewer: the terrain mesh,
 * the city meshes, the overlays, the HUD and the dragged puzzle cuts, and
 * supersede a preview that is still building.
 *
 * Called when the selected region changes (modules/regions/regions.js), so the
 * old box is never on screen beside the new region's settings.
 */
function clearViewerModel() {
    _previewSeq++;
    // The superseded preview's mesh will never be shown, so the same body must
    // not be skipped as a duplicate when this region is selected again.
    _previewGate.reset();
    _lastPreview = null;
    window.appState.puzzleEdges = null;
    window.appState.modelPreviewState = 'idle';
    _hudMeshLines = [];
    _hudBed = null;
    _hudGrid = '';
    _renderHud();
    const status = document.getElementById('modelStatus');
    if (status) status.textContent = '';
    // A background City Model build of the old region polls /api/export/status and
    // writes its progress into #modelStatus; clearing the key supersedes it (the
    // poll returns on its next tick) and cancels one that is only scheduled.
    _cancelFullModel();
    if (!modelScene) return;
    _removeCityFull();
    _removeCityQuick();
    if (terrainMesh) {
        modelScene.remove(terrainMesh);
        _disposeMesh(terrainMesh);
        terrainMesh = null;
        window.appState.terrainMesh = null;
    }
    ['groundGrid', 'axesHelper', 'vertScale', 'horizScale', 'bedOutline', 'puzzleCuts']
        .forEach(name => {
            const old = modelScene.getObjectByName(name);
            if (!old) return;
            old.traverse(c => { c.geometry?.dispose(); c.material?.map?.dispose(); c.material?.dispose(); });
            modelScene.remove(old);
        });
    geometry_scale_for_overlays = { scale: 1, cols: 0, rows: 0, totalHeightMm: 0 };
    needsRender = true;
}

function _replaceMesh(newMesh) {
    if (terrainMesh) {
        modelScene.remove(terrainMesh);
        _disposeMesh(terrainMesh);
    }
    terrainMesh = newMesh;
    _applyViewMode(terrainMesh);
    modelScene.add(terrainMesh);
    window.appState.terrainMesh = terrainMesh;
    needsRender = true;
    updatePuzzlePreview();
}

// ─────────────────────────────────────────────────────────────────────────────
// Color helpers
// ─────────────────────────────────────────────────────────────────────────────

/** How far the 3D view pulls a colormap towards grey, and how much it darkens it. */
const TERRAIN_DESAT = 0.4, TERRAIN_DIM = 0.85;

/** Return [r,g,b] in 0–1 range for elevation t in [0,1] using colormap cmap.
 *
 * The map's colormaps are made for a flat image; lit in 3D and seen beside the
 * city's own parts, their greens read as neon and swamp the model. The 3D view
 * therefore desaturates and darkens them. The Edit map still uses them raw
 * (`mapElevationToColor`).
 */
function _elevColor(t, cmap) {
    if (cmap === 'none') return [0.55, 0.55, 0.55];
    // dem-loader.js is always loaded before this file
    const rgb = window.mapElevationToColor(t, cmap);
    const lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
    return rgb.map(c => (c + (lum - c) * TERRAIN_DESAT) * TERRAIN_DIM);
}

/** Per-vertex colours for a part, by its own height range, in colormap `cmap`.
 *
 * Used for the finished model's terrain part (`_showCityFull`), which arrives as
 * bare geometry: the preview mesh's colours are per DEM pixel and cannot be
 * reused for it. Satellite falls back to the terrain ramp, as the preview does.
 *
 * @param {Float32Array} positions viewer-frame xyz triples (y up)
 * @returns {Float32Array} one rgb triple per vertex
 */
function _heightColors(positions, cmap) {
    const n = positions.length / 3;
    let yMin = Infinity, yMax = -Infinity;
    for (let i = 0; i < n; i++) {
        const y = positions[i * 3 + 1];
        if (y < yMin) yMin = y;
        if (y > yMax) yMax = y;
    }
    const range = Math.max(yMax - yMin, 1e-6);
    const ramp = cmap === 'satellite' ? 'terrain' : cmap;
    const colors = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
        const rgb = _elevColor((positions[i * 3 + 1] - yMin) / range, ramp);
        colors[i * 3] = rgb[0]; colors[i * 3 + 1] = rgb[1]; colors[i * 3 + 2] = rgb[2];
    }
    return colors;
}

/** Recolor the current mesh with a new colormap (no server round-trip). */
function _rebuildColors(cmap) {
    _satToken++;                 // a satellite drape still fetching was for the previous choice
    // The finished model's terrain part is the ground on screen while it is shown,
    // and it has no satellite UVs, so it takes the colour ramp either way.
    const fullTerrain = cityFullGroup?.getObjectByName('terrain');
    if (fullTerrain) {
        const g = fullTerrain.geometry;
        g.setAttribute('color', new THREE.BufferAttribute(
            _heightColors(g.attributes.position.array, cmap), 3));
        const mat = _realMaterial(fullTerrain);
        mat.vertexColors = true;
        mat.color.set(0xffffff);
        mat.needsUpdate = true;
        needsRender = true;
    }
    if (!terrainMesh) return;
    if (cmap === 'satellite') { _applySatelliteTexture(); return; }
    // The mesh's own material, also while Normals shows its stand-in: after
    // Satellite it has no vertex colours, so turning Normals off would otherwise
    // bring back untextured terrain.
    const mat = _realMaterial(terrainMesh);
    if (mat.map) { mat.map.dispose(); mat.map = null; }
    const geo    = terrainMesh.geometry;
    const colors = _heightColors(geo.attributes.position.array, cmap);
    // As _buildMeshFromPreview: 'none' is the plain grey, without the water tint.
    if (cmap !== 'none') _paintWater(colors, terrainMesh.userData.water?.idx, terrainMesh.userData.water?.pct);
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    mat.vertexColors = true;
    mat.color.set(0xffffff);
    mat.needsUpdate = true;
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
    const token = ++_satToken;
    const st = window.appState || {};
    // At least the DEM grid's resolution (texture detail is free), at most 2048 px.
    const grid = st.generatedModelData ? Math.max(st.generatedModelData.width, st.generatedModelData.height) : 1024;
    const want = Math.min(2048, Math.max(1024, grid));
    const have = st.satImgSourceCanvas ? Math.max(st.satImgSourceCanvas.width, st.satImgSourceCanvas.height) : 0;
    if (!have || have < 0.9 * want || !_sameBbox(st._satImgBbox, st.currentDemBbox)) {
        try { await window.loadSatelliteRGBImage?.({ dim: want }); } catch (_) { /* toast shown there */ }
    }
    const canvas = window.appState?.satImgSourceCanvas;
    // Dropped unless it is still the latest request, for the same mesh (a rebuild
    // re-drapes its own) and with Satellite still chosen: a colormap picked while
    // the image loaded must not be painted over.
    if (token !== _satToken || mesh !== terrainMesh) return;
    if ((document.getElementById('viewerColormap')?.value ?? 'satellite') !== 'satellite') return;
    if (!canvas) {
        window.showToast?.('No satellite image for this region - showing the terrain colormap.', 'warning');
        _rebuildColors('terrain');
        return;
    }
    const mat = _realMaterial(mesh);
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
// View modes: Normals and Wireframe, on every displayed mesh
// ─────────────────────────────────────────────────────────────────────────────

/** The mesh's own material, also while Normals shows a MeshNormalMaterial in its place. */
function _realMaterial(mesh) {
    return mesh._savedMaterial || mesh.material;
}

/** Every model mesh in the scene: terrain, quick-view buildings, finished-model parts. */
function _displayedMeshes() {
    const meshes = [terrainMesh, cityQuickMesh].filter(Boolean);
    cityFullGroup?.traverse(o => { if (o.isMesh) meshes.push(o); });
    return meshes;
}

/** Free a mesh's geometry, its own material and texture, and the Normals stand-in. */
function _disposeMesh(mesh) {
    mesh.geometry?.dispose();
    const real = _realMaterial(mesh);
    real.map?.dispose();
    real.dispose();
    if (mesh.material !== real) mesh.material.dispose();
}

/**
 * Bring one mesh in line with the Normals and Wireframe toggles. Called for every
 * mesh when a toggle changes and for each mesh as it is created, so the city parts
 * and a rebuilt terrain follow them too. Wireframe is set on both materials, so it
 * survives Normals being turned off.
 */
function _applyViewMode(mesh) {
    if (_normalsActive && !mesh._savedMaterial) {
        mesh._savedMaterial = mesh.material;
        mesh.material = new THREE.MeshNormalMaterial({
            side: mesh.material.side, flatShading: mesh.material.flatShading });
    } else if (!_normalsActive && mesh._savedMaterial) {
        mesh.material.dispose();
        mesh.material = mesh._savedMaterial;
        delete mesh._savedMaterial;
    }
    mesh.material.wireframe = _wireframeActive;
    _realMaterial(mesh).wireframe = _wireframeActive;
    mesh.material.needsUpdate = true;
}

function setViewerNormals(active) {
    _normalsActive = !!active;
    _displayedMeshes().forEach(_applyViewMode);
    needsRender = true;
}

function setViewerWireframe(on) {
    _wireframeActive = !!on;
    _displayedMeshes().forEach(_applyViewMode);
    needsRender = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// HUD
// ─────────────────────────────────────────────────────────────────────────────

let _hudMeshLines = [];     // mesh stats, from the last preview
let _hudBed = null;         // { text, fits } from updateBedOutline
let _hudGrid = '';          // ground-grid cell size, from _updateSceneOverlays

/** Draw the HUD from its parts: mesh stats, the ground grid, the printer bed line. */
function _renderHud() {
    const hud = document.getElementById('viewerHud');
    if (!hud) return;
    hud.textContent = _hudMeshLines.join('\n');
    // Both used to be sprites in the scene, over the model.
    if (_hudGrid) {
        const line = document.createElement('div');
        line.textContent = _hudGrid;
        line.style.color = '#9a9a9a';
        hud.appendChild(line);
    }
    if (!_hudBed) return;
    const line = document.createElement('div');
    line.textContent = _hudBed.text;
    if (!_hudBed.fits) line.style.color = '#f0a060';
    hud.appendChild(line);
}

function _updateHud(data) {
    const pv = data.preview;
    _hudMeshLines = [`${data.face_count.toLocaleString()} faces  |  ${data.cols}×${data.rows} pts`
        + (pv?.adaptive ? `  |  adaptive ±${pv.max_error_mm} mm${pv.stride > 1 ? `, every ${pv.stride} px` : ''}` : '')];
    const r = window.appState?.selectedRegion;
    if (r) _hudMeshLines.push(`~${haversineDiagKm(r.north, r.south, r.east, r.west).toFixed(1)} km diagonal`);
    _renderHud();
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
    // The knob both export routes send (export-handlers.js::_puzzleOptions), so
    // the drawn cut is the one the server cuts.
    const knob = {
        shape: document.getElementById('splitKnobShape')?.value || 'notched',
        // Blank or 0: scale to the piece (puzzle-cuts.js::knobSize).
        knobWidth: num('splitKnobWidth', 0),
        knobDepth: num('splitKnobDepth', 0),
        clearance: _num('splitClearance', 0.45),
    };
    return {
        cols, rows, key, knob,
        colEdges: own ? saved.cols : evenEdges(g.modelWidthMm, cols),
        rowEdges: own ? saved.rows : evenEdges(g.modelDepthMm, rows),
        // A knob that scales with the piece always fits; a set one needs room.
        minGap: knob.knobWidth > 0 && knob.knobDepth > 0
            ? minPieceMm(knob.knobWidth, knob.knobDepth, knob.clearance) : MIN_AUTO_PIECE_MM,
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
    // Hidden unless the viewer asks for them (ModelContainer.vue's "Cut lines" control).
    if (!grid || (window.appState?.cutView ?? 'hidden') === 'hidden') { needsRender = true; return; }

    // Model mm (x from west, y from south) -> display units, as the mesh is mapped.
    const g = geometry_scale_for_overlays;
    const S = g.scale || 1;
    const dx = (xMm) => xMm * S - g.xOffset;
    const dz = (yMm) => (g.modelDepthMm - yMm) * S - g.zOffset;
    const zS = dz(0);
    // The real cut, knobs and all, so the preview shows the pieces that come
    // out of the printer; straight lines when the knob does not fit.
    const { polylines, knobs } = jigsawCuts(grid.colEdges, grid.rowEdges, grid.knob);
    // Draped on the terrain: each segment is split at the DEM pixel spacing and every
    // point is lifted to the surface (plus a hair, so the line is not lost in it).
    const step = Math.max(g.mmPerPx || 1, 0.25);
    const lift = 0.5;
    const verts = [];
    const at = (x, y) => [dx(x), (_surfaceMm(x, y) + lift) * S, dz(y)];
    for (const line of polylines) {
        for (let i = 1; i < line.length; i++) {
            const [x0, y0] = line[i - 1], [x1, y1] = line[i];
            const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / step));
            let prev = at(x0, y0);
            for (let j = 1; j <= n; j++) {
                const next = at(x0 + (x1 - x0) * j / n, y0 + (y1 - y0) * j / n);
                verts.push(...prev, ...next);
                prev = next;
            }
        }
    }
    const group = new THREE.Group();
    group.name = 'puzzleCuts';
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
    // Depth-tested, so hills and buildings in front of a cut hide it like a real seam.
    const editing = window.appState?.cutView === 'edit';
    const lines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: editing ? 0xff2222 : 0xcc4444 }));
    group.add(lines);
    if (grid.cols * grid.rows > 1) {
        const custom = isCustom(grid.colEdges) || isCustom(grid.rowEdges);
        const label = _makeTextSprite(`${grid.cols}×${grid.rows}${custom ? ' custom' : ''}`
            + `${knobs ? '' : ' · knob too big for these pieces'}${editing ? ' · drag a cut to move it' : ''}`,
            { fontSize: 15, color: '#ff9999' });
        label.scale.multiplyScalar(0.6);
        label.position.set(0, (g.totalHeightMm || 0) * S + 0.5, zS + 6);
        group.add(label);
    }
    modelScene.add(group);
    needsRender = true;
}

// ── Dragging cut lines ──────────────────────────────────────────────────────
let _cutDrag = null;    // { axis: 'cols' | 'rows', index, grid } while a cut is dragged
let _raycaster = null;

/** Terrain top (mm) at model mm (x from west, y from south), bilinear between DEM pixels. */
function _surfaceMm(xMm, yMm) {
    const g = geometry_scale_for_overlays;
    if (!g.topZ) return g.totalHeightMm || 0;
    const fc = Math.min(Math.max(xMm / g.mmPerPx, 0), g.cols - 1);
    const fr = Math.min(Math.max((g.modelDepthMm - yMm) / g.mmPerPx, 0), g.rows - 1);
    const c0 = Math.floor(fc), r0 = Math.floor(fr);
    const c1 = Math.min(c0 + 1, g.cols - 1), r1 = Math.min(r0 + 1, g.rows - 1);
    const z = (c, r) => { const v = g.topZ[r * g.cols + c]; return Number.isFinite(v) ? v : 0; };
    const tc = fc - c0, tr = fr - r0;
    return (z(c0, r0) * (1 - tc) + z(c1, r0) * tc) * (1 - tr)
        + (z(c0, r1) * (1 - tc) + z(c1, r1) * tc) * tr;
}

/** Model mm {x from west, y from south} under the mouse: on the terrain, else on its top plane. */
function _mouseToModelMm(e) {
    const lines = modelScene?.getObjectByName('puzzleCuts');
    if (!lines || !modelCamera) return null;
    const rect = modelRenderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1);
    _raycaster = _raycaster || new THREE.Raycaster();
    _raycaster.setFromCamera(ndc, modelCamera);
    const g = geometry_scale_for_overlays;
    const S = g.scale || 1;
    let hit = terrainMesh && _raycaster.intersectObject(terrainMesh, false)[0]?.point;
    if (!hit) {
        hit = new THREE.Vector3();
        const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -(g.totalHeightMm || 0) * S);
        if (!_raycaster.ray.intersectPlane(plane, hit)) return null;
    }
    return { x: (hit.x + g.xOffset) / S, y: g.modelDepthMm - (hit.z + g.zOffset) / S };
}

/** The interior cut line under the mouse: { axis, index, grid, cursor } or null. */
function _cutUnderMouse(e) {
    // Only the "Move" setting picks cuts up, so orbiting never nudges one.
    if (window.appState?.cutView !== 'edit' || !modelScene?.getObjectByName('puzzleCuts')) return null;
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

/**
 * Auto-rotate orbits the camera, as OrbitControls.autoRotate would (the page loads
 * three.js core only, so the viewer's own orbit code does it): turning the scene's
 * objects instead left every part but the terrain, the cut lines and the bed
 * outline standing still.
 */
function setViewerAutoRotate(val) {
    viewerAutoRotate = !!val;
    needsRender = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.haversineDiagKm      = haversineDiagKm;
window.updatePuzzlePreview  = updatePuzzlePreview;
window._readBuildParams     = _readBuildParams;
window.setViewerAutoRotate  = setViewerAutoRotate;
window.resetViewerCamera    = resetViewerCamera;
window.rebuildViewerColors  = _rebuildColors;
window.setViewerNormals     = setViewerNormals;
window.setViewerWireframe   = setViewerWireframe;
window.refreshCityLayers    = refreshCityLayers;
// The F-DETAIL switches are part of the finished model's request (cityExportBody) too.
if (typeof document !== 'undefined') {
    const detailControl = /^city(Alhambra|RoofShapes|Trees)$/;
    ['input', 'change'].forEach((type) => document.addEventListener(type, (e) => {
        if (detailControl.test(e.target?.id || '')) refreshCityLayers();
    }));
}
window.updateBedOutline     = updateBedOutline;
window.puzzleEdgesFor       = puzzleEdgesFor;
window.resetPuzzleEdges     = resetPuzzleEdges;
window.clearViewerModel     = clearViewerModel;

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
