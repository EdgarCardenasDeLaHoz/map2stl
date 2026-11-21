/**
 * modules/regions.js
 * ==================
 * Load, render, select, save, and delete geographic regions.
 *
 * Public API (exposed on window):
 *   window.selectCoordinate(index) → Promise<void>
 *   window.goToEdit(index)
 *
 * Dependencies (resolved at call-time via window):
 *   window.getCoordinatesData()     / window.setCoordinatesData(data)
 *   window.setSelectedRegion(r)
 *   window.getMap()
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
 *   clearLayerCache()               (global function in app.js)
 *   clearLayerDisplays()            (global function in app.js)
 *   switchView(view)                (global function in app.js)
 */
import { applyDefaultProjection } from '../dem/projection-default.js';


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
    // An imported mesh is registered against the previous region's bbox, and so
    // are the control point pairs picked for it.
    window.clearMeshLayer?.();
    window.clearMeshPointPairs?.();
    window.clearBorders?.();
    // The 3D viewer keeps the previous mesh until a new preview replaces it, and
    // the dragged puzzle cuts come back on any region of the same size.
    window.clearViewerModel?.();
    // A city fetch task of the old region otherwise keeps its progress on screen
    // and keeps polling into appState.cityFetch; aborting it also cancels the
    // server-side task (over-size regions return before loadCityData clears it).
    window.cancelCityFetch?.();
    window.appState.cityFetch = null;

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
        _setEl('exportModelHeight', rp.height ?? 20);
        _setEl('exportBaseHeight', rp.base ?? 5);
        _setEl('mmPerPixel', 1.0);
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
        // Large regions default to laea (sinusoidal shears a continent); a saved projection wins.
        if (!hasSaved) applyDefaultProjection(selectedRegion);
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
