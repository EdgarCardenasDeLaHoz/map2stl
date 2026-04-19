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
    container.style.position = 'relative';

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
        container.innerHTML = '<div style="padding:20px;color:#888;text-align:center;">3D preview unavailable (WebGL not supported by this browser/GPU)</div>';
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
    hud.style.cssText = 'position:absolute;bottom:8px;left:8px;color:#ccc;font:11px/1.5 monospace;pointer-events:none;text-shadow:0 1px 3px #000;';
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

        const { data, error: previewErr } = await window.api.export.preview({
            dem_values:   ldd.values,
            height:       ldd.height,
            width:        ldd.width,
            model_height: window.appState.demParams.height || 20,
            base_height:  baseHeight,
            exaggeration,
            sea_level_cap: document.getElementById('exportSeaLevelCap')?.checked || false,
        });
        if (previewErr) throw new Error(previewErr);

        const cmap = document.getElementById('viewerColormap')?.value || 'terrain';
        _replaceMesh(_buildMeshFromPreview(data, cmap));
        _fitCameraToMesh(terrainMesh);
        _updateHud(data);

        window.appState.generatedModelData = {
            values: ldd.values, width: ldd.width, height: ldd.height,
            resolution: window.appState.demParams.height || 20,
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
    // numpy2stl vertex layout: [col, row, z_mm]
    // Three.js layout: x=col (→right), y=z_mm (→up), z=row (→back)
    const rawVerts = data.vertices;
    const rawFaces = data.faces;
    const zMin = data.z_min, zRange = Math.max(data.z_max - data.z_min, 1);
    const cRange = Math.max(data.cols - 1, 1);
    const rRange = Math.max(data.rows - 1, 1);

    const positions = new Float32Array(rawVerts.length * 3);
    const colors    = new Float32Array(rawVerts.length * 3);
    for (let i = 0; i < rawVerts.length; i++) {
        const [c, r, z] = rawVerts[i];
        positions[i * 3]     = c / cRange * 100 - 50;    // x
        positions[i * 3 + 1] = (z - zMin) / zRange * 30; // y (elevation)
        positions[i * 3 + 2] = r / rRange * 100 - 50;    // z

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
    const pX = parseInt(document.getElementById('splitCols')?.value) || 3;
    const pY = parseInt(document.getElementById('splitRows')?.value) || 3;
    if (pX * pY > 64) { window.showToast('Too many pieces (max 64 total)', 'warning'); return; }
    window.showToast('Puzzle 3MF export: backend implementation pending', 'warning');
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
