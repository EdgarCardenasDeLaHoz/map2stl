/**
 * modules/app-setup.js
 *
 * Exposed on window:
 *   setupStackedLayers, setupAutoReload,
 *   loadAllLayers
 *
 * Depends on:
 *   window.appState.layerStatus, window.appState.layerBboxes,
 *   window.appState.lastDemData, window.appState.selectedRegion
 *   window.getCoordinatesData?.()
 *   window.loadDEM?.(), window.loadWaterMask?.(),
 *   window.loadSatelliteRGBImage?.(), window.clearLayerCache?.(),
 *   window.updateLayerStatusIndicators?.(), window.isLayerCurrent?.(),
 *   window.renderCombinedView?.(), window.updateStackedLayers?.(),
 *   window.switchView?.(), window.switchDemSubtab?.()
 *   showToast (global, from ui-helpers.js)
 */

// ─── setupStackedLayers ───────────────────────────────────────────────────────

/**
 * Initialise stacked layers: wire mode selector buttons and load buttons.
 */
window.setupStackedLayers = function setupStackedLayers() {
    // Wire mode selector buttons
    document.getElementById('layerModeSelector')
        ?.querySelectorAll('.layer-mode-btn')
        .forEach(btn => {
            btn.addEventListener('click', () => window.setStackMode?.(btn.dataset.mode));
        });

    // 📡 Load satellite imagery — fetch ESRI tiles, then switch to Sat mode
    const loadSatImgBtn = document.getElementById('loadSatImgBtn');
    if (loadSatImgBtn) {
        loadSatImgBtn.addEventListener('click', async () => {
            loadSatImgBtn.disabled = true;
            const origText = loadSatImgBtn.textContent;
            loadSatImgBtn.textContent = '⏳';
            try {
                await window.loadSatelliteRGBImage?.();
                window.setStackMode?.('SatImg');
            } finally {
                loadSatImgBtn.disabled = false;
                loadSatImgBtn.textContent = origText;
            }
        });
    }
};

// ─── setupAutoReload ──────────────────────────────────────────────────────────

/**
 * Watch settings inputs and auto-reload all layers when they change
 * (only if the `#autoReloadLayers` checkbox is checked).
 */
window.setupAutoReload = function setupAutoReload() {
    const autoReloadCheckbox = document.getElementById('autoReloadLayers');
    const settingsToWatch = ['paramDim'];

    settingsToWatch.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', () => {
                if (autoReloadCheckbox && autoReloadCheckbox.checked && window.appState.selectedRegion) {
                    window.showToast('Settings changed - reloading layers...', 'info');
                    window.loadAllLayers?.();
                }
            });
        }
    });
};

// ─── loadAllLayers ────────────────────────────────────────────────────────────

/**
 * Load the DEM first, then every secondary layer in parallel — water mask,
 * hydrology, land cover, satellite imagery, trails, and city data.
 * Switches to the Edit view first.
 * @returns {Promise<void>}
 */
window.loadAllLayers = async function loadAllLayers() {
    const boundingBox    = window.getBoundingBox?.();
    const selectedRegion = window.appState.selectedRegion;

    if (!boundingBox && !selectedRegion) {
        window.showToast('Please select a region or draw a bounding box first.', 'warning');
        return;
    }

    // Switch to DEM view
    window.switchView?.('dem');

    // Show loading state
    const demImage = document.getElementById('demImage');
    if (demImage) demImage.innerHTML = '<p style="text-align:center;padding:50px;">Loading all layers...</p>';

    try {
        // Load DEM first (other layers depend on DEM dimensions)
        await window.loadDEM?.();

        // Load secondary layers in parallel.
        // Note: loadSatelliteImage() is an alias for loadEsaLandCover() (see
        // dem-main.js) — call it once, not both, or the second call's
        // AbortController cancels the first mid-flight (ERR_ABORTED + a
        // confusing "signal is aborted" error toast for no reason).
        // Rivers, satellite image and trails only when the layer is in the model or shown
        // (stores/editLayers.ts::isEditLayerWanted): on a continent-sized box the rivers alone
        // took minutes for a layer that was off (Amazon, 2026-10-04). Switching one on later
        // loads it (stacked-layers.js LAYER_AUTOLOAD).
        const wanted = (id) => (window.isEditLayerWanted ? window.isEditLayerWanted(id) : true);
        const tasks = [
            window.loadWaterMask?.(),
            wanted('water') && window.loadWaterHydrology?.(),
            window.loadEsaLandCover?.(),
            wanted('satellite') && window.loadSatelliteRGBImage?.(),
            // activate:false - a bulk load must not change which layer the user is looking at.
            wanted('trails') && window.loadTrails?.({ activate: false }),
        ].filter(Boolean);

        // haversineDiagKm lives on window (model-viewer.js), not appState, and
        // takes the bbox as explicit args — this previously always evaluated to
        // undefined, so bulk-load never included city data for any region size.
        const r = selectedRegion || boundingBox;
        const diagKm = (r && typeof window.haversineDiagKm === 'function')
            ? window.haversineDiagKm(r.north, r.south, r.east, r.west)
            : null;
        const maxDiag = window.CITY_MAX_DIAG_KM ?? 10;
        const maxDiagCoarse = window.CITY_COARSE_MAX_DIAG_KM ?? 25;
        // loadCityData() resolves full vs. coarse detail internally from the
        // region size — only regions beyond the coarse cap are skipped here.
        // loadCityRaster() (fast raster-only path) still requires full detail,
        // so it's only used under the original 10km cap.
        if (diagKm !== null && diagKm <= maxDiag && window.loadCityRaster) {
            tasks.push(window.loadCityRaster());
        } else if (diagKm !== null && diagKm <= maxDiagCoarse && window.loadCityData) {
            tasks.push(window.loadCityData());
        } else if (diagKm !== null) {
            window.showToast?.(`Skipping city/building data — region too large (${diagKm.toFixed(1)} km, max ${maxDiagCoarse} km).`, 'info');
        }

        // allSettled, not all: these layers are independent, and one flaky
        // source — trails in particular, since Overpass returns 500s under
        // load — must not throw away the layers that did arrive.
        const results = await Promise.allSettled(tasks);
        results.filter(r => r.status === 'rejected')
            .forEach(r => console.warn('Layer failed during bulk load:', r.reason));

        // Precompute combined output, but keep the visible canvas on Layers
        // so the user always lands on a non-empty default rendering pane.
        window.renderCombinedView?.();
        window.switchDemSubtab?.('layers');

    } catch (error) {
        console.error('Error loading layers:', error);
        window.showToast('Error loading layers: ' + error.message, 'error');
    }
};

// ─── ensureA11yLabels ────────────────────────────────────────────────────────

/**
 * Add aria-labels for controls that are rendered dynamically or hidden from
 * normal label-for associations so automated audits can still resolve names.
 */
window.ensureA11yLabels = function ensureA11yLabels() {
    const byId = {
        cityLayerBuildings: 'Include city buildings layer',
        cityLayerRoads: 'Include city roads layer',
        cityLayerWaterways: 'Include city waterways layer',
        layerBuildingsColor: 'Buildings layer color',
        layerRoadsColor: 'Roads layer color',
        layerWaterwaysColor: 'Waterways layer color',
        cityRoofShapes: 'Enable slanted city roof shapes',
        exportSeaLevelCap: 'Enable sea level cap',
        exportWalls: 'Enable model side walls',
        exportFloor: 'Enable model floor',
        exportEngraveLabel: 'Enable engraved label',
        exportContours: 'Enable contour lines',
        exportLabelText: 'Export label text',
        puzzleEnabled: 'Enable puzzle split mode',
        splitCols: 'Puzzle split columns',
        splitRows: 'Puzzle split rows',
        splitKnobWidth: 'Puzzle knob width in millimetres',
        splitKnobDepth: 'Puzzle knob depth in millimetres',
        splitClearance: 'Puzzle fit clearance in millimetres',
        bedSizeSelect: 'Printer bed size preset',
        bedCustomW: 'Custom printer bed width in millimetres',
        bedCustomH: 'Custom printer bed height in millimetres',
        crossSectionAxis: 'Cross-section axis',
        crossSectionValue: 'Cross-section coordinate value',
        crossSectionThickness: 'Cross-section slab depth in millimetres',
        viewerWireframe: 'Show viewer wireframe',
        viewerNormals: 'Show viewer normals',
        viewerAutoRotate: 'Enable viewer auto-rotate',
    };

    Object.entries(byId).forEach(([id, label]) => {
        const el = document.getElementById(id);
        if (el && !el.getAttribute('aria-label')) {
            el.setAttribute('aria-label', label);
        }
    });
};

document.addEventListener('DOMContentLoaded', () => {
    window.ensureA11yLabels?.();

    const root = document.body;
    if (!root) return;
    const observer = new MutationObserver(() => window.ensureA11yLabels?.());
    observer.observe(root, { childList: true, subtree: true });
});

window.ensureA11yLabels?.();
