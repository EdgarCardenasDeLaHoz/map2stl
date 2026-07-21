/**
 * modules/model-viewer.js — Three.js 3D terrain viewer + puzzle export.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   previewModelIn3D()                          — build/replace terrain in viewer
 *   haversineDiagKm(N, S, E, W)                — bbox diagonal in km
 *   updatePuzzlePreview()                       — draw puzzle cut lines in viewer
 *   exportPuzzle3MF()                           — stub: puzzle 3MF export
 *   window.setViewerAutoRotate(val)             — set auto-rotate flag from app.js
 *   window.resetViewerCamera()                  — fit camera to current mesh
 *   window.rebuildViewerColors(cmap)            — recolor mesh from a colormap name
 *   window.setViewerNormals(bool)               — toggle normals-debug material
 *
 * State exposed on window.appState:
 *   window.appState.terrainMesh  — current terrain mesh (or null)
 *   window.appState.viewerScene  — the THREE.Scene
 *
 * External dependencies:
 *   THREE                                       — global Three.js
 *   window.appState.generatedModelData
 *   window.appState.lastDemData
 *   window.showToast(msg, type)                 — file-top global in app.js
 *   window.mapElevationToColor(t, cmap)         — from dem-loader.js (loaded first)
 */

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
        _isDragging = true;
        _isPanning  = e.shiftKey || e.button === 1;
        _prevMouse  = { x: e.clientX, y: e.clientY };
        e.preventDefault();
    });

    el.addEventListener('mousemove', e => {
        if (!_isDragging) return;
        const dx = e.clientX - _prevMouse.x;
        const dy = e.clientY - _prevMouse.y;
        _prevMouse = { x: e.clientX, y: e.clientY };
        if (_isPanning) _orbitPan(dx, dy); else _orbitRotate(dx, dy);
    });

    el.addEventListener('mouseup',    () => { _isDragging = false; });
    el.addEventListener('mouseleave', () => { _isDragging = false; });

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

    needsRender = true;
}

// ─────────────────────────────────────────────────────────────────────────────
// Preview
// ─────────────────────────────────────────────────────────────────────────────

async function previewModelIn3D() {
    const ldd = window.appState?.lastDemData;
    if (!ldd?.values?.length) {
        window.showToast('Load a DEM first (Edit tab → Reload).', 'warning'); return;
    }

    const previewBtn = document.getElementById('previewModelBtn');
    const statusEl   = document.getElementById('modelStatus');
    if (previewBtn) previewBtn.disabled = true;
    if (statusEl) statusEl.textContent = '⏳ Building mesh…';

    if (!modelRenderer) initModelViewer();

    try {
        // Read all build params directly from the DOM — single source of truth.
        // (appState.demParams.height is only synced on manual `change` events,
        //  so it can lag the input when auto-rebuild fires.)
        const modelHeight  = parseFloat(document.getElementById('exportModelHeight')?.value) || 30;
        const baseHeight   = parseFloat(document.getElementById('exportBaseHeight')?.value)  || 5;
        const exaggeration = parseFloat(document.getElementById('exportExaggeration')?.value) || 1.0;
        const mmPerPixel   = parseFloat(document.getElementById('mmPerPixel')?.value) || 1.0;

        const ds = window._demSettings ? window._demSettings() : {};
        const { data, error: previewErr } = await window.api.export.preview({
            ...ds,
            model_height: modelHeight,
            base_height:  baseHeight,
            exaggeration,
            mm_per_pixel:  mmPerPixel,
            sea_level_cap: document.getElementById('exportSeaLevelCap')?.checked || false,
            solid:         document.getElementById('viewerSolidPreview')?.checked || false,
            // These previously only applied at file-export time, so the live
            // preview never reflected them until you downloaded the model.
            engrave_label:    document.getElementById('exportEngraveLabel')?.checked || false,
            label_text:       document.getElementById('exportLabelText')?.value || window.appState?.selectedRegion?.name || '',
            contours:         document.getElementById('exportContours')?.checked || false,
            contour_interval: parseInt(document.getElementById('exportContourInterval')?.value) || 100,
            contour_style:    document.getElementById('exportContourStyle')?.value || 'engraved',
        });
        if (previewErr) throw new Error(previewErr);

        const cmap = document.getElementById('viewerColormap')?.value || 'terrain';
        _replaceMesh(_buildMeshFromPreview(data, cmap));
        _fitCameraToMesh(terrainMesh);
        _updateHud(data);
        _updateSceneOverlays(data);

        window.appState.generatedModelData = {
            values: ldd.values, width: ldd.width, height: ldd.height,
            mmPerPixel,
            modelHeight,
            exaggeration, baseHeight,
            vmin: ldd.vmin, vmax: ldd.vmax,
        };
        window._setExportButtonsEnabled?.(true);
        window.appState._updateWorkflowStepper?.();
        if (statusEl) statusEl.textContent = `Preview: ${ldd.width}×${ldd.height}, ${data.face_count.toLocaleString()} faces`;
        window.showToast('3D preview ready — drag to rotate, shift+drag to pan, scroll to zoom.', 'success');
    } catch (e) {
        if (statusEl) statusEl.textContent = '❌ ' + e.message;
        window.showToast('Preview failed: ' + e.message, 'error');
    } finally {
        if (previewBtn) previewBtn.disabled = false;
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
    const totalHeightMm = (data.model_height || 0) + (data.base_height || 0);
    // Longest physical dimension maps to 100 display units; others scale equally.
    const longest = Math.max(widthMm, depthMm, totalHeightMm, 1);
    const SCALE = 100 / longest;

    const xOffset = (widthMm * SCALE) / 2;
    const zOffset = (depthMm * SCALE) / 2;
    const zMin    = data.z_min;
    const zRange  = Math.max(data.z_max - data.z_min, 1);

    const positions = new Float32Array(rawVerts.length * 3);
    const colors    = new Float32Array(rawVerts.length * 3);
    for (let i = 0; i < rawVerts.length; i++) {
        const [c, r, z] = rawVerts[i];
        positions[i * 3]     = c * mmPerPx * SCALE - xOffset; // x (mm in display units)
        positions[i * 3 + 1] = z * SCALE;                     // y (z is already mm)
        positions[i * 3 + 2] = r * mmPerPx * SCALE - zOffset; // z (mm in display units)

        const rgb = _elevColor((z - zMin) / zRange, cmap);
        colors[i * 3] = rgb[0]; colors[i * 3 + 1] = rgb[1]; colors[i * 3 + 2] = rgb[2];
    }

    // Stash physical dims (mm) and the display-scale factor for overlay code.
    geometry_scale_for_overlays = {
        scale: SCALE,
        widthMm, depthMm, totalHeightMm,
    };

    const indices = new Uint32Array(rawFaces.length * 3);
    for (let i = 0; i < rawFaces.length; i++) {
        indices[i * 3] = rawFaces[i][0]; indices[i * 3 + 1] = rawFaces[i][1]; indices[i * 3 + 2] = rawFaces[i][2];
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
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
    const lines = [`${data.face_count.toLocaleString()} faces  |  ${data.cols}×${data.rows} pts`];
    const r = window.appState?.selectedRegion;
    if (r) lines.push(`~${haversineDiagKm(r.north, r.south, r.east, r.west).toFixed(1)} km diagonal`);
    hud.textContent = lines.join('\n');
}

// ─────────────────────────────────────────────────────────────────────────────
// Puzzle preview
// ─────────────────────────────────────────────────────────────────────────────

function updatePuzzlePreview() {
    if (!terrainMesh || !modelScene) return;
    const old = modelScene.getObjectByName('puzzleCuts');
    if (old) modelScene.remove(old);
    if (!document.getElementById('puzzleEnabled')?.checked) { needsRender = true; return; }

    const pX = parseInt(document.getElementById('splitCols')?.value) || 3;
    const pY = parseInt(document.getElementById('splitRows')?.value) || 3;
    const w = 100, h = 100;
    const verts = [];
    for (let i = 1; i < pX; i++) { const x = (i / pX) * w - w / 2; verts.push(x, 0, -h / 2, x, 0, h / 2); }
    for (let j = 1; j < pY; j++) { const z = (j / pY) * h - h / 2; verts.push(-w / 2, 0, z, w / 2, 0, z); }
    const geo  = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
    const mat  = new THREE.LineBasicMaterial({ color: 0xff2222, depthTest: false });
    const lines = new THREE.LineSegments(geo, mat);
    lines.name = 'puzzleCuts';
    lines.position.y = 6;
    modelScene.add(lines);
    needsRender = true;
}

async function exportPuzzle3MF() {
    const region = window.appState?.selectedRegion;
    if (!region) { window.showToast('Select a region first', 'warning'); return; }
    const md = window.appState?.generatedModelData;
    if (!md) { window.showToast('Please generate a model first', 'warning'); return; }

    const pX = parseInt(document.getElementById('splitCols')?.value) || 3;
    const pY = parseInt(document.getElementById('splitRows')?.value) || 3;
    if (pX * pY > 64) { window.showToast('Too many pieces (max 64 total)', 'warning'); return; }

    const connectorMm = parseFloat(document.getElementById('splitPuzzleM')?.value) || 50;
    const connectorsN = parseInt(document.getElementById('splitPuzzleBaseN')?.value) || 10;
    const borderH = parseFloat(document.getElementById('splitBorderHeight')?.value) || 1.0;
    const borderOff = parseFloat(document.getElementById('splitBorderOffset')?.value) || 5.0;
    const includeBorder = document.getElementById('splitIncludeBorder')?.checked ?? true;

    const statusEl = document.getElementById('modelStatus');
    const setStatus = (msg) => { if (statusEl) statusEl.textContent = msg; };

    try {
        setStatus(`Starting puzzle export (${pX}×${pY})...`);

        const ds = window._demSettings ? window._demSettings() : {};
        const body = {
            ...ds,
            model_height: md.resolution,
            base_height: md.baseHeight,
            exaggeration: md.exaggeration,
            sea_level_cap: document.getElementById('exportSeaLevelCap')?.checked || false,
            name: region.name || 'terrain',
            split_cols: pX,
            split_rows: pY,
            connector_size_mm: connectorMm,
            connectors_per_edge: connectorsN,
            border_height_mm: borderH,
            border_offset_mm: borderOff,
            include_border: includeBorder,
        };

        // Start the async task
        const startResp = await fetch('/api/export/puzzle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!startResp.ok) {
            const err = await startResp.json();
            throw new Error(err.error || 'Failed to start puzzle export');
        }
        const { task_id } = await startResp.json();

        // Poll for progress
        let status = { status: 'running', progress: 0 };
        while (status.status === 'running') {
            await new Promise(r => setTimeout(r, 300));
            const pollResp = await fetch(`/api/export/status/${encodeURIComponent(task_id)}`);
            if (!pollResp.ok) throw new Error('Lost connection to export task');
            status = await pollResp.json();
            setStatus(`Puzzle: ${status.message} (${status.progress}%)`);
        }

        if (status.status === 'error') throw new Error(status.message || 'Puzzle export failed');

        // Download
        setStatus('Downloading puzzle 3MF...');
        const dlResp = await fetch(`/api/export/download/${encodeURIComponent(task_id)}`);
        if (!dlResp.ok) throw new Error('Download failed');
        const blob = await dlResp.blob();

        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${region.name || 'terrain'}_puzzle.3mf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);

        const pieces = dlResp.headers.get('X-Piece-Count') || (pX * pY);
        window.showToast(`Puzzle 3MF ready - ${pieces} pieces`, 'success', 4000);
        setStatus(`Puzzle export complete (${pieces} pieces)`);
    } catch (e) {
        console.error('Puzzle export error:', e);
        window.showToast('Puzzle export failed: ' + e.message, 'error');
        setStatus('Puzzle export failed');
    }
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
window.exportPuzzle3MF      = exportPuzzle3MF;
window.setViewerAutoRotate  = setViewerAutoRotate;
window.resetViewerCamera    = resetViewerCamera;
window.rebuildViewerColors  = _rebuildColors;
window.setViewerNormals     = setViewerNormals;

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
    'exportExaggeration', 'exportSeaLevelCap', 'viewerSolidPreview',
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
