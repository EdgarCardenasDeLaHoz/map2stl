/**
 * modules/regions.js
 * ==================
 * Load, render, select, save, and delete geographic regions.
 *
 * Public API (exposed on window):
 *   window.loadCoordinates()       → Promise<void>
 *   window.selectCoordinate(index) → Promise<void>
 *   window.goToEdit(index)
 *
 * Dependencies (resolved at call-time via window):
 *   window.api.regions.list()
 *   window.getCoordinatesData()     / window.setCoordinatesData(data)
 *   window.setSelectedRegion(r)
 *   window.drawRegionBoxes(regions) (region-boxes.js)
 *   window.getMap()
 *   window.getGlobeScene()
 *   window.BBOX_COLORS
 *   window.getSidebarState()        / window.setSidebarState(s)
 *   window.clearCityOverlay?.()
 *   window.haversineDiagKm(n, s, e, w)
 *   window.loadDEM?.()
 *   window.loadWaterMask?.()
 *   window.loadSatelliteImage?.()   (or bare call — global in app.js)
 *   window.loadAndApplyRegionSettings?.(name)
 *   window.loadWaterMask?.()
 *   window._updateCitiesLoadButton?.(region)
 *   window.appState._updateWorkflowStepper?.()
 *   renderCoordinatesList()         (global from region-ui.js)
 *   detectContinent(lat, lon)       (global from region-ui.js)
 *   clearLayerCache()               (global function in app.js)
 *   clearLayerDisplays()            (global function in app.js)
 *   switchView(view)                (global function in app.js)
 */

// ── Auto-scale thresholds ────────────────────────────────────────────────────

/**
 * Breakpoint tables used by selectCoordinate to auto-set DEM dim (and
 * water/ESA output resolution, also in pixels) based on the selected
 * region's diagonal distance. Each entry applies when diagKm <= maxKm.
 */
const AUTO_SCALE = {
    dim: [
        { maxKm: 10, dim: 600 },
        { maxKm: 50, dim: 500 },
        { maxKm: 200, dim: 600 },
        { maxKm: Infinity, dim: 600 },
    ],
};

// ── loadCoordinates ─────────────────────────────────────────────────────────

/**
 * Fetch all saved regions from `/api/regions` and populate the UI: the sidebar
 * list, the outline boxes on the map (region-boxes.js::drawRegionBoxes) and the
 * globe markers.
 * @returns {Promise<void>}
 */
async function loadCoordinates() {
    const list = document.getElementById('coordinatesList');

    if (!list) {
        console.error('coordinatesList element not found!');
        return;
    }

    list.innerHTML = '<div class="loading"><span class="spinner"></span>Loading coordinates...</div>';

    try {
        const { data, error } = await window.api.regions.list();
        if (error) throw new Error(error);

        window.setCoordinatesData?.(data.regions || []);

        // Map boxes and the sidebar list, which show the same viewport set
        // (region-boxes.js::drawRegionBoxes renders the list too).
        window.drawRegionBoxes(window.getCoordinatesData?.() || []);

        // Add markers to globe
        updateGlobeMarkers();

    } catch (error) {
        console.error('Error loading coordinates:', error);
        list.innerHTML = '<div class="loading" style="color:red;">Error loading coordinates: ' + error.message + '</div>';
    }
}
window.loadCoordinates = loadCoordinates;

// ── updateGlobeMarkers ──────────────────────────────────────────────────────

/**
 * Refresh all coordinate markers on the Three.js globe from `coordinatesData`.
 */
function updateGlobeMarkers() {
    const globeScene = window.getGlobeScene?.();

    // Check if globeScene exists and has the markers group
    if (!globeScene || !globeScene.children || globeScene.children.length < 3) {
        return; // Globe not initialized yet
    }

    // Clear existing markers
    const markersGroup = globeScene.children[2];
    if (!markersGroup || !markersGroup.children) {
        return;
    }

    while (markersGroup.children.length > 0) {
        markersGroup.remove(markersGroup.children[0]);
    }

    const coordinatesData = window.getCoordinatesData?.() || [];
    const BBOX_COLORS = window.BBOX_COLORS || [];

    // Add markers for each region
    coordinatesData.forEach((region, i) => {
        const centerLat = (region.north + region.south) / 2;
        const centerLng = (region.east + region.west) / 2;
        const cssColor = BBOX_COLORS[i % BBOX_COLORS.length]?.color;
        const color = cssColor ? parseInt(cssColor.slice(1), 16) : 0xff0000;

        const marker = createGlobeMarker(centerLat, centerLng, color);
        markersGroup.add(marker);
    });
}

// ── createGlobeMarker ───────────────────────────────────────────────────────

/**
 * Create a Three.js sprite marker positioned on the globe surface.
 * @param {number} lat   - Latitude in degrees
 * @param {number} lng   - Longitude in degrees
 * @param {number} color - Three.js integer color (default red 0xff0000)
 * @returns {THREE.Mesh} The created marker mesh
 */
function createGlobeMarker(lat, lng, color = 0xff0000) {
    const phi = (90 - lat) * (Math.PI / 180);
    const theta = (lng + 180) * (Math.PI / 180);

    const geometry = new THREE.SphereGeometry(0.05, 8, 8);
    const material = new THREE.MeshBasicMaterial({ color });
    const marker = new THREE.Mesh(geometry, material);

    marker.position.x = 5 * Math.sin(phi) * Math.cos(theta);
    marker.position.y = 5 * Math.cos(phi);
    marker.position.z = 5 * Math.sin(phi) * Math.sin(theta);

    return marker;
}

// ── selectCoordinate ────────────────────────────────────────────────────────

/**
 * Select a region by index: sets `selectedRegion`, flies the map to it,
 * loads and applies region settings, and updates all list/table UIs.
 *
 * Side effects (in order):
 *  1. Sets `window.appState.selectedRegion` and calls `window.setSelectedRegion`.
 *  2. Calls `clearLayerCache()` and `clearLayerDisplays()` to flush stale layer data.
 *  3. Calls `clearCityOverlay()` if available so city auto-load triggers for the new region.
 *  4. Awaits `window.loadAndApplyRegionSettings(name)` — writes demParams, dim, sat_scale etc.
 *     Falls back to `selectedRegion.parameters` if no saved settings exist.
 *  5. Without saved settings: raises `#paramDim` to the AUTO_SCALE breakpoint for the region's
 *     size (never lowers it) and sets every layer resolution to it.
 *  6. Calls `map.fitBounds` (wrapped in try/catch — fails silently if map is hidden).
 *  7. If the Edit (DEM) view is visible: fires `loadDEM` → then `loadWaterMask`,
 *     `loadSatelliteImage`, and optionally `loadCityData` (only if diagKm ≤ 15) in parallel.
 *  8. Calls `window.appState._updateWorkflowStepper`.
 *
 * @param {number} index - Index into `coordinatesData` (from `window.getCoordinatesData()`)
 * @param {{skipEditReload?: boolean}} [opts]
 * @returns {Promise<void>}
 */
async function selectCoordinate(index, opts = {}) {
    const coordinatesData = window.getCoordinatesData?.() || [];
    const selectedRegion = coordinatesData[index];
    window.setSelectedRegion?.(selectedRegion);
    window.appState.selectedRegion = selectedRegion;

    // Populate bbox inputs immediately so they're never empty after selection
    window.setBboxInputValues?.(selectedRegion.north, selectedRegion.south,
        selectedRegion.east, selectedRegion.west);
    // The inputs are only the display. `appState.boundingBox` is what every
    // layer fetch reads, and `loadAllLayers()` below is one of those callers, so
    // without this the new region is selected and the old region's data loads.
    window.setBboxRectangle?.(selectedRegion.north, selectedRegion.south,
        selectedRegion.east, selectedRegion.west);

    // CRITICAL: Clear cached layer data and clear visual displays when region changes
    // Prevents stale water mask / land cover / DEM from showing with new region
    window.clearLayerCache();
    window.clearLayerDisplays();
    // Clear city overlay so auto-load triggers for the new region
    if (typeof window.clearCityOverlay === 'function') window.clearCityOverlay();
    // An imported mesh is registered against the previous region's bbox.
    window.clearMeshLayer?.();
    window.clearBorders?.();

    // Highlight in sidebar list
    document.querySelectorAll('.coordinate-item').forEach(item => {
        item.classList.toggle('selected', item.dataset.regionName === selectedRegion.name);
    });

    // Load parameters: try saved region_settings.json first, fall back to
    // legacy coordinates.json parameters, then hard-coded defaults.
    // Await so settings are applied before DEM is loaded below.
    const hasSaved = await window.loadAndApplyRegionSettings?.(selectedRegion.name);
    if (!hasSaved && selectedRegion.parameters) {
        const rp = selectedRegion.parameters;
        const _setEl = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
        const _setChk = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.checked = Boolean(v); };
        _setEl('paramDim', rp.dim || 600);
        _setEl('paramDepthScale', rp.depth_scale ?? 0.5);
        _setEl('paramWaterScale', rp.water_scale ?? 0.05);
        _setChk('paramSubtractWater', rp.subtract_water !== false);
        _setEl('waterResolution', rp.dim ?? 600);
        _setEl('exportModelHeight', rp.height ?? 10);
        _setEl('exportBaseHeight', rp.base ?? 2);
        if (window.appState?.demParams) {
            window.appState.demParams.depthScale = rp.depth_scale ?? 0.5;
            window.appState.demParams.waterScale = rp.water_scale ?? 0.05;
            window.appState.demParams.subtractWater = rp.subtract_water !== false;
            window.appState.demParams.dim = rp.dim ?? 600;
            window.appState.demParams.height = rp.height ?? 10;
            window.appState.demParams.base = rp.base ?? 2;
        }
    }

    // Populate label editor with selected region's current label
    const labelEditEl = document.getElementById('regionLabelEdit');
    if (labelEditEl) labelEditEl.value = selectedRegion.label || '';

    // Refresh datalist of existing labels from all regions
    const datalist = document.getElementById('regionLabelsList');
    if (datalist) {
        const labels = [...new Set(coordinatesData.map(r => r.label).filter(Boolean))].sort();
        datalist.innerHTML = labels.map(l => `<option value="${l}">`).join('');
    }

    // Show/hide Cities tab based on region diagonal
    window._updateCitiesLoadButton?.(selectedRegion);

    // Auto-select water/land cover resolution (sat_scale) and DEM dim based on region diagonal.
    // ESA WorldCover is 10m native; use that for city scale to avoid quality loss.
    // Also raise paramDim for small regions so the water/sat alignment target is high enough.
    {
        const diagKm = window.haversineDiagKm?.(
            selectedRegion.north, selectedRegion.south,
            selectedRegion.east, selectedRegion.west
        );
        // dim: output resolution in pixels — applies to DEM, water mask, and ESA land cover.
        // Use the same breakpoint table to auto-set waterResolution and esaResolution
        // (both now hold pixel counts, not m/px).
        const autoDim = AUTO_SCALE.dim.find(t => diagKm <= t.maxKm)?.dim ?? 600;

        // DEM dim: only raise if lower than the auto value and no saved settings loaded.
        const dimEl = document.getElementById('paramDim');
        if (dimEl) {
            const currentDim = parseInt(dimEl.value) || 600;
            // Raise dim if it is lower than what the region size warrants.
            // Never lower the user's explicit choice.
            // Skip if saved settings were loaded — respect the persisted dim.
            if (!hasSaved && autoDim > currentDim) dimEl.value = String(autoDim);
        }
        // Every layer's resolution follows the terrain's Detail unless the region saved its
        // own (F-EDITPANEL, user 2026-10-04: "a number entry and a check box to override").
        if (!hasSaved && dimEl) {
            for (const id of ['waterResolution', 'esaResolution', 'satImgResolution', 'cityRasterDim', 'trailsDim']) {
                const el = document.getElementById(id);
                if (el) el.value = dimEl.value;
            }
        }
    }

    // Fly to region on map (if map is visible)
    const map = window.getMap?.();
    if (map) {
        const bounds = [[selectedRegion.south, selectedRegion.west],
        [selectedRegion.north, selectedRegion.east]];
        try { map.fitBounds(bounds, { padding: [20, 20] }); } catch (e) { /* best-effort; failure is non-fatal */ }
    }

    // If the user is already in Edit/DEM view, region selection should immediately
    // refresh the loaded layers for the new bbox.
    const demContainerVisible = !document.getElementById('demContainer')?.classList.contains('hidden');
    if (!opts.skipEditReload && demContainerVisible) {
        await window.loadAllLayers?.();
    }

    window.appState._updateWorkflowStepper?.();

    // Emit on the event bus so any module can react to region selection
    // without needing a direct function reference.
    window.events?.emit(window.EV?.REGION_SELECTED, index, selectedRegion);
}
window.selectCoordinate = selectCoordinate;

// ── goToEdit ────────────────────────────────────────────────────────────────

/**
 * Select a region and immediately navigate to the Edit (DEM) tab,
 * triggering a full layer load (DEM + water mask + satellite).
 * @param {number} index - Index into `coordinatesData`
 */
async function goToEdit(index) {
    await window.selectCoordinate(index, { skipEditReload: true });
    window.switchView('dem');

    // Keep the region list visible so the user can switch to another region
    document.getElementById('sidebarListView')?.classList.remove('hidden');
    document.getElementById('sidebarTableView')?.classList.add('hidden');
    window.setRegionEditorOpen?.(false);

    // Ensure sidebar is in normal mode (visible, not expanded/hidden). Editing a
    // region needs the panel on screen; SidebarPanel.vue owns the mode, so go
    // through it rather than moving its classes underneath it.
    if (window.getSidebarState?.() !== 'normal') {
        window.setSidebarMode?.('normal');
    }

    window.loadAllLayers?.().catch(err => {
        console.error('Error loading layers in goToEdit:', err);
        window.showToast?.('Error loading layers: ' + (err?.message || err), 'error');
    });
}
window.goToEdit = goToEdit;
