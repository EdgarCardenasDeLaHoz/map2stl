/**
 * modules/model-viewer.js — Three.js 3D terrain viewer + puzzle export.
 *
 * Loaded as a plain <script> before app.js.
 *
 * Public API (all on window):
 *   initModelViewer()                           — init Three.js scene
 *   createTerrainMesh(vals, w, h, exag)         — build PlaneGeometry mesh
 *   previewModelIn3D()                          — build/replace terrain in viewer
 *   haversineDiagKm(N, S, E, W)                — bbox diagonal in km
 *   updatePuzzlePreview()                       — draw puzzle cut lines in viewer
 *   exportPuzzle3MF()                           — stub: puzzle 3MF export
 *   window.setViewerAutoRotate(val)             — set auto-rotate flag from app.js
 *
 * State exposed on window.appState:
 *   window.appState.terrainMesh  — current terrain mesh (or null)
 *   window.appState.viewerScene  — the THREE.Scene
 *
 * External dependencies:
 *   THREE                                   — global Three.js
 *   window.appState.generatedModelData
 *   window.appState.lastDemData
 *   showToast(msg, type)                    — file-top global in app.js
 */

// ─────────────────────────────────────────────────────────────────────────────
// Module-scope state
// ─────────────────────────────────────────────────────────────────────────────

let modelScene    = null;
let modelCamera   = null;
let modelRenderer = null;
let modelMesh     = null;
let terrainMesh   = null;
let viewerAutoRotate = false;

// ─────────────────────────────────────────────────────────────────────────────
// Viewer init
// ─────────────────────────────────────────────────────────────────────────────

function initModelViewer() {
    const container = document.getElementById('modelViewer');
    if (!container) return;

    container.innerHTML = '';

    modelScene = new THREE.Scene();
    modelScene.background = new THREE.Color(0x1a1a1a);

    const aspect = container.clientWidth / container.clientHeight;
    modelCamera  = new THREE.PerspectiveCamera(60, aspect, 0.1, 1000);
    modelCamera.position.set(0, 100, 150);
    modelCamera.lookAt(0, 0, 0);

    try {
        modelRenderer = new THREE.WebGLRenderer({ antialias: true });
    } catch (e) {
        console.error('WebGL unavailable for 3D viewer:', e);
        container.innerHTML = '<div style="padding:20px;color:#888;text-align:center;">3D preview unavailable (WebGL not supported by this browser/GPU)</div>';
        return;
    }
    modelRenderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(modelRenderer.domElement);

    modelScene.add(new THREE.AmbientLight(0x404040, 0.6));
    const dl1 = new THREE.DirectionalLight(0xffffff, 0.8); dl1.position.set(50, 100, 50);  modelScene.add(dl1);
    const dl2 = new THREE.DirectionalLight(0xffffff, 0.3); dl2.position.set(-50, 50, -50); modelScene.add(dl2);
    modelScene.add(new THREE.GridHelper(200, 20, 0x444444, 0x333333));

    // Simple orbit controls
    let isDragging = false;
    let prev = { x: 0, y: 0 };

    modelRenderer.domElement.addEventListener('mousedown', (e) => {
        isDragging = true; prev = { x: e.clientX, y: e.clientY };
    });
    modelRenderer.domElement.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const dx = e.clientX - prev.x;
        const dy = e.clientY - prev.y;
        const sph = new THREE.Spherical();
        sph.setFromVector3(modelCamera.position);
        sph.theta -= dx * 0.01;
        sph.phi    = Math.max(0.1, Math.min(Math.PI - 0.1, sph.phi - dy * 0.01));
        modelCamera.position.setFromSpherical(sph);
        modelCamera.lookAt(0, 0, 0);
        prev = { x: e.clientX, y: e.clientY };
    });
    modelRenderer.domElement.addEventListener('mouseup',    () => { isDragging = false; });
    modelRenderer.domElement.addEventListener('mouseleave', () => { isDragging = false; });
    modelRenderer.domElement.addEventListener('wheel', (e) => {
        e.preventDefault();
        const dir  = e.deltaY > 0 ? 1 : -1;
        const dist = modelCamera.position.length();
        modelCamera.position.setLength(Math.max(20, Math.min(500, dist * (1 + dir * 0.1))));
    });

    window.addEventListener('resize', () => {
        if (!container.offsetParent) return;
        const w = container.clientWidth, h = container.clientHeight;
        modelCamera.aspect = w / h;
        modelCamera.updateProjectionMatrix();
        modelRenderer.setSize(w, h);
    });

    // Sync aliases for legacy code that reads viewerScene/viewerRenderer/viewerCamera
    window.appState.viewerScene = modelScene;

    (function animate() {
        requestAnimationFrame(animate);
        if (viewerAutoRotate && terrainMesh) terrainMesh.rotation.y += 0.005;
        modelRenderer.render(modelScene, modelCamera);
    })();
}

// ─────────────────────────────────────────────────────────────────────────────
// Mesh creation
// ─────────────────────────────────────────────────────────────────────────────

function createTerrainMesh(demValues, width, height, exaggeration) {
    const geometry = new THREE.PlaneGeometry(100, 100, width - 1, height - 1);
    geometry.rotateX(-Math.PI / 2);

    const positions = geometry.attributes.position.array;
    const vmin  = demValues.reduce((a, b) => Math.min(a, b), Infinity);
    const vmax  = demValues.reduce((a, b) => Math.max(a, b), -Infinity);
    const range = vmax - vmin || 1;

    for (let i = 0; i < demValues.length && i * 3 < positions.length; i++) {
        positions[i * 3 + 1] = ((demValues[i] - vmin) / range) * 30 * exaggeration;
    }
    geometry.computeVertexNormals();

    const material = new THREE.MeshStandardMaterial({
        color: 0x8fbc8f, flatShading: false, side: THREE.DoubleSide
    });

    const colors = [];
    for (let i = 0; i < demValues.length; i++) {
        const t = (demValues[i] - vmin) / range;
        let r, g, b;
        if (t < 0.2) {
            r = 0.2; g = 0.4 + t * 2; b = 0.6 - t;
        } else if (t < 0.5) {
            r = 0.3 + t * 0.4; g = 0.6; b = 0.3;
        } else if (t < 0.8) {
            r = 0.5 + t * 0.3; g = 0.4 + t * 0.2; b = 0.3;
        } else {
            const s = (t - 0.8) / 0.2;
            r = g = b = 0.8 + s * 0.2;
        }
        colors.push(r, g, b);
    }
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    material.vertexColors = true;

    return new THREE.Mesh(geometry, material);
}

// ─────────────────────────────────────────────────────────────────────────────
// Preview
// ─────────────────────────────────────────────────────────────────────────────

function previewModelIn3D() {
    const gmd = window.appState?.generatedModelData;
    const ldd = window.appState?.lastDemData;
    const source = gmd || (ldd ? {
        values:      ldd.values,
        width:       ldd.width,
        height:      ldd.height,
        exaggeration: parseFloat(document.getElementById('modelExaggeration')?.value) || 1.5
    } : null);

    if (!source?.values?.length) {
        showToast('Load a DEM first (Edit tab → Reload).', 'warning'); return;
    }

    if (!modelRenderer) initModelViewer();

    if (terrainMesh) {
        modelScene.remove(terrainMesh);
        terrainMesh.geometry.dispose();
        terrainMesh.material.dispose();
        terrainMesh = null;
    }
    if (modelMesh && modelMesh !== terrainMesh) {
        modelScene.remove(modelMesh);
        modelMesh.geometry.dispose();
        modelMesh.material.dispose();
    }

    terrainMesh = createTerrainMesh(source.values, source.width, source.height, source.exaggeration);
    modelMesh   = terrainMesh;
    terrainMesh.position.set(0, 0, 0);
    modelScene.add(terrainMesh);

    terrainMesh.material.wireframe = document.getElementById('viewerWireframe')?.checked ?? false;

    window.appState.terrainMesh = terrainMesh;

    updatePuzzlePreview();

    const statusEl = document.getElementById('modelStatus');
    if (statusEl) statusEl.textContent = `Preview: ${source.width}×${source.height}, ${source.exaggeration}× exag.`;
    showToast('3D preview loaded! Drag to rotate, scroll to zoom.', 'success');
}

// ─────────────────────────────────────────────────────────────────────────────
// Puzzle preview
// ─────────────────────────────────────────────────────────────────────────────

function updatePuzzlePreview() {
    if (!terrainMesh || !modelScene) return;
    const old = modelScene.getObjectByName('puzzleCuts');
    if (old) modelScene.remove(old);
    if (!document.getElementById('puzzleEnabled')?.checked) return;

    const pX = parseInt(document.getElementById('puzzlePiecesX')?.value) || 3;
    const pY = parseInt(document.getElementById('puzzlePiecesY')?.value) || 3;
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
}

async function exportPuzzle3MF() {
    const region = window.appState?.selectedRegion;
    if (!region) { showToast('Select a region first', 'warning'); return; }
    const pX = parseInt(document.getElementById('puzzlePiecesX')?.value) || 3;
    const pY = parseInt(document.getElementById('puzzlePiecesY')?.value) || 3;
    if (pX * pY > 64) { showToast('Too many pieces (max 64 total)', 'warning'); return; }
    showToast('Puzzle 3MF export: backend implementation pending', 'warning');
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

window.initModelViewer      = initModelViewer;
window.createTerrainMesh    = createTerrainMesh;
window.previewModelIn3D     = previewModelIn3D;
window.haversineDiagKm      = haversineDiagKm;
window.updatePuzzlePreview  = updatePuzzlePreview;
window.exportPuzzle3MF      = exportPuzzle3MF;
window.setViewerAutoRotate  = setViewerAutoRotate;
