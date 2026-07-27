/**
 * modules/layers/mesh-layer.js — Import an STL/OBJ mesh as a stacked layer.
 *
 * Upload (or pick from the on-disk mesh library), convert to a heightmap for
 * the current DEM bbox, register it via mesh-registration.js's point-pair
 * picker, and either display it as a toggleable stacked layer or merge it
 * into the active DEM.
 *
 * Public API (all on window):
 *   uploadMeshLayer(file)            — POST the file, store upload_id on appState.meshImport
 *   selectLibraryMeshFile(relPath)   — use a library file instead of an upload
 *   computeMeshHeightmap(opts)       — fetch heightmap for the current appState bbox
 *   applyMeshRegistration(result)    — called by mesh-registration.js with the /register response
 *   applyMeshToDem()                 — patch appState.lastDemData.values with the registered mesh
 *   clearMeshLayer()                 — reset mesh layer state
 *
 * Dependencies:
 *   window.api.mesh.*                — modules/core/api.js
 *   window.decodeMeshValues/Mask     — modules/core/ui-helpers.js
 *   window.renderDEMCanvas           — modules/dem/dem-main.js (reused for heightmap colorization)
 *   window.appState.currentDemBbox   — from app.js
 *   window.appState.lastDemData      — from dem-main.js
 *   window.setStackMode/updateStackedLayers — modules/layers/stacked-layers.js
 *   window.showToast                 — from app.js
 *
 * State published to window.appState.meshImport:
 *   { uploadId, libraryRelPath, filename, heightmap: {values, width, height, bbox},
 *     registered: {values, mask, width, height, rmsResidualPx} | null }
 *
 * Fires a `mesh-import-registered` window CustomEvent (detail = the register
 * result) after applyMeshRegistration() runs, so UI components (e.g.
 * MeshImportSection.vue) can react without polling appState.
 */

window.appState = window.appState || {};

function _ensureMeshImportState() {
    if (!window.appState.meshImport) {
        window.appState.meshImport = {
            uploadId: null,
            libraryRelPath: null,
            filename: null,
            heightmap: null,
            registered: null,
        };
    }
    return window.appState.meshImport;
}

/** Upload a File object (from an <input type=file>) as a new mesh layer source. */
window.uploadMeshLayer = async function uploadMeshLayer(file) {
    if (!file) return;
    const state = _ensureMeshImportState();
    window.setLayerStatus?.('meshImport', 'loading');

    const { data, error } = await window.api.mesh.upload(file);
    if (error) {
        window.showToast?.('Mesh upload failed: ' + error, 'error');
        window.setLayerStatus?.('meshImport', 'error');
        return;
    }

    state.uploadId = data.upload_id;
    state.libraryRelPath = null;
    state.filename = data.filename;
    state.heightmap = null;
    state.registered = null;
    window.setLayerStatus?.('meshImport', 'idle');
    window.showToast?.(`Uploaded ${data.filename}`, 'success');
};

/** Select a file from the on-disk mesh library instead of uploading. */
window.selectLibraryMeshFile = function selectLibraryMeshFile(relPath, filename) {
    const state = _ensureMeshImportState();
    state.uploadId = null;
    state.libraryRelPath = relPath;
    state.filename = filename || relPath.split('/').pop();
    state.heightmap = null;
    state.registered = null;
    window.setLayerStatus?.('meshImport', 'idle');
};

// Ray-casting cost scales with pixel count (and mesh face count), not just
// per-pixel resolution — a few hundred thousand pixels can take 10+ seconds
// even for a simple mesh. 300px/side keeps the first preview responsive.
// (Kept in sync with MeshImportSection.vue's own TARGET_GRID_PX — this copy
// is the safety-net default when no explicit resolutionM is passed, e.g.
// from autoRegisterMesh; the UI's slider always shows/overrides the real
// value used.)
const _TARGET_GRID_PX = 300;

/**
 * Suggest a resolution (m/px) that keeps a heightmap grid near
 * _TARGET_GRID_PX on its longer side, for the given bbox. Falls back to 5.0
 * when no bbox is available (matches the server schema's own default).
 * Exposed on window so MeshImportSection.vue can reuse the exact same
 * calculation for its resolution field's initial value, rather than keeping
 * a second copy that could drift out of sync (modules coordinate via
 * window.*, not imports — see CLAUDE.md editing rule 3).
 */
window.suggestedMeshResolutionM = function suggestedMeshResolutionM(bbox) {
    if (!bbox) return 5.0;
    const midLat = (bbox.north + bbox.south) / 2;
    const latM = Math.abs(bbox.north - bbox.south) * 111320;
    const lonM = Math.abs(bbox.east - bbox.west) * 111320 * Math.cos(midLat * Math.PI / 180);
    const longerSideM = Math.max(latM, lonM);
    const suggested = longerSideM / _TARGET_GRID_PX;
    return Math.max(0.5, Math.min(100, Math.round(suggested * 10) / 10));
};

/**
 * Convert the current mesh source (upload or library file) to a heightmap
 * for the active DEM bbox, and render it into appState.meshSourceCanvas.
 * @param {{resolutionM?: number, upAxis?: string, infill?: string}} [opts]
 */
window.computeMeshHeightmap = async function computeMeshHeightmap(opts = {}) {
    const state = _ensureMeshImportState();
    if (!state.uploadId && !state.libraryRelPath) {
        window.showToast?.('Upload or select a mesh file first.', 'warning');
        return;
    }
    const bbox = window.appState?.currentDemBbox;
    if (!bbox) {
        window.showToast?.('Select a region before computing a mesh heightmap.', 'warning');
        return;
    }

    const body = {
        north: bbox.north, south: bbox.south, east: bbox.east, west: bbox.west,
        resolution_m: opts.resolutionM ?? window.suggestedMeshResolutionM(bbox),
        up_axis: opts.upAxis ?? 'z',
        infill: opts.infill ?? 'none',
    };

    window.setLayerStatus?.('meshImport', 'loading');
    const { data, error } = state.uploadId
        ? await window.api.mesh.heightmap(state.uploadId, body)
        : await window.api.mesh.libraryHeightmap(state.libraryRelPath, body);

    if (error) {
        window.showToast?.('Mesh heightmap failed: ' + error, 'error');
        window.setLayerStatus?.('meshImport', 'error');
        return;
    }

    const values = window.decodeMeshValues(data);
    const [h, w] = data.dimensions;
    state.heightmap = {
        values, width: w, height: h, bbox: data.bbox,
        minElevation: data.min_elevation, maxElevation: data.max_elevation,
        validPct: data.valid_pct,
    };
    state.registered = null; // new heightmap invalidates any prior registration

    _renderMeshPreviewCanvas(values, w, h, data.min_elevation, data.max_elevation);
    window.setLayerStatus?.('meshImport', 'loaded');
    window.showToast?.(
        `Mesh heightmap ready (${w}×${h}px, ${data.valid_pct.toFixed(0)}% valid)`, 'success');
};

/**
 * "Auto" mode: geocode the mesh's filename/foldername, run the automatic
 * OSM-based registration search, and match/create a saved region for the
 * result — then load that region's DEM and compute the mesh heightmap for
 * it, so the manual picker (openMeshRegistrationModal) opens pre-populated
 * for the user to confirm or refine. Never silently trusts the automatic
 * result: it always hands off to the manual picker rather than calling
 * applyMeshRegistration directly, since the underlying algorithm's
 * confidence score is not a calibrated match probability (see
 * core/mesh_import.py:auto_register docstring).
 *
 * @returns {Promise<object|null>} the auto-register API result, or null on failure
 */
window.autoRegisterMesh = async function autoRegisterMesh(opts = {}) {
    const state = _ensureMeshImportState();
    if (!state.uploadId && !state.libraryRelPath) {
        window.showToast?.('Upload or select a mesh file first.', 'warning');
        return null;
    }

    window.setLayerStatus?.('meshImport', 'loading');
    window.showToast?.('Auto mode: geocoding + registering against OSM… this can take a minute.', 'info');

    const body = { resolution: opts.resolution ?? 512, filename_hint: opts.filenameHint ?? null };
    const { data, error } = state.uploadId
        ? await window.api.mesh.autoRegister(state.uploadId, body)
        : await window.api.mesh.libraryAutoRegister(state.libraryRelPath, body);

    if (error) {
        window.showToast?.('Auto mode failed: ' + error, 'error');
        window.setLayerStatus?.('meshImport', 'error');
        return null;
    }

    if (data.status === 'unavailable') {
        window.showToast?.('Auto mode is unavailable on this server (registration dependencies not installed).', 'warning');
        window.setLayerStatus?.('meshImport', 'idle');
        return data;
    }
    if (data.status === 'geocode_failed' || !data.bbox) {
        window.showToast?.(
            `Auto mode couldn't determine a location for "${data.city_name || '?'}" — use the manual picker instead.`,
            'warning');
        window.setLayerStatus?.('meshImport', 'idle');
        return data;
    }

    // Select the matched/created region so loadDEM() has a bbox to work with,
    // mirroring the minimum state selectCoordinate() sets (regions.js) —
    // not the full side-effect chain (city overlay, param table, map fly-to),
    // which is more than auto mode needs and couples it to sidebar UI state.
    const region = data.region || {};
    window.appState.selectedRegion = {
        name: region.name || data.city_name,
        north: data.bbox.north, south: data.bbox.south,
        east: data.bbox.east, west: data.bbox.west,
    };
    window.appState.currentDemBbox = { ...data.bbox };

    const regionMsg = region.created
        ? `created region "${region.name}"`
        : `matched existing region "${region.name}" (IoU ${region.iou?.toFixed(2)})`;
    window.showToast?.(
        `Auto mode: ${data.city_name} — ${regionMsg}. Confidence ${data.confidence?.toFixed(2)}, ` +
        `footprint IoU ${data.footprint_iou?.toFixed(2)}. Loading DEM…`, 'info', 6000);

    await window.loadDEM?.();
    // NOTE: `opts` here is the auto-register call's own options (currently
    // just `resolution`, the OSM raster resolution — an unrelated parameter
    // to computeMeshHeightmap's `resolutionM`, the mesh heightmap's
    // metres-per-pixel). Never forward `opts` directly into
    // computeMeshHeightmap: without an explicit resolutionM it falls back to
    // a flat 5m default, which the server rejects for a full city bbox
    // (grid too large — see core/mesh_import._check_grid_size). Let
    // computeMeshHeightmap compute its own bbox-aware suggested resolution.
    await window.computeMeshHeightmap({
        upAxis: opts.upAxis, infill: opts.infill,
        resolutionM: opts.heightmapResolutionM,
    });
    window.openMeshRegistrationModal?.();

    return data;
};

/** Render the (unregistered) mesh heightmap into a preview canvas, reusing the DEM colour LUT. */
function _renderMeshPreviewCanvas(values, width, height, vmin, vmax) {
    const canvas = window.renderDEMCanvas?.(values, width, height, 'terrain', vmin, vmax,
        { skipStateUpdate: true });
    window.appState.meshPreviewCanvas = canvas || null;
    return canvas;
}

/**
 * Called by mesh-registration.js once the user has fitted a registration.
 * Renders the warped heightmap (now on the reference/DEM pixel grid) as the
 * MeshImport stacked layer's source canvas.
 * @param {{values: Float32Array, mask: Uint8Array, width: number, height: number,
 *           minElevation: number, maxElevation: number, rmsResidualPx: number}} result
 */
window.applyMeshRegistration = function applyMeshRegistration(result) {
    const state = _ensureMeshImportState();
    state.registered = result;

    const canvas = window.renderDEMCanvas?.(
        result.values, result.width, result.height, 'terrain',
        result.minElevation, result.maxElevation, { skipStateUpdate: true });

    // Cut out invalid (unregistered/out-of-mask) pixels so the layer only
    // covers the mesh's real footprint, not the full reference canvas.
    if (canvas) {
        const ctx = canvas.getContext('2d');
        const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const mask = result.mask;
        for (let i = 0; i < mask.length; i++) {
            if (!mask[i]) img.data[i * 4 + 3] = 0;
        }
        ctx.putImageData(img, 0, 0);
    }

    window.appState.meshSourceCanvas = canvas || null;
    window.setStackMode?.('MeshImport');
    window.updateStackedLayers?.();
    window.showToast?.(
        `Mesh registered (RMS residual ${result.rmsResidualPx.toFixed(1)}px)`, 'success');
    window.dispatchEvent(new CustomEvent('mesh-import-registered', { detail: result }));
};

/**
 * Replace/blend the registered mesh heightmap into the active DEM, in the
 * mesh's footprint only. Mirrors composite-dem.js's applyCompositeToDem.
 * @param {number} [blendWeight=1] - 1 = mesh fully replaces DEM in its footprint, 0 = no change.
 */
window.applyMeshToDem = function applyMeshToDem(blendWeight = 1) {
    const state = window.appState?.meshImport;
    const reg = state?.registered;
    if (!reg) {
        window.showToast?.('No registered mesh layer — register it first.', 'warning');
        return;
    }
    const dem = window.appState?.lastDemData;
    if (!dem) {
        window.showToast?.('No DEM loaded.', 'warning');
        return;
    }
    if (reg.width !== dem.width || reg.height !== dem.height) {
        window.showToast?.(
            'Registered mesh grid does not match the current DEM grid — re-register.', 'error');
        return;
    }

    const w = Math.max(0, Math.min(1, blendWeight));
    const values = dem.values;
    const meshValues = reg.values;
    const mask = reg.mask;
    for (let i = 0; i < values.length; i++) {
        if (!mask[i]) continue;
        const mv = meshValues[i];
        if (!Number.isFinite(mv)) continue;
        values[i] = w >= 1 ? mv : values[i] * (1 - w) + mv * w;
    }

    dem.min = Math.min(dem.min, reg.minElevation);
    dem.max = Math.max(dem.max, reg.maxElevation);
    window.appState.lastDemData = dem;
    if (window.appState) {
        window.appState.originalDemValues = new Float32Array(values);
    }

    window.recolorDEM?.();
    window.showToast?.('Mesh applied to DEM', 'success');
};

/** Reset mesh layer state (e.g. when switching regions). */
window.clearMeshLayer = function clearMeshLayer() {
    window.appState.meshImport = null;
    window.appState.meshSourceCanvas = null;
    window.appState.meshPreviewCanvas = null;
};
