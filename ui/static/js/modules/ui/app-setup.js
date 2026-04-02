/**
 * modules/app-setup.js
 *
 * Exposed on window:
 *   setupStackedLayers, setupAutoReload, clearAllBoundingBoxes,
 *   loadAllLayers, setupOpacityControls
 *
 * Depends on:
 *   window.appState.layerStatus, window.appState.layerBboxes,
 *   window.appState.lastDemData, window.appState.selectedRegion
 *   window.getCoordinatesData?.(), window.getDrawnItems?.(),
 *   window.getPreloadedLayer?.(), window.getEditMarkersLayer?.(),
 *   window.getWaterOpacity?.(), window.setWaterOpacity?.()
 *   window.BBOX_COLORS, window.resetBboxColorIndex?.(),
 *   window.updateBboxIndicator?.()
 *   window.loadDEM?.(), window.loadWaterMask?.(),
 *   window.loadSatelliteImage?.(), window.loadSatelliteRGBImage?.(), window.clearLayerCache?.(),
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

    // 🌍 Load land use — fetch ESA land cover, then switch to ESA mode
    const loadSatBtn = document.getElementById('loadSatBtn');
    if (loadSatBtn) {
        loadSatBtn.addEventListener('click', async () => {
            loadSatBtn.disabled = true;
            const origText = loadSatBtn.textContent;
            loadSatBtn.textContent = '⏳';
            try {
                await window.loadSatelliteImage?.();
                window.setStackMode?.('Sat');
            } finally {
                loadSatBtn.disabled = false;
                loadSatBtn.textContent = origText;
            }
        });
    }

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

// ─── clearAllBoundingBoxes ────────────────────────────────────────────────────

/**
 * Remove all drawn and preloaded bounding box layers from the map,
 * reset selection state, and clear cached layer data.
 */
window.clearAllBoundingBoxes = function clearAllBoundingBoxes() {
    const drawnItems      = window.getDrawnItems?.();
    const preloadedLayer  = window.getPreloadedLayer?.();
    const editMarkersLayer = window.getEditMarkersLayer?.();

    if (drawnItems)       drawnItems.clearLayers();
    if (preloadedLayer)   preloadedLayer.clearLayers();
    if (editMarkersLayer) editMarkersLayer.clearLayers();

    window.setBoundingBox?.(null);
    window.setSelectedRegion?.(null);
    window.appState.selectedRegion = null;
    window.resetBboxColorIndex?.();
    window.updateBboxIndicator?.(window.BBOX_COLORS?.[0]?.color);

    // Clear all cached layer data
    window.clearLayerCache?.();

    // Clear layer displays
    const demImage        = document.getElementById('demImage');
    const waterMaskImage  = document.getElementById('waterMaskImage');
    const satelliteImage  = document.getElementById('satelliteImage');
    const combinedImage   = document.getElementById('combinedImage');
    if (demImage)       demImage.innerHTML       = '<p style="text-align:center;padding:50px;color:#888;">Select a region to view DEM</p>';
    if (waterMaskImage) waterMaskImage.innerHTML = '<p style="text-align:center;padding:50px;color:#888;">Select a region to view water mask</p>';
    if (satelliteImage) satelliteImage.innerHTML = '<p style="text-align:center;padding:50px;color:#888;">Select a region to view land cover</p>';
    if (combinedImage)  combinedImage.innerHTML  = '<p style="text-align:center;padding:50px;color:#888;">Select a region to view combined layers</p>';

    window.showToast('All selections cleared', 'info');
};

// ─── loadAllLayers ────────────────────────────────────────────────────────────

/**
 * Load DEM, water mask, and land cover in sequence for the current region.
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

        // Load secondary layers in parallel
        await Promise.all([
            window.loadWaterMask?.(),
            window.loadSatelliteImage?.(),
        ]);

        // Render combined view automatically
        window.switchDemSubtab?.('combined');
        window.renderCombinedView?.();

    } catch (error) {
        console.error('Error loading layers:', error);
        window.showToast('Error loading layers: ' + error.message, 'error');
    }
};

// ─── setupOpacityControls ─────────────────────────────────────────────────────

/**
 * Wire the single active-layer opacity slider to the stack view canvas.
 */
window.setupOpacityControls = function setupOpacityControls() {
    const slider = document.getElementById('activeLayerOpacity');
    const label  = document.getElementById('activeLayerOpacityLabel');
    if (slider) {
        slider.addEventListener('input', () => {
            if (label) label.textContent = slider.value + '%';
            window.events?.emit(window.EV?.STACKED_UPDATE);
        });
    }
};
