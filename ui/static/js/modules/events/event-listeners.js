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

window.setupEventListeners = function setupEventListeners() {
    // Tab buttons
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => window.switchView?.(tab.dataset.view));
    });

    // Control buttons
    document.getElementById('loadRegionBtn').onclick = () => window.loadSelectedRegion?.();
    document.getElementById('saveRegionBtn').onclick = () => window.saveCurrentRegion?.();
    document.getElementById('submitBtn').onclick = () => window.submitBoundingBox?.();

    window._setupBboxListeners?.();

    const sidebarToggleBtn = document.getElementById('sidebarToggleBtn');
    if (sidebarToggleBtn) sidebarToggleBtn.onclick = () => window.cycleSidebarState?.();

    const bboxVisToggleBtn = document.getElementById('bboxVisToggleBtn');
    if (bboxVisToggleBtn) bboxVisToggleBtn.onclick = () => window.toggleBboxLayerVisibility?.();

    const sidebarTableSearch = document.getElementById('sidebarTableSearch');
    if (sidebarTableSearch) {
        sidebarTableSearch.addEventListener('input', () => window.renderSidebarTable?.(sidebarTableSearch.value));
    }

    const statusToggleBtn = document.getElementById('statusToggleBtn');
    if (statusToggleBtn) statusToggleBtn.onclick = () => window.toggleStatusPanel?.();

    const applyParamsBtn = document.getElementById('applyParamsBtn');
    if (applyParamsBtn) applyParamsBtn.onclick = () => window.applyRegionParams?.();

    const clearBboxBtn = document.getElementById('clearBboxBtn');
    if (clearBboxBtn) clearBboxBtn.onclick = () => window.clearAllBoundingBoxes?.();

    window._setupModelExportListeners?.();
    window._setupMapAndDemListeners?.();

    window.setupOpacityControls?.();
    window.setupAutoReload?.();
    window.setupStackedLayers?.();
    window.setupCoordinateSearch?.();
    window.setupRegionsTable?.();
    window.setupKeyboardShortcuts?.();

    window._setupResizablePanel?.();

    window.initCurveEditor?.();
    window.initPresetProfiles?.();
    window.initRegionNotes?.();
    window.initRegionThumbnails?.();
    window.enableStackedZoomPan?.();

    window._setupSettingsJsonToggle?.();
    window._setupCityAndExportListeners?.();
    window._setupSidebarEditView?.();
};
