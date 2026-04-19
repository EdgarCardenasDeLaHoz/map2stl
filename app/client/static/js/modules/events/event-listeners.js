/**
 * modules/event-listeners.js
 * ==========================
 * Orchestrator: wires all UI event listeners by calling domain modules.
 *
 * Domain modules (loaded before this file):
 *   event-listeners-map.js     — map, DEM, terrain overlays, draw tool, bbox
 *   event-listeners-export.js  — model export, city, puzzle, 3D viewer
 *   event-listeners-ui.js      — resizable panel, JSON toggle, sidebar edit view
 *   keyboard-shortcuts.js      — global keyboard shortcuts
 *
 * Exposes on window:
 *   window.setupEventListeners()
 */

// Named so addEventListener deduplicates if setupEventListeners is called more than once
function _onCollapsibleClick(e) {
    const header = e.target.closest('.collapsible-header');
    if (header) window.toggleCollapsible?.(header);
}

let _listenersWired = false;
window.setupEventListeners = function setupEventListeners() {
    if (_listenersWired) return;
    _listenersWired = true;
    // Tab buttons
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => window.switchView?.(tab.dataset.view));
    });

    // Collapsible section headers — event delegation replaces inline onclick="toggleCollapsible(this)"
    document.addEventListener('click', _onCollapsibleClick);

    // Control buttons
    document.getElementById('loadRegionBtn')?.addEventListener('click', () => window.loadSelectedRegion?.());
    document.getElementById('saveRegionBtn')?.addEventListener('click', () => window.saveCurrentRegion?.());
    document.getElementById('submitBtn')?.addEventListener('click', () => window.submitBoundingBox?.());

    window._setupBboxListeners?.();

    document.getElementById('sidebarToggleBtn')?.addEventListener('click', () => window.cycleSidebarState?.());
    document.getElementById('bboxVisToggleBtn')?.addEventListener('click', () => window.toggleBboxLayerVisibility?.());

    document.getElementById('sidebarTableSearch')?.addEventListener('input', e => window.renderSidebarTable?.(e.target.value));
    document.getElementById('statusToggleBtn')?.addEventListener('click', () => window.toggleStatusPanel?.());
    document.getElementById('applyParamsBtn')?.addEventListener('click', () => window.applyRegionParams?.());
    document.getElementById('clearBboxBtn')?.addEventListener('click', () => window.clearAllBoundingBoxes?.());

    window._setupModelExportListeners?.();
    window._setupMapAndDemListeners?.();

    window.setupOpacityControls?.();
    window.setupAutoReload?.();
    window.setupStackedLayers?.();
    window.setupCoordinateSearch?.();
    window.setupRegionsTable?.();
    window.setupKeyboardShortcuts?.();

    // Compare view — region load, colormap, exaggeration
    for (const side of ['Left', 'Right']) {
        document.getElementById(`compare${side}Region`)?.addEventListener('change', () => window.loadCompareRegion?.(side.toLowerCase()));
        document.getElementById(`compare${side}Colormap`)?.addEventListener('change', () => window.applyCompareColormap?.(side.toLowerCase()));
        document.getElementById(`compare${side}Exag`)?.addEventListener('change', () => window.updateCompareExagLabel?.(side.toLowerCase()));
    }

    window._setupResizablePanel?.();

    window.initCurveEditor?.();
    window.initPresetProfiles?.();
    window.initRegionNotes?.();
    window.initRegionThumbnails?.();
    window.enableStackedZoomPan?.();

    window._setupSettingsJsonToggle?.();
    window._setupCityAndExportListeners?.();
    window._setupSidebarEditView?.();

    // Hydrology section
    document.getElementById('loadHydrologyBtn')?.addEventListener('click', () => window.loadHydrology?.());
    document.getElementById('clearHydrologyBtn')?.addEventListener('click', () => window.clearHydrology?.());
    document.getElementById('hydroSource')?.addEventListener('change', e => {
        const controls = document.getElementById('hydroRiversControls');
        if (controls) controls.style.display = e.target.value === 'hydrorivers' ? '' : 'none';
    });

    // qlLoadHydro (quick-load) wired in event-listeners-map.js via _asyncBtn
};
