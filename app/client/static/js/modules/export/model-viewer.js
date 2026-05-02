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
let _rawPreviewData = null;     // last server preview response (for simplification)
let _fullGeometry   = null;     // saved geometry when simplification is active

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
        const exaggeration = parseFloat(document.getElementById('exportExaggeration')?.value) || 1.0;
        const baseHeight   = parseFloat(document.getElementById('exportBaseHeight')?.value)  || 5;
        const modelHeight  = parseFloat(document.getElementById('exportModelHeight')?.value) || 30;

        const ds = window._demSettings ? window._demSettings() : {};
        const { data, error: previewErr } = await window.api.export.preview({
            ...ds,
            model_height: modelHeight,
            base_height:  baseHeight,
            exaggeration,
            sea_level_cap: document.getElementById('exportSeaLevelCap')?.checked || false,
        });
        if (previewErr) throw new Error(previewErr);

        const cmap = document.getElementById('viewerColormap')?.value || 'terrain';
        _rawPreviewData = data;
        if (_fullGeometry) { _fullGeometry.dispose(); _fullGeometry = null; }
        _replaceMesh(_buildMeshFromPreview(data, cmap));
        _fitCameraToMesh(terrainMesh);
        _updateHud(data);
        _updateSceneOverlays(data);
        // Re-apply active visualization modes to the new mesh
        if (document.getElementById('viewerSimplify')?.checked) applySimplification();
        if (document.getElementById('viewerSurfaceGroups')?.checked) _applySurfaceGroups(true);

        window.appState.generatedModelData = {
            values: ldd.values, width: ldd.width, height: ldd.height,
            resolution: window.appState.demParams.height || 20,
            modelHeight, exaggeration, baseHeight,
            walls: document.getElementById('exportWalls')?.checked ?? true,
            floor: document.getElementById('exportFloor')?.checked ?? true,
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
    // numpy2stl vertex layout: [col, row, z_mm]
    // Three.js layout: x=col (→right), y=z_mm (→up), z=row (→back)
    // Footprint uses correct aspect ratio; height is normalized to 30 units so terrain
    // features remain visible regardless of model_height. Actual mm printed dimensions
    // are shown in the HUD.
    const rawVerts = data.vertices;
    const rawFaces = data.faces;
    const zMin = data.z_min, zRange = Math.max(data.z_max - data.z_min, 1);
    const cRange = Math.max(data.cols - 1, 1);
    const rRange = Math.max(data.rows - 1, 1);

    // Correct footprint aspect ratio (was always 100×100 = square)
    const viewW = 100;
    const viewD = (data.rows / Math.max(data.cols, 1)) * viewW;
    // Scale height proportionally: viewW units = data.dim mm → 1 mm = viewW/dim units
    const heightScale = viewW / (data.dim || 600);

    const positions = new Float32Array(rawVerts.length * 3);
    const colors    = new Float32Array(rawVerts.length * 3);
    for (let i = 0; i < rawVerts.length; i++) {
        const [c, r, z] = rawVerts[i];
        positions[i * 3]     = c / cRange * viewW - viewW / 2;    // x
        positions[i * 3 + 1] = (z - zMin) * heightScale;          // y proportional to real mm
        positions[i * 3 + 2] = r / rRange * viewD - viewD / 2;    // z (proportional)

        const rgb = _elevColor((z - zMin) / zRange, cmap);
        colors[i * 3] = rgb[0]; colors[i * 3 + 1] = rgb[1]; colors[i * 3 + 2] = rgb[2];
    }

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

    const dim        = data.dim || 600;
    const depthMm    = Math.round(dim * data.rows / Math.max(data.cols, 1));
    const heightMm   = Math.round(data.model_height + data.base_height);
    const lines = [
        `${data.face_count.toLocaleString()} faces  |  ${data.cols}×${data.rows} pts`,
        `Print size: ${dim} × ${depthMm} × ${heightMm} mm  (H: ${data.model_height} mm + Base: ${data.base_height} mm)`,
    ];
    const r = window.appState?.selectedRegion;
    if (r) lines.push(`~${haversineDiagKm(r.north, r.south, r.east, r.west).toFixed(1)} km diagonal`);
    hud.textContent = lines.join('\n');
}

// ─────────────────────────────────────────────────────────────────────────────
// Scene overlays — grid, axes, scale bar
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Create a canvas-texture sprite with a single text label.
 * Returns a THREE.Sprite; caller sets its .position and adds to scene.
 */
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
    // scale: sprite is sized in scene units; 1 unit ≈ 1mm-equivalent at viewW=100
    const aspect = canvas.width / canvas.height;
    sprite.scale.set(aspect * 5.5, 5.5, 1);
    return sprite;
}

/** Pick a nice tick interval (mm) for a given total range. */
function _niceInterval(rangeMm) {
    const raw = rangeMm / 6;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const mult of [1, 2, 2.5, 5, 10]) {
        if (mult * mag >= raw) return mult * mag;
    }
    return mag * 10;
}

function _updateSceneOverlays(data) {
    if (!modelScene) return;

    // Remove any previous overlay objects (including label sprites group)
    ['groundGrid', 'axesHelper', 'scaleLabels', 'vertScale'].forEach(name => {
        const old = modelScene.getObjectByName(name);
        if (old) {
            old.traverse(c => { c.geometry?.dispose(); c.material?.map?.dispose(); c.material?.dispose(); });
            modelScene.remove(old);
        }
    });

    const viewW       = 100;
    const viewD       = (data.rows / Math.max(data.cols, 1)) * viewW;
    const heightScale = viewW / (data.dim || 600);   // display units per mm
    const mmPerUnit   = (data.dim || 600) / viewW;   // mm per display unit
    const depthMm     = (data.dim || 600) * data.rows / Math.max(data.cols, 1);

    // ── Ground grid ─────────────────────────────────────────────────────────
    const gridDivs   = 10;
    const gridSize   = Math.max(viewW, viewD) * 1.1;
    const gridHelper = new THREE.GridHelper(gridSize, gridDivs, 0x555555, 0x303030);
    gridHelper.name  = 'groundGrid';
    gridHelper.position.y = 0;
    modelScene.add(gridHelper);

    // ── Axes helper ─────────────────────────────────────────────────────────
    const axisLen    = Math.max(viewW, viewD) * 0.18;
    const axesHelper = new THREE.AxesHelper(axisLen);
    axesHelper.name  = 'axesHelper';
    axesHelper.position.set(-viewW / 2, 0, viewD / 2);
    modelScene.add(axesHelper);

    // ── Scale label sprites ──────────────────────────────────────────────────
    const labelGroup = new THREE.Group();
    labelGroup.name  = 'scaleLabels';

    // X-axis labels (along front edge, Z = viewD/2 + small offset)
    const xIntervalMm = _niceInterval((data.dim || 600));
    const xIntervalU  = xIntervalMm / mmPerUnit;
    const xLabelY     = -1.5;   // just below grid plane
    const xLabelZ     = viewD / 2 + 6;
    const xStart      = -viewW / 2;
    for (let xU = 0; xU <= viewW + 0.01; xU += xIntervalU) {
        const xMm   = Math.round(xU * mmPerUnit);
        const label = _makeTextSprite(`${xMm}`, { fontSize: 20 });
        label.position.set(xStart + xU, xLabelY, xLabelZ);
        labelGroup.add(label);
    }
    // X-axis unit label
    const xUnitLabel = _makeTextSprite('mm →', { fontSize: 18, color: '#ff9966' });
    xUnitLabel.position.set(viewW / 2 + 10, xLabelY, xLabelZ);
    labelGroup.add(xUnitLabel);

    // Z-axis labels (along left edge, X = -viewW/2 - small offset)
    const zIntervalMm = _niceInterval(depthMm);
    const zIntervalU  = zIntervalMm / (depthMm / viewD);
    const zLabelY     = -1.5;
    const zLabelX     = -viewW / 2 - 8;
    const zStart      = -viewD / 2;
    for (let zU = 0; zU <= viewD + 0.01; zU += zIntervalU) {
        const zMm   = Math.round(zU * (depthMm / viewD));
        const label = _makeTextSprite(`${zMm}`, { fontSize: 20 });
        label.position.set(zLabelX, zLabelY, zStart + zU);
        labelGroup.add(label);
    }

    modelScene.add(labelGroup);

    // ── Vertical scale bar (back-right corner) ────────────────────────────
    const totalHeightMm = (data.model_height || 0) + (data.base_height || 0);
    const totalHeightU  = totalHeightMm * heightScale;

    const vertGroup = new THREE.Group();
    vertGroup.name  = 'vertScale';

    // Vertical line
    const linePts = [
        new THREE.Vector3(0, 0, 0),
        new THREE.Vector3(0, totalHeightU, 0),
    ];
    const lineGeo = new THREE.BufferGeometry().setFromPoints(linePts);
    const lineMat = new THREE.LineBasicMaterial({ color: 0xaaaaaa, depthTest: false });
    const line    = new THREE.Line(lineGeo, lineMat);
    vertGroup.add(line);

    // Tick marks + labels
    const vIntervalMm = _niceInterval(totalHeightMm);
    const tickHalfLen = 2;
    for (let h = 0; h <= totalHeightMm + 0.01; h += vIntervalMm) {
        const yU = h * heightScale;
        // Tick mark
        const tickGeo = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(-tickHalfLen, yU, 0),
            new THREE.Vector3(tickHalfLen,  yU, 0),
        ]);
        vertGroup.add(new THREE.Line(tickGeo, lineMat));
        // Label
        const label = _makeTextSprite(`${Math.round(h)} mm`, { fontSize: 19, color: '#bbddff' });
        label.position.set(tickHalfLen + 8, yU, 0);
        vertGroup.add(label);
    }

    // Position at back-right corner
    vertGroup.position.set(viewW / 2 + 6, 0, -viewD / 2);
    modelScene.add(vertGroup);

    needsRender = true;
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
    // Match the proportional footprint used in _buildMeshFromPreview
    const md2 = window.appState?.generatedModelData;
    const aspect = md2 ? md2.height / Math.max(md2.width, 1) : 1;
    const w = 100, h = aspect * 100;
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
            model_height: md.modelHeight || 30,
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

function setViewerAutoRotate(val) {
    viewerAutoRotate = val;
}

// ─────────────────────────────────────────────────────────────────────────────
// Surface group coloring — BFS on face adjacency, random hue per component
// ─────────────────────────────────────────────────────────────────────────────

function _hslToRgb(h, s, l) {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const f = t => { if (t < 0) t += 1; if (t > 1) t -= 1;
        if (t < 1/6) return p + (q - p) * 6 * t;
        if (t < 1/2) return q;
        if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
        return p; };
    return [f(h + 1/3), f(h), f(h - 1/3)];
}

function _buildSurfaceGroupColors(geometry) {
    const idx = geometry.index.array;
    const nFaces = idx.length / 3;
    const nVerts = geometry.attributes.position.count;

    // Edge → face list (integer key: min * nVerts + max)
    const edgeFaces = new Map();
    for (let f = 0; f < nFaces; f++) {
        const a = idx[f*3], b = idx[f*3+1], c = idx[f*3+2];
        for (const [u, v] of [[a,b],[b,c],[a,c]]) {
            const k = u < v ? u * nVerts + v : v * nVerts + u;
            const list = edgeFaces.get(k); if (list) list.push(f); else edgeFaces.set(k, [f]);
        }
    }

    // BFS connected components
    const comp = new Int32Array(nFaces).fill(-1);
    let numComp = 0;
    for (let start = 0; start < nFaces; start++) {
        if (comp[start] !== -1) continue;
        const queue = [start]; comp[start] = numComp;
        for (let qi = 0; qi < queue.length; qi++) {
            const f = queue[qi];
            const a = idx[f*3], b = idx[f*3+1], c = idx[f*3+2];
            for (const [u, v] of [[a,b],[b,c],[a,c]]) {
                const k = u < v ? u * nVerts + v : v * nVerts + u;
                for (const nb of (edgeFaces.get(k) || [])) {
                    if (comp[nb] === -1) { comp[nb] = numComp; queue.push(nb); }
                }
            }
        }
        numComp++;
    }

    // Assign hues evenly spaced (golden angle) — visually distinct
    const compRgb = Array.from({ length: numComp }, (_, i) =>
        _hslToRgb(((i * 137.508) % 360) / 360, 0.75, 0.55));

    // Per-vertex color from first-seen face component
    const vComp = new Int32Array(nVerts).fill(-1);
    for (let f = 0; f < nFaces; f++) {
        for (const v of [idx[f*3], idx[f*3+1], idx[f*3+2]])
            if (vComp[v] === -1) vComp[v] = comp[f];
    }

    const colors = new Float32Array(nVerts * 3);
    for (let v = 0; v < nVerts; v++) {
        const ci = vComp[v] >= 0 ? vComp[v] : 0;
        colors[v*3] = compRgb[ci][0]; colors[v*3+1] = compRgb[ci][1]; colors[v*3+2] = compRgb[ci][2];
    }
    return { colors, numComp };
}

function _applySurfaceGroups(active) {
    if (!terrainMesh) return;
    if (active) {
        const geo = terrainMesh.geometry;
        const { colors, numComp } = _buildSurfaceGroupColors(geo);
        geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        geo.attributes.color.needsUpdate = true;
        terrainMesh.material.vertexColors = true;
        terrainMesh.material.color.set(0xffffff);
        terrainMesh.material.needsUpdate = true;
        needsRender = true;
        window.showToast?.(`${numComp} surface group${numComp !== 1 ? 's' : ''} found`, 'info');
    } else {
        _rebuildColors(document.getElementById('viewerColormap')?.value || 'terrain');
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Mesh simplification — client-side grid subsampling (top surface, no re-fetch)
// ─────────────────────────────────────────────────────────────────────────────

function _buildSimplifiedTopSurface(step) {
    if (!_rawPreviewData) return null;
    const data = _rawPreviewData;
    const { vertices: rawVerts, cols, rows } = data;
    const viewW = 100;
    const viewD = (rows / Math.max(cols, 1)) * viewW;
    const cRange = Math.max(cols - 1, 1);
    const rRange = Math.max(rows - 1, 1);
    const heightScale = viewW / (data.dim || 600);
    const zMin = data.z_min;
    const zRange = Math.max(data.z_max - data.z_min, 1);
    const cmap = document.getElementById('viewerColormap')?.value || 'terrain';

    // Build z-grid from top surface vertices (those with integer col/row in grid bounds)
    const zGrid = new Float32Array(cols * rows).fill(zMin);
    for (const [c, r, z] of rawVerts) {
        const ci = Math.round(c), ri = Math.round(r);
        if (ci >= 0 && ci < cols && ri >= 0 && ri < rows) zGrid[ri * cols + ci] = z;
    }

    // Subsampled column and row indices (always include last)
    const sC = [], sR = [];
    for (let c = 0; c < cols; c += step) sC.push(c);
    if (sC.at(-1) !== cols - 1) sC.push(cols - 1);
    for (let r = 0; r < rows; r += step) sR.push(r);
    if (sR.at(-1) !== rows - 1) sR.push(rows - 1);

    const nV = sC.length * sR.length;
    const positions = new Float32Array(nV * 3);
    const colors    = new Float32Array(nV * 3);
    for (let ri = 0; ri < sR.length; ri++) {
        for (let ci = 0; ci < sC.length; ci++) {
            const vi = ri * sC.length + ci;
            const c = sC[ci], r = sR[ri], z = zGrid[r * cols + c];
            positions[vi*3]   = c / cRange * viewW - viewW / 2;
            positions[vi*3+1] = (z - zMin) * heightScale;
            positions[vi*3+2] = r / rRange * viewD - viewD / 2;
            const rgb = _elevColor((z - zMin) / zRange, cmap);
            colors[vi*3] = rgb[0]; colors[vi*3+1] = rgb[1]; colors[vi*3+2] = rgb[2];
        }
    }

    const nF = (sC.length - 1) * (sR.length - 1) * 2;
    const indices = new Uint32Array(nF * 3);
    let fi = 0;
    for (let ri = 0; ri < sR.length - 1; ri++) {
        for (let ci = 0; ci < sC.length - 1; ci++) {
            const tl = ri * sC.length + ci, tr = tl + 1;
            const bl = tl + sC.length,     br = bl + 1;
            indices[fi*3] = tl; indices[fi*3+1] = bl; indices[fi*3+2] = tr; fi++;
            indices[fi*3] = tr; indices[fi*3+1] = bl; indices[fi*3+2] = br; fi++;
        }
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
    geo.setIndex(new THREE.BufferAttribute(indices, 1));
    geo.computeVertexNormals();
    return geo;
}

function applySimplification() {
    if (!terrainMesh || !_rawPreviewData) return;
    const enabled = document.getElementById('viewerSimplify')?.checked;
    const ratio = Math.max(0.01, Math.min(1, parseFloat(document.getElementById('viewerSimplifyRatio')?.value) || 0.25));
    const step  = Math.max(2, Math.round(1 / ratio));

    if (enabled) {
        const simGeo = _buildSimplifiedTopSurface(step);
        if (!simGeo) return;
        if (!_fullGeometry) _fullGeometry = terrainMesh.geometry;
        else terrainMesh.geometry.dispose();
        terrainMesh.geometry = simGeo;
    } else {
        if (_fullGeometry) {
            terrainMesh.geometry.dispose();
            terrainMesh.geometry = _fullGeometry;
            _fullGeometry = null;
        }
    }
    // Re-apply surface groups if active
    if (document.getElementById('viewerSurfaceGroups')?.checked) _applySurfaceGroups(true);
    needsRender = true;
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
window.applySurfaceGroups   = _applySurfaceGroups;
window.applySimplification  = applySimplification;
